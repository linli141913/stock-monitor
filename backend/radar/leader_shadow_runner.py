"""阶段6三级龙头本地影子编排器。

本模块只编排调用方已经准备好的评分输入，不抓取数据、不读取环境变量、
不创建数据库连接、不注册调度任务，也不生成正式状态或交易建议。
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from radar.leader_input_gate import (
    LeaderInputEvidence,
    LeaderInputGatePolicy,
    build_leader_input_gate,
)
from radar.leader_repository import LeaderRepository
from radar.leader_scoring import (
    LeaderScoringAudit,
    LeaderScoringInput,
    build_leader_scoring_audit,
)
from radar.leader_state_machine import (
    DEFAULT_LEADER_STATE_MACHINE_POLICY,
    LeaderState,
    LeaderStateMachinePolicy,
    LeaderStateRecord,
    LeaderTransitionAction,
    LeaderTransitionDecision,
    decide_leader_transition,
)
from radar.repository import RepositoryConflictError


UTC = timezone.utc


class LeaderShadowRunError(RuntimeError):
    """龙头影子编排基础错误。"""


class LeaderShadowRunInProgressError(LeaderShadowRunError):
    """当前进程已有龙头影子运行。"""


class LeaderShadowRunAlreadyExistsError(LeaderShadowRunError):
    """相同运行标识已经存在且内容不能再次写入。"""


class LeaderShadowRunExecutionError(LeaderShadowRunError):
    """龙头影子编排或仓储写入失败。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _symbol(value: Any) -> str:
    text = _required_text(value, "symbol")
    if len(text) != 6 or not text.isdigit():
        raise ValueError("symbol必须是6位数字")
    return text


def _datetime_text(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _aware_utc(value, "时间").isoformat(timespec="microseconds")


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True)
class LeaderShadowCandidate:
    """一只证券的已准备评分输入和可追溯展示元数据。"""

    scoring_input: LeaderScoringInput
    name: str
    industry_code: Optional[str] = None
    industry_name: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    invalidation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _symbol(self.scoring_input.symbol)
        if not _required_text(self.name, "name"):
            raise ValueError("name不能为空")


@dataclass(frozen=True)
class LeaderShadowRunResult:
    radar_run_id: str
    as_of: datetime
    status: str
    quality: str
    coverage: float
    formal_usable: bool
    eligible_count: int
    preliminary_count: int
    candidate_count: int
    confirmed_count: int
    removed_count: int
    blocked_count: int
    persisted: bool
    persisted_transition_count: int
    audits: Tuple[LeaderScoringAudit, ...]
    decisions: Tuple[LeaderTransitionDecision, ...]


class LeaderShadowRunner:
    """把评分、状态机和阶段6临时仓储串成一轮本地影子运行。"""

    def __init__(
        self,
        repository: LeaderRepository,
        *,
        policy: LeaderStateMachinePolicy = (
            DEFAULT_LEADER_STATE_MACHINE_POLICY
        ),
        clock: Callable[[], datetime] = _utc_now,
        run_lock=None,
    ):
        if policy.formal_state_enabled:
            raise ValueError("阶段6D影子运行器不得启用正式状态")
        self._repository = repository
        self._policy = policy
        self._clock = clock
        self._run_lock = run_lock or threading.Lock()

    def run_once(
        self,
        radar_run_id: str,
        as_of: datetime,
        candidates: Sequence[LeaderShadowCandidate],
        *,
        previous_states: Optional[
            Mapping[str, LeaderStateRecord]
        ] = None,
    ) -> LeaderShadowRunResult:
        radar_run_id = _required_text(radar_run_id, "radarRunId")
        as_of = _aware_utc(as_of, "as_of")
        candidate_items = tuple(candidates)
        self._validate_candidates(candidate_items, as_of)
        if not self._run_lock.acquire(blocking=False):
            raise LeaderShadowRunInProgressError(
                "当前进程已有龙头影子运行"
            )

        previous_states = previous_states or {}
        try:
            audits = []
            decisions = []
            entries = []
            transitions = []
            reasons = Counter()
            healthy_count = 0
            for candidate in sorted(
                candidate_items,
                key=lambda item: item.scoring_input.symbol,
            ):
                audit = build_leader_scoring_audit(
                    candidate.scoring_input,
                    policy=self._policy,
                )
                previous = previous_states.get(candidate.scoring_input.symbol)
                decision = decide_leader_transition(
                    audit.snapshot,
                    previous,
                    policy=self._policy,
                )
                audit_reasons = tuple(audit.reasons)
                decision_reasons = tuple(decision.reasons)
                entry_reasons = _dedupe(
                    (*audit_reasons, *decision_reasons)
                )
                first_rejection = (
                    audit.first_rejection_reason
                    or decision.first_rejection_reason
                )
                reason_key = (
                    first_rejection
                    or f"state_{decision.to_state.value}"
                )
                reasons[reason_key] += 1
                healthy_count += audit.data_status.value == "healthy"
                audits.append(audit)
                decisions.append(decision)
                entries.append(
                    self._entry(
                        candidate,
                        audit=audit,
                        decision=decision,
                        reasons=entry_reasons,
                        first_rejection_reason=first_rejection,
                    )
                )
                transitions.append(
                    self._transition(
                        radar_run_id,
                        candidate,
                        audit=audit,
                        decision=decision,
                        reasons=entry_reasons,
                        first_rejection_reason=first_rejection,
                    )
                )

            eligible_count = len(candidate_items)
            coverage = (
                healthy_count / eligible_count
                if eligible_count
                else 0.0
            )
            state_counts = Counter(
                decision.to_state.value for decision in decisions
            )
            removed_count = sum(
                decision.action == LeaderTransitionAction.REMOVE
                for decision in decisions
            )
            blocked_count = sum(
                decision.action == LeaderTransitionAction.BLOCKED
                for decision in decisions
            )
            quality = (
                "empty"
                if not eligible_count
                else "complete"
                if healthy_count == eligible_count
                else "degraded"
            )
            snapshot = {
                "radarRunId": radar_run_id,
                "asOf": as_of,
                "ruleVersion": self._policy.version,
                "ruleVersionId": None,
                "eligibleCount": eligible_count,
                "preliminaryCount": state_counts["preliminary"],
                "candidateCount": state_counts["candidate"],
                "confirmedCount": state_counts["confirmed"],
                "removedCount": removed_count,
                "coverage": coverage,
                "quality": quality,
                "reasonCounts": dict(sorted(reasons.items())),
                "formalUsable": False,
            }
            persisted, persisted_transition_count = (
                self._repository.save_shadow_run(
                    snapshot,
                    entries,
                    transitions,
                )
            )
            return LeaderShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status="shadow_only",
                quality=quality,
                coverage=coverage,
                formal_usable=False,
                eligible_count=eligible_count,
                preliminary_count=state_counts["preliminary"],
                candidate_count=state_counts["candidate"],
                confirmed_count=state_counts["confirmed"],
                removed_count=removed_count,
                blocked_count=blocked_count,
                persisted=persisted or bool(persisted_transition_count),
                persisted_transition_count=persisted_transition_count,
                audits=tuple(audits),
                decisions=tuple(decisions),
            )
        except LeaderShadowRunError:
            raise
        except RepositoryConflictError as exc:
            raise LeaderShadowRunAlreadyExistsError(
                f"龙头影子运行{radar_run_id}内容冲突或已经写入"
            ) from exc
        except Exception as exc:
            raise LeaderShadowRunExecutionError(
                f"龙头影子执行失败：{type(exc).__name__}"
            ) from exc
        finally:
            self._run_lock.release()

    def run_evidence_once(
        self,
        radar_run_id: str,
        as_of: datetime,
        evidence_items: Sequence[LeaderInputEvidence],
        *,
        previous_states: Optional[
            Mapping[str, LeaderStateRecord]
        ] = None,
        input_gate_policy: LeaderInputGatePolicy = (
            LeaderInputGatePolicy()
        ),
    ) -> LeaderShadowRunResult:
        gated = tuple(
            build_leader_input_gate(
                item,
                policy=input_gate_policy,
                scoring_policy=self._policy,
            ).to_shadow_candidate()
            for item in evidence_items
        )
        return self.run_once(
            radar_run_id,
            as_of,
            gated,
            previous_states=previous_states,
        )

    @staticmethod
    def _validate_candidates(
        candidates: Sequence[LeaderShadowCandidate],
        as_of: datetime,
    ) -> None:
        symbols = set()
        for candidate in candidates:
            if not isinstance(candidate, LeaderShadowCandidate):
                raise TypeError("candidates必须全部是LeaderShadowCandidate")
            symbol = candidate.scoring_input.symbol
            if symbol in symbols:
                raise ValueError(f"同一影子轮次包含重复证券：{symbol}")
            symbols.add(symbol)
            candidate_as_of = _aware_utc(
                candidate.scoring_input.as_of,
                "scoring_input.as_of",
            )
            if candidate_as_of != as_of:
                raise ValueError(
                    f"{symbol}的scoring_input.as_of必须与批次as_of一致"
                )

    @staticmethod
    def _entry(
        candidate: LeaderShadowCandidate,
        *,
        audit: LeaderScoringAudit,
        decision: LeaderTransitionDecision,
        reasons: Tuple[str, ...],
        first_rejection_reason: Optional[str],
    ) -> Dict[str, Any]:
        snapshot = audit.snapshot
        evidence = {
            **dict(candidate.evidence),
            "score": audit.score,
            "dataStatus": audit.data_status.value,
            "ruleVersion": audit.rule_version,
            "industryGatePassed": snapshot.industry_gate_passed,
            "stockGatePassed": snapshot.stock_gate_passed,
            "marketLeadershipPassed": snapshot.market_leadership_passed,
            "industryContributionPassed": (
                snapshot.industry_contribution_passed
            ),
            "liquidityPassed": snapshot.liquidity_passed,
            "tradabilityPassed": snapshot.tradability_passed,
            "continuityPassed": snapshot.continuity_passed,
            "recoveryPassed": snapshot.recovery_passed,
            "riskFilterPassed": snapshot.risk_filter_passed,
            "consecutiveSignalPeriods": (
                snapshot.consecutive_signal_periods
            ),
        }
        invalidation = {
            **dict(candidate.invalidation),
            "fromState": decision.from_state.value,
            "toState": decision.to_state.value,
            "action": decision.action.value,
            "cooldownUntil": _datetime_text(decision.cooldown_until),
        }
        return {
            "symbol": snapshot.symbol,
            "name": candidate.name,
            "industryCode": candidate.industry_code,
            "industryName": candidate.industry_name,
            "state": decision.to_state.value,
            "score": audit.score,
            "businessExposureStatus": (
                snapshot.business_exposure_status.value
            ),
            "dataStatus": snapshot.data_status.value,
            "firstRejectionReason": first_rejection_reason,
            "reasons": list(reasons),
            "evidence": evidence,
            "invalidation": invalidation,
            "stateAgePeriods": decision.state_age_periods,
            "formalUsable": False,
        }

    @staticmethod
    def _transition(
        radar_run_id: str,
        candidate: LeaderShadowCandidate,
        *,
        audit: LeaderScoringAudit,
        decision: LeaderTransitionDecision,
        reasons: Tuple[str, ...],
        first_rejection_reason: Optional[str],
    ) -> Dict[str, Any]:
        snapshot = audit.snapshot
        transition_id = (
            f"{radar_run_id}:{snapshot.symbol}:"
            f"{snapshot.as_of.isoformat()}"
        )
        return {
            "transitionId": transition_id,
            "symbol": snapshot.symbol,
            "radarRunId": radar_run_id,
            "asOf": snapshot.as_of,
            "fromState": decision.from_state.value,
            "toState": decision.to_state.value,
            "action": decision.action.value,
            "ruleVersion": decision.rule_version,
            "ruleVersionId": None,
            "reasons": list(reasons),
            "firstRejectionReason": first_rejection_reason,
            "stateAgePeriods": decision.state_age_periods,
            "cooldownUntil": decision.cooldown_until,
            "formalUsable": False,
        }
