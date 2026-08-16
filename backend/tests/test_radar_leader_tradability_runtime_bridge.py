import unittest
from dataclasses import replace

from radar.leader_formal_research_runtime_bridge import (
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionStatus,
)
from radar.leader_tradability_features import SecurityLifecycleStatus
from radar.leader_tradability_runtime_bridge import (
    LEADER_TRADABILITY_RUNTIME_BRIDGE_CONTRACT_ID,
    LeaderTradabilityRuntimeBridgeStatus,
    build_leader_tradability_runtime_bridge,
)
from radar.sources.leader_tradability_public_poc import (
    run_public_composite_tradability_poc,
)
from tests import test_radar_leader_research_source_admission as helpers


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        return ()


class LeaderTradabilityRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.plan = helper.plan

    def build(self, tradability_bundle=None):
        return build_leader_tradability_runtime_bridge(
            self.context,
            tradability_bundle=tradability_bundle,
        )

    def test_complete_same_quote_batch_is_ready_in_candidate_plan_order(self):
        result = self.build(self.helper.tradability_bundle())
        symbols = tuple(item.symbol for item in self.plan.items)

        self.assertEqual(
            result.status,
            LeaderTradabilityRuntimeBridgeStatus.READY,
        )
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(tuple(item.symbol for item in result.items), symbols)
        self.assertEqual(tuple(result.tradability_inputs_by_symbol), symbols)
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertEqual(
            result.to_evidence()["contractId"],
            LEADER_TRADABILITY_RUNTIME_BRIDGE_CONTRACT_ID,
        )
        self.assertNotIn("records", str(result.to_evidence()).lower())
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_missing_provider_is_explicitly_missing_without_placeholder(self):
        result = self.build()

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertEqual(
            result.reasons,
            ("leader_tradability_runtime_bridge_source_missing",),
        )
        self.assertEqual(result.tradability_inputs_by_symbol, {})
        self.assertIsNone(result.tradability_admission_value)
        self.assertTrue(all(
            item.reasons
            == ("leader_tradability_runtime_bridge_source_missing",)
            for item in result.items
        ))

    def test_missing_official_observation_preserves_partial_semantics(self):
        result = self.build(
            self.helper.tradability_bundle(include_official=False)
        )

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertIn("public_official_observation_missing", result.reasons)
        self.assertTrue(any(
            "public_official_observation_missing" in item.reasons
            for item in result.items
        ))
        tradability_component = next(
            item
            for item in result.source_admission.components
            if item.name == "tradability"
        )
        self.assertEqual(tradability_component.status.value, "partial")

    def test_official_aggregator_conflict_is_blocked_with_original_reason(self):
        bundle = self.helper.tradability_bundle()
        aggregator = (
            replace(
                bundle.aggregator_observations[0],
                lifecycle_status=SecurityLifecycleStatus.ST,
            ),
            *bundle.aggregator_observations[1:],
        )
        report = run_public_composite_tradability_poc(
            query=bundle.query,
            quotes=bundle.quotes,
            official_observations=bundle.official_observations,
            aggregator_observations=aggregator,
            executed=True,
        )
        result = self.build(replace(
            bundle,
            aggregator_observations=aggregator,
            report=report,
        ))

        self.assertEqual(result.ready_count, 0)
        self.assertIn("public_lifecycle_status_conflict", result.reasons)
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )
        self.assertEqual(result.tradability_inputs_by_symbol, {})

    def test_cross_run_or_forged_report_is_source_unverified(self):
        bundle = self.helper.tradability_bundle()
        forged_record = replace(
            bundle.report.records[0],
            reasons=("secret_token_value",),
        )
        cases = (
            replace(bundle, radar_run_id="other-run"),
            replace(
                bundle,
                report=replace(
                    bundle.report,
                    records=(
                        forged_record,
                        *bundle.report.records[1:],
                    ),
                ),
            ),
        )

        for malformed in cases:
            with self.subTest(run_id=malformed.radar_run_id):
                result = self.build(malformed)
                self.assertEqual(result.ready_count, 0)
                self.assertEqual(
                    result.reasons,
                    ("leader_tradability_runtime_bridge_source_unverified",),
                )
                self.assertEqual(
                    result.source_admission.status,
                    LeaderResearchSourceAdmissionStatus.BLOCKED,
                )
                self.assertNotIn(
                    "secret_token_value",
                    str(result.to_evidence()),
                )

    def test_tradability_bundle_reaches_existing_d8_source_admission(self):
        bridge = self.build(self.helper.tradability_bundle())
        result = build_leader_formal_research_runtime_bridge(
            self.context,
            repository=_EmptyReviewRepository(),
            tradability_bundle=bridge.tradability_admission_value,
        )

        component = next(
            item
            for item in result.source_admission.components
            if item.name == "tradability"
        )
        self.assertEqual(component.status.value, "ready")
        self.assertIsNotNone(result.source_admission.provider_result)
        self.assertEqual(
            tuple(
                result.source_admission.provider_result
                .tradability_inputs_by_symbol
            ),
            tuple(item.symbol for item in self.plan.items),
        )


if __name__ == "__main__":
    unittest.main()
