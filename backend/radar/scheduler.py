"""默认关闭的雷达影子调度接入合同。

本模块不创建或启动APScheduler，不连接数据库，不读取生产路径，也不请求
外部来源。调用方必须显式提供一次性执行器、锁路径和调度器。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Union

from radar.config import RadarSettings
from radar.formal_execution_contracts import (
    FORMAL_JOB_IDS,
    FormalExecutionResult,
    FormalExecutor,
)
from radar.formal_execution_guard import (
    FormalExecutionBindingLoader,
    FormalExecutionGuardDecision,
    evaluate_formal_execution_guard,
)
from radar.formal_readiness_contracts import FormalModuleName
from radar.formal_readiness_store import (
    FormalReadinessLoadResult,
)
from radar.formal_shadow_input_bundle import (
    PrivateTmpFileLockIdentity,
    PrivateTmpNoFollowFileLock,
)
from radar.run_lock import CrossProcessFileLock


UTC = timezone.utc
RADAR_SHADOW_JOB_ID = "radar-shadow-scan"
LOGGER = logging.getLogger(__name__)
PathLike = Union[str, os.PathLike]


class ShadowResultLike(Protocol):
    status: str


ExecuteOnce = Callable[[str, datetime], ShadowResultLike]
ReadinessCheck = Callable[[datetime], Optional[str]]
StartedCallback = Callable[[], None]
SettingsProvider = Callable[[], RadarSettings]
FormalReadinessLoader = Callable[[], FormalReadinessLoadResult]
Clock = Callable[[], datetime]


class ScheduledRunState(str, Enum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    LOCKED = "locked"
    COMPLETED = "completed"


class FormalRunState(str, Enum):
    BLOCKED = "blocked"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ScheduledRunOutcome:
    state: ScheduledRunState
    radar_run_id: Optional[str] = None
    result_status: Optional[str] = None
    item_count: Optional[int] = None
    gate_passed: Optional[bool] = None
    gate_reasons: tuple[str, ...] = ()
    skip_reason: Optional[str] = None
    duration_seconds: float = 0.0


class ScheduleRegistrationState(str, Enum):
    DISABLED = "disabled"
    ALREADY_REGISTERED = "already_registered"
    REGISTERED = "registered"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ScheduleRegistration:
    state: ScheduleRegistrationState
    job_id: str = RADAR_SHADOW_JOB_ID
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class ShadowJobSpec:
    job_id: str
    job: Callable[[], Any]
    interval_seconds: int
    next_run_time: Optional[datetime] = None


@dataclass(frozen=True)
class FormalJobSpec:
    """仅由调用方显式提供的正式任务规格；本模块不创建生产任务。"""

    module: FormalModuleName
    job_id: str
    executor: FormalExecutor
    config_sha256: str
    binding_loader: FormalExecutionBindingLoader
    lock_path: Path
    interval_seconds: int
    next_run_time: Optional[datetime] = None


@dataclass(frozen=True)
class FormalRunOutcome:
    state: FormalRunState
    reason_code: Optional[str] = None
    result: Optional[FormalExecutionResult] = None


class SchedulerLike(Protocol):
    def get_job(self, job_id: str) -> Any:
        ...

    def add_job(self, func: Callable[[], Any], trigger: str, **kwargs: Any) -> Any:
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("雷达调度asOf必须包含时区")
    return value.astimezone(UTC)


def _build_run_id(as_of: datetime, prefix: str = "radar-shadow") -> str:
    prefix = prefix.strip()
    if not prefix:
        raise ValueError("雷达运行标识前缀不能为空")
    return f"{prefix}-{as_of.strftime('%Y%m%dT%H%M%S%fZ')}"


_FORMAL_REQUEST_FIELDS = {
    "trendRotation": "formal_trend_requested",
    "etfObservation": "formal_etf_requested",
    "leaderObservation": "formal_leader_requested",
}


def _formal_invocation_id(job_id: str, _clock: Clock) -> str:
    return f"{job_id}-{time.monotonic_ns()}"


def _formal_spec_reason(spec: FormalJobSpec) -> Optional[str]:
    if not isinstance(spec, FormalJobSpec):
        return "formal_spec_invalid"
    if (
        not isinstance(spec.module, str)
        or spec.module not in _FORMAL_REQUEST_FIELDS
    ):
        return "formal_module_invalid"
    if not isinstance(spec.job_id, str):
        return "formal_execution_job_identity_mismatch"
    if spec.job_id != FORMAL_JOB_IDS[spec.module]:
        return "formal_execution_job_identity_mismatch"
    if not callable(spec.executor):
        return "formal_executor_invalid"
    if (
        not isinstance(spec.config_sha256, str)
        or len(spec.config_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in spec.config_sha256
        )
    ):
        return "formal_execution_config_sha256_invalid"
    if not callable(spec.binding_loader):
        return "formal_execution_binding_loader_missing"
    if (
        type(spec.interval_seconds) is not int
        or spec.interval_seconds <= 0
    ):
        return "formal_interval_invalid"
    if spec.next_run_time is not None:
        try:
            _aware_utc(spec.next_run_time)
        except Exception:
            return "formal_next_run_time_invalid"
    if _validated_formal_lock_path(spec.lock_path) is None:
        return "formal_execution_lock_path_invalid"
    return None


def _validated_formal_lock_path(value: Optional[PathLike]) -> Optional[Path]:
    if not isinstance(value, Path):
        return None
    try:
        candidate = Path(os.path.abspath(os.fspath(value)))
        private_tmp = Path("/private/tmp")
        candidate.relative_to(private_tmp)
        parent = candidate.parent.resolve(strict=True)
        parent.relative_to(private_tmp)
        if not parent.is_dir():
            return None
        current = private_tmp
        for part in parent.relative_to(private_tmp).parts:
            current = current / part
            if current.is_symlink():
                return None
        if candidate.exists() and (
            candidate.is_symlink() or not candidate.is_file()
        ):
            return None
        return candidate
    except (OSError, TypeError, ValueError):
        return None


def _evaluate_spec_guard(
    spec: FormalJobSpec,
    *,
    settings_provider: SettingsProvider,
    readiness_loader: FormalReadinessLoader,
    clock: Clock,
    invocation_id: str,
) -> FormalExecutionGuardDecision:
    reason = _formal_spec_reason(spec)
    if reason:
        return FormalExecutionGuardDecision(False, reason)
    try:
        settings = settings_provider()
    except Exception:
        return FormalExecutionGuardDecision(False, "formal_settings_unverified")
    if not isinstance(settings, RadarSettings):
        return FormalExecutionGuardDecision(False, "formal_settings_unverified")
    request_value = getattr(settings, _FORMAL_REQUEST_FIELDS[spec.module])
    if type(request_value) is not bool:
        return FormalExecutionGuardDecision(False, "formal_request_invalid")
    try:
        now = _aware_utc(clock())
    except Exception:
        return FormalExecutionGuardDecision(False, "formal_clock_unverified")
    assert spec.executor is not None
    assert spec.binding_loader is not None
    return evaluate_formal_execution_guard(
        module=spec.module,
        job_id=spec.job_id,
        executor=spec.executor,
        config_sha256=spec.config_sha256,
        requested=request_value,
        binding_loader=spec.binding_loader,
        readiness_loader=readiness_loader,
        now=now,
        invocation_id=invocation_id,
    )


def _same_formal_wrapper(
    existing: Any,
    *,
    spec: FormalJobSpec,
    binding_sha256: str,
    lock_identity: PrivateTmpFileLockIdentity,
) -> bool:
    try:
        wrapped = getattr(existing, "func", existing)
    except Exception:
        return False
    if type(wrapped) is not ScheduledFormalJob:
        return False
    try:
        wrapped_lock_path = _validated_formal_lock_path(
            wrapped.spec.lock_path,
        )
        spec_lock_path = _validated_formal_lock_path(spec.lock_path)
        wrapped_next_run_time = (
            None
            if wrapped.spec.next_run_time is None
            else _aware_utc(wrapped.spec.next_run_time)
        )
        spec_next_run_time = (
            None
            if spec.next_run_time is None
            else _aware_utc(spec.next_run_time)
        )
        return (
            wrapped.formal_wrapper_contract_version
            == "radar-scheduled-formal-job-v1"
            and type(wrapped.spec) is FormalJobSpec
            and wrapped.spec.module == spec.module
            and wrapped.spec.job_id == spec.job_id
            and wrapped.registered_binding_sha256 == binding_sha256
            and wrapped.registered_executor_id
            == getattr(spec.executor, "executor_id", None)
            and wrapped.registered_executor_contract_version
            == getattr(spec.executor, "contract_version", None)
            and getattr(wrapped.spec.executor, "module", None)
            == wrapped.spec.module
            and getattr(wrapped.spec.executor, "executor_id", None)
            == wrapped.registered_executor_id
            and getattr(wrapped.spec.executor, "contract_version", None)
            == wrapped.registered_executor_contract_version
            and wrapped.spec.config_sha256 == spec.config_sha256
            and wrapped_lock_path is not None
            and wrapped_lock_path == spec_lock_path
            and wrapped.registered_lock_identity == lock_identity
            and lock_identity.normalized_path == os.fspath(spec_lock_path)
            and type(wrapped.spec.interval_seconds) is int
            and wrapped.spec.interval_seconds == spec.interval_seconds
            and wrapped_next_run_time == spec_next_run_time
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class ScheduledFormalJob:
    """正式任务 wrapper：锁前与锁内均完整复验。"""

    spec: FormalJobSpec
    settings_provider: SettingsProvider
    readiness_loader: FormalReadinessLoader
    clock: Clock = _utc_now
    registered_binding_sha256: Optional[str] = None
    registered_executor_id: Optional[str] = None
    registered_executor_contract_version: Optional[str] = None
    registered_lock_identity: Optional[PrivateTmpFileLockIdentity] = None

    @property
    def formal_wrapper_contract_version(self) -> str:
        return "radar-scheduled-formal-job-v1"

    def _guard(self, invocation_id: str) -> FormalExecutionGuardDecision:
        return _evaluate_spec_guard(
            self.spec,
            settings_provider=self.settings_provider,
            readiness_loader=self.readiness_loader,
            clock=self.clock,
            invocation_id=invocation_id,
        )

    def __call__(self) -> FormalRunOutcome:
        invocation_id = _formal_invocation_id(self.spec.job_id, self.clock)
        outer = self._guard(invocation_id)
        if not outer.allowed:
            return FormalRunOutcome(
                state=FormalRunState.BLOCKED,
                reason_code=outer.reason_code,
            )
        if (
            self.registered_binding_sha256 is None
            or outer.binding is None
            or outer.binding.content_sha256
            != self.registered_binding_sha256
        ):
            return FormalRunOutcome(
                state=FormalRunState.BLOCKED,
                reason_code="formal_execution_binding_changed",
            )
        try:
            if self.registered_lock_identity is None:
                raise ValueError("formal_execution_lock_unverified")
            lock = PrivateTmpNoFollowFileLock(
                self.spec.lock_path,
                protect_root=True,
                expected_identity=self.registered_lock_identity,
            )
            lock_acquired = lock.acquire(blocking=False)
        except (OSError, TypeError, ValueError):
            return FormalRunOutcome(
                state=FormalRunState.BLOCKED,
                reason_code="formal_execution_lock_unverified",
            )
        if not lock_acquired:
            return FormalRunOutcome(
                state=FormalRunState.BLOCKED,
                reason_code="formal_execution_lock_contended",
            )
        try:
            inner = self._guard(invocation_id)
            if not inner.allowed:
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code=inner.reason_code,
                )
            if (
                inner.binding is None
                or inner.binding.content_sha256
                != self.registered_binding_sha256
            ):
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code="formal_execution_binding_changed",
                )
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code="formal_execution_lock_unverified",
                )
            assert inner.context is not None
            assert self.spec.executor is not None
            try:
                execution_started_at = _aware_utc(self.clock())
            except Exception as error:
                raise ValueError("formal_clock_unverified") from error
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code="formal_execution_lock_unverified",
                )
            raw_result = self.spec.executor(inner.context)
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code="formal_execution_lock_unverified",
                )
            try:
                execution_finished_at = _aware_utc(self.clock())
            except Exception as error:
                raise ValueError("formal_clock_unverified") from error
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                return FormalRunOutcome(
                    state=FormalRunState.BLOCKED,
                    reason_code="formal_execution_lock_unverified",
                )
            if not isinstance(raw_result, FormalExecutionResult):
                raise TypeError("formal_execution_result_invalid")
            if set(raw_result.__dict__) != set(FormalExecutionResult.model_fields):
                raise TypeError("formal_execution_result_invalid")
            result = FormalExecutionResult.model_validate(
                raw_result.model_dump(mode="json", by_alias=True),
            )
            if (
                execution_finished_at < execution_started_at
                or result.started_at < execution_started_at
                or result.finished_at > execution_finished_at
            ):
                raise ValueError("formal_execution_result_window_mismatch")
            return FormalRunOutcome(
                state=FormalRunState.COMPLETED,
                result=result,
            )
        finally:
            lock.release()


@dataclass(frozen=True)
class ScheduledShadowJob:
    """为一次性影子执行器增加功能开关、跨进程锁和任务审计日志。"""

    settings: RadarSettings
    execute_once: ExecuteOnce
    lock_path: PathLike
    clock: Callable[[], datetime] = _utc_now
    readiness_check: Optional[ReadinessCheck] = None
    run_id_prefix: str = "radar-shadow"
    on_started: Optional[StartedCallback] = None

    def __call__(self) -> ScheduledRunOutcome:
        if not self.settings.enabled or not self.settings.shadow_mode:
            LOGGER.info("雷达影子调度跳过：功能开关关闭")
            return ScheduledRunOutcome(
                state=ScheduledRunState.DISABLED,
                skip_reason="feature_disabled",
            )

        if self.readiness_check is not None:
            readiness_as_of = _aware_utc(self.clock())
            skip_reason = self.readiness_check(readiness_as_of)
            if skip_reason:
                LOGGER.info("雷达影子调度跳过：%s", skip_reason)
                return ScheduledRunOutcome(
                    state=ScheduledRunState.SKIPPED,
                    skip_reason=str(skip_reason),
                )

        lock = CrossProcessFileLock(self.lock_path)
        if not lock.acquire(blocking=False):
            LOGGER.warning("雷达影子调度跳过：已有进程持有任务锁")
            return ScheduledRunOutcome(
                state=ScheduledRunState.LOCKED,
                skip_reason="lock_contended",
            )

        started = time.monotonic()
        radar_run_id: Optional[str] = None
        try:
            as_of = _aware_utc(self.clock())
            radar_run_id = _build_run_id(as_of, self.run_id_prefix)
            LOGGER.info("雷达影子调度开始：run_id=%s", radar_run_id)
            if self.on_started is not None:
                self.on_started()
            result = self.execute_once(radar_run_id, as_of)
        except Exception:
            LOGGER.exception(
                "雷达影子调度失败：run_id=%s duration=%.3fs",
                radar_run_id or "unassigned",
                time.monotonic() - started,
            )
            raise
        finally:
            lock.release()

        duration = time.monotonic() - started
        gate_passed = getattr(result, "gate_passed", None)
        raw_gate_reasons = (
            getattr(result, "gate_reasons", ())
            if gate_passed is False
            else ()
        )
        gate_reasons = (
            tuple(raw_gate_reasons)
            if isinstance(raw_gate_reasons, (list, tuple))
            else ()
        )
        if gate_passed is False:
            LOGGER.warning(
                "雷达影子门禁拒绝：run_id=%s gate_reasons=%s",
                radar_run_id,
                ",".join(gate_reasons) or "unspecified",
            )
        LOGGER.info(
            "雷达影子调度完成：run_id=%s status=%s duration=%.3fs",
            radar_run_id,
            result.status,
            duration,
        )
        return ScheduledRunOutcome(
            state=ScheduledRunState.COMPLETED,
            radar_run_id=radar_run_id,
            result_status=result.status,
            item_count=getattr(result, "item_count", None),
            gate_passed=gate_passed,
            gate_reasons=gate_reasons,
            duration_seconds=duration,
        )


def register_shadow_jobs(
    scheduler: SchedulerLike,
    specs: tuple[ShadowJobSpec, ...],
    settings: RadarSettings,
) -> tuple[ScheduleRegistration, ...]:
    """注册相互独立的影子任务；默认关闭且永不启动调度器。"""

    if not settings.enabled or not settings.shadow_mode:
        return tuple(
            ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=spec.job_id,
            )
            for spec in specs
        )

    registrations = []
    seen_ids = set()
    for spec in specs:
        if not spec.job_id.strip():
            raise ValueError("雷达调度任务ID不能为空")
        if spec.job_id in seen_ids:
            raise ValueError(f"雷达调度任务ID重复：{spec.job_id}")
        if spec.interval_seconds <= 0:
            raise ValueError("雷达扫描间隔必须大于0秒")
        seen_ids.add(spec.job_id)

        if scheduler.get_job(spec.job_id) is not None:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.ALREADY_REGISTERED,
                job_id=spec.job_id,
            ))
            continue
        job_options = {
            "id": spec.job_id,
            "seconds": spec.interval_seconds,
            "max_instances": 1,
            "coalesce": True,
            "replace_existing": False,
            "misfire_grace_time": spec.interval_seconds,
        }
        if spec.next_run_time is not None:
            job_options["next_run_time"] = _aware_utc(spec.next_run_time)
        scheduler.add_job(spec.job, "interval", **job_options)
        registrations.append(ScheduleRegistration(
            state=ScheduleRegistrationState.REGISTERED,
            job_id=spec.job_id,
        ))
    return tuple(registrations)


def register_shadow_job(
    scheduler: SchedulerLike,
    job: ScheduledShadowJob,
    settings: RadarSettings,
) -> ScheduleRegistration:
    """显式配置传入的调度器，但不创建或启动调度器。"""

    return register_shadow_jobs(
        scheduler,
        (ShadowJobSpec(
            RADAR_SHADOW_JOB_ID,
            job,
            settings.stock_scan_interval_seconds,
        ),),
        settings,
    )[0]


def register_formal_jobs(
    scheduler: SchedulerLike,
    specs: tuple[FormalJobSpec, ...],
    *,
    settings_provider: SettingsProvider,
    readiness_loader: FormalReadinessLoader,
    clock: Clock = _utc_now,
) -> tuple[ScheduleRegistration, ...]:
    """注册显式正式任务缝；注册与执行均失败关闭且不启动调度器。"""

    if not specs:
        return ()
    structural_reasons = tuple(_formal_spec_reason(spec) for spec in specs)
    valid_job_ids = [
        spec.job_id
        for spec, reason in zip(specs, structural_reasons)
        if reason is None
    ]
    valid_modules = [
        spec.module
        for spec, reason in zip(specs, structural_reasons)
        if reason is None
    ]
    valid_lock_paths = [
        _validated_formal_lock_path(spec.lock_path)
        for spec, reason in zip(specs, structural_reasons)
        if reason is None
    ]
    valid_lock_roots = [
        lock_path.parent
        for lock_path in valid_lock_paths
        if lock_path is not None
    ]

    registrations = []
    for spec, structural_reason in zip(specs, structural_reasons):
        if structural_reason is not None:
            invalid_job_id = getattr(spec, "job_id", "formal-spec-invalid")
            if not isinstance(invalid_job_id, str) or not invalid_job_id:
                invalid_job_id = "formal-spec-invalid"
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=invalid_job_id,
                reason_code=structural_reason,
            ))
            continue
        if valid_job_ids.count(spec.job_id) > 1:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.CONFLICT,
                job_id=spec.job_id,
                reason_code="formal_job_id_duplicate",
            ))
            continue
        if valid_modules.count(spec.module) > 1:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.CONFLICT,
                job_id=spec.job_id,
                reason_code="formal_module_duplicate",
            ))
            continue
        lock_path = _validated_formal_lock_path(spec.lock_path)
        if valid_lock_paths.count(lock_path) > 1:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.CONFLICT,
                job_id=spec.job_id,
                reason_code="formal_lock_path_not_independent",
            ))
            continue
        assert lock_path is not None
        if valid_lock_roots.count(lock_path.parent) > 1:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.CONFLICT,
                job_id=spec.job_id,
                reason_code="formal_lock_root_not_independent",
            ))
            continue

        invocation_id = f"registration-{spec.job_id}"
        outer = _evaluate_spec_guard(
            spec,
            settings_provider=settings_provider,
            readiness_loader=readiness_loader,
            clock=clock,
            invocation_id=invocation_id,
        )
        if not outer.allowed:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=spec.job_id,
                reason_code=outer.reason_code,
            ))
            continue
        try:
            lock = PrivateTmpNoFollowFileLock(
                spec.lock_path,
                protect_root=True,
            )
            lock_acquired = lock.acquire(blocking=False)
        except (OSError, TypeError, ValueError):
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=spec.job_id,
                reason_code="formal_execution_lock_unverified",
            ))
            continue
        if not lock_acquired:
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=spec.job_id,
                reason_code="formal_execution_lock_contended",
            ))
            continue
        try:
            inner = _evaluate_spec_guard(
                spec,
                settings_provider=settings_provider,
                readiness_loader=readiness_loader,
                clock=clock,
                invocation_id=invocation_id,
            )
            if not inner.allowed or inner.binding is None:
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code=(
                        inner.reason_code
                        or "formal_execution_binding_unverified"
                    ),
                ))
                continue
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_execution_lock_unverified",
                ))
                continue
            if lock.identity is None:
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_execution_lock_unverified",
                ))
                continue
            try:
                existing = scheduler.get_job(spec.job_id)
            except Exception:
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_scheduler_unverified",
                ))
                continue
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_execution_lock_unverified",
                ))
                continue
            if existing is not None:
                if _same_formal_wrapper(
                    existing,
                    spec=spec,
                    binding_sha256=inner.binding.content_sha256,
                    lock_identity=lock.identity,
                ):
                    registrations.append(ScheduleRegistration(
                        state=ScheduleRegistrationState.ALREADY_REGISTERED,
                        job_id=spec.job_id,
                    ))
                else:
                    registrations.append(ScheduleRegistration(
                        state=ScheduleRegistrationState.CONFLICT,
                        job_id=spec.job_id,
                        reason_code="formal_existing_job_identity_conflict",
                    ))
                continue
            scheduled = ScheduledFormalJob(
                spec=spec,
                settings_provider=settings_provider,
                readiness_loader=readiness_loader,
                clock=clock,
                registered_binding_sha256=inner.binding.content_sha256,
                registered_executor_id=getattr(
                    spec.executor,
                    "executor_id",
                    None,
                ),
                registered_executor_contract_version=getattr(
                    spec.executor,
                    "contract_version",
                    None,
                ),
                registered_lock_identity=lock.identity,
            )
            job_options = {
                "id": spec.job_id,
                "seconds": spec.interval_seconds,
                "max_instances": 1,
                "coalesce": True,
                "replace_existing": False,
                "misfire_grace_time": spec.interval_seconds,
            }
            if spec.next_run_time is not None:
                job_options["next_run_time"] = _aware_utc(
                    spec.next_run_time,
                )
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_execution_lock_unverified",
                ))
                continue
            try:
                scheduler.add_job(scheduled, "interval", **job_options)
            except Exception:
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_scheduler_unverified",
                ))
                continue
            try:
                lock.assert_still_held()
            except (OSError, TypeError, ValueError):
                registrations.append(ScheduleRegistration(
                    state=ScheduleRegistrationState.DISABLED,
                    job_id=spec.job_id,
                    reason_code="formal_execution_lock_unverified",
                ))
                continue
            registrations.append(ScheduleRegistration(
                state=ScheduleRegistrationState.REGISTERED,
                job_id=spec.job_id,
            ))
        finally:
            lock.release()
    return tuple(registrations)
