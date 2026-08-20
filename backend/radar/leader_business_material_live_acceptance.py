"""阶段6候选全集官方主营材料的一键只读验收编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.leader_business_material_batch_collection import (
    collect_leader_business_material_review_queue,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_runtime_candidate_plan import (
    is_leader_runtime_candidate_plan_valid,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceResult,
    LeaderTradabilityLiveAcceptanceStatus,
)
from radar.sources.leader_risk_official import (
    CninfoIssuerResolutionStatus,
    CninfoIssuerRosterResolutionResult,
    fetch_cninfo_issuer_scopes_from_roster,
)


LEADER_BUSINESS_MATERIAL_LIVE_ACCEPTANCE_CONTRACT_ID = (
    "radar-leader-business-material-live-acceptance-v1"
)


class LeaderBusinessMaterialLiveAcceptanceStatus(str, Enum):
    COMPLETED = "completed"
    NOT_READY = "not_ready"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


IssuerScopeLoader = Callable[..., Any]
MaterialCollector = Callable[..., Any]


def _empty_summary() -> Mapping[str, int]:
    return MappingProxyType({
        "pendingReview": 0,
        "missing": 0,
        "sourceFailed": 0,
        "sourceUnverified": 0,
    })


@dataclass(frozen=True, repr=False)
class LeaderBusinessMaterialLiveAcceptanceResult:
    status: LeaderBusinessMaterialLiveAcceptanceStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    candidate_plan_id: Optional[str]
    candidate_count: int
    reasons: Tuple[str, ...] = ()
    issuer_status: Optional[str] = None
    queue_status: Optional[str] = None
    review_summary: Mapping[str, int] = field(default_factory=_empty_summary)
    review_queue: Optional[LeaderBusinessMaterialReviewQueue] = field(
        default=None,
        repr=False,
    )
    contract_id: str = LEADER_BUSINESS_MATERIAL_LIVE_ACCEPTANCE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def __repr__(self) -> str:
        return (
            "LeaderBusinessMaterialLiveAcceptanceResult("
            f"status={self.status.value!r}, "
            f"candidate_count={self.candidate_count!r})"
        )

    def to_evidence(self) -> Mapping[str, object]:
        """输出批次级脱敏证据；候选和文档明细只留在本地报告。"""

        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "issuerStatus": self.issuer_status,
            "queueStatus": self.queue_status,
            "reviewSummary": dict(self.review_summary),
            "reasons": list(self.reasons),
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
    status: LeaderBusinessMaterialLiveAcceptanceStatus,
    tradability: Any,
    *,
    reasons: Sequence[str] = (),
    issuer_status: Optional[str] = None,
    queue: Optional[LeaderBusinessMaterialReviewQueue] = None,
) -> LeaderBusinessMaterialLiveAcceptanceResult:
    plan = (
        tradability.candidate_plan
        if isinstance(tradability, LeaderTradabilityLiveAcceptanceResult)
        else None
    )
    summary = dict(_empty_summary())
    if queue is not None:
        keys = {
            LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW: (
                "pendingReview"
            ),
            LeaderBusinessMaterialReviewItemStatus.MISSING: "missing",
            LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED: (
                "sourceFailed"
            ),
            LeaderBusinessMaterialReviewItemStatus.SOURCE_UNVERIFIED: (
                "sourceUnverified"
            ),
        }
        for item in queue.items:
            summary[keys[item.status]] += 1
    return LeaderBusinessMaterialLiveAcceptanceResult(
        status=status,
        radar_run_id=(
            tradability.radar_run_id
            if isinstance(tradability, LeaderTradabilityLiveAcceptanceResult)
            else None
        ),
        as_of=(
            tradability.as_of
            if isinstance(tradability, LeaderTradabilityLiveAcceptanceResult)
            else None
        ),
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        candidate_count=(plan.candidate_count if plan is not None else 0),
        reasons=_dedupe(reasons),
        issuer_status=issuer_status,
        queue_status=queue.status.value if queue is not None else None,
        review_summary=MappingProxyType(summary),
        review_queue=queue,
    )


def run_leader_business_material_live_acceptance(
    tradability: Any,
    *,
    window_from: Any,
    issuer_scope_loader: IssuerScopeLoader = (
        fetch_cninfo_issuer_scopes_from_roster
    ),
    material_collector: MaterialCollector = (
        collect_leader_business_material_review_queue
    ),
) -> LeaderBusinessMaterialLiveAcceptanceResult:
    """串接同轮候选、发行人名册与官方材料发现，不改变正式状态。"""

    if not isinstance(tradability, LeaderTradabilityLiveAcceptanceResult):
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("tradability_acceptance_contract_unverified",),
        )
    if tradability.status is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED:
        statuses = {
            LeaderTradabilityLiveAcceptanceStatus.NOT_READY: (
                LeaderBusinessMaterialLiveAcceptanceStatus.NOT_READY
            ),
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED: (
                LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED
            ),
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED: (
                LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED
            ),
        }
        return _result(
            statuses[tradability.status],
            tradability,
            reasons=(
                "tradability_acceptance_not_completed",
                *tradability.reasons,
            ),
        )
    plan = tradability.candidate_plan
    if (
        plan is None
        or not is_leader_runtime_candidate_plan_valid(plan)
        or tradability.as_of is None
        or not isinstance(window_from, date)
        or isinstance(window_from, datetime)
        or window_from > tradability.as_of.date()
        or not callable(issuer_scope_loader)
        or not callable(material_collector)
    ):
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("business_material_acceptance_contract_unverified",),
        )
    symbols = tuple(item.symbol for item in plan.items)
    try:
        issuer_result = issuer_scope_loader(
            symbols,
            fetched_at=tradability.as_of,
        )
    except Exception:
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED,
            tradability,
            reasons=("cninfo_issuer_roster_loader_failed",),
            issuer_status=CninfoIssuerResolutionStatus.SOURCE_FAILED.value,
        )
    if type(issuer_result) is not CninfoIssuerRosterResolutionResult:
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("cninfo_issuer_roster_result_unverified",),
        )
    if issuer_result.status is not CninfoIssuerResolutionStatus.READY:
        status = (
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED
            if issuer_result.status is CninfoIssuerResolutionStatus.SOURCE_FAILED
            else LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED
        )
        return _result(
            status,
            tradability,
            reasons=issuer_result.reasons,
            issuer_status=issuer_result.status.value,
        )
    try:
        queue = material_collector(
            plan,
            issuer_scopes=issuer_result.scopes,
            window_from=window_from,
        )
    except Exception:
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED,
            tradability,
            reasons=("business_material_collector_failed",),
            issuer_status=issuer_result.status.value,
        )
    if (
        type(queue) is not LeaderBusinessMaterialReviewQueue
        or queue.candidate_plan_id != plan.candidate_set_id
        or queue.candidate_count != plan.candidate_count
    ):
        return _result(
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED,
            tradability,
            reasons=("business_material_review_queue_unverified",),
            issuer_status=issuer_result.status.value,
        )
    queue_reasons = (
        *queue.reasons,
        *(
            reason
            for item in queue.items
            for reason in item.reasons
        ),
    )
    if queue.status in {
        LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
        LeaderBusinessMaterialReviewQueueStatus.MISSING,
    }:
        status = LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
    elif queue.status is LeaderBusinessMaterialReviewQueueStatus.SOURCE_FAILED:
        status = LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED
    else:
        status = LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_UNVERIFIED
    return _result(
        status,
        tradability,
        reasons=queue_reasons,
        issuer_status=issuer_result.status.value,
        queue=queue,
    )
