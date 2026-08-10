"""阶段6L-F4研究组件、E3风险条目、F2与F3的纯内存编排。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_audit import (
    LeaderResearchReadinessAuditInput,
)
from radar.leader_research_readiness_audit_batch import (
    LeaderResearchReadinessAuditBatchEntry,
    LeaderResearchReadinessAuditBatchInput,
    LeaderResearchReadinessAuditBatchResult,
    LeaderResearchReadinessAuditBatchStatus,
    build_leader_research_readiness_audit_batch,
)
from radar.leader_risk_candidate_projection_batch import (
    LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID,
    LeaderRiskCandidateProjectionBatchItem,
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.leader_runtime_inputs import (
    LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID,
    LeaderResearchComponentBatchItem,
    LeaderRuntimeAssembly,
    attach_leader_risk_candidate_projections,
    attach_leader_research_readiness_audits,
    build_leader_research_component_set_id,
)


UTC = timezone.utc
SYMBOL_PATTERN = re.compile(r"^\d{6}$")
LEADER_RESEARCH_READINESS_RUNTIME_BATCH_CONTRACT_ID = (
    "radar-leader-research-readiness-runtime-batch-v1"
)
CONTRACT_UNVERIFIED = (
    "leader_research_readiness_runtime_batch_contract_unverified"
)
RUNTIME_CONTRACT_UNVERIFIED = (
    "leader_research_readiness_runtime_batch_runtime_contract_unverified"
)
RUNTIME_NOT_READY = (
    "leader_research_readiness_runtime_batch_runtime_not_ready"
)
RISK_CONTRACT_UNVERIFIED = (
    "leader_research_readiness_runtime_batch_risk_contract_unverified"
)
COMPONENT_CONTRACT_UNVERIFIED = (
    "leader_research_readiness_runtime_batch_component_contract_unverified"
)


class LeaderResearchReadinessRuntimeBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderResearchReadinessRuntimeBatchInput:
    assembly: Any = field(repr=False)
    risk_projection_batch: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchReadinessRuntimeBatchResult:
    status: LeaderResearchReadinessRuntimeBatchStatus
    as_of: Optional[datetime]
    component_count: int
    audit_batch: LeaderResearchReadinessAuditBatchResult = field(
        repr=False
    )
    runtime_assembly: Optional[LeaderRuntimeAssembly] = field(
        default=None,
        repr=False,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = (
        LEADER_RESEARCH_READINESS_RUNTIME_BATCH_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self):
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "asOf": (
                self.as_of.isoformat()
                if self.as_of is not None
                else None
            ),
            "componentCount": self.component_count,
            "auditCount": self.audit_batch.audit_count,
            "blockedCount": self.audit_batch.blocked_count,
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": (
                    self.state_transition_allowed
                ),
            },
        }


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _empty_audit_batch(
    as_of: Optional[datetime],
) -> LeaderResearchReadinessAuditBatchResult:
    if as_of is None:
        return build_leader_research_readiness_audit_batch(None)
    return build_leader_research_readiness_audit_batch(
        LeaderResearchReadinessAuditBatchInput(
            as_of=as_of,
            entries=(),
        )
    )


def _result(
    *,
    status: LeaderResearchReadinessRuntimeBatchStatus,
    as_of: Optional[datetime],
    component_count: int,
    audit_batch: LeaderResearchReadinessAuditBatchResult,
    runtime_assembly: Optional[LeaderRuntimeAssembly],
    reasons: Sequence[str],
) -> LeaderResearchReadinessRuntimeBatchResult:
    return LeaderResearchReadinessRuntimeBatchResult(
        status=status,
        as_of=as_of,
        component_count=component_count,
        audit_batch=audit_batch,
        runtime_assembly=runtime_assembly,
        reasons=_dedupe(reasons),
    )


def _expected_risk_status(
    items: Tuple[LeaderRiskCandidateProjectionBatchItem, ...],
) -> LeaderRiskCandidateProjectionBatchStatus:
    ready_count = sum(item.included for item in items)
    if ready_count == len(items):
        return LeaderRiskCandidateProjectionBatchStatus.READY
    if ready_count:
        return LeaderRiskCandidateProjectionBatchStatus.PARTIAL
    statuses = {item.status for item in items}
    if ResearchFeatureStatus.SOURCE_FAILED in statuses:
        return LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED
    if ResearchFeatureStatus.SOURCE_UNVERIFIED in statuses:
        return LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED
    if ResearchFeatureStatus.STALE in statuses:
        return LeaderRiskCandidateProjectionBatchStatus.STALE
    return LeaderRiskCandidateProjectionBatchStatus.MISSING


def _risk_batch_is_valid(
    value: Any,
    *,
    as_of: datetime,
    candidate_symbols: Tuple[str, ...],
) -> bool:
    if (
        not isinstance(value, LeaderRiskCandidateProjectionBatchResult)
        or value.batch_contract_id
        != LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID
        or _aware_utc(value.as_of) != as_of
        or not isinstance(value.status, LeaderRiskCandidateProjectionBatchStatus)
        or not isinstance(value.items, tuple)
        or value.input_count != len(value.items)
        or not isinstance(value.reasons, tuple)
        or any(
            not isinstance(reason, str) or not reason
            for reason in value.reasons
        )
        or any((
            value.risk_filter_passed is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.applied_to_d3 is not False,
            value.applied_to_d1 is not False,
        ))
    ):
        return False
    if any(
        not isinstance(item, LeaderRiskCandidateProjectionBatchItem)
        or item.index != index
        or not isinstance(item.symbol, str)
        or SYMBOL_PATTERN.fullmatch(item.symbol) is None
        or not isinstance(item.status, ResearchFeatureStatus)
        or not isinstance(item.reasons, tuple)
        or any(
            not isinstance(reason, str) or not reason
            for reason in item.reasons
        )
        or any((
            item.risk_filter_passed is not False,
            item.formal_gate_ready is not False,
            item.formal_usable is not False,
            item.applied_to_d3 is not False,
            item.applied_to_d1 is not False,
        ))
        or (
            item.status == ResearchFeatureStatus.READY
            and (item.projection is None or bool(item.reasons))
        )
        or (
            item.status != ResearchFeatureStatus.READY
            and (item.projection is not None or not item.reasons)
        )
        for index, item in enumerate(value.items)
    ):
        return False
    item_symbols = tuple(item.symbol for item in value.items)
    if (
        len(set(item_symbols)) != len(item_symbols)
        or set(item_symbols) != set(candidate_symbols)
        or value.status != _expected_risk_status(value.items)
    ):
        return False
    expected_reasons = (
        ()
        if value.status == LeaderRiskCandidateProjectionBatchStatus.READY
        else (
            ("risk_candidate_projection_batch_partial",)
            if value.status
            == LeaderRiskCandidateProjectionBatchStatus.PARTIAL
            else (
                "risk_candidate_projection_batch_no_ready_items",
            )
        )
    )
    return value.reasons == expected_reasons


def is_leader_research_runtime_risk_batch_valid(
    value: Any,
    *,
    as_of: datetime,
    candidate_symbols: Tuple[str, ...],
) -> bool:
    """公开F4完整E3批次校验，供后续纯内存编排复用。"""

    return _risk_batch_is_valid(
        value,
        as_of=as_of,
        candidate_symbols=candidate_symbols,
    )


def _component_is_bound(
    value: Any,
    *,
    index: int,
    symbol: str,
    as_of: datetime,
    industry_code: Optional[str],
    radar_run_id: str,
    quote_batch_id: str,
) -> bool:
    if (
        not isinstance(value, LeaderResearchComponentBatchItem)
        or value.index != index
        or value.contract_id
        != LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID
        or value.symbol != symbol
        or _aware_utc(value.as_of) != as_of
        or not isinstance(value.industry_code, str)
        or value.industry_code != industry_code
        or not isinstance(value.industry_release_id, str)
        or not value.industry_release_id
        or value.radar_run_id != radar_run_id
        or value.quote_batch_id != quote_batch_id
        or not isinstance(value.quote_source_contract_id, str)
        or not value.quote_source_contract_id
    ):
        return False
    return value.component_set_id == build_leader_research_component_set_id(
        symbol=value.symbol,
        as_of=value.as_of,
        radar_run_id=value.radar_run_id,
        quote_batch_id=value.quote_batch_id,
        quote_source_contract_id=value.quote_source_contract_id,
        industry_code=value.industry_code,
        industry_release_id=value.industry_release_id,
    )


def _runtime_structure_is_valid(
    assembly: LeaderRuntimeAssembly,
    *,
    as_of: datetime,
) -> bool:
    if (
        assembly.status != "ready"
        or not isinstance(assembly.evidence_items, tuple)
        or not isinstance(assembly.research_component_items, tuple)
        or not assembly.evidence_items
        or len(assembly.evidence_items)
        != len(assembly.research_component_items)
        or not isinstance(assembly.radar_run_id, str)
        or not assembly.radar_run_id
        or not isinstance(assembly.quote_batch_id, str)
        or not assembly.quote_batch_id
    ):
        return False
    symbols = []
    for evidence in assembly.evidence_items:
        symbol = getattr(evidence, "symbol", None)
        if (
            not isinstance(symbol, str)
            or SYMBOL_PATTERN.fullmatch(symbol) is None
            or _aware_utc(getattr(evidence, "as_of", None)) != as_of
        ):
            return False
        symbols.append(symbol)
    return len(set(symbols)) == len(symbols)


def _runtime_status(
    value: LeaderResearchReadinessAuditBatchStatus,
) -> LeaderResearchReadinessRuntimeBatchStatus:
    return {
        LeaderResearchReadinessAuditBatchStatus.READY: (
            LeaderResearchReadinessRuntimeBatchStatus.READY
        ),
        LeaderResearchReadinessAuditBatchStatus.PARTIAL: (
            LeaderResearchReadinessRuntimeBatchStatus.PARTIAL
        ),
        LeaderResearchReadinessAuditBatchStatus.BLOCKED: (
            LeaderResearchReadinessRuntimeBatchStatus.BLOCKED
        ),
        LeaderResearchReadinessAuditBatchStatus.MISSING: (
            LeaderResearchReadinessRuntimeBatchStatus.MISSING
        ),
    }[value]


def build_leader_research_readiness_runtime_batch(
    input_value: Any,
) -> LeaderResearchReadinessRuntimeBatchResult:
    """用同轮组件与完整E3条目构建F2，再将可信映射回挂F3。"""

    if not isinstance(
        input_value,
        LeaderResearchReadinessRuntimeBatchInput,
    ):
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
            as_of=None,
            component_count=0,
            audit_batch=_empty_audit_batch(None),
            runtime_assembly=None,
            reasons=(CONTRACT_UNVERIFIED,),
        )
    assembly = input_value.assembly
    if not isinstance(assembly, LeaderRuntimeAssembly):
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
            as_of=None,
            component_count=0,
            audit_batch=_empty_audit_batch(None),
            runtime_assembly=None,
            reasons=(RUNTIME_CONTRACT_UNVERIFIED,),
        )
    as_of = _aware_utc(assembly.as_of)
    if as_of is None:
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
            as_of=None,
            component_count=0,
            audit_batch=_empty_audit_batch(None),
            runtime_assembly=assembly,
            reasons=(RUNTIME_CONTRACT_UNVERIFIED,),
        )
    if (
        assembly.status != "ready"
        and not assembly.evidence_items
        and not assembly.research_component_items
    ):
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.MISSING,
            as_of=as_of,
            component_count=0,
            audit_batch=_empty_audit_batch(as_of),
            runtime_assembly=assembly,
            reasons=(RUNTIME_NOT_READY,),
        )
    if not _runtime_structure_is_valid(assembly, as_of=as_of):
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
            as_of=as_of,
            component_count=(
                len(assembly.research_component_items)
                if isinstance(assembly.research_component_items, tuple)
                else 0
            ),
            audit_batch=_empty_audit_batch(None),
            runtime_assembly=assembly,
            reasons=(RUNTIME_CONTRACT_UNVERIFIED,),
        )

    candidate_symbols = tuple(
        item.symbol for item in assembly.evidence_items
    )
    risk_batch = input_value.risk_projection_batch
    if not _risk_batch_is_valid(
        risk_batch,
        as_of=as_of,
        candidate_symbols=candidate_symbols,
    ):
        return _result(
            status=LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
            as_of=as_of,
            component_count=len(assembly.research_component_items),
            audit_batch=_empty_audit_batch(None),
            runtime_assembly=assembly,
            reasons=(RISK_CONTRACT_UNVERIFIED,),
        )

    risk_by_symbol = {item.symbol: item for item in risk_batch.items}
    risk_attached_assembly = attach_leader_risk_candidate_projections(
        assembly,
        projections_by_symbol=risk_batch.projections_by_symbol,
    )
    entries = []
    component_contract_unverified = False
    for index, (evidence, component) in enumerate(zip(
        assembly.evidence_items,
        assembly.research_component_items,
    )):
        if not _component_is_bound(
            component,
            index=index,
            symbol=evidence.symbol,
            as_of=as_of,
            industry_code=evidence.industry_code,
            radar_run_id=assembly.radar_run_id,
            quote_batch_id=assembly.quote_batch_id,
        ):
            audit_input = None
            component_contract_unverified = True
        else:
            audit_input = LeaderResearchReadinessAuditInput(
                symbol=evidence.symbol,
                as_of=as_of,
                cross_sectional_features=(
                    component.cross_sectional_features
                ),
                history_features=component.history_features,
                liquidity_features=component.liquidity_features,
                business_catalyst_features=(
                    component.business_catalyst_features
                ),
                tradability_features=component.tradability_features,
                risk_projection_item=risk_by_symbol[evidence.symbol],
            )
        entries.append(LeaderResearchReadinessAuditBatchEntry(
            symbol=evidence.symbol,
            audit_input=audit_input,
        ))

    audit_batch = build_leader_research_readiness_audit_batch(
        LeaderResearchReadinessAuditBatchInput(
            as_of=as_of,
            entries=tuple(entries),
        )
    )
    attached = attach_leader_research_readiness_audits(
        risk_attached_assembly,
        audits_by_symbol=audit_batch.audits_by_symbol,
    )
    return _result(
        status=_runtime_status(audit_batch.status),
        as_of=as_of,
        component_count=len(assembly.research_component_items),
        audit_batch=audit_batch,
        runtime_assembly=attached,
        reasons=(
            (COMPONENT_CONTRACT_UNVERIFIED,)
            if component_contract_unverified
            else ()
        ),
    )
