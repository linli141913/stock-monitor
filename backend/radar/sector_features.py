from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationRecord,
    IndustryClassificationSnapshot,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    QuoteSnapshot,
    SectorBreadthSnapshot,
    SectorConstituentCompleteness,
    SectorFeatureBatch,
    SectorFeatureSnapshot,
    SectorMetricValue,
    SectorReturnSnapshot,
    SectorTurnoverSnapshot,
    SourceBatch,
    SourceStatus,
    UnitVerificationStatus,
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
    maximum_age_seconds: int,
    maximum_future_skew_seconds: int,
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


def _metric(
    value: Optional[float],
    *,
    available: bool,
    reasons: Iterable[str],
) -> SectorMetricValue:
    return SectorMetricValue(
        rawValue=value if available else None,
        available=available,
        formalUsable=False,
        reasons=_dedupe_reasons(reasons),
    )


def _accepted_records(
    classification: IndustryClassificationSnapshot,
) -> List[IndustryClassificationRecord]:
    return [
        record
        for record in classification.records
        if (
            record.record_status == IndustryRecordStatus.ACCEPTED
            and record.identity_status != IndustryIdentityStatus.UNRESOLVED
        )
    ]


def _validate_classification_universe(
    classification: IndustryClassificationSnapshot,
    stock_symbols: Sequence[str],
) -> List[IndustryClassificationRecord]:
    if classification.release is None:
        raise ValueError("行业分类快照缺少发布版本")
    if classification.status == SourceStatus.FAILED:
        raise ValueError("失败的行业分类快照不能计算行业特征")
    if not classification.completeness.shadow_usable:
        raise ValueError("行业分类快照不允许影子计算")
    if classification.release.knowledge_effective_from > classification.meta.as_of:
        raise ValueError("行业版本在本轮asOf时尚未知")
    if (
        classification.release.knowledge_effective_to is not None
        and classification.release.knowledge_effective_to
        <= classification.meta.as_of
    ):
        raise ValueError("行业版本在本轮asOf时已经失效")
    if classification.completeness.current_master_count != len(stock_symbols):
        raise ValueError("行业分类当前主档数量与A股股票池不一致")

    stock_set = set(stock_symbols)
    accepted = _accepted_records(classification)
    identities = []
    for record in accepted:
        if record.release_period != classification.release.release_period:
            raise ValueError("行业记录与发布版本不一致")
        identity = record.security_identity
        if identity is None:
            raise ValueError("accepted行业记录缺少证券身份")
        if identity not in stock_set:
            raise ValueError("行业记录证券身份不在A股股票池")
        identities.append(identity)
    if len(identities) != len(set(identities)):
        raise ValueError("行业分类存在重复稳定证券身份")
    if len(identities) != classification.completeness.mapped_count:
        raise ValueError("行业分类映射数量与accepted记录不一致")

    gap_symbols = [gap.symbol for gap in classification.current_master_gaps]
    if len(gap_symbols) != len(set(gap_symbols)):
        raise ValueError("行业分类当前主档缺口存在重复代码")
    if not set(gap_symbols).issubset(stock_set):
        raise ValueError("行业分类缺口包含A股股票池之外的代码")
    if set(identities) | set(gap_symbols) != stock_set:
        raise ValueError("行业分类映射与缺口未覆盖完整A股股票池")
    if set(identities) & set(gap_symbols):
        raise ValueError("同一股票不能同时属于已映射和未确认缺口")
    return accepted


def _group_by_division(
    records: Sequence[IndustryClassificationRecord],
) -> Dict[Tuple[str, str, str, str], List[str]]:
    divisions_by_code = {}
    grouped = defaultdict(list)
    for record in records:
        identity = record.security_identity
        key = (
            record.category_code,
            record.category_name,
            record.division_code,
            record.division_name,
        )
        previous = divisions_by_code.setdefault(
            record.division_code,
            key,
        )
        if previous != key:
            raise ValueError("同一大类代码对应多个门类或名称")
        grouped[key].append(identity)
    return {
        key: sorted(symbols)
        for key, symbols in grouped.items()
    }


def _build_sector(
    *,
    release_period: str,
    key: Tuple[str, str, str, str],
    symbols: Sequence[str],
    quotes_by_symbol: Dict[str, List[QuoteSnapshot]],
    as_of: datetime,
    market_cap_unit_status: UnitVerificationStatus,
    turnover_unit_status: UnitVerificationStatus,
    maximum_age_seconds: int,
    maximum_future_skew_seconds: int,
) -> SectorFeatureSnapshot:
    category_code, category_name, division_code, division_name = key
    expected_count = len(symbols)
    returned_count = 0
    fresh_count = 0
    source_time_present_count = 0
    change_present_count = 0
    market_cap_present_count = 0
    turnover_present_count = 0
    valid_return_count = 0
    valid_market_cap_count = 0
    valid_turnover_count = 0
    row_reasons = []
    return_reasons = []
    market_cap_reasons = []
    turnover_reasons = []
    usable_quotes: Dict[str, QuoteSnapshot] = {}

    if expected_count <= 1:
        row_reasons.append("insufficient_constituents")

    for symbol in symbols:
        rows = quotes_by_symbol.get(symbol, [])
        if not rows:
            row_reasons.append("constituent_quote_missing")
            continue
        returned_count += 1
        if len(rows) != 1:
            row_reasons.append("duplicate_constituent_quote")
            continue
        item = rows[0]
        if item.source_time is not None:
            source_time_present_count += 1
        if item.change_percent is not None:
            change_present_count += 1
        if item.market_cap_source is not None:
            market_cap_present_count += 1
        if item.turnover_amount_source is not None:
            turnover_present_count += 1

        time_reasons = _source_time_reasons(
            item.source_time,
            as_of=as_of,
            maximum_age_seconds=maximum_age_seconds,
            maximum_future_skew_seconds=maximum_future_skew_seconds,
        )
        if time_reasons:
            row_reasons.extend(time_reasons)
            continue
        fresh_count += 1
        usable_quotes[symbol] = item

        if item.change_percent is None:
            return_reasons.append("change_percent_missing")
        else:
            valid_return_count += 1

        if item.market_cap_source is None:
            market_cap_reasons.append("market_cap_missing")
        elif item.market_cap_source <= 0:
            market_cap_reasons.append("market_cap_not_positive")
        else:
            valid_market_cap_count += 1

        if item.turnover_amount_source is None:
            turnover_reasons.append("turnover_amount_missing")
        elif item.turnover_amount_source < 0:
            turnover_reasons.append("turnover_amount_negative")
        else:
            valid_turnover_count += 1

    denominator = expected_count or 1
    field_coverage = {
        "source_time": source_time_present_count / denominator,
        "change_percent": change_present_count / denominator,
        "market_cap_source": market_cap_present_count / denominator,
        "turnover_amount_source": turnover_present_count / denominator,
    }
    if returned_count < expected_count:
        row_reasons.append("constituent_quote_coverage_incomplete")
    if fresh_count < expected_count and not any(
        reason.startswith("source_time_")
        for reason in row_reasons
    ):
        row_reasons.append("constituent_source_time_incomplete")
    if valid_return_count < expected_count and not return_reasons:
        return_reasons.append("change_percent_incomplete")
    if valid_market_cap_count < expected_count and not market_cap_reasons:
        market_cap_reasons.append("market_cap_incomplete")
    if valid_turnover_count < expected_count and not turnover_reasons:
        turnover_reasons.append("turnover_amount_incomplete")

    equal_blocking = _dedupe_reasons([*row_reasons, *return_reasons])
    cap_blocking = _dedupe_reasons([
        *row_reasons,
        *return_reasons,
        *market_cap_reasons,
    ])
    turnover_blocking = _dedupe_reasons([
        *row_reasons,
        *turnover_reasons,
    ])
    equal_available = not equal_blocking
    cap_available = not cap_blocking
    turnover_available = not turnover_blocking

    equal_return = None
    if equal_available:
        equal_return = sum(
            usable_quotes[symbol].change_percent
            for symbol in symbols
        ) / expected_count

    cap_weighted_return = None
    ex_top_return = None
    top_contributor_symbol = None
    top_contribution = None
    if cap_available:
        total_market_cap = sum(
            usable_quotes[symbol].market_cap_source
            for symbol in symbols
        )
        contributions = []
        for symbol in symbols:
            item = usable_quotes[symbol]
            weight = item.market_cap_source / total_market_cap
            contributions.append((
                symbol,
                weight,
                weight * item.change_percent,
            ))
        cap_weighted_return = sum(value[2] for value in contributions)
        top_contributor_symbol, top_weight, top_contribution = max(
            contributions,
            key=lambda value: value[2],
        )
        other_weight = 1.0 - top_weight
        if other_weight > 0:
            ex_top_return = (
                cap_weighted_return - top_contribution
            ) / other_weight

    ex_top_available = cap_available and ex_top_return is not None
    ex_top_reasons = list(cap_blocking)
    if cap_available and ex_top_return is None:
        ex_top_reasons.append("ex_top_has_no_remaining_constituent")

    advancers = 0
    decliners = 0
    flat = 0
    for item in usable_quotes.values():
        if item.change_percent is None:
            continue
        if item.change_percent > 0:
            advancers += 1
        elif item.change_percent < 0:
            decliners += 1
        else:
            flat += 1
    unavailable = expected_count - advancers - decliners - flat
    valid_breadth_count = advancers + decliners + flat
    breadth_available = equal_available and valid_breadth_count == expected_count
    up_ratio = (
        advancers / valid_breadth_count
        if breadth_available and valid_breadth_count
        else None
    )

    turnover_value = None
    if turnover_available:
        turnover_value = sum(
            usable_quotes[symbol].turnover_amount_source
            for symbol in symbols
        )

    completeness_reasons = _dedupe_reasons([
        *row_reasons,
        *return_reasons,
        *market_cap_reasons,
        *turnover_reasons,
    ])
    completeness = SectorConstituentCompleteness(
        expectedCount=expected_count,
        returnedCount=returned_count,
        freshCount=fresh_count,
        validReturnCount=valid_return_count,
        validMarketCapCount=valid_market_cap_count,
        validTurnoverCount=valid_turnover_count,
        rowCoverage=(returned_count / expected_count if expected_count else 0.0),
        requiredFieldCoverage=field_coverage,
        isComplete=not completeness_reasons,
        reasons=completeness_reasons,
    )

    cap_warnings = []
    if market_cap_unit_status != UnitVerificationStatus.VERIFIED:
        cap_warnings.append("market_cap_unit_unverified")
    turnover_warnings = []
    if turnover_unit_status != UnitVerificationStatus.VERIFIED:
        turnover_warnings.append("turnover_unit_unverified")
    returns = SectorReturnSnapshot(
        equalReturn=_metric(
            equal_return,
            available=equal_available,
            reasons=equal_blocking,
        ),
        capWeightedReturn=_metric(
            cap_weighted_return,
            available=cap_available,
            reasons=[*cap_blocking, *cap_warnings],
        ),
        exTopReturn=_metric(
            ex_top_return,
            available=ex_top_available,
            reasons=[*ex_top_reasons, *cap_warnings],
        ),
        topContributorSymbol=top_contributor_symbol,
        topContributionPercentPoints=top_contribution,
        marketCapUnitStatus=market_cap_unit_status,
        formalUsable=False,
        reasons=_dedupe_reasons([
            *equal_blocking,
            *cap_blocking,
            *ex_top_reasons,
            *cap_warnings,
        ]),
    )
    breadth = SectorBreadthSnapshot(
        advancers=advancers,
        decliners=decliners,
        flat=flat,
        unavailable=unavailable,
        upRatio=_metric(
            up_ratio,
            available=breadth_available,
            reasons=equal_blocking,
        ),
        formalUsable=False,
        reasons=equal_blocking,
    )
    turnover = SectorTurnoverSnapshot(
        rawValue=turnover_value,
        contributingCount=valid_turnover_count,
        unitStatus=turnover_unit_status,
        available=turnover_available,
        formalUsable=False,
        reasons=_dedupe_reasons([
            *turnover_blocking,
            *turnover_warnings,
        ]),
    )
    shadow_usable = all((
        equal_available,
        cap_available,
        ex_top_available,
        breadth_available,
        turnover_available,
    ))
    sector_reasons = _dedupe_reasons([
        *completeness_reasons,
        *ex_top_reasons,
        *cap_warnings,
        *turnover_warnings,
        "formal_use_not_approved",
    ])
    return SectorFeatureSnapshot(
        releasePeriod=release_period,
        categoryCode=category_code,
        categoryName=category_name,
        divisionCode=division_code,
        divisionName=division_name,
        constituentSymbols=tuple(symbols),
        completeness=completeness,
        returns=returns,
        breadth=breadth,
        turnover=turnover,
        shadowUsable=shadow_usable,
        formalUsable=False,
        reasons=sector_reasons,
    )


def build_sector_features(
    classification: IndustryClassificationSnapshot,
    quote_batch: SourceBatch[QuoteSnapshot],
    *,
    stock_symbols: Iterable[str],
    etf_symbols: Iterable[str],
    market_cap_unit_status: UnitVerificationStatus = (
        UnitVerificationStatus.UNVERIFIED
    ),
    turnover_unit_status: UnitVerificationStatus = (
        UnitVerificationStatus.UNVERIFIED
    ),
    maximum_age_seconds: int = 90,
    maximum_future_skew_seconds: int = 5,
) -> SectorFeatureBatch:
    if maximum_age_seconds < 0:
        raise ValueError("maximum_age_seconds不得小于0")
    if maximum_future_skew_seconds < 0:
        raise ValueError("maximum_future_skew_seconds不得小于0")
    if classification.meta.radar_run_id != quote_batch.meta.radar_run_id:
        raise ValueError("行业分类与股票行情必须属于同一radarRunId")
    if classification.meta.as_of != quote_batch.meta.as_of:
        raise ValueError("行业分类与股票行情必须使用同一asOf")
    if len(quote_batch.items) != quote_batch.meta.returned_count:
        raise ValueError("行情批次returnedCount与实际记录数不一致")

    stocks = _normalize_universe(stock_symbols, "A股股票池")
    etfs = _normalize_universe(etf_symbols, "ETF池")
    stock_set = set(stocks)
    etf_set = set(etfs)
    overlap = stock_set & etf_set
    if overlap:
        raise ValueError(f"A股股票池与ETF池重叠：{sorted(overlap)[0]}")

    accepted = _validate_classification_universe(classification, stocks)
    grouped = _group_by_division(accepted)
    quotes_by_symbol = defaultdict(list)
    excluded_etf_symbols = set()
    unknown_quote_symbols = set()
    for item in quote_batch.items:
        if item.symbol in stock_set:
            quotes_by_symbol[item.symbol].append(item)
        elif item.symbol in etf_set:
            excluded_etf_symbols.add(item.symbol)
        else:
            unknown_quote_symbols.add(item.symbol)
    duplicate_quote_symbols = tuple(sorted(
        symbol
        for symbol, rows in quotes_by_symbol.items()
        if len(rows) > 1
    ))

    release = classification.release
    sectors = [
        _build_sector(
            release_period=release.release_period,
            key=key,
            symbols=symbols,
            quotes_by_symbol=quotes_by_symbol,
            as_of=classification.meta.as_of,
            market_cap_unit_status=market_cap_unit_status,
            turnover_unit_status=turnover_unit_status,
            maximum_age_seconds=maximum_age_seconds,
            maximum_future_skew_seconds=maximum_future_skew_seconds,
        )
        for key, symbols in sorted(
            grouped.items(),
            key=lambda value: value[0][2],
        )
    ]

    batch_reasons = []
    if classification.status != SourceStatus.HEALTHY:
        batch_reasons.append("classification_source_degraded")
    if classification.completeness.unconfirmed_count:
        batch_reasons.append("classification_mapping_incomplete")
    expected_quote_count = len(stocks) + len(etfs)
    if quote_batch.meta.expected_count is None:
        batch_reasons.append("quote_expected_count_unknown")
    elif quote_batch.meta.expected_count != expected_quote_count:
        batch_reasons.append("quote_expected_count_mismatch")
    batch_reasons.extend(_source_time_reasons(
        quote_batch.meta.source_time,
        as_of=classification.meta.as_of,
        maximum_age_seconds=maximum_age_seconds,
        maximum_future_skew_seconds=maximum_future_skew_seconds,
    ))
    batch_reasons.extend(
        f"quote_source_issue:{issue.code}"
        for issue in quote_batch.meta.issues
    )
    if duplicate_quote_symbols:
        batch_reasons.append("duplicate_quote_symbols")
    if unknown_quote_symbols:
        batch_reasons.append("unknown_quote_symbols")
    unavailable_sector_count = sum(
        not sector.shadow_usable
        for sector in sectors
    )
    if unavailable_sector_count:
        batch_reasons.append("sector_features_incomplete")
    if market_cap_unit_status != UnitVerificationStatus.VERIFIED:
        batch_reasons.append("market_cap_unit_unverified")
    if turnover_unit_status != UnitVerificationStatus.VERIFIED:
        batch_reasons.append("turnover_unit_unverified")
    batch_reasons.append("formal_use_not_approved")
    normalized_reasons = _dedupe_reasons(batch_reasons)
    shadow_usable = bool(sectors) and unavailable_sector_count == 0
    status = (
        SourceStatus.HEALTHY
        if not normalized_reasons
        else SourceStatus.DEGRADED
    )
    return SectorFeatureBatch(
        radarRunId=classification.meta.radar_run_id,
        classificationBatchId=classification.meta.batch_id,
        quoteBatchId=quote_batch.meta.batch_id,
        releasePeriod=release.release_period,
        classificationDocumentSha256=release.document_sha256,
        asOf=classification.meta.as_of,
        sourceTime=quote_batch.meta.source_time,
        fetchedAt=max(
            classification.meta.fetched_at,
            quote_batch.meta.fetched_at,
        ),
        classificationMappingCoverage=(
            classification.completeness.mapping_coverage
        ),
        mappedConstituentCount=len(accepted),
        unconfirmedStockCount=(
            classification.completeness.unconfirmed_count
        ),
        sectors=sectors,
        excludedEtfCount=len(excluded_etf_symbols),
        duplicateQuoteSymbols=duplicate_quote_symbols,
        unknownQuoteSymbols=tuple(sorted(unknown_quote_symbols)),
        status=status,
        shadowUsable=shadow_usable,
        formalUsable=False,
        reasons=normalized_reasons,
    )
