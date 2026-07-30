import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.leader_repository import LeaderRepository
from radar.migrations import (
    STAGE5_RADAR_MIGRATIONS,
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)
from radar.repository import (
    RepositoryConflictError,
    RepositoryStateError,
)


UTC = timezone.utc
APPLIED_AT = datetime(2026, 7, 25, 5, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 24, 1, 30, tzinfo=UTC)


class LeaderRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.repository = LeaderRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )
        self.insert_run()

    def tearDown(self):
        self.connection.close()

    def insert_run(self, radar_run_id="run-1", as_of=AS_OF):
        as_of_text = as_of.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES (?, ?, 'succeeded', 1, ?, ?)
            """,
            (radar_run_id, as_of_text, as_of_text, as_of_text),
        )
        self.connection.commit()

    @staticmethod
    def snapshot(radar_run_id="run-1"):
        return {
            "radarRunId": radar_run_id,
            "asOf": AS_OF,
            "ruleVersion": "radar-leader-state-machine-v1",
            "ruleVersionId": None,
            "eligibleCount": 2,
            "preliminaryCount": 0,
            "candidateCount": 1,
            "confirmedCount": 0,
            "removedCount": 1,
            "coverage": 1.0,
            "quality": "shadow_only",
            "reasonCounts": {"business_exposure_missing": 1},
            "formalUsable": False,
        }

    @staticmethod
    def entry(
        symbol="000001",
        *,
        state="candidate",
        score=0,
        formal_usable=False,
    ):
        return {
            "symbol": symbol,
            "name": "测试证券",
            "industryCode": "C39",
            "industryName": "计算机、通信和其他电子设备制造业",
            "state": state,
            "score": score,
            "businessExposureStatus": "verified",
            "dataStatus": "healthy",
            "firstRejectionReason": None,
            "reasons": [],
            "evidence": {"verified": True},
            "invalidation": {},
            "stateAgePeriods": 2,
            "formalUsable": formal_usable,
        }

    @staticmethod
    def transition(
        transition_id="transition-1",
        *,
        radar_run_id="run-1",
        as_of=AS_OF,
    ):
        return {
            "transitionId": transition_id,
            "symbol": "000001",
            "radarRunId": radar_run_id,
            "asOf": as_of,
            "fromState": "out",
            "toState": "preliminary",
            "action": "enter",
            "ruleVersion": "radar-leader-state-machine-v1",
            "ruleVersionId": None,
            "reasons": ["industry_gate_failed"],
            "firstRejectionReason": "industry_gate_failed",
            "stateAgePeriods": 1,
            "cooldownUntil": None,
            "formalUsable": False,
        }

    def test_version_five_is_optional_and_default_runtime_accepts_both(self):
        legacy = sqlite3.connect(":memory:")
        try:
            self.assertEqual(
                apply_pending_migrations(
                    legacy,
                    migrations=STAGE5_RADAR_MIGRATIONS,
                    clock=lambda: APPLIED_AT,
                ),
                [1, 2, 3, 4],
            )
            self.assertEqual(
                validate_applied_migrations(
                    legacy,
                    migrations=STAGE5_RADAR_MIGRATIONS,
                ),
                [1, 2, 3, 4],
            )
            self.assertEqual(
                validate_applied_migrations(legacy),
                [1, 2, 3, 4],
            )
            self.assertNotIn(
                "radar_leader_candidate_snapshots",
                {
                    row[0]
                    for row in legacy.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                },
            )

            with self.assertRaises(RepositoryStateError):
                LeaderRepository(legacy)
        finally:
            legacy.close()

        self.assertEqual(
            validate_applied_migrations(self.connection),
            [1, 2, 3, 4, 5],
        )

    def test_snapshot_round_trip_preserves_real_zero_and_is_idempotent(self):
        entry = self.entry(score=0)
        snapshot = self.snapshot()

        self.assertTrue(
            self.repository.save_candidate_snapshot(snapshot, [entry])
        )
        self.assertFalse(
            self.repository.save_candidate_snapshot(snapshot, [entry])
        )

        loaded = self.repository.get_candidate_snapshot("run-1")
        self.assertEqual(loaded["asOf"], AS_OF)
        self.assertEqual(loaded["entries"][0]["score"], 0.0)
        self.assertEqual(loaded["entries"][0]["symbol"], "000001")
        self.assertFalse(loaded["formalUsable"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_snapshots"
            ).fetchone()[0],
            1,
        )

    def test_snapshot_constraint_failure_rolls_back_snapshot_and_entries(self):
        invalid_entry = self.entry(
            state="preliminary",
            formal_usable=True,
        )

        with self.assertRaises(RepositoryConflictError):
            self.repository.save_candidate_snapshot(
                self.snapshot(),
                [invalid_entry],
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_entries"
            ).fetchone()[0],
            0,
        )

    def test_snapshot_conflicting_content_does_not_replace_existing_batch(self):
        snapshot = self.snapshot()
        entry = self.entry()
        self.assertTrue(
            self.repository.save_candidate_snapshot(snapshot, [entry])
        )

        with self.assertRaises(RepositoryConflictError):
            self.repository.save_candidate_snapshot(
                {**snapshot, "quality": "degraded"},
                [entry],
            )

        loaded = self.repository.get_candidate_snapshot("run-1")
        self.assertEqual(loaded["quality"], "shadow_only")
        self.assertEqual(len(loaded["entries"]), 1)

    def test_duplicate_symbols_are_rejected_before_any_write(self):
        entry = self.entry()

        with self.assertRaises(RepositoryConflictError):
            self.repository.save_candidate_snapshot(
                self.snapshot(),
                [entry, {**entry, "name": "重复证券"}],
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_state_history_is_idempotent_conflict_safe_and_ordered(self):
        first = self.transition()
        second = self.transition(
            "transition-2",
            as_of=AS_OF + timedelta(days=1),
        )

        self.assertTrue(self.repository.save_state_transition(first))
        self.assertFalse(self.repository.save_state_transition(first))
        self.assertTrue(self.repository.save_state_transition(second))

        with self.assertRaises(RepositoryConflictError):
            self.repository.save_state_transition({
                **first,
                "toState": "candidate",
            })

        history = self.repository.list_state_history("000001")
        self.assertEqual(
            [item["transitionId"] for item in history],
            ["transition-1", "transition-2"],
        )
        self.assertEqual(history[0]["action"], "enter")
        self.assertEqual(len(history), 2)

    def test_previous_state_reader_excludes_current_and_future_records(self):
        first = self.transition()
        second_as_of = AS_OF + timedelta(days=1)
        future_as_of = AS_OF + timedelta(days=2)
        second = {
            **self.transition(
                "transition-2",
                as_of=second_as_of,
            ),
            "fromState": "preliminary",
            "toState": "preliminary",
            "action": "hold",
            "stateAgePeriods": 2,
        }
        future = {
            **self.transition(
                "transition-3",
                as_of=future_as_of,
            ),
            "fromState": "preliminary",
            "toState": "candidate",
            "action": "upgrade",
            "stateAgePeriods": 1,
        }
        self.repository.save_state_transition(first)
        self.repository.save_state_transition(second)
        self.repository.save_state_transition(future)

        before_second = self.repository.get_latest_state_records_before(
            second_as_of
        )
        after_second = self.repository.get_latest_state_records_before(
            second_as_of + timedelta(seconds=1)
        )

        self.assertEqual(before_second["000001"].state.value, "preliminary")
        self.assertEqual(before_second["000001"].state_age_periods, 1)
        self.assertEqual(
            before_second["000001"].last_evaluated_at,
            AS_OF,
        )
        self.assertEqual(after_second["000001"].state_age_periods, 2)
        self.assertEqual(
            after_second["000001"].last_evaluated_at,
            second_as_of,
        )
        self.assertEqual(after_second["000001"].state.value, "preliminary")

    def test_foreign_keys_reject_unknown_run_and_roll_back(self):
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_candidate_snapshot(
                self.snapshot("missing-run"),
                [self.entry()],
            )

        with self.assertRaises(RepositoryConflictError):
            self.repository.save_state_transition(
                self.transition(radar_run_id="missing-run")
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_state_history"
            ).fetchone()[0],
            0,
        )

    def test_shadow_run_snapshot_and_history_are_one_transaction(self):
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_shadow_run(
                self.snapshot(),
                [self.entry()],
                [self.transition(radar_run_id="missing-run")],
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_entries"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_state_history"
            ).fetchone()[0],
            0,
        )

    def test_file_backed_database_survives_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "stage6c.sqlite"
            connection = sqlite3.connect(database_path)
            apply_pending_migrations(
                connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
                clock=lambda: APPLIED_AT,
            )
            connection.execute(
                """
                INSERT INTO radar_runs (
                    radar_run_id, as_of, status, shadow_mode,
                    started_at, created_at
                ) VALUES (?, ?, 'succeeded', 1, ?, ?)
                """,
                (
                    "file-run",
                    AS_OF.isoformat(),
                    AS_OF.isoformat(),
                    AS_OF.isoformat(),
                ),
            )
            connection.commit()
            repository = LeaderRepository(
                connection,
                clock=lambda: APPLIED_AT,
            )
            repository.save_candidate_snapshot(
                self.snapshot("file-run"),
                [self.entry(score=0)],
            )
            repository.save_state_transition(
                self.transition(radar_run_id="file-run")
            )
            connection.close()

            reopened = sqlite3.connect(database_path)
            try:
                self.assertEqual(
                    validate_applied_migrations(
                        reopened,
                        migrations=STAGE6_RADAR_MIGRATIONS,
                    ),
                    [1, 2, 3, 4, 5],
                )
                self.assertEqual(
                    reopened.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0],
                    "ok",
                )
                reopened_repository = LeaderRepository(
                    reopened,
                    clock=lambda: APPLIED_AT,
                )
                self.assertEqual(
                    reopened_repository.get_latest_candidate_snapshot()[
                        "radarRunId"
                    ],
                    "file-run",
                )
                self.assertEqual(
                    len(reopened_repository.list_state_history("000001")),
                    1,
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
