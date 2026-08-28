"""上交所与深交所公开实时状态的只读适配器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.sources.leader_tradability_public_poc import (
    PublicSecurityContext,
    PublicSourceKind,
    PublicTradabilityObservation,
)


EXCHANGE_OFFICIAL_BATCH_CONTRACT_ID = (
    "radar-leader-tradability-exchange-official-batch-v1"
)
SSE_STATUS_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/snap/"
SZSE_STATUS_URL = (
    "https://www.szse.cn/api/market/ssjjhq/getTimeData"
)
SSE_STATUS_SELECT = (
    "name,tradephase,cpxxprodusta,cpxxlmttype,up_limit,down_limit"
)
MAXIMUM_SCOPE_COUNT = 6000
MAXIMUM_WORKERS = 8
DEFAULT_WORKERS = 4
REQUEST_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 5
MAXIMUM_CLOCK_SKEW_SECONDS = 5
MAXIMUM_RECOVERABLE_CLOCK_SKEW_SECONDS = 15
CLOCK_SKEW_RETRY_EPSILON_SECONDS = 0.05
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_SYMBOL_PATTERN = re.compile(r"[036][0-9]{5}")


class ExchangeOfficialSourceError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, repr=False)
class ExchangeOfficialObservationBatch:
    status: str
    observations: Tuple[PublicTradabilityObservation, ...] = ()
    reasons: Tuple[str, ...] = ()
    source_time: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    contract_id: str = EXCHANGE_OFFICIAL_BATCH_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status,
            "returnedCount": len(self.observations),
            "sourceTime": (
                self.source_time.isoformat()
                if self.source_time is not None
                else None
            ),
            "fetchedAt": (
                self.fetched_at.isoformat()
                if self.fetched_at is not None
                else None
            ),
            "reasons": list(self.reasons),
            "formalUsable": False,
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _valid_context(context: Any, exchange: str) -> bool:
    if (
        type(context) is not PublicSecurityContext
        or context.exchange != exchange
        or _SYMBOL_PATTERN.fullmatch(context.symbol or "") is None
    ):
        return False
    if exchange == "sse":
        return context.symbol.startswith("6")
    return context.symbol.startswith(("0", "3"))


def _lifecycle(name: Any, *, is_delisting: bool = False):
    if is_delisting:
        return SecurityLifecycleStatus.DELISTING
    normalized = str(name or "").strip().upper().replace(" ", "")
    if not normalized:
        raise ExchangeOfficialSourceError(
            "exchange_official_name_missing"
        )
    if normalized.startswith("*ST"):
        return SecurityLifecycleStatus.STAR_ST
    if normalized.startswith("ST"):
        return SecurityLifecycleStatus.ST
    return SecurityLifecycleStatus.NORMAL


def _sse_source_time(payload: Mapping[str, Any]) -> datetime:
    try:
        day = str(int(payload.get("date"))).zfill(8)
        moment = str(int(payload.get("time"))).zfill(6)
        return datetime.strptime(
            day + moment,
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=SHANGHAI_TZ)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExchangeOfficialSourceError(
            "exchange_official_source_time_invalid"
        ) from exc


def _szse_source_time(payload: Mapping[str, Any]) -> datetime:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ExchangeOfficialSourceError(
            "exchange_official_payload_invalid"
        )
    try:
        return datetime.strptime(
            str(data.get("marketTime") or "").strip(),
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=SHANGHAI_TZ)
    except ValueError as exc:
        raise ExchangeOfficialSourceError(
            "exchange_official_source_time_invalid"
        ) from exc


def _validate_times(
    *,
    source_time: datetime,
    fetched_at: datetime,
    trading_date: date,
) -> None:
    if not _aware(fetched_at):
        raise ExchangeOfficialSourceError(
            "exchange_official_fetched_at_invalid"
        )
    if source_time.date() != trading_date:
        raise ExchangeOfficialSourceError(
            "exchange_official_trading_date_mismatch"
        )
    future_skew_seconds = (source_time - fetched_at).total_seconds()
    if future_skew_seconds > MAXIMUM_CLOCK_SKEW_SECONDS:
        retry_after_seconds = None
        if (
            future_skew_seconds
            <= MAXIMUM_RECOVERABLE_CLOCK_SKEW_SECONDS
        ):
            retry_after_seconds = (
                future_skew_seconds
                - MAXIMUM_CLOCK_SKEW_SECONDS
                + CLOCK_SKEW_RETRY_EPSILON_SECONDS
            )
        raise ExchangeOfficialSourceError(
            "exchange_official_source_time_in_future",
            retry_after_seconds=retry_after_seconds,
        )


def _sse_trading_status(value: Any) -> TradingSessionStatus:
    phase = str(value or "").strip().upper()
    if not phase:
        raise ExchangeOfficialSourceError(
            "exchange_official_phase_missing"
        )
    if phase[0] == "P":
        return TradingSessionStatus.SUSPENDED
    if phase[0] in {"M", "N"}:
        return TradingSessionStatus.ABNORMAL
    if phase[0] in {"S", "C", "D", "T", "B", "E", "U"}:
        return TradingSessionStatus.TRADING
    raise ExchangeOfficialSourceError(
        "exchange_official_phase_unknown"
    )


def _szse_trading_status(
    phase1_value: Any,
    phase2_value: Any,
) -> TradingSessionStatus:
    phase1 = str(phase1_value or "").strip().zfill(2)
    phase2 = str(phase2_value or "").strip().zfill(2)
    valid_phase1 = {"00", "02", "03", "04", "05", "06", "07", "11", "12"}
    valid_phase2 = {"00", "04", "06", "08", "09", "11"}
    if phase1 not in valid_phase1 or phase2 not in valid_phase2:
        raise ExchangeOfficialSourceError(
            "exchange_official_phase_unknown"
        )
    if "04" in {phase1, phase2} or "06" in {phase1, phase2}:
        return TradingSessionStatus.SUSPENDED
    if phase1 == "12":
        return TradingSessionStatus.ABNORMAL
    return TradingSessionStatus.TRADING


def _positive_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def build_sse_official_observation(
    *,
    context: PublicSecurityContext,
    trading_date: date,
    payload: Any,
    fetched_at: datetime,
) -> PublicTradabilityObservation:
    if not _valid_context(context, "sse"):
        raise ExchangeOfficialSourceError(
            "exchange_official_scope_invalid"
        )
    if not isinstance(payload, Mapping):
        raise ExchangeOfficialSourceError(
            "exchange_official_payload_invalid"
        )
    if str(payload.get("code") or "").strip() != context.symbol:
        raise ExchangeOfficialSourceError(
            "exchange_official_symbol_mismatch"
        )
    snap = payload.get("snap")
    if not isinstance(snap, list) or len(snap) < 6:
        raise ExchangeOfficialSourceError(
            "exchange_official_payload_invalid"
        )
    source_time = _sse_source_time(payload)
    _validate_times(
        source_time=source_time,
        fetched_at=fetched_at,
        trading_date=trading_date,
    )
    lifecycle = _lifecycle(snap[0])
    limit_type = str(snap[3] or "").strip().upper()
    upper = _positive_float(snap[4])
    lower = _positive_float(snap[5])
    price_limit_mode = None
    if limit_type == "N" and upper is not None and lower is not None:
        price_limit_mode = PriceLimitMode.BOUNDED
    elif limit_type == "P":
        price_limit_mode = PriceLimitMode.NO_LIMIT
        upper = None
        lower = None
    return PublicTradabilityObservation(
        symbol=context.symbol,
        exchange=context.exchange,
        board=context.board,
        trading_date=trading_date,
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_contract_id="sse-public-status-v1",
        source_name="上海证券交易所",
        source_url=SSE_STATUS_URL,
        document_id=(
            f"sse-status-{context.symbol}-{source_time:%Y%m%dT%H%M%S}"
        ),
        source_time=source_time,
        fetched_at=fetched_at,
        content_sha256=_canonical_sha256(payload),
        effective_from=trading_date,
        effective_until=trading_date,
        lifecycle_status=lifecycle,
        trading_status=_sse_trading_status(snap[1]),
        special_session=(
            None
            if lifecycle == SecurityLifecycleStatus.DELISTING
            else PriceLimitSpecialSession.NONE
        ),
        price_limit_mode=price_limit_mode,
        upper_limit_price=upper,
        lower_limit_price=lower,
    )


def build_szse_official_observation(
    *,
    context: PublicSecurityContext,
    trading_date: date,
    payload: Any,
    fetched_at: datetime,
) -> PublicTradabilityObservation:
    if not _valid_context(context, "szse"):
        raise ExchangeOfficialSourceError(
            "exchange_official_scope_invalid"
        )
    if not isinstance(payload, Mapping) or str(payload.get("code")) != "0":
        raise ExchangeOfficialSourceError(
            "exchange_official_payload_invalid"
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ExchangeOfficialSourceError(
            "exchange_official_payload_invalid"
        )
    if str(data.get("code") or "").strip() != context.symbol:
        raise ExchangeOfficialSourceError(
            "exchange_official_symbol_mismatch"
        )
    source_time = _szse_source_time(payload)
    _validate_times(
        source_time=source_time,
        fetched_at=fetched_at,
        trading_date=trading_date,
    )
    lifecycle = _lifecycle(
        data.get("name"),
        is_delisting=data.get("isDelisting") is True,
    )
    return PublicTradabilityObservation(
        symbol=context.symbol,
        exchange=context.exchange,
        board=context.board,
        trading_date=trading_date,
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_contract_id="szse-public-status-v1",
        source_name="深圳证券交易所",
        source_url=SZSE_STATUS_URL,
        document_id=(
            f"szse-status-{context.symbol}-{source_time:%Y%m%dT%H%M%S}"
        ),
        source_time=source_time,
        fetched_at=fetched_at,
        content_sha256=_canonical_sha256(payload),
        effective_from=trading_date,
        effective_until=trading_date,
        lifecycle_status=lifecycle,
        trading_status=_szse_trading_status(
            data.get("tradingPhaseCode1"),
            data.get("tradingPhaseCode2"),
        ),
        special_session=(
            None
            if lifecycle == SecurityLifecycleStatus.DELISTING
            else PriceLimitSpecialSession.NONE
        ),
    )


def _request_json(context: PublicSecurityContext) -> Any:
    session = requests.Session()
    session.trust_env = False
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0",
    }
    if context.exchange == "sse":
        response = session.get(
            SSE_STATUS_URL + context.symbol,
            params={"select": SSE_STATUS_SELECT},
            headers={
                **headers,
                "Referer": "https://www.sse.com.cn/",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    else:
        response = session.get(
            SZSE_STATUS_URL,
            params={"marketId": "1", "code": context.symbol},
            headers={
                **headers,
                "Referer": "https://www.szse.cn/market/trend/",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    return response.json()


def _unique_reasons(values) -> Tuple[str, ...]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def collect_exchange_official_observations(
    *,
    contexts: Tuple[PublicSecurityContext, ...],
    trading_date: date,
    request_json: Optional[
        Callable[[PublicSecurityContext], Any]
    ] = None,
    clock: Callable[[], datetime] = lambda: datetime.now(SHANGHAI_TZ),
    max_workers: int = DEFAULT_WORKERS,
) -> ExchangeOfficialObservationBatch:
    if (
        not isinstance(contexts, tuple)
        or not 1 <= len(contexts) <= MAXIMUM_SCOPE_COUNT
        or not isinstance(trading_date, date)
        or isinstance(trading_date, datetime)
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or not 1 <= max_workers <= MAXIMUM_WORKERS
        or len({item.symbol for item in contexts}) != len(contexts)
        or any(
            not (
                _valid_context(item, "sse")
                or _valid_context(item, "szse")
            )
            for item in contexts
        )
    ):
        return ExchangeOfficialObservationBatch(
            status="source_unverified",
            reasons=("exchange_official_scope_invalid",),
        )

    fetcher = request_json or _request_json
    observations = [None] * len(contexts)
    errors = [None] * len(contexts)

    def collect_one(index: int, item: PublicSecurityContext):
        for attempt in range(REQUEST_ATTEMPTS):
            try:
                payload = fetcher(item)
                fetched_at = clock()
                builder = (
                    build_sse_official_observation
                    if item.exchange == "sse"
                    else build_szse_official_observation
                )
                observations[index] = builder(
                    context=item,
                    trading_date=trading_date,
                    payload=payload,
                    fetched_at=fetched_at,
                )
                return
            except ExchangeOfficialSourceError as exc:
                if (
                    attempt + 1 < REQUEST_ATTEMPTS
                    and exc.retry_after_seconds is not None
                ):
                    time.sleep(exc.retry_after_seconds)
                    continue
                errors[index] = ("source_unverified", exc.reason_code)
                return
            except Exception:
                if attempt + 1 == REQUEST_ATTEMPTS:
                    errors[index] = (
                        "source_failed",
                        "exchange_official_request_failed",
                    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(collect_one, index, item)
            for index, item in enumerate(contexts)
        ]
        for future in as_completed(futures):
            future.result()

    failures = [item for item in errors if item is not None]
    if failures:
        status = (
            "source_failed"
            if any(item[0] == "source_failed" for item in failures)
            else "source_unverified"
        )
        reasons = _unique_reasons(item[1] for item in failures)
        return ExchangeOfficialObservationBatch(
            status=status,
            reasons=reasons,
        )
    completed = tuple(observations)
    return ExchangeOfficialObservationBatch(
        status="completed",
        observations=completed,
        source_time=max(item.source_time for item in completed),
        fetched_at=max(item.fetched_at for item in completed),
    )
