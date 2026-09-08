"""默认关闭的生产影子运行时接入。

本模块只在两个雷达开关同时开启时构造基础分频任务；行业和市场聚合
任务还分别需要独立开关。它不会创建数据库、
应用迁移、创建锁目录、启动调度器或修改环境变量。
"""

from __future__ import annotations

import logging
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
from radar.repository import (
    RadarRepository,
    RadarRepositoryError,
    RepositoryStateError,
)
from radar.scheduler import (
    FormalJobSpec,
    FormalReadinessLoader,
    ScheduleRegistration,
    ScheduleRegistrationState,
    ScheduledRunOutcome,
    ScheduledRunState,
    ScheduledShadowJob,
    ShadowJobSpec,
    SettingsProvider,
    register_formal_jobs,
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
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
_RUNTIME_LOG_HANDLER = logging.StreamHandler()
_RUNTIME_LOG_HANDLER.setLevel(logging.INFO)
_RUNTIME_LOG_HANDLER.setFormatter(logging.Formatter("%(message)s"))
LOGGER.addHandler(_RUNTIME_LOG_HANDLER)
LOGGER.propagate = False
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
RADAR_ETF_STAGE5_RUNTIME_LOCK_PATH = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime/"
    "radar-etf-stage5-shadow.lock"
)
RADAR_ETF_PRODUCT_MASTER_RUNTIME_LOCK_PATH = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime/"
    "radar-etf-product-master.lock"
)
RADAR_REGISTRY_JOB_ID = "radar-shadow-registry"
RADAR_STOCK_QUOTES_JOB_ID = "radar-shadow-stock-quotes"
RADAR_ETF_QUOTES_JOB_ID = "radar-shadow-etf-quotes"
RADAR_SECTOR_FEATURES_JOB_ID = "radar-shadow-sector-features"
RADAR_MARKET_FEATURES_JOB_ID = "radar-shadow-market-features"
RADAR_ETF_PRODUCT_MASTER_JOB_ID = "radar-etf-stage5-product-master"
RADAR_REGISTRY_INTERVAL_SECONDS = 1800
RADAR_REGISTRY_INITIAL_DELAY_SECONDS = 90
RADAR_STOCK_INITIAL_DELAY_SECONDS = 0
RADAR_ETF_INITIAL_DELAY_SECONDS = 30
RADAR_SECTOR_INITIAL_DELAY_SECONDS = 60
RADAR_MARKET_INITIAL_DELAY_SECONDS = 120
RADAR_ETF_PRODUCT_MASTER_INTERVAL_SECONDS = 86400
RADAR_ETF_PRODUCT_MASTER_INITIAL_DELAY_SECONDS = 150
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
RADAR_ETF_PRODUCT_MASTER_TASK_NAME = "radarEtfProductMaster"
RADAR_LEADER_STAGE6_TASK_NAME = "radarLeaderStage6"


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
            LOGGER.error(
                "radar_task_outcome task=%s state=failed "
                "error_type=%s",
                self.task_name,
                type(exc).__name__,
            )
            raise

        gate_passed = (
            str(outcome.gate_passed).lower()
            if outcome.gate_passed is not None
            else "unknown"
        )
        gate_reasons = ",".join(outcome.gate_reasons) or "none"
        log_method = LOGGER.info
        if (
            outcome.state == ScheduledRunState.COMPLETED
            and outcome.result_status != "succeeded"
            and outcome.gate_passed is not True
        ):
            log_method = LOGGER.warning
        log_method(
            "radar_task_outcome task=%s run_id=%s state=%s status=%s "
            "gate_passed=%s gate_reasons=%s item_count=%s "
            "duration_ms=%d",
            self.task_name,
            outcome.radar_run_id or "none",
            outcome.state.value,
            outcome.result_status or "none",
            gate_passed,
            gate_reasons,
            (
                str(outcome.item_count)
                if outcome.item_count is not None
                else "none"
            ),
            round(outcome.duration_seconds * 1000),
        )

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
                monitoring_health.record_task_degraded(
                    self.task_name,
                    outcome.gate_reasons,
                    item_count=outcome.item_count,
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
        etf_stage5_lock_path: PathLike = RADAR_ETF_STAGE5_RUNTIME_LOCK_PATH,
        etf_product_master_lock_path: PathLike = (
            RADAR_ETF_PRODUCT_MASTER_RUNTIME_LOCK_PATH
        ),
        etf_product_master_fetcher: Optional[Callable] = None,
        leader_history_input_provider: Optional[Callable] = None,
        leader_business_catalyst_input_provider: Optional[Callable] = None,
        leader_tradability_input_provider: Optional[Callable] = None,
        leader_sector_rule_input_provider: Optional[Callable] = None,
        leader_research_input_provider: Optional[Callable] = None,
        leader_formal_research_source_provenance_provider: Optional[
            Callable
        ] = None,
        leader_observation_publisher: Optional[Callable] = None,
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
        self.etf_stage5_lock_path = Path(etf_stage5_lock_path)
        self.etf_product_master_lock_path = Path(
            etf_product_master_lock_path
        )
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
        self.etf_product_master_fetcher = etf_product_master_fetcher
        self.leader_history_input_provider = (
            leader_history_input_provider
        )
        self.leader_business_catalyst_input_provider = (
            leader_business_catalyst_input_provider
        )
        self.leader_tradability_input_provider = (
            leader_tradability_input_provider
        )
        self.leader_sector_rule_input_provider = (
            leader_sector_rule_input_provider
        )
        self.leader_research_input_provider = (
            leader_research_input_provider
        )
        self.leader_formal_research_source_provenance_provider = (
            leader_formal_research_source_provenance_provider
        )
        self.leader_observation_publisher = leader_observation_publisher
        if (
            self.settings.etf_stage5_enabled
            and self.etf_product_master_fetcher is None
        ):
            from radar.sources.etf_product_master import (
                fetch_etf_product_master,
            )

            self.etf_product_master_fetcher = fetch_etf_product_master
        self._sector_run_lock = threading.Lock()
        self._market_run_lock = threading.Lock()
        self._leader_stage6_run_lock = threading.Lock()
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

    def etf_product_master_readiness_reason(
        self,
        as_of: datetime,
    ) -> Optional[str]:
        as_of = _aware_utc(as_of)
        try:
            with self._connection(read_only=True) as connection:
                from radar.etf_repository import EtfRepository

                latest_attempt = EtfRepository(
                    connection,
                    clock=self.clock,
                ).latest_product_master_attempt_at()
        except RadarRepositoryError:
            return "etf_stage5_storage_not_ready"
        if (
            latest_attempt is not None
            and latest_attempt.astimezone(SHANGHAI_TZ).date()
            == as_of.astimezone(SHANGHAI_TZ).date()
        ):
            return "etf_product_master_already_attempted_today"
        return None

    def execute(
        self,
        scope: RadarTaskScope,
        radar_run_id: str,
        as_of: datetime,
    ):
        with self._connection(read_only=False) as connection:
            repository = RadarRepository(connection, clock=self.clock)
            etf_stage5_processor = None
            if (
                scope == RadarTaskScope.ETF_QUOTES
                and self.settings.etf_stage5_enabled
            ):
                def process_etf_stage5(
                    current_run_id,
                    current_as_of,
                    quote_batch,
                    quote_health,
                ):
                    from radar.etf_repository import EtfRepository
                    from radar.etf_shadow_runner import EtfStage5ShadowRunner

                    stage5_runner = EtfStage5ShadowRunner(
                        EtfRepository(connection, clock=self.clock),
                        settings=self.settings,
                        lock_path=self.etf_stage5_lock_path,
                        clock=self.clock,
                    )
                    return stage5_runner.run_once(
                        current_run_id,
                        current_as_of,
                        quote_batch,
                        quote_health,
                    )

                etf_stage5_processor = process_etf_stage5
            runner = ScopedShadowRunner(
                repository=repository,
                settings=self.settings,
                sources=self.sources,
                clock=self.clock,
                etf_stage5_processor=etf_stage5_processor,
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
            leader_stage6_processor = None
            if self.settings.leader_stage6_enabled:
                def process_leader_stage6(
                    current_run_id,
                    current_as_of,
                    quote_batch,
                    quote_health,
                ):
                    from radar.leader_input_gate import (
                        LeaderInputGatePolicy,
                    )
                    from radar.leader_formal_research_runtime_assembly import (
                        build_leader_formal_research_runtime_assembly,
                    )
                    from radar.leader_formal_research_production_acceptance import (
                        LeaderFormalResearchProductionAcceptanceInput,
                        PROVENANCE_UNBOUND,
                        build_leader_formal_research_source_provenance_from_assembly,
                        build_leader_formal_research_production_acceptance,
                    )
                    from radar.sector_rule_runtime_bridge import (
                        build_sector_rule_runtime_bridge,
                    )
                    from radar.leader_repository import LeaderRepository
                    from radar.leader_research_input_provider_batch import (
                        LeaderResearchInputProviderBatchStatus,
                        build_leader_research_input_provider_batch_from_plan,
                    )
                    from radar.leader_research_runtime_provider import (
                        build_leader_research_runtime_source_context,
                    )
                    from radar.leader_risk_review_repository import (
                        LeaderRiskReviewRepository,
                    )
                    from radar.leader_research_single_pass_orchestration import (
                        LeaderResearchSinglePassInput,
                        LeaderResearchSinglePassStatus,
                        build_leader_research_single_pass,
                    )
                    from radar.leader_runtime_candidate_plan import (
                        LeaderRuntimeCandidatePlanInput,
                        LeaderRuntimeCandidatePlanStatus,
                        build_leader_runtime_candidate_plan,
                    )
                    from radar.leader_shadow_runner import LeaderShadowRunner

                    monitoring_health.record_task_started(
                        RADAR_LEADER_STAGE6_TASK_NAME
                    )
                    try:
                        sector_rows = (
                            repository
                            .list_latest_sector_feature_rows_at_or_before(
                                current_as_of
                            )
                        )
                        release_ids = {
                            str(row["industryReleaseId"])
                            for row in sector_rows
                        }
                        industry_records = ()
                        if len(release_ids) == 1:
                            industry_records = (
                                repository
                                .list_industry_classification_records_by_release_id(
                                    next(iter(release_ids))
                                )
                            )
                        market_snapshot = repository.get_market_feature_row(
                            current_run_id
                        )
                        security_records = (
                            repository.list_security_master_records(
                                current_as_of
                            )
                        )
                        plan = build_leader_runtime_candidate_plan(
                            LeaderRuntimeCandidatePlanInput(
                                as_of=current_as_of,
                                quote_batch=quote_batch,
                                quote_health=quote_health,
                                market_snapshot=market_snapshot,
                                sector_rows=sector_rows,
                                industry_records=industry_records,
                                security_records=security_records,
                            )
                        )
                        if plan.status != LeaderRuntimeCandidatePlanStatus.READY:
                            monitoring_health.record_task_degraded(
                                RADAR_LEADER_STAGE6_TASK_NAME,
                                plan.gate_reasons
                                or ("leader_candidate_plan_not_ready",),
                                item_count=0,
                            )
                            return plan

                        observation_publish_reasons = ()
                        if self.leader_observation_publisher is not None:
                            try:
                                self.leader_observation_publisher(
                                    plan,
                                    quote_batch.items,
                                    security_records,
                                    self.clock(),
                                )
                            except Exception:
                                observation_publish_reasons = (
                                    "leader_observation_publish_failed",
                                )

                        source_context = (
                            build_leader_research_runtime_source_context(
                                candidate_plan=plan,
                                quote_batch=quote_batch,
                                quote_health=quote_health,
                                security_records=security_records,
                                industry_records=industry_records,
                            )
                        )
                        runtime_assembly_health_reasons = ()
                        runtime_production_acceptance = None
                        runtime_production_acceptance_health_reasons = ()
                        if self.leader_research_input_provider is not None:
                            sector_rule_admission_value = None
                            if (
                                self.leader_sector_rule_input_provider
                                is not None
                            ):
                                try:
                                    sector_rule_bridge = (
                                        build_sector_rule_runtime_bridge(
                                            source_context,
                                            source_batch=(
                                                self.leader_sector_rule_input_provider(
                                                    source_context
                                                )
                                            ),
                                        )
                                    )
                                    sector_rule_admission_value = (
                                        sector_rule_bridge
                                        .sector_rule_admission_value
                                    )
                                except Exception:
                                    sector_rule_admission_value = None
                            provider_input = self.leader_research_input_provider(
                                source_context
                            )
                        else:
                            try:
                                risk_review_repository = (
                                    LeaderRiskReviewRepository(
                                        connection,
                                        clock=self.clock,
                                    )
                                )
                            except (
                                RepositoryStateError,
                                sqlite3.OperationalError,
                            ):
                                risk_review_repository = None
                            runtime_assembly = (
                                build_leader_formal_research_runtime_assembly(
                                    source_context,
                                    repository=risk_review_repository,
                                    sector_rule_provider=(
                                        self.leader_sector_rule_input_provider
                                    ),
                                    history_provider=(
                                        self.leader_history_input_provider
                                    ),
                                    business_catalyst_provider=(
                                        self.leader_business_catalyst_input_provider
                                    ),
                                    tradability_provider=(
                                        self.leader_tradability_input_provider
                                    ),
                                )
                            )
                            provider_input = runtime_assembly.provider_input
                            sector_rule_admission_value = (
                                runtime_assembly.sector_rule_readiness
                            )
                            runtime_assembly_health_reasons = (
                                runtime_assembly.health_reasons
                            )
                            provenance = (
                                build_leader_formal_research_source_provenance_from_assembly(
                                    runtime_assembly
                                )
                            )
                            if (
                                self.leader_formal_research_source_provenance_provider
                                is not None
                            ):
                                try:
                                    external_provenance = (
                                        self.leader_formal_research_source_provenance_provider(
                                            source_context,
                                            runtime_assembly,
                                        )
                                    )
                                    if tuple(external_provenance) != provenance:
                                        runtime_production_acceptance_health_reasons = (
                                            PROVENANCE_UNBOUND,
                                        )
                                except Exception:
                                    runtime_production_acceptance_health_reasons = (
                                        "leader_formal_research_production_provenance_provider_failed",
                                    )
                            runtime_production_acceptance = (
                                build_leader_formal_research_production_acceptance(
                                    LeaderFormalResearchProductionAcceptanceInput(
                                        assembly=runtime_assembly,
                                        provenance=provenance,
                                    )
                                )
                            )
                            runtime_production_acceptance_health_reasons = tuple(
                                dict.fromkeys((
                                    *runtime_production_acceptance_health_reasons,
                                    *runtime_production_acceptance.health_reasons,
                                ))
                            )
                        provider_result = (
                            build_leader_research_input_provider_batch_from_plan(
                                provider_input
                            )
                        )
                        orchestration_input = LeaderResearchSinglePassInput(
                            candidate_plan=plan,
                            provider_input=provider_input,
                            source_context=source_context,
                            as_of=current_as_of,
                            quote_batch=quote_batch,
                            quote_health=quote_health,
                            market_snapshot=market_snapshot,
                            sector_rows=sector_rows,
                            industry_records=industry_records,
                            security_records=security_records,
                            sector_rule_readiness=(
                                sector_rule_admission_value
                            ),
                        )
                        if provider_result.status in (
                            LeaderResearchInputProviderBatchStatus.BLOCKED,
                            LeaderResearchInputProviderBatchStatus.MISSING,
                        ):
                            single_pass = build_leader_research_single_pass(
                                orchestration_input
                            )
                            single_pass = replace(
                                single_pass,
                                reasons=tuple(dict.fromkeys((
                                    *single_pass.reasons,
                                    *runtime_assembly_health_reasons,
                                    *runtime_production_acceptance_health_reasons,
                                    *observation_publish_reasons,
                                ))),
                                production_acceptance=(
                                    runtime_production_acceptance
                                ),
                            )
                            monitoring_health.record_task_degraded(
                                RADAR_LEADER_STAGE6_TASK_NAME,
                                single_pass.gate_reasons,
                                item_count=0,
                            )
                            return single_pass

                        leader_repository = LeaderRepository(
                            connection,
                            clock=self.clock,
                        )
                        previous_states = (
                            leader_repository
                            .get_latest_state_records_before(current_as_of)
                        )
                        single_pass = build_leader_research_single_pass(
                            replace(
                                orchestration_input,
                                previous_states=previous_states,
                            )
                        )
                        single_pass = replace(
                            single_pass,
                            reasons=tuple(dict.fromkeys((
                                *single_pass.reasons,
                                *runtime_assembly_health_reasons,
                                *runtime_production_acceptance_health_reasons,
                                *observation_publish_reasons,
                            ))),
                            production_acceptance=(
                                runtime_production_acceptance
                            ),
                        )
                        if single_pass.runtime_assembly is None:
                            monitoring_health.record_task_degraded(
                                RADAR_LEADER_STAGE6_TASK_NAME,
                                single_pass.gate_reasons,
                                item_count=0,
                            )
                            return single_pass

                        shadow_result = LeaderShadowRunner(
                            leader_repository,
                            clock=self.clock,
                            run_lock=self._leader_stage6_run_lock,
                        ).run_evidence_once(
                            current_run_id,
                            current_as_of,
                            single_pass.runtime_assembly.evidence_items,
                            previous_states=previous_states,
                            input_gate_policy=LeaderInputGatePolicy(
                                maximum_source_age_seconds=(
                                    self.settings
                                    .maximum_quote_age_seconds
                                ),
                                minimum_row_coverage=(
                                    self.settings.minimum_row_coverage
                                ),
                                minimum_required_field_coverage=(
                                    self.settings
                                    .minimum_required_field_coverage
                                ),
                            ),
                        )
                        if (
                            single_pass.status
                            == LeaderResearchSinglePassStatus.READY
                        ):
                            monitoring_health.record_task_success(
                                RADAR_LEADER_STAGE6_TASK_NAME,
                                item_count=shadow_result.eligible_count,
                            )
                        else:
                            monitoring_health.record_task_degraded(
                                RADAR_LEADER_STAGE6_TASK_NAME,
                                single_pass.gate_reasons,
                                item_count=shadow_result.eligible_count,
                            )
                        return single_pass
                    except Exception as exc:
                        monitoring_health.record_task_failure(
                            RADAR_LEADER_STAGE6_TASK_NAME,
                            exc,
                        )
                        raise

                leader_stage6_processor = process_leader_stage6
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
                leader_stage6_processor=leader_stage6_processor,
            )
            return runner.run_once(radar_run_id, as_of)

    def execute_etf_product_master(
        self,
        radar_run_id: str,
        as_of: datetime,
    ):
        if self.etf_product_master_fetcher is None:
            raise RuntimeError("ETF产品主档来源未配置")
        with self._connection(read_only=False) as connection:
            from radar.etf_product_master_runner import (
                EtfProductMasterRunner,
            )
            from radar.etf_repository import EtfRepository

            runner = EtfProductMasterRunner(
                EtfRepository(connection, clock=self.clock),
                settings=self.settings,
                fetcher=self.etf_product_master_fetcher,
                clock=self.clock,
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

    def build_etf_product_master_job(self) -> HealthTrackedJob:
        product_settings = replace(
            self.settings,
            enabled=(
                self.settings.enabled
                and self.settings.etf_stage5_enabled
            ),
        )
        scheduled = ScheduledShadowJob(
            settings=product_settings,
            execute_once=self.execute_etf_product_master,
            lock_path=self.etf_product_master_lock_path,
            clock=self.clock,
            readiness_check=self.etf_product_master_readiness_reason,
            run_id_prefix=RADAR_ETF_PRODUCT_MASTER_JOB_ID,
            on_started=lambda: monitoring_health.record_task_started(
                RADAR_ETF_PRODUCT_MASTER_TASK_NAME
            ),
        )
        return HealthTrackedJob(
            task_name=RADAR_ETF_PRODUCT_MASTER_TASK_NAME,
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
        if self.settings.etf_stage5_enabled:
            specs.append(ShadowJobSpec(
                RADAR_ETF_PRODUCT_MASTER_JOB_ID,
                self.build_etf_product_master_job(),
                RADAR_ETF_PRODUCT_MASTER_INTERVAL_SECONDS,
                phase_anchor + timedelta(
                    seconds=RADAR_ETF_PRODUCT_MASTER_INITIAL_DELAY_SECONDS
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
    etf_stage5_lock_path: PathLike = RADAR_ETF_STAGE5_RUNTIME_LOCK_PATH,
    etf_product_master_lock_path: PathLike = (
        RADAR_ETF_PRODUCT_MASTER_RUNTIME_LOCK_PATH
    ),
    settings: Optional[RadarSettings] = None,
    sources: Optional[ShadowSources] = None,
    sector_quote_fetcher: Optional[SectorQuoteFetcher] = None,
    market_index_fetcher: Optional[MarketIndexFetcher] = None,
    market_quote_fetcher: Optional[MarketQuoteFetcher] = None,
    etf_product_master_fetcher: Optional[Callable] = None,
    leader_history_input_provider: Optional[Callable] = None,
    leader_business_catalyst_input_provider: Optional[Callable] = None,
    leader_tradability_input_provider: Optional[Callable] = None,
    leader_sector_rule_input_provider: Optional[Callable] = None,
    leader_research_input_provider: Optional[Callable] = None,
    leader_formal_research_source_provenance_provider: Optional[
        Callable
    ] = None,
    leader_observation_publisher: Optional[Callable] = None,
    clock: Callable[[], datetime] = _utc_now,
    market_status_provider: MarketStatusProvider = (
        market_calendar.get_market_status
    ),
    connection_factory: ConnectionFactory = _default_connection_factory,
) -> tuple[ScheduleRegistration, ...]:
    """把默认关闭的分频任务接到现有调度器，但不启动调度器。"""

    effective_settings = settings or load_radar_settings()
    ordered_job_ids = [
        RADAR_REGISTRY_JOB_ID,
        RADAR_STOCK_QUOTES_JOB_ID,
        RADAR_ETF_QUOTES_JOB_ID,
        RADAR_SECTOR_FEATURES_JOB_ID,
        RADAR_MARKET_FEATURES_JOB_ID,
    ]
    if effective_settings.etf_stage5_enabled:
        ordered_job_ids.append(RADAR_ETF_PRODUCT_MASTER_JOB_ID)
    if not effective_settings.enabled or not effective_settings.shadow_mode:
        return tuple(
            ScheduleRegistration(
                state=ScheduleRegistrationState.DISABLED,
                job_id=job_id,
            )
            for job_id in ordered_job_ids
        )

    if leader_observation_publisher is None:
        from radar.leader_observation_store import (
            publish_runtime_leader_observation,
        )

        leader_observation_publisher = publish_runtime_leader_observation

    runtime = RadarRuntime(
        database_path=database_path,
        lock_path=lock_path,
        sector_lock_path=sector_lock_path,
        market_lock_path=market_lock_path,
        etf_stage5_lock_path=etf_stage5_lock_path,
        etf_product_master_lock_path=etf_product_master_lock_path,
        settings=effective_settings,
        sources=sources or build_default_shadow_sources(
            effective_settings,
            clock=clock,
        ),
        sector_quote_fetcher=sector_quote_fetcher,
        market_index_fetcher=market_index_fetcher,
        market_quote_fetcher=market_quote_fetcher,
        etf_product_master_fetcher=etf_product_master_fetcher,
        leader_history_input_provider=leader_history_input_provider,
        leader_business_catalyst_input_provider=(
            leader_business_catalyst_input_provider
        ),
        leader_tradability_input_provider=(
            leader_tradability_input_provider
        ),
        leader_sector_rule_input_provider=(
            leader_sector_rule_input_provider
        ),
        leader_research_input_provider=leader_research_input_provider,
        leader_formal_research_source_provenance_provider=(
            leader_formal_research_source_provenance_provider
        ),
        leader_observation_publisher=leader_observation_publisher,
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
        for job_id in ordered_job_ids
    )


def register_production_formal_jobs(
    scheduler,
    *,
    specs: tuple[FormalJobSpec, ...],
    settings_provider: SettingsProvider,
    readiness_loader: FormalReadinessLoader,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[ScheduleRegistration, ...]:
    """只转发完整强身份规格；空规格不读取配置或证据。

    本缝不创建正式 executor、binding、任务ID、锁或调度器。
    """

    return register_formal_jobs(
        scheduler,
        specs,
        settings_provider=settings_provider,
        readiness_loader=readiness_loader,
        clock=clock,
    )
