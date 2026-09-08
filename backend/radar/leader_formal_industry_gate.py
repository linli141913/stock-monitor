"""阶段6正式行业门的冻结证据合同。

本模块只证明同轮可信行业状态、行业规则证据和父池横截面证据已经
齐备。行业门的最终通过阈值尚未完成版本化批准，因此即使证据齐备也
保持正式门和状态迁移关闭。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_evidence_candidate_plan import (
    LeaderActiveIndustryState,
    is_leader_evidence_candidate_industry_scope_valid,
    is_leader_evidence_candidate_industry_scope_valid_for_descendant,
)
from radar.leader_formal_research_batch import (
    LeaderFormalResearchBatchResult,
    LeaderFormalResearchBatchStatus,
)
from radar.leader_research_single_pass_orchestration import (
    LeaderResearchSinglePassResult,
    LeaderResearchSinglePassStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sector_rule_readiness import SectorThresholdApprovalEvidence


LEADER_FORMAL_INDUSTRY_GATE_EVIDENCE_CONTRACT_ID = (
    "radar-leader-formal-industry-gate-evidence-v1"
)
INPUT_UNVERIFIED = "leader_formal_industry_gate_input_unverified"
POLICY_UNAPPROVED = "leader_formal_industry_gate_policy_unapproved"
POLICY_INPUT_UNVERIFIED = (
    "leader_formal_industry_gate_policy_input_unverified"
)
SECTOR_STATE_APPROVAL_NOT_APPLICABLE = (
    "leader_formal_industry_gate_sector_state_approval_not_applicable"
)
LEADER_FORMAL_INDUSTRY_GATE_POLICY_AUDIT_CONTRACT_ID = (
    "radar-leader-formal-industry-gate-policy-audit-v1"
)
DOCUMENTED_QUALITATIVE_REQUIREMENTS = (
    "industry_state_active",
    "not_single_stock_advance",
    "industry_turnover_qualified",
    "diffusion_persistence_or_reflux",
    "catalyst_source_reliable",
    "data_complete",
)
POLICY_REQUIREMENT_SOURCE_REFERENCES = (
    "docs/股票监测助手V5.0升级规划书.md::7.4评分计算合同",
    "docs/股票监测助手V5.0升级规划书.md::9.5硬门槛/行业门槛",
    "docs/股票监测助手V5.0升级规划书.md::9.8状态机正式合同",
)
REQUIRED_POLICY_FIELDS = (
    "metric_fields_and_units",
    "primary_and_fallback_sources",
    "source_time_and_max_latency",
    "statistics_window",
    "minimum_sample_size",
    "normalization_formula_and_bounds",
    "single_stock_concentration_threshold",
    "industry_turnover_threshold",
    "diffusion_persistence_reflux_rule",
    "catalyst_source_admission_rule",
    "minimum_data_completeness",
    "missing_stale_conflict_policy",
    "degraded_calculation_policy",
    "entry_hold_exit_thresholds",
    "approval_identity_and_time",
)


class LeaderFormalIndustryGateEvidenceStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    BLOCKED = "blocked"


class LeaderFormalIndustryGatePolicyAuditStatus(str, Enum):
    UNAPPROVED = "unapproved"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class LeaderFormalIndustryGatePolicyAudit:
    status: LeaderFormalIndustryGatePolicyAuditStatus
    qualitative_requirements: Tuple[str, ...]
    requirement_source_references: Tuple[str, ...]
    required_policy_fields: Tuple[str, ...]
    observed_approval_contract_id: Optional[str]
    observed_approval_id: Optional[str]
    reasons: Tuple[str, ...]
    contract_id: str = (
        LEADER_FORMAL_INDUSTRY_GATE_POLICY_AUDIT_CONTRACT_ID
    )
    approved: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "qualitativeRequirements": list(
                self.qualitative_requirements
            ),
            "requirementSourceReferences": list(
                self.requirement_source_references
            ),
            "requiredPolicyFields": list(self.required_policy_fields),
            "observedApprovalContractId": (
                self.observed_approval_contract_id
            ),
            "observedApprovalId": self.observed_approval_id,
            "reasons": list(self.reasons),
            "approved": False,
        }


@dataclass(frozen=True)
class LeaderFormalIndustryGateEvidenceItem:
    index: int
    symbol: str
    industry_code: str
    industry_state: LeaderActiveIndustryState
    sector_rule_evidence_ready: bool
    cross_section_evidence_ready: bool
    evidence_ready: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    formal_gate_passed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "industryCode": self.industry_code,
            "industryState": self.industry_state.value,
            "sectorRuleEvidenceReady": self.sector_rule_evidence_ready,
            "crossSectionEvidenceReady": (
                self.cross_section_evidence_ready
            ),
            "evidenceReady": self.evidence_ready,
            "reasons": list(self.reasons),
            "formalGatePassed": False,
        }


@dataclass(frozen=True)
class LeaderFormalIndustryGateEvidenceResult:
    status: LeaderFormalIndustryGateEvidenceStatus
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    parent_candidate_plan_id: Optional[str]
    candidate_plan_id: Optional[str]
    industry_scope_snapshot_id: Optional[str]
    policy_audit: LeaderFormalIndustryGatePolicyAudit
    items: Tuple[LeaderFormalIndustryGateEvidenceItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_FORMAL_INDUSTRY_GATE_EVIDENCE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def candidate_count(self) -> int:
        return len(self.items)

    @property
    def evidence_ready_count(self) -> int:
        return sum(item.evidence_ready for item in self.items)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "parentCandidatePlanId": self.parent_candidate_plan_id,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "industryScopeSnapshotId": self.industry_scope_snapshot_id,
            "evidenceReadyCount": self.evidence_ready_count,
            "policyAudit": self.policy_audit.to_evidence(),
            "items": [item.to_evidence() for item in self.items],
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _build_policy_audit(
    policy_approval: Any,
) -> LeaderFormalIndustryGatePolicyAudit:
    if policy_approval is None:
        return LeaderFormalIndustryGatePolicyAudit(
            status=(
                LeaderFormalIndustryGatePolicyAuditStatus.UNAPPROVED
            ),
            qualitative_requirements=(
                DOCUMENTED_QUALITATIVE_REQUIREMENTS
            ),
            requirement_source_references=(
                POLICY_REQUIREMENT_SOURCE_REFERENCES
            ),
            required_policy_fields=REQUIRED_POLICY_FIELDS,
            observed_approval_contract_id=None,
            observed_approval_id=None,
            reasons=(POLICY_UNAPPROVED,),
        )
    if type(policy_approval) is SectorThresholdApprovalEvidence:
        return LeaderFormalIndustryGatePolicyAudit(
            status=(
                LeaderFormalIndustryGatePolicyAuditStatus.UNAPPROVED
            ),
            qualitative_requirements=(
                DOCUMENTED_QUALITATIVE_REQUIREMENTS
            ),
            requirement_source_references=(
                POLICY_REQUIREMENT_SOURCE_REFERENCES
            ),
            required_policy_fields=REQUIRED_POLICY_FIELDS,
            observed_approval_contract_id=policy_approval.contract_id,
            observed_approval_id=policy_approval.approval_id,
            reasons=(
                SECTOR_STATE_APPROVAL_NOT_APPLICABLE,
                POLICY_UNAPPROVED,
            ),
        )
    return LeaderFormalIndustryGatePolicyAudit(
        status=(
            LeaderFormalIndustryGatePolicyAuditStatus.SOURCE_UNVERIFIED
        ),
        qualitative_requirements=DOCUMENTED_QUALITATIVE_REQUIREMENTS,
        requirement_source_references=(
            POLICY_REQUIREMENT_SOURCE_REFERENCES
        ),
        required_policy_fields=REQUIRED_POLICY_FIELDS,
        observed_approval_contract_id=None,
        observed_approval_id=None,
        reasons=(POLICY_INPUT_UNVERIFIED,),
    )


def _blocked(
    plan: Any,
    *,
    policy_audit: LeaderFormalIndustryGatePolicyAudit,
    reason: str = INPUT_UNVERIFIED,
) -> LeaderFormalIndustryGateEvidenceResult:
    valid = (
        plan
        if isinstance(plan, LeaderRuntimeCandidatePlan)
        and is_leader_runtime_candidate_plan_valid(plan)
        else None
    )
    return LeaderFormalIndustryGateEvidenceResult(
        status=LeaderFormalIndustryGateEvidenceStatus.BLOCKED,
        radar_run_id=valid.radar_run_id if valid else None,
        as_of=valid.as_of if valid else None,
        parent_candidate_plan_id=(
            valid.candidate_set_id if valid else None
        ),
        candidate_plan_id=None,
        industry_scope_snapshot_id=None,
        policy_audit=policy_audit,
        reasons=(reason,),
    )


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def build_leader_formal_industry_gate_evidence(
    parent_plan: Any,
    *,
    candidate_plan: Any,
    industry_scope: Any,
    single_pass: Any,
    policy_approval: Any = None,
) -> LeaderFormalIndustryGateEvidenceResult:
    """绑定可信行业状态与父池横截面；不判定正式通过。"""

    policy_audit = _build_policy_audit(policy_approval)
    if (
        policy_audit.status
        is LeaderFormalIndustryGatePolicyAuditStatus.SOURCE_UNVERIFIED
    ):
        return _blocked(
            parent_plan,
            policy_audit=policy_audit,
            reason=POLICY_INPUT_UNVERIFIED,
        )

    try:
        inputs_valid = all((
            is_leader_runtime_candidate_plan_valid(parent_plan),
            is_leader_runtime_candidate_plan_valid(candidate_plan),
            candidate_plan.parent_candidate_set_id
            == parent_plan.candidate_set_id,
            candidate_plan.radar_run_id == parent_plan.radar_run_id,
            candidate_plan.as_of == parent_plan.as_of,
            (
                is_leader_evidence_candidate_industry_scope_valid(
                    industry_scope,
                    plan=parent_plan,
                )
                or is_leader_evidence_candidate_industry_scope_valid_for_descendant(
                    industry_scope,
                    plan=parent_plan,
                )
            ),
            type(single_pass) is LeaderResearchSinglePassResult,
            single_pass.status in (
                LeaderResearchSinglePassStatus.READY,
                LeaderResearchSinglePassStatus.PARTIAL,
            ),
            single_pass.radar_run_id == candidate_plan.radar_run_id,
            single_pass.as_of == candidate_plan.as_of,
            single_pass.candidate_plan_id
            == candidate_plan.candidate_set_id,
            type(single_pass.formal_research_result)
            is LeaderFormalResearchBatchResult,
            len(single_pass.formal_research_result.items)
            == candidate_plan.candidate_count,
        ))
    except (AttributeError, KeyError, TypeError, ValueError):
        inputs_valid = False
    if not inputs_valid:
        return _blocked(parent_plan, policy_audit=policy_audit)

    scope_by_code = {
        item.industry_code: item.state
        for item in industry_scope.items
    }
    formal_items = single_pass.formal_research_result.items
    items = []
    for index, (plan_item, formal) in enumerate(zip(
        candidate_plan.items,
        formal_items,
    )):
        if (
            formal.index != index
            or formal.symbol != plan_item.symbol
            or formal.industry_code != plan_item.industry_code
            or plan_item.industry_code not in scope_by_code
        ):
            return _blocked(parent_plan, policy_audit=policy_audit)
        sector = formal.item("sector_formal_gate")
        cross_section = formal.item("cross_section")
        sector_ready = (
            sector.status is LeaderFormalResearchBatchStatus.READY
        )
        cross_section_ready = (
            cross_section.status is LeaderFormalResearchBatchStatus.READY
        )
        state = scope_by_code[plan_item.industry_code]
        active = state in {
            LeaderActiveIndustryState.OBSERVE,
            LeaderActiveIndustryState.STARTUP,
            LeaderActiveIndustryState.CONFIRMED,
        }
        evidence_ready = bool(
            active and sector_ready and cross_section_ready
        )
        reasons = (
            (POLICY_UNAPPROVED,)
            if evidence_ready
            else _dedupe((
                *sector.reasons,
                *cross_section.reasons,
                (
                    "leader_formal_industry_state_inactive"
                    if not active else ""
                ),
            ))
        )
        items.append(LeaderFormalIndustryGateEvidenceItem(
            index=index,
            symbol=plan_item.symbol,
            industry_code=plan_item.industry_code,
            industry_state=state,
            sector_rule_evidence_ready=sector_ready,
            cross_section_evidence_ready=cross_section_ready,
            evidence_ready=evidence_ready,
            reasons=reasons,
        ))
    ready = all(item.evidence_ready for item in items)
    return LeaderFormalIndustryGateEvidenceResult(
        status=(
            LeaderFormalIndustryGateEvidenceStatus.READY
            if ready
            else LeaderFormalIndustryGateEvidenceStatus.MISSING
        ),
        radar_run_id=parent_plan.radar_run_id,
        as_of=parent_plan.as_of,
        parent_candidate_plan_id=parent_plan.candidate_set_id,
        candidate_plan_id=candidate_plan.candidate_set_id,
        industry_scope_snapshot_id=industry_scope.state_snapshot_id,
        policy_audit=policy_audit,
        items=tuple(items),
        reasons=_dedupe(tuple(
            reason for item in items for reason in item.reasons
        )),
    )
