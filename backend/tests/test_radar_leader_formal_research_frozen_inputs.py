import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_formal_research_frozen_inputs import (
    LeaderFormalResearchFrozenInputComponent,
    LeaderFormalResearchFrozenInputPackage,
    build_leader_formal_research_frozen_source_loaders,
)
from radar.leader_formal_research_production_collectors import (
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionSourceStatus,
    resolve_leader_formal_research_production_delivery,
)
from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeComponentStatus,
    build_leader_formal_research_runtime_assembly,
)
from tests import test_radar_leader_business_catalyst_runtime_bridge as business_helpers
from tests import test_radar_leader_research_source_admission as source_helpers
from tests import test_radar_sector_rule_runtime_bridge as sector_helpers


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        return ()


class LeaderFormalResearchFrozenInputsTests(unittest.TestCase):
    def setUp(self):
        source = source_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_in_one_batch"
            )
        )
        source.setUp()
        self.source = source
        self.context = source.context
        self.plan = source.plan
        self.symbols = tuple(item.symbol for item in self.plan.items)

        business = business_helpers.LeaderBusinessCatalystRuntimeBridgeTests(
            methodName=(
                "test_complete_reviewed_batch_is_ready_in_candidate_plan_order"
            )
        )
        business.setUp()
        self.business = business

        sector = sector_helpers.SectorRuleRuntimeBridgeTests(
            methodName="test_complete_versioned_evidence_returns_ready_contract"
        )
        sector.setUp()
        self.sector = sector

    def component(self, name, payload, **changes):
        values = {
            "component_name": name,
            "source_contract_id": f"real-{name}-frozen-source-v1",
            "status": LeaderFormalResearchProductionSourceStatus.COMPLETED,
            "source_time": self.plan.as_of - timedelta(seconds=2),
            "fetched_at": self.plan.as_of + timedelta(seconds=2),
            "symbols": self.symbols,
            "payload": payload,
        }
        values.update(changes)
        return LeaderFormalResearchFrozenInputComponent(**values)

    def package(self, **changes):
        values = {
            "radar_run_id": self.plan.radar_run_id,
            "candidate_plan_id": self.plan.candidate_set_id,
            "quote_batch_id": self.plan.quote_batch_id,
            "as_of": self.plan.as_of,
            "components": (
                self.component("history", self.source.history_entries()),
                self.component(
                    "business_catalyst",
                    self.business.source_batch(),
                ),
                self.component(
                    "tradability",
                    self.source.tradability_bundle(),
                ),
                self.component("sector_rule", self.sector.source_batch()),
            ),
        }
        values.update(changes)
        return LeaderFormalResearchFrozenInputPackage(**values)

    def test_complete_same_round_package_replays_all_four_sources(self):
        loaders = build_leader_formal_research_frozen_source_loaders(
            self.package()
        )
        providers = build_leader_formal_research_validated_provider_set(
            loaders
        )

        result = build_leader_formal_research_runtime_assembly(
            self.context,
            repository=_EmptyReviewRepository(),
            sector_rule_provider=providers.sector_rule_provider,
            history_provider=providers.history_provider,
            business_catalyst_provider=providers.business_catalyst_provider,
            tradability_provider=providers.tradability_provider,
        )

        statuses = {item.name: item.status for item in result.components}
        self.assertEqual(
            statuses,
            {
                "sector_rule": LeaderFormalResearchRuntimeComponentStatus.READY,
                "history": LeaderFormalResearchRuntimeComponentStatus.READY,
                "business_catalyst": (
                    LeaderFormalResearchRuntimeComponentStatus.READY
                ),
                "tradability": LeaderFormalResearchRuntimeComponentStatus.READY,
                "risk": LeaderFormalResearchRuntimeComponentStatus.MISSING,
            },
        )

    def test_cross_round_package_is_source_unverified(self):
        package = replace(self.package(), quote_batch_id="other-quotes")
        loaders = build_leader_formal_research_frozen_source_loaders(package)
        providers = build_leader_formal_research_validated_provider_set(
            loaders
        )

        delivery = providers.history_provider(self.context)
        result = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="history",
            value=delivery,
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(delivery.payload)

    def test_missing_component_remains_not_run(self):
        package = replace(
            self.package(),
            components=tuple(
                item
                for item in self.package().components
                if item.component_name != "tradability"
            ),
        )
        loaders = build_leader_formal_research_frozen_source_loaders(package)
        providers = build_leader_formal_research_validated_provider_set(
            loaders
        )

        delivery = providers.tradability_provider(self.context)
        result = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="tradability",
            value=delivery,
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.NOT_RUN,
        )
        self.assertIsNone(delivery.payload)

    def test_duplicate_component_is_source_unverified(self):
        package = self.package()
        duplicate = replace(
            package,
            components=(*package.components, package.components[0]),
        )
        loaders = build_leader_formal_research_frozen_source_loaders(duplicate)
        providers = build_leader_formal_research_validated_provider_set(
            loaders
        )

        delivery = providers.history_provider(self.context)

        self.assertEqual(
            delivery.proof.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(delivery.payload)

    def test_noncompleted_component_cannot_carry_payload(self):
        package = self.package()
        history = replace(
            package.components[0],
            status=LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
        )
        loaders = build_leader_formal_research_frozen_source_loaders(replace(
            package,
            components=(history, *package.components[1:]),
        ))
        providers = build_leader_formal_research_validated_provider_set(
            loaders
        )

        delivery = providers.history_provider(self.context)

        self.assertEqual(
            delivery.proof.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(delivery.payload)

    def test_package_evidence_never_exposes_symbols_or_payload(self):
        evidence = str(self.package().to_evidence())

        self.assertNotIn(self.symbols[0], evidence)
        self.assertNotIn("history_entries", evidence)
        self.assertIn("componentCount", evidence)


if __name__ == "__main__":
    unittest.main()
