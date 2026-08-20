import unittest
from datetime import timedelta

from radar.leader_business_catalyst_production_collector import (
    LeaderBusinessCatalystProductionFrozenBatch,
)
from radar.leader_formal_research_production_acceptance import (
    LeaderFormalResearchProductionAcceptanceStatus,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeComponentStatus,
)
from radar.leader_history_production_collector import (
    LeaderHistoryProductionFrozenBatch,
)
from radar.leader_phase6_production_readiness import (
    LeaderPhase6ProductionFrozenInputs,
    build_leader_phase6_production_readiness,
)
from radar.leader_tradability_production_collector import (
    LeaderTradabilityProductionFrozenBatch,
)
from radar.sector_rule_production_collector import (
    SectorRuleProductionFrozenBatch,
)
from tests import test_radar_leader_business_catalyst_runtime_bridge as business_helpers
from tests import test_radar_leader_history_production_collector as history_helpers
from tests import test_radar_leader_research_source_admission as source_helpers
from tests import test_radar_sector_rule_runtime_bridge as sector_helpers


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        return ()


class LeaderPhase6ProductionReadinessTests(unittest.TestCase):
    def setUp(self):
        source = source_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_in_one_batch"
            )
        )
        source.setUp()
        self.source = source
        self.context = source.context

        history = history_helpers.LeaderHistoryProductionCollectorTests(
            methodName=(
                "test_complete_candidate_universe_replays_history_in_plan_order"
            )
        )
        history.setUp()
        self.history_batch = history.bundle()

        business = business_helpers.LeaderBusinessCatalystRuntimeBridgeTests(
            methodName=(
                "test_complete_reviewed_batch_is_ready_in_candidate_plan_order"
            )
        )
        business.setUp()
        self.business_batch = business.source_batch()

        sector = sector_helpers.SectorRuleRuntimeBridgeTests(
            methodName="test_complete_versioned_evidence_returns_ready_contract"
        )
        sector.setUp()
        self.sector_batch = sector.source_batch()

    def inputs(self, *, tradability_bundle=None):
        return LeaderPhase6ProductionFrozenInputs(
            history=LeaderHistoryProductionFrozenBatch(
                expected_trade_dates=self.history_batch.expected_trade_dates,
                memberships_by_industry=(
                    self.history_batch.memberships_by_industry
                ),
                series_by_symbol=self.history_batch.series_by_symbol,
                calendar_evidence=self.history_batch.calendar_evidence,
            ),
            business_catalyst=(
                LeaderBusinessCatalystProductionFrozenBatch(
                    source_batch=self.business_batch,
                    fetched_at=self.context.as_of,
                    source_status=(
                        LeaderFormalResearchProductionSourceStatus.COMPLETED
                    ),
                )
            ),
            tradability=LeaderTradabilityProductionFrozenBatch(
                source_bundle=(
                    self.source.tradability_bundle()
                    if tradability_bundle is None
                    else tradability_bundle
                ),
                fetched_at=self.context.as_of,
                source_status=(
                    LeaderFormalResearchProductionSourceStatus.COMPLETED
                ),
            ),
            sector_rule=SectorRuleProductionFrozenBatch(
                source_batch=self.sector_batch,
                fetched_at=self.context.as_of + timedelta(seconds=2),
                source_status=(
                    LeaderFormalResearchProductionSourceStatus.COMPLETED
                ),
            ),
        )

    def test_one_entrypoint_replays_four_sources_and_keeps_d8_missing(self):
        result = build_leader_phase6_production_readiness(
            self.context,
            repository=_EmptyReviewRepository(),
            frozen_inputs=self.inputs(),
        )

        statuses = {
            item.name: item.status for item in result.assembly.components
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
            result.acceptance.status,
            LeaderFormalResearchProductionAcceptanceStatus.MISSING,
        )
        self.assertEqual(
            set(result.acceptance.missing_components),
            {
                "sector_rule",
                "history",
                "business_catalyst",
                "tradability",
                "risk",
            },
        )
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_unverified_four_source_is_visible_without_partial_payload(self):
        inputs = self.inputs(tradability_bundle=(
            self.source.tradability_bundle(include_official=False)
        ))
        result = build_leader_phase6_production_readiness(
            self.context,
            repository=_EmptyReviewRepository(),
            frozen_inputs=inputs,
        )
        statuses = {
            item.name: item.status for item in result.assembly.components
        }

        self.assertEqual(
            statuses["tradability"],
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("tradability", result.acceptance.missing_components)

    def test_evidence_is_deidentified_and_never_opens_formal_gate(self):
        result = build_leader_phase6_production_readiness(
            self.context,
            repository=_EmptyReviewRepository(),
            frozen_inputs=self.inputs(),
        )
        evidence = str(result.to_evidence())

        for item in self.context.candidate_plan.items:
            self.assertNotIn(item.symbol, evidence)
        self.assertFalse(result.to_evidence()["gate"]["formalUsable"])
        self.assertNotIn("payload", evidence.lower())


if __name__ == "__main__":
    unittest.main()
