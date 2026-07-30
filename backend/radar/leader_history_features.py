"""阶段6L-B1同口径历史研究特征。

本模块只接收调用方显式提供的历史序列并做内存计算，不抓取数据、不连接
数据库，也不把原始研究指标转换为正式龙头分数或状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus


LEADER_HISTORY_FEATURE_VERSION = "radar-leader-history-feature-v1"
REQUIRED_OBSERVATIONS = 21
MAXIMUM_FUTURE_SKEW_SECONDS = 5
RETURN_WINDOWS = (3, 5, 10, 20)
UTC = timezone.utc


class HistorySeriesRole(str, Enum):
    CANDIDATE_SECURITY = "candidate_security"
    INDUSTRY_BENCHMARK = "industry_benchmark"
    BOARD_INDEX = "board_index"


class HistoryAdjustmentBasis(str, Enum):
    FORWARD_ADJUSTED = "forward_adjusted"
    POINT_IN_TIME_EQUAL_WEIGHT_PRICE_RETURN = (
        "point_in_time_equal_weight_price_return"
    )
    CONTINUOUS_INDEX = "continuous_index"


@dataclass(frozen=True)
class AdjustedHistoryPoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class AdjustedHistorySeries:
    role: HistorySeriesRole
    symbol: str
    source_contract_id: str
    adjustment_basis: HistoryAdjustmentBasis
    return_basis: str
    status: ResearchFeatureStatus
    source_time: datetime
    fetched_at: datetime
    points: Tuple[AdjustedHistoryPoint, ...]


@dataclass(frozen=True)
class LeaderHistoryFeatureInput:
    as_of: datetime
    expected_trade_dates: Tuple[date, ...]
    candidate: AdjustedHistorySeries
    industry_benchmark: AdjustedHistorySeries
    board_index: AdjustedHistorySeries


def _round_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Mapping):
        return {
            str(key): _round_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_round_value(item) for item in value]
    return value


@dataclass(frozen=True)
class LeaderHistoryFeatureResult:
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = ()
    source_contract_ids: Tuple[str, ...] = ()
    history_end_date: Optional[date] = None
    observation_count: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": LEADER_HISTORY_FEATURE_VERSION,
            "status": self.status.value,
            "scoreReady": False,
            "researchScore": None,
            "historyEndDate": (
                self.history_end_date.isoformat()
                if self.history_end_date is not None
                else None
            ),
            "observationCount": self.observation_count,
            "sourceContractIds": list(self.source_contract_ids),
            "metrics": _round_value(self.metrics),
            "reasons": list(self.reasons),
        }


def missing_leader_history_features(
    reasons: Sequence[str] = ("continuity_history_missing",),
    *,
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING,
) -> LeaderHistoryFeatureResult:
    return LeaderHistoryFeatureResult(
        status=status,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _aware_utc(value: datetime) -> Optional[datetime]:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _invalid_result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    series: Sequence[AdjustedHistorySeries],
) -> LeaderHistoryFeatureResult:
    return LeaderHistoryFeatureResult(
        status=status,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        source_contract_ids=tuple(
            item.source_contract_id
            for item in series
            if item.source_contract_id.strip()
        ),
    )


def _series_status_result(
    series_by_name: Sequence[Tuple[str, AdjustedHistorySeries]],
) -> Optional[LeaderHistoryFeatureResult]:
    priority = (
        ResearchFeatureStatus.SOURCE_FAILED,
        ResearchFeatureStatus.STALE,
        ResearchFeatureStatus.SOURCE_UNVERIFIED,
        ResearchFeatureStatus.MISSING,
    )
    all_series = tuple(item for _, item in series_by_name)
    for status in priority:
        blocked = tuple(
            f"{name}_source_{status.value}"
            for name, item in series_by_name
            if item.status == status
        )
        if blocked:
            return _invalid_result(status, blocked, all_series)
    return None


def _price_is_valid(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _period_returns(prices: Sequence[float]) -> Dict[str, float]:
    return {
        f"{window}d": prices[-1] / prices[-1 - window] - 1
        for window in RETURN_WINDOWS
    }


def _daily_returns(prices: Sequence[float]) -> Tuple[float, ...]:
    return tuple(
        prices[index] / prices[index - 1] - 1
        for index in range(1, len(prices))
    )


def _outperformance_days(
    candidate_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    window: int,
) -> int:
    return sum(
        candidate > benchmark
        for candidate, benchmark in zip(
            candidate_returns[-window:],
            benchmark_returns[-window:],
        )
    )


def _maximum_drawdown(
    prices: Sequence[float],
) -> Tuple[float, int]:
    peak = prices[0]
    maximum_drawdown = 0.0
    trough_index = 0
    for index, price in enumerate(prices):
        peak = max(peak, price)
        drawdown = price / peak - 1
        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
            trough_index = index
    return maximum_drawdown, trough_index


def _recovery_from_trough(
    prices: Sequence[float],
    maximum_drawdown: float,
    trough_index: int,
) -> Optional[float]:
    if maximum_drawdown >= 0:
        return None
    return prices[-1] / prices[trough_index] - 1


def build_leader_history_features(
    input_value: LeaderHistoryFeatureInput,
) -> LeaderHistoryFeatureResult:
    """校验同口径历史并计算压缩研究指标。"""

    as_of = _aware_utc(input_value.as_of)
    series_by_name = (
        ("candidate", input_value.candidate),
        ("industry", input_value.industry_benchmark),
        ("board", input_value.board_index),
    )
    all_series = tuple(item for _, item in series_by_name)
    if as_of is None:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("history_as_of_timezone_missing",),
            all_series,
        )

    blocked_result = _series_status_result(series_by_name)
    if blocked_result is not None:
        return blocked_result

    expected_roles = {
        "candidate": HistorySeriesRole.CANDIDATE_SECURITY,
        "industry": HistorySeriesRole.INDUSTRY_BENCHMARK,
        "board": HistorySeriesRole.BOARD_INDEX,
    }
    expected_adjustments = {
        "candidate": HistoryAdjustmentBasis.FORWARD_ADJUSTED,
        "industry": (
            HistoryAdjustmentBasis.POINT_IN_TIME_EQUAL_WEIGHT_PRICE_RETURN
        ),
        "board": HistoryAdjustmentBasis.CONTINUOUS_INDEX,
    }
    contract_reasons = []
    for name, item in series_by_name:
        if item.role != expected_roles[name]:
            contract_reasons.append(f"{name}_series_role_unverified")
        if item.adjustment_basis != expected_adjustments[name]:
            contract_reasons.append(
                f"{name}_adjustment_basis_unverified"
            )
        if item.return_basis != "price_return":
            contract_reasons.append(f"{name}_return_basis_unverified")
        if not item.symbol.strip() or not item.source_contract_id.strip():
            contract_reasons.append(f"{name}_source_identity_missing")
    if contract_reasons:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            contract_reasons,
            all_series,
        )

    timestamp_reasons = []
    future_limit = as_of + timedelta(
        seconds=MAXIMUM_FUTURE_SKEW_SECONDS
    )
    for _, item in series_by_name:
        source_time = _aware_utc(item.source_time)
        fetched_at = _aware_utc(item.fetched_at)
        if source_time is None or fetched_at is None:
            timestamp_reasons.append(
                "history_timestamp_timezone_missing"
            )
            continue
        if source_time > future_limit:
            timestamp_reasons.append("history_source_time_future")
        if fetched_at > future_limit:
            timestamp_reasons.append("history_fetched_at_future")
        if fetched_at < source_time:
            timestamp_reasons.append(
                "history_fetched_before_source_time"
            )
    if timestamp_reasons:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            timestamp_reasons,
            all_series,
        )

    expected_dates = tuple(input_value.expected_trade_dates)
    if len(expected_dates) < REQUIRED_OBSERVATIONS:
        return _invalid_result(
            ResearchFeatureStatus.MISSING,
            ("history_observations_below_21",),
            all_series,
        )
    if (
        len(expected_dates) != len(set(expected_dates))
        or expected_dates != tuple(sorted(expected_dates))
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("expected_trade_dates_invalid",),
            all_series,
        )
    expected_window = expected_dates[-REQUIRED_OBSERVATIONS:]
    price_windows = {}
    for name, item in series_by_name:
        point_dates = tuple(point.trade_date for point in item.points)
        if (
            len(point_dates) != len(set(point_dates))
            or point_dates != tuple(sorted(point_dates))
            or point_dates[-REQUIRED_OBSERVATIONS:] != expected_window
        ):
            return _invalid_result(
                ResearchFeatureStatus.MISSING,
                ("history_dates_misaligned",),
                all_series,
            )
        point_window = item.points[-REQUIRED_OBSERVATIONS:]
        if (
            len(point_window) != REQUIRED_OBSERVATIONS
            or any(
                not _price_is_valid(point.close)
                for point in point_window
            )
        ):
            return _invalid_result(
                ResearchFeatureStatus.MISSING,
                ("history_price_invalid",),
                all_series,
            )
        price_windows[name] = tuple(
            float(point.close)
            for point in point_window
        )

    price_returns = {
        name: _period_returns(prices)
        for name, prices in price_windows.items()
    }
    daily_returns = {
        name: _daily_returns(prices)
        for name, prices in price_windows.items()
    }
    drawdowns = {}
    trough_indices = {}
    recoveries = {}
    for name, prices in price_windows.items():
        drawdown, trough_index = _maximum_drawdown(prices)
        drawdowns[name] = drawdown
        trough_indices[name] = trough_index
        recoveries[name] = _recovery_from_trough(
            prices,
            drawdown,
            trough_index,
        )

    excess_returns = {
        "vsIndustry": {
            key: price_returns["candidate"][key]
            - price_returns["industry"][key]
            for key in price_returns["candidate"]
        },
        "vsBoard": {
            key: price_returns["candidate"][key]
            - price_returns["board"][key]
            for key in price_returns["candidate"]
        },
    }
    outperformance_days = {
        f"last{window}": {
            "vsIndustry": _outperformance_days(
                daily_returns["candidate"],
                daily_returns["industry"],
                window,
            ),
            "vsBoard": _outperformance_days(
                daily_returns["candidate"],
                daily_returns["board"],
                window,
            ),
        }
        for window in (5, 10)
    }
    metrics = {
        "priceReturns": price_returns,
        "excessReturns": excess_returns,
        "outperformanceDays": outperformance_days,
        "maximumDrawdown20d": drawdowns,
        "drawdownAdvantage20d": {
            "vsIndustry": (
                drawdowns["candidate"] - drawdowns["industry"]
            ),
            "vsBoard": (
                drawdowns["candidate"] - drawdowns["board"]
            ),
        },
        "recoveryFromTrough20d": recoveries,
    }
    reasons = (
        ("recovery_event_absent",)
        if recoveries["candidate"] is None
        else ()
    )
    return LeaderHistoryFeatureResult(
        status=ResearchFeatureStatus.READY,
        reasons=reasons,
        source_contract_ids=tuple(
            item.source_contract_id
            for item in all_series
        ),
        history_end_date=expected_window[-1],
        observation_count=REQUIRED_OBSERVATIONS,
        metrics=metrics,
    )
