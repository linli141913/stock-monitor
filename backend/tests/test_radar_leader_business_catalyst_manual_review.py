import unittest
from dataclasses import fields, replace
from datetime import timedelta

from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    build_leader_business_catalyst_features,
)
from radar.leader_business_catalyst_manual_review import (
    LEADER_BUSINESS_MANUAL_REVIEW_ADAPTER_CONTRACT_ID,
    LeaderOfficialBusinessManualReviewBatchEntry,
    LeaderOfficialBusinessManualReviewBatchStatus,
    LeaderOfficialBusinessManualReviewArtifact,
    ManualReviewerKind,
    apply_official_business_manual_review,
    apply_official_business_manual_reviews_batch,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchStatus,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
    build_verified_leader_research_provider_input,
)
from radar.leader_business_catalyst_official_adapter import (
    build_leader_business_catalyst_inputs_from_official_artifacts_batch,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)
from tests.test_radar_leader_business_catalyst_official_adapter import (
    AS_OF,
    batch_entry,
    build as build_material,
)


def review_artifact(**changes):
    value = LeaderOfficialBusinessManualReviewArtifact(
        review_version="manual-review-v1",
        supersedes_review_version=None,
        symbol="000001",
        industry_code="66",
        industry_release_id="industry-release-1",
        catalyst_id="catalyst-66-20260809-v1",
        relation=BusinessCatalystRelation.DIRECT,
        reviewer_kind=ManualReviewerKind.HUMAN,
        reviewer_key="reviewer-local-1",
        reviewed_at=AS_OF - timedelta(hours=1),
        effective_until=AS_OF + timedelta(days=30),
        basis_evidence_ids=("business-proof-000001-v1",),
        basis_catalyst_id="catalyst-66-20260809-v1",
        decision_summary="人工核对官方材料后的压缩结论",
    )
    return replace(value, **changes)


def batch_review_entry(item, *, artifact=True):
    return LeaderOfficialBusinessManualReviewBatchEntry(
        symbol=item.symbol,
        review_artifact=(
            review_artifact(
                review_version=f"manual-review-{item.symbol}-v1",
                symbol=item.symbol,
                industry_code=item.industry_code,
                industry_release_id=item.industry_release_id,
                catalyst_id=(
                    f"catalyst-{item.industry_code}-20260809-v1"
                ),
                basis_evidence_ids=(
                    f"business-proof-{item.symbol}-v1",
                ),
                basis_catalyst_id=(
                    f"catalyst-{item.industry_code}-20260809-v1"
                ),
                reviewed_at=item.as_of - timedelta(hours=1),
                effective_until=item.as_of + timedelta(days=30),
            )
            if artifact
            else None
        ),
    )


class LeaderBusinessCatalystManualReviewTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        self.plan = self.f6.candidate_plan()

    def material_batch(self, *, failed_index=None):
        return build_leader_business_catalyst_inputs_from_official_artifacts_batch(
            candidate_plan=self.plan,
            entries=tuple(
                batch_entry(
                    item,
                    source_status=(
                        ResearchFeatureStatus.SOURCE_FAILED
                        if index == failed_index
                        else ResearchFeatureStatus.READY
                    ),
                )
                for index, item in enumerate(self.plan.items)
            ),
        )

    def test_human_direct_review_builds_replayable_research_input(self):
        result = apply_official_business_manual_review(
            build_material(),
            review_artifact(),
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.input_value)
        self.assertTrue(result.review_id.startswith("business-review:"))
        feature = build_leader_business_catalyst_features(
            result.input_value
        )
        self.assertEqual(feature.status, ResearchFeatureStatus.READY)
        self.assertEqual(feature.relation, BusinessCatalystRelation.DIRECT)
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        self.assertNotIn("压缩结论", repr(result))

    def test_disproved_and_unconfirmed_human_reviews_preserve_semantics(self):
        cases = (
            (
                BusinessCatalystRelation.DISPROVED,
                ResearchFeatureStatus.READY,
            ),
            (
                BusinessCatalystRelation.UNCONFIRMED,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ),
        )

        for relation, expected_status in cases:
            with self.subTest(relation=relation):
                result = apply_official_business_manual_review(
                    build_material(),
                    review_artifact(relation=relation),
                )
                feature = build_leader_business_catalyst_features(
                    result.input_value
                )
                self.assertEqual(result.status, ResearchFeatureStatus.READY)
                self.assertEqual(feature.status, expected_status)
                self.assertEqual(feature.relation, relation)

    def test_review_artifact_has_no_ai_or_caller_review_method_field(self):
        names = {
            item.name
            for item in fields(LeaderOfficialBusinessManualReviewArtifact)
        }

        self.assertNotIn("review_method", names)
        self.assertNotIn("ai_analysis", names)
        self.assertEqual(tuple(ManualReviewerKind), (ManualReviewerKind.HUMAN,))

        result = apply_official_business_manual_review(
            build_material(),
            review_artifact(reviewer_kind="human"),
        )
        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.input_value)

    def test_identity_and_basis_drift_are_rejected_without_input(self):
        cases = (
            review_artifact(symbol="000002"),
            review_artifact(basis_evidence_ids=("business-proof-missing",)),
            review_artifact(basis_catalyst_id="other-catalyst"),
            review_artifact(reviewer_key=""),
        )

        for artifact in cases:
            with self.subTest(version=artifact.review_version):
                result = apply_official_business_manual_review(
                    build_material(),
                    artifact,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.input_value)

    def test_future_expired_and_malformed_review_times_fail_closed(self):
        cases = (
            review_artifact(reviewed_at=AS_OF + timedelta(seconds=1)),
            review_artifact(effective_until=AS_OF - timedelta(seconds=1)),
            review_artifact(reviewed_at="bad-time"),
        )

        for artifact in cases:
            with self.subTest(effective_until=artifact.effective_until):
                result = apply_official_business_manual_review(
                    build_material(),
                    artifact,
                )
                self.assertNotEqual(result.status, ResearchFeatureStatus.READY)
                self.assertIsNone(result.input_value)

    def test_material_adapter_identity_cannot_be_relabelled(self):
        material = replace(
            build_material(),
            contract_id="caller-material-adapter-v1",
        )

        result = apply_official_business_manual_review(
            material,
            review_artifact(),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_manual_review_material_unverified",),
        )
        self.assertIsNone(result.input_value)
        self.assertEqual(
            result.contract_id,
            LEADER_BUSINESS_MANUAL_REVIEW_ADAPTER_CONTRACT_ID,
        )

    def test_review_id_is_deterministic_and_summary_is_not_exposed(self):
        first = apply_official_business_manual_review(
            build_material(),
            review_artifact(),
        )
        replay = apply_official_business_manual_review(
            build_material(),
            review_artifact(),
        )
        changed = apply_official_business_manual_review(
            build_material(),
            review_artifact(decision_summary="另一份人工结论"),
        )

        self.assertEqual(first.review_id, replay.review_id)
        self.assertNotEqual(first.review_id, changed.review_id)
        self.assertNotIn("decisionSummary", first.to_evidence())
        self.assertNotIn("压缩结论", repr(first))

    def test_batch_restores_plan_order_and_exposes_read_only_reviewed_map(self):
        result = apply_official_business_manual_reviews_batch(
            self.material_batch(),
            tuple(
                batch_review_entry(item)
                for item in reversed(self.plan.items)
            ),
        )
        symbols = tuple(item.symbol for item in self.plan.items)

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessManualReviewBatchStatus.READY,
        )
        self.assertEqual(tuple(result.inputs_by_symbol), symbols)
        self.assertEqual(tuple(item.symbol for item in result.items), symbols)
        self.assertTrue(all(
            build_leader_business_catalyst_features(value).relation
            == BusinessCatalystRelation.DIRECT
            for value in result.inputs_by_symbol.values()
        ))
        with self.assertRaises(TypeError):
            result.inputs_by_symbol["000999"] = result.items[0].input_value
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_batch_preserves_material_failure_without_forging_review(self):
        entries = tuple(
            batch_review_entry(item, artifact=index != 1)
            for index, item in enumerate(self.plan.items)
        )

        result = apply_official_business_manual_reviews_batch(
            self.material_batch(failed_index=1),
            entries,
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessManualReviewBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertIsNone(result.items[1].input_value)
        self.assertNotIn(self.plan.items[1].symbol, result.inputs_by_symbol)

    def test_batch_keeps_missing_reviews_explicit(self):
        result = apply_official_business_manual_reviews_batch(
            self.material_batch(),
            tuple(
                batch_review_entry(item, artifact=False)
                for item in self.plan.items
            ),
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessManualReviewBatchStatus.MISSING,
        )
        self.assertEqual(result.inputs_by_symbol, {})
        self.assertTrue(all(
            item.reasons == ("business_manual_review_missing",)
            for item in result.items
        ))

    def test_batch_rejects_missing_duplicate_and_extra_candidate_entries(self):
        entries = tuple(
            batch_review_entry(item) for item in self.plan.items
        )
        cases = (
            entries[:-1],
            (*entries, entries[0]),
            (*entries, replace(entries[0], symbol="000999")),
        )

        for malformed in cases:
            with self.subTest(size=len(malformed)):
                result = apply_official_business_manual_reviews_batch(
                    self.material_batch(),
                    malformed,
                )
                self.assertEqual(
                    result.status,
                    LeaderOfficialBusinessManualReviewBatchStatus.BLOCKED,
                )
                self.assertEqual(result.inputs_by_symbol, {})

    def test_reviewed_batch_enters_existing_provider_as_analysis_only(self):
        reviewed = apply_official_business_manual_reviews_batch(
            self.material_batch(),
            tuple(
                batch_review_entry(item) for item in self.plan.items
            ),
        )
        raw = self.f6.raw_inputs()
        context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        provider_input = build_verified_leader_research_provider_input(
            context,
            provider_contract_id="official-business-manual-review-batch-v1",
            business_catalyst_inputs_by_symbol=(
                reviewed.inputs_by_symbol
            ),
        )
        provider = build_leader_research_input_provider_batch_from_plan(
            provider_input
        )

        self.assertEqual(
            provider.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(provider.business_catalyst_inputs_by_symbol),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertFalse(provider.formal_usable)
        self.assertFalse(provider.state_transition_allowed)


if __name__ == "__main__":
    unittest.main()
