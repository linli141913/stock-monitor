import unittest
from datetime import datetime, timezone

from radar.leader_scoring import (
    LeaderDimensionInput,
    LeaderGateInput,
    LeaderMetricStatus,
    LeaderScoringInput,
    build_leader_scoring_audit,
)
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderDataStatus,
    LeaderState,
    LeaderTransitionAction,
    decide_leader_transition,
)


UTC = timezone.utc
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


def scoring_input(*, dims=None, gates=None, consecutive=2):
    return LeaderScoringInput(
        symbol="000001",
        as_of=AS_OF,
        dimensions=dims if dims is not None else dimensions(),
        gates=gates if gates is not None else complete_gates(),
        consecutive_signal_periods=consecutive,
    )


class LeaderScoringTests(unittest.TestCase):
    def test_complete_verified_inputs_build_healthy_snapshot_and_preserve_zero(self):
        audit = build_leader_scoring_audit(scoring_input())

        self.assertEqual(audit.score, 95)
        self.assertEqual(audit.data_status, LeaderDataStatus.HEALTHY)
        self.assertEqual(audit.reasons, ())
        self.assertIsNone(audit.first_rejection_reason)
        self.assertEqual(audit.snapshot.score, 95)
        self.assertFalse(audit.formal_state_enabled)

    def test_missing_dimension_is_not_silent_zero_and_blocks_state(self):
        dims = tuple(
            item
            for item in dimensions()
            if item.field_name != "business_exposure"
        )
        audit = build_leader_scoring_audit(scoring_input(dims=dims))
        decision = decide_leader_transition(audit.snapshot)

        self.assertEqual(audit.data_status, LeaderDataStatus.MISSING)
        self.assertIn("dimension_missing:business_exposure", audit.reasons)
        self.assertEqual(audit.first_rejection_reason, "data_status_missing")
        self.assertEqual(decision.to_state, LeaderState.OUT)
        self.assertEqual(decision.action, LeaderTransitionAction.BLOCKED)

    def test_stale_dimension_marks_whole_snapshot_stale(self):
        base_dims = dimensions()
        dims = tuple(
            LeaderDimensionInput(
                field_name=item.field_name,
                score=None,
                status=LeaderMetricStatus.STALE,
                reasons=("source_time_over_90s",),
            )
            if item.field_name == "market_leadership"
            else item
            for item in base_dims
        )
        audit = build_leader_scoring_audit(scoring_input(dims=dims))

        self.assertEqual(audit.data_status, LeaderDataStatus.STALE)
        self.assertIn("dimension_stale:market_leadership", audit.reasons)
        self.assertEqual(audit.first_rejection_reason, "data_status_stale")

    def test_source_failure_has_higher_severity_than_missing(self):
        dims = (
            LeaderDimensionInput(
                field_name="industry_strength",
                score=None,
                status=LeaderMetricStatus.SOURCE_FAILED,
            ),
            LeaderDimensionInput(
                field_name="market_leadership",
                score=None,
                status=LeaderMetricStatus.MISSING,
            ),
            *dimensions(
                industry_strength=0,
                market_leadership=0,
            )[2:],
        )
        audit = build_leader_scoring_audit(scoring_input(dims=dims))

        self.assertEqual(audit.data_status, LeaderDataStatus.SOURCE_FAILED)
        self.assertEqual(
            audit.first_rejection_reason,
            "data_status_source_failed",
        )

    def test_business_exposure_gate_becomes_first_rejection_reason(self):
        audit = build_leader_scoring_audit(
            scoring_input(
                gates=complete_gates(
                    business_exposure_status=BusinessExposureStatus.UNCONFIRMED,
                )
            )
        )

        self.assertEqual(
            audit.first_rejection_reason,
            "business_exposure_unconfirmed",
        )
        self.assertEqual(
            audit.snapshot.business_exposure_status,
            BusinessExposureStatus.UNCONFIRMED,
        )

    def test_risk_gate_failure_precedes_soft_evidence_gaps(self):
        audit = build_leader_scoring_audit(
            scoring_input(
                gates=complete_gates(
                    risk_filter_passed=False,
                    recovery_passed=False,
                )
            )
        )

        self.assertEqual(audit.first_rejection_reason, "risk_filter_failed")

    def test_verified_dimension_cannot_exceed_policy_weight(self):
        with self.assertRaises(ValueError):
            build_leader_scoring_audit(
                scoring_input(dims=dimensions(auxiliary=6))
            )

    def test_unknown_and_duplicate_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            build_leader_scoring_audit(
                scoring_input(dims=(
                    *dimensions(),
                    LeaderDimensionInput(field_name="media_heat", score=1),
                ))
            )
        with self.assertRaises(ValueError):
            build_leader_scoring_audit(
                scoring_input(dims=(
                    *dimensions(),
                    LeaderDimensionInput(field_name="auxiliary", score=0),
                ))
            )

    def test_unverified_dimension_must_not_carry_score(self):
        with self.assertRaises(ValueError):
            LeaderDimensionInput(
                field_name="industry_strength",
                score=1,
                status=LeaderMetricStatus.SOURCE_UNVERIFIED,
            )

    def test_low_score_without_other_gaps_reports_score_blocker(self):
        audit = build_leader_scoring_audit(
            scoring_input(dims=dimensions(
                industry_strength=10,
                market_leadership=10,
                relative_strength_continuity=10,
                liquidity_tradability=10,
                business_exposure=5,
                auxiliary=0,
            ))
        )

        self.assertEqual(audit.score, 45)
        self.assertEqual(audit.first_rejection_reason, "score_below_60")


if __name__ == "__main__":
    unittest.main()
