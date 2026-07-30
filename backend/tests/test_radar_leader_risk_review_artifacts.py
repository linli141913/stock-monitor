import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentPage,
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
    ManualRiskDocumentFactSubmission,
    ManualRiskReviewArtifactInput,
    ManualRiskReviewSubmission,
    build_manual_risk_review_artifact,
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
AS_OF = datetime(2026, 7, 29, 2, 0, tzinfo=UTC)
PUBLISHED_AT = AS_OF - timedelta(days=2)
FETCHED_AT = AS_OF - timedelta(hours=4)
REVIEWED_AT = AS_OF - timedelta(hours=1)
CONTENT_SHA256 = "c" * 64
DOCUMENT_ID = "cninfo:1225443882"
TARGET_DOCUMENT_ID = "cninfo:1224000001"
ISSUER_IDENTITY = "cninfo-org:9900012108"
RAW_CASE_ID = "证监立案字 0202026001号"
NORMALIZED_CASE = "证监立案字0202026001号"
CASE_ID = (
    "case:"
    + hashlib.sha256(
        NORMALIZED_CASE.encode("utf-8")
    ).hexdigest()
)
RAW_AUDIT_ID = "XYZ审字〔2025〕001号"
AUDIT_ID = (
    "audit-report:"
    + hashlib.sha256(
        RAW_AUDIT_ID.encode("utf-8")
    ).hexdigest()
)
MANUAL_PAGE_TEXT = (
    "人工核对页内原始字段如下，但版式没有标准标签。\n"
    f"{RAW_CASE_ID}\n"
    "2024年度\n"
    f"{RAW_AUDIT_ID}\n"
    "1224000001\n"
    "2026年7月20日\n"
    "2026年7月21日至2026年10月20日\n"
)
LABELED_PAGE_TEXT = (
    f"案号：{RAW_CASE_ID}\n"
    "报告期：2024年度\n"
    "原公告编号：1224000001\n"
    "生效日期：2026年7月20日\n"
)


def make_document(**changes):
    document = OfficialRiskDocumentMetadata(
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
    return replace(document, **changes)


def make_content(page_text=MANUAL_PAGE_TEXT, **changes):
    content = OfficialRiskDocumentContentResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        byte_count=125_092,
        page_count=1,
        pages=(
            OfficialRiskDocumentPage(
                page_number=1,
                text=page_text,
            ),
        ),
        fetched_at=FETCHED_AT,
        reasons=(),
    )
    return replace(content, **changes)


def make_event(**changes):
    event = LeaderRiskEventEvidence(
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
        fact_summary="收到正式立案告知书。",
        official_status=RiskOfficialStatus.ACTIVE,
    )
    return replace(event, **changes)


def make_fact_result(content, document=None, events=()):
    actual_document = document or make_document()
    return extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=AS_OF,
            document=actual_document,
            content_sha256=content.content_sha256,
            pages=content.pages,
            extracted_at=content.fetched_at,
            source_status=content.status,
            event_versions=tuple(events),
            reviews=(),
        )
    )


def make_context(
    *,
    page_text=MANUAL_PAGE_TEXT,
    events=(),
):
    document = make_document()
    content = make_content(page_text)
    facts = make_fact_result(content, document, events)
    candidate_result = build_risk_document_review_candidate(
        content,
        facts,
    )
    return document, content, facts, candidate_result.candidate


def make_manual_facts():
    return (
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.CASE_ID,
            source_value=RAW_CASE_ID,
            page_number=1,
            source_fragment=RAW_CASE_ID,
        ),
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.REPORTING_PERIOD,
            source_value="2024年度",
            page_number=1,
            source_fragment="2024年度",
        ),
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.AUDIT_REPORT_ID,
            source_value=RAW_AUDIT_ID,
            page_number=1,
            source_fragment=RAW_AUDIT_ID,
        ),
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
            source_value="1224000001",
            page_number=1,
            source_fragment="1224000001",
        ),
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.EFFECTIVE_DATE,
            source_value="2026年7月20日",
            page_number=1,
            source_fragment="2026年7月20日",
        ),
        ManualRiskDocumentFactSubmission(
            fact_kind=RiskDocumentFactKind.EFFECTIVE_INTERVAL,
            source_value="2026年7月21日至2026年10月20日",
            page_number=1,
            source_fragment=(
                "2026年7月21日至2026年10月20日"
            ),
        ),
    )


def make_submission(candidate, **changes):
    submission = ManualRiskReviewSubmission(
        review_version="manual-review-v1",
        supersedes_review_version=None,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=REVIEWED_AT,
        effective_until=None,
        candidate_id=candidate.candidate_id,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        fact_supplements=make_manual_facts(),
        relation_review=None,
    )
    return replace(submission, **changes)


def make_input(
    document,
    content,
    facts,
    candidate,
    *,
    submission=None,
    events=(),
    previous=(),
    as_of=AS_OF,
):
    return ManualRiskReviewArtifactInput(
        as_of=as_of,
        document=document,
        content=content,
        facts=facts,
        candidate=candidate,
        event_versions=tuple(events),
        submission=(
            submission
            if submission is not None
            else make_submission(candidate)
        ),
        previous_artifacts=tuple(previous),
    )


def make_relation_context():
    event = make_event()
    document, content, facts, candidate = make_context(
        page_text=LABELED_PAGE_TEXT,
        events=(event,),
    )
    fact_ids = {
        fact.fact_kind: fact.fact_id
        for fact in facts.facts
    }
    review = RiskDocumentVersionReview(
        review_id="risk-review-1",
        mapping_version="manual-review-v1",
        relation_kind=RiskDocumentRelationKind.RESOLVES,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=REVIEWED_AT,
        effective_until=None,
        source_document_id=DOCUMENT_ID,
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        target_document_id=event.document_id,
        replacement_event_version=None,
        basis_fact_ids=(
            fact_ids[RiskDocumentFactKind.REFERENCED_DOCUMENT_ID],
            fact_ids[RiskDocumentFactKind.CASE_ID],
        ),
        decision_summary="人工核对当前公告精确引用原事件。",
    )
    submission = make_submission(
        candidate,
        fact_supplements=(),
        relation_review=review,
    )
    return (
        document,
        content,
        facts,
        candidate,
        event,
        submission,
    )


class LeaderRiskReviewArtifactTests(unittest.TestCase):
    def test_all_six_manual_fact_kinds_are_normalized_safely(self):
        document, content, facts, candidate = make_context()
        self.assertEqual(
            candidate.candidate_kind,
            RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING,
        )

        result = build_manual_risk_review_artifact(
            make_input(document, content, facts, candidate)
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        artifact = result.artifact
        values = {
            fact.fact_kind: fact.normalized_value
            for fact in artifact.facts
        }
        self.assertEqual(
            values,
            {
                RiskDocumentFactKind.CASE_ID: CASE_ID,
                RiskDocumentFactKind.REPORTING_PERIOD: "2024",
                RiskDocumentFactKind.AUDIT_REPORT_ID: AUDIT_ID,
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID: (
                    TARGET_DOCUMENT_ID
                ),
                RiskDocumentFactKind.EFFECTIVE_DATE: "2026-07-20",
                RiskDocumentFactKind.EFFECTIVE_INTERVAL: (
                    "2026-07-21/2026-10-20"
                ),
            },
        )
        self.assertTrue(all(
            fact.extractor_version
            == MANUAL_RISK_DOCUMENT_REVIEW_VERSION
            for fact in artifact.facts
        ))
        self.assertEqual(artifact.relations, ())
        self.assertFalse(artifact.formal_usable)
        self.assertFalse(artifact.applied_to_d3)
        self.assertFalse(artifact.applied_to_d1)
        rendered = repr(result)
        self.assertNotIn(RAW_CASE_ID, rendered)
        self.assertNotIn(RAW_AUDIT_ID, rendered)
        self.assertNotIn(MANUAL_PAGE_TEXT, rendered)
        self.assertNotIn(
            RAW_CASE_ID,
            repr(make_submission(candidate)),
        )

    def test_fact_page_fragment_value_and_duplicates_are_strict(self):
        document, content, facts, candidate = make_context()
        base = make_manual_facts()[0]
        cases = (
            replace(base, page_number=2),
            replace(base, source_fragment="不存在的原文"),
            replace(base, source_value="另一个案号"),
            replace(
                make_manual_facts()[-1],
                source_value="2026年10月20日至2026年7月21日",
                source_fragment=(
                    "2026年7月21日至2026年10月20日"
                ),
            ),
        )

        for fact in cases:
            with self.subTest(fact_kind=fact.fact_kind):
                submission = make_submission(
                    candidate,
                    fact_supplements=(fact,),
                )
                result = build_manual_risk_review_artifact(
                    make_input(
                        document,
                        content,
                        facts,
                        candidate,
                        submission=submission,
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.artifact)

        duplicate_submission = make_submission(
            candidate,
            fact_supplements=(base, base),
        )
        duplicate_result = build_manual_risk_review_artifact(
            make_input(
                document,
                content,
                facts,
                candidate,
                submission=duplicate_submission,
            )
        )
        self.assertEqual(
            duplicate_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_fact_duplicate",
            duplicate_result.reasons,
        )

        conflict_text = (
            MANUAL_PAGE_TEXT
            + "\n证监立案字0202026999号\n"
        )
        (
            conflict_document,
            conflict_content,
            conflict_facts,
            conflict_candidate,
        ) = make_context(page_text=conflict_text)
        conflicting_fact = replace(
            base,
            source_value="证监立案字0202026999号",
            source_fragment="证监立案字0202026999号",
        )
        conflict_submission = make_submission(
            conflict_candidate,
            fact_supplements=(base, conflicting_fact),
        )
        conflict_result = build_manual_risk_review_artifact(
            make_input(
                conflict_document,
                conflict_content,
                conflict_facts,
                conflict_candidate,
                submission=conflict_submission,
            )
        )
        self.assertEqual(
            conflict_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_fact_conflict",
            conflict_result.reasons,
        )

    def test_candidate_is_rebuilt_and_forgery_is_rejected(self):
        document, content, facts, candidate = make_context()
        forged = replace(
            candidate,
            content_sha256="f" * 64,
        )

        result = build_manual_risk_review_artifact(
            make_input(document, content, facts, forged)
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.artifact)
        self.assertIn(
            "manual_risk_review_candidate_unverified",
            result.reasons,
        )

    def test_relation_review_is_revalidated_by_d3(self):
        (
            document,
            content,
            facts,
            candidate,
            event,
            submission,
        ) = make_relation_context()
        self.assertEqual(
            candidate.candidate_kind,
            RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED,
        )

        artifact_input = make_input(
            document,
            content,
            facts,
            candidate,
            submission=submission,
            events=(event,),
        )
        result = build_manual_risk_review_artifact(artifact_input)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.artifact.facts, ())
        self.assertEqual(len(result.artifact.relations), 1)
        self.assertEqual(
            result.artifact.relations[0].target_event_id,
            event.event_id,
        )
        self.assertNotIn(
            submission.relation_review.decision_summary,
            repr(result),
        )
        self.assertNotIn(
            submission.relation_review.decision_summary,
            repr(submission),
        )
        self.assertNotIn(
            event.fact_summary,
            repr(artifact_input),
        )

    def test_ai_or_mismatched_relation_review_is_rejected(self):
        (
            document,
            content,
            facts,
            candidate,
            event,
            submission,
        ) = make_relation_context()
        cases = (
            replace(
                submission,
                relation_review=replace(
                    submission.relation_review,
                    review_method="ai",
                ),
            ),
            replace(
                submission,
                relation_review=replace(
                    submission.relation_review,
                    mapping_version="other-version",
                ),
            ),
            replace(
                submission,
                fact_supplements=(make_manual_facts()[0],),
            ),
        )

        for invalid_submission in cases:
            with self.subTest(
                review=invalid_submission.relation_review
            ):
                result = build_manual_risk_review_artifact(
                    make_input(
                        document,
                        content,
                        facts,
                        candidate,
                        submission=invalid_submission,
                        events=(event,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.artifact)

    def test_review_versions_form_one_linear_chain(self):
        document, content, facts, candidate = make_context()
        first = build_manual_risk_review_artifact(
            make_input(document, content, facts, candidate)
        )
        second_submission = make_submission(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
        )

        second = build_manual_risk_review_artifact(
            make_input(
                document,
                content,
                facts,
                candidate,
                submission=second_submission,
                previous=(first.artifact,),
            )
        )

        self.assertEqual(second.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            second.artifact.artifact_id,
            first.artifact.artifact_id,
        )
        self.assertEqual(
            second.artifact.supersedes_review_version,
            first.artifact.review_version,
        )

        invalid_submissions = (
            replace(
                second_submission,
                supersedes_review_version=None,
            ),
            replace(
                second_submission,
                supersedes_review_version="missing-version",
            ),
            replace(
                second_submission,
                review_version="manual-review-v1",
            ),
            replace(
                second_submission,
                reviewed_at=first.artifact.reviewed_at,
            ),
        )
        for invalid_submission in invalid_submissions:
            with self.subTest(
                review_version=invalid_submission.review_version,
                supersedes=(
                    invalid_submission.supersedes_review_version
                ),
            ):
                result = build_manual_risk_review_artifact(
                    make_input(
                        document,
                        content,
                        facts,
                        candidate,
                        submission=invalid_submission,
                        previous=(first.artifact,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.artifact)

    def test_cross_candidate_previous_artifact_is_rejected(self):
        document, content, facts, candidate = make_context()
        first = build_manual_risk_review_artifact(
            make_input(document, content, facts, candidate)
        )
        forged_previous = replace(
            first.artifact,
            candidate_id="other-candidate",
        )
        submission = make_submission(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
        )

        result = build_manual_risk_review_artifact(
            make_input(
                document,
                content,
                facts,
                candidate,
                submission=submission,
                previous=(forged_previous,),
            )
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_history_unverified",
            result.reasons,
        )

    def test_forged_previous_artifact_payload_is_rejected(self):
        document, content, facts, candidate = make_context()
        first = build_manual_risk_review_artifact(
            make_input(document, content, facts, candidate)
        )
        submission = make_submission(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
        )
        forged_history = (
            replace(first.artifact, reviewer_key=""),
            replace(first.artifact, facts=("not-a-fact",)),
        )

        for forged_previous in forged_history:
            with self.subTest(
                reviewer=forged_previous.reviewer_key,
                fact_count=len(forged_previous.facts),
            ):
                result = build_manual_risk_review_artifact(
                    make_input(
                        document,
                        content,
                        facts,
                        candidate,
                        submission=submission,
                        previous=(forged_previous,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "manual_risk_review_history_unverified",
                    result.reasons,
                )

    def test_future_precontent_and_expired_reviews_are_not_ready(self):
        document, content, facts, candidate = make_context()
        cases = (
            (
                make_submission(
                    candidate,
                    reviewed_at=AS_OF + timedelta(seconds=1),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "manual_risk_review_timestamp_unverified",
            ),
            (
                make_submission(
                    candidate,
                    reviewed_at=FETCHED_AT - timedelta(seconds=1),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "manual_risk_review_timestamp_unverified",
            ),
            (
                make_submission(
                    candidate,
                    effective_until=REVIEWED_AT + timedelta(minutes=1),
                ),
                ResearchFeatureStatus.STALE,
                "manual_risk_review_artifact_expired",
            ),
        )

        for submission, status, reason in cases:
            with self.subTest(reason=reason):
                result = build_manual_risk_review_artifact(
                    make_input(
                        document,
                        content,
                        facts,
                        candidate,
                        submission=submission,
                    )
                )
                self.assertEqual(result.status, status)
                self.assertIsNone(result.artifact)
                self.assertIn(reason, result.reasons)

        stale_with_invalid_history = make_submission(
            candidate,
            supersedes_review_version="missing-version",
            effective_until=REVIEWED_AT + timedelta(minutes=1),
        )
        invalid_history_result = build_manual_risk_review_artifact(
            make_input(
                document,
                content,
                facts,
                candidate,
                submission=stale_with_invalid_history,
            )
        )
        self.assertEqual(
            invalid_history_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_history_unverified",
            invalid_history_result.reasons,
        )

        stale_invalid_fact = replace(
            stale_with_invalid_history,
            supersedes_review_version=None,
            fact_supplements=(
                replace(
                    make_manual_facts()[0],
                    source_fragment="不存在的原文",
                ),
            ),
        )
        stale_invalid_result = build_manual_risk_review_artifact(
            make_input(
                document,
                content,
                facts,
                candidate,
                submission=stale_invalid_fact,
            )
        )
        self.assertEqual(
            stale_invalid_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_fragment_unverified",
            stale_invalid_result.reasons,
        )

    def test_malformed_contract_returns_stable_unverified_status(self):
        result = build_manual_risk_review_artifact(None)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.artifact)
        self.assertEqual(
            result.reasons,
            ("manual_risk_review_contract_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
