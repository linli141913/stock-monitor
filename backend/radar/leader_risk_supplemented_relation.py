"""阶段6L-D7补录事实后的关系复核桥接。

本模块只重放D6研究视图并验证一次人工关系审核。输出是独立研究关系，
不修改D3/D6，不连接数据库，也不生成D1正式解除证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
)
from radar.leader_risk_review_artifacts import (
    AcceptedManualRiskReviewArtifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    RiskDocumentResearchReplayResult,
    replay_risk_document_research_evidence,
)
from radar.sources.leader_risk_document_content import (
    RiskDocumentReviewCandidateKind,
)


SUPPLEMENTED_RISK_DOCUMENT_RELATION_CONTRACT_ID = (
    "radar-leader-risk-supplemented-relation-v1"
)
SAFE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
UTC = timezone.utc


@dataclass(frozen=True)
class SupplementedRiskDocumentRelationInput:
    replay_input: RiskDocumentResearchReplayInput = field(
        repr=False,
    )
    replay_result: RiskDocumentResearchReplayResult = field(
        repr=False,
    )
    review: RiskDocumentVersionReview = field(repr=False)


@dataclass(frozen=True)
class AcceptedSupplementedRiskDocumentRelation:
    relation_id: str
    review_id: str
    mapping_version: str
    relation_kind: RiskDocumentRelationKind
    source_document_id: str
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    manual_basis_fact_ids: Tuple[str, ...]
    reviewed_at: datetime
    effective_until: Optional[datetime]
    source_artifact_id: str
    source_artifact_version: str
    source_replay_version: str
    relation_contract_id: str = (
        SUPPLEMENTED_RISK_DOCUMENT_RELATION_CONTRACT_ID
    )
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False


@dataclass(frozen=True)
class SupplementedRiskDocumentRelationResult:
    status: ResearchFeatureStatus
    relation: Optional[
        AcceptedSupplementedRiskDocumentRelation
    ] = None
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    relation: Optional[
        AcceptedSupplementedRiskDocumentRelation
    ] = None,
) -> SupplementedRiskDocumentRelationResult:
    return SupplementedRiskDocumentRelationResult(
        status=status,
        relation=relation,
        reasons=_dedupe(reasons),
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SAFE_IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _active_artifact(
    replay_input: RiskDocumentResearchReplayInput,
    replay_result: RiskDocumentResearchReplayResult,
) -> Optional[AcceptedManualRiskReviewArtifact]:
    artifacts = replay_input.artifacts
    if not isinstance(artifacts, (tuple, list)):
        return None
    matches = tuple(
        artifact
        for artifact in artifacts
        if (
            isinstance(
                artifact,
                AcceptedManualRiskReviewArtifact,
            )
            and artifact.artifact_id
            == replay_result.active_artifact_id
            and artifact.review_version
            == replay_result.active_review_version
        )
    )
    if len(matches) != 1 or matches[0] is not artifacts[-1]:
        return None
    return matches[0]


def _source_view_valid(
    replay_result: RiskDocumentResearchReplayResult,
    artifact: AcceptedManualRiskReviewArtifact,
) -> bool:
    return (
        replay_result.status == ResearchFeatureStatus.READY
        and artifact.candidate_kind
        == RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
        and bool(replay_result.manual_facts)
        and not replay_result.relations
        and replay_result.manual_review_applied_to_view is True
        and replay_result.formal_usable is False
        and replay_result.d3_mutated is False
        and replay_result.applied_to_d1 is False
        and artifact.facts == replay_result.manual_facts
        and not artifact.relations
    )


def _basis_matches(
    facts_by_id: Dict[str, RiskDocumentFact],
    basis_fact_ids: Sequence[str],
    fact_kind: RiskDocumentFactKind,
    normalized_value: str,
) -> bool:
    return any(
        fact_id in facts_by_id
        and facts_by_id[fact_id].fact_kind == fact_kind
        and facts_by_id[fact_id].normalized_value
        == normalized_value
        for fact_id in basis_fact_ids
    )


def _target_event(
    replay_input: RiskDocumentResearchReplayInput,
    review: RiskDocumentVersionReview,
) -> Optional[LeaderRiskEventEvidence]:
    matches = tuple(
        event
        for event in replay_input.event_versions
        if (
            isinstance(event, LeaderRiskEventEvidence)
            and event.event_id == review.target_event_id
            and event.event_version
            == review.target_event_version
        )
    )
    return matches[0] if len(matches) == 1 else None


def _review_structure_valid(
    review: Any,
    replay_input: RiskDocumentResearchReplayInput,
    artifact: AcceptedManualRiskReviewArtifact,
) -> bool:
    if not isinstance(review, RiskDocumentVersionReview):
        return False
    reviewed_at = _aware_utc(review.reviewed_at)
    artifact_reviewed_at = _aware_utc(artifact.reviewed_at)
    as_of = _aware_utc(replay_input.as_of)
    effective_until = (
        _aware_utc(review.effective_until)
        if review.effective_until is not None
        else None
    )
    basis_fact_ids = review.basis_fact_ids
    return (
        _safe_identifier(review.review_id)
        and _safe_identifier(review.mapping_version)
        and review.review_method == "manual"
        and _safe_identifier(review.reviewer_key)
        and isinstance(
            review.relation_kind,
            RiskDocumentRelationKind,
        )
        and reviewed_at is not None
        and artifact_reviewed_at is not None
        and as_of is not None
        and artifact_reviewed_at < reviewed_at <= as_of
        and (
            review.effective_until is None
            or (
                effective_until is not None
                and effective_until >= reviewed_at
            )
        )
        and review.source_document_id
        == replay_input.document.document_id
        and _safe_identifier(review.source_document_id)
        and _safe_identifier(review.target_event_id)
        and _safe_identifier(review.target_event_version)
        and _safe_identifier(review.target_document_id)
        and review.target_document_id
        != review.source_document_id
        and isinstance(basis_fact_ids, (tuple, list))
        and bool(basis_fact_ids)
        and all(
            _safe_identifier(fact_id)
            for fact_id in basis_fact_ids
        )
        and len(basis_fact_ids) == len(set(basis_fact_ids))
        and (
            review.relation_kind
            == RiskDocumentRelationKind.RESOLVES
            and review.replacement_event_version is None
            or review.relation_kind
            == RiskDocumentRelationKind.SUPERSEDES
            and _safe_identifier(
                review.replacement_event_version
            )
            and review.replacement_event_version
            != review.target_event_version
        )
    )


def _event_and_basis_valid(
    replay_input: RiskDocumentResearchReplayInput,
    replay_result: RiskDocumentResearchReplayResult,
    review: RiskDocumentVersionReview,
    event: LeaderRiskEventEvidence,
) -> Tuple[bool, Tuple[str, ...]]:
    facts = replay_result.facts
    manual_facts = replay_result.manual_facts
    if (
        not isinstance(facts, (tuple, list))
        or not isinstance(manual_facts, (tuple, list))
        or any(
            not isinstance(fact, RiskDocumentFact)
            for fact in facts
        )
        or any(
            not isinstance(fact, RiskDocumentFact)
            for fact in manual_facts
        )
    ):
        return False, ()
    facts_by_id = {fact.fact_id: fact for fact in facts}
    if len(facts_by_id) != len(facts):
        return False, ()
    basis_fact_ids = review.basis_fact_ids
    manual_ids = {fact.fact_id for fact in manual_facts}
    manual_basis_ids = tuple(
        fact_id
        for fact_id in basis_fact_ids
        if fact_id in manual_ids
    )
    published_at = _aware_utc(event.published_at)
    current_published_at = _aware_utc(
        replay_input.document.published_at
    )
    identity_valid = (
        event.document_id == review.target_document_id
        and event.symbol == replay_input.document.symbol
        and event.issuer_identity
        == replay_input.document.issuer_identity
        and published_at is not None
        and current_published_at is not None
        and published_at < current_published_at
        and all(
            fact_id in facts_by_id
            for fact_id in basis_fact_ids
        )
        and bool(manual_basis_ids)
        and _basis_matches(
            facts_by_id,
            basis_fact_ids,
            RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
            event.document_id,
        )
    )
    if not identity_valid:
        return False, manual_basis_ids
    if event.category in {
        RiskCategory.INVESTIGATION,
        RiskCategory.LITIGATION,
    } and (
        not isinstance(event.case_id, str)
        or not _basis_matches(
            facts_by_id,
            basis_fact_ids,
            RiskDocumentFactKind.CASE_ID,
            event.case_id,
        )
    ):
        return False, manual_basis_ids
    if event.category in {
        RiskCategory.EARNINGS,
        RiskCategory.AUDIT,
    } and (
        not isinstance(event.reporting_period, str)
        or not _basis_matches(
            facts_by_id,
            basis_fact_ids,
            RiskDocumentFactKind.REPORTING_PERIOD,
            event.reporting_period,
        )
    ):
        return False, manual_basis_ids
    return True, manual_basis_ids


def _relation_id(
    review: RiskDocumentVersionReview,
    artifact: AcceptedManualRiskReviewArtifact,
) -> str:
    material = "|".join((
        SUPPLEMENTED_RISK_DOCUMENT_RELATION_CONTRACT_ID,
        artifact.artifact_id,
        artifact.review_version,
        review.review_id,
        review.mapping_version,
        review.relation_kind.value,
        review.source_document_id,
        review.target_event_id,
        review.target_event_version,
        review.target_document_id,
        review.replacement_event_version or "",
        *review.basis_fact_ids,
    ))
    return (
        "supplemented-risk-relation:"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


def review_supplemented_risk_document_relation(
    input_value: Any,
) -> SupplementedRiskDocumentRelationResult:
    """重算D6视图并生成不修改D3/D1的补录事实研究关系。"""

    if (
        not isinstance(
            input_value,
            SupplementedRiskDocumentRelationInput,
        )
        or not isinstance(
            input_value.replay_input,
            RiskDocumentResearchReplayInput,
        )
        or not isinstance(
            input_value.replay_result,
            RiskDocumentResearchReplayResult,
        )
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_contract_unverified",),
        )

    replayed = replay_risk_document_research_evidence(
        input_value.replay_input
    )
    if replayed != input_value.replay_result:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_replay_mismatch",),
        )
    if replayed.status != ResearchFeatureStatus.READY:
        return _result(replayed.status, replayed.reasons)

    artifact = _active_artifact(
        input_value.replay_input,
        replayed,
    )
    if (
        artifact is None
        or not _source_view_valid(replayed, artifact)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_source_view_unverified",),
        )

    review = input_value.review
    if not _review_structure_valid(
        review,
        input_value.replay_input,
        artifact,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_review_unverified",),
        )
    as_of = _aware_utc(input_value.replay_input.as_of)
    effective_until = (
        _aware_utc(review.effective_until)
        if review.effective_until is not None
        else None
    )
    if effective_until is not None and effective_until < as_of:
        return _result(
            ResearchFeatureStatus.STALE,
            ("supplemented_risk_relation_review_expired",),
        )

    event = _target_event(input_value.replay_input, review)
    if event is None:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_target_unverified",),
        )
    basis_valid, manual_basis_ids = _event_and_basis_valid(
        input_value.replay_input,
        replayed,
        review,
        event,
    )
    if not basis_valid:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("supplemented_risk_relation_basis_unverified",),
        )

    relation = AcceptedSupplementedRiskDocumentRelation(
        relation_id=_relation_id(review, artifact),
        review_id=review.review_id,
        mapping_version=review.mapping_version,
        relation_kind=review.relation_kind,
        source_document_id=review.source_document_id,
        target_event_id=review.target_event_id,
        target_event_version=review.target_event_version,
        target_document_id=review.target_document_id,
        replacement_event_version=(
            review.replacement_event_version
        ),
        basis_fact_ids=tuple(review.basis_fact_ids),
        manual_basis_fact_ids=manual_basis_ids,
        reviewed_at=_aware_utc(review.reviewed_at),
        effective_until=effective_until,
        source_artifact_id=artifact.artifact_id,
        source_artifact_version=artifact.review_version,
        source_replay_version=replayed.replay_version,
    )
    return _result(
        ResearchFeatureStatus.READY,
        (),
        relation=relation,
    )
