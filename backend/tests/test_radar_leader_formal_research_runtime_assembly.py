import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

from radar.leader_formal_research_runtime_assembly import (
    LEADER_FORMAL_RESEARCH_RUNTIME_ASSEMBLY_CONTRACT_ID,
    LeaderFormalResearchRuntimeAssemblyStatus,
    LeaderFormalResearchRuntimeComponentStatus,
    build_leader_formal_research_runtime_assembly,
)
from tests import test_radar_leader_risk_lifecycle_batch as risk_helpers
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceComponentAdmission,
    LeaderResearchSourceComponentStatus,
)
from radar.leader_risk_review_repository import (
    LeaderRiskReviewVersionChain,
)


class _ReviewRepository:
    def __init__(self, result=()):
        self.result = result
        self.calls = []

    def list_review_version_chains(self, symbols, as_of):
        self.calls.append((symbols, as_of))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class LeaderFormalResearchRuntimeAssemblyTests(unittest.TestCase):
    def setUp(self):
        helper = risk_helpers.LeaderRiskLifecycleBatchTests(
            methodName=(
                "test_two_human_versions_replay_into_partial_runtime_batch"
            )
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan
        self.context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=helper.raw["quote_batch"],
            quote_health=helper.raw["quote_health"],
            security_records=helper.raw["security_records"],
            industry_records=helper.raw["industry_records"],
        )

    def build(self, **kwargs):
        repository = kwargs.pop("repository", _ReviewRepository())
        result = build_leader_formal_research_runtime_assembly(
            self.context,
            repository=repository,
            **kwargs,
        )
        return result, repository

    @staticmethod
    def component_map(result):
        return {item.name: item for item in result.components}

    def test_unconfigured_providers_are_distinct_from_missing_sources(self):
        result, repository = self.build()
        components = self.component_map(result)

        self.assertEqual(
            tuple(components),
            (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            ),
        )
        for name in (
            "sector_rule",
            "history",
            "business_catalyst",
            "tradability",
        ):
            self.assertEqual(
                components[name].status,
                LeaderFormalResearchRuntimeComponentStatus.NOT_CONFIGURED,
            )
        self.assertEqual(
            components["risk"].status,
            LeaderFormalResearchRuntimeComponentStatus.MISSING,
        )
        self.assertEqual(
            result.status,
            LeaderFormalResearchRuntimeAssemblyStatus.MISSING,
        )
        self.assertEqual(repository.calls[0][1], self.plan.as_of)

    def test_configured_none_is_missing_and_every_provider_runs_once(self):
        providers = {
            name: Mock(return_value=None)
            for name in (
                "sector_rule_provider",
                "history_provider",
                "business_catalyst_provider",
                "tradability_provider",
            )
        }
        result, _ = self.build(**providers)
        components = self.component_map(result)

        for provider in providers.values():
            provider.assert_called_once_with(self.context)
        for name in (
            "sector_rule",
            "history",
            "business_catalyst",
            "tradability",
            "risk",
        ):
            self.assertEqual(
                components[name].status,
                LeaderFormalResearchRuntimeComponentStatus.MISSING,
            )
        self.assertNotIn("not_configured", " ".join(result.reasons))

    def test_two_version_d8_chain_enters_risk_only_without_opening_gate(self):
        chain = LeaderRiskReviewVersionChain(
            symbol=self.plan.items[0].symbol,
            document_id=self.helper.document.document_id,
            candidate_id="candidate-risk-1",
            versions=self.helper._versions(),
        )

        result, _ = self.build(
            repository=_ReviewRepository((chain,)),
        )
        risk = self.component_map(result)["risk"]

        self.assertEqual(
            risk.status,
            LeaderFormalResearchRuntimeComponentStatus.PARTIAL,
        )
        self.assertEqual(risk.ready_count, 1)
        self.assertEqual(
            result.status,
            LeaderFormalResearchRuntimeAssemblyStatus.PARTIAL,
        )
        self.assertIsNotNone(result.provider_input)
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_provider_and_repository_failures_are_deidentified(self):
        providers = {
            name: Mock(side_effect=RuntimeError(f"secret-{name}"))
            for name in (
                "sector_rule_provider",
                "history_provider",
                "business_catalyst_provider",
                "tradability_provider",
            )
        }
        result, _ = self.build(
            repository=_ReviewRepository(RuntimeError("secret-database")),
            **providers,
        )
        components = self.component_map(result)

        for component in components.values():
            self.assertEqual(
                component.status,
                LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED,
            )
        evidence = str(result.to_evidence())
        self.assertNotIn("secret-", evidence)
        self.assertNotIn(self.plan.items[0].symbol, evidence)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_wrong_identity_is_source_unverified_not_missing(self):
        forged = object()
        result, _ = self.build(
            repository=_ReviewRepository((forged,)),
            sector_rule_provider=Mock(return_value=forged),
            history_provider=Mock(return_value=forged),
            business_catalyst_provider=Mock(return_value=forged),
            tradability_provider=Mock(return_value=forged),
        )
        components = self.component_map(result)

        for component in components.values():
            self.assertEqual(
                component.status,
                LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
            )
        self.assertEqual(
            result.status,
            LeaderFormalResearchRuntimeAssemblyStatus.BLOCKED,
        )

    def test_existing_stale_component_semantics_are_preserved(self):
        stale_component = LeaderResearchSourceComponentAdmission(
            name="history",
            status=LeaderResearchSourceComponentStatus.STALE,
            candidate_count=self.plan.candidate_count,
            ready_count=0,
            reasons=("public_history_snapshot_stale",),
        )
        stale_bridge = SimpleNamespace(
            source_admission=SimpleNamespace(
                components=(stale_component,),
            ),
            reasons=("public_history_snapshot_stale",),
            history_entries=None,
        )

        with patch(
            "radar.leader_formal_research_runtime_assembly."
            "build_leader_history_runtime_bridge",
            return_value=stale_bridge,
        ):
            result, _ = self.build(
                history_provider=Mock(return_value=object()),
            )

        self.assertEqual(
            self.component_map(result)["history"].status,
            LeaderFormalResearchRuntimeComponentStatus.STALE,
        )
        self.assertIn(
            "leader_formal_research_runtime_component_history_stale",
            result.health_reasons,
        )

    def test_evidence_contract_has_counts_and_stable_health_reasons_only(self):
        result, _ = self.build()
        evidence = result.to_evidence()

        self.assertEqual(
            evidence["contractId"],
            LEADER_FORMAL_RESEARCH_RUNTIME_ASSEMBLY_CONTRACT_ID,
        )
        self.assertEqual(evidence["candidateCount"], self.plan.candidate_count)
        self.assertEqual(len(evidence["components"]), 5)
        self.assertTrue(all(
            reason.startswith("leader_formal_research_runtime_component_")
            for reason in result.health_reasons
        ))
        self.assertNotIn("items", evidence)
        self.assertNotIn("symbol", str(evidence).lower())


if __name__ == "__main__":
    unittest.main()
