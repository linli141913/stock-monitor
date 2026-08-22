"""用独立日线来源核验分钟历史中的无交易日。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

from radar.sources.leader_history_public_poc import TENCENT_HISTORY_URL


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TRADING_PRESENCE_CONTRACT_ID = "radar-sector-trading-presence-v1"
TRADING_PRESENCE_SOURCE_CONTRACT_ID = (
    "tencent-qfq-daily-trading-presence-v1"
)
SINA_TRADING_PRESENCE_SOURCE_CONTRACT_ID = (
    "sina-daily-trading-presence-v1"
)
TRADING_PRESENCE_REQUEST_TIMEOUT_SECONDS = 8
MAXIMUM_TRADING_PRESENCE_WORKERS = 8


@dataclass(frozen=True, repr=False)
class HistoricalTradingPresenceBatch:
    verified_trading_dates_by_symbol: Mapping[
        str, Tuple[date, ...]
    ] = field(repr=False)
    verified_non_trading_dates_by_symbol: Mapping[
        str, Tuple[date, ...]
    ] = field(repr=False)
    source_hashes_by_symbol: Mapping[str, str] = field(repr=False)
    source_status: str
    requested_count: int
    failure_count: int
    source_contract_ids_by_symbol: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
    )
    failure_reason_counts: Mapping[str, int] = field(default_factory=dict)
    failed_symbols: Tuple[str, ...] = field(default_factory=tuple, repr=False)
    contract_id: str = TRADING_PRESENCE_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "sourceContractIds": sorted(set(
                self.source_contract_ids_by_symbol.values()
            )),
            "sourceStatus": self.source_status,
            "requestedCount": self.requested_count,
            "returnedCount": len(self.source_hashes_by_symbol),
            "failureCount": self.failure_count,
            "failureReasonCounts": dict(self.failure_reason_counts),
        }


def _query_symbol(symbol: str) -> str:
    return ("sh" if symbol.startswith("6") else "sz") + symbol


def _default_requester(query_symbol: str) -> Any:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            TENCENT_HISTORY_URL,
            params={"param": f"{query_symbol},day,,,120,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TRADING_PRESENCE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()


def _default_sina_daily_requester(symbol: str) -> Tuple[date, ...]:
    import akshare as ak

    prefix = "sh" if symbol.startswith("6") else "sz"
    frame = ak.stock_zh_a_daily(
        symbol=f"{prefix}{symbol}",
        adjust="",
    )
    if frame is None or "date" not in frame.columns:
        raise ValueError("sina_trading_presence_rows_missing")
    values = []
    for value in frame["date"].tolist():
        if isinstance(value, datetime):
            values.append(value.date())
        elif isinstance(value, date):
            values.append(value)
        else:
            values.append(date.fromisoformat(str(value)[:10]))
    result = tuple(sorted(set(values)))
    if not result:
        raise ValueError("sina_trading_presence_rows_empty")
    return result


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


def _parse_dates(payload: Any, query_symbol: str) -> Tuple[date, ...]:
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        raise ValueError("trading_presence_payload_invalid")
    data = payload.get("data")
    symbol_data = data.get(query_symbol) if isinstance(data, Mapping) else None
    rows = symbol_data.get("qfqday") if isinstance(symbol_data, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("trading_presence_rows_missing")
    values = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        try:
            values.append(date.fromisoformat(str(row[0])[:10]))
        except (IndexError, ValueError):
            continue
    values = tuple(sorted(set(values)))
    if not values:
        raise ValueError("trading_presence_rows_empty")
    return values


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.RequestException):
        return "transport_failed"
    if isinstance(exc, ValueError):
        return {
            "trading_presence_window_not_enclosed": "window_not_enclosed",
            "trading_presence_rows_empty": "rows_empty",
        }.get(str(exc), "payload_invalid")
    return "request_failed"


def fetch_historical_trading_presence_batch(
    dates_to_verify_by_symbol: Mapping[str, Tuple[date, ...]],
    *,
    expected_trade_dates: Tuple[date, ...],
    requester: Callable[[str], Any] = _default_requester,
    sina_daily_requester: Optional[
        Callable[[str], Tuple[date, ...]]
    ] = None,
    terminal_non_trading_symbols: Tuple[str, ...] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(SHANGHAI_TZ),
    max_workers: int = MAXIMUM_TRADING_PRESENCE_WORKERS,
) -> HistoricalTradingPresenceBatch:
    """只在日线返回完整包围目标日期时，才把缺行解释为无交易。"""

    valid = bool(
        isinstance(dates_to_verify_by_symbol, Mapping)
        and dates_to_verify_by_symbol
        and isinstance(expected_trade_dates, tuple)
        and expected_trade_dates
        and type(max_workers) is int
        and 1 <= max_workers <= MAXIMUM_TRADING_PRESENCE_WORKERS
    )
    if not valid:
        return HistoricalTradingPresenceBatch(
            verified_trading_dates_by_symbol={},
            verified_non_trading_dates_by_symbol={},
            source_hashes_by_symbol={},
            source_status="source_unverified",
            requested_count=0,
            failure_count=0,
        )
    targets = {}
    for symbol, values in dates_to_verify_by_symbol.items():
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isdigit()
            or not isinstance(values, tuple)
            or not values
            or len(values) != len(set(values))
            or any(value not in expected_trade_dates for value in values)
        ):
            return HistoricalTradingPresenceBatch(
                verified_trading_dates_by_symbol={},
                verified_non_trading_dates_by_symbol={},
                source_hashes_by_symbol={},
                source_status="source_unverified",
                requested_count=len(dates_to_verify_by_symbol),
                failure_count=0,
            )
        targets[symbol] = tuple(sorted(values))
    terminal_non_trading = set(terminal_non_trading_symbols)
    active_sina_requester = (
        _default_sina_daily_requester
        if requester is _default_requester
        else sina_daily_requester
    )
    if (
        len(terminal_non_trading) != len(terminal_non_trading_symbols)
        or not terminal_non_trading.issubset(set(targets))
    ):
        return HistoricalTradingPresenceBatch(
            verified_trading_dates_by_symbol={},
            verified_non_trading_dates_by_symbol={},
            source_hashes_by_symbol={},
            source_status="source_unverified",
            requested_count=len(targets),
            failure_count=0,
        )
    trading = {}
    non_trading = {}
    hashes = {}
    contracts = {}
    failures = []

    def load(symbol: str):
        query_symbol = _query_symbol(symbol)
        fetched_at = clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("trading_presence_fetched_at_timezone_missing")
        target_dates = targets[symbol]
        try:
            payload = requester(query_symbol)
            returned_dates = _parse_dates(payload, query_symbol)
            source_contract_id = TRADING_PRESENCE_SOURCE_CONTRACT_ID
            content_digest = _digest(payload)
        except Exception:
            if active_sina_requester is None:
                raise
            returned_dates = tuple(active_sina_requester(symbol))
            if not returned_dates:
                raise ValueError("trading_presence_rows_empty")
            source_contract_id = SINA_TRADING_PRESENCE_SOURCE_CONTRACT_ID
            content_digest = _digest(returned_dates)
        def unresolved_absences(values: Tuple[date, ...]) -> Tuple[date, ...]:
            returned = set(values)
            first = values[0]
            last = values[-1]
            return tuple(
                target
                for target in target_dates
                if (
                    target not in returned
                    and not first < target < last
                    and not (
                        symbol in terminal_non_trading
                        and target > last
                    )
                )
            )

        unresolved = unresolved_absences(returned_dates)
        if unresolved:
            if (
                source_contract_id == TRADING_PRESENCE_SOURCE_CONTRACT_ID
                and active_sina_requester is not None
            ):
                fallback_dates = tuple(active_sina_requester(symbol))
                if fallback_dates:
                    returned_dates = fallback_dates
                    source_contract_id = (
                        SINA_TRADING_PRESENCE_SOURCE_CONTRACT_ID
                    )
                    content_digest = _digest(returned_dates)
                    unresolved = unresolved_absences(returned_dates)
            if unresolved:
                raise ValueError("trading_presence_window_not_enclosed")
        returned = set(returned_dates)
        return (
            symbol,
            tuple(value for value in target_dates if value in returned),
            tuple(value for value in target_dates if value not in returned),
            content_digest,
            source_contract_id,
        )

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(targets)),
        thread_name_prefix="sector-trading-presence",
    ) as pool:
        futures = {pool.submit(load, symbol): symbol for symbol in targets}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                key, yes, no, digest, source_contract_id = future.result()
            except Exception as exc:
                failures.append((symbol, _failure_reason(exc)))
            else:
                trading[key] = yes
                non_trading[key] = no
                hashes[key] = digest
                contracts[key] = source_contract_id
    return HistoricalTradingPresenceBatch(
        verified_trading_dates_by_symbol=trading,
        verified_non_trading_dates_by_symbol=non_trading,
        source_hashes_by_symbol=hashes,
        source_contract_ids_by_symbol=contracts,
        source_status="source_failed" if failures else "ready",
        requested_count=len(targets),
        failure_count=len(failures),
        failure_reason_counts={
            reason: sum(item_reason == reason for _, item_reason in failures)
            for reason in sorted({item_reason for _, item_reason in failures})
        },
        failed_symbols=tuple(sorted(symbol for symbol, _ in failures)),
    )
