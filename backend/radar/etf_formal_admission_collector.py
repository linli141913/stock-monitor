"""Read-only live collector for forward ETF formal-admission evidence.

External sources are collected before a replay sample is frozen.  The
resulting current-observation evidence is then rebound to the later sample
time, so no response fetched after the sample can leak into that sample.
This module does not open SQLite, score or rank ETFs, or enable a formal gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from market_calendar import parse_sse_calendar
from radar.contracts import (
    EtfIndexIdentityEvidence,
    EtfProductMasterRecord,
    EtfRankingInputAudit,
    IndexConstituentSetEvidence,
    IndexIndustryExposureResult,
    IndexMethodologyEvidence,
    IndustryClassificationSnapshot,
    QuoteSnapshot,
    SourceBatch,
)
from radar.etf_formal_admission import (
    EtfFormalAdmissionBundle,
    EtfIndustryScopeEvidence,
    EtfLifecycleEvidence,
    build_etf_formal_admission_bundle,
    build_etf_industry_scope_evidence,
    build_etf_lifecycle_evidence_from_current_master,
    provide_etf_formal_admission_evidence,
)
from radar.etf_industry_exposure import calculate_index_industry_exposure
from radar.etf_ranking_inputs import (
    build_etf_daily_fact,
    build_ranking_input_audit,
)
from radar.sources.etf_daily_facts import fetch_etf_daily_share_observations
from radar.sources.etf_fund_size_history import (
    fetch_verified_sse_etf_fund_size_snapshot,
)
from radar.sources.etf_index_evidence import (
    OfficialIndexIdentityResolution,
    OfficialIndexPocResult,
    OfficialSseFundRelationDocument,
    fetch_csindex_index_poc,
    fetch_official_csindex_identity_resolution,
    fetch_official_sse_fund_relation_document,
    verify_official_sse_fund_index_relation,
)
from radar.sources.etf_tracking_history import (
    build_verified_etf_tracking_window,
    fetch_csindex_price_history,
    fetch_supported_manager_nav_history,
)
from radar.sources.etf_turnover_history import (
    fetch_verified_sse_etf_turnover_window,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
    _fetch_calendar_document,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MAXIMUM_FORMAL_ETF_COUNT = 10
TRACKING_POINT_COUNT = 61
TURNOVER_TRADING_DAY_COUNT = 20


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}_timezone_required")
    return value


def _safe_call(
    reasons: list[str],
    reason: str,
    function: Callable[..., Any],
    **kwargs: Any,
) -> Any:
    try:
        return function(**kwargs)
    except Exception:
        reasons.append(reason)
        return None


def _default_calendar(*, as_of: datetime) -> PublicCalendarDocument:
    return _fetch_calendar_document(as_of=as_of)


@dataclass(frozen=True)
class EtfFormalAdmissionLiveProviders:
    calendar: Callable[..., PublicCalendarDocument] = _default_calendar
    quote: Callable[..., SourceBatch] = fetch_tencent_quotes
    daily_shares: Callable[..., SourceBatch] = (
        fetch_etf_daily_share_observations
    )
    relation_document: Callable[..., OfficialSseFundRelationDocument] = (
        fetch_official_sse_fund_relation_document
    )
    index_resolution: Callable[..., OfficialIndexIdentityResolution] = (
        fetch_official_csindex_identity_resolution
    )
    index_poc: Callable[..., OfficialIndexPocResult] = fetch_csindex_index_poc
    fund_size: Callable[..., Any] = (
        fetch_verified_sse_etf_fund_size_snapshot
    )
    turnover: Callable[..., Any] = fetch_verified_sse_etf_turnover_window
    nav_history: Callable[..., Any] = fetch_supported_manager_nav_history
    index_history: Callable[..., Any] = fetch_csindex_price_history


@dataclass(frozen=True)
class CollectedEtfFormalAdmissionMaterial:
    symbol: str
    product: EtfProductMasterRecord
    lifecycle_evidence: Optional[EtfLifecycleEvidence] = None
    industry_scope_evidence: Optional[EtfIndustryScopeEvidence] = None
    index_relation_evidence: Optional[EtfIndexIdentityEvidence] = None
    methodology_evidence: Optional[IndexMethodologyEvidence] = None
    constituent_evidence: Optional[IndexConstituentSetEvidence] = None
    industry_exposure_evidence: Optional[
        IndexIndustryExposureResult
    ] = None
    ranking_input_evidence: Optional[EtfRankingInputAudit] = None
    source_reasons: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.symbol != self.product.symbol:
            raise ValueError("etf_formal_material_identity_mismatch")
        if len(self.source_reasons) != len(set(self.source_reasons)):
            raise ValueError("etf_formal_material_reasons_duplicated")


@dataclass(frozen=True)
class EtfFormalAdmissionMaterialCollection:
    material_as_of: datetime
    items: Tuple[CollectedEtfFormalAdmissionMaterial, ...]

    def __post_init__(self) -> None:
        _aware(self.material_as_of, "materialAsOf")
        symbols = tuple(item.symbol for item in self.items)
        if (
            not symbols
            or symbols != tuple(sorted(symbols))
            or len(symbols) != len(set(symbols))
            or len(symbols) > MAXIMUM_FORMAL_ETF_COUNT
        ):
            raise ValueError("etf_formal_material_collection_invalid")

    def to_summary(self) -> dict[str, Any]:
        component_names = (
            "lifecycle_evidence",
            "industry_scope_evidence",
            "index_relation_evidence",
            "methodology_evidence",
            "constituent_evidence",
            "industry_exposure_evidence",
            "ranking_input_evidence",
        )
        return {
            "contractId": "radar-etf-formal-material-summary-v1",
            "materialAsOf": self.material_as_of.isoformat(),
            "symbolCount": len(self.items),
            "items": [{
                "symbol": item.symbol,
                "sourceReasons": list(item.source_reasons),
                "components": {
                    name.removesuffix("_evidence"): (
                        getattr(item, name) is not None
                    )
                    for name in component_names
                },
            } for item in self.items],
        }


def _completed_trade_dates(
    document: PublicCalendarDocument,
    *,
    reference_at: datetime,
    count: int,
) -> Tuple[date, ...]:
    _aware(reference_at, "calendarReferenceAt")
    if not isinstance(document, PublicCalendarDocument) or count < 1:
        raise ValueError("etf_formal_calendar_unverified")
    current = reference_at.date() - timedelta(days=1)
    values = []
    snapshots = {}
    for _ in range(800):
        if current.year not in snapshots:
            snapshots[current.year] = parse_sse_calendar(
                document.text,
                current.year,
            )
        if (
            current.weekday() < 5
            and current.isoformat()
            not in snapshots[current.year].closed_days
        ):
            values.append(current)
            if len(values) == count:
                return tuple(reversed(values))
        current -= timedelta(days=1)
    raise ValueError("etf_formal_calendar_window_unavailable")


def _rebind_model(value: Any, as_of: datetime) -> Any:
    if value is None:
        return None
    payload = value.model_dump(mode="python", by_alias=True)
    payload["asOf"] = as_of
    return type(value).model_validate(payload)


def _weighted_constituents(
    result: Optional[OfficialIndexPocResult],
) -> Optional[IndexConstituentSetEvidence]:
    if result is None:
        return None
    candidates = tuple(
        item for item in result.constituent_sets
        if item.returned_count > 0
        and item.weight_count == item.returned_count
        and all(value.weight is not None for value in item.items)
    )
    return candidates[0] if len(candidates) == 1 else None


def _manager_nav_supported(product: EtfProductMasterRecord) -> bool:
    manager = str(product.manager or "").replace(" ", "")
    return any(value in manager for value in ("华泰柏瑞", "华夏基金"))


def collect_live_etf_formal_admission_materials(
    *,
    symbols: Sequence[str],
    radar_run_id: str,
    product_master: SourceBatch,
    industry_classification: IndustryClassificationSnapshot,
    started_at: datetime,
    providers: Optional[EtfFormalAdmissionLiveProviders] = None,
    clock: Callable[[], datetime] = _now,
) -> EtfFormalAdmissionMaterialCollection:
    """Collect bounded public sources; all failures remain missing evidence."""
    started_at = _aware(started_at, "startedAt")
    normalized = tuple(sorted(dict.fromkeys(
        str(value or "").strip() for value in symbols
    )))
    if (
        not normalized
        or len(normalized) != len(tuple(symbols))
        or len(normalized) > MAXIMUM_FORMAL_ETF_COUNT
        or any(len(value) != 6 or not value.isdigit() for value in normalized)
    ):
        raise ValueError("formal_etf_symbols_invalid")
    if not isinstance(product_master, SourceBatch):
        raise ValueError("formal_etf_product_master_unverified")
    if not isinstance(industry_classification, IndustryClassificationSnapshot):
        raise ValueError("formal_etf_industry_classification_unverified")
    products = {item.symbol: item for item in product_master.items}
    if any(symbol not in products for symbol in normalized):
        raise ValueError("formal_etf_product_not_found")

    active = providers or EtfFormalAdmissionLiveProviders()
    common_reasons: list[str] = []
    reference_at = _aware(clock(), "collectionReferenceAt")
    calendar = _safe_call(
        common_reasons,
        "etf_calendar_source_failed",
        active.calendar,
        as_of=reference_at,
    )
    trade_dates: Tuple[date, ...] = ()
    if calendar is not None:
        try:
            trade_dates = _completed_trade_dates(
                calendar,
                reference_at=reference_at,
                count=TRACKING_POINT_COUNT,
            )
        except Exception:
            common_reasons.append("etf_calendar_window_unverified")

    quote_batch = _safe_call(
        common_reasons,
        "etf_quote_source_failed",
        active.quote,
        symbols=normalized,
        radar_run_id=radar_run_id,
        batch_id=f"{radar_run_id}-formal-etf-quotes",
        as_of=reference_at,
    )
    share_batch = None
    if trade_dates:
        share_batch = _safe_call(
            common_reasons,
            "etf_share_source_failed",
            active.daily_shares,
            radar_run_id=radar_run_id,
            batch_id=f"{radar_run_id}-formal-etf-shares",
            as_of=reference_at,
            report_date=trade_dates[-1],
            sse_symbols=tuple(
                symbol for symbol in normalized
                if products[symbol].exchange == "sse"
            ),
            szse_symbols=tuple(
                symbol for symbol in normalized
                if products[symbol].exchange == "szse"
            ),
        )
    quotes = {
        item.symbol: item
        for item in getattr(quote_batch, "items", ())
        if isinstance(item, QuoteSnapshot)
    }
    shares = {
        item.symbol: item
        for item in getattr(share_batch, "items", ())
    }

    raw_items = []
    for symbol in normalized:
        product = products[symbol]
        reasons = list(common_reasons)
        document = _safe_call(
            reasons,
            "etf_index_relation_source_failed",
            active.relation_document,
            symbol=symbol,
        )
        requested_index_name = (
            getattr(document, "index_name", None)
            or product.target_index_name
        )
        resolution = None
        if requested_index_name:
            resolution = _safe_call(
                reasons,
                "etf_index_identity_source_failed",
                active.index_resolution,
                requested_index_name=requested_index_name,
            )
        index_result = None
        index_code = getattr(resolution, "index_code", None)
        index_name = getattr(resolution, "index_name", None)
        if index_code:
            index_result = _safe_call(
                reasons,
                "etf_index_material_source_failed",
                active.index_poc,
                index_code=index_code,
                as_of=reference_at,
            )

        fund_size = turnover = nav_history = index_history = None
        if trade_dates and product.exchange == "sse":
            fund_size = _safe_call(
                reasons,
                "etf_fund_size_source_failed",
                active.fund_size,
                symbol=symbol,
                expected_trade_date=trade_dates[-1],
            )
            turnover = _safe_call(
                reasons,
                "etf_turnover_source_failed",
                active.turnover,
                symbol=symbol,
                expected_trade_dates=trade_dates[-TURNOVER_TRADING_DAY_COUNT:],
            )
        if trade_dates and index_code and index_name:
            if _manager_nav_supported(product):
                nav_history = _safe_call(
                    reasons,
                    "etf_nav_source_failed",
                    active.nav_history,
                    manager=product.manager or "",
                    symbol=symbol,
                    expected_trade_dates=trade_dates,
                )
            else:
                reasons.append("etf_nav_provider_not_supported")
            index_history = _safe_call(
                reasons,
                "etf_index_history_source_failed",
                active.index_history,
                index_code=index_code,
                index_name=index_name,
                expected_trade_dates=trade_dates,
            )
        raw_items.append((
            symbol,
            product,
            document,
            resolution,
            index_result,
            fund_size,
            turnover,
            nav_history,
            index_history,
            quotes.get(symbol),
            shares.get(symbol),
            reasons,
        ))

    material_as_of = _aware(clock(), "materialAsOf")
    collected = []
    for (
        symbol,
        product,
        document,
        resolution,
        index_result,
        fund_size,
        turnover,
        nav_history,
        index_history,
        quote,
        share,
        reasons,
    ) in raw_items:
        lifecycle = scope = relation = methodology = constituents = None
        exposure = ranking = None
        try:
            lifecycle = build_etf_lifecycle_evidence_from_current_master(
                product=product,
                as_of=material_as_of,
            )
        except Exception:
            reasons.append("etf_lifecycle_evidence_unverified")
        if resolution is not None:
            try:
                scope = build_etf_industry_scope_evidence(
                    symbol=symbol,
                    index_resolution=resolution,
                    as_of=material_as_of,
                )
            except Exception:
                reasons.append("etf_industry_scope_evidence_unverified")
        if document is not None and resolution is not None:
            try:
                relation = verify_official_sse_fund_index_relation(
                    document=document,
                    official_master_target_index_name=(
                        product.target_index_name or ""
                    ),
                    provider_resolution=resolution,
                    as_of=material_as_of,
                )
            except Exception:
                reasons.append("etf_index_relation_evidence_unverified")
        if index_result is not None:
            methodology = _rebind_model(
                index_result.methodology,
                material_as_of,
            )
            constituents = _weighted_constituents(index_result)
            constituents = _rebind_model(constituents, material_as_of)
        if constituents is not None:
            try:
                exposure = calculate_index_industry_exposure(
                    constituents,
                    industry_classification,
                    as_of=material_as_of,
                    computed_at=material_as_of,
                )
            except Exception:
                reasons.append("etf_industry_exposure_evidence_unverified")

        tracking = None
        if nav_history is not None and index_history is not None:
            try:
                tracking = build_verified_etf_tracking_window(
                    symbol=symbol,
                    index_code=index_history.index_code,
                    expected_trade_dates=trade_dates,
                    nav_history=nav_history,
                    index_history=index_history,
                    computed_at=material_as_of,
                )
            except Exception:
                reasons.append("etf_tracking_evidence_unverified")
        daily_fact = None
        if share is not None:
            try:
                daily_fact = build_etf_daily_fact(
                    share,
                    computed_at=material_as_of,
                    turnover_window=turnover,
                    fund_size_snapshot=fund_size,
                    tracking_window=tracking,
                )
            except Exception:
                reasons.append("etf_daily_fact_evidence_unverified")
        if quote is not None:
            upstream_ready = all((
                lifecycle is not None,
                scope is not None,
                relation is not None and relation.formal_ready,
                methodology is not None and methodology.formal_ready,
                constituents is not None and constituents.formal_ready,
                exposure is not None and exposure.formal_ready,
            ))
            ranking = build_ranking_input_audit(
                quote,
                daily_fact,
                as_of=material_as_of,
                formal_gate_ready=upstream_ready,
            )
        collected.append(CollectedEtfFormalAdmissionMaterial(
            symbol=symbol,
            product=product,
            lifecycle_evidence=lifecycle,
            industry_scope_evidence=scope,
            index_relation_evidence=relation,
            methodology_evidence=methodology,
            constituent_evidence=constituents,
            industry_exposure_evidence=exposure,
            ranking_input_evidence=ranking,
            source_reasons=tuple(dict.fromkeys(reasons)),
        ))
    return EtfFormalAdmissionMaterialCollection(
        material_as_of=material_as_of,
        items=tuple(collected),
    )


def finalize_etf_formal_admission_materials(
    *,
    materials: EtfFormalAdmissionMaterialCollection,
    sample_id: str,
    radar_run_id: str,
    as_of: datetime,
) -> EtfFormalAdmissionBundle:
    """Bind pre-fetched current observations to a later forward sample."""
    as_of = _aware(as_of, "asOf")
    if not isinstance(materials, EtfFormalAdmissionMaterialCollection):
        raise ValueError("etf_formal_material_collection_unverified")
    if materials.material_as_of > as_of:
        raise ValueError("etf_formal_material_from_future")
    admissions = []
    for item in materials.items:
        lifecycle = (
            replace(item.lifecycle_evidence, as_of=as_of)
            if item.lifecycle_evidence is not None
            else None
        )
        scope = (
            replace(item.industry_scope_evidence, as_of=as_of)
            if item.industry_scope_evidence is not None
            else None
        )
        admission = provide_etf_formal_admission_evidence(
            product=item.product,
            as_of=as_of,
            lifecycle_evidence=lifecycle,
            industry_scope_evidence=scope,
            index_relation_evidence=_rebind_model(
                item.index_relation_evidence,
                as_of,
            ),
            methodology_evidence=_rebind_model(
                item.methodology_evidence,
                as_of,
            ),
            constituent_evidence=_rebind_model(
                item.constituent_evidence,
                as_of,
            ),
            industry_exposure_evidence=_rebind_model(
                item.industry_exposure_evidence,
                as_of,
            ),
            ranking_input_evidence=_rebind_model(
                item.ranking_input_evidence,
                as_of,
            ),
        )
        admissions.append(admission)
    return build_etf_formal_admission_bundle(
        sample_id=sample_id,
        radar_run_id=radar_run_id,
        as_of=as_of,
        admissions=tuple(admissions),
    )
