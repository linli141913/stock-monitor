"""A股与港股官方交易日历和交易时段判断。"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
import re
from typing import Callable, FrozenSet, Optional
from zoneinfo import ZoneInfo

import requests


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SSE_CALENDAR_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
HKEX_CALENDAR_URL = "https://www.hkex.com.hk/News/HKEX-Calendar?defaultdate={year}-07-01&sc_lang=en"
CACHE_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class CalendarSnapshot:
    market: str
    year: int
    closed_days: FrozenSet[str]
    half_days: FrozenSet[str]
    source_url: str
    checked_at: str


@dataclass(frozen=True)
class CalendarDay:
    kind: str
    source_url: Optional[str]
    checked_at: str
    error: Optional[str] = None


@dataclass(frozen=True)
class MarketStatus:
    code: str
    label: str


_snapshot_cache = {}


def _now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def _fetch_text(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=5,
    )
    response.raise_for_status()
    charset_match = re.search(
        br"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)",
        response.content[:2048],
        flags=re.IGNORECASE,
    )
    encoding = (
        charset_match.group(1).decode("ascii")
        if charset_match
        else response.apparent_encoding or "utf-8"
    )
    return response.content.decode(encoding, errors="replace")


def _expand_date_range(year: int, start_month: int, start_day: int, end_month: int, end_day: int):
    current = date(year, start_month, start_day)
    end = date(year, end_month, end_day)
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def parse_sse_calendar(page_text: str, year: int) -> CalendarSnapshot:
    section = re.search(
        rf"<strong>\s*{year}年休市安排\s*</strong>.*?</table>",
        page_text,
        flags=re.DOTALL,
    )
    if not section:
        raise ValueError(f"上交所页面未提供 {year} 年休市安排")

    closed_days = set()
    for row in re.findall(r"<tr\b.*?</tr>", section.group(0), flags=re.DOTALL):
        match = re.search(
            r"(\d{1,2})月(\d{1,2})日.*?至(\d{1,2})月(\d{1,2})日.*?休市",
            row,
            flags=re.DOTALL,
        )
        if match is None:
            continue
        closed_days.update(
            _expand_date_range(year, *(int(value) for value in match.groups()))
        )
    if not closed_days:
        raise ValueError("上交所休市安排解析结果为空")

    return CalendarSnapshot(
        market="cn",
        year=year,
        closed_days=frozenset(closed_days),
        half_days=frozenset(),
        source_url=SSE_CALENDAR_URL,
        checked_at=_now_iso(),
    )


def parse_hkex_calendar(page_text: str, year: int) -> CalendarSnapshot:
    payload_match = re.search(
        r"calendarDataSource\s*=\s*'(.*?)';",
        page_text,
        flags=re.DOTALL,
    )
    if not payload_match:
        raise ValueError("港交所页面缺少 calendarDataSource")

    payload = json.loads(payload_match.group(1))
    closed_days = set()
    half_days = set()
    for item in payload.get("monthly", []):
        day = str(item.get("startdate", ""))
        if not day.startswith(f"{year}-"):
            continue
        if (
            item.get("holidayIcon") == "HongKongPublicHolidays"
            and item.get("description") == "Hong Kong Market is closed"
        ):
            closed_days.add(day)
        if (
            item.get("activityIcon") == "SecuritiesandDerivatives"
            and str(item.get("name", "")).startswith("Half-Day Trading Day")
        ):
            half_days.add(day)

    if not closed_days and not half_days:
        raise ValueError(f"港交所页面未提供 {year} 年证券市场日历")

    return CalendarSnapshot(
        market="hk",
        year=year,
        closed_days=frozenset(closed_days),
        half_days=frozenset(half_days),
        source_url=HKEX_CALENDAR_URL.format(year=year),
        checked_at=_now_iso(),
    )


def _load_snapshot(market: str, year: int, fetcher: Callable[[str], str]) -> CalendarSnapshot:
    url = SSE_CALENDAR_URL if market == "cn" else HKEX_CALENDAR_URL.format(year=year)
    page_text = fetcher(url)
    if market == "cn":
        return parse_sse_calendar(page_text, year)
    if market == "hk":
        return parse_hkex_calendar(page_text, year)
    raise ValueError(f"不支持的市场: {market}")


def get_calendar_day_kind(
    market: str,
    day: date,
    fetcher: Callable[[str], str] = _fetch_text,
) -> CalendarDay:
    checked_at = _now_iso()
    if day.weekday() >= 5:
        return CalendarDay("closed", None, checked_at)

    cache_key = (market, day.year)
    snapshot = None
    if fetcher is _fetch_text:
        cached = _snapshot_cache.get(cache_key)
        if cached:
            cached_at, cached_snapshot = cached
            if datetime.now(SHANGHAI_TZ) - cached_at < CACHE_TTL:
                snapshot = cached_snapshot

    try:
        if snapshot is None:
            snapshot = _load_snapshot(market, day.year, fetcher)
            if fetcher is _fetch_text:
                _snapshot_cache[cache_key] = (datetime.now(SHANGHAI_TZ), snapshot)
    except Exception:
        return CalendarDay("unknown", None, checked_at, "官方交易日历暂不可用")

    day_text = day.isoformat()
    if day_text in snapshot.closed_days:
        kind = "closed"
    elif day_text in snapshot.half_days:
        kind = "half"
    else:
        kind = "full"
    return CalendarDay(kind, snapshot.source_url, snapshot.checked_at)


def calculate_market_status(market: str, now: datetime, day_kind: str) -> MarketStatus:
    if day_kind == "unknown":
        return MarketStatus("unknown", "状态未知")
    if day_kind == "closed":
        return MarketStatus("holiday", "休市")

    local_time = now.astimezone(SHANGHAI_TZ).time().replace(tzinfo=None)
    if market == "cn":
        morning_start, morning_end = time(9, 30), time(11, 30)
        afternoon_start, afternoon_end = time(13, 0), time(15, 0)
    elif market == "hk":
        morning_start, morning_end = time(9, 30), time(12, 0)
        afternoon_start, afternoon_end = time(13, 0), time(16, 0)
    else:
        return MarketStatus("unknown", "状态未知")

    if local_time < morning_start:
        return MarketStatus("pre_open", "盘前")
    if morning_start <= local_time <= morning_end:
        return MarketStatus("trading", "交易中")
    if day_kind == "half":
        return MarketStatus("closed", "已收市")
    if morning_end < local_time < afternoon_start:
        return MarketStatus("lunch_break", "午间休市")
    if afternoon_start <= local_time <= afternoon_end:
        return MarketStatus("trading", "交易中")
    return MarketStatus("closed", "已收市")


def get_market_status(market: str, now: Optional[datetime] = None):
    current = now or datetime.now(SHANGHAI_TZ)
    day = get_calendar_day_kind(market, current.date())
    status = calculate_market_status(market, current, day.kind)
    return status, day
