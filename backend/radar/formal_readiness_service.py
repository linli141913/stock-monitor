"""阶段10无副作用的逐模块正式就绪判定。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping, Optional, Tuple, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from radar.formal_readiness_contracts import (
    FORMAL_MODULE_REQUIRED_TRADING_DAYS,
    FormalEvidenceRef,
    FormalGateReadiness,
    FormalGateState,
    FormalModuleName,
    FormalModuleReadiness,
    RadarFormalFreshnessPolicy,
    RadarFormalReadiness,
)
from radar.formal_shadow_ledger import FormalShadowLedger, FormalShadowLedgerV2
from radar.formal_shadow_calendar import OfficialSseCalendarProvider
from radar.formal_shadow_ledger_store import formal_shadow_ledger_content_sha256
from radar.formal_operational_checks import (
    RadarFormalOperationalChecks,
    canonical_operational_checks_sha256,
)
from radar.formal_operational_evidence_collector import (
    OperationalEvidenceInput,
    _read_relative_bytes,
    collect_and_build_operational_checks,
)
from radar.strict_json import strict_json_loads


_MODULES: Tuple[FormalModuleName, ...] = (
    "trendRotation",
    "etfObservation",
    "leaderObservation",
)
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
_OPERATIONAL_COLLECTOR_ROOT = Path("/private/tmp")
_OPERATIONAL_ASSET_ROOT = Path(__file__).resolve().parents[2]
_MAX_OPERATIONAL_COLLECTOR_INPUT_BYTES = 8 * 1024 * 1024


class _FrozenDict(dict):
    """保持Pydantic序列化兼容的不可变模块映射。"""

    def __init__(self, *args, **kwargs) -> None:
        dict.__init__(self, *args, **kwargs)

    def _immutable(self, *args, **kwargs) -> None:
        raise TypeError("formal_readiness_mapping_immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class FormalReadinessInputs(BaseModel):
    """调用方提供的已验证强类型门和唯一版本化运维检查包。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: datetime
    stage9_replay_run_id: Optional[str] = None
    stage9_quality_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    stage9_quality_state: FormalGateReadiness
    stage9_domain_states: Mapping[FormalModuleName, FormalGateReadiness]
    shadow_ledger: Optional[Union[FormalShadowLedgerV2, FormalShadowLedger]] = None
    shadow_ledger_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    rule_version_states: Mapping[FormalModuleName, FormalGateReadiness]
    calibration_states: Mapping[FormalModuleName, FormalGateReadiness]
    data_quality_states: Mapping[FormalModuleName, FormalGateReadiness]
    freshness_policy: Optional[RadarFormalFreshnessPolicy] = None
    operational_checks: Optional[RadarFormalOperationalChecks] = None
    operational_checks_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    requested_by_module: Mapping[FormalModuleName, StrictBool]
    enabled_by_module: Mapping[FormalModuleName, StrictBool]
    evidence: Tuple[FormalEvidenceRef, ...] = ()
    # 兼容旧离线输入文件；就绪判定不会读取这个自报字段。
    last_observed_trading_date_by_module: Optional[
        Mapping[FormalModuleName, Optional[date]]
    ] = None

    @field_validator("checked_at")
    @classmethod
    def checked_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checked_at_timezone_required")
        return value

    @field_validator(
        "stage9_domain_states",
        "rule_version_states",
        "calibration_states",
        "data_quality_states",
        "requested_by_module",
        "enabled_by_module",
        "last_observed_trading_date_by_module",
    )
    @classmethod
    def module_maps_must_be_complete(
        cls,
        value: Mapping[FormalModuleName, object],
    ) -> Optional[Mapping[FormalModuleName, object]]:
        if value is None:
            return None
        if set(value) != set(_MODULES):
            raise ValueError("formal_readiness_module_map_incomplete")
        return _FrozenDict(value)

    @model_validator(mode="after")
    def evidence_must_not_be_from_future(self) -> "FormalReadinessInputs":
        if any(
            item.evidence_type == "formal_operational_checks"
            for item in self.evidence
        ):
            raise ValueError("formal_operational_checks_evidence_reserved")
        for item in self.evidence:
            if (
                item.source_time > self.checked_at
                or item.fetched_at > self.checked_at
                or item.generated_at > self.checked_at
            ):
                raise ValueError("evidence_after_checkedAt")
        return self


def _state_reason(gate: str, state: FormalGateReadiness) -> Tuple[str, ...]:
    if state == "ready":
        return ()
    return (f"{gate}_{state}",)


def _shadow_gate(
    inputs: FormalReadinessInputs,
    module: FormalModuleName,
) -> tuple[FormalGateReadiness, Tuple[str, ...], int, Optional[date]]:
    if inputs.shadow_ledger is None:
        return "not_ready", ("shadow_ledger_missing",), 0, None
    if not isinstance(inputs.shadow_ledger, FormalShadowLedgerV2):
        return "not_ready", ("shadow_ledger_v2_required",), 0, None
    last_observed = inputs.shadow_ledger.latest_ready_trading_date_by_module[module]
    checked_shanghai_date = inputs.checked_at.astimezone(
        _SHANGHAI_TIMEZONE,
    ).date()
    future_reasons = []
    if last_observed is not None and last_observed > checked_shanghai_date:
        future_reasons.append("last_observed_trading_date_after_checkedAt")
        last_observed = None
    observed = (
        inputs.shadow_ledger.latest_ready_streak_by_module[module]
        if module == "etfObservation"
        else inputs.shadow_ledger.ready_trading_days_by_module[module]
    )
    future_observations = tuple(
        observation
        for observation in inputs.shadow_ledger.observations
        if observation.module == module
        and (
            observation.observed_at > inputs.checked_at
            or observation.source_time > inputs.checked_at
            or observation.fetched_at > inputs.checked_at
        )
    )
    if future_observations:
        future_reasons.append("shadow_observation_after_checkedAt")
    if future_reasons:
        return "failed", tuple(future_reasons), observed, last_observed
    required = FORMAL_MODULE_REQUIRED_TRADING_DAYS[module]
    identity_state, identity_reasons = _shadow_identity_gate(inputs)
    if identity_state != "ready":
        if observed < required:
            return "not_ready", ("shadow_days_insufficient",), observed, last_observed
        return identity_state, identity_reasons, observed, last_observed
    ledger = inputs.shadow_ledger
    assert isinstance(ledger, FormalShadowLedgerV2)
    assert ledger.calendar_observed_through is not None
    checked_date = inputs.checked_at.astimezone(_SHANGHAI_TIMEZONE).date()
    if ledger.calendar_observed_through > checked_date:
        return (
            "failed",
            ("shadow_calendar_observed_through_after_checkedAt",),
            observed,
            last_observed,
        )
    provider = OfficialSseCalendarProvider(ledger.calendar_evidence)
    cursor = checked_date
    latest_verified_trading_date = None
    for _ in range(370):
        result = provider.is_trading_day(cursor)
        if result is None:
            return (
                "not_ready" if cursor > ledger.calendar_observed_through else "failed",
                (
                    (
                        "shadow_calendar_observed_through_gap"
                        if cursor > ledger.calendar_observed_through
                        else "shadow_calendar_cutoff_unverified"
                    ),
                ),
                observed,
                last_observed,
            )
        if result:
            latest_verified_trading_date = cursor
            break
        cursor -= timedelta(days=1)
    if latest_verified_trading_date is None:
        return (
            "failed",
            ("shadow_calendar_cutoff_unverified",),
            observed,
            last_observed,
        )
    _start, latest_observed = ledger.observed_trading_date_range_by_module[module]
    if latest_observed != latest_verified_trading_date:
        return (
            "not_ready",
            ("shadow_calendar_observed_through_gap",),
            observed,
            last_observed,
        )
    latest_item = next(
        (
            item for item in ledger.observations
            if item.module == module
            and item.observed_date == latest_verified_trading_date
        ),
        None,
    )
    if latest_item is None:
        return (
            "not_ready",
            ("shadow_calendar_observed_through_gap",),
            observed,
            last_observed,
        )
    if latest_item.observation_status != "ready":
        return (
            "not_ready",
            ("shadow_latest_trading_day_not_ready",),
            observed,
            last_observed,
        )
    policy = inputs.freshness_policy
    if (
        policy is not None
        and inputs.checked_at - latest_item.source_time
        > timedelta(seconds=policy.evidence_max_age_seconds)
    ):
        return (
            "failed",
            ("shadow_evidence_expired",),
            observed,
            last_observed,
        )
    if observed < required:
        return "not_ready", ("shadow_days_insufficient",), observed, last_observed
    if last_observed is None:
        return (
            "not_ready",
            ("last_observed_trading_date_missing",),
            observed,
            None,
        )
    return "ready", (), observed, last_observed


def formal_shadow_ledger_evidence_ref(
    ledger: FormalShadowLedgerV2,
    content_sha256: str,
) -> FormalEvidenceRef:
    """生成正式就绪门唯一认可的、可重复计算的影子台账引用。"""

    ledger = FormalShadowLedgerV2.model_validate(
        ledger.model_dump(mode="json", by_alias=True, warnings="none")
    )
    if formal_shadow_ledger_content_sha256(ledger) != content_sha256:
        raise ValueError("shadow_ledger_sha256_mismatch")
    if not ledger.observations or not ledger.calendar_evidence:
        raise ValueError("shadow_ledger_evidence_incomplete")
    latest_ready_observations = []
    for module in _MODULES:
        ready = tuple(
            item for item in ledger.observations
            if item.module == module and item.observation_status == "ready"
        )
        if ready:
            latest_ready_observations.append(
                max(ready, key=lambda item: item.observed_date)
            )
    if not latest_ready_observations:
        raise ValueError("shadow_ledger_evidence_incomplete")
    # 单一内容引用采用各模块最新 ready 来源中的最早时刻，避免某模块或非 ready
    # 观察刷新其他模块；真正的模块有效期仍在 _shadow_gate 内逐模块判定。
    source_time = min(item.source_time for item in latest_ready_observations)
    fetched_at = max(
        max(item.fetched_at for item in latest_ready_observations),
        max(item.fetched_at for item in ledger.calendar_evidence),
    )
    generated_at = max(
        max(item.observed_at for item in latest_ready_observations),
        fetched_at,
    )
    # 不同观察的最大 sourceTime 仍不可能晚于所有观察的最大 fetchedAt。
    return FormalEvidenceRef(
        evidenceType="formal_shadow_ledger",
        contractVersion="radar-formal-shadow-ledger-v2",
        contentSha256=content_sha256,
        subjectId="radar-formal-shadow-ledger-v2",
        sourceTime=source_time,
        fetchedAt=fetched_at,
        generatedAt=generated_at,
    )


def _shadow_identity_gate(
    inputs: FormalReadinessInputs,
) -> tuple[FormalGateReadiness, Tuple[str, ...]]:
    ledger = inputs.shadow_ledger
    if not isinstance(ledger, FormalShadowLedgerV2):
        return "not_ready", ("shadow_ledger_v2_required",)
    if not inputs.shadow_ledger_sha256:
        return "not_ready", ("shadow_ledger_sha256_missing",)
    try:
        actual_sha256 = formal_shadow_ledger_content_sha256(ledger)
    except (TypeError, ValueError):
        return "failed", ("shadow_ledger_unverified",)
    if actual_sha256 != inputs.shadow_ledger_sha256:
        return "failed", ("shadow_ledger_sha256_mismatch",)
    refs = tuple(
        item for item in inputs.evidence
        if item.evidence_type == "formal_shadow_ledger"
    )
    if not refs:
        return "not_ready", ("shadow_ledger_evidence_missing",)
    if len(refs) != 1:
        return "failed", ("shadow_ledger_evidence_not_unique",)
    ref = refs[0]
    if ref.contract_version != "radar-formal-shadow-ledger-v2":
        return "failed", ("shadow_ledger_evidence_contract_mismatch",)
    if ref.content_sha256 != actual_sha256:
        return "failed", ("shadow_ledger_evidence_sha256_mismatch",)
    if ref.subject_id != "radar-formal-shadow-ledger-v2":
        return "failed", ("shadow_ledger_evidence_subject_mismatch",)
    try:
        expected_ref = formal_shadow_ledger_evidence_ref(ledger, actual_sha256)
    except (TypeError, ValueError):
        return "failed", ("shadow_ledger_calendar_evidence_missing",)
    if ref != expected_ref:
        return "failed", ("shadow_ledger_evidence_time_mismatch",)
    if ledger.calendar_observed_through is None or not ledger.calendar_evidence:
        return "not_ready", ("shadow_ledger_calendar_evidence_missing",)
    return "ready", ()


def _module_state(gates: Tuple[FormalGateState, ...]) -> Literal[
    "collecting", "not_ready", "ready_to_enable", "failed"
]:
    states = tuple(gate.state for gate in gates)
    if "failed" in states:
        return "failed"
    if all(state == "ready" for state in states):
        return "ready_to_enable"
    if "not_ready" in states:
        return "not_ready"
    return "collecting"


def _gate(
    name: str,
    state: FormalGateReadiness,
    reason_codes: Tuple[str, ...] | None = None,
) -> FormalGateState:
    return FormalGateState(
        gate=name,
        state=state,
        required=True,
        reasonCodes=reason_codes if reason_codes is not None else _state_reason(name, state),
    )


def _stage9_gate(
    inputs: FormalReadinessInputs,
) -> tuple[FormalGateReadiness, Tuple[str, ...]]:
    if inputs.stage9_quality_state == "failed":
        return "failed", ("stage9_quality_failed",)
    if not inputs.stage9_replay_run_id or not inputs.stage9_quality_sha256:
        return "not_ready", ("stage9_quality_identity_missing",)
    quality_items = tuple(
        item for item in inputs.evidence if item.evidence_type == "stage9_quality"
    )
    if not quality_items:
        if not tuple(
            item for item in inputs.evidence
            if item.evidence_type != "formal_shadow_ledger"
        ):
            return "not_ready", ("stage9_evidence_missing",)
        return "not_ready", ("stage9_evidence_type_mismatch",)
    if len(quality_items) != 1:
        return "not_ready", ("stage9_evidence_not_unique",)
    quality_item = quality_items[0]
    if quality_item.contract_version != "radar-replay-quality-v2":
        return "not_ready", ("stage9_evidence_contract_mismatch",)
    if quality_item.content_sha256 != inputs.stage9_quality_sha256:
        return "not_ready", ("stage9_evidence_sha256_mismatch",)
    if quality_item.subject_id != inputs.stage9_replay_run_id:
        return "not_ready", ("stage9_evidence_subject_mismatch",)
    return "ready", ()


def _operational_gates(
    inputs: FormalReadinessInputs,
) -> Mapping[str, tuple[FormalGateReadiness, Tuple[str, ...]]]:
    """把唯一强类型检查包映射到既有四个冻结门。

    缺包是尚未收集；任何内容寻址、主体、时间或内层合同异常均失败关闭。
    不接受旧调用方提供的散装状态字段。
    """

    names = ("performance", "security", "rollback", "runbook")
    if inputs.operational_checks is None:
        return {
            name: ("not_ready", ("operational_checks_missing",))
            for name in names
        }
    if not inputs.operational_checks_sha256:
        return {
            name: ("failed", ("operational_checks_sha256_missing",))
            for name in names
        }
    try:
        checks = RadarFormalOperationalChecks.model_validate(
            inputs.operational_checks.model_dump(mode="json", by_alias=True),
        )
        if canonical_operational_checks_sha256(checks) != inputs.operational_checks_sha256:
            raise ValueError("operational_checks_sha256_mismatch")
        if inputs.stage9_replay_run_id and checks.subject_id != inputs.stage9_replay_run_id:
            raise ValueError("operational_checks_subject_mismatch")
        if checks.checked_at > inputs.checked_at:
            raise ValueError("operational_checks_after_checkedAt")
        if not _operational_collector_input_verified(checks):
            raise ValueError("operational_collector_input_unverified")
        by_gate = {item.gate: item for item in checks.gates}
        if set(by_gate) != set(names):
            raise ValueError("operational_checks_gates_incomplete")
    except (TypeError, ValueError):
        return {
            name: ("failed", ("operational_checks_unverified",))
            for name in names
        }
    return {
        name: (by_gate[name].state, by_gate[name].reason_codes)
        for name in names
    }


def _operational_collector_input_verified(
    checks: RadarFormalOperationalChecks,
) -> bool:
    reference = checks.collector_input_ref
    if reference is None:
        return False
    try:
        raw = _read_relative_bytes(
            _OPERATIONAL_COLLECTOR_ROOT,
            reference.relative_path,
            maximum=_MAX_OPERATIONAL_COLLECTOR_INPUT_BYTES,
        )
        if hashlib.sha256(raw).hexdigest() != reference.content_sha256:
            return False
        source = OperationalEvidenceInput.model_validate(strict_json_loads(
            raw,
            error_code="operational_collector_input_unverified",
        ))
        if not (
            source.subject_id == checks.subject_id
            and source.checked_at == checks.checked_at
        ):
            return False
        rebuilt = _rebuild_operational_checks(checks)
        return (
            canonical_operational_checks_sha256(rebuilt)
            == canonical_operational_checks_sha256(checks)
            and rebuilt.model_dump(mode="json", by_alias=True)
            == checks.model_dump(mode="json", by_alias=True)
        )
    except (OSError, TypeError, ValueError):
        return False


def _rebuild_operational_checks(
    checks: RadarFormalOperationalChecks,
) -> RadarFormalOperationalChecks:
    """只从部署侧固定根重放正式运营证据，不接受请求载荷自报根目录。"""

    reference = checks.collector_input_ref
    if reference is None:
        raise ValueError("operational_collector_input_unverified")
    return collect_and_build_operational_checks(
        reference,
        snapshot_root=_OPERATIONAL_COLLECTOR_ROOT,
        asset_root=_OPERATIONAL_ASSET_ROOT,
        project_asset_root=_OPERATIONAL_ASSET_ROOT,
    )


def _freshness_data_quality_gate(
    inputs: FormalReadinessInputs,
) -> tuple[FormalGateReadiness, Tuple[str, ...]]:
    policy = inputs.freshness_policy
    if policy is None:
        return "not_ready", ("formal_freshness_policy_missing",)
    for item in inputs.evidence:
        if item.evidence_type == "formal_shadow_ledger":
            continue
        if inputs.checked_at - item.source_time > timedelta(
            seconds=policy.evidence_max_age_seconds,
        ):
            return "failed", ("formal_evidence_expired",)
    if (
        inputs.operational_checks is not None
        and (
            inputs.checked_at - inputs.operational_checks.checked_at
            > timedelta(seconds=policy.operational_checks_max_age_seconds)
            or any(
                inputs.checked_at - item.source_time
                > timedelta(
                    seconds=policy.operational_checks_max_age_seconds,
                )
                for item in inputs.operational_checks.evidence
            )
        )
    ):
        return "failed", ("formal_operational_checks_expired",)
    return "ready", ()


def _merge_data_quality_gate(
    configured_state: FormalGateReadiness,
    freshness_state: FormalGateReadiness,
    freshness_reasons: Tuple[str, ...],
) -> tuple[FormalGateReadiness, Tuple[str, ...]]:
    if freshness_state == "failed":
        return freshness_state, freshness_reasons
    if configured_state == "failed":
        return configured_state, _state_reason("data_quality", configured_state)
    if freshness_state == "not_ready":
        return freshness_state, freshness_reasons
    return configured_state, _state_reason("data_quality", configured_state)


def _operational_summary_evidence(
    inputs: FormalReadinessInputs,
) -> Optional[FormalEvidenceRef]:
    checks = inputs.operational_checks
    expected_sha256 = inputs.operational_checks_sha256
    if checks is None or expected_sha256 is None:
        return None
    try:
        checks = RadarFormalOperationalChecks.model_validate(
            checks.model_dump(mode="json", by_alias=True),
        )
        if canonical_operational_checks_sha256(checks) != expected_sha256:
            return None
        if (
            inputs.stage9_replay_run_id
            and checks.subject_id != inputs.stage9_replay_run_id
        ):
            return None
        if checks.checked_at > inputs.checked_at:
            return None
        if not _operational_collector_input_verified(checks):
            return None
    except (TypeError, ValueError):
        return None
    return FormalEvidenceRef(
        evidenceType="formal_operational_checks",
        contractVersion="radar-formal-operational-checks-v1",
        contentSha256=expected_sha256,
        subjectId=checks.subject_id,
        sourceTime=checks.checked_at,
        fetchedAt=checks.checked_at,
        generatedAt=checks.checked_at,
    )


def build_formal_readiness(inputs: FormalReadinessInputs) -> RadarFormalReadiness:
    """组合强类型输入，并从固定只读工件根重放运营证据。"""

    if not isinstance(inputs, FormalReadinessInputs):
        raise TypeError("formal_readiness_inputs_invalid")
    inputs = FormalReadinessInputs.model_validate(
        inputs.model_dump(warnings="none"),
    )
    for item in inputs.evidence:
        if (
            item.source_time > inputs.checked_at
            or item.fetched_at > inputs.checked_at
            or item.generated_at > inputs.checked_at
        ):
            raise ValueError("evidence_after_checkedAt")
    stage9_binding_state, stage9_binding_reasons = _stage9_gate(inputs)
    operational_gates = _operational_gates(inputs)
    freshness_state, freshness_reasons = _freshness_data_quality_gate(inputs)
    modules = []
    for module in _MODULES:
        shadow_state, shadow_reasons, observed, last_observed = _shadow_gate(
            inputs,
            module,
        )
        if stage9_binding_state == "ready":
            module_stage9_state = inputs.stage9_domain_states[module]
            stage9_reasons = _state_reason(
                "stage9_domain",
                module_stage9_state,
            )
        else:
            module_stage9_state = stage9_binding_state
            stage9_reasons = stage9_binding_reasons
        data_quality_state, data_quality_reasons = _merge_data_quality_gate(
            inputs.data_quality_states[module],
            freshness_state,
            freshness_reasons,
        )
        gates = (
            _gate("stage9_quality", module_stage9_state, stage9_reasons),
            _gate("rule_version", inputs.rule_version_states[module]),
            _gate("calibration", inputs.calibration_states[module]),
            _gate("shadow_ledger", shadow_state, shadow_reasons),
            _gate("data_quality", data_quality_state, data_quality_reasons),
            _gate("performance", *operational_gates["performance"]),
            _gate("security", *operational_gates["security"]),
            _gate("rollback", *operational_gates["rollback"]),
            _gate("runbook", *operational_gates["runbook"]),
        )
        reasons = tuple(
            reason
            for gate in gates
            for reason in gate.reason_codes
        )
        state = _module_state(gates)
        requested = inputs.requested_by_module[module]
        enabled = inputs.enabled_by_module[module]
        modules.append(FormalModuleReadiness(
            module=module,
            state=state,
            requested=requested,
            configuredEnabled=enabled,
            formalEnabled=False,
            observedTradingDays=observed,
            requiredTradingDays=FORMAL_MODULE_REQUIRED_TRADING_DAYS[module],
            lastObservedTradingDate=last_observed,
            gates=gates,
            reasonCodes=reasons,
        ))
    module_tuple = tuple(modules)
    reported_stage9_state = inputs.stage9_quality_state
    if reported_stage9_state == "ready" and stage9_binding_state != "ready":
        reported_stage9_state = stage9_binding_state
    operational_summary = _operational_summary_evidence(inputs)
    report_evidence = inputs.evidence + (
        (operational_summary,) if operational_summary is not None else ()
    )
    return RadarFormalReadiness(
        checkedAt=inputs.checked_at,
        freshnessPolicy=inputs.freshness_policy,
        state=(
            "failed" if any(item.state == "failed" for item in module_tuple)
            else "ready_to_enable" if any(item.state == "ready_to_enable" for item in module_tuple)
            else "not_ready" if any(item.state == "not_ready" for item in module_tuple)
            else "collecting"
        ),
        anyFormalEnabled=False,
        allModulesFormalEnabled=False,
        stage9ReplayRunId=inputs.stage9_replay_run_id,
        stage9QualitySha256=inputs.stage9_quality_sha256,
        stage9QualityState=reported_stage9_state,
        modules=module_tuple,
        evidence=report_evidence,
        reasonCodes=tuple(
            sorted({reason for item in module_tuple for reason in item.reason_codes})
        ),
    )
