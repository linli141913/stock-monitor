"""把显式复合可交易性POC批次接到龙头研究来源准入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_explicit_missing_leader_research_provider_input,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionResult,
    LeaderResearchSourceComponentStatus,
    LeaderResearchTradabilityAdmissionBundle,
    build_leader_research_source_admission,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositeTradabilityQuery,
    PublicCompositeTradabilityReport,
    run_public_composite_tradability_poc,
)


LEADER_TRADABILITY_RUNTIME_BRIDGE_CONTRACT_ID = (
    "radar-leader-tradability-runtime-bridge-v1"
)
SOURCE_MISSING = "leader_tradability_runtime_bridge_source_missing"
SOURCE_UNVERIFIED = (
    "leader_tradability_runtime_bridge_source_unverified"
)
BRIDGE_MISSING = "leader_tradability_runtime_bridge_missing"
UTC = timezone.utc


class LeaderTradabilityRuntimeBridgeStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderTradabilityRuntimeBridgeItem:
    index: int
    symbol: str
    status: LeaderTradabilityRuntimeBridgeStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderTradabilityRuntimeBridgeResult:
    status: LeaderTradabilityRuntimeBridgeStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    items: Tuple[LeaderTradabilityRuntimeBridgeItem, ...]
    reasons: Tuple[str, ...]
    source_admission: LeaderResearchSourceAdmissionResult = field(repr=False)
    provider_input: Any = field(repr=False)
    tradability_admission_value: Any = field(repr=False)
    tradability_inputs_by_symbol: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
    )
    contract_id: str = LEADER_TRADABILITY_RUNTIME_BRIDGE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderTradabilityRuntimeBridgeStatus.READY
            for item in self.items
        )

    @property
    def missing_count(self) -> int:
        return self.candidate_count - self.ready_count

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "missingCount": self.missing_count,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "sourceAdmissionStatus": self.source_admission.status.value,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
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


def _replayable_report(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> Optional[PublicCompositeTradabilityReport]:
    """只为同批且可重放的公开报告保留原始失败原因。"""

    plan = context.candidate_plan
    symbols = tuple(item.symbol for item in plan.items)
    if (
        type(value) is not LeaderResearchTradabilityAdmissionBundle
        or value.candidate_plan_id != plan.candidate_set_id
        or value.radar_run_id != plan.radar_run_id
        or value.quote_batch_id != plan.quote_batch_id
        or type(value.query) is not PublicCompositeTradabilityQuery
        or _aware_utc(value.query.as_of) != plan.as_of
        or tuple(item.symbol for item in value.query.securities) != symbols
        or tuple(item.symbol for item in value.quotes) != symbols
        or value.quotes != tuple(
            context.quotes_by_symbol[symbol] for symbol in symbols
        )
        or type(value.report) is not PublicCompositeTradabilityReport
    ):
        return None
    replayed = run_public_composite_tradability_poc(
        query=value.query,
        quotes=value.quotes,
        official_observations=value.official_observations,
        aggregator_observations=value.aggregator_observations,
        executed=True,
    )
    if replayed != value.report:
        return None
    return replayed


def _record_reasons_by_symbol(
    report: Optional[PublicCompositeTradabilityReport],
) -> Mapping[str, Tuple[str, ...]]:
    if report is None:
        return {}
    return {
        str(getattr(record, "symbol", "")): tuple(
            getattr(record, "reasons", ())
        )
        for record in report.records
        if getattr(record, "symbol", None)
    }


def build_leader_tradability_runtime_bridge(
    context: LeaderResearchRuntimeSourceContext,
    *,
    tradability_bundle: Any = None,
) -> LeaderTradabilityRuntimeBridgeResult:
    """重放显式可交易性来源包；不抓取、不写库、不补造观察。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(SOURCE_UNVERIFIED)
    plan = context.candidate_plan
    symbols = tuple(item.symbol for item in plan.items)
    source_admission = build_leader_research_source_admission(
        LeaderResearchSourceAdmissionInput(
            context=context,
            history_entries=None,
            business_review_batch=None,
            tradability_bundle=tradability_bundle,
            risk_projection_bundle=None,
        )
    )
    provider_input = source_admission.provider_input
    if provider_input is None:
        provider_input = build_explicit_missing_leader_research_provider_input(
            context
        )
    provider_result = source_admission.provider_result
    tradability_inputs = (
        dict(provider_result.tradability_inputs_by_symbol)
        if provider_result is not None
        else {}
    )
    component = next(
        (
            item
            for item in source_admission.components
            if item.name == "tradability"
        ),
        None,
    )
    replayed_report = _replayable_report(
        tradability_bundle,
        context=context,
    )

    if tradability_bundle is None:
        top_reasons = (SOURCE_MISSING,)
    elif (
        component is None
        or component.status == LeaderResearchSourceComponentStatus.BLOCKED
    ):
        top_reasons = (
            tuple(replayed_report.reasons)
            if replayed_report is not None and replayed_report.reasons
            else (SOURCE_UNVERIFIED,)
        )
    else:
        top_reasons = component.reasons
    record_reasons = _record_reasons_by_symbol(replayed_report)

    items = tuple(
        LeaderTradabilityRuntimeBridgeItem(
            index=index,
            symbol=symbol,
            status=(
                LeaderTradabilityRuntimeBridgeStatus.READY
                if symbol in tradability_inputs
                else LeaderTradabilityRuntimeBridgeStatus.MISSING
            ),
            reasons=(
                ()
                if symbol in tradability_inputs
                else record_reasons.get(symbol) or top_reasons
                or (BRIDGE_MISSING,)
            ),
        )
        for index, symbol in enumerate(symbols)
    )
    missing = any(
        item.status == LeaderTradabilityRuntimeBridgeStatus.MISSING
        for item in items
    )
    reasons = _dedupe(
        top_reasons
        or ((BRIDGE_MISSING,) if missing else ())
    )
    return LeaderTradabilityRuntimeBridgeResult(
        status=(
            LeaderTradabilityRuntimeBridgeStatus.MISSING
            if missing
            else LeaderTradabilityRuntimeBridgeStatus.READY
        ),
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(symbols),
        items=items,
        reasons=reasons,
        source_admission=source_admission,
        provider_input=provider_input,
        tradability_admission_value=tradability_bundle,
        tradability_inputs_by_symbol=MappingProxyType(tradability_inputs),
    )
