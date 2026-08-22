"""行业阈值提案审阅、批准留痕及运行时绑定。

本模块只消费已经持久化并通过哈希校验的真实历史汇总。自动校准只能
生成审阅稿；只有显式提供完整八状态策略、批准人和批准时间，才会形成
批准记录。记录保存在项目运行仓，不访问 SQLite，也不改变正式门。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from radar.sector_history_store import (
    DEFAULT_SECTOR_HISTORY_STORE_DIR,
    SectorHistoryStoreReadResult,
    load_latest_sector_history_evidence,
)
from radar.sector_rule_readiness import (
    REQUIRED_STATE_IDS,
    REQUIRED_THRESHOLD_POLICY_FIELDS,
    SECTOR_RULE_VERSION,
    SectorThresholdApprovalEvidence,
)
from radar.sector_rule_runtime_bridge import SectorRuleRuntimeSourceBatch


SECTOR_THRESHOLD_REVIEW_CONTRACT_ID = (
    "radar-sector-threshold-review-v1"
)
SECTOR_THRESHOLD_APPROVAL_RECORD_CONTRACT_ID = (
    "radar-sector-threshold-approval-record-v1"
)
APPROVAL_FILENAME = "threshold-approval.json"
ALLOWED_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})
ALLOWED_FAILURE_BEHAVIORS = frozenset({
    "hold",
    "degrade",
    "invalidate",
    "stop_evaluation",
})
CALIBRATION_METRICS = frozenset({
    "turnoverRatio20d",
    "relativeReturn",
    "persistencePositiveRatio5d",
})


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


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


@dataclass(frozen=True, repr=False)
class SectorThresholdReviewDraft:
    status: str
    reasons: Tuple[str, ...]
    calibration_identity: str
    source_evidence_sha256: str
    published_at: datetime
    rule_version: str
    observation_date_count: int
    train_observation_date_count: int
    holdout_observation_date_count: int
    train_end_date: str
    holdout_start_date: str
    market_regimes: Tuple[str, ...]
    metric_quantiles: Mapping[str, Mapping[str, float]] = field(
        repr=False
    )
    metric_sample_counts: Mapping[str, int] = field(repr=False)
    holdout_metric_sample_counts: Mapping[str, int] = field(repr=False)
    required_state_ids: Tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(REQUIRED_STATE_IDS))
    )
    required_policy_fields: Tuple[str, ...] = field(
        default_factory=lambda: tuple(
            sorted(REQUIRED_THRESHOLD_POLICY_FIELDS)
        )
    )
    contract_id: str = SECTOR_THRESHOLD_REVIEW_CONTRACT_ID
    formal_approval: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "calibrationIdentity": self.calibration_identity,
            "publishedAt": self.published_at.isoformat(),
            "ruleVersion": self.rule_version,
            "observationDateCount": self.observation_date_count,
            "trainObservationDateCount": (
                self.train_observation_date_count
            ),
            "holdoutObservationDateCount": (
                self.holdout_observation_date_count
            ),
            "trainEndDate": self.train_end_date,
            "holdoutStartDate": self.holdout_start_date,
            "marketRegimes": list(self.market_regimes),
            "metricQuantiles": self.metric_quantiles,
            "metricSampleCounts": self.metric_sample_counts,
            "holdoutMetricSampleCounts": (
                self.holdout_metric_sample_counts
            ),
            "requiredStateIds": list(self.required_state_ids),
            "requiredPolicyFields": list(self.required_policy_fields),
            "formalApproval": False,
        }


@dataclass(frozen=True, repr=False)
class SectorThresholdApprovalRecord:
    calibration_identity: str
    source_evidence_sha256: str
    rule_version: str
    threshold_set_id: str
    approval_id: str
    approved_by: str
    approved_at: datetime
    state_policies: Mapping[str, Mapping[str, Any]] = field(repr=False)
    record_sha256: str
    contract_id: str = SECTOR_THRESHOLD_APPROVAL_RECORD_CONTRACT_ID

    def payload_without_hash(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "calibrationIdentity": self.calibration_identity,
            "sourceEvidenceSha256": self.source_evidence_sha256,
            "ruleVersion": self.rule_version,
            "thresholdSetId": self.threshold_set_id,
            "approvalId": self.approval_id,
            "approvedBy": self.approved_by,
            "approvedAt": self.approved_at.isoformat(),
            "statePolicies": self.state_policies,
        }

    def to_payload(self) -> Mapping[str, object]:
        return {
            **self.payload_without_hash(),
            "recordSha256": self.record_sha256,
        }


@dataclass(frozen=True, repr=False)
class SectorThresholdApprovalLoadResult:
    status: str
    reasons: Tuple[str, ...]
    record: Optional[SectorThresholdApprovalRecord] = field(
        default=None,
        repr=False,
    )
    evidence: Optional[SectorThresholdApprovalEvidence] = field(
        default=None,
        repr=False,
    )
    source_batch: Optional[SectorRuleRuntimeSourceBatch] = field(
        default=None,
        repr=False,
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "ruleVersion": (
                self.record.rule_version if self.record else None
            ),
            "thresholdSetId": (
                self.record.threshold_set_id if self.record else None
            ),
            "approvalId": (
                self.record.approval_id if self.record else None
            ),
            "approvedBy": (
                self.record.approved_by if self.record else None
            ),
            "approvedAt": (
                self.record.approved_at.isoformat()
                if self.record else None
            ),
        }


def _failed(reason: str, *, status: str = "failed") \
        -> SectorThresholdApprovalLoadResult:
    return SectorThresholdApprovalLoadResult(
        status=status,
        reasons=(reason,),
    )


def build_sector_threshold_review_draft(
    history_evidence: SectorHistoryStoreReadResult,
) -> SectorThresholdReviewDraft:
    """从已验证历史汇总生成确定性、未批准的审阅稿。"""

    if (
        type(history_evidence) is not SectorHistoryStoreReadResult
        or history_evidence.status != "available"
        or not isinstance(history_evidence.payload, Mapping)
        or not isinstance(history_evidence.evidence_sha256, str)
        or len(history_evidence.evidence_sha256) != 64
        or not _aware(history_evidence.published_at)
    ):
        raise ValueError("sector_threshold_review_history_unverified")
    payload = history_evidence.payload
    analysis = payload.get("analysis")
    calibration = (
        analysis.get("calibrationProposal")
        if isinstance(analysis, Mapping) else None
    )
    if not isinstance(calibration, Mapping):
        raise ValueError("sector_threshold_review_calibration_unverified")
    required = {
        "observationDateCount": int,
        "trainObservationDateCount": int,
        "holdoutObservationDateCount": int,
        "trainEndDate": str,
        "holdoutStartDate": str,
        "marketRegimes": list,
        "metricQuantiles": Mapping,
        "metricSampleCounts": Mapping,
        "holdoutMetricSampleCounts": Mapping,
    }
    if (
        calibration.get("status") != "proposal_ready"
        or calibration.get("formalApproval") is not False
        or any(
            not isinstance(calibration.get(key), expected)
            for key, expected in required.items()
        )
        or calibration["trainObservationDateCount"] <= 0
        or calibration["holdoutObservationDateCount"] <= 0
        or calibration["trainEndDate"] >= calibration["holdoutStartDate"]
        or set(calibration["metricQuantiles"]) != CALIBRATION_METRICS
        or set(calibration["metricSampleCounts"]) != CALIBRATION_METRICS
        or set(calibration["holdoutMetricSampleCounts"])
        != CALIBRATION_METRICS
    ):
        raise ValueError("sector_threshold_review_calibration_unverified")
    identity_payload = {
        "contractId": SECTOR_THRESHOLD_REVIEW_CONTRACT_ID,
        "sourceEvidenceSha256": history_evidence.evidence_sha256,
        "ruleVersion": SECTOR_RULE_VERSION,
        "requestIdentity": payload.get("requestIdentity"),
        "calibrationProposal": calibration,
    }
    return SectorThresholdReviewDraft(
        status="review_ready",
        reasons=(),
        calibration_identity=_digest(identity_payload),
        source_evidence_sha256=history_evidence.evidence_sha256,
        published_at=history_evidence.published_at,
        rule_version=SECTOR_RULE_VERSION,
        observation_date_count=calibration["observationDateCount"],
        train_observation_date_count=(
            calibration["trainObservationDateCount"]
        ),
        holdout_observation_date_count=(
            calibration["holdoutObservationDateCount"]
        ),
        train_end_date=calibration["trainEndDate"],
        holdout_start_date=calibration["holdoutStartDate"],
        market_regimes=tuple(calibration["marketRegimes"]),
        metric_quantiles=calibration["metricQuantiles"],
        metric_sample_counts=calibration["metricSampleCounts"],
        holdout_metric_sample_counts=(
            calibration["holdoutMetricSampleCounts"]
        ),
    )


def _validate_condition(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "metric", "operator", "value",
    }:
        return False
    numeric = value.get("value")
    return bool(
        value.get("metric") in CALIBRATION_METRICS
        and value.get("operator") in ALLOWED_OPERATORS
        and not isinstance(numeric, bool)
        and isinstance(numeric, (int, float))
        and math.isfinite(float(numeric))
    )


def _validate_state_policies(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != REQUIRED_STATE_IDS:
        return False
    for policy in value.values():
        if (
            not isinstance(policy, Mapping)
            or set(policy) != REQUIRED_THRESHOLD_POLICY_FIELDS
            or not all(
                _validate_condition(policy.get(key))
                for key in ("entry", "hold", "exit")
            )
            or any(
                isinstance(policy.get(key), bool)
                or not isinstance(policy.get(key), int)
                or policy.get(key) < minimum
                for key, minimum in (
                    ("consecutive_observations", 1),
                    ("minimum_hold_time", 0),
                    ("cooldown", 0),
                )
            )
            or policy.get("data_failure_behavior")
            not in ALLOWED_FAILURE_BEHAVIORS
        ):
            return False
    return True


def _threshold_set_id(
    calibration_identity: str,
    state_policies: Mapping[str, Mapping[str, Any]],
) -> str:
    return "radar-sector-threshold-set-v1:" + _digest({
        "calibrationIdentity": calibration_identity,
        "statePolicies": state_policies,
    })


def _approval_id(
    *,
    threshold_set_id: str,
    approved_by: str,
    approved_at: datetime,
) -> str:
    return "radar-sector-threshold-approval-v1:" + _digest({
        "thresholdSetId": threshold_set_id,
        "approvedBy": approved_by,
        "approvedAt": approved_at.isoformat(),
    })


def approve_sector_threshold_review(
    draft: SectorThresholdReviewDraft,
    *,
    state_policies: Mapping[str, Mapping[str, Any]],
    approved_by: str,
    approved_at: datetime,
    observed_at: Optional[datetime] = None,
) -> SectorThresholdApprovalRecord:
    """显式批准完整策略；本函数不会自动选择任何阈值。"""

    if (
        type(draft) is not SectorThresholdReviewDraft
        or draft.status != "review_ready"
        or draft.formal_approval is not False
    ):
        raise ValueError("sector_threshold_review_unverified")
    if not _validate_state_policies(state_policies):
        raise ValueError("sector_threshold_review_scope_incomplete")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("sector_threshold_review_approver_missing")
    if not _aware(approved_at):
        raise ValueError("sector_threshold_review_approved_at_unverified")
    effective_observed_at = observed_at or datetime.now(
        approved_at.tzinfo
    )
    if not _aware(effective_observed_at):
        raise ValueError("sector_threshold_review_observed_at_unverified")
    if approved_at < draft.published_at:
        raise ValueError("sector_threshold_review_approval_before_review")
    if approved_at > effective_observed_at:
        raise ValueError("sector_threshold_review_approval_in_future")
    threshold_set_id = _threshold_set_id(
        draft.calibration_identity,
        state_policies,
    )
    approval_id = _approval_id(
        threshold_set_id=threshold_set_id,
        approved_by=approved_by.strip(),
        approved_at=approved_at,
    )
    base = {
        "contractId": SECTOR_THRESHOLD_APPROVAL_RECORD_CONTRACT_ID,
        "calibrationIdentity": draft.calibration_identity,
        "sourceEvidenceSha256": draft.source_evidence_sha256,
        "ruleVersion": draft.rule_version,
        "thresholdSetId": threshold_set_id,
        "approvalId": approval_id,
        "approvedBy": approved_by.strip(),
        "approvedAt": approved_at.isoformat(),
        "statePolicies": state_policies,
    }
    return SectorThresholdApprovalRecord(
        calibration_identity=draft.calibration_identity,
        source_evidence_sha256=draft.source_evidence_sha256,
        rule_version=draft.rule_version,
        threshold_set_id=threshold_set_id,
        approval_id=approval_id,
        approved_by=approved_by.strip(),
        approved_at=approved_at,
        state_policies=state_policies,
        record_sha256=_digest(base),
    )


def publish_sector_threshold_approval(
    record: SectorThresholdApprovalRecord,
    *,
    store_dir: Path = DEFAULT_SECTOR_HISTORY_STORE_DIR,
) -> None:
    if (
        type(record) is not SectorThresholdApprovalRecord
        or record.record_sha256 != _digest(record.payload_without_hash())
        or not _validate_state_policies(record.state_policies)
    ):
        raise ValueError("sector_threshold_approval_record_unverified")
    root = Path(store_dir).expanduser().resolve()
    _write_atomic(root / APPROVAL_FILENAME, record.to_payload())


def load_sector_threshold_approval(
    *,
    store_dir: Path = DEFAULT_SECTOR_HISTORY_STORE_DIR,
    history_evidence: Optional[SectorHistoryStoreReadResult] = None,
) -> SectorThresholdApprovalLoadResult:
    root = Path(store_dir).expanduser().resolve()
    history = history_evidence or load_latest_sector_history_evidence(
        store_dir=root
    )
    try:
        draft = build_sector_threshold_review_draft(history)
    except ValueError:
        return _failed("sector_threshold_review_history_unverified")
    path = root / APPROVAL_FILENAME
    if not path.is_file():
        return _failed(
            "sector_threshold_approval_snapshot_missing",
            status="not_ready",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        approved_at = datetime.fromisoformat(payload.get("approvedAt"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _failed("sector_threshold_approval_record_unverified")
    if (
        not isinstance(payload, Mapping)
        or payload.get("contractId")
        != SECTOR_THRESHOLD_APPROVAL_RECORD_CONTRACT_ID
        or not _aware(approved_at)
        or not isinstance(payload.get("recordSha256"), str)
        or not isinstance(payload.get("approvedBy"), str)
        or not payload.get("approvedBy").strip()
        or not _validate_state_policies(payload.get("statePolicies"))
    ):
        return _failed("sector_threshold_approval_record_unverified")
    base = dict(payload)
    expected_hash = base.pop("recordSha256", None)
    if expected_hash != _digest(base):
        return _failed("sector_threshold_approval_hash_mismatch")
    if (
        payload.get("calibrationIdentity") != draft.calibration_identity
        or payload.get("sourceEvidenceSha256")
        != draft.source_evidence_sha256
    ):
        return _failed(
            "sector_threshold_approval_calibration_mismatch",
            status="not_ready",
        )
    state_policies = payload["statePolicies"]
    threshold_set_id = _threshold_set_id(
        draft.calibration_identity,
        state_policies,
    )
    approval_id = _approval_id(
        threshold_set_id=threshold_set_id,
        approved_by=payload["approvedBy"],
        approved_at=approved_at,
    )
    if (
        payload.get("ruleVersion") != draft.rule_version
        or payload.get("thresholdSetId") != threshold_set_id
        or payload.get("approvalId") != approval_id
    ):
        return _failed("sector_threshold_approval_identity_mismatch")
    record = SectorThresholdApprovalRecord(
        calibration_identity=draft.calibration_identity,
        source_evidence_sha256=draft.source_evidence_sha256,
        rule_version=draft.rule_version,
        threshold_set_id=threshold_set_id,
        approval_id=approval_id,
        approved_by=payload["approvedBy"],
        approved_at=approved_at,
        state_policies=state_policies,
        record_sha256=expected_hash,
    )
    evidence = SectorThresholdApprovalEvidence(
        rule_version=record.rule_version,
        threshold_set_id=record.threshold_set_id,
        approval_id=record.approval_id,
        approved_at=record.approved_at,
        state_ids=tuple(sorted(record.state_policies)),
        policy_fields=tuple(sorted(REQUIRED_THRESHOLD_POLICY_FIELDS)),
    )
    return SectorThresholdApprovalLoadResult(
        status="approved",
        reasons=(),
        record=record,
        evidence=evidence,
    )


def bind_latest_sector_threshold_approval(
    source_batch: SectorRuleRuntimeSourceBatch,
    *,
    store_dir: Path = DEFAULT_SECTOR_HISTORY_STORE_DIR,
    history_evidence: Optional[SectorHistoryStoreReadResult] = None,
) -> SectorThresholdApprovalLoadResult:
    if type(source_batch) is not SectorRuleRuntimeSourceBatch:
        return _failed("sector_threshold_runtime_source_unverified")
    loaded = load_sector_threshold_approval(
        store_dir=store_dir,
        history_evidence=history_evidence,
    )
    if loaded.status != "approved" or loaded.evidence is None:
        return loaded
    if source_batch.rule_version != loaded.evidence.rule_version:
        return _failed("sector_threshold_approval_rule_version_mismatch")
    return replace(
        loaded,
        source_batch=replace(
            source_batch,
            threshold_approval_evidence=loaded.evidence,
        ),
    )
