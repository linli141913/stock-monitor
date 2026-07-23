import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.migrations import (
    INDUSTRY_STORAGE_MIGRATION,
    INITIAL_RADAR_MIGRATION,
    MARKET_ENVIRONMENT_STORAGE_MIGRATION,
    Migration,
    MigrationApplyError,
    MigrationDriftError,
    RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)


APPLIED_AT = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
AS_OF = "2026-07-22T10:00:00+08:00"
FETCHED_AT = "2026-07-22T10:00:02+08:00"


class MarketStorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")

    def tearDown(self):
        self.connection.close()

    def apply(self, migrations=RADAR_MIGRATIONS):
        return apply_pending_migrations(
            self.connection,
            migrations=migrations,
            clock=lambda: APPLIED_AT,
        )

    def insert_run(self, radar_run_id="market-run", as_of=AS_OF):
        self.connection.execute(
            "INSERT INTO radar_runs ("
            "radar_run_id, as_of, status, shadow_mode, started_at, created_at"
            ") VALUES (?, ?, 'running', 1, ?, ?)",
            (radar_run_id, as_of, as_of, as_of),
        )

    def insert_environment(self, **overrides):
        values = {
            "radar_run_id": "market-run",
            "index_batch_id": "index-batch",
            "quote_batch_id": "quote-batch",
            "as_of": AS_OF,
            "source_time": "2026-07-22T10:00:00+08:00",
            "fetched_at": FETCHED_AT,
            "index_expected_count": 4,
            "index_returned_count": 4,
            "index_valid_count": 4,
            "index_row_coverage": 1.0,
            "index_required_field_coverage_json": (
                '{"price":1.0,"change_percent":1.0,"source_time":1.0}'
            ),
            "index_is_complete": 1,
            "index_reasons_json": "[]",
            "breadth_expected_count": 2,
            "breadth_returned_count": 2,
            "breadth_valid_count": 2,
            "breadth_row_coverage": 1.0,
            "breadth_required_field_coverage_json": (
                '{"change_percent":1.0,"source_time":1.0}'
            ),
            "breadth_is_complete": 1,
            "breadth_reasons_json": "[]",
            "advancers": 1,
            "decliners": 1,
            "flat": 0,
            "unavailable": 0,
            "turnover_raw_value": 0.0,
            "turnover_contributing_count": 2,
            "turnover_unit_status": "unverified",
            "turnover_expected_count": 2,
            "turnover_returned_count": 2,
            "turnover_valid_count": 2,
            "turnover_row_coverage": 1.0,
            "turnover_required_field_coverage_json": (
                '{"turnover_amount_source":1.0,"source_time":1.0}'
            ),
            "turnover_is_complete": 1,
            "turnover_reasons_json": '["turnover_unit_unverified"]',
            "excluded_etf_count": 1,
            "duplicate_symbol_count": 0,
            "unknown_symbol_count": 0,
            "evidence_summary_json": (
                '{"sourceBatchIds":["index-batch","quote-batch"],'
                '"stockUniverseSha256":"' + "a" * 64 + '"}'
            ),
            "created_at": FETCHED_AT,
        }
        values.update(overrides)
        columns = tuple(values)
        self.connection.execute(
            "INSERT INTO market_environment_snapshots ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )

    def insert_index(self, **overrides):
        values = {
            "radar_run_id": "market-run",
            "as_of": AS_OF,
            "index_key": "sse_composite",
            "symbol": "000001",
            "name": "上证指数",
            "exchange": "sse",
            "source_symbol": "sh000001",
            "source_time": "2026-07-22T10:00:00+08:00",
            "fetched_at": FETCHED_AT,
            "price": 0.0,
            "change_percent": None,
            "source": "tencent_finance",
            "missing_fields_json": '["change_percent"]',
            "created_at": FETCHED_AT,
        }
        values.update(overrides)
        columns = tuple(values)
        self.connection.execute(
            "INSERT INTO market_index_feature_snapshots ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )

    def test_version_three_metadata_and_earlier_migrations_are_frozen(self):
        self.assertEqual(MARKET_ENVIRONMENT_STORAGE_MIGRATION.version, 3)
        self.assertEqual(
            MARKET_ENVIRONMENT_STORAGE_MIGRATION.name,
            "market_environment_and_index_features",
        )
        self.assertEqual(
            MARKET_ENVIRONMENT_STORAGE_MIGRATION.checksum,
            "9168f50f76eb83ee68f32dc2e256a071ac7ee88a02b5ba3df8d024cbc0609877",
        )
        self.assertEqual(INITIAL_RADAR_MIGRATION.name, "initial_radar_foundation")
        self.assertEqual(
            INDUSTRY_STORAGE_MIGRATION.name,
            "industry_classification_and_sector_features",
        )
        self.assertEqual(
            INITIAL_RADAR_MIGRATION.checksum,
            "04eb31d34ff45c00a9feea86b5c80b559e8508dc24c694562893c3bc7d456b1c",
        )
        self.assertEqual(
            INDUSTRY_STORAGE_MIGRATION.checksum,
            "fb24ef22bafdc30662204c24ec64d876b817e7acb48c0b7045744d13b4599b25",
        )

    def test_version_three_creates_only_aggregate_market_tables(self):
        self.assertEqual(self.apply(), [1, 2, 3])
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("market_environment_snapshots", tables)
        self.assertIn("market_index_feature_snapshots", tables)
        self.assertNotIn("market_security_quote_snapshots", tables)
        self.assertNotIn("market_state_history", tables)

        forbidden_fragments = (
            "market_state",
            "market_score",
            "sector_score",
            "trading_signal",
        )
        columns = {
            row[1]
            for table in (
                "market_environment_snapshots",
                "market_index_feature_snapshots",
            )
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        for fragment in forbidden_fragments:
            self.assertFalse(any(fragment in column for column in columns))

    def test_version_three_required_objects_participate_in_drift_validation(self):
        self.apply()
        self.connection.execute("DROP INDEX idx_market_environment_as_of")
        self.connection.commit()

        with self.assertRaises(MigrationDriftError):
            validate_applied_migrations(self.connection)

    def test_version_three_is_idempotent_and_drift_is_rejected(self):
        self.assertEqual(self.apply(), [1, 2, 3])
        self.assertEqual(self.apply(), [])
        row = self.connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations WHERE version=3"
        ).fetchone()
        self.assertEqual(
            row,
            (
                MARKET_ENVIRONMENT_STORAGE_MIGRATION.name,
                MARKET_ENVIRONMENT_STORAGE_MIGRATION.checksum,
            ),
        )

        self.connection.execute(
            "UPDATE radar_schema_migrations SET checksum='tampered' WHERE version=3"
        )
        self.connection.commit()
        with self.assertRaises(MigrationDriftError):
            self.apply()

    def test_version_two_database_upgrades_without_mutating_earlier_ledger(self):
        self.assertEqual(
            self.apply((INITIAL_RADAR_MIGRATION, INDUSTRY_STORAGE_MIGRATION)),
            [1, 2],
        )
        before = self.connection.execute(
            "SELECT version, name, checksum FROM radar_schema_migrations "
            "ORDER BY version"
        ).fetchall()

        self.assertEqual(self.apply(), [3])

        after = self.connection.execute(
            "SELECT version, name, checksum FROM radar_schema_migrations "
            "WHERE version <= 2 ORDER BY version"
        ).fetchall()
        self.assertEqual(after, before)
        self.assertEqual(validate_applied_migrations(self.connection), [1, 2, 3])

    def test_version_three_failure_rolls_back_all_partial_objects(self):
        self.assertEqual(
            self.apply((INITIAL_RADAR_MIGRATION, INDUSTRY_STORAGE_MIGRATION)),
            [1, 2],
        )
        broken = Migration(
            version=3,
            name="broken_market_storage",
            statements=(
                "CREATE TABLE market_partial_table (id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ),
        )

        with self.assertRaises(MigrationApplyError):
            self.apply((INITIAL_RADAR_MIGRATION, INDUSTRY_STORAGE_MIGRATION, broken))

        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("market_partial_table", tables)
        self.assertEqual(
            self.connection.execute(
                "SELECT version FROM radar_schema_migrations ORDER BY version"
            ).fetchall(),
            [(1,), (2,)],
        )

    def test_environment_requires_matching_run_and_is_unique_per_run(self):
        self.apply()
        self.insert_run()
        self.insert_environment()

        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_environment(index_batch_id="duplicate-index-batch")

        self.insert_run(radar_run_id="other-run")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_environment(
                radar_run_id="other-run",
                as_of="2026-07-22T10:00:01+08:00",
            )

    def test_environment_enforces_counts_coverage_time_and_json(self):
        self.apply()
        self.insert_run()
        invalid_values = (
            {"index_expected_count": 5},
            {"breadth_returned_count": 1, "breadth_row_coverage": 1.0},
            {"advancers": 2},
            {"turnover_valid_count": 1},
            {
                "turnover_returned_count": 1,
                "turnover_valid_count": 1,
                "turnover_contributing_count": 1,
                "turnover_row_coverage": 0.5,
            },
            {"fetched_at": "2026-07-22T09:59:59+08:00"},
            {"source_time": "2026-07-22T10:00:03+08:00"},
            {"index_reasons_json": "not-json"},
            {"index_reasons_json": "{}"},
            {"evidence_summary_json": "[]"},
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.insert_environment(**overrides)
                finally:
                    self.connection.execute(
                        "DELETE FROM market_environment_snapshots"
                    )

    def test_turnover_null_and_real_zero_are_distinct(self):
        self.apply()
        self.insert_run()
        self.insert_environment(turnover_raw_value=0.0)
        stored_zero = self.connection.execute(
            "SELECT turnover_raw_value FROM market_environment_snapshots"
        ).fetchone()[0]
        self.assertEqual(stored_zero, 0.0)

        self.connection.execute("DELETE FROM market_environment_snapshots")
        self.insert_environment(
            turnover_raw_value=None,
            turnover_contributing_count=0,
            turnover_valid_count=0,
            turnover_is_complete=0,
        )
        stored_null = self.connection.execute(
            "SELECT turnover_raw_value FROM market_environment_snapshots"
        ).fetchone()[0]
        self.assertIsNone(stored_null)

    def test_index_rows_enforce_parent_identity_uniqueness_and_time(self):
        self.apply()
        self.insert_run()
        self.insert_environment()
        self.insert_index()

        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_index()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_index(
                index_key="szse_component",
                symbol="399001",
                name="深证成指",
                exchange="szse",
                source_symbol="sz399001",
                source_time="2026-07-22T10:00:03+08:00",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_index(
                index_key="star50",
                symbol="000688",
                name="科创50",
                exchange="szse",
                source_symbol="sz000688",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_index(
                radar_run_id="missing-run",
                index_key="star50",
                symbol="000688",
                name="科创50",
                exchange="sse",
                source_symbol="sh000688",
            )

    def test_index_rows_preserve_real_zero_and_null(self):
        self.apply()
        self.insert_run()
        self.insert_environment()
        self.insert_index(price=0.0, change_percent=None)

        row = self.connection.execute(
            "SELECT price, change_percent FROM market_index_feature_snapshots"
        ).fetchone()
        self.assertEqual(row, (0.0, None))

    def test_temporary_file_reopens_with_version_three_intact(self):
        self.connection.close()
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "radar-market.db"
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(
                    apply_pending_migrations(
                        connection,
                        clock=lambda: APPLIED_AT,
                    ),
                    [1, 2, 3],
                )
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(validate_applied_migrations(connection), [1, 2, 3])
                self.assertEqual(
                    apply_pending_migrations(
                        connection,
                        clock=lambda: APPLIED_AT,
                    ),
                    [],
                )
        self.connection = sqlite3.connect(":memory:")


if __name__ == "__main__":
    unittest.main()
