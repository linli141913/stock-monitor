"""阶段6L-F5三级龙头研究输入的同轮只读提供器契约。"""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureInput,
)
from radar.leader_history_features import LeaderHistoryFeatureInput
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_runtime_batch import (
    is_leader_research_runtime_risk_batch_valid,
)
from radar.leader_risk_candidate_projection import (
    LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
    LeaderRiskCandidateProjection,
)
from radar.leader_risk_official_deterministic import (
    is_leader_official_deterministic_risk_projection_valid,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
    LeaderRiskCandidateProjectionBatchResult,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
)
from radar.leader_runtime_inputs import (
    LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID,
    LeaderResearchComponentBatchItem,
    LeaderRuntimeAssembly,
    build_leader_research_component_set_id,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureInput,
)


UTC = timezone.utc
SYMBOL_PATTERN = re.compile(r"^\d{6}$")
LEADER_RESEARCH_INPUT_PROVIDER_BATCH_CONTRACT_ID = (
    "radar-leader-research-input-provider-batch-v1"
)
CONTRACT_UNVERIFIED = (
    "leader_research_input_provider_batch_contract_unverified"
)
RUN_MISMATCH = "leader_research_input_provider_batch_run_mismatch"
AS_OF_MISMATCH = "leader_research_input_provider_batch_as_of_mismatch"
CANDIDATE_MISMATCH = (
    "leader_research_input_provider_batch_candidate_mismatch"
)
RISK_CONTRACT_UNVERIFIED = (
    "leader_research_input_provider_batch_risk_contract_unverified"
)


class LeaderResearchInputProviderBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    MISSING = "missing"


class LeaderResearchInputProviderItemStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderResearchInputProviderBatchEntry:
    symbol: str
    history_input: Any = field(repr=False)
    business_catalyst_input: Any = field(repr=False)
    tradability_input: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchInputProviderBatchInput:
    assembly: Any = field(repr=False)
    radar_run_id: Any
    as_of: Any
    provider_contract_id: Any
    entries: Any = field(repr=False)
    risk_projection_batch: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchInputProviderPlanBatchInput:
    candidate_plan: Any = field(repr=False)
    radar_run_id: Any
    as_of: Any
    provider_contract_id: Any
    entries: Any = field(repr=False)
    risk_projection_batch: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchInputProviderBatchItem:
    index: int
    symbol: str
    status: LeaderResearchInputProviderItemStatus
    missing_inputs: Tuple[str, ...] = field(default_factory=tuple)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    history_input: Optional[LeaderHistoryFeatureInput] = field(
        default=None,
        repr=False,
    )
    business_catalyst_input: Optional[
        LeaderBusinessCatalystFeatureInput
    ] = field(default=None, repr=False)
    tradability_input: Optional[LeaderTradabilityFeatureInput] = field(
        default=None,
        repr=False,
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "missingInputs": list(self.missing_inputs),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderResearchInputProviderBatchResult:
    status: LeaderResearchInputProviderBatchStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_count: int
    items: Tuple[
        LeaderResearchInputProviderBatchItem,
        ...,
    ] = field(default_factory=tuple)
    risk_projection_batch: Optional[
        LeaderRiskCandidateProjectionBatchResult
    ] = field(default=None, repr=False)
    provider_contract_id: Optional[str] = None
    candidate_plan_id: Optional[str] = None
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = LEADER_RESEARCH_INPUT_PROVIDER_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def history_inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderHistoryFeatureInput]:
        return MappingProxyType({
            item.symbol: item.history_input
            for item in self.items
            if (
                item.status != LeaderResearchInputProviderItemStatus.BLOCKED
                and item.history_input is not None
            )
        })

    @property
    def business_catalyst_inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderBusinessCatalystFeatureInput]:
        return MappingProxyType({
            item.symbol: item.business_catalyst_input
            for item in self.items
            if (
                item.status != LeaderResearchInputProviderItemStatus.BLOCKED
                and item.business_catalyst_input is not None
            )
        })

    @property
    def tradability_inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderTradabilityFeatureInput]:
        return MappingProxyType({
            item.symbol: item.tradability_input
            for item in self.items
            if (
                item.status != LeaderResearchInputProviderItemStatus.BLOCKED
                and item.tradability_input is not None
            )
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidateCount": self.candidate_count,
            "providerContractId": self.provider_contract_id,
            "candidatePlanId": self.candidate_plan_id,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": self.state_transition_allowed,
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


def _result(
    *,
    status: LeaderResearchInputProviderBatchStatus,
    radar_run_id: Optional[str],
    as_of: Optional[datetime],
    candidate_count: int,
    reasons: Sequence[str],
    items: Sequence[LeaderResearchInputProviderBatchItem] = (),
    risk_projection_batch: Optional[
        LeaderRiskCandidateProjectionBatchResult
    ] = None,
    provider_contract_id: Optional[str] = None,
    candidate_plan_id: Optional[str] = None,
) -> LeaderResearchInputProviderBatchResult:
    return LeaderResearchInputProviderBatchResult(
        status=status,
        radar_run_id=radar_run_id,
        as_of=as_of,
        candidate_count=candidate_count,
        items=tuple(items),
        risk_projection_batch=risk_projection_batch,
        provider_contract_id=provider_contract_id,
        candidate_plan_id=candidate_plan_id,
        reasons=_dedupe(reasons),
    )


def _blocked(
    reason: str,
    *,
    radar_run_id: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> LeaderResearchInputProviderBatchResult:
    return _result(
        status=LeaderResearchInputProviderBatchStatus.BLOCKED,
        radar_run_id=radar_run_id,
        as_of=as_of,
        candidate_count=0,
        reasons=(reason,),
    )


def _component_is_valid(
    value: Any,
    *,
    index: int,
    symbol: str,
    industry_code: str,
    as_of: datetime,
    radar_run_id: str,
    quote_batch_id: str,
) -> bool:
    if (
        not isinstance(value, LeaderResearchComponentBatchItem)
        or value.index != index
        or value.symbol != symbol
        or value.industry_code != industry_code
        or _aware_utc(value.as_of) != as_of
        or value.radar_run_id != radar_run_id
        or value.quote_batch_id != quote_batch_id
        or value.contract_id
        != LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID
        or not isinstance(value.industry_release_id, str)
        or not value.industry_release_id
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


def _assembly_candidates(
    assembly: Any,
) -> Optional[Tuple[Tuple[str, str, LeaderResearchComponentBatchItem], ...]]:
    if (
        not isinstance(assembly, LeaderRuntimeAssembly)
        or assembly.status != "ready"
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
        return None
    as_of = _aware_utc(assembly.as_of)
    if as_of is None:
        return None
    candidates = []
    for index, (evidence, component) in enumerate(zip(
        assembly.evidence_items,
        assembly.research_component_items,
    )):
        symbol = getattr(evidence, "symbol", None)
        industry_code = getattr(evidence, "industry_code", None)
        if (
            not isinstance(symbol, str)
            or SYMBOL_PATTERN.fullmatch(symbol) is None
            or not isinstance(industry_code, str)
            or not industry_code
            or _aware_utc(getattr(evidence, "as_of", None)) != as_of
            or not _component_is_valid(
                component,
                index=index,
                symbol=symbol,
                industry_code=industry_code,
                as_of=as_of,
                radar_run_id=assembly.radar_run_id,
                quote_batch_id=assembly.quote_batch_id,
            )
        ):
            return None
        candidates.append((symbol, industry_code, component))
    if len({symbol for symbol, _, _ in candidates}) != len(candidates):
        return None
    return tuple(candidates)


def _plan_candidates(
    plan: Any,
) -> Optional[Tuple[Tuple[str, str, Any], ...]]:
    if not is_leader_runtime_candidate_plan_valid(plan):
        return None
    return tuple(
        (item.symbol, item.industry_code, item)
        for item in plan.items
    )


def _projection_item_is_valid(
    value: LeaderRiskCandidateProjectionBatchItem,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    if value.status != ResearchFeatureStatus.READY:
        return value.projection is None and bool(value.reasons)
    projection = value.projection
    if is_leader_official_deterministic_risk_projection_valid(
        projection,
        symbol=symbol,
        as_of=as_of,
    ):
        return not value.reasons
    return bool(
        isinstance(projection, LeaderRiskCandidateProjection)
        and projection.symbol == symbol
        and _aware_utc(projection.as_of) == as_of
        and projection.projection_contract_id
        == LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID
        and projection.audit_contract_id
        == RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
        and projection.bundle_contract_id
        == RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
        and projection.issuer_identity
        and not value.reasons
        and all((
            projection.risk_filter_passed is False,
            projection.formal_gate_ready is False,
            projection.formal_usable is False,
            projection.applied_to_d3 is False,
            projection.applied_to_d1 is False,
        ))
    )


def _entry_item(
    *,
    index: int,
    symbol: str,
    industry_code: str,
    component: Any,
    entry: LeaderResearchInputProviderBatchEntry,
    risk_item: LeaderRiskCandidateProjectionBatchItem,
    as_of: datetime,
) -> LeaderResearchInputProviderBatchItem:
    missing_inputs = []
    reasons = []
    invalid = False
    component_not_ready = False

    history_input = entry.history_input
    if history_input is None:
        missing_inputs.append("history")
    elif (
        not isinstance(history_input, LeaderHistoryFeatureInput)
        or _aware_utc(history_input.as_of) != as_of
        or history_input.candidate.symbol != symbol
        or history_input.industry_benchmark.symbol != industry_code
    ):
        invalid = True
        reasons.append("leader_research_history_input_unverified")
    elif any(
        series.status != ResearchFeatureStatus.READY
        for series in (
            history_input.candidate,
            history_input.industry_benchmark,
            history_input.board_index,
        )
    ):
        component_not_ready = True
        reasons.append("leader_research_history_source_not_ready")

    business_input = entry.business_catalyst_input
    if business_input is None:
        missing_inputs.append("business_catalyst")
    elif (
        not isinstance(
            business_input,
            LeaderBusinessCatalystFeatureInput,
        )
        or _aware_utc(business_input.as_of) != as_of
        or business_input.symbol != symbol
        or business_input.industry_code != industry_code
        or business_input.industry_release_id
        != component.industry_release_id
    ):
        invalid = True
        reasons.append("leader_research_business_input_unverified")
    elif business_input.source_status != ResearchFeatureStatus.READY:
        component_not_ready = True
        reasons.append("leader_research_business_source_not_ready")

    tradability_input = entry.tradability_input
    if tradability_input is None:
        missing_inputs.append("tradability")
    elif (
        not isinstance(
            tradability_input,
            LeaderTradabilityFeatureInput,
        )
        or _aware_utc(tradability_input.as_of) != as_of
        or tradability_input.quote.symbol != symbol
        or tradability_input.quote_source_contract_id
        != component.quote_source_contract_id
    ):
        invalid = True
        reasons.append("leader_research_tradability_input_unverified")
    elif (
        tradability_input.quote_source_status
        != ResearchFeatureStatus.READY
    ):
        component_not_ready = True
        reasons.append("leader_research_tradability_source_not_ready")

    if risk_item.status != ResearchFeatureStatus.READY:
        missing_inputs.append("risk_projection")
    elif not _projection_item_is_valid(
        risk_item,
        symbol=symbol,
        as_of=as_of,
    ):
        if risk_item.status == ResearchFeatureStatus.READY:
            invalid = True
            reasons.append("leader_research_risk_projection_unverified")

    if invalid:
        return LeaderResearchInputProviderBatchItem(
            index=index,
            symbol=symbol,
            status=LeaderResearchInputProviderItemStatus.BLOCKED,
            missing_inputs=tuple(missing_inputs),
            reasons=_dedupe(reasons),
        )
    return LeaderResearchInputProviderBatchItem(
        index=index,
        symbol=symbol,
        status=(
            LeaderResearchInputProviderItemStatus.PARTIAL
            if missing_inputs or component_not_ready
            else LeaderResearchInputProviderItemStatus.READY
        ),
        missing_inputs=tuple(missing_inputs),
        reasons=_dedupe(reasons),
        history_input=history_input,
        business_catalyst_input=business_input,
        tradability_input=tradability_input,
    )


def _build_bound_provider_batch(
    *,
    candidates: Tuple[Tuple[str, str, Any], ...],
    radar_run_id: str,
    as_of: datetime,
    provider_contract_id: Any,
    entries: Any,
    risk_batch: Any,
    candidate_plan_id: Optional[str] = None,
) -> LeaderResearchInputProviderBatchResult:
    if (
        not isinstance(provider_contract_id, str)
        or not provider_contract_id.strip()
        or not isinstance(entries, tuple)
    ):
        return _blocked(
            CONTRACT_UNVERIFIED,
            radar_run_id=radar_run_id,
            as_of=as_of,
        )
    if any(
        not isinstance(entry, LeaderResearchInputProviderBatchEntry)
        or not isinstance(entry.symbol, str)
        or SYMBOL_PATTERN.fullmatch(entry.symbol) is None
        for entry in entries
    ):
        return _blocked(
            CONTRACT_UNVERIFIED,
            radar_run_id=radar_run_id,
            as_of=as_of,
        )
    candidate_symbols = tuple(symbol for symbol, _, _ in candidates)
    entry_symbols = tuple(entry.symbol for entry in entries)
    if (
        len(set(entry_symbols)) != len(entry_symbols)
        or set(entry_symbols) != set(candidate_symbols)
    ):
        return _blocked(
            CANDIDATE_MISMATCH,
            radar_run_id=radar_run_id,
            as_of=as_of,
        )
    if not is_leader_research_runtime_risk_batch_valid(
        risk_batch,
        as_of=as_of,
        candidate_symbols=candidate_symbols,
    ):
        return _blocked(
            RISK_CONTRACT_UNVERIFIED,
            radar_run_id=radar_run_id,
            as_of=as_of,
        )

    entries_by_symbol = {entry.symbol: entry for entry in entries}
    risk_by_symbol = {item.symbol: item for item in risk_batch.items}
    items = tuple(
        _entry_item(
            index=index,
            symbol=symbol,
            industry_code=industry_code,
            component=component,
            entry=entries_by_symbol[symbol],
            risk_item=risk_by_symbol[symbol],
            as_of=as_of,
        )
        for index, (symbol, industry_code, component) in enumerate(
            candidates
        )
    )
    ready_count = sum(
        item.status == LeaderResearchInputProviderItemStatus.READY
        for item in items
    )
    blocked_count = sum(
        item.status == LeaderResearchInputProviderItemStatus.BLOCKED
        for item in items
    )
    if ready_count == len(items):
        status = LeaderResearchInputProviderBatchStatus.READY
        reasons = ()
    elif blocked_count == len(items):
        status = LeaderResearchInputProviderBatchStatus.BLOCKED
        reasons = ("leader_research_input_provider_batch_blocked",)
    elif all(
        set(item.missing_inputs) == {
            "history",
            "business_catalyst",
            "tradability",
            "risk_projection",
        }
        for item in items
    ) and not ready_count:
        status = LeaderResearchInputProviderBatchStatus.MISSING
        reasons = ("leader_research_input_provider_batch_missing",)
    else:
        status = LeaderResearchInputProviderBatchStatus.PARTIAL
        reasons = ("leader_research_input_provider_batch_partial",)
    return _result(
        status=status,
        radar_run_id=radar_run_id,
        as_of=as_of,
        candidate_count=len(items),
        items=items,
        risk_projection_batch=risk_batch,
        provider_contract_id=provider_contract_id,
        candidate_plan_id=candidate_plan_id,
        reasons=reasons,
    )


def build_leader_research_input_provider_batch(
    input_value: Any,
) -> LeaderResearchInputProviderBatchResult:
    """验证Assembly候选输入，不抓取来源、不重算研究特征。"""

    if not isinstance(input_value, LeaderResearchInputProviderBatchInput):
        return _blocked(CONTRACT_UNVERIFIED)
    candidates = _assembly_candidates(input_value.assembly)
    if candidates is None:
        return _blocked(CONTRACT_UNVERIFIED)
    assembly = input_value.assembly
    as_of = _aware_utc(assembly.as_of)
    if (
        not isinstance(input_value.radar_run_id, str)
        or input_value.radar_run_id != assembly.radar_run_id
    ):
        return _blocked(RUN_MISMATCH)
    input_as_of = _aware_utc(input_value.as_of)
    if input_as_of is None or input_as_of != as_of:
        return _blocked(
            AS_OF_MISMATCH,
            radar_run_id=assembly.radar_run_id,
            as_of=as_of,
        )
    return _build_bound_provider_batch(
        candidates=candidates,
        radar_run_id=assembly.radar_run_id,
        as_of=as_of,
        provider_contract_id=input_value.provider_contract_id,
        entries=input_value.entries,
        risk_batch=input_value.risk_projection_batch,
    )


def build_leader_research_input_provider_batch_from_plan(
    input_value: Any,
) -> LeaderResearchInputProviderBatchResult:
    """验证候选计划输入，不抓取来源、不提前构建Assembly。"""

    if not isinstance(
        input_value,
        LeaderResearchInputProviderPlanBatchInput,
    ):
        return _blocked(CONTRACT_UNVERIFIED)
    plan = input_value.candidate_plan
    candidates = _plan_candidates(plan)
    if candidates is None or not isinstance(plan, LeaderRuntimeCandidatePlan):
        return _blocked(CONTRACT_UNVERIFIED)
    as_of = _aware_utc(plan.as_of)
    if (
        not isinstance(input_value.radar_run_id, str)
        or input_value.radar_run_id != plan.radar_run_id
    ):
        return _blocked(RUN_MISMATCH)
    input_as_of = _aware_utc(input_value.as_of)
    if input_as_of is None or input_as_of != as_of:
        return _blocked(
            AS_OF_MISMATCH,
            radar_run_id=plan.radar_run_id,
            as_of=as_of,
        )
    return _build_bound_provider_batch(
        candidates=candidates,
        radar_run_id=plan.radar_run_id,
        as_of=as_of,
        provider_contract_id=input_value.provider_contract_id,
        entries=input_value.entries,
        risk_batch=input_value.risk_projection_batch,
        candidate_plan_id=plan.candidate_set_id,
    )
