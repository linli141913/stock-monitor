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
from typing import Any, Callable, Optional, Protocol, Union

from radar.config import RadarSettings
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


class ScheduledRunState(str, Enum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    LOCKED = "locked"
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


@dataclass(frozen=True)
class ScheduleRegistration:
    state: ScheduleRegistrationState
    job_id: str = RADAR_SHADOW_JOB_ID


@dataclass(frozen=True)
class ShadowJobSpec:
    job_id: str
    job: Callable[[], Any]
    interval_seconds: int
    next_run_time: Optional[datetime] = None


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
