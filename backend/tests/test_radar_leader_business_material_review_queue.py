import unittest
from datetime import date, timedelta

from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueueStatus,
    build_leader_business_material_review_queue,
)
from radar.sources.leader_business_official import (
    CninfoBusinessMaterialQuery,
    OfficialBusinessMaterialDiscoveryBatch,
    OfficialBusinessMaterialDiscoveryStatus,
    OfficialBusinessMaterialDocument,
)
from radar.sources.leader_risk_official import CninfoRiskIssuerScope
from tests import test_radar_leader_research_source_admission as helpers


class LeaderBusinessMaterialReviewQueueTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.plan = helper.plan

    def batch(self, item, *, documents=True, status=None):
        scope = CninfoRiskIssuerScope(
            symbol=item.symbol,
            issuer_identity=f"cninfo-org:org-{item.symbol}",
            resolved_at=self.plan.as_of - timedelta(minutes=2),
        )
        query = CninfoBusinessMaterialQuery(
            candidate_plan_id=self.plan.candidate_set_id,
            scope=scope,
            window_from=date(2025, 1, 1),
            window_until=self.plan.as_of.date(),
        )
        docs = (
            (OfficialBusinessMaterialDocument(
                document_id=f"cninfo:report-{item.symbol}",
                document_version=f"cninfo:report-{item.symbol}:v1",
                symbol=item.symbol,
                issuer_identity=scope.issuer_identity,
                title="年度报告",
                published_at=self.plan.as_of - timedelta(days=30),
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/2026-07-01/"
                    f"report-{item.symbol}.PDF"
                ),
            ),)
            if documents else ()
        )
        return OfficialBusinessMaterialDiscoveryBatch(
            status=status or (
                OfficialBusinessMaterialDiscoveryStatus.READY
                if docs else OfficialBusinessMaterialDiscoveryStatus.MISSING
            ),
            query=query,
            fetched_at=self.plan.as_of,
            documents=docs,
            coverage_complete=True,
        )

    def test_complete_batches_restore_candidate_order_for_review(self):
        result = build_leader_business_material_review_queue(
            self.plan,
            tuple(self.batch(item) for item in reversed(self.plan.items)),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertTrue(all(
            item.status == LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
            for item in result.items
        ))
        self.assertFalse(result.formal_usable)

    def test_empty_official_result_stays_missing(self):
        result = build_leader_business_material_review_queue(
            self.plan,
            tuple(self.batch(item, documents=False) for item in self.plan.items),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewQueueStatus.MISSING,
        )
        self.assertTrue(all(
            item.status == LeaderBusinessMaterialReviewItemStatus.MISSING
            for item in result.items
        ))

    def test_missing_duplicate_and_cross_plan_batches_are_blocked(self):
        batches = tuple(self.batch(item) for item in self.plan.items)
        cross = self.batch(self.plan.items[0])
        cross = OfficialBusinessMaterialDiscoveryBatch(
            **{
                **cross.__dict__,
                "query": CninfoBusinessMaterialQuery(
                    candidate_plan_id="other-plan",
                    scope=cross.query.scope,
                    window_from=cross.query.window_from,
                    window_until=cross.query.window_until,
                ),
            }
        )
        cases = (batches[:-1], (*batches, batches[0]), (cross, *batches[1:]))

        for malformed in cases:
            result = build_leader_business_material_review_queue(
                self.plan,
                malformed,
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialReviewQueueStatus.BLOCKED,
            )
            self.assertEqual(result.items, ())


if __name__ == "__main__":
    unittest.main()
