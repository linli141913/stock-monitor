"""阶段6 龙头状态机合同与纯迁移函数。

本模块只计算研究性状态迁移，不接入生产数据库、API、调度或提醒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Iterable, Optional, Tuple


LEADER_STATE_MACHINE_VERSION = "radar-leader-state-machine-v1"


class LeaderState(str, Enum):
    OUT = "out"
    PRELIMINARY = "preliminary"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"


class LeaderTransitionAction(str, Enum):
    BLOCKED = "blocked"
    HOLD = "hold"
    ENTER = "enter"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    REMOVE = "remove"


class LeaderDataStatus(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"


class BusinessExposureStatus(str, Enum):
    VERIFIED = "verified"
    UNCONFIRMED = "unconfirmed"
    MISSING = "missing"
    DISPROVED = "disproved"


STATE_RANK: Dict[LeaderState, int] = {
    LeaderState.OUT: 0,
    LeaderState.PRELIMINARY: 1,
    LeaderState.CANDIDATE: 2,
    LeaderState.CONFIRMED: 3,
}


@dataclass(frozen=True)
class LeaderStateRule:
    enter_score: float
    maintain_score: float
    required_consecutive_periods: int
    minimum_hold_periods: int

    def __post_init__(self):
        if not 0 <= self.maintain_score <= self.enter_score <= 100:
            raise ValueError("龙头状态分数阈值必须满足0<=保持分<=进入分<=100")
        if self.required_consecutive_periods < 1:
            raise ValueError("连续满足次数必须至少为1")
        if self.minimum_hold_periods < 1:
            raise ValueError("最短保持周期必须至少为1")


def _default_state_rules() -> Dict[LeaderState, LeaderStateRule]:
    return {
        LeaderState.PRELIMINARY: LeaderStateRule(
            enter_score=60,
            maintain_score=50,
            required_consecutive_periods=1,
            minimum_hold_periods=1,
        ),
        LeaderState.CANDIDATE: LeaderStateRule(
            enter_score=75,
            maintain_score=65,
            required_consecutive_periods=2,
            minimum_hold_periods=2,
        ),
        LeaderState.CONFIRMED: LeaderStateRule(
            enter_score=88,
            maintain_score=78,
            required_consecutive_periods=2,
            minimum_hold_periods=3,
        ),
    }


@dataclass(frozen=True)
class LeaderStateMachinePolicy:
    version: str = LEADER_STATE_MACHINE_VERSION
    formal_state_enabled: bool = False
    score_weights: Tuple[Tuple[str, float], ...] = (
        ("industry_strength", 25),
        ("market_leadership", 25),
        ("relative_strength_continuity", 20),
        ("liquidity_tradability", 15),
        ("business_exposure", 10),
        ("auxiliary", 5),
    )
    rules: Dict[LeaderState, LeaderStateRule] = field(
        default_factory=_default_state_rules
    )
    allowed_edges: Tuple[Tuple[LeaderState, LeaderState], ...] = (
        (LeaderState.OUT, LeaderState.PRELIMINARY),
        (LeaderState.PRELIMINARY, LeaderState.CANDIDATE),
        (LeaderState.CANDIDATE, LeaderState.CONFIRMED),
        (LeaderState.CONFIRMED, LeaderState.CANDIDATE),
        (LeaderState.CANDIDATE, LeaderState.PRELIMINARY),
        (LeaderState.PRELIMINARY, LeaderState.OUT),
        (LeaderState.CANDIDATE, LeaderState.OUT),
        (LeaderState.CONFIRMED, LeaderState.OUT),
    )
    cooldown_minutes_after_removal: int = 180

    def __post_init__(self):
        if not self.version.strip():
            raise ValueError("龙头状态机规则版本不能为空")
        rules = dict(self.rules)
        object.__setattr__(self, "rules", rules)
        if set(rules) != {
            LeaderState.PRELIMINARY,
            LeaderState.CANDIDATE,
            LeaderState.CONFIRMED,
        }:
            raise ValueError("龙头状态机必须定义预备、候选和已确认三类状态")
        if self.cooldown_minutes_after_removal < 0:
            raise ValueError("移出后冷却期不能为负数")
        fields = [field_name for field_name, _ in self.score_weights]
        if len(fields) != len(set(fields)):
            raise ValueError("龙头评分权重字段不得重复")
        if abs(sum(weight for _, weight in self.score_weights) - 100) > 1e-9:
            raise ValueError("龙头评分权重合计必须等于100")
        auxiliary = dict(self.score_weights).get("auxiliary", 0)
        if auxiliary > 5:
            raise ValueError("辅助指标权重不得超过5")
        if len(self.allowed_edges) != len(set(self.allowed_edges)):
            raise ValueError("状态迁移边不得重复")
        for source, target in self.allowed_edges:
            if source not in STATE_RANK or target not in STATE_RANK:
                raise ValueError("状态迁移边包含未知状态")


@dataclass(frozen=True)
class LeaderEvidenceSnapshot:
    symbol: str
    as_of: datetime
    score: float
    data_status: LeaderDataStatus = LeaderDataStatus.HEALTHY
    industry_gate_passed: bool = False
    stock_gate_passed: bool = False
    market_leadership_passed: bool = False
    industry_contribution_passed: bool = False
    liquidity_passed: bool = False
    tradability_passed: bool = False
    continuity_passed: bool = False
    recovery_passed: bool = False
    risk_filter_passed: bool = True
    business_exposure_status: BusinessExposureStatus = (
        BusinessExposureStatus.MISSING
    )
    consecutive_signal_periods: int = 1
    first_rejection_hint: Optional[str] = None

    def __post_init__(self):
        if not self.symbol.strip():
            raise ValueError("证券代码不能为空")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of必须包含时区")
        if not 0 <= self.score <= 100:
            raise ValueError("龙头评分必须在0到100之间")
        if self.consecutive_signal_periods < 0:
            raise ValueError("连续满足周期不能为负数")


@dataclass(frozen=True)
class LeaderStateRecord:
    symbol: str
    state: LeaderState
    state_age_periods: int
    rule_version: str
    state_since: datetime
    last_evaluated_at: datetime
    cooldown_until: Optional[datetime] = None

    def __post_init__(self):
        if not self.symbol.strip():
            raise ValueError("证券代码不能为空")
        if self.state_age_periods < 0:
            raise ValueError("状态保持周期不能为负数")
        for field_name, value in (
            ("state_since", self.state_since),
            ("last_evaluated_at", self.last_evaluated_at),
            ("cooldown_until", self.cooldown_until),
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name}必须包含时区")


@dataclass(frozen=True)
class LeaderTransitionDecision:
    symbol: str
    from_state: LeaderState
    to_state: LeaderState
    action: LeaderTransitionAction
    rule_version: str
    formal_state_enabled: bool
    reasons: Tuple[str, ...]
    first_rejection_reason: Optional[str]
    state_age_periods: int
    cooldown_until: Optional[datetime] = None


DEFAULT_LEADER_STATE_MACHINE_POLICY = LeaderStateMachinePolicy()


def _dedupe(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _edge_allowed(
    source: LeaderState,
    target: LeaderState,
    policy: LeaderStateMachinePolicy,
) -> bool:
    return source == target or (source, target) in policy.allowed_edges


def _target_reasons(
    snapshot: LeaderEvidenceSnapshot,
    target: LeaderState,
    policy: LeaderStateMachinePolicy,
) -> Tuple[str, ...]:
    if target == LeaderState.OUT:
        return ()
    rule = policy.rules[target]
    reasons = []
    if snapshot.data_status != LeaderDataStatus.HEALTHY:
        reasons.append(f"data_status_{snapshot.data_status.value}")
    if not snapshot.industry_gate_passed:
        reasons.append("industry_gate_failed")
    if not snapshot.stock_gate_passed:
        reasons.append("stock_gate_failed")
    if not snapshot.risk_filter_passed:
        reasons.append("risk_filter_failed")
    if not snapshot.market_leadership_passed:
        reasons.append("market_leadership_missing")
    if not snapshot.liquidity_passed:
        reasons.append("liquidity_missing")
    if not snapshot.tradability_passed:
        reasons.append("tradability_missing")
    if snapshot.score < rule.enter_score:
        reasons.append(f"score_below_{int(rule.enter_score)}")
    if snapshot.consecutive_signal_periods < rule.required_consecutive_periods:
        reasons.append(
            f"consecutive_periods_below_{rule.required_consecutive_periods}"
        )
    if snapshot.business_exposure_status == BusinessExposureStatus.DISPROVED:
        reasons.append("business_exposure_disproved")
    if target in {LeaderState.CANDIDATE, LeaderState.CONFIRMED}:
        if snapshot.business_exposure_status != BusinessExposureStatus.VERIFIED:
            reasons.append(
                f"business_exposure_{snapshot.business_exposure_status.value}"
            )
        if not snapshot.industry_contribution_passed:
            reasons.append("industry_contribution_missing")
        if not snapshot.continuity_passed:
            reasons.append("continuity_missing")
    if target == LeaderState.CONFIRMED and not snapshot.recovery_passed:
        reasons.append("recovery_missing")
    return _dedupe(reasons)


def _highest_entry_state(
    snapshot: LeaderEvidenceSnapshot,
    policy: LeaderStateMachinePolicy,
) -> Tuple[LeaderState, Tuple[str, ...]]:
    checked = (
        LeaderState.CONFIRMED,
        LeaderState.CANDIDATE,
        LeaderState.PRELIMINARY,
    )
    first_reasons = ()
    for state in checked:
        reasons = _target_reasons(snapshot, state, policy)
        if not reasons:
            return state, ()
        if not first_reasons:
            first_reasons = reasons
    return LeaderState.OUT, first_reasons


def _can_maintain_current(
    snapshot: LeaderEvidenceSnapshot,
    current_state: LeaderState,
    policy: LeaderStateMachinePolicy,
) -> Tuple[bool, Tuple[str, ...]]:
    if current_state == LeaderState.OUT:
        return False, ()
    rule = policy.rules[current_state]
    reasons = list(_target_reasons(snapshot, current_state, policy))
    reasons = [
        reason
        for reason in reasons
        if not reason.startswith("score_below_")
        and not reason.startswith("consecutive_periods_below_")
    ]
    if snapshot.score < rule.maintain_score:
        reasons.append(f"score_below_maintain_{int(rule.maintain_score)}")
    return not reasons, _dedupe(reasons)


def decide_leader_transition(
    snapshot: LeaderEvidenceSnapshot,
    previous: Optional[LeaderStateRecord] = None,
    *,
    policy: LeaderStateMachinePolicy = DEFAULT_LEADER_STATE_MACHINE_POLICY,
) -> LeaderTransitionDecision:
    previous_state = previous.state if previous else LeaderState.OUT
    previous_age = previous.state_age_periods if previous else 0
    if previous and previous.symbol != snapshot.symbol:
        raise ValueError("上一状态证券代码与本轮证据不一致")

    if snapshot.data_status != LeaderDataStatus.HEALTHY:
        reasons = (f"data_status_{snapshot.data_status.value}",)
        return LeaderTransitionDecision(
            symbol=snapshot.symbol,
            from_state=previous_state,
            to_state=previous_state,
            action=(
                LeaderTransitionAction.HOLD
                if previous_state != LeaderState.OUT
                else LeaderTransitionAction.BLOCKED
            ),
            rule_version=policy.version,
            formal_state_enabled=policy.formal_state_enabled,
            reasons=reasons,
            first_rejection_reason=reasons[0],
            state_age_periods=(
                previous_age + (1 if previous_state != LeaderState.OUT else 0)
            ),
            cooldown_until=previous.cooldown_until if previous else None,
        )

    if (
        previous
        and previous_state == LeaderState.OUT
        and previous.cooldown_until is not None
        and snapshot.as_of < previous.cooldown_until
    ):
        return LeaderTransitionDecision(
            symbol=snapshot.symbol,
            from_state=previous_state,
            to_state=LeaderState.OUT,
            action=LeaderTransitionAction.BLOCKED,
            rule_version=policy.version,
            formal_state_enabled=policy.formal_state_enabled,
            reasons=("cooldown_active",),
            first_rejection_reason="cooldown_active",
            state_age_periods=0,
            cooldown_until=previous.cooldown_until,
        )

    target_state, target_reasons = _highest_entry_state(snapshot, policy)
    if not _edge_allowed(previous_state, target_state, policy):
        target_state = _step_toward(previous_state, target_state)

    previous_rank = STATE_RANK[previous_state]
    target_rank = STATE_RANK[target_state]

    if target_rank > previous_rank:
        action = (
            LeaderTransitionAction.ENTER
            if previous_state == LeaderState.OUT
            else LeaderTransitionAction.UPGRADE
        )
        return LeaderTransitionDecision(
            symbol=snapshot.symbol,
            from_state=previous_state,
            to_state=target_state,
            action=action,
            rule_version=policy.version,
            formal_state_enabled=policy.formal_state_enabled,
            reasons=(f"{action.value}_{target_state.value}",),
            first_rejection_reason=None,
            state_age_periods=1,
        )

    if target_rank == previous_rank and target_state != LeaderState.OUT:
        return LeaderTransitionDecision(
            symbol=snapshot.symbol,
            from_state=previous_state,
            to_state=target_state,
            action=LeaderTransitionAction.HOLD,
            rule_version=policy.version,
            formal_state_enabled=policy.formal_state_enabled,
            reasons=("state_maintained",),
            first_rejection_reason=None,
            state_age_periods=previous_age + 1,
            cooldown_until=previous.cooldown_until if previous else None,
        )

    if previous_state != LeaderState.OUT:
        can_maintain, maintain_reasons = _can_maintain_current(
            snapshot,
            previous_state,
            policy,
        )
        if can_maintain:
            return LeaderTransitionDecision(
                symbol=snapshot.symbol,
                from_state=previous_state,
                to_state=previous_state,
                action=LeaderTransitionAction.HOLD,
                rule_version=policy.version,
                formal_state_enabled=policy.formal_state_enabled,
                reasons=("hysteresis_hold",),
                first_rejection_reason=None,
                state_age_periods=previous_age + 1,
                cooldown_until=previous.cooldown_until if previous else None,
            )
        current_rule = policy.rules[previous_state]
        severe_reasons = {
            "business_exposure_disproved",
            "risk_filter_failed",
            "industry_gate_failed",
            "stock_gate_failed",
        }
        if (
            previous_age < current_rule.minimum_hold_periods
            and not severe_reasons.intersection(maintain_reasons)
        ):
            return LeaderTransitionDecision(
                symbol=snapshot.symbol,
                from_state=previous_state,
                to_state=previous_state,
                action=LeaderTransitionAction.HOLD,
                rule_version=policy.version,
                formal_state_enabled=policy.formal_state_enabled,
                reasons=("minimum_hold_active", *maintain_reasons),
                first_rejection_reason=(
                    maintain_reasons[0] if maintain_reasons else None
                ),
                state_age_periods=previous_age + 1,
                cooldown_until=previous.cooldown_until if previous else None,
            )
        if target_state == LeaderState.OUT:
            cooldown_until = (
                snapshot.as_of
                + timedelta(minutes=policy.cooldown_minutes_after_removal)
                if policy.cooldown_minutes_after_removal
                else None
            )
            reasons = target_reasons or maintain_reasons or ("state_removed",)
            return LeaderTransitionDecision(
                symbol=snapshot.symbol,
                from_state=previous_state,
                to_state=LeaderState.OUT,
                action=LeaderTransitionAction.REMOVE,
                rule_version=policy.version,
                formal_state_enabled=policy.formal_state_enabled,
                reasons=reasons,
                first_rejection_reason=reasons[0] if reasons else None,
                state_age_periods=0,
                cooldown_until=cooldown_until,
            )
        reasons = target_reasons or maintain_reasons or (
            f"downgrade_to_{target_state.value}",
        )
        return LeaderTransitionDecision(
            symbol=snapshot.symbol,
            from_state=previous_state,
            to_state=target_state,
            action=LeaderTransitionAction.DOWNGRADE,
            rule_version=policy.version,
            formal_state_enabled=policy.formal_state_enabled,
            reasons=reasons,
            first_rejection_reason=reasons[0],
            state_age_periods=1,
            cooldown_until=previous.cooldown_until if previous else None,
        )

    reasons = target_reasons or ("no_leader_state",)
    first_rejection = snapshot.first_rejection_hint or reasons[0]
    return LeaderTransitionDecision(
        symbol=snapshot.symbol,
        from_state=LeaderState.OUT,
        to_state=LeaderState.OUT,
        action=LeaderTransitionAction.BLOCKED,
        rule_version=policy.version,
        formal_state_enabled=policy.formal_state_enabled,
        reasons=reasons,
        first_rejection_reason=first_rejection,
        state_age_periods=0,
    )


def _step_toward(source: LeaderState, target: LeaderState) -> LeaderState:
    source_rank = STATE_RANK[source]
    target_rank = STATE_RANK[target]
    if source_rank == target_rank:
        return source
    if target_rank > source_rank:
        return {
            LeaderState.OUT: LeaderState.PRELIMINARY,
            LeaderState.PRELIMINARY: LeaderState.CANDIDATE,
            LeaderState.CANDIDATE: LeaderState.CONFIRMED,
        }[source]
    return {
        LeaderState.CONFIRMED: LeaderState.CANDIDATE,
        LeaderState.CANDIDATE: LeaderState.PRELIMINARY,
        LeaderState.PRELIMINARY: LeaderState.OUT,
    }[source]


def assert_unique_active_leader_states(
    records: Iterable[LeaderStateRecord],
) -> None:
    active_by_symbol: Dict[str, LeaderState] = {}
    for record in records:
        if record.state == LeaderState.OUT:
            continue
        existing = active_by_symbol.get(record.symbol)
        if existing is not None:
            raise ValueError(
                f"同一证券不能同时存在多个龙头状态: {record.symbol}"
            )
        active_by_symbol[record.symbol] = record.state
