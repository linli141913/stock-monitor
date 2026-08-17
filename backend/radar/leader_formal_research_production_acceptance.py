"""阶段6真实来源证明与正式门验收合同。

本模块只审计来源提供方交付的真实性证明和五源总装结果，不抓取、不写库，
也不打开正式评分、龙头状态或状态迁移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_formal_research_runtime_assembly import (
    COMPONENT_NAMES,
    LeaderFormalResearchRuntimeAssemblyResult,
    LeaderFormalResearchRuntimeAssemblyStatus,
    LeaderFormalResearchRuntimeComponentStatus,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceProof,
    LeaderFormalResearchProductionSourceStatus,
)


LEADER_FORMAL_RESEARCH_PRODUCTION_ACCEPTANCE_CONTRACT_ID = (
    "radar-leader-formal-research-production-acceptance-v1"
)
PROVENANCE_CONTRACT_ID = (
    "radar-leader-formal-research-source-provenance-v1"
)
PROVENANCE_MISSING = (
    "leader_formal_research_production_provenance_missing"
)
PROVENANCE_NOT_COMPLETED = (
    "leader_formal_research_production_source_not_completed"
)
PROVENANCE_COVERAGE_INCOMPLETE = (
    "leader_formal_research_production_source_coverage_incomplete"
)
PROVENANCE_IDENTITY_UNVERIFIED = (
    "leader_formal_research_production_provenance_identity_unverified"
)
PROVENANCE_TIME_UNVERIFIED = (
    "leader_formal_research_production_provenance_time_unverified"
)
PROVENANCE_UNBOUND = (
    "leader_formal_research_production_provenance_unbound"
)
ASSEMBLY_UNVERIFIED = (
    "leader_formal_research_production_assembly_unverified"
)
ASSEMBLY_FORMAL_GATE_OPEN = (
    "leader_formal_research_production_assembly_gate_already_open"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
UTC = timezone.utc


class LeaderFormalResearchProductionAcceptanceStatus(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    MISSING = "missing"
    BLOCKED = "blocked"


class LeaderFormalResearchSourceProvenanceStatus(str, Enum):
    COMPLETED = "completed"
    NOT_RUN = "not_run"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class LeaderFormalResearchSourceProvenance:
    component_name: str
    contract_id: str
    status: LeaderFormalResearchSourceProvenanceStatus
    radar_run_id: str
    candidate_plan_id: str
    as_of: datetime
    source_time: Optional[datetime]
    fetched_at: Optional[datetime]
    candidate_count: int
    ready_count: int
    provenance_contract_id: str = PROVENANCE_CONTRACT_ID


@dataclass(frozen=True)
class LeaderFormalResearchProductionAcceptanceInput:
    assembly: Any = field(repr=False)
    provenance: Tuple[LeaderFormalResearchSourceProvenance, ...] = ()


@dataclass(frozen=True)
class LeaderFormalResearchProductionAcceptanceResult:
    status: LeaderFormalResearchProductionAcceptanceStatus
    candidate_count: int
    missing_components: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    component_statuses: Tuple[Tuple[str, str], ...] = ()
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_ACCEPTANCE_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def health_reasons(self) -> Tuple[str, ...]:
        if self.status == LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW:
            return ()
        return _dedupe((
            "leader_formal_research_production_acceptance_"
            f"{self.status.value}",
            *self.reasons,
        ))

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidateCount": self.candidate_count,
            "missingComponents": list(self.missing_components),
            "reasons": list(self.reasons),
            "componentStatuses": [
                {"name": name, "status": status}
                for name, status in self.component_statuses
            ],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _blocked(
    *,
    candidate_count: int,
    reasons: Sequence[str],
    component_statuses: Sequence[Tuple[str, str]] = (),
) -> LeaderFormalResearchProductionAcceptanceResult:
    return LeaderFormalResearchProductionAcceptanceResult(
        status=LeaderFormalResearchProductionAcceptanceStatus.BLOCKED,
        candidate_count=candidate_count,
        reasons=_dedupe(reasons),
        component_statuses=tuple(component_statuses),
    )


def _provenance_index(
    value: Any,
) -> Tuple[Optional[Mapping[str, LeaderFormalResearchSourceProvenance]], Tuple[str, ...]]:
    if not isinstance(value, tuple):
        return None, (PROVENANCE_IDENTITY_UNVERIFIED,)
    result = {}
    reasons = []
    for item in value:
        if not isinstance(item, LeaderFormalResearchSourceProvenance):
            reasons.append(PROVENANCE_IDENTITY_UNVERIFIED)
            continue
        if item.provenance_contract_id != PROVENANCE_CONTRACT_ID:
            reasons.append(PROVENANCE_IDENTITY_UNVERIFIED)
            continue
        if item.component_name not in COMPONENT_NAMES:
            reasons.append(PROVENANCE_IDENTITY_UNVERIFIED)
            continue
        if item.component_name in result:
            reasons.append(PROVENANCE_IDENTITY_UNVERIFIED)
            continue
        result[item.component_name] = item
    return result, _dedupe(reasons)


def _assembly_as_of(assembly: LeaderFormalResearchRuntimeAssemblyResult) -> Optional[datetime]:
    admission = getattr(getattr(assembly, "formal_bridge", None), "source_admission", None)
    return _aware_utc(getattr(admission, "as_of", None))


def build_leader_formal_research_source_provenance_from_assembly(
    assembly: Any,
) -> Tuple[LeaderFormalResearchSourceProvenance, ...]:
    """只从总装保留的来源证明生成验收输入，拒绝外部自由拼接。"""

    if not isinstance(assembly, LeaderFormalResearchRuntimeAssemblyResult):
        return ()
    component_by_name = {
        item.name: item
        for item in assembly.components
        if item.name in COMPONENT_NAMES
    }
    proofs = getattr(assembly, "production_source_proofs", ())
    if not isinstance(proofs, tuple):
        return ()
    result = []
    for proof in proofs:
        if not isinstance(proof, LeaderFormalResearchProductionSourceProof):
            continue
        component = component_by_name.get(proof.component_name)
        if component is None:
            continue
        try:
            status = LeaderFormalResearchSourceProvenanceStatus(
                proof.status.value
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if proof.expected_count != assembly.candidate_count:
            continue
        if (
            proof.status == LeaderFormalResearchProductionSourceStatus.COMPLETED
            and proof.returned_count != component.ready_count
        ):
            continue
        result.append(LeaderFormalResearchSourceProvenance(
            component_name=proof.component_name,
            contract_id=proof.source_contract_id,
            status=status,
            radar_run_id=proof.radar_run_id,
            candidate_plan_id=proof.candidate_plan_id,
            as_of=proof.as_of,
            source_time=proof.source_time,
            fetched_at=proof.fetched_at,
            candidate_count=assembly.candidate_count,
            ready_count=component.ready_count,
        ))
    return tuple(result)


def build_leader_formal_research_production_acceptance(
    input_value: Any,
) -> LeaderFormalResearchProductionAcceptanceResult:
    """审计五源真实证明；READY只表示可进入人工正式门复核。"""

    if not isinstance(
        input_value,
        LeaderFormalResearchProductionAcceptanceInput,
    ):
        return _blocked(candidate_count=0, reasons=(ASSEMBLY_UNVERIFIED,))
    assembly = input_value.assembly
    if not isinstance(
        assembly,
        LeaderFormalResearchRuntimeAssemblyResult,
    ):
        return _blocked(candidate_count=0, reasons=(ASSEMBLY_UNVERIFIED,))
    candidate_count = assembly.candidate_count
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
    ):
        return _blocked(candidate_count=0, reasons=(ASSEMBLY_UNVERIFIED,))
    if any((
        assembly.formal_score_ready,
        assembly.formal_gate_ready,
        assembly.formal_usable,
        assembly.state_transition_allowed,
    )):
        return _blocked(
            candidate_count=candidate_count,
            reasons=(ASSEMBLY_FORMAL_GATE_OPEN,),
        )
    if tuple(item.name for item in assembly.components) != COMPONENT_NAMES:
        return _blocked(
            candidate_count=candidate_count,
            reasons=(ASSEMBLY_UNVERIFIED,),
        )

    component_statuses = tuple(
        (item.name, item.status.value)
        for item in assembly.components
    )
    assembly_missing = tuple(
        item.name
        for item in assembly.components
        if (
            item.status != LeaderFormalResearchRuntimeComponentStatus.READY
            or item.ready_count != candidate_count
            or item.candidate_count != candidate_count
        )
    )
    if assembly.status != LeaderFormalResearchRuntimeAssemblyStatus.READY:
        assembly_missing = tuple(dict.fromkeys((
            *assembly_missing,
            *COMPONENT_NAMES,
        )))
    provenance, provenance_reasons = _provenance_index(
        input_value.provenance
    )
    if provenance is None or provenance_reasons:
        return _blocked(
            candidate_count=candidate_count,
            reasons=_dedupe((
                *provenance_reasons,
                ASSEMBLY_UNVERIFIED if assembly_missing else None,
            )),
            component_statuses=component_statuses,
        )

    missing = list(dict.fromkeys(assembly_missing))
    reasons = list(provenance_reasons)
    if not provenance:
        return LeaderFormalResearchProductionAcceptanceResult(
            status=LeaderFormalResearchProductionAcceptanceStatus.MISSING,
            candidate_count=candidate_count,
            missing_components=tuple(missing or COMPONENT_NAMES),
            reasons=_dedupe((
                PROVENANCE_MISSING,
                ASSEMBLY_UNVERIFIED if assembly_missing else None,
            )),
            component_statuses=component_statuses,
        )
    expected_as_of = _assembly_as_of(assembly)
    if expected_as_of is None:
        observed_as_of = {
            _aware_utc(item.as_of)
            for item in provenance.values()
            if _aware_utc(item.as_of) is not None
        }
        if len(observed_as_of) == 1:
            expected_as_of = next(iter(observed_as_of))
        else:
            return _blocked(
                candidate_count=candidate_count,
                reasons=(PROVENANCE_TIME_UNVERIFIED,),
                component_statuses=component_statuses,
            )
    for component_name in COMPONENT_NAMES:
        evidence = provenance.get(component_name)
        if evidence is None:
            if component_name not in missing:
                missing.append(component_name)
            reasons.append(PROVENANCE_MISSING)
            continue
        as_of = _aware_utc(evidence.as_of)
        source_time = (
            _aware_utc(evidence.source_time)
            if evidence.source_time is not None
            else None
        )
        fetched_at = _aware_utc(evidence.fetched_at)
        identity_ok = bool(
            evidence.component_name == component_name
            and evidence.radar_run_id == assembly.radar_run_id
            and evidence.candidate_plan_id == assembly.candidate_plan_id
            and expected_as_of is not None
            and as_of == expected_as_of
            and isinstance(evidence.contract_id, str)
            and bool(evidence.contract_id.strip())
            and evidence.candidate_count == candidate_count
        )
        if not identity_ok:
            if component_name not in missing:
                missing.append(component_name)
            reasons.append(PROVENANCE_IDENTITY_UNVERIFIED)
            continue
        if (
            fetched_at is None
            or source_time is None
            or source_time > as_of + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            )
            or fetched_at < source_time
        ):
            if component_name not in missing:
                missing.append(component_name)
            reasons.append(PROVENANCE_TIME_UNVERIFIED)
        if evidence.status != LeaderFormalResearchSourceProvenanceStatus.COMPLETED:
            if component_name not in missing:
                missing.append(component_name)
            reasons.append(PROVENANCE_NOT_COMPLETED)
        if evidence.ready_count != candidate_count:
            if component_name not in missing:
                missing.append(component_name)
            reasons.append(PROVENANCE_COVERAGE_INCOMPLETE)

    if PROVENANCE_IDENTITY_UNVERIFIED in reasons:
        return _blocked(
            candidate_count=candidate_count,
            reasons=reasons,
            component_statuses=component_statuses,
        )
    if missing or reasons:
        return LeaderFormalResearchProductionAcceptanceResult(
            status=LeaderFormalResearchProductionAcceptanceStatus.MISSING,
            candidate_count=candidate_count,
            missing_components=tuple(missing),
            reasons=_dedupe(reasons),
            component_statuses=component_statuses,
        )
    return LeaderFormalResearchProductionAcceptanceResult(
        status=LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW,
        candidate_count=candidate_count,
        component_statuses=component_statuses,
    )
