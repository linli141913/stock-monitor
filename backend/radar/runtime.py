"""默认关闭的生产影子运行时接入。

本模块只在两个雷达开关同时开启时构造基础分频任务；行业和市场聚合
任务还分别需要独立开关。它不会创建数据库、
应用迁移、创建锁目录、启动调度器或修改环境变量。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Union
from zoneinfo import ZoneInfo

import market_calendar
import monitoring_health
from radar.config import RadarSettings, load_radar_settings
from radar.migrations import validate_applied_migrations
from radar.market_shadow_runner import (
    IndexFetcher as MarketIndexFetcher,
    MarketShadowPolicy,
    MarketShadowRunner,
    QuoteFetcher as MarketQuoteFetcher,
    build_default_market_index_fetcher,
    build_default_market_quote_fetcher,
)
from radar.repository import RadarRepository
from radar.scheduler import (
    ScheduleRegistration,
    ScheduleRegistrationState,
    ScheduledRunOutcome,
    ScheduledRunState,
    ScheduledShadowJob,
    ShadowJobSpec,
    register_shadow_jobs,
)
from radar.sector_shadow_runner import (
    QuoteFetcher as SectorQuoteFetcher,
    SectorShadowPolicy,
    SectorShadowRunner,
    build_default_sector_quote_fetcher,
)
from radar.scoped_runner import (
    ETF_SOURCE,
    SECURITY_SOURCE,
    RadarTaskScope,
    ScopedShadowRunner,
)
from radar.shadow_runner import ShadowSources, build_default_shadow_sources


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PathLike = Union[str, Path]
ConnectionFactory = Callable[[Path, bool], sqlite3.Connection]
MarketStatusProvider = Callable[[str, Optional[datetime]], tuple]

RADAR_RUNTIME_LOCK_PATH = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime/"
    "radar-shadow.lock"
)
RADAR_SECTOR_RUNTIME_LOCK_PATH = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime/"
    "radar-sector-shadow.lock"
)
RADAR_MARKET_RUNTIME_LOCK_PATH = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime/"
    "radar-market-shadow.lock"
)
RADAR_REGISTRY_JOB_ID = "radar-shadow-registry"
RADAR_STOCK_QUOTES_JOB_ID = "radar-shadow-stock-quotes"
RADAR_ETF_QUOTES_JOB_ID = "radar-shadow-etf-quotes"
RADAR_SECTOR_FEATURES_JOB_ID = "radar-shadow-sector-features"
RADAR_MARKET_FEATURES_JOB_ID = "radar-shadow-market-features"
RADAR_REGISTRY_INTERVAL_SECONDS = 1800
RADAR_REGISTRY_INITIAL_DELAY_SECONDS = 90
RADAR_STOCK_INITIAL_DELAY_SECONDS = 0
RADAR_ETF_INITIAL_DELAY_SECONDS = 30
RADAR_SECTOR_INITIAL_DELAY_SECONDS = 60
RADAR_MARKET_INITIAL_DELAY_SECONDS = 120
RADAR_REGISTRY_WINDOW_START = time(8, 45)
RADAR_REGISTRY_WINDOW_END = time(16, 0)

TASK_NAMES = {
    RadarTaskScope.REGISTRY: "radarRegistry",
    RadarTaskScope.STOCK_QUOTES: "radarStockQuotes",
    RadarTaskScope.ETF_QUOTES: "radarEtfQuotes",
}
JOB_IDS = {
    RadarTaskScope.REGISTRY: RADAR_REGISTRY_JOB_ID,
    RadarTaskScope.STOCK_QUOTES: RADAR_STOCK_QUOTES_JOB_ID,
    RadarTaskScope.ETF_QUOTES: RADAR_ETF_QUOTES_JOB_ID,
}
RUN_ID_PREFIXES = dict(JOB_IDS)
RADAR_SECTOR_TASK_NAME = "radarSectorFeatures"
RADAR_MARKET_TASK_NAME = "radarMarketFeatures"


class RadarSourceDegradedError(RuntimeError):
    """任务完成但来源健康未达到影子准入门槛。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("雷达运行时间必须包含时区")
    return value.astimezone(UTC)


def _default_connection_factory(
    database_path: Path,
    read_only: bool,
) -> sqlite3.Connection:
    resolved = database_path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError("雷达数据库路径不是现有文件")
    mode = "ro" if read_only else "rw"
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode={mode}",
        uri=True,
        timeout=30,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@dataclass(frozen=True)
class HealthTrackedJob:
    task_name: str
    scheduled_job: ScheduledShadowJob

    def __call__(self) -> ScheduledRunOutcome:
        try:
            outcome = self.scheduled_job()
        except Exception as exc:
            monitoring_health.record_task_failure(self.task_name, exc)
            raise

        if outcome.state == ScheduledRunState.COMPLETED:
            if (
                outcome.result_status == "succeeded"
                or outcome.gate_passed is True
            ):
                monitoring_health.record_task_success(
                    self.task_name,
                    item_count=outcome.item_count,
                )
            else:
                monitoring_health.record_task_failure(
                    self.task_name,
                    RadarSourceDegradedError(
                        f"雷达来源状态为{outcome.result_status or 'unknown'}"
                    ),
                )
        else:
            monitoring_health.record_task_skipped(
                self.task_name,
                outcome.skip_reason or outcome.state.value,
            )
        return outcome


class RadarRuntime:
    """为分频任务提供门禁、连接生命周期和确定性执行入口。"""

    def __init__(
        self,
        *,
        database_path: PathLike,
        lock_path: PathLike,
        settings: RadarSettings,
        sources: ShadowSources,
        sector_lock_path: PathLike = RADAR_SECTOR_RUNTIME_LOCK_PATH,
        sector_quote_fetcher: Optional[SectorQuoteFetcher] = None,
        market_lock_path: PathLike = RADAR_MARKET_RUNTIME_LOCK_PATH,
        market_index_fetcher: Optional[MarketIndexFetcher] = None,
        market_quote_fetcher: Optional[MarketQuoteFetcher] = None,
        clock: Callable[[], datetime] = _utc_now,
        market_status_provider: MarketStatusProvider = (
            market_calendar.get_market_status
        ),
        connection_factory: ConnectionFactory = _default_connection_factory,
    ):
        self.database_path = Path(database_path)
        self.lock_path = Path(lock_path)
        self.sector_lock_path = Path(sector_lock_path)
        self.market_lock_path = Path(market_lock_path)
        self.settings = settings
        self.sources = sources
        self.sector_quote_fetcher = (
            sector_quote_fetcher
            or build_default_sector_quote_fetcher(clock=clock)
        )
        self.market_index_fetcher = (
            market_index_fetcher
            or build_default_market_index_fetcher(
                timeout_seconds=settings.quote_timeout_seconds,
                clock=clock,
            )
        )
        self.market_quote_fetcher = (
            market_quote_fetcher
            or build_default_market_quote_fetcher(
                batch_size=settings.quote_batch_size,
                timeout_seconds=settings.quote_timeout_seconds,
                clock=clock,
            )
        )
        self._sector_run_lock = threading.Lock()
        self._market_run_lock = threading.Lock()
        self.clock = clock
        self.market_status_provider = market_status_provider
        self.connection_factory = connection_factory

    @contextmanager
    def _connection(self, *, read_only: bool) -> Iterator[sqlite3.Connection]:
        connection = self.connection_factory(self.database_path, read_only)
        try:
            validate_applied_migrations(connection)
            yield connection
        finally:
            connection.close()

    def readiness_reason(
        self,
        scope: RadarTaskScope,
        as_of: datetime,
    ) -> Optional[str]:
        as_of = _aware_utc(as_of)
        local_now = as_of.astimezone(SHANGHAI_TZ)
        market_status, calendar_day = self.market_status_provider("cn", local_now)

        if calendar_day.kind == "unknown" or market_status.code == "unknown":
            return "market_calendar_unknown"
        if scope in {RadarTaskScope.STOCK_QUOTES, RadarTaskScope.ETF_QUOTES}:
            if market_status.code == "trading":
                return None
            return {
                "pre_open": "pre_open",
                "lunch_break": "lunch_break",
                "holiday": "market_holiday",
                "closed": "market_closed",
            }.get(market_status.code, "market_not_trading")

        if scope != RadarTaskScope.REGISTRY:
            raise ValueError("雷达任务范围无效")
        if calendar_day.kind == "closed" or market_status.code == "holiday":
            return "market_holiday"
        local_time = local_now.time().replace(tzinfo=None)
        if local_time < RADAR_REGISTRY_WINDOW_START:
            return "registry_window_not_open"
        if local_time > RADAR_REGISTRY_WINDOW_END:
            return "registry_window_closed"

        with self._connection(read_only=True) as connection:
            repository = RadarRepository(connection, clock=self.clock)
            latest_security = repository.latest_healthy_source_as_of(
                SECURITY_SOURCE
            )
            latest_etf = repository.latest_healthy_source_as_of(ETF_SOURCE)
        local_date = local_now.date()
        if (
            latest_security is not None
            and latest_etf is not None
            and latest_security.astimezone(SHANGHAI_TZ).date() == local_date
            and latest_etf.astimezone(SHANGHAI_TZ).date() == local_date
        ):
            return "registry_already_current"
        return None

    def execute(
        self,
        scope: RadarTaskScope,
        radar_run_id: str,
        as_of: datetime,
    ):
        with self._connection(read_only=False) as connection:
            repository = RadarRepository(connection, clock=self.clock)
            runner = ScopedShadowRunner(
                repository=repository,
                settings=self.settings,
                sources=self.sources,
                clock=self.clock,
            )
            return runner.run_once(scope, radar_run_id, as_of)

    def execute_sector(
        self,
        radar_run_id: str,
        as_of: datetime,
    ):
        with self._connection(read_only=False) as connection:
            repository = RadarRepository(connection, clock=self.clock)
            runner = SectorShadowRunner(
                repository,
                quote_fetcher=self.sector_quote_fetcher,
                policy=SectorShadowPolicy(
                    minimum_quote_row_coverage=(
                        self.settings.minimum_row_coverage
                    ),
                    minimum_required_field_coverage=(
                        self.settings.minimum_required_field_coverage
                    ),
                    maximum_quote_age_seconds=(
                        self.settings.maximum_quote_age_seconds
                    ),
                ),
                clock=self.clock,
                run_lock=self._sector_run_lock,
            )
            return runner.run_once(radar_run_id, as_of)

    def execute_market(
        self,
        radar_run_id: str,
        as_of: datetime,
    ):
        with self._connection(read_only=False) as connection:
            repository = RadarRepository(connection, clock=self.clock)
            runner = MarketShadowRunner(
                repository,
                index_fetcher=self.market_index_fetcher,
                quote_fetcher=self.market_quote_fetcher,
                policy=MarketShadowPolicy(
                    minimum_quote_row_coverage=(
                        self.settings.minimum_row_coverage
                    ),
                    minimum_required_field_coverage=(
                        self.settings.minimum_required_field_coverage
                    ),
                    maximum_quote_age_seconds=(
                        self.settings.maximum_quote_age_seconds
                    ),
                ),
                clock=self.clock,
                run_lock=self._market_run_lock,
            )
            return runner.run_once(radar_run_id, as_of)

    def build_job(self, scope: RadarTaskScope) -> HealthTrackedJob:
        task_name = TASK_NAMES[scope]
        scheduled = ScheduledShadowJob(
            settings=self.settings,
            execute_once=lambda radar_run_id, as_of: self.execute(
                scope,
                radar_run_id,
                as_of,
            ),
            lock_path=self.lock_path,
            clock=self.clock,
            readiness_check=lambda as_of: self.readiness_reason(scope, as_of),
            run_id_prefix=RUN_ID_PREFIXES[scope],
            on_started=lambda: monitoring_health.record_task_started(task_name),
        )
        return HealthTrackedJob(task_name=task_name, scheduled_job=scheduled)

    def build_sector_job(self) -> HealthTrackedJob:
        sector_settings = replace(
            self.settings,
            enabled=(
                self.settings.enabled
                and self.settings.sector_shadow_enabled
            ),
        )
        scheduled = ScheduledShadowJob(
            settings=sector_settings,
            execute_once=self.execute_sector,
            lock_path=self.sector_lock_path,
            clock=self.clock,
            readiness_check=lambda as_of: self.readiness_reason(
                RadarTaskScope.STOCK_QUOTES,
                as_of,
            ),
            run_id_prefix=RADAR_SECTOR_FEATURES_JOB_ID,
            on_started=lambda: monitoring_health.record_task_started(
                RADAR_SECTOR_TASK_NAME
            ),
        )
        return HealthTrackedJob(
            task_name=RADAR_SECTOR_TASK_NAME,
            scheduled_job=scheduled,
        )

    def build_market_job(self) -> HealthTrackedJob:
        market_settings = replace(
            self.settings,
            enabled=(
                self.settings.enabled
                and self.settings.market_shadow_enabled
            ),
        )
        scheduled = ScheduledShadowJob(
            settings=market_settings,
            execute_once=self.execute_market,
            lock_path=self.market_lock_path,
            clock=self.clock,
            readiness_check=lambda as_of: self.readiness_reason(
                RadarTaskScope.STOCK_QUOTES,
                as_of,
            ),
            run_id_prefix=RADAR_MARKET_FEATURES_JOB_ID,
            on_started=lambda: monitoring_health.record_task_started(
                RADAR_MARKET_TASK_NAME
            ),
        )
        return HealthTrackedJob(
            task_name=RADAR_MARKET_TASK_NAME,
            scheduled_job=scheduled,
        )

    def job_specs(self) -> tuple[ShadowJobSpec, ...]:
        phase_anchor = _aware_utc(self.clock())
        specs = [
            ShadowJobSpec(
                RADAR_REGISTRY_JOB_ID,
                self.build_job(RadarTaskScope.REGISTRY),
                RADAR_REGISTRY_INTERVAL_SECONDS,
                phase_anchor + timedelta(
                    seconds=RADAR_REGISTRY_INITIAL_DELAY_SECONDS
                ),
            ),
            ShadowJobSpec(
                RADAR_STOCK_QUOTES_JOB_ID,
                self.build_job(RadarTaskScope.STOCK_QUOTES),
                self.settings.stock_scan_interval_seconds,
                phase_anchor + timedelta(
                    seconds=RADAR_STOCK_INITIAL_DELAY_SECONDS
                ),
            ),
            ShadowJobSpec(
                RADAR_ETF_QUOTES_JOB_ID,
                self.build_job(RadarTaskScope.ETF_QUOTES),
                self.settings.etf_scan_interval_seconds,
                phase_anchor + timedelta(
                    seconds=RADAR_ETF_INITIAL_DELAY_SECONDS
                ),
            ),
        ]
        if self.settings.sector_shadow_enabled:
            specs.append(ShadowJobSpec(
                RADAR_SECTOR_FEATURES_JOB_ID,
                self.build_sector_job(),
                self.settings.sector_scan_interval_seconds,
                phase_anchor + timedelta(
                    seconds=RADAR_SECTOR_INITIAL_DELAY_SECONDS
                ),
            ))
        if self.settings.market_shadow_enabled:
            specs.append(ShadowJobSpec(
                RADAR_MARKET_FEATURES_JOB_ID,
                self.build_market_job(),
                self.settings.market_scan_interval_seconds,
                phase_anchor + timedelta(
                    seconds=RADAR_MARKET_INITIAL_DELAY_SECONDS
                ),
            ))
        return tuple(specs)


def register_production_shadow_jobs(
    scheduler,
    *,
    database_path: PathLike,
    lock_path: PathLike = RADAR_RUNTIME_LOCK_PATH,
    sector_lock_path: PathLike = RADAR_SECTOR_RUNTIME_LOCK_PATH,
    market_lock_path: PathLike = RADAR_MARKET_RUNTIME_LOCK_PATH,
    settings: Optional[RadarSettings] = None,
    sources: Optional[ShadowSources] = None,
    sector_quote_fetcher: Optional[SectorQuoteFetcher] = None,
    market_index_fetcher: Optional[MarketIndexFetcher] = None,
    market_quote_fetcher: Optional[MarketQuoteFetcher] = None,
    clock: Callable[[], datetime] = _utc_now,
    market_status_provider: MarketStatusProvider = (
        market_calendar.get_market_status
    ),
    connection_factory: ConnectionFactory = _default_connection_factory,
) -> tuple[ScheduleRegistration, ...]:
    """把默认关闭的分频任务接到现有调度器，但不启动调度器。"""

    effective_settings = settings or load_radar_settings()
    if not effective_settings.enabled or not effective_settings.shadow_mode:
        return tuple(
            ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=job_id,
            )
            for job_id in (
                RADAR_REGISTRY_JOB_ID,
                RADAR_STOCK_QUOTES_JOB_ID,
                RADAR_ETF_QUOTES_JOB_ID,
                RADAR_SECTOR_FEATURES_JOB_ID,
                RADAR_MARKET_FEATURES_JOB_ID,
            )
        )

    runtime = RadarRuntime(
        database_path=database_path,
        lock_path=lock_path,
        sector_lock_path=sector_lock_path,
        market_lock_path=market_lock_path,
        settings=effective_settings,
        sources=sources or build_default_shadow_sources(
            effective_settings,
            clock=clock,
        ),
        sector_quote_fetcher=sector_quote_fetcher,
        market_index_fetcher=market_index_fetcher,
        market_quote_fetcher=market_quote_fetcher,
        clock=clock,
        market_status_provider=market_status_provider,
        connection_factory=connection_factory,
    )
    registrations = register_shadow_jobs(
        scheduler,
        runtime.job_specs(),
        effective_settings,
    )
    registration_by_id = {
        registration.job_id: registration
        for registration in registrations
    }
    if not effective_settings.sector_shadow_enabled:
        registration_by_id[RADAR_SECTOR_FEATURES_JOB_ID] = ScheduleRegistration(
            state=ScheduleRegistrationState.DISABLED,
            job_id=RADAR_SECTOR_FEATURES_JOB_ID,
        )
    if not effective_settings.market_shadow_enabled:
        registration_by_id[RADAR_MARKET_FEATURES_JOB_ID] = ScheduleRegistration(
            state=ScheduleRegistrationState.DISABLED,
            job_id=RADAR_MARKET_FEATURES_JOB_ID,
        )
    return tuple(
        registration_by_id[job_id]
        for job_id in (
            RADAR_REGISTRY_JOB_ID,
            RADAR_STOCK_QUOTES_JOB_ID,
            RADAR_ETF_QUOTES_JOB_ID,
            RADAR_SECTOR_FEATURES_JOB_ID,
            RADAR_MARKET_FEATURES_JOB_ID,
        )
    )
