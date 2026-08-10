"""阶段6L-F1候选研究证据完整度与首次否决审计。

本模块只消费已经计算完成的冻结研究结果，不重算特征、不连接外部系统，
也不把研究完整度转换为正式分数或龙头状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    LeaderBusinessCatalystFeatureResult,
)
from radar.leader_history_features import LeaderHistoryFeatureResult
from radar.leader_liquidity_features import LeaderLiquidityFeatureResult
from radar.leader_research_features import (
    LeaderResearchDimension,
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
)
from radar.leader_risk_candidate_projection import (
    LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
    LeaderRiskCandidateProjection,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
)
from radar.leader_risk_evidence_bundle import (
    FormalRiskGateGap,
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
)
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureResult,
    OnePriceLimitState,
    PriceLimitState,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)


LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID = (
    "radar-leader-research-readiness-audit-v1"
)
UTC = timezone.utc
REQUIRED_ITEM_KEYS = (
    "industry_strength",
    "security_tradability",
    "market_leadership",
    "liquidity",
    "history_continuity",
    "business_catalyst",
    "risk_projection",
)

CONTRACT_UNVERIFIED = "leader_research_audit_contract_unverified"
IDENTITY_MISMATCH = "leader_research_audit_identity_mismatch"
AS_OF_MISMATCH = "leader_research_audit_as_of_mismatch"
FORMAL_INDUSTRY_UNAVAILABLE = (
    "leader_formal_industry_gate_unavailable"
)
FORMAL_STOCK_UNAVAILABLE = "leader_formal_stock_gate_unavailable"
FORMAL_STOCK_FAILED = "leader_formal_stock_gate_failed"
CROSS_SECTION_UNAVAILABLE = (
    "leader_cross_section_evidence_unavailable"
)
LIQUIDITY_UNAVAILABLE = "leader_liquidity_evidence_unavailable"
HISTORY_UNAVAILABLE = "leader_history_evidence_unavailable"
HISTORY_RECOVERY_ABSENT = (
    "leader_history_recovery_evidence_absent"
)
BUSINESS_DISPROVED = "leader_business_exposure_disproved"
BUSINESS_UNCONFIRMED = "leader_business_exposure_unconfirmed"
BUSINESS_UNAVAILABLE = "leader_business_evidence_unavailable"
RISK_UNAVAILABLE = "leader_risk_evidence_unavailable"
FORMAL_RISK_UNAVAILABLE = "leader_formal_risk_gate_unavailable"
FORMAL_EVALUATION_DISABLED = "leader_formal_evaluation_disabled"


class LeaderResearchReadinessAuditStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderResearchReadinessAuditInput:
    symbol: str
    as_of: datetime
    cross_sectional_features: LeaderResearchFeatureResult = field(
        repr=False
    )
    history_features: LeaderHistoryFeatureResult = field(repr=False)
    liquidity_features: LeaderLiquidityFeatureResult = field(repr=False)
    business_catalyst_features: (
        LeaderBusinessCatalystFeatureResult
    ) = field(repr=False)
    tradability_features: LeaderTradabilityFeatureResult = field(
        repr=False
    )
    risk_projection_item: (
        LeaderRiskCandidateProjectionBatchItem
    ) = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchReadinessAuditItem:
    key: str
    status: ResearchFeatureStatus
    evidence_available: bool
    requirement_satisfied: bool
    veto_reason: Optional[str] = None
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    source_contract_ids: Tuple[str, ...] = field(
        default_factory=tuple
    )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "evidenceAvailable": self.evidence_available,
            "requirementSatisfied": self.requirement_satisfied,
            "vetoReason": self.veto_reason,
            "reasons": list(self.reasons),
            "sourceContractIds": list(self.source_contract_ids),
        }


@dataclass(frozen=True)
class LeaderResearchReadinessAuditResult:
    status: LeaderResearchReadinessAuditStatus
    symbol: Optional[str]
    as_of: Optional[datetime]
    items: Tuple[
        LeaderResearchReadinessAuditItem,
        ...,
    ] = field(default_factory=tuple)
    required_item_count: int = len(REQUIRED_ITEM_KEYS)
    evidence_available_count: int = 0
    satisfied_count: int = 0
    missing_items: Tuple[str, ...] = field(default_factory=tuple)
    blocked_items: Tuple[str, ...] = field(default_factory=tuple)
    first_veto_reason: Optional[str] = None
    first_research_blocker_reason: Optional[str] = None
    ordered_blocker_codes: Tuple[str, ...] = field(
        default_factory=tuple
    )
    contract_id: str = LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def item(self, key: str) -> LeaderResearchReadinessAuditItem:
        for value in self.items:
            if value.key == key:
                return value
        raise KeyError(key)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "auditStatus": self.status.value,
            "candidate": {
                "symbol": self.symbol,
                "asOf": (
                    self.as_of.isoformat()
                    if self.as_of is not None
                    else None
                ),
            },
            "items": [
                item.to_evidence()
                for item in self.items
            ],
            "completeness": {
                "requiredItemCount": self.required_item_count,
                "evidenceAvailableCount": (
                    self.evidence_available_count
                ),
                "satisfiedCount": self.satisfied_count,
                "missingItems": list(self.missing_items),
                "blockedItems": list(self.blocked_items),
            },
            "firstVetoReason": self.first_veto_reason,
            "firstResearchBlockerReason": (
                self.first_research_blocker_reason
            ),
            "orderedBlockerCodes": list(
                self.ordered_blocker_codes
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


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[Optional[str]]) -> Tuple[str, ...]:
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


def _blocked_result(
    reason: str,
    *,
    symbol: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> LeaderResearchReadinessAuditResult:
    return LeaderResearchReadinessAuditResult(
        status=LeaderResearchReadinessAuditStatus.BLOCKED,
        symbol=symbol,
        as_of=as_of,
        first_veto_reason=reason,
        ordered_blocker_codes=(reason,),
    )


def _result_contract_is_valid(
    value: Any,
    expected_type: type,
) -> bool:
    return (
        isinstance(value, expected_type)
        and isinstance(value.status, ResearchFeatureStatus)
        and _valid_strings(value.reasons)
    )


def _dimension_contract_is_valid(
    value: Any,
) -> bool:
    if (
        not isinstance(value, LeaderResearchDimension)
        or not isinstance(value.status, ResearchFeatureStatus)
        or not _valid_strings(value.reasons)
        or not _valid_strings(value.source_contract_ids)
    ):
        return False
    if value.status == ResearchFeatureStatus.READY:
        return (
            value.research_score is not None
            and bool(value.source_contract_ids)
        )
    return value.research_score is None


def _risk_contract_reason(
    value: LeaderResearchReadinessAuditInput,
    as_of: datetime,
) -> Optional[str]:
    item = value.risk_projection_item
    if (
        not isinstance(item, LeaderRiskCandidateProjectionBatchItem)
        or isinstance(item.index, bool)
        or not isinstance(item.index, int)
        or item.index < 0
        or not isinstance(item.status, ResearchFeatureStatus)
        or not _valid_strings(item.reasons)
        or not isinstance(item.symbol, str)
        or not item.symbol.strip()
        or (
            item.input_symbol is not None
            and (
                not isinstance(item.input_symbol, str)
                or not item.input_symbol.strip()
            )
        )
    ):
        return CONTRACT_UNVERIFIED
    if (
        item.symbol != value.symbol
        or (
            item.input_symbol is not None
            and item.input_symbol != value.symbol
        )
    ):
        return IDENTITY_MISMATCH
    if any((
        item.risk_filter_passed is not False,
        item.formal_gate_ready is not False,
        item.formal_usable is not False,
        item.applied_to_d3 is not False,
        item.applied_to_d1 is not False,
    )):
        return CONTRACT_UNVERIFIED
    if item.status != ResearchFeatureStatus.READY:
        return (
            CONTRACT_UNVERIFIED
            if item.projection is not None
            else None
        )
    if item.reasons:
        return CONTRACT_UNVERIFIED
    projection = item.projection
    if not isinstance(projection, LeaderRiskCandidateProjection):
        return CONTRACT_UNVERIFIED
    if projection.symbol != value.symbol:
        return IDENTITY_MISMATCH
    projection_as_of = _aware_utc(projection.as_of)
    if projection_as_of is None or projection_as_of != as_of:
        return AS_OF_MISMATCH
    if (
        projection.projection_contract_id
        != LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID
        or projection.audit_contract_id
        != RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
        or projection.bundle_contract_id
        != RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
        or not isinstance(projection.formal_gate_gaps, tuple)
        or any(
            not isinstance(gap, FormalRiskGateGap)
            for gap in projection.formal_gate_gaps
        )
    ):
        return CONTRACT_UNVERIFIED
    if any((
        projection.risk_filter_passed is not False,
        projection.formal_gate_ready is not False,
        projection.formal_usable is not False,
        projection.applied_to_d3 is not False,
        projection.applied_to_d1 is not False,
    )):
        return CONTRACT_UNVERIFIED
    return None


def _dimension_item(
    key: str,
    dimension: LeaderResearchDimension,
) -> LeaderResearchReadinessAuditItem:
    available = dimension.status == ResearchFeatureStatus.READY
    return LeaderResearchReadinessAuditItem(
        key=key,
        status=dimension.status,
        evidence_available=available,
        requirement_satisfied=available,
        veto_reason=(
            None
            if available
            else CROSS_SECTION_UNAVAILABLE
        ),
        reasons=dimension.reasons,
        source_contract_ids=dimension.source_contract_ids,
    )


def _tradability_item(
    value: LeaderTradabilityFeatureResult,
) -> LeaderResearchReadinessAuditItem:
    available = value.status == ResearchFeatureStatus.READY
    if not available:
        veto_reason = FORMAL_STOCK_UNAVAILABLE
        satisfied = False
    elif value.research_eligible is not True or value.preliminary_only:
        veto_reason = FORMAL_STOCK_FAILED
        satisfied = False
    else:
        veto_reason = None
        satisfied = True
    return LeaderResearchReadinessAuditItem(
        key="security_tradability",
        status=value.status,
        evidence_available=available,
        requirement_satisfied=satisfied,
        veto_reason=veto_reason,
        reasons=value.reasons,
    )


def _simple_result_item(
    *,
    key: str,
    status: ResearchFeatureStatus,
    reasons: Tuple[str, ...],
    source_contract_ids: Tuple[str, ...],
    unavailable_reason: str,
) -> LeaderResearchReadinessAuditItem:
    available = status == ResearchFeatureStatus.READY
    return LeaderResearchReadinessAuditItem(
        key=key,
        status=status,
        evidence_available=available,
        requirement_satisfied=available,
        veto_reason=None if available else unavailable_reason,
        reasons=reasons,
        source_contract_ids=source_contract_ids,
    )


def _history_item(
    value: LeaderHistoryFeatureResult,
) -> LeaderResearchReadinessAuditItem:
    if value.status != ResearchFeatureStatus.READY:
        return _simple_result_item(
            key="history_continuity",
            status=value.status,
            reasons=value.reasons,
            source_contract_ids=value.source_contract_ids,
            unavailable_reason=HISTORY_UNAVAILABLE,
        )
    recovery_absent = "recovery_event_absent" in value.reasons
    return LeaderResearchReadinessAuditItem(
        key="history_continuity",
        status=value.status,
        evidence_available=True,
        requirement_satisfied=not recovery_absent,
        veto_reason=(
            HISTORY_RECOVERY_ABSENT
            if recovery_absent
            else None
        ),
        reasons=value.reasons,
        source_contract_ids=value.source_contract_ids,
    )


def _business_item(
    value: LeaderBusinessCatalystFeatureResult,
) -> LeaderResearchReadinessAuditItem:
    available = value.status == ResearchFeatureStatus.READY
    if value.relation == BusinessCatalystRelation.DISPROVED:
        veto_reason = BUSINESS_DISPROVED
        satisfied = False
    elif value.relation == BusinessCatalystRelation.UNCONFIRMED:
        veto_reason = BUSINESS_UNCONFIRMED
        satisfied = False
    elif not available:
        veto_reason = BUSINESS_UNAVAILABLE
        satisfied = False
    else:
        veto_reason = None
        satisfied = True
    return LeaderResearchReadinessAuditItem(
        key="business_catalyst",
        status=value.status,
        evidence_available=available,
        requirement_satisfied=satisfied,
        veto_reason=veto_reason,
        reasons=value.reasons,
    )


def _risk_item(
    value: LeaderRiskCandidateProjectionBatchItem,
) -> LeaderResearchReadinessAuditItem:
    if value.status != ResearchFeatureStatus.READY:
        return LeaderResearchReadinessAuditItem(
            key="risk_projection",
            status=value.status,
            evidence_available=False,
            requirement_satisfied=False,
            veto_reason=RISK_UNAVAILABLE,
            reasons=value.reasons,
        )
    projection = value.projection
    gap_reasons = tuple(
        f"formal_gate_gap:{gap.value}"
        for gap in projection.formal_gate_gaps
    )
    return LeaderResearchReadinessAuditItem(
        key="risk_projection",
        status=value.status,
        evidence_available=True,
        requirement_satisfied=False,
        veto_reason=FORMAL_RISK_UNAVAILABLE,
        reasons=gap_reasons,
        source_contract_ids=(
            projection.document_source_contract_id,
            projection.audit_contract_id,
            projection.bundle_contract_id,
        ),
    )


def build_leader_research_readiness_audit(
    input_value: Any,
) -> LeaderResearchReadinessAuditResult:
    """按固定业务顺序审计研究证据，不生成正式候选资格。"""

    if not isinstance(input_value, LeaderResearchReadinessAuditInput):
        return _blocked_result(CONTRACT_UNVERIFIED)
    if (
        not isinstance(input_value.symbol, str)
        or not input_value.symbol.strip()
    ):
        return _blocked_result(CONTRACT_UNVERIFIED)
    symbol = input_value.symbol.strip()
    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _blocked_result(
            AS_OF_MISMATCH,
            symbol=symbol,
        )
    if not isinstance(
        input_value.cross_sectional_features,
        LeaderResearchFeatureResult,
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    dimensions = input_value.cross_sectional_features.dimensions
    if (
        not isinstance(dimensions, tuple)
        or any(
            not _dimension_contract_is_valid(dimension)
            for dimension in dimensions
        )
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    dimension_by_name = {
        dimension.field_name: dimension
        for dimension in dimensions
    }
    if (
        len(dimension_by_name) != len(dimensions)
        or "industry_strength" not in dimension_by_name
        or "market_leadership" not in dimension_by_name
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    result_contracts = (
        (
            input_value.history_features,
            LeaderHistoryFeatureResult,
        ),
        (
            input_value.liquidity_features,
            LeaderLiquidityFeatureResult,
        ),
        (
            input_value.business_catalyst_features,
            LeaderBusinessCatalystFeatureResult,
        ),
        (
            input_value.tradability_features,
            LeaderTradabilityFeatureResult,
        ),
    )
    if any(
        not _result_contract_is_valid(value, expected_type)
        for value, expected_type in result_contracts
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    if not isinstance(
        input_value.business_catalyst_features.relation,
        BusinessCatalystRelation,
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    business = input_value.business_catalyst_features
    if (
        business.status == ResearchFeatureStatus.READY
        and business.relation == BusinessCatalystRelation.UNCONFIRMED
    ) or (
        business.status != ResearchFeatureStatus.READY
        and business.relation != BusinessCatalystRelation.UNCONFIRMED
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    for source_result in (
        input_value.history_features,
        input_value.liquidity_features,
    ):
        if not _valid_strings(source_result.source_contract_ids):
            return _blocked_result(
                CONTRACT_UNVERIFIED,
                symbol=symbol,
                as_of=as_of,
            )
        if (
            source_result.status == ResearchFeatureStatus.READY
            and not source_result.source_contract_ids
        ):
            return _blocked_result(
                CONTRACT_UNVERIFIED,
                symbol=symbol,
                as_of=as_of,
            )
    tradability = input_value.tradability_features
    if (
        not isinstance(
            tradability.lifecycle_status,
            SecurityLifecycleStatus,
        )
        or not isinstance(
            tradability.trading_status,
            TradingSessionStatus,
        )
        or not isinstance(
            tradability.price_limit_state,
            PriceLimitState,
        )
        or not isinstance(
            tradability.one_price_limit_state,
            OnePriceLimitState,
        )
        or (
            tradability.status == ResearchFeatureStatus.READY
            and (
                not isinstance(tradability.research_eligible, bool)
                or not isinstance(tradability.preliminary_only, bool)
            )
        )
        or (
            tradability.status != ResearchFeatureStatus.READY
            and (
                tradability.research_eligible is not None
                or tradability.preliminary_only is not None
            )
        )
    ):
        return _blocked_result(
            CONTRACT_UNVERIFIED,
            symbol=symbol,
            as_of=as_of,
        )
    risk_contract_reason = _risk_contract_reason(input_value, as_of)
    if risk_contract_reason is not None:
        return _blocked_result(
            risk_contract_reason,
            symbol=symbol,
            as_of=as_of,
        )

    items = (
        _dimension_item(
            "industry_strength",
            dimension_by_name["industry_strength"],
        ),
        _tradability_item(tradability),
        _dimension_item(
            "market_leadership",
            dimension_by_name["market_leadership"],
        ),
        _simple_result_item(
            key="liquidity",
            status=input_value.liquidity_features.status,
            reasons=input_value.liquidity_features.reasons,
            source_contract_ids=(
                input_value.liquidity_features.source_contract_ids
            ),
            unavailable_reason=LIQUIDITY_UNAVAILABLE,
        ),
        _history_item(input_value.history_features),
        _business_item(input_value.business_catalyst_features),
        _risk_item(input_value.risk_projection_item),
    )
    evidence_available_count = sum(
        item.evidence_available
        for item in items
    )
    satisfied_count = sum(
        item.requirement_satisfied
        for item in items
    )
    missing_items = tuple(
        item.key
        for item in items
        if not item.evidence_available
    )
    blocked_items = tuple(
        item.key
        for item in items
        if item.evidence_available
        and not item.requirement_satisfied
    )
    research_blockers = tuple(
        item.veto_reason
        for item in items
        if item.veto_reason is not None
    )
    stock_blocker = (
        FORMAL_STOCK_FAILED
        if items[1].evidence_available
        and not items[1].requirement_satisfied
        else FORMAL_STOCK_UNAVAILABLE
    )
    ordered_blocker_codes = _dedupe((
        FORMAL_INDUSTRY_UNAVAILABLE,
        stock_blocker,
        *research_blockers,
        FORMAL_EVALUATION_DISABLED,
    ))
    return LeaderResearchReadinessAuditResult(
        status=(
            LeaderResearchReadinessAuditStatus.READY
            if evidence_available_count == len(REQUIRED_ITEM_KEYS)
            else LeaderResearchReadinessAuditStatus.PARTIAL
        ),
        symbol=symbol,
        as_of=as_of,
        items=items,
        evidence_available_count=evidence_available_count,
        satisfied_count=satisfied_count,
        missing_items=missing_items,
        blocked_items=blocked_items,
        first_veto_reason=ordered_blocker_codes[0],
        first_research_blocker_reason=(
            research_blockers[0]
            if research_blockers
            else None
        ),
        ordered_blocker_codes=ordered_blocker_codes,
    )
