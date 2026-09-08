import unittest
from dataclasses import replace
from datetime import timedelta
from radar.leader_formal_research_production_acceptance import (
    LEADER_FORMAL_RESEARCH_PRODUCTION_ACCEPTANCE_CONTRACT_ID,
    LeaderFormalResearchProductionAcceptanceInput,
    LeaderFormalResearchProductionAcceptanceStatus,
    LeaderFormalResearchSourceProvenance,
    LeaderFormalResearchSourceProvenanceStatus,
    build_leader_formal_research_source_provenance_from_assembly,
    build_leader_formal_research_production_acceptance,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceProof,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeAssemblyResult,
    LeaderFormalResearchRuntimeAssemblyStatus,
    LeaderFormalResearchRuntimeComponent,
    LeaderFormalResearchRuntimeComponentStatus,
)
from tests import test_radar_leader_risk_lifecycle_batch as risk_helpers


class LeaderFormalResearchProductionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        helper = risk_helpers.LeaderRiskLifecycleBatchTests(
            methodName=(
                "test_two_human_versions_replay_into_partial_runtime_batch"
            )
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan

    def assembly(self, *, ready=True):
        status = (
            LeaderFormalResearchRuntimeComponentStatus.READY
            if ready
            else LeaderFormalResearchRuntimeComponentStatus.MISSING
        )
        components = tuple(
            LeaderFormalResearchRuntimeComponent(
                name=name,
                status=status,
                candidate_count=self.plan.candidate_count,
                ready_count=(self.plan.candidate_count if ready else 0),
                reasons=() if ready else (f"{name}_missing",),
            )
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        return LeaderFormalResearchRuntimeAssemblyResult(
            status=(
                LeaderFormalResearchRuntimeAssemblyStatus.READY
                if ready
                else LeaderFormalResearchRuntimeAssemblyStatus.MISSING
            ),
            radar_run_id=self.plan.radar_run_id,
            candidate_plan_id=self.plan.candidate_set_id,
            candidate_count=self.plan.candidate_count,
            components=components,
            reasons=(),
            provider_input=object(),
            formal_bridge=object(),
        )

    def provenance(self, *, status="completed", as_of=None, name=None):
        as_of = as_of or self.plan.as_of
        return LeaderFormalResearchSourceProvenance(
            component_name=name or "sector_rule",
            contract_id=f"test-{name or 'sector_rule'}-v1",
            status=LeaderFormalResearchSourceProvenanceStatus(status),
            radar_run_id=self.plan.radar_run_id,
            candidate_plan_id=self.plan.candidate_set_id,
            as_of=as_of,
            source_time=as_of - timedelta(seconds=1),
            fetched_at=as_of,
            candidate_count=self.plan.candidate_count,
            ready_count=self.plan.candidate_count,
        )

    def input(self, *, assembly=None, provenance=()):
        return LeaderFormalResearchProductionAcceptanceInput(
            assembly=assembly or self.assembly(),
            provenance=tuple(provenance),
        )

    def test_unconfigured_provenance_keeps_gate_missing(self):
        result = build_leader_formal_research_production_acceptance(
            self.input()
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.MISSING,
        )
        self.assertEqual(len(result.missing_components), 5)
        self.assertIn(
            "leader_formal_research_production_provenance_missing",
            result.reasons,
        )
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)

    def test_all_real_same_round_sources_are_ready_for_review_only(self):
        provenance = tuple(
            self.provenance(name=name)
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        result = build_leader_formal_research_production_acceptance(
            self.input(provenance=provenance)
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW,
        )
        self.assertEqual(result.missing_components, ())
        self.assertEqual(result.reasons, ())
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        self.assertEqual(
            result.to_evidence()["contractId"],
            LEADER_FORMAL_RESEARCH_PRODUCTION_ACCEPTANCE_CONTRACT_ID,
        )

    def test_bound_assembly_proofs_are_the_only_auto_provenance(self):
        proofs = tuple(
            LeaderFormalResearchProductionSourceProof(
                component_name=name,
                source_contract_id=f"source-{name}-v1",
                status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
                radar_run_id=self.plan.radar_run_id,
                candidate_plan_id=self.plan.candidate_set_id,
                quote_batch_id=self.plan.quote_batch_id,
                as_of=self.plan.as_of,
                source_time=self.plan.as_of - timedelta(seconds=1),
                fetched_at=self.plan.as_of,
                expected_count=self.plan.candidate_count,
                returned_count=self.plan.candidate_count,
            )
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        assembly = replace(
            self.assembly(),
            production_source_proofs=proofs,
        )

        provenance = build_leader_formal_research_source_provenance_from_assembly(
            assembly
        )
        result = build_leader_formal_research_production_acceptance(
            self.input(assembly=assembly, provenance=provenance)
        )

        self.assertEqual(len(provenance), 5)
        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW,
        )

    def test_not_run_failed_and_unverified_sources_never_pass(self):
        for status in (
            LeaderFormalResearchSourceProvenanceStatus.NOT_RUN.value,
            LeaderFormalResearchSourceProvenanceStatus.SOURCE_FAILED.value,
            LeaderFormalResearchSourceProvenanceStatus.SOURCE_UNVERIFIED.value,
        ):
            with self.subTest(status=status):
                provenance = tuple(
                    self.provenance(
                        name=name,
                        status=status if name == "history" else "completed",
                    )
                    for name in (
                        "sector_rule",
                        "history",
                        "business_catalyst",
                        "tradability",
                        "risk",
                    )
                )
                result = build_leader_formal_research_production_acceptance(
                    self.input(provenance=provenance)
                )
                self.assertEqual(
                    result.status,
                    LeaderFormalResearchProductionAcceptanceStatus.MISSING,
                )
                self.assertIn("history", result.missing_components)

    def test_missing_source_time_never_passes(self):
        provenance = tuple(
            self.provenance(name=name)
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        provenance = (
            *provenance[:1],
            replace(provenance[1], source_time=None),
            *provenance[2:],
        )

        result = build_leader_formal_research_production_acceptance(
            self.input(provenance=provenance)
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.MISSING,
        )
        self.assertIn(
            "leader_formal_research_production_provenance_time_unverified",
            result.reasons,
        )

    def test_fetched_source_clock_skew_uses_same_five_second_boundary_as_delivery(self):
        provenance = tuple(
            self.provenance(name=name)
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        within_boundary = (
            *provenance[:3],
            replace(
                provenance[3],
                source_time=self.plan.as_of + timedelta(seconds=5),
                fetched_at=self.plan.as_of,
            ),
            provenance[4],
        )

        accepted = build_leader_formal_research_production_acceptance(
            self.input(provenance=within_boundary)
        )

        self.assertEqual(
            accepted.status,
            LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW,
        )

        beyond_boundary = (
            *provenance[:3],
            replace(
                provenance[3],
                source_time=(
                    self.plan.as_of
                    + timedelta(seconds=5, microseconds=1)
                ),
                fetched_at=self.plan.as_of,
            ),
            provenance[4],
        )
        rejected = build_leader_formal_research_production_acceptance(
            self.input(provenance=beyond_boundary)
        )
        self.assertEqual(
            rejected.status,
            LeaderFormalResearchProductionAcceptanceStatus.MISSING,
        )
        self.assertIn(
            "leader_formal_research_production_provenance_time_unverified",
            rejected.reasons,
        )

    def test_identity_or_coverage_mismatch_is_blocked_without_raw_details(self):
        provenance = tuple(
            self.provenance(name=name)
            for name in (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            )
        )
        provenance = (
            *provenance[:1],
            replace(
                provenance[1],
                candidate_plan_id="other-plan",
                ready_count=1,
            ),
            *provenance[2:],
        )
        result = build_leader_formal_research_production_acceptance(
            self.input(provenance=provenance)
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.BLOCKED,
        )
        self.assertIn(
            "leader_formal_research_production_provenance_identity_unverified",
            result.reasons,
        )
        self.assertNotIn(self.plan.items[0].symbol, str(result.to_evidence()))
        self.assertNotIn("other-plan", str(result.to_evidence()))

    def test_incomplete_runtime_assembly_is_reported_before_provenance(self):
        result = build_leader_formal_research_production_acceptance(
            self.input(assembly=self.assembly(ready=False))
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionAcceptanceStatus.MISSING,
        )
        self.assertEqual(set(result.missing_components), {
            "sector_rule",
            "history",
            "business_catalyst",
            "tradability",
            "risk",
        })


if __name__ == "__main__":
    unittest.main()
