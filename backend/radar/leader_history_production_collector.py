"""候选全集历史连续性的生产只读输入冻结与准入。

调用方必须先显式冻结官方交易日、时点行业成员和所有证券/指数历史序列，
本模块只做身份、全集、时间和现有历史 POC 重放。不联网、不读写数据库，
也不会生成正式分数或龙头状态。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_history_features import (
    AdjustedHistoryPoint,
    HistoryAdjustmentBasis,
)
from radar.leader_research_source_admission import (
    LeaderResearchHistoryAdmissionEntry,
)
from radar.leader_runtime_candidate_plan import (
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_history_public_poc import (
    EASTMONEY_QFQ_CONTRACT_ID,
    EASTMONEY_QFQ_URL,
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    PublicHistorySeries,
    REQUIRED_HISTORY_DATES,
    TENCENT_HISTORY_URL,
    TENCENT_INDEX_CONTRACT_ID,
    TENCENT_QFQ_CONTRACT_ID,
    TENCENT_QFQ_VERIFIED_NON_TRADING_CONTRACT_ID,
    VERIFIED_NON_TRADING_PRESENCE_SOURCE_CONTRACT_ID,
    is_public_history_series_complete,
    parse_eastmoney_qfq_rows,
    parse_tencent_history_payload,
    run_public_history_input_poc,
)
from radar.sector_history_trading_presence import (
    HistoricalTradingPresenceBatch,
    fetch_historical_trading_presence_batch,
    fetch_sina_historical_trading_dates,
)
from radar.sources.leader_tradability_public_poc import (
    PublicTradingCalendarEvidence,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)


LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID = (
    "radar-leader-history-production-collector-v1"
)
LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID = (
    "radar-leader-history-production-source-v1"
)
LEADER_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID = (
    "radar-leader-history-series-checkpoint-v1"
)
MAXIMUM_PRODUCTION_INDUSTRY_MEMBER_COUNT = 6000
MAXIMUM_FUTURE_SKEW_SECONDS = 5
HISTORY_REQUEST_TIMEOUT_SECONDS = 8
HISTORY_FETCH_WORKERS = 8
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_SYMBOL_PATTERN = re.compile(r"^[036][0-9]{5}$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_BARE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, repr=False)
class LeaderHistoryProductionFrozenBatch:
    """历史来源在候选计划前冻结后的纯内存批次。"""

    expected_trade_dates: Tuple[date, ...]
    memberships_by_industry: Mapping[str, PointInTimeIndustryMembership] = field(
        repr=False
    )
    series_by_symbol: Mapping[str, PublicHistorySeries] = field(repr=False)
    calendar_evidence: Optional[PublicTradingCalendarEvidence] = field(
        default=None,
        repr=False,
    )
    contract_id: str = LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID
    source_status: str = "ready"
    failure_count: int = 0
    failure_symbols: Tuple[str, ...] = field(default=(), repr=False)
    checkpoint_reused_count: int = 0
    checkpoint_saved_count: int = 0

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "expectedTradeDateCount": len(self.expected_trade_dates),
            "industryCount": len(self.memberships_by_industry),
            "fetchedSeriesCount": len(self.series_by_symbol),
            "returnedCount": len(self.series_by_symbol),
            "calendarEvidenceReady": self.calendar_evidence is not None,
            "sourceStatus": self.source_status,
            "failureCount": self.failure_count,
            "checkpointReusedCount": self.checkpoint_reused_count,
            "checkpointSavedCount": self.checkpoint_saved_count,
        }


def _unverified() -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        source_time=None,
        fetched_at=None,
        symbols=(),
        payload=None,
    )


def _source_failed() -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
        source_time=None,
        fetched_at=None,
        symbols=(),
        payload=None,
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _board_index(symbol: str) -> str:
    return "sh000001" if symbol.startswith("6") else "sz399001"


def _query_symbol(symbol: str) -> str:
    if symbol in {"sh000001", "sz399001"}:
        return symbol
    if re.fullmatch(r"[036][0-9]{5}", symbol):
        return ("sh" if symbol.startswith("6") else "sz") + symbol
    raise ValueError("history_symbol_invalid")


def _tencent_day_equivalence_contract_id(
    qfq_rows: Any,
    raw_rows: Any,
    *,
    current_day: date,
) -> Optional[str]:
    if qfq_rows == raw_rows:
        return "tencent-qfq-day-exact-equivalence-v1"
    if not isinstance(qfq_rows, list) or not isinstance(raw_rows, list):
        return None

    def completed(rows: list) -> Optional[list]:
        values = []
        for row in rows:
            try:
                trade_day = date.fromisoformat(str(row[0])[:10])
            except (IndexError, TypeError, ValueError):
                return None
            if trade_day > current_day:
                return None
            if trade_day < current_day:
                values.append(row)
        return values

    qfq_completed = completed(qfq_rows)
    raw_completed = completed(raw_rows)
    if (
        not qfq_completed
        or qfq_completed != raw_completed
    ):
        return None
    return "tencent-qfq-completed-day-exact-equivalence-v1"


def _default_history_requester(query_symbol: str) -> Any:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            TENCENT_HISTORY_URL,
            params={"param": f"{query_symbol},day,,,40,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if query_symbol in {"sh000001", "sz399001"}:
            return payload
        data = payload.get("data") if isinstance(payload, Mapping) else None
        stock_data = (
            data.get(query_symbol) if isinstance(data, Mapping) else None
        )
        if (
            not isinstance(stock_data, Mapping)
            or "qfqday" in stock_data
            or not isinstance(stock_data.get("day"), list)
        ):
            return payload
        raw_response = session.get(
            TENCENT_HISTORY_URL,
            params={"param": f"{query_symbol},day,,,40,"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
        )
        raw_response.raise_for_status()
        raw_payload = raw_response.json()
        raw_data = (
            raw_payload.get("data")
            if isinstance(raw_payload, Mapping) else None
        )
        raw_stock_data = (
            raw_data.get(query_symbol)
            if isinstance(raw_data, Mapping) else None
        )
        equivalence_contract_id = (
            _tencent_day_equivalence_contract_id(
                stock_data["day"],
                raw_stock_data.get("day")
                if isinstance(raw_stock_data, Mapping)
                else None,
                current_day=datetime.now(SHANGHAI_TZ).date(),
            )
        )
        if equivalence_contract_id is None:
            return payload
        verified_stock_data = dict(stock_data)
        verified_stock_data["qfqday"] = list(stock_data["day"])
        verified_stock_data["qfqDayEquivalenceContractId"] = (
            equivalence_contract_id
        )
        verified_data = dict(data)
        verified_data[query_symbol] = verified_stock_data
        verified_payload = dict(payload)
        verified_payload["data"] = verified_data
        return verified_payload


def _default_eastmoney_qfq_requester(
    symbol: str,
    start_date: date,
    end_date: date,
) -> Any:
    market_code = 1 if symbol.startswith("6") else 0
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            EASTMONEY_QFQ_URL,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": (
                    "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
                ),
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "101",
                "fqt": "1",
                "secid": f"{market_code}.{symbol}",
                "beg": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
            },
            timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, Mapping) else None
    klines = data.get("klines") if isinstance(data, Mapping) else None
    if not isinstance(klines, list):
        raise ValueError("eastmoney_qfq_rows_missing")
    rows = []
    for value in klines:
        fields = value.split(",") if isinstance(value, str) else ()
        if len(fields) < 3:
            continue
        rows.append({
            "日期": fields[0],
            "股票代码": symbol,
            "收盘": fields[2],
        })
    return tuple(rows)


def _checkpoint_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _checkpoint_scope(
    *,
    classification_document_sha256: str,
    expected_trade_dates: Tuple[date, ...],
) -> Mapping[str, object]:
    return {
        "collectorContractId": (
            LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID
        ),
        "classificationDocumentSha256": (
            classification_document_sha256
        ),
        "expectedTradeDates": [
            value.isoformat() for value in expected_trade_dates
        ],
        "sourceContracts": {
            TENCENT_QFQ_CONTRACT_ID: TENCENT_HISTORY_URL,
            TENCENT_INDEX_CONTRACT_ID: TENCENT_HISTORY_URL,
            EASTMONEY_QFQ_CONTRACT_ID: EASTMONEY_QFQ_URL,
        },
    }


def _series_checkpoint_payload(
    series: PublicHistorySeries,
    *,
    scope: Mapping[str, object],
    scope_identity: str,
) -> Mapping[str, object]:
    payload = {
        "symbol": series.symbol,
        "sourceContractId": series.source_contract_id,
        "sourceUrl": series.source_url,
        "adjustmentBasis": series.adjustment_basis.value,
        "fetchedAt": series.fetched_at.isoformat(),
        "contentSha256": series.content_sha256,
        "points": [
            [item.trade_date.isoformat(), item.close]
            for item in series.points
        ],
        "verifiedNonTradingDates": [
            value.isoformat()
            for value in series.verified_non_trading_dates
        ],
        "upstreamContentSha256": series.upstream_content_sha256,
        "tradingPresenceContentSha256": (
            series.trading_presence_content_sha256
        ),
        "tradingPresenceSourceContractId": (
            series.trading_presence_source_contract_id
        ),
    }
    return {
        "contractId": LEADER_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID,
        "scopeIdentity": scope_identity,
        "scope": scope,
        "payloadSha256": _checkpoint_digest(payload),
        "payload": payload,
    }


def _series_from_checkpoint(
    value: Any,
    *,
    symbol: str,
    scope: Mapping[str, object],
    scope_identity: str,
    expected_trade_dates: Tuple[date, ...],
    as_of: datetime,
) -> PublicHistorySeries:
    if (
        not isinstance(value, Mapping)
        or value.get("contractId")
        != LEADER_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID
        or value.get("scopeIdentity") != scope_identity
        or value.get("scope") != scope
        or _checkpoint_digest(scope) != scope_identity
        or not isinstance(value.get("payload"), Mapping)
        or value.get("payloadSha256")
        != _checkpoint_digest(value["payload"])
    ):
        raise ValueError("leader_history_checkpoint_unverified")
    payload = value["payload"]
    if (
        payload.get("symbol") != symbol
        or not isinstance(payload.get("points"), list)
    ):
        raise ValueError("leader_history_checkpoint_unverified")
    try:
        series = PublicHistorySeries(
            symbol=symbol,
            source_contract_id=payload["sourceContractId"],
            source_url=payload["sourceUrl"],
            adjustment_basis=HistoryAdjustmentBasis(
                payload["adjustmentBasis"]
            ),
            fetched_at=datetime.fromisoformat(payload["fetchedAt"]),
            content_sha256=payload["contentSha256"],
            points=tuple(
                AdjustedHistoryPoint(
                    trade_date=date.fromisoformat(row[0]),
                    close=row[1],
                )
                for row in payload["points"]
                if isinstance(row, list) and len(row) == 2
            ),
            verified_non_trading_dates=tuple(
                date.fromisoformat(value)
                for value in payload.get("verifiedNonTradingDates", [])
            ),
            upstream_content_sha256=payload.get("upstreamContentSha256"),
            trading_presence_content_sha256=payload.get(
                "tradingPresenceContentSha256"
            ),
            trading_presence_source_contract_id=payload.get(
                "tradingPresenceSourceContractId"
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("leader_history_checkpoint_unverified") from exc
    expected_adjustment = (
        HistoryAdjustmentBasis.CONTINUOUS_INDEX
        if symbol in {"sh000001", "sz399001"}
        else HistoryAdjustmentBasis.FORWARD_ADJUSTED
    )
    if not is_public_history_series_complete(
        series,
        symbol=symbol,
        expected_dates=expected_trade_dates,
        expected_adjustment=expected_adjustment,
        as_of=as_of,
    ):
        raise ValueError("leader_history_checkpoint_unverified")
    return series


def _write_atomic_checkpoint(
    path: Path,
    payload: Mapping[str, object],
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def fetch_leader_history_series_batch(
    symbols: Tuple[str, ...],
    expected_trade_dates: Tuple[date, ...],
    *,
    requester: Any = None,
    fallback_requester: Any = None,
    clock: Any = None,
    max_workers: int = HISTORY_FETCH_WORKERS,
    checkpoint_dir: Optional[Path] = None,
    classification_document_sha256: Optional[str] = None,
    presence_loader: Any = fetch_historical_trading_presence_batch,
    independent_presence_requester: Any = (
        fetch_sina_historical_trading_dates
    ),
    terminal_non_trading_symbols: Tuple[str, ...] = (),
) -> LeaderHistoryProductionFrozenBatch:
    """按唯一证券/指数代码抓取日线；可断点续跑但不交付部分批次。"""

    if (
        not isinstance(symbols, tuple)
        or not symbols
        or not isinstance(expected_trade_dates, tuple)
        or len(expected_trade_dates) != REQUIRED_HISTORY_DATES
        or any(not isinstance(day, date) for day in expected_trade_dates)
        or expected_trade_dates != tuple(sorted(expected_trade_dates))
        or len(expected_trade_dates) != len(set(expected_trade_dates))
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or max_workers < 1
        or max_workers > HISTORY_FETCH_WORKERS
        or (checkpoint_dir is None)
        != (classification_document_sha256 is None)
        or (
            classification_document_sha256 is not None
            and (
                not isinstance(classification_document_sha256, str)
                or _BARE_SHA256_PATTERN.fullmatch(
                    classification_document_sha256
                ) is None
            )
        )
        or not callable(presence_loader)
        or not callable(independent_presence_requester)
        or not isinstance(terminal_non_trading_symbols, tuple)
        or len(terminal_non_trading_symbols)
        != len(set(terminal_non_trading_symbols))
        or any(
            not isinstance(symbol, str)
            or symbol not in symbols
            for symbol in terminal_non_trading_symbols
        )
    ):
        return LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=expected_trade_dates
            if isinstance(expected_trade_dates, tuple)
            else (),
            memberships_by_industry={},
            series_by_symbol={},
            source_status="source_unverified",
        )
    unique_symbols = tuple(dict.fromkeys(symbols))
    try:
        query_symbols = {
            symbol: _query_symbol(symbol) for symbol in unique_symbols
        }
    except (TypeError, ValueError):
        return LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=expected_trade_dates,
            memberships_by_industry={},
            series_by_symbol={},
            source_status="source_unverified",
        )
    request = requester or _default_history_requester
    fallback = fallback_requester or _default_eastmoney_qfq_requester
    now = clock or (lambda: datetime.now(SHANGHAI_TZ))
    results = {}
    failures = []
    checkpoint_reused_count = 0
    checkpoint_saved_count = 0
    checkpoint_scope = None
    checkpoint_identity = None
    checkpoint_series_dir = None
    checkpoint_as_of = None
    if checkpoint_dir is not None:
        try:
            checkpoint_root = Path(checkpoint_dir).expanduser().resolve()
            checkpoint_root.relative_to(Path("/private/tmp"))
            checkpoint_scope = _checkpoint_scope(
                classification_document_sha256=(
                    classification_document_sha256
                ),
                expected_trade_dates=expected_trade_dates,
            )
            checkpoint_identity = _checkpoint_digest(checkpoint_scope)
            checkpoint_root = (
                checkpoint_root
                / f"leader-history-{checkpoint_identity[:16]}"
            ).resolve()
            checkpoint_root.relative_to(Path("/private/tmp"))
            checkpoint_series_dir = checkpoint_root / "series"
            checkpoint_series_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_as_of = now()
            if not _aware(checkpoint_as_of):
                raise ValueError("history_checkpoint_time_unverified")
        except (OSError, TypeError, ValueError):
            return LeaderHistoryProductionFrozenBatch(
                expected_trade_dates=expected_trade_dates,
                memberships_by_industry={},
                series_by_symbol={},
                source_status="source_unverified",
            )
        for symbol in unique_symbols:
            path = checkpoint_series_dir / f"series-{symbol}.json"
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                series = _series_from_checkpoint(
                    stored,
                    symbol=symbol,
                    scope=checkpoint_scope,
                    scope_identity=checkpoint_identity,
                    expected_trade_dates=expected_trade_dates,
                    as_of=checkpoint_as_of,
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            results[symbol] = series
            checkpoint_reused_count += 1

    def save_checkpoint(symbol: str, series: PublicHistorySeries) -> None:
        nonlocal checkpoint_saved_count
        if checkpoint_series_dir is None:
            return
        try:
            _write_atomic_checkpoint(
                checkpoint_series_dir / f"series-{symbol}.json",
                _series_checkpoint_payload(
                    series,
                    scope=checkpoint_scope,
                    scope_identity=checkpoint_identity,
                ),
            )
        except (OSError, TypeError, ValueError):
            return
        checkpoint_saved_count += 1

    def load(symbol: str):
        payload = request(query_symbols[symbol])
        board = symbol in {"sh000001", "sz399001"}
        fetched_at = now()
        if not _aware(fetched_at):
            raise ValueError("history_fetched_at_timezone_missing")
        try:
            series = parse_tencent_history_payload(
                symbol=symbol,
                query_symbol=query_symbols[symbol],
                payload=payload,
                expected_trade_dates=expected_trade_dates,
                fetched_at=fetched_at,
                adjustment_basis=(
                    HistoryAdjustmentBasis.CONTINUOUS_INDEX
                    if board
                    else HistoryAdjustmentBasis.FORWARD_ADJUSTED
                ),
            )
        except ValueError as exc:
            if board or str(exc) != "tencent_history_rows_missing":
                raise
            series = None
        if series is not None and not is_public_history_series_complete(
            series,
            symbol=symbol,
            expected_dates=expected_trade_dates,
            expected_adjustment=(
                HistoryAdjustmentBasis.CONTINUOUS_INDEX
                if board else HistoryAdjustmentBasis.FORWARD_ADJUSTED
            ),
            as_of=fetched_at,
        ):
            if board:
                raise ValueError("tencent_history_series_incomplete")
            series = None
        return symbol, series, payload

    pending_symbols = tuple(
        symbol for symbol in unique_symbols if symbol not in results
    )
    if pending_symbols:
        partial_primary = {}
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(pending_symbols))
        ) as pool:
            futures = {
                pool.submit(load, symbol): symbol
                for symbol in pending_symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    key, series, payload = future.result()
                except Exception:
                    if symbol in {"sh000001", "sz399001"}:
                        failures.append(symbol)
                else:
                    if series is not None:
                        results[key] = series
                        save_checkpoint(key, series)
                    elif symbol not in {"sh000001", "sz399001"}:
                        partial_primary[symbol] = payload
    else:
        partial_primary = {}

    fallback_symbols = tuple(
        symbol for symbol in unique_symbols
        if symbol not in results and symbol not in failures
    )
    fallback_failures = []
    for symbol in fallback_symbols:
        try:
            rows = fallback(
                symbol,
                expected_trade_dates[0],
                expected_trade_dates[-1],
            )
            fallback_fetched_at = now()
            if not _aware(fallback_fetched_at):
                raise ValueError("history_fetched_at_timezone_missing")
            series = parse_eastmoney_qfq_rows(
                symbol=symbol,
                rows=rows,
                expected_trade_dates=expected_trade_dates,
                fetched_at=fallback_fetched_at,
            )
            if not is_public_history_series_complete(
                series,
                symbol=symbol,
                expected_dates=expected_trade_dates,
                expected_adjustment=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
                as_of=fallback_fetched_at,
            ):
                raise ValueError("eastmoney_qfq_series_incomplete")
            results[symbol] = series
            save_checkpoint(symbol, series)
        except Exception:
            fallback_failures.append(symbol)

    alignable_payloads = {
        symbol: partial_primary[symbol]
        for symbol in fallback_failures
        if symbol in partial_primary
    }
    if alignable_payloads:
        partial_series = {}
        gaps_by_symbol = {}
        for symbol, payload in alignable_payloads.items():
            try:
                fetched_at = now()
                if not _aware(fetched_at):
                    raise ValueError("history_fetched_at_timezone_missing")
                series = parse_tencent_history_payload(
                    symbol=symbol,
                    query_symbol=query_symbols[symbol],
                    payload=payload,
                    expected_trade_dates=expected_trade_dates,
                    fetched_at=fetched_at,
                    adjustment_basis=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
                )
                point_dates = {item.trade_date for item in series.points}
                gaps = tuple(
                    value for value in expected_trade_dates
                    if value not in point_dates
                )
                if not gaps:
                    raise ValueError("history_alignment_gap_missing")
            except (TypeError, ValueError):
                continue
            partial_series[symbol] = series
            gaps_by_symbol[symbol] = gaps

        if gaps_by_symbol:
            payloads_by_query = {
                query_symbols[symbol]: alignable_payloads[symbol]
                for symbol in gaps_by_symbol
            }

            def presence_requester(query_symbol: str):
                return payloads_by_query[query_symbol]

            try:
                presence = presence_loader(
                    gaps_by_symbol,
                    expected_trade_dates=expected_trade_dates,
                    terminal_non_trading_symbols=tuple(
                        symbol for symbol in terminal_non_trading_symbols
                        if symbol in gaps_by_symbol
                    ),
                    requester=presence_requester,
                )
            except Exception:
                presence = None
            if (
                type(presence) is HistoricalTradingPresenceBatch
                and presence.source_status == "ready"
                and presence.failure_count == 0
            ):
                for symbol, gaps in gaps_by_symbol.items():
                    if (
                        presence.verified_non_trading_dates_by_symbol.get(
                            symbol
                        ) != gaps
                        or presence.verified_trading_dates_by_symbol.get(
                            symbol, ()
                        )
                        or symbol not in presence.source_hashes_by_symbol
                        or symbol
                        not in presence.source_contract_ids_by_symbol
                    ):
                        continue
                    try:
                        independent_dates = tuple(sorted(set(
                            independent_presence_requester(symbol)
                        )))
                    except Exception:
                        continue
                    if not independent_dates:
                        continue
                    payload = alignable_payloads[symbol]
                    query_symbol = query_symbols[symbol]
                    stock_data = payload.get("data", {}).get(query_symbol, {})
                    rows = stock_data.get("qfqday")
                    actual = {}
                    if isinstance(rows, list):
                        for row in rows:
                            try:
                                day = date.fromisoformat(str(row[0])[:10])
                                close = float(row[2])
                            except (IndexError, TypeError, ValueError):
                                continue
                            if math.isfinite(close) and close > 0:
                                actual[day] = close
                    independent_set = set(independent_dates)
                    first_independent = independent_dates[0]
                    last_independent = independent_dates[-1]
                    if any(
                        gap in independent_set
                        or not (
                            first_independent < gap < last_independent
                            or (
                                symbol in terminal_non_trading_symbols
                                and gap > last_independent
                            )
                            or (
                                first_independent < gap
                                and any(day > gap for day in actual)
                            )
                        )
                        for gap in gaps
                    ):
                        continue
                    points = []
                    valid = True
                    for day in expected_trade_dates:
                        if day in actual:
                            close = actual[day]
                        elif day in gaps:
                            prior_dates = tuple(
                                value for value in actual if value < day
                            )
                            if not prior_dates:
                                valid = False
                                break
                            close = actual[max(prior_dates)]
                        else:
                            valid = False
                            break
                        points.append(AdjustedHistoryPoint(
                            trade_date=day,
                            close=close,
                        ))
                    if not valid:
                        continue
                    upstream = partial_series[symbol]
                    primary_presence_hash = (
                        presence.source_hashes_by_symbol[symbol]
                    )
                    independent_presence_hash = (
                        "sha256:" + _checkpoint_digest([
                            value.isoformat() for value in independent_dates
                        ])
                    )
                    presence_hash = "sha256:" + _checkpoint_digest({
                        "primary": primary_presence_hash,
                        "independent": independent_presence_hash,
                    })
                    presence_contract = (
                        VERIFIED_NON_TRADING_PRESENCE_SOURCE_CONTRACT_ID
                    )
                    aligned_payload = {
                        "derivationContractId": (
                            "verified-non-trading-zero-return-alignment-v1"
                        ),
                        "upstreamContentSha256": upstream.content_sha256,
                        "tradingPresenceContentSha256": presence_hash,
                        "tradingPresenceSourceContractId": presence_contract,
                        "verifiedNonTradingDates": [
                            value.isoformat() for value in gaps
                        ],
                        "points": [
                            [item.trade_date.isoformat(), item.close]
                            for item in points
                        ],
                    }
                    aligned = PublicHistorySeries(
                        symbol=symbol,
                        source_contract_id=(
                            TENCENT_QFQ_VERIFIED_NON_TRADING_CONTRACT_ID
                        ),
                        source_url=TENCENT_HISTORY_URL,
                        adjustment_basis=(
                            HistoryAdjustmentBasis.FORWARD_ADJUSTED
                        ),
                        fetched_at=max(upstream.fetched_at, now()),
                        content_sha256=(
                            "sha256:" + _checkpoint_digest(aligned_payload)
                        ),
                        points=tuple(points),
                        verified_non_trading_dates=gaps,
                        upstream_content_sha256=upstream.content_sha256,
                        trading_presence_content_sha256=presence_hash,
                        trading_presence_source_contract_id=presence_contract,
                    )
                    if not is_public_history_series_complete(
                        aligned,
                        symbol=symbol,
                        expected_dates=expected_trade_dates,
                        expected_adjustment=(
                            HistoryAdjustmentBasis.FORWARD_ADJUSTED
                        ),
                        as_of=aligned.fetched_at,
                    ):
                        continue
                    results[symbol] = aligned
                    save_checkpoint(symbol, aligned)
                    fallback_failures.remove(symbol)
    failures.extend(fallback_failures)
    return LeaderHistoryProductionFrozenBatch(
        expected_trade_dates=expected_trade_dates,
        memberships_by_industry={},
        series_by_symbol=results if not failures else {},
        source_status="source_failed" if failures else "ready",
        failure_count=len(failures),
        failure_symbols=tuple(sorted(set(failures))),
        checkpoint_reused_count=checkpoint_reused_count,
        checkpoint_saved_count=checkpoint_saved_count,
    )


def _expected_members(
    context: LeaderResearchRuntimeSourceContext,
    industry_code: str,
) -> Optional[Tuple[Tuple[str, ...], int]]:
    values = context.industry_constituent_symbols_by_code.get(industry_code)
    if not isinstance(values, tuple) or not values:
        return None
    if len(values) != len(set(values)) or any(
        not isinstance(symbol, str)
        or re.fullmatch(r"\d{6}", symbol) is None
        for symbol in values
    ):
        return None
    members = tuple(
        symbol for symbol in values
        if _SYMBOL_PATTERN.fullmatch(symbol) is not None
    )
    if not members:
        return None
    return members, len(values) - len(members)


def _valid_batch(
    batch: Any,
    context: LeaderResearchRuntimeSourceContext,
) -> bool:
    if (
        type(batch) is not LeaderHistoryProductionFrozenBatch
        or batch.contract_id != LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID
        or batch.source_status != "ready"
        or not isinstance(batch.failure_count, int)
        or isinstance(batch.failure_count, bool)
        or batch.failure_count != 0
        or not isinstance(batch.failure_symbols, tuple)
        or batch.failure_symbols
        or not isinstance(batch.expected_trade_dates, tuple)
        or len(batch.expected_trade_dates) != REQUIRED_HISTORY_DATES
        or any(not isinstance(day, date) for day in batch.expected_trade_dates)
        or not isinstance(batch.memberships_by_industry, Mapping)
        or not isinstance(batch.series_by_symbol, Mapping)
    ):
        return False
    if (
        batch.expected_trade_dates != tuple(sorted(batch.expected_trade_dates))
        or len(batch.expected_trade_dates) != len(set(batch.expected_trade_dates))
        or batch.expected_trade_dates[-1] > context.as_of.date()
    ):
        return False
    calendar = batch.calendar_evidence
    if (
        type(calendar) is not PublicTradingCalendarEvidence
        or calendar.exchange != "sse"
        or calendar.trading_dates != batch.expected_trade_dates
        or not all(isinstance(value, str) for value in (
            calendar.source_contract_id,
            calendar.source_name,
            calendar.source_url,
            calendar.document_id,
            calendar.content_sha256,
        ))
        or calendar.source_contract_id not in {
            "sse-public-trading-calendar-v1",
            "sse-a-share-trading-calendar-v1",
        }
        or calendar.source_name != "上海证券交易所"
        or urlparse(calendar.source_url).hostname not in {
            "www.sse.com.cn",
            "sse.com.cn",
        }
        or not calendar.document_id.strip()
        or _SHA256_PATTERN.fullmatch(calendar.content_sha256) is None
        or not _aware(calendar.source_time)
        or not _aware(calendar.fetched_at)
        or calendar.fetched_at < calendar.source_time
        or calendar.fetched_at > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return False
    industries = tuple(dict.fromkeys(
        item.industry_code for item in context.candidate_plan.items
    ))
    if any(
        industry not in batch.memberships_by_industry
        for industry in industries
    ):
        return False
    expected_series = set()
    for item in context.candidate_plan.items:
        member_scope = _expected_members(context, item.industry_code)
        membership = batch.memberships_by_industry.get(item.industry_code)
        if (
            member_scope is None
            or type(membership) is not PointInTimeIndustryMembership
            or not isinstance(membership.member_symbols, tuple)
            or membership.release_id != item.industry_release_id
            or membership.industry_code != item.industry_code
            or not isinstance(membership.source_contract_id, str)
            or not isinstance(membership.release_id, str)
            or not membership.source_contract_id.strip()
            or not membership.release_id.strip()
            or not _aware(membership.first_observed_at)
            or not _aware(membership.fetched_at)
            or membership.fetched_at > context.as_of + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            )
            or membership.first_observed_at > membership.fetched_at
            or not _SYMBOL_PATTERN.fullmatch(item.symbol)
        ):
            return False
        members, excluded_count = member_scope
        if (
            tuple(membership.member_symbols) != members
            or membership.excluded_out_of_scope_count != excluded_count
        ):
            return False
        expected_series.update(members)
        expected_series.add(_board_index(item.symbol))
    allowed_series_sources = {
        TENCENT_QFQ_CONTRACT_ID: TENCENT_HISTORY_URL,
        TENCENT_QFQ_VERIFIED_NON_TRADING_CONTRACT_ID: TENCENT_HISTORY_URL,
        TENCENT_INDEX_CONTRACT_ID: TENCENT_HISTORY_URL,
        EASTMONEY_QFQ_CONTRACT_ID: EASTMONEY_QFQ_URL,
    }
    if any(
        not isinstance(symbol, str)
        or not isinstance(series, PublicHistorySeries)
        or series.symbol != symbol
        or allowed_series_sources.get(series.source_contract_id)
        != series.source_url
        for symbol, series in batch.series_by_symbol.items()
    ):
        return False
    return expected_series.issubset(set(batch.series_by_symbol))


def collect_leader_history_production_source(
    context: Any,
    batch: Any,
) -> LeaderFormalResearchProductionCollectedSource:
    """把候选全集历史冻结批次重放为正式研究 history 来源。"""

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or not is_leader_runtime_candidate_plan_valid(context.candidate_plan)
    ):
        return _unverified()
    if (
        type(batch) is LeaderHistoryProductionFrozenBatch
        and batch.source_status == "source_failed"
    ):
        return _source_failed()
    if not _valid_batch(batch, context):
        return _unverified()

    plan = context.candidate_plan
    entries = []
    source_times = [batch.calendar_evidence.source_time]
    fetched_times = [batch.calendar_evidence.fetched_at]
    for item in plan.items:
        member_scope = _expected_members(context, item.industry_code)
        if member_scope is None:
            return _unverified()
        members, _ = member_scope
        membership = batch.memberships_by_industry[item.industry_code]
        fetched_times.append(membership.fetched_at)
        candidate_membership = replace(
            membership,
            candidate_symbol=item.symbol,
        )
        query = PublicHistoryPocQuery(
            as_of=context.as_of,
            expected_trade_dates=batch.expected_trade_dates,
            candidate_symbol=item.symbol,
            board_index_symbol=_board_index(item.symbol),
            membership=candidate_membership,
            series_by_symbol=batch.series_by_symbol,
            maximum_member_count=MAXIMUM_PRODUCTION_INDUSTRY_MEMBER_COUNT,
        )
        try:
            result = run_public_history_input_poc(query)
        except Exception:
            return _unverified()
        if result.resolution_status != "ready":
            return _unverified()
        entries.append(LeaderResearchHistoryAdmissionEntry(
            symbol=item.symbol,
            candidate_plan_id=plan.candidate_set_id,
            radar_run_id=plan.radar_run_id,
            quote_batch_id=plan.quote_batch_id,
            query=query,
            result=result,
        ))
        source_times.append(datetime.combine(
            batch.expected_trade_dates[-1],
            time(15, 0),
            tzinfo=SHANGHAI_TZ,
        ))
        fetched_times.extend(
            series.fetched_at
            for symbol, series in batch.series_by_symbol.items()
            if symbol in set(members or ()) | {_board_index(item.symbol)}
        )

    if len(entries) != plan.candidate_count or not fetched_times:
        return _unverified()
    source_time = max(source_times)
    fetched_at = max(fetched_times)
    if (
        not _aware(source_time)
        or not _aware(fetched_at)
        or fetched_at < source_time
        or source_time > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return _unverified()
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
        source_time=source_time,
        fetched_at=fetched_at,
        symbols=tuple(item.symbol for item in plan.items),
        payload=tuple(entries),
    )


def build_leader_history_production_loader(
    batch: LeaderHistoryProductionFrozenBatch,
):
    """为现有四源生产 provider 构造一个无网络 history loader。"""

    if type(batch) is not LeaderHistoryProductionFrozenBatch:
        raise ValueError("leader_history_production_batch_unverified")

    def load(context: LeaderResearchRuntimeSourceContext):
        return collect_leader_history_production_source(context, batch)

    return load
