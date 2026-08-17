import unittest
from datetime import timedelta
from unittest.mock import Mock

from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
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


class LeaderFormalResearchProductionCollectorsTests(unittest.TestCase):
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
        self.assertEqual(business.plan.candidate_set_id, self.plan.candidate_set_id)
        self.assertEqual(sector.plan.candidate_set_id, self.plan.candidate_set_id)

    def collected(self, component_name, payload, *, status=None, symbols=None):
        return LeaderFormalResearchProductionCollectedSource(
            component_name=component_name,
            source_contract_id=f"real-{component_name}-source-v1",
            status=(
                status
                or LeaderFormalResearchProductionSourceStatus.COMPLETED
            ),
            source_time=self.plan.as_of - timedelta(seconds=2),
            fetched_at=self.plan.as_of + timedelta(seconds=2),
            symbols=self.symbols if symbols is None else symbols,
            payload=payload,
        )

    def test_all_four_sources_must_replay_before_runtime_delivery(self):
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                history_loader=Mock(return_value=self.collected(
                    "history",
                    self.source.history_entries(),
                )),
                business_catalyst_loader=Mock(return_value=self.collected(
                    "business_catalyst",
                    self.business.source_batch(),
                )),
                tradability_loader=Mock(return_value=self.collected(
                    "tradability",
                    self.source.tradability_bundle(),
                )),
                sector_rule_loader=Mock(return_value=self.collected(
                    "sector_rule",
                    self.sector.source_batch(),
                )),
            )
        )

        result = build_leader_formal_research_runtime_assembly(
            self.context,
            repository=_EmptyReviewRepository(),
            sector_rule_provider=providers.sector_rule_provider,
            history_provider=providers.history_provider,
            business_catalyst_provider=providers.business_catalyst_provider,
            tradability_provider=providers.tradability_provider,
        )

        statuses = {
            item.name: item.status for item in result.components
        }
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
        self.assertEqual(
            tuple(
                proof.component_name
                for proof in result.production_source_proofs
            ),
            (
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            ),
        )

    def test_business_missing_review_is_downgraded_and_payload_removed(self):
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                business_catalyst_loader=Mock(return_value=self.collected(
                    "business_catalyst",
                    self.business.source_batch(missing_review_index=1),
                )),
            )
        )

        delivery = providers.business_catalyst_provider(self.context)
        result = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="business_catalyst",
            value=delivery,
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            delivery.proof.returned_count,
            self.plan.candidate_count - 1,
        )
        self.assertIsNone(delivery.payload)

    def test_history_missing_entry_cannot_keep_completed_declaration(self):
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                history_loader=Mock(return_value=self.collected(
                    "history",
                    self.source.history_entries()[:-1],
                )),
            )
        )

        delivery = providers.history_provider(self.context)

        self.assertEqual(
            delivery.proof.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(delivery.payload)
        self.assertLess(
            delivery.proof.returned_count,
            self.plan.candidate_count,
        )

    def test_not_run_source_is_not_replayed_or_promoted(self):
        loader = Mock(return_value=self.collected(
            "tradability",
            None,
            status=LeaderFormalResearchProductionSourceStatus.NOT_RUN,
            symbols=(),
        ))
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                tradability_loader=loader,
            )
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
        loader.assert_called_once_with(self.context)


if __name__ == "__main__":
    unittest.main()
