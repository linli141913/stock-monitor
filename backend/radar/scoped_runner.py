"""按证券名册、股票行情和ETF行情拆分的影子任务执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Sequence

from radar.config import RadarSettings
from radar.contracts import (
    RadarBatchMeta,
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
from radar.shadow_runner import (
    ShadowRunAlreadyExistsError,
    ShadowRunDisabledError,
    ShadowRunExecutionError,
    ShadowSources,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health


UTC = timezone.utc
SECURITY_SOURCE = "official_exchange_security_master"
ETF_SOURCE = "official_exchange_etf_registry"
QUOTE_SOURCE = "tencent_finance"


class RadarTaskScope(str, Enum):
    REGISTRY = "registry"
    STOCK_QUOTES = "stock_quotes"
    ETF_QUOTES = "etf_quotes"


class _BatchValidationError(RuntimeError):
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ScopedShadowRunResult:
    radar_run_id: str
    as_of: datetime
    scope: RadarTaskScope
    status: str
    item_count: int
    source_health: tuple[SourceHealthResult, ...]
    security_history: HistoryWriteResult = HistoryWriteResult()
    etf_history: HistoryWriteResult = HistoryWriteResult()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


class ScopedShadowRunner:
    """运行一个明确范围；数据库连接和跨进程锁由调用方管理。"""

    def __init__(
        self,
        *,
        repository: RadarRepository,
        settings: RadarSettings,
        sources: ShadowSources,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._repository = repository
        self._settings = settings
        self._sources = sources
        self._clock = clock

    def run_once(
        self,
        scope: RadarTaskScope,
        radar_run_id: str,
        as_of: datetime,
    ) -> ScopedShadowRunResult:
        if not self._settings.enabled or not self._settings.shadow_mode:
            raise ShadowRunDisabledError(
                "RADAR_ENABLED和RADAR_SHADOW_MODE必须同时开启"
            )
        if not isinstance(scope, RadarTaskScope):
            raise ValueError("雷达任务范围无效")
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")

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

            if scope == RadarTaskScope.REGISTRY:
                return self._run_registry(radar_run_id, as_of)
            return self._run_quotes(scope, radar_run_id, as_of)
        except ShadowRunAlreadyExistsError:
            raise
        except _BatchValidationError as exc:
            if run_started:
                self._mark_failed(radar_run_id, exc.error_code)
            raise ShadowRunExecutionError(
                "影子采集批次合同不一致，整轮已标记失败"
            ) from exc
        except Exception as exc:
            if run_started:
                self._mark_failed(radar_run_id, "scoped_shadow_runner_internal_error")
            raise ShadowRunExecutionError(
                f"分频影子采集执行失败：{type(exc).__name__}"
            ) from exc

    def _run_registry(
        self,
        radar_run_id: str,
        as_of: datetime,
    ) -> ScopedShadowRunResult:
        security_batch_id = f"{radar_run_id}:security-master"
        security_batch = self._fetch_or_failure(
            source_key="security_master",
            source_name=SECURITY_SOURCE,
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
            source=SECURITY_SOURCE,
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
            source_name=ETF_SOURCE,
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
            source=ETF_SOURCE,
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

        health = (security_health, etf_health)
        status = self._result_status(health)
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
        return ScopedShadowRunResult(
            radar_run_id=radar_run_id,
            as_of=as_of,
            scope=RadarTaskScope.REGISTRY,
            status=status,
            item_count=(
                security_batch.meta.returned_count
                + etf_batch.meta.returned_count
            ),
            source_health=health,
            security_history=security_history,
            etf_history=etf_history,
        )

    def _run_quotes(
        self,
        scope: RadarTaskScope,
        radar_run_id: str,
        as_of: datetime,
    ) -> ScopedShadowRunResult:
        if scope == RadarTaskScope.STOCK_QUOTES:
            symbols = self._repository.list_security_symbols(as_of)
            batch_suffix = "stock-quotes"
        elif scope == RadarTaskScope.ETF_QUOTES:
            symbols = self._repository.list_etf_symbols(as_of)
            batch_suffix = "etf-quotes"
        else:
            raise ValueError("行情任务范围无效")

        batch_id = f"{radar_run_id}:{batch_suffix}"
        batch = self._fetch_or_failure(
            source_key=batch_suffix,
            source_name=QUOTE_SOURCE,
            radar_run_id=radar_run_id,
            batch_id=batch_id,
            as_of=as_of,
            expected_count=len(symbols),
            fetch=lambda: self._sources.quotes(
                symbols,
                radar_run_id,
                batch_id,
                as_of,
            ),
        )
        self._validate_batch(
            batch.meta,
            radar_run_id=radar_run_id,
            batch_id=batch_id,
            source=QUOTE_SOURCE,
            as_of=as_of,
            item_count=len(batch.items),
        )
        quote_health = self._evaluate_quote_health(batch.meta)
        self._repository.record_source_status(batch.meta, quote_health)
        status = self._result_status((quote_health,))
        if scope == RadarTaskScope.STOCK_QUOTES:
            counts = {
                "expected_stock_count": len(symbols),
                "returned_stock_count": batch.meta.returned_count,
            }
        else:
            counts = {
                "expected_etf_count": len(symbols),
                "returned_etf_count": batch.meta.returned_count,
            }
        self._repository.complete_run(
            radar_run_id,
            status=status,
            completed_at=_aware_utc(self._clock(), "completed_at"),
            error_code=(
                None if status == "succeeded" else "source_health_degraded"
            ),
            **counts,
        )
        return ScopedShadowRunResult(
            radar_run_id=radar_run_id,
            as_of=as_of,
            scope=scope,
            status=status,
            item_count=batch.meta.returned_count,
            source_health=(quote_health,),
        )

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
                        message=f"{source_name}采集异常：{type(exc).__name__}",
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
                "scoped_shadow_runner_batch_identity",
            )
        if meta.returned_count != item_count:
            raise _BatchValidationError(
                "批次returnedCount与实际记录数不一致",
                "scoped_shadow_runner_batch_count",
            )
        if _aware_utc(meta.fetched_at, "批次fetched_at") < as_of:
            raise _BatchValidationError(
                "批次抓取时间早于本轮asOf",
                "scoped_shadow_runner_batch_time",
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

    @staticmethod
    def _result_status(health: Sequence[SourceHealthResult]) -> str:
        return (
            "succeeded"
            if all(item.status == SourceStatus.HEALTHY for item in health)
            else "degraded"
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
