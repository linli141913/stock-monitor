import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_catalyst_facts import (
    extract_official_business_catalyst_facts,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystDocument,
    OfficialBusinessCatalystKind,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)


UTC = timezone.utc
PUBLISHED_AT = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
FETCHED_AT = PUBLISHED_AT + timedelta(hours=1)


def document(**changes):
    values = {
        "document_id": "cninfo:8301",
        "document_version": "cninfo:8301:1777500000000",
        "symbol": "000001",
        "issuer_identity": "cninfo-org:9900000001",
        "title": "关于签订重大合同的公告",
        "published_at": PUBLISHED_AT,
        "source_url": (
            "https://static.cninfo.com.cn/finalpage/2026-08-20/8301.PDF"
        ),
        "event_kind": OfficialBusinessCatalystKind.MAJOR_CONTRACT,
    }
    values.update(changes)
    return OfficialBusinessCatalystDocument(**values)


def content(*page_texts, **changes):
    pages = tuple(
        OfficialBusinessDocumentPage(index, text)
        for index, text in enumerate(page_texts, start=1)
    )
    values = {
        "status": AutomaticBusinessEvidenceStatus.READY,
        "document_id": "cninfo:8301",
        "document_version": "cninfo:8301:1777500000000",
        "symbol": "000001",
        "issuer_identity": "cninfo-org:9900000001",
        "document_kind": OfficialBusinessDocumentKind.CATALYST,
        "content_sha256": "b" * 64,
        "byte_count": 1200,
        "page_count": len(pages),
        "pages": pages,
        "fetched_at": FETCHED_AT,
    }
    values.update(changes)
    return OfficialBusinessDocumentContentResult(**values)


class LeaderBusinessCatalystFactTests(unittest.TestCase):
    def test_contract_fact_extracts_event_and_explicit_business_object(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content("公司签订工业软件项目合同，合同金额2亿元。"),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件",))
        self.assertEqual(
            result.event_kind,
            OfficialBusinessCatalystKind.MAJOR_CONTRACT,
        )
        self.assertEqual(result.fragments[0].page_number, 1)
        self.assertRegex(result.fragments[0].fragment_sha256, r"^[0-9a-f]{64}$")
        self.assertFalse(result.negative_event)
        self.assertNotIn("工业软件", repr(result))

    def test_title_only_or_no_business_object_remains_unverified(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content("重大合同公告。公司经营情况正常。"),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_catalyst_fact_object_missing",),
        )

    def test_termination_with_same_business_object_is_negative_evidence(self):
        result = extract_official_business_catalyst_facts(
            document(title="关于终止重大合同的公告"),
            content("公司终止工业软件项目合同，双方不再履行。"),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("工业软件",))
        self.assertTrue(result.negative_event)

    def test_earnings_product_metrics_extract_only_explicit_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明。"
                "公司主要产品商品代鸡苗及鸡肉产品的市场销售价格承压；"
                "水产加工业务收入同比提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("商品代鸡苗", "鸡肉", "水产加工"),
        )

    def test_earnings_metric_objects_strip_structural_prefixes(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "报告期内，生猪销售价格同比下降；"
                "公司转基因玉米种销量较上年增长；"
                "主要受益于无人化智能装备业务增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("生猪", "转基因玉米种", "无人化智能装备"),
        )

    def test_earnings_product_level_income_and_margin_metrics_extract_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "报告期内，本期火腿及肉制品营业收入有所下降；"
                "生物质纤维素长丝及氨纶纤维销量增加，"
                "氨纶纤维毛利水平提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("生物质纤维素长丝", "氨纶纤维", "火腿", "肉制品"),
        )

    def test_enumerated_period_product_metric_extracts_only_named_product(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content("一是2026年上半年自捕鱼销售均价同比上涨。"),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("自捕鱼",))
        self.assertEqual(result.fragments[0].page_number, 1)
        self.assertFalse(result.formal_usable)

    def test_enumerated_company_or_market_metrics_are_not_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "一是2026年上半年公司营业收入增加；"
                "二是市场销售均价上涨。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_named_business_recovery_is_an_explicit_operating_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "进料加工业务有序恢复，"
                "水产加工业务收入同比提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("进料加工", "水产加工"),
        )

    def test_generic_business_recovery_is_not_an_explicit_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content("公司业务有序恢复，流动性逐步改善。"),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_generic_recovery_and_input_cost_increases_are_not_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "经营业务恢复，国内业务逐步恢复，传统业务全面恢复；"
                "核心业务恢复；"
                "一是2026年上半年原材料采购销售价格上涨；"
                "二是2026年上半年能源销售价格上涨；"
                "三是2026年上半年运输销售均价上涨。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_named_livestock_profit_decline_extracts_farming_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "生猪价格持续处于低位，导致公司报告期内"
                "生猪养殖利润下降。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("生猪养殖",))

    def test_named_segment_revenue_growth_extracts_segment_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content("新品销售推动乳业板块营业收入实现同比增长。"),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("乳业",))

    def test_named_commodity_price_cause_extracts_commodity_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "因钨矿市场价格下降导致当期营业收入及盈利水平下降。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("钨矿",))

    def test_new_causal_patterns_reject_generic_financial_phrases(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "导致公司利润下降；导致公司报告期内项目养殖利润下降；"
                "推动公司营业收入增长；推动行业营业收入增长；"
                "因市场价格下降导致当期营业收入下降；"
                "因原材料价格上涨导致当期营业收入下降。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_quoted_project_before_epc_signature_is_explicit_contract_object(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content(
                "公司就“上海晶纾风力发电有限公司驭风行动50MW"
                "分散式风电项目”签署EPC承包合同。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            (
                "上海晶纾风力发电有限公司驭风行动50MW分散式风电项目",
            ),
        )

    def test_project_award_with_signed_quoted_epc_object_is_explicit(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "公司及合作方组成的联合体与代县风和新能源有限公司"
                "就“上海晶纾风力发电有限公司驭风行动50MW"
                "分散式风电项目”签署EPC承包合同。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            (
                "上海晶纾风力发电有限公司驭风行动50MW分散式风电项目",
            ),
        )

    def test_unsigned_or_unquoted_epc_text_is_not_a_contract_object(self):
        for event_kind in (
            OfficialBusinessCatalystKind.MAJOR_CONTRACT,
            OfficialBusinessCatalystKind.PROJECT_AWARD,
        ):
            with self.subTest(event_kind=event_kind):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=event_kind),
                    content(
                        "公司拟就“50MW分散式风电项目”开展沟通；"
                        "公司拟与甲方就“50MW分散式风电项目”"
                        "签署EPC承包合同；"
                        "公司计划与甲方就“风电建设项目”"
                        "签署EPC承包合同；"
                        "公司拟与甲方达成意向，就“风电建设项目”"
                        "签署EPC承包合同；"
                        "公司拟与甲方(证券代码605289.SH)"
                        "就“风电建设项目”签署EPC承包合同；"
                        "50MW分散式风电项目签署EPC承包合同；"
                        "公司就“EPC工程总承包项目”签署EPC承包合同；"
                        "公司就“EPC项目”签署EPC承包合同；"
                        "公司就“工程项目”签署EPC承包合同；"
                        "公司就“EPC总包项目”签署EPC承包合同；"
                        "公司就“工程总包项目”签署EPC承包合同；"
                        "公司就“EPC施工总承包项目”签署EPC承包合同；"
                        "公司就“机电工程总承包项目”签署EPC承包合同；"
                        "公司就“已建成50MW风电项目”签署承包合同。"
                    ),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_company_level_income_and_margin_metrics_are_not_business_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content("报告期内，公司营业收入增长，整体毛利率提升。"),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_generic_earnings_metrics_do_not_become_business_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "主营业务收入同比下降，归属于上市公司股东的净利润增长。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_causal_metric_fragments_are_not_business_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "因湖北众兴基地项目满产，产量同比增加；"
                "致使报告期销量同比下降。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_contract_execution_boilerplate_is_not_a_business_object(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content(
                "本制度所称日常经营重大合同是指公司合同；"
                "公司收到中标通知书后将与招标人洽谈签订上述合同。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_quoted_contract_and_award_service_are_explicit_objects(self):
        contract = extract_official_business_catalyst_facts(
            document(),
            content("公司续签了《苹果独立维修提供商协议》。"),
        )
        award = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "公司成功中标阿尔及利亚国家石油公司"
                "第3、7标段油气服务项目。"
            ),
        )

        self.assertEqual(contract.business_terms, ("苹果独立维修提供商",))
        self.assertEqual(award.business_terms, ("油气服务",))

    def test_document_content_identity_drift_is_rejected(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content(
                "公司签订工业软件项目合同。",
                content_sha256="not-a-sha",
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_catalyst_fact_identity_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
