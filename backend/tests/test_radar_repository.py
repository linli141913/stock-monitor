import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.contracts import (
    EtfRegistryRecord,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceHealthResult,
    SourceIssue,
    SourceStatus,
)
from radar.migrations import apply_pending_migrations
from radar.repository import (
    HistoryAsOfError,
    RadarRepository,
    RepositoryConflictError,
    RepositoryWriteError,
)


UTC = timezone.utc
UTC_PLUS_8 = timezone(timedelta(hours=8))
AS_OF_1 = datetime(2026, 7, 18, 9, 30, tzinfo=UTC_PLUS_8)
AS_OF_2 = datetime(2026, 7, 19, 9, 30, tzinfo=UTC_PLUS_8)
FETCHED_1 = datetime(2026, 7, 18, 9, 31, tzinfo=UTC_PLUS_8)
FETCHED_2 = datetime(2026, 7, 19, 9, 31, tzinfo=UTC_PLUS_8)
WRITTEN_AT = datetime(2026, 7, 18, 1, 32, tzinfo=UTC)


class RadarRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: WRITTEN_AT,
        )

    def tearDown(self):
        self.connection.close()

    def start_run(self, radar_run_id="run-1"):
        return self.repository.start_run(
            radar_run_id,
            AS_OF_1,
            started_at=FETCHED_1,
            shadow_mode=True,
        )

    def security_record(
        self,
        *,
        symbol="000001",
        name="平安银行",
        exchange="szse",
        fetched_at=FETCHED_1,
        source_fields=None,
    ):
        return SecurityMasterRecord(
            symbol=symbol,
            name=name,
            exchange=exchange,
            board="主板",
            listingDate="1991-04-03",
            totalShares=19_405_918_198.0,
            circulatingShares=19_405_546_950.0,
            sourceIndustry="银行",
            sourceReportDate="2026-07-17",
            source=exchange,
            fetchedAt=fetched_at,
            sourceFields=source_fields or {"证券代码": symbol, "证券简称": name},
        )

    def etf_record(
        self,
        *,
        name="芯片ETF",
        fetched_at=FETCHED_1,
        fund_shares=0.0,
    ):
        return EtfRegistryRecord(
            symbol="512760",
            name=name,
            exchange="sse",
            sourceType="股票型",
            investmentType=None,
            listingDate=None,
            fundShares=fund_shares,
            manager=None,
            sponsor=None,
            custodian=None,
            nav=None,
            sourceReportDate="2026-07-17",
            source="sse",
            fetchedAt=fetched_at,
            sourceFields={"基金代码": "512760", "基金简称": name},
        )

    def batch(self, *, as_of, items, source="security_master"):
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="run-1",
                batchId=f"{source}-{as_of.date().isoformat()}",
                source=source,
                asOf=as_of,
                sourceTime=None,
                fetchedAt=max(
                    (item.fetched_at for item in items),
                    default=as_of,
                ),
                expectedCount=len(items),
                returnedCount=len(items),
                rowCoverage=1.0 if items else 0.0,
                requiredFieldCoverage={},
                issues=[],
            ),
            items=items,
        )

    def test_run_start_and_completion_are_idempotent_and_keep_real_zero(self):
        self.assertTrue(self.start_run())
        self.assertFalse(self.start_run())

        self.assertTrue(self.repository.complete_run(
            "run-1",
            status="succeeded",
            completed_at=FETCHED_2,
            expected_stock_count=0,
            returned_stock_count=0,
            expected_etf_count=0,
            returned_etf_count=0,
        ))
        self.assertFalse(self.repository.complete_run(
            "run-1",
            status="succeeded",
            completed_at=FETCHED_2,
            expected_stock_count=0,
            returned_stock_count=0,
            expected_etf_count=0,
            returned_etf_count=0,
        ))

        row = self.connection.execute(
            "SELECT status, stock_coverage, etf_coverage, as_of, completed_at "
            "FROM radar_runs WHERE radar_run_id='run-1'"
        ).fetchone()
        self.assertEqual(row[0:3], ("succeeded", 0.0, 0.0))
        self.assertEqual(row[3], "2026-07-18T01:30:00.000000+00:00")
        self.assertEqual(row[4], "2026-07-19T01:31:00.000000+00:00")

        with self.assertRaises(RepositoryConflictError):
            self.repository.complete_run(
                "run-1",
                status="failed",
                completed_at=FETCHED_2,
                error_code="late_override",
            )

    def test_source_health_write_is_idempotent_and_preserves_reasons(self):
        self.start_run()
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="quotes-1",
            source="tencent_finance",
            asOf=AS_OF_1,
            sourceTime=None,
            fetchedAt=FETCHED_1,
            expectedCount=0,
            returnedCount=0,
            rowCoverage=0.0,
            requiredFieldCoverage={"price": 0.0},
            issues=[SourceIssue(code="empty", message="来源返回空数据")],
        )
        health = SourceHealthResult(
            status=SourceStatus.FAILED,
            allowsNewState=False,
            reasons=("source_returned_no_rows",),
            ageSeconds=None,
        )

        diagnostics = {
            "gatePassed": False,
            "gateReasons": ["quote_item_source_time_stale"],
            "quoteItemTimeSummary": {
                "missingCount": 0,
                "staleCount": 2,
                "futureCount": 0,
            },
        }
        self.assertTrue(self.repository.record_source_status(
            meta,
            health,
            diagnostics=diagnostics,
        ))
        self.assertFalse(self.repository.record_source_status(
            meta,
            health,
            diagnostics=diagnostics,
        ))

        row = self.connection.execute(
            "SELECT expected_count, returned_count, row_coverage, issues_json "
            "FROM radar_source_status"
        ).fetchone()
        self.assertEqual(row[0:3], (0, 0, 0.0))
        payload = json.loads(row[3])
        self.assertFalse(payload["allowsNewState"])
        self.assertEqual(payload["healthReasons"], ["source_returned_no_rows"])
        self.assertEqual(payload["sourceIssues"][0]["code"], "empty")
        self.assertEqual(payload["diagnostics"], diagnostics)

        changed_health = health.model_copy(update={"status": SourceStatus.DEGRADED})
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_source_status(
                meta,
                changed_health,
                diagnostics=diagnostics,
            )
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_source_status(
                meta,
                health,
                diagnostics={
                    **diagnostics,
                    "gateReasons": ["different_reason"],
                },
            )

    def test_source_status_rejects_batch_as_of_different_from_run(self):
        self.start_run()
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="quotes-wrong-as-of",
            source="tencent_finance",
            asOf=AS_OF_2,
            sourceTime=None,
            fetchedAt=FETCHED_2,
            expectedCount=1,
            returnedCount=1,
            rowCoverage=1.0,
            requiredFieldCoverage={"price": 1.0},
            issues=[],
        )
        health = SourceHealthResult(
            status=SourceStatus.HEALTHY,
            allowsNewState=True,
            reasons=(),
            ageSeconds=None,
        )

        with self.assertRaises(RepositoryConflictError):
            self.repository.record_source_status(meta, health)

        count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_source_status"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_unchanged_security_content_does_not_create_history_version(self):
        first = self.batch(as_of=AS_OF_1, items=[self.security_record()])
        repeated = self.batch(
            as_of=AS_OF_2,
            items=[self.security_record(fetched_at=FETCHED_2)],
        )

        self.assertEqual(
            self.repository.sync_security_master(first).as_tuple(),
            (1, 0, 0),
        )
        self.assertEqual(
            self.repository.sync_security_master(repeated).as_tuple(),
            (0, 1, 0),
        )
        rows = self.connection.execute(
            "SELECT effective_from, effective_to FROM security_master_history"
        ).fetchall()
        self.assertEqual(rows, [("2026-07-18T01:30:00.000000+00:00", None)])

    def test_changed_security_closes_old_version_and_inserts_new_version(self):
        first = self.batch(as_of=AS_OF_1, items=[self.security_record()])
        changed = self.batch(
            as_of=AS_OF_2,
            items=[self.security_record(
                name="平安银行股份",
                fetched_at=FETCHED_2,
                source_fields={"证券代码": "000001", "证券简称": "平安银行股份"},
            )],
        )
        self.repository.sync_security_master(first)

        result = self.repository.sync_security_master(changed)

        self.assertEqual(result.as_tuple(), (1, 0, 1))
        rows = self.connection.execute(
            "SELECT name, effective_from, effective_to "
            "FROM security_master_history ORDER BY effective_from"
        ).fetchall()
        self.assertEqual(rows, [
            (
                "平安银行",
                "2026-07-18T01:30:00.000000+00:00",
                "2026-07-19T01:30:00.000000+00:00",
            ),
            (
                "平安银行股份",
                "2026-07-19T01:30:00.000000+00:00",
                None,
            ),
        ])

    def test_as_of_symbol_reads_exclude_future_versions_and_deduplicate_sources(self):
        first = self.batch(as_of=AS_OF_1, items=[self.security_record()])
        changed = self.batch(
            as_of=AS_OF_2,
            items=[
                self.security_record(
                    name="平安银行股份",
                    fetched_at=FETCHED_2,
                    source_fields={
                        "证券代码": "000001",
                        "证券简称": "平安银行股份",
                    },
                ),
                self.security_record(
                    symbol="000002",
                    name="万科A",
                    fetched_at=FETCHED_2,
                ),
            ],
        )
        self.repository.sync_security_master(first)
        self.repository.sync_security_master(changed)
        self.repository.sync_etf_registry(
            self.batch(
                as_of=AS_OF_2,
                items=[self.etf_record(fetched_at=FETCHED_2)],
                source="etf_registry",
            )
        )

        self.assertEqual(
            self.repository.list_security_symbols(AS_OF_1),
            ("000001",),
        )
        self.assertEqual(
            self.repository.list_security_symbols(AS_OF_2),
            ("000001", "000002"),
        )
        self.assertEqual(self.repository.list_etf_symbols(AS_OF_1), ())
        self.assertEqual(
            self.repository.list_etf_symbols(AS_OF_2),
            ("512760",),
        )
        master_records = self.repository.list_security_master_records(AS_OF_2)
        self.assertEqual(
            tuple((record.symbol, record.name) for record in master_records),
            (("000001", "平安银行股份"), ("000002", "万科A")),
        )
        self.assertEqual(
            master_records[0].source_fields,
            {"证券代码": "000001", "证券简称": "平安银行股份"},
        )

    def test_latest_healthy_source_as_of_ignores_degraded_rows(self):
        self.start_run("run-healthy")
        healthy_meta = RadarBatchMeta(
            radarRunId="run-healthy",
            batchId="security-healthy",
            source="official_exchange_security_master",
            asOf=AS_OF_1,
            fetchedAt=FETCHED_1,
            expectedCount=1,
            returnedCount=1,
            rowCoverage=1.0,
            requiredFieldCoverage={"symbol": 1.0},
            issues=[],
        )
        self.repository.record_source_status(
            healthy_meta,
            SourceHealthResult(
                status=SourceStatus.HEALTHY,
                allowsNewState=True,
                reasons=(),
            ),
        )
        self.repository.complete_run(
            "run-healthy",
            status="succeeded",
            completed_at=FETCHED_1,
        )

        self.repository.start_run(
            "run-degraded",
            AS_OF_2,
            started_at=FETCHED_2,
            shadow_mode=True,
        )
        degraded_meta = healthy_meta.model_copy(update={
            "radar_run_id": "run-degraded",
            "batch_id": "security-degraded",
            "as_of": AS_OF_2,
            "fetched_at": FETCHED_2,
        })
        self.repository.record_source_status(
            degraded_meta,
            SourceHealthResult(
                status=SourceStatus.DEGRADED,
                allowsNewState=False,
                reasons=("coverage_below_threshold",),
            ),
        )

        self.assertEqual(
            self.repository.latest_healthy_source_as_of(
                "official_exchange_security_master"
            ),
            AS_OF_1.astimezone(UTC),
        )

    def test_changed_history_rejects_non_forward_as_of(self):
        latest = self.batch(as_of=AS_OF_2, items=[self.security_record()])
        older_change = self.batch(
            as_of=AS_OF_1,
            items=[self.security_record(name="倒灌旧名称")],
        )
        self.repository.sync_security_master(latest)

        with self.assertRaises(HistoryAsOfError):
            self.repository.sync_security_master(older_change)

        row = self.connection.execute(
            "SELECT name, effective_to FROM security_master_history"
        ).fetchone()
        self.assertEqual(row, ("平安银行", None))

    def test_history_batch_rolls_back_all_changes_when_one_row_fails(self):
        self.repository.sync_security_master(
            self.batch(as_of=AS_OF_1, items=[self.security_record()])
        )
        changed = self.security_record(name="新名称", fetched_at=FETCHED_2)
        invalid = self.security_record(
            symbol="000002",
            name="非法交易所记录",
            exchange="invalid",
            fetched_at=FETCHED_2,
        )

        with self.assertRaises(RepositoryWriteError):
            self.repository.sync_security_master(
                self.batch(as_of=AS_OF_2, items=[changed, invalid])
            )

        rows = self.connection.execute(
            "SELECT symbol, name, effective_to FROM security_master_history"
        ).fetchall()
        self.assertEqual(rows, [("000001", "平安银行", None)])

    def test_missing_security_in_later_batch_does_not_close_current_version(self):
        self.repository.sync_security_master(
            self.batch(as_of=AS_OF_1, items=[self.security_record()])
        )

        result = self.repository.sync_security_master(
            self.batch(as_of=AS_OF_2, items=[])
        )

        self.assertEqual(result.as_tuple(), (0, 0, 0))
        effective_to = self.connection.execute(
            "SELECT effective_to FROM security_master_history"
        ).fetchone()[0]
        self.assertIsNone(effective_to)

    def test_etf_history_preserves_zero_and_null_then_versions_changes(self):
        first = self.batch(
            as_of=AS_OF_1,
            items=[self.etf_record()],
            source="etf_registry",
        )
        repeated = self.batch(
            as_of=AS_OF_2,
            items=[self.etf_record(fetched_at=FETCHED_2)],
            source="etf_registry",
        )
        self.assertEqual(
            self.repository.sync_etf_registry(first).as_tuple(),
            (1, 0, 0),
        )
        self.assertEqual(
            self.repository.sync_etf_registry(repeated).as_tuple(),
            (0, 1, 0),
        )

        changed_as_of = AS_OF_2 + timedelta(days=1)
        changed = self.batch(
            as_of=changed_as_of,
            items=[self.etf_record(name="芯片产业ETF", fetched_at=FETCHED_2)],
            source="etf_registry",
        )
        self.assertEqual(
            self.repository.sync_etf_registry(changed).as_tuple(),
            (1, 0, 1),
        )
        rows = self.connection.execute(
            "SELECT name, fund_shares, nav, effective_to "
            "FROM etf_product_registry ORDER BY effective_from"
        ).fetchall()
        self.assertEqual(rows[0][0:3], ("芯片ETF", 0.0, None))
        self.assertIsNotNone(rows[0][3])
        self.assertEqual(rows[1], ("芯片产业ETF", 0.0, None, None))

    def test_duplicate_identity_is_rejected_before_any_history_write(self):
        duplicate_batch = self.batch(
            as_of=AS_OF_1,
            items=[self.security_record(), self.security_record(name="重复")],
        )

        with self.assertRaises(RepositoryConflictError):
            self.repository.sync_security_master(duplicate_batch)

        count = self.connection.execute(
            "SELECT COUNT(*) FROM security_master_history"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_repository_works_after_reopening_explicit_temporary_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "radar-stage-2b2.db"
            connection = sqlite3.connect(db_path)
            apply_pending_migrations(connection)
            repository = RadarRepository(connection, clock=lambda: WRITTEN_AT)
            repository.start_run("file-run", AS_OF_1, started_at=FETCHED_1)
            connection.close()

            reopened = sqlite3.connect(db_path)
            try:
                status = reopened.execute(
                    "SELECT status FROM radar_runs WHERE radar_run_id='file-run'"
                ).fetchone()[0]
                self.assertEqual(status, "running")
                self.assertEqual(
                    reopened.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
