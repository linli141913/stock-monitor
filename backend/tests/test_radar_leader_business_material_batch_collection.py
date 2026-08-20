import unittest
from datetime import date, timedelta

from radar.leader_business_material_batch_collection import (
    collect_leader_business_material_review_queue,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.sources.leader_risk_official import CninfoRiskIssuerScope
from tests import test_radar_leader_research_source_admission as helpers


class LeaderBusinessMaterialBatchCollectionTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.plan = helper.plan
        self.scopes = tuple(
            CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=f"cninfo-org:org-{item.symbol}",
                resolved_at=self.plan.as_of - timedelta(minutes=2),
            )
            for item in reversed(self.plan.items)
        )

    def payload(self, query):
        symbol = query.scope.symbol
        return {
            "totalAnnouncement": 1,
            "totalpages": 1,
            "hasMore": False,
            "announcements": [{
                "secCode": symbol,
                "orgId": f"org-{symbol}",
                "announcementId": f"report-{symbol}",
                "announcementTitle": "2025年年度报告",
                "announcementTime": int(
                    (self.plan.as_of - timedelta(days=30)).timestamp() * 1000
                ),
                "adjunctUrl": (
                    "finalpage/2026-07-01/"
                    f"report-{symbol}.PDF"
                ),
                "adjunctType": "PDF",
            }],
        }

    def test_candidate_universe_is_collected_once_and_restored_to_plan_order(self):
        calls = []

        def transport(query):
            calls.append(query.scope.symbol)
            return self.payload(query)

        result = collect_leader_business_material_review_queue(
            self.plan,
            issuer_scopes=self.scopes,
            window_from=date(2025, 1, 1),
            transport=transport,
            clock=lambda: self.plan.as_of,
            max_workers=2,
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertEqual(set(calls), {
            item.symbol for item in self.plan.items
        })
        self.assertEqual(len(calls), self.plan.candidate_count)

    def test_one_source_failure_is_visible_without_partial_formal_result(self):
        failed_symbol = self.plan.items[0].symbol

        def transport(query):
            if query.scope.symbol == failed_symbol:
                raise RuntimeError("source unavailable")
            return self.payload(query)

        result = collect_leader_business_material_review_queue(
            self.plan,
            issuer_scopes=self.scopes,
            window_from=date(2025, 1, 1),
            transport=transport,
            clock=lambda: self.plan.as_of,
            max_workers=2,
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewQueueStatus.SOURCE_FAILED,
        )
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_missing_duplicate_or_cross_symbol_scopes_are_blocked_before_requests(self):
        calls = []

        def transport(query):
            calls.append(query)
            return self.payload(query)

        malformed = (
            self.scopes[:-1],
            (*self.scopes, self.scopes[0]),
            (
                CninfoRiskIssuerScope(
                    symbol="000999",
                    issuer_identity="cninfo-org:org-000999",
                    resolved_at=self.plan.as_of - timedelta(minutes=2),
                ),
                *self.scopes[1:],
            ),
        )
        for scopes in malformed:
            result = collect_leader_business_material_review_queue(
                self.plan,
                issuer_scopes=scopes,
                window_from=date(2025, 1, 1),
                transport=transport,
                clock=lambda: self.plan.as_of,
                max_workers=2,
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialReviewQueueStatus.BLOCKED,
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
