import importlib
import unittest
from dataclasses import replace
from types import SimpleNamespace

from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassInput,
    LeaderResearchSinglePassStatus,
    build_leader_research_single_pass,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_phase6_state_decision_review import (
    build_leader_phase6_state_decision_review,
)
from radar.leader_phase6_live_source_readiness import (
    _build_live_state_decision_review,
)
from radar.leader_runtime_candidate_plan import (
    derive_leader_runtime_candidate_plan_subset,
)
from tests import test_radar_leader_evidence_candidate_plan as scope_helpers
from tests import test_radar_leader_formal_research_batch as formal_helpers
from tests import test_radar_leader_phase6_state_decision_review as review_helpers
from tests import test_radar_sector_rule_readiness as sector_readiness_helpers


try:
    gate_module = importlib.import_module(
        "radar.leader_formal_industry_gate"
    )
except ModuleNotFoundError:
    gate_module = None


class LeaderFormalIndustryGateTests(unittest.TestCase):
    def setUp(self):
        review = review_helpers.LeaderPhase6StateDecisionReviewTests(
            methodName=(
                "test_review_preserves_parent_partition_and_first_"
                "denial_reasons"
            )
        )
        review.setUp()
        self.parent_plan = review.parent_plan
        self.child_plan = review.child_plan
        self.qualification = review.qualification

        scope = scope_helpers.LeaderEvidenceCandidatePlanTests(
            methodName=(
                "test_research_ranking_derives_small_plan_with_"
                "parent_provenance"
            )
        )
        scope.setUp()
        self.assertEqual(scope.preliminary_plan, self.parent_plan)
        _production, self.industry_scope = scope.produced_industry_scope()

        self.raw = review.helper.raw_inputs()
        self.context = build_leader_research_runtime_source_context(
            candidate_plan=self.child_plan,
            quote_batch=self.raw["quote_batch"],
            quote_health=self.raw["quote_health"],
            security_records=self.raw["security_records"],
            industry_records=self.raw["industry_records"],
        )
        formal = formal_helpers.LeaderFormalResearchBatchTests(
            methodName=(
                "test_complete_same_run_evidence_returns_ready_"
                "research_batch"
            )
        )
        formal.setUp()
        self.provider_input = review.helper.provider_input(self.child_plan)
        self.sector_rule_readiness = formal.sector_readiness()
        self.single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.child_plan,
                provider_input=self.provider_input,
                source_context=self.context,
                sector_rule_readiness=self.sector_rule_readiness,
                **self.raw,
            )
        )
        self.assertEqual(
            self.single_pass.status,
            LeaderResearchSinglePassStatus.PARTIAL,
        )

    def test_trusted_active_scope_and_parent_cross_section_are_evidence_ready(
        self,
    ):
        builder = getattr(
            gate_module,
            "build_leader_formal_industry_gate_evidence",
            None,
        )
        self.assertTrue(callable(builder))

        result = builder(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=self.single_pass,
        )

        self.assertEqual(result.status.value, "ready")
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(result.evidence_ready_count, 2)
        self.assertEqual(
            tuple(item.industry_state.value for item in result.items),
            ("observe", "observe"),
        )
        self.assertTrue(all(
            item.sector_rule_evidence_ready
            and item.cross_section_evidence_ready
            and item.evidence_ready
            for item in result.items
        ))
        self.assertTrue(all(
            item.reasons
            == ("leader_formal_industry_gate_policy_unapproved",)
            for item in result.items
        ))
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.state_transition_allowed)

    def test_policy_audit_exposes_documented_requirements_and_missing_fields(
        self,
    ):
        result = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=self.single_pass,
        )

        audit = result.policy_audit
        self.assertEqual(audit.status.value, "unapproved")
        self.assertEqual(
            audit.qualitative_requirements,
            (
                "industry_state_active",
                "not_single_stock_advance",
                "industry_turnover_qualified",
                "diffusion_persistence_or_reflux",
                "catalyst_source_reliable",
                "data_complete",
            ),
        )
        self.assertEqual(
            audit.requirement_source_references,
            (
                "docs/股票监测助手V5.0升级规划书.md::7.4评分计算合同",
                "docs/股票监测助手V5.0升级规划书.md::9.5硬门槛/行业门槛",
                "docs/股票监测助手V5.0升级规划书.md::9.8状态机正式合同",
            ),
        )
        self.assertEqual(
            audit.required_policy_fields,
            (
                "metric_fields_and_units",
                "primary_and_fallback_sources",
                "source_time_and_max_latency",
                "statistics_window",
                "minimum_sample_size",
                "normalization_formula_and_bounds",
                "single_stock_concentration_threshold",
                "industry_turnover_threshold",
                "diffusion_persistence_reflux_rule",
                "catalyst_source_admission_rule",
                "minimum_data_completeness",
                "missing_stale_conflict_policy",
                "degraded_calculation_policy",
                "entry_hold_exit_thresholds",
                "approval_identity_and_time",
            ),
        )
        self.assertIsNone(audit.observed_approval_contract_id)
        self.assertIsNone(audit.observed_approval_id)
        self.assertEqual(
            audit.reasons,
            ("leader_formal_industry_gate_policy_unapproved",),
        )
        self.assertFalse(audit.approved)
        evidence = result.to_evidence()["policyAudit"]
        self.assertEqual(evidence["status"], "unapproved")
        self.assertEqual(evidence["requiredPolicyFields"], list(
            audit.required_policy_fields
        ))
        self.assertEqual(evidence["requirementSourceReferences"], list(
            audit.requirement_source_references
        ))
        self.assertNotIn("thresholdValues", evidence)

    def test_sector_state_threshold_approval_cannot_approve_leader_gate(
        self,
    ):
        sector_approval = sector_readiness_helpers.threshold_approval()

        result = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=self.single_pass,
            policy_approval=sector_approval,
        )

        self.assertEqual(result.status.value, "ready")
        self.assertEqual(result.evidence_ready_count, 2)
        self.assertEqual(
            result.policy_audit.observed_approval_contract_id,
            "radar-sector-threshold-approval-v1",
        )
        self.assertEqual(
            result.policy_audit.observed_approval_id,
            sector_approval.approval_id,
        )
        self.assertEqual(
            result.policy_audit.reasons,
            (
                "leader_formal_industry_gate_sector_state_approval_"
                "not_applicable",
                "leader_formal_industry_gate_policy_unapproved",
            ),
        )
        self.assertTrue(all(
            item.reasons
            == ("leader_formal_industry_gate_policy_unapproved",)
            for item in result.items
        ))
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.state_transition_allowed)

    def test_unverified_policy_input_fails_closed(self):
        result = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=self.single_pass,
            policy_approval=SimpleNamespace(
                contract_id="radar-leader-formal-industry-gate-policy-v1",
                approval_id="forged-approval",
            ),
        )

        self.assertEqual(result.status.value, "blocked")
        self.assertEqual(
            result.reasons,
            ("leader_formal_industry_gate_policy_input_unverified",),
        )
        self.assertEqual(
            result.policy_audit.status.value,
            "source_unverified",
        )
        self.assertFalse(result.policy_audit.approved)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.state_transition_allowed)

    def test_scope_remains_bound_through_verified_candidate_lineage(self):
        builder = gate_module.build_leader_formal_industry_gate_evidence
        intermediate = derive_leader_runtime_candidate_plan_subset(
            self.parent_plan,
            symbols=tuple(
                item.symbol for item in self.parent_plan.items[:3]
            ),
            derivation_policy_id="phase6-industry-lineage-parent-v1",
        )
        child = derive_leader_runtime_candidate_plan_subset(
            intermediate,
            symbols=tuple(item.symbol for item in intermediate.items[:2]),
            derivation_policy_id="phase6-industry-lineage-child-v1",
        )
        review = review_helpers.LeaderPhase6StateDecisionReviewTests(
            methodName=(
                "test_review_preserves_parent_partition_and_first_"
                "denial_reasons"
            )
        )
        review.setUp()
        raw = review.helper.raw_inputs()
        context = build_leader_research_runtime_source_context(
            candidate_plan=child,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        formal = formal_helpers.LeaderFormalResearchBatchTests(
            methodName=(
                "test_complete_same_run_evidence_returns_ready_"
                "research_batch"
            )
        )
        formal.setUp()
        single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=child,
                provider_input=review.helper.provider_input(child),
                source_context=context,
                sector_rule_readiness=formal.sector_readiness(),
                **raw,
            )
        )

        result = builder(
            intermediate,
            candidate_plan=child,
            industry_scope=self.industry_scope,
            single_pass=single_pass,
        )

        self.assertEqual(result.status.value, "ready")
        self.assertEqual(result.candidate_plan_id, child.candidate_set_id)

    def test_replaced_scope_loses_trusted_producer_identity(self):
        result = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=replace(self.industry_scope, complete=True),
            single_pass=self.single_pass,
        )

        self.assertEqual(result.status.value, "blocked")
        self.assertEqual(
            result.reasons,
            ("leader_formal_industry_gate_input_unverified",),
        )

    def test_missing_cross_section_is_preserved_as_review_evidence(self):
        child_symbols = {
            item.symbol for item in self.child_plan.items
        }
        narrowed_quote_batch = self.raw["quote_batch"].model_copy(
            update={
                "items": [
                    item for item in self.raw["quote_batch"].items
                    if item.symbol in child_symbols
                ],
            },
            deep=True,
        )
        narrowed_context = build_leader_research_runtime_source_context(
            candidate_plan=self.child_plan,
            quote_batch=narrowed_quote_batch,
            quote_health=self.raw["quote_health"],
            security_records=self.raw["security_records"],
            industry_records=self.raw["industry_records"],
        )
        single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.child_plan,
                provider_input=self.provider_input,
                source_context=narrowed_context,
                as_of=self.child_plan.as_of,
                quote_batch=narrowed_quote_batch,
                quote_health=self.raw["quote_health"],
                market_snapshot=self.raw["market_snapshot"],
                sector_rows=self.raw["sector_rows"],
                industry_records=self.raw["industry_records"],
                security_records=self.raw["security_records"],
                sector_rule_readiness=self.sector_rule_readiness,
            )
        )
        gate = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=single_pass,
        )
        self.assertEqual(gate.status.value, "missing")

        result = build_leader_phase6_state_decision_review(
            self.parent_plan,
            qualification=self.qualification,
            single_pass=single_pass,
            industry_gate_evidence=gate,
        )

        self.assertEqual(result.status.value, "ready_for_review")
        self.assertIs(result.industry_gate_evidence, gate)
        qualified = tuple(
            item for item in result.items if item.review_eligible
        )
        self.assertTrue(all(
            "leader_cross_section_evidence_unavailable" in item.reasons
            for item in qualified
        ))
        self.assertFalse(result.formal_gate_ready)

    def test_state_review_replaces_placeholders_with_bound_gate_evidence(self):
        gate = gate_module.build_leader_formal_industry_gate_evidence(
            self.parent_plan,
            candidate_plan=self.child_plan,
            industry_scope=self.industry_scope,
            single_pass=self.single_pass,
        )

        try:
            result = build_leader_phase6_state_decision_review(
                self.parent_plan,
                qualification=self.qualification,
                single_pass=self.single_pass,
                industry_gate_evidence=gate,
            )
        except TypeError as exc:
            self.fail(f"状态评审尚未接收正式行业门证据: {exc}")

        qualified = tuple(
            item for item in result.items if item.review_eligible
        )
        self.assertEqual(len(qualified), 2)
        self.assertTrue(all(
            item.reasons[0]
            == "leader_formal_industry_gate_policy_unapproved"
            for item in qualified
        ))
        self.assertTrue(all(
            "leader_formal_industry_gate_unavailable" not in item.reasons
            and "leader_cross_section_evidence_unavailable"
            not in item.reasons
            for item in qualified
        ))
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["industryGateEvidence"]["status"],
            "ready",
        )
        self.assertFalse(evidence["gate"]["formalGateReady"])

    def test_live_review_keeps_parent_quotes_and_bound_industry_scope(self):
        runtime = SimpleNamespace(
            quote_batch=self.raw["quote_batch"],
            quote_health=self.raw["quote_health"],
            market_snapshot=self.raw["market_snapshot"],
            sector_rows=self.raw["sector_rows"],
            industry_records=self.raw["industry_records"],
            security_records=self.raw["security_records"],
        )
        readiness = SimpleNamespace(
            readiness=SimpleNamespace(
                assembly=SimpleNamespace(
                    provider_input=self.provider_input,
                    sector_rule_readiness=self.sector_rule_readiness,
                )
            )
        )

        try:
            result = _build_live_state_decision_review(
                SimpleNamespace(candidate_plan=self.parent_plan),
                self.qualification,
                SimpleNamespace(
                    candidate_plan=self.child_plan,
                    runtime_inputs=runtime,
                    source_context=self.context,
                ),
                readiness,
                industry_scope=self.industry_scope,
            )
        except TypeError as exc:
            self.fail(f"真实入口尚未传递行业范围: {exc}")

        self.assertEqual(result.status.value, "ready_for_review")
        gate = result.industry_gate_evidence
        self.assertIsNotNone(gate)
        self.assertEqual(gate.status.value, "ready")
        self.assertTrue(all(
            item.cross_section_evidence_ready for item in gate.items
        ))
        qualified = tuple(
            item for item in result.items if item.review_eligible
        )
        self.assertTrue(all(
            "leader_cross_section_evidence_unavailable" not in item.reasons
            for item in qualified
        ))


if __name__ == "__main__":
    unittest.main()
