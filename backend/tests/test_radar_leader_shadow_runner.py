import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_scoring import (
    LeaderDimensionInput,
    LeaderGateInput,
    LeaderMetricStatus,
    LeaderScoringInput,
)
from radar.leader_shadow_runner import (
    LeaderShadowCandidate,
    LeaderShadowRunInProgressError,
    LeaderShadowRunner,
)
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderStateRecord,
)
from radar.migrations import (
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
)


UTC = timezone.utc
APPLIED_AT = datetime(2026, 7, 25, 5, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 27, 1, 45, tzinfo=UTC)


def complete_gates(**overrides):
    values = {
        "industry_gate_passed": True,
        "stock_gate_passed": True,
        "market_leadership_passed": True,
        "industry_contribution_passed": True,
        "liquidity_passed": True,
        "tradability_passed": True,
        "continuity_passed": True,
        "recovery_passed": True,
        "risk_filter_passed": True,
        "business_exposure_status": BusinessExposureStatus.VERIFIED,
    }
    values.update(overrides)
    return LeaderGateInput(**values)


def dimensions(**overrides):
    values = {
        "industry_strength": 25,
        "market_leadership": 25,
        "relative_strength_continuity": 20,
        "liquidity_tradability": 15,
        "business_exposure": 10,
        "auxiliary": 0,
    }
    values.update(overrides)
    return tuple(
        LeaderDimensionInput(field_name=field_name, score=score)
        for field_name, score in values.items()
    )


def scoring_input(
    symbol="000001",
    *,
    as_of=AS_OF,
    dims=None,
    gates=None,
    consecutive=2,
):
    return LeaderScoringInput(
        symbol=symbol,
        as_of=as_of,
        dimensions=dims if dims is not None else dimensions(),
        gates=gates if gates is not None else complete_gates(),
        consecutive_signal_periods=consecutive,
    )


class LeaderShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.insert_run()
        self.runner = LeaderShadowRunner(
            self.repository(),
            clock=lambda: APPLIED_AT,
        )

    def tearDown(self):
        self.connection.close()

    def repository(self):
        from radar.leader_repository import LeaderRepository

        return LeaderRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )

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
    def candidate(symbol="000001", *, scoring=None):
        return LeaderShadowCandidate(
            scoring_input=scoring or scoring_input(symbol),
            name=f"测试证券{symbol}",
            industry_code="C39",
            industry_name="计算机、通信和其他电子设备制造业",
            evidence={"source": "fixture"},
        )

    def test_complete_input_is_shadow_only_and_starts_at_preliminary(self):
        result = self.runner.run_once(
            "run-1",
            AS_OF,
            [self.candidate()],
        )

        self.assertEqual(result.status, "shadow_only")
        self.assertEqual(result.quality, "complete")
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.preliminary_count, 1)
        self.assertEqual(result.candidate_count, 0)
        self.assertFalse(result.formal_usable)
        self.assertTrue(result.persisted)
        self.assertEqual(result.persisted_transition_count, 1)
        self.assertEqual(result.decisions[0].to_state.value, "preliminary")
        self.assertEqual(result.decisions[0].action.value, "enter")

        snapshot = self.repository().get_candidate_snapshot("run-1")
        self.assertFalse(snapshot["formalUsable"])
        self.assertEqual(snapshot["entries"][0]["state"], "preliminary")
        self.assertFalse(snapshot["entries"][0]["formalUsable"])
        self.assertEqual(
            len(self.repository().list_state_history("000001")),
            1,
        )

    def test_previous_state_is_used_for_next_upgrade(self):
        first = self.runner.run_once(
            "run-1",
            AS_OF,
            [self.candidate()],
        )
        previous = LeaderStateRecord(
            symbol="000001",
            state=first.decisions[0].to_state,
            state_age_periods=first.decisions[0].state_age_periods,
            rule_version=first.decisions[0].rule_version,
            state_since=AS_OF,
            last_evaluated_at=AS_OF,
        )
        next_as_of = AS_OF + timedelta(days=1)
        self.insert_run("run-2", next_as_of)
        next_candidate = self.candidate(
            scoring=scoring_input("000001", as_of=next_as_of)
        )

        result = self.runner.run_once(
            "run-2",
            next_as_of,
            [next_candidate],
            previous_states={"000001": previous},
        )

        self.assertEqual(result.decisions[0].to_state.value, "candidate")
        self.assertEqual(result.decisions[0].action.value, "upgrade")
        self.assertEqual(result.candidate_count, 1)

    def test_missing_data_is_degraded_and_blocked_without_formal_state(self):
        incomplete_dimensions = tuple(
            item
            for item in dimensions()
            if item.field_name != "business_exposure"
        )
        result = self.runner.run_once(
            "run-1",
            AS_OF,
            [
                self.candidate(
                    scoring=scoring_input(
                        dims=incomplete_dimensions,
                    )
                )
            ],
        )

        self.assertEqual(result.quality, "degraded")
        self.assertEqual(result.coverage, 0.0)
        self.assertEqual(result.blocked_count, 1)
        self.assertEqual(result.decisions[0].action.value, "blocked")
        self.assertEqual(
            result.audits[0].first_rejection_reason,
            "data_status_missing",
        )
        loaded = self.repository().get_candidate_snapshot("run-1")
        self.assertEqual(
            loaded["entries"][0]["firstRejectionReason"],
            "data_status_missing",
        )
        self.assertFalse(loaded["entries"][0]["formalUsable"])

    def test_empty_board_is_persisted_as_empty_shadow_snapshot(self):
        result = self.runner.run_once("run-1", AS_OF, [])

        self.assertEqual(result.quality, "empty")
        self.assertEqual(result.coverage, 0.0)
        self.assertEqual(result.eligible_count, 0)
        self.assertEqual(result.persisted_transition_count, 0)
        loaded = self.repository().get_candidate_snapshot("run-1")
        self.assertEqual(loaded["entries"], [])
        self.assertEqual(loaded["reasonCounts"], {})

    def test_reordered_retry_is_idempotent(self):
        candidates = [
            self.candidate("000001"),
            self.candidate("000002"),
        ]
        first = self.runner.run_once("run-1", AS_OF, candidates)
        retry = self.runner.run_once(
            "run-1",
            AS_OF,
            list(reversed(candidates)),
        )

        self.assertTrue(first.persisted)
        self.assertFalse(retry.persisted)
        self.assertEqual(retry.persisted_transition_count, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_candidate_entries"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_state_history"
            ).fetchone()[0],
            2,
        )

    def test_duplicate_symbols_and_mismatched_as_of_are_rejected(self):
        with self.assertRaises(ValueError):
            self.runner.run_once(
                "run-1",
                AS_OF,
                [self.candidate(), self.candidate()],
            )

        with self.assertRaises(ValueError):
            self.runner.run_once(
                "run-1",
                AS_OF,
                [
                    self.candidate(
                        scoring=scoring_input(
                            as_of=AS_OF + timedelta(seconds=1)
                        )
                    )
                ],
            )

        self.assertIsNone(
            self.repository().get_candidate_snapshot("run-1")
        )

    def test_run_lock_rejects_reentrant_execution(self):
        lock = __import__("threading").Lock()
        lock.acquire()
        runner = LeaderShadowRunner(
            self.repository(),
            clock=lambda: APPLIED_AT,
            run_lock=lock,
        )
        try:
            with self.assertRaises(LeaderShadowRunInProgressError):
                runner.run_once("run-1", AS_OF, [])
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
