"""候选全集官方主营材料的只读人工复核清单。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Tuple

from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDiscoveryBatch,
    OfficialBusinessMaterialDiscoveryStatus,
    OfficialBusinessMaterialDocument,
)


class LeaderBusinessMaterialReviewItemStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


class LeaderBusinessMaterialReviewQueueStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderBusinessMaterialReviewQueueItem:
    index: int
    symbol: str
    industry_code: str
    industry_release_id: str
    status: LeaderBusinessMaterialReviewItemStatus
    documents: Tuple[OfficialBusinessMaterialDocument, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "documentCount": len(self.documents),
            "documentIds": [item.document_id for item in self.documents],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderBusinessMaterialReviewQueue:
    status: LeaderBusinessMaterialReviewQueueStatus
    candidate_plan_id: str
    candidate_count: int
    items: Tuple[LeaderBusinessMaterialReviewQueueItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def items_by_symbol(self):
        return MappingProxyType({item.symbol: item for item in self.items})

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": "radar-leader-business-material-review-queue-v1",
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "items": [item.to_evidence() for item in self.items],
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def build_leader_business_material_review_queue(
    candidate_plan: Any,
    batches: Any,
) -> LeaderBusinessMaterialReviewQueue:
    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or not isinstance(batches, tuple)
        or any(type(batch) is not OfficialBusinessMaterialDiscoveryBatch for batch in batches)
    ):
        return LeaderBusinessMaterialReviewQueue(
            status=LeaderBusinessMaterialReviewQueueStatus.BLOCKED,
            candidate_plan_id="",
            candidate_count=0,
            reasons=("business_material_review_queue_contract_unverified",),
        )
    expected = tuple(item.symbol for item in candidate_plan.items)
    actual = tuple(batch.query.scope.symbol for batch in batches)
    if (
        len(actual) != len(set(actual))
        or set(actual) != set(expected)
        or any(
            batch.query.candidate_plan_id != candidate_plan.candidate_set_id
            or not batch.coverage_complete
            for batch in batches
        )
    ):
        return LeaderBusinessMaterialReviewQueue(
            status=LeaderBusinessMaterialReviewQueueStatus.BLOCKED,
            candidate_plan_id=candidate_plan.candidate_set_id,
            candidate_count=len(expected),
            reasons=("business_material_review_queue_candidate_mismatch",),
        )
    by_symbol = {batch.query.scope.symbol: batch for batch in batches}
    status_map = {
        OfficialBusinessMaterialDiscoveryStatus.READY: (
            LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
        ),
        OfficialBusinessMaterialDiscoveryStatus.MISSING: (
            LeaderBusinessMaterialReviewItemStatus.MISSING
        ),
        OfficialBusinessMaterialDiscoveryStatus.SOURCE_FAILED: (
            LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED
        ),
        OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED: (
            LeaderBusinessMaterialReviewItemStatus.SOURCE_UNVERIFIED
        ),
    }
    items = tuple(
        LeaderBusinessMaterialReviewQueueItem(
            index=index,
            symbol=plan_item.symbol,
            industry_code=plan_item.industry_code,
            industry_release_id=plan_item.industry_release_id,
            status=status_map[by_symbol[plan_item.symbol].status],
            documents=by_symbol[plan_item.symbol].documents,
            reasons=by_symbol[plan_item.symbol].reasons,
        )
        for index, plan_item in enumerate(candidate_plan.items)
    )
    statuses = {item.status for item in items}
    if statuses == {LeaderBusinessMaterialReviewItemStatus.MISSING}:
        overall = LeaderBusinessMaterialReviewQueueStatus.MISSING
    elif LeaderBusinessMaterialReviewItemStatus.SOURCE_UNVERIFIED in statuses:
        overall = LeaderBusinessMaterialReviewQueueStatus.SOURCE_UNVERIFIED
    elif LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED in statuses:
        overall = LeaderBusinessMaterialReviewQueueStatus.SOURCE_FAILED
    else:
        overall = LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW
    return LeaderBusinessMaterialReviewQueue(
        status=overall,
        candidate_plan_id=candidate_plan.candidate_set_id,
        candidate_count=len(expected),
        items=items,
    )
