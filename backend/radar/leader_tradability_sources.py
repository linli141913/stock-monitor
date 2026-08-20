"""阶段6L-C3可交易性真实来源合同与规则解析。

本模块只处理调用方提供的证据，不发起网络请求、不连接数据库，也不改变
正式评分或状态机门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
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
from radar.leader_tradability_features import (
    LeaderSecurityLifecycleEvidence,
    LeaderTradabilityFeatureInput,
    LeaderTradingRuleEvidence,
    LeaderTradingStatusEvidence,
    PRICE_TOLERANCE,
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
    build_leader_tradability_features,
)


LEADER_TRADABILITY_SOURCE_VERSION = (
    "radar-leader-tradability-source-v1"
)
PRICE_TICK = Decimal("0.01")
CATALOG_START_DATE = date(2023, 4, 10)
CURRENT_RULE_START_DATE = date(2026, 7, 6)
UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PriceLimitSpecialSession(str, Enum):
    NONE = "none"
    RELISTING_FIRST_DAY = "relisting_first_day"
    DELISTING_FIRST_DAY = "delisting_first_day"


class TradabilitySourceGrade(str, Enum):
    OFFICIAL_PRIMARY = "official_primary"
    OFFICIAL_RESTRICTED_CANDIDATE = (
        "official_restricted_candidate"
    )
    SECONDARY_CROSSCHECK = "secondary_crosscheck"
    MANUAL_VERIFIED = "manual_verified"
    REJECTED_INFERENCE = "rejected_inference"


@dataclass(frozen=True)
class TradingRuleCatalogEntry:
    exchange: str
    board_group: str
    rule_version: str
    published_on: date
    effective_from: date
    effective_until: Optional[date]
    normal_limit_rate: float
    risk_warning_limit_rate: float
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str


@dataclass(frozen=True)
class ResolvedTradingRule:
    exchange: str
    board: str
    trading_date: date
    rule_version: str
    price_limit_mode: PriceLimitMode
    limit_rate: Optional[float]
    expected_upper_limit_price: Optional[float]
    expected_lower_limit_price: Optional[float]
    published_on: date
    effective_from: date
    effective_until: Optional[date]
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str


@dataclass(frozen=True)
class OfficialDailyTradabilityReference:
    symbol: str
    exchange: str
    board: str
    trading_date: date
    lifecycle_status: SecurityLifecycleStatus
    trading_status: TradingSessionStatus
    special_session: PriceLimitSpecialSession
    price_limit_mode: PriceLimitMode
    upper_limit_price: Optional[float]
    lower_limit_price: Optional[float]
    source_grade: TradabilitySourceGrade
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str
    source_time: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class LeaderTradabilitySourceResolution:
    status: ResearchFeatureStatus
    feature_input: Optional[LeaderTradabilityFeatureInput]
    catalog_rule: Optional[ResolvedTradingRule]
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple
    )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": LEADER_TRADABILITY_SOURCE_VERSION,
            "status": self.status.value,
            "ruleVersion": (
                self.catalog_rule.rule_version
                if self.catalog_rule is not None
                else None
            ),
            "priceLimitMode": (
                self.catalog_rule.price_limit_mode.value
                if self.catalog_rule is not None
                else PriceLimitMode.UNKNOWN.value
            ),
            "limitRate": (
                self.catalog_rule.limit_rate
                if self.catalog_rule is not None
                else None
            ),
            "inputReady": self.feature_input is not None,
            "scoreReady": False,
            "formalUsable": False,
            "references": [dict(item) for item in self.references],
            "reasons": list(self.reasons),
        }


_CATALOG: Tuple[TradingRuleCatalogEntry, ...] = (
    TradingRuleCatalogEntry(
        exchange="sse",
        board_group="main",
        rule_version="sse-trading-rule-2023",
        published_on=date(2023, 2, 17),
        effective_from=CATALOG_START_DATE,
        effective_until=date(2026, 7, 5),
        normal_limit_rate=0.10,
        risk_warning_limit_rate=0.05,
        source_contract_id="sse-trading-rule-2023",
        source_name="上海证券交易所",
        source_url=(
            "https://www.sse.com.cn/lawandrules/"
            "sselawsrules2025/repeal/rules/c/"
            "c_20250612_10824490.shtml"
        ),
        document_id="上证发〔2023〕32号",
    ),
    TradingRuleCatalogEntry(
        exchange="sse",
        board_group="star",
        rule_version="sse-trading-rule-2023",
        published_on=date(2023, 2, 17),
        effective_from=CATALOG_START_DATE,
        effective_until=date(2026, 7, 5),
        normal_limit_rate=0.20,
        risk_warning_limit_rate=0.20,
        source_contract_id="sse-trading-rule-2023",
        source_name="上海证券交易所",
        source_url=(
            "https://www.sse.com.cn/lawandrules/"
            "sselawsrules2025/repeal/rules/c/"
            "c_20250612_10824490.shtml"
        ),
        document_id="上证发〔2023〕32号",
    ),
    TradingRuleCatalogEntry(
        exchange="szse",
        board_group="main",
        rule_version="szse-trading-rule-2023",
        published_on=date(2023, 2, 17),
        effective_from=CATALOG_START_DATE,
        effective_until=date(2026, 7, 5),
        normal_limit_rate=0.10,
        risk_warning_limit_rate=0.05,
        source_contract_id="szse-trading-rule-2023",
        source_name="深圳证券交易所",
        source_url=(
            "https://www.szse.cn/lawrules/index/rule/"
            "t20230217_598773.html"
        ),
        document_id="深证上〔2023〕98号",
    ),
    TradingRuleCatalogEntry(
        exchange="szse",
        board_group="chinext",
        rule_version="szse-trading-rule-2023",
        published_on=date(2023, 2, 17),
        effective_from=CATALOG_START_DATE,
        effective_until=date(2026, 7, 5),
        normal_limit_rate=0.20,
        risk_warning_limit_rate=0.20,
        source_contract_id="szse-trading-rule-2023",
        source_name="深圳证券交易所",
        source_url=(
            "https://www.szse.cn/lawrules/index/rule/"
            "t20230217_598773.html"
        ),
        document_id="深证上〔2023〕98号",
    ),
    TradingRuleCatalogEntry(
        exchange="sse",
        board_group="main",
        rule_version="sse-trading-rule-2026",
        published_on=date(2026, 4, 24),
        effective_from=CURRENT_RULE_START_DATE,
        effective_until=None,
        normal_limit_rate=0.10,
        risk_warning_limit_rate=0.10,
        source_contract_id="sse-trading-rule-2026",
        source_name="上海证券交易所",
        source_url=(
            "https://www.sse.com.cn/lawandrules/"
            "sselawsrules2025/stocks/exchange/c/"
            "c_20260424_10816482.shtml"
        ),
        document_id="上证发〔2026〕41号",
    ),
    TradingRuleCatalogEntry(
        exchange="sse",
        board_group="star",
        rule_version="sse-trading-rule-2026",
        published_on=date(2026, 4, 24),
        effective_from=CURRENT_RULE_START_DATE,
        effective_until=None,
        normal_limit_rate=0.20,
        risk_warning_limit_rate=0.20,
        source_contract_id="sse-trading-rule-2026",
        source_name="上海证券交易所",
        source_url=(
            "https://www.sse.com.cn/lawandrules/"
            "sselawsrules2025/stocks/exchange/c/"
            "c_20260424_10816482.shtml"
        ),
        document_id="上证发〔2026〕41号",
    ),
    TradingRuleCatalogEntry(
        exchange="szse",
        board_group="main",
        rule_version="szse-trading-rule-2026",
        published_on=date(2026, 4, 24),
        effective_from=CURRENT_RULE_START_DATE,
        effective_until=None,
        normal_limit_rate=0.10,
        risk_warning_limit_rate=0.10,
        source_contract_id="szse-trading-rule-2026",
        source_name="深圳证券交易所",
        source_url=(
            "https://www.szse.cn/lawrules/rule/trade/current/"
            "t20260424_620190.html"
        ),
        document_id="深证上〔2026〕551号",
    ),
    TradingRuleCatalogEntry(
        exchange="szse",
        board_group="chinext",
        rule_version="szse-trading-rule-2026",
        published_on=date(2026, 4, 24),
        effective_from=CURRENT_RULE_START_DATE,
        effective_until=None,
        normal_limit_rate=0.20,
        risk_warning_limit_rate=0.20,
        source_contract_id="szse-trading-rule-2026",
        source_name="深圳证券交易所",
        source_url=(
            "https://www.szse.cn/lawrules/rule/trade/current/"
            "t20260424_620190.html"
        ),
        document_id="深证上〔2026〕551号",
    ),
)


def _board_group(exchange: str, board: str) -> str:
    normalized_exchange = str(exchange or "").strip().lower()
    normalized_board = str(board or "").strip()
    groups = {
        ("sse", "主板A股"): "main",
        ("sse", "主板"): "main",
        ("sse", "科创板"): "star",
        ("szse", "主板"): "main",
        ("szse", "创业板"): "chinext",
    }
    try:
        return groups[(normalized_exchange, normalized_board)]
    except KeyError as exc:
        raise ValueError("不支持的交易所或板块") from exc


def _catalog_entry(
    exchange: str,
    board_group: str,
    trading_date: date,
) -> TradingRuleCatalogEntry:
    matches = tuple(
        entry
        for entry in _CATALOG
        if entry.exchange == exchange
        and entry.board_group == board_group
        and entry.effective_from <= trading_date
        and (
            entry.effective_until is None
            or trading_date <= entry.effective_until
        )
    )
    if len(matches) != 1:
        raise ValueError("交易日不在当前规则目录覆盖范围内")
    return matches[0]


def _rounded_limit(
    previous_close: float,
    multiplier: Decimal,
) -> float:
    value = (
        Decimal(str(previous_close)) * multiplier
    ).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
    return float(value)


def _aware_utc(value: datetime) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _required_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _https_url(value: str) -> bool:
    parts = urlsplit(str(value or "").strip())
    return parts.scheme == "https" and bool(parts.netloc)


def _positive_finite(value: Optional[float]) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _price_matches(
    value: Optional[float],
    target: Optional[float],
) -> bool:
    if value is None or target is None:
        return False
    return (
        abs(Decimal(str(value)) - Decimal(str(target)))
        <= PRICE_TOLERANCE
    )


def _resolution(
    *,
    status: ResearchFeatureStatus,
    reason: str,
    catalog_rule: Optional[ResolvedTradingRule] = None,
    references: Tuple[Mapping[str, Any], ...] = (),
) -> LeaderTradabilitySourceResolution:
    return LeaderTradabilitySourceResolution(
        status=status,
        feature_input=None,
        catalog_rule=catalog_rule,
        reasons=(reason,),
        references=references,
    )


def _catalog_datetime(value: date, *, end: bool = False) -> datetime:
    return datetime.combine(
        value,
        time.max if end else time.min,
        tzinfo=SHANGHAI_TZ,
    )


def resolve_trading_rule_catalog(
    *,
    exchange: str,
    board: str,
    lifecycle_status: SecurityLifecycleStatus,
    trading_date: date,
    listed_trading_day_count: int,
    special_session: PriceLimitSpecialSession,
    previous_close: Optional[float],
) -> ResolvedTradingRule:
    if (
        not isinstance(trading_date, date)
        or isinstance(trading_date, datetime)
    ):
        raise ValueError("交易日无效")
    if not isinstance(lifecycle_status, SecurityLifecycleStatus):
        raise ValueError("证券生命周期状态无效")
    if not isinstance(special_session, PriceLimitSpecialSession):
        raise ValueError("特殊交易日类型无效")
    if (
        not isinstance(listed_trading_day_count, int)
        or isinstance(listed_trading_day_count, bool)
        or listed_trading_day_count < 0
    ):
        raise ValueError("上市交易日计数无效")
    if previous_close is not None and (
        not isinstance(previous_close, (int, float))
        or isinstance(previous_close, bool)
        or not math.isfinite(float(previous_close))
        or float(previous_close) <= 0
    ):
        raise ValueError("前收盘价无效")

    normalized_exchange = str(exchange or "").strip().lower()
    group = _board_group(normalized_exchange, board)
    entry = _catalog_entry(
        normalized_exchange,
        group,
        trading_date,
    )
    no_limit = (
        listed_trading_day_count <= 5
        or special_session
        in {
            PriceLimitSpecialSession.RELISTING_FIRST_DAY,
            PriceLimitSpecialSession.DELISTING_FIRST_DAY,
        }
    )
    limit_rate = None
    upper_limit = None
    lower_limit = None
    mode = PriceLimitMode.NO_LIMIT
    if not no_limit:
        mode = PriceLimitMode.BOUNDED
        if lifecycle_status in {
            SecurityLifecycleStatus.ST,
            SecurityLifecycleStatus.STAR_ST,
        }:
            limit_rate = entry.risk_warning_limit_rate
        else:
            limit_rate = entry.normal_limit_rate
        if previous_close is not None:
            rate = Decimal(str(limit_rate))
            upper_limit = _rounded_limit(
                previous_close,
                Decimal("1") + rate,
            )
            lower_limit = _rounded_limit(
                previous_close,
                Decimal("1") - rate,
            )

    return ResolvedTradingRule(
        exchange=normalized_exchange,
        board=str(board or "").strip(),
        trading_date=trading_date,
        rule_version=entry.rule_version,
        price_limit_mode=mode,
        limit_rate=limit_rate,
        expected_upper_limit_price=upper_limit,
        expected_lower_limit_price=lower_limit,
        published_on=entry.published_on,
        effective_from=entry.effective_from,
        effective_until=entry.effective_until,
        source_contract_id=entry.source_contract_id,
        source_name=entry.source_name,
        source_url=entry.source_url,
        document_id=entry.document_id,
    )


def build_leader_tradability_source_input(
    *,
    as_of: datetime,
    quote: QuoteSnapshot,
    quote_source_contract_id: str,
    quote_source_status: ResearchFeatureStatus,
    lifecycle: LeaderSecurityLifecycleEvidence,
    official_reference: Optional[
        OfficialDailyTradabilityReference
    ],
) -> LeaderTradabilitySourceResolution:
    if _aware_utc(as_of) is None:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="tradability_source_as_of_timezone_missing",
        )
    if not isinstance(quote_source_status, ResearchFeatureStatus):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_source_status_invalid",
        )
    if quote_source_status != ResearchFeatureStatus.READY:
        reasons = {
            ResearchFeatureStatus.MISSING: "quote_source_missing",
            ResearchFeatureStatus.SOURCE_UNVERIFIED: (
                "quote_source_unverified"
            ),
            ResearchFeatureStatus.STALE: "quote_source_stale",
            ResearchFeatureStatus.SOURCE_FAILED: (
                "quote_source_failed"
            ),
        }
        return _resolution(
            status=quote_source_status,
            reason=reasons[quote_source_status],
        )
    if not _required_text(quote_source_contract_id):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="quote_source_contract_id_missing",
        )
    if official_reference is None:
        return _resolution(
            status=ResearchFeatureStatus.MISSING,
            reason="official_reference_source_unavailable",
        )

    reference = official_reference
    base_reference = ({
        "kind": "official_daily_reference",
        "sourceContractId": reference.source_contract_id,
        "sourceName": reference.source_name,
        "sourceUrl": reference.source_url,
        "documentId": reference.document_id,
        "sourceGrade": (
            reference.source_grade.value
            if isinstance(
                reference.source_grade,
                TradabilitySourceGrade,
            )
            else None
        ),
    },)
    if (
        not isinstance(
            reference.source_grade,
            TradabilitySourceGrade,
        )
        or reference.source_grade
        != TradabilitySourceGrade.OFFICIAL_PRIMARY
    ):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_grade_not_accepted",
            references=base_reference,
        )
    if not all((
        isinstance(
            reference.lifecycle_status,
            SecurityLifecycleStatus,
        ),
        isinstance(
            reference.trading_status,
            TradingSessionStatus,
        ),
        isinstance(
            reference.special_session,
            PriceLimitSpecialSession,
        ),
        isinstance(
            reference.price_limit_mode,
            PriceLimitMode,
        ),
    )):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_enum_invalid",
            references=base_reference,
        )
    if not all((
        _required_text(reference.symbol),
        _required_text(reference.exchange),
        _required_text(reference.board),
        _required_text(reference.source_contract_id),
        _required_text(reference.source_name),
        _required_text(reference.document_id),
    )):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_identity_missing",
            references=base_reference,
        )
    if not _https_url(reference.source_url):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_source_url_unverified",
            references=base_reference,
        )

    if reference.symbol != quote.symbol:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_symbol_mismatch",
            references=base_reference,
        )
    if lifecycle.symbol != quote.symbol:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_symbol_mismatch",
            references=base_reference,
        )
    if reference.exchange != lifecycle.exchange:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_exchange_mismatch",
            references=base_reference,
        )
    if reference.board != lifecycle.board:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_board_mismatch",
            references=base_reference,
        )
    trading_date = as_of.astimezone(SHANGHAI_TZ).date()
    if reference.trading_date != trading_date:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_trading_date_mismatch",
            references=base_reference,
        )
    if reference.lifecycle_status != lifecycle.lifecycle_status:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_lifecycle_conflict",
            references=base_reference,
        )
    if reference.lifecycle_status == SecurityLifecycleStatus.UNKNOWN:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="lifecycle_status_unknown",
            references=base_reference,
        )
    if reference.trading_status == TradingSessionStatus.UNKNOWN:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_status_unknown",
            references=base_reference,
        )
    if (
        reference.special_session
        == PriceLimitSpecialSession.RELISTING_FIRST_DAY
        and reference.lifecycle_status
        != SecurityLifecycleStatus.RELISTED
    ) or (
        reference.special_session
        == PriceLimitSpecialSession.DELISTING_FIRST_DAY
        and reference.lifecycle_status
        != SecurityLifecycleStatus.DELISTING
    ):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_special_session_conflict",
            references=base_reference,
        )

    as_of_utc = _aware_utc(as_of)
    source_time = _aware_utc(reference.source_time)
    fetched_at = _aware_utc(reference.fetched_at)
    if source_time is None or fetched_at is None:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_timestamp_timezone_missing",
            references=base_reference,
        )
    source_age = (as_of_utc - source_time).total_seconds()
    if source_age < -MAXIMUM_FUTURE_SKEW_SECONDS:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_from_future",
            references=base_reference,
        )
    if source_age > MAXIMUM_SOURCE_AGE_SECONDS:
        return _resolution(
            status=ResearchFeatureStatus.STALE,
            reason="official_reference_stale",
            references=base_reference,
        )
    if (
        fetched_at + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
        < source_time
    ):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_fetched_before_source",
            references=base_reference,
        )
    if (
        fetched_at - as_of_utc
    ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_reference_fetched_in_future",
            references=base_reference,
        )

    if quote.previous_close is None:
        return _resolution(
            status=ResearchFeatureStatus.MISSING,
            reason="previous_close_missing",
            references=base_reference,
        )
    try:
        catalog_rule = resolve_trading_rule_catalog(
            exchange=lifecycle.exchange,
            board=lifecycle.board,
            lifecycle_status=lifecycle.lifecycle_status,
            trading_date=trading_date,
            listed_trading_day_count=(
                lifecycle.listed_trading_day_count
            ),
            special_session=reference.special_session,
            previous_close=quote.previous_close,
        )
    except ValueError:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="trading_rule_catalog_unsupported",
            references=base_reference,
        )

    catalog_reference = {
        "kind": "official_rule_catalog",
        "sourceContractId": catalog_rule.source_contract_id,
        "sourceName": catalog_rule.source_name,
        "sourceUrl": catalog_rule.source_url,
        "documentId": catalog_rule.document_id,
        "ruleVersion": catalog_rule.rule_version,
    }
    references = base_reference + (catalog_reference,)
    if reference.price_limit_mode != catalog_rule.price_limit_mode:
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_rule_catalog_conflict",
            catalog_rule=catalog_rule,
            references=references,
        )
    if reference.price_limit_mode == PriceLimitMode.BOUNDED:
        if not (
            _positive_finite(reference.upper_limit_price)
            and _positive_finite(reference.lower_limit_price)
        ) or (
            float(reference.lower_limit_price)
            >= float(reference.upper_limit_price)
        ):
            return _resolution(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="official_bounded_price_limit_invalid",
                catalog_rule=catalog_rule,
                references=references,
            )
        if not (
            _price_matches(
                reference.upper_limit_price,
                catalog_rule.expected_upper_limit_price,
            )
            and _price_matches(
                reference.lower_limit_price,
                catalog_rule.expected_lower_limit_price,
            )
        ):
            return _resolution(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="official_rule_catalog_conflict",
                catalog_rule=catalog_rule,
                references=references,
            )
    elif (
        reference.upper_limit_price is not None
        or reference.lower_limit_price is not None
    ):
        return _resolution(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reason="official_no_limit_boundary_present",
            catalog_rule=catalog_rule,
            references=references,
        )

    secondary_values = (
        quote.upper_limit_price_source,
        quote.lower_limit_price_source,
    )
    if (
        reference.price_limit_mode == PriceLimitMode.BOUNDED
        and any(value is not None for value in secondary_values)
    ):
        if any(
            value is not None and not _positive_finite(value)
            for value in secondary_values
        ):
            return _resolution(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="secondary_price_limit_invalid",
                catalog_rule=catalog_rule,
                references=references,
            )
        secondary_conflict = (
            quote.upper_limit_price_source is not None
            and not _price_matches(
                quote.upper_limit_price_source,
                reference.upper_limit_price,
            )
        ) or (
            quote.lower_limit_price_source is not None
            and not _price_matches(
                quote.lower_limit_price_source,
                reference.lower_limit_price,
            )
        )
        references += ({
            "kind": "secondary_price_limit_crosscheck",
            "sourceContractId": quote_source_contract_id,
            "sourceGrade": (
                TradabilitySourceGrade.
                SECONDARY_CROSSCHECK.value
            ),
        },)
        if secondary_conflict:
            return _resolution(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reason="secondary_price_limit_conflict",
                catalog_rule=catalog_rule,
                references=references,
            )

    feature_input = LeaderTradabilityFeatureInput(
        as_of=as_of,
        quote=quote,
        quote_source_contract_id=quote_source_contract_id,
        quote_source_status=quote_source_status,
        lifecycle=lifecycle,
        trading_status=LeaderTradingStatusEvidence(
            symbol=quote.symbol,
            trading_date=trading_date,
            status=reference.trading_status,
            source_contract_id=reference.source_contract_id,
            source_name=reference.source_name,
            source_time=reference.source_time,
            fetched_at=reference.fetched_at,
        ),
        trading_rule=LeaderTradingRuleEvidence(
            symbol=quote.symbol,
            trading_date=trading_date,
            rule_version=catalog_rule.rule_version,
            price_limit_mode=reference.price_limit_mode,
            upper_limit_price=reference.upper_limit_price,
            lower_limit_price=reference.lower_limit_price,
            source_contract_id=(
                f"{reference.source_contract_id}:"
                f"{catalog_rule.rule_version}"
            ),
            source_name=catalog_rule.source_name,
            source_url=catalog_rule.source_url,
            published_at=_catalog_datetime(
                catalog_rule.published_on
            ),
            effective_from=_catalog_datetime(
                catalog_rule.effective_from
            ),
            effective_until=(
                _catalog_datetime(
                    catalog_rule.effective_until,
                    end=True,
                )
                if catalog_rule.effective_until is not None
                else None
            ),
        ),
    )
    preflight = build_leader_tradability_features(feature_input)
    if preflight.status != ResearchFeatureStatus.READY:
        return LeaderTradabilitySourceResolution(
            status=preflight.status,
            feature_input=None,
            catalog_rule=catalog_rule,
            reasons=(
                preflight.reasons
                or ("tradability_c1_preflight_failed",)
            ),
            references=references,
        )
    return LeaderTradabilitySourceResolution(
        status=ResearchFeatureStatus.READY,
        feature_input=feature_input,
        catalog_rule=catalog_rule,
        references=references,
    )
