"""阶段6L-F6候选计划到F4的单次Assembly纯内存编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.contracts import QuoteSnapshot, RadarBatchMeta, SourceBatch
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchResult,
    LeaderResearchInputProviderBatchStatus,
    LeaderResearchInputProviderPlanBatchInput,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_input_gate import LeaderSourceKind
from radar.leader_research_readiness_runtime_batch import (
    LeaderResearchReadinessRuntimeBatchInput,
    LeaderResearchReadinessRuntimeBatchResult,
    LeaderResearchReadinessRuntimeBatchStatus,
    build_leader_research_readiness_runtime_batch,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.leader_runtime_inputs import (
    LeaderRuntimeAssembly,
    build_leader_runtime_evidence,
)


UTC = timezone.utc
LEADER_RESEARCH_SINGLE_PASS_CONTRACT_ID = (
    "radar-leader-research-single-pass-v1"
)
CONTRACT_UNVERIFIED = "leader_research_single_pass_contract_unverified"
PLAN_BLOCKED = "leader_research_single_pass_plan_blocked"
SOURCE_MISMATCH = "leader_research_single_pass_source_mismatch"
PROVIDER_BLOCKED = "leader_research_single_pass_provider_blocked"
PROVIDER_MISSING = "leader_research_single_pass_provider_missing"
ASSEMBLY_MISMATCH = "leader_research_single_pass_assembly_mismatch"


class LeaderResearchSinglePassStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderResearchSinglePassInput:
    candidate_plan: Any = field(repr=False)
    provider_input: Any = field(repr=False)
    source_context: Any = field(repr=False)
    as_of: Any
    quote_batch: Any = field(repr=False)
    quote_health: Any = field(repr=False)
    market_snapshot: Any = field(repr=False)
    sector_rows: Any = field(repr=False)
    industry_records: Any = field(repr=False)
    security_records: Any = field(repr=False)
    previous_states: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True)
class LeaderResearchSinglePassResult:
    status: LeaderResearchSinglePassStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_plan_id: Optional[str]
    candidate_count: int
    provider_result: Optional[
        LeaderResearchInputProviderBatchResult
    ] = field(default=None, repr=False)
    readiness_result: Optional[
        LeaderResearchReadinessRuntimeBatchResult
    ] = field(default=None, repr=False)
    runtime_assembly: Optional[LeaderRuntimeAssembly] = field(
        default=None,
        repr=False,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = LEADER_RESEARCH_SINGLE_PASS_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def gate_reasons(self) -> Tuple[str, ...]:
        return self.reasons

    def to_evidence(self):
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "providerStatus": (
                self.provider_result.status.value
                if self.provider_result is not None
                else None
            ),
            "readinessStatus": (
                self.readiness_result.status.value
                if self.readiness_result is not None
                else None
            ),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": self.state_transition_allowed,
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


def _result(
    *,
    status: LeaderResearchSinglePassStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    reasons: Sequence[str],
    provider_result: Optional[
        LeaderResearchInputProviderBatchResult
    ] = None,
    readiness_result: Optional[
        LeaderResearchReadinessRuntimeBatchResult
    ] = None,
    runtime_assembly: Optional[LeaderRuntimeAssembly] = None,
) -> LeaderResearchSinglePassResult:
    return LeaderResearchSinglePassResult(
        status=status,
        radar_run_id=plan.radar_run_id if plan is not None else None,
        as_of=plan.as_of if plan is not None else None,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        candidate_count=(plan.candidate_count if plan is not None else 0),
        provider_result=provider_result,
        readiness_result=readiness_result,
        runtime_assembly=runtime_assembly,
        reasons=_dedupe(reasons),
    )


def _source_matches_plan(
    input_value: LeaderResearchSinglePassInput,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    try:
        quote_batch = input_value.quote_batch
        meta = getattr(quote_batch, "meta", None)
        items = getattr(quote_batch, "items", None)
        context = input_value.source_context
        if not all((
            isinstance(quote_batch, SourceBatch),
            isinstance(meta, RadarBatchMeta),
            isinstance(items, list),
            isinstance(context, LeaderResearchRuntimeSourceContext),
            is_leader_research_runtime_source_context_valid(context),
            context.candidate_plan == plan,
            _aware_utc(input_value.as_of) == plan.as_of,
            meta.radar_run_id == plan.radar_run_id,
            meta.batch_id == plan.quote_batch_id,
            _aware_utc(meta.as_of) == plan.as_of,
        )):
            return False
        quotes_by_symbol = {}
        for quote in items:
            if (
                not isinstance(quote, QuoteSnapshot)
                or quote.symbol in quotes_by_symbol
            ):
                return False
            quotes_by_symbol[quote.symbol] = quote
        expected_quotes = context.quotes_by_symbol
        return all(
            quotes_by_symbol.get(item.symbol)
            == expected_quotes.get(item.symbol)
            for item in plan.items
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _assembly_matches_plan(
    assembly: LeaderRuntimeAssembly,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    if (
        assembly.status != "ready"
        or assembly.radar_run_id != plan.radar_run_id
        or assembly.quote_batch_id != plan.quote_batch_id
        or _aware_utc(assembly.as_of) != plan.as_of
        or len(assembly.evidence_items) != plan.candidate_count
        or len(assembly.research_component_items)
        != plan.candidate_count
    ):
        return False
    for plan_item, evidence, component in zip(
        plan.items,
        assembly.evidence_items,
        assembly.research_component_items,
    ):
        source_ids = {
            source.source_kind: source.source_contract_id
            for source in evidence.sources
        }
        if any((
            evidence.symbol != plan_item.symbol,
            evidence.industry_code != plan_item.industry_code,
            component.symbol != plan_item.symbol,
            component.industry_code != plan_item.industry_code,
            component.industry_release_id
            != plan_item.industry_release_id,
            component.quote_source_contract_id
            != plan_item.quote_source_contract_id,
            source_ids.get(LeaderSourceKind.MARKET)
            != plan.market_source_contract_id,
            source_ids.get(LeaderSourceKind.SECTOR)
            != plan_item.sector_source_contract_id,
            source_ids.get(LeaderSourceKind.QUOTE)
            != plan_item.quote_source_contract_id,
        )):
            return False
    return True


def _provider_quotes_match_source(
    provider_result: LeaderResearchInputProviderBatchResult,
    quote_batch: SourceBatch,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    quotes_by_symbol = {}
    for quote in quote_batch.items:
        symbol = getattr(quote, "symbol", None)
        if not isinstance(symbol, str) or symbol in quotes_by_symbol:
            return False
        quotes_by_symbol[symbol] = quote
    tradability = provider_result.tradability_inputs_by_symbol
    for plan_item in plan.items:
        input_item = tradability.get(plan_item.symbol)
        if input_item is None:
            continue
        if (
            input_item.quote_source_contract_id
            != plan_item.quote_source_contract_id
            or input_item.quote != quotes_by_symbol.get(plan_item.symbol)
        ):
            return False
    return True


def _status(
    value: LeaderResearchReadinessRuntimeBatchStatus,
) -> LeaderResearchSinglePassStatus:
    return {
        LeaderResearchReadinessRuntimeBatchStatus.READY: (
            LeaderResearchSinglePassStatus.READY
        ),
        LeaderResearchReadinessRuntimeBatchStatus.PARTIAL: (
            LeaderResearchSinglePassStatus.PARTIAL
        ),
        LeaderResearchReadinessRuntimeBatchStatus.BLOCKED: (
            LeaderResearchSinglePassStatus.BLOCKED
        ),
        LeaderResearchReadinessRuntimeBatchStatus.MISSING: (
            LeaderResearchSinglePassStatus.MISSING
        ),
    }[value]


def _readiness_reasons(
    readiness: LeaderResearchReadinessRuntimeBatchResult,
) -> Tuple[str, ...]:
    reasons = list(readiness.reasons)
    for item in readiness.audit_batch.items:
        if item.audit is not None:
            reasons.append(item.audit.first_research_blocker_reason)
    return _dedupe(reasons)


def _build_leader_research_single_pass(
    input_value: Any,
) -> LeaderResearchSinglePassResult:
    """消费两阶段输入，只构建一次Assembly并直接进入F4。"""

    if not isinstance(input_value, LeaderResearchSinglePassInput):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=None,
            reasons=(CONTRACT_UNVERIFIED,),
        )
    plan = input_value.candidate_plan
    if (
        not isinstance(plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(plan)
    ):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan if isinstance(plan, LeaderRuntimeCandidatePlan) else None,
            reasons=(PLAN_BLOCKED,),
        )
    if not _source_matches_plan(input_value, plan):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan,
            reasons=(SOURCE_MISMATCH,),
        )
    provider_input = input_value.provider_input
    if (
        not isinstance(
            provider_input,
            LeaderResearchInputProviderPlanBatchInput,
        )
        or provider_input.candidate_plan != plan
    ):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan,
            reasons=(PROVIDER_BLOCKED,),
        )
    provider_result = (
        build_leader_research_input_provider_batch_from_plan(
            provider_input
        )
    )
    if provider_result.status == LeaderResearchInputProviderBatchStatus.BLOCKED:
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan,
            provider_result=provider_result,
            reasons=(PROVIDER_BLOCKED,),
        )
    if provider_result.status == LeaderResearchInputProviderBatchStatus.MISSING:
        return _result(
            status=LeaderResearchSinglePassStatus.MISSING,
            plan=plan,
            provider_result=provider_result,
            reasons=(PROVIDER_MISSING, *provider_result.reasons),
        )
    if not _provider_quotes_match_source(
        provider_result,
        input_value.quote_batch,
        plan,
    ):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan,
            provider_result=provider_result,
            reasons=(SOURCE_MISMATCH,),
        )

    assembly = build_leader_runtime_evidence(
        as_of=input_value.as_of,
        quote_batch=input_value.quote_batch,
        quote_health=input_value.quote_health,
        market_snapshot=input_value.market_snapshot,
        sector_rows=input_value.sector_rows,
        industry_records=input_value.industry_records,
        security_records=input_value.security_records,
        previous_states=input_value.previous_states,
        history_inputs_by_symbol=(
            provider_result.history_inputs_by_symbol
        ),
        business_catalyst_inputs_by_symbol=(
            provider_result.business_catalyst_inputs_by_symbol
        ),
        tradability_inputs_by_symbol=(
            provider_result.tradability_inputs_by_symbol
        ),
        risk_candidate_projections_by_symbol=(
            provider_result.risk_projection_batch.projections_by_symbol
        ),
    )
    if not _assembly_matches_plan(assembly, plan):
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=plan,
            provider_result=provider_result,
            runtime_assembly=assembly,
            reasons=(ASSEMBLY_MISMATCH,),
        )
    readiness = build_leader_research_readiness_runtime_batch(
        LeaderResearchReadinessRuntimeBatchInput(
            assembly=assembly,
            risk_projection_batch=(
                provider_result.risk_projection_batch
            ),
        )
    )
    return _result(
        status=_status(readiness.status),
        plan=plan,
        provider_result=provider_result,
        readiness_result=readiness,
        runtime_assembly=readiness.runtime_assembly,
        reasons=_readiness_reasons(readiness),
    )


def build_leader_research_single_pass(
    input_value: Any,
) -> LeaderResearchSinglePassResult:
    """消费两阶段输入；畸形嵌套合同统一封闭为阻断。"""

    try:
        return _build_leader_research_single_pass(input_value)
    except (AttributeError, KeyError, TypeError, ValueError):
        plan = getattr(input_value, "candidate_plan", None)
        try:
            verified_plan = (
                plan
                if isinstance(plan, LeaderRuntimeCandidatePlan)
                and is_leader_runtime_candidate_plan_valid(plan)
                else None
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            verified_plan = None
        return _result(
            status=LeaderResearchSinglePassStatus.BLOCKED,
            plan=verified_plan,
            reasons=(CONTRACT_UNVERIFIED,),
        )
