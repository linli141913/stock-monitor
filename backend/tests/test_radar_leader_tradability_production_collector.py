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
from radar.leader_tradability_features import SecurityLifecycleStatus
from radar.leader_tradability_production_collector import (
    LeaderTradabilityProductionFrozenBatch,
    _bundle_times,
    build_leader_tradability_production_loader,
    collect_leader_tradability_production_source,
)
from radar.sources.leader_tradability_public_poc import (
    run_public_composite_tradability_poc,
)
from tests import test_radar_leader_research_source_admission as helpers


class LeaderTradabilityProductionCollectorTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_in_one_batch"
            )
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.bundle = helper.tradability_bundle()

    def frozen(self, *, bundle=None, status=None, fetched_at=None):
        return LeaderTradabilityProductionFrozenBatch(
            source_bundle=self.bundle if bundle is None else bundle,
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
        source = collect_leader_tradability_production_source(
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
                tradability_loader=(
                    build_leader_tradability_production_loader(frozen)
                ),
            )
        )
        delivery = providers.tradability_provider(self.context)
        resolution = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="tradability",
            value=delivery,
        )
        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )

    def test_static_security_identity_without_source_time_is_accepted(self):
        query = replace(
            self.bundle.query,
            securities=tuple(
                replace(item, identity_source_time=None)
                for item in self.bundle.query.securities
            ),
        )
        bundle = replace(self.bundle, query=query)

        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=bundle),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(
            source.symbols,
            tuple(item.symbol for item in self.context.candidate_plan.items),
        )

    def test_official_ten_second_bucket_skew_is_accepted(self):
        official = tuple(
            replace(
                item,
                source_time=item.fetched_at + timedelta(seconds=6),
            )
            for item in self.bundle.official_observations
        )
        report = run_public_composite_tradability_poc(
            query=self.bundle.query,
            quotes=self.bundle.quotes,
            official_observations=official,
            aggregator_observations=self.bundle.aggregator_observations,
            executed=True,
        )
        bundle = replace(
            self.bundle,
            official_observations=official,
            report=report,
        )

        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=bundle),
        )

        self.assertIsNotNone(_bundle_times(bundle))
        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(source.reasons, ())

    def test_missing_official_observation_closes_whole_batch(self):
        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=self.helper.tradability_bundle(
                include_official=False,
            )),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)
        self.assertIn("tradability_bundle_shape_unverified", source.reasons)

    def test_bridge_reasons_are_preserved_without_raw_details(self):
        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=self.helper.tradability_bundle(
                include_official=False,
            )),
        )
        self.assertIn("tradability_bundle_shape_unverified", source.reasons)
        self.assertNotIn("000001", repr(source.reasons))

    def test_missing_aggregator_observation_closes_whole_batch(self):
        bundle = replace(
            self.bundle,
            aggregator_observations=(),
        )
        report = run_public_composite_tradability_poc(
            query=bundle.query,
            quotes=bundle.quotes,
            official_observations=bundle.official_observations,
            aggregator_observations=(),
            executed=True,
        )
        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=replace(bundle, report=report)),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_official_aggregator_conflict_never_becomes_completed(self):
        aggregator = (
            replace(
                self.bundle.aggregator_observations[0],
                lifecycle_status=SecurityLifecycleStatus.ST,
            ),
            *self.bundle.aggregator_observations[1:],
        )
        report = run_public_composite_tradability_poc(
            query=self.bundle.query,
            quotes=self.bundle.quotes,
            official_observations=self.bundle.official_observations,
            aggregator_observations=aggregator,
            executed=True,
        )
        bundle = replace(
            self.bundle,
            aggregator_observations=aggregator,
            report=report,
        )

        source = collect_leader_tradability_production_source(
            self.context,
            self.frozen(bundle=bundle),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_cross_plan_and_late_fetch_are_rejected(self):
        cases = (
            self.frozen(bundle=replace(
                self.bundle,
                candidate_plan_id="other-plan",
            )),
            self.frozen(
                fetched_at=self.context.as_of + timedelta(seconds=6),
            ),
        )

        for frozen in cases:
            with self.subTest(fetched_at=frozen.fetched_at):
                source = collect_leader_tradability_production_source(
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
                frozen = LeaderTradabilityProductionFrozenBatch(
                    source_bundle=None,
                    fetched_at=None,
                    source_status=status,
                )
                source = collect_leader_tradability_production_source(
                    self.context,
                    frozen,
                )
                self.assertEqual(source.status, status)
                self.assertEqual(source.symbols, ())
                self.assertIsNone(source.payload)

    def test_evidence_hides_quotes_observations_and_symbols(self):
        evidence = str(self.frozen().to_evidence())
        source_evidence = str(
            collect_leader_tradability_production_source(
                self.context,
                self.frozen(),
            ).to_evidence()
        )

        self.assertNotIn("observations", evidence.lower())
        self.assertNotIn("quotes", evidence.lower())
        for item in self.context.candidate_plan.items:
            self.assertNotIn(item.symbol, evidence)
            self.assertNotIn(item.symbol, source_evidence)


if __name__ == "__main__":
    unittest.main()
