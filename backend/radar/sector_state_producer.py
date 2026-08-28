"""行业八阶段的确定性影子状态生产者。

本模块只消费已经冻结的当轮行业横截面、同分钟历史和具名阈值批准。
它不联网、不读写数据库、不打开正式门；输出快照只能作为下一次真实观测
的前态，重复或倒序观测一律失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import tempfile
from typing import Any, Mapping, Optional, Tuple

from radar.sector_history_backfill import SectorHistoricalAnalysis
from radar.sector_rule_readiness import (
    REQUIRED_STATE_IDS,
    SECTOR_RULE_VERSION,
)
from radar.sector_threshold_review import (
    SectorThresholdApprovalRecord,
    is_sector_threshold_approval_record_valid,
)


SECTOR_STATE_TRANSITION_POLICY_VERSION = (
    "radar-sector-state-transition-conservative-v1"
)
SECTOR_STATE_OBSERVATION_CONTRACT_ID = (
    "radar-sector-state-observation-v1"
)
SECTOR_STATE_SNAPSHOT_CONTRACT_ID = "radar-sector-state-snapshot-v1"
SECTOR_STATE_PRODUCTION_CONTRACT_ID = "radar-sector-state-production-v1"
_SNAPSHOT_PRODUCER_TOKEN = object()
_STATE_PRODUCTION_TOKEN = object()


class SectorLifecycleState(str, Enum):
    UNCLASSIFIED = "unclassified"
    OBSERVE = "observe"
    STARTUP = "startup"
    CONFIRMED = "confirmed"
    ACCELERATING = "accelerating"
    DIVERGENCE = "divergence"
    REFLOW = "reflow"
    RETREAT = "retreat"
    INVALID = "invalid"


class SectorStateProductionStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


ELIGIBLE_EVIDENCE_STATES = frozenset({
    SectorLifecycleState.OBSERVE,
    SectorLifecycleState.STARTUP,
    SectorLifecycleState.CONFIRMED,
})


def is_sector_evidence_candidate_state(value: Any) -> bool:
    """阶段6只允许观察、启动、确认行业产生新的深度核验候选。"""

    return isinstance(value, SectorLifecycleState) and (
        value in ELIGIBLE_EVIDENCE_STATES
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _finite_number(value: Any) -> bool:
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def sector_threshold_record_sha256(
    record: SectorThresholdApprovalRecord,
) -> str:
    """按阈值批准模块的规范化口径复算记录摘要。"""

    return _digest(record.payload_without_hash())


@dataclass(frozen=True)
class SectorStateMetrics:
    turnover_ratio_20d: float
    relative_return: float
    persistence_positive_ratio_5d: float
    latest_completed_history_date: Any

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "turnoverRatio20d": self.turnover_ratio_20d,
            "relativeReturn": self.relative_return,
            "persistencePositiveRatio5d": (
                self.persistence_positive_ratio_5d
            ),
            "latestCompletedHistoryDate": (
                self.latest_completed_history_date.isoformat()
            ),
        }


@dataclass(frozen=True)
class SectorStateRecord:
    industry_code: str
    state: SectorLifecycleState = SectorLifecycleState.UNCLASSIFIED
    state_since: Optional[datetime] = None
    pending_state: Optional[SectorLifecycleState] = None
    pending_count: int = 0
    cooldowns: Tuple[Tuple[SectorLifecycleState, datetime], ...] = ()

    def cooldown_until(
        self,
        state: SectorLifecycleState,
    ) -> Optional[datetime]:
        return dict(self.cooldowns).get(state)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "industryCode": self.industry_code,
            "state": self.state.value,
            "stateSince": (
                self.state_since.isoformat() if self.state_since else None
            ),
            "pendingState": (
                self.pending_state.value if self.pending_state else None
            ),
            "pendingCount": self.pending_count,
            "cooldowns": {
                state.value: value.isoformat()
                for state, value in self.cooldowns
            },
        }


def _snapshot_payload(
    value: "SectorStatePreviousSnapshot",
) -> Mapping[str, object]:
    return {
        "contractId": value.contract_id,
        "industryCodes": list(value.industry_codes),
        "classificationDocumentSha256": (
            value.classification_document_sha256
        ),
        "ruleVersion": value.rule_version,
        "transitionPolicyVersion": value.transition_policy_version,
        "thresholdSetId": value.threshold_set_id,
        "approvalId": value.approval_id,
        "observedAt": value.observed_at.isoformat(),
        "lastQuoteBatchId": value.last_quote_batch_id,
        "records": [record.to_evidence() for record in value.records],
    }


@dataclass(frozen=True, repr=False)
class SectorStatePreviousSnapshot:
    industry_codes: Tuple[str, ...]
    classification_document_sha256: str
    rule_version: str
    transition_policy_version: str
    threshold_set_id: str
    approval_id: str
    observed_at: datetime
    last_quote_batch_id: Optional[str]
    records: Tuple[SectorStateRecord, ...] = field(repr=False)
    snapshot_sha256: str
    _producer_token: Any = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    contract_id: str = SECTOR_STATE_SNAPSHOT_CONTRACT_ID


@dataclass(frozen=True, repr=False)
class SectorStateObservation:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    classification_document_sha256: str
    as_of: datetime
    comparable_time: time
    sector_returns_percent_points: Mapping[str, float] = field(repr=False)
    sector_turnover_amount_cny: Mapping[str, float] = field(repr=False)
    market_equal_return_percent_points: float
    historical_analyses: Tuple[SectorHistoricalAnalysis, ...] = field(
        repr=False
    )
    approval_record: SectorThresholdApprovalRecord = field(repr=False)
    rule_version: str = SECTOR_RULE_VERSION
    transition_policy_version: str = (
        SECTOR_STATE_TRANSITION_POLICY_VERSION
    )
    contract_id: str = SECTOR_STATE_OBSERVATION_CONTRACT_ID


@dataclass(frozen=True)
class SectorStateProductionItem:
    industry_code: str
    previous_state: SectorLifecycleState
    state: SectorLifecycleState
    metrics: SectorStateMetrics
    transitioned: bool
    reasons: Tuple[str, ...]
    evidence_candidate_eligible: bool

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "industryCode": self.industry_code,
            "previousState": self.previous_state.value,
            "state": self.state.value,
            "metrics": self.metrics.to_evidence(),
            "transitioned": self.transitioned,
            "reasons": list(self.reasons),
            "evidenceCandidateEligible": (
                self.evidence_candidate_eligible
            ),
        }


@dataclass(frozen=True, repr=False)
class SectorStateProductionResult:
    status: SectorStateProductionStatus
    candidate_plan_id: Optional[str]
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    items: Tuple[SectorStateProductionItem, ...] = field(
        default=(),
        repr=False,
    )
    next_snapshot: Optional[SectorStatePreviousSnapshot] = field(
        default=None,
        repr=False,
    )
    sector_rule_bridge: Any = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    _producer_token: Any = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    contract_id: str = SECTOR_STATE_PRODUCTION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "transitionPolicyVersion": (
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            "items": [item.to_evidence() for item in self.items],
            "nextSnapshotSha256": (
                self.next_snapshot.snapshot_sha256
                if self.next_snapshot else None
            ),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _blocked(
    observation: Any,
    reason: str,
) -> SectorStateProductionResult:
    return SectorStateProductionResult(
        status=SectorStateProductionStatus.BLOCKED,
        candidate_plan_id=getattr(observation, "candidate_plan_id", None),
        radar_run_id=getattr(observation, "radar_run_id", None),
        as_of=(
            observation.as_of
            if _aware(getattr(observation, "as_of", None))
            else None
        ),
        reasons=(reason,),
    )


def _new_snapshot(
    *,
    industry_codes: Tuple[str, ...],
    classification_document_sha256: str,
    rule_version: str,
    transition_policy_version: str,
    threshold_set_id: str,
    approval_id: str,
    observed_at: datetime,
    last_quote_batch_id: Optional[str],
    records: Tuple[SectorStateRecord, ...],
) -> SectorStatePreviousSnapshot:
    value = SectorStatePreviousSnapshot(
        industry_codes=industry_codes,
        classification_document_sha256=classification_document_sha256,
        rule_version=rule_version,
        transition_policy_version=transition_policy_version,
        threshold_set_id=threshold_set_id,
        approval_id=approval_id,
        observed_at=observed_at,
        last_quote_batch_id=last_quote_batch_id,
        records=records,
        snapshot_sha256="",
    )
    object.__setattr__(
        value,
        "_producer_token",
        _SNAPSHOT_PRODUCER_TOKEN,
    )
    object.__setattr__(
        value,
        "snapshot_sha256",
        _digest(_snapshot_payload(value)),
    )
    return value


def build_initial_sector_state_snapshot(
    *,
    industry_codes: Tuple[str, ...],
    classification_document_sha256: str,
    rule_version: str,
    transition_policy_version: str,
    threshold_set_id: str,
    approval_id: str,
    observed_before: datetime,
) -> SectorStatePreviousSnapshot:
    """建立不带任何行业结论的首次影子快照。"""

    if (
        not isinstance(industry_codes, tuple)
        or not industry_codes
        or industry_codes != tuple(sorted(industry_codes))
        or len(industry_codes) != len(set(industry_codes))
        or any(
            not isinstance(code, str)
            or len(code) != 2
            or not code.isdigit()
            for code in industry_codes
        )
        or not isinstance(classification_document_sha256, str)
        or len(classification_document_sha256) != 64
        or not _aware(observed_before)
        or rule_version != SECTOR_RULE_VERSION
        or transition_policy_version
        != SECTOR_STATE_TRANSITION_POLICY_VERSION
        or not isinstance(threshold_set_id, str)
        or not threshold_set_id.strip()
        or not isinstance(approval_id, str)
        or not approval_id.strip()
    ):
        raise ValueError("sector_state_initial_snapshot_unverified")
    return _new_snapshot(
        industry_codes=industry_codes,
        classification_document_sha256=classification_document_sha256,
        rule_version=rule_version,
        transition_policy_version=transition_policy_version,
        threshold_set_id=threshold_set_id,
        approval_id=approval_id,
        observed_at=observed_before,
        last_quote_batch_id=None,
        records=tuple(
            SectorStateRecord(industry_code=code)
            for code in industry_codes
        ),
    )


def _private_tmp_path(value: Any) -> Path:
    path = Path(value).expanduser()
    text = str(path)
    if not path.is_absolute() or not text.startswith("/private/tmp/"):
        raise ValueError("sector_state_snapshot_path_unverified")
    resolved = path.resolve()
    try:
        resolved.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError("sector_state_snapshot_path_unverified") from exc
    return resolved


def write_sector_state_snapshot(
    snapshot: Any,
    path: Path,
) -> None:
    """只向 ``/private/tmp`` 原子写入生产者生成的影子前态。"""

    target = _private_tmp_path(path)
    if (
        type(snapshot) is not SectorStatePreviousSnapshot
        or snapshot._producer_token is not _SNAPSHOT_PRODUCER_TOKEN
        or snapshot.snapshot_sha256 != _digest(_snapshot_payload(snapshot))
    ):
        raise ValueError("sector_state_snapshot_unverified")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **_snapshot_payload(snapshot),
        "snapshotSha256": snapshot.snapshot_sha256,
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_sector_state_snapshot(
    path: Path,
    *,
    expected_industry_codes: Tuple[str, ...],
    classification_document_sha256: str,
    rule_version: str,
    transition_policy_version: str,
    threshold_set_id: str,
    approval_id: str,
    before_as_of: datetime,
) -> SectorStatePreviousSnapshot:
    """读取并校验严格早于本轮的完整影子前态。"""

    source = _private_tmp_path(path)
    if not _aware(before_as_of):
        raise ValueError("sector_state_snapshot_unverified")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(payload["observedAt"])
        records = []
        for item in payload["records"]:
            cooldowns = tuple(sorted(
                [(
                    SectorLifecycleState(state),
                    datetime.fromisoformat(until),
                )
                for state, until in item["cooldowns"].items()
                ],
                key=lambda value: value[0].value,
            ))
            records.append(SectorStateRecord(
                industry_code=item["industryCode"],
                state=SectorLifecycleState(item["state"]),
                state_since=(
                    datetime.fromisoformat(item["stateSince"])
                    if item.get("stateSince") else None
                ),
                pending_state=(
                    SectorLifecycleState(item["pendingState"])
                    if item.get("pendingState") else None
                ),
                pending_count=item["pendingCount"],
                cooldowns=cooldowns,
            ))
        snapshot = SectorStatePreviousSnapshot(
            industry_codes=tuple(payload["industryCodes"]),
            classification_document_sha256=(
                payload["classificationDocumentSha256"]
            ),
            rule_version=payload["ruleVersion"],
            transition_policy_version=payload["transitionPolicyVersion"],
            threshold_set_id=payload["thresholdSetId"],
            approval_id=payload["approvalId"],
            observed_at=observed_at,
            last_quote_batch_id=payload.get("lastQuoteBatchId"),
            records=tuple(records),
            snapshot_sha256=payload["snapshotSha256"],
            contract_id=payload["contractId"],
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("sector_state_snapshot_unverified") from exc
    object.__setattr__(
        snapshot,
        "_producer_token",
        _SNAPSHOT_PRODUCER_TOKEN,
    )
    if (
        snapshot.contract_id != SECTOR_STATE_SNAPSHOT_CONTRACT_ID
        or snapshot.snapshot_sha256 != _digest(_snapshot_payload(snapshot))
        or snapshot.industry_codes != expected_industry_codes
        or snapshot.classification_document_sha256
        != classification_document_sha256
        or snapshot.rule_version != rule_version
        or snapshot.transition_policy_version
        != transition_policy_version
        or snapshot.threshold_set_id != threshold_set_id
        or snapshot.approval_id != approval_id
        or not _aware(snapshot.observed_at)
        or snapshot.observed_at >= before_as_of
        or not _snapshot_structure_valid(snapshot)
    ):
        raise ValueError("sector_state_snapshot_unverified")
    return snapshot


def _snapshot_structure_valid(value: Any) -> bool:
    if (
        type(value) is not SectorStatePreviousSnapshot
        or value.contract_id != SECTOR_STATE_SNAPSHOT_CONTRACT_ID
        or value._producer_token is not _SNAPSHOT_PRODUCER_TOKEN
        or value.snapshot_sha256 != _digest(_snapshot_payload(value))
        or not _aware(value.observed_at)
        or not isinstance(value.records, tuple)
        or tuple(record.industry_code for record in value.records)
        != value.industry_codes
    ):
        return False
    for record in value.records:
        if (
            type(record) is not SectorStateRecord
            or not isinstance(record.state, SectorLifecycleState)
            or (
                record.state == SectorLifecycleState.UNCLASSIFIED
                and record.state_since is not None
            )
            or (
                record.state != SectorLifecycleState.UNCLASSIFIED
                and not _aware(record.state_since)
            )
            or (
                record.pending_state is None
                and record.pending_count != 0
            )
            or (
                record.pending_state is not None
                and (
                    not isinstance(record.pending_state, SectorLifecycleState)
                    or record.pending_count < 1
                )
            )
            or any(
                not isinstance(state, SectorLifecycleState)
                or state == SectorLifecycleState.UNCLASSIFIED
                or not _aware(until)
                for state, until in record.cooldowns
            )
            or len(record.cooldowns) != len(dict(record.cooldowns))
        ):
            return False
    return True


def _snapshot_valid(
    value: Any,
    observation: SectorStateObservation,
) -> bool:
    return bool(
        _snapshot_structure_valid(value)
        and value.industry_codes
        == tuple(sorted(observation.sector_returns_percent_points))
        and value.industry_codes
        == tuple(sorted(observation.sector_turnover_amount_cny))
        and value.classification_document_sha256
        == observation.classification_document_sha256
        and value.rule_version == observation.rule_version
        and value.transition_policy_version
        == observation.transition_policy_version
        and value.threshold_set_id
        == observation.approval_record.threshold_set_id
        and value.approval_id == observation.approval_record.approval_id
    )


def _observation_valid(value: Any) -> bool:
    if (
        type(value) is not SectorStateObservation
        or value.contract_id != SECTOR_STATE_OBSERVATION_CONTRACT_ID
        or not all(
            isinstance(item, str) and item.strip()
            for item in (
                value.candidate_plan_id,
                value.radar_run_id,
                value.quote_batch_id,
            )
        )
        or not isinstance(value.classification_document_sha256, str)
        or len(value.classification_document_sha256) != 64
        or not _aware(value.as_of)
        or type(value.comparable_time) is not time
        or value.rule_version != SECTOR_RULE_VERSION
        or value.transition_policy_version
        != SECTOR_STATE_TRANSITION_POLICY_VERSION
        or not isinstance(value.sector_returns_percent_points, Mapping)
        or not isinstance(value.sector_turnover_amount_cny, Mapping)
        or not value.sector_returns_percent_points
        or set(value.sector_returns_percent_points)
        != set(value.sector_turnover_amount_cny)
        or not isinstance(value.historical_analyses, tuple)
        or not _finite_number(
            value.market_equal_return_percent_points
        )
    ):
        return False
    codes = tuple(sorted(value.sector_returns_percent_points))
    analyses = tuple(item.division_code for item in value.historical_analyses)
    if tuple(sorted(analyses)) != codes or len(analyses) != len(set(analyses)):
        return False
    for code in codes:
        sector_return = value.sector_returns_percent_points[code]
        turnover = value.sector_turnover_amount_cny[code]
        if (
            isinstance(sector_return, bool)
            or not isinstance(sector_return, (int, float))
            or not math.isfinite(float(sector_return))
            or isinstance(turnover, bool)
            or not isinstance(turnover, (int, float))
            or not math.isfinite(float(turnover))
            or float(turnover) < 0
        ):
            return False
    approval = value.approval_record
    return bool(
        is_sector_threshold_approval_record_valid(approval)
        and approval.rule_version == value.rule_version
        and approval.approved_at <= value.as_of
        and set(approval.state_policies) == REQUIRED_STATE_IDS
    )


def build_sector_state_observation_from_runtime(
    context: Any,
    *,
    source_batch: Any,
    historical_analyses: Any,
    approval_record: Any,
    comparable_time: time,
) -> Tuple[SectorStateObservation, Any]:
    """从已通过现有行业桥的同轮输入构造状态观测。"""

    from radar.leader_research_runtime_provider import (
        is_leader_research_runtime_source_context_valid,
    )
    from radar.sector_rule_runtime_bridge import (
        SectorRuleRuntimeBridgeStatus,
        SectorRuleRuntimeSourceBatch,
        build_sector_rule_runtime_bridge,
    )

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or type(source_batch) is not SectorRuleRuntimeSourceBatch
        or not isinstance(historical_analyses, tuple)
        or type(approval_record) is not SectorThresholdApprovalRecord
        or type(comparable_time) is not time
    ):
        raise ValueError("sector_state_runtime_input_unverified")
    bridge = build_sector_rule_runtime_bridge(
        context,
        source_batch=source_batch,
    )
    plan = context.candidate_plan
    if (
        bridge.status != SectorRuleRuntimeBridgeStatus.READY
        or bridge.reasons
        or bridge.readiness_result is None
    ):
        raise ValueError("sector_state_runtime_input_unverified")
    from radar.leader_phase6_live_prefreeze import (
        resolve_sector_comparable_time,
    )
    try:
        source_comparable_time = resolve_sector_comparable_time(
            source_batch.feature_batch.source_time
        )
    except ValueError as exc:
        raise ValueError("sector_state_runtime_input_unverified") from exc
    if comparable_time != source_comparable_time:
        raise ValueError("sector_state_runtime_input_unverified")
    parent_codes = tuple(sorted({
        item.industry_code for item in plan.items
    }))
    feature_by_code = {
        item.division_code: item
        for item in source_batch.feature_batch.sectors
        if item.division_code in parent_codes
    }
    analysis_by_code = {
        item.division_code: item
        for item in historical_analyses
        if type(item) is SectorHistoricalAnalysis
    }
    coverage_by_code = {
        item.division_code: item
        for item in source_batch.history_evidence.rows
        if item.division_code in parent_codes
    }
    approval_evidence = source_batch.threshold_approval_evidence
    if (
        tuple(sorted(feature_by_code)) != parent_codes
        or not set(parent_codes).issubset(analysis_by_code)
        or tuple(sorted(coverage_by_code)) != parent_codes
        or len(analysis_by_code) != len(historical_analyses)
        or approval_record.rule_version
        != approval_evidence.rule_version
        or approval_record.threshold_set_id
        != approval_evidence.threshold_set_id
        or approval_record.approval_id != approval_evidence.approval_id
        or approval_record.approved_at != approval_evidence.approved_at
        or approval_record.record_sha256
        != sector_threshold_record_sha256(approval_record)
    ):
        raise ValueError("sector_state_runtime_input_unverified")
    for code in parent_codes:
        analysis = analysis_by_code[code]
        coverage = coverage_by_code[code]
        if (
            analysis.comparable_time != comparable_time
            or tuple(
                item.trade_date
                for item in analysis.same_minute_turnover_samples[-20:]
            ) != coverage.same_minute_trading_dates[-20:]
            or tuple(
                item.trade_date
                for item in analysis.relative_return_samples[-4:]
            ) != coverage.persistence_trading_dates[-4:]
        ):
            raise ValueError("sector_state_runtime_input_unverified")
        feature = feature_by_code[code]
        if (
            not feature.returns.equal_return.available
            or not feature.turnover.available
            or not isinstance(
                feature.returns.equal_return.raw_value,
                (int, float),
            )
            or isinstance(feature.returns.equal_return.raw_value, bool)
            or not isinstance(feature.turnover.raw_value, (int, float))
            or isinstance(feature.turnover.raw_value, bool)
        ):
            raise ValueError("sector_state_runtime_input_unverified")
    baseline = source_batch.market_baseline_evidence
    observation = SectorStateObservation(
        candidate_plan_id=plan.candidate_set_id,
        radar_run_id=plan.radar_run_id,
        quote_batch_id=plan.quote_batch_id,
        classification_document_sha256=(
            source_batch.feature_batch.classification_document_sha256
        ),
        as_of=plan.as_of,
        comparable_time=comparable_time,
        sector_returns_percent_points={
            code: float(
                feature_by_code[code].returns.equal_return.raw_value
            )
            for code in parent_codes
        },
        sector_turnover_amount_cny={
            code: float(feature_by_code[code].turnover.raw_value)
            for code in parent_codes
        },
        market_equal_return_percent_points=float(
            baseline.equal_weighted_return
        ),
        historical_analyses=tuple(
            analysis_by_code[code] for code in parent_codes
        ),
        approval_record=approval_record,
        rule_version=source_batch.rule_version,
    )
    if not _observation_valid(observation):
        raise ValueError("sector_state_runtime_input_unverified")
    return observation, bridge


def _metrics(
    observation: SectorStateObservation,
) -> Optional[Mapping[str, SectorStateMetrics]]:
    result = {}
    analysis_by_code = {
        item.division_code: item for item in observation.historical_analyses
    }
    for code in sorted(observation.sector_returns_percent_points):
        analysis = analysis_by_code[code]
        turnover_history = analysis.same_minute_turnover_samples[-20:]
        relative_history = analysis.relative_return_samples[-4:]
        if (
            len(turnover_history) != 20
            or len(relative_history) != 4
            or any(
                item.trade_date >= observation.as_of.date()
                or not math.isfinite(float(item.value))
                or float(item.value) < 0
                for item in turnover_history
            )
            or any(
                item.trade_date >= observation.as_of.date()
                or not math.isfinite(float(item.value))
                for item in relative_history
            )
        ):
            return None
        baseline = median(float(item.value) for item in turnover_history)
        if baseline <= 0:
            return None
        current_relative = (
            float(observation.sector_returns_percent_points[code])
            - float(observation.market_equal_return_percent_points)
        ) / 100.0
        persistence = (
            sum(float(item.value) > 0 for item in relative_history)
            + (current_relative > 0)
        ) / 5.0
        result[code] = SectorStateMetrics(
            turnover_ratio_20d=(
                float(observation.sector_turnover_amount_cny[code])
                / baseline
            ),
            relative_return=current_relative,
            persistence_positive_ratio_5d=persistence,
            latest_completed_history_date=max(
                item.trade_date for item in turnover_history
            ),
        )
    return result


def _condition_matches(
    condition: Mapping[str, Any],
    metrics: SectorStateMetrics,
) -> bool:
    metric_values = {
        "turnoverRatio20d": metrics.turnover_ratio_20d,
        "relativeReturn": metrics.relative_return,
        "persistencePositiveRatio5d": (
            metrics.persistence_positive_ratio_5d
        ),
    }
    try:
        actual = metric_values[condition["metric"]]
        expected = float(condition["value"])
        operator = condition["operator"]
    except (KeyError, TypeError, ValueError):
        return False
    return {
        "gt": actual > expected,
        "gte": actual >= expected,
        "lt": actual < expected,
        "lte": actual <= expected,
        "eq": actual == expected,
    }.get(operator, False)


_ACTIVE_STATES = frozenset({
    SectorLifecycleState.OBSERVE,
    SectorLifecycleState.STARTUP,
    SectorLifecycleState.CONFIRMED,
    SectorLifecycleState.ACCELERATING,
    SectorLifecycleState.DIVERGENCE,
    SectorLifecycleState.REFLOW,
})


_PROGRESSION = {
    SectorLifecycleState.OBSERVE: SectorLifecycleState.STARTUP,
    SectorLifecycleState.STARTUP: SectorLifecycleState.CONFIRMED,
    SectorLifecycleState.CONFIRMED: SectorLifecycleState.ACCELERATING,
    SectorLifecycleState.DIVERGENCE: SectorLifecycleState.REFLOW,
    SectorLifecycleState.REFLOW: SectorLifecycleState.ACCELERATING,
    SectorLifecycleState.INVALID: SectorLifecycleState.OBSERVE,
}


_EXIT_TARGET = {
    SectorLifecycleState.OBSERVE: SectorLifecycleState.UNCLASSIFIED,
    SectorLifecycleState.STARTUP: SectorLifecycleState.OBSERVE,
    SectorLifecycleState.CONFIRMED: SectorLifecycleState.STARTUP,
    SectorLifecycleState.ACCELERATING: SectorLifecycleState.CONFIRMED,
    SectorLifecycleState.DIVERGENCE: SectorLifecycleState.CONFIRMED,
    SectorLifecycleState.REFLOW: SectorLifecycleState.DIVERGENCE,
    SectorLifecycleState.RETREAT: SectorLifecycleState.UNCLASSIFIED,
    SectorLifecycleState.INVALID: SectorLifecycleState.UNCLASSIFIED,
}


def _entry_target(
    state: SectorLifecycleState,
    metrics: SectorStateMetrics,
    policies: Mapping[str, Mapping[str, Any]],
) -> Optional[SectorLifecycleState]:
    if state == SectorLifecycleState.UNCLASSIFIED:
        target = SectorLifecycleState.OBSERVE
        return target if _condition_matches(
            policies[target.value]["entry"], metrics
        ) else None
    if (
        state == SectorLifecycleState.RETREAT
        and _condition_matches(policies["invalid"]["entry"], metrics)
    ):
        return SectorLifecycleState.INVALID
    if (
        state in _ACTIVE_STATES
        and _condition_matches(policies["retreat"]["entry"], metrics)
    ):
        return SectorLifecycleState.RETREAT
    if (
        state in {
            SectorLifecycleState.ACCELERATING,
            SectorLifecycleState.REFLOW,
        }
        and _condition_matches(policies["divergence"]["entry"], metrics)
    ):
        return SectorLifecycleState.DIVERGENCE
    target = _PROGRESSION.get(state)
    if target is not None and _condition_matches(
        policies[target.value]["entry"], metrics
    ):
        return target
    return None


def _can_leave(
    record: SectorStateRecord,
    *,
    observed_at: datetime,
    policies: Mapping[str, Mapping[str, Any]],
) -> bool:
    if record.state == SectorLifecycleState.UNCLASSIFIED:
        return True
    policy = policies[record.state.value]
    minimum = int(policy["minimum_hold_time"])
    if record.state == SectorLifecycleState.INVALID:
        minimum = max(minimum, int(policy["cooldown"]))
    return bool(
        record.state_since is not None
        and observed_at >= record.state_since + timedelta(seconds=minimum)
    )


def _with_transition(
    record: SectorStateRecord,
    *,
    target: SectorLifecycleState,
    observed_at: datetime,
    policies: Mapping[str, Mapping[str, Any]],
) -> SectorStateRecord:
    cooldowns = {
        state: until
        for state, until in record.cooldowns
        if until > observed_at
    }
    if record.state != SectorLifecycleState.UNCLASSIFIED:
        seconds = int(policies[record.state.value]["cooldown"])
        if seconds:
            cooldowns[record.state] = observed_at + timedelta(
                seconds=seconds
            )
    return SectorStateRecord(
        industry_code=record.industry_code,
        state=target,
        state_since=(
            observed_at
            if target != SectorLifecycleState.UNCLASSIFIED else None
        ),
        cooldowns=tuple(sorted(
            cooldowns.items(),
            key=lambda item: item[0].value,
        )),
    )


def _evaluate_record(
    record: SectorStateRecord,
    *,
    metrics: SectorStateMetrics,
    observed_at: datetime,
    policies: Mapping[str, Mapping[str, Any]],
) -> Tuple[SectorStateRecord, Tuple[str, ...]]:
    target = _entry_target(record.state, metrics, policies)
    if target is not None:
        target_cooldown = record.cooldown_until(target)
        if target_cooldown is not None and observed_at < target_cooldown:
            return replace(
                record,
                pending_state=None,
                pending_count=0,
            ), ("sector_state_target_cooldown_active",)
        pending_count = (
            record.pending_count + 1
            if record.pending_state == target else 1
        )
        required = int(
            policies[target.value]["consecutive_observations"]
        )
        if pending_count >= required and _can_leave(
            record,
            observed_at=observed_at,
            policies=policies,
        ):
            return _with_transition(
                record,
                target=target,
                observed_at=observed_at,
                policies=policies,
            ), (f"sector_state_entered:{target.value}",)
        return replace(
            record,
            pending_state=target,
            pending_count=pending_count,
        ), (f"sector_state_entry_pending:{target.value}",)

    current_policy = (
        policies.get(record.state.value)
        if record.state != SectorLifecycleState.UNCLASSIFIED
        else None
    )
    if (
        current_policy is not None
        and _condition_matches(current_policy["exit"], metrics)
        and _can_leave(
            record,
            observed_at=observed_at,
            policies=policies,
        )
    ):
        exit_target = _EXIT_TARGET[record.state]
        return _with_transition(
            record,
            target=exit_target,
            observed_at=observed_at,
            policies=policies,
        ), (f"sector_state_exited:{record.state.value}",)
    return replace(
        record,
        pending_state=None,
        pending_count=0,
    ), ("sector_state_held",)


def produce_sector_state_snapshot(
    observation: Any,
    *,
    previous_snapshot: Any,
) -> SectorStateProductionResult:
    """用一次严格递增的真实观测产生全行业八阶段影子快照。"""

    if not _observation_valid(observation):
        return _blocked(observation, "sector_state_observation_unverified")
    if not _snapshot_valid(previous_snapshot, observation):
        return _blocked(
            observation,
            "sector_state_previous_snapshot_unverified",
        )
    if (
        observation.as_of <= previous_snapshot.observed_at
        or observation.quote_batch_id
        == previous_snapshot.last_quote_batch_id
    ):
        return _blocked(
            observation,
            "sector_state_observation_not_increasing",
        )
    metrics_by_code = _metrics(observation)
    if metrics_by_code is None:
        return _blocked(observation, "sector_state_metrics_unverified")

    policies = observation.approval_record.state_policies
    next_records = []
    items = []
    for previous in previous_snapshot.records:
        current, reasons = _evaluate_record(
            previous,
            metrics=metrics_by_code[previous.industry_code],
            observed_at=observation.as_of,
            policies=policies,
        )
        next_records.append(current)
        items.append(SectorStateProductionItem(
            industry_code=previous.industry_code,
            previous_state=previous.state,
            state=current.state,
            metrics=metrics_by_code[previous.industry_code],
            transitioned=current.state != previous.state,
            reasons=reasons,
            evidence_candidate_eligible=(
                is_sector_evidence_candidate_state(current.state)
            ),
        ))
    snapshot = _new_snapshot(
        industry_codes=previous_snapshot.industry_codes,
        classification_document_sha256=(
            observation.classification_document_sha256
        ),
        rule_version=observation.rule_version,
        transition_policy_version=observation.transition_policy_version,
        threshold_set_id=observation.approval_record.threshold_set_id,
        approval_id=observation.approval_record.approval_id,
        observed_at=observation.as_of,
        last_quote_batch_id=observation.quote_batch_id,
        records=tuple(next_records),
    )
    result = SectorStateProductionResult(
        status=SectorStateProductionStatus.READY,
        candidate_plan_id=observation.candidate_plan_id,
        radar_run_id=observation.radar_run_id,
        as_of=observation.as_of,
        items=tuple(items),
        next_snapshot=snapshot,
    )
    object.__setattr__(result, "_producer_token", _STATE_PRODUCTION_TOKEN)
    return result


def produce_sector_state_from_runtime(
    context: Any,
    *,
    source_batch: Any,
    historical_analyses: Any,
    approval_record: Any,
    comparable_time: time,
    previous_snapshot: Any,
) -> SectorStateProductionResult:
    """重放既有行业桥后，产生可供阶段6候选范围消费的状态。"""

    try:
        observation, bridge = build_sector_state_observation_from_runtime(
            context,
            source_batch=source_batch,
            historical_analyses=historical_analyses,
            approval_record=approval_record,
            comparable_time=comparable_time,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _blocked(
            context,
            "sector_state_runtime_input_unverified",
        )
    result = produce_sector_state_snapshot(
        observation,
        previous_snapshot=previous_snapshot,
    )
    if result.status != SectorStateProductionStatus.READY:
        return result
    bound = replace(result, sector_rule_bridge=bridge)
    object.__setattr__(bound, "_producer_token", _STATE_PRODUCTION_TOKEN)
    return bound


def produce_sector_state_from_prefrozen(
    context: Any,
    *,
    prefrozen_inputs: Any,
    previous_snapshot: Any,
) -> SectorStateProductionResult:
    """直接消费阶段6最终身份已经冻结的行业输入。"""

    from radar.leader_formal_research_production_provider import (
        LeaderFormalResearchProductionSourceStatus,
    )
    from radar.leader_phase6_live_prefreeze import (
        LeaderPhase6PrefrozenInputs,
    )
    from radar.sector_rule_production_collector import (
        SectorRuleProductionFrozenBatch,
    )

    sector_rule = getattr(prefrozen_inputs, "sector_rule", None)
    if (
        type(prefrozen_inputs) is not LeaderPhase6PrefrozenInputs
        or type(sector_rule) is not SectorRuleProductionFrozenBatch
        or sector_rule.source_status
        != LeaderFormalResearchProductionSourceStatus.COMPLETED
    ):
        return _blocked(
            context,
            "sector_state_prefrozen_input_unverified",
        )
    return produce_sector_state_from_runtime(
        context,
        source_batch=sector_rule.source_batch,
        historical_analyses=(
            prefrozen_inputs.sector_historical_analyses
        ),
        approval_record=prefrozen_inputs.threshold_approval_record,
        comparable_time=prefrozen_inputs.sector_comparable_time,
        previous_snapshot=previous_snapshot,
    )
