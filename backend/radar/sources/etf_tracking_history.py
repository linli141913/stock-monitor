"""Official ETF NAV and price-index tracking metrics.

The first production adapter is intentionally narrow: the fund series comes
from the fund manager's official public site and the index series comes from
the index provider's official public site.  IOPV and exchange prices are never
substituted for fund NAV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
import statistics
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests


HUATAI_PB_NAV_HISTORY_URL = (
    "https://www.huatai-pb.com/common-web/chart/"
    "fundnettable/getFundNetTableJson"
)
CHINAAMC_FUND_ROOT_URL = "https://fund.chinaamc.com/fund"
CSINDEX_PRICE_HISTORY_URL = (
    "https://www.csindex.com.cn/csindex-home/perf/index-perf"
)
HUATAI_PB_NAV_SOURCE_CONTRACT_ID = "huatai-pb-official-fund-nav-history-v1"
CHINAAMC_NAV_SOURCE_CONTRACT_ID = "chinaamc-official-fund-nav-history-v1"
CSINDEX_PRICE_SOURCE_CONTRACT_ID = "csindex-official-price-index-history-v1"
ETF_TRACKING_FORMULA_VERSION = "radar-etf-tracking-60-return-v1"
REQUIRED_RETURN_COUNT = 60
REQUIRED_POINT_COUNT = REQUIRED_RETURN_COUNT + 1
REQUEST_TIMEOUT_SECONDS = 20.0
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class EtfNavHistoryPoint:
    trade_date: date
    unit_nav: float
    accumulated_nav: float


@dataclass(frozen=True)
class OfficialEtfNavHistory:
    symbol: str
    expected_trade_dates: Tuple[date, ...]
    points: Tuple[EtfNavHistoryPoint, ...]
    source_contract_id: str
    source_url: str
    content_sha256: str
    fetched_at: datetime
    formal_usable: bool
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class IndexPriceHistoryPoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class OfficialIndexPriceHistory:
    index_code: str
    index_name: str
    index_return_kind: str
    expected_trade_dates: Tuple[date, ...]
    points: Tuple[IndexPriceHistoryPoint, ...]
    source_contract_id: str
    source_url: str
    content_sha256: str
    fetched_at: datetime
    formal_usable: bool
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class VerifiedEtfTrackingWindow:
    symbol: str
    index_code: str
    expected_trade_dates: Tuple[date, ...]
    tracking_difference: Optional[float]
    tracking_error: Optional[float]
    index_correlation: Optional[float]
    latest_unit_nav: Optional[float]
    window_trading_days: int
    sample_count: int
    formula_version: str
    nav_source_contract_id: str
    index_source_contract_id: str
    computed_at: datetime
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


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _get_json(session, url: str, **kwargs) -> Any:
    for attempt in range(2):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                raise
    raise RuntimeError("official_tracking_request_unreachable")


def _get_text(session, url: str, **kwargs) -> str:
    for attempt in range(2):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
            return str(response.text)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                raise
    raise RuntimeError("official_tracking_request_unreachable")


def _expected_dates(
    values: Sequence[date],
    *,
    fetched_at: datetime,
    future_reason: str,
) -> Tuple[date, ...]:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("tracking_fetched_at_timezone_required")
    expected = tuple(values)
    if (
        not expected
        or any(not isinstance(item, date) for item in expected)
        or expected != tuple(sorted(set(expected)))
    ):
        raise ValueError("tracking_trade_dates_invalid")
    if any(item > fetched_at.date() for item in expected):
        raise ValueError(future_reason)
    return expected


def _positive_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _date(value: Any, pattern: Optional[str] = None) -> Optional[date]:
    text = str(value or "").strip()
    try:
        if pattern:
            return datetime.strptime(text, pattern).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_huatai_pb_nav_history(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    payload: Any,
    fetched_at: datetime,
) -> OfficialEtfNavHistory:
    if re.fullmatch(r"\d{6}", symbol or "") is None:
        raise ValueError("fund_nav_symbol_invalid")
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=fetched_at,
        future_reason="tracking_trade_date_future",
    )


    reasons = []
    rows = payload.get("dataList") if isinstance(payload, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        rows = []
        reasons.append("fund_nav_source_failed")

    values = {}
    seen = set()
    expected_set = set(expected)
    for row in rows:
        if not isinstance(row, Mapping):
            reasons.append("fund_nav_source_failed")
            continue
        trade_date = _date(row.get("date"))
        if trade_date is None:
            reasons.append("fund_nav_invalid_date")
            continue
        if trade_date in seen:
            reasons.append("fund_nav_duplicate_date")
            continue
        seen.add(trade_date)
        if trade_date not in expected_set:
            if expected[0] <= trade_date <= expected[-1]:
                reasons.append("fund_nav_off_calendar_date")
            continue
        if str(row.get("fundcode") or "").strip() != symbol:
            reasons.append("fund_nav_identity_conflict")
            continue
        unit_nav = _positive_float(row.get("netvalue"))
        accumulated_nav = _positive_float(row.get("totalnetvalue"))
        if unit_nav is None or accumulated_nav is None:
            reasons.append("fund_nav_invalid")
            continue
        values[trade_date] = EtfNavHistoryPoint(
            trade_date=trade_date,
            unit_nav=unit_nav,
            accumulated_nav=accumulated_nav,
        )
    if set(values) != expected_set:
        reasons.append("fund_nav_window_incomplete")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return OfficialEtfNavHistory(
        symbol=symbol,
        expected_trade_dates=expected,
        points=tuple(values[item] for item in expected if item in values),
        source_contract_id=HUATAI_PB_NAV_SOURCE_CONTRACT_ID,
        source_url=HUATAI_PB_NAV_HISTORY_URL,
        content_sha256=_digest(payload),
        fetched_at=fetched_at,
        formal_usable=not unique_reasons,
        reasons=unique_reasons,
    )


def fetch_huatai_pb_nav_history(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialEtfNavHistory:
    fetched_at = clock()
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=fetched_at,
        future_reason="tracking_trade_date_future",
    )
    active_session = session or _session()
    try:
        payload = _get_json(
            active_session,
            HUATAI_PB_NAV_HISTORY_URL,
            params={
                "fundcode": symbol,
                "from": expected[0].isoformat(),
                "to": expected[-1].isoformat(),
                "pages": "1-500",
                "siteId": "",
            },
            headers={
                "Referer": (
                    "https://www.huatai-pb.com/products/zhishu/"
                    f"{symbol}/index.html"
                ),
                "User-Agent": "Mozilla/5.0",
            },
        )
    except Exception:
        payload = {"sourceFailure": True}
    return build_huatai_pb_nav_history(
        symbol=symbol,
        expected_trade_dates=expected,
        payload=payload,
        fetched_at=fetched_at,
    )


def build_chinaamc_nav_history(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    payload: Any,
    product_page: str,
    fetched_at: datetime,
) -> OfficialEtfNavHistory:
    """Parse the manager's own symbol-scoped history JSON.

    The JSON arrays do not carry a fund code, so the official product page is
    required as an independent response identity check.  A requested URL by
    itself is not accepted as proof that the response belongs to the fund.
    """
    if re.fullmatch(r"\d{6}", symbol or "") is None:
        raise ValueError("fund_nav_symbol_invalid")
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=fetched_at,
        future_reason="tracking_trade_date_future",
    )
    source_url = f"{CHINAAMC_FUND_ROOT_URL}/{symbol}/zoust_all.js"
    reasons = []
    identity_match = re.search(
        r'id=["\']codetext["\'][^>]*>([^<]*)</div>',
        product_page if isinstance(product_page, str) else "",
        flags=re.IGNORECASE,
    )
    if (
        identity_match is None
        or re.search(
            rf"(?<!\d){re.escape(symbol)}(?!\d)",
            identity_match.group(1),
        ) is None
    ):
        reasons.append("fund_nav_identity_conflict")

    if not isinstance(payload, Mapping):
        dates = units = accumulated = []
        reasons.append("fund_nav_source_failed")
    else:
        dates = payload.get("ShowData")
        units = payload.get("danweijingzhiName")
        accumulated = payload.get("leijiJingzhiName")
        if any(
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            for value in (dates, units, accumulated)
        ):
            dates = units = accumulated = []
            reasons.append("fund_nav_source_failed")
        elif len(dates) != len(units) or len(dates) != len(accumulated):
            dates = units = accumulated = []
            reasons.append("fund_nav_source_failed")

    values = {}
    seen = set()
    expected_set = set(expected)
    for raw_date, raw_unit, raw_accumulated in zip(dates, units, accumulated):
        trade_date = _date(raw_date)
        if trade_date is None:
            reasons.append("fund_nav_invalid_date")
            continue
        if trade_date in seen:
            reasons.append("fund_nav_duplicate_date")
            continue
        seen.add(trade_date)
        if trade_date not in expected_set:
            if expected[0] <= trade_date <= expected[-1]:
                reasons.append("fund_nav_off_calendar_date")
            continue
        unit_nav = _positive_float(raw_unit)
        accumulated_nav = _positive_float(raw_accumulated)
        if unit_nav is None or accumulated_nav is None:
            reasons.append("fund_nav_invalid")
            continue
        values[trade_date] = EtfNavHistoryPoint(
            trade_date=trade_date,
            unit_nav=unit_nav,
            accumulated_nav=accumulated_nav,
        )
    if set(values) != expected_set:
        reasons.append("fund_nav_window_incomplete")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return OfficialEtfNavHistory(
        symbol=symbol,
        expected_trade_dates=expected,
        points=tuple(values[item] for item in expected if item in values),
        source_contract_id=CHINAAMC_NAV_SOURCE_CONTRACT_ID,
        source_url=source_url,
        content_sha256=_digest({
            "productPageSha256": hashlib.sha256(
                product_page.encode("utf-8")
                if isinstance(product_page, str)
                else b""
            ).hexdigest(),
            "history": payload,
        }),
        fetched_at=fetched_at,
        formal_usable=not unique_reasons,
        reasons=unique_reasons,
    )


def fetch_chinaamc_nav_history(
    *,
    symbol: str,
    expected_trade_dates: Sequence[date],
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialEtfNavHistory:
    started_at = clock()
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=started_at,
        future_reason="tracking_trade_date_future",
    )
    active_session = session or _session()
    page_url = f"{CHINAAMC_FUND_ROOT_URL}/{symbol}/index.shtml"
    history_url = f"{CHINAAMC_FUND_ROOT_URL}/{symbol}/zoust_all.js"
    try:
        product_page = _get_text(
            active_session,
            page_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        payload = _get_json(
            active_session,
            history_url,
            headers={
                "Referer": page_url,
                "User-Agent": "Mozilla/5.0",
            },
        )
    except Exception:
        product_page = ""
        payload = {"sourceFailure": True}
    completed_at = clock()
    return build_chinaamc_nav_history(
        symbol=symbol,
        expected_trade_dates=expected,
        payload=payload,
        product_page=product_page,
        fetched_at=completed_at,
    )


def fetch_supported_manager_nav_history(
    *,
    manager: str,
    symbol: str,
    expected_trade_dates: Sequence[date],
    **kwargs: Any,
) -> OfficialEtfNavHistory:
    normalized = re.sub(r"\s+", "", str(manager or ""))
    if "华泰柏瑞" in normalized:
        return fetch_huatai_pb_nav_history(
            symbol=symbol,
            expected_trade_dates=expected_trade_dates,
            **kwargs,
        )
    if "华夏基金" in normalized:
        return fetch_chinaamc_nav_history(
            symbol=symbol,
            expected_trade_dates=expected_trade_dates,
            **kwargs,
        )
    raise ValueError("fund_nav_provider_not_supported")


def build_csindex_price_history(
    *,
    index_code: str,
    index_name: str,
    expected_trade_dates: Sequence[date],
    payload: Any,
    fetched_at: datetime,
) -> OfficialIndexPriceHistory:
    if not index_code.strip() or not index_name.strip():
        raise ValueError("index_price_identity_required")
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=fetched_at,
        future_reason="tracking_trade_date_future",
    )
    reasons = []
    if not isinstance(payload, Mapping) or str(payload.get("code")) != "200":
        rows = []
        reasons.append("index_price_source_failed")
    else:
        rows = payload.get("data")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = []
            reasons.append("index_price_source_failed")

    normalized_name = re.sub(r"\s+", "", index_name)
    values = {}
    seen = set()
    expected_set = set(expected)
    for row in rows:
        if not isinstance(row, Mapping):
            reasons.append("index_price_source_failed")
            continue
        trade_date = _date(row.get("tradeDate"), "%Y%m%d")
        if trade_date is None:
            reasons.append("index_price_invalid_date")
            continue
        if trade_date in seen:
            reasons.append("index_price_duplicate_date")
            continue
        seen.add(trade_date)
        if trade_date not in expected_set:
            if expected[0] <= trade_date <= expected[-1]:
                reasons.append("index_price_off_calendar_date")
            continue
        returned_code = str(row.get("indexCode") or "").strip()
        returned_name = re.sub(
            r"\s+", "", str(row.get("indexNameCnAll") or "")
        )
        if returned_code != index_code or returned_name != normalized_name:
            reasons.append("index_price_identity_conflict")
            continue
        close = _positive_float(row.get("close"))
        if close is None:
            reasons.append("index_price_invalid")
            continue
        values[trade_date] = IndexPriceHistoryPoint(
            trade_date=trade_date,
            close=close,
        )
    if set(values) != expected_set:
        reasons.append("index_price_window_incomplete")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return OfficialIndexPriceHistory(
        index_code=index_code,
        index_name=index_name,
        index_return_kind="price_index",
        expected_trade_dates=expected,
        points=tuple(values[item] for item in expected if item in values),
        source_contract_id=CSINDEX_PRICE_SOURCE_CONTRACT_ID,
        source_url=CSINDEX_PRICE_HISTORY_URL,
        content_sha256=_digest(payload),
        fetched_at=fetched_at,
        formal_usable=not unique_reasons,
        reasons=unique_reasons,
    )


def fetch_csindex_price_history(
    *,
    index_code: str,
    index_name: str,
    expected_trade_dates: Sequence[date],
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialIndexPriceHistory:
    fetched_at = clock()
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=fetched_at,
        future_reason="tracking_trade_date_future",
    )
    active_session = session or _session()
    try:
        payload = _get_json(
            active_session,
            CSINDEX_PRICE_HISTORY_URL,
            params={
                "indexCode": index_code,
                "startDate": expected[0].strftime("%Y%m%d"),
                "endDate": expected[-1].strftime("%Y%m%d"),
            },
            headers={
                "Referer": "https://www.csindex.com.cn/",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except Exception:
        payload = {"sourceFailure": True}
    return build_csindex_price_history(
        index_code=index_code,
        index_name=index_name,
        expected_trade_dates=expected,
        payload=payload,
        fetched_at=fetched_at,
    )


def _correlation(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right)
    )
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_ss * right_ss)
    if denominator == 0:
        return None
    return numerator / denominator


def build_verified_etf_tracking_window(
    *,
    symbol: str,
    index_code: str,
    expected_trade_dates: Sequence[date],
    nav_history: OfficialEtfNavHistory,
    index_history: OfficialIndexPriceHistory,
    computed_at: datetime,
) -> VerifiedEtfTrackingWindow:
    expected = _expected_dates(
        expected_trade_dates,
        fetched_at=computed_at,
        future_reason="tracking_trade_date_future",
    )
    reasons = []
    if len(expected) != REQUIRED_POINT_COUNT:
        reasons.append("tracking_expected_window_not_60_returns")
    if nav_history.symbol != symbol:
        reasons.append("tracking_fund_identity_conflict")
    if index_history.index_code != index_code:
        reasons.append("tracking_index_identity_conflict")
    if nav_history.expected_trade_dates != expected:
        reasons.append("tracking_nav_date_scope_conflict")
    if index_history.expected_trade_dates != expected:
        reasons.append("tracking_index_date_scope_conflict")
    if not nav_history.formal_usable:
        reasons.append("tracking_nav_source_unverified")
    if not index_history.formal_usable:
        reasons.append("tracking_index_source_unverified")

    tracking_difference = None
    tracking_error = None
    index_correlation = None
    latest_unit_nav = None
    sample_count = 0
    if not reasons:
        nav_values = [item.accumulated_nav for item in nav_history.points]
        index_values = [item.close for item in index_history.points]
        if (
            len(nav_values) == REQUIRED_POINT_COUNT
            and len(index_values) == REQUIRED_POINT_COUNT
        ):
            nav_returns = [
                nav_values[i] / nav_values[i - 1] - 1
                for i in range(1, len(nav_values))
            ]
            index_returns = [
                index_values[i] / index_values[i - 1] - 1
                for i in range(1, len(index_values))
            ]
            active_returns = [
                fund_return - index_return
                for fund_return, index_return in zip(
                    nav_returns, index_returns
                )
            ]
            index_correlation = _correlation(nav_returns, index_returns)
            if index_correlation is None:
                reasons.append("tracking_correlation_undefined")
            else:
                tracking_difference = (
                    nav_values[-1] / nav_values[0]
                    - index_values[-1] / index_values[0]
                )
                tracking_error = (
                    statistics.stdev(active_returns) * math.sqrt(250)
                )
                latest_unit_nav = nav_history.points[-1].unit_nav
                sample_count = len(active_returns)
        else:
            reasons.append("tracking_aligned_window_incomplete")

    unique_reasons = tuple(dict.fromkeys(reasons))
    formal_usable = not unique_reasons
    if not formal_usable:
        tracking_difference = None
        tracking_error = None
        index_correlation = None
        latest_unit_nav = None
        sample_count = 0
    return VerifiedEtfTrackingWindow(
        symbol=symbol,
        index_code=index_code,
        expected_trade_dates=expected,
        tracking_difference=tracking_difference,
        tracking_error=tracking_error,
        index_correlation=index_correlation,
        latest_unit_nav=latest_unit_nav,
        window_trading_days=REQUIRED_RETURN_COUNT,
        sample_count=sample_count,
        formula_version=ETF_TRACKING_FORMULA_VERSION,
        nav_source_contract_id=nav_history.source_contract_id,
        index_source_contract_id=index_history.source_contract_id,
        computed_at=computed_at,
        formal_usable=formal_usable,
        reasons=unique_reasons,
    )
