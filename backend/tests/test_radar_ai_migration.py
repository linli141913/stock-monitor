import sqlite3
import unittest
from datetime import datetime, timezone

from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    STAGE8_RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)


APPLIED_AT = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
FROZEN_PRIOR_MIGRATIONS = (
    (1, "initial_radar_foundation", "04eb31d34ff45c00a9feea86b5c80b559e8508dc24c694562893c3bc7d456b1c"),
    (2, "industry_classification_and_sector_features", "fb24ef22bafdc30662204c24ec64d876b817e7acb48c0b7045744d13b4599b25"),
    (3, "market_environment_and_index_features", "9168f50f76eb83ee68f32dc2e256a071ac7ee88a02b5ba3df8d024cbc0609877"),
    (4, "etf_versioned_storage", "be9eb9a5fdde70b9787cc57b10ba44337ee103520bb2f793834038a72ea498fb"),
    (5, "leader_state_storage", "cb3ab7d786869bc507bb176d436b8abd0f2f915bd7913160bd07339ef0aea602"),
    (6, "leader_risk_review_storage", "9305c180761b649873f3a4d7af50dfc0a587683935a8e4c56cffa6c4f9f4b2f2"),
)


class RadarAiMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")

    def tearDown(self):
        self.connection.close()

    def apply(self):
        return apply_pending_migrations(
            self.connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )

    def test_version_seven_adds_only_ai_and_notification_preference_tables(self):
        self.assertEqual(self.apply(), [1, 2, 3, 4, 5, 6, 7])
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({
            "radar_ai_analysis_runs",
            "radar_ai_outputs",
            "radar_notification_preferences",
        }.issubset(tables))
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=STAGE8_RADAR_MIGRATIONS,
            ),
            [1, 2, 3, 4, 5, 6, 7],
        )

    def test_prior_migration_names_and_checksums_do_not_change(self):
        self.assertEqual(
            tuple(
                (item.version, item.name, item.checksum)
                for item in STAGE6_REVIEW_RADAR_MIGRATIONS
            ),
            FROZEN_PRIOR_MIGRATIONS,
        )
        self.apply()
        rows = self.connection.execute(
            "SELECT version, name, checksum FROM radar_schema_migrations "
            "WHERE version <= 6 ORDER BY version"
        ).fetchall()
        self.assertEqual(tuple(rows), FROZEN_PRIOR_MIGRATIONS)

    def test_stage8_migration_is_idempotent_and_has_reuse_constraints(self):
        self.apply()
        self.assertEqual(self.apply(), [])
        columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info(radar_ai_analysis_runs)"
            )
        }
        self.assertTrue({
            "reuse_key",
            "evidence_fingerprint",
            "prompt_version",
            "model",
            "analysis_status",
            "usage_total_tokens",
        }.issubset(columns))


if __name__ == "__main__":
    unittest.main()
