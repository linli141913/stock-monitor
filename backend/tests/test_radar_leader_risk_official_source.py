import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import requests

from radar.leader_risk_invalidation_features import RiskCategory
from radar.sources.leader_risk_official import (
    CNINFO_ISSUER_SEARCH_URL,
    CNINFO_QUERY_URL,
    CninfoIssuerResolutionStatus,
    CninfoRiskIssuerScope,
    CninfoRiskDiscoveryQuery,
    OfficialRiskSourceStatus,
    fetch_cninfo_issuer_scope,
    fetch_cninfo_risk_discovery,
    parse_cninfo_issuer_search_payload,
    parse_cninfo_risk_discovery_payload,
)


FETCHED_AT = datetime(
    2026,
    7,
    28,
    14,
    0,
    tzinfo=timezone.utc,
)


def make_query(**changes):
    query = CninfoRiskDiscoveryQuery(
        search_key="立案告知书",
        candidate_category=RiskCategory.INVESTIGATION,
        window_from=date(2024, 1, 1),
        window_until=date(2026, 7, 28),
        page_number=1,
        page_size=3,
    )
    return replace(query, **changes)


def make_scope(**changes):
    scope = CninfoRiskIssuerScope(
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
        resolved_at=FETCHED_AT,
    )
    return replace(scope, **changes)


def make_row(**changes):
    row = {
        "secCode": "300081",
        "secName": "ST恒信",
        "orgId": "9900012108",
        "announcementId": "1225443882",
        "announcementTitle": (
            "关于收到中国证监会<em>立案</em>"
            "<em>告知</em><em>书</em>的公告"
        ),
        "announcementTime": 1785154802000,
        "adjunctUrl": "finalpage/2026-07-27/1225443882.PDF",
        "adjunctType": "PDF",
        "columnId": "09020202||160203||250301||251302",
        "pageColumn": "SZCY",
        "announcementType": "01010503||010112||012317",
        "associateAnnouncement": None,
    }
    row.update(changes)
    return row


def make_payload(*, rows=None, total=None):
    announcements = [make_row()] if rows is None else rows
    total_records = len(announcements) if total is None else total
    return {
        "totalRecordNum": total_records,
        "totalAnnouncement": total_records,
        "totalpages": 1 if total_records else 0,
        "hasMore": False,
        "announcements": announcements,
    }


class LeaderRiskOfficialSourceTests(unittest.TestCase):
    def test_exact_official_issuer_scope_is_resolved(self):
        result = parse_cninfo_issuer_search_payload(
            "300081",
            [{"code": "300081", "orgId": "9900012108"}],
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            CninfoIssuerResolutionStatus.READY,
        )
        self.assertEqual(result.scope.symbol, "300081")
        self.assertEqual(
            result.scope.issuer_identity,
            "cninfo-org:9900012108",
        )

    def test_issuer_scope_requires_one_exact_symbol_identity(self):
        cases = (
            [],
            [{"code": "000725", "orgId": "9900012108"}],
            [
                {"code": "300081", "orgId": "9900012108"},
                {"code": "300081", "orgId": "9900012109"},
            ],
        )
        for payload in cases:
            with self.subTest(payload_count=len(payload)):
                result = parse_cninfo_issuer_search_payload(
                    "300081",
                    payload,
                    fetched_at=FETCHED_AT,
                )
                self.assertEqual(
                    result.status,
                    CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.scope)

    def test_issuer_scope_fetch_is_bounded_and_sanitized(self):
        captured = {}

        def transport(url, *, data, headers, timeout):
            captured.update({
                "url": url,
                "data": data,
                "timeout": timeout,
            })
            return [{"code": "300081", "orgId": "9900012108"}]

        result = fetch_cninfo_issuer_scope(
            "300081",
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            CninfoIssuerResolutionStatus.READY,
        )
        self.assertEqual(captured["url"], CNINFO_ISSUER_SEARCH_URL)
        self.assertEqual(captured["data"]["keyWord"], "300081")
        self.assertEqual(captured["data"]["maxNum"], "10")
        self.assertEqual(captured["timeout"], 20.0)

    def test_valid_cninfo_metadata_is_discovery_only(self):
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(result.total_records, 1)
        self.assertEqual(len(result.documents), 1)
        document = result.documents[0]
        self.assertEqual(
            document.document_id,
            "cninfo:1225443882",
        )
        self.assertEqual(
            document.issuer_identity,
            "cninfo-org:9900012108",
        )
        self.assertEqual(document.symbol, "300081")
        self.assertEqual(
            document.title,
            "关于收到中国证监会立案告知书的公告",
        )
        self.assertEqual(
            document.source_url,
            (
                "https://static.cninfo.com.cn/finalpage/"
                "2026-07-27/1225443882.PDF"
            ),
        )
        self.assertEqual(
            document.published_at.isoformat(),
            "2026-07-27T20:20:02+08:00",
        )
        self.assertEqual(
            document.raw_announcement_types,
            ("01010503", "010112", "012317"),
        )
        self.assertFalse(result.coverage_complete)
        self.assertFalse(
            result.open_event_carry_forward_complete
        )
        self.assertFalse(result.correction_links_complete)
        self.assertFalse(result.formal_usable)
        self.assertIn(
            "cninfo_keyword_discovery_not_coverage_proof",
            result.reasons,
        )

    def test_real_empty_result_preserves_zero_without_claiming_no_risk(
        self,
    ):
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(rows=[], total=0),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(result.total_records, 0)
        self.assertEqual(result.documents, ())
        self.assertFalse(result.coverage_complete)
        self.assertIn("cninfo_discovery_empty", result.reasons)

    def test_real_empty_null_announcements_is_normalized(self):
        payload = make_payload(rows=[], total=0)
        payload["announcements"] = None

        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            payload,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(result.total_records, 0)
        self.assertEqual(result.documents, ())
        self.assertIn("cninfo_discovery_empty", result.reasons)

    def test_association_field_never_becomes_a_formal_relation(self):
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(
                rows=[
                    make_row(
                        associateAnnouncement="1225000000",
                    )
                ]
            ),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertTrue(
            result.documents[0].association_reported
        )
        self.assertFalse(result.correction_links_complete)
        self.assertIn(
            "cninfo_association_semantics_unverified",
            result.reasons,
        )

    def test_invalid_query_is_rejected_before_transport(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return make_payload()

        result = fetch_cninfo_risk_discovery(
            make_query(page_size=31),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(calls, [])
        self.assertIn(
            "cninfo_query_page_size_invalid",
            result.reasons,
        )

    def test_malformed_payload_returns_stable_unverified_status(self):
        cases = (
            (None, "cninfo_response_contract_unverified"),
            (
                make_payload(rows=[make_row(secCode=[])]),
                "cninfo_document_identity_unverified",
            ),
            (
                make_payload(rows=[make_row(announcementTime="bad")]),
                "cninfo_document_time_unverified",
            ),
            (
                make_payload(rows=[make_row(adjunctType="HTML")]),
                "cninfo_document_url_unverified",
            ),
        )

        for payload, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = parse_cninfo_risk_discovery_payload(
                    make_query(),
                    payload,
                    fetched_at=FETCHED_AT,
                )
                self.assertEqual(
                    result.status,
                    OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.formal_usable)
                self.assertIn(expected_reason, result.reasons)

    def test_future_document_time_is_rejected(self):
        future_ms = int(
            (FETCHED_AT + timedelta(seconds=1)).timestamp()
            * 1000
        )
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(
                rows=[make_row(announcementTime=future_ms)]
            ),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "cninfo_document_published_in_future",
            result.reasons,
        )

    def test_document_outside_query_window_is_rejected(self):
        outside_window_ms = int(
            datetime(
                2023,
                12,
                31,
                12,
                tzinfo=timezone.utc,
            ).timestamp()
            * 1000
        )
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(
                rows=[
                    make_row(
                        announcementTime=outside_window_ms,
                    )
                ]
            ),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.documents, ())
        self.assertIn(
            "cninfo_document_outside_query_window",
            result.reasons,
        )

    def test_document_id_must_match_official_pdf_identity(self):
        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            make_payload(
                rows=[
                    make_row(
                        announcementId="9999999999",
                    )
                ]
            ),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.documents, ())
        self.assertIn(
            "cninfo_document_url_identity_mismatch",
            result.reasons,
        )

    def test_path_traversal_and_duplicate_documents_are_rejected(self):
        cases = (
            (
                [
                    make_row(
                        adjunctUrl=(
                            "finalpage/2026-07-27/../secret.PDF"
                        )
                    )
                ],
                "cninfo_document_url_unverified",
            ),
            (
                [make_row(), make_row()],
                "cninfo_document_identity_duplicate",
            ),
        )

        for rows, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = parse_cninfo_risk_discovery_payload(
                    make_query(),
                    make_payload(rows=rows, total=len(rows)),
                    fetched_at=FETCHED_AT,
                )
                self.assertEqual(
                    result.status,
                    OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_fetch_uses_bounded_https_request(self):
        captured = {}

        def transport(url, *, data, headers, timeout):
            captured.update({
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            })
            return make_payload()

        result = fetch_cninfo_risk_discovery(
            make_query(),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(captured["url"], CNINFO_QUERY_URL)
        self.assertEqual(captured["timeout"], 20.0)
        self.assertEqual(captured["data"]["pageNum"], "1")
        self.assertEqual(captured["data"]["pageSize"], "3")
        self.assertEqual(
            captured["data"]["seDate"],
            "2024-01-01~2026-07-28",
        )
        self.assertEqual(
            captured["data"]["searchkey"],
            "立案告知书",
        )
        self.assertIn("Referer", captured["headers"])

    def test_candidate_scope_is_sent_to_cninfo(self):
        captured = {}

        def transport(url, *, data, headers, timeout):
            captured["stock"] = data["stock"]
            return make_payload()

        result = fetch_cninfo_risk_discovery(
            make_query(candidate_scopes=(make_scope(),)),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(captured["stock"], "300081,9900012108")

    def test_candidate_scope_can_be_bound_to_frozen_plan_shard(self):
        query = make_query(
            candidate_scopes=(make_scope(),),
            candidate_plan_id="candidate-plan:fixture",
            shard_index=0,
            shard_count=1,
        )

        result = fetch_cninfo_risk_discovery(
            query,
            fetched_at=FETCHED_AT,
            transport=lambda *args, **kwargs: make_payload(),
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(
            result.query.candidate_plan_id,
            "candidate-plan:fixture",
        )
        self.assertEqual(result.query.shard_index, 0)
        self.assertEqual(result.query.shard_count, 1)

    def test_malformed_candidate_scope_fails_closed_without_request(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return make_payload()

        for scope in (
            make_scope(symbol=300081),
            make_scope(issuer_identity=9900012108),
        ):
            with self.subTest(scope=scope):
                result = fetch_cninfo_risk_discovery(
                    make_query(candidate_scopes=(scope,)),
                    fetched_at=FETCHED_AT,
                    transport=transport,
                )
                self.assertEqual(
                    result.status,
                    OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
                )
        self.assertEqual(calls, [])

    def test_more_than_thirty_candidate_scopes_fail_before_request(self):
        calls = []
        scopes = tuple(
            make_scope(
                symbol=f"{300000 + index:06d}",
                issuer_identity=f"fixture{index}",
            )
            for index in range(31)
        )

        result = fetch_cninfo_risk_discovery(
            make_query(candidate_scopes=scopes),
            fetched_at=FETCHED_AT,
            transport=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "cninfo_query_candidate_scope_unverified",
            result.reasons,
        )
        self.assertEqual(calls, [])

    def test_document_outside_candidate_scope_is_rejected(self):
        result = parse_cninfo_risk_discovery_payload(
            make_query(candidate_scopes=(make_scope(),)),
            make_payload(rows=[make_row(secCode="000725")]),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.documents, ())
        self.assertIn(
            "cninfo_document_outside_candidate_scope",
            result.reasons,
        )

    def test_transport_failure_is_explicit(self):
        def transport(*args, **kwargs):
            raise requests.ConnectionError("fixture unavailable")

        result = fetch_cninfo_risk_discovery(
            make_query(),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.documents, ())
        self.assertIn(
            "cninfo_source_request_failed",
            result.reasons,
        )

    def test_pagination_mismatch_is_rejected(self):
        payload = make_payload()
        payload["hasMore"] = "true"

        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            payload,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "cninfo_response_pagination_unverified",
            result.reasons,
        )

    def test_real_cninfo_page_count_inconsistency_preserves_documents(self):
        payload = make_payload(total=343)
        payload["totalpages"] = 114
        payload["hasMore"] = True

        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            payload,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(
            [document.document_id for document in result.documents],
            ["cninfo:1225443882"],
        )
        self.assertEqual(result.reported_total_pages, 114)
        self.assertEqual(result.total_pages, 115)
        self.assertIn(
            "cninfo_response_total_pages_normalized",
            result.reasons,
        )
        self.assertNotIn(
            "cninfo_response_pagination_inconsistent",
            result.reasons,
        )
        self.assertFalse(result.coverage_complete)
        self.assertFalse(result.formal_usable)

    def test_vendor_floor_totalpages_accepts_the_real_last_page(self):
        payload = make_payload(rows=[make_row()], total=343)
        payload["totalpages"] = 114
        payload["hasMore"] = False

        result = parse_cninfo_risk_discovery_payload(
            make_query(page_number=115),
            payload,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(result.reported_total_pages, 114)
        self.assertEqual(result.total_pages, 115)
        self.assertEqual(len(result.documents), 1)

    def test_short_page_preserves_documents_and_flags_inconsistency(self):
        payload = make_payload(total=5)
        payload["totalpages"] = 2
        payload["hasMore"] = True

        result = parse_cninfo_risk_discovery_payload(
            make_query(),
            payload,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            result.status,
            OfficialRiskSourceStatus.PARTIAL,
        )
        self.assertEqual(len(result.documents), 1)
        self.assertIn(
            "cninfo_response_page_records_inconsistent",
            result.reasons,
        )
        self.assertFalse(result.coverage_complete)

    def test_empty_expected_page_and_out_of_range_page_are_unverified(self):
        empty_expected_page = make_payload(rows=[], total=5)
        empty_expected_page["totalpages"] = 2
        out_of_range_page = make_payload(rows=[], total=5)
        out_of_range_page["totalpages"] = 2

        empty_result = parse_cninfo_risk_discovery_payload(
            make_query(page_number=2),
            empty_expected_page,
            fetched_at=FETCHED_AT,
        )
        out_of_range_result = parse_cninfo_risk_discovery_payload(
            make_query(page_number=3),
            out_of_range_page,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(
            empty_result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "cninfo_response_page_records_unverified",
            empty_result.reasons,
        )
        self.assertEqual(
            out_of_range_result.status,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "cninfo_response_page_out_of_range",
            out_of_range_result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
