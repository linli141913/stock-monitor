"""阶段6K运行时龙头证据组装。

本模块只消费调用方已经冻结的全市场行情批次、市场/行业聚合和证券行业
映射。它不抓取数据、不连接数据库，也不为尚未冻结公式的维度补分。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationRecord,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    QuoteSnapshot,
    SecurityMasterRecord,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
)
from radar.leader_input_gate import (
    LeaderDimensionEvidence,
    LeaderInputEvidence,
    LeaderSourceEvidence,
    LeaderSourceKind,
)
from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureInput,
    build_leader_business_catalyst_features,
    missing_leader_business_catalyst_features,
)
from radar.leader_history_features import (
    LeaderHistoryFeatureInput,
    LeaderHistoryFeatureResult,
    build_leader_history_features,
    missing_leader_history_features,
)
from radar.leader_liquidity_features import (
    LeaderLiquidityFeatureResult,
    build_leader_liquidity_features,
)
from radar.leader_research_features import (
    ResearchFeatureStatus,
    build_leader_research_features,
    build_leader_research_market_context,
    is_research_quote_eligible,
)
from radar.leader_scoring import LeaderGateInput, LeaderMetricStatus
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderStateRecord,
)
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureInput,
    build_leader_tradability_features,
    missing_leader_tradability_features,
)


UTC = timezone.utc
MAX_CANDIDATES_PER_INDUSTRY = 5


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _minimum_coverage(values: Mapping[str, float]) -> float:
    numeric = tuple(float(value) for value in values.values())
    return min(numeric) if numeric else 0.0


def _source_status(
    status: SourceStatus,
) -> LeaderMetricStatus:
    return {
        SourceStatus.HEALTHY: LeaderMetricStatus.VERIFIED,
        SourceStatus.DEGRADED: LeaderMetricStatus.SOURCE_UNVERIFIED,
        SourceStatus.STALE: LeaderMetricStatus.STALE,
        SourceStatus.FAILED: LeaderMetricStatus.SOURCE_FAILED,
    }[status]


def _research_source_status(
    status: LeaderMetricStatus,
) -> ResearchFeatureStatus:
    return {
        LeaderMetricStatus.VERIFIED: ResearchFeatureStatus.READY,
        LeaderMetricStatus.MISSING: ResearchFeatureStatus.MISSING,
        LeaderMetricStatus.STALE: ResearchFeatureStatus.STALE,
        LeaderMetricStatus.SOURCE_FAILED: (
            ResearchFeatureStatus.SOURCE_FAILED
        ),
        LeaderMetricStatus.SOURCE_UNVERIFIED: (
            ResearchFeatureStatus.SOURCE_UNVERIFIED
        ),
        LeaderMetricStatus.NOT_APPLICABLE: (
            ResearchFeatureStatus.MISSING
        ),
    }[status]


def _research_dimension_status(
    status: ResearchFeatureStatus,
) -> LeaderMetricStatus:
    return {
        ResearchFeatureStatus.READY: (
            LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        ResearchFeatureStatus.MISSING: LeaderMetricStatus.MISSING,
        ResearchFeatureStatus.STALE: LeaderMetricStatus.STALE,
        ResearchFeatureStatus.SOURCE_FAILED: (
            LeaderMetricStatus.SOURCE_FAILED
        ),
        ResearchFeatureStatus.SOURCE_UNVERIFIED: (
            LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
    }[status]


@dataclass(frozen=True)
class LeaderRuntimeAssembly:
    status: str
    as_of: datetime
    evidence_items: Tuple[LeaderInputEvidence, ...]
    gate_reasons: Tuple[str, ...]
    scanned_count: int
    mapped_count: int

    @property
    def item_count(self) -> int:
        return len(self.evidence_items)


def _not_ready(
    *,
    as_of: datetime,
    reasons: Sequence[str],
    scanned_count: int,
    mapped_count: int = 0,
) -> LeaderRuntimeAssembly:
    return LeaderRuntimeAssembly(
        status="not_ready",
        as_of=as_of,
        evidence_items=(),
        gate_reasons=_dedupe(reasons),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
    )


def _accepted_industry_map(
    records: Sequence[IndustryClassificationRecord],
) -> Tuple[
    Mapping[str, IndustryClassificationRecord],
    Tuple[str, ...],
]:
    accepted = {}
    conflicts = set()
    for record in records:
        if (
            record.record_status != IndustryRecordStatus.ACCEPTED
            or record.identity_status == IndustryIdentityStatus.UNRESOLVED
            or record.security_identity is None
        ):
            continue
        current = accepted.get(record.security_identity)
        if (
            current is not None
            and current.division_code != record.division_code
        ):
            conflicts.add(record.security_identity)
            continue
        accepted[record.security_identity] = record
    for symbol in conflicts:
        accepted.pop(symbol, None)
    return accepted, tuple(sorted(conflicts))


def _market_source(
    market_snapshot: Mapping,
) -> LeaderSourceEvidence:
    index_completeness = market_snapshot["indexCompleteness"]
    breadth_completeness = market_snapshot["breadth"]["completeness"]
    complete = bool(
        index_completeness["isComplete"]
        and breadth_completeness["isComplete"]
        and market_snapshot["duplicateSymbolCount"] == 0
        and market_snapshot["unknownSymbolCount"] == 0
    )
    reasons = _dedupe((
        *index_completeness.get("reasons", ()),
        *breadth_completeness.get("reasons", ()),
        "market_aggregate_incomplete" if not complete else None,
    ))
    return LeaderSourceEvidence(
        source_contract_id=(
            "radar-market-aggregate-v1:"
            f"{market_snapshot['radarRunId']}"
        ),
        source_kind=LeaderSourceKind.MARKET,
        source_name="radar_market_environment",
        source_time=market_snapshot.get("sourceTime"),
        fetched_at=market_snapshot["fetchedAt"],
        status=(
            LeaderMetricStatus.VERIFIED
            if complete
            else LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        row_coverage=min(
            float(index_completeness["rowCoverage"]),
            float(breadth_completeness["rowCoverage"]),
        ),
        required_field_coverage=min(
            _minimum_coverage(
                index_completeness["requiredFieldCoverage"]
            ),
            _minimum_coverage(
                breadth_completeness["requiredFieldCoverage"]
            ),
        ),
        reasons=reasons,
    )


def _sector_source(
    sector: Mapping,
) -> LeaderSourceEvidence:
    complete = bool(sector["isComplete"] and sector["shadowUsable"])
    reasons = _dedupe((
        *sector.get("reasons", ()),
        "sector_aggregate_incomplete" if not complete else None,
    ))
    return LeaderSourceEvidence(
        source_contract_id=(
            "radar-sector-aggregate-v1:"
            f"{sector['radarRunId']}:{sector['divisionCode']}"
        ),
        source_kind=LeaderSourceKind.SECTOR,
        source_name="radar_sector_features",
        source_time=sector.get("sourceTime"),
        fetched_at=sector["fetchedAt"],
        status=(
            LeaderMetricStatus.VERIFIED
            if complete
            else LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        row_coverage=float(sector["rowCoverage"]),
        required_field_coverage=_minimum_coverage(
            sector["requiredFieldCoverage"]
        ),
        reasons=reasons,
    )


def _quote_source(
    quote_batch: SourceBatch[QuoteSnapshot],
    quote_health: SourceHealthResult,
    quote: QuoteSnapshot,
) -> LeaderSourceEvidence:
    return LeaderSourceEvidence(
        source_contract_id=(
            "tencent-full-market-quote-v1:"
            f"{quote_batch.meta.batch_id}"
        ),
        source_kind=LeaderSourceKind.QUOTE,
        source_name=quote.source,
        source_time=quote.source_time,
        fetched_at=quote.fetched_at,
        status=_source_status(quote_health.status),
        row_coverage=float(quote_batch.meta.row_coverage or 0),
        required_field_coverage=_minimum_coverage(
            quote_batch.meta.required_field_coverage
        ),
        reasons=tuple(quote_health.reasons),
    )


def _dimensions(
    *,
    market_source_id: str,
    sector_source_id: str,
    quote_source_id: str,
    history_result: LeaderHistoryFeatureResult,
    liquidity_result: LeaderLiquidityFeatureResult,
) -> Tuple[LeaderDimensionEvidence, ...]:
    if history_result.status == ResearchFeatureStatus.READY:
        continuity_status = LeaderMetricStatus.SOURCE_UNVERIFIED
        continuity_reasons = ("continuity_research_only",)
    else:
        continuity_status = {
            ResearchFeatureStatus.MISSING: LeaderMetricStatus.MISSING,
            ResearchFeatureStatus.SOURCE_UNVERIFIED: (
                LeaderMetricStatus.SOURCE_UNVERIFIED
            ),
            ResearchFeatureStatus.STALE: LeaderMetricStatus.STALE,
            ResearchFeatureStatus.SOURCE_FAILED: (
                LeaderMetricStatus.SOURCE_FAILED
            ),
        }[history_result.status]
        continuity_reasons = (
            history_result.reasons
            or ("continuity_history_missing",)
        )
    continuity_source_id = (
        history_result.source_contract_ids[0]
        if history_result.source_contract_ids
        else quote_source_id
    )
    return (
        LeaderDimensionEvidence(
            field_name="industry_strength",
            score=None,
            source_contract_id=sector_source_id,
            status=LeaderMetricStatus.SOURCE_UNVERIFIED,
            reasons=("formal_sector_state_missing",),
        ),
        LeaderDimensionEvidence(
            field_name="market_leadership",
            score=None,
            source_contract_id=market_source_id,
            status=LeaderMetricStatus.SOURCE_UNVERIFIED,
            reasons=("formal_leader_score_incomplete",),
        ),
        LeaderDimensionEvidence(
            field_name="relative_strength_continuity",
            score=None,
            source_contract_id=continuity_source_id,
            status=continuity_status,
            reasons=continuity_reasons,
        ),
        LeaderDimensionEvidence(
            field_name="liquidity_tradability",
            score=None,
            source_contract_id=(
                liquidity_result.source_contract_ids[0]
                if liquidity_result.source_contract_ids
                else quote_source_id
            ),
            status=_research_dimension_status(
                liquidity_result.status
            ),
            reasons=(
                liquidity_result.reasons
                or ("liquidity_evidence_missing",)
            ),
        ),
        LeaderDimensionEvidence(
            field_name="business_exposure",
            score=None,
            source_contract_id=None,
            status=LeaderMetricStatus.MISSING,
            reasons=("business_exposure_evidence_missing",),
        ),
        LeaderDimensionEvidence(
            field_name="auxiliary",
            score=None,
            source_contract_id=None,
            status=LeaderMetricStatus.NOT_APPLICABLE,
            reasons=("auxiliary_rule_disabled",),
        ),
    )


def build_leader_runtime_evidence(
    *,
    as_of: datetime,
    quote_batch: SourceBatch[QuoteSnapshot],
    quote_health: SourceHealthResult,
    market_snapshot: Optional[Mapping],
    sector_rows: Sequence[Mapping],
    industry_records: Sequence[IndustryClassificationRecord],
    security_records: Sequence[SecurityMasterRecord],
    previous_states: Optional[Mapping[str, LeaderStateRecord]] = None,
    history_inputs_by_symbol: Optional[
        Mapping[str, LeaderHistoryFeatureInput]
    ] = None,
    business_catalyst_inputs_by_symbol: Optional[
        Mapping[str, LeaderBusinessCatalystFeatureInput]
    ] = None,
    tradability_inputs_by_symbol: Optional[
        Mapping[str, LeaderTradabilityFeatureInput]
    ] = None,
) -> LeaderRuntimeAssembly:
    """组装每个行业涨跌幅前5名的研究性输入，不生成正式分数。"""

    as_of = _aware_utc(as_of, "as_of")
    if _aware_utc(quote_batch.meta.as_of, "quote_batch.as_of") != as_of:
        raise ValueError("行情批次as_of必须与龙头批次一致")
    scanned_count = len(quote_batch.items)
    if (
        quote_health.status != SourceStatus.HEALTHY
        or not quote_health.allows_new_state
    ):
        return _not_ready(
            as_of=as_of,
            reasons=("quote_source_not_healthy", *quote_health.reasons),
            scanned_count=scanned_count,
        )
    if market_snapshot is None:
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_missing",),
            scanned_count=scanned_count,
        )
    market_as_of = market_snapshot.get("asOf")
    if not isinstance(market_as_of, datetime):
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_as_of_missing",),
            scanned_count=scanned_count,
        )
    if _aware_utc(market_as_of, "market_snapshot.asOf") > as_of:
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_from_future",),
            scanned_count=scanned_count,
        )
    if not sector_rows:
        return _not_ready(
            as_of=as_of,
            reasons=("sector_snapshot_missing",),
            scanned_count=scanned_count,
        )
    if any(
        _aware_utc(row["asOf"], "sector_snapshot.asOf") > as_of
        for row in sector_rows
    ):
        return _not_ready(
            as_of=as_of,
            reasons=("sector_snapshot_from_future",),
            scanned_count=scanned_count,
        )

    industry_by_symbol, mapping_conflicts = _accepted_industry_map(
        industry_records
    )
    if not industry_by_symbol:
        return _not_ready(
            as_of=as_of,
            reasons=(
                "industry_mapping_missing",
                "industry_mapping_conflict" if mapping_conflicts else None,
            ),
            scanned_count=scanned_count,
        )

    security_by_symbol = {
        record.symbol: record
        for record in security_records
        if record.exchange in {"sse", "szse"}
    }
    quote_by_symbol = {
        quote.symbol: quote
        for quote in quote_batch.items
        if quote.symbol in security_by_symbol
    }
    sector_by_code = {
        str(row["divisionCode"]): row
        for row in sector_rows
    }
    grouped = {}
    for symbol, industry in industry_by_symbol.items():
        quote = quote_by_symbol.get(symbol)
        if (
            quote is None
            or not is_research_quote_eligible(quote, as_of)
            or industry.division_code not in sector_by_code
        ):
            continue
        grouped.setdefault(industry.division_code, []).append(quote)

    market_source = _market_source(market_snapshot)
    market_quotes = tuple(quote_by_symbol.values())
    research_market_context = (
        build_leader_research_market_context(
            as_of=as_of,
            market_quotes=market_quotes,
            security_by_symbol=security_by_symbol,
            market_snapshot=market_snapshot,
        )
    )
    previous_states = previous_states or {}
    history_inputs_by_symbol = history_inputs_by_symbol or {}
    business_catalyst_inputs_by_symbol = (
        business_catalyst_inputs_by_symbol or {}
    )
    tradability_inputs_by_symbol = tradability_inputs_by_symbol or {}
    evidence_items = []
    mapped_count = sum(len(items) for items in grouped.values())
    for division_code in sorted(grouped):
        sector = sector_by_code[division_code]
        sector_source = _sector_source(sector)
        ordered_quotes = sorted(
            grouped[division_code],
            key=lambda quote: (-float(quote.change_percent), quote.symbol),
        )[:MAX_CANDIDATES_PER_INDUSTRY]
        for rank, quote in enumerate(ordered_quotes, start=1):
            security = security_by_symbol[quote.symbol]
            industry = industry_by_symbol[quote.symbol]
            quote_source = _quote_source(
                quote_batch,
                quote_health,
                quote,
            )
            previous = previous_states.get(quote.symbol)
            history_input = history_inputs_by_symbol.get(quote.symbol)
            if history_input is None:
                history_result = missing_leader_history_features()
            elif history_input.as_of != as_of:
                history_result = missing_leader_history_features(
                    ("continuity_history_as_of_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            elif history_input.candidate.symbol != quote.symbol:
                history_result = missing_leader_history_features(
                    ("continuity_candidate_identity_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            elif (
                history_input.industry_benchmark.symbol
                != industry.division_code
            ):
                history_result = missing_leader_history_features(
                    ("continuity_industry_identity_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            else:
                history_result = build_leader_history_features(
                    history_input
                )
            business_input = business_catalyst_inputs_by_symbol.get(
                quote.symbol
            )
            if business_input is None:
                business_result = (
                    missing_leader_business_catalyst_features()
                )
            elif business_input.as_of != as_of:
                business_result = (
                    missing_leader_business_catalyst_features(
                        ("business_evidence_as_of_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            elif (
                business_input.symbol != quote.symbol
                or business_input.industry_code
                != industry.division_code
                or business_input.industry_release_id
                != str(sector["industryReleaseId"])
            ):
                business_result = (
                    missing_leader_business_catalyst_features(
                        ("business_evidence_identity_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            else:
                business_result = (
                    build_leader_business_catalyst_features(
                        business_input
                    )
                )
            liquidity_result = build_leader_liquidity_features(
                as_of=as_of,
                quote=quote,
                source_contract_id=quote_source.source_contract_id,
                source_status=_research_source_status(
                    quote_source.status
                ),
            )
            tradability_input = tradability_inputs_by_symbol.get(
                quote.symbol
            )
            if tradability_input is None:
                tradability_result = (
                    missing_leader_tradability_features()
                )
            elif tradability_input.as_of != as_of:
                tradability_result = (
                    missing_leader_tradability_features(
                        ("tradability_evidence_as_of_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            elif (
                tradability_input.quote.symbol != quote.symbol
                or (
                    tradability_input.quote_source_contract_id
                    != quote_source.source_contract_id
                )
            ):
                tradability_result = (
                    missing_leader_tradability_features(
                        ("tradability_evidence_identity_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            else:
                tradability_result = (
                    build_leader_tradability_features(
                        tradability_input
                    )
                )
            research_features = build_leader_research_features(
                as_of=as_of,
                candidate_quote=quote,
                candidate_security=security,
                industry_quotes=tuple(grouped[division_code]),
                market_quotes=market_quotes,
                security_by_symbol=security_by_symbol,
                sector=sector,
                sector_rows=sector_rows,
                market_snapshot=market_snapshot,
                sector_source_contract_id=(
                    sector_source.source_contract_id
                ),
                market_source_contract_id=(
                    market_source.source_contract_id
                ),
                quote_source_contract_id=(
                    quote_source.source_contract_id
                ),
                sector_source_status=_research_source_status(
                    sector_source.status
                ),
                market_source_status=_research_source_status(
                    market_source.status
                ),
                quote_source_status=_research_source_status(
                    quote_source.status
                ),
                market_context=research_market_context,
            )
            evidence_items.append(LeaderInputEvidence(
                symbol=quote.symbol,
                name=security.name,
                as_of=as_of,
                industry_code=industry.division_code,
                industry_name=industry.division_name,
                dimensions=_dimensions(
                    market_source_id=market_source.source_contract_id,
                    sector_source_id=sector_source.source_contract_id,
                    quote_source_id=quote_source.source_contract_id,
                    history_result=history_result,
                    liquidity_result=liquidity_result,
                ),
                gates=LeaderGateInput(
                    industry_gate_passed=False,
                    stock_gate_passed=False,
                    market_leadership_passed=False,
                    industry_contribution_passed=False,
                    liquidity_passed=False,
                    tradability_passed=False,
                    continuity_passed=False,
                    recovery_passed=False,
                    risk_filter_passed=False,
                    business_exposure_status=BusinessExposureStatus.MISSING,
                ),
                sources=(
                    market_source,
                    sector_source,
                    quote_source,
                ),
                consecutive_signal_periods=(
                    previous.state_age_periods + 1
                    if previous is not None
                    else 1
                ),
                evidence={
                    "runtimeInputVersion": "radar-leader-runtime-input-v1",
                    "researchFeatures": {
                        **research_features.to_evidence(),
                        "historyContinuity": (
                            history_result.to_evidence()
                        ),
                        "liquidityTradability": (
                            liquidity_result.to_evidence()
                        ),
                        "businessCatalyst": (
                            business_result.to_evidence()
                        ),
                        "securityTradability": (
                            tradability_result.to_evidence()
                        ),
                    },
                    "withinIndustryRankByChangePercent": rank,
                    "quote": {
                        "price": quote.price,
                        "changePercent": quote.change_percent,
                        "turnoverAmountSource": (
                            quote.turnover_amount_source
                        ),
                        "turnoverRatePercent": (
                            quote.turnover_rate_percent
                        ),
                        "volumeRatio": quote.volume_ratio,
                        "marketCapSource": quote.market_cap_source,
                    },
                    "sector": {
                        "equalReturn": sector.get("equalReturn"),
                        "capWeightedReturn": (
                            sector.get("capWeightedReturn")
                        ),
                        "exTopReturn": sector.get("exTopReturn"),
                        "upRatio": sector.get("upRatio"),
                        "topContributorSymbol": (
                            sector.get("topContributorSymbol")
                        ),
                        "topContributionPercentPoints": (
                            sector.get("topContributionPercentPoints")
                        ),
                    },
                    "market": {
                        "indices": tuple({
                            "indexKey": item["indexKey"],
                            "changePercent": item["changePercent"],
                        } for item in market_snapshot.get("indices", ())),
                    },
                },
                invalidation={
                    "missingPrerequisites": [
                        "formal_sector_state",
                        "stock_hard_filter",
                        (
                            "continuity_formal_rule"
                            if history_result.status
                            == ResearchFeatureStatus.READY
                            else "continuity_history"
                        ),
                        "business_exposure_evidence",
                        "tradability_fields",
                        "risk_evidence",
                    ],
                },
            ))

    if not evidence_items:
        return _not_ready(
            as_of=as_of,
            reasons=(
                "leader_candidate_input_empty",
                "industry_mapping_conflict" if mapping_conflicts else None,
            ),
            scanned_count=scanned_count,
            mapped_count=mapped_count,
        )
    return LeaderRuntimeAssembly(
        status="ready",
        as_of=as_of,
        evidence_items=tuple(evidence_items),
        gate_reasons=(
            ("industry_mapping_conflict",)
            if mapping_conflicts
            else ()
        ),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
    )
