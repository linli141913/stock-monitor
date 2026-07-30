import hashlib
import unittest
from dataclasses import replace
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
    RISK_DOCUMENT_RESEARCH_REPLAY_VERSION,
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_supplemented_relation import (
    SUPPLEMENTED_RISK_DOCUMENT_RELATION_CONTRACT_ID,
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
PAGE_TEXT = f"本页版式没有标准标签。\n{RAW_CASE_ID}\n"


def make_document(**changes):
    value = OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        issuer_name="ST恒信",
        title="关于收到中国证监会立案告知书的公告",
        published_at=PUBLISHED_AT,
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-27/1225443882.PDF"
        ),
        candidate_category=RiskCategory.INVESTIGATION,
        formal_usable=False,
    )
    return replace(value, **changes)


def make_content(**changes):
    value = OfficialRiskDocumentContentResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        byte_count=125_092,
        page_count=1,
        pages=(OfficialRiskDocumentPage(1, PAGE_TEXT),),
        fetched_at=FETCHED_AT,
        reasons=(),
    )
    return replace(value, **changes)


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
        fact_summary="不应进入D7对象表示的事件敏感摘要。",
        official_status=RiskOfficialStatus.ACTIVE,
    )
    return replace(value, **changes)


def make_fact(kind, value, *, page_number=1):
    identity = f"{DOCUMENT_ID}|{kind.value}|{value}|{page_number}"
    return RiskDocumentFact(
        fact_id="riskfact:" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest(),
        fact_kind=kind,
        normalized_value=value,
        page_number=page_number,
        fragment_sha256="a" * 64,
        extractor_version=MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    )


def make_context(*, event=None, artifact_changes=None):
    event = event or make_event()
    document = make_document()
    content = make_content()
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
    material = (
        f"{candidate.candidate_id}|{candidate.candidate_kind.value}"
    )
    artifact = AcceptedManualRiskReviewArtifact(
        artifact_id=hashlib.sha256(
            material.encode("utf-8")
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
            make_fact(RiskDocumentFactKind.CASE_ID, CASE_ID),
            make_fact(
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
                TARGET_DOCUMENT_ID,
            ),
        ),
        relations=(),
    )
    if artifact_changes:
        artifact = replace(artifact, **artifact_changes)
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
    return replay_input, replay_result, event


def make_review(
    replay_result,
    event,
    **changes,
):
    value = RiskDocumentVersionReview(
        review_id="supplemented-review-1",
        mapping_version="supplemented-map-v1",
        relation_kind=RiskDocumentRelationKind.RESOLVES,
        review_method="manual",
        reviewer_key="reviewer-local-2",
        reviewed_at=RELATION_REVIEWED_AT,
        effective_until=None,
        source_document_id=DOCUMENT_ID,
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        target_document_id=event.document_id,
        replacement_event_version=None,
        basis_fact_ids=tuple(
            fact.fact_id for fact in replay_result.manual_facts
        ),
        decision_summary="不应进入D7对象表示的人工审核敏感摘要。",
    )
    return replace(value, **changes)


def run_review(
    *,
    replay_input=None,
    replay_result=None,
    event=None,
    review=None,
):
    if replay_input is None:
        replay_input, replay_result, event = make_context()
    if review is None:
        review = make_review(replay_result, event)
    return review_supplemented_risk_document_relation(
        SupplementedRiskDocumentRelationInput(
            replay_input=replay_input,
            replay_result=replay_result,
            review=review,
        )
    )


class SupplementedRiskDocumentRelationTests(unittest.TestCase):
    def test_manual_facts_can_bridge_a_resolves_relation(self):
        replay_input, replay_result, event = make_context()
        original_view = replay_result

        result = run_review(
            replay_input=replay_input,
            replay_result=replay_result,
            event=event,
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.relation)
        relation = result.relation
        self.assertTrue(
            relation.relation_id.startswith(
                "supplemented-risk-relation:"
            )
        )
        self.assertEqual(
            relation.manual_basis_fact_ids,
            tuple(
                fact.fact_id
                for fact in replay_result.manual_facts
            ),
        )
        self.assertEqual(
            relation.source_artifact_id,
            replay_result.active_artifact_id,
        )
        self.assertEqual(
            relation.source_replay_version,
            RISK_DOCUMENT_RESEARCH_REPLAY_VERSION,
        )
        self.assertEqual(
            relation.relation_contract_id,
            SUPPLEMENTED_RISK_DOCUMENT_RELATION_CONTRACT_ID,
        )
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.applied_to_d3)
        self.assertFalse(result.applied_to_d1)
        self.assertEqual(replay_result, original_view)

    def test_supersedes_requires_a_distinct_replacement_version(self):
        replay_input, replay_result, event = make_context()
        review = make_review(
            replay_result,
            event,
            relation_kind=RiskDocumentRelationKind.SUPERSEDES,
            replacement_event_version="v2",
        )

        result = run_review(
            replay_input=replay_input,
            replay_result=replay_result,
            event=event,
            review=review,
        )
        invalid = run_review(
            replay_input=replay_input,
            replay_result=replay_result,
            event=event,
            review=replace(
                review,
                replacement_event_version=event.event_version,
            ),
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.relation.replacement_event_version,
            "v2",
        )
        self.assertEqual(
            invalid.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )

    def test_recomputed_d6_view_must_match_exactly(self):
        replay_input, replay_result, event = make_context()
        forged = replace(
            replay_result,
            facts=replay_result.facts[:-1],
        )

        result = run_review(
            replay_input=replay_input,
            replay_result=forged,
            event=event,
            review=make_review(replay_result, event),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "supplemented_risk_relation_replay_mismatch",
            result.reasons,
        )

    def test_source_view_must_be_an_unrelated_fact_supplement(self):
        replay_input, replay_result, event = make_context()
        no_manual = replace(replay_result, manual_facts=())
        with_relation = replace(
            replay_result,
            relations=("forged-relation",),
        )

        no_manual_result = run_review(
            replay_input=replay_input,
            replay_result=no_manual,
            event=event,
            review=make_review(replay_result, event),
        )
        with_relation_result = run_review(
            replay_input=replay_input,
            replay_result=with_relation,
            event=event,
            review=make_review(replay_result, event),
        )

        self.assertEqual(
            no_manual_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            with_relation_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )

    def test_target_event_identity_and_exact_version_are_required(self):
        replay_input, replay_result, event = make_context()
        reviews = (
            make_review(
                replay_result,
                event,
                target_event_version="v0",
            ),
            make_review(
                replay_result,
                event,
                target_document_id="cninfo:1224999999",
            ),
            make_review(
                replay_result,
                event,
                target_document_id=DOCUMENT_ID,
            ),
        )

        for review in reviews:
            with self.subTest(review=review):
                result = run_review(
                    replay_input=replay_input,
                    replay_result=replay_result,
                    event=event,
                    review=review,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )

    def test_basis_must_include_matching_manual_category_facts(self):
        replay_input, replay_result, event = make_context()
        case_fact, document_fact = replay_result.manual_facts
        reviews = (
            make_review(
                replay_result,
                event,
                basis_fact_ids=(document_fact.fact_id,),
            ),
            make_review(
                replay_result,
                event,
                basis_fact_ids=(case_fact.fact_id,),
            ),
            make_review(
                replay_result,
                event,
                basis_fact_ids=(
                    case_fact.fact_id,
                    case_fact.fact_id,
                ),
            ),
            make_review(
                replay_result,
                event,
                basis_fact_ids=("bad fact id",),
            ),
        )

        for review in reviews:
            with self.subTest(review=review):
                result = run_review(
                    replay_input=replay_input,
                    replay_result=replay_result,
                    event=event,
                    review=review,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )

    def test_review_must_be_manual_current_and_after_artifact(self):
        replay_input, replay_result, event = make_context()
        reviews = (
            make_review(
                replay_result,
                event,
                review_method="ai",
            ),
            make_review(
                replay_result,
                event,
                reviewed_at=ARTIFACT_REVIEWED_AT,
            ),
            make_review(
                replay_result,
                event,
                reviewed_at=AS_OF + timedelta(seconds=1),
            ),
        )

        for review in reviews:
            with self.subTest(review=review):
                result = run_review(
                    replay_input=replay_input,
                    replay_result=replay_result,
                    event=event,
                    review=review,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )

        expired = run_review(
            replay_input=replay_input,
            replay_result=replay_result,
            event=event,
            review=make_review(
                replay_result,
                event,
                effective_until=AS_OF - timedelta(seconds=1),
            ),
        )
        self.assertEqual(expired.status, ResearchFeatureStatus.STALE)
        self.assertIn(
            "supplemented_risk_relation_review_expired",
            expired.reasons,
        )

    def test_category_specific_reporting_period_is_required(self):
        earnings_event = make_event(
            category=RiskCategory.EARNINGS,
            reporting_period="2025",
            case_id=None,
        )
        replay_input, replay_result, event = make_context(
            event=earnings_event
        )

        result = run_review(
            replay_input=replay_input,
            replay_result=replay_result,
            event=event,
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )

    def test_repr_hides_pages_event_and_review_summaries(self):
        replay_input, replay_result, event = make_context()
        review = make_review(replay_result, event)
        input_value = SupplementedRiskDocumentRelationInput(
            replay_input=replay_input,
            replay_result=replay_result,
            review=review,
        )

        result = review_supplemented_risk_document_relation(
            input_value
        )
        rendered = repr(input_value) + repr(result)

        self.assertNotIn(RAW_CASE_ID, rendered)
        self.assertNotIn(event.fact_summary, rendered)
        self.assertNotIn(review.decision_summary, rendered)


if __name__ == "__main__":
    unittest.main()
