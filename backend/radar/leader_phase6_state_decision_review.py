"""阶段6资格结果到状态决策前审查输入的只读绑定。

本模块保留父核验池的完整分区，并把既有F6研究批次的首次否决原因
绑定到合格候选。它不生成评分、状态迁移或数据库写入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_evidence_candidate_plan import (
    LeaderActiveIndustryState,
    is_leader_evidence_candidate_plan_valid,
)
from radar.leader_evidence_qualification import (
    LeaderEvidenceQualificationExcludedItem,
    LeaderEvidenceQualificationResult,
    LeaderEvidenceQualificationStatus,
)
from radar.leader_formal_research_batch import (
    LeaderFormalResearchBatchResult,
    LeaderFormalResearchCandidate,
)
from radar.leader_formal_industry_gate import (
    LEADER_FORMAL_INDUSTRY_GATE_EVIDENCE_CONTRACT_ID,
    LeaderFormalIndustryGateEvidenceItem,
    LeaderFormalIndustryGateEvidenceResult,
    LeaderFormalIndustryGateEvidenceStatus,
)
from radar.leader_research_readiness_audit import (
    FORMAL_EVALUATION_DISABLED,
)
from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassResult,
    LeaderResearchSinglePassStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


LEADER_PHASE6_STATE_DECISION_REVIEW_CONTRACT_ID = (
    "radar-leader-phase6-state-decision-review-v1"
)
PARTITION_UNVERIFIED = "leader_phase6_state_review_partition_unverified"
INPUT_UNVERIFIED = "leader_phase6_state_review_input_unverified"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class LeaderPhase6StateDecisionReviewStatus(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"


class LeaderPhase6StateDecisionQualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class LeaderPhase6StateDecisionReviewItem:
    index: int
    symbol: str
    industry_code: str
    qualification_status: LeaderPhase6StateDecisionQualificationStatus
    qualified_candidate_index: Optional[int]
    review_eligible: bool
    first_rejection_reason: str
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "industryCode": self.industry_code,
            "qualificationStatus": self.qualification_status.value,
            "qualifiedCandidateIndex": self.qualified_candidate_index,
            "reviewEligible": self.review_eligible,
            "firstRejectionReason": self.first_rejection_reason,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LeaderPhase6StateDecisionReviewResult:
    status: LeaderPhase6StateDecisionReviewStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    parent_candidate_plan_id: Optional[str]
    parent_candidate_count: int
    qualified_candidate_plan_id: Optional[str]
    qualification_id: Optional[str]
    items: Tuple[LeaderPhase6StateDecisionReviewItem, ...] = ()
    industry_gate_evidence: Any = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_PHASE6_STATE_DECISION_REVIEW_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def qualified_candidate_count(self) -> int:
        return sum(
            item.qualification_status
            is LeaderPhase6StateDecisionQualificationStatus.QUALIFIED
            for item in self.items
        )

    @property
    def excluded_candidate_count(self) -> int:
        return sum(
            item.qualification_status
            is LeaderPhase6StateDecisionQualificationStatus.EXCLUDED
            for item in self.items
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "parentCandidatePlanId": self.parent_candidate_plan_id,
            "parentCandidateCount": self.parent_candidate_count,
            "qualifiedCandidatePlanId": self.qualified_candidate_plan_id,
            "qualifiedCandidateCount": self.qualified_candidate_count,
            "excludedCandidateCount": self.excluded_candidate_count,
            "qualificationId": self.qualification_id,
            "industryGateEvidence": (
                self.industry_gate_evidence.to_evidence()
                if self.industry_gate_evidence is not None
                else None
            ),
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _blocked(
    plan: Any,
    reason: str,
) -> LeaderPhase6StateDecisionReviewResult:
    valid_plan = (
        plan
        if isinstance(plan, LeaderRuntimeCandidatePlan)
        and is_leader_runtime_candidate_plan_valid(plan)
        else None
    )
    return LeaderPhase6StateDecisionReviewResult(
        status=LeaderPhase6StateDecisionReviewStatus.BLOCKED,
        radar_run_id=(valid_plan.radar_run_id if valid_plan else None),
        as_of=(valid_plan.as_of if valid_plan else None),
        parent_candidate_plan_id=(
            valid_plan.candidate_set_id if valid_plan else None
        ),
        parent_candidate_count=(
            valid_plan.candidate_count if valid_plan else 0
        ),
        qualified_candidate_plan_id=None,
        qualification_id=None,
        reasons=(reason,),
    )


def _valid_reason_tuple(value: Any, *, require: bool = False) -> bool:
    return bool(
        isinstance(value, tuple)
        and (not require or value)
        and all(isinstance(item, str) and item for item in value)
    )


def _single_pass_candidates(
    value: Any,
    *,
    child_plan: LeaderRuntimeCandidatePlan,
) -> Optional[Tuple[LeaderFormalResearchCandidate, ...]]:
    if (
        type(value) is not LeaderResearchSinglePassResult
        or value.status not in (
            LeaderResearchSinglePassStatus.READY,
            LeaderResearchSinglePassStatus.PARTIAL,
        )
        or value.radar_run_id != child_plan.radar_run_id
        or value.as_of != child_plan.as_of
        or value.candidate_plan_id != child_plan.candidate_set_id
        or value.candidate_count != child_plan.candidate_count
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
        or type(value.formal_research_result)
        is not LeaderFormalResearchBatchResult
    ):
        return None
    formal = value.formal_research_result
    if (
        formal.radar_run_id != child_plan.radar_run_id
        or formal.as_of != child_plan.as_of
        or formal.candidate_plan_id != child_plan.candidate_set_id
        or formal.candidate_count != child_plan.candidate_count
        or len(formal.items) != child_plan.candidate_count
        or any((
            formal.formal_score_ready is not False,
            formal.formal_gate_ready is not False,
            formal.formal_usable is not False,
            formal.state_transition_allowed is not False,
        ))
        or any(
            type(candidate) is not LeaderFormalResearchCandidate
            or candidate.index != index
            or candidate.symbol != child_plan.items[index].symbol
            or candidate.industry_code
            != child_plan.items[index].industry_code
            or any((
                candidate.formal_score_ready is not False,
                candidate.formal_gate_ready is not False,
                candidate.formal_usable is not False,
                candidate.state_transition_allowed is not False,
            ))
            for index, candidate in enumerate(formal.items)
        )
    ):
        return None
    return formal.items


def _industry_gate_items(
    value: Any,
    *,
    parent_plan: LeaderRuntimeCandidatePlan,
    child_plan: LeaderRuntimeCandidatePlan,
) -> Optional[Tuple[LeaderFormalIndustryGateEvidenceItem, ...]]:
    if value is None:
        return ()
    if (
        type(value) is not LeaderFormalIndustryGateEvidenceResult
        or value.contract_id
        != LEADER_FORMAL_INDUSTRY_GATE_EVIDENCE_CONTRACT_ID
        or value.status not in (
            LeaderFormalIndustryGateEvidenceStatus.READY,
            LeaderFormalIndustryGateEvidenceStatus.MISSING,
        )
        or value.parent_candidate_plan_id != parent_plan.candidate_set_id
        or value.candidate_plan_id != child_plan.candidate_set_id
        or value.radar_run_id != parent_plan.radar_run_id
        or value.as_of != parent_plan.as_of
        or value.candidate_count != child_plan.candidate_count
        or (
            value.status is LeaderFormalIndustryGateEvidenceStatus.READY
            and value.evidence_ready_count != child_plan.candidate_count
        )
        or (
            value.status is LeaderFormalIndustryGateEvidenceStatus.MISSING
            and value.evidence_ready_count >= child_plan.candidate_count
        )
        or not isinstance(value.items, tuple)
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return None
    for index, (item, plan_item) in enumerate(zip(
        value.items,
        child_plan.items,
    )):
        if (
            type(item) is not LeaderFormalIndustryGateEvidenceItem
            or item.index != index
            or item.symbol != plan_item.symbol
            or item.industry_code != plan_item.industry_code
            or type(item.sector_rule_evidence_ready) is not bool
            or type(item.cross_section_evidence_ready) is not bool
            or type(item.evidence_ready) is not bool
            or item.evidence_ready is not bool(
                item.sector_rule_evidence_ready
                and item.cross_section_evidence_ready
                and item.industry_state in {
                    LeaderActiveIndustryState.OBSERVE,
                    LeaderActiveIndustryState.STARTUP,
                    LeaderActiveIndustryState.CONFIRMED,
                }
            )
            or item.formal_gate_passed is not False
            or not _valid_reason_tuple(item.reasons, require=True)
        ):
            return None
    return value.items


def build_leader_phase6_state_decision_review(
    parent_plan: Any,
    *,
    qualification: Any,
    single_pass: Any,
    industry_gate_evidence: Any = None,
) -> LeaderPhase6StateDecisionReviewResult:
    """绑定资格分区和既有研究首次否决原因，不执行状态机。"""

    try:
        parent_valid = is_leader_runtime_candidate_plan_valid(parent_plan)
    except (AttributeError, KeyError, TypeError, ValueError):
        parent_valid = False
    if not parent_valid:
        return _blocked(parent_plan, INPUT_UNVERIFIED)
    if (
        type(qualification) is not LeaderEvidenceQualificationResult
        or qualification.status
        is not LeaderEvidenceQualificationStatus.READY
        or qualification.parent_candidate_plan_id
        != parent_plan.candidate_set_id
        or qualification.parent_candidate_count
        != parent_plan.candidate_count
        or any((
            qualification.formal_score_ready is not False,
            qualification.formal_gate_ready is not False,
            qualification.formal_usable is not False,
            qualification.state_transition_allowed is not False,
        ))
        or _SHA256_PATTERN.fullmatch(qualification.qualification_id or "")
        is None
        or not is_leader_runtime_candidate_plan_valid(
            qualification.candidate_plan
        )
        or not is_leader_evidence_candidate_plan_valid(
            qualification.evidence_plan
        )
    ):
        return _blocked(parent_plan, INPUT_UNVERIFIED)
    child_plan = qualification.candidate_plan
    evidence_plan = qualification.evidence_plan
    if (
        child_plan.parent_candidate_set_id != parent_plan.candidate_set_id
        or evidence_plan.preliminary_candidate_plan_id
        != parent_plan.candidate_set_id
        or evidence_plan.preliminary_candidate_count
        != parent_plan.candidate_count
        or evidence_plan.candidate_plan != child_plan
    ):
        return _blocked(parent_plan, INPUT_UNVERIFIED)
    formal_candidates = _single_pass_candidates(
        single_pass,
        child_plan=child_plan,
    )
    if formal_candidates is None:
        return _blocked(parent_plan, INPUT_UNVERIFIED)
    industry_gate_items = _industry_gate_items(
        industry_gate_evidence,
        parent_plan=parent_plan,
        child_plan=child_plan,
    )
    if industry_gate_items is None:
        return _blocked(parent_plan, INPUT_UNVERIFIED)

    qualified_by_parent_index = {}
    for evidence_item, formal_candidate in zip(
        evidence_plan.items,
        formal_candidates,
    ):
        if (
            evidence_item.parent_index in qualified_by_parent_index
            or evidence_item.parent_index >= parent_plan.candidate_count
            or parent_plan.items[evidence_item.parent_index].symbol
            != evidence_item.symbol
        ):
            return _blocked(parent_plan, PARTITION_UNVERIFIED)
        qualified_by_parent_index[evidence_item.parent_index] = (
            evidence_item.index,
            formal_candidate,
        )

    excluded_by_parent_index = {}
    for excluded in qualification.excluded_items:
        if (
            type(excluded) is not LeaderEvidenceQualificationExcludedItem
            or excluded.index in excluded_by_parent_index
            or excluded.index in qualified_by_parent_index
            or excluded.index >= parent_plan.candidate_count
            or parent_plan.items[excluded.index].symbol != excluded.symbol
            or not isinstance(
                excluded.status,
                AutomaticBusinessEvidenceStatus,
            )
            or excluded.status
            is AutomaticBusinessEvidenceStatus.SOURCE_FAILED
            or not _valid_reason_tuple(excluded.reasons, require=True)
        ):
            return _blocked(parent_plan, PARTITION_UNVERIFIED)
        excluded_by_parent_index[excluded.index] = excluded
    if set(qualified_by_parent_index) | set(excluded_by_parent_index) != set(
        range(parent_plan.candidate_count)
    ):
        return _blocked(parent_plan, PARTITION_UNVERIFIED)

    items = []
    for index, parent_item in enumerate(parent_plan.items):
        qualified = qualified_by_parent_index.get(index)
        if qualified is not None:
            qualified_index, formal_candidate = qualified
            if industry_gate_items:
                gate_item = industry_gate_items[qualified_index]
                later_reason = formal_candidate.first_missing_reason
                if (
                    gate_item.cross_section_evidence_ready
                    and later_reason
                    == "leader_cross_section_evidence_unavailable"
                ):
                    later_reason = None
                reasons = tuple(dict.fromkeys((
                    *gate_item.reasons,
                    *((later_reason,) if later_reason else ()),
                )))
                first_rejection = (
                    reasons[0] if reasons else FORMAL_EVALUATION_DISABLED
                )
            else:
                first_rejection = (
                    formal_candidate.first_veto_reason
                    or formal_candidate.first_missing_reason
                    or FORMAL_EVALUATION_DISABLED
                )
                reasons = tuple(dict.fromkeys(
                    reason
                    for reason in (
                        formal_candidate.first_veto_reason,
                        formal_candidate.first_missing_reason,
                    )
                    if reason
                ))
            items.append(LeaderPhase6StateDecisionReviewItem(
                index=index,
                symbol=parent_item.symbol,
                industry_code=parent_item.industry_code,
                qualification_status=(
                    LeaderPhase6StateDecisionQualificationStatus.QUALIFIED
                ),
                qualified_candidate_index=qualified_index,
                review_eligible=True,
                first_rejection_reason=first_rejection,
                reasons=reasons,
            ))
            continue
        excluded = excluded_by_parent_index[index]
        items.append(LeaderPhase6StateDecisionReviewItem(
            index=index,
            symbol=parent_item.symbol,
            industry_code=parent_item.industry_code,
            qualification_status=(
                LeaderPhase6StateDecisionQualificationStatus.EXCLUDED
            ),
            qualified_candidate_index=None,
            review_eligible=False,
            first_rejection_reason=excluded.reasons[0],
            reasons=excluded.reasons,
        ))

    return LeaderPhase6StateDecisionReviewResult(
        status=LeaderPhase6StateDecisionReviewStatus.READY_FOR_REVIEW,
        radar_run_id=parent_plan.radar_run_id,
        as_of=parent_plan.as_of,
        parent_candidate_plan_id=parent_plan.candidate_set_id,
        parent_candidate_count=parent_plan.candidate_count,
        qualified_candidate_plan_id=child_plan.candidate_set_id,
        qualification_id=qualification.qualification_id,
        items=tuple(items),
        industry_gate_evidence=industry_gate_evidence,
    )
