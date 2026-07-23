from datetime import datetime
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

import asset_context
from radar.contracts import (
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q={query}"


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
    ):
        result[field_name] = sum(
            getattr(item, field_name) is not None
            for item in items
        ) / len(items)
    return result


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
        try:
            response = active_session.get(
                TENCENT_QUOTE_URL.format(query=query),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=timeout_seconds,
            )
            response.encoding = "gbk"
            response.raise_for_status()
            last_fetched_at = clock()
        except Exception as exc:
            issues.append(SourceIssue(
                code="batch_request_failed",
                source="tencent_finance",
                batchIndex=start // batch_size,
                message=f"腾讯批量行情请求失败：{type(exc).__name__}",
                symbols=batch_symbols,
            ))
            continue

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
            items_by_symbol[symbol] = QuoteSnapshot(
                symbol=symbol,
                name=fields[1].strip() if len(fields) > 1 else "",
                sourceTime=_source_time(fields[30] if len(fields) > 30 else None),
                fetchedAt=last_fetched_at,
                price=_optional_float(fields[3] if len(fields) > 3 else None),
                changePercent=_optional_float(
                    fields[32] if len(fields) > 32 else None
                ),
                turnoverAmountSource=_optional_float(
                    fields[37] if len(fields) > 37 else None
                ),
                turnoverRatePercent=_optional_float(
                    fields[38] if len(fields) > 38 else None
                ),
                marketCapSource=_optional_float(
                    fields[45] if len(fields) > 45 else None
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
