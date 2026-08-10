"""阶段6三级龙头研究来源的统一只读准入。

本模块只接纳现有严格来源适配器的冻结结果，并把可用输入一次性绑定到
同一候选计划。它不抓取、不落盘、不补造缺失值，也不打开任何正式状态门。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.contracts import QuoteSnapshot
from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureInput,
)
from radar.leader_business_catalyst_manual_review import (
    LEADER_BUSINESS_MANUAL_REVIEW_BATCH_CONTRACT_ID,
    LeaderOfficialBusinessManualReviewBatchItem,
    LeaderOfficialBusinessManualReviewBatchResult,
    LeaderOfficialBusinessManualReviewBatchStatus,
)
from radar.leader_history_features import LeaderHistoryFeatureInput
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchResult,
    LeaderResearchInputProviderBatchStatus,
    LeaderResearchInputProviderPlanBatchInput,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_research_readiness_runtime_batch import (
    is_leader_research_runtime_risk_batch_valid,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_verified_leader_research_provider_input,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.leader_tradability_features import LeaderTradabilityFeatureInput
from radar.sources.leader_history_public_poc import (
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    PublicHistoryPocResult,
    PublicHistoryResolutionStatus,
    run_public_history_input_poc,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositePocStatus,
    PublicCompositeTradabilityQuery,
    PublicCompositeTradabilityReport,
    PublicTradabilityObservation,
    PublicTradabilityRuntimeInputResult,
    build_public_tradability_runtime_inputs,
    quote_batch_content_sha256,
)


UTC = timezone.utc
SAFE_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_:.+-]{0,119}$")
LEADER_RESEARCH_SOURCE_ADMISSION_CONTRACT_ID = (
    "radar-leader-research-source-admission-v1"
)
LEADER_RESEARCH_SOURCE_ADMISSION_PROVIDER_CONTRACT_ID = (
    "radar-leader-research-source-admission-provider-v1"
)
SOURCE_ADMISSION_CONTRACT_UNVERIFIED = (
    "leader_research_source_admission_contract_unverified"
)
SOURCE_ADMISSION_HISTORY_UNVERIFIED = (
    "leader_research_source_admission_history_unverified"
)
SOURCE_ADMISSION_BUSINESS_UNVERIFIED = (
    "leader_research_source_admission_business_unverified"
)
SOURCE_ADMISSION_TRADABILITY_UNVERIFIED = (
    "leader_research_source_admission_tradability_unverified"
)
SOURCE_ADMISSION_RISK_UNVERIFIED = (
    "leader_research_source_admission_risk_unverified"
)
SOURCE_ADMISSION_PROVIDER_UNVERIFIED = (
    "leader_research_source_admission_provider_unverified"
)


class LeaderResearchSourceAdmissionStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"


class LeaderResearchSourceComponentStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    STALE = "stale"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderResearchHistoryAdmissionEntry:
    symbol: str
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    query: Any = field(repr=False)
    result: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchTradabilityAdmissionBundle:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    query: Any = field(repr=False)
    quotes: Any = field(repr=False)
    official_observations: Any = field(repr=False)
    aggregator_observations: Any = field(repr=False)
    report: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchRiskAdmissionBundle:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    batch: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderResearchSourceAdmissionInput:
    context: Any = field(repr=False)
    history_entries: Any = field(repr=False)
    business_review_batch: Any = field(repr=False)
    tradability_bundle: Any = field(repr=False)
    risk_projection_bundle: Any = field(repr=False)
    provider_contract_id: str = (
        LEADER_RESEARCH_SOURCE_ADMISSION_PROVIDER_CONTRACT_ID
    )


@dataclass(frozen=True)
class LeaderResearchSourceComponentAdmission:
    name: str
    status: LeaderResearchSourceComponentStatus
    candidate_count: int
    ready_count: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderResearchSourceAdmissionResult:
    status: LeaderResearchSourceAdmissionStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_plan_id: Optional[str]
    candidate_count: int
    components: Tuple[
        LeaderResearchSourceComponentAdmission,
        ...,
    ] = field(default_factory=tuple)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    provider_input: Optional[
        LeaderResearchInputProviderPlanBatchInput
    ] = field(default=None, repr=False)
    provider_result: Optional[
        LeaderResearchInputProviderBatchResult
    ] = field(default=None, repr=False)
    contract_id: str = LEADER_RESEARCH_SOURCE_ADMISSION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "components": [item.to_evidence() for item in self.components],
            "providerStatus": (
                self.provider_result.status.value
                if self.provider_result is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _reason_valid(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and SAFE_REASON_PATTERN.fullmatch(value) is not None
    )


def _reasons_valid(value: Any) -> bool:
    return bool(
        isinstance(value, tuple)
        and all(_reason_valid(reason) for reason in value)
    )


def _component(
    name: str,
    *,
    candidate_count: int,
    ready_count: int,
    reasons: Sequence[str] = (),
    blocked: bool = False,
    declared_status: Optional[
        LeaderResearchSourceComponentStatus
    ] = None,
) -> LeaderResearchSourceComponentAdmission:
    if blocked:
        status = LeaderResearchSourceComponentStatus.BLOCKED
    elif ready_count == candidate_count and candidate_count:
        status = LeaderResearchSourceComponentStatus.READY
    elif ready_count:
        status = LeaderResearchSourceComponentStatus.PARTIAL
    elif declared_status is not None:
        status = declared_status
    else:
        status = LeaderResearchSourceComponentStatus.MISSING
    return LeaderResearchSourceComponentAdmission(
        name=name,
        status=status,
        candidate_count=candidate_count,
        ready_count=ready_count,
        reasons=_dedupe(reasons),
    )


def _nonready_component_status(
    statuses: Sequence[ResearchFeatureStatus],
) -> LeaderResearchSourceComponentStatus:
    values = set(statuses)
    if ResearchFeatureStatus.SOURCE_FAILED in values:
        return LeaderResearchSourceComponentStatus.SOURCE_FAILED
    if ResearchFeatureStatus.SOURCE_UNVERIFIED in values:
        return LeaderResearchSourceComponentStatus.SOURCE_UNVERIFIED
    if ResearchFeatureStatus.STALE in values:
        return LeaderResearchSourceComponentStatus.STALE
    return LeaderResearchSourceComponentStatus.MISSING


def _result(
    *,
    status: LeaderResearchSourceAdmissionStatus,
    context: Optional[LeaderResearchRuntimeSourceContext],
    reasons: Sequence[str],
    components: Sequence[LeaderResearchSourceComponentAdmission] = (),
    provider_input: Optional[
        LeaderResearchInputProviderPlanBatchInput
    ] = None,
    provider_result: Optional[
        LeaderResearchInputProviderBatchResult
    ] = None,
) -> LeaderResearchSourceAdmissionResult:
    plan = context.candidate_plan if context is not None else None
    return LeaderResearchSourceAdmissionResult(
        status=status,
        radar_run_id=context.radar_run_id if context is not None else None,
        as_of=context.as_of if context is not None else None,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        candidate_count=len(plan.items) if plan is not None else 0,
        components=tuple(components),
        reasons=_dedupe(reasons),
        provider_input=provider_input,
        provider_result=provider_result,
    )


def _history_result_valid(
    result: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    if (
        type(result) is not PublicHistoryPocResult
        or result.real_poc_status != "not_run"
        or result.resolution_status not in {
            item.value for item in PublicHistoryResolutionStatus
        }
        or not _reasons_valid(result.reasons)
        or not isinstance(result.member_count, int)
        or isinstance(result.member_count, bool)
        or result.member_count < 0
        or not isinstance(result.expected_series_count, int)
        or isinstance(result.expected_series_count, bool)
        or result.expected_series_count < 0
        or not isinstance(result.complete_series_count, int)
        or isinstance(result.complete_series_count, bool)
        or result.complete_series_count < 0
        or result.complete_series_count > result.expected_series_count
        or not isinstance(result.excluded_out_of_scope_count, int)
        or isinstance(result.excluded_out_of_scope_count, bool)
        or result.excluded_out_of_scope_count < 0
        or (
            result.expected_series_count > 0
            and result.expected_series_count != result.member_count + 1
        )
        or not isinstance(result.series_coverage, (int, float))
        or isinstance(result.series_coverage, bool)
        or not math.isfinite(float(result.series_coverage))
        or not 0.0 <= float(result.series_coverage) <= 1.0
        or any((
            result.formal_score_ready is not False,
            result.formal_gate_ready is not False,
            result.formal_usable is not False,
            result.state_transition_allowed is not False,
        ))
    ):
        return False
    if result.resolution_status == PublicHistoryResolutionStatus.READY.value:
        history = result.history_input
        return bool(
            result.reasons == ("history_public_research_input_ready",)
            and result.member_count > 0
            and result.expected_series_count > 0
            and result.complete_series_count == result.expected_series_count
            and float(result.series_coverage) == 1.0
            and type(history) is LeaderHistoryFeatureInput
            and getattr(
                getattr(history, "candidate", None),
                "symbol",
                None,
            ) == symbol
            and _aware_utc(getattr(history, "as_of", None)) == as_of
        )
    expected_coverage = (
        result.complete_series_count / result.expected_series_count
        if result.expected_series_count
        else 0.0
    )
    return bool(
        result.history_input is None
        and result.reasons
        and math.isclose(
            float(result.series_coverage),
            expected_coverage,
            abs_tol=1e-9,
        )
        and (
            result.resolution_status
            == PublicHistoryResolutionStatus.FAILED.value
            or result.complete_series_count < result.expected_series_count
        )
    )


def _admit_history(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> Tuple[
    Optional[Mapping[str, LeaderHistoryFeatureInput]],
    LeaderResearchSourceComponentAdmission,
]:
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)
    candidate_count = len(candidate_symbols)
    if value is None:
        return {}, _component(
            "history",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=("leader_history_source_missing",),
        )
    if (
        not isinstance(value, tuple)
        or len(value) != candidate_count
        or tuple(getattr(item, "symbol", None) for item in value)
        != candidate_symbols
        or any(type(item) is not LeaderResearchHistoryAdmissionEntry for item in value)
    ):
        return None, _component(
            "history",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_HISTORY_UNVERIFIED,),
            blocked=True,
        )
    inputs = {}
    reasons = []
    resolution_statuses = []
    for plan_item, entry in zip(plan.items, value):
        query = entry.query
        membership = getattr(query, "membership", None)
        expected_members = (
            context.industry_constituent_symbols_by_code.get(
                plan_item.industry_code
            )
        )
        if (
            entry.candidate_plan_id != plan.candidate_set_id
            or entry.radar_run_id != plan.radar_run_id
            or entry.quote_batch_id != plan.quote_batch_id
            or type(query) is not PublicHistoryPocQuery
            or _aware_utc(getattr(query, "as_of", None)) != plan.as_of
            or query.candidate_symbol != plan_item.symbol
            or type(membership) is not PointInTimeIndustryMembership
            or membership.candidate_symbol != plan_item.symbol
            or membership.industry_code != plan_item.industry_code
            or membership.release_id != plan_item.industry_release_id
            or expected_members is None
            or membership.member_symbols != expected_members
        ):
            return None, _component(
                "history",
                candidate_count=candidate_count,
                ready_count=0,
                reasons=(SOURCE_ADMISSION_HISTORY_UNVERIFIED,),
                blocked=True,
            )
        replayed = run_public_history_input_poc(query)
        if replayed != entry.result:
            return None, _component(
                "history",
                candidate_count=candidate_count,
                ready_count=0,
                reasons=(SOURCE_ADMISSION_HISTORY_UNVERIFIED,),
                blocked=True,
            )
        if not _history_result_valid(
            entry.result,
            symbol=entry.symbol,
            as_of=plan.as_of,
        ):
            return None, _component(
                "history",
                candidate_count=candidate_count,
                ready_count=0,
                reasons=(SOURCE_ADMISSION_HISTORY_UNVERIFIED,),
                blocked=True,
            )
        if entry.result.history_input is not None:
            inputs[entry.symbol] = entry.result.history_input
        else:
            reasons.extend(entry.result.reasons)
            resolution_statuses.append(entry.result.resolution_status)
    declared_status = None
    if resolution_statuses:
        declared_status = (
            LeaderResearchSourceComponentStatus.SOURCE_FAILED
            if PublicHistoryResolutionStatus.FAILED.value
            in resolution_statuses
            else LeaderResearchSourceComponentStatus.PARTIAL
        )
    return inputs, _component(
        "history",
        candidate_count=candidate_count,
        ready_count=len(inputs),
        reasons=reasons,
        declared_status=declared_status,
    )


def _business_batch_valid(
    value: Any,
    *,
    candidate_plan_id: str,
    candidate_symbols: Tuple[str, ...],
) -> bool:
    if (
        type(value) is not LeaderOfficialBusinessManualReviewBatchResult
        or value.contract_id != LEADER_BUSINESS_MANUAL_REVIEW_BATCH_CONTRACT_ID
        or value.status == LeaderOfficialBusinessManualReviewBatchStatus.BLOCKED
        or value.candidate_plan_id != candidate_plan_id
        or value.candidate_count != len(candidate_symbols)
        or not isinstance(value.items, tuple)
        or len(value.items) != len(candidate_symbols)
        or tuple(getattr(item, "symbol", None) for item in value.items)
        != candidate_symbols
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    ready_count = 0
    for index, item in enumerate(value.items):
        if (
            type(item) is not LeaderOfficialBusinessManualReviewBatchItem
            or item.index != index
            or not isinstance(item.status, ResearchFeatureStatus)
            or not _reasons_valid(item.reasons)
            or (
                item.status == ResearchFeatureStatus.READY
                and (
                    type(item.input_value)
                    is not LeaderBusinessCatalystFeatureInput
                    or item.reasons
                    != ("business_manual_review_input_ready",)
                    or not isinstance(item.review_id, str)
                    or not item.review_id
                )
            )
            or (
                item.status != ResearchFeatureStatus.READY
                and item.input_value is not None
            )
        ):
            return False
        ready_count += item.status == ResearchFeatureStatus.READY
    if ready_count == len(candidate_symbols):
        expected_status = LeaderOfficialBusinessManualReviewBatchStatus.READY
    elif all(
        item.status == ResearchFeatureStatus.MISSING
        for item in value.items
    ):
        expected_status = LeaderOfficialBusinessManualReviewBatchStatus.MISSING
    else:
        expected_status = LeaderOfficialBusinessManualReviewBatchStatus.PARTIAL
    expected_reasons = (
        ()
        if expected_status == LeaderOfficialBusinessManualReviewBatchStatus.READY
        else (
            ("business_manual_review_batch_partial",)
            if expected_status
            == LeaderOfficialBusinessManualReviewBatchStatus.PARTIAL
            else ("business_manual_review_batch_missing",)
        )
    )
    return value.status == expected_status and value.reasons == expected_reasons


def _admit_business(
    value: Any,
    *,
    candidate_plan_id: str,
    candidate_symbols: Tuple[str, ...],
) -> Tuple[
    Optional[Mapping[str, LeaderBusinessCatalystFeatureInput]],
    LeaderResearchSourceComponentAdmission,
]:
    candidate_count = len(candidate_symbols)
    if value is None:
        return {}, _component(
            "business_catalyst",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=("leader_business_catalyst_source_missing",),
        )
    if not _business_batch_valid(
        value,
        candidate_plan_id=candidate_plan_id,
        candidate_symbols=candidate_symbols,
    ):
        return None, _component(
            "business_catalyst",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_BUSINESS_UNVERIFIED,),
            blocked=True,
        )
    inputs = value.inputs_by_symbol
    return inputs, _component(
        "business_catalyst",
        candidate_count=candidate_count,
        ready_count=len(inputs),
        reasons=value.reasons,
        declared_status=_nonready_component_status(
            tuple(item.status for item in value.items)
        ),
    )


def _tradability_result_valid(
    value: Any,
    *,
    candidate_symbols: Tuple[str, ...],
    as_of: datetime,
) -> bool:
    if (
        type(value) is not PublicTradabilityRuntimeInputResult
        or not isinstance(value.status, ResearchFeatureStatus)
        or not isinstance(value.inputs, tuple)
        or not _reasons_valid(value.reasons)
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    if value.status != ResearchFeatureStatus.READY:
        return bool(not value.inputs and value.reasons)
    return bool(
        not value.reasons
        and len(value.inputs) == len(candidate_symbols)
        and tuple(
            getattr(getattr(item, "quote", None), "symbol", None)
            for item in value.inputs
        ) == candidate_symbols
        and all(
            type(item) is LeaderTradabilityFeatureInput
            and _aware_utc(item.as_of) == as_of
            for item in value.inputs
        )
    )


def _tradability_bundle_bound(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> bool:
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)
    if (
        type(value) is not LeaderResearchTradabilityAdmissionBundle
        or value.candidate_plan_id != plan.candidate_set_id
        or value.radar_run_id != plan.radar_run_id
        or value.quote_batch_id != plan.quote_batch_id
        or type(value.query) is not PublicCompositeTradabilityQuery
        or _aware_utc(value.query.as_of) != plan.as_of
        or tuple(
            getattr(item, "symbol", None)
            for item in value.query.securities
        ) != candidate_symbols
        or value.query.quote_batch_evidence is None
        or value.query.quote_batch_evidence.batch_id != plan.quote_batch_id
        or not isinstance(value.quotes, tuple)
        or tuple(getattr(item, "symbol", None) for item in value.quotes)
        != candidate_symbols
        or any(type(item) is not QuoteSnapshot for item in value.quotes)
        or not isinstance(value.official_observations, tuple)
        or any(
            type(item) is not PublicTradabilityObservation
            for item in value.official_observations
        )
        or not isinstance(value.aggregator_observations, tuple)
        or any(
            type(item) is not PublicTradabilityObservation
            for item in value.aggregator_observations
        )
        or type(value.report) is not PublicCompositeTradabilityReport
        or not _reasons_valid(value.report.reasons)
    ):
        return False
    expected_quotes = tuple(
        context.quotes_by_symbol[symbol]
        for symbol in candidate_symbols
    )
    if value.quotes != expected_quotes:
        return False
    return value.query.quote_batch_evidence.content_sha256 == (
        quote_batch_content_sha256(
            batch_id=plan.quote_batch_id,
            quotes=value.quotes,
        )
    )


def _admit_tradability(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> Tuple[
    Optional[Mapping[str, LeaderTradabilityFeatureInput]],
    LeaderResearchSourceComponentAdmission,
]:
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)
    candidate_count = len(candidate_symbols)
    if value is None:
        return {}, _component(
            "tradability",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=("leader_tradability_source_missing",),
        )
    if not _tradability_bundle_bound(value, context=context):
        return None, _component(
            "tradability",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_TRADABILITY_UNVERIFIED,),
            blocked=True,
        )
    replayed = build_public_tradability_runtime_inputs(
        query=value.query,
        quotes=value.quotes,
        official_observations=value.official_observations,
        aggregator_observations=value.aggregator_observations,
        report=value.report,
    )
    if not _tradability_result_valid(
        replayed,
        candidate_symbols=candidate_symbols,
        as_of=plan.as_of,
    ):
        return None, _component(
            "tradability",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_TRADABILITY_UNVERIFIED,),
            blocked=True,
        )
    if replayed.status == ResearchFeatureStatus.SOURCE_UNVERIFIED:
        return None, _component(
            "tradability",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_TRADABILITY_UNVERIFIED,),
            blocked=True,
        )
    if replayed.status != ResearchFeatureStatus.READY:
        if (
            value.report.fixture_resolution_status
            == PublicCompositePocStatus.PARTIAL
        ):
            declared_status = (
                LeaderResearchSourceComponentStatus.PARTIAL
            )
        elif (
            value.report.fixture_resolution_status
            == PublicCompositePocStatus.NOT_RUN
        ):
            declared_status = (
                LeaderResearchSourceComponentStatus.SOURCE_UNVERIFIED
            )
        else:
            declared_status = _nonready_component_status(
                (replayed.status,)
            )
        return {}, _component(
            "tradability",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=replayed.reasons,
            declared_status=declared_status,
        )
    inputs = {}
    for plan_item, item in zip(plan.items, replayed.inputs):
        expected_quote = context.quotes_by_symbol[plan_item.symbol]
        if item.quote != expected_quote:
            return None, _component(
                "tradability",
                candidate_count=candidate_count,
                ready_count=0,
                reasons=(SOURCE_ADMISSION_TRADABILITY_UNVERIFIED,),
                blocked=True,
            )
        inputs[plan_item.symbol] = replace(
            item,
            quote=item.quote.model_copy(deep=True),
            quote_source_contract_id=plan_item.quote_source_contract_id,
        )
    return inputs, _component(
        "tradability",
        candidate_count=candidate_count,
        ready_count=len(inputs),
        reasons=replayed.reasons,
    )


def _admit_risk(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> Tuple[
    Optional[LeaderRiskCandidateProjectionBatchResult],
    LeaderResearchSourceComponentAdmission,
]:
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)
    candidate_count = len(candidate_symbols)
    if value is None:
        return None, _component(
            "risk",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=("leader_risk_projection_source_missing",),
        )
    if (
        type(value) is not LeaderResearchRiskAdmissionBundle
        or value.candidate_plan_id != plan.candidate_set_id
        or value.radar_run_id != plan.radar_run_id
        or value.quote_batch_id != plan.quote_batch_id
        or type(value.batch) is not LeaderRiskCandidateProjectionBatchResult
        or not _reasons_valid(value.batch.reasons)
        or any(not _reasons_valid(item.reasons) for item in value.batch.items)
        or not is_leader_research_runtime_risk_batch_valid(
            value.batch,
            as_of=plan.as_of,
            candidate_symbols=candidate_symbols,
        )
        or tuple(item.symbol for item in value.batch.items)
        != candidate_symbols
    ):
        return None, _component(
            "risk",
            candidate_count=candidate_count,
            ready_count=0,
            reasons=(SOURCE_ADMISSION_RISK_UNVERIFIED,),
            blocked=True,
        )
    batch = value.batch
    declared_status = {
        LeaderRiskCandidateProjectionBatchStatus.PARTIAL: (
            LeaderResearchSourceComponentStatus.PARTIAL
        ),
        LeaderRiskCandidateProjectionBatchStatus.MISSING: (
            LeaderResearchSourceComponentStatus.MISSING
        ),
        LeaderRiskCandidateProjectionBatchStatus.STALE: (
            LeaderResearchSourceComponentStatus.STALE
        ),
        LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED: (
            LeaderResearchSourceComponentStatus.SOURCE_FAILED
        ),
        LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED: (
            LeaderResearchSourceComponentStatus.SOURCE_UNVERIFIED
        ),
    }.get(batch.status)
    return batch, _component(
        "risk",
        candidate_count=candidate_count,
        ready_count=batch.ready_count,
        reasons=batch.reasons,
        declared_status=declared_status,
    )


def _admission_status(
    provider_status: LeaderResearchInputProviderBatchStatus,
) -> LeaderResearchSourceAdmissionStatus:
    return {
        LeaderResearchInputProviderBatchStatus.READY: (
            LeaderResearchSourceAdmissionStatus.READY
        ),
        LeaderResearchInputProviderBatchStatus.PARTIAL: (
            LeaderResearchSourceAdmissionStatus.PARTIAL
        ),
        LeaderResearchInputProviderBatchStatus.MISSING: (
            LeaderResearchSourceAdmissionStatus.MISSING
        ),
        LeaderResearchInputProviderBatchStatus.BLOCKED: (
            LeaderResearchSourceAdmissionStatus.BLOCKED
        ),
    }[provider_status]


def build_leader_research_source_admission(
    input_value: Any,
) -> LeaderResearchSourceAdmissionResult:
    """严格接纳四类来源并一次性生成现有F5提供器批次。"""

    if (
        type(input_value) is not LeaderResearchSourceAdmissionInput
        or not is_leader_research_runtime_source_context_valid(
            getattr(input_value, "context", None)
        )
        or input_value.provider_contract_id
        != LEADER_RESEARCH_SOURCE_ADMISSION_PROVIDER_CONTRACT_ID
    ):
        return _result(
            status=LeaderResearchSourceAdmissionStatus.BLOCKED,
            context=None,
            reasons=(SOURCE_ADMISSION_CONTRACT_UNVERIFIED,),
        )
    context = input_value.context
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)

    try:
        history, history_component = _admit_history(
            input_value.history_entries,
            context=context,
        )
        business, business_component = _admit_business(
            input_value.business_review_batch,
            candidate_plan_id=plan.candidate_set_id,
            candidate_symbols=candidate_symbols,
        )
        tradability, tradability_component = _admit_tradability(
            input_value.tradability_bundle,
            context=context,
        )
        risk, risk_component = _admit_risk(
            input_value.risk_projection_bundle,
            context=context,
        )
    except (AttributeError, TypeError, ValueError):
        return _result(
            status=LeaderResearchSourceAdmissionStatus.BLOCKED,
            context=context,
            reasons=(SOURCE_ADMISSION_CONTRACT_UNVERIFIED,),
        )
    components = (
        history_component,
        business_component,
        tradability_component,
        risk_component,
    )
    blocked = next((
        item for item in components
        if item.status == LeaderResearchSourceComponentStatus.BLOCKED
    ), None)
    if blocked is not None:
        return _result(
            status=LeaderResearchSourceAdmissionStatus.BLOCKED,
            context=context,
            reasons=blocked.reasons,
            components=components,
        )

    try:
        provider_input = build_verified_leader_research_provider_input(
            context,
            provider_contract_id=input_value.provider_contract_id,
            history_inputs_by_symbol=history,
            business_catalyst_inputs_by_symbol=business,
            tradability_inputs_by_symbol=tradability,
            risk_projection_batch=risk,
        )
        provider_result = (
            build_leader_research_input_provider_batch_from_plan(
                provider_input
            )
        )
    except (TypeError, ValueError):
        return _result(
            status=LeaderResearchSourceAdmissionStatus.BLOCKED,
            context=context,
            reasons=(SOURCE_ADMISSION_PROVIDER_UNVERIFIED,),
            components=components,
        )
    if provider_result.status == LeaderResearchInputProviderBatchStatus.BLOCKED:
        return _result(
            status=LeaderResearchSourceAdmissionStatus.BLOCKED,
            context=context,
            reasons=(
                SOURCE_ADMISSION_PROVIDER_UNVERIFIED,
                *provider_result.reasons,
            ),
            components=components,
        )
    return _result(
        status=_admission_status(provider_result.status),
        context=context,
        reasons=provider_result.reasons,
        components=components,
        provider_input=provider_input,
        provider_result=provider_result,
    )
