import unittest
from dataclasses import replace
from datetime import date, timedelta

import requests

from radar.sources.leader_business_official import (
    CninfoBusinessMaterialQuery,
    OfficialBusinessMaterialDiscoveryStatus,
    fetch_cninfo_business_materials,
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

    def paged_payload(self, *, page_number, total=33, has_more=None):
        start = (page_number - 1) * 30
        stop = min(start + 30, total)
        actual_has_more = stop < total if has_more is None else has_more
        return {
            "totalAnnouncement": total,
            "totalpages": 1,
            "hasMore": actual_has_more,
            "announcements": [{
                "secCode": self.item.symbol,
                "orgId": "gssz0000001",
                "announcementId": f"report-{index:03d}",
                "announcementTitle": f"年度报告材料{index:03d}",
                "announcementTime": int(
                    (
                        self.plan.as_of - timedelta(days=30 + index)
                    ).timestamp() * 1000
                ),
                "adjunctUrl": (
                    "finalpage/2026-07-01/"
                    f"report-{index:03d}.PDF"
                ),
                "adjunctType": "PDF",
            } for index in range(start, stop)],
        }

    def test_fetch_collects_the_second_page_before_marking_coverage_complete(self):
        pages = []

        def transport(query):
            pages.append(query.page_number)
            return self.paged_payload(page_number=query.page_number)

        result = fetch_cninfo_business_materials(
            self.query,
            transport=transport,
            clock=lambda: self.plan.as_of,
        )

        self.assertEqual(
            result.status,
            OfficialBusinessMaterialDiscoveryStatus.READY,
        )
        self.assertEqual(pages, [1, 2])
        self.assertEqual(len(result.documents), 33)
        self.assertTrue(result.coverage_complete)

    def test_second_page_failure_never_returns_partial_documents(self):
        def transport(query):
            if query.page_number == 2:
                raise RuntimeError("page unavailable")
            return self.paged_payload(page_number=1)

        result = fetch_cninfo_business_materials(
            self.query,
            transport=transport,
            clock=lambda: self.plan.as_of,
        )

        self.assertEqual(
            result.status,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.documents, ())
        self.assertFalse(result.coverage_complete)

    def test_cross_page_total_drift_is_source_unverified(self):
        def transport(query):
            return self.paged_payload(
                page_number=query.page_number,
                total=(33 if query.page_number == 1 else 34),
                has_more=(query.page_number == 1),
            )

        result = fetch_cninfo_business_materials(
            self.query,
            transport=transport,
            clock=lambda: self.plan.as_of,
        )

        self.assertEqual(
            result.status,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_material_page_coverage_unverified",),
        )

    def test_transient_request_error_retries_once_but_programming_error_does_not(self):
        request_attempts = []

        def transient(query):
            request_attempts.append(query.page_number)
            if len(request_attempts) == 1:
                raise requests.ConnectionError("temporary")
            return self.payload()

        recovered = fetch_cninfo_business_materials(
            self.query,
            transport=transient,
            clock=lambda: self.plan.as_of,
        )
        programming_attempts = []

        def programming_error(query):
            programming_attempts.append(query.page_number)
            raise RuntimeError("unexpected")

        failed = fetch_cninfo_business_materials(
            self.query,
            transport=programming_error,
            clock=lambda: self.plan.as_of,
        )

        self.assertEqual(
            recovered.status,
            OfficialBusinessMaterialDiscoveryStatus.READY,
        )
        self.assertEqual(request_attempts, [1, 1])
        self.assertEqual(
            failed.status,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(programming_attempts, [1])


if __name__ == "__main__":
    unittest.main()
