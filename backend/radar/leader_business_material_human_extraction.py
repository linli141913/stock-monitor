"""把人工核对的官方主营事实转换为现有官方材料工件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Tuple

from radar.leader_business_catalyst_features import BusinessProofType
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialBusinessMaterialBatchEntry,
    LeaderOfficialBusinessProofArtifact,
    LeaderOfficialCatalystArtifact,
    OfficialDisclosurePlatform,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


class LeaderBusinessMaterialHumanExtractionStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderBusinessMaterialHumanExtractionEntry:
    symbol: str
    reviewer_key: str
    reviewed_at: datetime
    catalyst_artifact: LeaderOfficialCatalystArtifact = field(repr=False)
    proof_document_id: str
    proof_type: BusinessProofType
    fact_summary: str


@dataclass(frozen=True)
class LeaderBusinessMaterialHumanExtractionBatch:
    status: LeaderBusinessMaterialHumanExtractionStatus
    candidate_plan_id: str
    material_entries: Tuple[LeaderOfficialBusinessMaterialBatchEntry, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": "radar-leader-business-human-extraction-v1",
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "materialEntryCount": len(self.material_entries),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _blocked(plan_id: str, reason: str):
    return LeaderBusinessMaterialHumanExtractionBatch(
        status=LeaderBusinessMaterialHumanExtractionStatus.BLOCKED,
        candidate_plan_id=plan_id,
        reasons=(reason,),
    )


def build_leader_business_material_human_extraction_batch(
    candidate_plan: Any,
    review_queue: Any,
    extractions: Any,
    *,
    validated_at: Any = None,
) -> LeaderBusinessMaterialHumanExtractionBatch:
    actual_validated_at = (
        candidate_plan.as_of
        if validated_at is None
        and isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        else validated_at
    )
    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or type(review_queue) is not LeaderBusinessMaterialReviewQueue
        or review_queue.candidate_plan_id != candidate_plan.candidate_set_id
        or review_queue.status == LeaderBusinessMaterialReviewQueueStatus.BLOCKED
        or not isinstance(extractions, tuple)
        or any(type(item) is not LeaderBusinessMaterialHumanExtractionEntry for item in extractions)
        or not _aware(actual_validated_at)
    ):
        return _blocked("", "business_material_human_extraction_contract_unverified")
    expected = tuple(item.symbol for item in candidate_plan.items)
    symbols = tuple(item.symbol for item in extractions)
    if len(symbols) != len(set(symbols)) or any(symbol not in expected for symbol in symbols):
        return _blocked(
            candidate_plan.candidate_set_id,
            "business_material_human_extraction_candidate_mismatch",
        )
    extraction_by_symbol = {item.symbol: item for item in extractions}
    queue_by_symbol = review_queue.items_by_symbol
    material_entries = []
    ready_count = 0
    for plan_item in candidate_plan.items:
        extraction = extraction_by_symbol.get(plan_item.symbol)
        queue_item = queue_by_symbol.get(plan_item.symbol)
        if extraction is None:
            material_entries.append(LeaderOfficialBusinessMaterialBatchEntry(
                symbol=plan_item.symbol,
                catalyst_artifact=None,
                proof_artifacts=(),
                source_status=ResearchFeatureStatus.MISSING,
            ))
            continue
        if (
            queue_item is None
            or queue_item.status != LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
            or not isinstance(extraction.reviewer_key, str)
            or not extraction.reviewer_key.strip()
            or not _aware(extraction.reviewed_at)
            or extraction.reviewed_at > actual_validated_at
            or not isinstance(extraction.proof_type, BusinessProofType)
            or not isinstance(extraction.fact_summary, str)
            or not extraction.fact_summary.strip()
            or type(extraction.catalyst_artifact) is not LeaderOfficialCatalystArtifact
            or extraction.catalyst_artifact.industry_code != plan_item.industry_code
            or extraction.catalyst_artifact.industry_release_id != plan_item.industry_release_id
        ):
            return _blocked(
                candidate_plan.candidate_set_id,
                "business_material_human_extraction_unverified",
            )
        document = next(
            (
                item for item in queue_item.documents
                if item.document_id == extraction.proof_document_id
            ),
            None,
        )
        if document is None or document.symbol != plan_item.symbol:
            return _blocked(
                candidate_plan.candidate_set_id,
                "business_material_human_extraction_document_unverified",
            )
        if (
            not _aware(document.published_at)
            or not _aware(extraction.catalyst_artifact.published_at)
            or extraction.reviewed_at < document.published_at
            or extraction.reviewed_at
            < extraction.catalyst_artifact.published_at
        ):
            return _blocked(
                candidate_plan.candidate_set_id,
                "business_material_human_extraction_time_unverified",
            )
        proof = LeaderOfficialBusinessProofArtifact(
            platform=OfficialDisclosurePlatform.CNINFO,
            source_contract_id="radar-leader-business-official-cninfo-v1",
            evidence_id=f"business-proof:{document.document_id}",
            evidence_version=document.document_version,
            symbol=plan_item.symbol,
            proof_type=extraction.proof_type,
            document_id=document.document_id,
            document_version=document.document_version,
            source_url=document.source_url,
            published_at=document.published_at,
            effective_from=document.published_at,
            effective_until=None,
            fact_summary=extraction.fact_summary,
        )
        material_entries.append(LeaderOfficialBusinessMaterialBatchEntry(
            symbol=plan_item.symbol,
            catalyst_artifact=extraction.catalyst_artifact,
            proof_artifacts=(proof,),
            source_status=ResearchFeatureStatus.READY,
        ))
        ready_count += 1
    return LeaderBusinessMaterialHumanExtractionBatch(
        status=(
            LeaderBusinessMaterialHumanExtractionStatus.READY
            if ready_count == len(expected)
            else LeaderBusinessMaterialHumanExtractionStatus.MISSING
        ),
        candidate_plan_id=candidate_plan.candidate_set_id,
        material_entries=tuple(material_entries),
    )
