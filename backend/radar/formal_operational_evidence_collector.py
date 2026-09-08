"""阶段10运营证据自动汇总器。

只接受 Task 1 内容寻址检查点、正式影子台账仓和冻结静态策略；不接受调用方
自报的回执全集、重入次数、安全结论或默认性能阈值。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from radar.formal_operational_checks import (
    OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION,
    OperationalCollectorInputRef,
    OperationalChecksInput,
    PerformanceInput,
    PerformancePolicy,
    RadarFormalOperationalChecks,
    SecurityFinding,
    SecurityInput,
    StaticAsset,
    StaticAssetsInput,
    _build_operational_checks,
    _COLLECTOR_BUILD_CAPABILITY,
)
from radar.formal_shadow_calendar import (
    OfficialSseCalendarProvider,
    load_official_sse_calendar_evidence,
)
from radar.formal_shadow_input_bundle import (
    COLLECTION_POLICY_FILENAME,
    ETF_ADMISSION_FILENAME,
    RECEIPT_FILENAME,
    SOURCE_ARTIFACT_FILENAME,
    TREND_SUPPORTING_FILENAME,
)
from radar.formal_shadow_ledger import FormalShadowLedgerV2, FormalShadowObservation
from radar.formal_shadow_ledger_store import (
    _open_directory,
    _open_root_directory,
    _read_bytes_at,
    _verify_directory_entry,
    _verify_regular_entry,
    _verify_root_path,
    load_latest_formal_shadow_ledger,
)
from radar.formal_shadow_observation_collector import (
    FormalShadowRunReceipt,
    adapt_formal_shadow_observation,
    load_formal_shadow_run_receipt,
)
from radar.strict_json import strict_json_loads
from radar.stage10_live_collection import (
    CHECKPOINT_POINTER_NAME,
    PRIVATE_TMP,
    Stage10FrozenSchedule as Stage10ScheduleManifest,
    Stage10FrozenScheduleSlot as Stage10ScheduleSlot,
    Stage10LiveCollectionResult,
    _load_checkpoint_by_sha,
    _snapshot_input,
    Stage10ScheduleSourceEvidence as ScheduleSourceEvidence,
)


OPERATIONAL_EVIDENCE_INPUT_CONTRACT_VERSION = (
    "radar-formal-operational-evidence-input-v1"
)
_PERFORMANCE_POLICY_VERSION = "operational-performance-policy-v1"
_SECURITY_POLICY_VERSION = "radar-formal-security-policy-v1"
_STATIC_POLICY_VERSION = "radar-formal-static-assets-policy-v1"
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_STATIC_BYTES = 2 * 1024 * 1024
_MODULES = ("trendRotation", "leaderObservation", "etfObservation")
_DRY_PREFLIGHT_STATES = {
    "dry_attempt_contended",
    "dry_calendar_unverified",
    "dry_confirmation_required",
    "dry_not_continuous_session",
    "dry_not_trading",
}
_PROTECTED_PARTS = {".git", "node_modules", "venv"}
_SENSITIVE = re.compile(
    rb"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]"
)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _policy_sha(value: BaseModel) -> str:
    payload = value.model_dump(mode="json", by_alias=True)
    expected = payload.pop("policySha256", None)
    actual = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if expected != actual:
        raise ValueError("operational_evidence_policy_sha_mismatch")
    return actual


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


def _relative_path(value: str) -> str:
    if not value or value.strip() != value or "\\" in value:
        raise ValueError("operational_evidence_path_invalid")
    # PurePosixPath 会静默归一化双斜杠、`.` 段和尾斜杠；这些都不是
    # 内容寻址契约允许的原始表示。
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("operational_evidence_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("operational_evidence_path_invalid")
    if (
        any(part in _PROTECTED_PARTS or part.startswith(".env") for part in path.parts)
        or any(path.parts[index:index + 2] == ("backend", "data") for index in range(max(0, len(path.parts) - 1)))
    ):
        raise ValueError("operational_evidence_path_forbidden")
    return path.as_posix()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("operational_evidence_time_timezone_required")
    return value


class LedgerStoreBinding(_FrozenModel):
    relative_path: str = Field(alias="relativePath")
    expected_content_sha256: str = Field(
        alias="expectedContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("relative_path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _relative_path(value)


class AttemptCheckpointBinding(_FrozenModel):
    attempt_root_relative_path: str = Field(alias="attemptRootRelativePath")
    checkpoint_sha256: str = Field(
        alias="checkpointSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("attempt_root_relative_path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _relative_path(value)


class PolicyReference(_FrozenModel):
    """策略文件的外部内容寻址引用。

    expectedContentSha256 不属于策略文件本身，因此不会让文件内的
    policySha256 成为唯一的自签证明。
    """

    path: str
    expected_content_sha256: str = Field(
        alias="expectedContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _relative_path(value)


class AttemptUniverseStoreBinding(LedgerStoreBinding):
    pass


class ScheduleManifestStoreBinding(LedgerStoreBinding):
    pass


class AttemptModuleOutcomes(_FrozenModel):
    stage9: Literal["success", "failed", "contended", "not_attempted"]
    trend_rotation: Literal["success", "failed", "contended", "not_attempted"] = Field(alias="trendRotation")
    leader_observation: Literal["success", "failed", "contended", "not_attempted"] = Field(alias="leaderObservation")
    etf_observation: Literal["success", "failed", "contended", "not_attempted"] = Field(alias="etfObservation")


class AttemptUniverseEntry(_FrozenModel):
    attempt_id: str = Field(alias="attemptId", min_length=1)
    slot_id: str = Field(
        alias="slotId",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    trade_date: date = Field(alias="tradeDate")
    schedule_slot: datetime = Field(alias="scheduleSlot")
    attempt_root_relative_path: str = Field(alias="attemptRootRelativePath")
    checkpoint_sha256: str = Field(alias="checkpointSha256", pattern=r"^[0-9a-f]{64}$")
    expected_run_id: Optional[str] = Field(default=None, alias="expectedRunId", min_length=1)
    expected_lock_state: Literal["acquired", "contended", "not_attempted"] = Field(alias="expectedLockState")
    module_outcomes: AttemptModuleOutcomes = Field(alias="moduleOutcomes")

    @field_validator("attempt_root_relative_path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("schedule_slot")
    @classmethod
    def schedule_slot_is_aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def schedule_matches_trade_date(self) -> "AttemptUniverseEntry":
        if self.schedule_slot.astimezone(_SHANGHAI).date() != self.trade_date:
            raise ValueError("operational_evidence_schedule_trade_date_mismatch")
        return self


class GlobalLockEvidence(_FrozenModel):
    contract_version: Literal["radar-stage10-global-lock-evidence-v1"] = Field(alias="contractVersion")
    lock_root_name: Literal[".radar-stage10-single-live-collection-lock-root"] = Field(alias="lockRootName")
    lock_name: Literal["collection.lock"] = Field(alias="lockName")
    acquisition_mode: Literal["exclusive_nonblocking"] = Field(alias="acquisitionMode")
    identity_check_mode: Literal["before_each_external_runner"] = Field(alias="identityCheckMode")
    covered_attempt_ids: Tuple[str, ...] = Field(alias="coveredAttemptIds", min_length=1)


class AttemptUniverse(_FrozenModel):
    contract_version: Literal["radar-formal-operational-attempt-universe-v1"] = Field(alias="contractVersion")
    universe_id: str = Field(alias="universeId", min_length=1)
    evidence_scope: Literal["live", "rehearsal"] = Field(alias="evidenceScope")
    schedule_manifest_sha256: str = Field(
        alias="scheduleManifestSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_attempts: Tuple[AttemptUniverseEntry, ...] = Field(alias="expectedAttempts", min_length=1)
    global_lock_evidence: GlobalLockEvidence = Field(alias="globalLockEvidence")

    @model_validator(mode="after")
    def universe_is_exact(self) -> "AttemptUniverse":
        ids = tuple(item.attempt_id for item in self.expected_attempts)
        roots = tuple(item.attempt_root_relative_path for item in self.expected_attempts)
        if (
            len(ids) != len(set(ids))
            or len(roots) != len(set(roots))
        ):
            raise ValueError("operational_evidence_attempt_universe_duplicate")
        if (
            len(self.global_lock_evidence.covered_attempt_ids)
            != len(set(self.global_lock_evidence.covered_attempt_ids))
            or set(self.global_lock_evidence.covered_attempt_ids) != set(ids)
        ):
            raise ValueError("operational_evidence_lock_universe_mismatch")
        return self


class AssetBinding(_FrozenModel):
    role: str = Field(min_length=1)
    path: str
    version: str = Field(min_length=1)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")

    @field_validator("role", "version")
    @classmethod
    def text_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("operational_evidence_identity_blank")
        return value

    @field_validator("path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _relative_path(value)


class FrozenPerformancePolicy(PerformancePolicy):
    contract_version: Literal[_PERFORMANCE_POLICY_VERSION] = Field(alias="contractVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    evidence_scope: Literal["live", "rehearsal"] = Field(alias="evidenceScope")
    schedule_interval_ms: StrictInt = Field(alias="scheduleIntervalMs", ge=1)
    min_sample_count: StrictInt = Field(alias="minSampleCount", ge=1)
    min_coverage: StrictFloat = Field(alias="minCoverage", ge=0, le=1)
    max_p95_duration_ms: StrictInt = Field(alias="maxP95DurationMs", ge=0)
    max_failed_rate: StrictFloat = Field(alias="maxFailedRate", ge=0, le=1)
    max_lock_contention_count: StrictInt = Field(alias="maxLockContentionCount", ge=0)

    @model_validator(mode="after")
    def identity_is_frozen(self) -> "FrozenPerformancePolicy":
        _policy_sha(self)
        return self


class FrozenSecurityPolicy(_FrozenModel):
    contract_version: Literal[_SECURITY_POLICY_VERSION] = Field(alias="contractVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    evidence_scope: Literal["static_only"] = Field(alias="evidenceScope")
    required_assets: Tuple[AssetBinding, ...] = Field(alias="requiredAssets", min_length=2)

    @model_validator(mode="after")
    def identity_is_frozen(self) -> "FrozenSecurityPolicy":
        roles = tuple(item.role for item in self.required_assets)
        if len(roles) != len(set(roles)):
            raise ValueError("operational_evidence_security_roles_invalid")
        required_paths = {
            "radar_config_source": "backend/radar/config.py",
            "formal_execution_guard_source": "backend/radar/formal_execution_guard.py",
        }
        by_role = {item.role: item.path for item in self.required_assets}
        if any(by_role.get(role) != path for role, path in required_paths.items()):
            raise ValueError("operational_evidence_security_roles_incomplete")
        _policy_sha(self)
        return self


class RequiredStaticAsset(AssetBinding):
    gate: Literal["rollback", "runbook"]


class FrozenStaticAssetPolicy(_FrozenModel):
    contract_version: Literal[_STATIC_POLICY_VERSION] = Field(alias="contractVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    evidence_scope: Literal["static_only"] = Field(alias="evidenceScope")
    required_assets: Tuple[RequiredStaticAsset, ...] = Field(alias="requiredAssets", min_length=2)

    @model_validator(mode="after")
    def assets_and_identity_are_frozen(self) -> "FrozenStaticAssetPolicy":
        roles = tuple(item.role for item in self.required_assets)
        identities = tuple((item.gate, item.path) for item in self.required_assets)
        if len(roles) != len(set(roles)):
            raise ValueError("operational_evidence_asset_role_reused")
        if len(identities) != len(set(identities)):
            raise ValueError("operational_evidence_static_asset_duplicate")
        if {item.gate for item in self.required_assets} != {"rollback", "runbook"}:
            raise ValueError("operational_evidence_static_roles_incomplete")
        _policy_sha(self)
        return self


class AttemptPerformanceEvidence(_FrozenModel):
    attempt_id: str = Field(alias="attemptId", min_length=1)
    run_id: Optional[str] = Field(default=None, alias="runId", min_length=1)
    trade_date: date = Field(alias="tradeDate")
    schedule_slot: Optional[datetime] = Field(default=None, alias="scheduleSlot")
    duration_ms: StrictInt = Field(alias="durationMs", ge=0)
    expected_count: StrictInt = Field(alias="expectedCount", ge=0)
    observed_count: StrictInt = Field(alias="observedCount", ge=0)
    unsuccessful: StrictBool
    lock_contention_count: StrictInt = Field(alias="lockContentionCount", ge=0)
    execution_complete: StrictBool = Field(default=True, alias="executionComplete")
    attempt_root_relative_path: Optional[str] = Field(default=None, alias="attemptRootRelativePath")
    checkpoint_sha256: Optional[str] = Field(default=None, alias="checkpointSha256", pattern=r"^[0-9a-f]{64}$")
    lock_state: Optional[Literal["acquired", "contended", "not_attempted"]] = Field(default=None, alias="lockState")
    module_outcomes: Optional[AttemptModuleOutcomes] = Field(default=None, alias="moduleOutcomes")

    @field_validator("attempt_root_relative_path")
    @classmethod
    def optional_path_is_safe(cls, value: Optional[str]) -> Optional[str]:
        return _relative_path(value) if value is not None else None

    @field_validator("schedule_slot")
    @classmethod
    def optional_schedule_is_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def counts_are_possible(self) -> "AttemptPerformanceEvidence":
        if self.observed_count > self.expected_count:
            raise ValueError("operational_evidence_attempt_counts_invalid")
        return self


class OperationalEvidenceInput(_FrozenModel):
    contract_version: Literal[OPERATIONAL_EVIDENCE_INPUT_CONTRACT_VERSION] = Field(alias="contractVersion")
    subject_id: str = Field(alias="subjectId", min_length=1)
    checked_at: datetime = Field(alias="checkedAt")
    ledger_store: LedgerStoreBinding = Field(alias="ledgerStore")
    attempt_universe: Optional[AttemptUniverseStoreBinding] = Field(default=None, alias="attemptUniverseStore")
    schedule_manifest_store: Optional[ScheduleManifestStoreBinding] = Field(
        default=None,
        alias="scheduleManifestStore",
    )
    attempts: Tuple[AttemptCheckpointBinding, ...] = ()
    performance_policy: Optional[PolicyReference] = Field(default=None, alias="performancePolicy")
    security_policy: Optional[PolicyReference] = Field(default=None, alias="securityPolicy")
    known_security_findings: Tuple[SecurityFinding, ...] = Field(default_factory=tuple, alias="knownSecurityFindings")
    static_asset_policy: Optional[PolicyReference] = Field(default=None, alias="staticAssetPolicy")

    @field_validator("checked_at")
    @classmethod
    def checked_time_is_aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def references_are_unique(self) -> "OperationalEvidenceInput":
        roots = tuple(item.attempt_root_relative_path for item in self.attempts)
        if len(roots) != len(set(roots)):
            raise ValueError("operational_evidence_attempt_reference_duplicate")
        if (self.attempt_universe is None) != (self.schedule_manifest_store is None):
            raise ValueError("operational_evidence_schedule_binding_incomplete")
        return self


def _nearest_rank(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.5)))
    return ordered[index]


def derive_performance_input(
    *,
    attempts: Sequence[AttemptPerformanceEvidence],
    policy: Optional[FrozenPerformancePolicy],
    reentry_proven: bool,
    policy_validation_error: Optional[str] = None,
    evidence_scope: Optional[Literal["live", "rehearsal"]] = None,
) -> PerformanceInput:
    """按唯一完整 attempt 聚合；覆盖率使用总观察数/总期望数。"""
    identities = tuple(item.attempt_id for item in attempts)
    if len(identities) != len(set(identities)):
        raise ValueError("operational_evidence_attempt_duplicate")
    runtime_identities = tuple(
        (item.run_id, item.trade_date) for item in attempts if item.run_id is not None
    )
    if len(runtime_identities) != len(set(runtime_identities)):
        raise ValueError("operational_evidence_runtime_duplicate")
    durations = tuple(item.duration_ms for item in attempts)
    expected = sum(item.expected_count for item in attempts)
    observed = sum(item.observed_count for item in attempts)
    complete_proof = bool(attempts) and reentry_proven
    successful_dates = tuple(
        item.trade_date for item in attempts if not item.unsuccessful
    )
    reentry_count = (
        len(successful_dates) - len(set(successful_dates))
        if reentry_proven else None
    )
    if evidence_scope is not None and policy is not None:
        scope = (
            "live"
            if evidence_scope == "live" and policy.evidence_scope == "live"
            else "rehearsal"
        )
    else:
        scope = evidence_scope or (policy.evidence_scope if policy is not None else None)
    return PerformanceInput(
        sampleCount=len(attempts),
        p50DurationMs=_nearest_rank(durations, 0.50),
        p95DurationMs=_nearest_rank(durations, 0.95),
        maxDurationMs=max(durations, default=0),
        scheduleIntervalMs=(policy.schedule_interval_ms if policy else 1),
        lockContentionCount=sum(item.lock_contention_count for item in attempts),
        reentryCount=reentry_count,
        failedCount=sum(1 for item in attempts if item.unsuccessful),
        coverage=(observed / expected if expected else 0.0),
        policy=policy,
        automaticCollection=True,
        reentryProofComplete=complete_proof,
        executionEvidenceComplete=(bool(attempts) and all(item.execution_complete for item in attempts)),
        evidenceScope=scope,
        policyValidationError=policy_validation_error,
    )


def _validated_root(root: Path, *, project_root: Optional[Path] = None) -> Path:
    if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
        raise ValueError("operational_evidence_root_invalid")
    try:
        metadata = root.lstat()
    except OSError as error:
        raise ValueError("operational_evidence_root_invalid") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("operational_evidence_root_invalid")
    if root != PRIVATE_TMP and PRIVATE_TMP not in root.parents:
        if project_root is None or root != project_root:
            raise ValueError("operational_evidence_root_forbidden")
    return root


def _read_relative_bytes(root: Path, relative_path: str, *, maximum: int) -> bytes:
    """逐段保留目录 FD 并在读取后反向复验祖先，拒绝链接和换件。"""
    parts = PurePosixPath(_relative_path(relative_path)).parts
    root_fd: Optional[int] = None
    directories: list[tuple[int, str, int]] = []
    try:
        root_fd = _open_root_directory(root, create_missing=False)
        _verify_root_path(root, root_fd)
        current = root_fd
        for part in parts[:-1]:
            child = _open_directory(part, dir_fd=current)
            _verify_directory_entry(current, part, child)
            directories.append((current, part, child))
            current = child
        payload, metadata = _read_bytes_at(current, parts[-1], max_bytes=maximum)
        _verify_regular_entry(current, parts[-1], metadata)
        for parent, name, child in reversed(directories):
            _verify_directory_entry(parent, name, child)
        _verify_root_path(root, root_fd)
        return payload
    except FileNotFoundError as error:
        raise ValueError("operational_evidence_asset_missing") from error
    except (OSError, TypeError, ValueError) as error:
        code = str(error) if str(error).startswith("operational_evidence_") else "operational_evidence_asset_unverified"
        raise ValueError(code) from error
    finally:
        for _parent, _name, child in reversed(directories):
            os.close(child)
        if root_fd is not None:
            os.close(root_fd)


def _read_bound_asset(root: Path, asset: AssetBinding, *, maximum: int = _MAX_STATIC_BYTES) -> bytes:
    payload = _read_relative_bytes(root, asset.path, maximum=maximum)
    if hashlib.sha256(payload).hexdigest() != asset.content_sha256:
        raise ValueError("operational_evidence_asset_hash_mismatch")
    return payload


def _strict_json_object(payload: bytes) -> Mapping[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("operational_evidence_json_invalid")

    def unique_object(pairs: Sequence[tuple[str, Any]]) -> Mapping[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("operational_evidence_json_invalid")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("operational_evidence_json_invalid") from error
    if not isinstance(parsed, dict):
        raise ValueError("operational_evidence_json_invalid")
    return parsed


def load_frozen_policy(
    asset_root: Path,
    reference: PolicyReference,
    model_type: type[BaseModel],
) -> BaseModel:
    """用输入中独立的期望 SHA 验证策略原文，再校验策略内部身份。"""
    payload = _read_relative_bytes(asset_root, reference.path, maximum=_MAX_JSON_BYTES)
    if hashlib.sha256(payload).hexdigest() != reference.expected_content_sha256:
        raise ValueError("operational_evidence_asset_hash_mismatch")
    try:
        return model_type.model_validate(_strict_json_object(payload))
    except (TypeError, ValueError) as error:
        raise ValueError("operational_evidence_policy_unverified") from error


def load_attempt_universe_store(
    snapshot_root: Path,
    binding: AttemptUniverseStoreBinding,
) -> AttemptUniverse:
    root_fd: Optional[int] = None
    directories: list[tuple[int, str, int]] = []
    manifests_fd: Optional[int] = None
    reports_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(snapshot_root, create_missing=False)
        _verify_root_path(snapshot_root, root_fd)
        current_fd = root_fd
        for part in PurePosixPath(binding.relative_path).parts:
            child_fd = _open_directory(part, dir_fd=current_fd)
            _verify_directory_entry(current_fd, part, child_fd)
            directories.append((current_fd, part, child_fd))
            current_fd = child_fd
        store_fd = current_fd
        latest_raw, latest_metadata = _read_bytes_at(
            store_fd,
            "latest.json",
            max_bytes=16 * 1024,
        )
        latest = _strict_json_object(latest_raw)
        if set(latest) != {"contractId", "manifestSha256", "manifestRelativePath"}:
            raise ValueError
        manifest_sha = latest["manifestSha256"]
        manifest_relative = latest["manifestRelativePath"]
        if (
            latest["contractId"] != "radar-formal-operational-attempt-universe-ref-v1"
            or not isinstance(manifest_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is None
            or manifest_relative != f"manifests/{manifest_sha}.json"
        ):
            raise ValueError
        manifests_fd = _open_directory("manifests", dir_fd=store_fd)
        _verify_directory_entry(store_fd, "manifests", manifests_fd)
        manifest_raw, manifest_metadata = _read_bytes_at(
            manifests_fd,
            f"{manifest_sha}.json",
            max_bytes=64 * 1024,
        )
        if hashlib.sha256(manifest_raw).hexdigest() != manifest_sha:
            raise ValueError
        manifest = _strict_json_object(manifest_raw)
        if set(manifest) != {"contractId", "contractVersion", "contentSha256", "reportRelativePath"}:
            raise ValueError
        report_sha = manifest["contentSha256"]
        report_relative = manifest["reportRelativePath"]
        if (
            manifest["contractId"] != "radar-formal-operational-attempt-universe-manifest-v1"
            or manifest["contractVersion"] != "radar-formal-operational-attempt-universe-v1"
            or not isinstance(report_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", report_sha) is None
            or report_relative != f"reports/{report_sha}.json"
        ):
            raise ValueError
        reports_fd = _open_directory("reports", dir_fd=store_fd)
        _verify_directory_entry(store_fd, "reports", reports_fd)
        payload, report_metadata = _read_bytes_at(
            reports_fd,
            f"{report_sha}.json",
            max_bytes=_MAX_JSON_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != report_sha:
            raise ValueError
        if report_sha != binding.expected_content_sha256:
            raise ValueError("operational_evidence_asset_hash_mismatch")
        universe = AttemptUniverse.model_validate(_strict_json_object(payload))
        _verify_regular_entry(reports_fd, f"{report_sha}.json", report_metadata)
        _verify_directory_entry(store_fd, "reports", reports_fd)
        _verify_regular_entry(manifests_fd, f"{manifest_sha}.json", manifest_metadata)
        _verify_directory_entry(store_fd, "manifests", manifests_fd)
        _verify_regular_entry(store_fd, "latest.json", latest_metadata)
        latest_again, latest_again_metadata = _read_bytes_at(
            store_fd,
            "latest.json",
            max_bytes=16 * 1024,
        )
        if latest_again != latest_raw:
            raise ValueError
        _verify_regular_entry(store_fd, "latest.json", latest_again_metadata)
        for parent_fd, name, child_fd in reversed(directories):
            _verify_directory_entry(parent_fd, name, child_fd)
        _verify_root_path(snapshot_root, root_fd)
        return universe
    except (OSError, KeyError, TypeError, ValueError) as error:
        if str(error) == "operational_evidence_asset_hash_mismatch":
            raise
        raise ValueError("operational_evidence_attempt_universe_unverified") from error
    finally:
        if reports_fd is not None:
            os.close(reports_fd)
        if manifests_fd is not None:
            os.close(manifests_fd)
        for _parent_fd, _name, child_fd in reversed(directories):
            os.close(child_fd)
        if root_fd is not None:
            os.close(root_fd)


def validate_attempt_universe_bindings(
    universe: AttemptUniverse,
    bindings: Sequence[AttemptCheckpointBinding],
) -> None:
    expected = {
        (item.attempt_root_relative_path, item.checkpoint_sha256)
        for item in universe.expected_attempts
    }
    actual = {
        (item.attempt_root_relative_path, item.checkpoint_sha256)
        for item in bindings
    }
    if len(actual) != len(bindings) or actual != expected:
        raise ValueError("operational_evidence_attempt_universe_mismatch")


def load_schedule_manifest_store(
    snapshot_root: Path,
    binding: ScheduleManifestStoreBinding,
) -> Stage10ScheduleManifest:
    """以固定目录 FD 读取独立 schedule manifest，并复验 latest 未漂移。"""
    root_fd: Optional[int] = None
    directories: list[tuple[int, str, int]] = []
    manifests_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(snapshot_root, create_missing=False)
        _verify_root_path(snapshot_root, root_fd)
        current_fd = root_fd
        for part in PurePosixPath(binding.relative_path).parts:
            child_fd = _open_directory(part, dir_fd=current_fd)
            _verify_directory_entry(current_fd, part, child_fd)
            directories.append((current_fd, part, child_fd))
            current_fd = child_fd
        store_fd = current_fd
        latest_raw, latest_metadata = _read_bytes_at(
            store_fd,
            "latest.json",
            max_bytes=16 * 1024,
        )
        latest = _strict_json_object(latest_raw)
        if set(latest) != {"contractId", "contentSha256", "relativePath"}:
            raise ValueError
        latest_sha = latest["contentSha256"]
        if (
            latest["contractId"] != "radar-stage10-schedule-manifest-ref-v1"
            or not isinstance(latest_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", latest_sha) is None
            or latest["relativePath"] != f"manifests/{latest_sha}.json"
        ):
            raise ValueError
        # ``latest`` 只用于证明这是一个结构完整且读取期间未漂移的
        # 调度仓。历史运营证据已显式绑定不可变 SHA，必须按该
        # SHA 重放；否则发布后续调度会使仍存在的旧证据无法验证。
        content_sha = binding.expected_content_sha256
        manifests_fd = _open_directory("manifests", dir_fd=store_fd)
        _verify_directory_entry(store_fd, "manifests", manifests_fd)
        payload, manifest_metadata = _read_bytes_at(
            manifests_fd,
            f"{content_sha}.json",
            max_bytes=_MAX_JSON_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != content_sha:
            raise ValueError
        schedule = Stage10ScheduleManifest.model_validate(
            _strict_json_object(payload),
        )
        _verify_regular_entry(
            manifests_fd,
            f"{content_sha}.json",
            manifest_metadata,
        )
        _verify_directory_entry(store_fd, "manifests", manifests_fd)
        _verify_regular_entry(store_fd, "latest.json", latest_metadata)
        latest_again, latest_again_metadata = _read_bytes_at(
            store_fd,
            "latest.json",
            max_bytes=16 * 1024,
        )
        if latest_again != latest_raw:
            raise ValueError
        _verify_regular_entry(store_fd, "latest.json", latest_again_metadata)
        for parent_fd, name, child_fd in reversed(directories):
            _verify_directory_entry(parent_fd, name, child_fd)
        _verify_root_path(snapshot_root, root_fd)
        return schedule
    except (OSError, KeyError, TypeError, ValueError) as error:
        if str(error) == "operational_evidence_asset_hash_mismatch":
            raise
        raise ValueError("operational_evidence_schedule_manifest_unverified") from error
    finally:
        if manifests_fd is not None:
            os.close(manifests_fd)
        for _parent_fd, _name, child_fd in reversed(directories):
            os.close(child_fd)
        if root_fd is not None:
            os.close(root_fd)


def validate_schedule_universe_bindings(
    schedule: Stage10ScheduleManifest,
    schedule_sha256: str,
    universe: AttemptUniverse,
    calendar: OfficialSseCalendarProvider,
) -> None:
    if (
        universe.schedule_manifest_sha256 != schedule_sha256
        or universe.evidence_scope != schedule.evidence_scope
    ):
        raise ValueError("operational_evidence_schedule_universe_mismatch")
    expected = {
        (item.slot_id, item.trade_date, item.schedule_slot)
        for item in schedule.slots
    }
    actual = {
        (item.slot_id, item.trade_date, item.schedule_slot)
        for item in universe.expected_attempts
    }
    if (
        len(expected) != len(schedule.slots)
        or actual != expected
    ):
        raise ValueError("operational_evidence_schedule_universe_mismatch")
    calendar_by_year = {item.year: item for item in calendar.evidence}
    for source in schedule.source_evidence:
        official = calendar_by_year.get(source.year)
        if official is None or any((
            official.source_document_sha256 != source.source_document_sha256,
            official.observed_through != source.observed_through,
        )):
            raise ValueError("operational_evidence_schedule_source_mismatch")
    if any(calendar.is_trading_day(value) is not True for value in schedule.official_trading_dates):
        raise ValueError("operational_evidence_schedule_calendar_mismatch")
    for item in schedule.slots:
        local = item.schedule_slot.astimezone(_SHANGHAI)
        local_time = (local.hour, local.minute, local.second)
        if (
            local.date() != item.trade_date
            or not (
                (9, 30, 0) <= local_time < (11, 30, 0)
                or (13, 0, 0) <= local_time < (14, 57, 0)
            )
        ):
            raise ValueError("operational_evidence_schedule_calendar_mismatch")


def _management_default_off_from_source(payload: bytes) -> Optional[bool]:
    try:
        tree = ast.parse(payload.decode("utf-8"))
    except (SyntaxError, UnicodeError, ValueError):
        return None
    setting_defaults = []
    radar_defaults = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "enabled"
            and isinstance(node.value, ast.Constant)
            and type(node.value.value) is bool
        ):
            setting_defaults.append(node.value.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_read_bool"
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "RADAR_ENABLED"
            and isinstance(node.args[2], ast.Constant)
            and type(node.args[2].value) is bool
        ):
            radar_defaults.append(node.args[2].value)
    if True in setting_defaults or True in radar_defaults:
        return False
    if False in setting_defaults and False in radar_defaults:
        return True
    return None


def scan_security_assets(
    asset_root: Path,
    policy: FrozenSecurityPolicy,
    assets: Optional[Sequence[AssetBinding]] = None,
    *,
    known_findings: Sequence[SecurityFinding | Mapping[str, Any]] = (),
) -> SecurityInput:
    """扫描冻结角色全集；只输出相对路径和稳定规则码。"""
    parsed_findings = [SecurityFinding.model_validate(item) for item in known_findings]
    frozen_assets = policy.required_assets
    if assets is not None and tuple(assets) != frozen_assets:
        return SecurityInput(
            scanComplete=False,
            findings=tuple(parsed_findings),
            managementInterfacesDefaultOff=False,
            aiAuthorityIsolated=False,
            policyId=policy.policy_id,
            policySha256=policy.policy_sha256,
            policyContractVersion=policy.contract_version,
            evidenceScope=policy.evidence_scope,
        )
    content_by_role: dict[str, bytes] = {}
    for asset in frozen_assets:
        content = _read_bound_asset(asset_root, asset)
        content_by_role[asset.role] = content
        if _SENSITIVE.search(content):
            parsed_findings.append(SecurityFinding(
                path=asset.path,
                ruleCode="security_sensitive_assignment",
            ))
    unique = {(item.path, item.rule_code): item for item in parsed_findings}
    management_default_off = _management_default_off_from_source(
        content_by_role["radar_config_source"],
    )
    # 当前项目没有一份能单独证明“AI 绝无状态修改权”的中央权限
    # 绑定资产；静态扫描不以任意 JSON 断言替代这一证明。
    ai_authority_isolated = None
    return SecurityInput(
        scanComplete=True,
        findings=tuple(unique[key] for key in sorted(unique)),
        managementInterfacesDefaultOff=management_default_off,
        aiAuthorityIsolated=ai_authority_isolated,
        policyId=policy.policy_id,
        policySha256=policy.policy_sha256,
        policyContractVersion=policy.contract_version,
        evidenceScope=policy.evidence_scope,
        automaticCollection=True,
        sourceAssets=tuple(
            {
                "path": item.path,
                "contentSha256": item.content_sha256,
                "version": item.version,
                "role": item.role,
            }
            for item in frozen_assets
        ),
    )


def _load_ledger(binding: LedgerStoreBinding, root: Path, checked_at: datetime) -> FormalShadowLedgerV2:
    store = root / binding.relative_path
    loaded = load_latest_formal_shadow_ledger(store, now=checked_at)
    if loaded.status != "available" or loaded.ledger is None or loaded.stored_ref is None:
        reason = loaded.reason_codes[0] if loaded.reason_codes else "operational_evidence_ledger_unverified"
        raise ValueError(reason)
    if loaded.stored_ref.content_sha256 != binding.expected_content_sha256:
        raise ValueError("operational_evidence_ledger_hash_mismatch")
    if not loaded.ledger.calendar_evidence:
        raise ValueError("operational_evidence_calendar_missing")
    return loaded.ledger


def _load_historical_ledger(
    binding: LedgerStoreBinding,
    root: Path,
    *,
    content_sha256: str,
    relative_path: str,
    checked_at: datetime,
) -> FormalShadowLedgerV2:
    """从同一内容仓完整重放 checkpoint 指向的不可变历史台账。"""
    if (
        re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
        or relative_path != f"reports/{content_sha256}.json"
    ):
        raise ValueError("operational_evidence_ledger_checkpoint_mismatch")
    try:
        payload = _strict_json_object(_read_relative_bytes(
            root,
            binding.relative_path + "/" + relative_path,
            maximum=_MAX_JSON_BYTES,
        ))
        if hashlib.sha256(_canonical_json(payload)).hexdigest() != content_sha256:
            raise ValueError
        ledger = FormalShadowLedgerV2.model_validate(payload)
        if not ledger.calendar_evidence:
            raise ValueError
        for evidence in ledger.calendar_evidence:
            calendar_raw = _read_relative_bytes(
                root,
                binding.relative_path
                + f"/calendar/{evidence.source_document_sha256}.html",
                maximum=_MAX_STATIC_BYTES,
            )
            envelope = _canonical_json({
                "contractVersion": "radar-formal-shadow-calendar-input-v1",
                "market": evidence.market,
                "sourceName": evidence.source_name,
                "sourceUrl": evidence.source_url,
                "year": evidence.year,
                "fetchedAt": evidence.fetched_at.isoformat(),
                "observedThrough": evidence.observed_through.isoformat(),
                "sourceDocumentSha256": evidence.source_document_sha256,
            })
            replayed = load_official_sse_calendar_evidence(
                envelope,
                calendar_raw,
            )
            if replayed != evidence:
                raise ValueError
        if any(item.observed_at > checked_at for item in ledger.observations):
            raise ValueError
        return ledger
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise ValueError(
            "operational_evidence_ledger_checkpoint_mismatch",
        ) from error


def _validate_ledger_successor(
    current: FormalShadowLedgerV2,
    historical: FormalShadowLedgerV2,
) -> None:
    """current latest 必须完整保留历史观察且日历/汇总不得倒退。"""
    current_by_identity = {
        (item.module, item.observed_date): item
        for item in current.observations
    }
    for item in historical.observations:
        candidate = current_by_identity.get((item.module, item.observed_date))
        if candidate != item:
            raise ValueError("operational_evidence_ledger_receipt_mismatch")
    if (
        current.calendar_observed_through is None
        or historical.calendar_observed_through is None
        or current.calendar_observed_through < historical.calendar_observed_through
    ):
        raise ValueError("operational_evidence_ledger_receipt_mismatch")
    for module, historical_dates in historical.verified_trading_dates_by_module.items():
        if not set(historical_dates).issubset(
            current.verified_trading_dates_by_module[module],
        ):
            raise ValueError("operational_evidence_ledger_receipt_mismatch")


def _checkpoint_pointer_sha(root: Path, relative: str) -> str:
    raw = _read_relative_bytes(root, relative + "/" + CHECKPOINT_POINTER_NAME, maximum=16 * 1024)
    try:
        pointer = strict_json_loads(
            raw,
            error_code="operational_evidence_checkpoint_unverified",
        )
    except (TypeError, ValueError) as error:
        raise ValueError("operational_evidence_checkpoint_unverified") from error
    if not isinstance(pointer, dict) or set(pointer) != {"contractId", "contentSha256", "relativePath"}:
        raise ValueError("operational_evidence_checkpoint_unverified")
    sha = pointer.get("contentSha256")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise ValueError("operational_evidence_checkpoint_unverified")
    return sha


def _module_input_relative(result: Stage10LiveCollectionResult, module: str) -> str:
    item = {
        "trendRotation": result.trend_rotation,
        "leaderObservation": result.leader_observation,
        "etfObservation": result.etf_observation,
    }[module]
    if not item.input_relative_path:
        raise ValueError("operational_evidence_execution_incomplete")
    return item.input_relative_path


def execution_manifest_complete(effects: Sequence[str]) -> bool:
    """只接受 Task 1 正常完成或 ETF 发布后离线恢复的完整顺序。"""
    return tuple(effects) in {
        ("stage9", "trendRotation", "leaderObservation", "etfObservation"),
        (
            "stage9", "trendRotation", "leaderObservation",
            "etfObservation_published", "etfObservation_replay",
        ),
    }


def _execution_manifest_is_safe_prefix(effects: Sequence[str]) -> bool:
    allowed = {
        "stage9", "trendRotation", "leaderObservation", "etfObservation",
        "etfObservation_published", "etfObservation_replay",
    }
    return (
        len(effects) == len(set(effects))
        and set(effects).issubset(allowed)
        and (not effects or effects[0] == "stage9")
    )


def _module_outcome(status: str) -> str:
    if status in {"available", "unchanged"}:
        return "success"
    if status in {"failed", "contended", "not_attempted"}:
        return status
    raise ValueError("operational_evidence_execution_status_unverified")


def _attempt_module_outcomes(result: Stage10LiveCollectionResult) -> AttemptModuleOutcomes:
    return AttemptModuleOutcomes(
        stage9=_module_outcome(result.stage9.status),
        trendRotation=_module_outcome(result.trend_rotation.status),
        leaderObservation=_module_outcome(result.leader_observation.status),
        etfObservation=_module_outcome(result.etf_observation.status),
    )


def _read_receipt_bundle(relative: str, module: str) -> tuple[FormalShadowObservation, FormalShadowRunReceipt]:
    input_root = PRIVATE_TMP / _relative_path(relative)
    _expected_sha, expected_contract, expected_run_id = _snapshot_input(input_root, module)
    files = {
        "receipt": _read_relative_bytes(PRIVATE_TMP, relative + "/" + RECEIPT_FILENAME, maximum=_MAX_JSON_BYTES),
        "source": _read_relative_bytes(PRIVATE_TMP, relative + "/" + SOURCE_ARTIFACT_FILENAME, maximum=_MAX_JSON_BYTES),
        "policy": _read_relative_bytes(PRIVATE_TMP, relative + "/" + COLLECTION_POLICY_FILENAME, maximum=_MAX_JSON_BYTES),
    }
    if module == "trendRotation":
        files["supporting"] = _read_relative_bytes(PRIVATE_TMP, relative + "/" + TREND_SUPPORTING_FILENAME, maximum=_MAX_JSON_BYTES)
    if module == "etfObservation":
        files["admission"] = _read_relative_bytes(PRIVATE_TMP, relative + "/" + ETF_ADMISSION_FILENAME, maximum=_MAX_JSON_BYTES)
    receipt = load_formal_shadow_run_receipt(files["receipt"])
    if (
        receipt.module != module
        or receipt.source_contract_id != expected_contract
        or receipt.run_id != expected_run_id
    ):
        raise ValueError("operational_evidence_receipt_identity_mismatch")
    observation = adapt_formal_shadow_observation(
        files["receipt"],
        files["source"],
        evaluated_at=receipt.observed_at,
        collection_policy_json_bytes=files["policy"],
        supporting_artifact_json_bytes=files.get("supporting"),
        formal_admission_json_bytes=files.get("admission"),
    )
    return observation, receipt


def _attempt_metrics(
    binding: AttemptCheckpointBinding,
    root: Path,
    ledger_binding: LedgerStoreBinding,
    ledger: FormalShadowLedgerV2,
    calendar: OfficialSseCalendarProvider,
    checked_at: datetime,
) -> Optional[AttemptPerformanceEvidence]:
    attempt_root = root / binding.attempt_root_relative_path
    attempt_fd: Optional[int] = None
    try:
        attempt_fd = _open_root_directory(attempt_root, create_missing=False)
        result = _load_checkpoint_by_sha(
            attempt_root,
            binding.checkpoint_sha256,
            root_fd=attempt_fd,
        )
    finally:
        if attempt_fd is not None:
            os.close(attempt_fd)
    module_states = (
        result.stage9.status,
        result.trend_rotation.status,
        result.leader_observation.status,
        result.etf_observation.status,
    )
    if result.preflight.status in _DRY_PREFLIGHT_STATES:
        if any((
            module_states != ("not_attempted",) * 4,
            result.effects != (),
            result.formal_enabled is not False,
            result.finished_at != result.started_at,
            result.finished_at > checked_at,
        )):
            raise ValueError("operational_evidence_execution_incomplete")
        # 内容寻址的真实 dry checkpoint 是“未取得现场样本”，不是 0ms
        # 成功样本。保留它对完整性的否定，不计入性能样本。
        expected_lock_state = (
            "contended"
            if result.preflight.status == "dry_attempt_contended"
            else "not_attempted"
        )
        return AttemptPerformanceEvidence(
            attemptId=result.attempt_id,
            runId=None,
            tradeDate=result.trade_date,
            scheduleSlot=result.started_at,
            durationMs=0,
            expectedCount=0,
            observedCount=0,
            unsuccessful=True,
            lockContentionCount=(1 if expected_lock_state == "contended" else 0),
            executionComplete=False,
            attemptRootRelativePath=binding.attempt_root_relative_path,
            checkpointSha256=binding.checkpoint_sha256,
            lockState=expected_lock_state,
            moduleOutcomes=_attempt_module_outcomes(result),
        )
    if any((
        result.preflight.status != "ready",
        result.formal_enabled is not False,
        result.finished_at < result.started_at,
        result.finished_at > checked_at,
        calendar.is_trading_day(result.trade_date) is not True,
    )):
        raise ValueError("operational_evidence_execution_incomplete")
    fully_registered = all((
        result.stage9.status == "available",
        result.trend_rotation.status in {"available", "unchanged"},
        result.leader_observation.status in {"available", "unchanged"},
        result.etf_observation.status in {"available", "unchanged"},
    ))
    if not fully_registered:
        outcomes = _attempt_module_outcomes(result)
        if not _execution_manifest_is_safe_prefix(result.effects):
            raise ValueError("operational_evidence_execution_manifest_invalid")
        duration = int((result.finished_at - result.started_at).total_seconds() * 1000)
        return AttemptPerformanceEvidence(
            attemptId=result.attempt_id,
            runId=result.stage9.radar_run_id,
            tradeDate=result.trade_date,
            scheduleSlot=result.started_at,
            durationMs=duration,
            expectedCount=0,
            observedCount=0,
            unsuccessful=True,
            lockContentionCount=sum(
                status == "contended" for status in module_states
            ),
            executionComplete=False,
            attemptRootRelativePath=binding.attempt_root_relative_path,
            checkpointSha256=binding.checkpoint_sha256,
            lockState="acquired",
            moduleOutcomes=outcomes,
        )
    if not execution_manifest_complete(result.effects):
        raise ValueError("operational_evidence_execution_manifest_invalid")
    module_states_by_name = {
        "trendRotation": result.trend_rotation,
        "leaderObservation": result.leader_observation,
        "etfObservation": result.etf_observation,
    }
    historical_by_module: dict[str, FormalShadowLedgerV2] = {}
    previous_ledger: Optional[FormalShadowLedgerV2] = None
    for module in _MODULES:
        state = module_states_by_name[module]
        if not isinstance(state.ledger_sha256, str) or not isinstance(
            state.ledger_relative_path,
            str,
        ):
            raise ValueError("operational_evidence_ledger_checkpoint_mismatch")
        historical = _load_historical_ledger(
            ledger_binding,
            root,
            content_sha256=state.ledger_sha256,
            relative_path=state.ledger_relative_path,
            checked_at=checked_at,
        )
        if previous_ledger is not None:
            _validate_ledger_successor(historical, previous_ledger)
        historical_calendar = OfficialSseCalendarProvider(
            historical.calendar_evidence,
        )
        if historical_calendar.is_trading_day(result.trade_date) is not True:
            raise ValueError("operational_evidence_ledger_checkpoint_mismatch")
        historical_by_module[module] = historical
        previous_ledger = historical
    if previous_ledger is None:
        raise ValueError("operational_evidence_ledger_checkpoint_mismatch")
    _validate_ledger_successor(ledger, previous_ledger)
    observations = []
    receipts = []
    for module in _MODULES:
        state = module_states_by_name[module]
        relative = _module_input_relative(result, module)
        if module == "trendRotation":
            expected_stage9 = (
                result.stage9.trend_input_relative_path,
                result.stage9.trend_input_sha256,
                result.stage9.trend_input_contract_id,
            )
        elif module == "leaderObservation":
            expected_stage9 = (
                result.stage9.leader_input_relative_path,
                result.stage9.leader_input_sha256,
                result.stage9.leader_input_contract_id,
            )
        else:
            expected_stage9 = (relative, state.input_sha256, state.input_contract_id)
        if (relative, state.input_sha256, state.input_contract_id) != expected_stage9:
            raise ValueError("operational_evidence_checkpoint_input_mismatch")
        package_sha, package_contract, package_run_id = _snapshot_input(PRIVATE_TMP / relative, module)
        if (
            package_sha != state.input_sha256
            or package_contract != state.input_contract_id
            or package_run_id != state.input_run_id
        ):
            raise ValueError("operational_evidence_checkpoint_input_mismatch")
        observation, receipt = _read_receipt_bundle(relative, module)
        if (
            receipt.run_id != result.stage9.radar_run_id
            or observation.observed_date != result.trade_date
        ):
            raise ValueError("operational_evidence_receipt_identity_mismatch")
        historical_matches = tuple(item for item in historical_by_module[module].observations if (
            item.module == observation.module
            and item.run_id == observation.run_id
            and item.observed_at == observation.observed_at
        ))
        if (
            len(historical_matches) != 1
            or historical_matches[0].model_dump(mode="json", by_alias=True)
            != observation.model_dump(mode="json", by_alias=True)
        ):
            raise ValueError("operational_evidence_ledger_receipt_mismatch")
        matches = tuple(item for item in ledger.observations if (
            item.module == observation.module
            and item.run_id == observation.run_id
            and item.observed_at == observation.observed_at
        ))
        if len(matches) != 1 or matches[0].model_dump(mode="json", by_alias=True) != observation.model_dump(mode="json", by_alias=True):
            raise ValueError("operational_evidence_ledger_receipt_mismatch")
        observations.append(observation)
        receipts.append(receipt)
    identities = tuple((item.module, item.run_id, item.observed_at) for item in observations)
    if len(identities) != len(set(identities)):
        raise ValueError("operational_evidence_receipt_duplicate")
    duration = int((result.finished_at - result.started_at).total_seconds() * 1000)
    return AttemptPerformanceEvidence(
        attemptId=result.attempt_id,
        runId=result.stage9.radar_run_id,
        tradeDate=result.trade_date,
        scheduleSlot=result.started_at,
        durationMs=duration,
        expectedCount=sum(item.expected_count for item in receipts),
        observedCount=sum(item.observed_count for item in receipts),
        unsuccessful=any(item.observation_status != "ready" or item.lock_state != "acquired" for item in observations),
        lockContentionCount=sum(1 for item in observations if item.lock_state == "contended"),
        executionComplete=True,
        attemptRootRelativePath=binding.attempt_root_relative_path,
        checkpointSha256=binding.checkpoint_sha256,
        lockState="acquired",
        moduleOutcomes=_attempt_module_outcomes(result),
    )


def _static_inputs(asset_root: Path, policy: Optional[FrozenStaticAssetPolicy]) -> tuple[StaticAssetsInput, StaticAssetsInput]:
    if policy is None:
        empty = StaticAssetsInput(assets=(), evidenceScope="static_only")
        return empty, empty
    grouped = {"rollback": [], "runbook": []}
    for item in policy.required_assets:
        _read_bound_asset(asset_root, item)
        grouped[item.gate].append(StaticAsset(
            path=item.path,
            contentSha256=item.content_sha256,
            version=item.version,
            role=item.role,
        ))
    identity = {
        "evidenceScope": policy.evidence_scope,
        "policyId": policy.policy_id,
        "policySha256": policy.policy_sha256,
        "policyContractVersion": policy.contract_version,
        "automaticCollection": True,
    }
    return (
        StaticAssetsInput(assets=tuple(grouped["rollback"]), **identity),
        StaticAssetsInput(assets=tuple(grouped["runbook"]), **identity),
    )


def collect_operational_evidence(
    raw_input: OperationalEvidenceInput | Mapping[str, Any],
    *,
    snapshot_root: Path,
    asset_root: Path,
    project_asset_root: Optional[Path] = None,
) -> OperationalChecksInput:
    """重放全部显式 attempt 和台账仓，生成既有 v1 检查输入。"""
    source = OperationalEvidenceInput.model_validate(raw_input)
    snapshots = _validated_root(snapshot_root)
    assets = _validated_root(asset_root, project_root=project_asset_root)
    ledger = _load_ledger(source.ledger_store, snapshots, source.checked_at)
    calendar = OfficialSseCalendarProvider(ledger.calendar_evidence)
    universe = None
    if source.attempt_universe is not None:
        universe = load_attempt_universe_store(snapshots, source.attempt_universe)
        validate_attempt_universe_bindings(universe, source.attempts)
        if source.schedule_manifest_store is None:
            raise ValueError("operational_evidence_schedule_binding_incomplete")
        schedule = load_schedule_manifest_store(
            snapshots,
            source.schedule_manifest_store,
        )
        validate_schedule_universe_bindings(
            schedule,
            source.schedule_manifest_store.expected_content_sha256,
            universe,
            calendar,
        )
    replayed_attempts = tuple(
        _attempt_metrics(
            item,
            snapshots,
            source.ledger_store,
            ledger,
            calendar,
            source.checked_at,
        )
        for item in source.attempts
    )
    attempt_metrics = tuple(item for item in replayed_attempts if item is not None)
    attempt_ids = tuple(item.attempt_id for item in attempt_metrics)
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("operational_evidence_attempt_duplicate")
    if universe is not None:
        expected_by_root = {
            item.attempt_root_relative_path: item
            for item in universe.expected_attempts
        }
        for item in attempt_metrics:
            expected = expected_by_root.get(item.attempt_root_relative_path or "")
            if expected is None or any((
                item.attempt_id != expected.attempt_id,
                item.trade_date != expected.trade_date,
                item.schedule_slot != expected.schedule_slot,
                item.run_id != expected.expected_run_id,
                item.checkpoint_sha256 != expected.checkpoint_sha256,
                item.lock_state != expected.expected_lock_state,
                item.module_outcomes != expected.module_outcomes,
            )):
                raise ValueError("operational_evidence_attempt_universe_identity_mismatch")

    policy = None
    policy_error = None
    if source.performance_policy is not None:
        try:
            loaded_policy = load_frozen_policy(
                assets,
                source.performance_policy,
                FrozenPerformancePolicy,
            )
            if not isinstance(loaded_policy, FrozenPerformancePolicy):
                raise ValueError("operational_evidence_policy_unverified")
            policy = loaded_policy
        except (TypeError, ValueError):
            policy_error = "performance_frozen_policy_unverified"
    performance = derive_performance_input(
        attempts=attempt_metrics,
        policy=policy,
        reentry_proven=(universe is not None),
        policy_validation_error=policy_error,
        evidence_scope=(universe.evidence_scope if universe is not None else None),
    )

    security: SecurityInput
    if source.security_policy is None:
        security = SecurityInput(
            scanComplete=False,
            findings=source.known_security_findings,
            managementInterfacesDefaultOff=None,
            aiAuthorityIsolated=None,
        )
    else:
        try:
            loaded_security_policy = load_frozen_policy(
                assets,
                source.security_policy,
                FrozenSecurityPolicy,
            )
            if not isinstance(loaded_security_policy, FrozenSecurityPolicy):
                raise ValueError("operational_evidence_policy_unverified")
            security_policy = loaded_security_policy
        except (TypeError, ValueError) as error:
            raise ValueError("operational_evidence_security_policy_unverified") from error
        security = scan_security_assets(
            assets,
            security_policy,
            known_findings=source.known_security_findings,
        )
    static_policy = None
    if source.static_asset_policy is not None:
        try:
            loaded_static_policy = load_frozen_policy(
                assets,
                source.static_asset_policy,
                FrozenStaticAssetPolicy,
            )
            if not isinstance(loaded_static_policy, FrozenStaticAssetPolicy):
                raise ValueError("operational_evidence_policy_unverified")
            static_policy = loaded_static_policy
        except (TypeError, ValueError) as error:
            raise ValueError("operational_evidence_static_policy_unverified") from error
    rollback, runbook = _static_inputs(assets, static_policy)
    return OperationalChecksInput(
        contractVersion="radar-formal-operational-checks-input-v1",
        checkedAt=source.checked_at,
        subjectId=source.subject_id,
        performance=performance,
        security=security,
        rollback=rollback,
        runbook=runbook,
    )


def collect_and_build_operational_checks(
    collector_input_ref: OperationalCollectorInputRef | Mapping[str, Any],
    *,
    snapshot_root: Path,
    asset_root: Path,
    project_asset_root: Optional[Path] = None,
) -> RadarFormalOperationalChecks:
    """从固定临时仓重载内容寻址输入，并在同一调用内生成正式报告。

    这是唯一能向内部检查器传入不可序列化 capability 的公开
    路径；旧 builder 保持兼容，但只能生成 rehearsal/collecting。
    """
    reference = OperationalCollectorInputRef.model_validate(collector_input_ref)
    if reference.contract_version != OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION:
        raise ValueError("operational_evidence_collector_input_contract_mismatch")
    snapshots = _validated_root(snapshot_root)
    if snapshots != PRIVATE_TMP:
        raise ValueError("operational_evidence_collector_store_forbidden")
    raw = _read_relative_bytes(
        snapshots,
        reference.relative_path,
        maximum=_MAX_JSON_BYTES,
    )
    if hashlib.sha256(raw).hexdigest() != reference.content_sha256:
        raise ValueError("operational_evidence_collector_input_hash_mismatch")
    source = OperationalEvidenceInput.model_validate(_strict_json_object(raw))
    checks_input = collect_operational_evidence(
        source,
        snapshot_root=snapshots,
        asset_root=asset_root,
        project_asset_root=project_asset_root,
    )
    return _build_operational_checks(
        checks_input,
        asset_root,
        capability=_COLLECTOR_BUILD_CAPABILITY,
        collector_input_ref=reference,
    )
