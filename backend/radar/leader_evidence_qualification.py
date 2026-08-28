"""用已重放的官方主营证据派生证据齐全候选子计划。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
)
from radar.leader_business_automatic_evidence import (
    LEADER_BUSINESS_AUTOMATIC_EVIDENCE_CONTRACT_ID,
    LeaderBusinessAutomaticEvidenceBatchItem,
    LeaderBusinessAutomaticEvidenceBatchResult,
)
from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
)
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialBusinessMaterialBatchEntry,
)
from radar.leader_business_catalyst_production_collector import (
    LeaderBusinessCatalystProductionFrozenBatch,
)
from radar.leader_business_catalyst_runtime_bridge import (
    LeaderBusinessCatalystRuntimeSourceBatch,
)
from radar.leader_business_deterministic_verification import (
    replay_deterministic_official_business_verification,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_business_official_verification_adapter import (
    LeaderOfficialBusinessDeterministicVerificationBatchEntry,
)
from radar.leader_evidence_candidate_plan import (
    LeaderEvidenceCandidatePlan,
    LeaderEvidenceCandidatePlanItem,
    LeaderEvidenceCandidatePlanStatus,
    is_leader_evidence_candidate_plan_valid,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    derive_leader_runtime_candidate_plan_subset,
    is_leader_runtime_candidate_plan_valid,
)


LEADER_EVIDENCE_QUALIFICATION_CONTRACT_ID = (
    "radar-leader-evidence-qualification-v1"
)
LEADER_EVIDENCE_QUALIFICATION_POLICY_VERSION = (
    "radar-leader-evidence-business-ready-only-v1"
)


class LeaderEvidenceQualificationStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderEvidenceQualificationExcludedItem:
    index: int
    symbol: str
    status: AutomaticBusinessEvidenceStatus
    reasons: Tuple[str, ...]

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, repr=False)
class LeaderEvidenceQualificationResult:
    status: LeaderEvidenceQualificationStatus
    parent_candidate_plan_id: Optional[str]
    parent_candidate_count: int
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = field(
        default=None,
        repr=False,
    )
    evidence_plan: Optional[LeaderEvidenceCandidatePlan] = field(
        default=None,
        repr=False,
    )
    business_automatic: Optional[
        LeaderBusinessAutomaticEvidenceBatchResult
    ] = field(default=None, repr=False)
    excluded_items: Tuple[
        LeaderEvidenceQualificationExcludedItem, ...
    ] = ()
    reasons: Tuple[str, ...] = ()
    qualification_id: Optional[str] = None
    contract_id: str = LEADER_EVIDENCE_QUALIFICATION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def qualified_candidate_count(self) -> int:
        return (
            self.candidate_plan.candidate_count
            if self.candidate_plan is not None
            else 0
        )

    @property
    def excluded_candidate_count(self) -> int:
        return len(self.excluded_items)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "qualificationId": self.qualification_id,
            "parentCandidatePlanId": self.parent_candidate_plan_id,
            "parentCandidateCount": self.parent_candidate_count,
            "qualifiedCandidatePlanId": (
                self.candidate_plan.candidate_set_id
                if self.candidate_plan is not None
                else None
            ),
            "qualifiedCandidateCount": self.qualified_candidate_count,
            "excludedCandidateCount": self.excluded_candidate_count,
            "excludedItems": [
                item.to_evidence() for item in self.excluded_items
            ],
            "reasons": list(self.reasons),
            "businessAutomatic": (
                self.business_automatic.to_evidence()
                if self.business_automatic is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _blocked(plan: Any) -> LeaderEvidenceQualificationResult:
    return LeaderEvidenceQualificationResult(
        status=LeaderEvidenceQualificationStatus.BLOCKED,
        parent_candidate_plan_id=(
            plan.candidate_set_id
            if isinstance(plan, LeaderRuntimeCandidatePlan)
            else None
        ),
        parent_candidate_count=(
            plan.candidate_count
            if isinstance(plan, LeaderRuntimeCandidatePlan)
            else 0
        ),
        reasons=("leader_business_evidence_qualification_unverified",),
    )


def _items_contract_valid(
    plan: LeaderRuntimeCandidatePlan,
    business: LeaderBusinessAutomaticEvidenceBatchResult,
) -> bool:
    if (
        len(business.items) != plan.candidate_count
        or business.candidate_plan_id != plan.candidate_set_id
        or business.candidate_count != plan.candidate_count
        or business.contract_id
        != LEADER_BUSINESS_AUTOMATIC_EVIDENCE_CONTRACT_ID
    ):
        return False
    for index, (plan_item, item) in enumerate(zip(
        plan.items,
        business.items,
    )):
        if (
            type(item) is not LeaderBusinessAutomaticEvidenceBatchItem
            or item.index != index
            or item.symbol != plan_item.symbol
            or not isinstance(item.reasons, tuple)
            or any(
                not isinstance(reason, str) or not reason
                for reason in item.reasons
            )
            or item.status not in AutomaticBusinessEvidenceStatus
        ):
            return False
        if item.status is AutomaticBusinessEvidenceStatus.READY:
            replayed = replay_deterministic_official_business_verification(
                item.artifact,
                as_of=plan.as_of,
            )
            if (
                replayed.status is not AutomaticBusinessEvidenceStatus.READY
                or replayed.artifact != item.artifact
                or item.artifact.symbol != plan_item.symbol
                or item.artifact.industry_code != plan_item.industry_code
                or item.artifact.industry_name != plan_item.industry_name
                or item.artifact.industry_release_id
                != plan_item.industry_release_id
                or type(item.material_entry)
                is not LeaderOfficialBusinessMaterialBatchEntry
                or item.material_entry.symbol != plan_item.symbol
            ):
                return False
        elif item.artifact is not None or item.material_entry is not None:
            return False
    return True


def _qualified_business_batch(
    child_plan: LeaderRuntimeCandidatePlan,
    items: Sequence[LeaderBusinessAutomaticEvidenceBatchItem],
) -> LeaderBusinessAutomaticEvidenceBatchResult:
    qualified_items = tuple(
        replace(item, index=index, checkpoint_path=None, reused=False)
        for index, item in enumerate(items)
    )
    fetched_at = max(item.artifact.validated_at for item in qualified_items)
    source_batch = LeaderBusinessCatalystRuntimeSourceBatch(
        candidate_plan_id=child_plan.candidate_set_id,
        radar_run_id=child_plan.radar_run_id,
        quote_batch_id=child_plan.quote_batch_id,
        as_of=child_plan.as_of,
        material_entries=tuple(
            item.material_entry for item in qualified_items
        ),
        review_entries=(),
        verification_entries=tuple(
            LeaderOfficialBusinessDeterministicVerificationBatchEntry(
                symbol=item.symbol,
                verification_artifact=item.artifact,
            )
            for item in qualified_items
        ),
    )
    return LeaderBusinessAutomaticEvidenceBatchResult(
        status=AutomaticBusinessEvidenceStatus.READY,
        candidate_plan_id=child_plan.candidate_set_id,
        candidate_count=child_plan.candidate_count,
        items=qualified_items,
        reasons=(),
        production_frozen_batch=LeaderBusinessCatalystProductionFrozenBatch(
            source_batch=source_batch,
            fetched_at=fetched_at,
            source_status=(
                LeaderFormalResearchProductionSourceStatus.COMPLETED
            ),
        ),
    )


def build_leader_business_evidence_qualification(
    plan: Any,
    business: Any,
) -> LeaderEvidenceQualificationResult:
    """只保留官方主营证据可重放且关系为正向直接的候选。"""

    if (
        not is_leader_runtime_candidate_plan_valid(plan)
        or type(business) is not LeaderBusinessAutomaticEvidenceBatchResult
        or not _items_contract_valid(plan, business)
    ):
        return _blocked(plan)
    if any(
        item.status is AutomaticBusinessEvidenceStatus.SOURCE_FAILED
        for item in business.items
    ):
        return LeaderEvidenceQualificationResult(
            status=LeaderEvidenceQualificationStatus.BLOCKED,
            parent_candidate_plan_id=plan.candidate_set_id,
            parent_candidate_count=plan.candidate_count,
            reasons=(
                "leader_business_evidence_qualification_source_failed",
            ),
        )
    qualified = []
    excluded = []
    for plan_item, item in zip(plan.items, business.items):
        if (
            item.status is AutomaticBusinessEvidenceStatus.READY
            and item.artifact.relation is BusinessCatalystRelation.DIRECT
        ):
            qualified.append(item)
            continue
        reasons = item.reasons or (
            "business_relation_disproved",
        )
        excluded.append(LeaderEvidenceQualificationExcludedItem(
            index=plan_item.index,
            symbol=plan_item.symbol,
            status=item.status,
            reasons=tuple(reasons),
        ))
    qualification_payload = {
        "contractId": LEADER_EVIDENCE_QUALIFICATION_CONTRACT_ID,
        "policyVersion": LEADER_EVIDENCE_QUALIFICATION_POLICY_VERSION,
        "businessContractId": business.contract_id,
        "businessRuleVersion": DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
        "parentCandidatePlanId": plan.candidate_set_id,
        "items": [
            {
                "index": item.index,
                "symbol": item.symbol,
                "status": item.status.value,
                "reasons": list(item.reasons),
                "verificationId": (
                    item.artifact.verification_id
                    if item.artifact is not None
                    else None
                ),
            }
            for item in business.items
        ],
    }
    qualification_id = _digest(qualification_payload)
    if not qualified:
        return LeaderEvidenceQualificationResult(
            status=LeaderEvidenceQualificationStatus.EMPTY,
            parent_candidate_plan_id=plan.candidate_set_id,
            parent_candidate_count=plan.candidate_count,
            excluded_items=tuple(excluded),
            reasons=("leader_business_evidence_qualification_empty",),
            qualification_id=qualification_id,
        )
    policy_id = ":".join((
        LEADER_EVIDENCE_QUALIFICATION_POLICY_VERSION,
        DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
        qualification_id,
    ))
    symbols = tuple(item.symbol for item in qualified)
    child_plan = derive_leader_runtime_candidate_plan_subset(
        plan,
        symbols=symbols,
        derivation_policy_id=policy_id,
    )
    if not is_leader_runtime_candidate_plan_valid(child_plan):
        return _blocked(plan)
    parent_index = {
        item.symbol: item.index for item in plan.items
    }
    evidence_plan = LeaderEvidenceCandidatePlan(
        status=LeaderEvidenceCandidatePlanStatus.READY,
        preliminary_candidate_plan_id=plan.candidate_set_id,
        preliminary_candidate_count=plan.candidate_count,
        candidate_plan=child_plan,
        items=tuple(
            LeaderEvidenceCandidatePlanItem(
                index=index,
                symbol=item.symbol,
                industry_code=item.industry_code,
                parent_index=parent_index[item.symbol],
                selection_kind="new_candidate",
                research_partial_score=None,
                previous_state=None,
            )
            for index, item in enumerate(child_plan.items)
        ),
        reasons=(),
        selection_policy_id=policy_id,
    )
    if not is_leader_evidence_candidate_plan_valid(evidence_plan):
        return _blocked(plan)
    qualified_business = _qualified_business_batch(child_plan, qualified)
    return LeaderEvidenceQualificationResult(
        status=LeaderEvidenceQualificationStatus.READY,
        parent_candidate_plan_id=plan.candidate_set_id,
        parent_candidate_count=plan.candidate_count,
        candidate_plan=child_plan,
        evidence_plan=evidence_plan,
        business_automatic=qualified_business,
        excluded_items=tuple(excluded),
        reasons=(),
        qualification_id=qualification_id,
    )


def derive_leader_qualified_business_material_acceptance(
    material: Any,
    *,
    parent_plan: Any,
    qualification: Any,
) -> LeaderBusinessMaterialLiveAcceptanceResult:
    """按资格子计划收缩同轮官方材料队列，不改变文档事实。"""

    queue = getattr(material, "review_queue", None)
    if (
        type(material) is not LeaderBusinessMaterialLiveAcceptanceResult
        or material.status
        is not LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
        or not is_leader_runtime_candidate_plan_valid(parent_plan)
        or type(qualification) is not LeaderEvidenceQualificationResult
        or qualification.status
        is not LeaderEvidenceQualificationStatus.READY
        or qualification.parent_candidate_plan_id
        != parent_plan.candidate_set_id
        or qualification.parent_candidate_count
        != parent_plan.candidate_count
        or material.radar_run_id != parent_plan.radar_run_id
        or material.as_of != parent_plan.as_of
        or material.candidate_plan_id != parent_plan.candidate_set_id
        or material.candidate_count != parent_plan.candidate_count
        or type(queue) is not LeaderBusinessMaterialReviewQueue
        or queue.candidate_plan_id != parent_plan.candidate_set_id
        or queue.candidate_count != parent_plan.candidate_count
        or len(queue.items) != parent_plan.candidate_count
        or any(
            item.index != index
            or item.symbol != plan_item.symbol
            or item.industry_code != plan_item.industry_code
            or item.industry_release_id
            != plan_item.industry_release_id
            for index, (item, plan_item) in enumerate(zip(
                queue.items,
                parent_plan.items,
            ))
        )
    ):
        raise ValueError(
            "leader_qualified_business_material_unverified"
        )
    child_plan = qualification.candidate_plan
    by_symbol = queue.items_by_symbol
    narrowed_items = tuple(
        replace(by_symbol[item.symbol], index=index)
        for index, item in enumerate(child_plan.items)
    )
    if any(
        item.status is not LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
        for item in narrowed_items
    ):
        raise ValueError(
            "leader_qualified_business_material_unverified"
        )
    narrowed_queue = LeaderBusinessMaterialReviewQueue(
        status=LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
        candidate_plan_id=child_plan.candidate_set_id,
        candidate_count=child_plan.candidate_count,
        items=narrowed_items,
        reasons=(),
    )
    return LeaderBusinessMaterialLiveAcceptanceResult(
        status=LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
        radar_run_id=child_plan.radar_run_id,
        as_of=child_plan.as_of,
        candidate_plan_id=child_plan.candidate_set_id,
        candidate_count=child_plan.candidate_count,
        reasons=(),
        issuer_status=material.issuer_status,
        queue_status=narrowed_queue.status.value,
        review_summary=MappingProxyType({
            "pendingReview": child_plan.candidate_count,
            "missing": 0,
            "sourceFailed": 0,
            "sourceUnverified": 0,
        }),
        review_queue=narrowed_queue,
    )
