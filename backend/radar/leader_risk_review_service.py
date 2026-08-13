"""D8人工审核预览与提交编排。

这里重新从数据库正文构建事实、候选和关系，浏览器提交的派生字段只作为
校验输入，不作为可信结果。提交结果仍然是研究性、正式可用为 False。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Tuple
from urllib.parse import urlsplit

from radar.api_contracts import RadarLeaderReviewVersionRequest
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
    RiskEventSubtype,
    RiskEvidenceSourceKind,
    RiskOfficialStatus,
)
from radar.leader_risk_lifecycle_batch import LeaderRiskLifecycleReviewVersion
from radar.leader_risk_evidence_bundle import (
    RiskResearchEvidenceBundleInput,
    build_risk_research_evidence_bundle,
)
from radar.leader_risk_evidence_bundle_audit import (
    RiskResearchEvidenceBundleAuditInput,
    audit_risk_research_evidence_bundle_versions,
)
from radar.leader_risk_review_artifacts import (
    ManualRiskDocumentFactSubmission,
    ManualRiskReviewArtifactInput,
    ManualRiskReviewSubmission,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_supplemented_relation import (
    SupplementedRiskDocumentRelationInput,
    review_supplemented_risk_document_relation,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.repository import RepositoryConflictError, RepositoryStateError
from radar.sources.leader_risk_document_content import (
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
)


UTC = timezone.utc
REQUIRED_FACT_KINDS = {
    RiskCategory.REDUCTION: (RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,),
    RiskCategory.UNLOCK: (RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,),
    RiskCategory.REGULATORY: (RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,),
    RiskCategory.INVESTIGATION: (
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        RiskDocumentFactKind.CASE_ID,
    ),
    RiskCategory.LITIGATION: (
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        RiskDocumentFactKind.CASE_ID,
    ),
    RiskCategory.EARNINGS: (
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        RiskDocumentFactKind.REPORTING_PERIOD,
    ),
    RiskCategory.AUDIT: (
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        RiskDocumentFactKind.REPORTING_PERIOD,
    ),
}


def _now(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("D8审核时间必须带时区")
    return value.astimezone(UTC)


def _candidate_context(
    repository: LeaderRiskReviewRepository,
    *,
    review_batch_id: str,
    document_id: str,
    candidate_category: str,
    as_of: datetime,
):
    document, content, versions = repository.get_review_batch_document_content(
        review_batch_id,
        document_id,
        candidate_category,
    )
    facts = extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=as_of,
            document=document,
            content_sha256=content.content_sha256 or "",
            pages=content.pages,
            extracted_at=content.fetched_at or as_of,
            source_status=content.status,
            event_versions=(),
            reviews=(),
        )
    )
    candidate_result = build_risk_document_review_candidate(content, facts)
    if (
        facts.status != ResearchFeatureStatus.MISSING
        or candidate_result.status != ResearchFeatureStatus.READY
        or candidate_result.candidate is None
    ):
        raise RepositoryStateError("正文事实候选当前不可进入人工审核")
    return document, content, facts, candidate_result.candidate, versions


def build_review_form_data(
    repository: LeaderRiskReviewRepository,
    *,
    review_batch_id: str,
    document_id: str,
    candidate_category: str,
    as_of: datetime,
    write_enabled: bool,
) -> Mapping[str, Any]:
    as_of = _now(as_of)
    document, content, facts, candidate, versions = _candidate_context(
        repository,
        review_batch_id=review_batch_id,
        document_id=document_id,
        candidate_category=candidate_category,
        as_of=as_of,
    )
    if candidate.candidate_kind != RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING:
        raise RepositoryStateError("当前候选需要关系复核，不属于首版事实补录表单")
    return {
        "document": document,
        "content": content,
        "candidate": candidate,
        "autoFacts": facts,
        "versions": versions,
        "requiredFactKinds": [
            item.value for item in REQUIRED_FACT_KINDS[document.candidate_category]
        ],
        "asOf": as_of,
        "writeEnabled": write_enabled,
        "writeReasonCode": (
            "d8_manual_review_write_enabled"
            if write_enabled else "d8_manual_review_write_disabled"
        ),
        "replayDiagnostic": build_review_replay_diagnostic(versions),
    }


def _review_replay_diagnostic(
    *,
    status: ResearchFeatureStatus,
    versions: Tuple[LeaderRiskLifecycleReviewVersion, ...],
    bundles: Tuple[Any, ...],
    reasons: Tuple[str, ...],
) -> Mapping[str, Any]:
    active_review_version = (
        versions[-1].submission.review_version
        if versions else None
    )
    return {
        "status": status.value,
        "reviewVersionCount": len(versions),
        "bundleCount": len(bundles),
        "activeReviewVersion": active_review_version,
        "materialChangeRequired": (
            len(versions) < 2
            or "risk_evidence_bundle_audit_material_change_missing"
            in reasons
        ),
        "reasonCodes": list(dict.fromkeys(reasons)),
        "formalUsable": False,
    }


def build_review_replay_diagnostic(
    versions: Tuple[LeaderRiskLifecycleReviewVersion, ...],
) -> Mapping[str, Any]:
    """只读重建D5-D9版本链，并返回适合接口展示的压缩诊断。"""

    if not isinstance(versions, tuple) or any(
        not isinstance(version, LeaderRiskLifecycleReviewVersion)
        for version in versions
    ):
        return _review_replay_diagnostic(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            versions=(),
            bundles=(),
            reasons=("risk_lifecycle_version_contract_unverified",),
        )
    if not versions:
        return _review_replay_diagnostic(
            status=ResearchFeatureStatus.MISSING,
            versions=versions,
            bundles=(),
            reasons=("risk_lifecycle_version_history_insufficient",),
        )

    artifacts = []
    bundles = []
    for version in versions:
        artifact_result = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=version.as_of,
                document=version.document,
                content=version.content,
                facts=version.facts,
                candidate=version.candidate,
                event_versions=version.event_versions,
                submission=version.submission,
                previous_artifacts=tuple(artifacts),
            )
        )
        if (
            artifact_result.status != ResearchFeatureStatus.READY
            or artifact_result.artifact is None
        ):
            return _review_replay_diagnostic(
                status=artifact_result.status,
                versions=versions,
                bundles=tuple(bundles),
                reasons=artifact_result.reasons,
            )
        artifacts.append(artifact_result.artifact)

        replay_input = RiskDocumentResearchReplayInput(
            as_of=version.as_of,
            document=version.document,
            content=version.content,
            facts=version.facts,
            event_versions=version.event_versions,
            artifacts=tuple(artifacts),
        )
        replay_result = replay_risk_document_research_evidence(replay_input)
        relation_input = SupplementedRiskDocumentRelationInput(
            replay_input=replay_input,
            replay_result=replay_result,
            review=version.relation_review,
        )
        relation_result = review_supplemented_risk_document_relation(
            relation_input
        )
        bundle_result = build_risk_research_evidence_bundle(
            RiskResearchEvidenceBundleInput(
                relation_input=relation_input,
                relation_result=relation_result,
            )
        )
        if (
            bundle_result.status != ResearchFeatureStatus.READY
            or bundle_result.bundle is None
        ):
            return _review_replay_diagnostic(
                status=bundle_result.status,
                versions=versions,
                bundles=tuple(bundles),
                reasons=bundle_result.reasons,
            )
        bundles.append(bundle_result.bundle)

    audit = audit_risk_research_evidence_bundle_versions(
        RiskResearchEvidenceBundleAuditInput(bundles=tuple(bundles))
    )
    return _review_replay_diagnostic(
        status=audit.status,
        versions=versions,
        bundles=tuple(bundles),
        reasons=audit.reasons,
    )


def _fact_submissions(
    request: RadarLeaderReviewVersionRequest,
) -> Tuple[ManualRiskDocumentFactSubmission, ...]:
    return tuple(
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind(item.fact_kind),
            source_value=item.source_value,
            page_number=item.page_number,
            source_fragment=item.source_fragment,
            mapped_document_id=item.mapped_document_id,
        )
        for item in request.fact_supplements
    )


def _fact_by_kind(artifact: Any, kind: RiskDocumentFactKind):
    matches = tuple(fact for fact in artifact.facts if fact.fact_kind == kind)
    if len(matches) != 1:
        raise ValueError(f"人工事实缺少唯一{kind.value}")
    return matches[0].normalized_value


def _event_id(request: RadarLeaderReviewVersionRequest) -> str:
    material = "|".join((
        request.document_id,
        request.target_event.document_id,
        request.content_sha256,
    ))
    return "risk-event-manual:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _material_review_signature(
    artifact: Any,
    event: LeaderRiskEventEvidence,
    relation: RiskDocumentVersionReview,
) -> Tuple[Any, ...]:
    return (
        tuple(sorted(fact.fact_id for fact in artifact.facts)),
        relation.relation_kind,
        relation.replacement_event_version,
        tuple(sorted(relation.basis_fact_ids)),
        event.official_status,
        _now(event.published_at),
    )


def _build_event(
    request: RadarLeaderReviewVersionRequest,
    *,
    as_of: datetime,
    document: Any,
    target_document_id: str,
    case_id: str | None,
    reporting_period: str | None,
) -> LeaderRiskEventEvidence:
    category = RiskCategory(request.candidate_category)
    parsed_url = urlsplit(request.target_event.source_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "static.cninfo.com.cn"
        or not parsed_url.path.lower().endswith(".pdf")
        or parsed_url.path.rsplit("/", 1)[-1][:-4]
        != target_document_id.removeprefix("cninfo:")
    ):
        raise ValueError("原公告地址必须与巨潮公告编号一致")
    published_at = _now(request.target_event.published_at)
    effective_from = _now(request.target_event.effective_from)
    effective_until = (
        _now(request.target_event.effective_until)
        if request.target_event.effective_until is not None else None
    )
    current_published_at = _now(document.published_at)
    if (
        published_at >= current_published_at
        or effective_from > as_of
        or (
            effective_until is not None
            and effective_until < effective_from
        )
    ):
        raise ValueError("原风险事件时间顺序无效")
    if (
        category in {RiskCategory.REDUCTION, RiskCategory.UNLOCK}
        and effective_until is None
    ):
        raise ValueError("减持或解禁事件必须填写结束时间")
    return LeaderRiskEventEvidence(
        event_id=_event_id(request),
        event_version=request.target_event.event_version,
        symbol=document.symbol,
        issuer_identity=document.issuer_identity,
        category=category,
        event_subtype=RiskEventSubtype(request.target_event.event_subtype),
        case_id=case_id,
        source_kind=RiskEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM,
        source_name=document.source_name,
        source_url=request.target_event.source_url,
        document_id=target_document_id,
        published_at=published_at,
        effective_from=effective_from,
        effective_until=effective_until,
        reporting_period=reporting_period,
        fact_summary=request.target_event.fact_summary,
        official_status=RiskOfficialStatus(request.target_event.official_status),
    )


def build_manual_review_version(
    repository: LeaderRiskReviewRepository,
    request: RadarLeaderReviewVersionRequest,
    *,
    as_of: datetime,
) -> Tuple[LeaderRiskLifecycleReviewVersion, bool]:
    as_of = _now(as_of)
    context = _candidate_context(
        repository,
        review_batch_id=request.review_batch_id,
        document_id=request.document_id,
        candidate_category=request.candidate_category,
        as_of=as_of,
    )
    document, content, facts, candidate, previous_versions = context
    if candidate.candidate_kind != RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING:
        raise RepositoryStateError("当前候选要求关系复核，首版接口拒绝此提交")
    if request.content_sha256 != content.content_sha256:
        raise RepositoryConflictError("正文校验值已变化，请重新打开审核表单")
    if request.candidate_id != candidate.candidate_id:
        raise RepositoryConflictError("审核候选已变化，请重新打开审核表单")

    fact_submissions = _fact_submissions(request)
    previous_version = previous_versions[-1] if previous_versions else None
    previous_artifacts = []
    for stored_version in previous_versions:
        artifact_result = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=stored_version.as_of,
                document=stored_version.document,
                content=stored_version.content,
                facts=stored_version.facts,
                candidate=stored_version.candidate,
                event_versions=stored_version.event_versions,
                submission=stored_version.submission,
                previous_artifacts=tuple(previous_artifacts),
            )
        )
        if (
            artifact_result.status != ResearchFeatureStatus.READY
            or artifact_result.artifact is None
        ):
            raise RepositoryStateError("已有人工审核版本链无法重建")
        previous_artifacts.append(artifact_result.artifact)
    review_version = f"manual-review-v{len(previous_versions) + 1}"
    if request.confirm_official_evidence is not True:
        raise ValueError("必须确认当前正文来自官方原文")
    submission_reviewed_at = as_of - timedelta(seconds=1)
    if content.fetched_at is not None and submission_reviewed_at < content.fetched_at:
        raise ValueError("正文抓取时间过晚，无法形成有序人工审核时间链")
    submission = ManualRiskReviewSubmission(
        review_version=review_version,
        supersedes_review_version=(
            previous_version.submission.review_version
            if previous_version is not None else None
        ),
        review_method="manual",
        reviewer_key=request.reviewer_key,
        reviewed_at=submission_reviewed_at,
        effective_until=request.effective_until,
        candidate_id=candidate.candidate_id,
        document_id=document.document_id,
        symbol=document.symbol,
        issuer_identity=document.issuer_identity,
        content_sha256=content.content_sha256 or "",
        fact_supplements=fact_submissions,
        relation_review=None,
    )

    provisional_artifact = build_manual_risk_review_artifact(
        ManualRiskReviewArtifactInput(
            as_of=as_of,
            document=document,
            content=content,
            facts=facts,
            candidate=candidate,
            event_versions=(),
            submission=submission,
            previous_artifacts=tuple(previous_artifacts),
        )
    )
    if (
        provisional_artifact.status != ResearchFeatureStatus.READY
        or provisional_artifact.artifact is None
    ):
        raise ValueError("人工事实未通过正文页码和原文片段校验")
    artifact = provisional_artifact.artifact
    target_document_id = _fact_by_kind(
        artifact,
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
    )
    category = document.candidate_category
    case_id = (
        _fact_by_kind(artifact, RiskDocumentFactKind.CASE_ID)
        if category in {RiskCategory.INVESTIGATION, RiskCategory.LITIGATION}
        else None
    )
    reporting_period = (
        _fact_by_kind(artifact, RiskDocumentFactKind.REPORTING_PERIOD)
        if category in {RiskCategory.EARNINGS, RiskCategory.AUDIT}
        else None
    )
    if request.target_event.document_id != target_document_id:
        raise ValueError("关联原公告编号必须与正文补录的原公告编号一致")

    event = _build_event(
        request,
        as_of=as_of,
        document=document,
        target_document_id=target_document_id,
        case_id=case_id,
        reporting_period=reporting_period,
    )
    final_facts = extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=as_of,
            document=document,
            content_sha256=content.content_sha256 or "",
            pages=content.pages,
            extracted_at=content.fetched_at or as_of,
            source_status=content.status,
            event_versions=(event,),
            reviews=(),
        )
    )
    final_candidate_result = build_risk_document_review_candidate(
        content,
        final_facts,
    )
    if (
        final_candidate_result.status != ResearchFeatureStatus.READY
        or final_candidate_result.candidate != candidate
    ):
        raise RepositoryConflictError("正文候选在提交时发生变化，请重新打开审核表单")
    final_artifact_result = build_manual_risk_review_artifact(
        ManualRiskReviewArtifactInput(
            as_of=as_of,
            document=document,
            content=content,
            facts=final_facts,
            candidate=candidate,
            event_versions=(event,),
            submission=submission,
            previous_artifacts=tuple(previous_artifacts),
        )
    )
    if (
        final_artifact_result.status != ResearchFeatureStatus.READY
        or final_artifact_result.artifact is None
    ):
        raise ValueError("人工事实与最终事件版本无法共同通过校验")
    facts_by_kind = {
        fact.fact_kind: fact.fact_id
        for fact in final_artifact_result.artifact.facts
    }
    basis_kinds = [RiskDocumentFactKind.REFERENCED_DOCUMENT_ID]
    if category in {RiskCategory.INVESTIGATION, RiskCategory.LITIGATION}:
        basis_kinds.append(RiskDocumentFactKind.CASE_ID)
    if category in {RiskCategory.EARNINGS, RiskCategory.AUDIT}:
        basis_kinds.append(RiskDocumentFactKind.REPORTING_PERIOD)
    basis_fact_ids = tuple(facts_by_kind[kind] for kind in basis_kinds)
    if (
        request.relation_kind == "resolves"
        and request.replacement_event_version is not None
    ) or (
        request.relation_kind == "supersedes"
        and (
            not request.replacement_event_version
            or request.replacement_event_version
            == request.target_event.event_version
        )
    ):
        raise ValueError("关系类型与替代事件版本不一致")
    relation = RiskDocumentVersionReview(
        review_id="risk-review-manual:" + hashlib.sha256(
            f"{request.document_id}|{review_version}".encode("utf-8")
        ).hexdigest(),
        mapping_version=review_version,
        relation_kind=RiskDocumentRelationKind(request.relation_kind),
        review_method="manual",
        reviewer_key=request.reviewer_key,
        reviewed_at=as_of,
        effective_until=request.effective_until,
        source_document_id=document.document_id,
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        target_document_id=event.document_id,
        replacement_event_version=request.replacement_event_version,
        basis_fact_ids=basis_fact_ids,
        decision_summary=request.decision_summary,
    )
    if previous_version is not None:
        previous_events = previous_version.event_versions
        previous_relation = previous_version.relation_review
        if (
            len(previous_events) != 1
            or not isinstance(
                previous_relation,
                RiskDocumentVersionReview,
            )
        ):
            raise RepositoryStateError("已有人工审核版本链无法重建")
        if _material_review_signature(
            previous_artifacts[-1],
            previous_events[0],
            previous_relation,
        ) == _material_review_signature(
            final_artifact_result.artifact,
            event,
            relation,
        ):
            raise ValueError("第二个人工审核版本缺少实质证据变化")
    return LeaderRiskLifecycleReviewVersion(
        as_of=as_of,
        document=document,
        content=content,
        facts=final_facts,
        candidate=candidate,
        event_versions=(event,),
        submission=submission,
        relation_review=relation,
    ), True
