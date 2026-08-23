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

    def test_forecast_header_does_not_invalidate_later_confirmed_metrics(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "本期业绩预计情况:预计净利润同比增长,"
                "三、业绩变动原因说明,本期火腿及肉制品营业收入有所下降;"
                "公司金针菇产品销售价格同比增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("金针菇", "火腿", "肉制品"))

    def test_forecasted_profit_can_be_explained_by_confirmed_product_metrics(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "报告期内,公司预计净利润同比上涨,主要是"
                "生物质纤维素长丝及氨纶纤维销量增加的同时"
                "氨纶纤维毛利水平提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("生物质纤维素长丝", "氨纶纤维"),
        )

    def test_distant_forecast_marker_still_rejects_forecasted_business_metric(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "公司预计受下游需求持续改善影响,"
                "本期珠宝黄金业务营业收入增长。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_reason_prefix_still_extracts_named_business_metrics(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明业绩变动原因主要是本期"
                "珠宝黄金业务营业收入增长、毛利增长等原因所致。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("珠宝黄金",))

    def test_reason_prefix_rejects_generic_business_metrics(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "主要是本期公司业务营业收入增长;"
                "主要是本期主营业务毛利增长;"
                "主要是本期新业务收入增长;"
                "主要是本期相关业务利润增长;"
                "主要是本期珠宝黄金业务营业收入预计增长。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_named_product_average_extracts_only_the_explicit_product(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "报告期内,钨精矿均价同比上涨,"
                "公司钨精矿及粉末产品盈利显著增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("钨精矿",))

    def test_generic_or_input_average_is_not_a_business_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "公司均价上涨;市场均价上涨;产品均价上涨;"
                "原材料均价上涨;能源均价回落;"
                "报告期内,钨精矿均价同比上涨,公司整体盈利增长;"
                "报告期内,钨精矿均价预计上涨,"
                "公司钨精矿产品盈利增长。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_named_product_delivery_growth_extracts_the_product(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "报告期内,受益于无人化智能装备产品交付量的"
                "同比大幅增加,公司营业收入同比增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("无人化智能装备",))

    def test_forecast_header_does_not_invalidate_confirmed_delivery_growth(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "一、本期业绩预计情况:预计净利润为正值且同比上升,"
                "三、业绩变动原因说明报告期内,受益于无人化智能装备"
                "产品交付量的同比大幅增加,公司营业收入同比增长,"
                "公司净利润同比上升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("无人化智能装备",))

    def test_generic_product_delivery_growth_is_not_a_business_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "受益于公司产品交付量增加;"
                "受益于行业产品交付量增加;"
                "受益于相关产品交付量增加;"
                "受益于产品交付量增加;"
                "受益于无人化智能装备产品交付量同比增加;"
                "报告期内,受益于无人化智能装备产品"
                "交付量的同比增加,公司产量同比增长;"
                "报告期内,受益于无人化智能装备产品"
                "交付量预计增加,公司营业收入同比增长。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_unconfirmed_or_cross_sentence_earnings_context_is_rejected(self):
        cases = (
            "公司预计主要是本期珠宝黄金业务营业收入增长。",
            "公司不主要是本期珠宝黄金业务营业收入增长。",
            "预计,报告期内,钨精矿均价同比上涨,"
            "公司钨精矿产品盈利增长。",
            "预计,报告期内,受益于无人化智能装备产品"
            "交付量的同比增加,公司营业收入同比增长。",
            "主要是本期珠宝黄金业务营业收入。已实现增长。",
            "报告期内,钨精矿均价同比。上涨,"
            "公司钨精矿产品盈利增长。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(
                        event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    ),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

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

    def test_confirmed_quoted_project_award_extracts_only_named_project(self):
        cases = (
            (
                "公司被确定为“乐平市农产品智慧仓储和物流设施建设"
                "项目勘察、设计、采购、施工总承包”的中标单位。",
                "乐平市农产品智慧仓储和物流设施建设"
                "项目勘察、设计、采购、施工总承包",
            ),
            (
                "招标人确认公司为“江西省鹰潭市人民医院病房改造项目”"
                "的中标供应商。",
                "江西省鹰潭市人民医院病房改造项目",
            ),
            (
                "确定江西磻溪建设工程有限公司(牵头方)、"
                "中外建工程设计与顾问有限公司、浙江省围海建设集团"
                "股份有限公司、江西金浔有色工程技术有限公司组成的"
                "联合体为“乐平市农产品智慧仓储和物流设施建设项目"
                "勘察、设计、采购、施工总承包”的中标单位。",
                "乐平市农产品智慧仓储和物流设施建设"
                "项目勘察、设计、采购、施工总承包",
            ),
        )

        for text, expected_term in cases:
            with self.subTest(expected_term=expected_term):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.READY,
                )
                self.assertEqual(result.business_terms, (expected_term,))
                self.assertEqual(result.fragments[0].page_number, 1)

    def test_unconfirmed_or_prospective_quoted_award_is_not_an_object(self):
        cases = (
            "公司为“智慧仓储项目”的中标单位。",
            "公司预中标智慧仓储项目。",
            "公司中标候选智慧仓储项目。",
            "公司拟被确定为“智慧仓储项目”的中标单位。",
            "公司被确定为“智慧仓储项目”的预中标单位。",
            "公司被确定为智慧仓储项目的中标单位。",
            "公司被确定为“EPC工程总承包项目”的中标单位。",
            "公司未被确定为“智慧仓储项目”的中标单位。",
            "招标人尚未确认公司为“智慧仓储项目”的中标供应商。",
            "公司被确定为“智慧仓储项目”的中标单位候选人。",
            "公司被确定为“智慧仓储项目”的中标单位(候选人)。",
            "董事会确定公司拟由甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "招标人是否确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "招标人尚待确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "经评审,初步确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "招标人暂时确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "招标人临时确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "招标人预先确定甲、乙组成的联合体为"
            "“智慧仓储项目”的中标单位。",
            "公司被确定为“智慧仓储项目”的中标单位,"
            "尚待最终定标。",
            "公司中标智慧仓储项目(候选人)。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_explicit_unsuccessful_award_remains_negative_evidence(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content("公司未中标智慧仓储项目。"),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("智慧仓储",))
        self.assertTrue(result.negative_event)

    def test_prospective_plain_contract_is_not_an_explicit_object(self):
        result = extract_official_business_catalyst_facts(
            document(),
            content(
                "公司拟签订工业软件项目合同。"
                "公司计划签署智能仓储项目合同。"
                "公司有意向续签智慧物流项目合同。"
                "公司预计签订数字医疗项目合同。"
                "公司拟签订《工业软件项目合同》。"
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

    def test_unexecuted_plain_contract_is_not_an_explicit_object(self):
        cases = (
            "公司未签订工业软件项目合同。",
            "公司不签署《智能仓储项目合同》。",
            "公司无法就“数字医疗项目”签署EPC承包合同。",
            "公司不能续签智慧物流项目合同。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

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

    def test_strict_named_business_results_extract_full_official_objects(self):
        cases = (
            (
                "2025年年度业绩增长主要得益于以下方面:"
                "(一)国外玉米业务多措并举,营业收入实现增长,"
                "利润大幅减亏。",
                ("国外玉米",),
            ),
            (
                "公司珠宝业务在2025年度实现了显著业绩增长。",
                ("珠宝",),
            ),
            (
                "公司节能业务板块巩固拓展,经营收入稳定增长。",
                ("节能",),
            ),
            (
                "公司心血管线核心产品盐酸贝尼地平片"
                "(注册商标:元治®)2026年进入第十一批国家药品"
                "集中带量采购执标期,受集采政策影响,"
                "该产品销售单价大幅下调、销量同步下滑,"
                "对公司营业收入及经营利润形成较大冲击。",
                ("盐酸贝尼地平片",),
            ),
        )

        for text, expected_terms in cases:
            with self.subTest(expected_terms=expected_terms):
                result = extract_official_business_catalyst_facts(
                    document(
                        event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    ),
                    content(text),
                )

                self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
                self.assertEqual(result.business_terms, expected_terms)

    def test_uncertain_generic_or_joint_business_results_remain_unverified(self):
        cases = (
            "国外玉米业务拟多措并举,营业收入实现增长,"
            "利润大幅减亏。",
            "公司珠宝业务预计在2025年度实现显著业绩增长。",
            "公司节能业务板块计划巩固拓展,经营收入稳定增长。",
            "公司业务板块巩固拓展,经营收入稳定增长。",
            "公司心血管线产品盐酸贝尼地平片受集采政策影响,"
            "该产品销售单价大幅下调、销量同步下滑,"
            "对公司营业收入及经营利润形成较大冲击。",
            "公司心血管线核心产品盐酸贝尼地平片受集采政策影响。"
            "该产品销售单价大幅下调、销量同步下滑,"
            "对公司营业收入及经营利润形成较大冲击。",
            "公司心血管线核心产品盐酸贝尼地平片受集采政策影响,"
            "该产品销售单价预计下调、销量同步下滑,"
            "对公司营业收入及经营利润形成较大冲击。",
            "报告期公司大力拓展主营业务相关衍生产品的销售,"
            "包括生物医药业务相关的美妆产品和保健品销售、"
            "环保业务相关的产品销售等,带动了收入和利润的增长。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(
                        event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    ),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_confirmed_notice_award_extracts_the_full_quoted_project(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "公司全资子公司中国汽车工业工程有限公司收到《中标通知书》,"
                "确认中汽工程中标《涪陵高新区新能源汽车轻量化零部件厂房及"
                "智能产线项目(一期)工程总承包》。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.business_terms,
            ("涪陵高新区新能源汽车轻量化零部件厂房及智能产线项目(一期)工程总承包",),
        )

    def test_incomplete_or_uncertain_notice_award_remains_unverified(self):
        cases = (
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》。",
            "公司收到《中标通知书》,中汽工程中标《涪陵高新区智能产线项目工程总承包》。",
            "公司收到《中标通知书》,"
            "中汽工程中标涪陵高新区智能产线项目工程总承包。",
            "公司收到《中标通知书》,确认中标《涪陵高新区智能产线项目工程总承包》。",
            "公司收到《中标通知书》,"
            "确认中汽工程中标涪陵高新区智能产线项目工程总承包。",
            "公司拟收到《中标通知书》,"
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》。",
            "公司未收到《中标通知书》,"
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》。",
            "公司收到《中标通知书》。"
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》。",
            "公司收到《中标通知书》,"
            "确认中汽工程中标《EPC工程总承包项目》。",
            "公司收到《中标通知书》,"
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》,"
            "尚待最终定标。",
            "公司收到《中标通知书》,"
            "确认中汽工程中标《涪陵高新区智能产线项目工程总承包》,"
            "中标候选人公示。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_unquoted_union_notice_award_extracts_full_project_name(self):
        cases = (
            "联合体收到招标人发来的中标通知书："
            "市政工程公司与中机国际组成的联合体被确定为"
            "临港开发区供排水提质增效一体化工程(排水达标区建设项目)"
            "EPC工程总承包(以下简称“项目”)中标人。",
            "市政工程公司与中机国际组成的联合体被确定为"
            "临港开发区供排水提质增效一体化工程(排水达标区建设项目)"
            "EPC工程总承包(以下简称“项目”)中标人。",
        )

        expected = (
            "临港开发区供排水提质增效一体化工程(排水达标区建设项目)"
            "EPC工程总承包",
        )
        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
                    content(text),
                )

                self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
                self.assertEqual(result.business_terms, expected)
                self.assertFalse(result.negative_event)

    def test_unquoted_union_award_requires_same_sentence_confirmed_subject(self):
        cases = (
            "联合体拟被确定为临港开发区供排水提质增效一体化工程"
            "(排水达标区建设项目)EPC工程总承包中标人。",
            "联合体为临港开发区供排水提质增效一体化工程"
            "(排水达标区建设项目)EPC工程总承包中标人。",
            "收到中标通知书。联合体被确定为临港开发区供排水提质增效"
            "一体化工程(排水达标区建设项目)EPC工程总承包中标人。",
            "联合体被确定为EPC工程总承包项目中标人。",
            "联合体被确定为智慧城市建设项目中标人。",
            "联合体被确定为重点工程建设项目EPC工程总承包中标人。",
            "联合体被确定为重大项目建设项目EPC工程总承包中标人。",
            "联合体被确定为临港开发区供排水提质增效一体化工程"
            "(排水达标区建设项目)EPC工程总承包中标候选人。",
            "联合体被确定为临港开发区供排水提质增效一体化工程"
            "(排水达标区建设项目)EPC工程总承包中标人，"
            "另一项目合同终止。",
            "联合体被确定为临港开发区供排水提质增效一体化工程"
            "(排水达标区建设项目)EPC工程总承包中标人，"
            "但该项目不再履行。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_unquoted_union_award_rejects_later_page_conflict(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "市政工程公司与中机国际组成的联合体被确定为"
                "临港开发区供排水提质增效一体化工程(排水达标区建设项目)"
                "EPC工程总承包(以下简称“项目”)中标人。",
                "另一项目合同终止。",
            ),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

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
