"""行业主线历史主动回填的纯内存合同与计算。

交易日只是历史样本索引，不是自然等待条件。本模块不联网、
不读写数据库，只消费显式冻结的官方行业版本和公开分钟行情。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from statistics import median
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.contracts import (
    IndustryClassificationRelease,
    IndustryHistoryStatus,
)
from radar.sector_rule_readiness import (
    ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID,
    SECTOR_RULE_VERSION,
    SectorHistoryCoverage,
    SectorHistoryCoverageEvidence,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
EASTMONEY_MINUTE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)
EASTMONEY_MINUTE_CONTRACT_ID = "eastmoney-a-share-5m-history-v1"
SINA_MINUTE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/"
    "CN_MarketDataService.getKLineData"
)
SINA_MINUTE_CONTRACT_ID = "sina-a-share-5m-history-v1"
TENCENT_DAILY_PROOF_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get"
)
TENCENT_DAILY_PROOF_CONTRACT_ID = (
    "tencent-qfq-daily-trading-presence-v1"
)
SECTOR_HISTORY_BACKFILL_CONTRACT_ID = "radar-sector-history-backfill-v1"
MAXIMUM_FUTURE_SKEW_SECONDS = 5
MINIMUM_HISTORY_DATE_COUNT = 21
MINIMUM_COMPARABLE_INDUSTRY_COUNT = 20


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _finite(value: Any) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
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


@dataclass(frozen=True)
class HistoricalMinuteBar:
    occurred_at: datetime
    close: float
    turnover_amount_cny: float
    volume_shares: Optional[int] = None

    def __post_init__(self) -> None:
        if not _aware(self.occurred_at):
            raise ValueError("historical_minute_time_timezone_missing")
        if not _finite(self.close) or float(self.close) <= 0:
            raise ValueError("historical_minute_close_invalid")
        if (
            not _finite(self.turnover_amount_cny)
            or float(self.turnover_amount_cny) < 0
        ):
            raise ValueError("historical_minute_turnover_invalid")
        if (
            self.volume_shares is not None
            and (
                isinstance(self.volume_shares, bool)
                or not isinstance(self.volume_shares, int)
                or self.volume_shares < 0
            )
        ):
            raise ValueError("historical_minute_volume_invalid")


@dataclass(frozen=True)
class HistoricalMinuteSeries:
    symbol: str
    source_contract_id: str
    source_url: str
    fetched_at: datetime
    content_sha256: str
    bars: Tuple[HistoricalMinuteBar, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.symbol.isdigit()
            or len(self.symbol) != 6
            or (self.source_contract_id, self.source_url) not in {
                (EASTMONEY_MINUTE_CONTRACT_ID, EASTMONEY_MINUTE_URL),
                (SINA_MINUTE_CONTRACT_ID, SINA_MINUTE_URL),
            }
            or not _aware(self.fetched_at)
            or not self.content_sha256.startswith("sha256:")
            or len(self.content_sha256) != 71
            or not isinstance(self.bars, tuple)
            or tuple(sorted(self.bars, key=lambda row: row.occurred_at))
            != self.bars
            or len({row.occurred_at for row in self.bars}) != len(self.bars)
        ):
            raise ValueError("historical_minute_series_invalid")


@dataclass(frozen=True)
class HistoricalDailyTradingProof:
    trade_date: date
    close: Decimal
    volume_shares: int
    source_contract_id: str
    source_url: str
    fetched_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.trade_date) is not date
            or not isinstance(self.close, Decimal)
            or not self.close.is_finite()
            or self.close <= 0
            or isinstance(self.volume_shares, bool)
            or not isinstance(self.volume_shares, int)
            or self.volume_shares < 0
            or self.source_contract_id != TENCENT_DAILY_PROOF_CONTRACT_ID
            or self.source_url != TENCENT_DAILY_PROOF_URL
            or not _aware(self.fetched_at)
            or not self.content_sha256.startswith("sha256:")
            or len(self.content_sha256) != 71
        ):
            raise ValueError("historical_daily_trading_proof_invalid")


@dataclass(frozen=True)
class HistoricalValue:
    trade_date: date
    value: float


@dataclass(frozen=True)
class SectorPersistenceSample:
    trade_date: date
    relative_return: float
    positive: bool


@dataclass(frozen=True)
class MarketHistoricalSample:
    trade_date: date
    equal_weighted_return: float
    market_cap_weighted_return: float


@dataclass(frozen=True)
class SectorHistoricalAnalysis:
    division_code: str
    member_count: int
    same_minute_turnover_samples: Tuple[HistoricalValue, ...]
    relative_return_samples: Tuple[HistoricalValue, ...]
    persistence_samples: Tuple[SectorPersistenceSample, ...]
    turnover_ratio_samples: Tuple[HistoricalValue, ...]
    persistence_ratio_samples: Tuple[HistoricalValue, ...]
    latest_turnover_ratio_20d: Optional[float]
    latest_persistence_positive_ratio_5d: float
    comparable_time: Optional[time] = None


@dataclass(frozen=True)
class SectorThresholdCalibrationProposal:
    status: str
    observation_date_count: int
    industry_count: int
    market_regimes: Tuple[str, ...]
    train_end_date: Optional[date]
    holdout_start_date: Optional[date]
    metric_quantiles: Mapping[str, Mapping[str, float]]
    metric_sample_counts: Mapping[str, int]
    train_observation_date_count: int
    holdout_observation_date_count: int
    holdout_metric_sample_counts: Mapping[str, int]
    reasons: Tuple[str, ...]
    formal_approval: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "status": self.status,
            "observationDateCount": self.observation_date_count,
            "industryCount": self.industry_count,
            "marketRegimes": list(self.market_regimes),
            "trainEndDate": (
                self.train_end_date.isoformat()
                if self.train_end_date is not None else None
            ),
            "holdoutStartDate": (
                self.holdout_start_date.isoformat()
                if self.holdout_start_date is not None else None
            ),
            "metricQuantiles": self.metric_quantiles,
            "metricSampleCounts": self.metric_sample_counts,
            "trainObservationDateCount": (
                self.train_observation_date_count
            ),
            "holdoutObservationDateCount": (
                self.holdout_observation_date_count
            ),
            "holdoutMetricSampleCounts": (
                self.holdout_metric_sample_counts
            ),
            "reasons": list(self.reasons),
            "formalApproval": False,
        }


@dataclass(frozen=True, repr=False)
class SectorHistoryBackfillQuery:
    radar_run_id: str
    as_of: datetime
    comparable_time: time
    expected_trade_dates: Tuple[date, ...]
    classification_release: Any = field(repr=False)
    memberships_by_division: Mapping[str, Tuple[str, ...]] = field(repr=False)
    series_by_symbol: Mapping[str, HistoricalMinuteSeries] = field(repr=False)
    total_shares_by_symbol: Mapping[str, float] = field(repr=False)
    source_batch_ids: Tuple[str, ...]
    verified_trading_dates_by_symbol: Mapping[
        str, Tuple[date, ...]
    ] = field(default_factory=dict, repr=False)
    verified_non_trading_dates_by_symbol: Mapping[
        str, Tuple[date, ...]
    ] = field(default_factory=dict, repr=False)
    daily_trading_proofs_by_symbol: Mapping[
        str, Tuple[HistoricalDailyTradingProof, ...]
    ] = field(default_factory=dict, repr=False)
    rule_version: str = SECTOR_RULE_VERSION


@dataclass(frozen=True)
class SectorHistoryBackfillResult:
    status: str
    reasons: Tuple[str, ...]
    sector_analyses: Tuple[SectorHistoricalAnalysis, ...]
    market_samples: Tuple[MarketHistoricalSample, ...]
    history_evidence: Optional[SectorHistoryCoverageEvidence] = field(
        default=None,
        repr=False,
    )
    calibration_proposal: SectorThresholdCalibrationProposal = field(
        default_factory=lambda: SectorThresholdCalibrationProposal(
            status="insufficient",
            observation_date_count=0,
            industry_count=0,
            market_regimes=(),
            train_end_date=None,
            holdout_start_date=None,
            metric_quantiles={},
            metric_sample_counts={},
            train_observation_date_count=0,
            holdout_observation_date_count=0,
            holdout_metric_sample_counts={},
            reasons=("sector_calibration_history_insufficient",),
        )
    )
    contract_id: str = SECTOR_HISTORY_BACKFILL_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "sectorCount": len(self.sector_analyses),
            "marketSampleCount": len(self.market_samples),
            "historyCoverageReady": self.history_evidence is not None,
            "calibrationProposal": self.calibration_proposal.to_evidence(),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def parse_eastmoney_minute_payload(
    *,
    symbol: str,
    payload: Any,
    expected_trade_dates: Sequence[date],
    fetched_at: datetime,
) -> HistoricalMinuteSeries:
    """解析东方财富5分钟K线；成交额保持单根bar增量语义。"""

    if not _aware(fetched_at):
        raise ValueError("eastmoney_minute_fetched_at_timezone_missing")
    if not isinstance(payload, Mapping):
        raise ValueError("eastmoney_minute_payload_invalid")
    data = payload.get("data")
    rows = data.get("klines") if isinstance(data, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("eastmoney_minute_rows_missing")
    expected = set(expected_trade_dates)
    bars = []
    for row in rows:
        if not isinstance(row, str):
            continue
        values = row.split(",")
        if len(values) < 7:
            continue
        try:
            occurred_at = datetime.strptime(
                values[0], "%Y-%m-%d %H:%M"
            ).replace(tzinfo=SHANGHAI_TZ)
            close = float(values[2])
            amount = float(values[6])
        except (InvalidOperation, TypeError, ValueError):
            continue
        if occurred_at.date() not in expected:
            continue
        bars.append(HistoricalMinuteBar(
            occurred_at=occurred_at,
            close=close,
            turnover_amount_cny=amount,
        ))
    bars.sort(key=lambda item: item.occurred_at)
    return HistoricalMinuteSeries(
        symbol=symbol,
        source_contract_id=EASTMONEY_MINUTE_CONTRACT_ID,
        source_url=EASTMONEY_MINUTE_URL,
        fetched_at=fetched_at,
        content_sha256=_canonical_sha256(payload),
        bars=tuple(bars),
    )


def parse_sina_minute_payload(
    *,
    symbol: str,
    payload: Any,
    expected_trade_dates: Sequence[date],
    fetched_at: datetime,
) -> HistoricalMinuteSeries:
    """解析新浪5分钟原始行，不使用复权派生值或补齐缺失。"""

    if not _aware(fetched_at):
        raise ValueError("sina_minute_fetched_at_timezone_missing")
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ValueError("sina_minute_payload_invalid")
    expected = set(expected_trade_dates)
    bars = []
    for row in payload:
        if not isinstance(row, Mapping):
            continue
        try:
            occurred_at = datetime.strptime(
                str(row.get("day")), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=SHANGHAI_TZ)
            close = float(row.get("close"))
            amount = float(row.get("amount"))
            raw_volume = Decimal(str(row.get("volume")))
            if raw_volume != raw_volume.to_integral_value():
                raise ValueError("sina_minute_volume_invalid")
            volume_shares = int(raw_volume)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if occurred_at.date() not in expected:
            continue
        bars.append(HistoricalMinuteBar(
            occurred_at=occurred_at,
            close=close,
            turnover_amount_cny=amount,
            volume_shares=volume_shares,
        ))
    bars.sort(key=lambda item: item.occurred_at)
    return HistoricalMinuteSeries(
        symbol=symbol,
        source_contract_id=SINA_MINUTE_CONTRACT_ID,
        source_url=SINA_MINUTE_URL,
        fetched_at=fetched_at,
        content_sha256=_canonical_sha256(payload),
        bars=tuple(bars),
    )


def _quantiles(values: Sequence[float]) -> Mapping[str, float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {}

    def value_at(percentile: float) -> float:
        position = (len(ordered) - 1) * percentile
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "q25": value_at(0.25),
        "q50": value_at(0.50),
        "q75": value_at(0.75),
    }


def _empty_result(*reasons: str) -> SectorHistoryBackfillResult:
    return SectorHistoryBackfillResult(
        status="partial",
        reasons=tuple(dict.fromkeys(reasons)),
        sector_analyses=(),
        market_samples=(),
    )


def _calibration_proposal(
    *,
    dates: Tuple[date, ...],
    market_samples: Tuple[MarketHistoricalSample, ...],
    analyses: Tuple[SectorHistoricalAnalysis, ...],
) -> SectorThresholdCalibrationProposal:
    observation_dates = dates[20:]
    regime_by_date = {
        item.trade_date: (
            "positive" if item.equal_weighted_return > 0 else "non_positive"
        )
        for item in market_samples
    }
    regimes = tuple(sorted({
        regime_by_date[value]
        for value in observation_dates
        if value in regime_by_date
    }))
    split = int(len(observation_dates) * 0.7)
    train_dates = observation_dates[:split]
    holdout_dates = observation_dates[split:]
    train_date_set = set(train_dates)
    holdout_date_set = set(holdout_dates)
    turnover_ratios = [
        sample.value
        for item in analyses
        for sample in item.turnover_ratio_samples
        if sample.trade_date in train_date_set
    ]
    persistence_values = [
        sample.value
        for item in analyses
        for sample in item.persistence_ratio_samples
        if sample.trade_date in train_date_set
    ]
    relative_values = [
        sample.value
        for item in analyses
        for sample in item.relative_return_samples
        if sample.trade_date in train_date_set
    ]
    holdout_turnover_ratios = [
        sample.value
        for item in analyses
        for sample in item.turnover_ratio_samples
        if sample.trade_date in holdout_date_set
    ]
    holdout_persistence_values = [
        sample.value
        for item in analyses
        for sample in item.persistence_ratio_samples
        if sample.trade_date in holdout_date_set
    ]
    holdout_relative_values = [
        sample.value
        for item in analyses
        for sample in item.relative_return_samples
        if sample.trade_date in holdout_date_set
    ]
    reasons = []
    if len(observation_dates) < 20:
        reasons.append("sector_calibration_history_below_20")
    if len(regimes) < 2:
        reasons.append("sector_calibration_market_regimes_below_2")
    if split < 1 or split >= len(observation_dates):
        reasons.append("sector_calibration_holdout_missing")
    train_end = observation_dates[split - 1] if split >= 1 else None
    holdout_start = (
        observation_dates[split]
        if split < len(observation_dates) else None
    )
    return SectorThresholdCalibrationProposal(
        status="proposal_ready" if not reasons else "insufficient",
        observation_date_count=len(observation_dates),
        industry_count=len(analyses),
        market_regimes=regimes,
        train_end_date=train_end,
        holdout_start_date=holdout_start,
        metric_quantiles={
            "turnoverRatio20d": _quantiles(turnover_ratios),
            "relativeReturn": _quantiles(relative_values),
            "persistencePositiveRatio5d": _quantiles(persistence_values),
        },
        metric_sample_counts={
            "turnoverRatio20d": len(turnover_ratios),
            "relativeReturn": len(relative_values),
            "persistencePositiveRatio5d": len(persistence_values),
        },
        train_observation_date_count=len(train_dates),
        holdout_observation_date_count=len(holdout_dates),
        holdout_metric_sample_counts={
            "turnoverRatio20d": len(holdout_turnover_ratios),
            "relativeReturn": len(holdout_relative_values),
            "persistencePositiveRatio5d": len(
                holdout_persistence_values
            ),
        },
        reasons=tuple(reasons),
    )


def build_sector_history_backfill(
    query: Any,
) -> SectorHistoryBackfillResult:
    """由网上已存在的历史分钟序列立即构建行业历史证据。"""

    if type(query) is not SectorHistoryBackfillQuery:
        return _empty_result("sector_history_query_unverified")
    dates = query.expected_trade_dates
    if (
        not query.radar_run_id.strip()
        or not _aware(query.as_of)
        or not isinstance(query.comparable_time, time)
        or not isinstance(dates, tuple)
        or len(dates) < MINIMUM_HISTORY_DATE_COUNT
        or dates != tuple(sorted(dates))
        or len(dates) != len(set(dates))
        or any(type(value) is not date for value in dates)
        or any(value >= query.as_of.date() for value in dates)
    ):
        return _empty_result("sector_history_query_unverified")
    release = query.classification_release
    if type(release) is not IndustryClassificationRelease:
        return _empty_result("sector_classification_release_unverified")
    if release.history_status not in {
        IndustryHistoryStatus.FORWARD_OBSERVED,
        IndustryHistoryStatus.OFFICIAL_ARCHIVE_VERIFIED,
    }:
        return _empty_result("sector_classification_history_unverified")
    if release.knowledge_effective_from.date() > dates[0]:
        return _empty_result(
            "sector_history_predates_classification_knowledge"
        )
    if (
        release.history_status == IndustryHistoryStatus.FORWARD_OBSERVED
        and release.first_observed_at.date() > dates[0]
    ):
        return _empty_result(
            "sector_history_predates_classification_observation"
        )
    memberships = query.memberships_by_division
    if (
        not isinstance(memberships, Mapping)
        or len(memberships) < MINIMUM_COMPARABLE_INDUSTRY_COUNT
    ):
        return _empty_result("sector_history_comparable_industries_below_20")
    expected_symbols = set()
    for division_code, members in memberships.items():
        if (
            not isinstance(division_code, str)
            or len(division_code) != 2
            or not division_code.isdigit()
            or not isinstance(members, tuple)
            or len(members) <= 1
            or len(members) != len(set(members))
            or any(
                not isinstance(symbol, str)
                or len(symbol) != 6
                or not symbol.isdigit()
                for symbol in members
            )
        ):
            return _empty_result("sector_history_membership_unverified")
        expected_symbols.update(members)
    if set(query.series_by_symbol) != expected_symbols:
        return _empty_result("sector_history_series_scope_incomplete")
    if set(query.total_shares_by_symbol) != expected_symbols:
        return _empty_result("sector_history_share_scope_incomplete")
    evidence_maps = (
        query.verified_trading_dates_by_symbol,
        query.verified_non_trading_dates_by_symbol,
    )
    if any(not isinstance(value, Mapping) for value in evidence_maps):
        return _empty_result("sector_history_trading_presence_unverified")
    expected_date_set = set(dates)
    for value in evidence_maps:
        for symbol, presence_dates in value.items():
            if (
                symbol not in expected_symbols
                or not isinstance(presence_dates, tuple)
                or len(presence_dates) != len(set(presence_dates))
                or any(day not in expected_date_set for day in presence_dates)
            ):
                return _empty_result(
                    "sector_history_trading_presence_unverified"
                )
    if any(
        set(query.verified_trading_dates_by_symbol.get(symbol, ()))
        & set(query.verified_non_trading_dates_by_symbol.get(symbol, ()))
        for symbol in expected_symbols
    ):
        return _empty_result("sector_history_trading_presence_conflict")
    if not isinstance(query.daily_trading_proofs_by_symbol, Mapping):
        return _empty_result("sector_history_daily_proof_unverified")
    for symbol, proofs in query.daily_trading_proofs_by_symbol.items():
        if (
            symbol not in expected_symbols
            or not isinstance(proofs, tuple)
            or any(
                type(proof) is not HistoricalDailyTradingProof
                for proof in proofs
            )
            or len({proof.trade_date for proof in proofs}) != len(proofs)
            or any(
                proof.trade_date not in expected_date_set
                or proof.trade_date not in set(
                    query.verified_trading_dates_by_symbol.get(symbol, ())
                )
                or proof.fetched_at > query.as_of + timedelta(
                    seconds=MAXIMUM_FUTURE_SKEW_SECONDS
                )
                for proof in proofs
            )
        ):
            return _empty_result("sector_history_daily_proof_unverified")

    daily_by_symbol = {}
    for symbol in sorted(expected_symbols):
        series = query.series_by_symbol.get(symbol)
        shares = query.total_shares_by_symbol.get(symbol)
        if (
            type(series) is not HistoricalMinuteSeries
            or series.symbol != symbol
            or series.fetched_at > query.as_of + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            )
            or not _finite(shares)
            or float(shares) <= 0
        ):
            return _empty_result("sector_history_series_unverified")
        grouped = {value: [] for value in dates}
        for bar in series.bars:
            if bar.occurred_at.date() in grouped:
                grouped[bar.occurred_at.date()].append(bar)
        daily = {}
        verified_trading = set(
            query.verified_trading_dates_by_symbol.get(symbol, ())
        )
        verified_non_trading = set(
            query.verified_non_trading_dates_by_symbol.get(symbol, ())
        )
        daily_proofs = {
            proof.trade_date: proof
            for proof in query.daily_trading_proofs_by_symbol.get(symbol, ())
        }
        for trade_day in dates:
            bars = sorted(grouped[trade_day], key=lambda row: row.occurred_at)
            if not bars and trade_day in verified_non_trading:
                daily[trade_day] = None
                continue
            if not bars:
                return _empty_result(
                    "sector_history_member_dates_incomplete",
                    (
                        "sector_history_member_date_missing:"
                        f"{symbol}:{trade_day.isoformat()}"
                    ),
                )
            bar_times = tuple(
                row.occurred_at.timetz().replace(tzinfo=None)
                for row in bars
            )
            comparable = [
                row for row in bars
                if row.occurred_at.timetz().replace(tzinfo=None)
                <= query.comparable_time
            ]
            complete_intraday = bool(
                bars
                and comparable
                and bar_times[0] <= time(9, 35)
                and query.comparable_time in bar_times
                and bar_times[-1] >= time(15, 0)
            )
            internal_zero_trade_verified = bool(
                not complete_intraday
                and comparable
                and bar_times[0] <= time(9, 35)
                and bar_times[-1] >= time(15, 0)
                and trade_day in verified_trading
            )
            proof = daily_proofs.get(trade_day)
            sparse_intraday_verified = bool(
                not complete_intraday
                and not internal_zero_trade_verified
                and trade_day in verified_trading
                and series.source_contract_id == SINA_MINUTE_CONTRACT_ID
                and proof is not None
                and all(row.volume_shares is not None for row in bars)
                and sum(row.volume_shares or 0 for row in bars)
                == proof.volume_shares
                and Decimal(str(bars[-1].close)) == proof.close
            )
            if (
                not complete_intraday
                and not internal_zero_trade_verified
                and not sparse_intraday_verified
            ):
                return _empty_result(
                    "sector_history_member_dates_incomplete",
                    (
                        "sector_history_member_intraday_unverified:"
                        f"{symbol}:{trade_day.isoformat()}"
                    ),
                )
            daily[trade_day] = (
                bars[-1].close,
                sum(row.turnover_amount_cny for row in comparable),
            )
        daily_by_symbol[symbol] = daily

    market_samples = []
    market_by_date = {}
    symbols = tuple(sorted(expected_symbols))

    def previous_close(symbol: str, trade_day: date) -> Optional[float]:
        for candidate_day in reversed(dates):
            if candidate_day >= trade_day:
                continue
            value = daily_by_symbol[symbol][candidate_day]
            if value is not None:
                return value[0]
        return None

    for index in range(1, len(dates)):
        trade_day = dates[index]
        active_symbols = tuple(
            symbol for symbol in symbols
            if daily_by_symbol[symbol][trade_day] is not None
        )
        previous_closes = {
            symbol: previous_close(symbol, trade_day)
            for symbol in active_symbols
        }
        if (
            not active_symbols
            or any(value is None for value in previous_closes.values())
        ):
            continue
        returns = {
            symbol: (
                daily_by_symbol[symbol][trade_day][0]
                / previous_closes[symbol]
                - 1
            )
            for symbol in active_symbols
        }
        equal_return = sum(returns.values()) / len(returns)
        caps = {
            symbol: (
                previous_closes[symbol]
                * float(query.total_shares_by_symbol[symbol])
            )
            for symbol in active_symbols
        }
        cap_total = sum(caps.values())
        cap_return = sum(
            returns[symbol] * caps[symbol] for symbol in active_symbols
        ) / cap_total
        sample = MarketHistoricalSample(
            trade_date=trade_day,
            equal_weighted_return=equal_return,
            market_cap_weighted_return=cap_return,
        )
        market_samples.append(sample)
        market_by_date[trade_day] = sample

    analyses = []
    coverage_rows = []
    for division_code in sorted(memberships):
        members = memberships[division_code]
        turnover_values = []
        for trade_day in dates:
            active_members = tuple(
                symbol for symbol in members
                if daily_by_symbol[symbol][trade_day] is not None
            )
            if len(active_members) <= 1:
                continue
            turnover_values.append(HistoricalValue(
                trade_date=trade_day,
                value=sum(
                    daily_by_symbol[symbol][trade_day][1]
                    for symbol in active_members
                ),
            ))
        turnover_values = tuple(turnover_values)
        relative_values = []
        for index in range(1, len(dates)):
            trade_day = dates[index]
            if trade_day not in market_by_date:
                continue
            active_members = tuple(
                symbol for symbol in members
                if daily_by_symbol[symbol][trade_day] is not None
                and previous_close(symbol, trade_day) is not None
            )
            if len(active_members) <= 1:
                continue
            sector_return = sum(
                daily_by_symbol[symbol][trade_day][0]
                / previous_close(symbol, trade_day)
                - 1
                for symbol in active_members
            ) / len(active_members)
            relative_values.append(HistoricalValue(
                trade_date=trade_day,
                value=(
                    sector_return
                    - market_by_date[trade_day].equal_weighted_return
                ),
            ))
        persistence = tuple(
            SectorPersistenceSample(
                trade_date=item.trade_date,
                relative_return=item.value,
                positive=item.value > 0,
            )
            for item in relative_values[-5:]
        )
        if len(turnover_values) < 20 or len(persistence) < 5:
            return _empty_result("sector_history_samples_below_minimum")
        turnover_ratios = []
        for index in range(20, len(turnover_values)):
            current_turnover = turnover_values[index].value
            baseline = median(
                item.value for item in turnover_values[index - 20:index]
            )
            if baseline > 0:
                turnover_ratios.append(HistoricalValue(
                    trade_date=turnover_values[index].trade_date,
                    value=current_turnover / baseline,
                ))
        persistence_ratios = tuple(
            HistoricalValue(
                trade_date=relative_values[index].trade_date,
                value=sum(
                    item.value > 0
                    for item in relative_values[index - 4:index + 1]
                ) / 5,
            )
            for index in range(4, len(relative_values))
        )
        analyses.append(SectorHistoricalAnalysis(
            division_code=division_code,
            member_count=len(members),
            same_minute_turnover_samples=turnover_values,
            relative_return_samples=tuple(relative_values),
            persistence_samples=persistence,
            turnover_ratio_samples=tuple(turnover_ratios),
            persistence_ratio_samples=persistence_ratios,
            latest_turnover_ratio_20d=(
                turnover_ratios[-1].value if turnover_ratios else None
            ),
            latest_persistence_positive_ratio_5d=(
                persistence_ratios[-1].value
            ),
            comparable_time=query.comparable_time,
        ))
        coverage_rows.append(SectorHistoryCoverage(
            division_code=division_code,
            same_minute_trading_dates=dates,
            persistence_trading_dates=tuple(
                item.trade_date for item in persistence
            ),
        ))

    history_evidence = SectorHistoryCoverageEvidence(
        radar_run_id=query.radar_run_id,
        rule_version=query.rule_version,
        classification_document_sha256=release.document_sha256,
        as_of=query.as_of,
        rows=tuple(coverage_rows),
        abnormal_day_filter_contract_id=(
            ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID
        ),
    )
    market_tuple = tuple(market_samples)
    analyses_tuple = tuple(analyses)
    return SectorHistoryBackfillResult(
        status="ready",
        reasons=(),
        sector_analyses=analyses_tuple,
        market_samples=market_tuple,
        history_evidence=history_evidence,
        calibration_proposal=_calibration_proposal(
            dates=dates,
            market_samples=market_tuple,
            analyses=analyses_tuple,
        ),
    )
