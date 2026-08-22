"""把人工或确定性官方关系验证安全接入既有主营研究输入。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessCatalystReview,
    build_leader_business_catalyst_features,
)
from radar.leader_business_catalyst_manual_review import (
    LeaderOfficialBusinessManualReviewBatchStatus,
    apply_official_business_manual_reviews_batch,
)
from radar.leader_business_catalyst_official_adapter import (
    LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID,
    LeaderOfficialBusinessMaterialBatchResult,
    LeaderOfficialBusinessMaterialBatchStatus,
)
from radar.leader_business_deterministic_verification import (
    DeterministicOfficialBusinessVerificationArtifact,
    replay_deterministic_official_business_verification,
)
from radar.leader_research_features import ResearchFeatureStatus


LEADER_BUSINESS_VERIFICATION_BATCH_CONTRACT_ID = (
    "radar-leader-business-official-verification-batch-v1"
)


class LeaderOfficialBusinessVerificationBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderOfficialBusinessDeterministicVerificationBatchEntry:
    symbol: str
    verification_artifact: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderOfficialBusinessVerificationBatchItem:
    index: int
    symbol: str
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = ()
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = field(
        default=None,
        repr=False,
    )
    verification_id: Optional[str] = None


@dataclass(frozen=True)
class LeaderOfficialBusinessVerificationBatchResult:
    status: LeaderOfficialBusinessVerificationBatchStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    items: Tuple[LeaderOfficialBusinessVerificationBatchItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_BUSINESS_VERIFICATION_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def inputs_by_symbol(self) -> Mapping[str, LeaderBusinessCatalystFeatureInput]:
        return MappingProxyType({
            item.symbol: item.input_value
            for item in self.items
            if item.status is ResearchFeatureStatus.READY
            and item.input_value is not None
        })


def _batch_result(
    status: LeaderOfficialBusinessVerificationBatchStatus,
    *,
    candidate_plan_id: Optional[str],
    candidate_count: int,
    items: Sequence[LeaderOfficialBusinessVerificationBatchItem] = (),
    reasons: Sequence[str] = (),
) -> LeaderOfficialBusinessVerificationBatchResult:
    return LeaderOfficialBusinessVerificationBatchResult(
        status=status,
        candidate_plan_id=candidate_plan_id,
        candidate_count=candidate_count,
        items=tuple(items),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _material_batch_valid(value: Any) -> bool:
    return bool(
        type(value) is LeaderOfficialBusinessMaterialBatchResult
        and value.contract_id == LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID
        and value.status in {
            LeaderOfficialBusinessMaterialBatchStatus.READY,
            LeaderOfficialBusinessMaterialBatchStatus.PARTIAL,
            LeaderOfficialBusinessMaterialBatchStatus.MISSING,
        }
        and isinstance(value.items, tuple)
        and value.candidate_count == len(value.items)
        and tuple(item.index for item in value.items) == tuple(range(len(value.items)))
        and len({item.symbol for item in value.items}) == len(value.items)
    )


def _deterministic_input(
    material_item: Any,
    artifact: Any,
) -> Tuple[ResearchFeatureStatus, Tuple[str, ...], Optional[LeaderBusinessCatalystFeatureInput], Optional[str]]:
    if (
        material_item.status is not ResearchFeatureStatus.READY
        or material_item.input_value is None
        or type(artifact) is not DeterministicOfficialBusinessVerificationArtifact
    ):
        return (
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_verification_unverified",),
            None,
            None,
        )
    input_value = material_item.input_value
    replayed = replay_deterministic_official_business_verification(
        artifact,
        as_of=input_value.as_of,
    )
    if (
        replayed.status is not AutomaticBusinessEvidenceStatus.READY
        or replayed.artifact is None
        or artifact.symbol != input_value.symbol
        or artifact.industry_code != input_value.industry_code
        or artifact.industry_release_id != input_value.industry_release_id
        or artifact.catalyst_document_id != input_value.catalyst.document_id
        or artifact.catalyst_document_version
        != input_value.catalyst.document_version
    ):
        return (
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_verification_unverified",),
            None,
            None,
        )
    matching_proofs = tuple(
        proof for proof in input_value.business_proofs
        if proof.document_id == artifact.annual_document_id
        and proof.evidence_version == artifact.annual_document_version
    )
    if len(matching_proofs) != 1:
        return (
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_material_unverified",),
            None,
            None,
        )
    review = LeaderBusinessCatalystReview(
        review_id=artifact.verification_id,
        mapping_version=artifact.rule_version,
        symbol=artifact.symbol,
        industry_code=artifact.industry_code,
        industry_release_id=artifact.industry_release_id,
        catalyst_id=input_value.catalyst.catalyst_id,
        relation=artifact.relation,
        review_method="deterministic_official",
        reviewer_key=artifact.rule_version,
        reviewed_at=artifact.validated_at,
        effective_until=None,
        basis_evidence_ids=tuple(proof.evidence_id for proof in matching_proofs),
        basis_catalyst_id=input_value.catalyst.catalyst_id,
        decision_summary="官方确定性规则重放",
    )
    verified_input = replace(input_value, reviews=(review,))
    feature = build_leader_business_catalyst_features(verified_input)
    if feature.status is not ResearchFeatureStatus.READY:
        return feature.status, feature.reasons, None, None
    return (
        ResearchFeatureStatus.READY,
        ("business_deterministic_verification_ready",),
        verified_input,
        artifact.verification_id,
    )


def _from_manual_batch(manual: Any) -> LeaderOfficialBusinessVerificationBatchResult:
    status_map = {
        LeaderOfficialBusinessManualReviewBatchStatus.READY: LeaderOfficialBusinessVerificationBatchStatus.READY,
        LeaderOfficialBusinessManualReviewBatchStatus.PARTIAL: LeaderOfficialBusinessVerificationBatchStatus.PARTIAL,
        LeaderOfficialBusinessManualReviewBatchStatus.MISSING: LeaderOfficialBusinessVerificationBatchStatus.MISSING,
        LeaderOfficialBusinessManualReviewBatchStatus.BLOCKED: LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
    }
    return _batch_result(
        status_map[manual.status],
        candidate_plan_id=manual.candidate_plan_id,
        candidate_count=manual.candidate_count,
        items=tuple(LeaderOfficialBusinessVerificationBatchItem(
            index=item.index,
            symbol=item.symbol,
            status=item.status,
            reasons=item.reasons,
            input_value=item.input_value,
            verification_id=item.review_id,
        ) for item in manual.items),
        reasons=manual.reasons,
    )


def apply_official_business_verifications_batch(
    material_batch: Any,
    *,
    manual_entries: Any = None,
    deterministic_entries: Any = None,
) -> LeaderOfficialBusinessVerificationBatchResult:
    """只允许纯人工或纯确定性官方批次，禁止混合和类型伪装。"""

    if manual_entries is not None and deterministic_entries is not None:
        return _batch_result(
            LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("business_verification_batch_mixed_forbidden",),
        )
    if not _material_batch_valid(material_batch):
        return _batch_result(
            LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("business_verification_material_batch_unverified",),
        )
    if manual_entries is not None:
        return _from_manual_batch(
            apply_official_business_manual_reviews_batch(material_batch, manual_entries)
        )
    if (
        not isinstance(deterministic_entries, tuple)
        or any(
            type(entry) is not LeaderOfficialBusinessDeterministicVerificationBatchEntry
            or not isinstance(entry.symbol, str)
            or not entry.symbol
            for entry in deterministic_entries
        )
    ):
        return _batch_result(
            LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
            candidate_plan_id=material_batch.candidate_plan_id,
            candidate_count=material_batch.candidate_count,
            reasons=("business_deterministic_batch_contract_unverified",),
        )
    material_symbols = tuple(item.symbol for item in material_batch.items)
    entry_symbols = tuple(entry.symbol for entry in deterministic_entries)
    if (
        len(entry_symbols) != len(set(entry_symbols))
        or set(entry_symbols) != set(material_symbols)
    ):
        return _batch_result(
            LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
            candidate_plan_id=material_batch.candidate_plan_id,
            candidate_count=material_batch.candidate_count,
            reasons=("business_deterministic_batch_candidate_mismatch",),
        )
    by_symbol = {entry.symbol: entry for entry in deterministic_entries}
    items = []
    for material_item in material_batch.items:
        status, reasons, input_value, verification_id = _deterministic_input(
            material_item,
            by_symbol[material_item.symbol].verification_artifact,
        )
        items.append(LeaderOfficialBusinessVerificationBatchItem(
            index=material_item.index,
            symbol=material_item.symbol,
            status=status,
            reasons=reasons,
            input_value=input_value,
            verification_id=verification_id,
        ))
    if all(item.status is ResearchFeatureStatus.READY for item in items):
        status = LeaderOfficialBusinessVerificationBatchStatus.READY
        reasons = ()
    elif all(item.status is ResearchFeatureStatus.MISSING for item in items):
        status = LeaderOfficialBusinessVerificationBatchStatus.MISSING
        reasons = ("business_deterministic_batch_missing",)
    else:
        status = LeaderOfficialBusinessVerificationBatchStatus.PARTIAL
        reasons = ("business_deterministic_batch_partial",)
    return _batch_result(
        status,
        candidate_plan_id=material_batch.candidate_plan_id,
        candidate_count=material_batch.candidate_count,
        items=items,
        reasons=reasons,
    )
