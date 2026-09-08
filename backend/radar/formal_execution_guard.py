"""默认关闭正式执行缝的无副作用身份与证据守卫。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.formal_execution_contracts import (
    FORMAL_JOB_IDS,
    FROZEN_FORMAL_EXECUTION_GATES,
    FormalExecutionBinding,
    FormalExecutionBindingReference,
    FormalExecutionContext,
    FormalExecutionGateSnapshot,
    FormalExecutor,
    FormalReadinessReference,
    formal_execution_binding_content_sha256,
)
from radar.formal_readiness_contracts import (
    FormalModuleName,
    RadarFormalReadiness,
    formal_readiness_freshness_reason,
)
from radar.formal_readiness_store import (
    FormalReadinessLoadResult,
    formal_readiness_content_sha256,
)


class FormalExecutionBindingLoadResult(BaseModel):
    """binding loader 的强类型、失败关闭返回值。"""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    status: Literal["available", "missing", "failed"]
    binding: Optional[FormalExecutionBinding] = None
    reason_codes: Tuple[str, ...] = Field(
        default_factory=tuple,
        alias="reasonCodes",
    )

    @model_validator(mode="after")
    def validate_status(self) -> "FormalExecutionBindingLoadResult":
        if self.status == "available" and self.binding is None:
            raise ValueError("formal_execution_binding_missing")
        if self.status != "available" and self.binding is not None:
            raise ValueError("formal_execution_binding_status_conflict")
        if any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("formal_execution_binding_reason_blank")
        return self


FormalExecutionBindingLoader = Callable[
    [],
    FormalExecutionBindingLoadResult,
]


@dataclass(frozen=True)
class FormalExecutionGuardDecision:
    allowed: bool
    reason_code: str
    context: Optional[FormalExecutionContext] = None
    binding: Optional[FormalExecutionBinding] = None


def _blocked(reason_code: str) -> FormalExecutionGuardDecision:
    return FormalExecutionGuardDecision(
        allowed=False,
        reason_code=reason_code,
    )


def _load_binding(
    loader: FormalExecutionBindingLoader,
) -> FormalExecutionBinding | FormalExecutionGuardDecision:
    try:
        loaded = loader()
    except Exception:
        return _blocked("formal_execution_binding_unverified")
    if not isinstance(loaded, FormalExecutionBindingLoadResult):
        return _blocked("formal_execution_binding_unverified")
    if set(loaded.__dict__) != set(FormalExecutionBindingLoadResult.model_fields):
        return _blocked("formal_execution_binding_unverified")
    raw_binding = loaded.binding
    if loaded.status == "missing":
        return _blocked("formal_execution_binding_missing")
    if loaded.status != "available" or raw_binding is None:
        return _blocked("formal_execution_binding_unverified")
    if not isinstance(raw_binding, FormalExecutionBinding):
        return _blocked("formal_execution_binding_unverified")
    try:
        if set(raw_binding.__dict__) != set(FormalExecutionBinding.model_fields):
            return _blocked("formal_execution_binding_unverified")
        if (
            formal_execution_binding_content_sha256(raw_binding)
            != raw_binding.content_sha256
        ):
            return _blocked("formal_execution_binding_hash_mismatch")
        return FormalExecutionBinding.model_validate(
            raw_binding.model_dump(mode="json", by_alias=True),
        )
    except Exception:
        return _blocked("formal_execution_binding_unverified")


def _load_readiness(
    loader: Callable[[], FormalReadinessLoadResult],
    *,
    now: datetime,
) -> tuple[
    Optional[RadarFormalReadiness],
    Optional[FormalReadinessReference],
    Optional[str],
]:
    try:
        loaded = loader()
    except Exception:
        return None, None, "formal_readiness_unverified"
    if not isinstance(loaded, FormalReadinessLoadResult):
        return None, None, "formal_readiness_unverified"
    if loaded.status != "available" or loaded.report is None:
        return None, None, "formal_readiness_unavailable"
    if loaded.report.freshness_policy is None:
        return None, None, "formal_freshness_policy_missing"
    try:
        report = RadarFormalReadiness.model_validate(
            loaded.report.model_dump(
                mode="json",
                by_alias=True,
                warnings="none",
            ),
        )
    except Exception:
        return None, None, "formal_readiness_unverified"
    stored_ref = loaded.stored_ref
    if stored_ref is None:
        return None, None, "formal_readiness_reference_missing"
    content_sha256 = stored_ref.content_sha256
    if (
        stored_ref.status != "available"
        or not isinstance(content_sha256, str)
        or len(content_sha256) != 64
        or any(character not in "0123456789abcdef" for character in content_sha256)
        or stored_ref.relative_path != f"reports/{content_sha256}.json"
    ):
        return None, None, "formal_readiness_reference_invalid"
    if stored_ref.checked_at != report.checked_at:
        return None, None, "formal_readiness_reference_time_mismatch"
    try:
        actual_sha256 = formal_readiness_content_sha256(report)
    except Exception:
        return None, None, "formal_readiness_unverified"
    if actual_sha256 != content_sha256:
        return None, None, "formal_readiness_reference_hash_mismatch"
    freshness_reason = formal_readiness_freshness_reason(report, now=now)
    if freshness_reason:
        return None, None, freshness_reason
    try:
        reference = FormalReadinessReference(
            contentSha256=content_sha256,
            relativePath=stored_ref.relative_path,
            checkedAt=stored_ref.checked_at,
        )
    except Exception:
        return None, None, "formal_readiness_reference_invalid"
    return report, reference, None


def evaluate_formal_execution_guard(
    *,
    module: FormalModuleName,
    job_id: str,
    executor: FormalExecutor,
    config_sha256: str,
    requested: bool,
    binding_loader: FormalExecutionBindingLoader,
    readiness_loader: Callable[[], FormalReadinessLoadResult],
    now: datetime,
    invocation_id: str,
) -> FormalExecutionGuardDecision:
    """按冻结顺序复验请求、binding、就绪证据和执行身份。"""

    if type(requested) is not bool:
        return _blocked("formal_request_invalid")
    if not requested:
        return _blocked("formal_request_not_enabled")
    if now.tzinfo is None or now.utcoffset() is None:
        return _blocked("formal_clock_unverified")

    loaded_binding = _load_binding(binding_loader)
    if isinstance(loaded_binding, FormalExecutionGuardDecision):
        return loaded_binding
    binding = loaded_binding
    if binding.generated_at > now:
        return _blocked("formal_execution_binding_future")
    if binding.expires_at <= now:
        return _blocked("formal_execution_binding_expired")

    report, readiness_reference, readiness_reason = _load_readiness(
        readiness_loader,
        now=now,
    )
    if readiness_reason:
        return _blocked(readiness_reason)
    assert report is not None
    assert readiness_reference is not None
    if report.any_formal_enabled or report.all_modules_formal_enabled or any(
        item.state == "formal_enabled" or item.formal_enabled
        for item in report.modules
    ):
        return _blocked("formal_readiness_runtime_proof_missing")
    readiness = next(
        (item for item in report.modules if item.module == module),
        None,
    )
    if readiness is None:
        return _blocked("formal_module_missing")
    gate_names = tuple(gate.gate for gate in readiness.gates)
    if (
        readiness.state != "ready_to_enable"
        or len(gate_names) != len(set(gate_names))
        or set(gate_names) != set(FROZEN_FORMAL_EXECUTION_GATES)
        or any(not gate.required or gate.state != "ready" for gate in readiness.gates)
    ):
        return _blocked("formal_module_not_ready")

    if module not in FORMAL_JOB_IDS or job_id != FORMAL_JOB_IDS[module]:
        return _blocked("formal_execution_job_identity_mismatch")
    if binding.module != module:
        return _blocked("formal_execution_binding_module_mismatch")
    if binding.job_id != job_id:
        return _blocked("formal_execution_binding_job_mismatch")
    if binding.config_sha256 != config_sha256:
        return _blocked("formal_execution_binding_config_mismatch")
    if not callable(executor):
        return _blocked("formal_executor_invalid")
    if getattr(executor, "module", None) != module:
        return _blocked("formal_executor_module_mismatch")
    if getattr(executor, "executor_id", None) != binding.executor_id:
        return _blocked("formal_executor_identity_mismatch")
    if (
        getattr(executor, "contract_version", None)
        != binding.executor_contract_version
    ):
        return _blocked("formal_executor_contract_mismatch")

    try:
        context = FormalExecutionContext(
            invocationId=invocation_id,
            module=module,
            jobId=job_id,
            executorId=binding.executor_id,
            executorContractVersion=binding.executor_contract_version,
            configSha256=config_sha256,
            readiness=readiness_reference,
            binding=FormalExecutionBindingReference(
                contentSha256=binding.content_sha256,
                generatedAt=binding.generated_at,
                expiresAt=binding.expires_at,
            ),
            gates=tuple(
                FormalExecutionGateSnapshot(gate=gate.gate, state="ready")
                for gate in readiness.gates
            ),
        )
    except Exception:
        return _blocked("formal_execution_context_unverified")
    return FormalExecutionGuardDecision(
        allowed=True,
        reason_code="",
        context=context,
        binding=binding,
    )
