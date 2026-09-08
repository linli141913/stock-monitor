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
    def test_official_3d_printing_equipment_sales_growth_extracts_exact_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "公司持续推进3D打印应用场景的拓展，"
                "不断挖掘3D打印多方位应用的可能性，"
                "推动下游产业化应用突破，3D打印设备销售量"
                "较上年同期增加，赋能经营业绩稳步增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("3D打印设备",))

    def test_official_named_business_pressure_extracts_exact_objects(self):
        cases = (
            (
                "公司出版业务以大众图书出版为主，"
                "受市场整体疲软、行业竞争加剧等因素影响，"
                "对营收、利润形成较大冲击，经营承压明显。",
                ("出版",),
            ),
            (
                "2026年上半年，天然橡胶下游需求走弱，"
                "公司橡胶产品销量及售价不及预期。",
                ("橡胶",),
            ),
            (
                "报告期内，由于影视业务的生产制作和发行周期"
                "导致公司收入确认存在一定的季节性波动等原因，"
                "公司上半年影视业务确认收入较少。",
                ("影视",),
            ),
        )

        for text, expected in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(
                        event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    ),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.READY,
                )
                self.assertEqual(result.business_terms, expected)

    def test_named_business_pressure_patterns_fail_closed(self):
        cases = (
            "公司预计出版业务以大众图书出版为主，"
            "对营收、利润形成较大冲击，经营承压明显。",
            "天然橡胶下游需求走弱，行业橡胶产品销量及售价不及预期。",
            "由于影视业务的生产制作和发行周期，"
            "公司上半年收入较少。",
            "公司出版业务以大众图书出版为主，"
            "对营收、利润形成较大冲击，经营承压明显。"
            "公司随后否认上述说法。",
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

    def test_forecast_or_generic_3d_printing_metric_is_not_confirmed(self):
        cases = (
            "公司预计3D打印设备销售量较上年同期增加。",
            "公司计划不断挖掘3D打印多方位应用的可能性，3D打印设备销售量较上年同期增加。",
            "公司预计不断挖掘3D打印多方位应用的可能性，3D打印设备销售量较上年同期增加。",
            "不断挖掘3D打印多方位应用的可能性，3D打印设备销售量较上年同期增加，上述说法不实。",
            "不断挖掘3D打印多方位应用的可能性，3D打印设备销售量较上年同期增加，以上仅为预测。",
            "设备销售量较上年同期增加。",
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

    def test_company_named_industry_field_revenue_extracts_exact_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明。公司在智慧交通行业领域取得了"
                "显著成效，整体收入相较于2024年有所提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("智慧交通",))

    def test_confirmed_named_product_causal_revenue_extracts_exact_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、本期业绩预盈的主要原因。"
                "报告期内，公司业绩预盈的主要原因如下：一是公司"
                "持续迭代全线互联网产品，"
                "通过功能升级与精细化运营，不断提升用户体验，"
                "驱动互联网产品业务营收增长，毛利水平同步提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("互联网",))

    def test_named_product_causal_revenue_pattern_fails_closed(self):
        cases = (
            "公司计划持续迭代全线互联网产品，驱动互联网产品业务"
            "营收增长，毛利水平同步提升。",
            "公司持续迭代全线互联网产品，驱动数字安全业务营收增长，"
            "毛利水平同步提升。",
            "公司持续迭代全线相关产品，驱动相关产品业务营收增长，"
            "毛利水平同步提升。",
            "公司持续迭代全线互联网产品，提升用户体验。"
            "互联网产品业务营收增长，毛利水平同步提升。",
            "公司持续迭代全线互联网产品，驱动互联网产品业务"
            "营收增长，毛利水平同步提升。公司随后否认上述说法。",
            "其中，非学历培训业务预计净利润为31000万元至45000万元。",
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

    def test_confirmed_real_estate_settlement_metrics_extract_exact_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "2三、业绩变动原因说明报告期内，公司房地产开发业务"
                "结转的收入虽较上年同期上升，但受结转收入的房地产项目"
                "毛利率降低的影响，公司整体营业毛利率同比下降。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("房地产开发",))

    def test_real_estate_settlement_pattern_fails_closed(self):
        cases = (
            "公司预计报告期内房地产开发业务结转收入上升，"
            "房地产项目毛利率下降。",
            "报告期内，行业房地产开发业务结转的收入虽上升，"
            "但项目毛利率降低。",
            "报告期内，公司房地产开发业务结转的收入虽较上年"
            "同期上升，但公司未披露项目毛利率变化。",
            "报告期内，公司房地产开发业务结转的收入虽较上年"
            "同期上升，但受结转收入的房地产项目毛利率降低的影响，"
            "公司整体营业毛利率同比下降。公司随后否认上述说法。",
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

    def test_confirmed_brokerage_transaction_impact_extracts_exact_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "此外，2025年公司在业务所在的核心城市市场占有率继续"
                "保持稳定，但由于二手房价格出现了一定程度的下降，"
                "对公司经纪业务的交易金额和佣金收入也产生了一定的负面影响。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("经纪",))

    def test_brokerage_transaction_impact_pattern_fails_closed(self):
        cases = (
            "公司预计，但由于二手房价格出现了一定程度的下降，"
            "对公司经纪业务的交易金额和佣金收入也产生了一定的"
            "负面影响。",
            "但由于二手房价格出现了一定程度的下降，对行业经纪业务"
            "的交易金额和佣金收入产生负面影响。",
            "但由于二手房价格出现了一定程度的下降，"
            "公司经纪业务继续开展。",
            "但由于二手房价格出现了一定程度的下降，"
            "对公司经纪业务的交易金额和佣金收入也产生了一定的"
            "负面影响。公司随后否认上述说法。",
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

    def test_named_industry_field_revenue_requires_company_actual_outcome(self):
        cases = (
            "智慧交通行业领域整体收入相较于2024年有所提升。",
            "公司所在智慧交通行业领域整体收入相较于2024年有所提升。",
            "公司在智慧交通行业领域取得了显著成效。",
            "公司在智慧交通行业领域取得了显著成效，行业整体收入提升。",
            "公司在智慧交通行业领域取得了显著成效，整体收入预计提升。",
            "预计公司在智慧交通行业领域取得显著成效，整体收入相较于"
            "2024年有所提升。",
            "公司在相关行业领域取得了显著成效，整体收入相较于2024年"
            "有所提升。",
            "公司在智慧交通行业领域取得了显著成效，整体收入相较于"
            "2024年有所提升。公司随后否认上述说法。",
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

    def test_official_metals_price_reason_extracts_atomic_objects(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、本期业绩变化的主要原因2026年半年度有色金属及"
                "贵金属产品市场价格同比上升，公司毛利率同比上升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("有色金属", "贵金属"))

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

    def test_causal_named_sales_margin_extracts_only_the_sold_object(self):
        cases = (
            (
                "三、本期业绩预亏主要原因1、2025年受国内国际大环境影响,"
                "棉纺织市场下游需求趋淡,部分纺企订单不足,"
                "叠加今年美国关税战的影响,纺企采购原料棉花偏谨慎,"
                "限制棉花需求,导致棉花市场需求不足,价格呈下跌趋势,"
                "年底新棉上市后价格有所回升,致使本年度销售皮棉毛利率"
                "比上年有大幅提升,但依然偏低,影响本期利润。",
                "皮棉",
            ),
            (
                "一、本期业绩预计情况：预计净利润为负值。"
                "三、业绩变动原因说明。"
                "报告期内，公司部分客户原计划建设项目开工率不足，"
                "致使沥青需求延期供货；同时原材料采购成本"
                "受国际原油价格波动影响，"
                "导致沥青销售业务毛利率同比下滑。",
                "沥青",
            ),
        )

        for text, expected_term in cases:
            with self.subTest(expected_term=expected_term):
                result = extract_official_business_catalyst_facts(
                    document(
                        event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    ),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.READY,
                )
                self.assertEqual(result.business_terms, (expected_term,))

    def test_registered_named_product_income_extracts_exact_product_name(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、本期业绩变化的主要原因。"
                "报告期内，中国首款四价流脑结合疫苗"
                "曼海欣®收入保持持续增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("曼海欣®",))

    def test_fixed_single_ticket_express_revenue_extracts_express_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "一、本期业绩预计情况：预计净利润同比增长。"
                "三、业绩变动原因说明。在此背景下，公司积极调整"
                "经营策略，优化货品结构，提高运营效率，保障末端权益，"
                "报告期内公司单票快递服务收入2.33元，同比较大幅度上升，"
                "整体带动公司实现归属于上市公司股东扣除非经常性"
                "损益后的净利润同比增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("快递",))

    def test_fixed_single_ticket_express_revenue_pattern_fails_closed(self):
        cases = (
            "自2025年8月起，快递行业价格得到理性回升。",
            "报告期内快递行业单票服务收入2.33元，同比较大幅度上升。",
            "报告期内单票快递服务收入2.33元，同比较大幅度上升。",
            "报告期内公司单票服务收入2.33元，同比较大幅度上升。",
            "报告期内公司单票快递物流服务收入2.33元，同比较大幅度上升。",
            "报告期内公司单票快递服务成本2.33元，同比较大幅度上升。",
            "报告期内公司单票快递服务收入预计2.33元，"
            "同比较大幅度上升。",
            "预计报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。",
            "报告期内公司单票快递服务收入2.34元，同比较大幅度上升。",
            "报告期内公司单票快递服务收入2.33元，同比上升。",
            "报告期内公司单票快递服务收入2.33元。同比较大幅度上升。",
            "公司否认：报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升仅为预测。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。上述说法不实。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。对此，公司予以否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该消息已被公司否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司已撤回上述表述。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。以上仅为推测。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司对此予以否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司予以否认该消息。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司撤回上述表述。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。对此消息，公司表示不实。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司否认了上述说法。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司明确否认上述说法。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司表示上述消息不属实。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司撤回了上述表述。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司已正式撤回上述表述。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司对此说法予以否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。对此，公司回应称该消息不属实。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该消息只是未经证实的市场推测。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该说法属于预测。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该内容只是推测。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该说法仍然成立，但该表述随后"
            "被公司否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该说法并未被公司否认，随后却被撤回。",
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

    def test_unrelated_followup_denial_does_not_retract_express_revenue(self):
        cases = (
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司已否认其他市场传闻，"
            "上述经营事实不受影响。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。公司随后否认外界猜测，"
            "该表述与本项收入无关。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。上述经营事实仍然有效且公司"
            "否认其他市场传闻。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。该说法并未被公司否认。",
            "报告期内公司单票快递服务收入2.33元，"
            "同比较大幅度上升。上述说法仍然成立，公司否认"
            "其他市场传闻。",
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
                    AutomaticBusinessEvidenceStatus.READY,
                )
                self.assertEqual(result.business_terms, ("快递",))

    def test_named_footwear_sales_pressure_extracts_footwear_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、本期业绩变化的主要原因。报告期内，受行业竞争持续加剧、"
                "市场有效需求疲软等因素影响，公司主营的皮鞋业务销售面临压力，"
                "整体收入未达预期。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("皮鞋",))

    def test_named_catering_product_sales_growth_extracts_catering_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "餐饮业务表现亮眼，年宵品、端午粽销售均实现大幅增长，"
                "烘焙产业稳步落地。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("餐饮",))

    def test_named_field_vehicle_cost_pressure_extracts_field_vehicle_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "除此之外，在非美国市场，场地电动车的市场需求较为分散且产品"
                "以定制化为主，定制化业务对人员、研发及项目管理要求较高，"
                "人工及管理成本上升，从而也一定程度影响了公司盈利能力。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("场地电动车",))

    def test_named_operating_fact_patterns_fail_closed(self):
        cases = (
            "行业主营的皮鞋业务销售面临压力，整体收入未达预期。",
            "公司主营的皮鞋业务销售预计面临压力，整体收入未达预期。",
            "公司主营的皮鞋业务销售未面临压力，整体收入未达预期。",
            "公司主营的皮鞋业务销售面临压力，整体收入达到预期。",
            "公司主营的皮鞋业务销售面临压力。整体收入未达预期。",
            "公司主营的皮鞋业务销售面临压力，整体收入未达预期。上述说法不实。",
            "餐饮行业表现亮眼，年宵品、端午粽销售均实现大幅增长。",
            "餐饮业务表现亮眼，产品销售均实现大幅增长。",
            "餐饮业务表现亮眼，年宵品销售实现大幅增长。",
            "餐饮业务表现亮眼，年宵品、端午粽销售预计实现大幅增长。",
            "餐饮业务表现亮眼，年宵品、端午粽销售未实现大幅增长。",
            "餐饮业务表现亮眼，年宵品、端午粽销售均实现大幅增长。以上仅为推测。",
            "在非美国市场，电动车的市场需求较为分散且产品以定制化为主，"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本上升，"
            "从而也一定程度影响了公司盈利能力。",
            "在非美国市场，场地电动车的市场需求预计较为分散且产品以定制化为主，"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本上升，"
            "从而也一定程度影响了公司盈利能力。",
            "在非美国市场，场地电动车的市场需求较为分散且产品以定制化为主。"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本上升，"
            "从而也一定程度影响了公司盈利能力。",
            "在非美国市场，场地电动车的市场需求较为分散且产品以定制化为主，"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本下降，"
            "从而也一定程度影响了公司盈利能力。",
            "在非美国市场，场地电动车的市场需求较为分散且产品以定制化为主，"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本上升，"
            "但并未影响公司盈利能力。",
            "在非美国市场，场地电动车的市场需求较为分散且产品以定制化为主，"
            "定制化业务对人员、研发及项目管理要求较高，人工及管理成本上升，"
            "从而也一定程度影响了公司盈利能力。公司撤回上述表述。",
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

    def test_new_named_margin_and_registered_product_patterns_fail_closed(self):
        cases = (
            "导致原材料销售业务毛利率同比下滑。",
            "导致公司整体毛利率下降。",
            "致使本年度销售皮棉毛利率预计提升。",
            "导致沥青销售业务毛利率预计下滑。",
            "致使本年度销售皮棉毛利率比上年提升或将达到10个百分点。",
            "导致沥青销售业务毛利率同比下滑的说法不实。",
            "导致本期销售部分产品毛利率同比提升。",
            "导致部分销售业务毛利率同比下滑。",
            "致使本期销售各类产品毛利率同比提升。",
            "导致各类销售业务毛利率同比下滑。",
            "导致本期销售系列产品毛利率同比提升。",
            "导致系列销售业务毛利率同比下滑。",
            "导致本期销售多款产品毛利率同比提升。",
            "导致多款销售业务毛利率同比下滑。",
            "没有证据表明该因素导致沥青销售业务毛利率同比下滑。",
            "尚无充分证据支持该因素导致沥青销售业务毛利率同比下滑。",
            "公司否认相关因素导致沥青销售业务毛利率同比下滑。",
            "公司否定相关因素导致沥青销售业务毛利率同比下滑。",
            "公司没有依据认定相关因素导致沥青销售业务毛利率同比下滑。",
            "公司未发现任何充分可靠且可复核的材料能够证明相关因素"
            "导致沥青销售业务毛利率同比下滑。",
            "导致本期销售全部产品毛利率同比提升。",
            "导致全部销售业务毛利率同比下滑。",
            "导致本期销售这类产品毛利率同比提升。",
            "导致这类销售业务毛利率同比下滑。",
            "导致本期销售同类产品毛利率同比提升。",
            "导致同类销售业务毛利率同比下滑。",
            "导致本期销售各款产品毛利率同比提升。",
            "导致各款销售业务毛利率同比下滑。",
            "导致本期销售众多产品毛利率同比提升。",
            "导致众多销售业务毛利率同比下滑。",
            "疫苗收入保持持续增长。",
            "预计疫苗曼海欣®收入保持持续增长。",
            "疫苗曼海欣®收入预计持续增长。",
            "某疫苗产品收入增长。",
            "产品虚构牌®收入持续增长。",
            "疫苗研发中的曼海欣®收入持续增长。",
            "中国首款四价流脑结合疫苗曼海欣®收入保持持续增长"
            "的预期尚待验证。",
            "中国首款四价流脑结合疫苗曼海欣®收入保持持续增长"
            "并不属实。",
            "致使本年度销售皮棉毛利率比上年提升仅为预测。",
            "导致沥青销售业务毛利率同比下滑系预测结果。",
            "中国首款四价流脑结合疫苗曼海欣®收入保持持续增长"
            "为预测值。",
            "以下为预测内容：致使本年度销售皮棉毛利率比上年提升。",
            "公司仅作推测：导致沥青销售业务毛利率同比下滑。",
            "公司计划降低原材料采购成本，"
            "导致沥青销售业务毛利率同比下滑。",
            "假设原计划建设项目开工率不足，且原材料采购成本"
            "受价格波动影响，导致沥青销售业务毛利率同比下滑。",
            "致使本年度销售皮棉毛利率比上年提升。上述说法不实。",
            "导致沥青销售业务毛利率同比下滑。以上仅为预测。",
            "中国首款四价流脑结合疫苗曼海欣®收入保持持续增长。"
            "公司随后否认该说法。",
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

    def test_named_products_with_realized_shipment_share_are_extracted(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明。报告期内，锂电铜箔和"
                "电子电路铜箔高附加值产品的出货占比均显著提升。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("锂电铜箔", "电子电路铜箔"))

    def test_named_product_demand_causally_driving_revenue_is_extracted(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明。报告期内，公司新能源电源、"
                "其他电源产品市场需求较好，带动公司整体营业收入实现同比增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("新能源电源",))

    def test_named_product_new_patterns_keep_future_or_generic_claims_unverified(self):
        cases = (
            "报告期内，公司计划提升锂电铜箔和电子电路铜箔高附加值产品的出货占比。",
            "报告期内，公司主要产品市场需求较好，带动公司整体营业收入同比增长。",
            "报告期内，公司新能源电源、其他电源产品市场需求较好，预计带动营业收入增长。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
                    content(text),
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

    def test_named_industry_subsegment_realized_revenue_decline_is_extracted(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明。公司酒店主业经营持续承压，"
                "酒店业客房板块收入同比下降。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("酒店业",))

    def test_named_industry_subsegment_future_claim_is_unverified(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content("公司预计酒店业客房板块收入同比下降。"),
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.business_terms, ())

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

    def test_named_product_output_with_business_revenue_share_extracts_product(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "转型,已全面切换到含锌铟固危废资源化利用这一高潜力赛道,"
                "2025年该业务占公司营收比重已达约95%,产精铟超过200吨,"
                "公司整体营业收入预计再增长。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("精铟",))

    def test_named_product_output_allows_long_earnings_reason_heading_prefix(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "三、业绩变动原因说明自2024年初新管理团队履职以来,"
                "公司坚定实施战略转型,已全面切换到含锌铟固危废资源化利用"
                "这一高潜力赛道,2025年该业务占公司营收比重已达约95%,"
                "产精铟超过200吨(其中自产精铟约121吨)。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("精铟",))

    def test_unrelated_preceding_negative_text_does_not_pollute_reason_heading(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
            content(
                "二、与会计师事务所沟通情况不存在分歧。"
                "三、业绩变动原因说明自2024年以来,"
                "2025年该业务占公司营收比重已达约95%,产精铟超过200吨。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("精铟",))

    def test_named_product_output_requires_confirmed_same_segment_context(self):
        cases = (
            "该业务占公司营收比重已达约95%,预计产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,有望产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,可能产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,或将产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,将产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,预期产精铟超过200吨。",
            "产精铟超过200吨,但该业务占公司营收比重未达要求。",
            "产精铟超过200吨,该业务占公司营收比重已达约95%。",
            "该业务占公司营收比重已达约95%,日产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,产量精铟超过200吨。",
            "该业务占公司营收比重已达约95%,产产品超过200吨。",
            "该业务占公司营收比重已达约95%,产精铟超过200吨,但不代表公司业务。",
            "该业务占公司营收比重已达约95%。产精铟超过200吨。",
            "该业务占公司营收比重已达约95%,产含锌铟固危废资源化利用超过200吨。",
        )

        for text in cases:
            with self.subTest(text=text):
                result = extract_official_business_catalyst_facts(
                    document(event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST),
                    content(text),
                )

                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(result.business_terms, ())

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

    def test_named_construction_notice_and_contract_extracts_construction_object(self):
        result = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "北京市大龙伟业房地产开发股份有限公司关于建筑施工项目收到"
                "中标通知书并签订合同的公告。重要内容提示：《建设工程施工合同》，"
                "合同金额为人民币762,685,875.42元。风险提示：如遇政策、市场、"
                "环境等不可预计因素，可能会导致合同无法如期履行。"
                "2026年1月15日，大龙顺发收到《中标通知书》，被确认为该项目中标人。"
            ),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.business_terms, ("建筑施工",))

    def test_named_construction_notice_and_contract_requires_final_same_page_confirmation(self):
        cases = (
            "关于建筑施工项目预中标公示的提示性公告。"
            "大龙顺发为该项目的第一中标候选人。",
            "关于建筑施工项目收到中标通知书并拟签订合同的公告。"
            "大龙顺发收到《中标通知书》，被确认为该项目中标人。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。"
            "大龙顺发拟收到《中标通知书》，被确认为该项目中标人。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。"
            "大龙顺发未收到《中标通知书》，被确认为该项目中标人。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。"
            "大龙顺发收到《中标通知书》，被确认为该项目中标候选人。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。"
            "大龙顺发能否获得《中标通知书》尚存在不确定性。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。"
            "另一公司收到《中标通知书》，被确认为该项目中标人。",
            "关于施工项目收到中标通知书并签订合同的公告。"
            "大龙顺发收到《中标通知书》，被确认为该项目中标人。",
            "关于建筑施工项目收到中标通知书并签订合同的公告。",
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

        split_pages = extract_official_business_catalyst_facts(
            document(event_kind=OfficialBusinessCatalystKind.PROJECT_AWARD),
            content(
                "关于建筑施工项目收到中标通知书并签订合同的公告。",
                "大龙顺发收到《中标通知书》，被确认为该项目中标人。",
            ),
        )
        self.assertEqual(
            split_pages.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(split_pages.business_terms, ())

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
