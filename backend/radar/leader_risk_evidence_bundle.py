"""阶段6L-D8风险研究证据包与正式门禁差距审计。

本模块只重算D7并封装D2-D7压缩引用。证据包就绪不等于正式门禁通过，
也不会生成D1解除证据、修改上游对象或连接数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    RiskDocumentFact,
    RiskDocumentRelationKind,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskOfficialStatus,
)
from radar.leader_risk_review_artifacts import (
    AcceptedManualRiskReviewArtifact,
)
from radar.leader_risk_supplemented_relation import (
    AcceptedSupplementedRiskDocumentRelation,
    SupplementedRiskDocumentRelationInput,
    SupplementedRiskDocumentRelationResult,
    review_supplemented_risk_document_relation,
)


RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID = (
    "radar-leader-risk-research-evidence-bundle-v1"
)


class FormalRiskGateGap(str, Enum):
    SOURCE_DOCUMENT_NOT_FORMAL = "source_document_not_formal"
    CONTENT_NOT_FORMAL = "content_not_formal"
    FACT_RESULT_NOT_FORMAL = "fact_result_not_formal"
    CORRECTION_LINKS_INCOMPLETE = "correction_links_incomplete"
    RESEARCH_REPLAY_NOT_FORMAL = "research_replay_not_formal"
    RESEARCH_RELATION_NOT_FORMAL = "research_relation_not_formal"
    D3_APPLICATION_MISSING = "d3_application_missing"
    D1_RESOLUTION_EVIDENCE_MISSING = (
        "d1_resolution_evidence_missing"
    )


@dataclass(frozen=True)
class RiskResearchEvidenceBundleInput:
    relation_input: SupplementedRiskDocumentRelationInput = field(
        repr=False,
    )
    relation_result: SupplementedRiskDocumentRelationResult = field(
        repr=False,
    )


@dataclass(frozen=True)
class RiskResearchEvidenceBundle:
    bundle_id: str
    built_at: datetime
    document_source_contract_id: str
    document_id: str
    symbol: str
    issuer_identity: str
    content_contract_id: str
    content_sha256: str
    content_fetched_at: datetime
    deterministic_fact_ids: Tuple[str, ...]
    manual_fact_ids: Tuple[str, ...]
    merged_fact_ids: Tuple[str, ...]
    active_artifact_id: str
    active_artifact_version: str
    artifact_contract_version: str
    replay_version: str
    relation_id: str
    review_id: str
    mapping_version: str
    relation_kind: RiskDocumentRelationKind
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    manual_basis_fact_ids: Tuple[str, ...]
    target_event_official_status: RiskOfficialStatus
    target_event_published_at: datetime
    formal_gate_gaps: Tuple[FormalRiskGateGap, ...]
    bundle_contract_id: str = (
        RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
    )
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False


@dataclass(frozen=True)
class RiskResearchEvidenceBundleResult:
    status: ResearchFeatureStatus
    bundle: Optional[RiskResearchEvidenceBundle] = None
    formal_gate_gaps: Tuple[FormalRiskGateGap, ...] = ()
    formal_gate_ready: bool = False
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
    bundle: Optional[RiskResearchEvidenceBundle] = None,
    formal_gate_gaps: Sequence[FormalRiskGateGap] = (),
) -> RiskResearchEvidenceBundleResult:
    return RiskResearchEvidenceBundleResult(
        status=status,
        bundle=bundle,
        formal_gate_gaps=tuple(formal_gate_gaps),
        reasons=_dedupe(reasons),
    )


def _active_artifact(
    input_value: SupplementedRiskDocumentRelationInput,
    relation: AcceptedSupplementedRiskDocumentRelation,
) -> Optional[AcceptedManualRiskReviewArtifact]:
    artifacts = input_value.replay_input.artifacts
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
            == relation.source_artifact_id
            and artifact.review_version
            == relation.source_artifact_version
        )
    )
    return matches[0] if len(matches) == 1 else None


def _target_event(
    input_value: SupplementedRiskDocumentRelationInput,
    relation: AcceptedSupplementedRiskDocumentRelation,
) -> Optional[LeaderRiskEventEvidence]:
    events = input_value.replay_input.event_versions
    if not isinstance(events, (tuple, list)):
        return None
    matches = tuple(
        event
        for event in events
        if (
            isinstance(event, LeaderRiskEventEvidence)
            and event.event_id == relation.target_event_id
            and event.event_version
            == relation.target_event_version
            and event.document_id == relation.target_document_id
        )
    )
    return matches[0] if len(matches) == 1 else None


def _fact_ids(
    facts: Any,
) -> Optional[Tuple[str, ...]]:
    if (
        not isinstance(facts, (tuple, list))
        or any(
            not isinstance(fact, RiskDocumentFact)
            for fact in facts
        )
    ):
        return None
    values = tuple(fact.fact_id for fact in facts)
    return values if len(values) == len(set(values)) else None


def _formal_gate_gaps(
    input_value: SupplementedRiskDocumentRelationInput,
    relation_result: SupplementedRiskDocumentRelationResult,
) -> Tuple[FormalRiskGateGap, ...]:
    replay_input = input_value.replay_input
    replay_result = input_value.replay_result
    gaps = []
    if replay_input.document.formal_usable is not True:
        gaps.append(
            FormalRiskGateGap.SOURCE_DOCUMENT_NOT_FORMAL
        )
    if replay_input.content.formal_usable is not True:
        gaps.append(FormalRiskGateGap.CONTENT_NOT_FORMAL)
    if replay_input.facts.formal_usable is not True:
        gaps.append(FormalRiskGateGap.FACT_RESULT_NOT_FORMAL)
    if (
        replay_input.facts.correction_links_complete is not True
        or replay_result.correction_links_complete is not True
    ):
        gaps.append(
            FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE
        )
    if replay_result.formal_usable is not True:
        gaps.append(
            FormalRiskGateGap.RESEARCH_REPLAY_NOT_FORMAL
        )
    if relation_result.formal_usable is not True:
        gaps.append(
            FormalRiskGateGap.RESEARCH_RELATION_NOT_FORMAL
        )
    if relation_result.applied_to_d3 is not True:
        gaps.append(FormalRiskGateGap.D3_APPLICATION_MISSING)
    gaps.append(
        FormalRiskGateGap.D1_RESOLUTION_EVIDENCE_MISSING
    )
    return tuple(gaps)


def _bundle_id(
    *,
    input_value: SupplementedRiskDocumentRelationInput,
    relation: AcceptedSupplementedRiskDocumentRelation,
    artifact: AcceptedManualRiskReviewArtifact,
    event: LeaderRiskEventEvidence,
    deterministic_fact_ids: Sequence[str],
    manual_fact_ids: Sequence[str],
    merged_fact_ids: Sequence[str],
    gaps: Sequence[FormalRiskGateGap],
) -> str:
    replay_input = input_value.replay_input
    replay_result = input_value.replay_result
    material = "|".join((
        RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
        replay_input.as_of.isoformat(),
        replay_input.document.source_contract_id,
        replay_input.document.document_id,
        replay_input.document.symbol,
        replay_input.document.issuer_identity,
        replay_input.content.content_contract_id,
        replay_input.content.content_sha256,
        replay_input.content.fetched_at.isoformat(),
        artifact.artifact_id,
        artifact.review_version,
        artifact.artifact_contract_version,
        replay_result.replay_version,
        relation.relation_id,
        relation.review_id,
        relation.mapping_version,
        relation.relation_kind.value,
        relation.target_event_id,
        relation.target_event_version,
        relation.target_document_id,
        relation.replacement_event_version or "",
        event.official_status.value,
        event.published_at.isoformat(),
        *deterministic_fact_ids,
        *manual_fact_ids,
        *merged_fact_ids,
        *relation.basis_fact_ids,
        *relation.manual_basis_fact_ids,
        *(gap.value for gap in gaps),
    ))
    return (
        "risk-research-evidence-bundle:"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


def build_risk_research_evidence_bundle(
    input_value: Any,
) -> RiskResearchEvidenceBundleResult:
    """重算D7并封装不可正式使用的D2-D7研究证据引用。"""

    if (
        not isinstance(
            input_value,
            RiskResearchEvidenceBundleInput,
        )
        or not isinstance(
            input_value.relation_input,
            SupplementedRiskDocumentRelationInput,
        )
        or not isinstance(
            input_value.relation_result,
            SupplementedRiskDocumentRelationResult,
        )
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_contract_unverified",),
        )

    replayed_relation = (
        review_supplemented_risk_document_relation(
            input_value.relation_input
        )
    )
    if replayed_relation != input_value.relation_result:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_relation_replay_mismatch",),
        )
    if replayed_relation.status != ResearchFeatureStatus.READY:
        return _result(
            replayed_relation.status,
            replayed_relation.reasons,
        )
    relation = replayed_relation.relation
    if not isinstance(
        relation,
        AcceptedSupplementedRiskDocumentRelation,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_relation_unverified",),
        )

    relation_input = input_value.relation_input
    replay_input = relation_input.replay_input
    replay_result = relation_input.replay_result
    artifact = _active_artifact(relation_input, relation)
    event = _target_event(relation_input, relation)
    deterministic_fact_ids = _fact_ids(
        replay_result.deterministic_facts
    )
    manual_fact_ids = _fact_ids(replay_result.manual_facts)
    merged_fact_ids = _fact_ids(replay_result.facts)
    if (
        artifact is None
        or event is None
        or deterministic_fact_ids is None
        or manual_fact_ids is None
        or merged_fact_ids is None
        or replay_input.content.fetched_at is None
        or replay_input.content.content_sha256 is None
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_chain_unverified",),
        )

    gaps = _formal_gate_gaps(
        relation_input,
        replayed_relation,
    )
    bundle = RiskResearchEvidenceBundle(
        bundle_id=_bundle_id(
            input_value=relation_input,
            relation=relation,
            artifact=artifact,
            event=event,
            deterministic_fact_ids=deterministic_fact_ids,
            manual_fact_ids=manual_fact_ids,
            merged_fact_ids=merged_fact_ids,
            gaps=gaps,
        ),
        built_at=replay_input.as_of,
        document_source_contract_id=(
            replay_input.document.source_contract_id
        ),
        document_id=replay_input.document.document_id,
        symbol=replay_input.document.symbol,
        issuer_identity=replay_input.document.issuer_identity,
        content_contract_id=(
            replay_input.content.content_contract_id
        ),
        content_sha256=replay_input.content.content_sha256,
        content_fetched_at=replay_input.content.fetched_at,
        deterministic_fact_ids=deterministic_fact_ids,
        manual_fact_ids=manual_fact_ids,
        merged_fact_ids=merged_fact_ids,
        active_artifact_id=artifact.artifact_id,
        active_artifact_version=artifact.review_version,
        artifact_contract_version=(
            artifact.artifact_contract_version
        ),
        replay_version=replay_result.replay_version,
        relation_id=relation.relation_id,
        review_id=relation.review_id,
        mapping_version=relation.mapping_version,
        relation_kind=relation.relation_kind,
        target_event_id=relation.target_event_id,
        target_event_version=relation.target_event_version,
        target_document_id=relation.target_document_id,
        replacement_event_version=(
            relation.replacement_event_version
        ),
        basis_fact_ids=relation.basis_fact_ids,
        manual_basis_fact_ids=relation.manual_basis_fact_ids,
        target_event_official_status=event.official_status,
        target_event_published_at=event.published_at,
        formal_gate_gaps=gaps,
    )
    return _result(
        ResearchFeatureStatus.READY,
        (),
        bundle=bundle,
        formal_gate_gaps=gaps,
    )
