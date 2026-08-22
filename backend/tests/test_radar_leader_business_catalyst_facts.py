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
