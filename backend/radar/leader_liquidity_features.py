"""阶段6L-B2流动性与可交易性研究证据。

本模块只消费调用方已经取得的当期行情，不抓取数据、不连接数据库，也不把
部分可用的当前值转换为正式流动性分数或门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from radar.contracts import QuoteSnapshot, UnitVerificationStatus
from radar.leader_research_features import (
    MAXIMUM_FUTURE_SKEW_SECONDS,
    MAXIMUM_SOURCE_AGE_SECONDS,
    ResearchFeatureStatus,
)


LEADER_LIQUIDITY_FEATURE_VERSION = (
    "radar-leader-liquidity-feature-v1"
)
UTC = timezone.utc


def _round_optional(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


@dataclass(frozen=True)
class LiquidityResearchMetric:
    value: Optional[float]
    unit: Optional[str]
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = ()

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "value": _round_optional(self.value),
            "unit": self.unit,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderLiquidityFeatureResult:
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = ()
    source_contract_ids: Tuple[str, ...] = ()
    metrics: Mapping[str, LiquidityResearchMetric] = field(
        default_factory=dict
    )

    def to_evidence(self) -> Dict[str, Any]:
        missing_metric = LiquidityResearchMetric(
            value=None,
            unit=None,
            status=ResearchFeatureStatus.MISSING,
        )
        return {
            "formulaVersion": LEADER_LIQUIDITY_FEATURE_VERSION,
            "status": self.status.value,
            "scoreReady": False,
            "formalUsable": False,
            "researchScore": None,
            "sourceContractIds": list(self.source_contract_ids),
            "metrics": {
                name: metric.to_evidence()
                for name, metric in self.metrics.items()
            },
            "tradability": {
                "tradingStatus": {
                    **missing_metric.to_evidence(),
                    "reasons": ["trading_status_missing"],
                },
                "priceLimitState": {
                    **missing_metric.to_evidence(),
                    "reasons": ["price_limit_state_missing"],
                },
                "onePriceLimitState": {
                    **missing_metric.to_evidence(),
                    "reasons": ["one_price_limit_state_missing"],
                },
                "spreadBps": LiquidityResearchMetric(
                    value=None,
                    unit="bps",
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                    reasons=("spread_source_unverified",),
                ).to_evidence(),
            },
            "reasons": list(self.reasons),
        }


def _result(
    *,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    source_contract_id: str,
    metrics: Optional[
        Mapping[str, LiquidityResearchMetric]
    ] = None,
) -> LeaderLiquidityFeatureResult:
    return LeaderLiquidityFeatureResult(
        status=status,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        source_contract_ids=(
            (source_contract_id,)
            if source_contract_id.strip()
            else ()
        ),
        metrics=metrics or {},
    )


def _aware_utc(value: datetime) -> Optional[datetime]:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _non_negative_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def build_leader_liquidity_features(
    *,
    as_of: datetime,
    quote: QuoteSnapshot,
    source_contract_id: str,
    source_status: ResearchFeatureStatus,
) -> LeaderLiquidityFeatureResult:
    """构建当前流动性原始证据，正式评分和门禁始终关闭。"""

    if source_status != ResearchFeatureStatus.READY:
        return _result(
            status=source_status,
            reasons=(f"quote_source_{source_status.value}",),
            source_contract_id=source_contract_id,
        )

    normalized_as_of = _aware_utc(as_of)
    source_time = (
        _aware_utc(quote.source_time)
        if quote.source_time is not None
        else None
    )
    fetched_at = _aware_utc(quote.fetched_at)
    if normalized_as_of is None or fetched_at is None:
        return _result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("quote_timestamp_timezone_missing",),
            source_contract_id=source_contract_id,
        )
    if source_time is None:
        return _result(
            status=ResearchFeatureStatus.MISSING,
            reasons=("quote_source_time_missing",),
            source_contract_id=source_contract_id,
        )
    age_seconds = (normalized_as_of - source_time).total_seconds()
    if age_seconds > MAXIMUM_SOURCE_AGE_SECONDS:
        return _result(
            status=ResearchFeatureStatus.STALE,
            reasons=("quote_source_stale",),
            source_contract_id=source_contract_id,
        )
    if age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS:
        return _result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("quote_source_time_future",),
            source_contract_id=source_contract_id,
        )
    fetched_age_seconds = (
        normalized_as_of - fetched_at
    ).total_seconds()
    if fetched_age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS:
        return _result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("quote_fetched_at_future",),
            source_contract_id=source_contract_id,
        )
    if fetched_at < source_time:
        return _result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("quote_fetched_before_source_time",),
            source_contract_id=source_contract_id,
        )

    reasons = [
        "same_time_turnover_history_missing",
        "trading_status_missing",
        "price_limit_state_missing",
        "one_price_limit_state_missing",
        "spread_source_unverified",
    ]
    turnover_amount_cny = _non_negative_number(
        quote.turnover_amount_cny
    )
    if (
        quote.turnover_amount_unit_status
        == UnitVerificationStatus.VERIFIED
        and turnover_amount_cny is not None
    ):
        turnover_amount_metric = LiquidityResearchMetric(
            value=turnover_amount_cny,
            unit="CNY",
            status=ResearchFeatureStatus.READY,
        )
    else:
        reasons.append("turnover_amount_unit_unverified")
        turnover_amount_metric = LiquidityResearchMetric(
            value=None,
            unit="CNY",
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("turnover_amount_unit_unverified",),
        )

    turnover_rate = _non_negative_number(
        quote.turnover_rate_percent
    )
    if turnover_rate is None:
        reasons.append("turnover_rate_invalid")
        turnover_rate_metric = LiquidityResearchMetric(
            value=None,
            unit="percent",
            status=ResearchFeatureStatus.MISSING,
            reasons=("turnover_rate_invalid",),
        )
    else:
        turnover_rate_metric = LiquidityResearchMetric(
            value=turnover_rate,
            unit="percent",
            status=ResearchFeatureStatus.READY,
        )

    metrics = {
        "turnoverAmountCny": turnover_amount_metric,
        "turnoverRatePercent": turnover_rate_metric,
        "sameTimeTurnoverBaseline": LiquidityResearchMetric(
            value=None,
            unit="CNY",
            status=ResearchFeatureStatus.MISSING,
            reasons=("same_time_turnover_history_missing",),
        ),
    }
    return _result(
        status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
        reasons=reasons,
        source_contract_id=source_contract_id,
        metrics=metrics,
    )
