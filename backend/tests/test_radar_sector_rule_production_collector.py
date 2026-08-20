import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionSourceStatus,
    resolve_leader_formal_research_production_delivery,
)
from radar.sector_rule_production_collector import (
    SectorRuleProductionFrozenBatch,
    build_sector_rule_production_loader,
    collect_sector_rule_production_source,
)
from tests import test_radar_sector_rule_runtime_bridge as helpers


class SectorRuleProductionCollectorTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.SectorRuleRuntimeBridgeTests(
            methodName="test_complete_versioned_evidence_returns_ready_contract"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.source_batch = helper.source_batch()

    def frozen(self, *, source_batch=None, status=None, fetched_at=None):
        return SectorRuleProductionFrozenBatch(
            source_batch=(
                self.source_batch if source_batch is None else source_batch
            ),
            fetched_at=(
                self.context.as_of + timedelta(seconds=2)
                if fetched_at is None
                else fetched_at
            ),
            source_status=(
                status
                or LeaderFormalResearchProductionSourceStatus.COMPLETED
            ),
        )

    def test_complete_rule_evidence_replays_into_existing_provider(self):
        frozen = self.frozen()
        source = collect_sector_rule_production_source(
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
                sector_rule_loader=build_sector_rule_production_loader(
                    frozen
                ),
            )
        )
        delivery = providers.sector_rule_provider(self.context)
        resolution = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="sector_rule",
            value=delivery,
        )
        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )

    def test_one_second_source_server_clock_lead_reaches_provider(self):
        feature_batch = self.source_batch.feature_batch.model_copy(update={
            "source_time": self.context.as_of,
            "fetched_at": self.context.as_of - timedelta(seconds=1),
        })
        frozen = self.frozen(
            source_batch=replace(
                self.source_batch,
                feature_batch=feature_batch,
            ),
            fetched_at=self.context.as_of - timedelta(seconds=1),
        )

        source = collect_sector_rule_production_source(
            self.context,
            frozen,
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )

    def test_missing_formal_rule_evidence_closes_whole_batch(self):
        source = collect_sector_rule_production_source(
            self.context,
            self.frozen(source_batch=self.helper.source_batch(
                with_gaps=True,
            )),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)

    def test_cross_release_and_late_fetch_are_rejected(self):
        cases = (
            self.frozen(source_batch=replace(
                self.source_batch,
                industry_release_id="other-release",
            )),
            self.frozen(
                fetched_at=self.context.as_of + timedelta(seconds=6),
            ),
        )

        for frozen in cases:
            with self.subTest(fetched_at=frozen.fetched_at):
                source = collect_sector_rule_production_source(
                    self.context,
                    frozen,
                )
                self.assertEqual(
                    source.status,
                    LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(source.payload)

    def test_source_failed_and_not_run_preserve_distinct_statuses(self):
        for status in (
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
            LeaderFormalResearchProductionSourceStatus.NOT_RUN,
        ):
            with self.subTest(status=status):
                frozen = SectorRuleProductionFrozenBatch(
                    source_batch=None,
                    fetched_at=None,
                    source_status=status,
                )
                source = collect_sector_rule_production_source(
                    self.context,
                    frozen,
                )
                self.assertEqual(source.status, status)
                self.assertEqual(source.symbols, ())
                self.assertIsNone(source.payload)

    def test_evidence_hides_features_thresholds_and_symbols(self):
        evidence = str(self.frozen().to_evidence())
        source_evidence = str(
            collect_sector_rule_production_source(
                self.context,
                self.frozen(),
            ).to_evidence()
        )

        self.assertNotIn("threshold", evidence.lower())
        self.assertNotIn("features", evidence.lower())
        for item in self.context.candidate_plan.items:
            self.assertNotIn(item.symbol, evidence)
            self.assertNotIn(item.symbol, source_evidence)


if __name__ == "__main__":
    unittest.main()
