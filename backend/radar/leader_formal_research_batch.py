"""龙头正式研究输入的只读批次提供方。

这里的 ``ready`` 只表示同一候选计划、同一运行批次和同一时点
所需的研究证据已经齐备，可进入后续确定性规则评估。它不表示
股票通过正式门槛，也不生成分数、梯队状态或状态迁移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.leader_research_readiness_audit import (
    LeaderResearchReadinessAuditItem,
    LeaderResearchReadinessAuditStatus,
)
from radar.leader_research_readiness_audit_batch import (
    LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID,
    LeaderResearchReadinessAuditBatchItem,
    LeaderResearchReadinessAuditBatchResult,
    LeaderResearchReadinessAuditBatchStatus,
    is_leader_research_readiness_audit_valid,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sector_rule_readiness import (
    SECTOR_RULE_READINESS_CONTRACT_ID,
    SectorRuleReadinessItem,
    SectorRuleReadinessResult,
    SectorRuleReadinessStatus,
)


UTC = timezone.utc
SAFE_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_:.+-]{0,119}$")
LEADER_FORMAL_RESEARCH_BATCH_CONTRACT_ID = (
    "radar-leader-formal-research-batch-v1"
)
SECTOR_ITEM_KEYS = (
    "classification_history",
    "classification_mapping",
    "current_sector_features",
    "market_baseline",
    "history_coverage",
    "same_minute_turnover_history",
    "persistence_history",
    "comparable_industries",
    "threshold_approval",
)
RESEARCH_ITEM_KEYS = (
    "candidate_discovery",
    "sector_formal_gate",
    "security_tradability",
    "cross_section",
    "liquidity",
    "history_continuity",
    "business_catalyst",
    "risk_evidence",
)

CANDIDATE_PLAN_UNVERIFIED = (
    "leader_candidate_discovery_contract_unverified"
)
SECTOR_READINESS_MISSING = "leader_sector_rule_readiness_missing"
SECTOR_READINESS_UNVERIFIED = (
    "leader_sector_rule_readiness_contract_unverified"
)
SECTOR_READINESS_IDENTITY_MISMATCH = (
    "leader_sector_rule_readiness_identity_mismatch"
)
RESEARCH_BATCH_MISSING = "leader_research_readiness_batch_missing"
RESEARCH_BATCH_UNVERIFIED = (
    "leader_research_readiness_batch_contract_unverified"
)
RESEARCH_BATCH_IDENTITY_MISMATCH = (
    "leader_research_readiness_batch_identity_mismatch"
)
FORMAL_RESEARCH_BATCH_MISSING = "leader_formal_research_batch_missing"


class LeaderFormalResearchBatchStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderFormalResearchItem:
    key: str
    status: LeaderFormalResearchBatchStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderFormalResearchCandidate:
    index: int
    symbol: str
    industry_code: str
    status: LeaderFormalResearchBatchStatus
    items: Tuple[LeaderFormalResearchItem, ...]
    first_missing_reason: Optional[str] = None
    first_veto_reason: Optional[str] = None
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def item(self, key: str) -> LeaderFormalResearchItem:
        for value in self.items:
            if value.key == key:
                return value
        raise KeyError(key)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "industryCode": self.industry_code,
            "status": self.status.value,
            "firstMissingReason": self.first_missing_reason,
            "firstVetoReason": self.first_veto_reason,
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True)
class LeaderFormalResearchBatchResult:
    status: LeaderFormalResearchBatchStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_plan_id: Optional[str]
    candidate_count: int
    items: Tuple[LeaderFormalResearchCandidate, ...] = field(
        default_factory=tuple
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    sector_rule_version: Optional[str] = None
    contract_id: str = LEADER_FORMAL_RESEARCH_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderFormalResearchBatchStatus.READY
            for item in self.items
        )

    @property
    def missing_count(self) -> int:
        return sum(
            item.status == LeaderFormalResearchBatchStatus.MISSING
            for item in self.items
        )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "missingCount": self.missing_count,
            "sectorRuleVersion": self.sector_rule_version,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
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


def _reasons_valid(value: Any) -> bool:
    return bool(
        isinstance(value, tuple)
        and all(
            isinstance(reason, str)
            and SAFE_REASON_PATTERN.fullmatch(reason) is not None
            for reason in value
        )
    )


def _item(
    key: str,
    reasons: Sequence[str] = (),
) -> LeaderFormalResearchItem:
    normalized = _dedupe(reasons)
    return LeaderFormalResearchItem(
        key=key,
        status=(
            LeaderFormalResearchBatchStatus.MISSING
            if normalized
            else LeaderFormalResearchBatchStatus.READY
        ),
        reasons=normalized,
    )


def _result(
    *,
    status: LeaderFormalResearchBatchStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    reasons: Sequence[str],
    items: Sequence[LeaderFormalResearchCandidate] = (),
    sector_rule_version: Optional[str] = None,
) -> LeaderFormalResearchBatchResult:
    return LeaderFormalResearchBatchResult(
        status=status,
        radar_run_id=plan.radar_run_id if plan is not None else None,
        as_of=plan.as_of if plan is not None else None,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        candidate_count=plan.candidate_count if plan is not None else 0,
        items=tuple(items),
        reasons=_dedupe(reasons),
        sector_rule_version=sector_rule_version,
    )


def _sector_result_valid(
    value: Any,
    *,
    plan: LeaderRuntimeCandidatePlan,
) -> Tuple[Optional[str], Tuple[str, ...]]:
    if value is None:
        return SECTOR_READINESS_MISSING, (SECTOR_READINESS_MISSING,)
    if (
        type(value) is not SectorRuleReadinessResult
        or value.contract_id != SECTOR_RULE_READINESS_CONTRACT_ID
        or not isinstance(value.status, SectorRuleReadinessStatus)
        or not isinstance(value.rule_version, str)
        or not value.rule_version.strip()
        or not isinstance(value.items, tuple)
        or tuple(getattr(item, "key", None) for item in value.items)
        != SECTOR_ITEM_KEYS
        or not _reasons_valid(value.reasons)
        or any(
            type(item) is not SectorRuleReadinessItem
            or not isinstance(item.status, SectorRuleReadinessStatus)
            or not _reasons_valid(item.reasons)
            or (
                item.status == SectorRuleReadinessStatus.READY
                and bool(item.reasons)
            )
            or (
                item.status == SectorRuleReadinessStatus.MISSING
                and not item.reasons
            )
            for item in value.items
        )
    ):
        return SECTOR_READINESS_UNVERIFIED, (
            SECTOR_READINESS_UNVERIFIED,
        )
    if (
        value.radar_run_id != plan.radar_run_id
        or _aware_utc(value.as_of) != plan.as_of
    ):
        return SECTOR_READINESS_IDENTITY_MISMATCH, (
            SECTOR_READINESS_IDENTITY_MISMATCH,
        )
    expected_reasons = _dedupe(tuple(
        reason
        for item in value.items
        for reason in item.reasons
    ))
    expected_status = (
        SectorRuleReadinessStatus.MISSING
        if expected_reasons
        else SectorRuleReadinessStatus.READY
    )
    if value.status != expected_status or value.reasons != expected_reasons:
        return SECTOR_READINESS_UNVERIFIED, (
            SECTOR_READINESS_UNVERIFIED,
        )
    return None, value.reasons


def _research_batch_validation_reason(
    value: Any,
    *,
    plan: LeaderRuntimeCandidatePlan,
) -> Optional[str]:
    if value is None:
        return RESEARCH_BATCH_MISSING
    if (
        type(value) is not LeaderResearchReadinessAuditBatchResult
        or value.batch_contract_id
        != LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID
        or not isinstance(
            value.status,
            LeaderResearchReadinessAuditBatchStatus,
        )
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
        or not isinstance(value.items, tuple)
        or value.input_count != len(value.items)
        or value.input_count != plan.candidate_count
        or not _reasons_valid(value.reasons)
        or value.reasons
    ):
        return RESEARCH_BATCH_UNVERIFIED
    if _aware_utc(value.as_of) != plan.as_of:
        return RESEARCH_BATCH_IDENTITY_MISMATCH
    plan_symbols = tuple(item.symbol for item in plan.items)
    if (
        tuple(getattr(item, "symbol", None) for item in value.items)
        != plan_symbols
    ):
        return RESEARCH_BATCH_IDENTITY_MISMATCH

    for index, item in enumerate(value.items):
        if (
            type(item) is not LeaderResearchReadinessAuditBatchItem
            or item.index != index
            or item.symbol != plan_symbols[index]
            or item.input_symbol != plan_symbols[index]
            or not isinstance(
                item.status,
                LeaderResearchReadinessAuditStatus,
            )
            or not _reasons_valid(item.reasons)
            or any((
                item.formal_score_ready is not False,
                item.formal_gate_ready is not False,
                item.formal_usable is not False,
                item.state_transition_allowed is not False,
            ))
        ):
            return RESEARCH_BATCH_UNVERIFIED
        audit = item.audit
        if audit is None:
            if (
                item.status != LeaderResearchReadinessAuditStatus.BLOCKED
                or not item.reasons
            ):
                return RESEARCH_BATCH_UNVERIFIED
            continue
        if (
            not is_leader_research_readiness_audit_valid(
                audit,
                symbol=item.symbol,
                as_of=plan.as_of,
            )
            or item.status != audit.status
            or (
                item.status == LeaderResearchReadinessAuditStatus.BLOCKED
                and item.reasons != audit.ordered_blocker_codes
            )
            or (
                item.status != LeaderResearchReadinessAuditStatus.BLOCKED
                and item.reasons
            )
        ):
            return RESEARCH_BATCH_UNVERIFIED

    if all(
        item.status == LeaderResearchReadinessAuditStatus.READY
        and item.audit is not None
        for item in value.items
    ):
        expected_status = LeaderResearchReadinessAuditBatchStatus.READY
    elif any(
        item.status in (
            LeaderResearchReadinessAuditStatus.READY,
            LeaderResearchReadinessAuditStatus.PARTIAL,
        )
        and item.audit is not None
        for item in value.items
    ):
        expected_status = LeaderResearchReadinessAuditBatchStatus.PARTIAL
    else:
        expected_status = LeaderResearchReadinessAuditBatchStatus.BLOCKED
    if value.status != expected_status:
        return RESEARCH_BATCH_UNVERIFIED
    return None


def _audit_missing_reasons(
    item: LeaderResearchReadinessAuditItem,
) -> Tuple[str, ...]:
    if item.evidence_available:
        return ()
    return _dedupe((
        item.veto_reason,
        *item.reasons,
    ))


def _candidate_items(
    *,
    sector_reasons: Sequence[str],
    audit_batch_item: Optional[
        LeaderResearchReadinessAuditBatchItem
    ],
    research_batch_reason: Optional[str],
) -> Tuple[LeaderFormalResearchItem, ...]:
    values = [
        _item("candidate_discovery"),
        _item("sector_formal_gate", sector_reasons),
    ]
    audit = (
        audit_batch_item.audit
        if audit_batch_item is not None
        else None
    )
    if (
        audit is None
        or audit.status == LeaderResearchReadinessAuditStatus.BLOCKED
    ):
        reasons = (
            (research_batch_reason,)
            if research_batch_reason is not None
            else (
                audit_batch_item.reasons
                if audit_batch_item is not None
                else (RESEARCH_BATCH_MISSING,)
            )
        )
        values.extend(
            _item(key, reasons)
            for key in RESEARCH_ITEM_KEYS[2:]
        )
        return tuple(values)

    by_key = {item.key: item for item in audit.items}
    values.append(_item(
        "security_tradability",
        _audit_missing_reasons(by_key["security_tradability"]),
    ))
    cross_section_reasons = _dedupe((
        *_audit_missing_reasons(by_key["industry_strength"]),
        *_audit_missing_reasons(by_key["market_leadership"]),
    ))
    values.append(_item("cross_section", cross_section_reasons))
    values.append(_item(
        "liquidity",
        _audit_missing_reasons(by_key["liquidity"]),
    ))
    values.append(_item(
        "history_continuity",
        _audit_missing_reasons(by_key["history_continuity"]),
    ))
    values.append(_item(
        "business_catalyst",
        _audit_missing_reasons(by_key["business_catalyst"]),
    ))
    values.append(_item(
        "risk_evidence",
        _audit_missing_reasons(by_key["risk_projection"]),
    ))
    return tuple(values)


def _candidate(
    *,
    index: int,
    symbol: str,
    industry_code: str,
    sector_reasons: Sequence[str],
    audit_batch_item: Optional[
        LeaderResearchReadinessAuditBatchItem
    ],
    research_batch_reason: Optional[str],
) -> LeaderFormalResearchCandidate:
    items = _candidate_items(
        sector_reasons=sector_reasons,
        audit_batch_item=audit_batch_item,
        research_batch_reason=research_batch_reason,
    )
    first_missing_reason = next((
        item.reasons[0]
        for item in items
        if item.status == LeaderFormalResearchBatchStatus.MISSING
        and item.reasons
    ), None)
    audit = (
        audit_batch_item.audit
        if audit_batch_item is not None
        else None
    )
    return LeaderFormalResearchCandidate(
        index=index,
        symbol=symbol,
        industry_code=industry_code,
        status=(
            LeaderFormalResearchBatchStatus.MISSING
            if first_missing_reason is not None
            else LeaderFormalResearchBatchStatus.READY
        ),
        items=items,
        first_missing_reason=first_missing_reason,
        first_veto_reason=(
            audit.first_veto_reason
            if audit is not None
            else None
        ),
    )


def provide_leader_formal_research_batch(
    *,
    candidate_plan: Any,
    sector_rule_readiness: Any = None,
    research_readiness_batch: Any = None,
) -> LeaderFormalResearchBatchResult:
    """生成同轮研究输入证据，不触发来源或正式状态。"""

    try:
        if (
            not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
            or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        ):
            return _result(
                status=LeaderFormalResearchBatchStatus.MISSING,
                plan=None,
                reasons=(CANDIDATE_PLAN_UNVERIFIED,),
            )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _result(
            status=LeaderFormalResearchBatchStatus.MISSING,
            plan=None,
            reasons=(CANDIDATE_PLAN_UNVERIFIED,),
        )
    plan = candidate_plan

    sector_reason, sector_reasons = _sector_result_valid(
        sector_rule_readiness,
        plan=plan,
    )
    research_reason = _research_batch_validation_reason(
        research_readiness_batch,
        plan=plan,
    )
    valid_research_batch = (
        research_readiness_batch
        if research_reason is None
        else None
    )
    candidates = tuple(
        _candidate(
            index=index,
            symbol=plan_item.symbol,
            industry_code=plan_item.industry_code,
            sector_reasons=sector_reasons,
            audit_batch_item=(
                valid_research_batch.items[index]
                if valid_research_batch is not None
                else None
            ),
            research_batch_reason=research_reason,
        )
        for index, plan_item in enumerate(plan.items)
    )
    missing = any(
        item.status == LeaderFormalResearchBatchStatus.MISSING
        for item in candidates
    )
    reasons = _dedupe((
        sector_reason,
        *sector_reasons,
        research_reason,
        FORMAL_RESEARCH_BATCH_MISSING if missing else None,
    ))
    return _result(
        status=(
            LeaderFormalResearchBatchStatus.MISSING
            if missing
            else LeaderFormalResearchBatchStatus.READY
        ),
        plan=plan,
        reasons=reasons,
        items=candidates,
        sector_rule_version=(
            sector_rule_readiness.rule_version
            if (
                type(sector_rule_readiness)
                is SectorRuleReadinessResult
                and sector_reason is None
            )
            else None
        ),
    )
