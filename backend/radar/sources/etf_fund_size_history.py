"""上交所ETF逐交易日基金规模的只读验证。

上交所ETF官网历史规模接口明确以亿元展示基金规模，并返回精确交易日期。
本模块只把调用方已经取得的官方JSON转换为人民币元口径；日期缺失、重复、
代码错配、来源报错或非有限数值都会失败关闭，不使用最新规模倒填历史日期。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests


SSE_ETF_FUND_SIZE_CONTRACT_ID = "sse-etf-fund-size-history-cny-v1"
SSE_FUND_SIZE_YI_CNY_SCALE = 100_000_000.0
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_FUND_SIZE_HISTORY_SQL_ID = (
    "COMMON_JJZWZ_JJLB_JJXQ_JJGM_CKLSGM_L"
)
REQUEST_TIMEOUT_SECONDS = 20.0
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class VerifiedEtfFundSizeSnapshot:
    symbol: str
    trade_date: date
    fund_size_cny: Optional[float]
    source_contract_id: str
    source_content_sha256: str
    fetched_at: datetime
    formal_usable: bool
    reasons: Tuple[str, ...]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _trade_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _fund_size_cny(value: Any) -> Optional[float]:
    try:
        parsed = float(
            Decimal(str(value)) * Decimal(str(SSE_FUND_SIZE_YI_CNY_SCALE))
        )
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _fetch_sse_fund_size_history(symbol: str) -> Any:
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            SSE_QUERY_URL,
            params={
                "isPagination": "true",
                "sqlId": SSE_FUND_SIZE_HISTORY_SQL_ID,
                "pageHelp.pageSize": "30",
                "pageHelp.pageNo": "1",
                "pageHelp.beginPage": "1",
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": "1",
                "FUND_CODE": symbol,
            },
            headers={
                "Referer": (
                    "https://etf.sse.com.cn/fundlist/funddetail/"
                    f"index.shtml?code={symbol}"
                ),
                "User-Agent": "Mozilla/5.0",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    finally:
        session.close()


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def fetch_verified_sse_etf_fund_size_snapshot(
    *,
    symbol: str,
    expected_trade_date: date,
    provider: Callable[[str], Any] = _fetch_sse_fund_size_history,
    clock: Callable[[], datetime] = _now,
) -> VerifiedEtfFundSizeSnapshot:
    """Fetch one bounded official history page and verify an exact date."""
    if re.fullmatch(r"5\d{5}", symbol or "") is None:
        raise ValueError("sse_etf_symbol_invalid")
    try:
        payload = provider(symbol)
    except Exception as exc:
        payload = {
            "actionErrors": [f"source_request_failed:{type(exc).__name__}"],
            "fieldErrors": {},
            "result": [],
        }
    return build_verified_sse_etf_fund_size_snapshot(
        symbol=symbol,
        expected_trade_date=expected_trade_date,
        payload=payload,
        fetched_at=clock(),
    )


def build_verified_sse_etf_fund_size_snapshot(
    *,
    symbol: str,
    expected_trade_date: date,
    payload: Any,
    fetched_at: datetime,
) -> VerifiedEtfFundSizeSnapshot:
    if re.fullmatch(r"5\d{5}", symbol or "") is None:
        raise ValueError("sse_etf_symbol_invalid")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fund_size_fetched_at_timezone_required")
    if not isinstance(expected_trade_date, date):
        raise ValueError("fund_size_trade_date_invalid")
    if expected_trade_date > fetched_at.date():
        raise ValueError("fund_size_trade_date_future")

    reasons = []
    rows: Sequence[Any] = ()
    if not isinstance(payload, Mapping):
        reasons.append("fund_size_source_failed")
    else:
        action_errors = payload.get("actionErrors")
        field_errors = payload.get("fieldErrors")
        raw_rows = payload.get("result")
        if action_errors or field_errors:
            reasons.append("fund_size_source_failed")
        if isinstance(raw_rows, Sequence) and not isinstance(
            raw_rows,
            (str, bytes),
        ):
            rows = raw_rows
        else:
            reasons.append("fund_size_source_failed")

    matching_rows = []
    for row in rows:
        if not isinstance(row, Mapping):
            reasons.append("fund_size_row_invalid")
            continue
        returned_symbol = str(row.get("FUND_CODE") or "").strip().zfill(6)
        if returned_symbol != symbol:
            reasons.append("fund_size_symbol_mismatch")
            continue
        if _trade_date(row.get("TRADE_DATE")) == expected_trade_date:
            matching_rows.append(row)

    if not matching_rows:
        reasons.append("fund_size_trade_date_missing")
    elif len(matching_rows) > 1:
        reasons.append("fund_size_trade_date_duplicate")

    fund_size_cny = None
    if len(matching_rows) == 1:
        fund_size_cny = _fund_size_cny(matching_rows[0].get("SCALE"))
        if fund_size_cny is None:
            reasons.append("fund_size_value_invalid")

    reasons_tuple = tuple(dict.fromkeys(reasons))
    formal_usable = not reasons_tuple
    return VerifiedEtfFundSizeSnapshot(
        symbol=symbol,
        trade_date=expected_trade_date,
        fund_size_cny=fund_size_cny if formal_usable else None,
        source_contract_id=SSE_ETF_FUND_SIZE_CONTRACT_ID,
        source_content_sha256=_digest(payload),
        fetched_at=fetched_at,
        formal_usable=formal_usable,
        reasons=reasons_tuple,
    )
