import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_formal_research_batch import (
    LeaderFormalResearchBatchStatus,
)
from radar.leader_formal_research_runtime_bridge import (
    LEADER_FORMAL_RESEARCH_RUNTIME_BRIDGE_CONTRACT_ID,
    LeaderFormalResearchRuntimeBridgeStatus,
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassInput,
    build_leader_research_single_pass,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionStatus,
)
from radar.leader_risk_review_repository import (
    LeaderRiskReviewVersionChain,
)
from tests import test_radar_leader_risk_lifecycle_batch as risk_helpers


class _ReviewRepository:
    def __init__(self, chains=()):
        self.chains = chains
        self.calls = []

    def list_review_version_chains(self, symbols, as_of):
        self.calls.append((symbols, as_of))
        return self.chains


class LeaderFormalResearchRuntimeBridgeTests(unittest.TestCase):
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

    def chain(self, *, versions=None, candidate_id="candidate-risk-1"):
        return LeaderRiskReviewVersionChain(
            symbol=self.plan.items[0].symbol,
            document_id=self.helper.document.document_id,
            candidate_id=candidate_id,
            versions=(
                self.helper._versions()
                if versions is None
                else versions
            ),
        )

    def build(self, chains=()):
        repository = _ReviewRepository(chains)
        result = build_leader_formal_research_runtime_bridge(
            self.context,
            repository=repository,
        )
        return result, repository

    def test_two_versions_ready_only_for_matching_candidate_in_plan_order(self):
        result, repository = self.build((self.chain(),))

        symbols = tuple(item.symbol for item in self.plan.items)
        self.assertEqual(
            repository.calls,
            [(symbols, self.plan.as_of)],
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            symbols,
        )
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(
            result.status,
            LeaderFormalResearchRuntimeBridgeStatus.MISSING,
        )
        self.assertEqual(
            result.items[0].status,
            LeaderFormalResearchRuntimeBridgeStatus.READY,
        )
        self.assertTrue(all(
            item.status == LeaderFormalResearchRuntimeBridgeStatus.MISSING
            for item in result.items[1:]
        ))
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertEqual(
            result.risk_projection_batch.ready_count,
            1,
        )
        self.assertIsNotNone(result.provider_input)
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["contractId"],
            LEADER_FORMAL_RESEARCH_RUNTIME_BRIDGE_CONTRACT_ID,
        )
        self.assertNotIn("researchScore", str(evidence))
        self.assertNotIn("leaderState", str(evidence))
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_no_versions_are_explicit_missing_for_every_candidate(self):
        result, _ = self.build()

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertTrue(all(
            item.reasons
            == ("leader_formal_research_bridge_review_versions_missing",)
            for item in result.items
        ))
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.MISSING,
        )

    def test_single_version_and_future_version_never_become_ready(self):
        first, second = self.helper._versions()
        cases = (
            (
                self.chain(versions=(first,)),
                "risk_evidence_bundle_audit_history_insufficient",
            ),
            (
                self.chain(versions=(
                    first,
                    replace(
                        second,
                        as_of=self.plan.as_of.replace(
                            microsecond=0,
                        ) + timedelta(hours=1),
                    ),
                )),
                "leader_formal_research_bridge_future_evidence",
            ),
        )

        for chain, reason in cases:
            with self.subTest(reason=reason):
                result, _ = self.build((chain,))
                self.assertEqual(result.ready_count, 0)
                self.assertEqual(
                    result.items[0].status,
                    LeaderFormalResearchRuntimeBridgeStatus.MISSING,
                )
                self.assertIn(reason, result.items[0].reasons)

    def test_multiple_review_chains_are_missing_instead_of_selected(self):
        result, _ = self.build((
            self.chain(candidate_id="candidate-risk-1"),
            self.chain(candidate_id="candidate-risk-2"),
        ))

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(
            result.items[0].reasons,
            ("leader_formal_research_bridge_review_chain_ambiguous",),
        )

    def test_single_pass_exposes_existing_formal_batch_without_opening_gate(self):
        bridge, _ = self.build((self.chain(),))
        result = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.plan,
                provider_input=bridge.provider_input,
                source_context=self.context,
                as_of=self.plan.as_of,
                quote_batch=self.helper.raw["quote_batch"],
                quote_health=self.helper.raw["quote_health"],
                market_snapshot=self.helper.raw["market_snapshot"],
                sector_rows=self.helper.raw["sector_rows"],
                industry_records=self.helper.raw["industry_records"],
                security_records=self.helper.raw["security_records"],
            )
        )

        self.assertIsNotNone(result.readiness_result)
        self.assertIsNotNone(result.formal_research_result)
        self.assertEqual(
            result.formal_research_result.status,
            LeaderFormalResearchBatchStatus.MISSING,
        )
        self.assertFalse(result.formal_research_result.formal_usable)
        self.assertFalse(
            result.formal_research_result.state_transition_allowed
        )


if __name__ == "__main__":
    unittest.main()
