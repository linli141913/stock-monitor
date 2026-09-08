import hashlib
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from radar.contracts import (
    EvidenceTemporalBasis,
    EtfManagementStyle,
    IndexEvidenceStatus,
)
from radar.sources.etf_index_evidence import (
    build_csindex_material_catalog,
    build_official_sse_fund_relation_document,
    build_constituent_set_evidence,
    build_methodology_evidence,
    build_official_csindex_identity_resolution,
    fetch_csindex_index_poc,
    fetch_official_csindex_identity_resolution,
    fetch_official_sse_fund_relation_document,
    parse_sse_fund_relation_document,
    parse_efunds_product_page,
    _constituent_cap,
    verify_official_sse_fund_index_relation,
    verify_etf_index_identity,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 24, 15, 30, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 24, 15, 31, tzinfo=SHANGHAI_TZ)
RELATION_FETCHED_AT = datetime(2026, 9, 2, 15, 31, tzinfo=SHANGHAI_TZ)
EVIDENCE_HASH = hashlib.sha256(b"official evidence").hexdigest()


class OfficialSseFundRelationDocumentTests(unittest.TestCase):
    def document_text(self, **overrides):
        values = {
            "cutoff": "2026 年 07 月 31 日",
            "index_name": "沪深 300 指数",
            "provider": "中证指数有限公司",
        }
        values.update(overrides)
        return (
            "本更新招募说明书所载内容截止日为"
            f"{values['cutoff']}，有关财务和业绩表现数据截止。\n"
            f"本基金的标的指数为{values['index_name']}。\n"
            f"该指数由{values['provider']}编制并发布。"
        )

    def announcement(self, **overrides):
        values = {
            "SECURITY_CODE": "510300",
            "SSEDATE": "2026-08-13",
            "TITLE": (
                "华泰柏瑞沪深300交易型开放式指数证券投资基金"
                "更新的招募说明书2026年第1号"
            ),
            "URL": (
                "/disclosure/fund/announcement/c/new/2026-08-13/"
                "510300_20260813_FPSL.pdf"
            ),
        }
        values.update(overrides)
        return values

    def test_parser_extracts_conservative_relation_evidence_date(self):
        parsed = parse_sse_fund_relation_document(
            self.document_text().encode("utf-8")
        )

        self.assertEqual(parsed.index_name, "沪深300指数")
        self.assertEqual(parsed.provider_name, "中证指数有限公司")
        self.assertEqual(parsed.content_cutoff_date.isoformat(), "2026-07-31")

    def test_parser_accepts_other_content_cutoff_with_layout_whitespace(self):
        parsed = parse_sse_fund_relation_document(
            (
                "除非另有说明，本更新招募说明书所载其余内容\n"
                "截止日为2025年 10月 31 日。\n"
                "本基金的标的指数为中证光伏产业指数。\n"
                "该指数由中证指数有限公司编制并发布。"
            ).encode("utf-8")
        )

        self.assertEqual(parsed.index_name, "中证光伏产业指数")
        self.assertEqual(parsed.provider_name, "中证指数有限公司")
        self.assertEqual(parsed.content_cutoff_date.isoformat(), "2025-10-31")

    def test_parser_accepts_manager_prospectus_multiple_cutoff_sentence(self):
        parsed = parse_sse_fund_relation_document(
            (
                "本招募说明书更新有关财务数据和净值表现数据"
                "截止日为2026年3月31日，主要人员情况截止日为"
                "2026年5月28日，其他所载内容截止日为2026年5月15日。\n"
                "本基金的标的指数为中证5G通信主题指数。\n"
                "该指数由中证指数有限公司编制并发布。"
            ).encode("utf-8")
        )

        self.assertEqual(parsed.index_name, "中证5G通信主题指数")
        self.assertEqual(parsed.provider_name, "中证指数有限公司")
        self.assertEqual(parsed.content_cutoff_date.isoformat(), "2026-05-15")

    def test_latest_exact_official_prospectus_becomes_versioned_document(self):
        result = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[
                self.announcement(SSEDATE="2025-03-22"),
                self.announcement(),
            ],
            document_content=self.document_text().encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.formal_ready)
        self.assertEqual(result.symbol, "510300")
        self.assertEqual(result.index_name, "沪深300指数")
        self.assertEqual(
            result.published_at,
            datetime(2026, 8, 13, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(
            result.evidence_valid_from,
            datetime(2026, 7, 31, tzinfo=SHANGHAI_TZ),
        )
        self.assertTrue(result.evidence_url.endswith("510300_20260813_FPSL.pdf"))
        self.assertEqual(len(result.evidence_sha256), 64)

    def test_symbol_mismatch_and_ambiguous_latest_rows_fail_closed(self):
        mismatch = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[self.announcement(SECURITY_CODE="510500")],
            document_content=self.document_text().encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )
        ambiguous = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[
                self.announcement(),
                self.announcement(URL="/another/510300_same_day.pdf"),
            ],
            document_content=self.document_text().encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )

        self.assertEqual(mismatch.status, IndexEvidenceStatus.CONFLICT)
        self.assertIn("fund_relation_symbol_mismatch", mismatch.reasons)
        self.assertEqual(ambiguous.status, IndexEvidenceStatus.CONFLICT)
        self.assertIn("fund_relation_announcement_ambiguous", ambiguous.reasons)

    def test_missing_cutoff_or_future_document_fails_closed(self):
        missing = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[self.announcement()],
            document_content=self.document_text(cutoff="未知").encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )
        future = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[self.announcement(SSEDATE="2026-08-14")],
            document_content=self.document_text(
                cutoff="2026 年 08 月 14 日"
            ).encode("utf-8"),
            fetched_at=datetime(2026, 8, 13, 15, 31, tzinfo=SHANGHAI_TZ),
        )

        self.assertFalse(missing.formal_ready)
        self.assertIn("fund_relation_evidence_date_missing", missing.reasons)
        self.assertFalse(future.formal_ready)
        self.assertIn("fund_relation_future_evidence", future.reasons)

    def test_exact_three_way_name_chain_builds_identity_evidence(self):
        document = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[self.announcement()],
            document_content=self.document_text().encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )
        provider = build_official_csindex_identity_resolution(
            requested_index_name="沪深300指数",
            search_payload={
                "code": "200",
                "success": True,
                "data": [{"indexCode": "000300", "indexName": "沪深300"}],
            },
            basic_payloads={
                "000300": {
                    "code": "200",
                    "data": {
                        "indexCode": "000300",
                        "indexFullNameCn": "沪深300指数",
                    },
                },
            },
            fetched_at=RELATION_FETCHED_AT,
        )

        result = verify_official_sse_fund_index_relation(
            document=document,
            official_master_target_index_name="沪深300指数",
            provider_resolution=provider,
            as_of=datetime(2026, 9, 2, 15, 30, tzinfo=SHANGHAI_TZ),
        )

        self.assertTrue(result.formal_ready)
        self.assertEqual(result.fund_index_code, "000300")
        self.assertEqual(result.effective_from, document.evidence_valid_from)

    def test_three_way_name_conflict_is_not_promoted(self):
        document = build_official_sse_fund_relation_document(
            symbol="510300",
            announcement_rows=[self.announcement()],
            document_content=self.document_text().encode("utf-8"),
            fetched_at=RELATION_FETCHED_AT,
        )
        provider = build_official_csindex_identity_resolution(
            requested_index_name="沪深300指数",
            search_payload={
                "code": "200",
                "success": True,
                "data": [{"indexCode": "000300", "indexName": "沪深300"}],
            },
            basic_payloads={
                "000300": {
                    "code": "200",
                    "data": {
                        "indexCode": "000300",
                        "indexFullNameCn": "沪深300指数",
                    },
                },
            },
            fetched_at=RELATION_FETCHED_AT,
        )

        result = verify_official_sse_fund_index_relation(
            document=document,
            official_master_target_index_name="中证500指数",
            provider_resolution=provider,
            as_of=datetime(2026, 9, 2, 15, 30, tzinfo=SHANGHAI_TZ),
        )

        self.assertFalse(result.formal_ready)
        self.assertIn("index_identity_conflict", result.reasons)

    def test_fetcher_uses_official_query_and_latest_pdf(self):
        outer = self

        class Response:
            def __init__(self, *, payload=None, content=b""):
                self._payload = payload
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "commonQuery.do" in url:
                    return Response(payload={"result": [outer.announcement()]})
                return Response(content=outer.document_text().encode("utf-8"))

        session = Session()
        result = fetch_official_sse_fund_relation_document(
            "510300",
            session=session,
            clock=lambda: RELATION_FETCHED_AT,
        )

        self.assertTrue(result.formal_ready)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(
            session.calls[0][1]["params"]["SECURITY_CODE"],
            "510300",
        )
        self.assertEqual(session.calls[0][1]["params"]["TITLE"], "招募说明书")

    def test_fetcher_classifies_query_failure(self):
        class Session:
            def get(self, *_args, **_kwargs):
                raise requests.ReadTimeout("stable")

        result = fetch_official_sse_fund_relation_document(
            "510300",
            session=Session(),
            clock=lambda: RELATION_FETCHED_AT,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.SOURCE_FAILED)
        self.assertFalse(result.formal_ready)
        self.assertIn("fund_relation_source_failed", result.reasons)


class EtfIndexIdentityEvidenceTests(unittest.TestCase):
    def relation(self, **overrides):
        values = {
            "symbol": "159915",
            "management_style": EtfManagementStyle.PASSIVE_INDEX,
            "fund_index_code": "399006",
            "fund_index_name": "创业板指数",
            "provider": "cnindex",
            "provider_index_code": "399006",
            "provider_index_name": "创业板指数",
            "published_at": datetime(
                2011,
                9,
                20,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2011,
                9,
                20,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "as_of": AS_OF,
            "fund_evidence_url": "https://fund.example/159915",
            "fund_evidence_sha256": EVIDENCE_HASH,
            "provider_evidence_url": "https://index.example/399006",
            "provider_evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
        }
        values.update(overrides)
        return verify_etf_index_identity(**values)

    def test_exact_code_and_name_can_be_formally_ready(self):
        result = self.relation()

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.identity_matched)
        self.assertTrue(result.formal_ready)
        self.assertEqual(result.reasons, ())

    def test_name_only_never_guesses_index_code(self):
        result = self.relation(fund_index_code=None)

        self.assertEqual(result.status, IndexEvidenceStatus.PENDING_EVIDENCE)
        self.assertFalse(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("index_identity_missing", result.reasons)

    def test_code_or_name_conflict_blocks_relation(self):
        result = self.relation(fund_index_code="000300")

        self.assertEqual(result.status, IndexEvidenceStatus.CONFLICT)
        self.assertFalse(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("index_identity_conflict", result.reasons)

    def test_active_etf_does_not_receive_a_fake_index_relation(self):
        result = self.relation(
            management_style=EtfManagementStyle.ACTIVE,
            fund_index_code=None,
            fund_index_name=None,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.PENDING_EVIDENCE)
        self.assertFalse(result.formal_ready)
        self.assertIn("active_etf_registry_only", result.reasons)

    def test_missing_relation_dates_preserves_verified_identity_only(self):
        result = self.relation(published_at=None, effective_from=None)

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("relation_published_at_missing", result.reasons)
        self.assertIn("relation_effective_from_missing", result.reasons)

    def test_efunds_page_parser_uses_structured_fields(self):
        html = b"""
        <table class="baseinfo-table"
               data-level1desc="\xe8\xa2\xab\xe5\x8a\xa8\xe8\x82\xa1\xe7\xa5\xa8\xe6\x8c\x87\xe6\x95\xb0">
          <tr><td>\xe6\xa0\x87\xe7\x9a\x84\xe6\x8c\x87\xe6\x95\xb0\xe5\x90\x8d\xe7\xa7\xb0\xef\xbc\x9a</td>
              <td>\xe5\x88\x9b\xe4\xb8\x9a\xe6\x9d\xbf\xe6\x8c\x87\xe6\x95\xb0</td></tr>
          <tr><td id="UNDERLYINGSECURITYID">399006</td></tr>
        </table>
        """

        parsed = parse_efunds_product_page(html)

        self.assertEqual(
            parsed.management_style,
            EtfManagementStyle.PASSIVE_INDEX,
        )
        self.assertEqual(parsed.index_name, "创业板指数")
        self.assertEqual(parsed.index_code, "399006")


class OfficialCsindexIdentityResolutionTests(unittest.TestCase):
    def search_payload(self, *items):
        return {
            "code": "200",
            "success": True,
            "data": list(items),
        }

    def basic_payload(self, code, full_name):
        return {
            "code": "200",
            "data": {
                "indexCode": code,
                "indexFullNameCn": full_name,
            },
        }

    def test_only_exact_official_full_name_is_resolved(self):
        result = build_official_csindex_identity_resolution(
            requested_index_name="沪深300指数",
            search_payload=self.search_payload(
                {
                    "indexCode": "000300",
                    "indexName": "沪深300",
                    "publishDate": "2005-04-08",
                    "indexClassify": "主题",
                },
                {
                    "indexCode": "H30075",
                    "indexName": "300期指",
                    "publishDate": "2013-04-03",
                },
            ),
            basic_payloads={
                "000300": self.basic_payload("000300", "沪深300指数"),
                "H30075": self.basic_payload(
                    "H30075",
                    "沪深300指数期货指数",
                ),
            },
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.identity_resolved)
        self.assertEqual(result.provider, "csindex")
        self.assertEqual(result.index_code, "000300")
        self.assertEqual(result.index_name, "沪深300指数")
        self.assertEqual(
            result.index_published_at,
            datetime(2005, 4, 8, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.index_classification, "主题")
        self.assertEqual(len(result.search_content_sha256), 64)
        self.assertEqual(len(result.identity_content_sha256 or ""), 64)

    def test_missing_and_duplicate_exact_names_fail_closed(self):
        missing = build_official_csindex_identity_resolution(
            requested_index_name="不存在指数",
            search_payload=self.search_payload({
                "indexCode": "000300",
                "indexName": "沪深300",
            }),
            basic_payloads={
                "000300": self.basic_payload("000300", "沪深300指数"),
            },
            fetched_at=FETCHED_AT,
        )
        conflict = build_official_csindex_identity_resolution(
            requested_index_name="重名指数",
            search_payload=self.search_payload(
                {"indexCode": "000001", "publishDate": "2020-01-01"},
                {"indexCode": "000002", "publishDate": "2021-01-01"},
            ),
            basic_payloads={
                "000001": self.basic_payload("000001", "重名指数"),
                "000002": self.basic_payload("000002", "重名指数"),
            },
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(missing.status, IndexEvidenceStatus.PENDING_EVIDENCE)
        self.assertFalse(missing.identity_resolved)
        self.assertIn("provider_index_identity_not_found", missing.reasons)
        self.assertEqual(conflict.status, IndexEvidenceStatus.CONFLICT)
        self.assertFalse(conflict.identity_resolved)
        self.assertIn("provider_index_identity_ambiguous", conflict.reasons)

    def test_fetcher_classifies_official_source_failure(self):
        class Session:
            def post(self, *_args, **_kwargs):
                raise requests.ReadTimeout("stable")

        result = fetch_official_csindex_identity_resolution(
            "沪深300指数",
            session=Session(),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.SOURCE_FAILED)
        self.assertFalse(result.identity_resolved)
        self.assertIn("provider_index_source_failed", result.reasons)

    def test_empty_name_and_naive_time_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "index_name_required"):
            build_official_csindex_identity_resolution(
                requested_index_name="  ",
                search_payload=self.search_payload(),
                basic_payloads={},
                fetched_at=FETCHED_AT,
            )
        with self.assertRaisesRegex(ValueError, "fetched_at_timezone_required"):
            build_official_csindex_identity_resolution(
                requested_index_name="沪深300指数",
                search_payload=self.search_payload(),
                basic_payloads={},
                fetched_at=FETCHED_AT.replace(tzinfo=None),
            )


class IndexVersionEvidenceTests(unittest.TestCase):
    def test_constituent_cap_accepts_official_top_ranked_securities_phrase(self):
        result = _constituent_cap(
            "按照过去一年日均总市值由高到低排名，"
            "选取排名前 50 的证券作为指数样本。"
        )

        self.assertEqual(result, 50)

    def test_official_file_request_retries_one_transient_timeout(self):
        from radar.sources.etf_index_evidence import _response_bytes

        class Response:
            content = b"official"

            def raise_for_status(self):
                return None

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.ReadTimeout("temporary")
                return Response()

        session = Session()

        result = _response_bytes(session, "https://official.example/file")

        self.assertEqual(result, b"official")
        self.assertEqual(session.calls, 2)


class OfficialCsindexMaterialCatalogTests(unittest.TestCase):
    def payload(self, **overrides):
        data = {
            "编制方案": [{
                "fileName": (
                    "20231208180438-931151_Index_Methodology_cn.pdf"
                ),
                "filePath": (
                    "https://oss-ch.csindex.com.cn/static/html/csindex/"
                    "public/uploads/indices/detail/files/zh_CN/"
                    "20231208180438-931151_Index_Methodology_cn.pdf"
                ),
                "fileType": "pdf",
            }],
            "样本列表": [{
                "fileName": "931151cons",
                "filePath": (
                    "https://oss-ch.csindex.com.cn/static/html/csindex/"
                    "public/uploads/file/autofile/cons/931151cons.xls"
                ),
                "fileType": "xls",
            }],
            "样本权重": [{
                "fileName": "931151closeweight",
                "filePath": (
                    "https://oss-ch.csindex.com.cn/static/html/csindex/"
                    "public/uploads/file/autofile/closeweight/"
                    "931151closeweight.xls?20260902134313"
                ),
                "fileType": "xls",
            }],
        }
        data.update(overrides)
        return {"code": "200", "success": True, "data": data}

    def test_dated_official_methodology_path_is_resolved_from_catalog(self):
        result = build_csindex_material_catalog(
            index_code="931151",
            payload=self.payload(),
            source_sha256=EVIDENCE_HASH,
            fetched_at=FETCHED_AT,
        )

        self.assertTrue(result.methodology_url.endswith(
            "20231208180438-931151_Index_Methodology_cn.pdf"
        ))
        self.assertEqual(
            result.methodology_published_at,
            datetime(2023, 12, 8, 18, 4, 38, tzinfo=SHANGHAI_TZ),
        )
        self.assertTrue(result.constituents_url.endswith("931151cons.xls"))
        self.assertTrue(result.close_weight_url.endswith("20260902134313"))

    def test_catalog_rejects_ambiguous_or_wrong_identity_material(self):
        duplicate_methodology = self.payload()
        duplicate_methodology["data"]["编制方案"].append(
            dict(duplicate_methodology["data"]["编制方案"][0])
        )
        wrong_index = self.payload(**{
            "编制方案": [{
                "fileName": "20231208180438-000300_Index_Methodology_cn.pdf",
                "filePath": (
                    "https://oss-ch.csindex.com.cn/static/html/csindex/"
                    "public/uploads/indices/detail/files/zh_CN/"
                    "20231208180438-000300_Index_Methodology_cn.pdf"
                ),
                "fileType": "pdf",
            }],
        })
        untrusted_domain = self.payload(**{
            "样本权重": [{
                "fileName": "931151closeweight",
                "filePath": "https://example.com/931151closeweight.xls",
                "fileType": "xls",
            }],
        })

        for payload in (duplicate_methodology, wrong_index, untrusted_domain):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    ValueError,
                    "csindex_material_catalog_unverified",
                ):
                    build_csindex_material_catalog(
                        index_code="931151",
                        payload=payload,
                        source_sha256=EVIDENCE_HASH,
                        fetched_at=FETCHED_AT,
                    )

    def test_chinese_numbered_methodology_sections_are_preserved(self):
        from radar.sources.etf_index_evidence import _section

        text = (
            "二、指数基日和基点\n"
            "以2012年12月31日为基日。\n"
            "三、样本选取方法\n"
            "同中证全指样本空间，按成交额和市值选取。\n"
            "四、指数计算\n"
            "按调整市值加权计算。\n"
            "五、指数样本和权重调整\n"
            "每半年调整一次。"
        )

        self.assertIn("样本选取方法", _section(text, 3, 4))
        self.assertIn("指数计算", _section(text, 4, 5))

    def test_top_level_selection_sections_ignore_table_of_contents(self):
        from radar.sources.etf_index_evidence import (
            _methodology_selection_parts,
            _section,
        )

        text = (
            "目录\n"
            "2、样本空间................1\n"
            "3、选样方法................1\n"
            "4、指数计算................2\n"
            "5、指数修正................5\n"
            "1、引言\n本指数反映市场整体表现。\n"
            "2、样本空间\n非ST、*ST沪深A股和红筹企业证券。\n"
            "3、选样方法\n按成交金额筛选后选取市值前300名。\n"
            "4、指数计算\n报告期指数按调整市值加权计算。\n"
            "5、指数修正\n采用除数修正。\n"
            "6、指数定期调样\n"
            "2、样本公司发生并购、合并、分立等情形时，"
            "按计算与维护细则处理。\n"
            "3、临时调整的其他情形从官方公告。"
        )

        universe, selection = _methodology_selection_parts(text)

        self.assertEqual(universe, "非ST、*ST沪深A股和红筹企业证券。")
        self.assertEqual(selection, "按成交金额筛选后选取市值前300名。")
        self.assertIn("报告期指数", _section(text, 4, 5))
        self.assertNotIn("................", _section(text, 4, 5))

    def test_csindex_poc_maps_sample_space_not_base_date_to_universe_rule(self):
        methodology_text = (
            "2023年12月 | 版本号 V1.2\n"
            "二、指数基日和基点\n以2012年12月31日为基日。\n"
            "三、样本选取方法\n"
            "1、样本空间\n同中证全指指数的样本空间。\n"
            "2、选样方法\n按成交额筛选后选取市值靠前证券。\n"
            "四、指数计算\n按调整市值加权计算。\n"
            "五、指数样本和权重调整\n每半年调整一次。"
        )
        basic = {
            "code": "200",
            "data": {
                "indexCode": "931151",
                "indexFullNameCn": "中证光伏产业指数",
                "indexCnDesc": "选取50只证券",
                "adjFreqCn": "每半年",
            },
        }

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class Session:
            def get(self, url, **_kwargs):
                if "index-basic-info" in url:
                    return Response(json.dumps(basic).encode())
                if "index-details-data" in url:
                    return Response(json.dumps(self_payload).encode())
                if url.endswith(".pdf"):
                    return Response(b"official methodology")
                return Response(b"official weights")

        self_payload = self.payload()
        constituent_frame = pd.DataFrame([{
            "日期Date": "2026-09-01",
            "成份券代码Constituent Code": "000001",
            "成份券名称Constituent Name": "证券一",
        }])
        weight_frame = constituent_frame.assign(**{"权重(%)weight": "100"})
        as_of = datetime(2026, 9, 2, 15, tzinfo=SHANGHAI_TZ)
        with patch(
            "radar.sources.etf_index_evidence._pdf_text",
            return_value=(methodology_text, 1),
        ), patch(
            "radar.sources.etf_index_evidence._read_excel",
            side_effect=(constituent_frame, weight_frame),
        ):
            result = fetch_csindex_index_poc(
                "931151",
                as_of,
                session=Session(),
                clock=lambda: as_of,
            )

        self.assertIn(
            "同中证全指指数的样本空间",
            result.methodology.universe_rule,
        )
        self.assertNotIn("基日", result.methodology.universe_rule)

    def test_csindex_current_observation_freezes_after_network_fetches(self):
        methodology_text = (
            "2023年12月 | 版本号 V1.2\n"
            "三、样本选取方法\n"
            "1、样本空间\n同中证全指指数的样本空间。\n"
            "2、选样方法\n按成交额筛选后选取市值靠前证券。\n"
            "四、指数计算\n按调整市值加权计算。\n"
            "五、指数样本和权重调整\n每半年调整一次。"
        )
        basic = {
            "code": "200",
            "data": {
                "indexCode": "931151",
                "indexFullNameCn": "中证光伏产业指数",
                "indexCnDesc": "选取50只证券",
                "adjFreqCn": "每半年",
            },
        }

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class Session:
            def get(self, url, **_kwargs):
                if "index-basic-info" in url:
                    return Response(json.dumps(basic).encode())
                if "index-details-data" in url:
                    return Response(json.dumps(self_payload).encode())
                if url.endswith(".pdf"):
                    return Response(b"official methodology")
                return Response(b"official weights")

        class Clock:
            def __init__(self, values):
                self.values = iter(values)

            def __call__(self):
                return next(self.values)

        self_payload = self.payload()
        constituent_frame = pd.DataFrame([{
            "日期Date": "2026-09-01",
            "成份券代码Constituent Code": "000001",
            "成份券名称Constituent Name": "证券一",
        }])
        weight_frame = constituent_frame.assign(**{"权重(%)weight": "100"})
        requested_as_of = datetime(2026, 9, 2, 15, tzinfo=SHANGHAI_TZ)
        catalog_fetched_at = requested_as_of + timedelta(seconds=1)
        completed_at = requested_as_of + timedelta(seconds=2)
        with patch(
            "radar.sources.etf_index_evidence._pdf_text",
            return_value=(methodology_text, 1),
        ), patch(
            "radar.sources.etf_index_evidence._read_excel",
            side_effect=(constituent_frame, weight_frame),
        ):
            result = fetch_csindex_index_poc(
                "931151",
                requested_as_of,
                session=Session(),
                clock=Clock([catalog_fetched_at, completed_at]),
            )

        self.assertEqual(result.methodology.as_of, completed_at)
        self.assertTrue(result.methodology.formal_ready)
        self.assertTrue(result.constituent_sets[-1].formal_ready)
        self.assertEqual(result.constituent_sets[-1].as_of, completed_at)

    def test_official_file_request_stops_after_bounded_retry(self):
        from radar.sources.etf_index_evidence import _response_bytes

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                raise requests.ReadTimeout("stable")

        session = Session()

        with self.assertRaises(requests.ReadTimeout):
            _response_bytes(session, "https://official.example/file")

        self.assertEqual(session.calls, 2)

    def methodology(self, **overrides):
        values = {
            "provider": "csindex",
            "index_code": "000300",
            "index_name": "沪深300指数",
            "provider_version": "2023-09",
            "published_at": datetime(
                2023,
                9,
                1,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2023,
                9,
                1,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "universe_rule": "沪深A股和符合条件的存托凭证",
            "selection_rule": "流动性筛选后选取总市值前300名",
            "weighting_method": "调整市值加权",
            "constituent_cap": 300,
            "rebalance_frequency": "semiannual",
            "as_of": AS_OF,
            "evidence_url": "https://index.example/methodology.pdf",
            "evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
        }
        values.update(overrides)
        return build_methodology_evidence(**values)

    def test_methodology_future_and_historical_versions_are_not_current(self):
        future = self.methodology(
            effective_from=datetime(
                2026,
                7,
                25,
                tzinfo=SHANGHAI_TZ,
            ),
        )
        expired = self.methodology(
            effective_to=datetime(
                2026,
                7,
                24,
                15,
                tzinfo=SHANGHAI_TZ,
            ),
        )

        self.assertFalse(future.formal_ready)
        self.assertIn("methodology_future_effective", future.reasons)
        self.assertFalse(expired.formal_ready)
        self.assertIn("methodology_historical_expired", expired.reasons)

    def test_methodology_missing_exact_dates_remains_pending(self):
        result = self.methodology(published_at=None, effective_from=None)

        self.assertFalse(result.formal_ready)
        self.assertIn("methodology_published_at_missing", result.reasons)
        self.assertIn("methodology_effective_from_missing", result.reasons)

    def test_current_official_methodology_is_forward_ready_without_backfill(self):
        result = self.methodology(
            effective_from=None,
            temporal_basis=EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION,
            first_observed_at=AS_OF,
            fetched_at=AS_OF,
        )

        self.assertTrue(result.formal_ready)
        self.assertEqual(result.reasons, ())
        self.assertIsNone(result.effective_from)
        self.assertEqual(
            result.temporal_basis,
            EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION,
        )

    def constituents(self, items, **overrides):
        values = {
            "provider": "csindex",
            "index_code": "000300",
            "index_name": "沪深300指数",
            "announced_at": datetime(
                2026,
                7,
                23,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2026,
                7,
                24,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "expected_count": len(items),
            "as_of": AS_OF,
            "evidence_url": "https://index.example/weights.xls",
            "evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
            "items": items,
        }
        values.update(overrides)
        return build_constituent_set_evidence(**values)

    def test_complete_weights_accept_true_zero(self):
        result = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 60.0},
            {"stockCode": "000002", "stockName": "乙", "weight": 40.0},
            {"stockCode": "000003", "stockName": "丙", "weight": 0.0},
        ])

        self.assertTrue(result.formal_ready)
        self.assertEqual(result.weight_count, 3)
        self.assertEqual(result.weight_total, 100.0)
        self.assertEqual(result.items[-1].weight, 0.0)

    def test_current_official_weights_are_forward_ready_without_backfill(self):
        result = self.constituents(
            [{"stockCode": "000001", "stockName": "甲", "weight": 100.0}],
            announced_at=None,
            effective_from=None,
            source_date=AS_OF.date(),
            temporal_basis=EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION,
            first_observed_at=AS_OF,
            fetched_at=AS_OF,
        )

        self.assertTrue(result.formal_ready)
        self.assertEqual(result.reasons, ())
        self.assertIsNone(result.announced_at)

    def test_duplicate_missing_weight_abnormal_total_and_empty_are_explicit(self):
        duplicate = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 50.0},
            {"stockCode": "000001", "stockName": "甲", "weight": 50.0},
        ])
        missing = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
            {"stockCode": "000002", "stockName": "乙", "weight": None},
        ])
        abnormal = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 80.0},
        ])
        empty = self.constituents([], expected_count=100)

        self.assertIn("constituent_duplicate", duplicate.reasons)
        self.assertIn("constituent_weight_missing", missing.reasons)
        self.assertIn("constituent_weight_total_abnormal", abnormal.reasons)
        self.assertIn("constituent_empty", empty.reasons)
        self.assertFalse(duplicate.formal_ready)
        self.assertFalse(missing.formal_ready)
        self.assertFalse(abnormal.formal_ready)
        self.assertFalse(empty.formal_ready)

    def test_future_and_expired_constituent_sets_are_not_current(self):
        items = [
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ]
        future = self.constituents(
            items,
            effective_from=datetime(
                2026,
                7,
                25,
                tzinfo=SHANGHAI_TZ,
            ),
        )
        expired = self.constituents(
            items,
            effective_to=datetime(
                2026,
                7,
                24,
                15,
                tzinfo=SHANGHAI_TZ,
            ),
        )

        self.assertIn("constituent_future_effective", future.reasons)
        self.assertIn("constituent_historical_expired", expired.reasons)
        self.assertFalse(future.formal_ready)
        self.assertFalse(expired.formal_ready)
