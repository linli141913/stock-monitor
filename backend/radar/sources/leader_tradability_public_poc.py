"""阶段6L-C4C免费组合源可交易性字段级POC。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from radar.contracts import QuoteSnapshot, QuoteTradingStatus
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_tradability_features import (
    LeaderSecurityLifecycleEvidence,
    LeaderTradabilityFeatureInput,
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import (
    PRICE_TOLERANCE,
    OfficialDailyTradabilityReference,
    PriceLimitSpecialSession,
    TradabilitySourceGrade,
    build_leader_tradability_source_input,
    resolve_trading_rule_catalog,
)


PUBLIC_COMPOSITE_TRADABILITY_CONTRACT_ID = (
    "radar-leader-tradability-public-composite-poc-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
MAXIMUM_DYNAMIC_SOURCE_AGE_SECONDS = 90
MAXIMUM_COMPOSITE_SCOPE_COUNT = 6000
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN = re.compile(
    r"[036][0-9]{5}"
)
_PUBLIC_AGGREGATOR_UPSTREAM_HOSTS = {
    "www.szse.cn",
    "www.sse.com.cn",
    "data.eastmoney.com",
    "quote.eastmoney.com",
    "push2.eastmoney.com",
    "hq.sinajs.cn",
}
UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PublicCompositePocStatus(str, Enum):
    NOT_RUN = "not_run"
    NO_DATA = "no_data"
    FIELD_CANDIDATE = "field_candidate"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class PublicSourceKind(str, Enum):
    EXCHANGE_OFFICIAL = "exchange_official"
    PUBLIC_AGGREGATOR = "public_aggregator"
    TENCENT_QUOTE = "tencent_quote"


@dataclass(frozen=True)
class _PublicSourceContract:
    source_kind: PublicSourceKind
    source_name: str
    allowed_hosts: Tuple[str, ...]
    exchanges: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    allowed_fields: Tuple[str, ...] = ()
    dynamic_fields: Tuple[str, ...] = ()


_PUBLIC_SOURCE_CONTRACTS = {
    "szse-public-security-list-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="深圳证券交易所",
        allowed_hosts=("www.szse.cn",),
        exchanges=("szse",),
        capabilities=("security_identity",),
    ),
    "szse-public-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="深圳证券交易所",
        allowed_hosts=("www.szse.cn",),
        exchanges=("szse",),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
            "price_limit_mode",
            "upper_limit_price",
            "lower_limit_price",
        ),
        dynamic_fields=("trading_status",),
    ),
    "szse-public-announcement-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="深圳证券交易所",
        allowed_hosts=("www.szse.cn",),
        exchanges=("szse",),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
        ),
    ),
    "sse-public-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="上海证券交易所",
        allowed_hosts=("www.sse.com.cn", "yunhq.sse.com.cn"),
        exchanges=("sse",),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
            "price_limit_mode",
            "upper_limit_price",
            "lower_limit_price",
        ),
        dynamic_fields=("trading_status",),
    ),
    "sse-public-security-list-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="上海证券交易所",
        allowed_hosts=("www.sse.com.cn", "query.sse.com.cn"),
        exchanges=("sse",),
        capabilities=("security_identity",),
    ),
    "sse-public-announcement-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="上海证券交易所",
        allowed_hosts=("www.sse.com.cn",),
        exchanges=("sse",),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
        ),
    ),
    "akshare-public-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
        source_name="AKShare公开数据聚合",
        allowed_hosts=("akshare.akfamily.xyz",),
        exchanges=("sse", "szse"),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
        ),
        dynamic_fields=("trading_status",),
    ),
    "akshare-public-static-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
        source_name="AKShare公开数据聚合",
        allowed_hosts=("akshare.akfamily.xyz",),
        exchanges=("sse", "szse"),
        capabilities=("tradability_observation",),
        allowed_fields=(
            "lifecycle_status",
            "trading_status",
            "special_session",
        ),
    ),
    "eastmoney-public-static-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
        source_name="东方财富公开数据聚合",
        allowed_hosts=("quote.eastmoney.com",),
        exchanges=("sse", "szse"),
        capabilities=("tradability_observation",),
        allowed_fields=("lifecycle_status",),
    ),
    "sina-public-quote-status-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
        source_name="新浪财经公开行情",
        allowed_hosts=("finance.sina.com.cn",),
        exchanges=("sse", "szse"),
        capabilities=("tradability_observation",),
        allowed_fields=("lifecycle_status", "trading_status"),
        dynamic_fields=("trading_status",),
    ),
    "szse-public-trading-calendar-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="深圳证券交易所",
        allowed_hosts=("www.szse.cn",),
        exchanges=("szse",),
        capabilities=("trading_calendar",),
    ),
    "sse-public-trading-calendar-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="上海证券交易所",
        allowed_hosts=("www.sse.com.cn",),
        exchanges=("sse",),
        capabilities=("trading_calendar",),
    ),
    "sse-a-share-trading-calendar-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
        source_name="上海证券交易所",
        allowed_hosts=("www.sse.com.cn",),
        exchanges=("sse", "szse"),
        capabilities=("trading_calendar",),
    ),
    "tencent-quote-snapshot-v1": _PublicSourceContract(
        source_kind=PublicSourceKind.TENCENT_QUOTE,
        source_name="腾讯财经",
        allowed_hosts=("qt.gtimg.cn",),
        exchanges=("sse", "szse"),
        capabilities=("quote_batch",),
    ),
}


@dataclass(frozen=True)
class PublicSecurityContext:
    symbol: str
    exchange: str
    board: str
    listing_date: Optional[date] = None
    identity_source_contract_id: str = ""
    identity_source_name: str = ""
    identity_source_url: str = ""
    identity_document_id: str = ""
    identity_source_time: Optional[datetime] = None
    identity_fetched_at: Optional[datetime] = None
    identity_content_sha256: str = ""


@dataclass(frozen=True, repr=False)
class PublicTradingCalendarEvidence:
    exchange: str
    trading_dates: Tuple[date, ...]
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str
    source_time: datetime
    fetched_at: datetime
    content_sha256: str


@dataclass(frozen=True, repr=False)
class PublicQuoteBatchEvidence:
    source_contract_id: str
    source_name: str
    source_url: str
    batch_id: str
    content_sha256: str


@dataclass(frozen=True, repr=False)
class PublicTradabilityObservation:
    symbol: str
    exchange: str
    board: str
    trading_date: date
    source_kind: PublicSourceKind
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str
    source_time: datetime
    fetched_at: datetime
    content_sha256: str
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None
    upstream_source_name: Optional[str] = None
    upstream_source_url: Optional[str] = None
    upstream_document_id: Optional[str] = None
    upstream_source_time: Optional[datetime] = None
    lifecycle_status: Optional[SecurityLifecycleStatus] = None
    trading_status: Optional[TradingSessionStatus] = None
    special_session: Optional[PriceLimitSpecialSession] = None
    price_limit_mode: Optional[PriceLimitMode] = None
    upper_limit_price: Optional[float] = None
    lower_limit_price: Optional[float] = None

    def __repr__(self) -> str:
        kind = (
            self.source_kind.value
            if isinstance(self.source_kind, PublicSourceKind)
            else "invalid"
        )
        return (
            "PublicTradabilityObservation("
            f"symbol={self.symbol!r}, source_kind={kind!r})"
        )


@dataclass(frozen=True)
class PublicCompositeTradabilityQuery:
    trading_date: date
    as_of: datetime
    securities: Tuple[PublicSecurityContext, ...]
    trading_calendars: Tuple[PublicTradingCalendarEvidence, ...] = ()
    quote_batch_evidence: Optional[PublicQuoteBatchEvidence] = None


@dataclass(frozen=True)
class PublicFieldSourceEvidence:
    source_contract_id: str
    source_kind: str
    source_time: Optional[datetime]
    fetched_at: Optional[datetime]
    content_sha256: Optional[str]
    freshness: str
    observed_value: Any
    upstream_source_name: Optional[str] = None
    upstream_document_id: Optional[str] = None
    upstream_source_time: Optional[datetime] = None


@dataclass(frozen=True)
class PublicFieldEvidence:
    field_name: str
    resolution: str
    sources: Tuple[PublicFieldSourceEvidence, ...]

    @property
    def source_contract_ids(self) -> Tuple[str, ...]:
        return tuple(item.source_contract_id for item in self.sources)


@dataclass(frozen=True)
class PublicCompositeTradabilityRecord:
    symbol: str
    lifecycle_status: Optional[SecurityLifecycleStatus]
    trading_status: Optional[TradingSessionStatus]
    special_session: Optional[PriceLimitSpecialSession]
    price_limit_mode: Optional[PriceLimitMode]
    upper_limit_price: Optional[float]
    lower_limit_price: Optional[float]
    field_evidence: Tuple[PublicFieldEvidence, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicCompositeTradabilityReport:
    fixture_resolution_status: PublicCompositePocStatus
    expected_count: int
    returned_count: int
    field_coverage: Mapping[str, float]
    reasons: Tuple[str, ...] = ()
    records: Tuple[PublicCompositeTradabilityRecord, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    formal_score_ready: bool = field(default=False, init=False)
    formal_gate_ready: bool = field(default=False, init=False)
    formal_usable: bool = field(default=False, init=False)
    state_transition_allowed: bool = field(default=False, init=False)
    real_poc_status: str = field(default="not_run", init=False)
    status: PublicCompositePocStatus = field(
        default=PublicCompositePocStatus.NOT_RUN,
        init=False,
    )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "contractId": PUBLIC_COMPOSITE_TRADABILITY_CONTRACT_ID,
            "status": self.status.value,
            "fixtureResolutionStatus": (
                self.fixture_resolution_status.value
            ),
            "realPocStatus": self.real_poc_status,
            "expectedCount": self.expected_count,
            "returnedCount": self.returned_count,
            "fieldCoverage": dict(self.field_coverage),
            "reasons": list(self.reasons),
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


@dataclass(frozen=True)
class PublicTradabilityRuntimeInputResult:
    status: ResearchFeatureStatus
    inputs: Tuple[LeaderTradabilityFeatureInput, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderTradabilityFeatureInput]:
        return MappingProxyType({
            item.quote.symbol: item for item in self.inputs
        })

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "inputCount": len(self.inputs),
            "symbols": [item.quote.symbol for item in self.inputs],
            "reasons": list(self.reasons),
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


def _unique_reasons(reasons: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _shanghai_date(value: Any) -> Optional[date]:
    if _aware_utc(value) is None:
        return None
    return value.astimezone(SHANGHAI_TZ).date()


def _safe_source_url(value: Any) -> bool:
    parts = urlsplit(str(value or "").strip())
    return all((
        parts.scheme == "https",
        bool(parts.netloc),
        not parts.username,
        not parts.password,
        not parts.query,
        not parts.fragment,
    ))


def _source_contract_matches(
    *,
    observation: PublicTradabilityObservation,
    expected_kind: PublicSourceKind,
) -> bool:
    contract = _PUBLIC_SOURCE_CONTRACTS.get(
        str(observation.source_contract_id or "").strip()
    )
    parts = urlsplit(str(observation.source_url or "").strip())
    return bool(
        contract is not None
        and contract.source_kind == expected_kind
        and "tradability_observation" in contract.capabilities
        and observation.source_kind == expected_kind
        and str(observation.source_name or "").strip()
        == contract.source_name
        and str(parts.hostname or "").lower()
        in contract.allowed_hosts
        and str(observation.exchange or "").strip().lower()
        in contract.exchanges
    )


def _valid_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _security_context_contract_matches(
    context: PublicSecurityContext,
) -> bool:
    contract = _PUBLIC_SOURCE_CONTRACTS.get(
        str(context.identity_source_contract_id or "").strip()
    )
    parts = urlsplit(str(context.identity_source_url or "").strip())
    return bool(
        contract is not None
        and contract.source_kind == PublicSourceKind.EXCHANGE_OFFICIAL
        and "security_identity" in contract.capabilities
        and str(context.identity_source_name or "").strip()
        == contract.source_name
        and str(parts.hostname or "").lower()
        in contract.allowed_hosts
        and str(context.exchange or "").strip().lower()
        in contract.exchanges
    )


def _calendar_contract_matches(
    calendar: PublicTradingCalendarEvidence,
) -> bool:
    contract = _PUBLIC_SOURCE_CONTRACTS.get(
        str(calendar.source_contract_id or "").strip()
    )
    parts = urlsplit(str(calendar.source_url or "").strip())
    return bool(
        contract is not None
        and contract.source_kind == PublicSourceKind.EXCHANGE_OFFICIAL
        and "trading_calendar" in contract.capabilities
        and str(calendar.source_name or "").strip()
        == contract.source_name
        and str(parts.hostname or "").lower()
        in contract.allowed_hosts
        and str(calendar.exchange or "").strip().lower()
        in contract.exchanges
    )


def _calendar_reasons(
    query: PublicCompositeTradabilityQuery,
) -> Tuple[str, ...]:
    reasons = []
    expected_exchanges = {
        str(item.exchange or "").strip().lower()
        for item in query.securities
    }
    exchanges = [
        str(item.exchange or "").strip().lower()
        for item in query.trading_calendars
    ]
    if len(exchanges) != len(set(exchanges)):
        reasons.append("public_query_duplicate_calendar")
    if set(exchanges) != expected_exchanges:
        reasons.append("public_query_calendar_coverage_mismatch")
    as_of_utc = _aware_utc(query.as_of)
    for item in query.trading_calendars:
        dates = item.trading_dates
        if (
            len(dates) != 6
            or any(
                not isinstance(value, date)
                or isinstance(value, datetime)
                for value in dates
            )
            or tuple(sorted(set(dates))) != dates
            or any(value.weekday() >= 5 for value in dates)
            or not dates
            or dates[-1] != query.trading_date
        ):
            reasons.append("public_query_calendar_window_invalid")
        if not all((
            _valid_text(item.source_contract_id),
            _valid_text(item.source_name),
            _valid_text(item.document_id),
        )):
            reasons.append("public_query_calendar_source_missing")
        if not _safe_source_url(item.source_url):
            reasons.append("public_query_calendar_url_sensitive")
        elif not _calendar_contract_matches(item):
            reasons.append("public_query_calendar_contract_mismatch")
        if not _SHA256_PATTERN.fullmatch(
            str(item.content_sha256 or "").strip().lower()
        ):
            reasons.append("public_query_calendar_digest_invalid")
        source_time = _aware_utc(item.source_time)
        fetched_at = _aware_utc(item.fetched_at)
        if source_time is None or fetched_at is None:
            reasons.append("public_query_calendar_timezone_missing")
        elif as_of_utc is not None and (
            source_time
            > as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
            or fetched_at
            > as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
        ):
            reasons.append("public_query_calendar_future_time")
        elif fetched_at < source_time:
            reasons.append("public_query_calendar_fetch_before_source")
    return _unique_reasons(reasons)


def _listed_trading_day_count(
    *,
    context: PublicSecurityContext,
    calendar: PublicTradingCalendarEvidence,
) -> Optional[int]:
    if not isinstance(context.listing_date, date):
        return None
    dates = calendar.trading_dates
    if len(dates) != 6:
        return None
    if context.listing_date < dates[0]:
        return 6
    if context.listing_date not in dates:
        return None
    return sum(value >= context.listing_date for value in dates)


def _query_reasons(
    query: PublicCompositeTradabilityQuery,
) -> Tuple[str, ...]:
    reasons = []
    if (
        not isinstance(query.trading_date, date)
        or isinstance(query.trading_date, datetime)
    ):
        reasons.append("public_query_trading_date_invalid")
    as_of_utc = _aware_utc(query.as_of)
    if as_of_utc is None:
        reasons.append("public_query_as_of_timezone_missing")
    elif _shanghai_date(query.as_of) != query.trading_date:
        reasons.append("public_query_as_of_date_mismatch")
    if not 1 <= len(query.securities) <= MAXIMUM_COMPOSITE_SCOPE_COUNT:
        reasons.append("public_query_sample_count_invalid")

    symbols = []
    for item in query.securities:
        symbol = str(item.symbol or "").strip()
        symbols.append(symbol)
        if not (len(symbol) == 6 and symbol.isdigit()):
            reasons.append("public_query_symbol_invalid")
        elif STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(
            symbol
        ) is None:
            reasons.append("public_query_symbol_out_of_scope")
        if str(item.exchange or "").strip().lower() not in {
            "sse", "szse"
        }:
            reasons.append("public_query_exchange_invalid")
        if not _valid_text(item.board):
            reasons.append("public_query_board_missing")
        if (
            not isinstance(item.listing_date, date)
            or isinstance(item.listing_date, datetime)
            or not isinstance(query.trading_date, date)
            or isinstance(query.trading_date, datetime)
            or item.listing_date > query.trading_date
        ):
            reasons.append("public_query_listing_date_invalid")
        if not all((
            _valid_text(item.identity_source_contract_id),
            _valid_text(item.identity_source_name),
            _valid_text(item.identity_document_id),
        )):
            reasons.append("public_query_identity_source_missing")
        if not _safe_source_url(item.identity_source_url):
            reasons.append("public_query_identity_url_sensitive")
        elif not _security_context_contract_matches(item):
            reasons.append("public_query_identity_contract_mismatch")
        if not _SHA256_PATTERN.fullmatch(
            str(item.identity_content_sha256 or "").strip().lower()
        ):
            reasons.append("public_query_identity_digest_invalid")
        raw_identity_source_time = item.identity_source_time
        identity_source_time = _aware_utc(raw_identity_source_time)
        identity_fetched_at = _aware_utc(item.identity_fetched_at)
        if (
            identity_fetched_at is None
            or raw_identity_source_time is not None
            and identity_source_time is None
        ):
            reasons.append("public_query_identity_timezone_missing")
        elif as_of_utc is not None and (
            identity_source_time is not None
            and identity_source_time
            > as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
            or identity_fetched_at
            > as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
        ):
            reasons.append("public_query_identity_future_time")
        elif (
            identity_source_time is not None
            and identity_fetched_at < identity_source_time
        ):
            reasons.append("public_query_identity_fetch_before_source")
    if len(symbols) != len(set(symbols)):
        reasons.append("public_query_duplicate_symbol")
    reasons.extend(_calendar_reasons(query))
    calendars_by_exchange = {
        str(item.exchange or "").strip().lower(): item
        for item in query.trading_calendars
    }
    for item in query.securities:
        calendar = calendars_by_exchange.get(
            str(item.exchange or "").strip().lower()
        )
        if calendar is not None and _listed_trading_day_count(
            context=item,
            calendar=calendar,
        ) is None:
            reasons.append("public_query_listing_calendar_mismatch")
    return _unique_reasons(reasons)


def _observation_reasons(
    *,
    query: PublicCompositeTradabilityQuery,
    observations: Tuple[PublicTradabilityObservation, ...],
    expected_kind: PublicSourceKind,
) -> Tuple[str, ...]:
    reasons = []
    expected = {
        item.symbol: item
        for item in query.securities
    }
    seen = set()
    seen_symbols = set()
    as_of_utc = _aware_utc(query.as_of)
    maximum_time = (
        as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
        if as_of_utc is not None
        else None
    )
    for item in observations:
        key = (item.symbol, item.source_contract_id)
        if key in seen:
            reasons.append("public_batch_duplicate_observation")
        seen.add(key)
        if item.symbol in seen_symbols:
            reasons.append("public_batch_multiple_kind_observations")
        seen_symbols.add(item.symbol)
        context = expected.get(item.symbol)
        if context is None:
            reasons.append("public_batch_extra_symbol")
        elif (
            str(item.exchange or "").strip().lower()
            != str(context.exchange or "").strip().lower()
            or str(item.board or "").strip()
            != str(context.board or "").strip()
        ):
            reasons.append("public_batch_security_identity_mismatch")
        if item.trading_date != query.trading_date:
            reasons.append("public_batch_trading_date_mismatch")
        if item.source_kind != expected_kind:
            reasons.append("public_source_kind_mismatch")
        if not all((
            _valid_text(item.source_contract_id),
            _valid_text(item.source_name),
            _valid_text(item.document_id),
        )):
            reasons.append("public_source_identity_missing")
        if not _safe_source_url(item.source_url):
            reasons.append("public_source_url_sensitive")
        elif not _source_contract_matches(
            observation=item,
            expected_kind=expected_kind,
        ):
            reasons.append("public_source_contract_mismatch")
        contract = _PUBLIC_SOURCE_CONTRACTS.get(
            str(item.source_contract_id or "").strip()
        )
        if contract is not None:
            observed_fields = {
                field_name
                for field_name in (
                    "lifecycle_status",
                    "trading_status",
                    "special_session",
                    "price_limit_mode",
                    "upper_limit_price",
                    "lower_limit_price",
                )
                if getattr(item, field_name) is not None
            }
            if not observed_fields.issubset(contract.allowed_fields):
                reasons.append("public_source_field_not_allowed")
        if not _SHA256_PATTERN.fullmatch(
            str(item.content_sha256 or "").strip().lower()
        ):
            reasons.append("public_source_content_digest_invalid")
        source_time = _aware_utc(item.source_time)
        fetched_at = _aware_utc(item.fetched_at)
        if source_time is None or fetched_at is None:
            reasons.append("public_source_timezone_missing")
        elif maximum_time is not None and (
            source_time > maximum_time
            or fetched_at > maximum_time
        ):
            reasons.append("public_source_future_time")
        elif fetched_at + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        ) < source_time:
            reasons.append("public_source_fetch_before_source")
        effective_from = item.effective_from
        effective_until = item.effective_until
        if (
            not isinstance(effective_from, date)
            or isinstance(effective_from, datetime)
            or not isinstance(effective_until, date)
            or isinstance(effective_until, datetime)
        ):
            reasons.append("public_source_effective_window_missing")
        elif effective_until < effective_from:
            reasons.append("public_source_effective_window_invalid")
        elif not (
            effective_from <= query.trading_date <= effective_until
        ):
            reasons.append("public_source_effective_window_inapplicable")
        upstream_values = (
            item.upstream_source_name,
            item.upstream_source_url,
            item.upstream_document_id,
            item.upstream_source_time,
        )
        if expected_kind == PublicSourceKind.PUBLIC_AGGREGATOR:
            if not all((
                _valid_text(item.upstream_source_name),
                _valid_text(item.upstream_document_id),
                item.upstream_source_time is not None,
            )):
                reasons.append("public_aggregator_upstream_missing")
            if not _safe_source_url(item.upstream_source_url):
                reasons.append("public_aggregator_upstream_url_sensitive")
            elif str(urlsplit(
                str(item.upstream_source_url)
            ).hostname or "").lower() not in (
                _PUBLIC_AGGREGATOR_UPSTREAM_HOSTS
            ):
                reasons.append("public_aggregator_upstream_untrusted")
            upstream_source_time = _aware_utc(
                item.upstream_source_time
            )
            if upstream_source_time is None:
                reasons.append("public_aggregator_upstream_timezone_missing")
            elif (
                maximum_time is not None
                and upstream_source_time > maximum_time
            ):
                reasons.append("public_aggregator_upstream_future_time")
            elif (
                source_time is not None
                and upstream_source_time > source_time
            ):
                reasons.append("public_aggregator_upstream_after_wrapper")
            elif (
                fetched_at is not None
                and upstream_source_time > fetched_at
            ):
                reasons.append("public_aggregator_upstream_after_fetch")
        elif any(value is not None for value in upstream_values):
            reasons.append("public_official_upstream_unexpected")
        if (
            item.lifecycle_status is not None
            and not isinstance(
                item.lifecycle_status,
                SecurityLifecycleStatus,
            )
        ):
            reasons.append("public_lifecycle_status_invalid")
        if (
            item.trading_status is not None
            and not isinstance(
                item.trading_status,
                TradingSessionStatus,
            )
        ):
            reasons.append("public_trading_status_invalid")
        if (
            item.special_session is not None
            and not isinstance(
                item.special_session,
                PriceLimitSpecialSession,
            )
        ):
            reasons.append("public_special_session_invalid")
        if (
            item.price_limit_mode is not None
            and not isinstance(item.price_limit_mode, PriceLimitMode)
        ):
            reasons.append("public_price_limit_mode_invalid")
        prices = (item.lower_limit_price, item.upper_limit_price)
        provided_prices = tuple(value for value in prices if value is not None)
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in provided_prices
        ):
            reasons.append("public_price_limit_value_invalid")
        if len(provided_prices) == 1:
            reasons.append("public_price_limit_pair_incomplete")
        if len(provided_prices) == 2 and not prices[0] < prices[1]:
            reasons.append("public_price_limit_order_invalid")
        if (
            item.price_limit_mode == PriceLimitMode.NO_LIMIT
            and provided_prices
        ):
            reasons.append("public_no_limit_prices_present")
    return _unique_reasons(reasons)


def _quote_batch_contract_matches(
    evidence: PublicQuoteBatchEvidence,
) -> bool:
    contract = _PUBLIC_SOURCE_CONTRACTS.get(
        str(evidence.source_contract_id or "").strip()
    )
    parts = urlsplit(str(evidence.source_url or "").strip())
    return bool(
        contract is not None
        and contract.source_kind == PublicSourceKind.TENCENT_QUOTE
        and "quote_batch" in contract.capabilities
        and str(evidence.source_name or "").strip()
        == contract.source_name
        and str(parts.hostname or "").lower()
        in contract.allowed_hosts
    )


def quote_batch_content_sha256(
    *,
    batch_id: str,
    quotes: Iterable[QuoteSnapshot],
) -> str:
    records = [
        quote.model_dump(mode="json", by_alias=True)
        for quote in quotes
    ]
    records.sort(key=lambda item: str(item.get("symbol") or ""))
    payload = json.dumps(
        {"batchId": str(batch_id or "").strip(), "records": records},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _quote_reasons(
    *,
    query: PublicCompositeTradabilityQuery,
    quotes: Tuple[QuoteSnapshot, ...],
) -> Tuple[str, ...]:
    reasons = []
    evidence = query.quote_batch_evidence
    if quotes and evidence is None:
        reasons.append("public_quote_batch_evidence_missing")
    if evidence is not None:
        if not all((
            _valid_text(evidence.source_contract_id),
            _valid_text(evidence.source_name),
            _valid_text(evidence.batch_id),
        )):
            reasons.append("public_quote_batch_identity_missing")
        if not _safe_source_url(evidence.source_url):
            reasons.append("public_quote_batch_url_sensitive")
        elif not _quote_batch_contract_matches(evidence):
            reasons.append("public_quote_batch_contract_mismatch")
        if not _SHA256_PATTERN.fullmatch(
            str(evidence.content_sha256 or "").strip().lower()
        ):
            reasons.append("public_quote_batch_digest_invalid")
        elif quotes and evidence.content_sha256 != quote_batch_content_sha256(
            batch_id=evidence.batch_id,
            quotes=quotes,
        ):
            reasons.append("public_quote_batch_digest_mismatch")
    as_of_utc = _aware_utc(query.as_of)
    maximum_time = (
        as_of_utc + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
        if as_of_utc is not None
        else None
    )
    for quote in quotes:
        if str(quote.source or "").strip() != "tencent_finance":
            reasons.append("public_quote_source_mismatch")
        source_time = _aware_utc(quote.source_time)
        fetched_at = _aware_utc(quote.fetched_at)
        if source_time is not None and (
            _shanghai_date(quote.source_time) != query.trading_date
        ):
            reasons.append("public_quote_trading_date_mismatch")
        if maximum_time is not None and (
            (source_time is not None and source_time > maximum_time)
            or (fetched_at is not None and fetched_at > maximum_time)
        ):
            reasons.append("public_quote_future_time")
        if (
            source_time is not None
            and fetched_at is not None
            and fetched_at + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            ) < source_time
        ):
            reasons.append("public_quote_fetch_before_source")
    return _unique_reasons(reasons)


def _price_matches(
    value: Optional[float],
    target: Optional[float],
) -> bool:
    if value is None or target is None:
        return False
    return abs(value - target) <= float(PRICE_TOLERANCE)


def _resolved_value(
    *,
    official: Optional[PublicTradabilityObservation],
    aggregator: Optional[PublicTradabilityObservation],
    field_name: str,
    conflict_reason: str,
    reasons: list[str],
    as_of: datetime,
):
    def eligible_value(
        observation: Optional[PublicTradabilityObservation],
    ):
        if observation is None:
            return None
        value = getattr(observation, field_name)
        if value in {
            SecurityLifecycleStatus.UNKNOWN,
            TradingSessionStatus.UNKNOWN,
            PriceLimitMode.UNKNOWN,
        }:
            reasons.append(f"public_{field_name}_unknown")
            return None
        if (
            field_name == "trading_status"
            and _requires_dynamic_freshness(
                observation,
                field_name,
            )
            and value is not None
            and _dynamic_age_reason(
                source_time=_observation_source_time(observation),
                as_of=as_of,
                missing_reason="missing",
                stale_reason="stale",
            ) is not None
        ):
            return None
        return value

    official_value = eligible_value(official)
    aggregator_value = eligible_value(aggregator)
    if (
        official_value is not None
        and aggregator_value is not None
        and official_value != aggregator_value
    ):
        reasons.append(conflict_reason)
    return official_value


def _dynamic_age_reason(
    *,
    source_time: Optional[datetime],
    as_of: datetime,
    missing_reason: str,
    stale_reason: str,
) -> Optional[str]:
    source_utc = _aware_utc(source_time)
    as_of_utc = _aware_utc(as_of)
    if source_utc is None or as_of_utc is None:
        return missing_reason
    if _shanghai_date(source_time) != _shanghai_date(as_of):
        return stale_reason
    source_local = source_time.astimezone(SHANGHAI_TZ)
    as_of_local = as_of.astimezone(SHANGHAI_TZ)
    lunch_start = datetime.combine(
        as_of_local.date(),
        time(11, 30),
        tzinfo=SHANGHAI_TZ,
    )
    lunch_end = datetime.combine(
        as_of_local.date(),
        time(13, 0),
        tzinfo=SHANGHAI_TZ,
    )
    if (
        lunch_start <= as_of_local < lunch_end
        and source_local >= lunch_start - timedelta(
            seconds=MAXIMUM_DYNAMIC_SOURCE_AGE_SECONDS
        )
    ):
        return None
    if (
        as_of_utc - source_utc
    ).total_seconds() > MAXIMUM_DYNAMIC_SOURCE_AGE_SECONDS:
        return stale_reason
    return None


def _observation_source_time(
    observation: PublicTradabilityObservation,
) -> Optional[datetime]:
    if observation.source_kind == PublicSourceKind.PUBLIC_AGGREGATOR:
        return observation.upstream_source_time
    return observation.source_time


def _requires_dynamic_freshness(
    observation: PublicTradabilityObservation,
    field_name: str,
) -> bool:
    contract = _PUBLIC_SOURCE_CONTRACTS.get(
        str(observation.source_contract_id or "").strip()
    )
    return bool(
        contract is not None
        and field_name in contract.dynamic_fields
    )


def _quote_status(
    quote: QuoteSnapshot,
) -> Optional[TradingSessionStatus]:
    if quote.trading_status == QuoteTradingStatus.SUSPENDED:
        return TradingSessionStatus.SUSPENDED
    if quote.trading_status in {
        QuoteTradingStatus.DELISTED,
        QuoteTradingStatus.UNLISTED,
    }:
        return TradingSessionStatus.ABNORMAL
    return None


def _quote_lifecycle(
    quote: QuoteSnapshot,
) -> Optional[SecurityLifecycleStatus]:
    if quote.trading_status == QuoteTradingStatus.DELISTED:
        return SecurityLifecycleStatus.DELISTING
    if quote.trading_status == QuoteTradingStatus.UNLISTED:
        return SecurityLifecycleStatus.ABNORMAL
    return None


def _quote_special_session(
    quote: QuoteSnapshot,
    *,
    listed_trading_day_count: int,
    lifecycle_status: Optional[SecurityLifecycleStatus],
) -> Optional[PriceLimitSpecialSession]:
    upper = quote.upper_limit_price_source
    lower = quote.lower_limit_price_source
    if (
        listed_trading_day_count <= 5
        or lifecycle_status == SecurityLifecycleStatus.DELISTING
        or not isinstance(upper, (int, float))
        or isinstance(upper, bool)
        or not isinstance(lower, (int, float))
        or isinstance(lower, bool)
        or not math.isfinite(float(upper))
        or not math.isfinite(float(lower))
        or float(upper) <= float(lower)
        or float(lower) <= 0
    ):
        return None
    return PriceLimitSpecialSession.NONE


def _field_coverage(
    records: Tuple[PublicCompositeTradabilityRecord, ...],
) -> Mapping[str, float]:
    names = (
        "lifecycleStatus",
        "tradingStatus",
        "specialSession",
        "priceLimitMode",
        "upperLimitPrice",
        "lowerLimitPrice",
    )
    if not records:
        return {name: 0.0 for name in names}
    result = {}
    for name in names:
        attribute = {
            "lifecycleStatus": "lifecycle_status",
            "tradingStatus": "trading_status",
            "specialSession": "special_session",
            "priceLimitMode": "price_limit_mode",
            "upperLimitPrice": "upper_limit_price",
            "lowerLimitPrice": "lower_limit_price",
        }[name]
        satisfied = 0
        for record in records:
            value = getattr(record, attribute)
            if value is not None or (
                attribute in {
                    "upper_limit_price", "lower_limit_price"
                }
                and record.price_limit_mode == PriceLimitMode.NO_LIMIT
            ):
                satisfied += 1
        result[name] = satisfied / len(records)
    return result


def _audit_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _field_evidence(
    *,
    official: Optional[PublicTradabilityObservation],
    aggregator: Optional[PublicTradabilityObservation],
    quote: Optional[QuoteSnapshot],
    quote_batch_evidence: Optional[PublicQuoteBatchEvidence],
    quote_current: bool,
    quote_special_session: Optional[PriceLimitSpecialSession],
    catalog_rule: Any,
    as_of: datetime,
    resolved_values: Mapping[str, Any],
    reasons: Iterable[str],
) -> Tuple[PublicFieldEvidence, ...]:
    conflict_reasons = {
        "lifecycle_status": {"public_lifecycle_status_conflict"},
        "trading_status": {"public_trading_status_conflict"},
        "special_session": {"public_special_session_conflict"},
        "price_limit_mode": {
            "public_price_limit_mode_conflict",
            "public_no_limit_quote_prices_present",
        },
        "upper_limit_price": {
            "public_price_limit_source_conflict",
            "public_price_limit_rule_conflict",
            "public_quote_price_limit_pair_incomplete",
            "public_no_limit_quote_prices_present",
        },
        "lower_limit_price": {
            "public_price_limit_source_conflict",
            "public_price_limit_rule_conflict",
            "public_quote_price_limit_pair_incomplete",
            "public_no_limit_quote_prices_present",
        },
    }
    reason_set = set(reasons)
    result = []
    for field_name, resolved_value in resolved_values.items():
        sources = []
        for observation in (official, aggregator):
            if (
                observation is not None
                and getattr(observation, field_name) is not None
            ):
                freshness = "effective"
                if (
                    field_name == "trading_status"
                    and _requires_dynamic_freshness(
                        observation,
                        field_name,
                    )
                ):
                    freshness = (
                        "current"
                        if _dynamic_age_reason(
                            source_time=_observation_source_time(
                                observation
                            ),
                            as_of=as_of,
                            missing_reason="missing",
                            stale_reason="stale",
                        ) is None
                        else "stale"
                    )
                sources.append(PublicFieldSourceEvidence(
                    source_contract_id=(
                        observation.source_contract_id
                    ),
                    source_kind=observation.source_kind.value,
                    source_time=observation.source_time,
                    fetched_at=observation.fetched_at,
                    content_sha256=observation.content_sha256,
                    freshness=freshness,
                    observed_value=_audit_value(
                        getattr(observation, field_name)
                    ),
                    upstream_source_name=(
                        observation.upstream_source_name
                    ),
                    upstream_document_id=(
                        observation.upstream_document_id
                    ),
                    upstream_source_time=(
                        observation.upstream_source_time
                    ),
                ))

        quote_has_field = False
        if quote is not None:
            if field_name == "lifecycle_status":
                quote_has_field = _quote_lifecycle(quote) is not None
            elif field_name == "trading_status":
                quote_has_field = _quote_status(quote) is not None
            elif field_name == "special_session":
                quote_has_field = quote_special_session is not None
            elif field_name == "upper_limit_price":
                quote_has_field = (
                    quote.upper_limit_price_source is not None
                )
            elif field_name == "lower_limit_price":
                quote_has_field = (
                    quote.lower_limit_price_source is not None
                )
        if quote_has_field:
            sources.append(PublicFieldSourceEvidence(
                source_contract_id=(
                    quote_batch_evidence.source_contract_id
                    if quote_batch_evidence is not None
                    else "quote-batch-missing"
                ),
                source_kind="tencent_quote",
                source_time=quote.source_time,
                fetched_at=quote.fetched_at,
                content_sha256=(
                    quote_batch_evidence.content_sha256
                    if quote_batch_evidence is not None
                    else None
                ),
                freshness="current" if quote_current else "stale",
                observed_value=_audit_value({
                    "lifecycle_status": _quote_lifecycle(quote),
                    "trading_status": _quote_status(quote),
                    "special_session": quote_special_session,
                    "upper_limit_price": (
                        quote.upper_limit_price_source
                    ),
                    "lower_limit_price": (
                        quote.lower_limit_price_source
                    ),
                }[field_name]),
            ))
        catalog_value = None
        if catalog_rule is not None:
            catalog_value = {
                "price_limit_mode": catalog_rule.price_limit_mode,
                "upper_limit_price": (
                    catalog_rule.expected_upper_limit_price
                ),
                "lower_limit_price": (
                    catalog_rule.expected_lower_limit_price
                ),
            }.get(field_name)
        if catalog_value is not None:
            sources.append(PublicFieldSourceEvidence(
                source_contract_id="radar-trading-rule-catalog-v1",
                source_kind="versioned_rule_catalog",
                source_time=None,
                fetched_at=None,
                content_sha256=None,
                freshness="derived",
                observed_value=_audit_value(catalog_value),
            ))

        independent_sources = [
            item
            for item in sources
            if item.freshness in {"effective", "current"}
        ]
        if field_name != "special_session":
            independent_sources.extend(
                item for item in sources if item.freshness == "derived"
            )
        if conflict_reasons[field_name] & reason_set:
            resolution = "conflict"
        elif (
            field_name in {"upper_limit_price", "lower_limit_price"}
            and "public_price_limit_rule_unavailable" in reason_set
        ):
            resolution = "unverified"
        elif any(item.freshness == "stale" for item in sources):
            resolution = "stale"
        elif resolved_value is None and field_name in {
            "upper_limit_price", "lower_limit_price"
        } and resolved_values.get("price_limit_mode") == PriceLimitMode.NO_LIMIT:
            resolution = "not_applicable"
        elif resolved_value is None:
            resolution = "missing"
        elif len({
            item.source_contract_id for item in independent_sources
        }) < 2:
            resolution = "single_source"
        else:
            resolution = "consistent"
        result.append(PublicFieldEvidence(
            field_name=field_name,
            resolution=resolution,
            sources=tuple(sources),
        ))
    return tuple(result)


def _resolve_record(
    *,
    query: PublicCompositeTradabilityQuery,
    context: PublicSecurityContext,
    listed_trading_day_count: int,
    quote: Optional[QuoteSnapshot],
    official: Optional[PublicTradabilityObservation],
    aggregator: Optional[PublicTradabilityObservation],
) -> Tuple[PublicCompositeTradabilityRecord, bool]:
    reasons: list[str] = []
    blocked = False
    if official is None:
        reasons.append("public_official_observation_missing")
    if aggregator is None:
        reasons.append("public_aggregator_observation_missing")
    elif official is not None:
        for field_name in (
            "lifecycle_status",
            "trading_status",
            "special_session",
        ):
            if (
                getattr(official, field_name) is not None
                and getattr(aggregator, field_name) is None
                and field_name != "special_session"
            ):
                reasons.append(
                    f"public_{field_name}_crosscheck_missing"
                )
            if (
                getattr(official, field_name) is None
                and getattr(aggregator, field_name) is not None
            ):
                reasons.append(
                    f"public_{field_name}_official_missing"
                )
    for observation in (official, aggregator):
        if (
            observation is not None
            and observation.trading_status is not None
            and _requires_dynamic_freshness(
                observation,
                "trading_status",
            )
            and _dynamic_age_reason(
                source_time=_observation_source_time(observation),
                as_of=query.as_of,
                missing_reason="public_trading_status_time_missing",
                stale_reason="public_trading_status_stale",
            ) is not None
        ):
            reasons.append("public_trading_status_stale")

    lifecycle = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="lifecycle_status",
        conflict_reason="public_lifecycle_status_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )
    trading_status = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="trading_status",
        conflict_reason="public_trading_status_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )
    special_session = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="special_session",
        conflict_reason="public_special_session_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )
    observed_mode = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="price_limit_mode",
        conflict_reason="public_price_limit_mode_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )
    observed_upper = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="upper_limit_price",
        conflict_reason="public_price_limit_source_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )
    observed_lower = _resolved_value(
        official=official,
        aggregator=aggregator,
        field_name="lower_limit_price",
        conflict_reason="public_price_limit_source_conflict",
        reasons=reasons,
        as_of=query.as_of,
    )

    if any(reason.endswith("_conflict") for reason in reasons):
        blocked = True
    if lifecycle is None:
        reasons.append("public_lifecycle_status_missing")
    if trading_status is None:
        reasons.append("public_trading_status_missing")
    if special_session is None:
        reasons.append("public_special_session_missing")

    trading_observation = None
    for observation in (official, aggregator):
        if (
            observation is not None
            and observation.trading_status is not None
            and (
                not _requires_dynamic_freshness(
                    observation,
                    "trading_status",
                )
                or _dynamic_age_reason(
                    source_time=_observation_source_time(observation),
                    as_of=query.as_of,
                    missing_reason="missing",
                    stale_reason="stale",
                ) is None
            )
        ):
            trading_observation = observation
            break
    if trading_observation is not None:
        trading_age_reason = None
        if _requires_dynamic_freshness(
            trading_observation,
            "trading_status",
        ):
            trading_age_reason = _dynamic_age_reason(
                source_time=_observation_source_time(trading_observation),
                as_of=query.as_of,
                missing_reason="public_trading_status_time_missing",
                stale_reason="public_trading_status_stale",
            )
        if trading_age_reason is not None:
            reasons.append(trading_age_reason)
            trading_status = None

    quote_current = False
    quote_special_session = None
    if quote is None:
        reasons.append("public_quote_missing")
    else:
        age_reason = _dynamic_age_reason(
            source_time=quote.source_time,
            as_of=query.as_of,
            missing_reason="public_quote_source_time_missing",
            stale_reason="public_quote_stale",
        )
        if age_reason is not None:
            reasons.append(age_reason)
        else:
            quote_current = True
            quote_trading = _quote_status(quote)
            if (
                quote_trading is not None
                and trading_status is not None
                and quote_trading != trading_status
            ):
                reasons.append("public_trading_status_conflict")
                blocked = True
            elif trading_status is None and quote_trading is not None:
                trading_status = quote_trading
                reasons.append("public_trading_status_official_missing")

            quote_lifecycle = _quote_lifecycle(quote)
            if (
                quote_lifecycle is not None
                and lifecycle is not None
                and quote_lifecycle != lifecycle
            ):
                reasons.append("public_lifecycle_status_conflict")
                blocked = True
            elif lifecycle is None and quote_lifecycle is not None:
                lifecycle = quote_lifecycle
                reasons.append("public_lifecycle_status_official_missing")

            quote_special_session = _quote_special_session(
                quote,
                listed_trading_day_count=listed_trading_day_count,
                lifecycle_status=lifecycle,
            )

    if (
        official is not None
        and official.special_session is not None
        and (
            aggregator is None
            or aggregator.special_session is None
        )
    ):
        if quote_special_session is None:
            reasons.append("public_special_session_crosscheck_missing")
        elif quote_special_session != official.special_session:
            reasons.append("public_special_session_conflict")
            blocked = True

    catalog_rule = None
    if lifecycle is not None and special_session is not None:
        try:
            catalog_rule = resolve_trading_rule_catalog(
                exchange=context.exchange,
                board=context.board,
                lifecycle_status=lifecycle,
                trading_date=query.trading_date,
                listed_trading_day_count=(
                    listed_trading_day_count
                ),
                special_session=special_session,
                previous_close=(
                    quote.previous_close
                    if quote is not None
                    else None
                ),
            )
        except (TypeError, ValueError):
            reasons.append("public_rule_catalog_unavailable")
            blocked = True

    resolved_mode = (
        catalog_rule.price_limit_mode
        if catalog_rule is not None
        else observed_mode
    )
    if (
        catalog_rule is not None
        and observed_mode is not None
        and observed_mode != catalog_rule.price_limit_mode
    ):
        reasons.append("public_price_limit_mode_conflict")
        blocked = True

    resolved_upper = observed_upper
    resolved_lower = observed_lower
    if resolved_mode == PriceLimitMode.BOUNDED:
        if quote is None or quote.previous_close is None:
            reasons.append("public_previous_close_missing")
        quote_upper = (
            quote.upper_limit_price_source
            if quote is not None and quote_current
            else None
        )
        quote_lower = (
            quote.lower_limit_price_source
            if quote is not None and quote_current
            else None
        )
        if (quote_upper is None) != (quote_lower is None):
            reasons.append("public_quote_price_limit_pair_incomplete")
            blocked = True
        elif quote_upper is None:
            reasons.append("public_quote_price_limits_missing")
        elif catalog_rule is None:
            reasons.append("public_price_limit_rule_unavailable")
        elif (
            catalog_rule.expected_upper_limit_price is None
            or catalog_rule.expected_lower_limit_price is None
        ):
            reasons.append("public_price_limit_rule_unavailable")
        elif not all((
            _price_matches(
                quote_upper,
                catalog_rule.expected_upper_limit_price,
            ),
            _price_matches(
                quote_lower,
                catalog_rule.expected_lower_limit_price,
            ),
        )):
            reasons.append("public_price_limit_rule_conflict")
            blocked = True
        else:
            if (
                resolved_upper is not None
                and not _price_matches(resolved_upper, quote_upper)
            ) or (
                resolved_lower is not None
                and not _price_matches(resolved_lower, quote_lower)
            ):
                reasons.append("public_price_limit_source_conflict")
                blocked = True
            resolved_upper = quote_upper
            resolved_lower = quote_lower
    elif resolved_mode == PriceLimitMode.NO_LIMIT:
        if quote_current and quote is not None and (
            quote.upper_limit_price_source is not None
            or quote.lower_limit_price_source is not None
        ):
            reasons.append("public_no_limit_quote_prices_present")
            blocked = True
        resolved_upper = None
        resolved_lower = None
    else:
        reasons.append("public_price_limit_mode_missing")

    final_reasons = _unique_reasons(reasons)
    resolved_values = {
        "lifecycle_status": lifecycle,
        "trading_status": trading_status,
        "special_session": special_session,
        "price_limit_mode": resolved_mode,
        "upper_limit_price": resolved_upper,
        "lower_limit_price": resolved_lower,
    }
    return PublicCompositeTradabilityRecord(
        symbol=context.symbol,
        lifecycle_status=lifecycle,
        trading_status=trading_status,
        special_session=special_session,
        price_limit_mode=resolved_mode,
        upper_limit_price=resolved_upper,
        lower_limit_price=resolved_lower,
        field_evidence=_field_evidence(
            official=official,
            aggregator=aggregator,
            quote=quote,
            quote_batch_evidence=query.quote_batch_evidence,
            quote_current=quote_current,
            quote_special_session=quote_special_session,
            catalog_rule=catalog_rule,
            as_of=query.as_of,
            resolved_values=resolved_values,
            reasons=final_reasons,
        ),
        reasons=final_reasons,
    ), blocked


def _report(
    *,
    query: PublicCompositeTradabilityQuery,
    status: PublicCompositePocStatus,
    returned_count: int,
    reasons: Iterable[str],
    records: Tuple[PublicCompositeTradabilityRecord, ...] = (),
    field_coverage: Optional[Mapping[str, float]] = None,
) -> PublicCompositeTradabilityReport:
    return PublicCompositeTradabilityReport(
        fixture_resolution_status=status,
        expected_count=len(query.securities),
        returned_count=returned_count,
        field_coverage=field_coverage or {},
        reasons=_unique_reasons(reasons),
        records=records,
    )


def run_public_composite_tradability_poc(
    *,
    query: PublicCompositeTradabilityQuery,
    quotes: Iterable[QuoteSnapshot],
    official_observations: Iterable[PublicTradabilityObservation],
    aggregator_observations: Iterable[PublicTradabilityObservation],
    executed: bool,
) -> PublicCompositeTradabilityReport:
    quote_items = tuple(quotes)
    official_items = tuple(official_observations)
    aggregator_items = tuple(aggregator_observations)

    query_reasons = _query_reasons(query)
    if query_reasons:
        return _report(
            query=query,
            status=PublicCompositePocStatus.BLOCKED,
            returned_count=0,
            reasons=query_reasons,
        )
    if not executed:
        return _report(
            query=query,
            status=PublicCompositePocStatus.NOT_RUN,
            returned_count=0,
            reasons=("public_poc_not_executed",),
        )

    validation_reasons = list(_observation_reasons(
        query=query,
        observations=official_items,
        expected_kind=PublicSourceKind.EXCHANGE_OFFICIAL,
    ))
    validation_reasons.extend(_observation_reasons(
        query=query,
        observations=aggregator_items,
        expected_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
    ))
    validation_reasons.extend(_quote_reasons(
        query=query,
        quotes=quote_items,
    ))
    expected_symbols = {item.symbol for item in query.securities}
    quote_symbols = [item.symbol for item in quote_items]
    if len(quote_symbols) != len(set(quote_symbols)):
        validation_reasons.append("public_batch_duplicate_quote")
    if any(symbol not in expected_symbols for symbol in quote_symbols):
        validation_reasons.append("public_batch_extra_symbol")
    returned_symbols = (
        set(quote_symbols)
        | {item.symbol for item in official_items}
        | {item.symbol for item in aggregator_items}
    ) & expected_symbols
    if validation_reasons:
        return _report(
            query=query,
            status=PublicCompositePocStatus.BLOCKED,
            returned_count=len(returned_symbols),
            reasons=validation_reasons,
        )
    if not returned_symbols:
        return _report(
            query=query,
            status=PublicCompositePocStatus.NO_DATA,
            returned_count=0,
            reasons=("public_poc_no_data",),
        )

    quotes_by_symbol = {item.symbol: item for item in quote_items}
    official_by_symbol = {
        item.symbol: item for item in official_items
    }
    aggregator_by_symbol = {
        item.symbol: item for item in aggregator_items
    }
    calendars_by_exchange = {
        str(item.exchange or "").strip().lower(): item
        for item in query.trading_calendars
    }
    records = []
    any_blocked = False
    report_reasons = []
    for context in query.securities:
        record, record_blocked = _resolve_record(
            query=query,
            context=context,
            listed_trading_day_count=(
                _listed_trading_day_count(
                    context=context,
                    calendar=calendars_by_exchange[
                        str(context.exchange or "").strip().lower()
                    ],
                )
                or 0
            ),
            quote=quotes_by_symbol.get(context.symbol),
            official=official_by_symbol.get(context.symbol),
            aggregator=aggregator_by_symbol.get(context.symbol),
        )
        records.append(record)
        report_reasons.extend(record.reasons)
        any_blocked = any_blocked or record_blocked
    record_tuple = tuple(records)
    reasons = _unique_reasons(report_reasons)
    status = PublicCompositePocStatus.PARTIAL
    if any_blocked:
        status = PublicCompositePocStatus.BLOCKED
    elif not reasons:
        status = PublicCompositePocStatus.FIELD_CANDIDATE
    return _report(
        query=query,
        status=status,
        returned_count=len(returned_symbols),
        reasons=reasons,
        records=record_tuple,
        field_coverage=_field_coverage(record_tuple),
    )


def _effective_datetime(value: date, *, end: bool = False) -> datetime:
    return datetime.combine(
        value,
        time.max if end else time.min,
        tzinfo=SHANGHAI_TZ,
    )


def build_public_tradability_runtime_inputs(
    *,
    query: PublicCompositeTradabilityQuery,
    quotes: Iterable[QuoteSnapshot],
    official_observations: Iterable[PublicTradabilityObservation],
    aggregator_observations: Iterable[PublicTradabilityObservation],
    report: Any,
) -> PublicTradabilityRuntimeInputResult:
    """重放公开源POC，并把整批候选转换为C3/C1研究输入。"""

    quote_items = tuple(quotes)
    official_items = tuple(official_observations)
    aggregator_items = tuple(aggregator_observations)
    if not isinstance(report, PublicCompositeTradabilityReport):
        return PublicTradabilityRuntimeInputResult(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("public_runtime_input_contract_unverified",),
        )
    replayed = run_public_composite_tradability_poc(
        query=query,
        quotes=quote_items,
        official_observations=official_items,
        aggregator_observations=aggregator_items,
        executed=True,
    )
    if replayed != report:
        return PublicTradabilityRuntimeInputResult(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("public_runtime_input_report_replay_mismatch",),
        )
    if (
        replayed.fixture_resolution_status
        != PublicCompositePocStatus.FIELD_CANDIDATE
    ):
        status = (
            ResearchFeatureStatus.SOURCE_UNVERIFIED
            if replayed.fixture_resolution_status
            == PublicCompositePocStatus.BLOCKED
            else ResearchFeatureStatus.MISSING
        )
        return PublicTradabilityRuntimeInputResult(
            status=status,
            reasons=(
                replayed.reasons
                or ("public_runtime_input_not_ready",)
            ),
        )

    contexts_by_symbol = {
        item.symbol: item for item in query.securities
    }
    calendars_by_exchange = {
        str(item.exchange).strip().lower(): item
        for item in query.trading_calendars
    }
    quotes_by_symbol = {item.symbol: item for item in quote_items}
    official_by_symbol = {
        item.symbol: item for item in official_items
    }
    records_by_symbol = {
        item.symbol: item for item in replayed.records
    }
    quote_contract_id = (
        query.quote_batch_evidence.source_contract_id
        if query.quote_batch_evidence is not None
        else ""
    )
    inputs = []
    reasons = []
    for context in query.securities:
        symbol = context.symbol
        quote = quotes_by_symbol.get(symbol)
        official = official_by_symbol.get(symbol)
        record = records_by_symbol.get(symbol)
        calendar = calendars_by_exchange.get(
            str(context.exchange).strip().lower()
        )
        listed_count = (
            _listed_trading_day_count(
                context=context,
                calendar=calendar,
            )
            if calendar is not None
            else None
        )
        if (
            quote is None
            or official is None
            or record is None
            or listed_count is None
            or official.effective_from is None
            or official.effective_until is None
            or record.lifecycle_status is None
            or record.trading_status is None
            or record.special_session is None
            or record.price_limit_mode is None
        ):
            reasons.append("public_runtime_input_item_incomplete")
            continue

        lifecycle = LeaderSecurityLifecycleEvidence(
            symbol=symbol,
            exchange=context.exchange,
            board=context.board,
            lifecycle_status=record.lifecycle_status,
            listed_trading_day_count=listed_count,
            source_contract_id=official.source_contract_id,
            source_name=official.source_name,
            source_url=official.source_url,
            document_id=official.document_id,
            published_at=official.source_time,
            effective_from=_effective_datetime(
                official.effective_from
            ),
            effective_until=_effective_datetime(
                official.effective_until,
                end=True,
            ),
            fetched_at=official.fetched_at,
        )
        resolution = build_leader_tradability_source_input(
            as_of=query.as_of,
            quote=quote,
            quote_source_contract_id=quote_contract_id,
            quote_source_status=ResearchFeatureStatus.READY,
            lifecycle=lifecycle,
            official_reference=OfficialDailyTradabilityReference(
                symbol=symbol,
                exchange=context.exchange,
                board=context.board,
                trading_date=query.trading_date,
                lifecycle_status=record.lifecycle_status,
                trading_status=record.trading_status,
                special_session=record.special_session,
                price_limit_mode=record.price_limit_mode,
                upper_limit_price=record.upper_limit_price,
                lower_limit_price=record.lower_limit_price,
                source_grade=TradabilitySourceGrade.OFFICIAL_PRIMARY,
                source_contract_id=official.source_contract_id,
                source_name=official.source_name,
                source_url=official.source_url,
                document_id=official.document_id,
                source_time=official.source_time,
                fetched_at=official.fetched_at,
            ),
        )
        if (
            resolution.status != ResearchFeatureStatus.READY
            or resolution.feature_input is None
        ):
            reasons.extend(
                resolution.reasons
                or ("public_runtime_input_item_preflight_failed",)
            )
            continue
        inputs.append(resolution.feature_input)

    if reasons or len(inputs) != len(contexts_by_symbol):
        return PublicTradabilityRuntimeInputResult(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=_unique_reasons(reasons),
        )
    return PublicTradabilityRuntimeInputResult(
        status=ResearchFeatureStatus.READY,
        inputs=tuple(inputs),
    )
