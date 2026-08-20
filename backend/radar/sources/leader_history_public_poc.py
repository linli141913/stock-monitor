"""阶段6L-B1免费历史输入POC的纯内存合同与组装。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_history_features import (
    AdjustedHistoryPoint,
    AdjustedHistorySeries,
    HistoryAdjustmentBasis,
    HistorySeriesRole,
    LeaderHistoryFeatureInput,
)
from radar.leader_research_features import ResearchFeatureStatus


PUBLIC_HISTORY_POC_CONTRACT_ID = "radar-leader-public-history-poc-v1"
TENCENT_HISTORY_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
)
TENCENT_QFQ_CONTRACT_ID = "tencent-qfq-daily-history-v1"
TENCENT_INDEX_CONTRACT_ID = "tencent-continuous-index-daily-v1"
MAXIMUM_INDUSTRY_MEMBER_COUNT = 80
REQUIRED_HISTORY_DATES = 21
MAXIMUM_FUTURE_SKEW_SECONDS = 5
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicHistoryResolutionStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class PublicHistorySeries:
    symbol: str
    source_contract_id: str
    source_url: str
    adjustment_basis: HistoryAdjustmentBasis
    fetched_at: datetime
    content_sha256: str
    points: Tuple[AdjustedHistoryPoint, ...]


@dataclass(frozen=True)
class PointInTimeIndustryMembership:
    release_id: str
    source_contract_id: str
    industry_code: str
    candidate_symbol: str
    member_symbols: Tuple[str, ...]
    published_date: date
    classification_start_date: date
    first_observed_at: datetime
    fetched_at: datetime
    document_sha256: str
    excluded_out_of_scope_count: int = 0


@dataclass(frozen=True)
class PublicHistoryPocQuery:
    as_of: datetime
    expected_trade_dates: Tuple[date, ...]
    candidate_symbol: str
    board_index_symbol: str
    membership: PointInTimeIndustryMembership
    series_by_symbol: Mapping[str, PublicHistorySeries] = field(repr=False)
    maximum_member_count: int = MAXIMUM_INDUSTRY_MEMBER_COUNT


@dataclass(frozen=True)
class PublicHistoryPocResult:
    resolution_status: str
    real_poc_status: str
    reasons: Tuple[str, ...]
    member_count: int
    expected_series_count: int
    complete_series_count: int
    series_coverage: float
    history_input: Optional[LeaderHistoryFeatureInput] = field(
        default=None,
        repr=False,
    )
    excluded_out_of_scope_count: int = 0
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> dict:
        return {
            "contractId": PUBLIC_HISTORY_POC_CONTRACT_ID,
            "resolutionStatus": self.resolution_status,
            "realPocStatus": self.real_poc_status,
            "reasons": list(self.reasons),
            "memberCount": self.member_count,
            "excludedOutOfScopeCount": self.excluded_out_of_scope_count,
            "expectedSeriesCount": self.expected_series_count,
            "completeSeriesCount": self.complete_series_count,
            "seriesCoverage": round(self.series_coverage, 6),
            "historyInputReady": self.history_input is not None,
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _valid_symbol(value: str) -> bool:
    return re.fullmatch(r"[036][0-9]{5}", value) is not None


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def parse_tencent_history_payload(
    *,
    symbol: str,
    query_symbol: str,
    payload: Any,
    expected_trade_dates: Sequence[date],
    fetched_at: datetime,
    adjustment_basis: HistoryAdjustmentBasis,
) -> PublicHistorySeries:
    """解析腾讯日K载荷，只保留官方窗口内的收盘价。"""

    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        raise ValueError("tencent_history_payload_invalid")
    stock_data = (payload.get("data") or {}).get(query_symbol)
    if not isinstance(stock_data, Mapping):
        raise ValueError("tencent_history_symbol_missing")
    if adjustment_basis == HistoryAdjustmentBasis.FORWARD_ADJUSTED:
        rows = stock_data.get("qfqday")
        contract_id = TENCENT_QFQ_CONTRACT_ID
    elif adjustment_basis == HistoryAdjustmentBasis.CONTINUOUS_INDEX:
        rows = stock_data.get("day")
        contract_id = TENCENT_INDEX_CONTRACT_ID
    else:
        raise ValueError("tencent_history_adjustment_unverified")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("tencent_history_rows_missing")

    expected = set(expected_trade_dates)
    points = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        if len(row) < 3:
            continue
        try:
            trade_date = date.fromisoformat(str(row[0])[:10])
            close = float(row[2])
        except (TypeError, ValueError):
            continue
        if trade_date in expected:
            points.append(AdjustedHistoryPoint(
                trade_date=trade_date,
                close=close,
            ))
    points.sort(key=lambda item: item.trade_date)
    return PublicHistorySeries(
        symbol=symbol,
        source_contract_id=contract_id,
        source_url=TENCENT_HISTORY_URL,
        adjustment_basis=adjustment_basis,
        fetched_at=fetched_at,
        content_sha256=_canonical_sha256(payload),
        points=tuple(points),
    )


def _series_is_complete(
    series: PublicHistorySeries,
    *,
    symbol: str,
    expected_dates: Tuple[date, ...],
    expected_adjustment: HistoryAdjustmentBasis,
    as_of: datetime,
) -> bool:
    expected_contract = (
        TENCENT_QFQ_CONTRACT_ID
        if expected_adjustment == HistoryAdjustmentBasis.FORWARD_ADJUSTED
        else TENCENT_INDEX_CONTRACT_ID
    )
    if (
        not isinstance(series, PublicHistorySeries)
        or series.symbol != symbol
        or series.source_contract_id != expected_contract
        or series.source_url != TENCENT_HISTORY_URL
        or series.adjustment_basis != expected_adjustment
        or not _aware(series.fetched_at)
        or series.fetched_at > as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
        or _SHA256_PATTERN.fullmatch(series.content_sha256) is None
        or tuple(item.trade_date for item in series.points) != expected_dates
    ):
        return False
    return all(
        not isinstance(item.close, bool)
        and isinstance(item.close, (int, float))
        and math.isfinite(float(item.close))
        and float(item.close) > 0
        for item in series.points
    )


def _equal_weight_index(
    member_series: Sequence[PublicHistorySeries],
) -> Tuple[AdjustedHistoryPoint, ...]:
    values = [1.0]
    for index in range(1, REQUIRED_HISTORY_DATES):
        daily_return = sum(
            series.points[index].close / series.points[index - 1].close - 1
            for series in member_series
        ) / len(member_series)
        values.append(values[-1] * (1 + daily_return))
    dates = tuple(item.trade_date for item in member_series[0].points)
    return tuple(
        AdjustedHistoryPoint(trade_date=day, close=value)
        for day, value in zip(dates, values)
    )


def _partial_result(
    *,
    reasons: Sequence[str],
    member_count: int,
    expected_series_count: int,
    complete_series_count: int,
    excluded_out_of_scope_count: int,
) -> PublicHistoryPocResult:
    coverage = (
        complete_series_count / expected_series_count
        if expected_series_count
        else 0.0
    )
    return PublicHistoryPocResult(
        resolution_status=PublicHistoryResolutionStatus.PARTIAL.value,
        real_poc_status="not_run",
        reasons=tuple(dict.fromkeys(reasons)),
        member_count=member_count,
        expected_series_count=expected_series_count,
        complete_series_count=complete_series_count,
        series_coverage=coverage,
        excluded_out_of_scope_count=excluded_out_of_scope_count,
    )


def run_public_history_input_poc(
    query: PublicHistoryPocQuery,
) -> PublicHistoryPocResult:
    """严格组装研究输入；Fixture永远不能声明真实POC完成。"""

    membership = query.membership
    members = tuple(membership.member_symbols)
    expected_series = tuple(dict.fromkeys(
        (*members, query.board_index_symbol)
    ))
    expected_count = len(expected_series)
    reasons = []
    excluded_count = membership.excluded_out_of_scope_count
    if (
        not isinstance(excluded_count, int)
        or isinstance(excluded_count, bool)
        or excluded_count < 0
    ):
        reasons.append("industry_membership_exclusion_count_invalid")
        excluded_count = 0

    if not _aware(query.as_of):
        reasons.append("history_as_of_timezone_missing")
    expected_dates = tuple(query.expected_trade_dates)
    if (
        len(expected_dates) != REQUIRED_HISTORY_DATES
        or expected_dates != tuple(sorted(expected_dates))
        or len(expected_dates) != len(set(expected_dates))
    ):
        reasons.append("official_trade_dates_invalid")
    if expected_dates and expected_dates[-1] > query.as_of.date():
        reasons.append("official_trade_dates_future")
    if (
        not _valid_symbol(query.candidate_symbol)
        or not re.fullmatch(r"(?:sh|sz)\d{6}", query.board_index_symbol)
    ):
        reasons.append("history_query_symbol_invalid")
    if (
        membership.candidate_symbol != query.candidate_symbol
        or query.candidate_symbol not in members
        or not members
        or len(members) != len(set(members))
        or any(not _valid_symbol(symbol) for symbol in members)
    ):
        reasons.append("industry_membership_identity_invalid")
    if (
        not isinstance(query.maximum_member_count, int)
        or isinstance(query.maximum_member_count, bool)
        or query.maximum_member_count < 1
        or query.maximum_member_count > 10000
    ):
        reasons.append("industry_member_budget_invalid")
    elif len(members) > query.maximum_member_count:
        reasons.append("industry_member_budget_exceeded")
    if (
        not membership.release_id.strip()
        or not membership.source_contract_id.strip()
        or not membership.industry_code.strip()
        or _SHA256_PATTERN.fullmatch(membership.document_sha256) is None
    ):
        reasons.append("industry_membership_source_invalid")
    if not _aware(membership.first_observed_at) or not _aware(
        membership.fetched_at
    ):
        reasons.append("industry_membership_time_invalid")
    elif (
        membership.first_observed_at > membership.fetched_at
        or membership.fetched_at > query.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        reasons.append("industry_membership_time_invalid")
    if expected_dates:
        first_date = expected_dates[0]
        last_date = expected_dates[-1]
        if membership.classification_start_date > first_date:
            reasons.append("industry_membership_effective_window_incomplete")
        if membership.published_date > last_date:
            reasons.append("industry_membership_publication_future")
        if (
            _aware(membership.first_observed_at)
            and membership.first_observed_at.date() > first_date
        ):
            reasons.append("industry_membership_forward_window_incomplete")

    complete = {}
    for symbol in expected_series:
        series = query.series_by_symbol.get(symbol)
        adjustment = (
            HistoryAdjustmentBasis.CONTINUOUS_INDEX
            if symbol == query.board_index_symbol
            else HistoryAdjustmentBasis.FORWARD_ADJUSTED
        )
        if series is not None and _series_is_complete(
            series,
            symbol=symbol,
            expected_dates=expected_dates,
            expected_adjustment=adjustment,
            as_of=query.as_of,
        ):
            complete[symbol] = series
    if len(complete) != expected_count:
        reasons.append("history_series_dates_incomplete")

    if reasons:
        return _partial_result(
            reasons=reasons,
            member_count=len(members),
            expected_series_count=expected_count,
            complete_series_count=len(complete),
            excluded_out_of_scope_count=excluded_count,
        )

    member_series = tuple(complete[symbol] for symbol in members)
    candidate_series = complete[query.candidate_symbol]
    board_series = complete[query.board_index_symbol]
    industry_points = _equal_weight_index(member_series)
    source_time = datetime.combine(
        expected_dates[-1],
        time(15, 0),
        tzinfo=SHANGHAI_TZ,
    )
    fetched_at = max(
        item.fetched_at for item in complete.values()
    )
    history_input = LeaderHistoryFeatureInput(
        as_of=query.as_of,
        expected_trade_dates=expected_dates,
        candidate=AdjustedHistorySeries(
            role=HistorySeriesRole.CANDIDATE_SECURITY,
            symbol=query.candidate_symbol,
            source_contract_id=candidate_series.source_contract_id,
            adjustment_basis=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
            return_basis="price_return",
            status=ResearchFeatureStatus.READY,
            source_time=source_time,
            fetched_at=fetched_at,
            points=candidate_series.points,
        ),
        industry_benchmark=AdjustedHistorySeries(
            role=HistorySeriesRole.INDUSTRY_BENCHMARK,
            symbol=membership.industry_code,
            source_contract_id=(
                f"{membership.source_contract_id}+{TENCENT_QFQ_CONTRACT_ID}"
            ),
            adjustment_basis=(
                HistoryAdjustmentBasis.POINT_IN_TIME_EQUAL_WEIGHT_PRICE_RETURN
            ),
            return_basis="price_return",
            status=ResearchFeatureStatus.READY,
            source_time=source_time,
            fetched_at=fetched_at,
            points=industry_points,
        ),
        board_index=AdjustedHistorySeries(
            role=HistorySeriesRole.BOARD_INDEX,
            symbol=query.board_index_symbol,
            source_contract_id=board_series.source_contract_id,
            adjustment_basis=HistoryAdjustmentBasis.CONTINUOUS_INDEX,
            return_basis="price_return",
            status=ResearchFeatureStatus.READY,
            source_time=source_time,
            fetched_at=fetched_at,
            points=board_series.points,
        ),
    )
    return PublicHistoryPocResult(
        resolution_status=PublicHistoryResolutionStatus.READY.value,
        real_poc_status="not_run",
        reasons=("history_public_research_input_ready",),
        member_count=len(members),
        expected_series_count=expected_count,
        complete_series_count=expected_count,
        series_coverage=1.0,
        history_input=history_input,
        excluded_out_of_scope_count=excluded_count,
    )
