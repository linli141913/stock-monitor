"""东方财富5分钟历史的有界并发采集器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Any, Callable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from radar.sector_history_backfill import (
    EASTMONEY_MINUTE_URL,
    SINA_MINUTE_URL,
    HistoricalMinuteSeries,
    parse_eastmoney_minute_payload,
    parse_sina_minute_payload,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SECTOR_HISTORY_MINUTE_BATCH_CONTRACT_ID = (
    "radar-sector-history-minute-batch-v1"
)
MINUTE_REQUEST_TIMEOUT_SECONDS = 12
MAXIMUM_MINUTE_FETCH_WORKERS = 12


@dataclass(frozen=True, repr=False)
class SectorHistoryMinuteFrozenBatch:
    expected_trade_dates: Tuple[date, ...]
    series_by_symbol: Mapping[str, HistoricalMinuteSeries] = field(repr=False)
    source_status: str
    failure_count: int
    requested_count: int
    failure_reason_counts: Mapping[str, int] = field(default_factory=dict)
    contract_id: str = SECTOR_HISTORY_MINUTE_BATCH_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "sourceStatus": self.source_status,
            "requestedCount": self.requested_count,
            "returnedCount": len(self.series_by_symbol),
            "failureCount": self.failure_count,
            "failureReasonCounts": dict(self.failure_reason_counts),
            "tradeDateCount": len(self.expected_trade_dates),
        }


def _default_requester(symbol: str) -> Any:
    market = 1 if symbol.startswith("6") else 0
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            EASTMONEY_MINUTE_URL,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "5",
                "fqt": "0",
                "secid": f"{market}.{symbol}",
                "beg": "0",
                "end": "20500000",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=MINUTE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()


def _default_sina_requester(symbol: str) -> Any:
    prefix = "sh" if symbol.startswith("6") else "sz"
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            SINA_MINUTE_URL,
            params={
                "symbol": f"{prefix}{symbol}",
                "scale": "5",
                "ma": "no",
                "datalen": "1970",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=MINUTE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        text = response.text
        try:
            encoded = text.split("=(", 1)[1].rsplit(");", 1)[0]
            payload = json.loads(encoded)
        except (IndexError, json.JSONDecodeError) as exc:
            raise ValueError("sina_minute_payload_invalid") from exc
        if not isinstance(payload, list):
            raise ValueError("sina_minute_payload_invalid")
        return payload


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        if status in {429, 456}:
            return "rate_limited"
        if status in {401, 403}:
            return "access_denied"
        if isinstance(status, int) and status >= 500:
            return "upstream_server_failed"
        return "http_failed"
    if isinstance(exc, requests.RequestException):
        return "transport_failed"
    if isinstance(exc, ValueError):
        if str(exc) == "minute_history_empty":
            return "rows_empty"
        return "payload_invalid"
    return "request_failed"


def fetch_sector_history_minute_series_batch(
    symbols: Tuple[str, ...],
    expected_trade_dates: Tuple[date, ...],
    *,
    requester: Optional[Callable[[str], Any]] = None,
    sina_requester: Callable[[str], Any] = _default_sina_requester,
    clock: Callable[[], datetime] = lambda: datetime.now(SHANGHAI_TZ),
    max_workers: int = MAXIMUM_MINUTE_FETCH_WORKERS,
) -> SectorHistoryMinuteFrozenBatch:
    """抓取唯一证券全量历史；失败项不阻止成功项用于断点续跑。"""

    valid = bool(
        isinstance(symbols, tuple)
        and symbols
        and isinstance(expected_trade_dates, tuple)
        and expected_trade_dates
        and expected_trade_dates
        == tuple(sorted(expected_trade_dates))
        and len(expected_trade_dates) == len(set(expected_trade_dates))
        and all(type(value) is date for value in expected_trade_dates)
        and type(max_workers) is int
        and 1 <= max_workers <= MAXIMUM_MINUTE_FETCH_WORKERS
    )
    unique_symbols = tuple(dict.fromkeys(symbols)) if valid else ()
    if (
        not valid
        or any(
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isdigit()
            or not symbol.startswith(("0", "3", "6"))
            for symbol in unique_symbols
        )
    ):
        return SectorHistoryMinuteFrozenBatch(
            expected_trade_dates=(
                expected_trade_dates
                if isinstance(expected_trade_dates, tuple) else ()
            ),
            series_by_symbol={},
            source_status="source_unverified",
            failure_count=0,
            requested_count=len(unique_symbols),
        )

    results = {}
    failures = []

    def load(symbol: str):
        if requester is None:
            payload = sina_requester(symbol)
            series = parse_sina_minute_payload(
                symbol=symbol,
                payload=payload,
                expected_trade_dates=expected_trade_dates,
                fetched_at=clock(),
            )
        else:
            payload = requester(symbol)
            series = parse_eastmoney_minute_payload(
                symbol=symbol,
                payload=payload,
                expected_trade_dates=expected_trade_dates,
                fetched_at=clock(),
            )
        return symbol, series

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(unique_symbols)),
        thread_name_prefix="sector-history-minute",
    ) as pool:
        futures = {
            pool.submit(load, symbol): symbol for symbol in unique_symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                key, series = future.result()
                if not series.bars:
                    raise ValueError("minute_history_empty")
            except Exception as exc:
                failures.append((symbol, _failure_reason(exc)))
            else:
                results[key] = series
    return SectorHistoryMinuteFrozenBatch(
        expected_trade_dates=expected_trade_dates,
        series_by_symbol=results,
        source_status="source_failed" if failures else "ready",
        failure_count=len(failures),
        requested_count=len(unique_symbols),
        failure_reason_counts={
            reason: sum(item_reason == reason for _, item_reason in failures)
            for reason in sorted({item_reason for _, item_reason in failures})
        },
    )
