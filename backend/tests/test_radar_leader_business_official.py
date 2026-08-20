import unittest
from dataclasses import replace
from datetime import date, timedelta

from radar.sources.leader_business_official import (
    CninfoBusinessMaterialQuery,
    OfficialBusinessMaterialDiscoveryStatus,
    parse_cninfo_business_material_payload,
)
from radar.sources.leader_risk_official import CninfoRiskIssuerScope
from tests import test_radar_leader_research_source_admission as helpers


class LeaderBusinessOfficialTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.plan = helper.plan
        self.item = self.plan.items[0]
        self.scope = CninfoRiskIssuerScope(
            symbol=self.item.symbol,
            issuer_identity="cninfo-org:gssz0000001",
            resolved_at=self.plan.as_of - timedelta(minutes=2),
        )
        self.query = CninfoBusinessMaterialQuery(
            candidate_plan_id=self.plan.candidate_set_id,
            scope=self.scope,
            window_from=date(2025, 1, 1),
            window_until=self.plan.as_of.date(),
        )

    def payload(self):
        return {
            "totalAnnouncement": 1,
            "totalpages": 1,
            "hasMore": False,
            "announcements": [{
                "secCode": self.item.symbol,
                "orgId": "gssz0000001",
                "announcementId": "1234567890",
                "announcementTitle": "2025年年度报告",
                "announcementTime": int(
                    (self.plan.as_of - timedelta(days=30)).timestamp() * 1000
                ),
                "adjunctUrl": "finalpage/2026-07-01/1234567890.PDF",
                "adjunctType": "PDF",
            }],
        }

    def test_complete_official_page_returns_reviewable_document(self):
        result = parse_cninfo_business_material_payload(
            self.query,
            self.payload(),
            fetched_at=self.plan.as_of,
        )

        self.assertEqual(
            result.status,
            OfficialBusinessMaterialDiscoveryStatus.READY,
        )
        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.documents[0].symbol, self.item.symbol)
        self.assertEqual(
            result.documents[0].source_url,
            "https://static.cninfo.com.cn/finalpage/2026-07-01/1234567890.PDF",
        )
        self.assertFalse(result.formal_usable)

    def test_forged_url_future_document_and_incomplete_page_fail_closed(self):
        cases = (
            {**self.payload(), "announcements": [{
                **self.payload()["announcements"][0],
                "adjunctUrl": "https://evil.example/report.pdf",
            }]},
            {**self.payload(), "announcements": [{
                **self.payload()["announcements"][0],
                "announcementTime": int(
                    (self.plan.as_of + timedelta(seconds=6)).timestamp() * 1000
                ),
            }]},
            {**self.payload(), "hasMore": True, "totalpages": 2},
        )

        for payload in cases:
            with self.subTest(payload=payload):
                result = parse_cninfo_business_material_payload(
                    self.query,
                    payload,
                    fetched_at=self.plan.as_of,
                )
                self.assertEqual(
                    result.status,
                    OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.coverage_complete)
                self.assertEqual(result.documents, ())

    def test_issuer_identity_drift_is_rejected(self):
        payload = self.payload()
        payload["announcements"][0]["orgId"] = "other-org"

        result = parse_cninfo_business_material_payload(
            self.query,
            payload,
            fetched_at=self.plan.as_of,
        )

        self.assertEqual(
            result.status,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
        )


if __name__ == "__main__":
    unittest.main()
