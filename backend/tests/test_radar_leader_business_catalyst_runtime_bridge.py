import unittest
from dataclasses import replace

from radar.leader_business_catalyst_runtime_bridge import (
    LEADER_BUSINESS_CATALYST_RUNTIME_BRIDGE_CONTRACT_ID,
    LeaderBusinessCatalystRuntimeBridgeStatus,
    LeaderBusinessCatalystRuntimeSourceBatch,
    build_leader_business_catalyst_runtime_bridge,
)
from radar.leader_formal_research_runtime_bridge import (
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionStatus,
)
from tests import test_radar_leader_research_source_admission as helpers
from tests.test_radar_leader_business_catalyst_manual_review import (
    batch_review_entry,
)
from tests.test_radar_leader_business_catalyst_official_adapter import (
    batch_entry,
)
from tests.test_radar_leader_business_official_verification_adapter import (
    LeaderBusinessOfficialVerificationAdapterTests,
)


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        return ()


class LeaderBusinessCatalystRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.plan = helper.plan

    def source_batch(self, *, failed_index=None, missing_review_index=None):
        return LeaderBusinessCatalystRuntimeSourceBatch(
            candidate_plan_id=self.plan.candidate_set_id,
            radar_run_id=self.plan.radar_run_id,
            quote_batch_id=self.plan.quote_batch_id,
            as_of=self.plan.as_of,
            material_entries=tuple(
                batch_entry(
                    item,
                    source_status=(
                        ResearchFeatureStatus.SOURCE_FAILED
                        if (
                            failed_index is not None
                            and item.symbol
                            == self.plan.items[failed_index].symbol
                        )
                        else ResearchFeatureStatus.READY
                    ),
                )
                for item in reversed(self.plan.items)
            ),
            review_entries=tuple(
                batch_review_entry(
                    item,
                    artifact=(
                        index != failed_index
                        and index != missing_review_index
                    ),
                )
                for index, item in enumerate(self.plan.items)
            ),
        )

    def deterministic_source_batch(self):
        verification = LeaderBusinessOfficialVerificationAdapterTests(
            methodName=(
                "test_deterministic_artifacts_replay_to_existing_ready_inputs"
            )
        )
        verification.setUp()
        return LeaderBusinessCatalystRuntimeSourceBatch(
            candidate_plan_id=self.plan.candidate_set_id,
            radar_run_id=self.plan.radar_run_id,
            quote_batch_id=self.plan.quote_batch_id,
            as_of=self.plan.as_of,
            material_entries=tuple(
                verification.material_entry(item)
                for item in self.plan.items
            ),
            review_entries=(),
            verification_entries=tuple(
                verification.verification_entry(item)
                for item in self.plan.items
            ),
        )

    def build(self, source_batch=None):
        return build_leader_business_catalyst_runtime_bridge(
            self.context,
            source_batch=source_batch,
        )

    def test_complete_reviewed_batch_is_ready_in_candidate_plan_order(self):
        result = self.build(self.source_batch())
        symbols = tuple(item.symbol for item in self.plan.items)

        self.assertEqual(
            result.status,
            LeaderBusinessCatalystRuntimeBridgeStatus.READY,
        )
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(tuple(item.symbol for item in result.items), symbols)
        self.assertEqual(tuple(result.business_inputs_by_symbol), symbols)
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertEqual(
            result.to_evidence()["contractId"],
            LEADER_BUSINESS_CATALYST_RUNTIME_BRIDGE_CONTRACT_ID,
        )
        self.assertNotIn("decisionSummary", result.to_evidence())
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_missing_provider_is_explicitly_missing_without_placeholder(self):
        result = self.build()

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertEqual(
            result.reasons,
            ("leader_business_catalyst_runtime_bridge_source_missing",),
        )
        self.assertEqual(result.business_inputs_by_symbol, {})
        self.assertIsNone(result.business_review_batch)
        self.assertTrue(all(
            item.reasons
            == ("leader_business_catalyst_runtime_bridge_source_missing",)
            for item in result.items
        ))

    def test_source_failure_is_localized_without_forging_review(self):
        result = self.build(self.source_batch(failed_index=1))

        self.assertEqual(result.ready_count, self.plan.candidate_count - 1)
        self.assertEqual(result.missing_count, 1)
        self.assertEqual(
            result.items[1].status,
            LeaderBusinessCatalystRuntimeBridgeStatus.MISSING,
        )
        self.assertIn(
            "official_business_material_source_failed",
            result.items[1].reasons,
        )
        self.assertNotIn(
            result.items[1].symbol,
            result.business_inputs_by_symbol,
        )

    def test_official_material_without_human_review_stays_unconfirmed(self):
        result = self.build(self.source_batch(missing_review_index=1))

        self.assertEqual(result.ready_count, self.plan.candidate_count - 1)
        self.assertEqual(
            result.items[1].reasons,
            ("business_manual_review_missing",),
        )
        self.assertNotIn(
            self.plan.items[1].symbol,
            result.business_inputs_by_symbol,
        )

    def test_deterministic_official_batch_reaches_existing_runtime_bridge(self):
        result = self.build(self.deterministic_source_batch())

        self.assertEqual(
            result.status,
            LeaderBusinessCatalystRuntimeBridgeStatus.READY,
        )
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertTrue(all(
            item.input_value.reviews[0].review_method
            == "deterministic_official"
            for item in result.business_review_batch.items
        ))

    def test_mixed_manual_and_deterministic_batch_is_blocked(self):
        mixed = replace(
            self.deterministic_source_batch(),
            review_entries=self.source_batch().review_entries,
        )

        result = self.build(mixed)

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(
            result.reasons,
            ("leader_business_catalyst_runtime_bridge_source_unverified",),
        )

    def test_cross_run_source_batch_is_blocked_not_relabelled_missing(self):
        result = self.build(replace(
            self.source_batch(),
            radar_run_id="other-run",
        ))

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertEqual(
            result.reasons,
            ("leader_business_catalyst_runtime_bridge_source_unverified",),
        )
        self.assertEqual(result.business_inputs_by_symbol, {})
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )

    def test_reviewed_batch_reaches_existing_d8_source_admission(self):
        business_bridge = self.build(self.source_batch())
        result = build_leader_formal_research_runtime_bridge(
            self.context,
            repository=_EmptyReviewRepository(),
            business_review_batch=business_bridge.business_review_batch,
        )

        business_component = next(
            item
            for item in result.source_admission.components
            if item.name == "business_catalyst"
        )
        self.assertEqual(business_component.status.value, "ready")
        self.assertIsNotNone(result.source_admission.provider_result)
        self.assertEqual(
            tuple(
                result.source_admission.provider_result
                .business_catalyst_inputs_by_symbol
            ),
            tuple(item.symbol for item in self.plan.items),
        )


if __name__ == "__main__":
    unittest.main()
