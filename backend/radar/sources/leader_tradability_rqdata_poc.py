"""阶段6L-C4B RQData可交易性字段级POC。

本模块只执行调用方显式注入的隔离POC传输，不读取配置、不连接数据库，
也不把供应商字段转换为C3官方来源或任何正式状态。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import io
import json
import math
import re
import socket
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
RQDATA_TRADABILITY_POC_CONTRACT_ID = (
    "radar-leader-tradability-rqdata-poc-v1"
)
RQDATA_ORDER_BOOK_ID_PATTERN = re.compile(
    r"^(?:[03][0-9]{5}\.XSHE|6[0-9]{5}\.XSHG)$"
)
RQDATA_POC_TIMEOUT_SECONDS = 20.0
RQDATA_HTTP_API_URL = "https://rqdata.ricequant.com/api"
RQDATA_MAXIMUM_RESPONSE_BYTES = 1_000_000
RQDATA_MAXIMUM_SAMPLE_COUNT = 8
RQDATA_MAXIMUM_DYNAMIC_AGE_SECONDS = 90.0
RQDATA_MAXIMUM_FUTURE_SKEW_SECONDS = 5.0
RQDATA_MAXIMUM_OBSERVATION_SKEW_SECONDS = 5.0
RQDATA_ALLOWED_BOARD_TYPES = frozenset((
    "MainBoard",
    "GEM",
    "SME",
))
RQDATA_ALLOWED_LIFECYCLE_STATUSES = frozenset((
    "Active",
    "Delisted",
    "TemporarySuspended",
))
RQDATA_ALLOWED_SPECIAL_TYPES = frozenset((
    "Normal",
    "ST",
    "StarST",
    "PT",
    "Other",
))
# 官方HTTP示例目前只明确展示连续交易阶段码T；其他值先保持未映射。
RQDATA_DOCUMENTED_TRADING_PHASE_CODES = frozenset(("T",))
RQDATA_BATCH_METHODS = (
    "instruments",
    "get_price",
    "current_snapshot",
)
RQDATA_SINGLE_SECURITY_METHODS = (
    "is_suspended",
    "is_st_stock",
)
RQDATA_ALLOWED_METHODS = frozenset((
    *RQDATA_BATCH_METHODS,
    *RQDATA_SINGLE_SECURITY_METHODS,
))
RQDATA_STABLE_TRANSPORT_REASONS = frozenset((
    "rqdata_http_token_unverified",
    "rqdata_http_method_unverified",
    "rqdata_http_payload_unverified",
    "rqdata_http_timeout_unverified",
    "rqdata_http_authentication_failed",
    "rqdata_http_permission_denied",
    "rqdata_http_quota_or_rate_limited",
    "rqdata_http_source_unavailable",
    "rqdata_http_request_rejected",
    "rqdata_http_timeout",
    "rqdata_http_network_failed",
    "rqdata_http_response_contract_unverified",
    "rqdata_http_response_too_large",
    "rqdata_http_response_encoding_invalid",
    "rqdata_http_transport_failed",
))
RQDATA_REQUIRED_FIELDS = {
    "instruments": (
        "order_book_id",
        "exchange",
        "board_type",
        "listed_date",
        "de_listed_date",
        "status",
        "special_type",
    ),
    "get_price": (
        "order_book_id",
        "prev_close",
        "limit_up",
        "limit_down",
    ),
    "is_suspended": (
        "order_book_id",
        "date",
        "is_suspended",
    ),
    "is_st_stock": (
        "order_book_id",
        "date",
        "is_st_stock",
    ),
    "current_snapshot": (
        "order_book_id",
        "datetime",
        "trading_phase_code",
        "prev_close",
        "limit_up",
        "limit_down",
    ),
}


class RqdataPocStatus(str, Enum):
    NOT_RUN = "not_run"
    FIELD_CANDIDATE = "field_candidate"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class RqdataPocTransportError(RuntimeError):
    """不携带上游正文或凭证的稳定POC传输错误。"""

    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str):
        safe_reason = (
            reason_code
            if reason_code in RQDATA_STABLE_TRANSPORT_REASONS
            else "rqdata_http_transport_failed"
        )
        self.reason_code = safe_reason
        super().__init__(safe_reason)

    def __repr__(self) -> str:
        return (
            "RqdataPocTransportError("
            f"reason_code={self.reason_code!r})"
        )


@dataclass(frozen=True)
class RqdataTradabilityPocQuery:
    as_of: datetime
    trading_date: date
    expected_order_book_ids: Tuple[str, ...]
    include_dynamic_snapshot: bool = False


@dataclass(frozen=True)
class RqdataPocCallSummary:
    method: str
    response_digest: Optional[str]
    row_count: int
    order_book_id: Optional[str] = None
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True, repr=False)
class RqdataTradabilityPocRecord:
    order_book_id: str
    trading_date: date
    symbol: str
    exchange: Optional[str]
    board_type: Optional[str]
    listed_date: Optional[date]
    de_listed_date: Optional[date]
    lifecycle_status: Optional[str]
    special_type: Optional[str]
    is_suspended: Optional[bool]
    is_st_stock: Optional[bool]
    prev_close: Optional[float]
    limit_up: Optional[float]
    limit_down: Optional[float]
    snapshot_at: Optional[datetime]
    trading_phase_code: Optional[str]


@dataclass(frozen=True)
class RqdataTradabilityPocReport:
    status: RqdataPocStatus
    real_poc_status: str
    as_of: Optional[datetime]
    trading_date: Optional[date]
    expected_count: int
    records: Tuple[RqdataTradabilityPocRecord, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    call_summaries: Tuple[RqdataPocCallSummary, ...] = ()
    field_coverage: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    contract_id: str = RQDATA_TRADABILITY_POC_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Dict[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "realPocStatus": self.real_poc_status,
            "asOf": (
                self.as_of.isoformat()
                if self.as_of is not None
                else None
            ),
            "tradingDate": (
                self.trading_date.isoformat()
                if self.trading_date is not None
                else None
            ),
            "expectedCount": self.expected_count,
            "recordCount": len(self.records),
            "fieldCoverage": list(self.field_coverage),
            "calls": [
                {
                    "method": item.method,
                    "orderBookId": item.order_book_id,
                    "responseDigest": item.response_digest,
                    "rowCount": item.row_count,
                    "reasons": list(item.reasons),
                }
                for item in self.call_summaries
            ],
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": (
                    self.state_transition_allowed
                ),
            },
        }


RqdataPocTransport = Callable[
    [str, Mapping[str, object], float],
    str,
]


class _RqdataHttpTransport:
    __slots__ = ("_token", "_opener", "_is_live")

    def __init__(
        self,
        token: str,
        opener: Callable[..., Any],
        *,
        is_live: bool,
    ):
        self._token = token
        self._opener = opener
        self._is_live = is_live

    def __repr__(self) -> str:
        return (
            "RqdataHttpTransport("
            f"endpoint={RQDATA_HTTP_API_URL!r}, token=<hidden>)"
        )

    def __call__(
        self,
        method: str,
        payload: Mapping[str, object],
        timeout: float,
    ) -> str:
        if method not in RQDATA_ALLOWED_METHODS:
            raise RqdataPocTransportError(
                "rqdata_http_method_unverified"
            )
        if (
            not isinstance(payload, Mapping)
            or "method" in payload
        ):
            raise RqdataPocTransportError(
                "rqdata_http_payload_unverified"
            )
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or float(timeout) != RQDATA_POC_TIMEOUT_SECONDS
        ):
            raise RqdataPocTransportError(
                "rqdata_http_timeout_unverified"
            )
        request_payload = {"method": method}
        request_payload.update(payload)
        request_bytes = None
        try:
            request_bytes = json.dumps(
                request_payload,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            pass
        if request_bytes is None:
            raise RqdataPocTransportError(
                "rqdata_http_payload_unverified"
            )
        request = Request(
            RQDATA_HTTP_API_URL,
            data=request_bytes,
            headers={
                "Content-Type": "application/json",
                "token": self._token,
            },
            method="POST",
        )
        transport_failure_reason = None
        try:
            with self._opener(
                request,
                timeout=RQDATA_POC_TIMEOUT_SECONDS,
            ) as response:
                response_bytes = response.read(
                    RQDATA_MAXIMUM_RESPONSE_BYTES + 1
                )
        except HTTPError as exc:
            if exc.code == 401:
                reason = "rqdata_http_authentication_failed"
            elif exc.code == 403:
                reason = "rqdata_http_permission_denied"
            elif exc.code == 429:
                reason = "rqdata_http_quota_or_rate_limited"
            elif exc.code >= 500:
                reason = "rqdata_http_source_unavailable"
            else:
                reason = "rqdata_http_request_rejected"
            transport_failure_reason = reason
            try:
                exc.close()
            except Exception:
                pass
        except (TimeoutError, socket.timeout):
            transport_failure_reason = "rqdata_http_timeout"
        except (URLError, OSError):
            transport_failure_reason = "rqdata_http_network_failed"
        except Exception:
            transport_failure_reason = "rqdata_http_transport_failed"
        if transport_failure_reason is not None:
            raise RqdataPocTransportError(
                transport_failure_reason
            ) from None
        if not isinstance(response_bytes, bytes):
            raise RqdataPocTransportError(
                "rqdata_http_response_contract_unverified"
            )
        if len(response_bytes) > RQDATA_MAXIMUM_RESPONSE_BYTES:
            raise RqdataPocTransportError(
                "rqdata_http_response_too_large"
            )
        decoded = None
        try:
            decoded = response_bytes.decode("utf-8")
        except UnicodeDecodeError:
            pass
        if decoded is None:
            raise RqdataPocTransportError(
                "rqdata_http_response_encoding_invalid"
            )
        return decoded


def build_rqdata_http_transport(
    token: Any,
    *,
    opener: Optional[Callable[..., Any]] = None,
) -> RqdataPocTransport:
    """构建固定端点传输；Token不进入对象表示或错误信息。"""

    if (
        not isinstance(token, str)
        or not token.strip()
        or len(token) > 4096
        or "\r" in token
        or "\n" in token
    ):
        raise RqdataPocTransportError(
            "rqdata_http_token_unverified"
        )
    is_live = opener is None
    return _RqdataHttpTransport(
        token.strip(),
        opener or urlopen,
        is_live=is_live,
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))


def _query_reasons(query: Any, fetched_at: Any) -> Tuple[str, ...]:
    if not isinstance(query, RqdataTradabilityPocQuery):
        return ("rqdata_query_contract_unverified",)
    reasons = []
    as_of_utc = _aware_utc(query.as_of)
    fetched_at_utc = _aware_utc(fetched_at)
    if as_of_utc is None:
        reasons.append("rqdata_query_as_of_timezone_missing")
    if fetched_at_utc is None:
        reasons.append("rqdata_fetched_at_timezone_missing")
    if (
        not isinstance(query.trading_date, date)
        or isinstance(query.trading_date, datetime)
    ):
        reasons.append("rqdata_query_trading_date_unverified")
    if not isinstance(query.expected_order_book_ids, tuple):
        reasons.append("rqdata_query_symbols_contract_unverified")
        expected = ()
    else:
        expected = query.expected_order_book_ids
    if not expected:
        reasons.append("rqdata_query_symbols_empty")
    if any(
        not isinstance(item, str)
        or RQDATA_ORDER_BOOK_ID_PATTERN.fullmatch(item) is None
        for item in expected
    ):
        reasons.append("rqdata_query_symbol_unverified")
    if len(set(expected)) != len(expected):
        reasons.append("rqdata_query_symbol_duplicated")
    if len(expected) > RQDATA_MAXIMUM_SAMPLE_COUNT:
        reasons.append("rqdata_query_sample_limit_exceeded")
    if not isinstance(query.include_dynamic_snapshot, bool):
        reasons.append("rqdata_query_dynamic_flag_unverified")
    if (
        as_of_utc is not None
        and fetched_at_utc is not None
        and isinstance(query.trading_date, date)
        and not isinstance(query.trading_date, datetime)
    ):
        local_date = fetched_at.astimezone(SHANGHAI_TZ).date()
        if query.trading_date > local_date:
            reasons.append("rqdata_query_trading_date_from_future")
        if query.include_dynamic_snapshot:
            if abs(
                (fetched_at_utc - as_of_utc).total_seconds()
            ) > RQDATA_MAXIMUM_OBSERVATION_SKEW_SECONDS:
                reasons.append(
                    "rqdata_dynamic_observation_time_mismatch"
                )
            if query.trading_date != local_date:
                reasons.append("rqdata_dynamic_snapshot_date_mismatch")
            local_time = fetched_at.astimezone(SHANGHAI_TZ).time()
            total_minutes = local_time.hour * 60 + local_time.minute
            if not (
                9 * 60 + 15 <= total_minutes <= 11 * 60 + 30
                or 13 * 60 <= total_minutes <= 15 * 60
            ):
                reasons.append(
                    "rqdata_dynamic_snapshot_window_closed"
                )
    return _dedupe(tuple(reasons))


def _request_calls(
    query: RqdataTradabilityPocQuery,
) -> Tuple[Tuple[str, Dict[str, object], Optional[str]], ...]:
    ids = list(query.expected_order_book_ids)
    trading_date = query.trading_date.isoformat()
    calls: List[Tuple[str, Dict[str, object], Optional[str]]] = [
        (
            "instruments",
            {
                "order_book_ids": ids,
                "market": "cn",
            },
            None,
        ),
        (
            "get_price",
            {
                "order_book_ids": ids,
                "start_date": trading_date,
                "end_date": trading_date,
                "frequency": "1d",
                "fields": [
                    "prev_close",
                    "limit_up",
                    "limit_down",
                ],
                "adjust_type": "none",
            },
            None,
        ),
    ]
    for method in RQDATA_SINGLE_SECURITY_METHODS:
        for order_book_id in query.expected_order_book_ids:
            calls.append((
                method,
                {
                    "order_book_id": order_book_id,
                    "count": 1,
                },
                order_book_id,
            ))
    if query.include_dynamic_snapshot:
        calls.append((
            "current_snapshot",
            {
                "order_book_ids": ids,
                "market": "cn",
            },
            None,
        ))
    return tuple(calls)


def _response_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _parse_csv_response(
    method: str,
    response_text: Any,
) -> Tuple[
    Tuple[Dict[str, str], ...],
    Optional[str],
    Tuple[str, ...],
    Tuple[str, ...],
]:
    if not isinstance(response_text, str):
        return (
            (),
            None,
            (f"rqdata_{method}_response_contract_unverified",),
            (),
        )
    digest = _response_digest(response_text)
    if not response_text.strip():
        return (
            (),
            digest,
            (),
            (f"rqdata_{method}_response_empty",),
        )
    reader = csv.DictReader(io.StringIO(
        response_text.lstrip("\ufeff")
    ))
    raw_fields = reader.fieldnames
    if raw_fields is None:
        return (
            (),
            digest,
            (f"rqdata_{method}_response_contract_unverified",),
            (),
        )
    fields = tuple(
        item.strip() if isinstance(item, str) else ""
        for item in raw_fields
    )
    if (
        any(not item for item in fields)
        or len(set(fields)) != len(fields)
    ):
        return (
            (),
            digest,
            (f"rqdata_{method}_response_fields_unverified",),
            (),
        )
    required = RQDATA_REQUIRED_FIELDS[method]
    if any(item not in fields for item in required):
        return (
            (),
            digest,
            (f"rqdata_{method}_response_fields_missing",),
            (),
        )
    if method == "get_price":
        trading_date_fields = tuple(
            item for item in ("date", "datetime") if item in fields
        )
        if len(trading_date_fields) != 1:
            reason = (
                "rqdata_get_price_response_fields_missing"
                if not trading_date_fields
                else "rqdata_get_price_response_fields_unverified"
            )
            return (), digest, (reason,), ()
    rows = []
    for raw_row in reader:
        if (
            not isinstance(raw_row, dict)
            or None in raw_row
            or any(value is None for value in raw_row.values())
        ):
            return (
                (),
                digest,
                (f"rqdata_{method}_response_row_unverified",),
                (),
            )
        rows.append({
            str(key).strip(): str(value).strip()
            for key, value in raw_row.items()
        })
    if not rows:
        return (
            (),
            digest,
            (),
            (f"rqdata_{method}_response_empty",),
        )
    return tuple(rows), digest, (), ()


def _index_rows(
    method: str,
    rows: Sequence[Mapping[str, str]],
    expected_ids: Sequence[str],
) -> Tuple[
    Dict[str, Mapping[str, str]],
    Tuple[str, ...],
    Tuple[str, ...],
]:
    counts: Dict[str, int] = {}
    indexed: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        order_book_id = row.get("order_book_id")
        if not isinstance(order_book_id, str):
            order_book_id = ""
        counts[order_book_id] = counts.get(order_book_id, 0) + 1
        indexed[order_book_id] = row
    expected_set = set(expected_ids)
    actual_set = set(indexed)
    if (
        any(count != 1 for count in counts.values())
        or actual_set - expected_set
    ):
        return (
            {},
            (f"rqdata_{method}_response_identity_mismatch",),
            (),
        )
    missing = expected_set - actual_set
    if missing:
        return (
            indexed,
            (),
            (f"rqdata_{method}_response_coverage_incomplete",),
        )
    return indexed, (), ()


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _parse_price_trading_date(
    row: Mapping[str, str],
) -> Optional[date]:
    value = row.get("datetime", row.get("date"))
    parsed_date = _parse_date(value)
    if parsed_date is not None:
        return parsed_date
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _parse_optional_date(value: Any) -> Tuple[Optional[date], bool]:
    if value in (None, "", "0000-00-00"):
        return None, True
    parsed = _parse_date(value)
    return parsed, parsed is not None


def _parse_bool(value: Any) -> Optional[bool]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    return None


def _parse_optional_positive_float(
    value: Any,
) -> Tuple[Optional[float], bool]:
    if value in (None, ""):
        return None, True
    if isinstance(value, bool):
        return None, False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(parsed) or parsed <= 0:
        return None, False
    return parsed, True


def _parse_snapshot_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed


def _complete_records(
    query: RqdataTradabilityPocQuery,
    rows_by_method: Mapping[str, Mapping[str, Mapping[str, str]]],
    *,
    fetched_at: datetime,
) -> Tuple[
    Tuple[RqdataTradabilityPocRecord, ...],
    Tuple[str, ...],
    Tuple[str, ...],
]:
    blocked = []
    partial = []
    records = []
    for order_book_id in query.expected_order_book_ids:
        instrument = rows_by_method.get("instruments", {}).get(
            order_book_id
        )
        price = rows_by_method.get("get_price", {}).get(order_book_id)
        suspended_row = rows_by_method.get(
            "is_suspended", {}
        ).get(order_book_id)
        st_row = rows_by_method.get("is_st_stock", {}).get(
            order_book_id
        )
        snapshot = rows_by_method.get(
            "current_snapshot", {}
        ).get(order_book_id)

        exchange = None
        board_type = None
        listed_date = None
        de_listed_date = None
        lifecycle_status = None
        special_type = None
        if instrument is not None:
            expected_exchange = (
                "XSHE" if order_book_id.endswith(".XSHE") else "XSHG"
            )
            exchange = instrument.get("exchange")
            board_type = instrument.get("board_type")
            listed_date = _parse_date(instrument.get("listed_date"))
            de_listed_date, de_listed_valid = _parse_optional_date(
                instrument.get("de_listed_date")
            )
            lifecycle_status = instrument.get("status")
            special_type = instrument.get("special_type")
            if (
                exchange != expected_exchange
                or board_type not in RQDATA_ALLOWED_BOARD_TYPES
                or listed_date is None
                or not de_listed_valid
                or lifecycle_status
                not in RQDATA_ALLOWED_LIFECYCLE_STATUSES
                or special_type not in RQDATA_ALLOWED_SPECIAL_TYPES
            ):
                blocked.append(
                    "rqdata_instruments_field_contract_unverified"
                )

        prev_close = None
        limit_up = None
        limit_down = None
        if price is not None:
            price_date = _parse_price_trading_date(price)
            if price_date != query.trading_date:
                blocked.append("rqdata_get_price_trading_date_mismatch")
            prev_close, prev_valid = _parse_optional_positive_float(
                price.get("prev_close")
            )
            limit_up, up_valid = _parse_optional_positive_float(
                price.get("limit_up")
            )
            limit_down, down_valid = _parse_optional_positive_float(
                price.get("limit_down")
            )
            if not prev_valid or prev_close is None or not up_valid or not down_valid:
                blocked.append("rqdata_price_values_unverified")
            elif (limit_up is None) != (limit_down is None):
                blocked.append("rqdata_price_limit_pair_incomplete")
            elif limit_up is None and limit_down is None:
                partial.append("rqdata_price_limit_values_missing")
            elif not limit_down < prev_close < limit_up:
                blocked.append("rqdata_price_limit_values_unverified")

        is_suspended = None
        if suspended_row is not None:
            if _parse_date(suspended_row.get("date")) != query.trading_date:
                blocked.append(
                    "rqdata_is_suspended_trading_date_mismatch"
                )
            is_suspended = _parse_bool(
                suspended_row.get("is_suspended")
            )
            if is_suspended is None:
                blocked.append("rqdata_is_suspended_value_unverified")

        is_st_stock = None
        if st_row is not None:
            if _parse_date(st_row.get("date")) != query.trading_date:
                blocked.append("rqdata_is_st_stock_trading_date_mismatch")
            is_st_stock = _parse_bool(st_row.get("is_st_stock"))
            if is_st_stock is None:
                blocked.append("rqdata_is_st_stock_value_unverified")
            elif special_type in ("ST", "StarST") and not is_st_stock:
                blocked.append("rqdata_st_status_conflict")
            elif special_type == "Normal" and is_st_stock:
                blocked.append("rqdata_st_status_conflict")

        snapshot_at = None
        trading_phase_code = None
        if snapshot is not None:
            snapshot_at = _parse_snapshot_time(snapshot.get("datetime"))
            raw_trading_phase_code = snapshot.get(
                "trading_phase_code"
            )
            if (
                raw_trading_phase_code
                in RQDATA_DOCUMENTED_TRADING_PHASE_CODES
            ):
                trading_phase_code = raw_trading_phase_code
            elif raw_trading_phase_code:
                partial.append("rqdata_trading_phase_code_unmapped")
            snap_prev, snap_prev_valid = _parse_optional_positive_float(
                snapshot.get("prev_close")
            )
            snap_up, snap_up_valid = _parse_optional_positive_float(
                snapshot.get("limit_up")
            )
            snap_down, snap_down_valid = _parse_optional_positive_float(
                snapshot.get("limit_down")
            )
            if (
                snapshot_at is None
                or snapshot_at.astimezone(SHANGHAI_TZ).date()
                != query.trading_date
                or not raw_trading_phase_code
                or not snap_prev_valid
                or not snap_up_valid
                or not snap_down_valid
            ):
                blocked.append(
                    "rqdata_current_snapshot_field_contract_unverified"
                )
            else:
                source_age = (
                    fetched_at.astimezone(UTC)
                    - snapshot_at.astimezone(UTC)
                ).total_seconds()
                if source_age < -RQDATA_MAXIMUM_FUTURE_SKEW_SECONDS:
                    blocked.append("rqdata_current_snapshot_from_future")
                elif source_age > RQDATA_MAXIMUM_DYNAMIC_AGE_SECONDS:
                    partial.append("rqdata_current_snapshot_stale")
                if price is not None and ((
                    prev_close is not None
                    and snap_prev is not None
                    and not math.isclose(prev_close, snap_prev)
                ) or (
                    limit_up != snap_up or limit_down != snap_down
                )):
                    blocked.append(
                        "rqdata_current_snapshot_price_conflict"
                    )

        records.append(RqdataTradabilityPocRecord(
            order_book_id=order_book_id,
            trading_date=query.trading_date,
            symbol=order_book_id[:6],
            exchange=exchange,
            board_type=board_type,
            listed_date=listed_date,
            de_listed_date=de_listed_date,
            lifecycle_status=lifecycle_status,
            special_type=special_type,
            is_suspended=is_suspended,
            is_st_stock=is_st_stock,
            prev_close=prev_close,
            limit_up=limit_up,
            limit_down=limit_down,
            snapshot_at=snapshot_at,
            trading_phase_code=trading_phase_code,
        ))
    return tuple(records), _dedupe(blocked), _dedupe(partial)


def _field_coverage(
    records: Sequence[RqdataTradabilityPocRecord],
) -> Tuple[str, ...]:
    if not records:
        return ()
    field_map = (
        ("security_identity", "exchange"),
        ("board", "board_type"),
        ("lifecycle", "lifecycle_status"),
        ("special_type", "special_type"),
        ("suspension", "is_suspended"),
        ("st_status", "is_st_stock"),
        ("previous_close", "prev_close"),
        ("upper_limit_price", "limit_up"),
        ("lower_limit_price", "limit_down"),
        ("dynamic_source_time", "snapshot_at"),
        ("trading_phase", "trading_phase_code"),
    )
    return tuple(
        evidence_name
        for evidence_name, attribute in field_map
        if all(
            getattr(record, attribute) is not None
            for record in records
        )
    )


def _empty_report(
    query: Any,
    *,
    status: RqdataPocStatus,
    reasons: Tuple[str, ...],
) -> RqdataTradabilityPocReport:
    is_query = isinstance(query, RqdataTradabilityPocQuery)
    expected = (
        query.expected_order_book_ids
        if is_query
        and isinstance(query.expected_order_book_ids, tuple)
        else ()
    )
    return RqdataTradabilityPocReport(
        status=status,
        real_poc_status=(
            "not_run"
            if status == RqdataPocStatus.NOT_RUN
            else "blocked"
        ),
        as_of=query.as_of if is_query else None,
        trading_date=query.trading_date if is_query else None,
        expected_count=len(expected),
        reasons=reasons,
    )


def run_rqdata_tradability_poc(
    query: Any,
    *,
    fetched_at: Any,
    transport: Optional[RqdataPocTransport],
) -> RqdataTradabilityPocReport:
    """运行隔离字段POC；没有传输时明确返回未运行。"""

    reasons = _query_reasons(query, fetched_at)
    if reasons:
        return _empty_report(
            query,
            status=RqdataPocStatus.BLOCKED,
            reasons=reasons,
        )
    if transport is None:
        return _empty_report(
            query,
            status=RqdataPocStatus.NOT_RUN,
            reasons=("rqdata_real_poc_not_run",),
        )
    if not callable(transport):
        return _empty_report(
            query,
            status=RqdataPocStatus.BLOCKED,
            reasons=("rqdata_transport_contract_unverified",),
        )

    is_live_transport = (
        isinstance(transport, _RqdataHttpTransport)
        and transport._is_live
    )
    if (
        is_live_transport
        and query.trading_date
        != fetched_at.astimezone(SHANGHAI_TZ).date()
    ):
        return _empty_report(
            query,
            status=RqdataPocStatus.BLOCKED,
            reasons=(
                "rqdata_real_poc_requires_current_trading_date",
            ),
        )

    blocked_reasons: List[str] = []
    partial_reasons: List[str] = []
    if not is_live_transport:
        partial_reasons.append("rqdata_fixture_transport_only")
    call_summaries: List[RqdataPocCallSummary] = []
    rows_by_method: Dict[
        str,
        Dict[str, Mapping[str, str]],
    ] = {}
    for method, payload, single_order_book_id in _request_calls(query):
        try:
            response_text = transport(
                method,
                payload,
                RQDATA_POC_TIMEOUT_SECONDS,
            )
        except RqdataPocTransportError as exc:
            reason = exc.reason_code
            blocked_reasons.append(reason)
            call_summaries.append(RqdataPocCallSummary(
                method=method,
                order_book_id=single_order_book_id,
                response_digest=None,
                row_count=0,
                reasons=(reason,),
            ))
            break
        except Exception:
            reason = f"rqdata_{method}_source_failed"
            blocked_reasons.append(reason)
            call_summaries.append(RqdataPocCallSummary(
                method=method,
                order_book_id=single_order_book_id,
                response_digest=None,
                row_count=0,
                reasons=(reason,),
            ))
            break

        rows, digest, parse_blocked, parse_partial = (
            _parse_csv_response(method, response_text)
        )
        expected_for_call = (
            (single_order_book_id,)
            if single_order_book_id is not None
            else query.expected_order_book_ids
        )
        indexed: Dict[str, Mapping[str, str]] = {}
        identity_blocked: Tuple[str, ...] = ()
        identity_partial: Tuple[str, ...] = ()
        if not parse_blocked and rows:
            indexed, identity_blocked, identity_partial = _index_rows(
                method,
                rows,
                expected_for_call,
            )
            if not identity_blocked:
                method_rows = rows_by_method.setdefault(method, {})
                for order_book_id, row in indexed.items():
                    if order_book_id in method_rows:
                        identity_blocked = (
                            f"rqdata_{method}_response_identity_mismatch",
                        )
                        break
                    method_rows[order_book_id] = row
        summary_reasons = _dedupe((
            *parse_blocked,
            *parse_partial,
            *identity_blocked,
            *identity_partial,
        ))
        blocked_reasons.extend(parse_blocked)
        blocked_reasons.extend(identity_blocked)
        partial_reasons.extend(parse_partial)
        partial_reasons.extend(identity_partial)
        call_summaries.append(RqdataPocCallSummary(
            method=method,
            order_book_id=single_order_book_id,
            response_digest=digest,
            row_count=len(rows),
            reasons=summary_reasons,
        ))
        if summary_reasons:
            break

    records, record_blocked, record_partial = _complete_records(
        query,
        rows_by_method,
        fetched_at=fetched_at,
    )
    blocked_reasons.extend(record_blocked)
    partial_reasons.extend(record_partial)
    if not blocked_reasons and not query.include_dynamic_snapshot:
        partial_reasons.append("rqdata_dynamic_snapshot_not_run")
    usable_row_count = sum(
        item.row_count for item in call_summaries
    )
    if is_live_transport and call_summaries and usable_row_count == 0:
        partial_reasons.append("rqdata_no_usable_response_data")

    blocked = _dedupe(blocked_reasons)
    partial = _dedupe(partial_reasons)
    if blocked:
        status = RqdataPocStatus.BLOCKED
        real_poc_status = (
            "failed" if is_live_transport else "not_run"
        )
    elif partial:
        status = RqdataPocStatus.PARTIAL
        if not is_live_transport:
            real_poc_status = "not_run"
        elif usable_row_count == 0:
            real_poc_status = "no_data"
        else:
            real_poc_status = "completed"
    else:
        status = RqdataPocStatus.FIELD_CANDIDATE
        real_poc_status = "completed"
    return RqdataTradabilityPocReport(
        status=status,
        real_poc_status=real_poc_status,
        as_of=query.as_of,
        trading_date=query.trading_date,
        expected_count=len(query.expected_order_book_ids),
        records=records,
        call_summaries=tuple(call_summaries),
        field_coverage=_field_coverage(records),
        reasons=_dedupe((*blocked, *partial)),
    )
