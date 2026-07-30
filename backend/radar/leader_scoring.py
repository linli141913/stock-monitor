"""阶段6 龙头评分输入审计。

本模块只把已验证的维度输入转换为状态机证据，不抓取数据、不落库。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Iterable, Optional, Tuple

from radar.leader_state_machine import (
    BusinessExposureStatus,
    DEFAULT_LEADER_STATE_MACHINE_POLICY,
    LeaderDataStatus,
    LeaderEvidenceSnapshot,
    LeaderState,
    LeaderStateMachinePolicy,
)


class LeaderMetricStatus(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class LeaderDimensionInput:
    field_name: str
    score: Optional[float]
    status: LeaderMetricStatus = LeaderMetricStatus.VERIFIED
    reasons: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.field_name.strip():
            raise ValueError("龙头评分维度名不能为空")
        if self.score is not None and not 0 <= self.score <= 100:
            raise ValueError("单项维度分必须在0到100之间")
        if self.status == LeaderMetricStatus.VERIFIED and self.score is None:
            raise ValueError("已验证维度必须提供分数，真实0应保存为0")
        if self.status != LeaderMetricStatus.VERIFIED and self.score is not None:
            raise ValueError("未验证、缺失、过期或失败维度不得携带可用分数")


@dataclass(frozen=True)
class LeaderGateInput:
    industry_gate_passed: bool
    stock_gate_passed: bool
    market_leadership_passed: bool
    industry_contribution_passed: bool
    liquidity_passed: bool
    tradability_passed: bool
    continuity_passed: bool
    recovery_passed: bool
    risk_filter_passed: bool
    business_exposure_status: BusinessExposureStatus


@dataclass(frozen=True)
class LeaderScoringInput:
    symbol: str
    as_of: datetime
    dimensions: Tuple[LeaderDimensionInput, ...]
    gates: LeaderGateInput
    consecutive_signal_periods: int = 1

    def __post_init__(self):
        if not self.symbol.strip():
            raise ValueError("证券代码不能为空")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of必须包含时区")
        if self.consecutive_signal_periods < 0:
            raise ValueError("连续满足周期不能为负数")


@dataclass(frozen=True)
class LeaderScoringAudit:
    symbol: str
    as_of: datetime
    score: float
    data_status: LeaderDataStatus
    formal_state_enabled: bool
    rule_version: str
    reasons: Tuple[str, ...]
    first_rejection_reason: Optional[str]
    snapshot: LeaderEvidenceSnapshot


def _dedupe(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _data_status_from_metric_statuses(
    statuses: Iterable[LeaderMetricStatus],
) -> LeaderDataStatus:
    status_set = set(statuses)
    if LeaderMetricStatus.SOURCE_FAILED in status_set:
        return LeaderDataStatus.SOURCE_FAILED
    if LeaderMetricStatus.STALE in status_set:
        return LeaderDataStatus.STALE
    if status_set.intersection({
        LeaderMetricStatus.MISSING,
        LeaderMetricStatus.SOURCE_UNVERIFIED,
        LeaderMetricStatus.NOT_APPLICABLE,
    }):
        return LeaderDataStatus.MISSING
    return LeaderDataStatus.HEALTHY


def _first_rejection_reason(
    *,
    data_status: LeaderDataStatus,
    gates: LeaderGateInput,
    score: float,
    reasons: Tuple[str, ...],
    policy: LeaderStateMachinePolicy,
) -> Optional[str]:
    if data_status != LeaderDataStatus.HEALTHY:
        return f"data_status_{data_status.value}"
    ordered_gate_reasons = (
        (not gates.industry_gate_passed, "industry_gate_failed"),
        (not gates.stock_gate_passed, "stock_gate_failed"),
        (not gates.risk_filter_passed, "risk_filter_failed"),
        (
            gates.business_exposure_status == BusinessExposureStatus.DISPROVED,
            "business_exposure_disproved",
        ),
        (
            gates.business_exposure_status == BusinessExposureStatus.UNCONFIRMED,
            "business_exposure_unconfirmed",
        ),
        (
            gates.business_exposure_status == BusinessExposureStatus.MISSING,
            "business_exposure_missing",
        ),
        (not gates.market_leadership_passed, "market_leadership_missing"),
        (
            not gates.industry_contribution_passed,
            "industry_contribution_missing",
        ),
        (not gates.liquidity_passed, "liquidity_missing"),
        (not gates.tradability_passed, "tradability_missing"),
        (not gates.continuity_passed, "continuity_missing"),
        (not gates.recovery_passed, "recovery_missing"),
    )
    for failed, reason in ordered_gate_reasons:
        if failed:
            return reason
    preliminary_rule = policy.rules[LeaderState.PRELIMINARY]
    if score < preliminary_rule.enter_score:
        return f"score_below_{int(preliminary_rule.enter_score)}"
    return reasons[0] if reasons else None


def build_leader_scoring_audit(
    scoring_input: LeaderScoringInput,
    *,
    policy: LeaderStateMachinePolicy = DEFAULT_LEADER_STATE_MACHINE_POLICY,
) -> LeaderScoringAudit:
    expected_weights = dict(policy.score_weights)
    expected_fields = tuple(expected_weights)
    dimensions_by_field: Dict[str, LeaderDimensionInput] = {}
    reasons = []
    score = 0.0
    for dimension in scoring_input.dimensions:
        if dimension.field_name in dimensions_by_field:
            raise ValueError(f"龙头评分维度重复: {dimension.field_name}")
        if dimension.field_name not in expected_weights:
            raise ValueError(f"未知龙头评分维度: {dimension.field_name}")
        dimensions_by_field[dimension.field_name] = dimension

    statuses = []
    for field_name in expected_fields:
        dimension = dimensions_by_field.get(field_name)
        if dimension is None:
            statuses.append(LeaderMetricStatus.MISSING)
            reasons.append(f"dimension_missing:{field_name}")
            continue
        statuses.append(dimension.status)
        if dimension.status == LeaderMetricStatus.VERIFIED:
            max_score = expected_weights[field_name]
            if dimension.score is None:
                raise ValueError("已验证维度必须提供分数")
            if dimension.score > max_score:
                raise ValueError(
                    f"维度{field_name}分数不能超过权重上限{max_score}"
                )
            score += dimension.score
            continue
        reasons.append(f"dimension_{dimension.status.value}:{field_name}")
        reasons.extend(dimension.reasons)

    data_status = _data_status_from_metric_statuses(statuses)
    reasons = _dedupe(reasons)
    first_rejection = _first_rejection_reason(
        data_status=data_status,
        gates=scoring_input.gates,
        score=score,
        reasons=reasons,
        policy=policy,
    )
    snapshot = LeaderEvidenceSnapshot(
        symbol=scoring_input.symbol,
        as_of=scoring_input.as_of,
        score=score,
        data_status=data_status,
        industry_gate_passed=scoring_input.gates.industry_gate_passed,
        stock_gate_passed=scoring_input.gates.stock_gate_passed,
        market_leadership_passed=scoring_input.gates.market_leadership_passed,
        industry_contribution_passed=(
            scoring_input.gates.industry_contribution_passed
        ),
        liquidity_passed=scoring_input.gates.liquidity_passed,
        tradability_passed=scoring_input.gates.tradability_passed,
        continuity_passed=scoring_input.gates.continuity_passed,
        recovery_passed=scoring_input.gates.recovery_passed,
        risk_filter_passed=scoring_input.gates.risk_filter_passed,
        business_exposure_status=scoring_input.gates.business_exposure_status,
        consecutive_signal_periods=scoring_input.consecutive_signal_periods,
        first_rejection_hint=first_rejection,
    )
    return LeaderScoringAudit(
        symbol=scoring_input.symbol,
        as_of=scoring_input.as_of,
        score=score,
        data_status=data_status,
        formal_state_enabled=policy.formal_state_enabled,
        rule_version=policy.version,
        reasons=reasons,
        first_rejection_reason=first_rejection,
        snapshot=snapshot,
    )
