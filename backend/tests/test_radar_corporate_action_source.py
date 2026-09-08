import unittest
from datetime import date, datetime, timezone

import requests


AS_OF = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 9, 1, 6, 59, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class CorporateActionOfficialSourceTests(unittest.TestCase):
    def test_cninfo_query_uses_official_category_and_explicit_window(self):
        from radar.sources.corporate_actions import _cninfo_payload

        session = FakeSession({
            "totalRecordNum": 0,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
            "announcements": [],
        })

        _cninfo_payload(
            session,
            query_from=date(2026, 5, 1),
            query_until=date(2026, 5, 19),
            page_number=1,
            category="category_qyfpxzcs_szsh",
        )

        url, kwargs = session.calls[0]
        self.assertEqual(
            url,
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        )
        self.assertEqual(
            kwargs["data"]["category"],
            "category_qyfpxzcs_szsh",
        )
        self.assertEqual(kwargs["data"]["searchkey"], "")
        self.assertEqual(kwargs["data"]["seDate"], "2026-05-01~2026-05-19")

    def test_cninfo_transient_gateway_error_retries_same_page_once(self):
        from radar.sources.corporate_actions import _cninfo_payload

        good = {
            "totalRecordNum": 0,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
            "announcements": [],
        }

        class GatewayResponse(FakeResponse):
            def __init__(self, payload, status_code):
                super().__init__(payload)
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(response=response)

        class RetrySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if len(self.calls) == 1:
                    return GatewayResponse({}, 504)
                return GatewayResponse(good, 200)

        session = RetrySession({})
        payload = _cninfo_payload(
            session,
            query_from=date(2026, 8, 1),
            query_until=date(2026, 8, 17),
            page_number=7,
            category="category_gqbd_szsh",
        )

        self.assertEqual(payload["totalRecordNum"], 0)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(
            session.calls[0][1]["data"],
            session.calls[1][1]["data"],
        )

    def test_cninfo_non_transient_http_error_is_not_retried(self):
        from radar.sources.corporate_actions import _cninfo_payload

        class BadRequestResponse(FakeResponse):
            status_code = 400

            def raise_for_status(self):
                response = requests.Response()
                response.status_code = self.status_code
                raise requests.HTTPError(response=response)

        class BadRequestSession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return BadRequestResponse({})

        session = BadRequestSession({})
        with self.assertRaises(requests.HTTPError):
            _cninfo_payload(
                session,
                query_from=date(2026, 8, 1),
                query_until=date(2026, 8, 17),
                page_number=7,
                category="category_gqbd_szsh",
            )
        self.assertEqual(len(session.calls), 1)

    def test_cninfo_vendor_floor_page_count_is_accepted_only_after_all_rows(self):
        from radar.sources.corporate_actions import (
            validate_cninfo_corporate_action_pages,
        )

        def row(index):
            return {
                "secCode": f"{index:06d}",
                "announcementId": str(1225000000 + index),
                "announcementTitle": "2025年度权益分派实施公告",
                "announcementTime": 1779120000000,
                "adjunctUrl": (
                    f"finalpage/2026-05-19/{1225000000 + index}.PDF"
                ),
                "pageColumn": "SZZB" if index < 30 else "SHZB",
            }

        pages = (
            {
                "totalRecordNum": 31,
                "totalAnnouncement": 31,
                "totalpages": 1,
                "hasMore": True,
                "announcements": [row(index) for index in range(30)],
            },
            {
                "totalRecordNum": 31,
                "totalAnnouncement": 31,
                "totalpages": 1,
                "hasMore": False,
                "announcements": [row(30)],
            },
        )

        rows = validate_cninfo_corporate_action_pages(pages)

        self.assertEqual(len(rows), 31)
        self.assertEqual(rows[-1]["pageColumn"], "SHZB")

    def test_cninfo_query_over_vendor_page_cap_is_split_by_date(self):
        from radar.sources.corporate_actions import (
            _fetch_cninfo_corporate_action_pages,
        )

        class CappedSession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                window = kwargs["data"]["seDate"]
                if window == "2026-08-01~2026-09-02":
                    rows = [{
                        "secCode": "600000",
                        "announcementId": str(1225000000 + index),
                        "announcementTitle": "股权变动公告",
                        "announcementTime": 1779120000000,
                        "adjunctUrl": (
                            f"finalpage/example/{1225000000 + index}.PDF"
                        ),
                        "pageColumn": "SHZB",
                    } for index in range(30)]
                    return FakeResponse({
                        "totalRecordNum": 3001,
                        "totalAnnouncement": 3001,
                        "totalpages": 100,
                        "hasMore": True,
                        "announcements": rows,
                    })
                return FakeResponse({
                    "totalRecordNum": 0,
                    "totalAnnouncement": 0,
                    "totalpages": 0,
                    "hasMore": False,
                    "announcements": [],
                })

        session = CappedSession({})
        rows = _fetch_cninfo_corporate_action_pages(
            session,
            query_from=date(2026, 8, 1),
            query_until=date(2026, 9, 2),
            category="category_gqbd_szsh",
            search_key="",
        )

        self.assertEqual(rows, ())
        self.assertEqual(
            [call[1]["data"]["seDate"] for call in session.calls],
            [
                "2026-08-01~2026-09-02",
                "2026-08-01~2026-08-17",
                "2026-08-18~2026-09-02",
            ],
        )

    def test_cninfo_single_day_over_vendor_page_cap_fails_closed(self):
        from radar.sources.corporate_actions import (
            _fetch_cninfo_corporate_action_pages,
        )

        session = FakeSession({
            "totalRecordNum": 3001,
            "totalAnnouncement": 3001,
            "totalpages": 100,
            "hasMore": True,
            "announcements": [{
                "secCode": f"{index:06d}",
                "announcementId": str(1225000000 + index),
                "announcementTitle": "股权变动公告",
                "announcementTime": 1779120000000,
                "adjunctUrl": f"finalpage/example/{index}.PDF",
                "pageColumn": "SHZB",
            } for index in range(30)],
        })

        with self.assertRaisesRegex(
            ValueError,
            "cninfo_corporate_action_daily_result_exceeds_page_cap",
        ):
            _fetch_cninfo_corporate_action_pages(
                session,
                query_from=date(2026, 9, 2),
                query_until=date(2026, 9, 2),
                category="category_gqbd_szsh",
                search_key="",
            )

    def test_cninfo_page_or_count_gap_fails_closed(self):
        from radar.sources.corporate_actions import (
            validate_cninfo_corporate_action_pages,
        )

        with self.assertRaisesRegex(
            ValueError,
            "cninfo_corporate_action_count_inconsistent",
        ):
            validate_cninfo_corporate_action_pages(({
                "totalRecordNum": 2,
                "totalAnnouncement": 2,
                "totalpages": 0,
                "hasMore": False,
                "announcements": [{
                    "secCode": "000001",
                    "announcementId": "1225000001",
                    "announcementTitle": "2025年度权益分派实施公告",
                    "announcementTime": 1779120000000,
                    "adjunctUrl": "finalpage/2026-05-19/1225000001.PDF",
                    "pageColumn": "SZZB",
                }],
            },))

    def test_cninfo_official_zero_result_null_rows_is_verified_empty(self):
        from radar.sources.corporate_actions import (
            validate_cninfo_corporate_action_pages,
        )

        rows = validate_cninfo_corporate_action_pages(({
            "totalRecordNum": 0,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
            "announcements": None,
        },))

        self.assertEqual(rows, ())

    def test_cninfo_verified_empty_distribution_window_covers_all_three_exchanges(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        session = FakeSession({
            "totalRecordNum": 0,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
            "announcements": [],
        })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=date(2026, 9, 1),
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(
            [collection.exchange for collection in collections],
            ["sse", "szse", "bse"],
        )
        self.assertTrue(
            all(collection.coverage_from == date(2026, 9, 1)
                for collection in collections)
        )
        self.assertTrue(
            all(collection.coverage_through == date(2026, 9, 1)
                for collection in collections)
        )
        self.assertTrue(all(collection.expected_count == 0
                            for collection in collections))
        self.assertTrue(all(collection.reasons == ()
                            for collection in collections))
        self.assertEqual(
            {call[1]["data"]["category"] for call in session.calls},
            {
                "category_qyfpxzcs_szsh",
                "category_pg_szsh",
                "category_zf_szsh",
                "category_gqbd_szsh",
                "",
            },
        )
        self.assertEqual(
            {
                call[1]["data"]["searchkey"]
                for call in session.calls
                if call[1]["data"]["category"] == ""
            },
            {
                "证券代码变更实施公告",
                "缩股实施公告",
                "股份拆细实施公告",
                "分立实施公告",
            },
        )

    def test_cninfo_unimplemented_exact_action_candidate_fails_closed(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        candidate = {
            "secCode": "600000",
            "announcementId": "1225999999",
            "announcementTitle": "关于股份拆细实施公告",
            "announcementTime": 1788220800000,
            "adjunctUrl": "finalpage/2026-09-01/1225999999.PDF",
            "pageColumn": "SHZB",
        }

        class QuerySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = (
                    [candidate]
                    if kwargs["data"]["searchkey"] == "股份拆细实施公告"
                    else []
                )
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows,
                })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=AS_OF.date(),
            session=QuerySession({}),
            clock=lambda: FETCHED_AT,
        )

        sse = next(item for item in collections if item.exchange == "sse")
        self.assertEqual(sse.expected_count, 1)
        self.assertEqual(sse.items, ())
        self.assertIn(
            "sse_corporate_action_types_incomplete",
            sse.reasons,
        )

    def test_cninfo_parser_failure_preserves_per_document_diagnostics(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        candidate = {
            "secCode": "600000",
            "announcementId": "1225999998",
            "announcementTitle": "2025年年度权益分派实施公告",
            "announcementTime": int(FETCHED_AT.timestamp() * 1000),
            "adjunctUrl": "finalpage/2026-09-01/1225999998.PDF",
            "pageColumn": "SHZB",
        }

        class QuerySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = (
                    [candidate]
                    if kwargs["data"]["category"]
                    == "category_qyfpxzcs_szsh"
                    else []
                )
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows,
                })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=AS_OF.date(),
            session=QuerySession({}),
            clock=lambda: FETCHED_AT,
            document_loader=lambda *_: (
                "证券代码：600000 除权除息日待定",
                "a" * 64,
            ),
        )

        sse = next(item for item in collections if item.exchange == "sse")
        self.assertEqual(sse.expected_count, 1)
        self.assertEqual(sse.items, ())
        self.assertEqual(len(sse.unresolved_documents), 1)
        unresolved = sse.unresolved_documents[0]
        self.assertEqual(unresolved.query_name, "equity_distribution")
        self.assertEqual(unresolved.symbol, "600000")
        self.assertEqual(unresolved.announcement_id, "1225999998")
        self.assertEqual(
            unresolved.reason,
            "cninfo_equity_distribution_effective_date_missing",
        )
        self.assertEqual(unresolved.source_sha256, "a" * 64)

    def test_cninfo_unimplemented_candidate_has_explicit_diagnostic(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        candidate = {
            "secCode": "600000",
            "announcementId": "1225999999",
            "announcementTitle": "关于股份拆细实施公告",
            "announcementTime": int(FETCHED_AT.timestamp() * 1000),
            "adjunctUrl": "finalpage/2026-09-01/1225999999.PDF",
            "pageColumn": "SHZB",
        }

        class QuerySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = (
                    [candidate]
                    if kwargs["data"]["searchkey"]
                    == "股份拆细实施公告"
                    else []
                )
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows,
                })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=AS_OF.date(),
            session=QuerySession({}),
            clock=lambda: FETCHED_AT,
        )

        sse = next(item for item in collections if item.exchange == "sse")
        self.assertEqual(len(sse.unresolved_documents), 1)
        unresolved = sse.unresolved_documents[0]
        self.assertEqual(unresolved.query_name, "stock_split")
        self.assertEqual(
            unresolved.reason,
            "cninfo_stock_split_parser_missing",
        )

    def test_versioned_cninfo_policy_names_every_required_action_type(self):
        from radar.sources.corporate_actions import (
            REQUIRED_CORPORATE_ACTION_TYPES,
            cninfo_corporate_action_query_policy,
        )

        policy = cninfo_corporate_action_query_policy()

        self.assertEqual(
            policy["policyId"],
            "cninfo-corporate-action-discovery-v2",
        )
        self.assertEqual(
            set(policy["requiredActionTypes"]),
            set(REQUIRED_CORPORATE_ACTION_TYPES),
        )
        self.assertEqual(
            policy["queries"]["equity_distribution"]["category"],
            "category_qyfpxzcs_szsh",
        )
        self.assertEqual(
            policy["queries"]["rights_issue"]["category"],
            "category_pg_szsh",
        )
        self.assertEqual(
            policy["queries"]["placement"]["category"],
            "category_zf_szsh",
        )
        self.assertEqual(
            policy["queries"]["equity_change"]["category"],
            "category_gqbd_szsh",
        )
        self.assertEqual(
            set(policy["deterministicActionTypes"]),
            {
                "cash_dividend",
                "bonus_share",
                "capitalization_issue",
                "rights_issue",
                "placement",
                "repurchase_cancellation",
                "merger",
                "code_change",
                "reverse_split",
            },
        )
        self.assertEqual(
            set(policy["missingActionTypes"]),
            {"stock_split", "demerger"},
        )
        self.assertEqual(
            policy["queries"]["code_change"],
            {
                "category": "",
                "searchKey": "证券代码变更实施公告",
                "actionTypes": ("code_change",),
            },
        )
        self.assertEqual(
            policy["queries"]["reverse_split"]["searchKey"],
            "缩股实施公告",
        )

    def test_cninfo_exact_search_query_cannot_be_widened(self):
        from radar.sources.corporate_actions import _cninfo_payload

        session = FakeSession({
            "totalRecordNum": 0,
            "totalAnnouncement": 0,
            "totalpages": 0,
            "hasMore": False,
            "announcements": None,
        })

        _cninfo_payload(
            session,
            query_from=date(2001, 1, 1),
            query_until=date(2026, 9, 1),
            page_number=1,
            category="",
            search_key="证券代码变更实施公告",
        )

        self.assertEqual(
            session.calls[0][1]["data"]["searchkey"],
            "证券代码变更实施公告",
        )
        with self.assertRaisesRegex(
            ValueError,
            "cninfo_corporate_action_query_unverified",
        ):
            _cninfo_payload(
                session,
                query_from=date(2001, 1, 1),
                query_until=date(2026, 9, 1),
                page_number=1,
                category="",
                search_key="代码变更",
            )

    def test_rights_issue_listing_document_yields_effective_event(self):
        from radar.sources.corporate_actions import parse_cninfo_rights_issue_text

        item = parse_cninfo_rights_issue_text(
            title="广东鸿特科技股份有限公司配股新增股份变动报告及上市公告书",
            text="""
            证券代码：300176
            公司本次配股配售的144,173,119股人民币普通股将于2026年9月2日起上市流通。
            新增股份的上市时间为2026年9月2日。
            """,
            symbol="300176",
            exchange="szse",
            announced_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/finalpage/2026-08-28/1225530947.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:1225530947",
        )

        self.assertEqual(item.action_type, "rights_issue")
        self.assertEqual(item.effective_on, date(2026, 9, 2))

    def test_rights_issue_result_document_yields_exact_ex_right_date(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_rights_issue_result_text,
        )

        item = parse_cninfo_rights_issue_result_text(
            title="广东鸿特科技股份有限公司配股发行结果公告",
            text="""
            证券代码：300176
            本次配股发行成功。
            本公告披露当日（2026 年 8 月 21 日，R+7 日）
            即为发行成功的除权基准日（配股除权日）。
            """,
            symbol="300176",
            exchange="szse",
            announced_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2026-08-21/"
                "1225486243.PDF"
            ),
            source_sha256="a" * 64,
            document_id="cninfo:1225486243",
        )

        self.assertEqual(item.action_type, "rights_issue")
        self.assertEqual(item.effective_on, date(2026, 8, 21))

    def test_placement_tip_is_rejected_but_listing_document_is_deterministic(self):
        from radar.sources.corporate_actions import parse_cninfo_placement_text

        common = {
            "symbol": "300083",
            "exchange": "szse",
            "announced_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-08-24/1225499794.PDF",
            "source_sha256": "b" * 64,
            "document_id": "cninfo:1225499794",
        }
        with self.assertRaisesRegex(
            ValueError,
            "cninfo_placement_title_unverified",
        ):
            parse_cninfo_placement_text(
                title="向特定对象发行股票上市的提示性公告",
                text="预计于2026年8月27日上市",
                **common,
            )

        item = parse_cninfo_placement_text(
            title="2025年度向特定对象发行股票上市公告书",
            text="""
            证券代码：300083
            本次向特定对象发行股票发行对象认购的股份。
            新增股份的上市时间为2026年8月27日。
            """,
            **common,
        )
        self.assertEqual(item.action_type, "placement")
        self.assertEqual(item.effective_on, date(2026, 8, 27))

    def test_placement_accepts_official_listing_time_variants(self):
        from radar.sources.corporate_actions import parse_cninfo_placement_text

        cases = (
            (
                "2025年度以简易程序向特定对象发行股票并在创业板上市之上市公告书",
                "股票代码：301125 本次发行的股票为境内上市人民币普通股。"
                "股票上市时间：2026年8月18日（上市首日）。",
                "301125",
                "szse",
                date(2026, 8, 18),
            ),
            (
                "向特定对象发行股票上市公告书",
                "证券代码：920476 本次向特定对象发行股票总额为4,405,940股。"
                "本次向特定对象发行新增股票将于2026年8月17日"
                "在北京证券交易所上市并公开交易。",
                "920476",
                "bse",
                date(2026, 8, 17),
            ),
        )
        for title, text, symbol, exchange, expected_date in cases:
            with self.subTest(symbol=symbol):
                item = parse_cninfo_placement_text(
                    title=title,
                    text=text,
                    symbol=symbol,
                    exchange=exchange,
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="c" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(item.effective_on, expected_date)

    def test_repurchase_cancellation_requires_completion_not_proposal(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_repurchase_cancellation_text,
        )

        common = {
            "symbol": "600690",
            "exchange": "sse",
            "announced_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-09-01/1225538081.PDF",
            "source_sha256": "c" * 64,
            "document_id": "cninfo:1225538081",
        }
        with self.assertRaisesRegex(
            ValueError,
            "cninfo_repurchase_cancellation_title_unverified",
        ):
            parse_cninfo_repurchase_cancellation_text(
                title="关于变更回购股份用途并注销的议案",
                text="拟注销以减少注册资本",
                **common,
            )

        item = parse_cninfo_repurchase_cancellation_text(
            title="关于部分回购股份注销完成暨股份变动的公告",
            text="""
            证券代码：600690
            本次回购股份注销事宜将于2026年9月1日办理完成。
            本次部分回购股份注销完成后，公司总股本将减少。
            """,
            **common,
        )
        self.assertEqual(item.action_type, "repurchase_cancellation")
        self.assertEqual(item.effective_on, date(2026, 9, 1))

    def test_repurchase_cancellation_accepts_official_completion_variants(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_repurchase_cancellation_text,
        )

        cases = (
            (
                "证券代码：002823 经中国证券登记结算有限责任公司深圳分公司"
                "审核确认，公司本次回购股份的注销事宜已于2026年8月13日办理完成。"
                "本次注销完成后，公司总股本减少。",
                "002823",
                "szse",
                date(2026, 8, 13),
            ),
            (
                "证券代码：300573 经中国证券登记结算有限责任公司深圳分公司"
                "审核确认，公司本次回购股份注销事宜已于2026年8月7日起生效。"
                "本次注销完成后，公司总股本减少。",
                "300573",
                "szse",
                date(2026, 8, 7),
            ),
            (
                "证券代码：920392 公司已于2026年8月5日在中国证券登记结算"
                "有限责任公司北京分公司办理完毕上述6200股回购股份的注销手续。"
                "本次回购股份注销完成后，公司股份总额减少。",
                "920392",
                "bse",
                date(2026, 8, 5),
            ),
        )
        for text, symbol, exchange, expected_date in cases:
            with self.subTest(symbol=symbol):
                item = parse_cninfo_repurchase_cancellation_text(
                    title="关于回购股份注销完成暨股份变动公告",
                    text=text,
                    symbol=symbol,
                    exchange=exchange,
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="d" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(item.effective_on, expected_date)

    def test_repurchase_cancellation_accepts_explicit_official_cancellation_date(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_repurchase_cancellation_text,
        )

        item = parse_cninfo_repurchase_cancellation_text(
            title="关于回购股份注销完成暨股份变动的公告",
            text="""
            证券代码：600201
            本次注销完成后，公司总股本减少。
            回购股份注销日：2026年7月21日。
            """,
            symbol="600201",
            exchange="sse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="d" * 64,
            document_id="cninfo:explicit-cancellation-date",
        )

        self.assertEqual(item.effective_on, date(2026, 7, 21))

    def test_placement_uses_exact_official_share_registration_date(self):
        from radar.sources.corporate_actions import parse_cninfo_placement_text

        item = parse_cninfo_placement_text(
            title="2026年度向特定对象发行股票上市公告书",
            text="""
            证券代码：688401
            本次向特定对象发行的12,020,153股股份已于2026年7月14日
            在中国证券登记结算有限责任公司上海分公司办理完毕股份登记手续。
            """,
            symbol="688401",
            exchange="sse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="d" * 64,
            document_id="cninfo:placement-registration-date",
        )

        self.assertEqual(item.effective_on, date(2026, 7, 14))

    def test_placement_accepts_parenthesized_official_registration_date(self):
        from radar.sources.corporate_actions import parse_cninfo_placement_text

        cases = (
            (
                "603268",
                "截至2026年7月29日（本次新增股份登记日），"
                "公司前十大股东持股情况如下。",
                date(2026, 7, 29),
            ),
            (
                "688072",
                "截至2026年7月1日（新增股份登记日），"
                "公司前十名股东持股情况如下。",
                date(2026, 7, 1),
            ),
        )
        for symbol, registration_text, expected_date in cases:
            with self.subTest(symbol=symbol):
                item = parse_cninfo_placement_text(
                    title="2026年度向特定对象发行股票上市公告书",
                    text=(
                        f"证券代码：{symbol} "
                        "本次向特定对象发行股票已完成登记。"
                        f"{registration_text}"
                    ),
                    symbol=symbol,
                    exchange="sse",
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="d" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(item.effective_on, expected_date)

    def test_placement_summary_is_not_counted_as_a_second_event(self):
        from radar.sources.corporate_actions import parse_cninfo_placement_text

        with self.assertRaisesRegex(ValueError, "cninfo_placement_title_unverified"):
            parse_cninfo_placement_text(
                title="向特定对象发行股票上市公告书（摘要）",
                text="证券代码：688012 本次向特定对象发行新增股份于2026年7月2日完成登记",
                symbol="688012",
                exchange="sse",
                announced_at=FETCHED_AT,
                source_url="https://static.cninfo.com.cn/example.PDF",
                source_sha256="d" * 64,
                document_id="cninfo:placement-summary",
            )

    def test_merger_requires_implementation_result_and_listing_date(self):
        from radar.sources.corporate_actions import parse_cninfo_merger_text

        common = {
            "symbol": "300277",
            "exchange": "szse",
            "announced_at": datetime(2026, 2, 9, tzinfo=timezone.utc),
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-02-09/1224973667.PDF",
            "source_sha256": "d" * 64,
            "document_id": "cninfo:1224973667",
        }
        with self.assertRaisesRegex(ValueError, "cninfo_merger_title_unverified"):
            parse_cninfo_merger_text(
                title="换股吸收合并草案",
                text="新增股份上市日待定",
                **common,
            )

        item = parse_cninfo_merger_text(
            title="关于换股吸收合并换股实施结果、股份变动暨新增股份上市公告",
            text="""
            证券代码：300277
            本次股票上市类型为吸收合并股份。
            股票上市流通日期为2026年2月11日。
            """,
            **common,
        )
        self.assertEqual(item.action_type, "merger")
        self.assertEqual(item.effective_on, date(2026, 2, 11))

    def test_code_change_preserves_old_and_new_symbol(self):
        from radar.sources.corporate_actions import parse_cninfo_code_change_text

        item = parse_cninfo_code_change_text(
            title="关于变更公司证券简称及证券代码的实施公告",
            text="""
            证券代码：300114
            变更前变更后 证券代码 300114 302132
            变更后的证券代码：302132
            变更后的证券简称及证券代码启用日期为2025年2月17日。
            """,
            symbol="300114",
            exchange="szse",
            announced_at=datetime(2025, 2, 15, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF",
            source_sha256="e" * 64,
            document_id="cninfo:1222544408",
        )

        self.assertEqual(item.action_type, "code_change")
        self.assertEqual(item.symbol, "300114")
        self.assertEqual(item.new_symbol, "302132")
        self.assertEqual(item.effective_on, date(2025, 2, 17))

    def test_reverse_split_requires_implemented_share_reduction(self):
        from radar.sources.corporate_actions import parse_cninfo_reverse_split_text

        common = {
            "symbol": "600381",
            "exchange": "sse",
            "announced_at": datetime(2014, 6, 27, tzinfo=timezone.utc),
            "source_url": "https://static.cninfo.com.cn/finalpage/2014-06-27/1200011113.PDF",
            "source_sha256": "f" * 64,
            "document_id": "cninfo:1200011113",
        }
        with self.assertRaisesRegex(
            ValueError,
            "cninfo_reverse_split_title_unverified",
        ):
            parse_cninfo_reverse_split_text(
                title="关于拟实施缩股方案的公告",
                text="缩股实施日待定",
                **common,
            )

        item = parse_cninfo_reverse_split_text(
            title="关于《重整计划》中缩股方案实施的公告",
            text="""
            股票代码：600381
            公司总股本将由重整前的1,601,845,390股缩减至198,925,752股。
            缩股实施日：2014年6月25日 除权日：2014年6月27日
            """,
            **common,
        )
        self.assertEqual(item.action_type, "reverse_split")
        self.assertEqual(item.effective_on, date(2014, 6, 25))

    def test_cninfo_collector_promotes_all_verified_implementation_documents(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        def row(identifier, symbol, board, title, announced_at):
            return {
                "secCode": symbol,
                "announcementId": identifier,
                "announcementTitle": title,
                "announcementTime": int(announced_at.timestamp() * 1000),
                "adjunctUrl": f"finalpage/example/{identifier}.PDF",
                "pageColumn": board,
            }

        documents = {
            "1": "证券代码：300176 本次配股配售新增股份的上市时间为2026年9月2日",
            "2": "证券代码：300083 本次向特定对象发行新增股份的上市时间为2026年8月27日",
            "3": "证券代码：600690 回购股份注销事宜将于2026年9月1日办理完成 回购股份注销完成后总股本减少",
            "4": "证券代码：300277 本次股票上市类型为吸收合并股份 股票上市流通日期为2026年2月11日",
            "5": "证券代码：300114 变更后的证券代码：302132 变更后的证券简称及证券代码启用日期为2025年2月17日",
            "6": "股票代码：600381 公司总股本将由1,601股缩减至198股 缩股实施日：2014年6月25日",
        }
        rows_by_query = {
            ("category_pg_szsh", ""): [row(
                "1", "300176", "SZCY",
                "配股新增股份变动报告及上市公告书",
                datetime(2026, 8, 28, tzinfo=timezone.utc),
            )],
            ("category_zf_szsh", ""): [row(
                "2", "300083", "SZCY",
                "向特定对象发行股票上市公告书",
                datetime(2026, 8, 24, tzinfo=timezone.utc),
            )],
            ("category_gqbd_szsh", ""): [
                row(
                    "3", "600690", "SHZB",
                    "关于部分回购股份注销完成暨股份变动的公告",
                    datetime(2026, 8, 31, tzinfo=timezone.utc),
                ),
                row(
                    "4", "300277", "SZCY",
                    "关于换股吸收合并换股实施结果、股份变动暨新增股份上市公告",
                    datetime(2026, 2, 9, tzinfo=timezone.utc),
                ),
            ],
            ("", "证券代码变更实施公告"): [row(
                "5", "300114", "SZCY",
                "关于变更公司证券简称及证券代码的实施公告",
                datetime(2025, 2, 15, tzinfo=timezone.utc),
            )],
            ("", "缩股实施公告"): [row(
                "6", "600381", "SHZB",
                "关于《重整计划》中缩股方案实施的公告",
                datetime(2014, 6, 27, tzinfo=timezone.utc),
            )],
        }

        class QuerySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = rows_by_query.get((
                    kwargs["data"]["category"],
                    kwargs["data"]["searchkey"],
                ), [])
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows or None,
                })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=date(2014, 1, 1),
            session=QuerySession({}),
            clock=lambda: FETCHED_AT,
            document_loader=lambda _, url: (
                documents[url.rsplit("/", 1)[-1].removesuffix(".PDF")],
                "9" * 64,
            ),
        )

        items = [item for collection in collections for item in collection.items]
        self.assertEqual(
            {item.action_type for item in items},
            {
                "rights_issue",
                "placement",
                "repurchase_cancellation",
                "merger",
                "code_change",
                "reverse_split",
            },
        )
        code_change = next(item for item in items if item.action_type == "code_change")
        self.assertEqual(code_change.new_symbol, "302132")
        self.assertTrue(all(collection.reasons == () for collection in collections))

    def test_cninfo_page_column_accepts_only_verified_a_share_boards(self):
        from radar.sources.corporate_actions import (
            cninfo_exchange_from_page_column,
        )

        self.assertEqual(cninfo_exchange_from_page_column("SHZB"), "sse")
        self.assertEqual(cninfo_exchange_from_page_column("SHKCB"), "sse")
        self.assertEqual(cninfo_exchange_from_page_column("SZZB"), "szse")
        self.assertEqual(cninfo_exchange_from_page_column("SZCY"), "szse")
        self.assertEqual(cninfo_exchange_from_page_column("BJS"), "bse")
        with self.assertRaisesRegex(
            ValueError,
            "cninfo_corporate_action_page_column_unverified",
        ):
            cninfo_exchange_from_page_column("UNKNOWN")

    def test_cninfo_distribution_parser_is_shared_by_all_three_exchanges(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度利润分配及资本公积金转增股本实施公告",
            text="""
            证券代码：000001 证券简称：平安银行
            向全体股东每 10 股转增 2 股，每 10 股派 1.00 元人民币现金。
            除权除息日为：2026 年 5 月 27 日
            """,
            symbol="000001",
            exchange="szse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example",
        )

        self.assertEqual(
            [item.action_type for item in items],
            ["capitalization_issue", "cash_dividend"],
        )
        self.assertTrue(all(item.exchange == "szse" for item in items))

    def test_distribution_accepts_official_a_share_code_and_rmb_wording(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派A股实施公告",
            text="""
            A股代码：601868 A股简称：中国能建
            A股每股现金红利人民币0.0312元（含税）
            股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
            A股 2026/8/21 - 2026/8/24 2026/8/24
            """,
            symbol="601868",
            exchange="sse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example-a-share-code",
        )

        self.assertEqual([item.action_type for item in items], ["cash_dividend"])
        self.assertEqual(items[0].effective_on, date(2026, 8, 24))

    def test_distribution_accepts_dual_listed_code_and_amount_before_currency(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度A股权益分派实施公告",
            text="""
            证券代码（A/H）：000063/00763
            每10股派发4.11元人民币现金（含税）
            股权登记日：2026年7月28日
            除权除息日：2026年7月29日
            """,
            symbol="000063",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:dual-listed",
        )

        self.assertEqual([item.action_type for item in items], ["cash_dividend"])
        self.assertEqual(items[0].effective_on, date(2026, 7, 29))

    def test_distribution_invalid_extracted_date_has_stable_failure_reason(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        with self.assertRaisesRegex(
            ValueError,
            "cninfo_equity_distribution_effective_date_invalid",
        ):
            parse_cninfo_equity_distribution_text(
                title="2025年年度权益分派实施公告",
                text=(
                    "证券代码：301019 每10股派发现金红利6.50元。"
                    "除权除息日为：2026年73月8日。"
                ),
                symbol="301019",
                exchange="szse",
                announced_at=FETCHED_AT,
                source_url="https://static.cninfo.com.cn/example.PDF",
                source_sha256="a" * 64,
                document_id="cninfo:bad-extracted-date",
            )

    def test_distribution_accepts_official_cash_wording_and_date_labels(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        cases = (
            (
                "股票代码：300946 每10股派发现金股利人民币1.20元。"
                "除权除息日（红利发放日）：2026年9月4日。",
                "300946",
                "szse",
                date(2026, 9, 4),
            ),
            (
                "存托凭证代码：689009 每份存托凭证现金红利：1.23852元。"
                "存托凭证登记日 除权（息）日 现金红利发放日 "
                "2026/8/6 2026/8/7 2026/8/7",
                "689009",
                "sse",
                date(2026, 8, 7),
            ),
            (
                "股票代码：900929 B股每股现金红利0.0996元。"
                "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日 "
                "B股 2026/8/12 2026/8/7 2026/8/10 2026/8/20",
                "900929",
                "sse",
                date(2026, 8, 10),
            ),
        )
        for text, symbol, exchange, expected_date in cases:
            with self.subTest(symbol=symbol):
                items = parse_cninfo_equity_distribution_text(
                    title="2026年半年度权益分派实施公告",
                    text=text,
                    symbol=symbol,
                    exchange=exchange,
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="b" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(
                    [item.action_type for item in items],
                    ["cash_dividend"],
                )
                self.assertEqual(items[0].effective_on, expected_date)

    def test_distribution_accepts_explicit_red_share_wording(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度利润分配实施公告",
            text="""
            证券代码：001388
            向全体股东每10股送红股4.80股，派5.00元人民币现金。
            除权除息日为：2026年7月17日。
            """,
            symbol="001388",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:red-share",
        )

        self.assertEqual(
            [item.action_type for item in items],
            ["bonus_share", "cash_dividend"],
        )

    def test_distribution_accepts_a_share_dividend_implementation_title(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度A股派息实施公告",
            text="""
            证券代码：000756
            每10股派发现金人民币1.50元（含税）。
            A股除权除息日为：2026年7月31日。
            """,
            symbol="000756",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:a-share-dividend",
        )

        self.assertEqual(
            [item.action_type for item in items],
            ["cash_dividend"],
        )

    def test_distribution_accepts_cninfo_midyear_dividend_titles(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        for title in (
            "2026年中期分红实施公告",
            "2026年中期分红A股实施公告",
        ):
            with self.subTest(title=title):
                items = parse_cninfo_equity_distribution_text(
                    title=title,
                    text="""
                    证券代码：300750
                    每10股派发现金人民币1.50元（含税）。
                    A股除权除息日为：2026年8月10日。
                    """,
                    symbol="300750",
                    exchange="szse",
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="a" * 64,
                    document_id="cninfo:midyear-dividend",
                )

                self.assertEqual(
                    [item.action_type for item in items],
                    ["cash_dividend"],
                )

    def test_cninfo_distribution_proposal_is_not_promoted(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        with self.assertRaisesRegex(
            ValueError,
            "cninfo_equity_distribution_title_unverified",
        ):
            parse_cninfo_equity_distribution_text(
                title="2025年度利润分配预案",
                text="拟每10股派1元，除权除息日待定",
                symbol="600000",
                exchange="sse",
                announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
                source_url="https://static.cninfo.com.cn/example.PDF",
                source_sha256="a" * 64,
                document_id="cninfo:example",
            )

    def test_sse_distribution_table_and_per_share_amount_are_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="中广天择传媒股份有限公司2025年年度权益分派实施公告",
            text="""
            证券代码：603721 证券简称：*ST天择
            A 股每股现金红利0.08元
            股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
            Ａ股 2026/5/25 － 2026/5/26 2026/5/26
            """,
            symbol="603721",
            exchange="sse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2026-05-19/"
                "1225312843.PDF"
            ),
            source_sha256="a" * 64,
            document_id="cninfo:1225312843",
        )

        self.assertEqual([item.action_type for item in items], ["cash_dividend"])
        self.assertEqual(items[0].effective_on, date(2026, 5, 26))

    def test_szse_colon_effective_date_is_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度利润分配及资本公积金转增股本实施公告",
            text="""
            证券代码：300811 证券简称：铂科新材
            每10股派2.000000元人民币现金，每10股转增4.000000股。
            除权除息日：2026年5月27日
            """,
            symbol="300811",
            exchange="szse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example",
        )

        self.assertEqual(
            [item.action_type for item in items],
            ["capitalization_issue", "cash_dividend"],
        )
        self.assertEqual(items[0].effective_on, date(2026, 5, 27))

    def test_sse_three_column_distribution_table_is_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text="""
            证券代码：688380 证券简称：中微半导
            每股现金红利0.3元
            股权登记日 除权（息）日 现金红利发放日
            2026/5/22 2026/5/25 2026/5/25
            """,
            symbol="688380",
            exchange="sse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example",
        )

        self.assertEqual([item.action_type for item in items], ["cash_dividend"])
        self.assertEqual(items[0].effective_on, date(2026, 5, 25))

    def test_szse_four_column_distribution_table_is_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text="""
            证券代码：301328 证券简称：维峰电子
            每10股派3.00元，每10股转增4.5股。
            股权登记日 除权除息日 本次所转的无限售条件流通股的起始交易日 现金红利发放日
            2026年5月25日 2026年5月26日 2026年5月26日 2026年5月26日
            """,
            symbol="301328",
            exchange="szse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example",
        )

        self.assertEqual(items[0].effective_on, date(2026, 5, 26))

    def test_szse_parenthesized_ex_date_label_is_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度分红派息、转增股本实施公告",
            text="""
            证券代码：301222 证券简称：浙江恒威
            每10股派5.00元，每10股转增4股。
            除权日（除息日）：2026年5月27日（星期三）
            """,
            symbol="301222",
            exchange="szse",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:example",
        )

        self.assertEqual(items[0].effective_on, date(2026, 5, 27))

    def test_szse_monthly_table_preserves_structured_actions_without_faking_announcement_time(self):
        from radar.sources.corporate_actions import (
            parse_szse_monthly_corporate_actions,
        )

        html = """
        <table><caption>DIVIDEND,BONUS AND RIGHTS ISSUES - （2026.07）</caption>
        <tr><th>Code</th><th>Securities</th><th>Bonus(Shs)</th><th>BPS</th>
        <th>Cash Div.</th><th>DPS</th><th>Rts Issues</th><th>RPS</th>
        <th>Pla. Pri.</th><th>Funds Raised</th><th>Ex-Date</th>
        <th>Reg. Date</th><th>Ex-Price</th><th>Pre-Closing</th></tr>
        <tr><td>000001</td><td>平安银行</td><td>10,000</td><td>0.100</td>
        <td>20,000</td><td>0.200</td><td>30,000</td><td>0.300</td>
        <td>8.88</td><td>266,400</td><td>2026/07/15</td>
        <td>2026/07/14</td><td>10.00</td><td>10.20</td></tr></table>
        """.encode("gb18030")

        collection = parse_szse_monthly_corporate_actions(
            html,
            source_url=(
                "https://docs.static.szse.cn/www/market/periodical/month/"
                "W020260806351614974469.html"
            ),
            source_time=datetime(2026, 8, 6, 6, 3, 58, tzinfo=timezone.utc),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(collection.exchange, "szse")
        self.assertEqual(collection.coverage_through, date(2026, 7, 31))
        self.assertEqual(collection.expected_count, 3)
        self.assertEqual(
            [item.action_type for item in collection.items],
            ["bonus_share", "cash_dividend", "rights_issue"],
        )
        self.assertTrue(all(item.announced_at is None for item in collection.items))
        self.assertTrue(
            all(item.effective_on == date(2026, 7, 15) for item in collection.items)
        )
        self.assertIn(
            "szse_company_announcement_time_missing",
            collection.reasons,
        )

    def test_bse_official_distribution_text_yields_deterministic_events(self):
        from radar.sources.corporate_actions import (
            parse_bse_equity_distribution_text,
        )

        items = parse_bse_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text="""
            证券代码：920002 证券简称：万达轴承
            2025 年年度权益分派实施公告
            向全体股东每 10 股转增 4 股，每 10 股派 1.00 元人民币现金。
            本次权益分派权益登记日为：2026 年 5 月 26 日
            除权除息日为：2026 年 5 月 27 日
            """,
            symbol="920002",
            announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2026-05-19/"
                "1225316970.PDF"
            ),
            source_sha256="a" * 64,
            document_id="cninfo:1225316970",
        )

        self.assertEqual(
            [item.action_type for item in items],
            ["capitalization_issue", "cash_dividend"],
        )
        self.assertTrue(
            all(item.effective_on == date(2026, 5, 27) for item in items)
        )
        self.assertTrue(all(item.announced_at is not None for item in items))

    def test_bse_generic_title_is_not_promoted_to_formal_action(self):
        from radar.sources.corporate_actions import (
            parse_bse_equity_distribution_text,
        )

        with self.assertRaisesRegex(
            ValueError,
            "bse_equity_distribution_title_unverified",
        ):
            parse_bse_equity_distribution_text(
                title="关于权益分派的提示性公告",
                text="除权除息日为 2026 年 5 月 27 日",
                symbol="920002",
                announced_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
                source_url="https://static.cninfo.com.cn/example.PDF",
                source_sha256="a" * 64,
                document_id="cninfo:example",
            )

    def test_three_exchange_collections_are_combined_without_hiding_scope_gaps(self):
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_corporate_action_forward_snapshot,
        )

        def collection(exchange, source, reasons=()):
            return CorporateActionExchangeCollection(
                exchange=exchange,
                source_id=f"{exchange}-official-empty",
                source=source,
                source_time=FETCHED_AT,
                fetched_at=FETCHED_AT,
                coverage_from=AS_OF.date(),
                coverage_through=AS_OF.date(),
                expected_count=0,
                items=(),
                reasons=reasons,
            )

        session = FakeSession({
            "result": [],
            "pageHelp": {"total": 0, "pageNo": 1, "pageCount": 0},
        })
        snapshot = fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
            szse_collector=lambda **_: collection("szse", "深交所"),
            bse_collector=lambda **_: collection(
                "bse",
                "北交所",
                ("bse_corporate_action_types_incomplete",),
            ),
        )

        self.assertEqual(snapshot.covered_exchanges, ["sse", "szse", "bse"])
        self.assertEqual(snapshot.missing_exchanges, [])
        self.assertEqual(
            snapshot.coverage_through_by_exchange,
            {"sse": AS_OF.date(), "szse": AS_OF.date(), "bse": AS_OF.date()},
        )
        self.assertEqual(
            snapshot.coverage_reasons_by_exchange,
            {"bse": ["bse_corporate_action_types_incomplete"]},
        )

    def test_cninfo_all_market_collections_are_merged_into_forward_snapshot(self):
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_corporate_action_forward_snapshot,
        )

        def collection(exchange, *, coverage_from, reasons=()):
            return CorporateActionExchangeCollection(
                exchange=exchange,
                source_id=f"{exchange}-official-empty",
                source=f"{exchange}官方公告",
                source_time=FETCHED_AT,
                fetched_at=FETCHED_AT,
                coverage_from=coverage_from,
                coverage_through=AS_OF.date(),
                expected_count=0,
                items=(),
                reasons=reasons,
            )

        snapshot = fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=FakeSession({
                "result": [],
                "pageHelp": {"total": 0, "pageNo": 1, "pageCount": 0},
            }),
            clock=lambda: FETCHED_AT,
            szse_collector=lambda **_: collection(
                "szse",
                coverage_from=date(2026, 8, 1),
            ),
            cninfo_collector=lambda **_: (
                collection(
                    "sse",
                    coverage_from=AS_OF.date(),
                    reasons=("sse_corporate_action_types_incomplete",),
                ),
                collection(
                    "szse",
                    coverage_from=AS_OF.date(),
                    reasons=("szse_corporate_action_types_incomplete",),
                ),
                collection(
                    "bse",
                    coverage_from=AS_OF.date(),
                    reasons=("bse_corporate_action_types_incomplete",),
                ),
            ),
        )

        self.assertEqual(snapshot.covered_exchanges, ["sse", "szse", "bse"])
        self.assertEqual(snapshot.missing_exchanges, [])
        self.assertEqual(
            snapshot.coverage_from_by_exchange,
            {
                "sse": AS_OF.date(),
                "szse": date(2026, 8, 1),
                "bse": AS_OF.date(),
            },
        )
        self.assertIn(
            "szse_corporate_action_types_incomplete",
            snapshot.coverage_reasons_by_exchange["szse"],
        )

    def test_monthly_and_cninfo_same_event_prefers_original_announcement(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            _merge_exchange_collections,
        )

        common = {
            "symbol": "000001",
            "exchange": "szse",
            "actionType": "cash_dividend",
            "effectiveOn": date(2026, 7, 15),
            "sourceName": "官方来源",
            "sourceUrl": "https://static.cninfo.com.cn/example.PDF",
            "sourceSha256": "a" * 64,
        }
        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly",
            source="深交所月表",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                **common,
                announcedAt=None,
                documentId="szse-monthly:000001",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        cninfo = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="cninfo-july",
            source="巨潮公告",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=AS_OF.date(),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                **common,
                announcedAt=FETCHED_AT,
                documentId="cninfo:1225000001",
            ),),
        )

        merged = _merge_exchange_collections("szse", (monthly, cninfo))

        self.assertEqual(merged.expected_count, 1)
        self.assertEqual(len(merged.items), 1)
        self.assertEqual(merged.items[0].document_id, "cninfo:1225000001")
        self.assertEqual(merged.items[0].announced_at, FETCHED_AT)
        self.assertNotIn(
            "szse_company_announcement_time_missing",
            merged.reasons,
        )

    def test_monthly_broad_bonus_is_replaced_by_precise_capitalization(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            _merge_exchange_collections,
        )

        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly",
            source="深交所月表",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                symbol="300806",
                exchange="szse",
                actionType="bonus_share",
                announcedAt=None,
                effectiveOn=date(2026, 7, 27),
                sourceName="深圳证券交易所统计月报",
                sourceUrl="https://docs.static.szse.cn/month.html",
                sourceSha256="a" * 64,
                documentId="szse-monthly:2026-07",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        official = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="cninfo-exact",
            source="巨潮官方公告",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=AS_OF.date(),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                symbol="300806",
                exchange="szse",
                actionType="capitalization_issue",
                announcedAt=FETCHED_AT,
                effectiveOn=date(2026, 7, 27),
                sourceName="巨潮资讯（深交所官方公告）",
                sourceUrl="https://static.cninfo.com.cn/example.PDF",
                sourceSha256="b" * 64,
                documentId="cninfo:precise-capitalization",
            ),),
        )

        merged = _merge_exchange_collections("szse", (monthly, official))

        self.assertEqual(merged.expected_count, 1)
        self.assertEqual(len(merged.items), 1)
        self.assertEqual(merged.items[0].action_type, "capitalization_issue")
        self.assertNotIn(
            "szse_company_announcement_time_missing",
            merged.reasons,
        )

    def test_szse_monthly_announcement_backfill_only_accepts_exact_prior_month_match(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_cninfo_szse_monthly_announcement_backfill,
        )

        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly-2026-07",
            source="深交所月表",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                symbol="000719",
                exchange="szse",
                actionType="cash_dividend",
                announcedAt=None,
                effectiveOn=date(2026, 7, 1),
                sourceName="深圳证券交易所统计月报",
                sourceUrl="https://docs.static.szse.cn/month.html",
                sourceSha256="a" * 64,
                documentId="szse-monthly:2026-07",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        candidate = {
            "secCode": "000719",
            "announcementId": "1225990001",
            "announcementTitle": "2025年年度权益分派实施公告",
            "announcementTime": int(
                datetime(2026, 6, 29, 8, 0, tzinfo=timezone.utc).timestamp()
                * 1000
            ),
            "adjunctUrl": "finalpage/2026-06-29/1225990001.PDF",
            "pageColumn": "SZZB",
        }
        unrelated = {
            **candidate,
            "secCode": "000001",
            "announcementId": "1225990002",
            "adjunctUrl": "finalpage/2026-06-29/1225990002.PDF",
        }
        session = FakeSession({
            "totalRecordNum": 2,
            "totalAnnouncement": 2,
            "totalpages": 1,
            "hasMore": False,
            "announcements": [candidate, unrelated],
        })

        backfill = fetch_cninfo_szse_monthly_announcement_backfill(
            monthly=monthly,
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
            document_loader=lambda _, url: (
                "证券代码：000719 每10股派1.00元 "
                "除权除息日为：2026年7月1日",
                "b" * 64,
            ),
        )

        self.assertEqual(backfill.coverage_from, date(2026, 7, 1))
        self.assertEqual(backfill.coverage_through, date(2026, 7, 31))
        self.assertEqual(backfill.expected_count, 1)
        self.assertEqual(len(backfill.items), 1)
        self.assertEqual(backfill.items[0].document_id, "cninfo:1225990001")
        self.assertIsNotNone(backfill.items[0].announced_at)
        self.assertEqual(session.calls[0][1]["data"]["seDate"], "2026-06-01~2026-06-30")

    def test_forward_snapshot_merges_szse_monthly_announcement_backfill(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_corporate_action_forward_snapshot,
        )

        common = {
            "symbol": "000719",
            "exchange": "szse",
            "actionType": "cash_dividend",
            "effectiveOn": date(2026, 7, 1),
            "sourceName": "官方来源",
            "sourceUrl": "https://static.cninfo.com.cn/example.PDF",
            "sourceSha256": "c" * 64,
        }
        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly",
            source="深交所月表",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                **common,
                announcedAt=None,
                documentId="szse-monthly:2026-07",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        backfill = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="cninfo-prior-month-backfill",
            source="巨潮回连",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                **common,
                announcedAt=FETCHED_AT,
                documentId="cninfo:1225990001",
            ),),
        )

        def empty_collection(exchange):
            return CorporateActionExchangeCollection(
                exchange=exchange,
                source_id=f"{exchange}-empty",
                source="巨潮公告",
                source_time=FETCHED_AT,
                fetched_at=FETCHED_AT,
                coverage_from=date(2026, 7, 1),
                coverage_through=AS_OF.date(),
                expected_count=0,
                items=(),
            )

        snapshot = fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=FakeSession({
                "result": [],
                "pageHelp": {"total": 0, "pageNo": 1, "pageCount": 0},
            }),
            clock=lambda: FETCHED_AT,
            szse_collector=lambda **_: monthly,
            szse_announcement_backfill_collector=lambda **_: backfill,
            cninfo_collector=lambda **_: tuple(
                empty_collection(exchange)
                for exchange in ("sse", "szse", "bse")
            ),
        )

        szse_items = [item for item in snapshot.items if item.exchange == "szse"]
        self.assertEqual(len(szse_items), 1)
        self.assertEqual(szse_items[0].document_id, "cninfo:1225990001")
        self.assertNotIn(
            "szse_company_announcement_time_missing",
            snapshot.coverage_reasons_by_exchange.get("szse", []),
        )

    def test_szse_monthly_backfill_uses_official_symbol_query_for_category_gap(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_cninfo_szse_monthly_announcement_backfill,
        )

        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly-2026-07",
            source="深交所月表",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 7, 1),
            coverage_through=date(2026, 7, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                symbol="002941",
                exchange="szse",
                actionType="cash_dividend",
                announcedAt=None,
                effectiveOn=date(2026, 7, 9),
                sourceName="深圳证券交易所统计月报",
                sourceUrl="https://docs.static.szse.cn/month.html",
                sourceSha256="a" * 64,
                documentId="szse-monthly:2026-07",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        row = {
            "secCode": "002941",
            "orgId": "9900024967",
            "announcementId": "1225406528",
            "announcementTitle": "2025年度利润分配实施的公告",
            "announcementTime": int(
                datetime(2026, 7, 3, tzinfo=timezone.utc).timestamp() * 1000
            ),
            "adjunctUrl": "finalpage/2026-07-03/1225406528.PDF",
            "pageColumn": "SZZB",
        }

        class SymbolQuerySession(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return FakeResponse({
                    "stockList": [{
                        "code": "002941",
                        "orgId": "9900024967",
                        "zwjc": "新疆交建",
                    }],
                })

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                data = kwargs["data"]
                rows = [row] if data.get("stock") == "002941,9900024967" else []
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows or None,
                })

        session = SymbolQuerySession({})
        result = fetch_cninfo_szse_monthly_announcement_backfill(
            monthly=monthly,
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
            document_loader=lambda *_: (
                "证券代码：002941 每10股派1.00元 "
                "除权除息日为：2026年7月9日",
                "b" * 64,
            ),
        )

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].document_id, "cninfo:1225406528")
        symbol_calls = [
            kwargs["data"]
            for url, kwargs in session.calls
            if url.endswith("/hisAnnouncement/query")
            and kwargs["data"].get("stock")
        ]
        self.assertEqual(len(symbol_calls), 1)
        self.assertEqual(symbol_calls[0]["stock"], "002941,9900024967")
        self.assertEqual(symbol_calls[0]["category"], "")
        self.assertEqual(symbol_calls[0]["seDate"], "2026-06-01~2026-07-31")

    def test_szse_monthly_backfill_matches_rights_issue_result_ex_date(self):
        from radar.replay_source_adapters import CorporateActionEvidenceItem
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_cninfo_szse_monthly_announcement_backfill,
        )

        monthly = CorporateActionExchangeCollection(
            exchange="szse",
            source_id="szse-monthly-2026-08",
            source="深交所月报",
            source_time=FETCHED_AT,
            fetched_at=FETCHED_AT,
            coverage_from=date(2026, 8, 1),
            coverage_through=date(2026, 8, 31),
            expected_count=1,
            items=(CorporateActionEvidenceItem(
                symbol="300176",
                exchange="szse",
                actionType="rights_issue",
                announcedAt=None,
                effectiveOn=date(2026, 8, 21),
                sourceName="深圳证券交易所统计月报",
                sourceUrl="https://docs.static.szse.cn/month.html",
                sourceSha256="a" * 64,
                documentId="szse-monthly:2026-08",
            ),),
            reasons=("szse_company_announcement_time_missing",),
        )
        row = {
            "secCode": "300176",
            "orgId": "9900016527",
            "announcementId": "1225486243",
            "announcementTitle": "广东鸿特科技股份有限公司配股发行结果公告",
            "announcementTime": int(
                datetime(2026, 8, 21, tzinfo=timezone.utc).timestamp()
                * 1000
            ),
            "adjunctUrl": "finalpage/2026-08-21/1225486243.PDF",
            "pageColumn": "SZCY",
        }

        class SymbolQuerySession(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return FakeResponse({
                    "stockList": [{
                        "code": "300176",
                        "orgId": "9900016527",
                        "zwjc": "鸿特科技",
                    }],
                })

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = (
                    [row]
                    if kwargs["data"].get("stock")
                    == "300176,9900016527"
                    else []
                )
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows or None,
                })

        result = fetch_cninfo_szse_monthly_announcement_backfill(
            monthly=monthly,
            as_of=AS_OF,
            session=SymbolQuerySession({}),
            clock=lambda: FETCHED_AT,
            document_loader=lambda *_: (
                """
                证券代码：300176
                本次配股发行成功。
                本公告披露当日（2026年8月21日，R+7日）
                即为发行成功的除权基准日（配股除权日）。
                """,
                "b" * 64,
            ),
        )

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].action_type, "rights_issue")
        self.assertEqual(result.items[0].effective_on, date(2026, 8, 21))
        self.assertEqual(result.items[0].document_id, "cninfo:1225486243")

    def test_cninfo_overlap_starts_at_szse_monthly_coverage_start(self):
        from radar.sources.corporate_actions import (
            CorporateActionExchangeCollection,
            fetch_corporate_action_forward_snapshot,
        )

        def collection(exchange, coverage_from, coverage_through):
            return CorporateActionExchangeCollection(
                exchange=exchange,
                source_id=f"{exchange}-official-empty",
                source=f"{exchange}官方公告",
                source_time=FETCHED_AT,
                fetched_at=FETCHED_AT,
                coverage_from=coverage_from,
                coverage_through=coverage_through,
                expected_count=0,
                items=(),
                reasons=(),
            )

        received = {}

        def collect_cninfo(**kwargs):
            received.update(kwargs)
            return tuple(
                collection(
                    exchange,
                    date(2026, 8, 1),
                    AS_OF.date(),
                )
                for exchange in ("sse", "szse", "bse")
            )

        fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=FakeSession({
                "result": [],
                "pageHelp": {"total": 0, "pageNo": 1, "pageCount": 0},
            }),
            clock=lambda: FETCHED_AT,
            szse_collector=lambda **_: collection(
                "szse",
                date(2026, 7, 1),
                date(2026, 7, 31),
            ),
            cninfo_collector=collect_cninfo,
        )

        self.assertEqual(received["query_from"], date(2026, 7, 1))

    def test_sse_verified_empty_query_is_partial_not_full_market_ready(self):
        from radar.sources.corporate_actions import (
            fetch_corporate_action_forward_snapshot,
        )

        session = FakeSession({
            "result": [],
            "pageHelp": {"total": 0, "pageNo": 1, "pageCount": 0},
        })

        snapshot = fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(snapshot.covered_exchanges, ["sse"])
        self.assertEqual(snapshot.missing_exchanges, ["szse", "bse"])
        self.assertEqual(snapshot.expected_count, 0)
        self.assertEqual(snapshot.returned_count_by_exchange, {"sse": 0})
        self.assertEqual(snapshot.items, [])
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://query.sse.com.cn/commonSoaQuery.do")
        self.assertEqual(kwargs["params"]["sqlId"], "PL_SCRL_SCRLB")
        self.assertEqual(kwargs["params"]["bizType"], 5)
        self.assertEqual(kwargs["params"]["tradeBeginDate"], "20260901")

    def test_sse_nonempty_calendar_is_not_promoted_from_title_to_event(self):
        from radar.sources.corporate_actions import (
            fetch_corporate_action_forward_snapshot,
        )

        session = FakeSession({
            "result": [{
                "stockCode": "600000",
                "stockAbbr": "浦发银行",
                "bizType": "5",
                "title": "分红送转提示",
            }],
            "pageHelp": {"total": 1, "pageNo": 1, "pageCount": 1},
        })

        snapshot = fetch_corporate_action_forward_snapshot(
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(snapshot.expected_count, 0)
        self.assertEqual(snapshot.returned_count_by_exchange, {"sse": 0})
        self.assertEqual(snapshot.items, [])
        self.assertIn("rawCount=1", snapshot.source_id)

    def test_invalid_official_payload_fails_closed(self):
        from radar.sources.corporate_actions import (
            fetch_corporate_action_forward_snapshot,
        )

        with self.assertRaisesRegex(RuntimeError, "sse_corporate_action_payload_invalid"):
            fetch_corporate_action_forward_snapshot(
                as_of=AS_OF,
                session=FakeSession({"result": "not-a-list"}),
                clock=lambda: FETCHED_AT,
            )

    def test_sse_four_column_distribution_table_uses_ex_date(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="青达环保2025年年度权益分派实施公告",
            text=(
                "证券代码：688501 每股现金红利0.28元 每股转增0.4股 "
                "股权登记日 除权（息）日 新增无限售条件流通股份上市日 "
                "现金红利发放日 2026/8/5 2026/8/6 2026/8/6 2026/8/6"
            ),
            symbol="688501",
            exchange="sse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="a" * 64,
            document_id="cninfo:1225446021",
        )

        self.assertEqual(
            {item.action_type for item in items},
            {"cash_dividend", "capitalization_issue"},
        )
        self.assertEqual({item.effective_on for item in items}, {date(2026, 8, 6)})

    def test_sse_ordinary_share_table_and_cash_dividend_wording_are_supported(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        cases = (
            (
                "600000",
                "普通股每股现金红利人民币0.42元 "
                "股份类别 股权登记日 最后交易日 除权（息）日 "
                "现金红利发放日 普通股 2026/7/15 － 2026/7/16 2026/7/16",
                date(2026, 7, 16),
            ),
            (
                "601939",
                "A股每股现金股息人民币0.2029元 "
                "股份类别 股权登记日 最后交易日 除权（息）日 "
                "现金红利发放日 A股 2026/7/10 － 2026/7/13 2026/7/13",
                date(2026, 7, 13),
            ),
        )
        for symbol, body, expected_date in cases:
            with self.subTest(symbol=symbol):
                items = parse_cninfo_equity_distribution_text(
                    title="2025年年度A股分红派息实施公告",
                    text=f"证券代码：{symbol} {body}",
                    symbol=symbol,
                    exchange="sse",
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="b" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].action_type, "cash_dividend")
                self.assertEqual(items[0].effective_on, expected_date)

    def test_szse_explicit_ex_date_variants_are_deterministic(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        cases = (
            ("002749", "除息日为：2026年8月27日", date(2026, 8, 27)),
            ("000709", "除息日：2026年7月23日", date(2026, 7, 23)),
            ("000063", "A股除息日为2026年7月29日", date(2026, 7, 29)),
            (
                "000532",
                "除权除息及红利发放日为：2026年7月2日",
                date(2026, 7, 2),
            ),
            (
                "300129",
                "除息日以及红利发放日为2026年7月31日",
                date(2026, 7, 31),
            ),
        )
        for symbol, label, expected_date in cases:
            with self.subTest(symbol=symbol):
                items = parse_cninfo_equity_distribution_text(
                    title="2025年年度权益分派实施公告",
                    text=f"证券代码：{symbol} 每10股派0.60元 {label}",
                    symbol=symbol,
                    exchange="szse",
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="c" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(items[0].effective_on, expected_date)

    def test_invalid_ex_date_can_use_two_matching_operational_dates(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text=(
                "证券代码：301019 每10股派发现金红利6.50元 每10股转增2股 "
                "除权除息日为：2026年73月8日 "
                "本次所送（转）股于2026年7月8日直接记入股东证券账户。"
                "现金红利将于2026年7月8日通过托管机构划入资金账户。"
            ),
            symbol="301019",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="d" * 64,
            document_id="cninfo:1225403233",
        )

        self.assertEqual({item.effective_on for item in items}, {date(2026, 7, 8)})

    def test_szse_page_number_inside_ex_date_heading_is_ignored(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text=(
                "证券代码：000888 每10股派发现金股利1.50元 "
                "本次权益分派股权登记日为：2026年7月16日，除权除息\n"
                "3\n日为：2026年7月17日。"
            ),
            symbol="000888",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="f" * 64,
            document_id="cninfo:1225417254",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].effective_on, date(2026, 7, 17))

    def test_cash_distribution_wording_is_supported(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年年度权益分派实施公告",
            text=(
                "证券代码：301500 向全体股东每10股派发现金分红0.55元 "
                "股权登记日：2026年7月9日 除息日：2026年7月10日"
            ),
            symbol="301500",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="1" * 64,
            document_id="cninfo:1225405288",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].action_type, "cash_dividend")
        self.assertEqual(items[0].effective_on, date(2026, 7, 10))

    def test_szse_dual_code_notice_can_verify_b_share_event(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_equity_distribution_text,
        )

        items = parse_cninfo_equity_distribution_text(
            title="2025年度权益分派实施公告",
            text=(
                "证券代码：000596、200596 每10股派现金34.00元 "
                "A股股权登记日为：2026年7月15日，除权除息日为："
                "2026年7月16日。B股最后交易日为：2026年7月16日，"
                "除权除息日为：2026年7月17日，股权登记日为："
                "2026年7月21日。"
            ),
            symbol="200596",
            exchange="szse",
            announced_at=FETCHED_AT,
            source_url="https://static.cninfo.com.cn/example.PDF",
            source_sha256="2" * 64,
            document_id="cninfo:1225417834",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].symbol, "200596")
        self.assertEqual(items[0].effective_on, date(2026, 7, 17))

    def test_cninfo_dual_code_notice_emits_a_and_b_share_events(self):
        from radar.sources.corporate_actions import (
            fetch_cninfo_equity_distribution_actions,
        )

        candidate = {
            "secCode": "000596",
            "announcementId": "1225417834",
            "announcementTitle": "2025年度权益分派实施公告",
            "announcementTime": int(FETCHED_AT.timestamp() * 1000),
            "adjunctUrl": "finalpage/2026-09-01/1225417834.PDF",
            "pageColumn": "SZZB",
        }

        class QuerySession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                rows = (
                    [candidate]
                    if kwargs["data"]["category"]
                    == "category_qyfpxzcs_szsh"
                    else []
                )
                return FakeResponse({
                    "totalRecordNum": len(rows),
                    "totalAnnouncement": len(rows),
                    "totalpages": 1 if rows else 0,
                    "hasMore": False,
                    "announcements": rows,
                })

        collections = fetch_cninfo_equity_distribution_actions(
            as_of=AS_OF,
            query_from=AS_OF.date(),
            session=QuerySession({}),
            clock=lambda: FETCHED_AT,
            document_loader=lambda *_: (
                "证券代码：000596、200596 每10股派现金34.00元 "
                "A股股权登记日为：2026年7月15日，除权除息日为："
                "2026年7月16日。B股最后交易日为：2026年7月16日，"
                "除权除息日为：2026年7月17日，股权登记日为："
                "2026年7月21日。",
                "3" * 64,
            ),
        )

        szse = next(item for item in collections if item.exchange == "szse")
        self.assertEqual({item.symbol for item in szse.items}, {"000596", "200596"})
        self.assertEqual(
            {item.symbol: item.effective_on for item in szse.items},
            {"000596": date(2026, 7, 16), "200596": date(2026, 7, 17)},
        )

    def test_szse_b_share_code_layout_variants_are_deterministic(self):
        from radar.sources.corporate_actions import (
            _szse_b_share_symbol_from_document,
        )

        cases = (
            ("证券代码：000530；200530 证券简称：冰山冷热；冰山B", "000530", "200530"),
            ("证券代码：001872/201872 证券简称：招商港口/招港B", "001872", "201872"),
            ("证券代码：000581200581 证券简称：威孚高科", "000581", "200581"),
            (
                "证券代码：000550 证券简称：江铃汽车 公告编号：2026-025\n"
                "200550 江铃B",
                "000550",
                "200550",
            ),
        )
        for text, primary, expected in cases:
            with self.subTest(primary=primary):
                self.assertEqual(
                    _szse_b_share_symbol_from_document(text, primary),
                    expected,
                )

    def test_repurchase_completion_date_variants_require_completed_registration(self):
        from radar.sources.corporate_actions import (
            parse_cninfo_repurchase_cancellation_text,
        )

        cases = (
            (
                "000950",
                "公司于2026年7月8日在中国结算深圳分公司办理完毕上述"
                "回购股份的注销手续。本次回购股份注销完成后股本结构变动表",
                date(2026, 7, 8),
            ),
            (
                "300628",
                "回购股份注销完成情况2026年7月7日，公司已在中国结算深圳"
                "分公司办理完毕上述191股回购股份的注销手续。"
                "本次回购股份注销完成后公司总股本减少",
                date(2026, 7, 7),
            ),
            (
                "000423",
                "截至2026年6月30日，公司已在中国结算深圳分公司办理完毕"
                "本次回购股份注销手续。本次回购股份注销完成后公司股本变动",
                date(2026, 6, 30),
            ),
            (
                "301071",
                "公司已于2026年6月30日在中国证券登记结算有限责任公司"
                "深圳分公司办理完成已回购股份5,822,020股的注销事宜。"
                "本次回购股份注销完成后公司总股本减少",
                date(2026, 6, 30),
            ),
        )
        for symbol, body, expected_date in cases:
            with self.subTest(symbol=symbol):
                item = parse_cninfo_repurchase_cancellation_text(
                    title="关于回购股份注销完成暨股份变动的公告",
                    text=f"证券代码：{symbol} {body}",
                    symbol=symbol,
                    exchange="szse",
                    announced_at=FETCHED_AT,
                    source_url="https://static.cninfo.com.cn/example.PDF",
                    source_sha256="e" * 64,
                    document_id=f"cninfo:{symbol}",
                )
                self.assertEqual(item.effective_on, expected_date)


if __name__ == "__main__":
    unittest.main()
