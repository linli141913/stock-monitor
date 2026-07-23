import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from radar.config import RadarSettings
from radar.contracts import (
    EtfRegistryRecord,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceIssue,
    SourceStatus,
)
from radar.migrations import apply_pending_migrations
from radar.repository import RadarRepository
from radar.shadow_runner import (
    OneShotShadowRunner,
    ShadowRunAlreadyExistsError,
    ShadowRunDisabledError,
    ShadowRunExecutionError,
    ShadowRunInProgressError,
    ShadowSources,
    build_default_shadow_sources,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)
SOURCE_TIME = AS_OF - timedelta(seconds=30)
FETCHED_AT = AS_OF + timedelta(seconds=2)


class ShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: FETCHED_AT,
        )

    def tearDown(self):
        self.connection.close()

    def security_batch(self, **meta_overrides):
        item = SecurityMasterRecord(
            symbol="000001",
            name="平安银行",
            exchange="szse",
            board="主板",
            listingDate="1991-04-03",
            sourceReportDate="2026-07-18",
            source="szse",
            fetchedAt=FETCHED_AT,
            sourceFields={"A股代码": "000001", "A股简称": "平安银行"},
        )
        meta = {
            "radarRunId": "run-1",
            "batchId": "run-1:security-master",
            "source": "official_exchange_security_master",
            "asOf": AS_OF,
            "sourceTime": None,
            "fetchedAt": FETCHED_AT,
            "expectedCount": 1,
            "returnedCount": 1,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {
                "symbol": 1.0,
                "name": 1.0,
                "listing_date": 1.0,
            },
            "issues": [],
        }
        meta.update(meta_overrides)
        return SourceBatch(
            meta=RadarBatchMeta(**meta),
            items=[item],
        )

    def etf_batch(self, **meta_overrides):
        item = EtfRegistryRecord(
            symbol="510300",
            name="沪深300ETF",
            exchange="sse",
            sourceType="股票ETF",
            sourceReportDate="2026-07-18",
            source="sse",
            fetchedAt=FETCHED_AT,
            sourceFields={"基金代码": "510300", "基金简称": "沪深300ETF"},
        )
        meta = {
            "radarRunId": "run-1",
            "batchId": "run-1:etf-registry",
            "source": "official_exchange_etf_registry",
            "asOf": AS_OF,
            "sourceTime": None,
            "fetchedAt": FETCHED_AT,
            "expectedCount": 1,
            "returnedCount": 1,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {
                "symbol": 1.0,
                "name": 1.0,
                "source_type": 1.0,
            },
            "issues": [],
        }
        meta.update(meta_overrides)
        return SourceBatch(
            meta=RadarBatchMeta(**meta),
            items=[item],
        )

    def quote_batch(self, symbols, **meta_overrides):
        items = [
            QuoteSnapshot(
                symbol=symbol,
                name=f"证券{symbol}",
                sourceTime=SOURCE_TIME,
                fetchedAt=FETCHED_AT,
                price=10.0,
                changePercent=1.0,
                turnoverAmountSource=100.0,
                turnoverRatePercent=2.0,
                volumeRatio=1.1,
                marketCapSource=1000.0,
            )
            for symbol in symbols
        ]
        meta = {
            "radarRunId": "run-1",
            "batchId": "run-1:quotes",
            "source": "tencent_finance",
            "asOf": AS_OF,
            "sourceTime": SOURCE_TIME,
            "fetchedAt": FETCHED_AT,
            "expectedCount": len(symbols),
            "returnedCount": len(symbols),
            "rowCoverage": 1.0 if symbols else 0.0,
            "requiredFieldCoverage": {
                "price": 1.0 if symbols else 0.0,
                "source_time": 1.0 if symbols else 0.0,
            },
            "issues": [],
        }
        meta.update(meta_overrides)
        return SourceBatch(
            meta=RadarBatchMeta(**meta),
            items=items,
        )

    def healthy_sources(self, calls=None):
        calls = calls if calls is not None else []

        def security(run_id, batch_id, as_of):
            calls.append(("security", run_id, batch_id, as_of))
            return self.security_batch(
                radarRunId=run_id,
                batchId=batch_id,
                asOf=as_of,
            )

        def etf(run_id, batch_id, as_of):
            calls.append(("etf", run_id, batch_id, as_of))
            return self.etf_batch(
                radarRunId=run_id,
                batchId=batch_id,
                asOf=as_of,
            )

        def quotes(symbols, run_id, batch_id, as_of):
            symbols = tuple(symbols)
            calls.append(("quotes", symbols, run_id, batch_id, as_of))
            return self.quote_batch(
                symbols,
                radarRunId=run_id,
                batchId=batch_id,
                asOf=as_of,
            )

        return ShadowSources(
            security_master=security,
            etf_registry=etf,
            quotes=quotes,
        )

    def runner(self, sources, **overrides):
        values = {
            "repository": self.repository,
            "settings": RadarSettings(enabled=True, shadow_mode=True),
            "sources": sources,
            "clock": lambda: FETCHED_AT,
        }
        values.update(overrides)
        return OneShotShadowRunner(**values)

    def test_disabled_flags_block_all_writes_and_source_calls(self):
        source_call = Mock()
        sources = ShadowSources(
            security_master=source_call,
            etf_registry=source_call,
            quotes=source_call,
        )

        with self.assertRaises(ShadowRunDisabledError):
            self.runner(
                sources,
                settings=RadarSettings(enabled=False, shadow_mode=True),
            ).run_once("run-1", AS_OF)
        with self.assertRaises(ShadowRunDisabledError):
            self.runner(
                sources,
                settings=RadarSettings(enabled=True, shadow_mode=False),
            ).run_once("run-2", AS_OF)

        source_call.assert_not_called()
        count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_runs"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    @patch("radar.shadow_runner.fetch_etf_registry")
    def test_default_etf_source_uses_previous_friday_on_weekend(self, fetcher):
        fetcher.return_value = self.etf_batch()
        settings = RadarSettings(enabled=True, shadow_mode=True)
        sources = build_default_shadow_sources(
            settings,
            clock=lambda: FETCHED_AT,
        )
        saturday = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)

        sources.etf_registry("run-1", "etf-1", saturday)

        self.assertEqual(fetcher.call_args.kwargs["snapshot_date"], date(2026, 7, 17))

    def test_healthy_one_shot_run_persists_histories_and_source_health(self):
        calls = []
        result = self.runner(self.healthy_sources(calls)).run_once(
            "run-1",
            AS_OF,
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.security_health.status, SourceStatus.HEALTHY)
        self.assertEqual(result.etf_health.status, SourceStatus.HEALTHY)
        self.assertEqual(result.quote_health.status, SourceStatus.HEALTHY)
        self.assertEqual(result.security_history.as_tuple(), (1, 0, 0))
        self.assertEqual(result.etf_history.as_tuple(), (1, 0, 0))
        self.assertEqual(calls[2][1], ("000001", "510300"))

        run = self.connection.execute(
            "SELECT status, shadow_mode, expected_stock_count, "
            "returned_stock_count, expected_etf_count, returned_etf_count "
            "FROM radar_runs WHERE radar_run_id='run-1'"
        ).fetchone()
        self.assertEqual(run, ("succeeded", 1, 1, 1, 1, 1))
        source_count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_source_status"
        ).fetchone()[0]
        self.assertEqual(source_count, 3)

    def test_degraded_registry_is_recorded_but_not_written_to_history(self):
        sources = self.healthy_sources()
        degraded_security = self.security_batch(
            expectedCount=None,
            rowCoverage=None,
            issues=[SourceIssue(
                code="source_request_failed",
                source="sse",
                message="上交所请求失败",
            )],
        )
        sources = ShadowSources(
            security_master=lambda *_args: degraded_security,
            etf_registry=sources.etf_registry,
            quotes=sources.quotes,
        )

        result = self.runner(sources).run_once("run-1", AS_OF)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.security_health.status, SourceStatus.DEGRADED)
        self.assertFalse(result.security_health.allows_new_state)
        self.assertEqual(result.security_history.as_tuple(), (0, 0, 0))
        security_rows = self.connection.execute(
            "SELECT COUNT(*) FROM security_master_history"
        ).fetchone()[0]
        self.assertEqual(security_rows, 0)
        etf_rows = self.connection.execute(
            "SELECT COUNT(*) FROM etf_product_registry"
        ).fetchone()[0]
        self.assertEqual(etf_rows, 1)

    def test_stale_quotes_degrade_run_without_changing_registry_histories(self):
        healthy = self.healthy_sources()

        def stale_quotes(symbols, run_id, batch_id, as_of):
            return self.quote_batch(
                symbols,
                radarRunId=run_id,
                batchId=batch_id,
                asOf=as_of,
                sourceTime=AS_OF - timedelta(minutes=5),
            )

        sources = ShadowSources(
            security_master=healthy.security_master,
            etf_registry=healthy.etf_registry,
            quotes=stale_quotes,
        )

        result = self.runner(sources).run_once("run-1", AS_OF)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.quote_health.status, SourceStatus.STALE)
        self.assertIn("source_time_stale", result.quote_health.reasons)
        self.assertEqual(result.security_history.inserted, 1)
        self.assertEqual(result.etf_history.inserted, 1)

    def test_quote_future_skew_is_measured_against_frozen_as_of(self):
        healthy = self.healthy_sources()

        def future_quotes(symbols, run_id, batch_id, as_of):
            return self.quote_batch(
                symbols,
                radarRunId=run_id,
                batchId=batch_id,
                asOf=as_of,
                sourceTime=AS_OF + timedelta(seconds=10),
            )

        sources = ShadowSources(
            security_master=healthy.security_master,
            etf_registry=healthy.etf_registry,
            quotes=future_quotes,
        )
        runner = self.runner(
            sources,
            clock=lambda: AS_OF + timedelta(seconds=20),
        )

        result = runner.run_once("run-1", AS_OF)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.quote_health.status, SourceStatus.DEGRADED)
        self.assertIn("source_time_in_future", result.quote_health.reasons)

    def test_source_exception_becomes_failed_health_without_leaking_message(self):
        healthy = self.healthy_sources()

        def failed_quotes(*_args):
            raise RuntimeError("secret upstream detail")

        sources = ShadowSources(
            security_master=healthy.security_master,
            etf_registry=healthy.etf_registry,
            quotes=failed_quotes,
        )

        result = self.runner(sources).run_once("run-1", AS_OF)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.quote_health.status, SourceStatus.FAILED)
        row = self.connection.execute(
            "SELECT issues_json FROM radar_source_status "
            "WHERE source='tencent_finance'"
        ).fetchone()
        payload = json.loads(row[0])
        message = payload["sourceIssues"][0]["message"]
        self.assertIn("RuntimeError", message)
        self.assertNotIn("secret upstream detail", message)

    def test_invalid_batch_identity_marks_run_failed_and_stops_pipeline(self):
        later_source = Mock()
        invalid = self.security_batch(radarRunId="wrong-run")
        sources = ShadowSources(
            security_master=lambda *_args: invalid,
            etf_registry=later_source,
            quotes=later_source,
        )

        with self.assertRaises(ShadowRunExecutionError):
            self.runner(sources).run_once("run-1", AS_OF)

        later_source.assert_not_called()
        run = self.connection.execute(
            "SELECT status, error_code FROM radar_runs WHERE radar_run_id='run-1'"
        ).fetchone()
        self.assertEqual(run[0], "failed")
        self.assertEqual(run[1], "shadow_runner_batch_identity")

    def test_existing_run_id_is_rejected_before_sources_repeat(self):
        calls = []
        runner = self.runner(self.healthy_sources(calls))
        runner.run_once("run-1", AS_OF)
        first_call_count = len(calls)

        with self.assertRaises(ShadowRunAlreadyExistsError):
            runner.run_once("run-1", AS_OF)

        self.assertEqual(len(calls), first_call_count)
        source_count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_source_status"
        ).fetchone()[0]
        self.assertEqual(source_count, 3)

    def test_nonblocking_lock_rejects_reentry_before_database_write(self):
        lock = Mock()
        lock.acquire.return_value = False
        runner = self.runner(self.healthy_sources(), run_lock=lock)

        with self.assertRaises(ShadowRunInProgressError):
            runner.run_once("run-1", AS_OF)

        lock.acquire.assert_called_once_with(blocking=False)
        lock.release.assert_not_called()
        count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_runs"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_one_shot_result_survives_explicit_temporary_database_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "radar-shadow.db"
            connection = sqlite3.connect(db_path)
            apply_pending_migrations(connection)
            repository = RadarRepository(
                connection,
                clock=lambda: FETCHED_AT,
            )
            runner = OneShotShadowRunner(
                repository=repository,
                settings=RadarSettings(enabled=True, shadow_mode=True),
                sources=self.healthy_sources(),
                clock=lambda: FETCHED_AT,
            )
            runner.run_once("run-1", AS_OF)
            connection.close()

            reopened = sqlite3.connect(db_path)
            try:
                status = reopened.execute(
                    "SELECT status FROM radar_runs WHERE radar_run_id='run-1'"
                ).fetchone()[0]
                self.assertEqual(status, "succeeded")
                self.assertEqual(
                    reopened.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
