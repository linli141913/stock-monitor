"""阶段10 ETF 现场行情捕获。

本模块不打开 SQLite，不写运行服务状态。纯转换器把一次已取得的
腾讯 ``SourceBatch`` 及其健康结论收敛为可审计的 ETF 影子工件和
同一字节内容绑定的运行回执；运行包装器只负责一次调用、锁和真实
时钟测量，发布由上层原子输入包负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import requests
import time
from typing import Callable, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.contracts import (
    QuoteSnapshot,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.formal_shadow_observation_collector import (
    ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
    ETF_TRUSTED_QUOTE_SOURCE_CONTRACT_ID,
    FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
    MODULE_COVERAGE_SCOPES,
    FormalShadowRunReceipt,
    EtfLiveShadowObservationArtifact,
    EtfLiveShadowRecord,
)
from radar.run_lock import CrossProcessFileLock
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.tencent_quotes import fetch_tencent_quotes


SHANGHAI = ZoneInfo("Asia/Shanghai")
MAXIMUM_SOURCE_AGE_SECONDS = 90
MAXIMUM_FUTURE_SKEW_SECONDS = 5
_TENCENT_SOURCE = "tencent_finance"


class FileLock(Protocol):
    def acquire(self, blocking: bool = False) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class EtfLiveShadowCapture:
    artifact: EtfLiveShadowObservationArtifact
    receipt: FormalShadowRunReceipt
    source_json_bytes: bytes
    receipt_json_bytes: bytes

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.source_json_bytes).hexdigest()


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}_timezone_required")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def validate_etf_live_shadow_symbols(values: Sequence[str]) -> Tuple[str, ...]:
    normalized = tuple(str(value or "").strip() for value in values)
    if (
        not normalized
        or normalized != tuple(sorted(normalized))
        or len(normalized) != len(set(normalized))
        or any(len(value) != 6 or not value.isdigit() for value in normalized)
    ):
        raise ValueError("etf_live_shadow_symbols_invalid")
    return normalized


def _finite(value: object, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (numeric > 0 if positive else True)


def _quote_record(
    quote: QuoteSnapshot,
    *,
    as_of: datetime,
    observed_at: datetime,
    source_health: SourceHealthResult,
) -> tuple[Optional[EtfLiveShadowRecord], str]:
    """返回唯一分类：record、missing 或 failed。"""
    if quote.source != _TENCENT_SOURCE:
        return None, "failed"
    if any((
        quote.source_time is None,
        not _finite(quote.price, positive=True),
        not _finite(quote.change_percent),
        not _finite(quote.turnover_amount_cny),
        quote.turnover_amount_unit_status != UnitVerificationStatus.VERIFIED,
    )):
        return None, "missing"
    source_time = quote.source_time
    fetched_at = quote.fetched_at
    if source_time > fetched_at:
        return None, "failed"
    age_seconds = (observed_at - source_time).total_seconds()
    stale = any((
        source_time.astimezone(SHANGHAI).date()
        != as_of.astimezone(SHANGHAI).date(),
        age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS,
        age_seconds > MAXIMUM_SOURCE_AGE_SECONDS,
        source_health.status == SourceStatus.STALE,
    ))
    return EtfLiveShadowRecord(
        symbol=quote.symbol,
        sourceTime=source_time,
        fetchedAt=fetched_at,
        lastPrice=float(quote.price),
        changePercent=float(quote.change_percent),
        turnoverAmountCny=float(quote.turnover_amount_cny),
        quoteSourceContractId=ETF_TRUSTED_QUOTE_SOURCE_CONTRACT_ID,
        status="stale" if stale else "ready",
    ), "record"


def _failed_capture(
    *,
    symbols: Tuple[str, ...],
    radar_run_id: str,
    as_of: datetime,
    fetched_at: datetime,
    observed_at: datetime,
    lock_state: str,
    duration_ms: int,
    source_health: SourceHealthResult,
) -> EtfLiveShadowObservationArtifact:
    return EtfLiveShadowObservationArtifact(
        contractId=ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
        status="failed",
        runId=radar_run_id,
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        requestedCount=len(symbols),
        returnedCount=0,
        missingCount=0,
        failedCount=len(symbols),
        staleCount=0,
        lockState=lock_state,
        durationMs=duration_ms,
        sourceHealthStatus=source_health.status.value,
        sourceHealthReasons=source_health.reasons,
        requestedSymbols=symbols,
        missingSymbols=(),
        failedSymbols=symbols,
        staleSymbols=(),
        records=(),
    )


def build_etf_live_shadow_capture(
    *,
    symbols: Sequence[str],
    radar_run_id: str,
    as_of: datetime,
    quote_batch: Optional[SourceBatch[QuoteSnapshot]],
    quote_health: SourceHealthResult,
    observed_at: datetime,
    lock_state: str,
    duration_ms: int,
) -> EtfLiveShadowCapture:
    """纯转换：真实来源失败不伪造 ``sourceTime`` 或可用观察日。"""
    requested = validate_etf_live_shadow_symbols(symbols)
    as_of = _aware(as_of, "etf_live_shadow_as_of")
    observed_at = _aware(observed_at, "etf_live_shadow_observed_at")
    if not radar_run_id.strip() or observed_at < as_of:
        raise ValueError("etf_live_shadow_identity_invalid")
    if lock_state not in {"acquired", "contended", "failed"}:
        raise ValueError("etf_live_shadow_lock_state_invalid")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
        raise ValueError("etf_live_shadow_duration_invalid")
    if not isinstance(quote_health, SourceHealthResult):
        raise ValueError("etf_live_shadow_health_unverified")

    fetched_at = as_of
    if quote_batch is not None:
        if not isinstance(quote_batch, SourceBatch):
            raise ValueError("etf_live_shadow_quote_batch_unverified")
        meta = quote_batch.meta
        fetched_at = _aware(meta.fetched_at, "etf_live_shadow_fetched_at")
        if any((
            meta.radar_run_id != radar_run_id,
            meta.batch_id != f"{radar_run_id}-stage10-etf-live-quotes",
            meta.source != _TENCENT_SOURCE,
            meta.as_of != as_of,
            meta.expected_count != len(requested),
            meta.returned_count != len(quote_batch.items),
            fetched_at < as_of,
        )):
            raise ValueError("etf_live_shadow_quote_batch_identity_mismatch")
    if fetched_at > observed_at:
        raise ValueError("etf_live_shadow_observed_before_fetched")

    if lock_state != "acquired" or quote_batch is None:
        artifact = _failed_capture(
            symbols=requested,
            radar_run_id=radar_run_id,
            as_of=as_of,
            fetched_at=fetched_at,
            observed_at=observed_at,
            lock_state=lock_state,
            duration_ms=duration_ms,
            source_health=quote_health,
        )
    else:
        by_symbol: dict[str, QuoteSnapshot] = {}
        known_source_times = []
        for item in quote_batch.items:
            if not isinstance(item, QuoteSnapshot) or item.symbol not in requested:
                raise ValueError("etf_live_shadow_quote_batch_scope_invalid")
            if item.symbol in by_symbol:
                raise ValueError("etf_live_shadow_quote_batch_duplicate")
            by_symbol[item.symbol] = item
            if (
                item.source_time is not None
                and item.source_time <= item.fetched_at <= fetched_at
                and item.source_time.astimezone(SHANGHAI).date()
                == as_of.astimezone(SHANGHAI).date()
            ):
                known_source_times.append(item.source_time)
        records = []
        missing = []
        failed = []
        for symbol in requested:
            item = by_symbol.get(symbol)
            if item is None:
                missing.append(symbol)
                continue
            record, classification = _quote_record(
                item,
                as_of=as_of,
                observed_at=observed_at,
                source_health=quote_health,
            )
            if classification == "record":
                assert record is not None
                records.append(record)
            elif classification == "missing":
                missing.append(symbol)
            else:
                failed.append(symbol)
        artifact_source_time = (
            max(known_source_times) if known_source_times else None
        )
        artifact_fetched_at = fetched_at
        if artifact_source_time is None:
            # 没有任何真实来源时间时不允许用 batch/asOf 伪补。
            artifact = _failed_capture(
                symbols=requested,
                radar_run_id=radar_run_id,
                as_of=as_of,
                fetched_at=fetched_at,
                observed_at=observed_at,
                lock_state=lock_state,
                duration_ms=duration_ms,
                source_health=quote_health,
            )
        else:
            stale = tuple(item.symbol for item in records if item.status == "stale")
            status = (
                "failed" if failed or lock_state != "acquired"
                else "missing" if missing or not records
                else "stale" if stale
                else "failed" if quote_health.status != SourceStatus.HEALTHY
                else "ready"
            )
            artifact = EtfLiveShadowObservationArtifact(
                contractId=ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
                status=status,
                runId=radar_run_id,
                asOf=as_of,
                sourceTime=artifact_source_time,
                fetchedAt=artifact_fetched_at,
                requestedCount=len(requested),
                returnedCount=len(records),
                missingCount=len(missing),
                failedCount=len(failed),
                staleCount=len(stale),
                lockState=lock_state,
                durationMs=duration_ms,
                sourceHealthStatus=quote_health.status.value,
                sourceHealthReasons=quote_health.reasons,
                requestedSymbols=requested,
                missingSymbols=tuple(missing),
                failedSymbols=tuple(failed),
                staleSymbols=stale,
                records=tuple(records),
            )

    source_json_bytes = _canonical_json_bytes(
        artifact.model_dump(mode="json", by_alias=True)
    )
    receipt = FormalShadowRunReceipt(
        contractId=FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
        module="etfObservation",
        runId=radar_run_id,
        asOf=artifact.as_of,
        sourceTime=artifact.source_time,
        fetchedAt=artifact.fetched_at,
        observedAt=observed_at,
        sourceContractId=ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
        sourceArtifactSha256=hashlib.sha256(source_json_bytes).hexdigest(),
        coverageScope=MODULE_COVERAGE_SCOPES["etfObservation"],
        expectedCount=artifact.requested_count,
        observedCount=artifact.returned_count,
        missingCount=artifact.missing_count,
        failedCount=artifact.failed_count,
        staleCount=artifact.stale_count,
        coverage=artifact.returned_count / artifact.requested_count,
        lockState=artifact.lock_state,
        durationMs=artifact.duration_ms,
        sourceReady=(
            artifact.lock_state == "acquired"
            and artifact.returned_count == artifact.requested_count
            and artifact.missing_count == 0
            and artifact.failed_count == 0
            and artifact.stale_count == 0
            and artifact.source_health_status == "healthy"
        ),
        sourceHealthStatus=artifact.source_health_status,
        sourceHealthReasons=artifact.source_health_reasons,
    )
    receipt_json_bytes = _canonical_json_bytes(
        receipt.model_dump(mode="json", by_alias=True)
    )
    return EtfLiveShadowCapture(
        artifact=artifact,
        receipt=receipt,
        source_json_bytes=source_json_bytes,
        receipt_json_bytes=receipt_json_bytes,
    )


def _failed_health() -> SourceHealthResult:
    return SourceHealthResult(
        status=SourceStatus.FAILED,
        allowsNewState=False,
        reasons=("etf_live_shadow_quote_capture_failed",),
        ageSeconds=None,
    )


def _duration_ms(started: float, finished: float) -> int:
    if any((
        isinstance(started, bool),
        isinstance(finished, bool),
        not isinstance(started, (int, float)),
        not isinstance(finished, (int, float)),
        not math.isfinite(float(started)),
        not math.isfinite(float(finished)),
        finished < started,
    )):
        raise ValueError("etf_live_shadow_monotonic_clock_invalid")
    return int((finished - started) * 1000)


def run_live_etf_shadow_capture(
    *,
    symbols: Sequence[str],
    radar_run_id: str,
    lock_path: Path,
    quote_fetcher: Callable[..., SourceBatch[QuoteSnapshot]] = fetch_tencent_quotes,
    clock: Callable[[], datetime],
    monotonic_clock: Callable[[], float] = time.monotonic,
    lock_factory: Callable[[Path], FileLock] = CrossProcessFileLock,
) -> EtfLiveShadowCapture:
    """实际一次采集包装器；调用方随后可把结果交给原子输入包发布。"""
    requested = validate_etf_live_shadow_symbols(symbols)
    started_at = _aware(clock(), "etf_live_shadow_started_at")
    started_monotonic = monotonic_clock()
    lock = lock_factory(lock_path)
    if not lock.acquire(blocking=False):
        observed_at = _aware(clock(), "etf_live_shadow_observed_at")
        duration_ms = _duration_ms(started_monotonic, monotonic_clock())
        return build_etf_live_shadow_capture(
            symbols=requested,
            radar_run_id=radar_run_id,
            as_of=started_at,
            quote_batch=None,
            quote_health=_failed_health(),
            observed_at=observed_at,
            lock_state="contended",
            duration_ms=duration_ms,
        )
    try:
        try:
            batch = quote_fetcher(
                requested,
                radar_run_id=radar_run_id,
                batch_id=f"{radar_run_id}-stage10-etf-live-quotes",
                as_of=started_at,
            )
        except requests.RequestException:
            observed_at = _aware(clock(), "etf_live_shadow_observed_at")
            health = _failed_health()
            batch = None
        else:
            observed_at = _aware(clock(), "etf_live_shadow_observed_at")
            health = evaluate_source_health(
                batch.meta,
                SourceHealthPolicy(
                    minimum_row_coverage=1.0,
                    minimum_required_field_coverage=1.0,
                    maximum_age_seconds=MAXIMUM_SOURCE_AGE_SECONDS,
                    maximum_future_skew_seconds=MAXIMUM_FUTURE_SKEW_SECONDS,
                    required_fields=("price", "source_time"),
                ),
                observed_at,
            )
        duration_ms = _duration_ms(started_monotonic, monotonic_clock())
        return build_etf_live_shadow_capture(
            symbols=requested,
            radar_run_id=radar_run_id,
            as_of=started_at,
            quote_batch=batch,
            quote_health=health,
            observed_at=observed_at,
            lock_state="acquired",
            duration_ms=duration_ms,
        )
    finally:
        lock.release()
