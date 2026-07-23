from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional, Sequence, Tuple

from radar.contracts import (
    FeatureCompleteness,
    IndexQuoteSnapshot,
    MarketBreadthSnapshot,
    MarketFeatureSnapshot,
    MarketTurnoverSnapshot,
    QuoteSnapshot,
    SourceBatch,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.market_indices import MARKET_INDEX_IDENTITIES


INDEX_HEALTH_POLICY = SourceHealthPolicy(
    minimum_row_coverage=1.0,
    minimum_required_field_coverage=1.0,
    maximum_age_seconds=90,
    maximum_future_skew_seconds=5,
    required_fields=("price", "change_percent", "source_time"),
)


def _normalize_universe(values: Iterable[str], label: str) -> Tuple[str, ...]:
    result = []
    for value in values:
        symbol = str(value or "").strip()
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError(f"{label}包含无效证券代码")
        if symbol in result:
            raise ValueError(f"{label}包含重复证券代码{symbol}")
        result.append(symbol)
    return tuple(result)


def _dedupe_reasons(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _source_time_reasons(
    source_time: Optional[datetime],
    *,
    as_of: datetime,
    maximum_age_seconds: int = 90,
    maximum_future_skew_seconds: int = 5,
) -> Tuple[str, ...]:
    if source_time is None:
        return ("source_time_missing",)
    signed_age_seconds = (as_of - source_time).total_seconds()
    reasons = []
    if signed_age_seconds < -maximum_future_skew_seconds:
        reasons.append("source_time_in_future")
    if max(0.0, signed_age_seconds) > maximum_age_seconds:
        reasons.append("source_time_stale")
    return tuple(reasons)


def _health_reasons(
    batch_meta,
    *,
    as_of: datetime,
    required_fields: Sequence[str],
    minimum_row_coverage: float,
    minimum_required_field_coverage: float,
) -> Tuple[str, ...]:
    policy = SourceHealthPolicy(
        minimum_row_coverage=minimum_row_coverage,
        minimum_required_field_coverage=minimum_required_field_coverage,
        maximum_age_seconds=90,
        maximum_future_skew_seconds=5,
        required_fields=tuple(required_fields),
    )
    result = evaluate_source_health(batch_meta, policy, now=as_of)
    return result.reasons if result.status != SourceStatus.HEALTHY else ()


def _feature_completeness(
    *,
    expected_count: int,
    returned_count: int,
    valid_count: int,
    required_field_coverage,
    reasons: Iterable[str],
) -> FeatureCompleteness:
    normalized_reasons = _dedupe_reasons(reasons)
    row_coverage = returned_count / expected_count if expected_count else 0.0
    return FeatureCompleteness(
        expectedCount=expected_count,
        returnedCount=returned_count,
        validCount=valid_count,
        rowCoverage=row_coverage,
        requiredFieldCoverage=required_field_coverage,
        isComplete=not normalized_reasons,
        reasons=normalized_reasons,
    )


def _index_completeness(
    batch: SourceBatch[IndexQuoteSnapshot],
    *,
    as_of: datetime,
    additional_reasons: Iterable[str],
) -> FeatureCompleteness:
    required_keys = {
        identity.index_key
        for identity in MARKET_INDEX_IDENTITIES
    }
    seen_keys = [item.index_key for item in batch.items]
    unique_keys = set(seen_keys)
    item_time_reasons = []
    valid_count = 0
    for item in batch.items:
        time_reasons = _source_time_reasons(item.source_time, as_of=as_of)
        item_time_reasons.extend(time_reasons)
        if not item.missing_fields() and not time_reasons:
            valid_count += 1
    reasons = list(additional_reasons)
    reasons.extend(item_time_reasons)
    reasons.extend(_health_reasons(
        batch.meta,
        as_of=as_of,
        required_fields=INDEX_HEALTH_POLICY.required_fields,
        minimum_row_coverage=INDEX_HEALTH_POLICY.minimum_row_coverage,
        minimum_required_field_coverage=(
            INDEX_HEALTH_POLICY.minimum_required_field_coverage
        ),
    ))
    if len(seen_keys) != len(unique_keys):
        reasons.append("duplicate_index_identities")
    if unique_keys != required_keys:
        reasons.append("required_index_identities_missing")
    if valid_count != len(MARKET_INDEX_IDENTITIES):
        reasons.append("required_index_fields_missing")
    return _feature_completeness(
        expected_count=len(MARKET_INDEX_IDENTITIES),
        returned_count=len(unique_keys),
        valid_count=min(valid_count, len(unique_keys)),
        required_field_coverage=batch.meta.required_field_coverage,
        reasons=reasons,
    )


def build_market_features(
    index_batch: SourceBatch[IndexQuoteSnapshot],
    quote_batch: SourceBatch[QuoteSnapshot],
    *,
    stock_symbols: Iterable[str],
    etf_symbols: Iterable[str],
    turnover_unit_status: UnitVerificationStatus = (
        UnitVerificationStatus.UNVERIFIED
    ),
    minimum_row_coverage: float = 0.995,
    minimum_required_field_coverage: float = 0.99,
    maximum_source_skew_seconds: int = 15,
) -> MarketFeatureSnapshot:
    if not 0 <= minimum_row_coverage <= 1:
        raise ValueError("minimum_row_coverage必须在0到1之间")
    if not 0 <= minimum_required_field_coverage <= 1:
        raise ValueError("minimum_required_field_coverage必须在0到1之间")
    if maximum_source_skew_seconds < 0:
        raise ValueError("maximum_source_skew_seconds不得小于0")
    if index_batch.meta.radar_run_id != quote_batch.meta.radar_run_id:
        raise ValueError("指数与股票行情必须属于同一radarRunId")
    if index_batch.meta.as_of != quote_batch.meta.as_of:
        raise ValueError("指数与股票行情必须使用同一asOf")

    stocks = _normalize_universe(stock_symbols, "A股股票池")
    etfs = _normalize_universe(etf_symbols, "ETF池")
    stock_set = set(stocks)
    etf_set = set(etfs)
    overlap = stock_set & etf_set
    if overlap:
        raise ValueError(f"A股股票池与ETF池重叠：{sorted(overlap)[0]}")

    as_of = index_batch.meta.as_of
    cross_source_reasons = []
    source_times = [
        value
        for value in (
            index_batch.meta.source_time,
            quote_batch.meta.source_time,
        )
        if value is not None
    ]
    if (
        len(source_times) == 2
        and (max(source_times) - min(source_times)).total_seconds()
        > maximum_source_skew_seconds
    ):
        cross_source_reasons.append("source_time_skew_above_threshold")

    quotes_by_symbol = defaultdict(list)
    excluded_etf_symbols = set()
    unknown_symbols = set()
    for item in quote_batch.items:
        if item.symbol in stock_set:
            quotes_by_symbol[item.symbol].append(item)
        elif item.symbol in etf_set:
            excluded_etf_symbols.add(item.symbol)
        else:
            unknown_symbols.add(item.symbol)

    duplicate_symbols = tuple(sorted(
        symbol
        for symbol, items in quotes_by_symbol.items()
        if len(items) > 1
    ))
    returned_stock_count = sum(
        bool(quotes_by_symbol.get(symbol))
        for symbol in stocks
    )

    breadth_counts = {
        "advancers": 0,
        "decliners": 0,
        "flat": 0,
        "unavailable": 0,
    }
    turnover_values = []
    valid_change_count = 0
    valid_source_time_count = 0
    valid_turnover_count = 0
    item_time_reasons = []
    for symbol in stocks:
        rows = quotes_by_symbol.get(symbol, [])
        if len(rows) != 1:
            breadth_counts["unavailable"] += 1
            continue
        item = rows[0]
        time_reasons = _source_time_reasons(item.source_time, as_of=as_of)
        if time_reasons:
            item_time_reasons.extend(time_reasons)
            breadth_counts["unavailable"] += 1
            continue
        valid_source_time_count += 1
        if item.turnover_amount_source is not None:
            valid_turnover_count += 1
            turnover_values.append(item.turnover_amount_source)
        if item.change_percent is None:
            breadth_counts["unavailable"] += 1
            continue
        valid_change_count += 1
        if item.change_percent > 0:
            breadth_counts["advancers"] += 1
        elif item.change_percent < 0:
            breadth_counts["decliners"] += 1
        else:
            breadth_counts["flat"] += 1

    expected_stock_count = len(stocks)
    denominator = expected_stock_count or 1
    base_reasons = list(cross_source_reasons)
    base_reasons.extend(item_time_reasons)
    if duplicate_symbols:
        base_reasons.append("duplicate_quote_symbols")
    if unknown_symbols:
        base_reasons.append("unknown_quote_symbols")

    breadth_coverage = {
        "change_percent": valid_change_count / denominator,
        "source_time": valid_source_time_count / denominator,
    }
    breadth_reasons = list(base_reasons)
    breadth_reasons.extend(_health_reasons(
        quote_batch.meta,
        as_of=as_of,
        required_fields=("change_percent", "source_time"),
        minimum_row_coverage=minimum_row_coverage,
        minimum_required_field_coverage=minimum_required_field_coverage,
    ))
    row_coverage = (
        returned_stock_count / expected_stock_count
        if expected_stock_count
        else 0.0
    )
    if row_coverage < minimum_row_coverage:
        breadth_reasons.append("row_coverage_below_threshold")
    for field_name, coverage in breadth_coverage.items():
        if coverage < minimum_required_field_coverage:
            breadth_reasons.append(
                f"required_field_coverage_below_threshold:{field_name}"
            )
    breadth_completeness = _feature_completeness(
        expected_count=expected_stock_count,
        returned_count=returned_stock_count,
        valid_count=valid_change_count,
        required_field_coverage=breadth_coverage,
        reasons=breadth_reasons,
    )
    breadth = MarketBreadthSnapshot(
        **breadth_counts,
        completeness=breadth_completeness,
    )

    turnover_coverage = {
        "turnover_amount_source": valid_turnover_count / denominator,
        "source_time": valid_source_time_count / denominator,
    }
    turnover_reasons = list(base_reasons)
    turnover_reasons.extend(_health_reasons(
        quote_batch.meta,
        as_of=as_of,
        required_fields=("turnover_amount_source", "source_time"),
        minimum_row_coverage=minimum_row_coverage,
        minimum_required_field_coverage=minimum_required_field_coverage,
    ))
    if row_coverage < minimum_row_coverage:
        turnover_reasons.append("row_coverage_below_threshold")
    for field_name, coverage in turnover_coverage.items():
        if coverage < minimum_required_field_coverage:
            turnover_reasons.append(
                f"required_field_coverage_below_threshold:{field_name}"
            )
    turnover_completeness = _feature_completeness(
        expected_count=expected_stock_count,
        returned_count=returned_stock_count,
        valid_count=valid_turnover_count,
        required_field_coverage=turnover_coverage,
        reasons=turnover_reasons,
    )
    unit_reasons = []
    if turnover_unit_status != UnitVerificationStatus.VERIFIED:
        unit_reasons.append("turnover_unit_unverified")
    turnover = MarketTurnoverSnapshot(
        rawValue=(sum(turnover_values) if turnover_values else None),
        contributingCount=valid_turnover_count,
        unitStatus=turnover_unit_status,
        formalUsable=(
            turnover_completeness.is_complete
            and turnover_unit_status == UnitVerificationStatus.VERIFIED
        ),
        completeness=turnover_completeness,
        reasons=_dedupe_reasons([
            *turnover_completeness.reasons,
            *unit_reasons,
        ]),
    )

    index_completeness = _index_completeness(
        index_batch,
        as_of=as_of,
        additional_reasons=cross_source_reasons,
    )
    fetched_at = max(index_batch.meta.fetched_at, quote_batch.meta.fetched_at)
    return MarketFeatureSnapshot(
        radarRunId=index_batch.meta.radar_run_id,
        indexBatchId=index_batch.meta.batch_id,
        quoteBatchId=quote_batch.meta.batch_id,
        asOf=as_of,
        sourceTime=max(source_times) if source_times else None,
        fetchedAt=fetched_at,
        indices=index_batch.items,
        indexCompleteness=index_completeness,
        breadth=breadth,
        turnover=turnover,
        excludedEtfCount=len(excluded_etf_symbols),
        duplicateSymbols=duplicate_symbols,
        unknownSymbols=tuple(sorted(unknown_symbols)),
    )
