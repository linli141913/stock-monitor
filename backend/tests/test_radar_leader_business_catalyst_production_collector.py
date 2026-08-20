import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_business_catalyst_production_collector import (
    LeaderBusinessCatalystProductionFrozenBatch,
    build_leader_business_catalyst_production_loader,
    collect_leader_business_catalyst_production_source,
)
from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionSourceStatus,
    resolve_leader_formal_research_production_delivery,
)
from radar.leader_research_features import ResearchFeatureStatus
from tests import test_radar_leader_business_catalyst_runtime_bridge as helpers


class LeaderBusinessCatalystProductionCollectorTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderBusinessCatalystRuntimeBridgeTests(
            methodName=(
                "test_complete_reviewed_batch_is_ready_in_candidate_plan_order"
            )
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.source_batch = helper.source_batch()

    def frozen(self, *, source_batch=None, status=None, fetched_at=None):
        return LeaderBusinessCatalystProductionFrozenBatch(
            source_batch=(
                self.source_batch if source_batch is None else source_batch
            ),
            fetched_at=(
                self.context.as_of if fetched_at is None else fetched_at
            ),
            source_status=(
                status
                or LeaderFormalResearchProductionSourceStatus.COMPLETED
            ),
        )

    def test_complete_candidate_set_replays_into_existing_provider(self):
        frozen = self.frozen()
        source = collect_leader_business_catalyst_production_source(
            self.context,
            frozen,
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(
            source.symbols,
            tuple(item.symbol for item in self.context.candidate_plan.items),
        )
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                business_catalyst_loader=(
                    build_leader_business_catalyst_production_loader(frozen)
                ),
            )
        )
        delivery = providers.business_catalyst_provider(self.context)
        resolution = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="business_catalyst",
            value=delivery,
        )
        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )

    def test_one_second_source_server_clock_lead_reaches_provider(self):
        source_batch = replace(
            self.source_batch,
            material_entries=tuple(
                replace(
                    entry,
                    catalyst_artifact=replace(
                        entry.catalyst_artifact,
                        published_at=self.context.as_of,
                    ),
                    proof_artifacts=tuple(
                        replace(proof, published_at=self.context.as_of)
                        for proof in entry.proof_artifacts
                    ),
                )
                for entry in self.source_batch.material_entries
            ),
            review_entries=tuple(
                replace(
                    entry,
                    review_artifact=replace(
                        entry.review_artifact,
                        reviewed_at=self.context.as_of,
                    ),
                )
                for entry in self.source_batch.review_entries
            ),
        )
        frozen = self.frozen(
            source_batch=source_batch,
            fetched_at=self.context.as_of - timedelta(seconds=1),
        )

        source = collect_leader_business_catalyst_production_source(
            self.context,
            frozen,
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )

    def test_missing_official_material_closes_batch_without_partial_payload(self):
        entries = list(self.source_batch.material_entries)
        entries[0] = replace(
            entries[0],
            source_status=ResearchFeatureStatus.MISSING,
        )
        runtime_source = replace(
            self.source_batch,
            material_entries=tuple(entries),
        )

        source = collect_leader_business_catalyst_production_source(
            self.context,
            self.frozen(source_batch=runtime_source),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)

    def test_missing_human_review_never_uses_unconfirmed_material(self):
        entries = list(self.source_batch.review_entries)
        entries[1] = replace(entries[1], review_artifact=None)
        runtime_source = replace(
            self.source_batch,
            review_entries=tuple(entries),
        )

        source = collect_leader_business_catalyst_production_source(
            self.context,
            self.frozen(source_batch=runtime_source),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_cross_plan_and_late_fetch_are_rejected(self):
        cases = (
            self.frozen(source_batch=replace(
                self.source_batch,
                candidate_plan_id="other-plan",
            )),
            self.frozen(
                fetched_at=self.context.as_of + timedelta(seconds=6),
            ),
        )

        for frozen in cases:
            with self.subTest(fetched_at=frozen.fetched_at):
                source = collect_leader_business_catalyst_production_source(
                    self.context,
                    frozen,
                )
                self.assertEqual(
                    source.status,
                    LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(source.payload)

    def test_source_failed_and_not_run_preserve_distinct_statuses(self):
        cases = (
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
            LeaderFormalResearchProductionSourceStatus.NOT_RUN,
        )

        for status in cases:
            with self.subTest(status=status):
                frozen = LeaderBusinessCatalystProductionFrozenBatch(
                    source_batch=None,
                    fetched_at=None,
                    source_status=status,
                )
                source = collect_leader_business_catalyst_production_source(
                    self.context,
                    frozen,
                )
                self.assertEqual(source.status, status)
                self.assertEqual(source.symbols, ())
                self.assertIsNone(source.payload)

    def test_evidence_hides_documents_reviews_and_candidate_symbols(self):
        frozen = self.frozen()
        evidence = frozen.to_evidence()
        source = collect_leader_business_catalyst_production_source(
            self.context,
            frozen,
        )

        self.assertNotIn("decision_summary", repr(evidence))
        self.assertNotIn("document_id", repr(evidence))
        for item in self.context.candidate_plan.items:
            self.assertNotIn(item.symbol, repr(evidence))
        self.assertNotIn("decision_summary", repr(source.to_evidence()))


if __name__ == "__main__":
    unittest.main()
