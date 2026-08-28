import importlib
import unittest
from dataclasses import replace

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_evidence_candidate_plan import (
    LeaderEvidenceCandidatePlan,
    LeaderEvidenceCandidatePlanItem,
    LeaderEvidenceCandidatePlanStatus,
)
from radar.leader_evidence_qualification import (
    LeaderEvidenceQualificationExcludedItem,
    LeaderEvidenceQualificationResult,
    LeaderEvidenceQualificationStatus,
)
from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassInput,
    LeaderResearchSinglePassStatus,
    build_leader_research_single_pass,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_runtime_candidate_plan import (
    derive_leader_runtime_candidate_plan_subset,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as helpers,
)


try:
    review_module = importlib.import_module(
        "radar.leader_phase6_state_decision_review"
    )
except ModuleNotFoundError:
    review_module = None


class LeaderPhase6StateDecisionReviewTests(unittest.TestCase):
    def setUp(self):
        self.helper = helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_single_pass_builds_assembly_once_and_each_"
                "feature_once_per_candidate"
            )
        )
        self.helper.setUp()
        self.parent_plan = self.helper.candidate_plan()
        qualified_symbols = tuple(
            item.symbol for item in self.parent_plan.items[:2]
        )
        self.child_plan = derive_leader_runtime_candidate_plan_subset(
            self.parent_plan,
            symbols=qualified_symbols,
            derivation_policy_id="phase6-state-review-test-v1",
        )
        raw = self.helper.raw_inputs()
        child_symbols = {
            item.symbol for item in self.child_plan.items
        }
        child_quote_batch = raw["quote_batch"].model_copy(
            update={
                "items": [
                    item
                    for item in raw["quote_batch"].items
                    if item.symbol in child_symbols
                ],
            },
            deep=True,
        )
        child_context = build_leader_research_runtime_source_context(
            candidate_plan=self.child_plan,
            quote_batch=child_quote_batch,
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        provider_input = self.helper.provider_input(self.child_plan)
        self.single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.child_plan,
                provider_input=provider_input,
                source_context=child_context,
                as_of=raw["as_of"],
                quote_batch=child_quote_batch,
                quote_health=raw["quote_health"],
                market_snapshot=raw["market_snapshot"],
                sector_rows=raw["sector_rows"],
                industry_records=raw["industry_records"],
                security_records=raw["security_records"],
            )
        )
        self.assertEqual(
            self.single_pass.status,
            LeaderResearchSinglePassStatus.PARTIAL,
        )
        parent_index = {
            item.symbol: item.index for item in self.parent_plan.items
        }
        evidence_plan = LeaderEvidenceCandidatePlan(
            status=LeaderEvidenceCandidatePlanStatus.READY,
            preliminary_candidate_plan_id=(
                self.parent_plan.candidate_set_id
            ),
            preliminary_candidate_count=self.parent_plan.candidate_count,
            candidate_plan=self.child_plan,
            items=tuple(
                LeaderEvidenceCandidatePlanItem(
                    index=index,
                    symbol=item.symbol,
                    industry_code=item.industry_code,
                    parent_index=parent_index[item.symbol],
                    selection_kind="new_candidate",
                    research_partial_score=None,
                )
                for index, item in enumerate(self.child_plan.items)
            ),
            selection_policy_id="phase6-state-review-test-v1",
        )
        excluded = tuple(
            LeaderEvidenceQualificationExcludedItem(
                index=item.index,
                symbol=item.symbol,
                status=AutomaticBusinessEvidenceStatus.MISSING,
                reasons=("business_catalyst_missing",),
            )
            for item in self.parent_plan.items[2:]
        )
        self.qualification = LeaderEvidenceQualificationResult(
            status=LeaderEvidenceQualificationStatus.READY,
            parent_candidate_plan_id=self.parent_plan.candidate_set_id,
            parent_candidate_count=self.parent_plan.candidate_count,
            candidate_plan=self.child_plan,
            evidence_plan=evidence_plan,
            excluded_items=excluded,
            qualification_id="a" * 64,
        )

    def test_review_preserves_parent_partition_and_first_denial_reasons(self):
        builder = getattr(
            review_module,
            "build_leader_phase6_state_decision_review",
            None,
        )
        self.assertTrue(callable(builder))

        result = builder(
            self.parent_plan,
            qualification=self.qualification,
            single_pass=self.single_pass,
        )

        self.assertEqual(result.status.value, "ready_for_review")
        self.assertEqual(result.parent_candidate_count, 5)
        self.assertEqual(result.qualified_candidate_count, 2)
        self.assertEqual(result.excluded_candidate_count, 3)
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            tuple(item.symbol for item in self.parent_plan.items),
        )
        self.assertEqual(
            tuple(item.review_eligible for item in result.items),
            (True, True, False, False, False),
        )
        self.assertTrue(all(
            item.first_rejection_reason
            for item in result.items
        ))
        self.assertEqual(
            tuple(
                item.first_rejection_reason
                for item in result.items[2:]
            ),
            ("business_catalyst_missing",) * 3,
        )
        evidence = result.to_evidence()
        self.assertFalse(evidence["gate"]["formalGateReady"])
        self.assertFalse(evidence["gate"]["stateTransitionAllowed"])

    def test_review_blocks_incomplete_or_cross_identity_partition(self):
        builder = getattr(
            review_module,
            "build_leader_phase6_state_decision_review",
            None,
        )
        self.assertTrue(callable(builder))
        invalid = LeaderEvidenceQualificationResult(
            status=self.qualification.status,
            parent_candidate_plan_id=(
                self.qualification.parent_candidate_plan_id
            ),
            parent_candidate_count=(
                self.qualification.parent_candidate_count
            ),
            candidate_plan=self.qualification.candidate_plan,
            evidence_plan=self.qualification.evidence_plan,
            excluded_items=self.qualification.excluded_items[:-1],
            qualification_id=self.qualification.qualification_id,
        )

        result = builder(
            self.parent_plan,
            qualification=invalid,
            single_pass=self.single_pass,
        )

        self.assertEqual(result.status.value, "blocked")
        self.assertEqual(
            result.reasons,
            ("leader_phase6_state_review_partition_unverified",),
        )
        self.assertEqual(result.items, ())
        self.assertFalse(result.state_transition_allowed)

    def test_review_rejects_any_upstream_formal_transition_flag(self):
        builder = getattr(
            review_module,
            "build_leader_phase6_state_decision_review",
            None,
        )
        self.assertTrue(callable(builder))
        tampered = replace(
            self.single_pass,
            formal_research_result=replace(
                self.single_pass.formal_research_result,
                state_transition_allowed=True,
            ),
        )

        result = builder(
            self.parent_plan,
            qualification=self.qualification,
            single_pass=tampered,
        )

        self.assertEqual(result.status.value, "blocked")
        self.assertEqual(
            result.reasons,
            ("leader_phase6_state_review_input_unverified",),
        )
        self.assertFalse(result.state_transition_allowed)

        qualification_result = builder(
            self.parent_plan,
            qualification=replace(
                self.qualification,
                state_transition_allowed=True,
            ),
            single_pass=self.single_pass,
        )
        self.assertEqual(qualification_result.status.value, "blocked")
        self.assertEqual(
            qualification_result.reasons,
            ("leader_phase6_state_review_input_unverified",),
        )

    def test_review_rejects_source_failed_item_in_ready_qualification(self):
        builder = getattr(
            review_module,
            "build_leader_phase6_state_decision_review",
            None,
        )
        self.assertTrue(callable(builder))
        failed_item = replace(
            self.qualification.excluded_items[0],
            status=AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            reasons=("business_source_failed",),
        )
        invalid = replace(
            self.qualification,
            excluded_items=(
                failed_item,
                *self.qualification.excluded_items[1:],
            ),
        )

        result = builder(
            self.parent_plan,
            qualification=invalid,
            single_pass=self.single_pass,
        )

        self.assertEqual(result.status.value, "blocked")
        self.assertEqual(
            result.reasons,
            ("leader_phase6_state_review_partition_unverified",),
        )

if __name__ == "__main__":
    unittest.main()
