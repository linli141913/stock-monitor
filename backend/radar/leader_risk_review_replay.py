"""阶段6L-D6人工审核工件重放与研究事实合并。

本模块只验证并组合D3基础事实和D5人工审核工件。它不修改上游对象、不连接
数据库，也不生成D1正式风险事件或解除证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import re
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    RISK_DOCUMENT_MAPPING_CONTRACT_ID,
    AcceptedRiskDocumentRelation,
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentFactResult,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
)
from radar.leader_risk_review_artifacts import (
    MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    AcceptedManualRiskReviewArtifact,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RiskDocumentReviewCandidate,
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
)
from radar.sources.leader_risk_official import (
    OfficialRiskDocumentMetadata,
)


RISK_DOCUMENT_RESEARCH_REPLAY_VERSION = (
    "radar-leader-risk-document-research-replay-v1"
)
SAFE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTC = timezone.utc


@dataclass(frozen=True)
class RiskDocumentResearchReplayInput:
    as_of: datetime
    document: OfficialRiskDocumentMetadata
    content: OfficialRiskDocumentContentResult
    facts: OfficialRiskDocumentFactResult
    event_versions: Tuple[LeaderRiskEventEvidence, ...] = field(
        repr=False,
    )
    artifacts: Tuple[
        AcceptedManualRiskReviewArtifact,
        ...,
    ] = ()


@dataclass(frozen=True)
class RiskDocumentResearchReplayResult:
    status: ResearchFeatureStatus
    document_id: Optional[str] = None
    symbol: Optional[str] = None
    issuer_identity: Optional[str] = None
    content_sha256: Optional[str] = None
    deterministic_facts: Tuple[RiskDocumentFact, ...] = ()
    manual_facts: Tuple[RiskDocumentFact, ...] = ()
    facts: Tuple[RiskDocumentFact, ...] = ()
    relations: Tuple[AcceptedRiskDocumentRelation, ...] = ()
    active_artifact_id: Optional[str] = None
    active_review_version: Optional[str] = None
    manual_review_applied_to_view: bool = False
    replay_version: str = RISK_DOCUMENT_RESEARCH_REPLAY_VERSION
    d3_mutated: bool = False
    formal_usable: bool = False
    applied_to_d1: bool = False
    correction_links_complete: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


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


def _result(
    input_value: Any,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    deterministic_facts: Sequence[RiskDocumentFact] = (),
    manual_facts: Sequence[RiskDocumentFact] = (),
    facts: Sequence[RiskDocumentFact] = (),
    relations: Sequence[AcceptedRiskDocumentRelation] = (),
    active_artifact: Optional[
        AcceptedManualRiskReviewArtifact
    ] = None,
) -> RiskDocumentResearchReplayResult:
    content = (
        input_value.content
        if isinstance(input_value, RiskDocumentResearchReplayInput)
        and isinstance(
            input_value.content,
            OfficialRiskDocumentContentResult,
        )
        else None
    )
    return RiskDocumentResearchReplayResult(
        status=status,
        document_id=content.document_id if content else None,
        symbol=content.symbol if content else None,
        issuer_identity=(
            content.issuer_identity if content else None
        ),
        content_sha256=(
            content.content_sha256 if content else None
        ),
        deterministic_facts=tuple(deterministic_facts),
        manual_facts=tuple(manual_facts),
        facts=tuple(facts),
        relations=tuple(relations),
        active_artifact_id=(
            active_artifact.artifact_id
            if active_artifact is not None
            else None
        ),
        active_review_version=(
            active_artifact.review_version
            if active_artifact is not None
            else None
        ),
        manual_review_applied_to_view=(
            active_artifact is not None
        ),
        reasons=_dedupe(reasons),
    )


def _reference_fact_result(
    input_value: RiskDocumentResearchReplayInput,
) -> OfficialRiskDocumentFactResult:
    return extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=input_value.as_of,
            document=input_value.document,
            content_sha256=(
                input_value.content.content_sha256 or ""
            ),
            pages=input_value.content.pages,
            extracted_at=(
                input_value.content.fetched_at
                or input_value.as_of
            ),
            source_status=input_value.content.status,
            event_versions=input_value.event_versions,
            reviews=(),
        )
    )


def _candidate(
    input_value: RiskDocumentResearchReplayInput,
) -> Tuple[
    Optional[RiskDocumentReviewCandidate],
    ResearchFeatureStatus,
    Tuple[str, ...],
]:
    reference = _reference_fact_result(input_value)
    if reference != input_value.facts:
        return (
            None,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_replay_fact_result_unverified",),
        )
    candidate_result = build_risk_document_review_candidate(
        input_value.content,
        input_value.facts,
    )
    if (
        candidate_result.status != ResearchFeatureStatus.READY
        or candidate_result.candidate is None
    ):
        status = (
            input_value.facts.status
            if input_value.facts.status
            != ResearchFeatureStatus.READY
            else candidate_result.status
        )
        return (
            None,
            status,
            (
                "risk_document_replay_candidate_not_ready",
                *candidate_result.reasons,
            ),
        )
    return (
        candidate_result.candidate,
        ResearchFeatureStatus.READY,
        (),
    )


def _expected_artifact_id(
    candidate: RiskDocumentReviewCandidate,
) -> str:
    material = (
        f"{candidate.candidate_id}|"
        f"{candidate.candidate_kind.value}"
    )
    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _valid_date_value(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2099


def _normalized_value_valid(
    fact: RiskDocumentFact,
) -> bool:
    value = fact.normalized_value
    if not isinstance(value, str):
        return False
    if fact.fact_kind == RiskDocumentFactKind.CASE_ID:
        return re.fullmatch(r"case:[0-9a-f]{64}", value) is not None
    if fact.fact_kind == RiskDocumentFactKind.AUDIT_REPORT_ID:
        return (
            re.fullmatch(
                r"audit-report:[0-9a-f]{64}",
                value,
            )
            is not None
        )
    if fact.fact_kind == RiskDocumentFactKind.REPORTING_PERIOD:
        return (
            re.fullmatch(
                r"(?:19|20)\d{2}(?:-H1|-Q1|-Q3)?",
                value,
            )
            is not None
        )
    if (
        fact.fact_kind
        == RiskDocumentFactKind.REFERENCED_DOCUMENT_ID
    ):
        return re.fullmatch(r"cninfo:\d{7,12}", value) is not None
    if fact.fact_kind == RiskDocumentFactKind.EFFECTIVE_DATE:
        return _valid_date_value(value)
    if fact.fact_kind == RiskDocumentFactKind.EFFECTIVE_INTERVAL:
        values = value.split("/")
        return (
            len(values) == 2
            and _valid_date_value(values[0])
            and _valid_date_value(values[1])
            and values[0] <= values[1]
        )
    return False


def _manual_fact_valid(
    fact: Any,
    document_id: str,
    page_count: int,
) -> bool:
    if (
        not isinstance(fact, RiskDocumentFact)
        or not isinstance(fact.fact_kind, RiskDocumentFactKind)
        or fact.extractor_version
        != MANUAL_RISK_DOCUMENT_REVIEW_VERSION
        or not isinstance(fact.page_number, int)
        or isinstance(fact.page_number, bool)
        or not 1 <= fact.page_number <= page_count
        or not isinstance(fact.fragment_sha256, str)
        or SHA256_PATTERN.fullmatch(
            fact.fragment_sha256
        ) is None
        or not _normalized_value_valid(fact)
    ):
        return False
    identity = (
        f"{document_id}|{fact.fact_kind.value}|"
        f"{fact.normalized_value}|{fact.page_number}"
    )
    expected_id = (
        "riskfact:"
        + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
    )
    return fact.fact_id == expected_id


def _fact_payload_valid(
    artifact: AcceptedManualRiskReviewArtifact,
    candidate: RiskDocumentReviewCandidate,
) -> bool:
    if (
        not isinstance(artifact.facts, (tuple, list))
        or not isinstance(artifact.relations, (tuple, list))
        or not artifact.facts
        or artifact.relations
        or any(
            not _manual_fact_valid(
                fact,
                candidate.document_id,
                candidate.page_count,
            )
            for fact in artifact.facts
        )
    ):
        return False
    keys = [
        (fact.fact_kind, fact.normalized_value, fact.page_number)
        for fact in artifact.facts
    ]
    if len(keys) != len(set(keys)):
        return False
    values_by_kind: Dict[
        RiskDocumentFactKind,
        set,
    ] = {}
    for fact in artifact.facts:
        values_by_kind.setdefault(
            fact.fact_kind,
            set(),
        ).add(fact.normalized_value)
    return not any(
        len(values) > 1
        for values in values_by_kind.values()
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


def _relation_valid(
    relation: Any,
    artifact: AcceptedManualRiskReviewArtifact,
    input_value: RiskDocumentResearchReplayInput,
    facts_by_id: Dict[str, RiskDocumentFact],
    events_by_key: Dict[
        Tuple[str, str],
        LeaderRiskEventEvidence,
    ],
) -> bool:
    if (
        not isinstance(relation, AcceptedRiskDocumentRelation)
        or not _safe_identifier(relation.relation_id)
        or not _safe_identifier(relation.mapping_version)
        or not _safe_identifier(relation.source_document_id)
        or not _safe_identifier(relation.target_event_id)
        or not _safe_identifier(relation.target_event_version)
        or not _safe_identifier(relation.target_document_id)
        or relation.mapping_version != artifact.review_version
        or relation.mapping_contract_id
        != RISK_DOCUMENT_MAPPING_CONTRACT_ID
        or relation.source_document_id
        != input_value.document.document_id
        or _aware_utc(relation.reviewed_at)
        != _aware_utc(artifact.reviewed_at)
        or not isinstance(
            relation.relation_kind,
            RiskDocumentRelationKind,
        )
        or not isinstance(
            relation.basis_fact_ids,
            (tuple, list),
        )
        or not relation.basis_fact_ids
        or any(
            not _safe_identifier(fact_id)
            for fact_id in relation.basis_fact_ids
        )
        or len(relation.basis_fact_ids)
        != len(set(relation.basis_fact_ids))
        or any(
            fact_id not in facts_by_id
            for fact_id in relation.basis_fact_ids
        )
    ):
        return False
    event = events_by_key.get((
        relation.target_event_id,
        relation.target_event_version,
    ))
    if (
        event is None
        or event.document_id != relation.target_document_id
        or event.symbol != input_value.document.symbol
        or event.issuer_identity
        != input_value.document.issuer_identity
        or _aware_utc(event.published_at) is None
        or _aware_utc(event.published_at)
        >= _aware_utc(input_value.document.published_at)
        or not _basis_matches(
            facts_by_id,
            relation.basis_fact_ids,
            RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
            event.document_id,
        )
    ):
        return False
    if event.category in {
        RiskCategory.INVESTIGATION,
        RiskCategory.LITIGATION,
    } and (
        not isinstance(event.case_id, str)
        or not _basis_matches(
            facts_by_id,
            relation.basis_fact_ids,
            RiskDocumentFactKind.CASE_ID,
            event.case_id,
        )
    ):
        return False
    if event.category in {
        RiskCategory.EARNINGS,
        RiskCategory.AUDIT,
    } and (
        not isinstance(event.reporting_period, str)
        or not _basis_matches(
            facts_by_id,
            relation.basis_fact_ids,
            RiskDocumentFactKind.REPORTING_PERIOD,
            event.reporting_period,
        )
    ):
        return False
    if (
        relation.relation_kind
        == RiskDocumentRelationKind.SUPERSEDES
    ):
        return (
            _safe_identifier(
                relation.replacement_event_version
            )
            and relation.replacement_event_version
            != event.event_version
        )
    return (
        relation.relation_kind
        == RiskDocumentRelationKind.RESOLVES
        and relation.replacement_event_version is None
    )


def _relation_payload_valid(
    artifact: AcceptedManualRiskReviewArtifact,
    input_value: RiskDocumentResearchReplayInput,
) -> bool:
    if (
        not isinstance(artifact.facts, (tuple, list))
        or not isinstance(artifact.relations, (tuple, list))
        or artifact.facts
        or len(artifact.relations) != 1
    ):
        return False
    facts_by_id = {
        fact.fact_id: fact
        for fact in input_value.facts.facts
    }
    events_by_key = {
        (event.event_id, event.event_version): event
        for event in input_value.event_versions
    }
    return _relation_valid(
        artifact.relations[0],
        artifact,
        input_value,
        facts_by_id,
        events_by_key,
    )


def _chain(
    input_value: RiskDocumentResearchReplayInput,
    candidate: RiskDocumentReviewCandidate,
) -> Tuple[
    Optional[AcceptedManualRiskReviewArtifact],
    ResearchFeatureStatus,
    Tuple[str, ...],
]:
    artifacts = input_value.artifacts
    if not isinstance(artifacts, (tuple, list)):
        return (
            None,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_artifact_chain_unverified",),
        )
    if not artifacts:
        return (
            None,
            ResearchFeatureStatus.MISSING,
            ("risk_document_review_artifact_missing",),
        )

    expected_artifact_id = _expected_artifact_id(candidate)
    as_of = _aware_utc(input_value.as_of)
    fetched_at = _aware_utc(input_value.content.fetched_at)
    if as_of is None or fetched_at is None:
        return (
            None,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_artifact_chain_unverified",),
        )

    versions = []
    previous_version = None
    previous_reviewed_at = None
    for artifact in artifacts:
        if not isinstance(
            artifact,
            AcceptedManualRiskReviewArtifact,
        ):
            return (
                None,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("risk_document_review_artifact_chain_unverified",),
            )
        reviewed_at = _aware_utc(artifact.reviewed_at)
        effective_until = (
            _aware_utc(artifact.effective_until)
            if artifact.effective_until is not None
            else None
        )
        identity_valid = (
            artifact.artifact_id == expected_artifact_id
            and artifact.candidate_id == candidate.candidate_id
            and artifact.candidate_kind == candidate.candidate_kind
            and artifact.document_id == candidate.document_id
            and artifact.symbol == candidate.symbol
            and artifact.issuer_identity
            == candidate.issuer_identity
            and artifact.content_sha256
            == candidate.content_sha256
            and artifact.review_method == "manual"
            and _safe_identifier(artifact.reviewer_key)
            and _safe_identifier(artifact.review_version)
            and artifact.supersedes_review_version
            == previous_version
            and artifact.artifact_contract_version
            == MANUAL_RISK_DOCUMENT_REVIEW_VERSION
            and artifact.formal_usable is False
            and artifact.applied_to_d3 is False
            and artifact.applied_to_d1 is False
            and reviewed_at is not None
            and fetched_at <= reviewed_at <= as_of
            and (
                previous_reviewed_at is None
                or reviewed_at > previous_reviewed_at
            )
            and (
                artifact.effective_until is None
                or (
                    effective_until is not None
                    and effective_until >= reviewed_at
                )
            )
        )
        if not identity_valid:
            return (
                None,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("risk_document_review_artifact_chain_unverified",),
            )
        payload_valid = (
            _fact_payload_valid(artifact, candidate)
            if candidate.candidate_kind
            == RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
            else _relation_payload_valid(artifact, input_value)
        )
        if not payload_valid:
            reason = (
                "risk_document_review_artifact_fact_unverified"
                if candidate.candidate_kind
                == RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
                else "risk_document_review_artifact_relation_unverified"
            )
            return (
                None,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                (reason,),
            )
        versions.append(artifact.review_version)
        previous_version = artifact.review_version
        previous_reviewed_at = reviewed_at

    if len(versions) != len(set(versions)):
        return (
            None,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_artifact_chain_unverified",),
        )
    latest = artifacts[-1]
    latest_effective_until = (
        _aware_utc(latest.effective_until)
        if latest.effective_until is not None
        else None
    )
    if (
        latest_effective_until is not None
        and latest_effective_until < as_of
    ):
        return (
            None,
            ResearchFeatureStatus.STALE,
            (
                "risk_document_review_artifact_"
                "latest_expired",
            ),
        )
    return latest, ResearchFeatureStatus.READY, ()


def _merge_facts(
    deterministic_facts: Sequence[RiskDocumentFact],
    manual_facts: Sequence[RiskDocumentFact],
) -> Tuple[
    Tuple[RiskDocumentFact, ...],
    Tuple[str, ...],
]:
    merged = list(deterministic_facts)
    values_by_kind: Dict[
        RiskDocumentFactKind,
        set,
    ] = {}
    for fact in deterministic_facts:
        values_by_kind.setdefault(
            fact.fact_kind,
            set(),
        ).add(fact.normalized_value)
    for fact in manual_facts:
        existing_values = values_by_kind.get(
            fact.fact_kind,
            set(),
        )
        if existing_values and fact.normalized_value not in existing_values:
            return (), (
                "risk_document_review_fact_merge_conflict",
            )
        if fact.normalized_value in existing_values:
            continue
        merged.append(fact)
        values_by_kind.setdefault(
            fact.fact_kind,
            set(),
        ).add(fact.normalized_value)
    return tuple(merged), ()


def replay_risk_document_research_evidence(
    input_value: Any,
) -> RiskDocumentResearchReplayResult:
    """验证D5版本链并生成不修改D3/D1的研究事实视图。"""

    if (
        not isinstance(
            input_value,
            RiskDocumentResearchReplayInput,
        )
        or not isinstance(
            input_value.document,
            OfficialRiskDocumentMetadata,
        )
        or not isinstance(
            input_value.content,
            OfficialRiskDocumentContentResult,
        )
        or not isinstance(
            input_value.facts,
            OfficialRiskDocumentFactResult,
        )
        or not isinstance(
            input_value.event_versions,
            (tuple, list),
        )
        or any(
            not isinstance(event, LeaderRiskEventEvidence)
            for event in input_value.event_versions
        )
    ):
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_replay_contract_unverified",),
        )

    candidate, candidate_status, candidate_reasons = _candidate(
        input_value
    )
    deterministic_facts = tuple(input_value.facts.facts)
    if candidate is None:
        return _result(
            input_value,
            candidate_status,
            candidate_reasons,
            deterministic_facts=deterministic_facts,
            facts=deterministic_facts,
        )

    active, chain_status, chain_reasons = _chain(
        input_value,
        candidate,
    )
    if active is None:
        return _result(
            input_value,
            chain_status,
            chain_reasons,
            deterministic_facts=deterministic_facts,
            facts=deterministic_facts,
        )

    manual_facts = tuple(active.facts)
    merged_facts, merge_reasons = _merge_facts(
        deterministic_facts,
        manual_facts,
    )
    if merge_reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            merge_reasons,
            deterministic_facts=deterministic_facts,
        )
    return _result(
        input_value,
        ResearchFeatureStatus.READY,
        (),
        deterministic_facts=deterministic_facts,
        manual_facts=manual_facts,
        facts=merged_facts,
        relations=active.relations,
        active_artifact=active,
    )
