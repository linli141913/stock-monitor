import hashlib
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentPage,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
    FormalRiskGateGap,
    RiskResearchEvidenceBundleInput,
    build_risk_research_evidence_bundle,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
    RiskEventSubtype,
    RiskEvidenceSourceKind,
    RiskOfficialStatus,
)
from radar.leader_risk_review_artifacts import (
    MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    AcceptedManualRiskReviewArtifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_supplemented_relation import (
    SupplementedRiskDocumentRelationInput,
    review_supplemented_risk_document_relation,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)
PUBLISHED_AT = AS_OF - timedelta(days=2)
FETCHED_AT = AS_OF - timedelta(hours=5)
ARTIFACT_REVIEWED_AT = AS_OF - timedelta(hours=2)
RELATION_REVIEWED_AT = AS_OF - timedelta(hours=1)
CONTENT_SHA256 = "d" * 64
DOCUMENT_ID = "cninfo:1225443882"
TARGET_DOCUMENT_ID = "cninfo:1224000001"
ISSUER_IDENTITY = "cninfo-org:9900012108"
RAW_CASE_ID = "证监立案字0202026001号"
CASE_ID = "case:" + hashlib.sha256(
    RAW_CASE_ID.encode("utf-8")
).hexdigest()
DOCUMENT_TITLE = "不应进入D8证据包的公告标题"
SOURCE_URL = (
    "https://static.cninfo.com.cn/finalpage/"
    "2026-07-27/1225443882.PDF"
)
EVENT_SUMMARY = "不应进入D8证据包的事件敏感摘要。"
REVIEW_SUMMARY = "不应进入D8证据包的人工审核敏感摘要。"


def make_document():
    return OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        issuer_name="ST恒信",
        title=DOCUMENT_TITLE,
        published_at=PUBLISHED_AT,
        source_name="巨潮资讯",
        source_url=SOURCE_URL,
        candidate_category=RiskCategory.INVESTIGATION,
        formal_usable=False,
    )


def make_event(**changes):
    value = LeaderRiskEventEvidence(
        event_id="risk-event-1",
        event_version="v1",
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        category=RiskCategory.INVESTIGATION,
        event_subtype=RiskEventSubtype.FORMAL_INVESTIGATION,
        case_id=CASE_ID,
        source_kind=(
            RiskEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
        ),
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-01-01/1224000001.PDF"
        ),
        document_id=TARGET_DOCUMENT_ID,
        published_at=PUBLISHED_AT - timedelta(days=30),
        effective_from=PUBLISHED_AT - timedelta(days=30),
        effective_until=None,
        reporting_period=None,
        fact_summary=EVENT_SUMMARY,
        official_status=RiskOfficialStatus.ACTIVE,
    )
    return replace(value, **changes)


def make_manual_fact(kind, value):
    identity = f"{DOCUMENT_ID}|{kind.value}|{value}|1"
    return RiskDocumentFact(
        fact_id="riskfact:" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest(),
        fact_kind=kind,
        normalized_value=value,
        page_number=1,
        fragment_sha256="a" * 64,
        extractor_version=MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    )


def make_relation_chain(
    *,
    relation_kind=RiskDocumentRelationKind.RESOLVES,
    replacement_event_version=None,
    effective_until=None,
):
    event = make_event()
    document = make_document()
    page_text = f"本页版式没有标准标签。\n{RAW_CASE_ID}\n"
    content = OfficialRiskDocumentContentResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        byte_count=125_092,
        page_count=1,
        pages=(OfficialRiskDocumentPage(1, page_text),),
        fetched_at=FETCHED_AT,
        reasons=(),
    )
    facts = extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=AS_OF,
            document=document,
            content_sha256=CONTENT_SHA256,
            pages=content.pages,
            extracted_at=FETCHED_AT,
            source_status=content.status,
            event_versions=(event,),
            reviews=(),
        )
    )
    candidate = build_risk_document_review_candidate(
        content,
        facts,
    ).candidate
    artifact_material = (
        f"{candidate.candidate_id}|{candidate.candidate_kind.value}"
    )
    artifact = AcceptedManualRiskReviewArtifact(
        artifact_id=hashlib.sha256(
            artifact_material.encode("utf-8")
        ).hexdigest(),
        review_version="manual-review-v1",
        supersedes_review_version=None,
        candidate_id=candidate.candidate_id,
        candidate_kind=(
            RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
        ),
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=ARTIFACT_REVIEWED_AT,
        effective_until=None,
        facts=(
            make_manual_fact(
                RiskDocumentFactKind.CASE_ID,
                CASE_ID,
            ),
            make_manual_fact(
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
                TARGET_DOCUMENT_ID,
            ),
        ),
        relations=(),
    )
    replay_input = RiskDocumentResearchReplayInput(
        as_of=AS_OF,
        document=document,
        content=content,
        facts=facts,
        event_versions=(event,),
        artifacts=(artifact,),
    )
    replay_result = replay_risk_document_research_evidence(
        replay_input
    )
    review = RiskDocumentVersionReview(
        review_id="supplemented-review-1",
        mapping_version="supplemented-map-v1",
        relation_kind=relation_kind,
        review_method="manual",
        reviewer_key="reviewer-local-2",
        reviewed_at=RELATION_REVIEWED_AT,
        effective_until=effective_until,
        source_document_id=DOCUMENT_ID,
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        target_document_id=event.document_id,
        replacement_event_version=replacement_event_version,
        basis_fact_ids=tuple(
            fact.fact_id for fact in replay_result.manual_facts
        ),
        decision_summary=REVIEW_SUMMARY,
    )
    relation_input = SupplementedRiskDocumentRelationInput(
        replay_input=replay_input,
        replay_result=replay_result,
        review=review,
    )
    relation_result = review_supplemented_risk_document_relation(
        relation_input
    )
    return relation_input, relation_result


def build_bundle(relation_input, relation_result):
    return build_risk_research_evidence_bundle(
        RiskResearchEvidenceBundleInput(
            relation_input=relation_input,
            relation_result=relation_result,
        )
    )


class RiskResearchEvidenceBundleTests(unittest.TestCase):
    def test_ready_chain_builds_compressed_non_formal_bundle(self):
        relation_input, relation_result = make_relation_chain()

        result = build_bundle(relation_input, relation_result)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.bundle)
        bundle = result.bundle
        relation = relation_result.relation
        replay = relation_input.replay_result
        self.assertTrue(
            bundle.bundle_id.startswith(
                "risk-research-evidence-bundle:"
            )
        )
        self.assertEqual(bundle.document_id, DOCUMENT_ID)
        self.assertEqual(bundle.content_sha256, CONTENT_SHA256)
        self.assertEqual(
            bundle.deterministic_fact_ids,
            tuple(fact.fact_id for fact in replay.deterministic_facts),
        )
        self.assertEqual(
            bundle.manual_fact_ids,
            tuple(fact.fact_id for fact in replay.manual_facts),
        )
        self.assertEqual(bundle.relation_id, relation.relation_id)
        self.assertEqual(
            bundle.target_event_version,
            relation.target_event_version,
        )
        self.assertEqual(
            bundle.bundle_contract_id,
            RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
        )
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.applied_to_d3)
        self.assertFalse(result.applied_to_d1)

    def test_formal_gate_gaps_are_complete_and_explicit(self):
        relation_input, relation_result = make_relation_chain()

        result = build_bundle(relation_input, relation_result)

        self.assertEqual(
            result.formal_gate_gaps,
            (
                FormalRiskGateGap.SOURCE_DOCUMENT_NOT_FORMAL,
                FormalRiskGateGap.CONTENT_NOT_FORMAL,
                FormalRiskGateGap.FACT_RESULT_NOT_FORMAL,
                FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE,
                FormalRiskGateGap.RESEARCH_REPLAY_NOT_FORMAL,
                FormalRiskGateGap.RESEARCH_RELATION_NOT_FORMAL,
                FormalRiskGateGap.D3_APPLICATION_MISSING,
                FormalRiskGateGap.D1_RESOLUTION_EVIDENCE_MISSING,
            ),
        )
        self.assertEqual(
            result.bundle.formal_gate_gaps,
            result.formal_gate_gaps,
        )

    def test_same_chain_builds_the_same_frozen_bundle(self):
        relation_input, relation_result = make_relation_chain()

        first = build_bundle(relation_input, relation_result)
        second = build_bundle(relation_input, relation_result)

        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.bundle.bundle_id = "forged"

    def test_forged_d7_result_is_rejected(self):
        relation_input, relation_result = make_relation_chain()
        forged = replace(
            relation_result,
            relation=replace(
                relation_result.relation,
                target_event_version="v0",
            ),
        )

        result = build_bundle(relation_input, forged)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.bundle)
        self.assertIn(
            "risk_evidence_bundle_relation_replay_mismatch",
            result.reasons,
        )

    def test_stale_d7_status_is_propagated_without_a_bundle(self):
        relation_input, relation_result = make_relation_chain(
            effective_until=AS_OF - timedelta(seconds=1),
        )

        result = build_bundle(relation_input, relation_result)

        self.assertEqual(result.status, ResearchFeatureStatus.STALE)
        self.assertIsNone(result.bundle)
        self.assertEqual(result.reasons, relation_result.reasons)

    def test_supersedes_replacement_version_is_preserved(self):
        relation_input, relation_result = make_relation_chain(
            relation_kind=RiskDocumentRelationKind.SUPERSEDES,
            replacement_event_version="v2",
        )

        result = build_bundle(relation_input, relation_result)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.bundle.relation_kind,
            RiskDocumentRelationKind.SUPERSEDES,
        )
        self.assertEqual(
            result.bundle.replacement_event_version,
            "v2",
        )

    def test_malformed_contract_returns_a_stable_failure(self):
        result = build_risk_research_evidence_bundle(None)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.bundle)
        self.assertEqual(
            result.reasons,
            ("risk_evidence_bundle_contract_unverified",),
        )

    def test_repr_and_bundle_hide_sensitive_source_text(self):
        relation_input, relation_result = make_relation_chain()
        input_value = RiskResearchEvidenceBundleInput(
            relation_input=relation_input,
            relation_result=relation_result,
        )

        result = build_risk_research_evidence_bundle(input_value)
        rendered = repr(input_value) + repr(result)

        for secret in (
            RAW_CASE_ID,
            EVENT_SUMMARY,
            REVIEW_SUMMARY,
            SOURCE_URL,
            DOCUMENT_TITLE,
        ):
            self.assertNotIn(secret, rendered)
        self.assertFalse(hasattr(result.bundle, "source_url"))
        self.assertFalse(hasattr(result.bundle, "decision_summary"))
        self.assertFalse(hasattr(result.bundle, "fact_summary"))


if __name__ == "__main__":
    unittest.main()
