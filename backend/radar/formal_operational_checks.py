"""阶段10正式启用的离线运维证据合同与纯检查器。

本模块刻意不读取环境、SQLite、网络或运行中的服务。调用方把已经收集到的
采样和静态资产放在显式目录中，本模块只形成可复核的版本化检查包。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal, Optional, Tuple

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


OperationalGateName = Literal["performance", "security", "rollback", "runbook"]
OperationalGateState = Literal["collecting", "not_ready", "ready", "failed"]
OPERATIONAL_GATE_NAMES: Tuple[OperationalGateName, ...] = (
    "performance", "security", "rollback", "runbook",
)
OPERATIONAL_CHECKS_CONTRACT_VERSION = "radar-formal-operational-checks-v1"
OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION = (
    "radar-formal-operational-checks-input-v1"
)
OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION = (
    "radar-formal-operational-evidence-input-v1"
)
_MAX_STATIC_ASSET_BYTES = 2 * 1024 * 1024
_COLLECTOR_BUILD_CAPABILITY = object()


class _OperationalModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


def _relative_path(value: str) -> str:
    if not value or value.strip() != value or "\\" in value:
        raise ValueError("operational_relative_path_invalid")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("operational_relative_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("operational_relative_path_invalid")
    return path.as_posix()


class OperationalEvidenceRef(_OperationalModel):
    gate: OperationalGateName
    evidence_type: str = Field(alias="evidenceType", min_length=1)
    contract_version: str = Field(alias="contractVersion", min_length=1)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    subject_id: str = Field(alias="subjectId", min_length=1)
    source_time: datetime = Field(alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    generated_at: datetime = Field(alias="generatedAt")
    evidence_scope: Optional[str] = Field(default=None, alias="evidenceScope")
    policy_id: Optional[str] = Field(default=None, alias="policyId", min_length=1)
    policy_sha256: Optional[str] = Field(default=None, alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    policy_contract_version: Optional[str] = Field(default=None, alias="policyContractVersion", min_length=1)

    @field_validator("source_time", "fetched_at", "generated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "operational_evidence_time")

    @model_validator(mode="after")
    def timestamps_ordered(self) -> "OperationalEvidenceRef":
        if self.source_time > self.fetched_at or self.fetched_at > self.generated_at:
            raise ValueError("operational_evidence_time_order_invalid")
        return self


class OperationalGateResult(_OperationalModel):
    gate: OperationalGateName
    state: OperationalGateState
    reason_codes: Tuple[str, ...] = Field(default_factory=tuple, alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def nonblank_reasons(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("operational_reason_codes_invalid")
        return value

    @model_validator(mode="after")
    def state_matches_reasons(self) -> "OperationalGateResult":
        if self.state == "ready" and self.reason_codes:
            raise ValueError("operational_ready_gate_has_reasons")
        if self.state != "ready" and not self.reason_codes:
            raise ValueError("operational_nonready_gate_reason_missing")
        return self


class OperationalCollectorInputRef(_OperationalModel):
    contract_version: Literal[
        OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION
    ] = Field(alias="contractVersion")
    relative_path: str = Field(alias="relativePath")
    content_sha256: str = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("relative_path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _relative_path(value)


class RadarFormalOperationalChecks(_OperationalModel):
    contract_version: Literal[OPERATIONAL_CHECKS_CONTRACT_VERSION] = Field(
        default=OPERATIONAL_CHECKS_CONTRACT_VERSION,
        alias="contractVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    subject_id: str = Field(alias="subjectId", min_length=1)
    state: OperationalGateState
    gates: Tuple[OperationalGateResult, ...]
    evidence: Tuple[OperationalEvidenceRef, ...]
    collector_input_ref: Optional[OperationalCollectorInputRef] = Field(
        default=None,
        alias="collectorInputRef",
    )

    @field_validator("checked_at")
    @classmethod
    def checked_time_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "operational_checkedAt")

    @model_validator(mode="after")
    def verify_report(self) -> "RadarFormalOperationalChecks":
        names = tuple(gate.gate for gate in self.gates)
        if len(names) != len(set(names)) or set(names) != set(OPERATIONAL_GATE_NAMES):
            raise ValueError("operational_gates_incomplete")
        expected_state: OperationalGateState
        states = tuple(gate.state for gate in self.gates)
        if "failed" in states:
            expected_state = "failed"
        elif all(state == "ready" for state in states):
            expected_state = "ready"
        elif "not_ready" in states:
            expected_state = "not_ready"
        else:
            expected_state = "collecting"
        if self.state != expected_state:
            raise ValueError("operational_state_gates_conflict")
        if self.state == "ready" and self.collector_input_ref is None:
            raise ValueError("operational_collector_input_ref_missing")
        evidence_gates = tuple(item.gate for item in self.evidence)
        if len(evidence_gates) != len(set(evidence_gates)) or set(evidence_gates) != set(OPERATIONAL_GATE_NAMES):
            raise ValueError("operational_evidence_gates_incomplete")
        for item in self.evidence:
            if item.evidence_type != f"formal_operational_{item.gate}":
                raise ValueError("operational_evidence_binding_mismatch")
            if item.contract_version != OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION:
                raise ValueError("operational_evidence_binding_mismatch")
            if item.subject_id != self.subject_id:
                raise ValueError("operational_evidence_subject_mismatch")
            if item.generated_at > self.checked_at or item.fetched_at > self.checked_at or item.source_time > self.checked_at:
                raise ValueError("operational_evidence_after_checkedAt")
        return self


def canonical_operational_checks_sha256(report: RadarFormalOperationalChecks) -> str:
    if not isinstance(report, RadarFormalOperationalChecks):
        raise TypeError("operational_checks_report_invalid")
    payload = report.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class PerformancePolicy(_OperationalModel):
    contract_version: str = Field(alias="contractVersion", min_length=1)
    # 旧人工输入保持兼容；自动汇总器会额外要求这三个冻结字段。
    policy_id: Optional[str] = Field(default=None, alias="policyId", min_length=1)
    policy_sha256: Optional[str] = Field(
        default=None,
        alias="policySha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_scope: Optional[str] = Field(default=None, alias="evidenceScope", min_length=1)
    schedule_interval_ms: Optional[StrictInt] = Field(default=None, alias="scheduleIntervalMs", ge=1)
    min_sample_count: Optional[StrictInt] = Field(default=None, alias="minSampleCount", ge=1)
    min_coverage: Optional[StrictFloat] = Field(default=None, alias="minCoverage", ge=0, le=1)
    max_p95_duration_ms: Optional[StrictInt] = Field(default=None, alias="maxP95DurationMs", ge=0)
    max_failed_rate: Optional[StrictFloat] = Field(default=None, alias="maxFailedRate", ge=0, le=1)
    max_lock_contention_count: Optional[StrictInt] = Field(default=None, alias="maxLockContentionCount", ge=0)


class PerformanceInput(_OperationalModel):
    sample_count: StrictInt = Field(alias="sampleCount", ge=0)
    p50_duration_ms: StrictInt = Field(alias="p50DurationMs", ge=0)
    p95_duration_ms: StrictInt = Field(alias="p95DurationMs", ge=0)
    max_duration_ms: StrictInt = Field(alias="maxDurationMs", ge=0)
    schedule_interval_ms: StrictInt = Field(alias="scheduleIntervalMs", ge=1)
    lock_contention_count: StrictInt = Field(alias="lockContentionCount", ge=0)
    reentry_count: Optional[StrictInt] = Field(alias="reentryCount", ge=0)
    failed_count: StrictInt = Field(alias="failedCount", ge=0)
    coverage: StrictFloat = Field(ge=0, le=1)
    policy: Optional[PerformancePolicy] = None
    automatic_collection: StrictBool = Field(default=False, alias="automaticCollection")
    reentry_proof_complete: StrictBool = Field(default=True, alias="reentryProofComplete")
    execution_evidence_complete: StrictBool = Field(default=True, alias="executionEvidenceComplete")
    evidence_scope: Optional[str] = Field(default=None, alias="evidenceScope")
    policy_validation_error: Optional[str] = Field(default=None, alias="policyValidationError")

    @model_validator(mode="after")
    def count_and_quantiles_consistent(self) -> "PerformanceInput":
        if self.failed_count > self.sample_count:
            raise ValueError("performance_failed_count_invalid")
        if not (self.p50_duration_ms <= self.p95_duration_ms <= self.max_duration_ms):
            raise ValueError("performance_duration_quantiles_invalid")
        if self.automatic_collection:
            if not self.reentry_proof_complete and self.reentry_count is not None:
                raise ValueError("performance_reentry_claim_without_proof")
            if self.reentry_proof_complete and self.reentry_count is None:
                raise ValueError("performance_reentry_proof_without_count")
        return self


class SecurityFinding(_OperationalModel):
    path: str
    rule_code: str = Field(alias="ruleCode", min_length=1)

    @field_validator("path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _relative_path(value)


class OperationalSourceAsset(_OperationalModel):
    path: str
    content_sha256: str = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    version: str = Field(min_length=1)
    role: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _relative_path(value)


class SecurityInput(_OperationalModel):
    scan_complete: StrictBool = Field(alias="scanComplete")
    findings: Tuple[SecurityFinding, ...] = ()
    management_interfaces_default_off: Optional[StrictBool] = Field(alias="managementInterfacesDefaultOff")
    ai_authority_isolated: Optional[StrictBool] = Field(alias="aiAuthorityIsolated")
    policy_id: Optional[str] = Field(default=None, alias="policyId", min_length=1)
    policy_sha256: Optional[str] = Field(default=None, alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    policy_contract_version: Optional[str] = Field(default=None, alias="policyContractVersion", min_length=1)
    evidence_scope: Optional[Literal["static_only"]] = Field(default=None, alias="evidenceScope")
    automatic_collection: StrictBool = Field(
        default=False,
        alias="automaticCollection",
    )
    source_assets: Tuple[OperationalSourceAsset, ...] = Field(
        default_factory=tuple,
        alias="sourceAssets",
    )


class StaticAsset(_OperationalModel):
    path: str
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    version: str = Field(min_length=1)
    role: Optional[str] = Field(default=None, min_length=1)

    @field_validator("path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _relative_path(value)


class StaticAssetsInput(_OperationalModel):
    assets: Tuple[StaticAsset, ...] = ()
    evidence_scope: Optional[Literal["static_only"]] = Field(default=None, alias="evidenceScope")
    policy_id: Optional[str] = Field(default=None, alias="policyId", min_length=1)
    policy_sha256: Optional[str] = Field(default=None, alias="policySha256", pattern=r"^[0-9a-f]{64}$")
    policy_contract_version: Optional[str] = Field(default=None, alias="policyContractVersion", min_length=1)
    automatic_collection: StrictBool = Field(
        default=False,
        alias="automaticCollection",
    )


class OperationalChecksInput(_OperationalModel):
    contract_version: Literal[OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION] = Field(
        alias="contractVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    subject_id: str = Field(alias="subjectId", min_length=1)
    performance: PerformanceInput
    security: SecurityInput
    rollback: StaticAssetsInput
    runbook: StaticAssetsInput

    @field_validator("checked_at")
    @classmethod
    def checked_time_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "operational_checkedAt")


def _result(gate: OperationalGateName, state: OperationalGateState, *reasons: str) -> OperationalGateResult:
    return OperationalGateResult(gate=gate, state=state, reasonCodes=tuple(reasons))


def _performance_result(value: PerformanceInput) -> OperationalGateResult:
    if value.automatic_collection:
        if value.policy_validation_error:
            return _result("performance", "failed", value.policy_validation_error)
        if value.policy is None:
            return _result("performance", "collecting", "performance_threshold_policy_missing")
        if not value.reentry_proof_complete or value.reentry_count is None:
            return _result("performance", "collecting", "performance_reentry_evidence_incomplete")
        if not value.execution_evidence_complete:
            return _result("performance", "collecting", "performance_execution_evidence_incomplete")
        if value.evidence_scope != "live":
            return _result("performance", "collecting", "performance_evidence_scope_not_live")
    if value.reentry_count is None:
        return _result("performance", "collecting", "performance_reentry_evidence_incomplete")
    if value.reentry_count:
        return _result("performance", "failed", "performance_reentry_detected")
    if value.max_duration_ms > value.schedule_interval_ms:
        return _result("performance", "not_ready", "performance_duration_exceeds_interval")
    if value.policy is None:
        return _result("performance", "collecting", "performance_threshold_policy_missing")
    policy = value.policy
    if any(
        item is None
        for item in (
            policy.min_sample_count,
            policy.min_coverage,
            policy.max_p95_duration_ms,
            policy.max_failed_rate,
            policy.max_lock_contention_count,
        )
    ):
        return _result("performance", "collecting", "performance_threshold_policy_incomplete")
    failed_rate = value.failed_count / value.sample_count if value.sample_count else 0.0
    violations = []
    if value.sample_count < policy.min_sample_count:
        violations.append("performance_sample_count_insufficient")
    if value.coverage < policy.min_coverage:
        violations.append("performance_coverage_insufficient")
    if value.p95_duration_ms > policy.max_p95_duration_ms:
        violations.append("performance_p95_threshold_exceeded")
    if failed_rate > policy.max_failed_rate:
        violations.append("performance_failed_rate_exceeded")
    if value.lock_contention_count > policy.max_lock_contention_count:
        violations.append("performance_lock_contention_exceeded")
    if violations:
        return _result("performance", "not_ready", *violations)
    if not value.automatic_collection:
        return _result(
            "performance",
            "collecting",
            "performance_automatic_collection_required",
        )
    frozen_payload = policy.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    frozen_sha = frozen_payload.pop("policySha256", None)
    if (
        policy.contract_version != "operational-performance-policy-v1"
        or policy.policy_id is None
        or frozen_sha is None
        or hashlib.sha256(_canonical_json(frozen_payload)).hexdigest()
        != frozen_sha
        or policy.evidence_scope != "live"
        or value.evidence_scope != "live"
    ):
        return _result(
            "performance",
            "collecting",
            "performance_frozen_policy_unverified",
        )
    return _result("performance", "ready")


def _security_result(value: SecurityInput, asset_root: Path) -> OperationalGateResult:
    if value.findings:
        return _result("security", "failed", "security_sensitive_content_detected")
    if not value.scan_complete:
        return _result("security", "collecting", "security_scan_incomplete")
    if value.management_interfaces_default_off is False:
        return _result("security", "failed", "security_management_interface_not_default_off")
    if value.ai_authority_isolated is False:
        return _result("security", "failed", "security_ai_authority_violation")
    unproven = []
    if value.management_interfaces_default_off is None:
        unproven.append("security_management_default_unproven")
    if value.ai_authority_isolated is None:
        unproven.append("security_ai_authority_unproven")
    if unproven:
        return _result("security", "not_ready", *unproven)
    if not value.automatic_collection:
        return _result(
            "security",
            "collecting",
            "security_automatic_collection_required",
        )
    if (
        value.policy_id is None
        or value.policy_sha256 is None
        or value.policy_contract_version != "radar-formal-security-policy-v1"
        or value.evidence_scope != "static_only"
    ):
        return _result(
            "security",
            "collecting",
            "security_frozen_policy_unverified",
        )
    required_sources = {
        "radar_config_source": "backend/radar/config.py",
        "formal_execution_guard_source": "backend/radar/formal_execution_guard.py",
    }
    by_role = {item.role: item for item in value.source_assets}
    if (
        len(by_role) != len(value.source_assets)
        or any(
            by_role.get(role) is None or by_role[role].path != path
            for role, path in required_sources.items()
        )
    ):
        return _result(
            "security",
            "collecting",
            "security_source_proof_incomplete",
        )
    for asset in value.source_assets:
        try:
            content = _read_relative_asset_bytes(asset_root, asset.path)
        except FileNotFoundError:
            return _result("security", "not_ready", "security_source_asset_missing")
        except (OSError, TypeError, ValueError):
            return _result("security", "failed", "security_source_asset_unverified")
        if hashlib.sha256(content).hexdigest() != asset.content_sha256:
            return _result("security", "failed", "security_source_asset_hash_mismatch")
    security_policy_payload = {
        "contractVersion": value.policy_contract_version,
        "policyId": value.policy_id,
        "evidenceScope": value.evidence_scope,
        "requiredAssets": [
            item.model_dump(mode="json", by_alias=True)
            for item in value.source_assets
        ],
    }
    if hashlib.sha256(_canonical_json(security_policy_payload)).hexdigest() != value.policy_sha256:
        return _result(
            "security",
            "collecting",
            "security_frozen_policy_unverified",
        )
    return _result("security", "ready")


def _static_assets_result(
    gate: Literal["rollback", "runbook"],
    value: StaticAssetsInput,
    asset_root: Path,
) -> OperationalGateResult:
    if not value.assets:
        return _result(gate, "not_ready", f"{gate}_assets_missing")
    for asset in value.assets:
        try:
            content = _read_relative_asset_bytes(asset_root, asset.path)
        except FileNotFoundError:
            return _result(gate, "not_ready", f"{gate}_asset_missing")
        except (OSError, TypeError, ValueError):
            return _result(gate, "failed", f"{gate}_asset_unverified")
        if hashlib.sha256(content).hexdigest() != asset.content_sha256:
            return _result(gate, "failed", f"{gate}_asset_hash_mismatch")
    if not value.automatic_collection:
        return _result(
            gate,
            "collecting",
            f"{gate}_automatic_collection_required",
        )
    if (
        value.policy_id is None
        or value.policy_sha256 is None
        or value.policy_contract_version
        != "radar-formal-static-assets-policy-v1"
        or value.evidence_scope != "static_only"
    ):
        return _result(
            gate,
            "collecting",
            f"{gate}_frozen_policy_unverified",
        )
    roles = tuple(item.role for item in value.assets)
    if any(role is None for role in roles) or len(roles) != len(set(roles)):
        return _result(
            gate,
            "collecting",
            f"{gate}_asset_roles_unverified",
        )
    return _result(gate, "ready")


def _asset_directory_flags() -> int:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(directory_flag, int) or not isinstance(nofollow_flag, int):
        raise ValueError("operational_asset_secure_flags_unavailable")
    return os.O_RDONLY | directory_flag | nofollow_flag


def _open_asset_directory(path: Path | str, *, dir_fd: Optional[int] = None) -> int:
    if dir_fd is None:
        descriptor = os.open(os.fspath(path), _asset_directory_flags())
    else:
        descriptor = os.open(os.fspath(path), _asset_directory_flags(), dir_fd=dir_fd)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("operational_asset_directory_unverified")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_asset_directory_entry(parent_fd: int, name: str, child_fd: int) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise ValueError("operational_asset_directory_unverified")


def _verify_asset_root(root: Path, root_fd: int) -> None:
    probe = _open_asset_directory(root)
    try:
        current, opened = os.fstat(probe), os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("operational_asset_directory_unverified")
    finally:
        os.close(probe)


def _read_relative_asset_bytes(asset_root: Path, relative_path: str) -> bytes:
    """以打开的目录句柄逐段读取普通文件，避免检查后按路径再次跟随。"""

    parts = PurePosixPath(relative_path).parts
    root_fd = _open_asset_directory(asset_root)
    directories: list[tuple[int, str, int]] = []
    file_fd: Optional[int] = None
    try:
        _verify_asset_root(asset_root, root_fd)
        current_fd = root_fd
        for part in parts[:-1]:
            child_fd = _open_asset_directory(part, dir_fd=current_fd)
            _verify_asset_directory_entry(current_fd, part, child_fd)
            directories.append((current_fd, part, child_fd))
            current_fd = child_fd
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow_flag, int):
            raise ValueError("operational_asset_secure_flags_unavailable")
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | nofollow_flag,
            dir_fd=current_fd,
        )
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_STATIC_ASSET_BYTES:
            raise ValueError("operational_asset_file_unverified")
        chunks = []
        remaining = _MAX_STATIC_ASSET_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(file_fd)
        current_metadata = os.stat(
            parts[-1],
            dir_fd=current_fd,
            follow_symlinks=False,
        )
        if (
            len(content) > _MAX_STATIC_ASSET_BYTES
            or not stat.S_ISREG(current_metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (after.st_dev, after.st_ino)
            or (after.st_dev, after.st_ino)
            != (current_metadata.st_dev, current_metadata.st_ino)
            or (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise ValueError("operational_asset_file_unverified")
        for parent_fd, name, child_fd in reversed(directories):
            _verify_asset_directory_entry(parent_fd, name, child_fd)
        _verify_asset_root(asset_root, root_fd)
        return content
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for _parent_fd, _name, child_fd in reversed(directories):
            os.close(child_fd)
        os.close(root_fd)


def _evidence_for(
    gate: OperationalGateName,
    payload: object,
    source: OperationalChecksInput,
    *,
    force_rehearsal: bool = False,
) -> OperationalEvidenceRef:
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    policy_payload = payload.get("policy") if isinstance(payload, dict) else None
    identity_payload = policy_payload if isinstance(policy_payload, dict) else payload
    automatic = (
        isinstance(payload, dict)
        and payload.get("automaticCollection") is True
    )
    return OperationalEvidenceRef(
        gate=gate,
        evidenceType=f"formal_operational_{gate}",
        contractVersion=OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION,
        contentSha256=digest,
        subjectId=source.subject_id,
        sourceTime=source.checked_at,
        fetchedAt=source.checked_at,
        generatedAt=source.checked_at,
        evidenceScope=(
            payload.get("evidenceScope")
            if automatic and isinstance(payload, dict) and not force_rehearsal
            else "rehearsal"
        ),
        policyId=(identity_payload.get("policyId") if isinstance(identity_payload, dict) else None),
        policySha256=(identity_payload.get("policySha256") if isinstance(identity_payload, dict) else None),
        policyContractVersion=(
            identity_payload.get("policyContractVersion", identity_payload.get("contractVersion"))
            if isinstance(identity_payload, dict) else None
        ),
    )


def _build_operational_checks(
    raw_input: OperationalChecksInput | dict,
    asset_root: Path,
    *,
    capability: object,
    collector_input_ref: Optional[OperationalCollectorInputRef] = None,
) -> RadarFormalOperationalChecks:
    values = OperationalChecksInput.model_validate(raw_input)
    if (
        not isinstance(asset_root, Path)
        or not asset_root.is_dir()
        or stat.S_ISLNK(asset_root.lstat().st_mode)
    ):
        raise ValueError("operational_asset_root_invalid")
    evaluated_gates = (
        _performance_result(values.performance),
        _security_result(values.security, asset_root),
        _static_assets_result("rollback", values.rollback, asset_root),
        _static_assets_result("runbook", values.runbook, asset_root),
    )
    trusted_collection = (
        capability is _COLLECTOR_BUILD_CAPABILITY
        and collector_input_ref is not None
    )
    gates = evaluated_gates if trusted_collection else tuple(
        _result(
            item.gate,
            "collecting",
            f"{item.gate}_collector_evidence_required",
        )
        for item in evaluated_gates
    )
    states = tuple(gate.state for gate in gates)
    state: OperationalGateState = (
        "failed" if "failed" in states else "ready" if all(item == "ready" for item in states)
        else "not_ready" if "not_ready" in states else "collecting"
    )
    evidence = (
        _evidence_for("performance", values.performance.model_dump(mode="json", by_alias=True), values, force_rehearsal=not trusted_collection),
        _evidence_for("security", values.security.model_dump(mode="json", by_alias=True), values, force_rehearsal=not trusted_collection),
        _evidence_for("rollback", values.rollback.model_dump(mode="json", by_alias=True), values, force_rehearsal=not trusted_collection),
        _evidence_for("runbook", values.runbook.model_dump(mode="json", by_alias=True), values, force_rehearsal=not trusted_collection),
    )
    return RadarFormalOperationalChecks(
        checkedAt=values.checked_at,
        subjectId=values.subject_id,
        state=state,
        gates=gates,
        evidence=evidence,
        collectorInputRef=(collector_input_ref if trusted_collection else None),
    )


def build_operational_checks(
    raw_input: OperationalChecksInput | dict,
    asset_root: Path,
) -> RadarFormalOperationalChecks:
    """兼容旧离线输入；序列化中间对象不能生成正式 ready。"""
    return _build_operational_checks(
        raw_input,
        asset_root,
        capability=object(),
    )
