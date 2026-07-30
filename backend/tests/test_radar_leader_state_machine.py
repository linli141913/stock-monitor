import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_state_machine import (
    BusinessExposureStatus,
    DEFAULT_LEADER_STATE_MACHINE_POLICY,
    LeaderDataStatus,
    LeaderEvidenceSnapshot,
    LeaderState,
    LeaderStateMachinePolicy,
    LeaderStateRecord,
    LeaderTransitionAction,
    assert_unique_active_leader_states,
    decide_leader_transition,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 27, 1, 35, tzinfo=UTC)


def snapshot(**overrides):
    values = {
        "symbol": "000001",
        "as_of": AS_OF,
        "score": 90,
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
        "consecutive_signal_periods": 2,
    }
    values.update(overrides)
    return LeaderEvidenceSnapshot(**values)


def record(
    state,
    *,
    age=2,
    symbol="000001",
    cooldown_until=None,
):
    return LeaderStateRecord(
        symbol=symbol,
        state=state,
        state_age_periods=age,
        rule_version=DEFAULT_LEADER_STATE_MACHINE_POLICY.version,
        state_since=AS_OF - timedelta(minutes=age * 3),
        last_evaluated_at=AS_OF - timedelta(minutes=3),
        cooldown_until=cooldown_until,
    )


class LeaderStateMachinePolicyTests(unittest.TestCase):
    def test_default_policy_is_versioned_and_keeps_formal_state_disabled(self):
        policy = DEFAULT_LEADER_STATE_MACHINE_POLICY

        self.assertEqual(policy.version, "radar-leader-state-machine-v1")
        self.assertFalse(policy.formal_state_enabled)
        self.assertEqual(dict(policy.score_weights)["auxiliary"], 5)
        self.assertEqual(set(policy.rules), {
            LeaderState.PRELIMINARY,
            LeaderState.CANDIDATE,
            LeaderState.CONFIRMED,
        })

    def test_policy_rejects_auxiliary_weight_above_five_percent(self):
        with self.assertRaises(ValueError):
            LeaderStateMachinePolicy(
                score_weights=(
                    ("industry_strength", 25),
                    ("market_leadership", 25),
                    ("relative_strength_continuity", 20),
                    ("liquidity_tradability", 14),
                    ("business_exposure", 10),
                    ("auxiliary", 6),
                ),
            )


class LeaderStateMachineTransitionTests(unittest.TestCase):
    def test_verified_complete_signal_enters_preliminary_first(self):
        decision = decide_leader_transition(snapshot())

        self.assertEqual(decision.from_state, LeaderState.OUT)
        self.assertEqual(decision.to_state, LeaderState.PRELIMINARY)
        self.assertEqual(decision.action, LeaderTransitionAction.ENTER)
        self.assertFalse(decision.formal_state_enabled)

    def test_candidate_requires_verified_business_exposure(self):
        previous = record(LeaderState.PRELIMINARY, age=2)
        decision = decide_leader_transition(
            snapshot(
                score=82,
                business_exposure_status=BusinessExposureStatus.UNCONFIRMED,
                recovery_passed=False,
            ),
            previous,
        )

        self.assertEqual(decision.to_state, LeaderState.PRELIMINARY)
        self.assertEqual(decision.action, LeaderTransitionAction.HOLD)
        self.assertEqual(decision.reasons, ("state_maintained",))

        blocked = decide_leader_transition(
            snapshot(
                score=82,
                business_exposure_status=BusinessExposureStatus.UNCONFIRMED,
                recovery_passed=False,
            ),
        )
        self.assertEqual(blocked.to_state, LeaderState.PRELIMINARY)

    def test_preliminary_upgrades_to_candidate_after_two_periods(self):
        decision = decide_leader_transition(
            snapshot(score=80, recovery_passed=False),
            record(LeaderState.PRELIMINARY, age=2),
        )

        self.assertEqual(decision.to_state, LeaderState.CANDIDATE)
        self.assertEqual(decision.action, LeaderTransitionAction.UPGRADE)

    def test_candidate_upgrades_to_confirmed_with_recovery_evidence(self):
        decision = decide_leader_transition(
            snapshot(score=90, recovery_passed=True),
            record(LeaderState.CANDIDATE, age=3),
        )

        self.assertEqual(decision.to_state, LeaderState.CONFIRMED)
        self.assertEqual(decision.action, LeaderTransitionAction.UPGRADE)

    def test_hysteresis_holds_candidate_below_enter_above_maintain(self):
        decision = decide_leader_transition(
            snapshot(score=70, recovery_passed=False),
            record(LeaderState.CANDIDATE, age=3),
        )

        self.assertEqual(decision.to_state, LeaderState.CANDIDATE)
        self.assertEqual(decision.action, LeaderTransitionAction.HOLD)
        self.assertEqual(decision.reasons, ("hysteresis_hold",))

    def test_minimum_hold_blocks_soft_downgrade(self):
        decision = decide_leader_transition(
            snapshot(
                score=62,
                industry_contribution_passed=False,
                continuity_passed=False,
                recovery_passed=False,
            ),
            record(LeaderState.CANDIDATE, age=1),
        )

        self.assertEqual(decision.to_state, LeaderState.CANDIDATE)
        self.assertEqual(decision.action, LeaderTransitionAction.HOLD)
        self.assertIn("minimum_hold_active", decision.reasons)

    def test_hard_gate_failure_removes_and_sets_cooldown(self):
        decision = decide_leader_transition(
            snapshot(score=86, risk_filter_passed=False),
            record(LeaderState.CANDIDATE, age=3),
        )

        self.assertEqual(decision.to_state, LeaderState.OUT)
        self.assertEqual(decision.action, LeaderTransitionAction.REMOVE)
        self.assertIn("risk_filter_failed", decision.reasons)
        self.assertEqual(
            decision.cooldown_until,
            AS_OF + timedelta(minutes=180),
        )

    def test_cooldown_blocks_immediate_reentry(self):
        decision = decide_leader_transition(
            snapshot(),
            record(
                LeaderState.OUT,
                age=0,
                cooldown_until=AS_OF + timedelta(minutes=30),
            ),
        )

        self.assertEqual(decision.to_state, LeaderState.OUT)
        self.assertEqual(decision.action, LeaderTransitionAction.BLOCKED)
        self.assertEqual(decision.first_rejection_reason, "cooldown_active")

    def test_stale_or_failed_data_never_creates_new_state(self):
        new_decision = decide_leader_transition(
            snapshot(data_status=LeaderDataStatus.STALE),
        )
        held_decision = decide_leader_transition(
            snapshot(data_status=LeaderDataStatus.SOURCE_FAILED),
            record(LeaderState.CANDIDATE, age=3),
        )

        self.assertEqual(new_decision.to_state, LeaderState.OUT)
        self.assertEqual(new_decision.action, LeaderTransitionAction.BLOCKED)
        self.assertEqual(held_decision.to_state, LeaderState.CANDIDATE)
        self.assertEqual(held_decision.action, LeaderTransitionAction.HOLD)

    def test_first_rejection_reason_explains_current_blocker(self):
        decision = decide_leader_transition(
            snapshot(
                score=74,
                industry_gate_passed=True,
                stock_gate_passed=True,
                business_exposure_status=BusinessExposureStatus.MISSING,
                first_rejection_hint="business_exposure_missing",
            )
        )

        self.assertEqual(decision.to_state, LeaderState.PRELIMINARY)

        blocked = decide_leader_transition(
            snapshot(
                score=55,
                industry_gate_passed=False,
                stock_gate_passed=True,
                first_rejection_hint="industry_gate_failed",
            )
        )
        self.assertEqual(blocked.to_state, LeaderState.OUT)
        self.assertEqual(
            blocked.first_rejection_reason,
            "industry_gate_failed",
        )

    def test_duplicate_active_states_for_same_symbol_are_rejected(self):
        with self.assertRaises(ValueError):
            assert_unique_active_leader_states((
                record(LeaderState.PRELIMINARY, age=1),
                record(LeaderState.CANDIDATE, age=1),
            ))

        assert_unique_active_leader_states((
            record(LeaderState.PRELIMINARY, age=1),
            record(LeaderState.OUT, age=0),
            record(LeaderState.CANDIDATE, age=1, symbol="000002"),
        ))


if __name__ == "__main__":
    unittest.main()
