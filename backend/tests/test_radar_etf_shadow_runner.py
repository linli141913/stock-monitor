import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from radar.config import RadarSettings
from radar.contracts import (
    EtfRegistryRecord,
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
)
from radar.etf_repository import EtfRepository
from radar.etf_shadow_runner import (
    EtfStage5ShadowDisabledError,
    EtfStage5ShadowExecutionError,
    EtfStage5ShadowInProgressError,
    EtfStage5ShadowRunner,
)
from radar.migrations import (
    STAGE5_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from radar.repository import RadarRepository
from radar.runtime import RadarRuntime
from radar.run_lock import CrossProcessFileLock
from radar.scoped_runner import RadarTaskScope, ScopedShadowRunner
from radar.shadow_runner import ShadowSources


UTC = timezone.utc
AS_OF = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
FETCHED_AT = AS_OF + timedelta(seconds=2)
APPLIED_AT = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)


class EtfStage5ShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temp_dir.name) / "etf-stage5.lock"
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE5_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.radar_repository = RadarRepository(
            self.connection,
            clock=lambda: FETCHED_AT,
        )
        self.etf_repository = EtfRepository(
            self.connection,
            clock=lambda: FETCHED_AT,
        )
        self.enabled_settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=True,
        )

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def start_run(self, radar_run_id="etf-run"):
        self.radar_repository.start_run(
            radar_run_id,
            AS_OF,
            started_at=AS_OF,
            shadow_mode=True,
        )
        self.radar_repository.complete_run(
            radar_run_id,
            status="succeeded",
            completed_at=FETCHED_AT,
            expected_etf_count=1,
            returned_etf_count=1,
        )

    @staticmethod
    def quote(symbol="159915"):
        return QuoteSnapshot(
            symbol=symbol,
            name="创业板ETF",
            sourceTime=AS_OF - timedelta(seconds=20),
            fetchedAt=FETCHED_AT,
            price=2.1,
            changePercent=1.2,
            turnoverAmountSource=123456.0,
            turnoverRatePercent=None,
            volumeRatio=None,
            marketCapSource=None,
        )

    @classmethod
    def quote_batch(cls, radar_run_id="etf-run", items=None):
        records = [cls.quote()] if items is None else list(items)
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=radar_run_id,
                batchId=f"{radar_run_id}:etf-quotes",
                source="tencent_finance",
                asOf=AS_OF,
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=FETCHED_AT,
                expectedCount=len(records),
                returnedCount=len(records),
                rowCoverage=(1.0 if records else 0.0),
                requiredFieldCoverage={
                    "price": 1.0 if records else 0.0,
                    "source_time": 1.0 if records else 0.0,
                },
            ),
            items=records,
        )

    @staticmethod
    def health(
        status=SourceStatus.HEALTHY,
        *,
        reasons=(),
    ):
        return SourceHealthResult(
            status=status,
            allowsNewState=status == SourceStatus.HEALTHY,
            reasons=reasons,
            ageSeconds=20,
        )

    def runner(self, settings=None):
        return EtfStage5ShadowRunner(
            self.etf_repository,
            settings=settings or self.enabled_settings,
            lock_path=self.lock_path,
            clock=lambda: FETCHED_AT,
        )

    def test_disabled_returns_before_creating_lock_or_writing(self):
        self.start_run()
        settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=False,
        )

        with self.assertRaises(EtfStage5ShadowDisabledError):
            self.runner(settings).run_once(
                "etf-run",
                AS_OF,
                self.quote_batch(),
                self.health(),
            )

        self.assertFalse(self.lock_path.exists())
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_feature_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_candidate_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_healthy_shared_batch_writes_features_but_keeps_true_empty_candidates(self):
        self.start_run()

        result = self.runner().run_once(
            "etf-run",
            AS_OF,
            self.quote_batch(),
            self.health(),
        )

        self.assertEqual(result.status, "not_ready")
        self.assertFalse(result.gate_passed)
        self.assertEqual(result.persisted_feature_count, 1)
        self.assertEqual(result.candidate_group_count, 0)
        self.assertIn("etf_rule_not_frozen", result.gate_reasons)
        feature = self.etf_repository.get_feature_snapshot(
            "etf-run",
            "159915",
        )
        self.assertEqual(feature["price"], 2.1)
        self.assertFalse(feature["formalUsable"])
        self.assertEqual(
            feature["fieldStates"]["turnoverAmount"],
            "source_unverified",
        )
        candidate = self.etf_repository.get_candidate_snapshot("etf-run")
        self.assertEqual(candidate["candidateGroupCount"], 0)
        self.assertEqual(candidate["entries"], [])
        self.assertEqual(candidate["quality"], "unavailable")

        repeated = self.runner().run_once(
            "etf-run",
            AS_OF,
            self.quote_batch(),
            self.health(),
        )
        self.assertEqual(repeated.persisted_feature_count, 0)

    def test_stale_batch_records_failure_summary_without_feature_rows(self):
        self.start_run()
        stale_health = self.health(
            SourceStatus.STALE,
            reasons=("source_time_stale",),
        )

        result = self.runner().run_once(
            "etf-run",
            AS_OF,
            self.quote_batch(),
            stale_health,
        )

        self.assertEqual(result.status, "stale")
        self.assertEqual(result.persisted_feature_count, 0)
        self.assertIn("quote_source_stale", result.gate_reasons)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_feature_snapshots"
            ).fetchone()[0],
            0,
        )
        candidate = self.etf_repository.get_candidate_snapshot("etf-run")
        self.assertEqual(candidate["staleCount"], 1)
        self.assertEqual(candidate["candidateGroupCount"], 0)

    def test_batch_identity_and_duplicate_symbols_are_rejected(self):
        self.start_run()
        with self.assertRaises(EtfStage5ShadowExecutionError):
            self.runner().run_once(
                "etf-run",
                AS_OF,
                self.quote_batch("other-run"),
                self.health(),
            )

        duplicate_batch = self.quote_batch(
            items=[self.quote(), self.quote()],
        )
        with self.assertRaises(EtfStage5ShadowExecutionError):
            self.runner().run_once(
                "etf-run",
                AS_OF,
                duplicate_batch,
                self.health(),
            )
        self.assertFalse(self.lock_path.exists())

    def test_independent_cross_process_lock_blocks_stage5_only(self):
        self.start_run()
        held_lock = CrossProcessFileLock(self.lock_path)
        self.assertTrue(held_lock.acquire(blocking=False))
        try:
            with self.assertRaises(EtfStage5ShadowInProgressError):
                self.runner().run_once(
                    "etf-run",
                    AS_OF,
                    self.quote_batch(),
                    self.health(),
                )
        finally:
            held_lock.release()

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_feature_snapshots"
            ).fetchone()[0],
            0,
        )


class EtfStage5SharedBatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE5_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: FETCHED_AT,
        )
        registry_batch = SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="registry-run",
                batchId="registry-run:etf-registry",
                source="official_exchange_etf_registry",
                asOf=AS_OF,
                fetchedAt=FETCHED_AT,
                expectedCount=1,
                returnedCount=1,
                rowCoverage=1.0,
                requiredFieldCoverage={
                    "symbol": 1.0,
                    "name": 1.0,
                    "source_type": 1.0,
                },
            ),
            items=[
                EtfRegistryRecord(
                    symbol="159915",
                    name="创业板ETF",
                    exchange="szse",
                    sourceType="股票ETF",
                    sourceReportDate=AS_OF.date(),
                    source="szse",
                    fetchedAt=FETCHED_AT,
                    sourceFields={},
                ),
            ],
        )
        self.repository.sync_etf_registry(registry_batch)

    def tearDown(self):
        self.connection.close()

    def test_existing_etf_quote_fetch_happens_once_and_same_batch_is_forwarded(self):
        quote_batch = EtfStage5ShadowRunnerTests.quote_batch(
            radar_run_id="quote-run",
        )
        quote_fetcher = Mock(return_value=quote_batch)
        stage5_processor = Mock(return_value=Mock(
            status="not_ready",
            gate_reasons=("etf_rule_not_frozen",),
        ))
        sources = ShadowSources(
            security_master=Mock(),
            etf_registry=Mock(),
            quotes=quote_fetcher,
        )
        runner = ScopedShadowRunner(
            repository=self.repository,
            settings=RadarSettings(
                enabled=True,
                shadow_mode=True,
                etf_stage5_enabled=True,
            ),
            sources=sources,
            clock=lambda: FETCHED_AT,
            etf_stage5_processor=stage5_processor,
        )

        result = runner.run_once(
            RadarTaskScope.ETF_QUOTES,
            "quote-run",
            AS_OF,
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.etf_stage5_status, "not_ready")
        quote_fetcher.assert_called_once()
        stage5_processor.assert_called_once()
        self.assertIs(stage5_processor.call_args.args[2], quote_batch)

    def test_stage5_failure_does_not_change_existing_etf_quote_health(self):
        quote_fetcher = Mock(return_value=(
            EtfStage5ShadowRunnerTests.quote_batch("quote-run-failure")
        ))
        stage5_processor = Mock(side_effect=RuntimeError("stage5 failed"))
        runner = ScopedShadowRunner(
            repository=self.repository,
            settings=RadarSettings(
                enabled=True,
                shadow_mode=True,
                etf_stage5_enabled=True,
            ),
            sources=ShadowSources(
                security_master=Mock(),
                etf_registry=Mock(),
                quotes=quote_fetcher,
            ),
            clock=lambda: FETCHED_AT,
            etf_stage5_processor=stage5_processor,
        )

        with self.assertLogs("radar.scoped_runner", level="ERROR"):
            result = runner.run_once(
                RadarTaskScope.ETF_QUOTES,
                "quote-run-failure",
                AS_OF,
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.etf_stage5_status, "failed")
        self.assertEqual(
            result.etf_stage5_reasons,
            ("etf_stage5_internal_error:RuntimeError",),
        )

    def test_runtime_uses_version_four_only_when_stage5_switch_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "stage5-runtime.sqlite"
            with sqlite3.connect(database_path) as connection:
                apply_pending_migrations(
                    connection,
                    migrations=STAGE5_RADAR_MIGRATIONS,
                    clock=lambda: APPLIED_AT,
                )
                repository = RadarRepository(
                    connection,
                    clock=lambda: FETCHED_AT,
                )
                repository.sync_etf_registry(SourceBatch(
                    meta=RadarBatchMeta(
                        radarRunId="registry-runtime",
                        batchId="registry-runtime:etf-registry",
                        source="official_exchange_etf_registry",
                        asOf=AS_OF,
                        fetchedAt=FETCHED_AT,
                        expectedCount=1,
                        returnedCount=1,
                        rowCoverage=1.0,
                        requiredFieldCoverage={
                            "symbol": 1.0,
                            "name": 1.0,
                            "source_type": 1.0,
                        },
                    ),
                    items=[
                        EtfRegistryRecord(
                            symbol="159915",
                            name="创业板ETF",
                            exchange="szse",
                            sourceType="股票ETF",
                            sourceReportDate=AS_OF.date(),
                            source="szse",
                            fetchedAt=FETCHED_AT,
                            sourceFields={},
                        ),
                    ],
                ))

            def quote_fetcher(symbols, run_id, batch_id, as_of):
                quote = EtfStage5ShadowRunnerTests.quote(symbols[0])
                return SourceBatch(
                    meta=RadarBatchMeta(
                        radarRunId=run_id,
                        batchId=batch_id,
                        source="tencent_finance",
                        asOf=as_of,
                        sourceTime=AS_OF - timedelta(seconds=20),
                        fetchedAt=FETCHED_AT,
                        expectedCount=1,
                        returnedCount=1,
                        rowCoverage=1.0,
                        requiredFieldCoverage={
                            "price": 1.0,
                            "source_time": 1.0,
                        },
                    ),
                    items=[quote],
                )

            runtime = RadarRuntime(
                database_path=database_path,
                lock_path=Path(directory) / "radar.lock",
                etf_stage5_lock_path=Path(directory) / "stage5.lock",
                settings=RadarSettings(
                    enabled=True,
                    shadow_mode=True,
                    etf_stage5_enabled=True,
                ),
                sources=ShadowSources(
                    security_master=Mock(),
                    etf_registry=Mock(),
                    quotes=quote_fetcher,
                ),
                clock=lambda: FETCHED_AT,
            )

            result = runtime.execute(
                RadarTaskScope.ETF_QUOTES,
                "runtime-etf-run",
                AS_OF,
            )

            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.etf_stage5_status, "not_ready")
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM radar_etf_feature_snapshots"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT candidate_group_count "
                        "FROM radar_etf_candidate_snapshots"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
