"""把显式官方主营材料和人工复核接到龙头研究来源准入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_catalyst_manual_review import (
    LeaderOfficialBusinessManualReviewBatchResult,
    apply_official_business_manual_reviews_batch,
)
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialBusinessMaterialBatchResult,
    build_leader_business_catalyst_inputs_from_official_artifacts_batch,
)
from radar.leader_business_official_verification_adapter import (
    apply_official_business_verifications_batch,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_explicit_missing_leader_research_provider_input,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionResult,
    build_leader_research_source_admission,
)


LEADER_BUSINESS_CATALYST_RUNTIME_SOURCE_CONTRACT_ID = (
    "radar-leader-business-catalyst-runtime-source-v1"
)
LEADER_BUSINESS_CATALYST_RUNTIME_BRIDGE_CONTRACT_ID = (
    "radar-leader-business-catalyst-runtime-bridge-v1"
)
SOURCE_MISSING = (
    "leader_business_catalyst_runtime_bridge_source_missing"
)
SOURCE_UNVERIFIED = (
    "leader_business_catalyst_runtime_bridge_source_unverified"
)
BRIDGE_MISSING = "leader_business_catalyst_runtime_bridge_missing"
UTC = timezone.utc


class LeaderBusinessCatalystRuntimeBridgeStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderBusinessCatalystRuntimeSourceBatch:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    as_of: datetime
    material_entries: Any = field(repr=False)
    review_entries: Any = field(repr=False)
    verification_entries: Any = field(default=None, repr=False)
    contract_id: str = (
        LEADER_BUSINESS_CATALYST_RUNTIME_SOURCE_CONTRACT_ID
    )


@dataclass(frozen=True)
class LeaderBusinessCatalystRuntimeBridgeItem:
    index: int
    symbol: str
    status: LeaderBusinessCatalystRuntimeBridgeStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderBusinessCatalystRuntimeBridgeResult:
    status: LeaderBusinessCatalystRuntimeBridgeStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    items: Tuple[LeaderBusinessCatalystRuntimeBridgeItem, ...]
    reasons: Tuple[str, ...]
    source_admission: LeaderResearchSourceAdmissionResult = field(repr=False)
    provider_input: Any = field(repr=False)
    business_admission_value: Any = field(repr=False)
    material_batch: Optional[
        LeaderOfficialBusinessMaterialBatchResult
    ] = field(default=None, repr=False)
    business_review_batch: Any = field(default=None, repr=False)
    business_inputs_by_symbol: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
    )
    contract_id: str = LEADER_BUSINESS_CATALYST_RUNTIME_BRIDGE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderBusinessCatalystRuntimeBridgeStatus.READY
            for item in self.items
        )

    @property
    def missing_count(self) -> int:
        return self.candidate_count - self.ready_count

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "missingCount": self.missing_count,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "sourceAdmissionStatus": self.source_admission.status.value,
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


def _source_batch_bound(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> bool:
    plan = context.candidate_plan
    return bool(
        type(value) is LeaderBusinessCatalystRuntimeSourceBatch
        and value.contract_id
        == LEADER_BUSINESS_CATALYST_RUNTIME_SOURCE_CONTRACT_ID
        and value.candidate_plan_id == plan.candidate_set_id
        and value.radar_run_id == plan.radar_run_id
        and value.quote_batch_id == plan.quote_batch_id
        and _aware_utc(value.as_of) == plan.as_of
    )


def _missing_items(
    symbols: Tuple[str, ...],
    reason: str,
) -> Tuple[LeaderBusinessCatalystRuntimeBridgeItem, ...]:
    return tuple(
        LeaderBusinessCatalystRuntimeBridgeItem(
            index=index,
            symbol=symbol,
            status=LeaderBusinessCatalystRuntimeBridgeStatus.MISSING,
            reasons=(reason,),
        )
        for index, symbol in enumerate(symbols)
    )


def build_leader_business_catalyst_runtime_bridge(
    context: LeaderResearchRuntimeSourceContext,
    *,
    source_batch: Any = None,
) -> LeaderBusinessCatalystRuntimeBridgeResult:
    """校验显式官方材料与关系验证；不抓取、不写库。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(SOURCE_UNVERIFIED)
    plan = context.candidate_plan
    symbols = tuple(item.symbol for item in plan.items)
    source_reason = None
    material_batch = None
    business_review_batch = None

    if source_batch is None:
        source_reason = SOURCE_MISSING
    elif not _source_batch_bound(source_batch, context=context):
        source_reason = SOURCE_UNVERIFIED
    elif (
        source_batch.verification_entries is not None
        and source_batch.review_entries
    ):
        source_reason = SOURCE_UNVERIFIED
    else:
        material_batch = (
            build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                candidate_plan=plan,
                entries=source_batch.material_entries,
            )
        )
        if source_batch.verification_entries is None:
            business_review_batch = apply_official_business_manual_reviews_batch(
                material_batch,
                source_batch.review_entries,
            )
        else:
            business_review_batch = apply_official_business_verifications_batch(
                material_batch,
                deterministic_entries=source_batch.verification_entries,
            )

    business_admission_value = business_review_batch
    if source_reason == SOURCE_UNVERIFIED:
        business_admission_value = source_batch

    source_admission = build_leader_research_source_admission(
        LeaderResearchSourceAdmissionInput(
            context=context,
            history_entries=None,
            business_review_batch=business_admission_value,
            tradability_bundle=None,
            risk_projection_bundle=None,
        )
    )
    provider_input = source_admission.provider_input
    if provider_input is None:
        provider_input = build_explicit_missing_leader_research_provider_input(
            context
        )
    provider_result = source_admission.provider_result
    business_inputs = (
        dict(provider_result.business_catalyst_inputs_by_symbol)
        if provider_result is not None
        else {}
    )

    if source_reason is not None:
        items = _missing_items(symbols, source_reason)
    elif (
        business_review_batch is None
        or len(business_review_batch.items) != len(symbols)
        or tuple(item.symbol for item in business_review_batch.items)
        != symbols
    ):
        source_reason = SOURCE_UNVERIFIED
        items = _missing_items(symbols, source_reason)
        business_inputs = {}
    else:
        items = tuple(
            LeaderBusinessCatalystRuntimeBridgeItem(
                index=index,
                symbol=symbol,
                status=(
                    LeaderBusinessCatalystRuntimeBridgeStatus.READY
                    if review_item.status == ResearchFeatureStatus.READY
                    and symbol in business_inputs
                    else LeaderBusinessCatalystRuntimeBridgeStatus.MISSING
                ),
                reasons=(
                    ()
                    if review_item.status == ResearchFeatureStatus.READY
                    and symbol in business_inputs
                    else review_item.reasons or (BRIDGE_MISSING,)
                ),
            )
            for index, (symbol, review_item) in enumerate(zip(
                symbols,
                business_review_batch.items,
            ))
        )

    missing = any(
        item.status == LeaderBusinessCatalystRuntimeBridgeStatus.MISSING
        for item in items
    )
    reasons = _dedupe((
        source_reason,
        BRIDGE_MISSING if missing and source_reason is None else None,
    ))
    return LeaderBusinessCatalystRuntimeBridgeResult(
        status=(
            LeaderBusinessCatalystRuntimeBridgeStatus.MISSING
            if missing
            else LeaderBusinessCatalystRuntimeBridgeStatus.READY
        ),
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(symbols),
        items=items,
        reasons=reasons,
        source_admission=source_admission,
        provider_input=provider_input,
        business_admission_value=business_admission_value,
        material_batch=material_batch,
        business_review_batch=business_review_batch,
        business_inputs_by_symbol=MappingProxyType(business_inputs),
    )
