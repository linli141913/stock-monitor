"""阶段6官方主营材料的版本化人工复核适配器。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessCatalystReview,
    build_leader_business_catalyst_features,
)
from radar.leader_business_catalyst_official_adapter import (
    LEADER_BUSINESS_OFFICIAL_MATERIAL_ADAPTER_CONTRACT_ID,
    LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID,
    OFFICIAL_BUSINESS_SOURCE_CONTRACTS,
    LeaderOfficialBusinessMaterialBatchItem,
    LeaderOfficialBusinessMaterialBatchResult,
    LeaderOfficialBusinessMaterialBatchStatus,
    LeaderOfficialMaterialAdapterResult,
)
from radar.leader_research_features import ResearchFeatureStatus


LEADER_BUSINESS_MANUAL_REVIEW_ADAPTER_CONTRACT_ID = (
    "radar-leader-business-manual-review-adapter-v1"
)
LEADER_BUSINESS_MANUAL_REVIEW_BATCH_CONTRACT_ID = (
    "radar-leader-business-manual-review-batch-v1"
)
UTC = timezone.utc
SAFE_REVIEWER_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,80}")


class ManualReviewerKind(str, Enum):
    HUMAN = "human"


@dataclass(frozen=True)
class LeaderOfficialBusinessManualReviewArtifact:
    review_version: str
    supersedes_review_version: Optional[str]
    symbol: str
    industry_code: str
    industry_release_id: str
    catalyst_id: str
    relation: BusinessCatalystRelation
    reviewer_kind: ManualReviewerKind
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    basis_evidence_ids: Tuple[str, ...]
    basis_catalyst_id: str
    decision_summary: str = field(repr=False)


@dataclass(frozen=True)
class LeaderOfficialBusinessManualReviewResult:
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = field(
        default=None,
        repr=False,
    )
    review_id: Optional[str] = None
    contract_id: str = (
        LEADER_BUSINESS_MANUAL_REVIEW_ADAPTER_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "reviewId": self.review_id,
            "inputReady": self.input_value is not None,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


class LeaderOfficialBusinessManualReviewBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderOfficialBusinessManualReviewBatchEntry:
    symbol: str
    review_artifact: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderOfficialBusinessManualReviewBatchItem:
    index: int
    symbol: str
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = field(
        default=None,
        repr=False,
    )
    review_id: Optional[str] = None

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "reviewId": self.review_id,
            "inputReady": self.input_value is not None,
        }


@dataclass(frozen=True)
class LeaderOfficialBusinessManualReviewBatchResult:
    status: LeaderOfficialBusinessManualReviewBatchStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    items: Tuple[LeaderOfficialBusinessManualReviewBatchItem, ...] = field(
        default_factory=tuple,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = LEADER_BUSINESS_MANUAL_REVIEW_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderBusinessCatalystFeatureInput]:
        return MappingProxyType({
            item.symbol: item.input_value
            for item in self.items
            if (
                item.status == ResearchFeatureStatus.READY
                and item.input_value is not None
            )
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = None,
    review_id: Optional[str] = None,
) -> LeaderOfficialBusinessManualReviewResult:
    return LeaderOfficialBusinessManualReviewResult(
        status=status,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        input_value=input_value,
        review_id=review_id,
    )


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _official_material_input_valid(
    material_result: Any,
) -> bool:
    if (
        type(material_result) is not LeaderOfficialMaterialAdapterResult
        or material_result.contract_id
        != LEADER_BUSINESS_OFFICIAL_MATERIAL_ADAPTER_CONTRACT_ID
        or material_result.status != ResearchFeatureStatus.READY
        or material_result.reasons
        != ("official_business_material_input_ready",)
        or material_result.input_value is None
        or type(material_result.input_value)
        is not LeaderBusinessCatalystFeatureInput
    ):
        return False
    input_value = material_result.input_value
    allowed_contract_ids = set(OFFICIAL_BUSINESS_SOURCE_CONTRACTS.values())
    if (
        input_value.reviews
        or input_value.catalyst.source_contract_id
        not in allowed_contract_ids
        or any(
            proof.source_contract_id not in allowed_contract_ids
            or proof.related_catalyst_ids
            for proof in input_value.business_proofs
        )
    ):
        return False
    feature = build_leader_business_catalyst_features(input_value)
    return bool(
        feature.status == ResearchFeatureStatus.SOURCE_UNVERIFIED
        and feature.relation == BusinessCatalystRelation.UNCONFIRMED
        and feature.reasons == ("business_relation_unconfirmed",)
    )


def _review_id(
    artifact: LeaderOfficialBusinessManualReviewArtifact,
) -> str:
    payload = {
        "reviewVersion": artifact.review_version,
        "symbol": artifact.symbol,
        "industryCode": artifact.industry_code,
        "industryReleaseId": artifact.industry_release_id,
        "catalystId": artifact.catalyst_id,
        "relation": artifact.relation.value,
        "reviewerKind": artifact.reviewer_kind.value,
        "reviewerKey": artifact.reviewer_key,
        "reviewedAt": artifact.reviewed_at.isoformat(),
        "effectiveUntil": (
            artifact.effective_until.isoformat()
            if artifact.effective_until is not None
            else None
        ),
        "basisEvidenceIds": list(artifact.basis_evidence_ids),
        "basisCatalystId": artifact.basis_catalyst_id,
        "decisionSummary": artifact.decision_summary,
    }
    digest = hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"business-review:{digest}"


def apply_official_business_manual_review(
    material_result: Any,
    review_artifact: Any,
) -> LeaderOfficialBusinessManualReviewResult:
    """把一份人工复核附加到已验证官方材料，仍不开放正式门禁。"""

    if not _official_material_input_valid(material_result):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_material_unverified",),
        )
    if type(review_artifact) is not LeaderOfficialBusinessManualReviewArtifact:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_contract_unverified",),
        )
    if (
        not _required_text(review_artifact.review_version)
        or review_artifact.supersedes_review_version is not None
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_version_unverified",),
        )
    if (
        not isinstance(
            review_artifact.relation,
            BusinessCatalystRelation,
        )
        or type(review_artifact.reviewer_kind) is not ManualReviewerKind
        or review_artifact.reviewer_kind != ManualReviewerKind.HUMAN
        or not _required_text(review_artifact.reviewer_key)
        or SAFE_REVIEWER_KEY_PATTERN.fullmatch(
            review_artifact.reviewer_key
        ) is None
        or not _required_text(review_artifact.decision_summary)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_identity_unverified",),
        )

    input_value = material_result.input_value
    catalyst = input_value.catalyst
    if (
        review_artifact.symbol != input_value.symbol
        or review_artifact.industry_code != input_value.industry_code
        or review_artifact.industry_release_id
        != input_value.industry_release_id
        or review_artifact.catalyst_id != catalyst.catalyst_id
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_identity_unverified",),
        )
    evidence_ids = tuple(
        proof.evidence_id for proof in input_value.business_proofs
    )
    if (
        review_artifact.basis_catalyst_id != catalyst.catalyst_id
        or not isinstance(review_artifact.basis_evidence_ids, tuple)
        or not review_artifact.basis_evidence_ids
        or any(
            not _required_text(evidence_id)
            or evidence_id not in evidence_ids
            for evidence_id in review_artifact.basis_evidence_ids
        )
        or len(set(review_artifact.basis_evidence_ids))
        != len(review_artifact.basis_evidence_ids)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_basis_unverified",),
        )

    as_of = _aware_utc(input_value.as_of)
    reviewed_at = _aware_utc(review_artifact.reviewed_at)
    effective_until = (
        _aware_utc(review_artifact.effective_until)
        if review_artifact.effective_until is not None
        else None
    )
    if (
        as_of is None
        or reviewed_at is None
        or (
            review_artifact.effective_until is not None
            and effective_until is None
        )
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_time_unverified",),
        )
    if effective_until is not None and effective_until < reviewed_at:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_review_effective_range_unverified",),
        )
    if reviewed_at > as_of:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_manual_reviewed_at_future",),
        )
    if effective_until is not None and effective_until < as_of:
        return _result(
            ResearchFeatureStatus.STALE,
            ("business_manual_review_expired",),
        )

    review_id = _review_id(review_artifact)
    review = LeaderBusinessCatalystReview(
        review_id=review_id,
        mapping_version=review_artifact.review_version,
        symbol=review_artifact.symbol,
        industry_code=review_artifact.industry_code,
        industry_release_id=review_artifact.industry_release_id,
        catalyst_id=review_artifact.catalyst_id,
        relation=review_artifact.relation,
        review_method="manual",
        reviewer_key=review_artifact.reviewer_key,
        reviewed_at=review_artifact.reviewed_at,
        effective_until=review_artifact.effective_until,
        basis_evidence_ids=review_artifact.basis_evidence_ids,
        basis_catalyst_id=review_artifact.basis_catalyst_id,
        decision_summary=review_artifact.decision_summary,
    )
    reviewed_input = replace(input_value, reviews=(review,))
    feature = build_leader_business_catalyst_features(reviewed_input)
    expected_status = (
        ResearchFeatureStatus.SOURCE_UNVERIFIED
        if review_artifact.relation
        == BusinessCatalystRelation.UNCONFIRMED
        else ResearchFeatureStatus.READY
    )
    if feature.status != expected_status:
        return _result(feature.status, feature.reasons)
    if (
        expected_status == ResearchFeatureStatus.SOURCE_UNVERIFIED
        and feature.reasons != ("business_relation_unconfirmed",)
    ):
        return _result(feature.status, feature.reasons)
    return _result(
        ResearchFeatureStatus.READY,
        ("business_manual_review_input_ready",),
        input_value=reviewed_input,
        review_id=review_id,
    )


def _material_batch_valid(value: Any) -> bool:
    if (
        type(value) is not LeaderOfficialBusinessMaterialBatchResult
        or value.contract_id
        != LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID
        or value.status
        == LeaderOfficialBusinessMaterialBatchStatus.BLOCKED
        or not _required_text(value.candidate_plan_id)
        or not isinstance(value.candidate_count, int)
        or value.candidate_count < 1
        or not isinstance(value.items, tuple)
        or len(value.items) != value.candidate_count
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    symbols = []
    for index, item in enumerate(value.items):
        if (
            type(item) is not LeaderOfficialBusinessMaterialBatchItem
            or item.index != index
            or not _required_text(item.symbol)
            or not isinstance(item.status, ResearchFeatureStatus)
            or not isinstance(item.reasons, tuple)
            or any(not _required_text(reason) for reason in item.reasons)
            or (
                item.status == ResearchFeatureStatus.READY
                and (
                    item.input_value is None
                    or item.reasons
                    != ("official_business_material_input_ready",)
                )
            )
            or (
                item.status != ResearchFeatureStatus.READY
                and item.input_value is not None
            )
        ):
            return False
        symbols.append(item.symbol)
    if len(set(symbols)) != len(symbols):
        return False
    if all(
        item.status == ResearchFeatureStatus.READY
        for item in value.items
    ):
        expected_status = LeaderOfficialBusinessMaterialBatchStatus.READY
        expected_reasons = ()
    elif all(
        item.status == ResearchFeatureStatus.MISSING
        for item in value.items
    ):
        expected_status = LeaderOfficialBusinessMaterialBatchStatus.MISSING
        expected_reasons = ("official_business_material_batch_missing",)
    else:
        expected_status = LeaderOfficialBusinessMaterialBatchStatus.PARTIAL
        expected_reasons = ("official_business_material_batch_partial",)
    return bool(
        value.status == expected_status
        and value.reasons == expected_reasons
    )


def _batch_result(
    *,
    status: LeaderOfficialBusinessManualReviewBatchStatus,
    candidate_plan_id: Optional[str],
    candidate_count: int,
    reasons: Sequence[str],
    items: Sequence[LeaderOfficialBusinessManualReviewBatchItem] = (),
) -> LeaderOfficialBusinessManualReviewBatchResult:
    return LeaderOfficialBusinessManualReviewBatchResult(
        status=status,
        candidate_plan_id=candidate_plan_id,
        candidate_count=candidate_count,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        items=tuple(items),
    )


def apply_official_business_manual_reviews_batch(
    material_batch: Any,
    entries: Any,
) -> LeaderOfficialBusinessManualReviewBatchResult:
    """按官方材料批次顺序应用人工复核，逐证券保留失败。"""

    if (
        not _material_batch_valid(material_batch)
        or not isinstance(entries, tuple)
        or any(
            type(entry)
            is not LeaderOfficialBusinessManualReviewBatchEntry
            or not _required_text(entry.symbol)
            for entry in entries
        )
    ):
        return _batch_result(
            status=LeaderOfficialBusinessManualReviewBatchStatus.BLOCKED,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("business_manual_review_batch_contract_unverified",),
        )
    material_symbols = tuple(item.symbol for item in material_batch.items)
    entry_symbols = tuple(entry.symbol for entry in entries)
    if (
        len(set(entry_symbols)) != len(entry_symbols)
        or set(entry_symbols) != set(material_symbols)
    ):
        return _batch_result(
            status=LeaderOfficialBusinessManualReviewBatchStatus.BLOCKED,
            candidate_plan_id=material_batch.candidate_plan_id,
            candidate_count=material_batch.candidate_count,
            reasons=("business_manual_review_batch_candidate_mismatch",),
        )

    entries_by_symbol = {entry.symbol: entry for entry in entries}
    items = []
    for material_item in material_batch.items:
        entry = entries_by_symbol[material_item.symbol]
        if material_item.status != ResearchFeatureStatus.READY:
            if entry.review_artifact is None:
                status = material_item.status
                reasons = material_item.reasons
            else:
                status = ResearchFeatureStatus.SOURCE_UNVERIFIED
                reasons = ("business_manual_review_material_not_ready",)
            items.append(LeaderOfficialBusinessManualReviewBatchItem(
                index=material_item.index,
                symbol=material_item.symbol,
                status=status,
                reasons=reasons,
            ))
            continue
        if entry.review_artifact is None:
            items.append(LeaderOfficialBusinessManualReviewBatchItem(
                index=material_item.index,
                symbol=material_item.symbol,
                status=ResearchFeatureStatus.MISSING,
                reasons=("business_manual_review_missing",),
            ))
            continue
        reviewed = apply_official_business_manual_review(
            LeaderOfficialMaterialAdapterResult(
                status=material_item.status,
                reasons=material_item.reasons,
                input_value=material_item.input_value,
            ),
            entry.review_artifact,
        )
        items.append(LeaderOfficialBusinessManualReviewBatchItem(
            index=material_item.index,
            symbol=material_item.symbol,
            status=reviewed.status,
            reasons=reviewed.reasons,
            input_value=reviewed.input_value,
            review_id=reviewed.review_id,
        ))

    if all(item.status == ResearchFeatureStatus.READY for item in items):
        status = LeaderOfficialBusinessManualReviewBatchStatus.READY
        reasons = ()
    elif all(item.status == ResearchFeatureStatus.MISSING for item in items):
        status = LeaderOfficialBusinessManualReviewBatchStatus.MISSING
        reasons = ("business_manual_review_batch_missing",)
    else:
        status = LeaderOfficialBusinessManualReviewBatchStatus.PARTIAL
        reasons = ("business_manual_review_batch_partial",)
    return _batch_result(
        status=status,
        candidate_plan_id=material_batch.candidate_plan_id,
        candidate_count=material_batch.candidate_count,
        reasons=reasons,
        items=items,
    )
