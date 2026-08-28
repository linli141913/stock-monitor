import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_business_annual_report_selector import (
    select_latest_official_annual_report,
)
from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_document_facts import (
    extract_official_business_facts,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueueItem,
)
from radar.leader_runtime_candidate_plan import LeaderRuntimeCandidatePlanItem
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 21, 4, 0, tzinfo=UTC)
PUBLISHED_AT = AS_OF - timedelta(days=100)
FETCHED_AT = AS_OF - timedelta(minutes=10)


def plan_item(**changes):
    values = {
        "index": 0,
        "symbol": "000001",
        "as_of": AS_OF,
        "industry_code": "65",
        "industry_name": "软件和信息技术服务业",
        "industry_release_id": "industry-release-2026h1",
        "within_industry_rank": 1,
        "quote_source_contract_id": "quote-v1",
        "sector_source_contract_id": "sector-v1",
    }
    values.update(changes)
    return LeaderRuntimeCandidatePlanItem(**values)


def report_document():
    return OfficialBusinessMaterialDocument(
        document_id="cninfo:1234567890",
        document_version="cninfo:1234567890:1777500000000",
        symbol="000001",
        issuer_identity="cninfo-org:9900000001",
        title="2025年年度报告",
        published_at=PUBLISHED_AT,
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-04-30/1234567890.PDF"
        ),
    )


def selection():
    document = report_document()
    return select_latest_official_annual_report(
        plan_item(),
        LeaderBusinessMaterialReviewQueueItem(
            index=0,
            symbol="000001",
            industry_code="65",
            industry_release_id="industry-release-2026h1",
            status=LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW,
            documents=(document,),
        ),
    )


def content(*pages, **changes):
    values = {
        "status": AutomaticBusinessEvidenceStatus.READY,
        "document_id": "cninfo:1234567890",
        "document_version": "cninfo:1234567890:1777500000000",
        "symbol": "000001",
        "issuer_identity": "cninfo-org:9900000001",
        "document_kind": OfficialBusinessDocumentKind.ANNUAL_REPORT,
        "content_sha256": "a" * 64,
        "byte_count": 1000,
        "page_count": len(pages),
        "pages": tuple(
            OfficialBusinessDocumentPage(index, text)
            for index, text in pages
        ),
        "fetched_at": FETCHED_AT,
    }
    values.update(changes)
    return OfficialBusinessDocumentContentResult(**values)


class LeaderBusinessDocumentFactTests(unittest.TestCase):
    def test_explicit_main_business_section_builds_page_hashed_facts(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码 000001\n所属行业 软件和信息技术服务业"),
                (20, "主营业务\n主要产品包括工业软件、云平台。"),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件", "云平台"))
        self.assertEqual(result.fragments[0].page_number, 20)
        self.assertRegex(result.fragments[0].fragment_sha256, r"^[0-9a-f]{64}$")
        self.assertNotIn("工业软件", repr(result))
        self.assertFalse(result.formal_usable)

    def test_official_metals_activity_keeps_atomic_business_object(self):
        result = extract_official_business_facts(
            plan_item(
                industry_code="32",
                industry_name="有色金属矿采选业",
            ),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司主要从事铜、铅、锌、金、银等多种有色金属、"
                    "贵金属的采选、冶炼和加工。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertIn("贵金属", result.business_terms)

    def test_directory_only_business_hit_is_not_evidence(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (2, "目录\n第三节 主营业务........20\n证券代码000001\n软件和信息技术服务业"),
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.reasons, ("business_fact_section_missing",))

    def test_industry_conflict_and_content_identity_drift_fail_closed(self):
        wrong_industry = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001 所属行业批发业"),
                (20, "主营业务：主要产品包括工业软件。"),
            ),
        )
        drifted = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001 软件和信息技术服务业"),
                (20, "主营业务：主要产品包括工业软件。"),
                document_version="cninfo:1234567890:changed",
            ),
        )

        self.assertEqual(
            wrong_industry.reasons,
            ("business_fact_industry_unverified",),
        )
        self.assertEqual(
            drifted.reasons,
            ("business_fact_identity_unverified",),
        )

    def test_inline_industry_phrase_is_not_a_declared_industry(self):
        result = extract_official_business_facts(
            plan_item(
                industry_code="70",
                industry_name="房地产业",
            ),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "1、房地产经纪服务\n"
                    "二、报告期内公司所处行业情况"
                ),
                (
                    89,
                    "评价管理层对资产组的认定，以及宏观经济和所属行业"
                    "的发展趋势；复核评估机构出具的报告。"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("房地产经纪服务",))

    def test_versioned_plan_industry_need_not_be_repeated_in_annual_report(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (20, "主营业务：主要产品包括工业软件。"),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)

    def test_structured_business_section_subheadings_are_official_terms(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的业务情况\n"
                    "(一)工业软件业务\n公司开展相关研发与销售。\n"
                    "(二)云平台产品\n提供企业服务。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件", "云平台"))

    def test_exchange_heading_with_suo_enters_primary_business_section(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    15,
                    "一、报告期内公司所从事的主要业务、经营模式、"
                    "行业情况说明\n"
                    "(一)主要业务、主要产品或服务情况\n"
                    "1.主要业务的情况\n"
                    "(1)操作系统业务\n公司提供相关产品和服务。\n"
                    "新增重要非主营业务情况\n"
                    "(二)主要经营模式\n"
                    "1.行业解决方案\n公司介绍采购流程。"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("操作系统",))

    def test_real_main_business_heading_and_numbered_items_are_extracted(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    12,
                    "一、报告期内公司从事的主要业务\n"
                    "报告期内，公司以森林经营和板材家居为主业。\n"
                    "1、森林经营：公司开展良种繁育和造林营林。\n"
                    "2、板材家居领域：公司提供全屋定制产品。\n"
                    "4.67\n75.42\n69.00%433,6\n"
                    "二、报告期内公司所处行业情况\n"
                    "1、林业行业发展情况。"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("森林经营", "板材家居领域"),
        )
        self.assertTrue(all(
            fragment.page_number == 12 for fragment in result.fragments
        ))

    def test_existing_named_business_in_primary_section_is_official_term(self):
        result = extract_official_business_facts(
            plan_item(
                industry_code="70",
                industry_name="房地产业",
            ),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    10,
                    "一、报告期内公司从事的主要业务\n"
                    "1、产城空间开发\n"
                    "公司空间开发板块致力于住宅、写字楼、产业园区"
                    "等不同业态的开发。基于现有房地产开发业务，公司将通过"
                    "存量优化提升与增量开发建设并进。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertIn("房地产开发", result.business_terms)

    def test_existing_business_phrase_rejects_generic_objects(self):
        for phrase in (
            "基于现有相关业务，公司稳步经营。",
            "基于现有公司业务，管理层提升效率。",
            "基于现有业务，公司将优化布局。",
        ):
            with self.subTest(phrase=phrase):
                result = extract_official_business_facts(
                    plan_item(),
                    selection(),
                    content(
                        (8, "证券代码000001"),
                        (20,
                            "一、报告期内公司从事的主要业务\n"
                            f"{phrase}\n"
                            "二、报告期内公司所处行业情况"
                        ),
                    ),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_pdf_spacing_inside_official_business_subheading_is_normalized(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "（一）海 洋 养 殖 业 务\n"
                    "公司开展相关生产与销售。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("海洋养殖",))

    def test_numbered_items_after_industry_boundary_are_not_business_facts(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司经营保持稳定。\n"
                    "二、报告期内公司所处行业情况\n"
                    "1、工业软件：行业发展迅速。"
                ),
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.reasons, ("business_fact_section_missing",))

    def test_explicit_company_main_activity_sentence_is_official_evidence(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的业务情况\n"
                    "公司主要从事远洋渔业捕捞、水产品加工销售及相关贸易，"
                    "围绕核心主业形成完整产业链。\n"
                    "报告期内公司新增重要非主营业务的说明"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("远洋渔业捕捞", "水产品加工销售"),
        )

    def test_explicit_primary_business_clause_extracts_listed_products(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司构建起以水稻、玉米、小麦等主粮作物，黄瓜、辣椒、"
                    "谷子、食葵等专精特新作物为主业，农业服务为配套。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            (
                "水稻",
                "玉米",
                "小麦等主粮作物",
                "黄瓜",
                "辣椒",
                "谷子",
                "食葵等专精特新作物",
            ),
        )

    def test_operating_mode_headings_are_not_business_objects(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "（一）主要业务\n"
                    "1、公司的主要服务\n"
                    "2、销售和结算模式\n"
                    "3、主要业绩驱动因素\n"
                    "4、公司主要产品及用途\n"
                    "5、采购模式\n"
                    "6、生产模式\n"
                    "二、核心竞争力分析"
                ),
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_core_competence_heading_ends_business_object_extraction(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "（一）工业软件业务\n"
                    "公司开展研发与销售。\n"
                    "二、核心竞争力分析\n"
                    "1、收入与成本\n"
                    "2、投资状况分析"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件",))

    def test_multi_page_business_overview_keeps_explicit_product_lists(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司开展养殖与销售。"
                ),
                (
                    21,
                    "2、公司主要产品及用途\n"
                    "主要产品为商品代肉鸡苗、商品代鸡苗除自用外，"
                    "主要销售给养殖客户。\n"
                    "主要产品包括钨矿、锡矿。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("商品代肉鸡苗", "商品代鸡苗", "钨矿", "锡矿"),
        )

    def test_explicit_main_business_includes_named_businesses(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "报告期内，公司的主营业务包括乳业和信息业务，"
                    "主要产品及经营模式未发生变化。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("乳业", "信息"))

    def test_numbered_named_product_description_is_business_evidence(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "2、盐酸贝尼地平片（注册商标：元治®）为国内首仿，"
                    "用于原发性高血压。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("盐酸贝尼地平片",))

    def test_company_product_output_sentence_keeps_both_named_products(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司产品以炼焦精煤为主，占公司商品煤产量50%以上。\n"
                    "二、报告期内公司所处行业情况"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("炼焦精煤", "商品煤"))

    def test_named_product_patterns_do_not_cross_business_section_boundary(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司经营保持稳定。\n"
                    "二、报告期内公司所处行业情况\n"
                    "行业主要产品包括钨矿、锡矿。\n"
                    "2、盐酸贝尼地平片（注册商标）为行业常见产品。\n"
                    "行业产品以炼焦精煤为主，占全国商品煤产量较高。"
                ),
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_company_business_list_after_industry_heading_remains_evidence(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司经营保持稳定。\n"
                    "二、报告期内公司所处行业情况\n"
                    "（一）公司主要业务、产品及应用领域\n"
                    "报告期内，公司从事的主要业务包括钨精矿、"
                    "仲钨酸铵、钨粉及碳化钨粉、硬质合金。"
                ),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("钨精矿", "仲钨酸铵", "钨粉", "碳化钨粉", "硬质合金"),
        )

    def test_peer_company_business_list_after_industry_heading_is_not_evidence(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001"),
                (
                    20,
                    "一、报告期内公司从事的主要业务\n"
                    "公司经营保持稳定。\n"
                    "二、报告期内公司所处行业情况\n"
                    "同行业公司从事的主要业务包括钨精矿、"
                    "仲钨酸铵和硬质合金。"
                ),
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_duplicate_terms_are_deduped_and_generic_terms_are_rejected(self):
        result = extract_official_business_facts(
            plan_item(),
            selection(),
            content(
                (8, "证券代码000001 软件和信息技术服务业"),
                (20, "主营业务：主要产品包括工业软件、产品、工业软件。"),
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件",))


if __name__ == "__main__":
    unittest.main()
