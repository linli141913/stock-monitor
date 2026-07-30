"""阶段6L-C1证券生命周期与可交易性研究证据。

本模块只消费调用方提供的版本化证据，不抓取数据、不连接数据库，也不改变
正式评分或状态机门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from radar.contracts import QuoteSnapshot
from radar.leader_research_features import (
    MAXIMUM_FUTURE_SKEW_SECONDS,
    MAXIMUM_SOURCE_AGE_SECONDS,
    ResearchFeatureStatus,
)


LEADER_TRADABILITY_FEATURE_VERSION = (
    "radar-leader-tradability-feature-v1"
)
PRICE_TOLERANCE = Decimal("0.005")
UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class SecurityLifecycleStatus(str, Enum):
    NORMAL = "normal"
    ST = "st"
    STAR_ST = "star_st"
    DELISTING = "delisting"
    RELISTED = "relisted"
    ABNORMAL = "abnormal"
    UNKNOWN = "unknown"


class TradingSessionStatus(str, Enum):
    TRADING = "trading"
    SUSPENDED = "suspended"
    ABNORMAL = "abnormal"
    UNKNOWN = "unknown"


class PriceLimitMode(str, Enum):
    BOUNDED = "bounded"
    NO_LIMIT = "no_limit"
    UNKNOWN = "unknown"


class PriceLimitState(str, Enum):
    NORMAL = "normal"
    LIMIT_UP = "limit_up"
    LIMIT_DOWN = "limit_down"
    NO_LIMIT = "no_limit"
    UNKNOWN = "unknown"


class OnePriceLimitState(str, Enum):
    NONE = "none"
    ONE_PRICE_LIMIT_UP = "one_price_limit_up"
    ONE_PRICE_LIMIT_DOWN = "one_price_limit_down"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LeaderSecurityLifecycleEvidence:
    symbol: str
    exchange: str
    board: str
    lifecycle_status: SecurityLifecycleStatus
    listed_trading_day_count: int
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    fetched_at: datetime


@dataclass(frozen=True)
class LeaderTradingStatusEvidence:
    symbol: str
    trading_date: date
    status: TradingSessionStatus
    source_contract_id: str
    source_name: str
    source_time: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class LeaderTradingRuleEvidence:
    symbol: str
    trading_date: date
    rule_version: str
    price_limit_mode: PriceLimitMode
    upper_limit_price: Optional[float]
    lower_limit_price: Optional[float]
    source_contract_id: str
    source_name: str
    source_url: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]


@dataclass(frozen=True)
class LeaderTradabilityFeatureInput:
    as_of: datetime
    quote: QuoteSnapshot
    quote_source_contract_id: str
    quote_source_status: ResearchFeatureStatus
    lifecycle: LeaderSecurityLifecycleEvidence
    trading_status: LeaderTradingStatusEvidence
    trading_rule: LeaderTradingRuleEvidence


@dataclass(frozen=True)
class LeaderTradabilityFeatureResult:
    status: ResearchFeatureStatus
    lifecycle_status: SecurityLifecycleStatus
    trading_status: TradingSessionStatus
    price_limit_state: PriceLimitState
    one_price_limit_state: OnePriceLimitState
    research_eligible: Optional[bool]
    preliminary_only: Optional[bool]
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": LEADER_TRADABILITY_FEATURE_VERSION,
            "status": self.status.value,
            "lifecycleStatus": self.lifecycle_status.value,
            "tradingStatus": self.trading_status.value,
            "priceLimitState": self.price_limit_state.value,
            "onePriceLimitState": self.one_price_limit_state.value,
            "researchEligible": self.research_eligible,
            "preliminaryOnly": self.preliminary_only,
            "scoreReady": False,
            "formalUsable": False,
            "researchScore": None,
            "references": [dict(item) for item in self.references],
            "reasons": list(self.reasons),
        }


def _references(
    value: LeaderTradabilityFeatureInput,
) -> Tuple[Mapping[str, Any], ...]:
    return (
        {
            "sourceContractId": value.quote_source_contract_id,
            "kind": "quote",
        },
        {
            "sourceContractId": value.lifecycle.source_contract_id,
            "kind": "security_lifecycle",
            "sourceName": value.lifecycle.source_name,
            "sourceUrl": value.lifecycle.source_url,
            "documentId": value.lifecycle.document_id,
            "effectiveFrom": value.lifecycle.effective_from.isoformat(),
            "effectiveUntil": (
                value.lifecycle.effective_until.isoformat()
                if value.lifecycle.effective_until is not None
                else None
            ),
        },
        {
            "sourceContractId": value.trading_status.source_contract_id,
            "kind": "trading_status",
            "sourceName": value.trading_status.source_name,
            "tradingDate": value.trading_status.trading_date.isoformat(),
        },
        {
            "sourceContractId": value.trading_rule.source_contract_id,
            "kind": "trading_rule",
            "sourceName": value.trading_rule.source_name,
            "sourceUrl": value.trading_rule.source_url,
            "ruleVersion": value.trading_rule.rule_version,
            "effectiveFrom": value.trading_rule.effective_from.isoformat(),
            "effectiveUntil": (
                value.trading_rule.effective_until.isoformat()
                if value.trading_rule.effective_until is not None
                else None
            ),
        },
    )


def missing_leader_tradability_features(
    reasons: Tuple[str, ...] = ("tradability_evidence_missing",),
    *,
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING,
) -> LeaderTradabilityFeatureResult:
    return LeaderTradabilityFeatureResult(
        status=status,
        lifecycle_status=SecurityLifecycleStatus.UNKNOWN,
        trading_status=TradingSessionStatus.UNKNOWN,
        price_limit_state=PriceLimitState.UNKNOWN,
        one_price_limit_state=OnePriceLimitState.UNKNOWN,
        research_eligible=None,
        preliminary_only=None,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _price_matches(value: Optional[float], target: Optional[float]) -> bool:
    if value is None or target is None:
        return False
    return abs(Decimal(str(value)) - Decimal(str(target))) <= PRICE_TOLERANCE


def _aware_utc(value: datetime) -> Optional[datetime]:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _invalid_result(
    value: LeaderTradabilityFeatureInput,
    *,
    status: ResearchFeatureStatus,
    reason: str,
) -> LeaderTradabilityFeatureResult:
    return LeaderTradabilityFeatureResult(
        status=status,
        lifecycle_status=(
            value.lifecycle.lifecycle_status
            if isinstance(
                value.lifecycle.lifecycle_status,
                SecurityLifecycleStatus,
            )
            else SecurityLifecycleStatus.UNKNOWN
        ),
        trading_status=(
            value.trading_status.status
            if isinstance(
                value.trading_status.status,
                TradingSessionStatus,
            )
            else TradingSessionStatus.UNKNOWN
        ),
        price_limit_state=PriceLimitState.UNKNOWN,
        one_price_limit_state=OnePriceLimitState.UNKNOWN,
        research_eligible=None,
        preliminary_only=None,
        reasons=(reason,),
        references=_references(value),
    )


def _positive_finite(value: Optional[float]) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _required_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _https_url(value: str) -> bool:
    parts = urlsplit(str(value or "").strip())
    return parts.scheme == "https" and bool(parts.netloc)


def build_leader_tradability_features(
    value: LeaderTradabilityFeatureInput,
) -> LeaderTradabilityFeatureResult:
    if not all((
        isinstance(
            value.quote_source_status,
            ResearchFeatureStatus,
        ),
        isinstance(
            value.lifecycle.lifecycle_status,
            SecurityLifecycleStatus,
        ),
        isinstance(
            value.trading_status.status,
            TradingSessionStatus,
        ),
        isinstance(
            value.trading_rule.price_limit_mode,
            PriceLimitMode,
        ),
    )):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="tradability_enum_invalid",
        )
    if value.quote_source_status != ResearchFeatureStatus.READY:
        reason_by_status = {
            ResearchFeatureStatus.SOURCE_FAILED: "quote_source_failed",
            ResearchFeatureStatus.STALE: "quote_source_stale",
            ResearchFeatureStatus.MISSING: "quote_source_missing",
            ResearchFeatureStatus.SOURCE_UNVERIFIED: (
                "quote_source_unverified"
            ),
        }
        return _invalid_result(
            value,
            status=value.quote_source_status,
            reason=reason_by_status[value.quote_source_status],
        )
    if not all((
        _required_text(value.quote_source_contract_id),
        _required_text(value.lifecycle.exchange),
        _required_text(value.lifecycle.board),
        _required_text(value.lifecycle.source_contract_id),
        _required_text(value.lifecycle.source_name),
        _required_text(value.lifecycle.document_id),
        _required_text(value.trading_status.source_contract_id),
        _required_text(value.trading_status.source_name),
        _required_text(value.trading_rule.rule_version),
        _required_text(value.trading_rule.source_contract_id),
        _required_text(value.trading_rule.source_name),
    )):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="tradability_source_identity_missing",
        )

    as_of = _aware_utc(value.as_of)
    lifecycle_times = (
        _aware_utc(value.lifecycle.published_at),
        _aware_utc(value.lifecycle.effective_from),
        (
            _aware_utc(value.lifecycle.effective_until)
            if value.lifecycle.effective_until is not None
            else None
        ),
        _aware_utc(value.lifecycle.fetched_at),
    )
    trading_times = (
        _aware_utc(value.trading_status.source_time),
        _aware_utc(value.trading_status.fetched_at),
    )
    rule_times = (
        _aware_utc(value.trading_rule.published_at),
        _aware_utc(value.trading_rule.effective_from),
        (
            _aware_utc(value.trading_rule.effective_until)
            if value.trading_rule.effective_until is not None
            else None
        ),
    )
    quote_source_time = (
        _aware_utc(value.quote.source_time)
        if value.quote.source_time is not None
        else None
    )
    quote_fetched_at = _aware_utc(value.quote.fetched_at)
    required_times = (
        as_of,
        lifecycle_times[0],
        lifecycle_times[1],
        lifecycle_times[3],
        trading_times[0],
        trading_times[1],
        rule_times[0],
        rule_times[1],
        quote_fetched_at,
    )
    if any(item is None for item in required_times):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="tradability_timestamp_timezone_missing",
        )
    if (
        value.lifecycle.effective_until is not None
        and lifecycle_times[2] is None
    ) or (
        value.trading_rule.effective_until is not None
        and rule_times[2] is None
    ):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="tradability_timestamp_timezone_missing",
        )

    symbol = value.quote.symbol
    if value.lifecycle.symbol != symbol:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_symbol_mismatch",
        )
    if value.trading_status.symbol != symbol:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_symbol_mismatch",
        )
    if value.trading_rule.symbol != symbol:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_symbol_mismatch",
        )
    trading_date = value.as_of.astimezone(SHANGHAI_TZ).date()
    if value.trading_status.trading_date != trading_date:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_date_mismatch",
        )
    if value.trading_rule.trading_date != trading_date:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_date_mismatch",
        )

    lifecycle_published, lifecycle_from, lifecycle_until, lifecycle_fetched = (
        lifecycle_times
    )
    if lifecycle_published > as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_published_in_future",
        )
    if (
        lifecycle_until is not None
        and lifecycle_until < lifecycle_from
    ):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_effective_interval_invalid",
        )
    if lifecycle_fetched < lifecycle_published:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_fetched_before_published",
        )
    if (
        lifecycle_fetched - as_of
    ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_fetched_in_future",
        )
    if lifecycle_from > as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_not_effective",
        )
    if lifecycle_until is not None and lifecycle_until < as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.STALE,
            reason="lifecycle_evidence_expired",
        )
    if not _https_url(value.lifecycle.source_url):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_source_url_unverified",
        )

    rule_published, rule_from, rule_until = rule_times
    if rule_published > as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_published_in_future",
        )
    if rule_until is not None and rule_until < rule_from:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_effective_interval_invalid",
        )
    if rule_from > as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_not_effective",
        )
    if rule_until is not None and rule_until < as_of:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.STALE,
            reason="trading_rule_expired",
        )
    if not _https_url(value.trading_rule.source_url):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_source_url_unverified",
        )

    trading_source_time, trading_fetched_at = trading_times
    trading_age_seconds = (as_of - trading_source_time).total_seconds()
    if trading_age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_from_future",
        )
    if trading_age_seconds > MAXIMUM_SOURCE_AGE_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.STALE,
            reason="trading_status_stale",
        )
    if trading_fetched_at < trading_source_time:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_fetched_before_source",
        )
    if (
        trading_fetched_at - as_of
    ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_fetched_in_future",
        )

    if quote_source_time is None:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.MISSING,
            reason="quote_source_time_missing",
        )
    quote_age_seconds = (as_of - quote_source_time).total_seconds()
    if quote_age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_source_from_future",
        )
    if quote_age_seconds > MAXIMUM_SOURCE_AGE_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.STALE,
            reason="quote_source_stale",
        )
    if quote_fetched_at < quote_source_time:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_fetched_before_source",
        )
    if (
        quote_fetched_at - as_of
    ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_fetched_in_future",
        )

    rule = value.trading_rule
    if rule.price_limit_mode == PriceLimitMode.UNKNOWN:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="price_limit_mode_unknown",
        )
    if rule.price_limit_mode == PriceLimitMode.BOUNDED:
        if not (
            _positive_finite(rule.upper_limit_price)
            and _positive_finite(rule.lower_limit_price)
        ):
            return _invalid_result(
                value,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="bounded_price_limit_missing",
            )
        if float(rule.lower_limit_price) >= float(rule.upper_limit_price):
            return _invalid_result(
                value,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="bounded_price_limit_invalid",
            )
    elif (
        rule.upper_limit_price is not None
        or rule.lower_limit_price is not None
    ):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="no_limit_price_boundary_present",
        )

    if value.lifecycle.lifecycle_status == SecurityLifecycleStatus.UNKNOWN:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_status_unknown",
        )
    if value.trading_status.status == TradingSessionStatus.UNKNOWN:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_unknown",
        )
    if value.lifecycle.listed_trading_day_count < 0:
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="listed_trading_day_count_invalid",
        )

    excluded_lifecycle = {
        SecurityLifecycleStatus.ST,
        SecurityLifecycleStatus.STAR_ST,
        SecurityLifecycleStatus.DELISTING,
        SecurityLifecycleStatus.ABNORMAL,
    }
    if value.lifecycle.lifecycle_status in excluded_lifecycle:
        reason = (
            "lifecycle_excluded:"
            f"{value.lifecycle.lifecycle_status.value}"
        )
        return LeaderTradabilityFeatureResult(
            status=ResearchFeatureStatus.READY,
            lifecycle_status=value.lifecycle.lifecycle_status,
            trading_status=value.trading_status.status,
            price_limit_state=PriceLimitState.UNKNOWN,
            one_price_limit_state=OnePriceLimitState.UNKNOWN,
            research_eligible=False,
            preliminary_only=False,
            reasons=(reason,),
            references=_references(value),
        )

    if value.trading_status.status in {
        TradingSessionStatus.SUSPENDED,
        TradingSessionStatus.ABNORMAL,
    }:
        reason = (
            "trading_status_excluded:"
            f"{value.trading_status.status.value}"
        )
        return LeaderTradabilityFeatureResult(
            status=ResearchFeatureStatus.READY,
            lifecycle_status=value.lifecycle.lifecycle_status,
            trading_status=value.trading_status.status,
            price_limit_state=PriceLimitState.UNKNOWN,
            one_price_limit_state=OnePriceLimitState.UNKNOWN,
            research_eligible=False,
            preliminary_only=False,
            reasons=(reason,),
            references=_references(value),
        )

    quote_values = (
        value.quote.price,
        value.quote.previous_close,
        value.quote.open_price,
        value.quote.high_price,
        value.quote.low_price,
    )
    if any(item is None for item in quote_values):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.MISSING,
            reason="quote_ohlc_missing",
        )
    if any(not _positive_finite(item) for item in quote_values):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_quote_zero_conflict",
        )
    current_price, _, open_price, high_price, low_price = quote_values
    if (
        high_price < low_price
        or not low_price <= open_price <= high_price
        or not low_price <= current_price <= high_price
    ):
        return _invalid_result(
            value,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_ohlc_inconsistent",
        )

    listed_trading_day_count = value.lifecycle.listed_trading_day_count
    if listed_trading_day_count <= 5:
        return LeaderTradabilityFeatureResult(
            status=ResearchFeatureStatus.READY,
            lifecycle_status=value.lifecycle.lifecycle_status,
            trading_status=value.trading_status.status,
            price_limit_state=PriceLimitState.UNKNOWN,
            one_price_limit_state=OnePriceLimitState.UNKNOWN,
            research_eligible=False,
            preliminary_only=False,
            reasons=("new_listing_first_five_trading_days",),
            references=_references(value),
        )
    preliminary_only = listed_trading_day_count <= 10

    if value.trading_rule.price_limit_mode == PriceLimitMode.NO_LIMIT:
        return LeaderTradabilityFeatureResult(
            status=ResearchFeatureStatus.READY,
            lifecycle_status=value.lifecycle.lifecycle_status,
            trading_status=value.trading_status.status,
            price_limit_state=PriceLimitState.NO_LIMIT,
            one_price_limit_state=OnePriceLimitState.NOT_APPLICABLE,
            research_eligible=True,
            preliminary_only=preliminary_only,
            references=_references(value),
        )

    quote = value.quote
    upper_limit_price = value.trading_rule.upper_limit_price
    lower_limit_price = value.trading_rule.lower_limit_price
    price_limit_state = PriceLimitState.NORMAL
    one_price_limit_state = OnePriceLimitState.NONE
    research_eligible = True
    reasons = ()
    if _price_matches(quote.price, upper_limit_price):
        price_limit_state = PriceLimitState.LIMIT_UP
        if all(
            _price_matches(item, upper_limit_price)
            for item in (
                quote.open_price,
                quote.high_price,
                quote.low_price,
                quote.price,
            )
        ):
            one_price_limit_state = (
                OnePriceLimitState.ONE_PRICE_LIMIT_UP
            )
            research_eligible = False
            reasons = ("one_price_limit_excluded:limit_up",)
    elif _price_matches(quote.price, lower_limit_price):
        price_limit_state = PriceLimitState.LIMIT_DOWN
        if all(
            _price_matches(item, lower_limit_price)
            for item in (
                quote.open_price,
                quote.high_price,
                quote.low_price,
                quote.price,
            )
        ):
            one_price_limit_state = (
                OnePriceLimitState.ONE_PRICE_LIMIT_DOWN
            )
            research_eligible = False
            reasons = ("one_price_limit_excluded:limit_down",)

    return LeaderTradabilityFeatureResult(
        status=ResearchFeatureStatus.READY,
        lifecycle_status=value.lifecycle.lifecycle_status,
        trading_status=value.trading_status.status,
        price_limit_state=price_limit_state,
        one_price_limit_state=one_price_limit_state,
        research_eligible=research_eligible,
        preliminary_only=preliminary_only,
        reasons=reasons,
        references=_references(value),
    )
