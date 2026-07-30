"""阶段6L-A龙头当期研究特征。

本模块只做内存纯计算，不抓取数据、不连接数据库，也不把部分研究分转换
为正式龙头总分或状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from radar.contracts import QuoteSnapshot, SecurityMasterRecord


LEADER_RESEARCH_FEATURE_VERSION = "radar-leader-research-feature-v1"
PARTICIPATING_WEIGHT = 55.0
REQUIRED_FORMAL_WEIGHT = 95.0
MINIMUM_SECTOR_POPULATION = 20
MINIMUM_INDUSTRY_POPULATION = 2
MINIMUM_MARKET_POPULATION = 100
MAXIMUM_SOURCE_AGE_SECONDS = 90
MAXIMUM_FUTURE_SKEW_SECONDS = 5
UTC = timezone.utc


class ResearchFeatureStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    SOURCE_UNVERIFIED = "source_unverified"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"


def average_tie_percentile(
    value: float,
    population: Sequence[float],
) -> Optional[float]:
    """返回并列值共享的平均百分位；样本不足或目标不在集合时返回缺失。"""

    values = tuple(float(item) for item in population)
    if len(values) < 2:
        return None
    numeric_value = float(value)
    lower = sum(item < numeric_value for item in values)
    equal = sum(item == numeric_value for item in values)
    if equal == 0:
        return None
    percentile = (
        lower + (equal - 1) / 2
    ) / (len(values) - 1)
    return min(1.0, max(0.0, percentile))


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _quote_is_current(
    quote: QuoteSnapshot,
    as_of: datetime,
) -> bool:
    if quote.source_time is None:
        return False
    source_time = _aware_utc(quote.source_time, "quote.source_time")
    age_seconds = (as_of - source_time).total_seconds()
    return (
        -MAXIMUM_FUTURE_SKEW_SECONDS
        <= age_seconds
        <= MAXIMUM_SOURCE_AGE_SECONDS
    )


def _row_is_current(
    row: Mapping[str, Any],
    as_of: datetime,
) -> bool:
    row_as_of = row.get("asOf")
    source_time = row.get("sourceTime")
    if not isinstance(row_as_of, datetime):
        return False
    if not isinstance(source_time, datetime):
        return False
    row_as_of = _aware_utc(row_as_of, "sector.asOf")
    source_time = _aware_utc(source_time, "sector.sourceTime")
    age_seconds = (as_of - source_time).total_seconds()
    return (
        row_as_of <= as_of
        and -MAXIMUM_FUTURE_SKEW_SECONDS
        <= age_seconds
        <= MAXIMUM_SOURCE_AGE_SECONDS
    )


def _board_index_key(
    security: SecurityMasterRecord,
) -> Optional[str]:
    board = security.board.strip()
    if not board:
        return None
    if security.exchange == "sse":
        if "科创" in board:
            return "star50"
        if "主板" in board or board in {"A股", "上交所A股"}:
            return "sse_composite"
        return None
    if security.exchange == "szse":
        if "创业" in board:
            return "chinext"
        if "主板" in board or board in {"A股", "深交所A股"}:
            return "szse_component"
        return None
    return None


def is_research_quote_eligible(
    quote: QuoteSnapshot,
    as_of: datetime,
) -> bool:
    """候选排序前统一拒绝过期和非有限涨跌幅。"""

    normalized_as_of = _aware_utc(as_of, "as_of")
    return (
        _quote_is_current(quote, normalized_as_of)
        and _number(quote.change_percent) is not None
    )


def _round_optional(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


@dataclass(frozen=True)
class LeaderResearchComponent:
    name: str
    maximum_score: float
    raw_value: Optional[float] = None
    normalized_value: Optional[float] = None
    score: Optional[float] = None
    population_size: int = 0
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING
    reasons: Tuple[str, ...] = ()

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "maximumScore": self.maximum_score,
            "rawValue": _round_optional(self.raw_value),
            "normalizedValue": _round_optional(self.normalized_value),
            "score": _round_optional(self.score),
            "populationSize": self.population_size,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderResearchDimension:
    field_name: str
    maximum_score: float
    research_score: Optional[float]
    status: ResearchFeatureStatus
    components: Tuple[LeaderResearchComponent, ...] = ()
    source_contract_ids: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.field_name.strip():
            raise ValueError("研究维度名不能为空")
        if self.maximum_score <= 0:
            raise ValueError("研究维度最高分必须大于0")
        if self.status == ResearchFeatureStatus.READY:
            if self.research_score is None:
                raise ValueError("可用研究维度必须保留分数，真实0不能写成缺失")
            if not 0 <= self.research_score <= self.maximum_score:
                raise ValueError("研究维度分数超出允许范围")
        elif self.research_score is not None:
            raise ValueError("不可用研究维度不得携带分数")

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "maximumScore": self.maximum_score,
            "researchScore": _round_optional(self.research_score),
            "status": self.status.value,
            "components": {
                component.name: component.to_evidence()
                for component in self.components
            },
            "sourceContractIds": list(self.source_contract_ids),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderResearchFeatureResult:
    dimensions: Tuple[LeaderResearchDimension, ...]

    def __post_init__(self):
        field_names = tuple(
            dimension.field_name
            for dimension in self.dimensions
        )
        if len(field_names) != len(set(field_names)):
            raise ValueError("研究维度不得重复")

    @property
    def research_partial_score(self) -> float:
        return sum(
            dimension.research_score or 0.0
            for dimension in self.dimensions
        )

    def dimension(self, field_name: str) -> LeaderResearchDimension:
        for dimension in self.dimensions:
            if dimension.field_name == field_name:
                return dimension
        raise KeyError(field_name)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": LEADER_RESEARCH_FEATURE_VERSION,
            "researchPartialScore": round(
                self.research_partial_score,
                6,
            ),
            "participatingWeight": PARTICIPATING_WEIGHT,
            "requiredFormalWeight": REQUIRED_FORMAL_WEIGHT,
            "scoreReady": False,
            "dimensions": {
                dimension.field_name: dimension.to_evidence()
                for dimension in self.dimensions
            },
        }


@dataclass(frozen=True)
class LeaderResearchMarketContext:
    as_of: datetime
    index_changes: Mapping[str, float]
    market_excess_returns: Tuple[float, ...]
    market_volume_ratios: Tuple[float, ...]
    market_status: ResearchFeatureStatus
    market_reasons: Tuple[str, ...] = ()


def build_leader_research_market_context(
    *,
    as_of: datetime,
    market_quotes: Sequence[QuoteSnapshot],
    security_by_symbol: Mapping[str, SecurityMasterRecord],
    market_snapshot: Mapping[str, Any],
) -> LeaderResearchMarketContext:
    """单批次构建全市场研究分母，供全部候选复用。"""

    normalized_as_of = _aware_utc(as_of, "as_of")
    market_status = ResearchFeatureStatus.READY
    market_reasons = ()
    market_as_of = market_snapshot.get("asOf")
    market_source_time = market_snapshot.get("sourceTime")
    if not isinstance(market_as_of, datetime):
        market_status = ResearchFeatureStatus.MISSING
        market_reasons = ("market_snapshot_as_of_missing",)
    elif _aware_utc(
        market_as_of,
        "market_snapshot.asOf",
    ) > normalized_as_of:
        market_status = ResearchFeatureStatus.SOURCE_UNVERIFIED
        market_reasons = ("market_snapshot_from_future",)
    elif not isinstance(market_source_time, datetime):
        market_status = ResearchFeatureStatus.MISSING
        market_reasons = ("market_snapshot_source_time_missing",)
    else:
        market_age_seconds = (
            normalized_as_of
            - _aware_utc(
                market_source_time,
                "market_snapshot.sourceTime",
            )
        ).total_seconds()
        if market_age_seconds > MAXIMUM_SOURCE_AGE_SECONDS:
            market_status = ResearchFeatureStatus.STALE
            market_reasons = ("market_snapshot_stale",)
        elif market_age_seconds < -MAXIMUM_FUTURE_SKEW_SECONDS:
            market_status = ResearchFeatureStatus.SOURCE_UNVERIFIED
            market_reasons = ("market_snapshot_source_time_future",)

    index_changes = {
        str(item.get("indexKey") or ""): change_percent
        for item in market_snapshot.get("indices", ())
        if (
            isinstance(item, Mapping)
            and (
                change_percent := _number(
                    item.get("changePercent")
                )
            ) is not None
        )
    }
    market_excess_returns = []
    market_volume_ratios = []
    for quote in market_quotes:
        security = security_by_symbol.get(quote.symbol)
        if (
            security is None
            or security.exchange not in {"sse", "szse"}
            or not _quote_is_current(quote, normalized_as_of)
        ):
            continue
        volume_ratio = _number(quote.volume_ratio)
        if volume_ratio is not None and volume_ratio >= 0:
            market_volume_ratios.append(volume_ratio)
        change_percent = _number(quote.change_percent)
        index_key = _board_index_key(security)
        index_change = index_changes.get(index_key or "")
        if change_percent is None or index_change is None:
            continue
        market_excess_returns.append(
            change_percent - index_change
        )
    return LeaderResearchMarketContext(
        as_of=normalized_as_of,
        index_changes=index_changes,
        market_excess_returns=tuple(market_excess_returns),
        market_volume_ratios=tuple(market_volume_ratios),
        market_status=market_status,
        market_reasons=market_reasons,
    )


def _missing_dimension(
    field_name: str,
    maximum_score: float,
    reasons: Sequence[str],
    source_contract_ids: Sequence[str],
    *,
    components: Sequence[LeaderResearchComponent] = (),
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING,
) -> LeaderResearchDimension:
    return LeaderResearchDimension(
        field_name=field_name,
        maximum_score=maximum_score,
        research_score=None,
        status=status,
        components=tuple(components),
        source_contract_ids=tuple(source_contract_ids),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _source_blocked_dimension(
    field_name: str,
    maximum_score: float,
    source_statuses: Sequence[
        Tuple[str, ResearchFeatureStatus]
    ],
    source_contract_ids: Sequence[str],
) -> Optional[LeaderResearchDimension]:
    priority = (
        ResearchFeatureStatus.SOURCE_FAILED,
        ResearchFeatureStatus.STALE,
        ResearchFeatureStatus.SOURCE_UNVERIFIED,
        ResearchFeatureStatus.MISSING,
    )
    for status in priority:
        blocked_sources = tuple(
            source_name
            for source_name, source_status in source_statuses
            if source_status == status
        )
        if blocked_sources:
            return _missing_dimension(
                field_name,
                maximum_score,
                tuple(
                    f"{source_name}_source_{status.value}"
                    for source_name in blocked_sources
                ),
                source_contract_ids,
                status=status,
            )
    return None


def _rank_component(
    *,
    name: str,
    raw_value: float,
    population: Sequence[float],
    maximum_score: float,
    positive_only: bool = False,
) -> Optional[LeaderResearchComponent]:
    if positive_only and raw_value <= 0:
        normalized = 0.0
    else:
        normalized = average_tie_percentile(raw_value, population)
    if normalized is None:
        return None
    return LeaderResearchComponent(
        name=name,
        maximum_score=maximum_score,
        raw_value=raw_value,
        normalized_value=normalized,
        score=maximum_score * normalized,
        population_size=len(population),
        status=ResearchFeatureStatus.READY,
    )


def _value_component(
    *,
    name: str,
    raw_value: float,
    maximum_score: float,
) -> LeaderResearchComponent:
    normalized = min(1.0, max(0.0, raw_value))
    return LeaderResearchComponent(
        name=name,
        maximum_score=maximum_score,
        raw_value=raw_value,
        normalized_value=normalized,
        score=maximum_score * normalized,
        population_size=1,
        status=ResearchFeatureStatus.READY,
    )


def _industry_strength_dimension(
    *,
    as_of: datetime,
    sector: Mapping[str, Any],
    sector_rows: Sequence[Mapping[str, Any]],
    source_contract_id: str,
) -> LeaderResearchDimension:
    target_code = str(sector.get("divisionCode") or "")
    raw_target_rows = tuple(
        row
        for row in sector_rows
        if str(row.get("divisionCode") or "") == target_code
    )
    if len(raw_target_rows) != 1:
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("target_sector_missing_or_duplicated",),
            (source_contract_id,),
        )
    raw_target = raw_target_rows[0]
    target_values = (
        _number(raw_target.get("equalReturn")),
        _number(raw_target.get("upRatio")),
        _number(raw_target.get("exTopReturn")),
    )
    if (
        raw_target.get("isComplete") is not True
        or raw_target.get("shadowUsable") is not True
        or not _row_is_current(raw_target, as_of)
        or any(value is None for value in target_values)
    ):
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("target_sector_fields_missing",),
            (source_contract_id,),
        )
    valid_rows = tuple(
        row
        for row in sector_rows
        if (
            row.get("isComplete") is True
            and row.get("shadowUsable") is True
            and _row_is_current(row, as_of)
            and _number(row.get("equalReturn")) is not None
            and _number(row.get("upRatio")) is not None
            and _number(row.get("exTopReturn")) is not None
        )
    )
    run_ids = {
        str(row.get("radarRunId") or "")
        for row in valid_rows
    }
    if "" in run_ids or len(run_ids) != 1:
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("sector_run_mixed",),
            (source_contract_id,),
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
    division_codes = tuple(
        str(row.get("divisionCode") or "")
        for row in valid_rows
    )
    if (
        "" in division_codes
        or len(division_codes) != len(set(division_codes))
    ):
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("sector_division_duplicated",),
            (source_contract_id,),
        )
    if len(valid_rows) < MINIMUM_SECTOR_POPULATION:
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("sector_population_below_20",),
            (source_contract_id,),
        )
    target_rows = tuple(
        row
        for row in valid_rows
        if str(row.get("divisionCode") or "") == target_code
    )
    if len(target_rows) != 1:
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("target_sector_missing_or_duplicated",),
            (source_contract_id,),
        )
    target = target_rows[0]
    equal_return = _number(target.get("equalReturn"))
    up_ratio = _number(target.get("upRatio"))
    ex_top_return = _number(target.get("exTopReturn"))
    if (
        equal_return is None
        or up_ratio is None
        or ex_top_return is None
    ):
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("target_sector_fields_missing",),
            (source_contract_id,),
        )
    equal_population = tuple(
        float(row["equalReturn"])
        for row in valid_rows
    )
    ex_top_population = tuple(
        float(row["exTopReturn"])
        for row in valid_rows
    )
    equal_component = _rank_component(
        name="equal_return",
        raw_value=equal_return,
        population=equal_population,
        maximum_score=10.0,
        positive_only=True,
    )
    ex_top_component = _rank_component(
        name="ex_top_return",
        raw_value=ex_top_return,
        population=ex_top_population,
        maximum_score=7.0,
        positive_only=True,
    )
    if equal_component is None or ex_top_component is None:
        return _missing_dimension(
            "industry_strength",
            25.0,
            ("sector_percentile_unavailable",),
            (source_contract_id,),
        )
    components = (
        equal_component,
        _value_component(
            name="up_ratio",
            raw_value=up_ratio,
            maximum_score=8.0,
        ),
        ex_top_component,
    )
    return LeaderResearchDimension(
        field_name="industry_strength",
        maximum_score=25.0,
        research_score=sum(
            component.score or 0.0
            for component in components
        ),
        status=ResearchFeatureStatus.READY,
        components=components,
        source_contract_ids=(source_contract_id,),
    )


def _market_leadership_dimension(
    *,
    as_of: datetime,
    candidate_quote: QuoteSnapshot,
    candidate_security: SecurityMasterRecord,
    industry_quotes: Sequence[QuoteSnapshot],
    market_context: LeaderResearchMarketContext,
    market_source_contract_id: str,
    quote_source_contract_id: str,
) -> LeaderResearchDimension:
    source_ids = (
        market_source_contract_id,
        quote_source_contract_id,
    )
    if market_context.market_status != ResearchFeatureStatus.READY:
        return _missing_dimension(
            "market_leadership",
            25.0,
            market_context.market_reasons,
            source_ids,
            status=market_context.market_status,
        )
    if not _quote_is_current(candidate_quote, as_of):
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("candidate_quote_not_current",),
            source_ids,
        )
    candidate_change = _number(candidate_quote.change_percent)
    candidate_cap = _number(candidate_quote.market_cap_source)
    if (
        candidate_change is None
        or candidate_cap is None
        or candidate_cap <= 0
    ):
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("candidate_leadership_fields_missing",),
            source_ids,
        )
    valid_change_quotes = tuple(
        quote
        for quote in industry_quotes
        if (
            _quote_is_current(quote, as_of)
            and _number(quote.change_percent) is not None
        )
    )
    if len(valid_change_quotes) < MINIMUM_INDUSTRY_POPULATION:
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("industry_population_below_2",),
            source_ids,
        )
    valid_contribution_quotes = tuple(
        quote
        for quote in valid_change_quotes
        if (
            _number(quote.market_cap_source) is not None
            and float(quote.market_cap_source) > 0
        )
    )
    if len(valid_contribution_quotes) < MINIMUM_INDUSTRY_POPULATION:
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("industry_contribution_population_below_2",),
            source_ids,
        )
    industry_changes = tuple(
        float(quote.change_percent)
        for quote in valid_change_quotes
    )
    industry_contributions = tuple(
        float(quote.market_cap_source)
        * max(float(quote.change_percent), 0.0)
        for quote in valid_contribution_quotes
    )
    candidate_contribution = candidate_cap * max(candidate_change, 0.0)

    candidate_index_key = _board_index_key(candidate_security)
    if candidate_index_key is None:
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("board_unrecognized",),
            source_ids,
        )
    candidate_index_change = market_context.index_changes.get(
        candidate_index_key
    )
    if candidate_index_change is None:
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("board_index_missing",),
            source_ids,
        )
    if (
        len(market_context.market_excess_returns)
        < MINIMUM_MARKET_POPULATION
    ):
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("market_population_below_100",),
            source_ids,
        )
    components = (
        _rank_component(
            name="industry_return_rank",
            raw_value=candidate_change,
            population=industry_changes,
            maximum_score=10.0,
        ),
        _rank_component(
            name="industry_contribution_rank",
            raw_value=candidate_contribution,
            population=industry_contributions,
            maximum_score=8.0,
            positive_only=True,
        ),
        _rank_component(
            name="board_excess_rank",
            raw_value=candidate_change - candidate_index_change,
            population=market_context.market_excess_returns,
            maximum_score=7.0,
            positive_only=True,
        ),
    )
    if any(component is None for component in components):
        return _missing_dimension(
            "market_leadership",
            25.0,
            ("leadership_percentile_unavailable",),
            source_ids,
        )
    ready_components = tuple(
        component
        for component in components
        if component is not None
    )
    return LeaderResearchDimension(
        field_name="market_leadership",
        maximum_score=25.0,
        research_score=sum(
            component.score or 0.0
            for component in ready_components
        ),
        status=ResearchFeatureStatus.READY,
        components=ready_components,
        source_contract_ids=source_ids,
    )


def _auxiliary_dimension(
    *,
    as_of: datetime,
    candidate_quote: QuoteSnapshot,
    market_context: LeaderResearchMarketContext,
    quote_source_contract_id: str,
) -> LeaderResearchDimension:
    candidate_volume_ratio = _number(candidate_quote.volume_ratio)
    if (
        not _quote_is_current(candidate_quote, as_of)
        or candidate_volume_ratio is None
        or candidate_volume_ratio < 0
    ):
        return _missing_dimension(
            "auxiliary",
            5.0,
            ("candidate_volume_ratio_missing",),
            (quote_source_contract_id,),
        )
    if (
        len(market_context.market_volume_ratios)
        < MINIMUM_MARKET_POPULATION
    ):
        return _missing_dimension(
            "auxiliary",
            5.0,
            ("volume_ratio_population_below_100",),
            (quote_source_contract_id,),
        )
    normalized = (
        0.0
        if candidate_volume_ratio <= 1
        else average_tie_percentile(
            candidate_volume_ratio,
            market_context.market_volume_ratios,
        )
    )
    if normalized is None:
        return _missing_dimension(
            "auxiliary",
            5.0,
            ("volume_ratio_percentile_unavailable",),
            (quote_source_contract_id,),
        )
    component = LeaderResearchComponent(
        name="volume_ratio_rank",
        maximum_score=5.0,
        raw_value=candidate_volume_ratio,
        normalized_value=normalized,
        score=5.0 * normalized,
        population_size=len(market_context.market_volume_ratios),
        status=ResearchFeatureStatus.READY,
    )
    return LeaderResearchDimension(
        field_name="auxiliary",
        maximum_score=5.0,
        research_score=component.score,
        status=ResearchFeatureStatus.READY,
        components=(component,),
        source_contract_ids=(quote_source_contract_id,),
    )


def build_leader_research_features(
    *,
    as_of: datetime,
    candidate_quote: QuoteSnapshot,
    candidate_security: SecurityMasterRecord,
    industry_quotes: Sequence[QuoteSnapshot],
    market_quotes: Sequence[QuoteSnapshot],
    security_by_symbol: Mapping[str, SecurityMasterRecord],
    sector: Mapping[str, Any],
    sector_rows: Sequence[Mapping[str, Any]],
    market_snapshot: Mapping[str, Any],
    sector_source_contract_id: str,
    market_source_contract_id: str,
    quote_source_contract_id: str,
    sector_source_status: ResearchFeatureStatus = (
        ResearchFeatureStatus.READY
    ),
    market_source_status: ResearchFeatureStatus = (
        ResearchFeatureStatus.READY
    ),
    quote_source_status: ResearchFeatureStatus = (
        ResearchFeatureStatus.READY
    ),
    market_context: Optional[
        LeaderResearchMarketContext
    ] = None,
) -> LeaderResearchFeatureResult:
    """计算当期研究特征，不产生正式评分输入。"""

    normalized_as_of = _aware_utc(as_of, "as_of")
    if market_context is None:
        market_context = build_leader_research_market_context(
            as_of=normalized_as_of,
            market_quotes=market_quotes,
            security_by_symbol=security_by_symbol,
            market_snapshot=market_snapshot,
        )
    elif market_context.as_of != normalized_as_of:
        raise ValueError("研究市场上下文as_of必须与候选一致")
    industry_dimension = _source_blocked_dimension(
        "industry_strength",
        25.0,
        (("sector", sector_source_status),),
        (sector_source_contract_id,),
    )
    if industry_dimension is None:
        industry_dimension = _industry_strength_dimension(
            as_of=normalized_as_of,
            sector=sector,
            sector_rows=sector_rows,
            source_contract_id=sector_source_contract_id,
        )
    market_dimension = _source_blocked_dimension(
        "market_leadership",
        25.0,
        (
            ("market", market_source_status),
            ("quote", quote_source_status),
        ),
        (
            market_source_contract_id,
            quote_source_contract_id,
        ),
    )
    if market_dimension is None:
        market_dimension = _market_leadership_dimension(
            as_of=normalized_as_of,
            candidate_quote=candidate_quote,
            candidate_security=candidate_security,
            industry_quotes=industry_quotes,
            market_context=market_context,
            market_source_contract_id=market_source_contract_id,
            quote_source_contract_id=quote_source_contract_id,
        )
    auxiliary_dimension = _source_blocked_dimension(
        "auxiliary",
        5.0,
        (("quote", quote_source_status),),
        (quote_source_contract_id,),
    )
    if auxiliary_dimension is None:
        auxiliary_dimension = _auxiliary_dimension(
            as_of=normalized_as_of,
            candidate_quote=candidate_quote,
            market_context=market_context,
            quote_source_contract_id=quote_source_contract_id,
        )
    return LeaderResearchFeatureResult(dimensions=(
        industry_dimension,
        market_dimension,
        auxiliary_dimension,
    ))
