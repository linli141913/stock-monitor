import sqlite3
import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_content_delivery import (
    LeaderRiskContentDeliveryStatus,
    LeaderRiskContentSelection,
    deliver_leader_risk_document_contents,
)
from radar.leader_risk_invalidation_features import RiskCategory
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from tests import test_radar_leader_risk_review_artifacts as helpers


AS_OF = helpers.AS_OF
APPLIED_AT = AS_OF + timedelta(hours=1)
BATCH_ID = "risk-review-content-batch-1"


def make_document(index, category):
    raw_id = str(1225443882 + index)
    return replace(
        helpers.make_document(),
        document_id=f"cninfo:{raw_id}",
        symbol=f"30008{index}",
        issuer_identity=f"cninfo-org:990001210{index}",
        issuer_name=f"测试发行人{index}",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            f"2026-07-27/{raw_id}.PDF"
        ),
        candidate_category=category,
    )


def make_content(document, index, *, status=ResearchFeatureStatus.READY):
    return replace(
        helpers.make_content(page_text=f"第{index}份真实正文测试页"),
        status=status,
        document_id=document.document_id,
        symbol=document.symbol,
        issuer_identity=document.issuer_identity,
        content_sha256=str(index) * 64,
        reasons=(
            () if status == ResearchFeatureStatus.READY
            else ("risk_document_content_request_failed",)
        ),
    )


class LeaderRiskContentDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        as_of = AS_OF.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES ('run-content-1', ?, 'succeeded', 1, ?, ?)
            """,
            (as_of, as_of, as_of),
        )
        self.connection.commit()
        self.repository = LeaderRiskReviewRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )
        categories = (
            RiskCategory.UNLOCK,
            RiskCategory.EARNINGS,
            RiskCategory.LITIGATION,
        )
        self.documents = tuple(
            make_document(index, category)
            for index, category in enumerate(categories, start=1)
        )
        self.repository.save_review_batch(
            {
                "reviewBatchId": BATCH_ID,
                "candidatePlanId": "candidate-plan-content-1",
                "radarRunId": "run-content-1",
                "asOf": AS_OF,
                "windowFrom": AS_OF.date() - timedelta(days=365),
                "windowUntil": AS_OF.date(),
                "candidateCount": 3,
                "shardCount": 1,
                "categoryCount": 7,
                "documentCount": 3,
                "queryCategoriesComplete": True,
                "queryPagesComplete": True,
                "queryWindowContinuous": True,
                "sourceContractId": (
                    "radar-leader-risk-cninfo-discovery-v1"
                ),
            },
            self.documents,
        )
        self.selections = tuple(
            LeaderRiskContentSelection(
                document_id=document.document_id,
                candidate_category=document.candidate_category.value,
            )
            for document in self.documents
        )

    def tearDown(self):
        self.connection.close()

    def content_count(self):
        return self.connection.execute(
            "SELECT COUNT(*) FROM radar_leader_risk_document_contents"
        ).fetchone()[0]

    def test_confirmation_and_scope_are_required_before_fetch(self):
        calls = []
        report = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            self.selections,
            confirmed=False,
            fetcher=lambda *args, **kwargs: calls.append(args),
            clock=lambda: APPLIED_AT,
        )
        self.assertEqual(report.status, LeaderRiskContentDeliveryStatus.NOT_RUN)
        self.assertEqual(calls, [])
        self.assertEqual(self.content_count(), 0)

        oversized = (*self.selections, self.selections[0])
        report = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            oversized,
            confirmed=True,
            fetcher=lambda *args, **kwargs: calls.append(args),
            clock=lambda: APPLIED_AT,
        )
        self.assertEqual(report.status, LeaderRiskContentDeliveryStatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertEqual(self.content_count(), 0)

    def test_any_fetch_failure_keeps_database_empty(self):
        calls = []

        def fetcher(document, **kwargs):
            calls.append(document.document_id)
            index = len(calls)
            return make_content(
                document,
                index,
                status=(
                    ResearchFeatureStatus.SOURCE_FAILED
                    if index == 2 else ResearchFeatureStatus.READY
                ),
            )

        report = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            self.selections,
            confirmed=True,
            fetcher=fetcher,
            clock=lambda: APPLIED_AT,
        )

        self.assertEqual(report.status, LeaderRiskContentDeliveryStatus.BLOCKED)
        self.assertEqual(report.fetched_count, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.content_count(), 0)

    def test_success_is_atomic_and_repeat_is_idempotent(self):
        calls = []

        def fetcher(document, **kwargs):
            calls.append(document.document_id)
            return make_content(document, len(calls))

        first = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            self.selections,
            confirmed=True,
            fetcher=fetcher,
            clock=lambda: APPLIED_AT,
        )
        self.assertEqual(first.status, LeaderRiskContentDeliveryStatus.READY)
        self.assertEqual(first.persisted_count, 3)
        self.assertEqual(first.available_count, 3)
        self.assertEqual(self.content_count(), 3)

        second = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            self.selections,
            confirmed=True,
            fetcher=lambda *args, **kwargs: self.fail(
                "已有快照不应重新调用来源"
            ),
            clock=lambda: APPLIED_AT,
        )
        self.assertEqual(second.status, LeaderRiskContentDeliveryStatus.READY)
        self.assertEqual(second.fetched_count, 0)
        self.assertEqual(second.persisted_count, 0)
        self.assertEqual(second.available_count, 3)
        self.assertEqual(self.content_count(), 3)

    def test_wrong_category_is_blocked_with_stable_reason(self):
        selection = replace(
            self.selections[1],
            candidate_category=RiskCategory.AUDIT.value,
        )
        selections = (
            self.selections[0],
            selection,
            self.selections[2],
        )
        report = deliver_leader_risk_document_contents(
            self.repository,
            BATCH_ID,
            selections,
            confirmed=True,
            fetcher=lambda *args, **kwargs: self.fail(
                "类别错配时不应调用来源"
            ),
            clock=lambda: APPLIED_AT,
        )
        self.assertEqual(report.status, LeaderRiskContentDeliveryStatus.BLOCKED)
        self.assertEqual(
            report.reasons,
            ("risk_content_delivery_selection_unverified",),
        )
        self.assertEqual(self.content_count(), 0)


if __name__ == "__main__":
    unittest.main()
