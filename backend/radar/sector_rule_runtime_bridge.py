"""把显式行业正式规则证据包接到龙头单轮研究编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from radar.contracts import SectorFeatureBatch
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.sector_rule_readiness import (
    SECTOR_RULE_VERSION,
    SectorRuleReadinessResult,
    SectorRuleReadinessStatus,
    evaluate_sector_rule_readiness,
)


SECTOR_RULE_RUNTIME_SOURCE_CONTRACT_ID = (
    "radar-sector-rule-runtime-source-v1"
)
SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID = (
    "radar-sector-rule-runtime-bridge-v1"
)
SOURCE_MISSING = "sector_rule_runtime_bridge_source_missing"
SOURCE_UNVERIFIED = "sector_rule_runtime_bridge_source_unverified"
UTC = timezone.utc


class SectorRuleRuntimeBridgeStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class SectorRuleRuntimeSourceBatch:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    industry_release_id: str
    as_of: datetime
    feature_batch: Any = field(repr=False)
    classification_release: Any = field(repr=False)
    history_evidence: Any = field(repr=False)
    market_baseline_evidence: Any = field(repr=False)
    threshold_approval_evidence: Any = field(repr=False)
    rule_version: str = SECTOR_RULE_VERSION
    contract_id: str = SECTOR_RULE_RUNTIME_SOURCE_CONTRACT_ID


@dataclass(frozen=True)
class SectorRuleRuntimeBridgeResult:
    status: SectorRuleRuntimeBridgeStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    reasons: Tuple[str, ...]
    readiness_result: Optional[SectorRuleReadinessResult] = field(
        default=None,
        repr=False,
    )
    sector_rule_admission_value: Any = field(default=None, repr=False)
    contract_id: str = SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID
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
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "ruleVersion": (
                self.readiness_result.rule_version
                if self.readiness_result is not None
                else None
            ),
            "items": (
                [
                    item.to_evidence()
                    for item in self.readiness_result.items
                ]
                if self.readiness_result is not None
                else []
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


def _source_batch_bound(
    value: Any,
    *,
    context: LeaderResearchRuntimeSourceContext,
) -> bool:
    plan = context.candidate_plan
    release_ids = {
        item.industry_release_id for item in plan.items
    }
    candidate_industry_codes = {
        item.industry_code for item in plan.items
    }
    if (
        type(value) is not SectorRuleRuntimeSourceBatch
        or value.contract_id != SECTOR_RULE_RUNTIME_SOURCE_CONTRACT_ID
        or value.candidate_plan_id != plan.candidate_set_id
        or value.radar_run_id != plan.radar_run_id
        or value.quote_batch_id != plan.quote_batch_id
        or _aware_utc(value.as_of) != plan.as_of
        or len(release_ids) != 1
        or value.industry_release_id not in release_ids
        or not isinstance(value.rule_version, str)
        or not value.rule_version.strip()
        or type(value.feature_batch) is not SectorFeatureBatch
        or value.feature_batch.radar_run_id != plan.radar_run_id
        or _aware_utc(value.feature_batch.as_of) != plan.as_of
    ):
        return False
    feature_industry_codes = {
        item.division_code for item in value.feature_batch.sectors
    }
    return candidate_industry_codes <= feature_industry_codes


def build_sector_rule_runtime_bridge(
    context: LeaderResearchRuntimeSourceContext,
    *,
    source_batch: Any = None,
) -> SectorRuleRuntimeBridgeResult:
    """重放显式行业证据包；不查询、不写库、不生成分数或状态。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(SOURCE_UNVERIFIED)
    plan = context.candidate_plan
    if source_batch is None:
        return SectorRuleRuntimeBridgeResult(
            status=SectorRuleRuntimeBridgeStatus.MISSING,
            radar_run_id=plan.radar_run_id,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            reasons=(SOURCE_MISSING,),
        )
    if not _source_batch_bound(source_batch, context=context):
        return SectorRuleRuntimeBridgeResult(
            status=SectorRuleRuntimeBridgeStatus.MISSING,
            radar_run_id=plan.radar_run_id,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            reasons=(SOURCE_UNVERIFIED,),
            sector_rule_admission_value=source_batch,
        )
    try:
        readiness = evaluate_sector_rule_readiness(
            feature_batch=source_batch.feature_batch,
            classification_release=source_batch.classification_release,
            history_evidence=source_batch.history_evidence,
            market_baseline_evidence=(
                source_batch.market_baseline_evidence
            ),
            threshold_approval_evidence=(
                source_batch.threshold_approval_evidence
            ),
            rule_version=source_batch.rule_version,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return SectorRuleRuntimeBridgeResult(
            status=SectorRuleRuntimeBridgeStatus.MISSING,
            radar_run_id=plan.radar_run_id,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            reasons=(SOURCE_UNVERIFIED,),
            sector_rule_admission_value=source_batch,
        )
    return SectorRuleRuntimeBridgeResult(
        status=(
            SectorRuleRuntimeBridgeStatus.READY
            if readiness.status == SectorRuleReadinessStatus.READY
            else SectorRuleRuntimeBridgeStatus.MISSING
        ),
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=plan.candidate_count,
        reasons=readiness.reasons,
        readiness_result=readiness,
        sector_rule_admission_value=readiness,
    )
