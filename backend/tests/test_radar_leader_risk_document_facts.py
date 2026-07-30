import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
    RiskEventSubtype,
    RiskEvidenceSourceKind,
    RiskOfficialStatus,
)
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentPage,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
PUBLISHED_AT = AS_OF - timedelta(days=2)
EXTRACTED_AT = AS_OF - timedelta(hours=1)
CONTENT_SHA256 = "a" * 64
CURRENT_DOCUMENT_ID = "cninfo:1225000001"
TARGET_DOCUMENT_ID = "cninfo:1224000001"
RAW_CASE_ID = "证监立案字0202026001号"
CASE_ID = (
    "case:"
    + hashlib.sha256(RAW_CASE_ID.encode("utf-8")).hexdigest()
)
RAW_AUDIT_REPORT_ID = "XYZ审字〔2025〕001号"
AUDIT_REPORT_ID = (
    "audit-report:"
    + hashlib.sha256(
        RAW_AUDIT_REPORT_ID.encode("utf-8")
    ).hexdigest()
)
DECISION_SUMMARY = "人工核对当前公告精确引用了原事件版本。"
PAGE_TEXT = (
    "本公告披露以下正式字段。\n"
    "案号：证监立案字 0202026001号\n"
    "报告期：2024年度\n"
    "审计报告编号：XYZ审字〔2025〕001号\n"
    "原公告编号：1224000001\n"
    "生效日期：2026年07月20日\n"
    "减持期间：2026年07月21日至2026年10月20日\n"
)


def make_document(**changes):
    document = OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=CURRENT_DOCUMENT_ID,
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
        issuer_name="ST恒信",
        title="关于收到立案告知书的公告",
        published_at=PUBLISHED_AT,
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-26/1225000001.PDF"
        ),
        candidate_category=RiskCategory.INVESTIGATION,
        raw_column_ids=("09020202",),
        raw_announcement_types=("01010503",),
        raw_page_column="SZCY",
        association_reported=False,
        formal_usable=False,
    )
    return replace(document, **changes)


def make_event(**changes):
    event = LeaderRiskEventEvidence(
        event_id="risk-event-1",
        event_version="v1",
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
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


def make_input(**changes):
    input_value = OfficialRiskDocumentFactInput(
        as_of=AS_OF,
        document=make_document(),
        content_sha256=CONTENT_SHA256,
        pages=(
            OfficialRiskDocumentPage(
                page_number=1,
                text=PAGE_TEXT,
            ),
        ),
        extracted_at=EXTRACTED_AT,
        source_status=ResearchFeatureStatus.READY,
        event_versions=(make_event(),),
        reviews=(),
    )
    return replace(input_value, **changes)


def fact_ids_by_kind(result):
    return {
        fact.fact_kind: fact.fact_id
        for fact in result.facts
    }


def make_review(
    base_result,
    *,
    relation_kind=RiskDocumentRelationKind.SUPERSEDES,
    basis_kinds=(
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        RiskDocumentFactKind.CASE_ID,
    ),
    **changes,
):
    ids = fact_ids_by_kind(base_result)
    review = RiskDocumentVersionReview(
        review_id="risk-review-1",
        mapping_version="risk-map-v1",
        relation_kind=relation_kind,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=EXTRACTED_AT + timedelta(minutes=10),
        effective_until=None,
        source_document_id=CURRENT_DOCUMENT_ID,
        target_event_id="risk-event-1",
        target_event_version="v1",
        target_document_id=TARGET_DOCUMENT_ID,
        replacement_event_version=(
            "v2"
            if relation_kind
            == RiskDocumentRelationKind.SUPERSEDES
            else None
        ),
        basis_fact_ids=tuple(ids[kind] for kind in basis_kinds),
        decision_summary=DECISION_SUMMARY,
    )
    return replace(review, **changes)


class LeaderRiskDocumentFactTests(unittest.TestCase):
    def test_explicit_labeled_facts_are_normalized_without_body(self):
        result = extract_official_risk_document_facts(make_input())

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        values = {
            fact.fact_kind: fact.normalized_value
            for fact in result.facts
        }
        self.assertEqual(
            values,
            {
                RiskDocumentFactKind.CASE_ID: CASE_ID,
                RiskDocumentFactKind.REPORTING_PERIOD: "2024",
                RiskDocumentFactKind.AUDIT_REPORT_ID: (
                    AUDIT_REPORT_ID
                ),
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID: (
                    TARGET_DOCUMENT_ID
                ),
                RiskDocumentFactKind.EFFECTIVE_DATE: "2026-07-20",
                RiskDocumentFactKind.EFFECTIVE_INTERVAL: (
                    "2026-07-21/2026-10-20"
                ),
            },
        )
        self.assertTrue(all(fact.page_number == 1 for fact in result.facts))
        self.assertTrue(all(
            len(fact.fragment_sha256) == 64
            for fact in result.facts
        ))
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.correction_links_complete)
        self.assertEqual(result.relations, ())
        self.assertIn(
            "risk_document_relation_missing",
            result.reasons,
        )
        rendered = repr(result)
        self.assertNotIn(PAGE_TEXT, rendered)
        self.assertNotIn("本公告披露以下正式字段", rendered)
        self.assertNotIn(RAW_CASE_ID, rendered)
        self.assertNotIn(RAW_AUDIT_REPORT_ID, rendered)

    def test_titles_and_unlabeled_keywords_do_not_create_facts(self):
        input_value = make_input(
            pages=(
                OfficialRiskDocumentPage(
                    page_number=1,
                    text=(
                        "公司收到立案调查材料，涉及2024年度事项，"
                        "后续将发布更正公告。"
                    ),
                ),
            ),
        )

        result = extract_official_risk_document_facts(input_value)

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertEqual(result.facts, ())
        self.assertIn(
            "risk_document_facts_missing",
            result.reasons,
        )

    def test_generic_report_text_is_not_an_audit_report_identity(self):
        result = extract_official_risk_document_facts(
            make_input(
                pages=(
                    OfficialRiskDocumentPage(
                        page_number=1,
                        text=(
                            "报告编号：这是整段说明文字，"
                            "并不是可核验的审计报告编号"
                        ),
                    ),
                )
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertEqual(result.facts, ())

    def test_source_failure_and_missing_have_priority(self):
        failed = extract_official_risk_document_facts(
            make_input(
                source_status=ResearchFeatureStatus.SOURCE_FAILED,
                pages=None,
            )
        )
        missing = extract_official_risk_document_facts(
            make_input(
                source_status=ResearchFeatureStatus.MISSING,
                pages=None,
            )
        )

        self.assertEqual(
            failed.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertIn(
            "risk_document_source_failed",
            failed.reasons,
        )
        self.assertEqual(
            missing.status,
            ResearchFeatureStatus.MISSING,
        )
        self.assertIn(
            "risk_document_source_missing",
            missing.reasons,
        )

    def test_invalid_hash_page_contract_and_size_are_rejected(self):
        cases = (
            (
                make_input(content_sha256="A" * 64),
                "risk_document_content_hash_unverified",
            ),
            (
                make_input(pages=()),
                "risk_document_pages_unverified",
            ),
            (
                make_input(
                    pages=(
                        OfficialRiskDocumentPage(1, PAGE_TEXT),
                        OfficialRiskDocumentPage(3, PAGE_TEXT),
                    )
                ),
                "risk_document_pages_unverified",
            ),
            (
                make_input(
                    pages=tuple(
                        OfficialRiskDocumentPage(index, "")
                        for index in range(1, 202)
                    )
                ),
                "risk_document_content_too_large",
            ),
            (
                make_input(
                    pages=(
                        OfficialRiskDocumentPage(
                            1,
                            "x" * 100_001,
                        ),
                    )
                ),
                "risk_document_content_too_large",
            ),
        )

        for input_value, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = extract_official_risk_document_facts(
                    input_value
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)
                self.assertFalse(result.formal_usable)

    def test_extraction_time_and_metadata_contract_are_enforced(self):
        cases = (
            (
                make_input(
                    extracted_at=PUBLISHED_AT
                    - timedelta(seconds=1),
                ),
                "risk_document_extracted_before_published",
            ),
            (
                make_input(
                    extracted_at=AS_OF + timedelta(seconds=1),
                ),
                "risk_document_extracted_in_future",
            ),
            (
                make_input(
                    document=make_document(
                        source_contract_id="other-contract",
                    )
                ),
                "risk_document_metadata_unverified",
            ),
            (
                make_input(
                    document=make_document(formal_usable=True)
                ),
                "risk_document_metadata_unverified",
            ),
        )

        for input_value, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = extract_official_risk_document_facts(
                    input_value
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_manual_supersedes_relation_targets_exact_event_version(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)

        result = extract_official_risk_document_facts(
            make_input(reviews=(review,))
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(len(result.relations), 1)
        relation = result.relations[0]
        self.assertEqual(
            relation.relation_kind,
            RiskDocumentRelationKind.SUPERSEDES,
        )
        self.assertEqual(relation.target_event_id, "risk-event-1")
        self.assertEqual(relation.target_event_version, "v1")
        self.assertEqual(relation.replacement_event_version, "v2")
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.correction_links_complete)
        self.assertNotIn(DECISION_SUMMARY, repr(result))

    def test_manual_resolves_relation_never_creates_replacement_version(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(
            base_result,
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )

        result = extract_official_risk_document_facts(
            make_input(reviews=(review,))
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(len(result.relations), 1)
        self.assertEqual(
            result.relations[0].relation_kind,
            RiskDocumentRelationKind.RESOLVES,
        )
        self.assertIsNone(
            result.relations[0].replacement_event_version
        )

    def test_ai_future_and_prepublication_reviews_are_rejected(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)
        cases = (
            (
                replace(review, review_method="ai"),
                "risk_document_review_method_unverified",
            ),
            (
                replace(
                    review,
                    reviewed_at=AS_OF + timedelta(seconds=1),
                ),
                "risk_document_reviewed_at_future",
            ),
            (
                replace(
                    review,
                    reviewed_at=PUBLISHED_AT
                    - timedelta(seconds=1),
                ),
                "risk_document_review_precedes_document",
            ),
        )

        for changed_review, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = extract_official_risk_document_facts(
                    make_input(reviews=(changed_review,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_target_and_original_document_must_match_exactly(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)
        cases = (
            (
                replace(
                    review,
                    target_event_version="v9",
                ),
                "risk_document_review_target_missing",
            ),
            (
                replace(
                    review,
                    target_document_id="cninfo:9999999999",
                ),
                "risk_document_review_identity_mismatch",
            ),
            (
                replace(
                    review,
                    source_document_id="cninfo:9999999998",
                ),
                "risk_document_review_identity_mismatch",
            ),
        )

        for changed_review, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = extract_official_risk_document_facts(
                    make_input(reviews=(changed_review,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_review_cannot_target_current_or_later_document(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)
        self_event = replace(
            make_event(),
            document_id=CURRENT_DOCUMENT_ID,
        )
        self_review = replace(
            review,
            target_document_id=CURRENT_DOCUMENT_ID,
        )
        later_event = replace(
            make_event(),
            published_at=PUBLISHED_AT + timedelta(hours=1),
            effective_from=PUBLISHED_AT + timedelta(hours=1),
        )

        self_result = extract_official_risk_document_facts(
            make_input(
                event_versions=(self_event,),
                reviews=(self_review,),
            )
        )
        later_result = extract_official_risk_document_facts(
            make_input(
                event_versions=(later_event,),
                reviews=(review,),
            )
        )

        self.assertIn(
            "risk_document_review_self_relation_forbidden",
            self_result.reasons,
        )
        self.assertIn(
            "risk_document_review_target_not_prior",
            later_result.reasons,
        )

    def test_review_requires_referenced_document_and_case_fact(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review_without_document = make_review(
            base_result,
            basis_kinds=(RiskDocumentFactKind.CASE_ID,),
        )
        review_without_case = make_review(
            base_result,
            basis_kinds=(
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
            ),
        )

        missing_document_result = (
            extract_official_risk_document_facts(
                make_input(reviews=(review_without_document,))
            )
        )
        missing_case_result = extract_official_risk_document_facts(
            make_input(reviews=(review_without_case,))
        )

        self.assertIn(
            "risk_document_review_reference_missing",
            missing_document_result.reasons,
        )
        self.assertIn(
            "risk_document_review_case_mismatch",
            missing_case_result.reasons,
        )

    def test_earnings_and_audit_require_matching_reporting_period(self):
        earnings_event = make_event(
            category=RiskCategory.EARNINGS,
            event_subtype=RiskEventSubtype.EARNINGS_LOSS,
            case_id=None,
            reporting_period="2024",
        )
        earnings_input = make_input(event_versions=(earnings_event,))
        base_result = extract_official_risk_document_facts(
            earnings_input
        )
        valid_review = make_review(
            base_result,
            basis_kinds=(
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
                RiskDocumentFactKind.REPORTING_PERIOD,
            ),
        )
        invalid_event = replace(
            earnings_event,
            reporting_period="2025",
        )

        valid_result = extract_official_risk_document_facts(
            replace(earnings_input, reviews=(valid_review,))
        )
        invalid_result = extract_official_risk_document_facts(
            replace(
                earnings_input,
                event_versions=(invalid_event,),
                reviews=(valid_review,),
            )
        )

        self.assertEqual(
            valid_result.status,
            ResearchFeatureStatus.READY,
        )
        self.assertEqual(
            invalid_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "risk_document_review_reporting_period_mismatch",
            invalid_result.reasons,
        )

    def test_replacement_version_rules_are_strict(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        supersedes = make_review(base_result)
        resolves = make_review(
            base_result,
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )
        cases = (
            (
                replace(
                    supersedes,
                    replacement_event_version=None,
                ),
                "risk_document_review_replacement_version_invalid",
            ),
            (
                replace(
                    supersedes,
                    replacement_event_version="v1",
                ),
                "risk_document_review_replacement_version_invalid",
            ),
            (
                replace(
                    resolves,
                    replacement_event_version="v2",
                ),
                "risk_document_review_replacement_version_unexpected",
            ),
        )

        for changed_review, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = extract_official_risk_document_facts(
                    make_input(reviews=(changed_review,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_expired_review_is_stale(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(
            base_result,
            effective_until=AS_OF - timedelta(seconds=1),
        )

        result = extract_official_risk_document_facts(
            make_input(reviews=(review,))
        )

        self.assertEqual(result.status, ResearchFeatureStatus.STALE)
        self.assertEqual(result.relations, ())
        self.assertIn(
            "risk_document_review_expired",
            result.reasons,
        )

    def test_current_review_replaces_expired_mapping_history(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        current_review = make_review(base_result)
        expired_review = replace(
            current_review,
            review_id="risk-review-old",
            mapping_version="risk-map-old",
            reviewed_at=EXTRACTED_AT,
            effective_until=AS_OF - timedelta(seconds=1),
        )

        result = extract_official_risk_document_facts(
            make_input(
                reviews=(expired_review, current_review)
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            [relation.relation_id for relation in result.relations],
            ["risk-review-1"],
        )
        self.assertIn(
            "risk_document_review_expired_ignored",
            result.reasons,
        )

    def test_multiple_active_mappings_for_same_target_are_rejected(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        first = make_review(base_result)
        second = replace(
            first,
            review_id="risk-review-2",
            mapping_version="risk-map-v2",
        )

        result = extract_official_risk_document_facts(
            make_input(reviews=(first, second))
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "risk_document_review_active_duplicate",
            result.reasons,
        )

    def test_duplicate_and_conflicting_reviews_are_rejected(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)
        duplicate = replace(review)
        conflicting = replace(
            review,
            review_id="risk-review-2",
            mapping_version="risk-map-v2",
            relation_kind=RiskDocumentRelationKind.RESOLVES,
            replacement_event_version=None,
        )

        duplicate_result = extract_official_risk_document_facts(
            make_input(reviews=(review, duplicate))
        )
        conflict_result = extract_official_risk_document_facts(
            make_input(reviews=(review, conflicting))
        )

        self.assertIn(
            "risk_document_review_identity_duplicate",
            duplicate_result.reasons,
        )
        self.assertIn(
            "risk_document_review_conflict",
            conflict_result.reasons,
        )

    def test_invalid_nested_types_return_stable_unverified_status(self):
        base_result = extract_official_risk_document_facts(
            make_input()
        )
        review = make_review(base_result)
        cases = (
            make_input(pages=(PAGE_TEXT,)),
            make_input(event_versions=(object(),)),
            make_input(reviews=(object(),)),
            make_input(
                document=make_document(source_url=[])
            ),
            make_input(
                document=make_document(
                    source_url="https://[invalid"
                )
            ),
            make_input(
                reviews=(
                    replace(review, review_id=[]),
                )
            ),
        )

        for input_value in cases:
            with self.subTest(input_value=repr(input_value)):
                result = extract_official_risk_document_facts(
                    input_value
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.formal_usable)
                self.assertTrue(result.reasons)


if __name__ == "__main__":
    unittest.main()
