"""把同轮预冻结、自动主营和官方风险绑定到阶段6五源入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    MAXIMUM_DETERMINISTIC_COLLECTION_DELAY_SECONDS,
)
from radar.leader_business_automatic_evidence import (
    LeaderBusinessAutomaticEvidenceBatchResult,
    run_leader_business_automatic_evidence,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
    run_leader_business_material_live_acceptance,
)
from radar.leader_business_material_review_submission import (
    build_leader_business_material_review_source_packet,
)
from radar.leader_business_catalyst_production_collector import (
    LeaderBusinessCatalystProductionFrozenBatch,
)
from radar.leader_formal_research_production_acceptance import (
    LeaderFormalResearchProductionAcceptanceStatus,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_formal_industry_gate import (
    build_leader_formal_industry_gate_evidence,
)
from radar.leader_evidence_candidate_plan import (
    derive_leader_evidence_candidate_tradability_acceptance,
)
from radar.leader_evidence_qualification import (
    LeaderEvidenceQualificationResult,
    LeaderEvidenceQualificationStatus,
    build_leader_business_evidence_qualification,
    derive_leader_qualified_business_material_acceptance,
)
from radar.leader_phase6_live_prefreeze import LeaderPhase6PrefrozenInputs
from radar.leader_phase6_production_readiness import (
    LeaderPhase6ProductionFrozenInputs,
    LeaderPhase6ProductionReadinessResult,
    build_leader_phase6_production_readiness,
)
from radar.leader_phase6_state_decision_review import (
    LeaderPhase6StateDecisionReviewResult,
    build_leader_phase6_state_decision_review,
)
from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassInput,
    build_leader_research_single_pass,
)
from radar.leader_research_runtime_provider import (
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_risk_official_deterministic import (
    LeaderOfficialDeterministicRiskFrozenBatch,
)
from radar.leader_risk_official_live_delivery import (
    LeaderRiskOfficialLiveDelivery,
    build_leader_risk_official_live_delivery,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceResult,
    LeaderTradabilityLiveAcceptanceStatus,
)
from radar.leader_tradability_production_collector import (
    LeaderTradabilityProductionFrozenBatch,
)
from radar.sector_threshold_review import (
    SectorThresholdApprovalLoadResult,
    bind_latest_sector_threshold_approval,
)


LEADER_PHASE6_LIVE_SOURCE_READINESS_CONTRACT_ID = (
    "radar-leader-phase6-live-source-readiness-v1"
)
LEADER_PHASE6_LIVE_SOURCE_COLLECTION_CONTRACT_ID = (
    "radar-leader-phase6-live-source-collection-v1"
)
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_SAFE_QUALIFIED_TRADABILITY_FAILURE_REASONS = frozenset({
    "leader_evidence_candidate_parent_acceptance_unverified",
    "leader_evidence_candidate_runtime_subset_unverified",
    "leader_evidence_candidate_runtime_inputs_unverified",
    "leader_evidence_candidate_sector_subset_unverified",
    "leader_evidence_candidate_parent_report_unverified",
    "leader_evidence_candidate_security_subset_unverified",
    "leader_evidence_candidate_report_subset_unverified",
    "leader_evidence_candidate_history_source_unverified",
    "leader_evidence_candidate_tradability_source_unverified",
    "leader_evidence_candidate_sector_rule_source_unverified",
})


class LeaderPhase6LiveSourceReadinessStatus(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    EMPTY = "empty"
    NOT_READY = "not_ready"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True, repr=False)
class LeaderPhase6LiveSourceReadinessResult:
    status: LeaderPhase6LiveSourceReadinessStatus
    radar_run_id: Optional[str]
    candidate_plan_id: Optional[str]
    as_of: Optional[datetime]
    candidate_count: int
    readiness: Optional[
        LeaderPhase6ProductionReadinessResult
    ] = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_PHASE6_LIVE_SOURCE_READINESS_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidateCount": self.candidate_count,
            "validEmptyResult": (
                self.status is LeaderPhase6LiveSourceReadinessStatus.EMPTY
            ),
            "reasons": list(self.reasons),
            "readiness": (
                self.readiness.to_evidence()
                if self.readiness is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True, repr=False)
class LeaderPhase6LiveSourceCollectionResult:
    status: LeaderPhase6LiveSourceReadinessStatus
    candidate_source_packet_sha256: Optional[str]
    candidate_source_packet: Optional[Mapping[str, object]] = field(
        default=None,
        repr=False,
    )
    readiness: Optional[
        LeaderPhase6LiveSourceReadinessResult
    ] = field(default=None, repr=False)
    material_acceptance: Optional[
        LeaderBusinessMaterialLiveAcceptanceResult
    ] = field(default=None, repr=False)
    business_automatic: Optional[
        LeaderBusinessAutomaticEvidenceBatchResult
    ] = field(default=None, repr=False)
    risk_live_delivery: Optional[
        LeaderRiskOfficialLiveDelivery
    ] = field(default=None, repr=False)
    qualification: Optional[
        LeaderEvidenceQualificationResult
    ] = field(default=None, repr=False)
    verification_candidate_source_packet_sha256: Optional[str] = None
    verification_candidate_source_packet: Optional[
        Mapping[str, object]
    ] = field(default=None, repr=False)
    verification_material_acceptance: Optional[
        LeaderBusinessMaterialLiveAcceptanceResult
    ] = field(default=None, repr=False)
    verification_business_automatic: Optional[
        LeaderBusinessAutomaticEvidenceBatchResult
    ] = field(default=None, repr=False)
    state_decision_review: Optional[
        LeaderPhase6StateDecisionReviewResult
    ] = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_PHASE6_LIVE_SOURCE_COLLECTION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "validEmptyResult": (
                self.status is LeaderPhase6LiveSourceReadinessStatus.EMPTY
            ),
            "candidateSourcePacketSha256": (
                self.candidate_source_packet_sha256
            ),
            "reasons": list(self.reasons),
            "qualification": (
                self.qualification.to_evidence()
                if self.qualification is not None
                else None
            ),
            "verificationCandidateSourcePacketSha256": (
                self.verification_candidate_source_packet_sha256
            ),
            "verificationMaterialAcceptance": (
                self.verification_material_acceptance.to_evidence()
                if self.verification_material_acceptance is not None
                else None
            ),
            "verificationBusinessAutomatic": (
                self.verification_business_automatic.to_evidence()
                if self.verification_business_automatic is not None
                else None
            ),
            "stateDecisionReview": (
                self.state_decision_review.to_evidence()
                if self.state_decision_review is not None
                else None
            ),
            "materialAcceptance": (
                self.material_acceptance.to_evidence()
                if self.material_acceptance is not None
                else None
            ),
            "businessAutomatic": (
                self.business_automatic.to_evidence()
                if self.business_automatic is not None
                else None
            ),
            "riskDelivery": (
                self.risk_live_delivery.delivery.to_evidence()
                if self.risk_live_delivery is not None
                else None
            ),
            "readiness": (
                self.readiness.to_evidence()
                if self.readiness is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: LeaderPhase6LiveSourceReadinessStatus,
    tradability: Any,
    *,
    readiness: Optional[LeaderPhase6ProductionReadinessResult] = None,
    reasons: Sequence[str] = (),
) -> LeaderPhase6LiveSourceReadinessResult:
    plan = getattr(tradability, "candidate_plan", None)
    return LeaderPhase6LiveSourceReadinessResult(
        status=status,
        radar_run_id=getattr(plan, "radar_run_id", None),
        candidate_plan_id=getattr(plan, "candidate_set_id", None),
        as_of=getattr(plan, "as_of", None),
        candidate_count=getattr(plan, "candidate_count", 0),
        readiness=readiness,
        reasons=_dedupe(reasons),
    )


def _business_frozen(
    value: LeaderBusinessAutomaticEvidenceBatchResult,
) -> LeaderBusinessCatalystProductionFrozenBatch:
    if value.status is AutomaticBusinessEvidenceStatus.READY:
        assert value.production_frozen_batch is not None
        return value.production_frozen_batch
    status = {
        AutomaticBusinessEvidenceStatus.SOURCE_FAILED: (
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED
        ),
        AutomaticBusinessEvidenceStatus.MISSING: (
            LeaderFormalResearchProductionSourceStatus.NOT_RUN
        ),
    }.get(
        value.status,
        LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
    )
    return LeaderBusinessCatalystProductionFrozenBatch(
        source_batch=None,
        fetched_at=None,
        source_status=status,
    )


def build_leader_phase6_live_source_readiness(
    tradability: Any,
    *,
    business_automatic: Any,
    risk_live_delivery: Any,
    repository: Any,
    sector_threshold_approval_binder: Callable[
        [Any], SectorThresholdApprovalLoadResult
    ] = bind_latest_sector_threshold_approval,
) -> LeaderPhase6LiveSourceReadinessResult:
    """只接受同一最终计划的五源冻结输入，不从旧工件改写身份。"""

    plan = getattr(tradability, "candidate_plan", None)
    context = getattr(tradability, "source_context", None)
    prefrozen = getattr(tradability, "phase6_prefrozen_inputs", None)
    tradability_frozen = getattr(tradability, "frozen_batch", None)
    if (
        type(tradability) is not LeaderTradabilityLiveAcceptanceResult
        or tradability.status
        is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED
        or plan is None
        or not is_leader_research_runtime_source_context_valid(context)
        or context.candidate_plan != plan
        or tradability.radar_run_id != plan.radar_run_id
        or tradability.candidate_plan_id != plan.candidate_set_id
        or tradability.as_of != plan.as_of
        or type(prefrozen) is not LeaderPhase6PrefrozenInputs
        or type(tradability_frozen)
        is not LeaderTradabilityProductionFrozenBatch
    ):
        return _result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=(
                "leader_phase6_live_prefrozen_identity_unverified",
            ),
        )
    if (
        type(business_automatic)
        is not LeaderBusinessAutomaticEvidenceBatchResult
        or business_automatic.candidate_plan_id != plan.candidate_set_id
        or business_automatic.candidate_count != plan.candidate_count
        or (
            business_automatic.status
            is AutomaticBusinessEvidenceStatus.READY
            and type(business_automatic.production_frozen_batch)
            is not LeaderBusinessCatalystProductionFrozenBatch
        )
        or (
            business_automatic.status
            is not AutomaticBusinessEvidenceStatus.READY
            and business_automatic.production_frozen_batch is not None
        )
    ):
        return _result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=(
                "leader_phase6_live_business_identity_unverified",
            ),
        )
    if (
        type(risk_live_delivery) is not LeaderRiskOfficialLiveDelivery
        or risk_live_delivery.candidate_plan != plan
        or re.fullmatch(
            r"[0-9a-f]{64}",
            risk_live_delivery.candidate_source_packet_sha256,
        ) is None
    ):
        return _result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("leader_phase6_live_risk_identity_unverified",),
        )
    frozen_inputs = LeaderPhase6ProductionFrozenInputs(
        history=prefrozen.history,
        business_catalyst=_business_frozen(business_automatic),
        tradability=tradability_frozen,
        sector_rule=prefrozen.sector_rule,
        risk=LeaderOfficialDeterministicRiskFrozenBatch(
            delivery=risk_live_delivery.delivery,
            document_contents=(),
        ),
    )
    try:
        readiness = build_leader_phase6_production_readiness(
            context,
            repository=repository,
            frozen_inputs=frozen_inputs,
            sector_threshold_approval_binder=(
                sector_threshold_approval_binder
            ),
        )
    except Exception:
        return _result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("leader_phase6_live_five_source_replay_failed",),
        )
    ready = (
        readiness.acceptance.status
        is LeaderFormalResearchProductionAcceptanceStatus.READY_FOR_REVIEW
    )
    reasons = (
        ()
        if ready
        else (
            *business_automatic.reasons,
            *readiness.assembly.reasons,
        )
    )
    return _result(
        (
            LeaderPhase6LiveSourceReadinessStatus.READY_FOR_REVIEW
            if ready
            else LeaderPhase6LiveSourceReadinessStatus.NOT_READY
        ),
        tradability,
        readiness=readiness,
        reasons=reasons,
    )


def _collection_result(
    status: LeaderPhase6LiveSourceReadinessStatus,
    *,
    packet_sha256: Optional[str] = None,
    candidate_source_packet: Optional[Mapping[str, object]] = None,
    readiness: Optional[LeaderPhase6LiveSourceReadinessResult] = None,
    material_acceptance: Optional[
        LeaderBusinessMaterialLiveAcceptanceResult
    ] = None,
    business_automatic: Optional[
        LeaderBusinessAutomaticEvidenceBatchResult
    ] = None,
    risk_live_delivery: Optional[LeaderRiskOfficialLiveDelivery] = None,
    qualification: Optional[LeaderEvidenceQualificationResult] = None,
    verification_packet_sha256: Optional[str] = None,
    verification_candidate_source_packet: Optional[
        Mapping[str, object]
    ] = None,
    verification_material_acceptance: Optional[
        LeaderBusinessMaterialLiveAcceptanceResult
    ] = None,
    verification_business_automatic: Optional[
        LeaderBusinessAutomaticEvidenceBatchResult
    ] = None,
    state_decision_review: Optional[
        LeaderPhase6StateDecisionReviewResult
    ] = None,
    reasons: Sequence[str] = (),
) -> LeaderPhase6LiveSourceCollectionResult:
    return LeaderPhase6LiveSourceCollectionResult(
        status=status,
        candidate_source_packet_sha256=packet_sha256,
        candidate_source_packet=candidate_source_packet,
        readiness=readiness,
        material_acceptance=material_acceptance,
        business_automatic=business_automatic,
        risk_live_delivery=risk_live_delivery,
        qualification=qualification,
        verification_candidate_source_packet_sha256=(
            verification_packet_sha256
        ),
        verification_candidate_source_packet=(
            verification_candidate_source_packet
        ),
        verification_material_acceptance=(
            verification_material_acceptance
        ),
        verification_business_automatic=(
            verification_business_automatic
        ),
        state_decision_review=state_decision_review,
        reasons=_dedupe(reasons),
    )


def _build_live_state_decision_review(
    parent_tradability: LeaderTradabilityLiveAcceptanceResult,
    qualification: LeaderEvidenceQualificationResult,
    qualified_tradability: LeaderTradabilityLiveAcceptanceResult,
    readiness: LeaderPhase6LiveSourceReadinessResult,
    *,
    industry_scope: Any = None,
) -> LeaderPhase6StateDecisionReviewResult:
    """复用既有F6单次组装，生成只读状态决策前审查。"""

    parent_plan = getattr(parent_tradability, "candidate_plan", None)
    child_plan = getattr(qualified_tradability, "candidate_plan", None)
    runtime = getattr(qualified_tradability, "runtime_inputs", None)
    production = getattr(readiness, "readiness", None)
    assembly = getattr(production, "assembly", None)
    provider_input = getattr(assembly, "provider_input", None)
    sector_rule_readiness = getattr(
        assembly,
        "sector_rule_readiness",
        None,
    )
    quote_batch = getattr(runtime, "quote_batch", None)
    try:
        single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=child_plan,
                provider_input=provider_input,
                source_context=qualified_tradability.source_context,
                as_of=child_plan.as_of,
                quote_batch=quote_batch,
                quote_health=runtime.quote_health,
                market_snapshot=runtime.market_snapshot,
                sector_rows=runtime.sector_rows,
                industry_records=runtime.industry_records,
                security_records=runtime.security_records,
                sector_rule_readiness=sector_rule_readiness,
            )
        )
        industry_gate_evidence = build_leader_formal_industry_gate_evidence(
            parent_plan,
            candidate_plan=child_plan,
            industry_scope=industry_scope,
            single_pass=single_pass,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        single_pass = None
        industry_gate_evidence = None
    return build_leader_phase6_state_decision_review(
        parent_plan,
        qualification=qualification,
        single_pass=single_pass,
        industry_gate_evidence=industry_gate_evidence,
    )


def run_leader_phase6_live_source_collection(
    tradability: Any,
    *,
    artifact_dir: Path,
    collected_at: datetime,
    repository: Any,
    industry_scope: Any = None,
    business_material_runner: Callable[..., Any] = (
        run_leader_business_material_live_acceptance
    ),
    business_automatic_runner: Callable[..., Any] = (
        run_leader_business_automatic_evidence
    ),
    risk_live_runner: Callable[..., Any] = (
        build_leader_risk_official_live_delivery
    ),
    qualification_runner: Callable[..., Any] = (
        build_leader_business_evidence_qualification
    ),
    qualified_tradability_runner: Callable[..., Any] = (
        derive_leader_evidence_candidate_tradability_acceptance
    ),
    qualified_material_runner: Callable[..., Any] = (
        derive_leader_qualified_business_material_acceptance
    ),
    sector_threshold_approval_binder: Callable[
        [Any], SectorThresholdApprovalLoadResult
    ] = bind_latest_sector_threshold_approval,
) -> LeaderPhase6LiveSourceCollectionResult:
    """同一内存候选计划依次采集主营与风险，再重放五源。"""

    plan = getattr(tradability, "candidate_plan", None)
    root = Path(artifact_dir).expanduser().resolve()
    try:
        root.relative_to(Path("/private/tmp"))
    except ValueError:
        return _collection_result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            reasons=("leader_phase6_live_source_path_unverified",),
        )
    if (
        type(tradability) is not LeaderTradabilityLiveAcceptanceResult
        or tradability.status
        is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED
        or plan is None
        or not isinstance(collected_at, datetime)
        or collected_at.tzinfo is None
        or collected_at.utcoffset() is None
        or collected_at < plan.as_of
        or collected_at > plan.as_of + timedelta(
            seconds=MAXIMUM_DETERMINISTIC_COLLECTION_DELAY_SECONDS
        )
        or not all(callable(value) for value in (
            business_material_runner,
            business_automatic_runner,
            risk_live_runner,
            qualification_runner,
            qualified_tradability_runner,
            qualified_material_runner,
            sector_threshold_approval_binder,
        ))
    ):
        return _collection_result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            reasons=("leader_phase6_live_source_contract_unverified",),
        )
    collection_step = "initialize"
    material = None
    business = None
    qualification = None
    state_decision_review = None
    verification_packet = None
    verification_packet_sha256 = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        local_date = plan.as_of.astimezone(_SHANGHAI_TZ).date()
        collection_step = "material"
        material = business_material_runner(
            tradability,
            window_from=date(local_date.year - 2, 1, 1),
        )
        if (
            type(material) is not LeaderBusinessMaterialLiveAcceptanceResult
            or material.status
            is not LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
            or material.candidate_plan_id != plan.candidate_set_id
            or material.candidate_count != plan.candidate_count
            or material.review_queue is None
        ):
            return _collection_result(
                LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
                material_acceptance=(
                    material
                    if type(material)
                    is LeaderBusinessMaterialLiveAcceptanceResult
                    else None
                ),
                reasons=(
                    "leader_phase6_live_material_source_unverified",
                ),
            )
        collection_step = "verification_packet"
        verification_packet = build_leader_business_material_review_source_packet(
            plan,
            material.review_queue,
        )
        verification_packet_sha256 = verification_packet["packetSha256"]
        collection_step = "business"
        business = business_automatic_runner(
            verification_packet,
            artifact_dir=root / "business-automatic",
            clock=lambda: collected_at,
        )
        collection_step = "qualification"
        qualification = qualification_runner(plan, business)
        if (
            type(qualification) is not LeaderEvidenceQualificationResult
            or qualification.status
            is LeaderEvidenceQualificationStatus.BLOCKED
        ):
            return _collection_result(
                LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
                packet_sha256=verification_packet_sha256,
                candidate_source_packet=verification_packet,
                material_acceptance=material,
                business_automatic=business,
                qualification=(
                    qualification
                    if type(qualification)
                    is LeaderEvidenceQualificationResult
                    else None
                ),
                verification_packet_sha256=(
                    verification_packet_sha256
                ),
                verification_candidate_source_packet=(
                    verification_packet
                ),
                verification_material_acceptance=material,
                verification_business_automatic=business,
                reasons=(
                    "leader_phase6_business_qualification_unverified",
                ),
            )
        if qualification.status is LeaderEvidenceQualificationStatus.EMPTY:
            replayed_qualification = (
                build_leader_business_evidence_qualification(
                    plan,
                    business,
                )
            )
            if replayed_qualification != qualification:
                return _collection_result(
                    LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
                    packet_sha256=verification_packet_sha256,
                    candidate_source_packet=verification_packet,
                    material_acceptance=material,
                    business_automatic=business,
                    verification_packet_sha256=(
                        verification_packet_sha256
                    ),
                    verification_candidate_source_packet=(
                        verification_packet
                    ),
                    verification_material_acceptance=material,
                    verification_business_automatic=business,
                    reasons=(
                        "leader_phase6_business_qualification_unverified",
                    ),
                )
            conclusive_empty = (
                business.status is AutomaticBusinessEvidenceStatus.READY
                and business.ready_count == plan.candidate_count
                and qualification.excluded_candidate_count
                == plan.candidate_count
                and all(
                    item.status is AutomaticBusinessEvidenceStatus.READY
                    for item in qualification.excluded_items
                )
            )
            return _collection_result(
                (
                    LeaderPhase6LiveSourceReadinessStatus.EMPTY
                    if conclusive_empty
                    else LeaderPhase6LiveSourceReadinessStatus.NOT_READY
                ),
                packet_sha256=verification_packet_sha256,
                candidate_source_packet=verification_packet,
                material_acceptance=material,
                business_automatic=business,
                qualification=qualification,
                verification_packet_sha256=(
                    verification_packet_sha256
                ),
                verification_candidate_source_packet=(
                    verification_packet
                ),
                verification_material_acceptance=material,
                verification_business_automatic=business,
                reasons=qualification.reasons,
            )
        collection_step = "qualified_tradability"
        qualified_tradability = qualified_tradability_runner(
            tradability,
            qualification.evidence_plan,
            sector_threshold_approval_binder=(
                sector_threshold_approval_binder
            ),
        )
        collection_step = "qualified_material"
        qualified_material = qualified_material_runner(
            material,
            parent_plan=plan,
            qualification=qualification,
        )
        collection_step = "qualified_packet"
        packet = build_leader_business_material_review_source_packet(
            qualification.candidate_plan,
            qualified_material.review_queue,
        )
        packet_sha256 = packet["packetSha256"]
        collection_step = "risk"
        risk = risk_live_runner(packet, collected_at=collected_at)
        collection_step = "readiness"
        readiness = build_leader_phase6_live_source_readiness(
            qualified_tradability,
            business_automatic=qualification.business_automatic,
            risk_live_delivery=risk,
            repository=repository,
            sector_threshold_approval_binder=(
                sector_threshold_approval_binder
            ),
        )
        collection_step = "state_decision_review"
        state_decision_review = _build_live_state_decision_review(
            tradability,
            qualification,
            qualified_tradability,
            readiness,
            industry_scope=industry_scope,
        )
    except Exception as exc:
        failure_reason = (
            str(exc)
            if (
                collection_step == "qualified_tradability"
                and str(exc)
                in _SAFE_QUALIFIED_TRADABILITY_FAILURE_REASONS
            )
            else f"leader_phase6_live_source_{collection_step}_failed"
        )
        return _collection_result(
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            qualification=(
                qualification
                if type(qualification)
                is LeaderEvidenceQualificationResult
                else None
            ),
            verification_packet_sha256=verification_packet_sha256,
            verification_candidate_source_packet=(
                verification_packet
                if isinstance(verification_packet, Mapping)
                else None
            ),
            verification_material_acceptance=(
                material
                if type(material)
                is LeaderBusinessMaterialLiveAcceptanceResult
                else None
            ),
            verification_business_automatic=(
                business
                if type(business)
                is LeaderBusinessAutomaticEvidenceBatchResult
                else None
            ),
            reasons=(
                failure_reason,
            ),
        )
    return _collection_result(
        readiness.status,
        packet_sha256=packet_sha256,
        candidate_source_packet=packet,
        readiness=readiness,
        material_acceptance=qualified_material,
        business_automatic=qualification.business_automatic,
        risk_live_delivery=risk,
        qualification=qualification,
        verification_packet_sha256=verification_packet_sha256,
        verification_candidate_source_packet=verification_packet,
        verification_material_acceptance=material,
        verification_business_automatic=business,
        state_decision_review=state_decision_review,
        reasons=readiness.reasons,
    )
