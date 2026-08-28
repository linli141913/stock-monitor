"""阶段6龙头正式研究五源只读总装与健康诊断。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.leader_business_catalyst_runtime_bridge import (
    SOURCE_UNVERIFIED as BUSINESS_SOURCE_UNVERIFIED,
    build_leader_business_catalyst_runtime_bridge,
)
from radar.leader_formal_research_runtime_bridge import (
    CHAIN_UNVERIFIED,
    REPOSITORY_UNAVAILABLE,
    SOURCE_ADMISSION_UNVERIFIED,
    LeaderFormalResearchRuntimeBridgeResult,
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    build_leader_formal_research_risk_source_proof,
    is_leader_formal_research_production_delivery,
    resolve_leader_formal_research_production_delivery,
)
from radar.leader_history_runtime_bridge import (
    SOURCE_UNVERIFIED as HISTORY_SOURCE_UNVERIFIED,
    build_leader_history_runtime_bridge,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceComponentAdmission,
    LeaderResearchSourceComponentStatus,
)
from radar.leader_tradability_runtime_bridge import (
    SOURCE_UNVERIFIED as TRADABILITY_SOURCE_UNVERIFIED,
    build_leader_tradability_runtime_bridge,
)
from radar.repository import RepositoryStateError
from radar.sector_rule_runtime_bridge import (
    SOURCE_UNVERIFIED as SECTOR_SOURCE_UNVERIFIED,
    SectorRuleRuntimeBridgeResult,
    SectorRuleRuntimeBridgeStatus,
    build_sector_rule_runtime_bridge,
)


LEADER_FORMAL_RESEARCH_RUNTIME_ASSEMBLY_CONTRACT_ID = (
    "radar-leader-formal-research-runtime-assembly-v1"
)
ASSEMBLY_CONTEXT_UNVERIFIED = (
    "leader_formal_research_runtime_assembly_context_unverified"
)
COMPONENT_NAMES = (
    "sector_rule",
    "history",
    "business_catalyst",
    "tradability",
    "risk",
)
Provider = Callable[[LeaderResearchRuntimeSourceContext], Any]


class LeaderFormalResearchRuntimeAssemblyStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"


class LeaderFormalResearchRuntimeComponentStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_CONFIGURED = "not_configured"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    STALE = "stale"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderFormalResearchRuntimeComponent:
    name: str
    status: LeaderFormalResearchRuntimeComponentStatus
    candidate_count: int
    ready_count: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderFormalResearchRuntimeAssemblyResult:
    status: LeaderFormalResearchRuntimeAssemblyStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    components: Tuple[LeaderFormalResearchRuntimeComponent, ...]
    reasons: Tuple[str, ...]
    provider_input: Any = field(repr=False)
    formal_bridge: LeaderFormalResearchRuntimeBridgeResult = field(
        repr=False,
    )
    sector_rule_readiness: Any = field(default=None, repr=False)
    production_source_proofs: Tuple[Any, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    contract_id: str = LEADER_FORMAL_RESEARCH_RUNTIME_ASSEMBLY_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def health_reasons(self) -> Tuple[str, ...]:
        return tuple(
            "leader_formal_research_runtime_component_"
            f"{item.name}_{item.status.value}"
            for item in self.components
            if item.status != LeaderFormalResearchRuntimeComponentStatus.READY
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "healthReasons": list(self.health_reasons),
            "components": [item.to_evidence() for item in self.components],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True)
class _ProviderResolution:
    configured: bool
    failed: bool
    source_unverified: bool = False
    bridge: Any = field(default=None, repr=False)
    proof: Any = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()


class _UnavailableReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        raise RepositoryStateError("review repository unavailable")


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _resolve_provider(
    context: LeaderResearchRuntimeSourceContext,
    provider: Optional[Provider],
    bridge_builder: Callable[..., Any],
    value_key: str,
    component_name: str,
) -> _ProviderResolution:
    if provider is None:
        return _ProviderResolution(configured=False, failed=False)
    try:
        source_value = provider(context)
        proof = None
        if is_leader_formal_research_production_delivery(source_value):
            delivery = resolve_leader_formal_research_production_delivery(
                context,
                component_name=component_name,
                value=source_value,
            )
            proof = (
                delivery.proof
                if getattr(delivery.proof, "component_name", None)
                == component_name
                else None
            )
            if (
                delivery.status
                == LeaderFormalResearchProductionDeliveryResolutionStatus
                .SOURCE_FAILED
            ):
                return _ProviderResolution(
                    configured=True,
                    failed=True,
                    proof=proof,
                    reasons=delivery.reasons,
                )
            if (
                delivery.status
                == LeaderFormalResearchProductionDeliveryResolutionStatus
                .SOURCE_UNVERIFIED
            ):
                return _ProviderResolution(
                    configured=True,
                    failed=False,
                    source_unverified=True,
                    proof=proof,
                    reasons=delivery.reasons,
                )
            source_value = delivery.payload
        bridge = bridge_builder(context, **{value_key: source_value})
    except Exception:
        return _ProviderResolution(configured=True, failed=True)
    return _ProviderResolution(
        configured=True,
        failed=False,
        bridge=bridge,
        proof=proof,
    )


def _mapped_status(
    value: LeaderResearchSourceComponentStatus,
) -> LeaderFormalResearchRuntimeComponentStatus:
    return {
        LeaderResearchSourceComponentStatus.READY: (
            LeaderFormalResearchRuntimeComponentStatus.READY
        ),
        LeaderResearchSourceComponentStatus.PARTIAL: (
            LeaderFormalResearchRuntimeComponentStatus.PARTIAL
        ),
        LeaderResearchSourceComponentStatus.MISSING: (
            LeaderFormalResearchRuntimeComponentStatus.MISSING
        ),
        LeaderResearchSourceComponentStatus.SOURCE_FAILED: (
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED
        ),
        LeaderResearchSourceComponentStatus.STALE: (
            LeaderFormalResearchRuntimeComponentStatus.STALE
        ),
        LeaderResearchSourceComponentStatus.SOURCE_UNVERIFIED: (
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
        ),
        LeaderResearchSourceComponentStatus.BLOCKED: (
            LeaderFormalResearchRuntimeComponentStatus.BLOCKED
        ),
    }[value]


def _admission_component(
    bridge: Any,
    component_name: str,
) -> Optional[LeaderResearchSourceComponentAdmission]:
    return next((
        item
        for item in bridge.source_admission.components
        if item.name == component_name
    ), None)


def _provider_component(
    *,
    name: str,
    admission_name: str,
    resolution: _ProviderResolution,
    candidate_count: int,
    unverified_reason: str,
) -> LeaderFormalResearchRuntimeComponent:
    if not resolution.configured:
        return LeaderFormalResearchRuntimeComponent(
            name=name,
            status=LeaderFormalResearchRuntimeComponentStatus.NOT_CONFIGURED,
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(
                f"leader_formal_research_runtime_{name}_provider_not_configured",
            ),
        )
    if resolution.source_unverified:
        return LeaderFormalResearchRuntimeComponent(
            name=name,
            status=(
                LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
            ),
            candidate_count=candidate_count,
            ready_count=0,
            reasons=_dedupe((
                *resolution.reasons,
                f"leader_formal_research_runtime_{name}_source_unverified",
            )),
        )
    if resolution.failed or resolution.bridge is None:
        return LeaderFormalResearchRuntimeComponent(
            name=name,
            status=LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED,
            candidate_count=candidate_count,
            ready_count=0,
            reasons=_dedupe((
                *resolution.reasons,
                f"leader_formal_research_runtime_{name}_provider_failed",
            )),
        )
    bridge = resolution.bridge
    admission = _admission_component(bridge, admission_name)
    bridge_reasons = tuple(getattr(bridge, "reasons", ()))
    admission_reasons = tuple(getattr(admission, "reasons", ()))
    source_unverified = (
        unverified_reason in bridge_reasons
        or any("unverified" in reason for reason in admission_reasons)
    )
    if source_unverified:
        status = (
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
        )
    elif admission is None:
        status = LeaderFormalResearchRuntimeComponentStatus.MISSING
    else:
        status = _mapped_status(admission.status)
    return LeaderFormalResearchRuntimeComponent(
        name=name,
        status=status,
        candidate_count=candidate_count,
        ready_count=(admission.ready_count if admission is not None else 0),
        reasons=_dedupe((*bridge_reasons, *admission_reasons)),
    )


def _sector_component(
    resolution: _ProviderResolution,
    candidate_count: int,
) -> LeaderFormalResearchRuntimeComponent:
    if not resolution.configured:
        return LeaderFormalResearchRuntimeComponent(
            name="sector_rule",
            status=LeaderFormalResearchRuntimeComponentStatus.NOT_CONFIGURED,
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(
                "leader_formal_research_runtime_sector_rule_provider_not_configured",
            ),
        )
    if resolution.source_unverified:
        return LeaderFormalResearchRuntimeComponent(
            name="sector_rule",
            status=(
                LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
            ),
            candidate_count=candidate_count,
            ready_count=0,
            reasons=_dedupe((
                *resolution.reasons,
                "leader_formal_research_runtime_sector_rule_source_unverified",
            )),
        )
    if resolution.failed or resolution.bridge is None:
        return LeaderFormalResearchRuntimeComponent(
            name="sector_rule",
            status=LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED,
            candidate_count=candidate_count,
            ready_count=0,
            reasons=_dedupe((
                *resolution.reasons,
                "leader_formal_research_runtime_sector_rule_provider_failed",
            )),
        )
    bridge: SectorRuleRuntimeBridgeResult = resolution.bridge
    if SECTOR_SOURCE_UNVERIFIED in bridge.reasons:
        status = LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
    elif bridge.status == SectorRuleRuntimeBridgeStatus.READY:
        status = LeaderFormalResearchRuntimeComponentStatus.READY
    else:
        status = LeaderFormalResearchRuntimeComponentStatus.MISSING
    return LeaderFormalResearchRuntimeComponent(
        name="sector_rule",
        status=status,
        candidate_count=candidate_count,
        ready_count=(candidate_count if status.value == "ready" else 0),
        reasons=bridge.reasons,
    )


def _risk_component(
    bridge: LeaderFormalResearchRuntimeBridgeResult,
    *,
    candidate_count: int,
    repository_failed: bool,
    resolution: Optional[_ProviderResolution] = None,
) -> LeaderFormalResearchRuntimeComponent:
    admission = _admission_component(bridge, "risk")
    reasons = _dedupe((
        *bridge.reasons,
        *(admission.reasons if admission is not None else ()),
    ))
    if resolution is not None and resolution.source_unverified:
        status = LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
        reasons = _dedupe((*resolution.reasons, *reasons))
    elif resolution is not None and resolution.failed:
        status = LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED
        reasons = _dedupe((*resolution.reasons, *reasons))
    elif repository_failed or REPOSITORY_UNAVAILABLE in reasons:
        status = LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED
    elif (
        CHAIN_UNVERIFIED in reasons
        or SOURCE_ADMISSION_UNVERIFIED in reasons
        or any("unverified" in reason for reason in reasons)
    ):
        status = LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED
    elif admission is None:
        status = LeaderFormalResearchRuntimeComponentStatus.MISSING
    else:
        status = _mapped_status(admission.status)
    return LeaderFormalResearchRuntimeComponent(
        name="risk",
        status=status,
        candidate_count=candidate_count,
        ready_count=(admission.ready_count if admission is not None else 0),
        reasons=reasons,
    )


def _assembly_status(
    components: Sequence[LeaderFormalResearchRuntimeComponent],
) -> LeaderFormalResearchRuntimeAssemblyStatus:
    statuses = {item.status for item in components}
    if statuses == {LeaderFormalResearchRuntimeComponentStatus.READY}:
        return LeaderFormalResearchRuntimeAssemblyStatus.READY
    if statuses & {
        LeaderFormalResearchRuntimeComponentStatus.SOURCE_FAILED,
        LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
        LeaderFormalResearchRuntimeComponentStatus.BLOCKED,
    } and not statuses & {
        LeaderFormalResearchRuntimeComponentStatus.READY,
        LeaderFormalResearchRuntimeComponentStatus.PARTIAL,
    }:
        return LeaderFormalResearchRuntimeAssemblyStatus.BLOCKED
    if statuses & {
        LeaderFormalResearchRuntimeComponentStatus.READY,
        LeaderFormalResearchRuntimeComponentStatus.PARTIAL,
    }:
        return LeaderFormalResearchRuntimeAssemblyStatus.PARTIAL
    return LeaderFormalResearchRuntimeAssemblyStatus.MISSING


def build_leader_formal_research_runtime_assembly(
    context: LeaderResearchRuntimeSourceContext,
    *,
    repository: Any,
    sector_rule_provider: Optional[Provider] = None,
    history_provider: Optional[Provider] = None,
    business_catalyst_provider: Optional[Provider] = None,
    tradability_provider: Optional[Provider] = None,
    risk_provider: Optional[Provider] = None,
) -> LeaderFormalResearchRuntimeAssemblyResult:
    """只读调用五类来源并生成单一研究输入和脱敏健康证据。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(ASSEMBLY_CONTEXT_UNVERIFIED)
    candidate_count = context.candidate_plan.candidate_count
    sector = _resolve_provider(
        context,
        sector_rule_provider,
        build_sector_rule_runtime_bridge,
        "source_batch",
        "sector_rule",
    )
    history = _resolve_provider(
        context,
        history_provider,
        build_leader_history_runtime_bridge,
        "history_entries",
        "history",
    )
    business = _resolve_provider(
        context,
        business_catalyst_provider,
        build_leader_business_catalyst_runtime_bridge,
        "source_batch",
        "business_catalyst",
    )
    tradability = _resolve_provider(
        context,
        tradability_provider,
        build_leader_tradability_runtime_bridge,
        "tradability_bundle",
        "tradability",
    )
    risk = _resolve_provider(
        context,
        risk_provider,
        lambda _context, *, official_risk_batch: official_risk_batch,
        "official_risk_batch",
        "risk",
    )

    repository_failed = False
    try:
        formal_bridge = build_leader_formal_research_runtime_bridge(
            context,
            repository=repository,
            history_entries=(
                history.bridge.history_entries
                if history.bridge is not None
                else None
            ),
            business_review_batch=(
                business.bridge.business_admission_value
                if business.bridge is not None
                else None
            ),
            tradability_bundle=(
                tradability.bridge.tradability_admission_value
                if tradability.bridge is not None
                else None
            ),
            official_risk_batch=(
                risk.bridge
                if risk.configured and risk.bridge is not None
                else (object() if risk.configured else None)
            ),
        )
    except Exception:
        repository_failed = True
        formal_bridge = build_leader_formal_research_runtime_bridge(
            context,
            repository=_UnavailableReviewRepository(),
        )

    components = (
        _sector_component(sector, candidate_count),
        _provider_component(
            name="history",
            admission_name="history",
            resolution=history,
            candidate_count=candidate_count,
            unverified_reason=HISTORY_SOURCE_UNVERIFIED,
        ),
        _provider_component(
            name="business_catalyst",
            admission_name="business_catalyst",
            resolution=business,
            candidate_count=candidate_count,
            unverified_reason=BUSINESS_SOURCE_UNVERIFIED,
        ),
        _provider_component(
            name="tradability",
            admission_name="tradability",
            resolution=tradability,
            candidate_count=candidate_count,
            unverified_reason=TRADABILITY_SOURCE_UNVERIFIED,
        ),
        _risk_component(
            formal_bridge,
            candidate_count=candidate_count,
            repository_failed=repository_failed,
            resolution=(risk if risk.configured else None),
        ),
    )
    reasons = _dedupe(
        reason for component in components for reason in component.reasons
    )
    return LeaderFormalResearchRuntimeAssemblyResult(
        status=_assembly_status(components),
        radar_run_id=context.radar_run_id,
        candidate_plan_id=context.candidate_plan.candidate_set_id,
        candidate_count=candidate_count,
        components=components,
        reasons=reasons,
        provider_input=formal_bridge.provider_input,
        sector_rule_readiness=(
            sector.bridge.sector_rule_admission_value
            if sector.bridge is not None
            else None
        ),
        production_source_proofs=tuple(
            (
                resolution.proof
                for resolution in (
                    sector,
                    history,
                    business,
                    tradability,
                    risk,
                )
                if resolution.proof is not None
            )
        ) + (
            ()
            if risk.proof is not None
            else (
                build_leader_formal_research_risk_source_proof(
                    context,
                    bridge=formal_bridge,
                ),
            )
        ),
        formal_bridge=formal_bridge,
    )
