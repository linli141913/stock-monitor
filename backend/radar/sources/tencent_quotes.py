from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

import asset_context
from radar.contracts import (
    QuoteTradingStatus,
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
    UnitVerificationStatus,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={query}"
TENCENT_REQUEST_ATTEMPTS = 2
TURNOVER_AMOUNT_SCALE_TO_CNY = 10000.0
TURNOVER_AMOUNT_CROSSCHECK_TOLERANCE_CNY = 10000.0
MARKET_CAP_SCALE_TO_CNY = 100_000_000.0
MARKET_CAP_CROSSCHECK_TOLERANCE_CNY = 500_000.0
TENCENT_NON_TRADING_STATUSES = {
    "S": QuoteTradingStatus.SUSPENDED,
    "D": QuoteTradingStatus.DELISTED,
    "U": QuoteTradingStatus.UNLISTED,
}


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _optional_float(value) -> Optional[float]:
    text = str(value or "").strip()
    if not text or text == "--":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _optional_non_negative_finite(value) -> Optional[float]:
    parsed = _optional_float(value)
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _source_time(value) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(
            tzinfo=SHANGHAI_TZ
        )
    except ValueError:
        return None


def _trading_status(value) -> Optional[QuoteTradingStatus]:
    return TENCENT_NON_TRADING_STATUSES.get(str(value or "").strip().upper())


def _turnover_amount_cny(
    raw_value,
    packed_value,
) -> tuple[Optional[float], UnitVerificationStatus]:
    raw_amount = _optional_float(raw_value)
    packed_fields = str(packed_value or "").split("/")
    exact_amount = (
        _optional_float(packed_fields[2])
        if len(packed_fields) > 2
        else None
    )
    if (
        raw_amount is None
        or exact_amount is None
        or not math.isfinite(raw_amount)
        or not math.isfinite(exact_amount)
        or raw_amount < 0
        or exact_amount < 0
        or abs(
            raw_amount * TURNOVER_AMOUNT_SCALE_TO_CNY
            - exact_amount
        ) > TURNOVER_AMOUNT_CROSSCHECK_TOLERANCE_CNY
    ):
        return None, UnitVerificationStatus.UNVERIFIED
    return exact_amount, UnitVerificationStatus.VERIFIED


def _market_cap_cny(
    raw_value,
    price_value,
    total_shares_value,
    currency_value,
) -> tuple[Optional[float], UnitVerificationStatus]:
    raw_market_cap = _optional_non_negative_finite(raw_value)
    price = _optional_non_negative_finite(price_value)
    total_shares = _optional_non_negative_finite(total_shares_value)
    currency = str(currency_value or "").strip().upper()
    if (
        raw_market_cap is None
        or raw_market_cap <= 0
        or price is None
        or price <= 0
        or total_shares is None
        or total_shares <= 0
        or currency != "CNY"
        or not market_cap_crosscheck_matches(
            raw_market_cap * MARKET_CAP_SCALE_TO_CNY,
            price * total_shares,
        )
    ):
        return None, UnitVerificationStatus.UNVERIFIED
    return (
        raw_market_cap * MARKET_CAP_SCALE_TO_CNY,
        UnitVerificationStatus.VERIFIED,
    )


def market_cap_crosscheck_matches(
    scaled_market_cap_cny: float,
    derived_market_cap_cny: float,
) -> bool:
    """仅容忍两位小数市值的半单位边界及浮点微误差。"""

    difference = abs(scaled_market_cap_cny - derived_market_cap_cny)
    return bool(
        difference <= MARKET_CAP_CROSSCHECK_TOLERANCE_CNY
        or math.isclose(
            difference,
            MARKET_CAP_CROSSCHECK_TOLERANCE_CNY,
            rel_tol=0.0,
            abs_tol=1e-3,
        )
    )


def _field_coverage(items):
    if not items:
        return {
            "price": 0.0,
            "source_time": 0.0,
            "change_percent": 0.0,
            "turnover_amount_source": 0.0,
            "turnover_rate_percent": 0.0,
            "volume_ratio": 0.0,
            "market_cap_source": 0.0,
            "market_cap_cny": 0.0,
            "total_shares_source": 0.0,
            "currency": 0.0,
        }
    result = {}
    for field_name in (
        "price",
        "source_time",
        "change_percent",
        "turnover_amount_source",
        "turnover_rate_percent",
        "volume_ratio",
        "market_cap_source",
        "market_cap_cny",
        "total_shares_source",
        "currency",
    ):
        result[field_name] = sum(
            getattr(item, field_name) is not None
            for item in items
        ) / len(items)
    return result


def _returned_symbols(response_text: str, expected_symbols) -> set[str]:
    expected = set(expected_symbols)
    returned = set()
    for line in response_text.split(";"):
        if "=" not in line:
            continue
        fields = line.split("=", 1)[1].strip().strip('"').split("~")
        symbol = asset_context.normalize_symbol(
            fields[2] if len(fields) > 2 else ""
        )
        if symbol in expected:
            returned.add(symbol)
    return returned


def fetch_tencent_quotes(
    symbols: Iterable[str],
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    batch_size: int = 100,
    timeout_seconds: float = 5.0,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[QuoteSnapshot]:
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size必须在1到100之间")
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds必须在0到30秒之间")

    valid_symbols = []
    invalid_symbols = []
    for value in symbols:
        normalized = asset_context.normalize_symbol(value)
        if normalized.isdigit() and len(normalized) == 6:
            if normalized not in valid_symbols:
                valid_symbols.append(normalized)
        else:
            invalid_symbols.append(str(value))

    issues = []
    if invalid_symbols:
        issues.append(SourceIssue(
            code="invalid_symbols",
            message=f"忽略{len(invalid_symbols)}个无效或非A股/ETF代码",
            symbols=invalid_symbols,
        ))

    active_session = session or requests.Session()
    active_session.trust_env = False
    items_by_symbol = {}
    last_fetched_at = clock()

    for start in range(0, len(valid_symbols), batch_size):
        batch_symbols = valid_symbols[start:start + batch_size]
        query = ",".join(
            f"{asset_context.quote_prefix(symbol)}{symbol}"
            for symbol in batch_symbols
        )
        response = None
        response_symbol_count = -1
        last_error = None
        for _attempt in range(TENCENT_REQUEST_ATTEMPTS):
            try:
                candidate = active_session.get(
                    TENCENT_QUOTE_URL.format(query=query),
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=timeout_seconds,
                )
                candidate.encoding = "gbk"
                candidate.raise_for_status()
                last_error = None
                candidate_symbols = _returned_symbols(
                    candidate.text,
                    batch_symbols,
                )
                if len(candidate_symbols) >= response_symbol_count:
                    response = candidate
                    response_symbol_count = len(candidate_symbols)
                    last_fetched_at = clock()
                if len(candidate_symbols) == len(batch_symbols):
                    break
            except Exception as exc:
                last_error = exc

        if response is None:
            issues.append(SourceIssue(
                code="batch_request_failed",
                source="tencent_finance",
                batchIndex=start // batch_size,
                message=(
                    "腾讯批量行情请求失败："
                    f"{type(last_error).__name__ if last_error else 'UnknownError'}"
                ),
                symbols=batch_symbols,
            ))
            continue
        if last_error is not None:
            issues.append(SourceIssue(
                code="batch_retry_failed",
                source="tencent_finance",
                batchIndex=start // batch_size,
                message=f"腾讯批量行情重试失败：{type(last_error).__name__}",
                symbols=batch_symbols,
            ))

        for line in response.text.split(";"):
            if "=" not in line:
                continue
            fields = line.split("=", 1)[1].strip().strip('"').split("~")
            if len(fields) <= 3:
                continue
            symbol = asset_context.normalize_symbol(fields[2])
            if symbol not in batch_symbols:
                continue
            if symbol in items_by_symbol:
                issues.append(SourceIssue(
                    code="duplicate_quote",
                    source="tencent_finance",
                    message=f"腾讯行情重复返回{symbol}",
                    symbols=[symbol],
                ))
                continue
            turnover_amount_cny, turnover_amount_unit_status = (
                _turnover_amount_cny(
                    fields[37] if len(fields) > 37 else None,
                    fields[35] if len(fields) > 35 else None,
                )
            )
            market_cap_cny, market_cap_unit_status = _market_cap_cny(
                fields[45] if len(fields) > 45 else None,
                fields[3] if len(fields) > 3 else None,
                fields[73] if len(fields) > 73 else None,
                fields[82] if len(fields) > 82 else None,
            )
            items_by_symbol[symbol] = QuoteSnapshot(
                symbol=symbol,
                name=fields[1].strip() if len(fields) > 1 else "",
                sourceTime=_source_time(fields[30] if len(fields) > 30 else None),
                fetchedAt=last_fetched_at,
                tradingStatus=_trading_status(
                    fields[40] if len(fields) > 40 else None
                ),
                price=_optional_float(fields[3] if len(fields) > 3 else None),
                previousClose=_optional_non_negative_finite(
                    fields[4] if len(fields) > 4 else None
                ),
                openPrice=_optional_non_negative_finite(
                    fields[5] if len(fields) > 5 else None
                ),
                changePercent=_optional_float(
                    fields[32] if len(fields) > 32 else None
                ),
                highPrice=_optional_non_negative_finite(
                    fields[33] if len(fields) > 33 else None
                ),
                lowPrice=_optional_non_negative_finite(
                    fields[34] if len(fields) > 34 else None
                ),
                turnoverAmountSource=_optional_float(
                    fields[37] if len(fields) > 37 else None
                ),
                turnoverAmountCny=turnover_amount_cny,
                turnoverAmountUnitStatus=(
                    turnover_amount_unit_status
                ),
                turnoverRatePercent=_optional_float(
                    fields[38] if len(fields) > 38 else None
                ),
                marketCapSource=_optional_non_negative_finite(
                    fields[45] if len(fields) > 45 else None
                ),
                marketCapCny=market_cap_cny,
                marketCapUnitStatus=market_cap_unit_status,
                totalSharesSource=_optional_non_negative_finite(
                    fields[73] if len(fields) > 73 else None
                ),
                currency=(
                    str(fields[82]).strip().upper()
                    if len(fields) > 82 and str(fields[82]).strip()
                    else None
                ),
                upperLimitPriceSource=_optional_non_negative_finite(
                    fields[47] if len(fields) > 47 else None
                ),
                lowerLimitPriceSource=_optional_non_negative_finite(
                    fields[48] if len(fields) > 48 else None
                ),
                volumeRatio=_optional_float(
                    fields[49] if len(fields) > 49 else None
                ),
            )

    items = [
        items_by_symbol[symbol]
        for symbol in valid_symbols
        if symbol in items_by_symbol
    ]
    missing_symbols = [
        symbol for symbol in valid_symbols if symbol not in items_by_symbol
    ]
    if missing_symbols:
        issues.append(SourceIssue(
            code="missing_symbols",
            source="tencent_finance",
            message=f"腾讯行情未返回{len(missing_symbols)}只证券",
            symbols=missing_symbols,
        ))

    expected_count = len(valid_symbols)
    row_coverage = len(items) / expected_count if expected_count else 0.0
    source_times = [item.source_time for item in items if item.source_time]
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="tencent_finance",
        asOf=as_of,
        sourceTime=max(source_times) if source_times else None,
        fetchedAt=last_fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage=_field_coverage(items),
        issues=issues,
    )
    return SourceBatch[QuoteSnapshot](meta=meta, items=items)


def fetch_tencent_quotes_concurrent(
    symbols: Iterable[str],
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    chunk_size: int = 100,
    max_workers: int = 8,
    timeout_seconds: float = 5.0,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[QuoteSnapshot]:
    """并行采集腾讯行情，保留每个子批次的真实来源时间。

    该入口只缩短网络采集窗口，不改写子批次返回的 sourceTime/fetchedAt。
    聚合批次仍使用调用方冻结的 as_of，来源健康门禁会据此拒绝未来或过期数据。
    """
    if not 1 <= chunk_size <= 100:
        raise ValueError("chunk_size必须在1到100之间")
    if not 1 <= max_workers <= 32:
        raise ValueError("max_workers必须在1到32之间")
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds必须在0到30秒之间")

    valid_symbols = []
    invalid_symbols = []
    for value in symbols:
        normalized = asset_context.normalize_symbol(value)
        if normalized.isdigit() and len(normalized) == 6:
            if normalized not in valid_symbols:
                valid_symbols.append(normalized)
        else:
            invalid_symbols.append(str(value))

    issues = []
    if invalid_symbols:
        issues.append(SourceIssue(
            code="invalid_symbols",
            source="tencent_finance",
            message=f"忽略{len(invalid_symbols)}个无效或非A股/ETF代码",
            symbols=invalid_symbols,
        ))

    chunks = [
        valid_symbols[start:start + chunk_size]
        for start in range(0, len(valid_symbols), chunk_size)
    ]
    child_batches = {}
    if chunks:
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(chunks)),
            thread_name_prefix="tencent-quotes",
        ) as executor:
            futures = {
                executor.submit(
                    fetch_tencent_quotes,
                    chunk,
                    radar_run_id=radar_run_id,
                    batch_id=f"{batch_id}-{index}",
                    as_of=as_of,
                    batch_size=len(chunk),
                    timeout_seconds=timeout_seconds,
                ): (index, chunk)
                for index, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                index, chunk = futures[future]
                try:
                    child_batches[index] = future.result()
                except Exception as exc:
                    issues.append(SourceIssue(
                        code="parallel_batch_failed",
                        source="tencent_finance",
                        batchIndex=index,
                        message=(
                            "腾讯并行行情子批次失败："
                            f"{type(exc).__name__}"
                        ),
                        symbols=chunk,
                    ))

    items_by_symbol = {}
    fetched_at = clock()
    for index in range(len(chunks)):
        child_batch = child_batches.get(index)
        if child_batch is None:
            continue
        fetched_at = max(fetched_at, child_batch.meta.fetched_at)
        for issue in child_batch.meta.issues:
            child_batch_index = issue.batch_index
            absolute_batch_index = (
                index
                if child_batch_index is None
                else index + child_batch_index
            )
            issues.append(issue.model_copy(
                update={"batch_index": absolute_batch_index},
            ))
        for item in child_batch.items:
            if item.symbol in items_by_symbol:
                issues.append(SourceIssue(
                    code="duplicate_quote",
                    source="tencent_finance",
                    message=f"腾讯行情重复返回{item.symbol}",
                    symbols=[item.symbol],
                ))
                continue
            items_by_symbol[item.symbol] = item

    items = [
        items_by_symbol[symbol]
        for symbol in valid_symbols
        if symbol in items_by_symbol
    ]
    missing_symbols = [
        symbol for symbol in valid_symbols if symbol not in items_by_symbol
    ]
    if missing_symbols:
        issues.append(SourceIssue(
            code="missing_symbols",
            source="tencent_finance",
            message=f"腾讯行情未返回{len(missing_symbols)}只证券",
            symbols=missing_symbols,
        ))

    source_times = [item.source_time for item in items if item.source_time]
    expected_count = len(valid_symbols)
    row_coverage = len(items) / expected_count if expected_count else 0.0
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="tencent_finance",
        asOf=as_of,
        sourceTime=max(source_times) if source_times else None,
        fetchedAt=fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage=_field_coverage(items),
        issues=issues,
    )
    return SourceBatch[QuoteSnapshot](meta=meta, items=items)
