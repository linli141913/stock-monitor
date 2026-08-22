"""阶段6四源、D8风险证据与正式门的单入口只读验收。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from radar.leader_business_catalyst_production_collector import (
    LeaderBusinessCatalystProductionFrozenBatch,
    build_leader_business_catalyst_production_loader,
)
from radar.leader_formal_research_production_acceptance import (
    LeaderFormalResearchProductionAcceptanceInput,
    LeaderFormalResearchProductionAcceptanceResult,
    build_leader_formal_research_production_acceptance,
    build_leader_formal_research_source_provenance_from_assembly,
)
from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeAssemblyResult,
    build_leader_formal_research_runtime_assembly,
)
from radar.leader_history_production_collector import (
    LeaderHistoryProductionFrozenBatch,
    build_leader_history_production_loader,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_tradability_production_collector import (
    LeaderTradabilityProductionFrozenBatch,
    build_leader_tradability_production_loader,
)
from radar.sector_rule_production_collector import (
    SectorRuleProductionFrozenBatch,
    build_sector_rule_production_loader,
)
from radar.sector_rule_runtime_bridge import SectorRuleRuntimeSourceBatch
from radar.sector_threshold_review import (
    SectorThresholdApprovalLoadResult,
    bind_latest_sector_threshold_approval,
)


LEADER_PHASE6_PRODUCTION_FROZEN_INPUTS_CONTRACT_ID = (
    "radar-leader-phase6-production-frozen-inputs-v1"
)
LEADER_PHASE6_PRODUCTION_READINESS_CONTRACT_ID = (
    "radar-leader-phase6-production-readiness-v1"
)
PHASE6_INPUTS_UNVERIFIED = (
    "leader_phase6_production_frozen_inputs_unverified"
)


@dataclass(frozen=True, repr=False)
class LeaderPhase6ProductionFrozenInputs:
    history: Any = field(repr=False)
    business_catalyst: Any = field(repr=False)
    tradability: Any = field(repr=False)
    sector_rule: Any = field(repr=False)
    contract_id: str = LEADER_PHASE6_PRODUCTION_FROZEN_INPUTS_CONTRACT_ID


@dataclass(frozen=True)
class LeaderPhase6ProductionReadinessResult:
    assembly: LeaderFormalResearchRuntimeAssemblyResult = field(repr=False)
    acceptance: LeaderFormalResearchProductionAcceptanceResult
    contract_id: str = LEADER_PHASE6_PRODUCTION_READINESS_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "assembly": self.assembly.to_evidence(),
            "acceptance": self.acceptance.to_evidence(),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _inputs_valid(value: Any) -> bool:
    return bool(
        type(value) is LeaderPhase6ProductionFrozenInputs
        and value.contract_id
        == LEADER_PHASE6_PRODUCTION_FROZEN_INPUTS_CONTRACT_ID
        and type(value.history) is LeaderHistoryProductionFrozenBatch
        and type(value.business_catalyst)
        is LeaderBusinessCatalystProductionFrozenBatch
        and type(value.tradability)
        is LeaderTradabilityProductionFrozenBatch
        and type(value.sector_rule) is SectorRuleProductionFrozenBatch
    )


def build_leader_phase6_production_readiness(
    context: LeaderResearchRuntimeSourceContext,
    *,
    repository: Any,
    frozen_inputs: LeaderPhase6ProductionFrozenInputs,
    sector_threshold_approval_binder: Callable[
        [SectorRuleRuntimeSourceBatch],
        SectorThresholdApprovalLoadResult,
    ] = bind_latest_sector_threshold_approval,
) -> LeaderPhase6ProductionReadinessResult:
    """一次重放四源和D8，再执行现有五源总装与正式门验收。"""

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or not _inputs_valid(frozen_inputs)
    ):
        raise ValueError(PHASE6_INPUTS_UNVERIFIED)
    providers = build_leader_formal_research_validated_provider_set(
        LeaderFormalResearchProductionSourceLoaders(
            history_loader=build_leader_history_production_loader(
                frozen_inputs.history
            ),
            business_catalyst_loader=(
                build_leader_business_catalyst_production_loader(
                    frozen_inputs.business_catalyst
                )
            ),
            tradability_loader=build_leader_tradability_production_loader(
                frozen_inputs.tradability
            ),
            sector_rule_loader=build_sector_rule_production_loader(
                frozen_inputs.sector_rule,
                threshold_approval_binder=(
                    sector_threshold_approval_binder
                ),
            ),
        )
    )
    assembly = build_leader_formal_research_runtime_assembly(
        context,
        repository=repository,
        sector_rule_provider=providers.sector_rule_provider,
        history_provider=providers.history_provider,
        business_catalyst_provider=providers.business_catalyst_provider,
        tradability_provider=providers.tradability_provider,
    )
    provenance = build_leader_formal_research_source_provenance_from_assembly(
        assembly
    )
    acceptance = build_leader_formal_research_production_acceptance(
        LeaderFormalResearchProductionAcceptanceInput(
            assembly=assembly,
            provenance=provenance,
        )
    )
    return LeaderPhase6ProductionReadinessResult(
        assembly=assembly,
        acceptance=acceptance,
    )
