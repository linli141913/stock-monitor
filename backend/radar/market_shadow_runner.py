"""市场环境当期聚合的单轮影子执行器。

本模块不创建数据库连接、不读取环境变量、不注册调度任务，也不保存
逐证券行情。调用方必须显式提供仓储、冻结时点、运行标识和两个行情来源。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence, Tuple

from radar.contracts import (
    IndexQuoteSnapshot,
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceHealthResult,
    SourceIssue,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.market_features import build_market_features
from radar.repository import (
    RadarRepository,
    RadarRepositoryError,
    RepositoryConflictError,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.market_indices import (
    MARKET_INDEX_IDENTITIES,
    fetch_market_indices,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes


UTC = timezone.utc


class MarketShadowRunError(RuntimeError):
    """市场环境影子执行器基础错误。"""


class MarketShadowRunInProgressError(MarketShadowRunError):
    """当前进程已有市场环境影子运行。"""


class MarketShadowRunAlreadyExistsError(MarketShadowRunError):
    """相同运行标识已经存在。"""


class MarketShadowRunExecutionError(MarketShadowRunError):
    """批次合同或仓储错误导致执行失败。"""


class _BatchValidationError(MarketShadowRunError):
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class _GateRejected(MarketShadowRunError):
    def __init__(self, *reasons: str):
        normalized = tuple(dict.fromkeys(reason for reason in reasons if reason))
        super().__init__("、".join(normalized))
        self.reasons = normalized


IndexFetcher = Callable[
    [str, str, datetime],
    SourceBatch[IndexQuoteSnapshot],
]
QuoteFetcher = Callable[
    [Sequence[str], str, str, datetime],
    SourceBatch[QuoteSnapshot],
]


@dataclass(frozen=True)
class MarketShadowPolicy:
    minimum_quote_row_coverage: float = 0.995
    minimum_required_field_coverage: float = 0.99
    maximum_quote_age_seconds: int = 90
    maximum_future_skew_seconds: int = 5
    maximum_source_skew_seconds: int = 15
    required_quote_fields: Tuple[str, ...] = (
        "price",
        "source_time",
        "change_percent",
        "turnover_amount_source",
    )
    required_index_fields: Tuple[str, ...] = (
        "price",
        "change_percent",
        "source_time",
    )

    def __post_init__(self):
        if not 0 <= self.minimum_quote_row_coverage <= 1:
            raise ValueError("minimum_quote_row_coverage必须在0到1之间")
        if not 0 <= self.minimum_required_field_coverage <= 1:
            raise ValueError("minimum_required_field_coverage必须在0到1之间")
        if self.maximum_quote_age_seconds <= 0:
            raise ValueError("maximum_quote_age_seconds必须大于0")
        if self.maximum_future_skew_seconds < 0:
            raise ValueError("maximum_future_skew_seconds不得小于0")
        if self.maximum_source_skew_seconds < 0:
            raise ValueError("maximum_source_skew_seconds不得小于0")
        if not self.required_quote_fields or not self.required_index_fields:
            raise ValueError("市场环境必需字段不能为空")


@dataclass(frozen=True)
class MarketShadowRunResult:
    radar_run_id: str
    as_of: datetime
    status: str
    gate_passed: bool
    gate_reasons: Tuple[str, ...]
    index_health: Optional[SourceHealthResult]
    quote_health: Optional[SourceHealthResult]
    stock_count: int
    etf_count: int
    returned_stock_count: int
    returned_etf_count: int
    persisted_environment_count: int
    persisted_index_count: int

    @property
    def item_count(self) -> int:
        """为通用调度监控提供已落库的市场环境聚合数。"""
        return self.persisted_environment_count


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def build_default_market_index_fetcher(
    *,
    timeout_seconds: float = 5.0,
    clock: Callable[[], datetime] = _utc_now,
) -> IndexFetcher:
    """构造真实腾讯指数来源，但不立即发出网络请求。"""

    def indices(
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[IndexQuoteSnapshot]:
        return fetch_market_indices(
            radar_run_id,
            batch_id,
            as_of,
            timeout_seconds=timeout_seconds,
            clock=clock,
        )

    return indices


def build_default_market_quote_fetcher(
    *,
    batch_size: int = 100,
    timeout_seconds: float = 5.0,
    clock: Callable[[], datetime] = _utc_now,
) -> QuoteFetcher:
    """构造真实腾讯A股和ETF行情来源，但不立即发出网络请求。"""

    def quotes(
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[QuoteSnapshot]:
        return fetch_tencent_quotes(
            symbols,
            radar_run_id=radar_run_id,
            batch_id=batch_id,
            as_of=as_of,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            clock=clock,
        )

    return quotes


class MarketShadowRunner:
    """从冻结A股池和ETF池生成并保存一轮市场环境聚合。"""

    def __init__(
        self,
        repository: RadarRepository,
        *,
        index_fetcher: IndexFetcher,
        quote_fetcher: QuoteFetcher,
        policy: MarketShadowPolicy = MarketShadowPolicy(),
        clock: Callable[[], datetime] = _utc_now,
        run_lock=None,
    ):
        self._repository = repository
        self._index_fetcher = index_fetcher
        self._quote_fetcher = quote_fetcher
        self._policy = policy
        self._clock = clock
        self._run_lock = run_lock or threading.Lock()

    def run_once(
        self,
        radar_run_id: str,
        as_of: datetime,
    ) -> MarketShadowRunResult:
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")
        started_at = _aware_utc(self._clock(), "started_at")
        run_lag_seconds = (started_at - as_of).total_seconds()
        if run_lag_seconds < 0:
            raise ValueError("as_of不能晚于执行器当前时间")
        if run_lag_seconds > self._policy.maximum_quote_age_seconds:
            raise ValueError("as_of已超过实时门禁，不允许补写历史轮次")
        if not self._run_lock.acquire(blocking=False):
            raise MarketShadowRunInProgressError(
                "当前进程已有市场环境影子运行"
            )

        run_started = False
        stock_symbols: Tuple[str, ...] = ()
        etf_symbols: Tuple[str, ...] = ()
        returned_stock_count = 0
        returned_etf_count = 0
        index_health = None
        quote_health = None
        try:
            try:
                created = self._repository.start_run(
                    radar_run_id,
                    as_of,
                    started_at=started_at,
                    shadow_mode=True,
                )
            except RepositoryConflictError as exc:
                raise MarketShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                ) from exc
            if not created:
                raise MarketShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                )
            run_started = True

            stock_symbols = self._repository.list_security_symbols(as_of)
            if not stock_symbols:
                raise _GateRejected("security_master_empty")
            if len(stock_symbols) != len(set(stock_symbols)):
                raise _GateRejected("security_master_identity_not_unique")

            etf_symbols = self._repository.list_etf_symbols(as_of)
            if not etf_symbols:
                raise _GateRejected("etf_registry_empty")
            if len(etf_symbols) != len(set(etf_symbols)):
                raise _GateRejected("etf_registry_identity_not_unique")
            if set(stock_symbols) & set(etf_symbols):
                raise _GateRejected("stock_etf_universe_overlap")

            index_batch_id = f"{radar_run_id}:tencent-indices"
            quote_batch_id = f"{radar_run_id}:tencent-quotes"
            index_batch = self._fetch_indices(
                radar_run_id=radar_run_id,
                batch_id=index_batch_id,
                as_of=as_of,
            )
            quote_batch = self._fetch_quotes(
                symbols=stock_symbols + etf_symbols,
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                as_of=as_of,
            )
            self._validate_index_batch(
                index_batch,
                radar_run_id=radar_run_id,
                batch_id=index_batch_id,
                as_of=as_of,
            )
            self._validate_quote_batch(
                quote_batch,
                symbols=stock_symbols + etf_symbols,
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                as_of=as_of,
            )

            index_health = evaluate_source_health(
                index_batch.meta,
                SourceHealthPolicy(
                    minimum_row_coverage=1.0,
                    minimum_required_field_coverage=1.0,
                    maximum_age_seconds=self._policy.maximum_quote_age_seconds,
                    maximum_future_skew_seconds=(
                        self._policy.maximum_future_skew_seconds
                    ),
                    required_fields=self._policy.required_index_fields,
                ),
                now=as_of,
            )
            quote_health = evaluate_source_health(
                quote_batch.meta,
                SourceHealthPolicy(
                    minimum_row_coverage=(
                        self._policy.minimum_quote_row_coverage
                    ),
                    minimum_required_field_coverage=(
                        self._policy.minimum_required_field_coverage
                    ),
                    maximum_age_seconds=self._policy.maximum_quote_age_seconds,
                    maximum_future_skew_seconds=(
                        self._policy.maximum_future_skew_seconds
                    ),
                    required_fields=self._policy.required_quote_fields,
                ),
                now=as_of,
            )
            self._repository.record_source_status(
                index_batch.meta,
                index_health,
            )
            self._repository.record_source_status(
                quote_batch.meta,
                quote_health,
            )

            try:
                features = build_market_features(
                    index_batch,
                    quote_batch,
                    stock_symbols=stock_symbols,
                    etf_symbols=etf_symbols,
                    turnover_unit_status=UnitVerificationStatus.UNVERIFIED,
                    minimum_row_coverage=(
                        self._policy.minimum_quote_row_coverage
                    ),
                    minimum_required_field_coverage=(
                        self._policy.minimum_required_field_coverage
                    ),
                    maximum_source_skew_seconds=(
                        self._policy.maximum_source_skew_seconds
                    ),
                )
            except ValueError as exc:
                raise _GateRejected("market_feature_contract_rejected") from exc

            returned_symbols = {item.symbol for item in quote_batch.items}
            returned_stock_count = len(returned_symbols & set(stock_symbols))
            returned_etf_count = len(returned_symbols & set(etf_symbols))
            gate_reasons = self._gate_reasons(
                stock_symbols=stock_symbols,
                etf_symbols=etf_symbols,
                index_batch=index_batch,
                quote_batch=quote_batch,
                index_health=index_health,
                quote_health=quote_health,
                features=features,
            )
            if gate_reasons:
                self._complete_degraded(
                    radar_run_id,
                    stock_symbols=stock_symbols,
                    etf_symbols=etf_symbols,
                    returned_stock_count=returned_stock_count,
                    returned_etf_count=returned_etf_count,
                    error_code="market_feature_gate_rejected",
                )
                return MarketShadowRunResult(
                    radar_run_id=radar_run_id,
                    as_of=as_of,
                    status="degraded",
                    gate_passed=False,
                    gate_reasons=gate_reasons,
                    index_health=index_health,
                    quote_health=quote_health,
                    stock_count=len(stock_symbols),
                    etf_count=len(etf_symbols),
                    returned_stock_count=returned_stock_count,
                    returned_etf_count=returned_etf_count,
                    persisted_environment_count=0,
                    persisted_index_count=0,
                )

            write_result = self._repository.record_market_feature_snapshot(
                features
            )
            self._complete_degraded(
                radar_run_id,
                stock_symbols=stock_symbols,
                etf_symbols=etf_symbols,
                returned_stock_count=returned_stock_count,
                returned_etf_count=returned_etf_count,
                error_code="market_features_shadow_unit_unverified",
            )
            return MarketShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status="degraded",
                gate_passed=True,
                gate_reasons=(),
                index_health=index_health,
                quote_health=quote_health,
                stock_count=len(stock_symbols),
                etf_count=len(etf_symbols),
                returned_stock_count=returned_stock_count,
                returned_etf_count=returned_etf_count,
                persisted_environment_count=int(write_result.inserted > 0),
                persisted_index_count=max(0, write_result.inserted - 1),
            )
        except (MarketShadowRunAlreadyExistsError, MarketShadowRunInProgressError):
            raise
        except _GateRejected as exc:
            if run_started:
                self._complete_degraded(
                    radar_run_id,
                    stock_symbols=stock_symbols,
                    etf_symbols=etf_symbols,
                    returned_stock_count=returned_stock_count,
                    returned_etf_count=returned_etf_count,
                    error_code="market_feature_gate_rejected",
                )
            return MarketShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status="degraded",
                gate_passed=False,
                gate_reasons=exc.reasons,
                index_health=index_health,
                quote_health=quote_health,
                stock_count=len(stock_symbols),
                etf_count=len(etf_symbols),
                returned_stock_count=returned_stock_count,
                returned_etf_count=returned_etf_count,
                persisted_environment_count=0,
                persisted_index_count=0,
            )
        except _BatchValidationError as exc:
            if run_started:
                self._mark_failed(radar_run_id, exc.error_code)
            raise MarketShadowRunExecutionError(
                "市场环境影子批次合同不一致，整轮已标记失败"
            ) from exc
        except Exception as exc:
            if run_started:
                self._mark_failed(radar_run_id, "market_shadow_internal_error")
            raise MarketShadowRunExecutionError(
                f"市场环境影子执行失败：{type(exc).__name__}"
            ) from exc
        finally:
            self._run_lock.release()

    def _fetch_indices(
        self,
        *,
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[IndexQuoteSnapshot]:
        try:
            return self._index_fetcher(radar_run_id, batch_id, as_of)
        except Exception as exc:
            return SourceBatch(
                meta=RadarBatchMeta(
                    radarRunId=radar_run_id,
                    batchId=batch_id,
                    source="tencent_finance_indices",
                    asOf=as_of,
                    sourceTime=None,
                    fetchedAt=_aware_utc(self._clock(), "fetched_at"),
                    expectedCount=len(MARKET_INDEX_IDENTITIES),
                    returnedCount=0,
                    rowCoverage=0.0,
                    requiredFieldCoverage={},
                    issues=[SourceIssue(
                        code="source_exception",
                        source="tencent_finance_indices",
                        message=(
                            "腾讯指数采集异常："
                            f"{type(exc).__name__}"
                        ),
                    )],
                ),
                items=[],
            )

    def _fetch_quotes(
        self,
        *,
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[QuoteSnapshot]:
        try:
            return self._quote_fetcher(
                symbols,
                radar_run_id,
                batch_id,
                as_of,
            )
        except Exception as exc:
            return SourceBatch(
                meta=RadarBatchMeta(
                    radarRunId=radar_run_id,
                    batchId=batch_id,
                    source="tencent_finance",
                    asOf=as_of,
                    sourceTime=None,
                    fetchedAt=_aware_utc(self._clock(), "fetched_at"),
                    expectedCount=len(symbols),
                    returnedCount=0,
                    rowCoverage=0.0,
                    requiredFieldCoverage={},
                    issues=[SourceIssue(
                        code="source_exception",
                        source="tencent_finance",
                        message=(
                            "腾讯行情采集异常："
                            f"{type(exc).__name__}"
                        ),
                    )],
                ),
                items=[],
            )

    @staticmethod
    def _validate_index_batch(
        batch: SourceBatch[IndexQuoteSnapshot],
        *,
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> None:
        meta = batch.meta
        if (
            meta.radar_run_id != radar_run_id
            or meta.batch_id != batch_id
            or meta.source != "tencent_finance_indices"
            or _aware_utc(meta.as_of, "指数批次as_of") != as_of
        ):
            raise _BatchValidationError(
                "指数批次身份、来源或asOf不一致",
                "market_shadow_index_batch_identity",
            )
        if meta.expected_count != len(MARKET_INDEX_IDENTITIES):
            raise _BatchValidationError(
                "指数批次expectedCount与固定指数集合不一致",
                "market_shadow_index_batch_expected_count",
            )
        if meta.returned_count != len(batch.items):
            raise _BatchValidationError(
                "指数批次returnedCount与实际记录数不一致",
                "market_shadow_index_batch_returned_count",
            )
        if _aware_utc(meta.fetched_at, "指数批次fetched_at") < as_of:
            raise _BatchValidationError(
                "指数批次抓取时间早于本轮asOf",
                "market_shadow_index_batch_time",
            )

    @staticmethod
    def _validate_quote_batch(
        batch: SourceBatch[QuoteSnapshot],
        *,
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> None:
        meta = batch.meta
        if (
            meta.radar_run_id != radar_run_id
            or meta.batch_id != batch_id
            or meta.source != "tencent_finance"
            or _aware_utc(meta.as_of, "行情批次as_of") != as_of
        ):
            raise _BatchValidationError(
                "行情批次身份、来源或asOf不一致",
                "market_shadow_quote_batch_identity",
            )
        if meta.expected_count != len(symbols):
            raise _BatchValidationError(
                "行情批次expectedCount与证券池不一致",
                "market_shadow_quote_batch_expected_count",
            )
        if meta.returned_count != len(batch.items):
            raise _BatchValidationError(
                "行情批次returnedCount与实际记录数不一致",
                "market_shadow_quote_batch_returned_count",
            )
        if _aware_utc(meta.fetched_at, "行情批次fetched_at") < as_of:
            raise _BatchValidationError(
                "行情批次抓取时间早于本轮asOf",
                "market_shadow_quote_batch_time",
            )

    def _gate_reasons(
        self,
        *,
        stock_symbols: Sequence[str],
        etf_symbols: Sequence[str],
        index_batch: SourceBatch[IndexQuoteSnapshot],
        quote_batch: SourceBatch[QuoteSnapshot],
        index_health: SourceHealthResult,
        quote_health: SourceHealthResult,
        features,
    ) -> Tuple[str, ...]:
        reasons = [
            f"index_health:{reason}"
            for reason in index_health.reasons
        ]
        reasons.extend(
            f"quote_health:{reason}"
            for reason in quote_health.reasons
        )
        if index_health.status != SourceStatus.HEALTHY and not index_health.reasons:
            reasons.append("index_health_not_healthy")
        if quote_health.status != SourceStatus.HEALTHY and not quote_health.reasons:
            reasons.append("quote_health_not_healthy")

        required_index_keys = {
            identity.index_key
            for identity in MARKET_INDEX_IDENTITIES
        }
        returned_index_keys = [item.index_key for item in index_batch.items]
        if len(returned_index_keys) != len(set(returned_index_keys)):
            reasons.append("index_identities_not_unique")
        if set(returned_index_keys) != required_index_keys:
            reasons.append("required_index_identities_missing")

        expected_quote_symbols = set(stock_symbols) | set(etf_symbols)
        returned_quote_symbols = [item.symbol for item in quote_batch.items]
        if len(returned_quote_symbols) != len(set(returned_quote_symbols)):
            reasons.append("quote_symbols_not_unique")
        if set(returned_quote_symbols) != expected_quote_symbols:
            reasons.append("quote_symbol_set_mismatch")

        for prefix, completeness in (
            ("index", features.index_completeness),
            ("breadth", features.breadth.completeness),
            ("turnover", features.turnover.completeness),
        ):
            if not completeness.is_complete:
                reasons.extend(completeness.reasons)
                reasons.append(f"{prefix}_features_incomplete")
        if features.excluded_etf_count != len(etf_symbols):
            reasons.append("excluded_etf_count_mismatch")
        if features.duplicate_symbols:
            reasons.append("duplicate_quote_symbols")
        if features.unknown_symbols:
            reasons.append("unknown_quote_symbols")
        if features.turnover.unit_status != UnitVerificationStatus.UNVERIFIED:
            reasons.append("turnover_unit_status_must_remain_unverified")
        if features.turnover.formal_usable:
            reasons.append("formal_use_must_remain_false")
        return _dedupe(reasons)

    def _complete_degraded(
        self,
        radar_run_id: str,
        *,
        stock_symbols: Sequence[str],
        etf_symbols: Sequence[str],
        returned_stock_count: int,
        returned_etf_count: int,
        error_code: str,
    ) -> None:
        self._repository.complete_run(
            radar_run_id,
            status="degraded",
            completed_at=_aware_utc(self._clock(), "completed_at"),
            expected_stock_count=(len(stock_symbols) if stock_symbols else None),
            returned_stock_count=(
                returned_stock_count if stock_symbols else None
            ),
            expected_etf_count=(len(etf_symbols) if etf_symbols else None),
            returned_etf_count=(returned_etf_count if etf_symbols else None),
            error_code=error_code,
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
