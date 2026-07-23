import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.contracts import (
    FeatureCompleteness,
    IndexQuoteSnapshot,
    MarketBreadthSnapshot,
    MarketFeatureSnapshot,
    MarketTurnoverSnapshot,
    UnitVerificationStatus,
)
from radar.migrations import RADAR_MIGRATIONS, apply_pending_migrations
from radar.repository import (
    RadarRepository,
    RepositoryConflictError,
    RepositoryStateError,
    RepositoryWriteError,
)


UTC = timezone.utc
UTC_PLUS_8 = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 7, 22, 10, 0, tzinfo=UTC_PLUS_8)
FETCHED_AT = AS_OF + timedelta(seconds=3)
WRITTEN_AT = AS_OF + timedelta(seconds=4)

INDEX_IDENTITIES = (
    ("sse_composite", "000001", "上证指数", "sse", "sh000001"),
    ("szse_component", "399001", "深证成指", "szse", "sz399001"),
    ("chinext", "399006", "创业板指", "szse", "sz399006"),
    ("star50", "000688", "科创50", "sse", "sh000688"),
)


class RadarMarketRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: WRITTEN_AT,
        )

    def tearDown(self):
        self.connection.close()

    @staticmethod
    def completeness(
        *,
        expected=4,
        returned=4,
        valid=4,
        field_coverage=None,
        is_complete=True,
        reasons=(),
    ):
        return FeatureCompleteness(
            expectedCount=expected,
            returnedCount=returned,
            validCount=valid,
            rowCoverage=(returned / expected if expected else 0.0),
            requiredFieldCoverage=field_coverage or {
                "price": 1.0,
                "change_percent": 1.0,
                "source_time": 1.0,
            },
            isComplete=is_complete,
            reasons=reasons,
        )

    @staticmethod
    def index_rows(*, preserve_null=False):
        rows = []
        for position, identity in enumerate(INDEX_IDENTITIES):
            price = 0.0 if position == 0 else 1000.0 + position
            change_percent = 0.0 if position == 0 else float(position)
            source_time = AS_OF
            if preserve_null and position == 1:
                price = None
                change_percent = None
                source_time = None
            rows.append(IndexQuoteSnapshot(
                indexKey=identity[0],
                symbol=identity[1],
                name=identity[2],
                exchange=identity[3],
                sourceSymbol=identity[4],
                sourceTime=source_time,
                fetchedAt=FETCHED_AT,
                price=price,
                changePercent=change_percent,
                source="tencent_finance",
            ))
        return rows

    def snapshot(
        self,
        *,
        radar_run_id="market-run",
        quote_batch_id="quotes-1",
        preserve_null=False,
    ):
        index_completeness = self.completeness()
        if preserve_null:
            index_completeness = self.completeness(
                valid=3,
                field_coverage={
                    "price": 0.75,
                    "change_percent": 0.75,
                    "source_time": 0.75,
                },
                is_complete=False,
                reasons=("required_field_coverage_below_threshold",),
            )
        breadth_completeness = self.completeness(
            expected=2,
            returned=2,
            valid=2,
            field_coverage={"change_percent": 1.0},
        )
        turnover_completeness = self.completeness(
            expected=2,
            returned=2,
            valid=2,
            field_coverage={"turnover_amount_source": 1.0},
        )
        return MarketFeatureSnapshot(
            radarRunId=radar_run_id,
            indexBatchId="indices-1",
            quoteBatchId=quote_batch_id,
            asOf=AS_OF,
            sourceTime=AS_OF,
            fetchedAt=FETCHED_AT,
            indices=self.index_rows(preserve_null=preserve_null),
            indexCompleteness=index_completeness,
            breadth=MarketBreadthSnapshot(
                advancers=0,
                decliners=1,
                flat=1,
                unavailable=0,
                completeness=breadth_completeness,
            ),
            turnover=MarketTurnoverSnapshot(
                rawValue=0.0,
                contributingCount=2,
                unitStatus=UnitVerificationStatus.UNVERIFIED,
                formalUsable=False,
                completeness=turnover_completeness,
                reasons=("turnover_unit_unverified",),
            ),
            excludedEtfCount=1,
            duplicateSymbols=("000777",),
            unknownSymbols=("999999",),
        )

    def prepare_run(self, radar_run_id="market-run", *, as_of=AS_OF):
        self.repository.start_run(
            radar_run_id,
            as_of,
            started_at=as_of,
        )

    def test_market_snapshot_is_atomic_idempotent_and_readable(self):
        self.prepare_run()
        snapshot = self.snapshot()

        self.assertEqual(
            self.repository.record_market_feature_snapshot(snapshot).as_tuple(),
            (5, 0, 0),
        )
        self.assertEqual(
            self.repository.record_market_feature_snapshot(snapshot).as_tuple(),
            (0, 5, 0),
        )

        row = self.repository.get_market_feature_row("market-run")
        latest_row = self.repository.get_latest_market_feature_row()
        self.assertEqual(row["radarRunId"], "market-run")
        self.assertEqual(latest_row["radarRunId"], "market-run")
        self.assertEqual(row["indexBatchId"], "indices-1")
        self.assertEqual(row["quoteBatchId"], "quotes-1")
        self.assertEqual(
            [item["indexKey"] for item in row["indices"]],
            [identity[0] for identity in INDEX_IDENTITIES],
        )
        self.assertEqual(len(row["indices"]), 4)
        self.assertEqual(row["duplicateSymbolCount"], 1)
        self.assertEqual(row["unknownSymbolCount"], 1)
        self.assertNotIn("duplicateSymbols", row["evidenceSummary"])
        self.assertNotIn("unknownSymbols", row["evidenceSummary"])
        self.assertIn("duplicateSymbolsSha256", row["evidenceSummary"])
        self.assertIn("unknownSymbolsSha256", row["evidenceSummary"])
        evidence_json = self.connection.execute(
            "SELECT evidence_summary_json FROM market_environment_snapshots"
        ).fetchone()[0]
        self.assertNotIn("000777", evidence_json)
        self.assertNotIn("999999", evidence_json)

    def test_market_conflict_does_not_overwrite_existing_snapshot(self):
        self.prepare_run()
        self.repository.record_market_feature_snapshot(self.snapshot())

        with self.assertRaises(RepositoryConflictError):
            self.repository.record_market_feature_snapshot(
                self.snapshot(quote_batch_id="quotes-conflict")
            )

        row = self.repository.get_market_feature_row("market-run")
        self.assertEqual(row["quoteBatchId"], "quotes-1")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_environment_snapshots"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_index_feature_snapshots"
            ).fetchone()[0],
            4,
        )

    def test_market_index_conflict_rolls_back_without_partial_change(self):
        self.prepare_run()
        self.repository.record_market_feature_snapshot(self.snapshot())
        conflicted = self.snapshot()
        conflicted.indices[1].price = 9999.0

        with self.assertRaises(RepositoryConflictError):
            self.repository.record_market_feature_snapshot(conflicted)

        row = self.repository.get_market_feature_row("market-run")
        self.assertEqual(row["indices"][1]["price"], 1001.0)
        self.assertEqual(len(row["indices"]), 4)

    def test_market_child_sqlite_failure_rolls_back_whole_snapshot(self):
        self.prepare_run("market-run-invalid")
        snapshot = self.snapshot(radar_run_id="market-run-invalid")
        snapshot.indices[-1].name = "错误指数身份"

        with self.assertRaises(RepositoryWriteError):
            self.repository.record_market_feature_snapshot(snapshot)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_environment_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_index_feature_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_market_write_requires_existing_run_and_matching_as_of(self):
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_market_feature_snapshot(self.snapshot())

        self.prepare_run("market-run", as_of=AS_OF + timedelta(seconds=1))
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_market_feature_snapshot(self.snapshot())

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_environment_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_market_read_preserves_real_zero_and_null(self):
        self.prepare_run()
        self.repository.record_market_feature_snapshot(
            self.snapshot(preserve_null=True)
        )

        row = self.repository.get_market_feature_row("market-run")
        self.assertEqual(row["turnover"]["rawValue"], 0.0)
        self.assertEqual(row["indices"][0]["price"], 0.0)
        self.assertEqual(row["indices"][0]["changePercent"], 0.0)
        self.assertIsNone(row["indices"][1]["price"])
        self.assertIsNone(row["indices"][1]["changePercent"])
        self.assertIsNone(row["indices"][1]["sourceTime"])
        self.assertEqual(
            row["indices"][1]["missingFields"],
            ("price", "change_percent", "source_time"),
        )

    def test_market_methods_reject_database_without_migration_version_three(self):
        connection = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                connection,
                migrations=RADAR_MIGRATIONS[:2],
            )
            repository = RadarRepository(connection, clock=lambda: WRITTEN_AT)
            repository.start_run("market-run", AS_OF, started_at=AS_OF)

            with self.assertRaises(RepositoryStateError):
                repository.record_market_feature_snapshot(self.snapshot())
            with self.assertRaises(RepositoryStateError):
                repository.get_market_feature_row("market-run")
        finally:
            connection.close()

    def test_market_methods_reject_incomplete_version_three_structure(self):
        self.connection.execute("DROP INDEX idx_market_environment_as_of")

        with self.assertRaises(RepositoryStateError):
            self.repository.record_market_feature_snapshot(self.snapshot())
        with self.assertRaises(RepositoryStateError):
            self.repository.get_market_feature_row("market-run")

    def test_market_snapshot_can_be_read_after_temporary_database_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "radar-market-repository.db"
            connection = sqlite3.connect(path)
            apply_pending_migrations(connection)
            repository = RadarRepository(connection, clock=lambda: WRITTEN_AT)
            repository.start_run("market-run", AS_OF, started_at=AS_OF)
            repository.record_market_feature_snapshot(self.snapshot())
            connection.close()

            reopened = sqlite3.connect(path)
            try:
                reopened_repository = RadarRepository(reopened)
                row = reopened_repository.get_market_feature_row("market-run")
                self.assertEqual(row["asOf"], AS_OF)
                self.assertEqual(len(row["indices"]), 4)
                self.assertEqual(
                    reopened.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    reopened.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
