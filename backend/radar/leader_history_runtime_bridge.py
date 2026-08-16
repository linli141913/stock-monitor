"""把已验证的历史连续性输入接到龙头研究来源准入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_explicit_missing_leader_research_provider_input,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchHistoryAdmissionEntry,
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionResult,
    build_leader_research_source_admission,
)


LEADER_HISTORY_RUNTIME_BRIDGE_CONTRACT_ID = (
    "radar-leader-history-runtime-bridge-v1"
)
SOURCE_MISSING = "leader_history_runtime_bridge_source_missing"
SOURCE_UNVERIFIED = "leader_history_runtime_bridge_source_unverified"
BRIDGE_MISSING = "leader_history_runtime_bridge_missing"


class LeaderHistoryRuntimeBridgeStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderHistoryRuntimeBridgeItem:
    index: int
    symbol: str
    status: LeaderHistoryRuntimeBridgeStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderHistoryRuntimeBridgeResult:
    status: LeaderHistoryRuntimeBridgeStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    items: Tuple[LeaderHistoryRuntimeBridgeItem, ...]
    reasons: Tuple[str, ...]
    source_admission: LeaderResearchSourceAdmissionResult = field(repr=False)
    provider_input: Any = field(repr=False)
    history_entries: Optional[Tuple[LeaderResearchHistoryAdmissionEntry, ...]] = field(
        default=None,
        repr=False,
    )
    history_inputs_by_symbol: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
    )
    contract_id: str = LEADER_HISTORY_RUNTIME_BRIDGE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderHistoryRuntimeBridgeStatus.READY
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


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _missing_items(
    symbols: Tuple[str, ...],
    reason: str,
) -> Tuple[LeaderHistoryRuntimeBridgeItem, ...]:
    return tuple(
        LeaderHistoryRuntimeBridgeItem(
            index=index,
            symbol=symbol,
            status=LeaderHistoryRuntimeBridgeStatus.MISSING,
            reasons=(reason,),
        )
        for index, symbol in enumerate(symbols)
    )


def build_leader_history_runtime_bridge(
    context: LeaderResearchRuntimeSourceContext,
    *,
    history_entries: Any = None,
) -> LeaderHistoryRuntimeBridgeResult:
    """校验显式历史证据批次；不抓取来源、不写库、不补齐缺失数据。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(SOURCE_UNVERIFIED)
    plan = context.candidate_plan
    symbols = tuple(item.symbol for item in plan.items)
    normalized_entries = (
        history_entries
        if isinstance(history_entries, tuple)
        else None
    )
    source_admission = build_leader_research_source_admission(
        LeaderResearchSourceAdmissionInput(
            context=context,
            history_entries=history_entries,
            business_review_batch=None,
            tradability_bundle=None,
            risk_projection_bundle=None,
        )
    )
    provider_input = source_admission.provider_input
    if provider_input is None:
        provider_input = build_explicit_missing_leader_research_provider_input(
            context
        )

    provider_result = source_admission.provider_result
    history_inputs = (
        dict(provider_result.history_inputs_by_symbol)
        if provider_result is not None
        else {}
    )
    component = next(
        (
            item
            for item in source_admission.components
            if item.name == "history"
        ),
        None,
    )
    component_blocked = (
        component is not None
        and component.status.value in {"blocked", "source_unverified"}
    ) or source_admission.status.value == "blocked"

    items = []
    for index, symbol in enumerate(symbols):
        if symbol in history_inputs:
            items.append(LeaderHistoryRuntimeBridgeItem(
                index=index,
                symbol=symbol,
                status=LeaderHistoryRuntimeBridgeStatus.READY,
            ))
            continue
        reason = SOURCE_UNVERIFIED if component_blocked else None
        if reason is None and normalized_entries is not None:
            if (
                len(normalized_entries) == len(symbols)
                and all(
                    type(entry) is LeaderResearchHistoryAdmissionEntry
                    for entry in normalized_entries
                )
                and tuple(entry.symbol for entry in normalized_entries)
                == symbols
            ):
                entry_reasons = tuple(
                    getattr(
                        getattr(normalized_entries[index], "result", None),
                        "reasons",
                        (),
                    )
                )
                reason = entry_reasons[0] if entry_reasons else BRIDGE_MISSING
            else:
                reason = SOURCE_UNVERIFIED
        if reason is None:
            reason = SOURCE_MISSING
        items.append(LeaderHistoryRuntimeBridgeItem(
            index=index,
            symbol=symbol,
            status=LeaderHistoryRuntimeBridgeStatus.MISSING,
            reasons=(reason,),
        ))

    missing = any(
        item.status == LeaderHistoryRuntimeBridgeStatus.MISSING
        for item in items
    )
    reasons = []
    if history_entries is None:
        reasons.append(SOURCE_MISSING)
    elif component_blocked:
        reasons.append(SOURCE_UNVERIFIED)
    if missing and not reasons:
        reasons.append(BRIDGE_MISSING)
    return LeaderHistoryRuntimeBridgeResult(
        status=(
            LeaderHistoryRuntimeBridgeStatus.MISSING
            if missing
            else LeaderHistoryRuntimeBridgeStatus.READY
        ),
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(plan.items),
        items=tuple(items),
        reasons=_dedupe(reasons),
        history_entries=normalized_entries,
        history_inputs_by_symbol=MappingProxyType(history_inputs),
        source_admission=source_admission,
        provider_input=provider_input,
    )
