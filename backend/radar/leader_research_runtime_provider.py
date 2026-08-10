"""阶段6本地运行时的显式缺失研究输入提供器。

当前没有获准的实时历史、主营催化、可交易性和正式风险提供器。本模块只
为完整候选集合返回可审计的缺失状态，不抓取、不缓存，也不补造数据。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

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
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchEntry,
    LeaderResearchInputProviderBatchStatus,
    LeaderResearchInputProviderPlanBatchInput,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


UTC = timezone.utc
LEADER_RESEARCH_EXPLICIT_MISSING_PROVIDER_CONTRACT_ID = (
    "radar-leader-research-explicit-missing-provider-v1"
)
LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID = (
    "radar-leader-research-runtime-source-context-v1"
)
RUNTIME_CONTEXT_UNVERIFIED = (
    "leader_research_runtime_context_unverified"
)
RUNTIME_PROVIDER_INPUT_UNVERIFIED = (
    "leader_research_runtime_provider_input_unverified"
)


@dataclass(frozen=True)
class LeaderResearchRuntimeSourceContext:
    candidate_plan: LeaderRuntimeCandidatePlan = field(repr=False)
    radar_run_id: str
    as_of: datetime
    quote_batch_id: str
    quote_health_status: SourceStatus
    quote_health_reasons: Tuple[str, ...]
    _candidate_quotes: Tuple[QuoteSnapshot, ...] = field(repr=False)
    _candidate_security_records: Tuple[
        SecurityMasterRecord,
        ...,
    ] = field(repr=False)
    _industry_constituents: Tuple[
        Tuple[str, Tuple[str, ...]],
        ...,
    ] = field(repr=False)
    context_id: str
    contract_id: str = (
        LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def quotes_by_symbol(self) -> Mapping[str, QuoteSnapshot]:
        return MappingProxyType({
            item.symbol: item.model_copy(deep=True)
            for item in self._candidate_quotes
        })

    @property
    def security_records_by_symbol(
        self,
    ) -> Mapping[str, SecurityMasterRecord]:
        return MappingProxyType({
            item.symbol: item.model_copy(deep=True)
            for item in self._candidate_security_records
        })

    @property
    def industry_constituent_symbols_by_code(
        self,
    ) -> Mapping[str, Tuple[str, ...]]:
        return MappingProxyType(dict(self._industry_constituents))

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "contextId": self.context_id,
            "candidatePlanId": self.candidate_plan.candidate_set_id,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat(),
            "quoteBatchId": self.quote_batch_id,
            "quoteHealthStatus": self.quote_health_status.value,
            "quoteHealthReasons": list(self.quote_health_reasons),
            "candidateCount": len(self._candidate_quotes),
            "industryCount": len(self._industry_constituents),
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


def _context_id(
    *,
    plan: LeaderRuntimeCandidatePlan,
    quote_health_status: SourceStatus,
    quote_health_reasons: Sequence[str],
    quotes: Sequence[QuoteSnapshot],
    security_records: Sequence[SecurityMasterRecord],
    industry_constituents: Sequence[Tuple[str, Tuple[str, ...]]],
) -> str:
    payload = {
        "contractId": LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID,
        "candidatePlanId": plan.candidate_set_id,
        "radarRunId": plan.radar_run_id,
        "asOf": plan.as_of.isoformat(),
        "quoteBatchId": plan.quote_batch_id,
        "quoteHealthStatus": quote_health_status.value,
        "quoteHealthReasons": list(quote_health_reasons),
        "quotes": [item.model_dump(mode="json") for item in quotes],
        "securityRecords": [
            item.model_dump(mode="json") for item in security_records
        ],
        "industryConstituents": [
            [industry_code, list(symbols)]
            for industry_code, symbols in industry_constituents
        ],
    }
    digest = hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"{LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID}:{digest}"


def _unique_by_symbol(values: Sequence[Any]) -> Optional[dict]:
    result = {}
    for value in values:
        symbol = getattr(value, "symbol", None)
        if not isinstance(symbol, str) or not symbol or symbol in result:
            return None
        result[symbol] = value
    return result


def build_leader_research_runtime_source_context(
    *,
    candidate_plan: Any,
    quote_batch: Any,
    quote_health: Any,
    security_records: Any,
    industry_records: Any,
) -> LeaderResearchRuntimeSourceContext:
    """冻结本轮来源身份和候选原始输入，不抓取或计算研究特征。"""

    if (
        not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or not isinstance(quote_batch, SourceBatch)
        or not isinstance(quote_health, SourceHealthResult)
        or quote_health.status != SourceStatus.HEALTHY
        or quote_health.allows_new_state is not True
        or not isinstance(security_records, tuple)
        or not isinstance(industry_records, tuple)
    ):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)
    plan = candidate_plan
    if (
        quote_batch.meta.radar_run_id != plan.radar_run_id
        or quote_batch.meta.batch_id != plan.quote_batch_id
        or _aware_utc(quote_batch.meta.as_of) != plan.as_of
    ):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)

    quote_by_symbol = _unique_by_symbol(quote_batch.items)
    security_by_symbol = _unique_by_symbol(security_records)
    if quote_by_symbol is None or security_by_symbol is None:
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)
    candidate_symbols = tuple(item.symbol for item in plan.items)
    if any(
        symbol not in quote_by_symbol or symbol not in security_by_symbol
        for symbol in candidate_symbols
    ):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)

    accepted_industry = {}
    for record in industry_records:
        if not isinstance(record, IndustryClassificationRecord):
            raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)
        if (
            record.identity_status != IndustryIdentityStatus.EXACT
            or record.record_status != IndustryRecordStatus.ACCEPTED
            or record.security_identity is None
        ):
            continue
        symbol = record.security_identity
        if symbol in accepted_industry:
            raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)
        accepted_industry[symbol] = record.division_code
    if any(
        accepted_industry.get(item.symbol) != item.industry_code
        for item in plan.items
    ):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)

    candidate_industries = tuple(dict.fromkeys(
        item.industry_code for item in plan.items
    ))
    industry_constituents = tuple(
        (
            industry_code,
            tuple(sorted(
                symbol
                for symbol, mapped_industry in accepted_industry.items()
                if mapped_industry == industry_code
            )),
        )
        for industry_code in candidate_industries
    )
    if any(not symbols for _, symbols in industry_constituents):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)

    quotes = tuple(
        quote_by_symbol[symbol].model_copy(deep=True)
        for symbol in candidate_symbols
    )
    candidate_security_records = tuple(
        security_by_symbol[symbol].model_copy(deep=True)
        for symbol in candidate_symbols
    )
    health_reasons = tuple(quote_health.reasons)
    context_id = _context_id(
        plan=plan,
        quote_health_status=quote_health.status,
        quote_health_reasons=health_reasons,
        quotes=quotes,
        security_records=candidate_security_records,
        industry_constituents=industry_constituents,
    )
    return LeaderResearchRuntimeSourceContext(
        candidate_plan=plan,
        radar_run_id=plan.radar_run_id,
        as_of=plan.as_of,
        quote_batch_id=plan.quote_batch_id,
        quote_health_status=quote_health.status,
        quote_health_reasons=health_reasons,
        _candidate_quotes=quotes,
        _candidate_security_records=candidate_security_records,
        _industry_constituents=industry_constituents,
        context_id=context_id,
    )


def is_leader_research_runtime_source_context_valid(value: Any) -> bool:
    if (
        not isinstance(value, LeaderResearchRuntimeSourceContext)
        or value.contract_id
        != LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID
        or not is_leader_runtime_candidate_plan_valid(value.candidate_plan)
        or value.radar_run_id != value.candidate_plan.radar_run_id
        or _aware_utc(value.as_of) != value.candidate_plan.as_of
        or value.quote_batch_id != value.candidate_plan.quote_batch_id
        or value.quote_health_status != SourceStatus.HEALTHY
        or not isinstance(value.quote_health_reasons, tuple)
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    symbols = tuple(item.symbol for item in value.candidate_plan.items)
    if (
        tuple(item.symbol for item in value._candidate_quotes) != symbols
        or tuple(
            item.symbol for item in value._candidate_security_records
        ) != symbols
    ):
        return False
    return value.context_id == _context_id(
        plan=value.candidate_plan,
        quote_health_status=value.quote_health_status,
        quote_health_reasons=value.quote_health_reasons,
        quotes=value._candidate_quotes,
        security_records=value._candidate_security_records,
        industry_constituents=value._industry_constituents,
    )


def build_explicit_missing_leader_research_provider_input(
    context: LeaderResearchRuntimeSourceContext,
) -> LeaderResearchInputProviderPlanBatchInput:
    """为计划全集生成显式缺失输入，不调用任何来源或特征函数。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(RUNTIME_CONTEXT_UNVERIFIED)
    plan = context.candidate_plan
    entries = tuple(
        LeaderResearchInputProviderBatchEntry(
            symbol=item.symbol,
            history_input=None,
            business_catalyst_input=None,
            tradability_input=None,
        )
        for item in plan.items
    )
    risk_batch = _missing_risk_projection_batch(plan)
    return LeaderResearchInputProviderPlanBatchInput(
        candidate_plan=plan,
        radar_run_id=plan.radar_run_id,
        as_of=plan.as_of,
        provider_contract_id=(
            LEADER_RESEARCH_EXPLICIT_MISSING_PROVIDER_CONTRACT_ID
        ),
        entries=entries,
        risk_projection_batch=risk_batch,
    )


def _missing_risk_projection_batch(
    plan: LeaderRuntimeCandidatePlan,
) -> LeaderRiskCandidateProjectionBatchResult:
    risk_items = tuple(
        LeaderRiskCandidateProjectionBatchItem(
            index=index,
            symbol=item.symbol,
            input_symbol=item.symbol,
            status=ResearchFeatureStatus.MISSING,
            reasons=("risk_evidence_bundle_missing",),
            projection=None,
        )
        for index, item in enumerate(plan.items)
    )
    return LeaderRiskCandidateProjectionBatchResult(
        status=LeaderRiskCandidateProjectionBatchStatus.MISSING,
        as_of=plan.as_of,
        input_count=len(risk_items),
        items=risk_items,
        reasons=("risk_candidate_projection_batch_no_ready_items",),
    )


def _verified_component_mapping(
    value: Any,
    candidate_symbols: Tuple[str, ...],
) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(RUNTIME_PROVIDER_INPUT_UNVERIFIED)
    if any(
        not isinstance(symbol, str)
        or symbol not in candidate_symbols
        for symbol in value
    ):
        raise ValueError(RUNTIME_PROVIDER_INPUT_UNVERIFIED)
    return dict(value)


def build_verified_leader_research_provider_input(
    context: LeaderResearchRuntimeSourceContext,
    *,
    provider_contract_id: str,
    history_inputs_by_symbol: Any = None,
    business_catalyst_inputs_by_symbol: Any = None,
    tradability_inputs_by_symbol: Any = None,
    risk_projection_batch: Any = None,
) -> LeaderResearchInputProviderPlanBatchInput:
    """把已验证研究组件绑定到当前候选计划，不调用任何外部来源。"""

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or not isinstance(provider_contract_id, str)
        or not provider_contract_id.strip()
    ):
        raise ValueError(RUNTIME_PROVIDER_INPUT_UNVERIFIED)
    plan = context.candidate_plan
    candidate_symbols = tuple(item.symbol for item in plan.items)
    history = _verified_component_mapping(
        history_inputs_by_symbol,
        candidate_symbols,
    )
    business = _verified_component_mapping(
        business_catalyst_inputs_by_symbol,
        candidate_symbols,
    )
    tradability = _verified_component_mapping(
        tradability_inputs_by_symbol,
        candidate_symbols,
    )
    provider_input = LeaderResearchInputProviderPlanBatchInput(
        candidate_plan=plan,
        radar_run_id=plan.radar_run_id,
        as_of=plan.as_of,
        provider_contract_id=provider_contract_id.strip(),
        entries=tuple(
            LeaderResearchInputProviderBatchEntry(
                symbol=symbol,
                history_input=history.get(symbol),
                business_catalyst_input=business.get(symbol),
                tradability_input=tradability.get(symbol),
            )
            for symbol in candidate_symbols
        ),
        risk_projection_batch=(
            _missing_risk_projection_batch(plan)
            if risk_projection_batch is None
            else risk_projection_batch
        ),
    )
    validation = build_leader_research_input_provider_batch_from_plan(
        provider_input
    )
    if validation.status == LeaderResearchInputProviderBatchStatus.BLOCKED:
        raise ValueError(RUNTIME_PROVIDER_INPUT_UNVERIFIED)
    return provider_input
