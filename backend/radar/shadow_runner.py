"""主线雷达一次性影子采集编排。

本模块不创建数据库连接、不注册调度任务、不读取或修改环境变量。
只有调用方显式提供仓储、开启的影子配置和来源依赖后，才会运行一轮采集。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from radar.config import RadarSettings
from radar.contracts import (
    EtfRegistryRecord,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceHealthResult,
    SourceIssue,
    SourceStatus,
)
from radar.repository import (
    HistoryWriteResult,
    RadarRepository,
    RadarRepositoryError,
    RepositoryConflictError,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.etf_registry import fetch_etf_registry
from radar.sources.security_master import fetch_security_master
from radar.sources.tencent_quotes import fetch_tencent_quotes


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class ShadowRunError(RuntimeError):
    """一次性影子运行基础错误。"""


class ShadowRunDisabledError(ShadowRunError):
    """雷达或影子功能开关未同时开启。"""


class ShadowRunInProgressError(ShadowRunError):
    """当前进程已经有一轮影子采集执行。"""


class ShadowRunAlreadyExistsError(ShadowRunError):
    """相同运行标识已经存在，拒绝重复采集。"""


class ShadowRunExecutionError(ShadowRunError):
    """批次合同或仓储一致性错误导致整轮失败。"""


class _BatchValidationError(ShadowRunError):
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


SecurityFetcher = Callable[
    [str, str, datetime],
    SourceBatch[SecurityMasterRecord],
]
EtfFetcher = Callable[
    [str, str, datetime],
    SourceBatch[EtfRegistryRecord],
]
QuoteFetcher = Callable[
    [Sequence[str], str, str, datetime],
    SourceBatch[QuoteSnapshot],
]


@dataclass(frozen=True)
class ShadowSources:
    security_master: SecurityFetcher
    etf_registry: EtfFetcher
    quotes: QuoteFetcher


@dataclass(frozen=True)
class ShadowRunResult:
    radar_run_id: str
    as_of: datetime
    status: str
    security_health: SourceHealthResult
    etf_health: SourceHealthResult
    quote_health: SourceHealthResult
    security_history: HistoryWriteResult
    etf_history: HistoryWriteResult
    quote_count: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _weekend_adjusted_snapshot_date(as_of: datetime) -> date:
    """周末回退到周五；法定节假日仍由来源失败显式暴露。"""
    local_date = as_of.astimezone(SHANGHAI_TZ).date()
    if local_date.weekday() == 5:
        return local_date - timedelta(days=1)
    if local_date.weekday() == 6:
        return local_date - timedelta(days=2)
    return local_date


def build_default_shadow_sources(
    settings: RadarSettings,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> ShadowSources:
    """构造阶段2A真实适配器，但不立即请求任何外部来源。"""

    def security(run_id: str, batch_id: str, as_of: datetime):
        return fetch_security_master(
            radar_run_id=run_id,
            batch_id=batch_id,
            as_of=as_of,
            clock=clock,
        )

    def etf(run_id: str, batch_id: str, as_of: datetime):
        snapshot_date = _weekend_adjusted_snapshot_date(as_of)
        return fetch_etf_registry(
            radar_run_id=run_id,
            batch_id=batch_id,
            as_of=as_of,
            snapshot_date=snapshot_date,
            clock=clock,
        )

    def quotes(
        symbols: Sequence[str],
        run_id: str,
        batch_id: str,
        as_of: datetime,
    ):
        return fetch_tencent_quotes(
            symbols,
            radar_run_id=run_id,
            batch_id=batch_id,
            as_of=as_of,
            batch_size=settings.quote_batch_size,
            timeout_seconds=settings.quote_timeout_seconds,
            clock=clock,
        )

    return ShadowSources(
        security_master=security,
        etf_registry=etf,
        quotes=quotes,
    )


class OneShotShadowRunner:
    """串联一次影子采集；不包含持续调度和跨进程任务锁。"""

    def __init__(
        self,
        *,
        repository: RadarRepository,
        settings: RadarSettings,
        sources: ShadowSources,
        clock: Callable[[], datetime] = _utc_now,
        run_lock=None,
    ):
        self._repository = repository
        self._settings = settings
        self._sources = sources
        self._clock = clock
        self._run_lock = run_lock or threading.Lock()

    def run_once(self, radar_run_id: str, as_of: datetime) -> ShadowRunResult:
        if not self._settings.enabled or not self._settings.shadow_mode:
            raise ShadowRunDisabledError(
                "RADAR_ENABLED和RADAR_SHADOW_MODE必须同时开启"
            )
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")
        if not self._run_lock.acquire(blocking=False):
            raise ShadowRunInProgressError("当前进程已有影子采集正在执行")

        run_started = False
        try:
            started_at = _aware_utc(self._clock(), "started_at")
            try:
                created = self._repository.start_run(
                    radar_run_id,
                    as_of,
                    started_at=started_at,
                    shadow_mode=True,
                )
            except RepositoryConflictError as exc:
                raise ShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                ) from exc
            if not created:
                raise ShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                )
            run_started = True

            security_batch_id = f"{radar_run_id}:security-master"
            security_batch = self._fetch_or_failure(
                source_key="security_master",
                source_name="official_exchange_security_master",
                radar_run_id=radar_run_id,
                batch_id=security_batch_id,
                as_of=as_of,
                expected_count=None,
                fetch=lambda: self._sources.security_master(
                    radar_run_id,
                    security_batch_id,
                    as_of,
                ),
            )
            self._validate_batch(
                security_batch.meta,
                radar_run_id=radar_run_id,
                batch_id=security_batch_id,
                source="official_exchange_security_master",
                as_of=as_of,
                item_count=len(security_batch.items),
            )
            security_health = self._evaluate_registry_health(
                security_batch.meta,
                required_fields=("symbol", "name", "listing_date"),
            )
            self._repository.record_source_status(
                security_batch.meta,
                security_health,
            )
            security_history = (
                self._repository.sync_security_master(security_batch)
                if security_health.allows_new_state
                else HistoryWriteResult()
            )

            etf_batch_id = f"{radar_run_id}:etf-registry"
            etf_batch = self._fetch_or_failure(
                source_key="etf_registry",
                source_name="official_exchange_etf_registry",
                radar_run_id=radar_run_id,
                batch_id=etf_batch_id,
                as_of=as_of,
                expected_count=None,
                fetch=lambda: self._sources.etf_registry(
                    radar_run_id,
                    etf_batch_id,
                    as_of,
                ),
            )
            self._validate_batch(
                etf_batch.meta,
                radar_run_id=radar_run_id,
                batch_id=etf_batch_id,
                source="official_exchange_etf_registry",
                as_of=as_of,
                item_count=len(etf_batch.items),
            )
            etf_health = self._evaluate_registry_health(
                etf_batch.meta,
                required_fields=("symbol", "name", "source_type"),
            )
            self._repository.record_source_status(etf_batch.meta, etf_health)
            etf_history = (
                self._repository.sync_etf_registry(etf_batch)
                if etf_health.allows_new_state
                else HistoryWriteResult()
            )

            quote_symbols = tuple(dict.fromkeys(
                [item.symbol for item in security_batch.items]
                + [item.symbol for item in etf_batch.items]
            ))
            quote_batch_id = f"{radar_run_id}:quotes"
            quote_batch = self._fetch_or_failure(
                source_key="quotes",
                source_name="tencent_finance",
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                as_of=as_of,
                expected_count=len(quote_symbols),
                fetch=lambda: self._sources.quotes(
                    quote_symbols,
                    radar_run_id,
                    quote_batch_id,
                    as_of,
                ),
            )
            self._validate_batch(
                quote_batch.meta,
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                source="tencent_finance",
                as_of=as_of,
                item_count=len(quote_batch.items),
            )
            quote_health = self._evaluate_quote_health(quote_batch.meta)
            self._repository.record_source_status(
                quote_batch.meta,
                quote_health,
            )

            health_results = (
                security_health,
                etf_health,
                quote_health,
            )
            status = (
                "succeeded"
                if all(
                    health.status == SourceStatus.HEALTHY
                    for health in health_results
                )
                else "degraded"
            )
            self._repository.complete_run(
                radar_run_id,
                status=status,
                completed_at=_aware_utc(self._clock(), "completed_at"),
                expected_stock_count=security_batch.meta.expected_count,
                returned_stock_count=security_batch.meta.returned_count,
                expected_etf_count=etf_batch.meta.expected_count,
                returned_etf_count=etf_batch.meta.returned_count,
                error_code=(
                    None if status == "succeeded" else "source_health_degraded"
                ),
            )
            return ShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status=status,
                security_health=security_health,
                etf_health=etf_health,
                quote_health=quote_health,
                security_history=security_history,
                etf_history=etf_history,
                quote_count=quote_batch.meta.returned_count,
            )
        except (ShadowRunAlreadyExistsError, ShadowRunInProgressError):
            raise
        except _BatchValidationError as exc:
            if run_started:
                self._mark_failed(radar_run_id, exc.error_code)
            raise ShadowRunExecutionError(
                "影子采集批次合同不一致，整轮已标记失败"
            ) from exc
        except Exception as exc:
            if run_started:
                self._mark_failed(radar_run_id, "shadow_runner_internal_error")
            raise ShadowRunExecutionError(
                f"影子采集执行失败：{type(exc).__name__}"
            ) from exc
        finally:
            self._run_lock.release()

    def _fetch_or_failure(
        self,
        *,
        source_key: str,
        source_name: str,
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
        expected_count: Optional[int],
        fetch: Callable[[], SourceBatch],
    ) -> SourceBatch:
        try:
            return fetch()
        except Exception as exc:
            fetched_at = _aware_utc(self._clock(), "fetched_at")
            return SourceBatch(
                meta=RadarBatchMeta(
                    radarRunId=radar_run_id,
                    batchId=batch_id,
                    source=source_name,
                    asOf=as_of,
                    sourceTime=None,
                    fetchedAt=fetched_at,
                    expectedCount=expected_count,
                    returnedCount=0,
                    rowCoverage=(0.0 if expected_count is not None else None),
                    requiredFieldCoverage={},
                    issues=[SourceIssue(
                        code="source_exception",
                        source=source_key,
                        message=(
                            f"{source_name}采集异常：{type(exc).__name__}"
                        ),
                    )],
                ),
                items=[],
            )

    @staticmethod
    def _validate_batch(
        meta: RadarBatchMeta,
        *,
        radar_run_id: str,
        batch_id: str,
        source: str,
        as_of: datetime,
        item_count: int,
    ) -> None:
        if (
            meta.radar_run_id != radar_run_id
            or meta.batch_id != batch_id
            or meta.source != source
            or _aware_utc(meta.as_of, "批次as_of") != as_of
        ):
            raise _BatchValidationError(
                "批次身份、来源或asOf与当前雷达运行不一致",
                "shadow_runner_batch_identity",
            )
        if meta.returned_count != item_count:
            raise _BatchValidationError(
                "批次returnedCount与实际记录数不一致",
                "shadow_runner_batch_count",
            )
        if _aware_utc(meta.fetched_at, "批次fetched_at") < as_of:
            raise _BatchValidationError(
                "批次抓取时间早于本轮asOf",
                "shadow_runner_batch_time",
            )

    def _evaluate_registry_health(
        self,
        meta: RadarBatchMeta,
        *,
        required_fields: Sequence[str],
    ) -> SourceHealthResult:
        return evaluate_source_health(
            meta,
            SourceHealthPolicy(
                minimum_row_coverage=self._settings.minimum_row_coverage,
                minimum_required_field_coverage=(
                    self._settings.minimum_required_field_coverage
                ),
                maximum_age_seconds=None,
                required_fields=tuple(required_fields),
            ),
            now=_aware_utc(meta.as_of, "健康判定as_of"),
        )

    def _evaluate_quote_health(
        self,
        meta: RadarBatchMeta,
    ) -> SourceHealthResult:
        return evaluate_source_health(
            meta,
            SourceHealthPolicy(
                minimum_row_coverage=self._settings.minimum_row_coverage,
                minimum_required_field_coverage=(
                    self._settings.minimum_required_field_coverage
                ),
                maximum_age_seconds=self._settings.maximum_quote_age_seconds,
                required_fields=("price", "source_time"),
            ),
            now=_aware_utc(meta.as_of, "健康判定as_of"),
        )

    def _mark_failed(self, radar_run_id: str, error_code: str) -> None:
        try:
            self._repository.complete_run(
                radar_run_id,
                status="failed",
                completed_at=_aware_utc(self._clock(), "completed_at"),
                error_code=error_code,
            )
        except RadarRepositoryError:
            return
