import unittest
from datetime import date, datetime, timedelta, timezone

import requests

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystKind,
    OfficialBusinessCatalystQuery,
    fetch_official_business_catalysts,
)
from radar.sources.leader_business_document_content import (
    fetch_official_business_document_content,
)


UTC = timezone.utc
FETCHED_AT = datetime(2026, 8, 21, 5, 0, tzinfo=UTC)


def query():
    return OfficialBusinessCatalystQuery(
        candidate_plan_id="candidate-plan-1",
        symbol="000001",
        issuer_identity="cninfo-org:9900000001",
        window_from=date(2026, 1, 1),
        window_until=date(2026, 8, 21),
    )


def row(identifier, title, *, days_ago, symbol="000001", org_id="9900000001"):
    published_at = FETCHED_AT - timedelta(days=days_ago)
    return {
        "announcementId": identifier,
        "announcementTitle": title,
        "announcementTime": int(published_at.timestamp() * 1000),
        "secCode": symbol,
        "orgId": org_id,
        "adjunctUrl": f"finalpage/2026-08-01/{identifier}.PDF",
        "adjunctType": "PDF",
    }


def payload(*rows, has_more=False, total_pages=None, total=None):
    actual_total = len(rows) if total is None else total
    return {
        "announcements": list(rows),
        "totalAnnouncement": actual_total,
        "totalpages": (
            (1 if actual_total else 0)
            if total_pages is None
            else total_pages
        ),
        "hasMore": has_more,
    }


class LeaderBusinessCatalystOfficialTests(unittest.TestCase):
    def test_official_empty_null_page_is_valid_missing_not_unverified(self):
        empty = {
            "announcements": None,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
        }

        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.MAJOR_CONTRACT,),
            transport=lambda _query, _kind: empty,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.MISSING)
        self.assertEqual(result.reasons, ("business_catalyst_missing",))

    def test_all_pages_are_fetched_and_coverage_is_reconciled(self):
        calls = []

        def transport(actual_query, _kind):
            calls.append(actual_query.page_number)
            if actual_query.page_number == 1:
                return payload(
                    row("7901", "关于签订重大合同的公告", days_ago=1),
                    has_more=True,
                    total_pages=2,
                    total=2,
                )
            return payload(
                row("7902", "合同公告", days_ago=2),
                has_more=False,
                total_pages=2,
                total=2,
            )

        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.MAJOR_CONTRACT,),
            transport=transport,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(
            tuple(item.document_id for item in result.documents),
            ("cninfo:7901", "cninfo:7902"),
        )

    def test_real_nonempty_zero_totalpages_uses_counts_and_has_more(self):
        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.EARNINGS_FORECAST,),
            transport=lambda _query, _kind: payload(
                row("7951", "业绩预告", days_ago=1),
                total_pages=0,
                total=1,
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.documents[0].document_id, "cninfo:7951")

    def test_allowlisted_events_are_deduped_sorted_and_capped_at_three(self):
        shared = row("8001", "关于签订重大合同的公告", days_ago=1)
        responses = {
            OfficialBusinessCatalystKind.MAJOR_CONTRACT: payload(
                shared,
                row("8002", "日常关联交易公告", days_ago=2),
            ),
            OfficialBusinessCatalystKind.PROJECT_AWARD: payload(
                row("8003", "项目中标公告", days_ago=3),
            ),
            OfficialBusinessCatalystKind.CAPACITY_START: payload(
                row("8004", "生产线建成投产公告", days_ago=4),
            ),
            OfficialBusinessCatalystKind.PRODUCT_CERTIFICATION: payload(
                row("8005", "产品获得认证公告", days_ago=5),
            ),
            OfficialBusinessCatalystKind.PRIVATE_PLACEMENT_PROJECT: payload(),
            OfficialBusinessCatalystKind.EARNINGS_FORECAST: payload(shared),
        }

        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            transport=lambda _query, kind: responses[kind],
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            tuple(item.document_id for item in result.documents),
            ("cninfo:8001", "cninfo:8003", "cninfo:8004"),
        )
        self.assertEqual(len(result.documents), 3)
        self.assertNotIn(
            "日常关联交易公告",
            tuple(item.title for item in result.documents),
        )
        self.assertFalse(result.formal_gate_ready)

    def test_contract_governance_documents_are_not_catalyst_events(self):
        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.MAJOR_CONTRACT,),
            transport=lambda _query, _kind: payload(
                row("8051", "日常经营重大合同管理制度", days_ago=1),
                row(
                    "8052",
                    "关于确定日常经营重大合同自愿性披露标准的公告",
                    days_ago=2,
                ),
                row(
                    "8054",
                    "日常经营重大合同信息披露管理办法（2026年4月修订）",
                    days_ago=2,
                ),
                row(
                    "8053",
                    "关于签订智算中心建设设备采购协议的公告",
                    days_ago=3,
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            tuple(item.document_id for item in result.documents),
            ("cninfo:8053",),
        )

    def test_intermediary_and_regulatory_replies_are_not_earnings_events(self):
        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.EARNINGS_FORECAST,),
            transport=lambda _query, _kind: payload(
                row(
                    "8061",
                    "会计师事务所关于业绩预告监管工作函的回复",
                    days_ago=1,
                ),
                row("8062", "2025年度业绩预告", days_ago=2),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            tuple(item.document_id for item in result.documents),
            ("cninfo:8062",),
        )

    def test_project_award_verification_opinion_is_not_an_award_event(self):
        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.PROJECT_AWARD,),
            transport=lambda _query, _kind: payload(
                row("8071", "保荐机构关于项目中标事项的核查意见", days_ago=1),
                row("8072", "关于项目中标的公告", days_ago=2),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            tuple(item.document_id for item in result.documents),
            ("cninfo:8072",),
        )

    def test_incomplete_page_future_document_and_wrong_issuer_fail_closed(self):
        cases = (
            payload(
                row("8101", "关于签订重大合同的公告", days_ago=1),
                has_more=True,
                total_pages=2,
            ),
            payload(row("8102", "关于签订重大合同的公告", days_ago=-1)),
            payload(row(
                "8103",
                "关于签订重大合同的公告",
                days_ago=1,
                org_id="other",
            )),
        )

        for value in cases:
            with self.subTest(value=value):
                result = fetch_official_business_catalysts(
                    query(),
                    fetched_at=FETCHED_AT,
                    kinds=(OfficialBusinessCatalystKind.MAJOR_CONTRACT,),
                    transport=lambda _query, _kind, payload_value=value: payload_value,
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.documents, ())

    def test_transient_request_is_retried_once_but_business_errors_are_not(self):
        attempts = []

        def transport(_query, kind):
            attempts.append(kind)
            if len(attempts) == 1:
                raise requests.ConnectionError("temporary")
            return payload(row("8201", "项目中标公告", days_ago=1))

        retried = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.PROJECT_AWARD,),
            transport=transport,
        )

        self.assertEqual(retried.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(len(attempts), 2)

        runtime_attempts = []

        def broken_transport(_query, kind):
            runtime_attempts.append(kind)
            raise RuntimeError("bad payload generator")

        failed = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.PROJECT_AWARD,),
            transport=broken_transport,
        )
        self.assertEqual(
            failed.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(len(runtime_attempts), 1)

    def test_catalyst_metadata_enters_shared_pdf_contract(self):
        result = fetch_official_business_catalysts(
            query(),
            fetched_at=FETCHED_AT,
            kinds=(OfficialBusinessCatalystKind.PROJECT_AWARD,),
            transport=lambda _query, _kind: payload(
                row("8301", "项目中标公告", days_ago=1)
            ),
        )
        calls = []

        content = fetch_official_business_document_content(
            result.documents[0],
            kind=OfficialBusinessDocumentKind.CATALYST,
            fetched_at=FETCHED_AT,
            transport=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertNotEqual(
            content.reasons,
            ("business_document_content_metadata_unverified",),
        )
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
