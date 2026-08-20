"""阶段6候选全集可交易性的真实来源只读验收编排。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.contracts import SourceHealthResult, SourceStatus
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateCollectionRequest,
    LeaderLiveCandidateCollectionResult,
    LeaderLiveCandidateCollectionSources,
    LeaderLiveCandidateCollectionStatus,
    LeaderLiveCandidateRuntimeInputs,
    collect_leader_live_candidate_batch,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_research_source_admission import (
    LeaderResearchTradabilityAdmissionBundle,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    LeaderRuntimeCandidatePlanInput,
    LeaderRuntimeCandidatePlanStatus,
    build_leader_runtime_candidate_plan,
)
from radar.leader_tradability_production_collector import (
    LeaderTradabilityProductionFrozenBatch,
    collect_leader_tradability_production_source,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.leader_tradability_exchange_official import (
    ExchangeOfficialObservationBatch,
    collect_exchange_official_observations,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
    PublicLivePocSourceError,
    SHANGHAI_TZ,
    _fetch_calendar_document,
    _fetch_sina_lifecycle_frame,
    build_public_aggregator_observations,
    build_public_calendar_evidence,
    build_public_quote_evidence,
    build_public_security_contexts,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositePocStatus,
    PublicCompositeTradabilityQuery,
    PublicCompositeTradabilityReport,
    run_public_composite_tradability_poc,
)


LEADER_TRADABILITY_LIVE_ACCEPTANCE_CONTRACT_ID = (
    "radar-leader-tradability-live-acceptance-v1"
)


class LeaderTradabilityLiveAcceptanceStatus(str, Enum):
    COMPLETED = "completed"
    NOT_READY = "not_ready"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


OfficialLoader = Callable[[Tuple[Any, ...], date], Any]
LifecycleLoader = Callable[[Tuple[Any, ...]], Any]
CalendarLoader = Callable[[datetime], Any]


@dataclass(frozen=True)
class LeaderTradabilityLiveAcceptanceSources:
    official_loader: OfficialLoader = field(repr=False)
    lifecycle_loader: LifecycleLoader = field(repr=False)
    calendar_loader: CalendarLoader = field(repr=False)

    def __post_init__(self) -> None:
        if not all(callable(value) for value in (
            self.official_loader,
            self.lifecycle_loader,
            self.calendar_loader,
        )):
            raise ValueError(
                "leader_tradability_live_acceptance_sources_unverified"
            )


@dataclass(frozen=True, repr=False)
class LeaderTradabilityLiveAcceptanceResult:
    status: LeaderTradabilityLiveAcceptanceStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_plan_id: Optional[str]
    candidate_count: int
    reasons: Tuple[str, ...] = ()
    source_statuses: Mapping[str, str] = field(default_factory=dict)
    candidate_collection: Optional[
        LeaderLiveCandidateCollectionResult
    ] = field(default=None, repr=False)
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = field(
        default=None,
        repr=False,
    )
    report: Optional[PublicCompositeTradabilityReport] = field(
        default=None,
        repr=False,
    )
    frozen_batch: Optional[LeaderTradabilityProductionFrozenBatch] = field(
        default=None,
        repr=False,
    )
    collected_source: Optional[
        LeaderFormalResearchProductionCollectedSource
    ] = field(default=None, repr=False)
    contract_id: str = LEADER_TRADABILITY_LIVE_ACCEPTANCE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def __repr__(self) -> str:
        return (
            "LeaderTradabilityLiveAcceptanceResult("
            f"status={self.status.value!r}, "
            f"candidate_count={self.candidate_count!r})"
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "sourceStatuses": dict(self.source_statuses),
            "reportStatus": (
                self.report.fixture_resolution_status.value
                if self.report is not None
                else None
            ),
            "returnedCount": (
                self.report.returned_count
                if self.report is not None
                else 0
            ),
            "fieldCoverage": (
                dict(self.report.field_coverage)
                if self.report is not None
                else {}
            ),
            "productionSourceStatus": (
                self.collected_source.status.value
                if self.collected_source is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: LeaderTradabilityLiveAcceptanceStatus,
    *,
    candidate_collection: Optional[
        LeaderLiveCandidateCollectionResult
    ] = None,
    as_of: Optional[datetime] = None,
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = None,
    reasons: Sequence[str] = (),
    source_statuses: Optional[Mapping[str, str]] = None,
    report: Optional[PublicCompositeTradabilityReport] = None,
    frozen_batch: Optional[LeaderTradabilityProductionFrozenBatch] = None,
    collected_source: Optional[
        LeaderFormalResearchProductionCollectedSource
    ] = None,
) -> LeaderTradabilityLiveAcceptanceResult:
    active_plan = candidate_plan or (
        candidate_collection.candidate_plan
        if candidate_collection is not None
        else None
    )
    return LeaderTradabilityLiveAcceptanceResult(
        status=status,
        radar_run_id=(
            candidate_collection.radar_run_id
            if candidate_collection is not None
            else None
        ),
        as_of=as_of or (
            candidate_collection.as_of
            if candidate_collection is not None
            else None
        ),
        candidate_plan_id=(
            active_plan.candidate_set_id if active_plan is not None else None
        ),
        candidate_count=(
            active_plan.candidate_count if active_plan is not None else 0
        ),
        reasons=_dedupe(reasons),
        source_statuses=MappingProxyType(dict(source_statuses or {})),
        candidate_collection=candidate_collection,
        candidate_plan=active_plan,
        report=report,
        frozen_batch=frozen_batch,
        collected_source=collected_source,
    )


def build_default_leader_tradability_live_acceptance_sources(
) -> LeaderTradabilityLiveAcceptanceSources:
    return LeaderTradabilityLiveAcceptanceSources(
        official_loader=lambda contexts, trading_date: (
            collect_exchange_official_observations(
                contexts=contexts,
                trading_date=trading_date,
            )
        ),
        lifecycle_loader=lambda contexts: _fetch_sina_lifecycle_frame(
            contexts=contexts
        ),
        calendar_loader=lambda as_of: _fetch_calendar_document(as_of=as_of),
    )


def _quote_health(quote_batch: Any, as_of: datetime) -> SourceHealthResult:
    return evaluate_source_health(
        quote_batch.meta,
        SourceHealthPolicy(
            minimum_row_coverage=0.995,
            minimum_required_field_coverage=0.99,
            maximum_age_seconds=90,
            maximum_future_skew_seconds=5,
            required_fields=("price", "source_time"),
        ),
        now=as_of,
    )


def _plan_scope(plan: LeaderRuntimeCandidatePlan) -> Tuple[Tuple[Any, ...], ...]:
    return tuple(
        (
            item.symbol,
            item.industry_code,
            item.industry_name,
            item.industry_release_id,
            item.within_industry_rank,
            item.quote_source_contract_id,
            item.sector_source_contract_id,
        )
        for item in plan.items
    )


def _refreeze_runtime_inputs(
    runtime: LeaderLiveCandidateRuntimeInputs,
    *,
    as_of: datetime,
) -> Tuple[Optional[LeaderLiveCandidateRuntimeInputs], Tuple[str, ...]]:
    quote_batch = runtime.quote_batch.model_copy(
        update={
            "meta": runtime.quote_batch.meta.model_copy(
                update={"as_of": as_of}
            )
        },
        deep=True,
    )
    quote_health = _quote_health(quote_batch, as_of)
    if (
        quote_health.status != SourceStatus.HEALTHY
        or not quote_health.allows_new_state
    ):
        return None, (
            "quote_source_not_healthy_after_evidence",
            *quote_health.reasons,
        )
    candidate_plan = build_leader_runtime_candidate_plan(
        LeaderRuntimeCandidatePlanInput(
            as_of=as_of,
            quote_batch=quote_batch,
            quote_health=quote_health,
            market_snapshot=runtime.market_snapshot,
            sector_rows=runtime.sector_rows,
            industry_records=runtime.industry_records,
            security_records=runtime.security_records,
        )
    )
    if candidate_plan.status != LeaderRuntimeCandidatePlanStatus.READY:
        return None, (
            "candidate_plan_not_ready_after_evidence",
            *candidate_plan.gate_reasons,
        )
    if _plan_scope(candidate_plan) != _plan_scope(runtime.candidate_plan):
        return None, ("candidate_scope_changed_after_refreeze",)
    try:
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=candidate_plan,
            quote_batch=quote_batch,
            quote_health=quote_health,
            security_records=runtime.security_records,
            industry_records=runtime.industry_records,
        )
    except Exception:
        return None, ("runtime_source_context_refreeze_rejected",)
    return LeaderLiveCandidateRuntimeInputs(
        candidate_plan=candidate_plan,
        source_context=source_context,
        as_of=as_of,
        security_master_batch=runtime.security_master_batch.model_copy(deep=True),
        quote_batch=quote_batch,
        quote_health=quote_health.model_copy(deep=True),
        market_snapshot=MappingProxyType(copy.deepcopy(
            dict(runtime.market_snapshot)
        )),
        sector_rows=tuple(
            MappingProxyType(copy.deepcopy(dict(row)))
            for row in runtime.sector_rows
        ),
        industry_records=tuple(
            record.model_copy(deep=True)
            for record in runtime.industry_records
        ),
        industry_release=runtime.industry_release.model_copy(deep=True),
        security_records=tuple(
            record.model_copy(deep=True)
            for record in runtime.security_records
        ),
    ), ()


def _frame_fetched_at(
    frame: Any,
    symbols: Tuple[str, ...],
) -> Optional[datetime]:
    if frame is None or not callable(getattr(frame, "iterrows", None)):
        return None
    row_symbols = []
    fetched_values = []
    try:
        for _, row in frame.iterrows():
            row_symbols.append(str(row.get("代码") or "").strip().zfill(6))
            fetched_at = _aware(row.get("抓取时间"))
            if fetched_at is None:
                return None
            fetched_values.append(fetched_at)
    except Exception:
        return None
    if tuple(row_symbols) != symbols or not fetched_values:
        return None
    return max(fetched_values)


def finalize_leader_tradability_live_acceptance(
    candidate_collection: Any,
    evidence_sources: Any,
) -> LeaderTradabilityLiveAcceptanceResult:
    statuses = {"candidateCollection": "source_unverified"}
    if (
        not isinstance(candidate_collection, LeaderLiveCandidateCollectionResult)
        or not isinstance(
            evidence_sources,
            LeaderTradabilityLiveAcceptanceSources,
        )
    ):
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            reasons=("leader_tradability_live_acceptance_contract_unverified",),
            source_statuses=statuses,
        )
    statuses["candidateCollection"] = candidate_collection.status.value
    if candidate_collection.status != LeaderLiveCandidateCollectionStatus.READY:
        status = (
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED
            if candidate_collection.status
            == LeaderLiveCandidateCollectionStatus.SOURCE_FAILED
            else LeaderTradabilityLiveAcceptanceStatus.NOT_READY
        )
        return _result(
            status,
            candidate_collection=candidate_collection,
            reasons=(
                "candidate_collection_not_ready",
                *candidate_collection.reasons,
            ),
            source_statuses=statuses,
        )
    runtime = candidate_collection.runtime_inputs
    if runtime is None or candidate_collection.as_of is None:
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("candidate_runtime_inputs_unverified",),
            source_statuses=statuses,
        )
    symbols = tuple(item.symbol for item in runtime.candidate_plan.items)
    trading_date = runtime.as_of.astimezone(SHANGHAI_TZ).date()
    try:
        contexts = build_public_security_contexts(
            batch=runtime.security_master_batch,
            symbols=symbols,
        )
    except PublicLivePocSourceError:
        statuses["securityIdentity"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("security_identity_scope_unverified",),
            source_statuses=statuses,
        )
    statuses["securityIdentity"] = "completed"

    try:
        official_batch = evidence_sources.official_loader(
            contexts,
            trading_date,
        )
    except Exception:
        statuses["officialTradability"] = "source_failed"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
            candidate_collection=candidate_collection,
            reasons=("exchange_official_source_failed",),
            source_statuses=statuses,
        )
    if not isinstance(official_batch, ExchangeOfficialObservationBatch):
        statuses["officialTradability"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("exchange_official_batch_unverified",),
            source_statuses=statuses,
        )
    statuses["officialTradability"] = official_batch.status
    if official_batch.status != "completed":
        status = (
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED
            if official_batch.status == "source_failed"
            else LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED
        )
        return _result(
            status,
            candidate_collection=candidate_collection,
            reasons=official_batch.reasons or (
                "exchange_official_batch_unverified",
            ),
            source_statuses=statuses,
        )
    official_symbols = tuple(
        item.symbol for item in official_batch.observations
    )
    official_fetched_at = _aware(official_batch.fetched_at)
    if official_symbols != symbols or official_fetched_at is None:
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("exchange_official_scope_unverified",),
            source_statuses=statuses,
        )

    try:
        lifecycle_frame = evidence_sources.lifecycle_loader(contexts)
    except Exception:
        statuses["sinaLifecycle"] = "source_failed"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
            candidate_collection=candidate_collection,
            reasons=("sina_lifecycle_source_failed",),
            source_statuses=statuses,
        )
    lifecycle_fetched_at = _frame_fetched_at(lifecycle_frame, symbols)
    if lifecycle_fetched_at is None:
        statuses["sinaLifecycle"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("sina_lifecycle_scope_unverified",),
            source_statuses=statuses,
        )
    aggregator_observations = build_public_aggregator_observations(
        contexts=contexts,
        trading_date=trading_date,
        fetched_at=lifecycle_fetched_at,
        st_frame=None,
        suspension_frame=None,
        lifecycle_frame=lifecycle_frame,
    )
    if tuple(item.symbol for item in aggregator_observations) != symbols:
        statuses["sinaLifecycle"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("sina_lifecycle_observations_unverified",),
            source_statuses=statuses,
        )
    statuses["sinaLifecycle"] = "completed"

    try:
        calendar_document = evidence_sources.calendar_loader(runtime.as_of)
    except Exception:
        statuses["tradingCalendar"] = "source_failed"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
            candidate_collection=candidate_collection,
            reasons=("trading_calendar_source_failed",),
            source_statuses=statuses,
        )
    if not isinstance(calendar_document, PublicCalendarDocument):
        statuses["tradingCalendar"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("trading_calendar_document_unverified",),
            source_statuses=statuses,
        )
    try:
        calendars = build_public_calendar_evidence(
            document=calendar_document,
            trading_date=trading_date,
            exchanges=tuple(item.exchange for item in contexts),
        )
    except PublicLivePocSourceError:
        statuses["tradingCalendar"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("trading_calendar_evidence_unverified",),
            source_statuses=statuses,
        )
    calendar_fetched_at = _aware(calendar_document.fetched_at)
    if calendar_fetched_at is None:
        statuses["tradingCalendar"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            reasons=("trading_calendar_time_unverified",),
            source_statuses=statuses,
        )
    statuses["tradingCalendar"] = "completed"

    final_as_of = max(
        runtime.as_of,
        official_fetched_at,
        lifecycle_fetched_at,
        calendar_fetched_at,
    )
    refrozen, refreeze_reasons = _refreeze_runtime_inputs(
        runtime,
        as_of=final_as_of,
    )
    if refrozen is None:
        statuses["candidateRefreeze"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            as_of=final_as_of,
            reasons=refreeze_reasons,
            source_statuses=statuses,
        )
    statuses["candidateRefreeze"] = "completed"
    try:
        quotes, quote_evidence = build_public_quote_evidence(
            refrozen.quote_batch,
            symbols=symbols,
        )
    except PublicLivePocSourceError:
        statuses["publicComposite"] = "source_unverified"
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            as_of=final_as_of,
            candidate_plan=refrozen.candidate_plan,
            reasons=("quote_evidence_scope_unverified",),
            source_statuses=statuses,
        )
    query = PublicCompositeTradabilityQuery(
        trading_date=trading_date,
        as_of=final_as_of,
        securities=contexts,
        trading_calendars=calendars,
        quote_batch_evidence=quote_evidence,
    )
    report = run_public_composite_tradability_poc(
        query=query,
        quotes=quotes,
        official_observations=official_batch.observations,
        aggregator_observations=aggregator_observations,
        executed=True,
    )
    if (
        report.fixture_resolution_status
        != PublicCompositePocStatus.FIELD_CANDIDATE
        or report.expected_count != len(symbols)
        or report.returned_count != len(symbols)
        or any(value != 1.0 for value in report.field_coverage.values())
    ):
        statuses["publicComposite"] = report.fixture_resolution_status.value
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            as_of=final_as_of,
            candidate_plan=refrozen.candidate_plan,
            reasons=("tradability_composite_report_unverified", *report.reasons),
            source_statuses=statuses,
            report=report,
        )
    statuses["publicComposite"] = "completed"
    bundle = LeaderResearchTradabilityAdmissionBundle(
        candidate_plan_id=refrozen.candidate_plan.candidate_set_id,
        radar_run_id=refrozen.candidate_plan.radar_run_id,
        quote_batch_id=refrozen.candidate_plan.quote_batch_id,
        query=query,
        quotes=quotes,
        official_observations=official_batch.observations,
        aggregator_observations=aggregator_observations,
        report=report,
    )
    embedded_fetched_at = max(
        *(item.identity_fetched_at for item in contexts),
        *(item.fetched_at for item in calendars),
        *(item.fetched_at for item in quotes),
        *(item.fetched_at for item in official_batch.observations),
        *(item.fetched_at for item in aggregator_observations),
    )
    frozen = LeaderTradabilityProductionFrozenBatch(
        source_bundle=bundle,
        fetched_at=embedded_fetched_at,
        source_status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
    )
    collected = collect_leader_tradability_production_source(
        refrozen.source_context,
        frozen,
    )
    if (
        collected.status
        != LeaderFormalResearchProductionSourceStatus.COMPLETED
        or collected.symbols != symbols
    ):
        statuses["productionCollector"] = collected.status.value
        return _result(
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            candidate_collection=candidate_collection,
            as_of=final_as_of,
            candidate_plan=refrozen.candidate_plan,
            reasons=(
                "tradability_production_source_unverified",
                *getattr(collected, "reasons", ()),
            ),
            source_statuses=statuses,
            report=report,
        )
    statuses["productionCollector"] = "completed"
    return _result(
        LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        candidate_collection=candidate_collection,
        as_of=final_as_of,
        candidate_plan=refrozen.candidate_plan,
        source_statuses=statuses,
        report=report,
        frozen_batch=frozen,
        collected_source=collected,
    )


def run_leader_tradability_live_acceptance(
    request: LeaderLiveCandidateCollectionRequest,
    candidate_sources: LeaderLiveCandidateCollectionSources,
    *,
    clock: Callable[[], datetime],
    evidence_sources: Optional[
        LeaderTradabilityLiveAcceptanceSources
    ] = None,
) -> LeaderTradabilityLiveAcceptanceResult:
    candidate_collection = collect_leader_live_candidate_batch(
        request,
        candidate_sources,
        clock=clock,
    )
    return finalize_leader_tradability_live_acceptance(
        candidate_collection,
        evidence_sources
        or build_default_leader_tradability_live_acceptance_sources(),
    )
