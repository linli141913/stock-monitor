"""ETF二十日成交额双源只读校验。

腾讯日K的成交额字段为万元，东方财富日K的成交额字段为人民币元。本模块
只在两源逐日交叉一致、日期全集完整时输出人民币元口径的20日平均成交额；
任一来源缺日、负值、非有限值或单位换算不一致都失败关闭。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests


ETF_TURNOVER_20D_CONTRACT_ID = (
    "tencent-eastmoney-etf-turnover-20d-crosscheck-v1"
)
SSE_ETF_TURNOVER_20D_CONTRACT_ID = (
    "sse-tencent-etf-turnover-20d-crosscheck-v1"
)
TENCENT_AMOUNT_SCALE_TO_CNY = 10_000.0
SSE_AMOUNT_SCALE_TO_CNY = 10_000.0
TURNOVER_ABSOLUTE_TOLERANCE_CNY = 10_000.0
TURNOVER_RELATIVE_TOLERANCE = 0.00001
# 上交所成交概况是主值；腾讯仅作数量级/传输异常交叉检查。两者因汇总
# 口径存在小幅差异，超过1%才视为实质冲突，同时把实际最大偏差写入证据。
SSE_TENCENT_RELATIVE_TOLERANCE = 0.01
REQUIRED_TRADING_DAYS = 20
TENCENT_HISTORY_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get"
)
EASTMONEY_HISTORY_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_DAILY_TURNOVER_SQL_ID = (
    "COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C"
)
REQUEST_TIMEOUT_SECONDS = 20.0
SOURCE_REQUEST_ATTEMPTS = 2
SSE_REQUEST_WORKERS = 5
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class EtfTurnoverHistoryPoint:
    trade_date: date
    turnover_amount_cny: float


@dataclass(frozen=True)
class VerifiedEtfTurnoverWindow:
    symbol: str
    expected_trade_dates: Tuple[date, ...]
    points: Tuple[EtfTurnoverHistoryPoint, ...]
    average_turnover_20d: Optional[float]
    sample_count: int
    source_contract_id: str
    tencent_content_sha256: str
    eastmoney_content_sha256: Optional[str]
    fetched_at: datetime
    formal_usable: bool
    reasons: Tuple[str, ...]
    sse_content_sha256: Optional[str] = None
    maximum_cross_source_relative_difference: Optional[float] = None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _amount(value: Any, *, scale: float = 1.0) -> Optional[float]:
    try:
        parsed = float(value) * scale
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _tencent_rows(
    payload: Any,
    *,
    query_symbol: str,
) -> dict[date, float]:
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        return {}
    data = payload.get("data")
    stock = data.get(query_symbol) if isinstance(data, Mapping) else None
    rows = stock.get("qfqday") if isinstance(stock, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}
    values = {}
    for row in rows:
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) < 9
        ):
            continue
        try:
            trade_date = date.fromisoformat(str(row[0])[:10])
        except ValueError:
            continue
        amount = _amount(row[8], scale=TENCENT_AMOUNT_SCALE_TO_CNY)
        if amount is not None and trade_date not in values:
            values[trade_date] = amount
    return values


def _eastmoney_rows(payload: Any, *, symbol: str) -> dict[date, float]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    if not isinstance(data, Mapping) or str(data.get("code") or "") != symbol:
        return {}
    rows = data.get("klines")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}
    values = {}
    for row in rows:
        fields = row.split(",") if isinstance(row, str) else ()
        if len(fields) < 7:
            continue
        try:
            trade_date = date.fromisoformat(fields[0][:10])
        except ValueError:
            continue
        amount = _amount(fields[6])
        if amount is not None and trade_date not in values:
            values[trade_date] = amount
    return values


def _amounts_match(left: float, right: float) -> bool:
    tolerance = max(
        TURNOVER_ABSOLUTE_TOLERANCE_CNY,
        max(left, right) * TURNOVER_RELATIVE_TOLERANCE,
    )
    return abs(left - right) <= tolerance


def _sse_tencent_amounts_match(left: float, right: float) -> bool:
    tolerance = max(
        TURNOVER_ABSOLUTE_TOLERANCE_CNY,
        max(left, right) * SSE_TENCENT_RELATIVE_TOLERANCE,
    )
    return abs(left - right) <= tolerance


def promote_verified_tencent_unadjusted_etf_history(
    *,
    query_symbol: str,
    adjusted_payload: Any,
    raw_payload: Any,
    current_date: date,
) -> Any:
    """Promote ``day`` only when completed adjusted/raw rows are identical."""
    if not isinstance(adjusted_payload, Mapping) or not isinstance(
        raw_payload,
        Mapping,
    ):
        return adjusted_payload
    adjusted_data = adjusted_payload.get("data")
    raw_data = raw_payload.get("data")
    adjusted_stock = (
        adjusted_data.get(query_symbol)
        if isinstance(adjusted_data, Mapping)
        else None
    )
    raw_stock = (
        raw_data.get(query_symbol)
        if isinstance(raw_data, Mapping)
        else None
    )
    if not isinstance(adjusted_stock, Mapping) or not isinstance(
        raw_stock,
        Mapping,
    ):
        return adjusted_payload
    if isinstance(adjusted_stock.get("qfqday"), list):
        return adjusted_payload

    def completed(rows: Any) -> Optional[list]:
        if not isinstance(rows, list):
            return None
        values = []
        for row in rows:
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
                return None
            try:
                trade_date = date.fromisoformat(str(row[0])[:10])
            except (IndexError, ValueError):
                return None
            if trade_date >= current_date:
                continue
            values.append(row)
        return values or None

    adjusted_completed = completed(adjusted_stock.get("day"))
    raw_completed = completed(raw_stock.get("day"))
    if adjusted_completed is None or adjusted_completed != raw_completed:
        return adjusted_payload
    verified_stock = dict(adjusted_stock)
    verified_stock["qfqday"] = list(adjusted_stock["day"])
    verified_stock["qfqDayEquivalenceContractId"] = (
        "tencent-etf-qfq-day-completed-exact-equivalence-v1"
    )
    verified_data = dict(adjusted_data)
    verified_data[query_symbol] = verified_stock
    verified_payload = dict(adjusted_payload)
    verified_payload["data"] = verified_data
    return verified_payload


def _sse_rows(
    payloads_by_date: Mapping[date, Any],
    *,
    symbol: str,
) -> tuple[dict[date, float], bool]:
    values = {}
    source_failed = False
    for requested_date, payload in payloads_by_date.items():
        if not isinstance(requested_date, date) or not isinstance(
            payload,
            Mapping,
        ):
            source_failed = True
            continue
        if payload.get("sourceFailure"):
            source_failed = True
        if payload.get("actionErrors") or payload.get("fieldErrors"):
            source_failed = True
        rows = payload.get("result")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            source_failed = True
            continue
        matching = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            returned_symbol = str(row.get("SEC_CODE") or "").strip().zfill(6)
            returned_date_text = str(row.get("TX_DATE") or "").strip()
            try:
                returned_date = datetime.strptime(
                    returned_date_text,
                    "%Y%m%d",
                ).date()
            except ValueError:
                continue
            if returned_symbol == symbol and returned_date == requested_date:
                amount = _amount(
                    row.get("TRADE_AMT"),
                    scale=SSE_AMOUNT_SCALE_TO_CNY,
                )
                if amount is not None:
                    matching.append(amount)
        if len(matching) == 1:
            values[requested_date] = matching[0]
        else:
            source_failed = True
    return values, source_failed


def build_verified_sse_etf_turnover_window(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    sse_payloads_by_date: Mapping[date, Any],
    tencent_payload: Any,
    fetched_at: datetime,
) -> VerifiedEtfTurnoverWindow:
    """Use exchange turnover as primary and Tencent as bounded corroboration."""
    if re.fullmatch(r"5\d{5}", symbol or "") is None:
        raise ValueError("sse_etf_symbol_invalid")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("turnover_fetched_at_timezone_required")
    expected = tuple(expected_trade_dates)
    if (
        not expected
        or any(not isinstance(item, date) for item in expected)
        or expected != tuple(sorted(set(expected)))
    ):
        raise ValueError("turnover_trade_dates_invalid")
    if any(item > fetched_at.date() for item in expected):
        raise ValueError("turnover_trade_date_future")

    query_symbol = "sh" + symbol
    tencent = _tencent_rows(tencent_payload, query_symbol=query_symbol)
    sse, sse_failed = _sse_rows(sse_payloads_by_date, symbol=symbol)
    expected_set = set(expected)
    points = []
    mismatch = False
    cross_source_differences = []
    for trade_date in expected:
        official = sse.get(trade_date)
        corroborating = tencent.get(trade_date)
        if official is None or corroborating is None:
            continue
        denominator = max(official, corroborating)
        cross_source_differences.append(
            0.0
            if denominator == 0
            else abs(official - corroborating) / denominator
        )
        if not _sse_tencent_amounts_match(official, corroborating):
            mismatch = True
            continue
        points.append(EtfTurnoverHistoryPoint(
            trade_date=trade_date,
            turnover_amount_cny=official,
        ))

    reasons = []
    if len(expected) != REQUIRED_TRADING_DAYS:
        reasons.append("turnover_expected_window_not_20d")
    if sse_failed:
        reasons.append("sse_turnover_source_failed")
    if set(sse).intersection(expected_set) != expected_set:
        reasons.append("sse_turnover_window_incomplete")
    if set(tencent).intersection(expected_set) != expected_set:
        reasons.append("tencent_turnover_window_incomplete")
    if len(points) != len(expected):
        reasons.append("turnover_window_incomplete")
    if mismatch:
        reasons.append("turnover_cross_source_mismatch")
    reasons_tuple = tuple(dict.fromkeys(reasons))
    formal_usable = not reasons_tuple
    average = (
        sum(item.turnover_amount_cny for item in points) / len(points)
        if formal_usable and points
        else None
    )
    canonical_sse_payloads = {
        item.isoformat(): sse_payloads_by_date.get(item)
        for item in expected
    }
    return VerifiedEtfTurnoverWindow(
        symbol=symbol,
        expected_trade_dates=expected,
        points=tuple(points),
        average_turnover_20d=average,
        sample_count=len(points),
        source_contract_id=SSE_ETF_TURNOVER_20D_CONTRACT_ID,
        tencent_content_sha256=_digest(tencent_payload),
        eastmoney_content_sha256=None,
        fetched_at=fetched_at,
        formal_usable=formal_usable,
        reasons=reasons_tuple,
        sse_content_sha256=_digest(canonical_sse_payloads),
        maximum_cross_source_relative_difference=(
            max(cross_source_differences)
            if cross_source_differences
            else None
        ),
    )


def _fetch_tencent_turnover(
    symbol: str,
    _expected_trade_dates: Sequence[date],
) -> Any:
    query_symbol = ("sh" if symbol.startswith("5") else "sz") + symbol
    last_error = None
    for _attempt in range(SOURCE_REQUEST_ATTEMPTS):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    TENCENT_HISTORY_URL,
                    params={"param": f"{query_symbol},day,,,40,qfq"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                adjusted_payload = response.json()
                data = (
                    adjusted_payload.get("data")
                    if isinstance(adjusted_payload, Mapping)
                    else None
                )
                stock = (
                    data.get(query_symbol)
                    if isinstance(data, Mapping)
                    else None
                )
                if isinstance(stock, Mapping) and isinstance(
                    stock.get("qfqday"),
                    list,
                ):
                    return adjusted_payload
                raw_response = session.get(
                    TENCENT_HISTORY_URL,
                    params={"param": f"{query_symbol},day,,,40,"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                raw_response.raise_for_status()
                return promote_verified_tencent_unadjusted_etf_history(
                    query_symbol=query_symbol,
                    adjusted_payload=adjusted_payload,
                    raw_payload=raw_response.json(),
                    current_date=datetime.now(SHANGHAI_TZ).date(),
                )
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or RuntimeError("tencent_turnover_source_failed")


def _fetch_eastmoney_turnover(
    symbol: str,
    expected_trade_dates: Sequence[date],
) -> Any:
    market = 1 if symbol.startswith("5") else 0
    last_error = None
    for _attempt in range(SOURCE_REQUEST_ATTEMPTS):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    EASTMONEY_HISTORY_URL,
                    params={
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57",
                        "ut": "7eea3edcaed734bea9cbfc24409ed989",
                        "klt": "101",
                        "fqt": "1",
                        "secid": f"{market}.{symbol}",
                        "beg": expected_trade_dates[0].strftime("%Y%m%d"),
                        "end": expected_trade_dates[-1].strftime("%Y%m%d"),
                    },
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                return response.json()
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or RuntimeError("eastmoney_turnover_source_failed")


def _fetch_sse_turnover_day(symbol: str, trade_date: date) -> Any:
    last_error = None
    for _attempt in range(SOURCE_REQUEST_ATTEMPTS):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    SSE_QUERY_URL,
                    params={
                        "sqlId": SSE_DAILY_TURNOVER_SQL_ID,
                        "SEC_CODE": symbol,
                        "TX_DATE": trade_date.isoformat(),
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
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or RuntimeError("sse_turnover_source_failed")


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def fetch_verified_etf_turnover_window(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    tencent_provider: Callable[[str, Sequence[date]], Any] = (
        _fetch_tencent_turnover
    ),
    eastmoney_provider: Callable[[str, Sequence[date]], Any] = (
        _fetch_eastmoney_turnover
    ),
    clock: Callable[[], datetime] = _now,
) -> VerifiedEtfTurnoverWindow:
    """Fetch a bounded 20-day window and fail closed per source."""
    expected = tuple(expected_trade_dates)
    if re.fullmatch(r"[15]\d{5}", symbol or "") is None:
        raise ValueError("etf_symbol_invalid")
    if not expected:
        raise ValueError("turnover_trade_dates_invalid")
    try:
        tencent_payload = tencent_provider(symbol, expected)
    except Exception as exc:
        tencent_payload = {
            "code": -1,
            "data": {},
            "sourceFailure": type(exc).__name__,
        }
    try:
        eastmoney_payload = eastmoney_provider(symbol, expected)
    except Exception as exc:
        eastmoney_payload = {
            "data": {"code": symbol, "klines": []},
            "sourceFailure": type(exc).__name__,
        }
    return build_verified_etf_turnover_window(
        symbol=symbol,
        expected_trade_dates=expected,
        tencent_payload=tencent_payload,
        eastmoney_payload=eastmoney_payload,
        fetched_at=clock(),
    )


def fetch_verified_sse_etf_turnover_window(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    sse_provider: Callable[[str, date], Any] = _fetch_sse_turnover_day,
    tencent_provider: Callable[[str, Sequence[date]], Any] = (
        _fetch_tencent_turnover
    ),
    clock: Callable[[], datetime] = _now,
) -> VerifiedEtfTurnoverWindow:
    """Fetch 20 exact SSE dates concurrently and cross-check with Tencent."""
    expected = tuple(expected_trade_dates)
    if re.fullmatch(r"5\d{5}", symbol or "") is None:
        raise ValueError("sse_etf_symbol_invalid")
    if not expected:
        raise ValueError("turnover_trade_dates_invalid")
    try:
        tencent_payload = tencent_provider(symbol, expected)
    except Exception as exc:
        tencent_payload = {
            "code": -1,
            "data": {},
            "sourceFailure": type(exc).__name__,
        }

    payloads_by_date = {}

    def fetch_one(trade_date: date):
        try:
            return sse_provider(symbol, trade_date)
        except Exception as exc:
            return {
                "actionErrors": [
                    f"source_request_failed:{type(exc).__name__}"
                ],
                "fieldErrors": {},
                "result": [],
                "sourceFailure": type(exc).__name__,
            }

    with ThreadPoolExecutor(max_workers=SSE_REQUEST_WORKERS) as executor:
        futures = {
            executor.submit(fetch_one, trade_date): trade_date
            for trade_date in expected
        }
        for future in as_completed(futures):
            payloads_by_date[futures[future]] = future.result()
    return build_verified_sse_etf_turnover_window(
        symbol=symbol,
        expected_trade_dates=expected,
        sse_payloads_by_date=payloads_by_date,
        tencent_payload=tencent_payload,
        fetched_at=clock(),
    )


def build_verified_etf_turnover_window(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    tencent_payload: Any,
    eastmoney_payload: Any,
    fetched_at: datetime,
) -> VerifiedEtfTurnoverWindow:
    if re.fullmatch(r"[15]\d{5}", symbol or "") is None:
        raise ValueError("etf_symbol_invalid")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("turnover_fetched_at_timezone_required")
    expected = tuple(expected_trade_dates)
    if (
        not expected
        or any(not isinstance(item, date) for item in expected)
        or expected != tuple(sorted(set(expected)))
    ):
        raise ValueError("turnover_trade_dates_invalid")
    if any(item > fetched_at.date() for item in expected):
        raise ValueError("turnover_trade_date_future")

    query_symbol = ("sh" if symbol.startswith("5") else "sz") + symbol
    tencent = _tencent_rows(tencent_payload, query_symbol=query_symbol)
    eastmoney = _eastmoney_rows(eastmoney_payload, symbol=symbol)
    expected_set = set(expected)
    points = []
    mismatch = False
    for trade_date in expected:
        left = tencent.get(trade_date)
        right = eastmoney.get(trade_date)
        if left is None or right is None:
            continue
        if not _amounts_match(left, right):
            mismatch = True
            continue
        points.append(EtfTurnoverHistoryPoint(
            trade_date=trade_date,
            turnover_amount_cny=right,
        ))

    reasons = []
    if isinstance(tencent_payload, Mapping) and tencent_payload.get(
        "sourceFailure"
    ):
        reasons.append("tencent_turnover_source_failed")
    if isinstance(eastmoney_payload, Mapping) and eastmoney_payload.get(
        "sourceFailure"
    ):
        reasons.append("eastmoney_turnover_source_failed")
    if len(expected) != REQUIRED_TRADING_DAYS:
        reasons.append("turnover_expected_window_not_20d")
    if set(tencent).intersection(expected_set) != expected_set:
        reasons.append("tencent_turnover_window_incomplete")
    if set(eastmoney).intersection(expected_set) != expected_set:
        reasons.append("eastmoney_turnover_window_incomplete")
    if len(points) != len(expected):
        reasons.append("turnover_window_incomplete")
    if mismatch:
        reasons.append("turnover_cross_source_mismatch")
    reasons = list(dict.fromkeys(reasons))
    formal_usable = not reasons
    average = (
        sum(item.turnover_amount_cny for item in points) / len(points)
        if formal_usable and points
        else None
    )
    return VerifiedEtfTurnoverWindow(
        symbol=symbol,
        expected_trade_dates=expected,
        points=tuple(points),
        average_turnover_20d=average,
        sample_count=len(points),
        source_contract_id=ETF_TURNOVER_20D_CONTRACT_ID,
        tencent_content_sha256=_digest(tencent_payload),
        eastmoney_content_sha256=_digest(eastmoney_payload),
        fetched_at=fetched_at,
        formal_usable=formal_usable,
        reasons=tuple(reasons),
    )
