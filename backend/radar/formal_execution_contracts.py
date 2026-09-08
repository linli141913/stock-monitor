"""默认关闭的正式执行身份、上下文与结果合同。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python

from radar.formal_readiness_contracts import (
    FROZEN_REQUIRED_FORMAL_GATES,
    FormalModuleName,
)


FORMAL_EXECUTION_BINDING_CONTRACT_VERSION = (
    "radar-formal-execution-binding-v1"
)
FORMAL_EXECUTION_CONTEXT_CONTRACT_VERSION = (
    "radar-formal-execution-context-v1"
)
FORMAL_EXECUTION_RESULT_CONTRACT_VERSION = (
    "radar-formal-execution-result-v1"
)
RADAR_FORMAL_TREND_ROTATION_JOB_ID = "radar-formal-trend-rotation"
RADAR_FORMAL_ETF_OBSERVATION_JOB_ID = "radar-formal-etf-observation"
RADAR_FORMAL_LEADER_OBSERVATION_JOB_ID = "radar-formal-leader-observation"
FORMAL_JOB_IDS: Mapping[FormalModuleName, str] = MappingProxyType({
    "trendRotation": RADAR_FORMAL_TREND_ROTATION_JOB_ID,
    "etfObservation": RADAR_FORMAL_ETF_OBSERVATION_JOB_ID,
    "leaderObservation": RADAR_FORMAL_LEADER_OBSERVATION_JOB_ID,
})
FROZEN_FORMAL_EXECUTION_GATES = FROZEN_REQUIRED_FORMAL_GATES


class _FrozenFormalExecutionModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("formal_execution_time_timezone_required")
    return value


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("formal_execution_identity_blank")
    return value


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _binding_hash_payload(
    value: "FormalExecutionBinding | Mapping[str, object]",
) -> dict:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise TypeError("formal_execution_binding_invalid")
    payload.pop("contentSha256", None)
    payload.pop("content_sha256", None)
    return to_jsonable_python(payload)


def formal_execution_binding_content_sha256(
    value: "FormalExecutionBinding | Mapping[str, object]",
) -> str:
    """按排除自身哈希字段的规范 JSON 口径计算 binding 哈希。"""

    return hashlib.sha256(_canonical_json(_binding_hash_payload(value))).hexdigest()


class FormalExecutionBinding(_FrozenFormalExecutionModel):
    contract_version: Literal[FORMAL_EXECUTION_BINDING_CONTRACT_VERSION] = Field(
        default=FORMAL_EXECUTION_BINDING_CONTRACT_VERSION,
        alias="contractVersion",
    )
    module: FormalModuleName
    job_id: StrictStr = Field(alias="jobId", min_length=1)
    executor_id: StrictStr = Field(alias="executorId", min_length=1)
    executor_contract_version: StrictStr = Field(
        alias="executorContractVersion",
        min_length=1,
    )
    config_sha256: StrictStr = Field(
        alias="configSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    generated_at: datetime = Field(alias="generatedAt")
    expires_at: datetime = Field(alias="expiresAt")
    content_sha256: StrictStr = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("job_id", "executor_id", "executor_contract_version")
    @classmethod
    def identities_must_not_be_blank(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("generated_at", "expires_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_binding(self) -> "FormalExecutionBinding":
        if self.job_id != FORMAL_JOB_IDS[self.module]:
            raise ValueError("formal_execution_binding_job_identity_mismatch")
        if self.expires_at <= self.generated_at:
            raise ValueError("formal_execution_binding_expiry_invalid")
        if (
            formal_execution_binding_content_sha256(self)
            != self.content_sha256
        ):
            raise ValueError(
                "formal_execution_binding_content_sha256_mismatch",
            )
        return self


def build_formal_execution_binding(
    *,
    module: FormalModuleName,
    job_id: str,
    executor_id: str,
    executor_contract_version: str,
    config_sha256: str,
    generated_at: datetime,
    expires_at: datetime,
) -> FormalExecutionBinding:
    """从强身份和有效期生成自身内容寻址的 binding。"""

    payload = {
        "contractVersion": FORMAL_EXECUTION_BINDING_CONTRACT_VERSION,
        "module": module,
        "jobId": job_id,
        "executorId": executor_id,
        "executorContractVersion": executor_contract_version,
        "configSha256": config_sha256,
        "generatedAt": generated_at,
        "expiresAt": expires_at,
    }
    payload["contentSha256"] = formal_execution_binding_content_sha256(
        payload,
    )
    return FormalExecutionBinding.model_validate(payload)


class FormalReadinessReference(_FrozenFormalExecutionModel):
    content_sha256: StrictStr = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    relative_path: StrictStr = Field(alias="relativePath", min_length=1)
    checked_at: datetime = Field(alias="checkedAt")

    @field_validator("checked_at")
    @classmethod
    def checked_at_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_reference(self) -> "FormalReadinessReference":
        if self.relative_path != f"reports/{self.content_sha256}.json":
            raise ValueError("formal_readiness_reference_invalid")
        return self


class FormalExecutionBindingReference(_FrozenFormalExecutionModel):
    contract_version: Literal[FORMAL_EXECUTION_BINDING_CONTRACT_VERSION] = Field(
        default=FORMAL_EXECUTION_BINDING_CONTRACT_VERSION,
        alias="contractVersion",
    )
    content_sha256: StrictStr = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    generated_at: datetime = Field(alias="generatedAt")
    expires_at: datetime = Field(alias="expiresAt")

    @field_validator("generated_at", "expires_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_window(self) -> "FormalExecutionBindingReference":
        if self.expires_at <= self.generated_at:
            raise ValueError("formal_execution_binding_expiry_invalid")
        return self


class FormalExecutionGateSnapshot(_FrozenFormalExecutionModel):
    gate: StrictStr = Field(min_length=1)
    state: Literal["ready"]

    @field_validator("gate")
    @classmethod
    def gate_must_not_be_blank(cls, value: str) -> str:
        return _require_non_blank(value)


class FormalExecutionContext(_FrozenFormalExecutionModel):
    contract_version: Literal[FORMAL_EXECUTION_CONTEXT_CONTRACT_VERSION] = Field(
        default=FORMAL_EXECUTION_CONTEXT_CONTRACT_VERSION,
        alias="contractVersion",
    )
    invocation_id: StrictStr = Field(alias="invocationId", min_length=1)
    module: FormalModuleName
    job_id: StrictStr = Field(alias="jobId", min_length=1)
    executor_id: StrictStr = Field(alias="executorId", min_length=1)
    executor_contract_version: StrictStr = Field(
        alias="executorContractVersion",
        min_length=1,
    )
    config_sha256: StrictStr = Field(
        alias="configSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    readiness: FormalReadinessReference
    binding: FormalExecutionBindingReference
    gates: Tuple[FormalExecutionGateSnapshot, ...]

    @field_validator(
        "invocation_id",
        "job_id",
        "executor_id",
        "executor_contract_version",
    )
    @classmethod
    def identities_must_not_be_blank(cls, value: str) -> str:
        return _require_non_blank(value)

    @model_validator(mode="after")
    def validate_context(self) -> "FormalExecutionContext":
        if self.job_id != FORMAL_JOB_IDS[self.module]:
            raise ValueError("formal_execution_context_job_identity_mismatch")
        gate_names = tuple(item.gate for item in self.gates)
        if (
            len(gate_names) != len(set(gate_names))
            or set(gate_names) != set(FROZEN_FORMAL_EXECUTION_GATES)
        ):
            raise ValueError("formal_execution_gates_incomplete")
        return self


class FormalCandidateResultRef(_FrozenFormalExecutionModel):
    contract_version: StrictStr = Field(alias="contractVersion", min_length=1)
    content_sha256: StrictStr = Field(
        alias="contentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    relative_path: StrictStr = Field(alias="relativePath", min_length=1)

    @field_validator("contract_version", "relative_path")
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("relative_path")
    @classmethod
    def relative_path_must_not_escape(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or "//" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value
        ):
            raise ValueError("formal_candidate_result_path_unverified")
        return value


class FormalExecutionResult(_FrozenFormalExecutionModel):
    contract_version: Literal[FORMAL_EXECUTION_RESULT_CONTRACT_VERSION] = Field(
        default=FORMAL_EXECUTION_RESULT_CONTRACT_VERSION,
        alias="contractVersion",
    )
    status: Literal["completed", "blocked", "failed"]
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime = Field(alias="finishedAt")
    reason_codes: Tuple[StrictStr, ...] = Field(
        default_factory=tuple,
        alias="reasonCodes",
    )
    candidate_result_refs: Tuple[FormalCandidateResultRef, ...] = Field(
        default_factory=tuple,
        alias="candidateResultRefs",
    )

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @field_validator("reason_codes")
    @classmethod
    def reasons_must_not_be_blank(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("formal_execution_reason_blank")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> "FormalExecutionResult":
        if self.finished_at < self.started_at:
            raise ValueError("formal_execution_result_time_invalid")
        if self.status in {"blocked", "failed"} and not self.reason_codes:
            raise ValueError("formal_execution_result_reason_required")
        return self


class FormalExecutor(Protocol):
    """实际 executor 必须显式暴露并固定这三个身份字段。"""

    module: FormalModuleName
    executor_id: str
    contract_version: str

    def __call__(self, context: FormalExecutionContext) -> FormalExecutionResult:
        ...
