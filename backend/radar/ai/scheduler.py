from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import monitoring_health

from radar.ai.config import RadarAiSettings, load_radar_ai_settings
from radar.ai.contracts import FrozenRadarEvidencePackage


RADAR_AI_AUTO_JOB_ID = "radar-ai-automatic-analysis"
RADAR_AI_TASK_NAME = "radarAiAnalysis"
RADAR_AI_INTERVAL_SECONDS = 180


@dataclass(frozen=True)
class RadarAiScheduleRegistration:
    state: str
    job_id: str


@dataclass(frozen=True)
class RadarAiJobOutcome:
    status: str
    analyzed_count: int = 0
    failed_count: int = 0
    reason: Optional[str] = None


class RadarAiAutomaticJob:
    def __init__(
        self,
        *,
        package_provider: Callable[[], Iterable[FrozenRadarEvidencePackage]],
        analyzer: Callable,
        run_lock: Optional[threading.Lock] = None,
    ):
        self.package_provider = package_provider
        self.analyzer = analyzer
        self.run_lock = run_lock or threading.Lock()

    def __call__(self) -> RadarAiJobOutcome:
        if not self.run_lock.acquire(blocking=False):
            reason = "analysis_job_already_running"
            monitoring_health.record_task_skipped(RADAR_AI_TASK_NAME, reason)
            return RadarAiJobOutcome("skipped", reason=reason)
        try:
            packages = tuple(self.package_provider())
            if not packages:
                reason = "formal_evidence_not_available"
                monitoring_health.record_task_skipped(RADAR_AI_TASK_NAME, reason)
                return RadarAiJobOutcome("skipped", reason=reason)
            analyzed_count = 0
            failed_count = 0
            for package in packages:
                result = self.analyzer(package)
                analyzed_count += 1
                if result.analysis_status != "success":
                    failed_count += 1
            if failed_count:
                monitoring_health.record_task_degraded(
                    RADAR_AI_TASK_NAME,
                    ("radar_ai_analysis_failed",),
                    item_count=analyzed_count,
                )
            else:
                monitoring_health.record_task_success(
                    RADAR_AI_TASK_NAME,
                    item_count=analyzed_count,
                )
            return RadarAiJobOutcome(
                "completed",
                analyzed_count=analyzed_count,
                failed_count=failed_count,
            )
        except Exception as exc:
            monitoring_health.record_task_failure(RADAR_AI_TASK_NAME, exc)
            return RadarAiJobOutcome("failed", reason=type(exc).__name__)
        finally:
            self.run_lock.release()


def _empty_formal_evidence_provider():
    # 当前正式状态门尚未开启，自动任务不得消费影子快照。
    return ()


def _default_analyzer(package):
    # 延迟导入避免调度注册时打开数据库或初始化模型客户端。
    from radar.ai.api import analyze_frozen_evidence

    return analyze_frozen_evidence(package, manual=False)


def register_radar_ai_job(
    scheduler,
    *,
    settings: Optional[RadarAiSettings] = None,
    package_provider: Optional[Callable] = None,
    analyzer: Optional[Callable] = None,
) -> RadarAiScheduleRegistration:
    effective = settings or load_radar_ai_settings()
    if not effective.enabled:
        return RadarAiScheduleRegistration("disabled", RADAR_AI_AUTO_JOB_ID)
    job = RadarAiAutomaticJob(
        package_provider=package_provider or _empty_formal_evidence_provider,
        analyzer=analyzer or _default_analyzer,
    )
    scheduler.add_job(
        job,
        "interval",
        id=RADAR_AI_AUTO_JOB_ID,
        seconds=RADAR_AI_INTERVAL_SECONDS,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return RadarAiScheduleRegistration("registered", RADAR_AI_AUTO_JOB_ID)
