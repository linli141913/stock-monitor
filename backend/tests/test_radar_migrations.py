import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import backup_database
from radar.migrations import (
    INDUSTRY_STORAGE_MIGRATION,
    INITIAL_RADAR_MIGRATION,
    Migration,
    MigrationApplyError,
    MigrationDriftError,
    RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)


APPLIED_AT = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)


class RadarMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")

    def tearDown(self):
        self.connection.close()

    def apply(self, migrations=None):
        kwargs = {"clock": lambda: APPLIED_AT}
        if migrations is not None:
            kwargs["migrations"] = migrations
        return apply_pending_migrations(self.connection, **kwargs)

    def table_names(self):
        return {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    def test_initial_migration_creates_only_first_foundation_tables(self):
        applied = self.apply((INITIAL_RADAR_MIGRATION,))

        self.assertEqual(applied, [1])
        self.assertTrue({
            "radar_schema_migrations",
            "radar_rule_versions",
            "radar_runs",
            "radar_source_status",
            "security_master_history",
            "etf_product_registry",
        }.issubset(self.table_names()))
        self.assertNotIn("market_environment_snapshots", self.table_names())
        self.assertNotIn("leader_candidates", self.table_names())
        self.assertNotIn("radar_ai_outputs", self.table_names())

        migration_row = self.connection.execute(
            "SELECT version, name, checksum, applied_at "
            "FROM radar_schema_migrations"
        ).fetchone()
        self.assertEqual(migration_row[0], 1)
        self.assertEqual(migration_row[1], INITIAL_RADAR_MIGRATION.name)
        self.assertEqual(migration_row[2], INITIAL_RADAR_MIGRATION.checksum)
        self.assertEqual(migration_row[3], APPLIED_AT.isoformat(timespec="seconds"))
        self.assertEqual(INITIAL_RADAR_MIGRATION.name, "initial_radar_foundation")
        self.assertEqual(len(INITIAL_RADAR_MIGRATION.statements), 12)
        self.assertEqual(
            INITIAL_RADAR_MIGRATION.checksum,
            "04eb31d34ff45c00a9feea86b5c80b559e8508dc24c694562893c3bc7d456b1c",
        )

    def test_reapplying_same_migration_is_idempotent(self):
        self.assertEqual(self.apply(), [1, 2, 3])
        self.assertEqual(self.apply(), [])

        count = self.connection.execute(
            "SELECT COUNT(*) FROM radar_schema_migrations"
        ).fetchone()[0]
        self.assertEqual(count, 3)

    def test_runtime_validation_is_read_only_and_requires_current_schema(self):
        self.apply()
        total_changes = self.connection.total_changes
        schema_version = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]

        validated = validate_applied_migrations(self.connection)

        self.assertEqual(validated, [1, 2, 3])
        self.assertEqual(self.connection.total_changes, total_changes)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version,
        )
        self.assertFalse(self.connection.in_transaction)

    def test_runtime_validation_rejects_missing_ledger_without_creating_it(self):
        with self.assertRaises(MigrationDriftError):
            validate_applied_migrations(self.connection)

        self.assertNotIn("radar_schema_migrations", self.table_names())

    def test_runtime_validation_rejects_missing_required_object(self):
        self.apply()
        self.connection.execute("DROP INDEX idx_radar_runs_as_of_status")
        self.connection.commit()

        with self.assertRaises(MigrationDriftError):
            validate_applied_migrations(self.connection)

    def test_changed_checksum_is_rejected_as_schema_drift(self):
        self.apply()
        self.connection.execute(
            "UPDATE radar_schema_migrations SET checksum='tampered' WHERE version=1"
        )
        self.connection.commit()

        with self.assertRaises(MigrationDriftError):
            self.apply()

    def test_industry_migration_checksum_is_recorded_and_drift_is_rejected(self):
        self.apply()
        migration_row = self.connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations WHERE version=2"
        ).fetchone()
        self.assertEqual(
            migration_row,
            (INDUSTRY_STORAGE_MIGRATION.name, INDUSTRY_STORAGE_MIGRATION.checksum),
        )

        self.connection.execute(
            "UPDATE radar_schema_migrations SET checksum='tampered' WHERE version=2"
        )
        self.connection.commit()

        with self.assertRaises(MigrationDriftError):
            self.apply()

    def test_unknown_future_version_is_rejected(self):
        self.apply()
        self.connection.execute(
            "INSERT INTO radar_schema_migrations "
            "(version, name, checksum, applied_at) VALUES (99, 'future', 'x', ?)",
            (APPLIED_AT.isoformat(timespec="seconds"),),
        )
        self.connection.commit()

        with self.assertRaises(MigrationDriftError):
            self.apply()

    def test_failed_migration_rolls_back_its_partial_schema(self):
        broken = Migration(
            version=2,
            name="broken_migration",
            statements=(
                "CREATE TABLE radar_partial_table (id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ),
        )

        with self.assertRaises(MigrationApplyError):
            self.apply((INITIAL_RADAR_MIGRATION, broken))

        self.assertNotIn("radar_partial_table", self.table_names())
        versions = self.connection.execute(
            "SELECT version FROM radar_schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(versions, [(1,)])

    def test_version_one_database_upgrades_without_changing_version_one(self):
        self.assertEqual(self.apply((INITIAL_RADAR_MIGRATION,)), [1])
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=(INITIAL_RADAR_MIGRATION,),
            ),
            [1],
        )
        before = self.connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations WHERE version=1"
        ).fetchone()

        self.assertEqual(self.apply(), [2, 3])

        after = self.connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations WHERE version=1"
        ).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(
            after,
            (
                "initial_radar_foundation",
                "04eb31d34ff45c00a9feea86b5c80b559e8508dc24c694562893c3bc7d456b1c",
            ),
        )
        self.assertEqual(validate_applied_migrations(self.connection), [1, 2, 3])

    def test_industry_tables_and_required_indexes_are_created(self):
        self.assertEqual(self.apply(), [1, 2, 3])

        self.assertTrue({
            "industry_classification_releases",
            "industry_classification_records",
            "sector_feature_snapshots",
        }.issubset(self.table_names()))
        schema_objects = {
            (row[0], row[1])
            for row in self.connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('index', 'trigger')"
            ).fetchall()
        }
        self.assertIn(("index", "uq_radar_runs_id_as_of"), schema_objects)
        self.assertIn(("trigger", "trg_sector_feature_release_time_insert"), schema_objects)
        self.assertIn(("trigger", "trg_sector_feature_release_time_update"), schema_objects)

    def test_industry_release_enforces_period_uniqueness_and_time_intervals(self):
        self.apply()
        self._insert_industry_release()

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_industry_release(industry_release_id="release-duplicate")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_industry_release(
                industry_release_id="release-invalid-time",
                release_period="2025H1",
                first_observed_at="2026-04-03T09:00:00+08:00",
                fetched_at="2026-04-03T08:59:59+08:00",
            )

    def test_classification_record_uniqueness_and_foreign_key_constraints(self):
        self.apply()
        self._insert_industry_release()
        self._insert_classification_record()

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_classification_record(
                source_symbol="000002",
                division_code="40",
                division_name="仪器仪表制造业",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_classification_record(
                industry_release_id="missing-release",
                source_symbol="000003",
                security_identity="000003",
            )

    def test_sector_snapshot_enforces_foreign_keys_uniqueness_and_knowledge_time(self):
        self.apply()
        self._insert_industry_release()
        self._insert_radar_run()
        self._insert_sector_snapshot()

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_sector_snapshot(quote_batch_id="quote-duplicate")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_sector_snapshot(
                radar_run_id="missing-run",
                division_code="40",
                division_name="仪器仪表制造业",
            )

        self._insert_radar_run(
            radar_run_id="run-before-knowledge",
            as_of="2026-07-20T09:30:00+08:00",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_sector_snapshot(
                radar_run_id="run-before-knowledge",
                as_of="2026-07-20T09:30:00+08:00",
                division_code="40",
                division_name="仪器仪表制造业",
            )

    def test_sector_snapshot_preserves_real_zero_and_null(self):
        self.apply()
        self._insert_industry_release()
        self._insert_radar_run()
        self._insert_sector_snapshot(
            equal_return=0.0,
            cap_weighted_return=None,
            ex_top_return=None,
            top_contributor_symbol=None,
            top_contribution_percent_points=None,
            up_ratio=0.0,
            turnover_raw_value=0.0,
        )

        stored = self.connection.execute(
            "SELECT equal_return, cap_weighted_return, ex_top_return, up_ratio, "
            "turnover_raw_value FROM sector_feature_snapshots"
        ).fetchone()
        self.assertEqual(stored, (0.0, None, None, 0.0, 0.0))

    def _insert_industry_release(
        self,
        *,
        industry_release_id="capco-2025H2",
        release_period="2025H2",
        first_observed_at="2026-07-21T08:00:00+08:00",
        fetched_at="2026-07-21T08:00:02+08:00",
    ):
        self.connection.execute(
            "INSERT INTO industry_classification_releases ("
            "industry_release_id, classification_system, scheme_version, "
            "release_period, source_page_title, publication_page_url, document_url, "
            "document_sha256, published_date, first_observed_at, fetched_at, "
            "knowledge_effective_from, knowledge_effective_to, "
            "classification_start_date, history_status, source_record_count, "
            "unique_source_symbol_count, required_field_coverage_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                industry_release_id,
                "capco_listed_company_industry",
                "capco-guideline-2023",
                release_period,
                "2025年下半年行业分类结果",
                "https://www.capco.org.cn/release.html",
                "https://www.capco.org.cn/result.pdf",
                "a" * 64,
                "2026-04-03",
                first_observed_at,
                fetched_at,
                "2026-07-21T08:00:00+08:00",
                None,
                "2025-12-20",
                "retrospective_unverified",
                5463,
                5463,
                '{"sourceSymbol": 1.0}',
                "2026-07-21T08:00:02+08:00",
            ),
        )

    def _insert_classification_record(
        self,
        *,
        industry_release_id="capco-2025H2",
        source_symbol="000001",
        security_identity="000001",
        division_code="39",
        division_name="计算机、通信和其他电子设备制造业",
    ):
        self.connection.execute(
            "INSERT INTO industry_classification_records ("
            "industry_release_id, source_symbol, source_name, security_identity, "
            "identity_status, category_code, category_name, division_code, "
            "division_name, manufacturing_subclass_code, "
            "manufacturing_subclass_name, record_status, issue_codes_json, "
            "source_fields_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                industry_release_id,
                source_symbol,
                "平安银行",
                security_identity,
                "exact",
                "C",
                "制造业",
                division_code,
                division_name,
                "CE",
                "计算机、通信和其他电子设备制造业",
                "accepted",
                "[]",
                "{}",
                "2026-07-21T08:00:02+08:00",
            ),
        )

    def _insert_radar_run(
        self,
        *,
        radar_run_id="sector-run",
        as_of="2026-07-21T09:30:00+08:00",
    ):
        self.connection.execute(
            "INSERT INTO radar_runs ("
            "radar_run_id, as_of, status, shadow_mode, started_at, created_at"
            ") VALUES (?, ?, 'running', 1, ?, ?)",
            (radar_run_id, as_of, as_of, as_of),
        )

    def _insert_sector_snapshot(
        self,
        *,
        radar_run_id="sector-run",
        as_of="2026-07-21T09:30:00+08:00",
        quote_batch_id="quote-batch",
        division_code="39",
        division_name="计算机、通信和其他电子设备制造业",
        equal_return=0.0,
        cap_weighted_return=0.1,
        ex_top_return=0.0,
        top_contributor_symbol="000001",
        top_contribution_percent_points=0.1,
        up_ratio=0.0,
        turnover_raw_value=0.0,
    ):
        self.connection.execute(
            "INSERT INTO sector_feature_snapshots ("
            "radar_run_id, industry_release_id, classification_batch_id, "
            "quote_batch_id, category_code, category_name, division_code, "
            "division_name, as_of, source_time, fetched_at, "
            "classification_mapping_coverage, mapped_constituent_count, "
            "unconfirmed_stock_count, expected_count, returned_count, fresh_count, "
            "valid_return_count, valid_market_cap_count, valid_turnover_count, "
            "row_coverage, required_field_coverage_json, is_complete, equal_return, "
            "cap_weighted_return, ex_top_return, top_contributor_symbol, "
            "top_contribution_percent_points, market_cap_basis, "
            "market_cap_unit_status, advancers, decliners, flat, unavailable, "
            "up_ratio, turnover_raw_value, turnover_contributing_count, "
            "turnover_unit_status, shadow_usable, reasons_json, "
            "evidence_summary_json, created_at"
            ") VALUES ("
            + ", ".join("?" for _ in range(42))
            + ")",
            (
                radar_run_id,
                "capco-2025H2",
                "classification-batch",
                quote_batch_id,
                "C",
                "制造业",
                division_code,
                division_name,
                as_of,
                "2026-07-21T09:29:59+08:00",
                "2026-07-21T09:30:02+08:00",
                0.983903,
                2,
                0,
                2,
                2,
                2,
                2,
                2 if cap_weighted_return is not None else 0,
                2,
                1.0,
                '{"changePercent": 1.0}',
                1,
                equal_return,
                cap_weighted_return,
                ex_top_return,
                top_contributor_symbol,
                top_contribution_percent_points,
                "total_market_cap_source",
                "unverified",
                0,
                1,
                1,
                0,
                up_ratio,
                turnover_raw_value,
                2,
                "unverified",
                1,
                "[]",
                '{"sourceBatchIds": ["classification-batch", "quote-batch"]}',
                "2026-07-21T09:30:02+08:00",
            ),
        )

    def test_source_status_requires_existing_run_and_valid_coverage(self):
        self.apply()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO radar_source_status ("
                "radar_run_id, batch_id, source, as_of, fetched_at, status, "
                "expected_count, returned_count, row_coverage, "
                "required_field_coverage_json, issues_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "missing-run",
                    "batch-1",
                    "tencent_finance",
                    "2026-07-18T10:00:00+08:00",
                    "2026-07-18T10:00:02+08:00",
                    "healthy",
                    1,
                    1,
                    1.0,
                    "{}",
                    "[]",
                    "2026-07-18T10:00:02+08:00",
                ),
            )

        self.connection.execute(
            "INSERT INTO radar_runs ("
            "radar_run_id, as_of, status, shadow_mode, started_at, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                "run-1",
                "2026-07-18T10:00:00+08:00",
                "running",
                1,
                "2026-07-18T10:00:00+08:00",
                "2026-07-18T10:00:00+08:00",
            ),
        )
        self.connection.execute(
            "INSERT INTO radar_source_status ("
            "radar_run_id, batch_id, source, as_of, fetched_at, status, "
            "expected_count, returned_count, row_coverage, "
            "required_field_coverage_json, issues_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run-1",
                "batch-1",
                "tencent_finance",
                "2026-07-18T10:00:00+08:00",
                "2026-07-18T10:00:02+08:00",
                "degraded",
                0,
                0,
                0.0,
                '{"price": 0.0}',
                "[]",
                "2026-07-18T10:00:02+08:00",
            ),
        )

        stored = self.connection.execute(
            "SELECT expected_count, returned_count, row_coverage "
            "FROM radar_source_status WHERE radar_run_id='run-1'"
        ).fetchone()
        self.assertEqual(stored, (0, 0, 0.0))

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO radar_source_status ("
                "radar_run_id, batch_id, source, as_of, fetched_at, status, "
                "expected_count, returned_count, row_coverage, "
                "required_field_coverage_json, issues_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "run-1",
                    "batch-invalid",
                    "tencent_finance",
                    "2026-07-18T10:00:00+08:00",
                    "2026-07-18T10:00:02+08:00",
                    "healthy",
                    1,
                    1,
                    1.1,
                    "{}",
                    "[]",
                    "2026-07-18T10:00:02+08:00",
                ),
            )

    def test_history_tables_keep_one_current_version_per_source(self):
        self.apply()
        security_row = (
            "000001",
            "平安银行",
            "szse",
            "主板",
            "szse",
            "2026-07-18",
            None,
            "{}",
            "security-checksum-1",
            "2026-07-18T05:00:00+00:00",
        )
        self.connection.execute(
            "INSERT INTO security_master_history ("
            "symbol, name, exchange, board, source, effective_from, effective_to, "
            "source_fields_json, record_checksum, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            security_row,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO security_master_history ("
                "symbol, name, exchange, board, source, effective_from, effective_to, "
                "source_fields_json, record_checksum, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "000001",
                    "平安银行新简称",
                    "szse",
                    "主板",
                    "szse",
                    "2026-07-19",
                    None,
                    "{}",
                    "security-checksum-2",
                    "2026-07-19T05:00:00+00:00",
                ),
            )

        self.connection.execute(
            "UPDATE security_master_history SET effective_to='2026-07-19' "
            "WHERE symbol='000001' AND effective_to IS NULL"
        )
        self.connection.execute(
            "INSERT INTO security_master_history ("
            "symbol, name, exchange, board, source, effective_from, effective_to, "
            "source_fields_json, record_checksum, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "000001",
                "平安银行新简称",
                "szse",
                "主板",
                "szse",
                "2026-07-19",
                None,
                "{}",
                "security-checksum-2",
                "2026-07-19T05:00:00+00:00",
            ),
        )
        self.connection.commit()

        count = self.connection.execute(
            "SELECT COUNT(*) FROM security_master_history WHERE symbol='000001'"
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_runner_requires_caller_owned_connection(self):
        with self.assertRaises(TypeError):
            apply_pending_migrations()

    def test_file_database_reopens_and_existing_backup_tool_preserves_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "radar-test.db"
            backup_dir = Path(temp_dir) / "backups"
            with sqlite3.connect(database_path) as connection:
                applied = apply_pending_migrations(
                    connection,
                    clock=lambda: APPLIED_AT,
                )
            self.assertEqual(applied, [1, 2, 3])

            backup = backup_database.create_database_backup(
                database_path,
                backup_dir,
                now=datetime(2026, 7, 18, 13, 0),
            )
            with sqlite3.connect(backup["backupPath"]) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT checksum FROM radar_schema_migrations WHERE version=1"
                    ).fetchone()[0],
                    INITIAL_RADAR_MIGRATION.checksum,
                )
                self.assertEqual(
                    apply_pending_migrations(
                        connection,
                        clock=lambda: APPLIED_AT,
                    ),
                    [],
                )
                self.assertEqual(validate_applied_migrations(connection), [1, 2, 3])
                self.assertIn("sector_feature_snapshots", {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                })


if __name__ == "__main__":
    unittest.main()
