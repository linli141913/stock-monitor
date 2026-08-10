"""阶段6L-F2多证券研究就绪审计的纯内存批次组装。

本模块逐条调用F1审计器，统一校验候选身份和批次时点，并把单证券失败
隔离在对应条目。它不重算研究特征，也不接入运行时、评分或状态机。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_audit import (
    AS_OF_MISMATCH as F1_AS_OF_MISMATCH,
    BUSINESS_DISPROVED,
    BUSINESS_UNAVAILABLE,
    BUSINESS_UNCONFIRMED,
    CONTRACT_UNVERIFIED as F1_CONTRACT_UNVERIFIED,
    CROSS_SECTION_UNAVAILABLE,
    FORMAL_EVALUATION_DISABLED,
    FORMAL_INDUSTRY_UNAVAILABLE,
    FORMAL_RISK_UNAVAILABLE,
    FORMAL_STOCK_FAILED,
    FORMAL_STOCK_UNAVAILABLE,
    HISTORY_RECOVERY_ABSENT,
    HISTORY_UNAVAILABLE,
    IDENTITY_MISMATCH as F1_IDENTITY_MISMATCH,
    LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID,
    LIQUIDITY_UNAVAILABLE,
    REQUIRED_ITEM_KEYS,
    RISK_UNAVAILABLE,
    LeaderResearchReadinessAuditInput,
    LeaderResearchReadinessAuditItem,
    LeaderResearchReadinessAuditResult,
    LeaderResearchReadinessAuditStatus,
    build_leader_research_readiness_audit,
)


UTC = timezone.utc
LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID = (
    "radar-leader-research-readiness-audit-batch-v1"
)

CONTRACT_UNVERIFIED = (
    "leader_research_readiness_audit_batch_contract_unverified"
)
AS_OF_TIMEZONE_MISSING = (
    "leader_research_readiness_audit_batch_as_of_timezone_missing"
)
EMPTY_BATCH = "leader_research_readiness_audit_batch_empty"
DUPLICATE_SYMBOL = (
    "leader_research_readiness_audit_batch_duplicate_symbol"
)
ITEM_CONTRACT_UNVERIFIED = (
    "leader_research_readiness_audit_batch_item_contract_unverified"
)
MAPPING_IDENTITY_MISMATCH = (
    "leader_research_readiness_audit_batch_mapping_identity_mismatch"
)
AS_OF_MISMATCH = (
    "leader_research_readiness_audit_batch_as_of_mismatch"
)
ITEM_AUDIT_FAILED = (
    "leader_research_readiness_audit_batch_item_audit_failed"
)
RESULT_UNVERIFIED = (
    "leader_research_readiness_audit_batch_result_unverified"
)
STABLE_F1_BLOCKER_CODES = frozenset((
    F1_CONTRACT_UNVERIFIED,
    F1_IDENTITY_MISMATCH,
    F1_AS_OF_MISMATCH,
    FORMAL_INDUSTRY_UNAVAILABLE,
    FORMAL_STOCK_UNAVAILABLE,
    FORMAL_STOCK_FAILED,
    CROSS_SECTION_UNAVAILABLE,
    LIQUIDITY_UNAVAILABLE,
    HISTORY_UNAVAILABLE,
    HISTORY_RECOVERY_ABSENT,
    BUSINESS_DISPROVED,
    BUSINESS_UNCONFIRMED,
    BUSINESS_UNAVAILABLE,
    RISK_UNAVAILABLE,
    FORMAL_RISK_UNAVAILABLE,
    FORMAL_EVALUATION_DISABLED,
))
BLOCKED_F1_CODES = frozenset((
    F1_CONTRACT_UNVERIFIED,
    F1_IDENTITY_MISMATCH,
    F1_AS_OF_MISMATCH,
))


class LeaderResearchReadinessAuditBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderResearchReadinessAuditBatchEntry:
    symbol: str
    audit_input: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchReadinessAuditBatchInput:
    as_of: datetime
    entries: Tuple[
        LeaderResearchReadinessAuditBatchEntry,
        ...,
    ] = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchReadinessAuditBatchItem:
    index: int
    symbol: str
    input_symbol: Optional[str]
    status: LeaderResearchReadinessAuditStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    audit: Optional[
        LeaderResearchReadinessAuditResult
    ] = field(default=None, repr=False)
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def included(self) -> bool:
        return (
            self.status
            in (
                LeaderResearchReadinessAuditStatus.READY,
                LeaderResearchReadinessAuditStatus.PARTIAL,
            )
            and self.audit is not None
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "inputSymbol": self.input_symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "included": self.included,
            "auditContractId": (
                self.audit.contract_id
                if self.audit is not None
                else None
            ),
            "firstVetoReason": (
                self.audit.first_veto_reason
                if self.audit is not None
                else None
            ),
            "firstResearchBlockerReason": (
                self.audit.first_research_blocker_reason
                if self.audit is not None
                else None
            ),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": (
                    self.state_transition_allowed
                ),
            },
        }


@dataclass(frozen=True)
class LeaderResearchReadinessAuditBatchResult:
    status: LeaderResearchReadinessAuditBatchStatus
    as_of: Optional[datetime]
    input_count: int
    items: Tuple[
        LeaderResearchReadinessAuditBatchItem,
        ...,
    ] = field(default_factory=tuple)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    batch_contract_id: str = (
        LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderResearchReadinessAuditStatus.READY
            and item.included
            for item in self.items
        )

    @property
    def partial_count(self) -> int:
        return sum(
            item.status == LeaderResearchReadinessAuditStatus.PARTIAL
            and item.included
            for item in self.items
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            item.status == LeaderResearchReadinessAuditStatus.BLOCKED
            for item in self.items
        )

    @property
    def audit_count(self) -> int:
        return self.ready_count + self.partial_count

    @property
    def audits_by_symbol(
        self,
    ) -> Mapping[str, LeaderResearchReadinessAuditResult]:
        return MappingProxyType({
            item.symbol: item.audit
            for item in self.items
            if item.included and item.audit is not None
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "batchContractId": self.batch_contract_id,
            "status": self.status.value,
            "asOf": (
                self.as_of.isoformat()
                if self.as_of is not None
                else None
            ),
            "reasons": list(self.reasons),
            "inputCount": self.input_count,
            "auditCount": self.audit_count,
            "readyCount": self.ready_count,
            "partialCount": self.partial_count,
            "blockedCount": self.blocked_count,
            "items": [
                item.to_evidence()
                for item in self.items
            ],
            "auditsBySymbol": {
                symbol: _audit_summary(audit)
                for symbol, audit in self.audits_by_symbol.items()
            },
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
    return tuple(dict.fromkeys(
        value
        for value in values
        if isinstance(value, str) and value
    ))


def _valid_strings(values: Any, *, allow_empty: bool = True) -> bool:
    return (
        isinstance(values, tuple)
        and (allow_empty or bool(values))
        and all(
            isinstance(value, str) and bool(value)
            for value in values
        )
    )


def _batch_result(
    *,
    status: LeaderResearchReadinessAuditBatchStatus,
    as_of: Optional[datetime],
    input_count: int,
    reasons: Sequence[str],
    items: Sequence[
        LeaderResearchReadinessAuditBatchItem
    ] = (),
) -> LeaderResearchReadinessAuditBatchResult:
    return LeaderResearchReadinessAuditBatchResult(
        status=status,
        as_of=as_of,
        input_count=input_count,
        items=tuple(items),
        reasons=_dedupe(reasons),
    )


def _batch_item(
    *,
    index: int,
    symbol: str,
    input_symbol: Optional[str],
    status: LeaderResearchReadinessAuditStatus,
    reasons: Sequence[str],
    audit: Optional[
        LeaderResearchReadinessAuditResult
    ] = None,
) -> LeaderResearchReadinessAuditBatchItem:
    return LeaderResearchReadinessAuditBatchItem(
        index=index,
        symbol=symbol,
        input_symbol=input_symbol,
        status=status,
        reasons=_dedupe(reasons),
        audit=audit,
    )


def _audit_item_is_valid(
    item: Any,
) -> bool:
    if (
        not isinstance(item, LeaderResearchReadinessAuditItem)
        or not isinstance(item.key, str)
        or not item.key
        or not isinstance(item.status, ResearchFeatureStatus)
        or not isinstance(item.evidence_available, bool)
        or not isinstance(item.requirement_satisfied, bool)
        or not _valid_strings(item.reasons)
        or not _valid_strings(item.source_contract_ids)
    ):
        return False
    if item.evidence_available is not (
        item.status == ResearchFeatureStatus.READY
    ):
        return False
    if item.requirement_satisfied and not item.evidence_available:
        return False
    if item.requirement_satisfied is not (item.veto_reason is None):
        return False
    return bool(
        item.veto_reason is None
        or item.veto_reason in STABLE_F1_BLOCKER_CODES
    )


def _audit_result_is_valid(
    result: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    if (
        not isinstance(result, LeaderResearchReadinessAuditResult)
        or not isinstance(
            result.status,
            LeaderResearchReadinessAuditStatus,
        )
        or result.contract_id
        != LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID
        or result.symbol != symbol
        or _aware_utc(result.as_of) != as_of
        or any((
            result.formal_score_ready is not False,
            result.formal_gate_ready is not False,
            result.formal_usable is not False,
            result.state_transition_allowed is not False,
        ))
        or isinstance(result.required_item_count, bool)
        or result.required_item_count != len(REQUIRED_ITEM_KEYS)
        or isinstance(result.evidence_available_count, bool)
        or not isinstance(result.evidence_available_count, int)
        or isinstance(result.satisfied_count, bool)
        or not isinstance(result.satisfied_count, int)
        or not _valid_strings(result.missing_items)
        or not _valid_strings(result.blocked_items)
        or not _valid_strings(
            result.ordered_blocker_codes,
            allow_empty=False,
        )
        or not isinstance(result.first_veto_reason, str)
        or not result.first_veto_reason
        or result.first_veto_reason not in STABLE_F1_BLOCKER_CODES
        or (
            result.first_research_blocker_reason is not None
            and (
                not isinstance(
                    result.first_research_blocker_reason,
                    str,
                )
                or not result.first_research_blocker_reason
                or (
                    result.first_research_blocker_reason
                    not in STABLE_F1_BLOCKER_CODES
                )
            )
        )
        or any(
            code not in STABLE_F1_BLOCKER_CODES
            for code in result.ordered_blocker_codes
        )
        or result.ordered_blocker_codes[0]
        != result.first_veto_reason
    ):
        return False

    if result.status == LeaderResearchReadinessAuditStatus.BLOCKED:
        return bool(
            result.items == ()
            and result.evidence_available_count == 0
            and result.satisfied_count == 0
            and result.missing_items == ()
            and result.blocked_items == ()
            and result.first_veto_reason in BLOCKED_F1_CODES
            and result.first_research_blocker_reason is None
            and result.ordered_blocker_codes
            == (result.first_veto_reason,)
        )

    if (
        not isinstance(result.items, tuple)
        or tuple(item.key for item in result.items)
        != REQUIRED_ITEM_KEYS
        or any(
            not _audit_item_is_valid(item)
            for item in result.items
        )
    ):
        return False
    evidence_available_count = sum(
        item.evidence_available
        for item in result.items
    )
    satisfied_count = sum(
        item.requirement_satisfied
        for item in result.items
    )
    missing_items = tuple(
        item.key
        for item in result.items
        if not item.evidence_available
    )
    blocked_items = tuple(
        item.key
        for item in result.items
        if (
            item.evidence_available
            and not item.requirement_satisfied
        )
    )
    expected_status = (
        LeaderResearchReadinessAuditStatus.READY
        if evidence_available_count == len(REQUIRED_ITEM_KEYS)
        else LeaderResearchReadinessAuditStatus.PARTIAL
    )
    research_blockers = tuple(
        item.veto_reason
        for item in result.items
        if item.veto_reason is not None
    )
    stock_blocker = (
        FORMAL_STOCK_FAILED
        if (
            result.items[1].evidence_available
            and not result.items[1].requirement_satisfied
        )
        else FORMAL_STOCK_UNAVAILABLE
    )
    expected_blocker_codes = _dedupe((
        FORMAL_INDUSTRY_UNAVAILABLE,
        stock_blocker,
        *research_blockers,
        FORMAL_EVALUATION_DISABLED,
    ))
    return bool(
        result.status == expected_status
        and result.evidence_available_count
        == evidence_available_count
        and result.satisfied_count == satisfied_count
        and result.missing_items == missing_items
        and result.blocked_items == blocked_items
        and result.first_veto_reason
        == FORMAL_INDUSTRY_UNAVAILABLE
        and result.first_research_blocker_reason
        == (research_blockers[0] if research_blockers else None)
        and result.ordered_blocker_codes == expected_blocker_codes
    )


def is_leader_research_readiness_audit_valid(
    result: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    """复用F2深层不变量，判断单份F1审计能否被只读下游消费。"""

    normalized_as_of = _aware_utc(as_of)
    return bool(
        isinstance(symbol, str)
        and symbol.strip()
        and normalized_as_of is not None
        and _audit_result_is_valid(
            result,
            symbol=symbol.strip(),
            as_of=normalized_as_of,
        )
    )


def _aggregate_status(
    items: Sequence[LeaderResearchReadinessAuditBatchItem],
) -> LeaderResearchReadinessAuditBatchStatus:
    if all(
        item.status == LeaderResearchReadinessAuditStatus.READY
        and item.included
        for item in items
    ):
        return LeaderResearchReadinessAuditBatchStatus.READY
    if any(item.included for item in items):
        return LeaderResearchReadinessAuditBatchStatus.PARTIAL
    return LeaderResearchReadinessAuditBatchStatus.BLOCKED


def _audit_summary(
    audit: LeaderResearchReadinessAuditResult,
) -> Mapping[str, object]:
    return {
        "contractId": audit.contract_id,
        "auditStatus": audit.status.value,
        "firstVetoReason": audit.first_veto_reason,
        "firstResearchBlockerReason": (
            audit.first_research_blocker_reason
        ),
        "missingItems": list(audit.missing_items),
        "blockedItems": list(audit.blocked_items),
    }


def build_leader_research_readiness_audit_batch(
    input_value: Any,
) -> LeaderResearchReadinessAuditBatchResult:
    """逐股执行F1审计，返回合法结果的只读证券映射。"""

    if not isinstance(
        input_value,
        LeaderResearchReadinessAuditBatchInput,
    ):
        return _batch_result(
            status=LeaderResearchReadinessAuditBatchStatus.BLOCKED,
            as_of=None,
            input_count=0,
            reasons=(CONTRACT_UNVERIFIED,),
        )

    entries = input_value.entries
    input_count = len(entries) if isinstance(entries, tuple) else 0
    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _batch_result(
            status=LeaderResearchReadinessAuditBatchStatus.BLOCKED,
            as_of=None,
            input_count=input_count,
            reasons=(AS_OF_TIMEZONE_MISSING,),
        )
    if not isinstance(entries, tuple) or any(
        not isinstance(
            entry,
            LeaderResearchReadinessAuditBatchEntry,
        )
        or not isinstance(entry.symbol, str)
        or not entry.symbol.strip()
        for entry in entries
    ):
        return _batch_result(
            status=LeaderResearchReadinessAuditBatchStatus.BLOCKED,
            as_of=as_of,
            input_count=input_count,
            reasons=(CONTRACT_UNVERIFIED,),
        )
    if not entries:
        return _batch_result(
            status=LeaderResearchReadinessAuditBatchStatus.MISSING,
            as_of=as_of,
            input_count=0,
            reasons=(EMPTY_BATCH,),
        )

    mapping_symbols = tuple(
        entry.symbol.strip()
        for entry in entries
    )
    input_symbols = tuple(
        entry.audit_input.symbol.strip()
        for entry in entries
        if isinstance(
            entry.audit_input,
            LeaderResearchReadinessAuditInput,
        )
        and isinstance(entry.audit_input.symbol, str)
        and entry.audit_input.symbol.strip()
    )
    if (
        len(set(mapping_symbols)) != len(mapping_symbols)
        or len(set(input_symbols)) != len(input_symbols)
    ):
        return _batch_result(
            status=LeaderResearchReadinessAuditBatchStatus.BLOCKED,
            as_of=as_of,
            input_count=input_count,
            reasons=(DUPLICATE_SYMBOL,),
        )

    items = []
    for index, entry in enumerate(entries):
        symbol = entry.symbol.strip()
        audit_input = entry.audit_input
        input_symbol = (
            audit_input.symbol.strip()
            if (
                isinstance(
                    audit_input,
                    LeaderResearchReadinessAuditInput,
                )
                and isinstance(audit_input.symbol, str)
                and audit_input.symbol.strip()
            )
            else None
        )
        if not isinstance(
            audit_input,
            LeaderResearchReadinessAuditInput,
        ):
            items.append(_batch_item(
                index=index,
                symbol=symbol,
                input_symbol=input_symbol,
                status=LeaderResearchReadinessAuditStatus.BLOCKED,
                reasons=(ITEM_CONTRACT_UNVERIFIED,),
            ))
            continue
        if symbol != input_symbol:
            items.append(_batch_item(
                index=index,
                symbol=symbol,
                input_symbol=input_symbol,
                status=LeaderResearchReadinessAuditStatus.BLOCKED,
                reasons=(MAPPING_IDENTITY_MISMATCH,),
            ))
            continue
        if _aware_utc(audit_input.as_of) != as_of:
            items.append(_batch_item(
                index=index,
                symbol=symbol,
                input_symbol=input_symbol,
                status=LeaderResearchReadinessAuditStatus.BLOCKED,
                reasons=(AS_OF_MISMATCH,),
            ))
            continue

        try:
            audit_result = build_leader_research_readiness_audit(
                audit_input
            )
        except Exception:
            items.append(_batch_item(
                index=index,
                symbol=symbol,
                input_symbol=input_symbol,
                status=LeaderResearchReadinessAuditStatus.BLOCKED,
                reasons=(ITEM_AUDIT_FAILED,),
            ))
            continue

        if not _audit_result_is_valid(
            audit_result,
            symbol=symbol,
            as_of=as_of,
        ):
            items.append(_batch_item(
                index=index,
                symbol=symbol,
                input_symbol=input_symbol,
                status=LeaderResearchReadinessAuditStatus.BLOCKED,
                reasons=(RESULT_UNVERIFIED,),
            ))
            continue
        items.append(_batch_item(
            index=index,
            symbol=symbol,
            input_symbol=input_symbol,
            status=audit_result.status,
            reasons=(
                audit_result.ordered_blocker_codes
                if (
                    audit_result.status
                    == LeaderResearchReadinessAuditStatus.BLOCKED
                )
                else ()
            ),
            audit=audit_result,
        ))

    return _batch_result(
        status=_aggregate_status(items),
        as_of=as_of,
        input_count=input_count,
        reasons=(),
        items=items,
    )
