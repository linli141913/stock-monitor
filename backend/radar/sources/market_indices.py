from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from radar.contracts import (
    IndexQuoteSnapshot,
    MarketIndexKey,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q={query}"


@dataclass(frozen=True)
class MarketIndexIdentity:
    index_key: MarketIndexKey
    symbol: str
    name: str
    exchange: str
    source_symbol: str


MARKET_INDEX_IDENTITIES: Tuple[MarketIndexIdentity, ...] = (
    MarketIndexIdentity(
        index_key=MarketIndexKey.SSE_COMPOSITE,
        symbol="000001",
        name="上证指数",
        exchange="sse",
        source_symbol="sh000001",
    ),
    MarketIndexIdentity(
        index_key=MarketIndexKey.SZSE_COMPONENT,
        symbol="399001",
        name="深证成指",
        exchange="szse",
        source_symbol="sz399001",
    ),
    MarketIndexIdentity(
        index_key=MarketIndexKey.CHINEXT,
        symbol="399006",
        name="创业板指",
        exchange="szse",
        source_symbol="sz399006",
    ),
    MarketIndexIdentity(
        index_key=MarketIndexKey.STAR50,
        symbol="000688",
        name="科创50",
        exchange="sse",
        source_symbol="sh000688",
    ),
)


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
    field_names = ("price", "change_percent", "source_time")
    if not items:
        return {field_name: 0.0 for field_name in field_names}
    return {
        field_name: sum(
            getattr(item, field_name) is not None
            for item in items
        ) / len(items)
        for field_name in field_names
    }


def fetch_market_indices(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    timeout_seconds: float = 5.0,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[IndexQuoteSnapshot]:
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds必须在0到30秒之间")

    identities_by_source_symbol = {
        identity.source_symbol: identity
        for identity in MARKET_INDEX_IDENTITIES
    }
    query = ",".join(identities_by_source_symbol)
    active_session = session or requests.Session()
    active_session.trust_env = False
    fetched_at = clock()
    issues = []
    items_by_source_symbol = {}

    try:
        response = active_session.get(
            TENCENT_QUOTE_URL.format(query=query),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout_seconds,
        )
        response.encoding = "gbk"
        response.raise_for_status()
        fetched_at = clock()
    except Exception as exc:
        issues.append(SourceIssue(
            code="source_request_failed",
            source="tencent_finance_indices",
            message=f"腾讯市场指数请求失败：{type(exc).__name__}",
        ))
        response = None

    if response is not None:
        for line in response.text.split(";"):
            if "=" not in line:
                continue
            assignment, payload = line.split("=", 1)
            source_symbol = assignment.strip()
            if source_symbol.startswith("v_"):
                source_symbol = source_symbol[2:]
            identity = identities_by_source_symbol.get(source_symbol)
            if identity is None:
                issues.append(SourceIssue(
                    code="unexpected_index",
                    source="tencent_finance_indices",
                    message=f"腾讯指数返回未请求的来源代码{source_symbol}",
                ))
                continue
            if source_symbol in items_by_source_symbol:
                issues.append(SourceIssue(
                    code="duplicate_index",
                    source="tencent_finance_indices",
                    message=f"腾讯指数重复返回{source_symbol}",
                    symbols=[identity.symbol],
                ))
                continue

            fields = payload.strip().strip('"').split("~")
            returned_symbol = str(fields[2] if len(fields) > 2 else "").strip()
            if returned_symbol != identity.symbol:
                issues.append(SourceIssue(
                    code="index_identity_mismatch",
                    source="tencent_finance_indices",
                    message=f"腾讯指数{source_symbol}返回代码身份不一致",
                    symbols=[returned_symbol] if returned_symbol else [],
                ))
                continue

            source_name = str(fields[1] if len(fields) > 1 else "").strip()
            items_by_source_symbol[source_symbol] = IndexQuoteSnapshot(
                indexKey=identity.index_key,
                symbol=identity.symbol,
                name=source_name or identity.name,
                exchange=identity.exchange,
                sourceSymbol=identity.source_symbol,
                sourceTime=_source_time(fields[30] if len(fields) > 30 else None),
                fetchedAt=fetched_at,
                price=_optional_float(fields[3] if len(fields) > 3 else None),
                changePercent=_optional_float(
                    fields[32] if len(fields) > 32 else None
                ),
            )

    items = [
        items_by_source_symbol[identity.source_symbol]
        for identity in MARKET_INDEX_IDENTITIES
        if identity.source_symbol in items_by_source_symbol
    ]
    missing = [
        identity
        for identity in MARKET_INDEX_IDENTITIES
        if identity.source_symbol not in items_by_source_symbol
    ]
    if missing:
        issues.append(SourceIssue(
            code="missing_indices",
            source="tencent_finance_indices",
            message=f"腾讯指数缺少{len(missing)}个必需指数",
            symbols=[identity.symbol for identity in missing],
        ))

    expected_count = len(MARKET_INDEX_IDENTITIES)
    source_times = [item.source_time for item in items if item.source_time]
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="tencent_finance_indices",
        asOf=as_of,
        sourceTime=max(source_times) if source_times else None,
        fetchedAt=fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=len(items) / expected_count,
        requiredFieldCoverage=_field_coverage(items),
        issues=issues,
    )
    return SourceBatch[IndexQuoteSnapshot](meta=meta, items=items)
