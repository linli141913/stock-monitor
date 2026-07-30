"""阶段6L-D5离线人工风险事实与关系审核工件。

本模块只在内存中验证D4人工候选、人工事实片段和D3关系审核。输出始终是
未应用到D3/D1的研究工件，不连接数据库，也不保存正文或原始人工输入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    AUDIT_REPORT_IDENTIFIER_PATTERN,
    AcceptedRiskDocumentRelation,
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentFactResult,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
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


MANUAL_RISK_DOCUMENT_REVIEW_VERSION = (
    "radar-leader-risk-document-manual-review-v1"
)
MAXIMUM_MANUAL_SOURCE_VALUE_CHARACTERS = 200
MAXIMUM_MANUAL_FRAGMENT_CHARACTERS = 500
SAFE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
CHINESE_DATE_PATTERN = re.compile(
    r"((?:19|20)\d{2})年"
    r"(0?[1-9]|1[0-2])月"
    r"(0?[1-9]|[12]\d|3[01])日"
)
REPORTING_PERIOD_PATTERN = re.compile(
    r"((?:19|20)\d{2})(年度|年半年度|年第一季度|年第三季度)"
)
UTC = timezone.utc


@dataclass(frozen=True)
class ManualRiskDocumentFactSubmission:
    fact_kind: RiskDocumentFactKind
    source_value: str = field(repr=False)
    page_number: int = 0
    source_fragment: str = field(default="", repr=False)


@dataclass(frozen=True)
class ManualRiskReviewSubmission:
    review_version: str
    supersedes_review_version: Optional[str]
    review_method: str
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    candidate_id: str
    document_id: str
    symbol: str
    issuer_identity: str
    content_sha256: str
    fact_supplements: Tuple[
        ManualRiskDocumentFactSubmission,
        ...,
    ] = ()
    relation_review: Optional[RiskDocumentVersionReview] = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True)
class AcceptedManualRiskReviewArtifact:
    artifact_id: str
    review_version: str
    supersedes_review_version: Optional[str]
    candidate_id: str
    candidate_kind: RiskDocumentReviewCandidateKind
    document_id: str
    symbol: str
    issuer_identity: str
    content_sha256: str
    review_method: str
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    facts: Tuple[RiskDocumentFact, ...] = ()
    relations: Tuple[AcceptedRiskDocumentRelation, ...] = ()
    artifact_contract_version: str = (
        MANUAL_RISK_DOCUMENT_REVIEW_VERSION
    )
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False


@dataclass(frozen=True)
class ManualRiskReviewArtifactInput:
    as_of: datetime
    document: OfficialRiskDocumentMetadata
    content: OfficialRiskDocumentContentResult
    facts: OfficialRiskDocumentFactResult
    candidate: RiskDocumentReviewCandidate
    event_versions: Tuple[LeaderRiskEventEvidence, ...] = field(
        repr=False,
    )
    submission: ManualRiskReviewSubmission
    previous_artifacts: Tuple[
        AcceptedManualRiskReviewArtifact,
        ...,
    ] = ()


@dataclass(frozen=True)
class ManualRiskReviewArtifactResult:
    status: ResearchFeatureStatus
    artifact: Optional[AcceptedManualRiskReviewArtifact] = None
    formal_usable: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    artifact: Optional[AcceptedManualRiskReviewArtifact] = None,
) -> ManualRiskReviewArtifactResult:
    return ManualRiskReviewArtifactResult(
        status=status,
        artifact=artifact,
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


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).strip("，,；;。")


def _normalized_date(value: str) -> Optional[str]:
    match = CHINESE_DATE_PATTERN.fullmatch(value)
    if match is None:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=UTC,
        ).date().isoformat()
    except ValueError:
        return None


def _normalized_fact_value(
    fact_kind: RiskDocumentFactKind,
    source_value: str,
) -> Optional[str]:
    compact_value = _compact(source_value)
    if fact_kind == RiskDocumentFactKind.CASE_ID:
        if not 2 <= len(compact_value) <= 160:
            return None
        return (
            "case:"
            + hashlib.sha256(
                compact_value.encode("utf-8")
            ).hexdigest()
        )
    if fact_kind == RiskDocumentFactKind.REPORTING_PERIOD:
        match = REPORTING_PERIOD_PATTERN.fullmatch(compact_value)
        if match is None:
            return None
        suffixes = {
            "年度": "",
            "年半年度": "-H1",
            "年第一季度": "-Q1",
            "年第三季度": "-Q3",
        }
        return f"{match.group(1)}{suffixes[match.group(2)]}"
    if fact_kind == RiskDocumentFactKind.AUDIT_REPORT_ID:
        if (
            AUDIT_REPORT_IDENTIFIER_PATTERN.fullmatch(
                compact_value
            ) is None
            or not any(
                character.isdigit()
                for character in compact_value
            )
        ):
            return None
        return (
            "audit-report:"
            + hashlib.sha256(
                compact_value.encode("utf-8")
            ).hexdigest()
        )
    if fact_kind == RiskDocumentFactKind.REFERENCED_DOCUMENT_ID:
        if re.fullmatch(r"\d{7,12}", compact_value) is None:
            return None
        return f"cninfo:{compact_value}"
    if fact_kind == RiskDocumentFactKind.EFFECTIVE_DATE:
        return _normalized_date(compact_value)
    if fact_kind == RiskDocumentFactKind.EFFECTIVE_INTERVAL:
        match = re.fullmatch(
            r"(.+?)(?:至|到|[-—－])(.+)",
            compact_value,
        )
        if match is None:
            return None
        start = _normalized_date(match.group(1))
        end = _normalized_date(match.group(2))
        if start is None or end is None or start > end:
            return None
        return f"{start}/{end}"
    return None


def _manual_fact(
    document_id: str,
    submission: ManualRiskDocumentFactSubmission,
    page_text: str,
) -> Tuple[Optional[RiskDocumentFact], Tuple[str, ...]]:
    if (
        not isinstance(
            submission,
            ManualRiskDocumentFactSubmission,
        )
        or not isinstance(
            submission.fact_kind,
            RiskDocumentFactKind,
        )
        or not isinstance(submission.page_number, int)
        or isinstance(submission.page_number, bool)
        or not isinstance(submission.source_value, str)
        or not isinstance(submission.source_fragment, str)
    ):
        return None, ("manual_risk_review_fact_unverified",)
    source_value = submission.source_value.strip()
    source_fragment = submission.source_fragment.strip()
    if (
        not source_value
        or len(source_value)
        > MAXIMUM_MANUAL_SOURCE_VALUE_CHARACTERS
        or not source_fragment
        or len(source_fragment)
        > MAXIMUM_MANUAL_FRAGMENT_CHARACTERS
    ):
        return None, ("manual_risk_review_fact_unverified",)
    if source_fragment not in page_text:
        return None, ("manual_risk_review_fragment_unverified",)
    if _compact(source_value) not in _compact(source_fragment):
        return None, ("manual_risk_review_value_unverified",)
    normalized_value = _normalized_fact_value(
        submission.fact_kind,
        source_value,
    )
    if normalized_value is None:
        return None, ("manual_risk_review_value_unverified",)

    identity = (
        f"{document_id}|{submission.fact_kind.value}|"
        f"{normalized_value}|{submission.page_number}"
    )
    return (
        RiskDocumentFact(
            fact_id=(
                "riskfact:"
                + hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()
            ),
            fact_kind=submission.fact_kind,
            normalized_value=normalized_value,
            page_number=submission.page_number,
            fragment_sha256=hashlib.sha256(
                source_fragment.encode("utf-8")
            ).hexdigest(),
            extractor_version=(
                MANUAL_RISK_DOCUMENT_REVIEW_VERSION
            ),
        ),
        (),
    )


def _reference_fact_result(
    input_value: ManualRiskReviewArtifactInput,
    reviews: Sequence[RiskDocumentVersionReview] = (),
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
            reviews=tuple(reviews),
        )
    )


def _artifact_id(
    candidate: RiskDocumentReviewCandidate,
) -> str:
    material = (
        f"{candidate.candidate_id}|"
        f"{candidate.candidate_kind.value}"
    )
    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _identity_reasons(
    input_value: ManualRiskReviewArtifactInput,
) -> Tuple[str, ...]:
    if (
        not isinstance(
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
            input_value.candidate,
            RiskDocumentReviewCandidate,
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
        return ("manual_risk_review_contract_unverified",)
    reference_facts = _reference_fact_result(input_value)
    if reference_facts != input_value.facts:
        return ("manual_risk_review_fact_result_unverified",)
    rebuilt = build_risk_document_review_candidate(
        input_value.content,
        input_value.facts,
    )
    if (
        rebuilt.status != ResearchFeatureStatus.READY
        or rebuilt.candidate != input_value.candidate
    ):
        return ("manual_risk_review_candidate_unverified",)
    document = input_value.document
    content = input_value.content
    candidate = input_value.candidate
    if (
        document.document_id != content.document_id
        or document.symbol != content.symbol
        or document.issuer_identity != content.issuer_identity
        or candidate.document_id != document.document_id
        or candidate.symbol != document.symbol
        or candidate.issuer_identity != document.issuer_identity
        or candidate.content_sha256 != content.content_sha256
    ):
        return ("manual_risk_review_identity_mismatch",)
    return ()


def _submission_reasons(
    input_value: ManualRiskReviewArtifactInput,
) -> Tuple[str, ...]:
    submission = input_value.submission
    if not isinstance(submission, ManualRiskReviewSubmission):
        return ("manual_risk_review_submission_unverified",)
    candidate = input_value.candidate
    content = input_value.content
    identity_values = (
        submission.review_version,
        submission.reviewer_key,
        submission.candidate_id,
        submission.document_id,
        submission.symbol,
        submission.issuer_identity,
    )
    if (
        any(not _safe_identifier(value) for value in identity_values)
        or (
            submission.supersedes_review_version is not None
            and not _safe_identifier(
                submission.supersedes_review_version
            )
        )
        or submission.review_method != "manual"
        or submission.candidate_id != candidate.candidate_id
        or submission.document_id != candidate.document_id
        or submission.symbol != candidate.symbol
        or submission.issuer_identity
        != candidate.issuer_identity
        or submission.content_sha256
        != candidate.content_sha256
        or not isinstance(
            submission.fact_supplements,
            (tuple, list),
        )
        or (
            submission.relation_review is not None
            and not isinstance(
                submission.relation_review,
                RiskDocumentVersionReview,
            )
        )
    ):
        return ("manual_risk_review_submission_unverified",)

    as_of = _aware_utc(input_value.as_of)
    fetched_at = _aware_utc(content.fetched_at)
    reviewed_at = _aware_utc(submission.reviewed_at)
    effective_until = (
        _aware_utc(submission.effective_until)
        if submission.effective_until is not None
        else None
    )
    if (
        as_of is None
        or fetched_at is None
        or reviewed_at is None
        or reviewed_at < fetched_at
        or reviewed_at > as_of
        or (
            submission.effective_until is not None
            and effective_until is None
        )
        or (
            effective_until is not None
            and effective_until < reviewed_at
        )
    ):
        return ("manual_risk_review_timestamp_unverified",)
    return ()


def _history_reasons(
    input_value: ManualRiskReviewArtifactInput,
    expected_artifact_id: str,
) -> Tuple[str, ...]:
    history = input_value.previous_artifacts
    submission = input_value.submission
    candidate = input_value.candidate
    if (
        not isinstance(history, (tuple, list))
        or any(
            not isinstance(
                artifact,
                AcceptedManualRiskReviewArtifact,
            )
            for artifact in history
        )
    ):
        return ("manual_risk_review_history_unverified",)
    if not history:
        return (
            ()
            if submission.supersedes_review_version is None
            else ("manual_risk_review_history_unverified",)
        )

    versions = []
    previous_time = None
    previous_version = None
    for artifact in history:
        reviewed_at = _aware_utc(artifact.reviewed_at)
        effective_until = (
            _aware_utc(artifact.effective_until)
            if artifact.effective_until is not None
            else None
        )
        fact_payload_valid = (
            candidate.candidate_kind
            == RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
            and bool(artifact.facts)
            and not artifact.relations
            and all(
                isinstance(fact, RiskDocumentFact)
                and fact.extractor_version
                == MANUAL_RISK_DOCUMENT_REVIEW_VERSION
                for fact in artifact.facts
            )
        )
        relation_payload_valid = (
            candidate.candidate_kind
            == RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED
            and not artifact.facts
            and bool(artifact.relations)
            and all(
                isinstance(
                    relation,
                    AcceptedRiskDocumentRelation,
                )
                for relation in artifact.relations
            )
        )
        valid = (
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
            and artifact.artifact_contract_version
            == MANUAL_RISK_DOCUMENT_REVIEW_VERSION
            and artifact.formal_usable is False
            and artifact.applied_to_d3 is False
            and artifact.applied_to_d1 is False
            and _safe_identifier(artifact.review_version)
            and reviewed_at is not None
            and (
                artifact.effective_until is None
                or (
                    effective_until is not None
                    and effective_until >= reviewed_at
                )
            )
            and (fact_payload_valid or relation_payload_valid)
            and (
                previous_time is None
                or reviewed_at > previous_time
            )
            and artifact.supersedes_review_version
            == previous_version
        )
        if not valid:
            return ("manual_risk_review_history_unverified",)
        versions.append(artifact.review_version)
        previous_time = reviewed_at
        previous_version = artifact.review_version

    new_reviewed_at = _aware_utc(submission.reviewed_at)
    if (
        len(versions) != len(set(versions))
        or submission.review_version in versions
        or submission.supersedes_review_version
        != history[-1].review_version
        or new_reviewed_at is None
        or previous_time is None
        or new_reviewed_at <= previous_time
    ):
        return ("manual_risk_review_history_unverified",)
    return ()


def _manual_facts(
    input_value: ManualRiskReviewArtifactInput,
) -> Tuple[Tuple[RiskDocumentFact, ...], Tuple[str, ...]]:
    submissions = input_value.submission.fact_supplements
    pages = {
        page.page_number: page.text
        for page in input_value.content.pages
    }
    facts = []
    reasons = []
    for submission in submissions:
        if (
            not isinstance(
                submission,
                ManualRiskDocumentFactSubmission,
            )
            or submission.page_number not in pages
        ):
            reasons.append("manual_risk_review_fact_unverified")
            continue
        fact, fact_reasons = _manual_fact(
            input_value.document.document_id,
            submission,
            pages[submission.page_number],
        )
        reasons.extend(fact_reasons)
        if fact is not None:
            facts.append(fact)
    keys = [
        (fact.fact_kind, fact.normalized_value, fact.page_number)
        for fact in facts
    ]
    if len(keys) != len(set(keys)):
        reasons.append("manual_risk_review_fact_duplicate")
    values_by_kind = {}
    for fact in facts:
        values_by_kind.setdefault(
            fact.fact_kind,
            set(),
        ).add(fact.normalized_value)
    if any(
        len(values) > 1
        for values in values_by_kind.values()
    ):
        reasons.append("manual_risk_review_fact_conflict")
    return tuple(facts), _dedupe(reasons)


def _relation_artifacts(
    input_value: ManualRiskReviewArtifactInput,
) -> Tuple[
    Tuple[AcceptedRiskDocumentRelation, ...],
    ResearchFeatureStatus,
    Tuple[str, ...],
]:
    submission = input_value.submission
    review = submission.relation_review
    if review is None:
        return (
            (),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("manual_risk_review_relation_missing",),
        )
    if (
        submission.fact_supplements
        or review.review_method != "manual"
        or review.mapping_version != submission.review_version
        or review.reviewer_key != submission.reviewer_key
        or _aware_utc(review.reviewed_at)
        != _aware_utc(submission.reviewed_at)
        or (
            _aware_utc(review.effective_until)
            if review.effective_until is not None
            else None
        )
        != (
            _aware_utc(submission.effective_until)
            if submission.effective_until is not None
            else None
        )
        or review.source_document_id
        != input_value.document.document_id
    ):
        return (
            (),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("manual_risk_review_relation_unverified",),
        )
    verified = _reference_fact_result(
        input_value,
        reviews=(review,),
    )
    if (
        verified.status != ResearchFeatureStatus.READY
        or not verified.relations
    ):
        return (), verified.status, verified.reasons
    return verified.relations, ResearchFeatureStatus.READY, ()


def build_manual_risk_review_artifact(
    input_value: Any,
) -> ManualRiskReviewArtifactResult:
    """验证一次离线人工审核并生成未应用的压缩版本工件。"""

    if not isinstance(
        input_value,
        ManualRiskReviewArtifactInput,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("manual_risk_review_contract_unverified",),
        )

    identity_reasons = _identity_reasons(input_value)
    if identity_reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            identity_reasons,
        )
    submission_reasons = _submission_reasons(input_value)
    if submission_reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            submission_reasons,
        )

    submission = input_value.submission
    artifact_id = _artifact_id(input_value.candidate)
    history_reasons = _history_reasons(
        input_value,
        artifact_id,
    )
    if history_reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            history_reasons,
        )

    facts: Tuple[RiskDocumentFact, ...] = ()
    relations: Tuple[AcceptedRiskDocumentRelation, ...] = ()
    candidate_kind = input_value.candidate.candidate_kind
    if (
        candidate_kind
        == RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
    ):
        if (
            not submission.fact_supplements
            or submission.relation_review is not None
        ):
            return _result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("manual_risk_review_fact_submission_unverified",),
            )
        facts, fact_reasons = _manual_facts(input_value)
        if fact_reasons or not facts:
            return _result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                (
                    *fact_reasons,
                    *(
                        ("manual_risk_review_fact_missing",)
                        if not facts
                        else ()
                    ),
                ),
            )
    elif (
        candidate_kind
        == RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED
    ):
        relations, relation_status, relation_reasons = (
            _relation_artifacts(input_value)
        )
        if relation_status != ResearchFeatureStatus.READY:
            return _result(
                relation_status,
                relation_reasons,
            )
    else:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("manual_risk_review_candidate_unverified",),
        )

    effective_until = (
        _aware_utc(submission.effective_until)
        if submission.effective_until is not None
        else None
    )
    as_of = _aware_utc(input_value.as_of)
    assert as_of is not None
    if effective_until is not None and effective_until < as_of:
        return _result(
            ResearchFeatureStatus.STALE,
            ("manual_risk_review_artifact_expired",),
        )

    artifact = AcceptedManualRiskReviewArtifact(
        artifact_id=artifact_id,
        review_version=submission.review_version,
        supersedes_review_version=(
            submission.supersedes_review_version
        ),
        candidate_id=input_value.candidate.candidate_id,
        candidate_kind=candidate_kind,
        document_id=input_value.candidate.document_id,
        symbol=input_value.candidate.symbol,
        issuer_identity=input_value.candidate.issuer_identity,
        content_sha256=input_value.candidate.content_sha256,
        review_method="manual",
        reviewer_key=submission.reviewer_key,
        reviewed_at=_aware_utc(submission.reviewed_at)
        or submission.reviewed_at,
        effective_until=effective_until,
        facts=facts,
        relations=relations,
    )
    return _result(
        ResearchFeatureStatus.READY,
        (),
        artifact,
    )
