"""阶段10正式启用的强类型只读合同。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Literal, Mapping, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


FormalModuleName = Literal[
    "trendRotation",
    "etfObservation",
    "leaderObservation",
]
FormalReadinessState = Literal[
    "collecting",
    "not_ready",
    "ready_to_enable",
    "failed",
    "formal_enabled",
]
FormalGateReadiness = Literal["collecting", "not_ready", "ready", "failed"]
FormalShadowProgressState = Literal["available", "missing", "failed"]
FORMAL_MODULE_REQUIRED_TRADING_DAYS: Mapping[FormalModuleName, int] = (
    MappingProxyType({
        "trendRotation": 20,
        "etfObservation": 5,
        "leaderObservation": 20,
    })
)
FROZEN_REQUIRED_FORMAL_GATES = (
    "stage9_quality",
    "rule_version",
    "calibration",
    "shadow_ledger",
    "data_quality",
    "performance",
    "security",
    "rollback",
    "runbook",
)
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))


class _FormalModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


class RadarFormalFreshnessPolicy(_FormalModel):
    """由调用方显式提供的正式就绪有效期策略。"""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    policy_version: Literal["radar-formal-freshness-policy-v1"] = Field(
        default="radar-formal-freshness-policy-v1",
        alias="policyVersion",
    )
    report_max_age_seconds: StrictInt = Field(
        alias="reportMaxAgeSeconds",
        gt=0,
    )
    evidence_max_age_seconds: StrictInt = Field(
        alias="evidenceMaxAgeSeconds",
        gt=0,
    )
    operational_checks_max_age_seconds: StrictInt = Field(
        alias="operationalChecksMaxAgeSeconds",
        gt=0,
    )


class FormalGateState(_FormalModel):
    gate: str = Field(min_length=1)
    state: FormalGateReadiness
    required: StrictBool = True
    reason_codes: Tuple[str, ...] = Field(default_factory=tuple, alias="reasonCodes")

    @field_validator("gate")
    @classmethod
    def gate_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate_blank")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_must_not_be_blank(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("reason_code_blank")
        return value


class FormalEvidenceRef(_FormalModel):
    evidence_type: str = Field(alias="evidenceType", min_length=1)
    contract_version: str = Field(alias="contractVersion", min_length=1)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    subject_id: str = Field(alias="subjectId", min_length=1)
    generated_at: datetime = Field(alias="generatedAt")
    source_time: datetime = Field(alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")

    @field_validator("subject_id")
    @classmethod
    def subject_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subjectId_blank")
        return value

    @field_validator("generated_at", "source_time", "fetched_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "formal_evidence_time")

    @model_validator(mode="after")
    def validate_temporal_order(self) -> "FormalEvidenceRef":
        if self.source_time > self.fetched_at:
            raise ValueError("sourceTime_after_fetchedAt")
        if self.fetched_at > self.generated_at:
            raise ValueError("fetchedAt_after_generatedAt")
        return self


class FormalModuleReadiness(_FormalModel):
    module: FormalModuleName
    state: FormalReadinessState
    requested: StrictBool
    configured_enabled: StrictBool = Field(alias="configuredEnabled")
    formal_enabled: StrictBool = Field(alias="formalEnabled")
    observed_trading_days: int = Field(alias="observedTradingDays", ge=0)
    required_trading_days: int = Field(alias="requiredTradingDays", ge=1)
    last_observed_trading_date: Optional[date] = Field(
        default=None,
        alias="lastObservedTradingDate",
    )
    gates: Tuple[FormalGateState, ...] = Field(default_factory=tuple)
    reason_codes: Tuple[str, ...] = Field(default_factory=tuple, alias="reasonCodes")

    @model_validator(mode="after")
    def validate_enablement(self) -> "FormalModuleReadiness":
        if self.required_trading_days != FORMAL_MODULE_REQUIRED_TRADING_DAYS[self.module]:
            raise ValueError("requiredTradingDays_contract_conflict")
        shadow_ready = any(
            gate.gate == "shadow_ledger" and gate.state == "ready"
            for gate in self.gates
        )
        if shadow_ready and self.observed_trading_days < self.required_trading_days:
            raise ValueError("shadow_ready_days_insufficient")
        if shadow_ready and self.last_observed_trading_date is None:
            raise ValueError("shadow_ready_last_date_missing")
        if self.state in {"ready_to_enable", "formal_enabled"}:
            prefix = self.state
            if not self.gates:
                raise ValueError(f"{prefix}_gates_empty")
            actual_gate_names = tuple(gate.gate for gate in self.gates)
            if (
                len(actual_gate_names) != len(set(actual_gate_names))
                or set(actual_gate_names) != set(FROZEN_REQUIRED_FORMAL_GATES)
            ):
                raise ValueError(f"{prefix}_required_gates_incomplete")
            if any(
                not gate.required or gate.state != "ready"
                for gate in self.gates
            ):
                raise ValueError(f"{prefix}_gate_not_ready")
        if self.state == "formal_enabled":
            if not self.formal_enabled:
                raise ValueError("formal_enabled_requires_formalEnabled")
            if not self.requested:
                raise ValueError("formal_enabled_requires_requested")
            if not self.configured_enabled:
                raise ValueError("formal_enabled_requires_configuredEnabled")
        elif self.formal_enabled:
            raise ValueError("formalEnabled_requires_formal_enabled_state")
        return self


class FormalShadowProgressModule(_FormalModel):
    """经内容哈希与 v2 台账合同验证后的单模块只读观察进度。"""

    module: FormalModuleName
    observed_trading_days: StrictInt = Field(alias="observedTradingDays", ge=0)
    required_trading_days: StrictInt = Field(alias="requiredTradingDays", ge=1)
    latest_ready_streak: StrictInt = Field(alias="latestReadyStreak", ge=0)
    latest_ready_trading_date: Optional[date] = Field(
        default=None,
        alias="latestReadyTradingDate",
    )

    @model_validator(mode="after")
    def validate_progress(self) -> "FormalShadowProgressModule":
        if self.required_trading_days != FORMAL_MODULE_REQUIRED_TRADING_DAYS[self.module]:
            raise ValueError("requiredTradingDays_contract_conflict")
        if self.latest_ready_streak > self.observed_trading_days:
            raise ValueError("latestReadyStreak_exceeds_observedTradingDays")
        if (self.observed_trading_days == 0) != (
            self.latest_ready_trading_date is None
        ):
            raise ValueError("latestReadyTradingDate_progress_conflict")
        return self


class RadarFormalShadowProgress(_FormalModel):
    """真实影子台账的最小只读投影；不表达正式启用结论。"""

    contract_version: Literal["radar-formal-shadow-progress-v1"] = Field(
        default="radar-formal-shadow-progress-v1",
        alias="contractVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    state: FormalShadowProgressState
    modules: Tuple[FormalShadowProgressModule, ...] = Field(default_factory=tuple)
    ledger_sha256: Optional[str] = Field(
        default=None,
        alias="ledgerSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    reason_codes: Tuple[str, ...] = Field(default_factory=tuple, alias="reasonCodes")

    @field_validator("checked_at")
    @classmethod
    def progress_checked_at_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "checkedAt")

    @field_validator("reason_codes")
    @classmethod
    def progress_reasons_must_not_be_blank(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("reason_code_blank")
        return value

    @model_validator(mode="after")
    def validate_progress_state(self) -> "RadarFormalShadowProgress":
        if self.state == "available":
            names = tuple(item.module for item in self.modules)
            if len(names) != len(set(names)) or set(names) != set(
                FORMAL_MODULE_REQUIRED_TRADING_DAYS
            ):
                raise ValueError("available_modules_incomplete")
            if self.ledger_sha256 is None:
                raise ValueError("available_ledgerSha256_missing")
            if self.reason_codes:
                raise ValueError("available_reasonCodes_not_empty")
            return self
        if self.modules:
            raise ValueError("unavailable_modules_must_be_empty")
        if self.ledger_sha256 is not None:
            raise ValueError("unavailable_ledgerSha256_must_be_null")
        if not self.reason_codes:
            raise ValueError("unavailable_reasonCodes_empty")
        return self


class RadarFormalReadiness(_FormalModel):
    contract_version: Literal["radar-formal-readiness-v1"] = Field(
        default="radar-formal-readiness-v1",
        alias="contractVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    freshness_policy: Optional[RadarFormalFreshnessPolicy] = Field(
        default=None,
        alias="freshnessPolicy",
    )
    state: FormalReadinessState
    any_formal_enabled: StrictBool = Field(alias="anyFormalEnabled")
    all_modules_formal_enabled: StrictBool = Field(alias="allModulesFormalEnabled")
    stage9_replay_run_id: Optional[str] = Field(
        default=None,
        alias="stage9ReplayRunId",
        min_length=1,
    )
    stage9_quality_sha256: Optional[str] = Field(
        default=None,
        alias="stage9QualitySha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    stage9_quality_state: FormalGateReadiness = Field(alias="stage9QualityState")
    modules: Tuple[FormalModuleReadiness, ...]
    evidence: Tuple[FormalEvidenceRef, ...] = Field(default_factory=tuple)
    reason_codes: Tuple[str, ...] = Field(default_factory=tuple, alias="reasonCodes")

    @field_validator("checked_at")
    @classmethod
    def checked_at_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "checkedAt")

    @model_validator(mode="after")
    def validate_consistency(self) -> "RadarFormalReadiness":
        module_names = tuple(item.module for item in self.modules)
        if len(set(module_names)) != len(module_names):
            raise ValueError("duplicate_module")
        if set(module_names) != {
            "trendRotation",
            "etfObservation",
            "leaderObservation",
        }:
            raise ValueError("modules_incomplete")
        actual_enabled = tuple(item.formal_enabled for item in self.modules)
        if self.any_formal_enabled != any(actual_enabled):
            raise ValueError("anyFormalEnabled_conflict")
        if self.all_modules_formal_enabled != all(actual_enabled):
            raise ValueError("allModulesFormalEnabled_conflict")
        expected_state = (
            "formal_enabled"
            if self.any_formal_enabled
            else "failed"
            if any(item.state == "failed" for item in self.modules)
            else "ready_to_enable"
            if any(item.state == "ready_to_enable" for item in self.modules)
            else "not_ready"
            if any(item.state == "not_ready" for item in self.modules)
            else "collecting"
        )
        if self.state != expected_state:
            raise ValueError("state_modules_conflict")
        readiness_claimed = any(
            module.state in {"ready_to_enable", "formal_enabled"}
            for module in self.modules
        )
        if readiness_claimed and self.freshness_policy is None:
            raise ValueError("formal_freshness_policy_missing")
        for item in self.evidence:
            if item.source_time > self.checked_at:
                raise ValueError("future_sourceTime")
            if item.fetched_at > self.checked_at:
                raise ValueError("future_fetchedAt")
            if item.generated_at > self.checked_at:
                raise ValueError("future_generatedAt")
        checked_shanghai_date = self.checked_at.astimezone(
            _SHANGHAI_TIMEZONE,
        ).date()
        if any(
            module.last_observed_trading_date is not None
            and module.last_observed_trading_date > checked_shanghai_date
            for module in self.modules
        ):
            raise ValueError("lastObservedTradingDate_after_checkedAt")
        stage9_gates = []
        for module in self.modules:
            module_stage9_gates = tuple(
                gate for gate in module.gates if gate.gate == "stage9_quality"
            )
            if len(module_stage9_gates) != 1:
                raise ValueError("stage9_quality_gate_count_conflict")
            stage9_gates.append(module_stage9_gates[0])
        if (
            self.stage9_quality_state == "failed"
            and any(gate.state not in {"failed", "not_ready"} for gate in stage9_gates)
        ):
            raise ValueError("stage9_failed_gate_ready_conflict")
        stage9_binding_required = (
            self.stage9_quality_state == "ready"
            or any(gate.state == "ready" for gate in stage9_gates)
            or any(
                module.state in {"ready_to_enable", "formal_enabled"}
                for module in self.modules
            )
        )
        if stage9_binding_required:
            if not self.stage9_replay_run_id or not self.stage9_quality_sha256:
                raise ValueError("stage9_ready_identity_missing")
            quality_evidence = tuple(
                item
                for item in self.evidence
                if item.evidence_type == "stage9_quality"
            )
            if not quality_evidence:
                if not self.evidence:
                    raise ValueError("stage9_ready_evidence_missing")
                raise ValueError("stage9_ready_evidence_type_mismatch")
            if len(quality_evidence) != 1:
                raise ValueError("stage9_ready_evidence_not_unique")
            stage9_evidence = quality_evidence[0]
            if stage9_evidence.contract_version != "radar-replay-quality-v2":
                raise ValueError("stage9_ready_evidence_contract_mismatch")
            if stage9_evidence.content_sha256 != self.stage9_quality_sha256:
                raise ValueError("stage9_ready_evidence_sha256_mismatch")
            if stage9_evidence.subject_id != self.stage9_replay_run_id:
                raise ValueError("stage9_ready_evidence_subject_mismatch")
        if readiness_claimed:
            operational_evidence = tuple(
                item for item in self.evidence
                if item.evidence_type == "formal_operational_checks"
            )
            if not operational_evidence:
                raise ValueError("formal_operational_checks_evidence_missing")
            if len(operational_evidence) != 1:
                raise ValueError("formal_operational_checks_evidence_not_unique")
            operational_item = operational_evidence[0]
            if (
                operational_item.contract_version
                != "radar-formal-operational-checks-v1"
            ):
                raise ValueError(
                    "formal_operational_checks_evidence_contract_mismatch",
                )
            if operational_item.subject_id != self.stage9_replay_run_id:
                raise ValueError(
                    "formal_operational_checks_evidence_subject_mismatch",
                )
        return self


def formal_readiness_freshness_reason(
    report: RadarFormalReadiness,
    *,
    now: datetime,
) -> Optional[str]:
    """以调用方的 aware clock 重新验证报告与逐条证据寿命。

    返回稳定原因码；``None``表示当前时点仍在显式策略边界内。
    """

    if not isinstance(report, RadarFormalReadiness):
        return "formal_readiness_unverified"
    if now.tzinfo is None or now.utcoffset() is None:
        return "formal_clock_unverified"
    if report.checked_at.tzinfo is None or report.checked_at.utcoffset() is None:
        return "formal_readiness_unverified"
    if report.checked_at > now:
        return "formal_readiness_checked_at_future"
    policy = report.freshness_policy
    readiness_claimed = any(
        module.state in {"ready_to_enable", "formal_enabled"}
        for module in report.modules
    )
    if policy is None:
        return "formal_freshness_policy_missing" if readiness_claimed else None
    if now - report.checked_at > timedelta(
        seconds=policy.report_max_age_seconds,
    ):
        return "formal_readiness_report_expired"
    for item in report.evidence:
        if item.source_time > now:
            return "formal_evidence_source_time_future"
        max_age_seconds = (
            policy.operational_checks_max_age_seconds
            if item.evidence_type == "formal_operational_checks"
            else policy.evidence_max_age_seconds
        )
        if now - item.source_time > timedelta(seconds=max_age_seconds):
            return (
                "formal_operational_checks_expired"
                if item.evidence_type == "formal_operational_checks"
                else "formal_evidence_expired"
            )
    return None
