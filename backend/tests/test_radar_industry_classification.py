import hashlib
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordProvenance,
    IndustryRecordStatus,
    SecurityMasterRecord,
    VerifiedSecurityAlias,
)
from radar.sources.industry_classification import (
    FetchedResource,
    IndustryClassificationProviders,
    OfficialIndustrySupplementRecord,
    fetch_industry_classification,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 21, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 21, 10, 0, 2, tzinfo=SHANGHAI_TZ)
PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)
DOCUMENT_URL = (
    "https://sp.capco.org.cn:82/file/202604/hangyefenlei/2025xiaban/"
    "2025xiabangupiaodaima.pdf"
)


def source_record(
    symbol="000001",
    name="平安银行",
    category_code="J",
    category_name="金融业",
    subclass_code=None,
    subclass_name=None,
    division_code="66",
    division_name="货币金融服务",
):
    subclass_code_text = subclass_code or ""
    subclass_name_text = subclass_name or ""
    return (
        f"{symbol}{name:<9}{category_code:<4}{category_name:<19}"
        f"{subclass_code_text:<5}{subclass_name_text:<20}"
        f"{division_code:<4}{division_name}"
    )


def fixture_layout(*rows):
    return "\n".join((
        "2025年下半年上市公司行业分类结果",
        "上市公司  上市公司  门类代码  门类简称  次类代码  次类简称  大类代码  大类简称",
        "代码    简称",
        *rows,
    ))


def fixture_html(
    published_date="2026-04-03",
    document_url=DOCUMENT_URL,
    title="2025年下半年上市公司行业分类结果",
):
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>发布时间：{published_date}</p>"
        f'<a href="{document_url}">{title}（按股票代码排序）</a>'
        "</body></html>"
    ).encode("utf-8")


def master(
    symbol,
    name="平安银行",
    listing_date=date(1991, 4, 3),
    *,
    exchange=None,
    source_industry=None,
    source_report_date=None,
):
    return SecurityMasterRecord(
        symbol=symbol,
        name=name,
        exchange=(
            exchange
            or ("szse" if symbol.startswith(("0", "3")) else "bse")
        ),
        board="A股",
        listingDate=listing_date,
        sourceIndustry=source_industry,
        sourceReportDate=source_report_date,
        source="official_exchange_security_master",
        fetchedAt=FETCHED_AT,
        sourceFields={
            "所属行业": source_industry,
            "报告日期": (
                source_report_date.isoformat()
                if source_report_date is not None
                else None
            ),
        },
    )


def providers_for(
    *rows,
    html=None,
    document=b"%PDF-fixture-v1",
    page_final_url=PAGE_URL,
    document_final_url=DOCUMENT_URL,
    page_error=None,
    document_error=None,
    supplement_loader=None,
):
    def fetch_page(_url, _timeout):
        if page_error:
            raise page_error
        return FetchedResource(
            final_url=page_final_url,
            content=html if html is not None else fixture_html(),
        )

    def fetch_document(_url, _timeout):
        if document_error:
            raise document_error
        return FetchedResource(
            final_url=document_final_url,
            content=document,
        )

    return IndustryClassificationProviders(
        fetch_page=fetch_page,
        fetch_document=fetch_document,
        extract_layout_pages=lambda _content: [fixture_layout(*rows)],
        fetch_current_supplements=supplement_loader,
    )


class IndustryClassificationContractTests(unittest.TestCase):
    def test_zero_counts_do_not_turn_missing_coverage_into_zero(self):
        completeness = IndustryClassificationCompleteness(
            sourceRecordCount=0,
            uniqueSourceSymbolCount=0,
            currentMasterCount=0,
            mappedCount=0,
            unconfirmedCount=0,
            excludedSourceCount=0,
            mappingCoverage=None,
            requiredFieldCoverage={},
            shadowUsable=False,
            formalUsable=False,
            reasons=("source_returned_no_rows",),
        )

        self.assertEqual(completeness.source_record_count, 0)
        self.assertIsNone(completeness.mapping_coverage)


class IndustryClassificationSourceTests(unittest.TestCase):
    def fetch(self, providers, current_master=None, **kwargs):
        return fetch_industry_classification(
            radar_run_id="run-industry-1",
            batch_id="industry-1",
            as_of=AS_OF,
            publication_page_url=PAGE_URL,
            current_security_master=current_master or [master("000001")],
            providers=providers,
            clock=lambda: FETCHED_AT,
            **kwargs,
        )

    def test_exact_identity_and_manufacturing_subclass_are_preserved(self):
        result = self.fetch(
            providers_for(
                source_record(),
                source_record(
                    symbol="000008",
                    name="神州高铁",
                    category_code="C",
                    category_name="制造业",
                    subclass_code="CG",
                    subclass_name="专用、通用及交通运输设备",
                    division_code="37",
                    division_name="铁路、船舶、航空航天和其他运输设备制造业",
                ),
            ),
            current_master=[master("000001"), master("000008", "神州高铁")],
        )

        self.assertEqual(result.completeness.source_record_count, 2)
        self.assertEqual(result.completeness.mapped_count, 2)
        self.assertEqual(result.completeness.mapping_coverage, 1.0)
        self.assertTrue(result.completeness.shadow_usable)
        self.assertFalse(result.completeness.formal_usable)
        by_symbol = {record.source_symbol: record for record in result.records}
        self.assertEqual(
            by_symbol["000001"].identity_status,
            IndustryIdentityStatus.EXACT,
        )
        self.assertEqual(
            by_symbol["000008"].manufacturing_subclass_code,
            "CG",
        )
        self.assertIsNone(by_symbol["000008"].middle_class_code)
        self.assertEqual(
            by_symbol["000008"].record_status,
            IndustryRecordStatus.ACCEPTED,
        )

    def test_duplicate_source_symbol_fails_the_batch(self):
        result = self.fetch(providers_for(source_record(), source_record()))

        self.assertEqual(result.status.value, "failed")
        self.assertIn(
            "duplicate_source_symbol",
            {issue.code for issue in result.issues},
        )

    def test_missing_required_field_fails_the_batch(self):
        malformed = "000001平安银行     J         金融业"

        result = self.fetch(providers_for(malformed))

        self.assertEqual(result.status.value, "failed")
        self.assertIn(
            "missing_required_field",
            {issue.code for issue in result.issues},
        )

    def test_classification_code_name_conflict_fails_the_batch(self):
        result = self.fetch(
            providers_for(
                source_record(),
                source_record(
                    symbol="000002",
                    name="万科A",
                    category_name="错误金融名称",
                    division_code="70",
                    division_name="房地产业",
                ),
            ),
            current_master=[master("000001"), master("000002", "万科A")],
        )

        self.assertEqual(result.status.value, "failed")
        self.assertIn(
            "classification_name_conflict",
            {issue.code for issue in result.issues},
        )

    def test_non_manufacturing_subclass_fails_the_batch(self):
        invalid = source_record(
            subclass_code="JA",
            subclass_name="错误次类",
        )

        result = self.fetch(providers_for(invalid))

        self.assertEqual(result.status.value, "failed")
        self.assertIn(
            "unexpected_manufacturing_subclass",
            {issue.code for issue in result.issues},
        )

    def test_known_release_hash_change_is_not_silently_accepted(self):
        document = b"%PDF-fixture-v2"
        result = self.fetch(
            providers_for(source_record(), document=document),
            known_document_hashes={"2025H2": "0" * 64},
        )

        self.assertEqual(result.status.value, "failed")
        self.assertIn(
            "document_hash_changed",
            {issue.code for issue in result.issues},
        )
        self.assertNotEqual(hashlib.sha256(document).hexdigest(), "0" * 64)

    def test_future_publication_is_rejected(self):
        html = fixture_html(published_date="2026-07-22")

        result = self.fetch(providers_for(source_record(), html=html))

        self.assertEqual(result.status.value, "failed")
        self.assertIn("future_publication", {issue.code for issue in result.issues})

    def test_historical_first_observation_is_marked_retrospective(self):
        result = self.fetch(providers_for(source_record()))

        self.assertEqual(
            result.release.history_status,
            IndustryHistoryStatus.RETROSPECTIVE_UNVERIFIED,
        )
        self.assertEqual(result.release.knowledge_effective_from, FETCHED_AT)

    def test_official_archive_mode_uses_published_date_without_faking_observation(self):
        result = self.fetch(
            providers_for(source_record()),
            verify_official_archive=True,
        )

        self.assertEqual(
            result.release.history_status,
            IndustryHistoryStatus.OFFICIAL_ARCHIVE_VERIFIED,
        )
        self.assertEqual(result.release.first_observed_at, FETCHED_AT)
        self.assertEqual(
            result.release.knowledge_effective_from,
            datetime(2026, 4, 3, tzinfo=SHANGHAI_TZ),
        )

    def test_unverified_bse_alias_stays_unresolved_without_name_matching(self):
        old_bse_row = source_record(
            symbol="830001",
            name="同名公司",
            category_code="C",
            category_name="制造业",
            subclass_code="CG",
            subclass_name="专用、通用及交通运输设备",
            division_code="35",
            division_name="专用设备制造业",
        )

        result = self.fetch(
            providers_for(old_bse_row),
            current_master=[master("920001", "同名公司")],
        )

        record = result.records[0]
        self.assertEqual(record.identity_status, IndustryIdentityStatus.UNRESOLVED)
        self.assertEqual(record.record_status, IndustryRecordStatus.UNCONFIRMED)
        self.assertIsNone(record.security_identity)
        self.assertIn("unverified_security_alias", record.issue_codes)
        self.assertEqual(result.completeness.mapped_count, 0)

    def test_verified_bse_alias_requires_versioned_official_evidence(self):
        old_bse_row = source_record(
            symbol="830001",
            name="北交公司",
            category_code="C",
            category_name="制造业",
            subclass_code="CG",
            subclass_name="专用、通用及交通运输设备",
            division_code="35",
            division_name="专用设备制造业",
        )
        alias = VerifiedSecurityAlias(
            sourceSymbol="830001",
            securityIdentity="920001",
            publishedDate=date(2025, 1, 1),
            effectiveFrom=datetime(2025, 1, 1, tzinfo=SHANGHAI_TZ),
            effectiveTo=None,
            evidenceUrl="https://www.bse.cn/service/code_mapping.html",
        )

        result = self.fetch(
            providers_for(old_bse_row),
            current_master=[master("920001", "北交公司")],
            verified_aliases=[alias],
        )

        self.assertEqual(
            result.records[0].identity_status,
            IndustryIdentityStatus.VERIFIED_ALIAS,
        )
        self.assertEqual(result.records[0].security_identity, "920001")
        self.assertEqual(result.completeness.mapped_count, 1)

    def test_b_share_is_explicitly_excluded_from_current_a_share_mapping(self):
        b_share = source_record(symbol="200001", name="深市B股")

        result = self.fetch(providers_for(b_share))

        record = result.records[0]
        self.assertIn("b_share_excluded", record.issue_codes)
        self.assertEqual(result.completeness.excluded_source_count, 1)

    def test_current_master_gap_keeps_new_listing_semantics(self):
        result = self.fetch(
            providers_for(source_record()),
            current_master=[
                master("000001"),
                master("001399", "新上市公司", date(2026, 1, 5)),
            ],
        )

        self.assertEqual(result.completeness.mapped_count, 1)
        self.assertEqual(result.completeness.unconfirmed_count, 1)
        self.assertEqual(result.completeness.mapping_coverage, 0.5)
        self.assertEqual(result.current_master_gaps[0].symbol, "001399")
        self.assertIn(
            "listed_on_or_after_classification_start",
            result.current_master_gaps[0].issue_codes,
        )

    def test_bse_exact_category_is_crosswalked_with_auditable_provenance(self):
        result = self.fetch(
            providers_for(
                source_record(),
                source_record(
                    symbol="000008",
                    name="参照公司",
                    category_code="C",
                    category_name="制造业",
                    subclass_code="CG",
                    subclass_name="专用、通用及交通运输设备",
                    division_code="35",
                    division_name="专用设备制造业",
                ),
            ),
            current_master=[
                master("000001"),
                master(
                    "920001",
                    "北交新股",
                    date(2026, 7, 1),
                    exchange="bse",
                    source_industry="专用设备制造业",
                    source_report_date=date(2026, 7, 20),
                ),
            ],
            supplement_official_exchange_categories=True,
        )

        self.assertEqual(result.completeness.mapped_count, 2)
        self.assertEqual(result.completeness.unconfirmed_count, 0)
        self.assertEqual(result.completeness.supplemental_record_count, 1)
        self.assertEqual(result.current_master_gaps, [])
        supplement = next(
            record
            for record in result.records
            if record.security_identity == "920001"
        )
        self.assertEqual(supplement.category_code, "C")
        self.assertEqual(supplement.division_code, "35")
        self.assertEqual(supplement.manufacturing_subclass_code, "CG")
        self.assertEqual(
            supplement.record_provenance,
            IndustryRecordProvenance.BSE_EXACT_CATEGORY_CROSSWALK,
        )
        self.assertEqual(supplement.knowledge_effective_from, FETCHED_AT)
        self.assertEqual(
            supplement.evidence_url,
            "https://www.bse.cn/nqxxController/nqxxCnzq.do",
        )

    def test_exchange_category_supplement_never_guesses_unknown_or_coarse_values(self):
        result = self.fetch(
            providers_for(source_record()),
            current_master=[
                master("000001"),
                master(
                    "920001",
                    "未知北交股",
                    date(2026, 8, 1),
                    exchange="bse",
                    source_industry="官方代码表不存在的行业",
                ),
                master(
                    "001399",
                    "深市新股",
                    date(2026, 8, 2),
                    exchange="szse",
                    source_industry="C 制造业",
                ),
                master(
                    "603999",
                    "沪市新股",
                    date(2026, 8, 3),
                    exchange="sse",
                    source_industry=None,
                ),
            ],
            supplement_official_exchange_categories=True,
        )

        self.assertEqual(result.completeness.supplemental_record_count, 0)
        self.assertEqual(result.completeness.mapped_count, 1)
        self.assertEqual(result.completeness.unconfirmed_count, 3)
        self.assertEqual(
            {gap.symbol for gap in result.current_master_gaps},
            {"920001", "001399", "603999"},
        )

    def test_cninfo_capco_record_supplements_sse_and_szse_exact_codes(self):
        def load_supplements(symbols, _as_of):
            self.assertEqual(set(symbols), {"001399", "603999"})
            return (
                OfficialIndustrySupplementRecord(
                    symbol="001399",
                    source_name="深市新股",
                    category_code="C",
                    category_name="制造业",
                    division_code="26",
                    division_name="化学原料和化学制品制造业",
                    effective_on=date(2026, 7, 2),
                    fetched_at=FETCHED_AT,
                    evidence_url=(
                        "https://webapi.cninfo.com.cn/api/stock/p_stock2110"
                    ),
                    source_fields={"F001V": "008001", "F003V": "C26"},
                ),
                OfficialIndustrySupplementRecord(
                    symbol="603999",
                    source_name="沪市新股",
                    category_code="C",
                    category_name="制造业",
                    division_code="26",
                    division_name="化学原料和化学制品制造业",
                    effective_on=date(2026, 7, 3),
                    fetched_at=FETCHED_AT,
                    evidence_url=(
                        "https://webapi.cninfo.com.cn/api/stock/p_stock2110"
                    ),
                    source_fields={"F001V": "008001", "F003V": "C26"},
                ),
            )

        result = self.fetch(
            providers_for(
                source_record(),
                source_record(
                    symbol="000008",
                    name="参照公司",
                    category_code="C",
                    category_name="制造业",
                    subclass_code="CC",
                    subclass_name="石油化工、化学制品",
                    division_code="26",
                    division_name="化学原料和化学制品制造业",
                ),
                supplement_loader=load_supplements,
            ),
            current_master=[
                master("000001"),
                master(
                    "001399",
                    "深市新股",
                    date(2026, 7, 2),
                    exchange="szse",
                    source_industry="C 制造业",
                ),
                master(
                    "603999",
                    "沪市新股",
                    date(2026, 7, 3),
                    exchange="sse",
                ),
            ],
            supplement_official_exchange_categories=True,
        )

        self.assertEqual(result.completeness.mapped_count, 3)
        self.assertEqual(result.completeness.supplemental_record_count, 2)
        self.assertEqual(result.current_master_gaps, [])
        supplements = [
            record for record in result.records
            if record.record_provenance
            == IndustryRecordProvenance.CNINFO_CAPCO_CURRENT_CROSSWALK
        ]
        self.assertEqual({record.security_identity for record in supplements}, {
            "001399", "603999",
        })
        self.assertTrue(all(
            record.manufacturing_subclass_code == "CC"
            for record in supplements
        ))

    def test_cninfo_supplement_rejects_future_or_crosswalk_mismatch(self):
        def load_supplements(_symbols, _as_of):
            return (
                OfficialIndustrySupplementRecord(
                    symbol="001399",
                    source_name="深市新股",
                    category_code="C",
                    category_name="制造业",
                    division_code="26",
                    division_name="不匹配的大类名称",
                    effective_on=date(2026, 7, 2),
                    fetched_at=FETCHED_AT,
                    evidence_url=(
                        "https://webapi.cninfo.com.cn/api/stock/p_stock2110"
                    ),
                    source_fields={"F001V": "008001", "F003V": "C26"},
                ),
                OfficialIndustrySupplementRecord(
                    symbol="603999",
                    source_name="沪市新股",
                    category_code="C",
                    category_name="制造业",
                    division_code="26",
                    division_name="化学原料和化学制品制造业",
                    effective_on=AS_OF.date() + timedelta(days=1),
                    fetched_at=FETCHED_AT,
                    evidence_url=(
                        "https://webapi.cninfo.com.cn/api/stock/p_stock2110"
                    ),
                    source_fields={"F001V": "008001", "F003V": "C26"},
                ),
            )

        result = self.fetch(
            providers_for(
                source_record(),
                source_record(
                    symbol="000008",
                    name="参照公司",
                    category_code="C",
                    category_name="制造业",
                    subclass_code="CC",
                    subclass_name="石油化工、化学制品",
                    division_code="26",
                    division_name="化学原料和化学制品制造业",
                ),
                supplement_loader=load_supplements,
            ),
            current_master=[
                master("000001"),
                master("001399", "深市新股", date(2026, 7, 2)),
                master(
                    "603999",
                    "沪市新股",
                    date(2026, 7, 3),
                    exchange="sse",
                ),
            ],
            supplement_official_exchange_categories=True,
        )

        self.assertEqual(result.completeness.supplemental_record_count, 0)
        self.assertEqual(
            {gap.symbol for gap in result.current_master_gaps},
            {"001399", "603999"},
        )

    def test_source_failure_is_sanitized_and_does_not_create_fake_release(self):
        result = self.fetch(
            providers_for(
                source_record(),
                page_error=RuntimeError("secret upstream detail"),
            )
        )

        self.assertEqual(result.status.value, "failed")
        self.assertIsNone(result.release)
        self.assertEqual(result.records, [])
        self.assertNotIn("secret upstream detail", result.issues[0].message)
        self.assertIn("RuntimeError", result.issues[0].message)

    def test_final_redirect_must_stay_on_official_domain(self):
        result = self.fetch(
            providers_for(
                source_record(),
                document_final_url="https://example.com/industry.pdf",
            )
        )

        self.assertEqual(result.status.value, "failed")
        self.assertIn("unapproved_source_domain", {i.code for i in result.issues})


if __name__ == "__main__":
    unittest.main()
