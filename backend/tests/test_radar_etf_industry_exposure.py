import hashlib
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    EtfManagementStyle,
    IndustryClassificationCompleteness,
    IndustryClassificationGap,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    RadarBatchMeta,
    SourceStatus,
)
from radar.etf_industry_exposure import (
    build_index_product_groups,
    calculate_constituent_overlap,
    calculate_index_industry_exposure,
)
from radar.sources.etf_index_evidence import (
    build_constituent_set_evidence,
    verify_etf_index_identity,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 6, 30, 15, 30, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 25, 0, 30, tzinfo=SHANGHAI_TZ)
EVIDENCE_HASH = hashlib.sha256(b"stage5d evidence").hexdigest()


def constituent_set(
    items,
    *,
    provider="csindex",
    index_code="000300",
    index_name="沪深300指数",
    source_date=date(2026, 6, 30),
    effective_from=AS_OF - timedelta(hours=8),
    effective_to=None,
    announced_at=AS_OF - timedelta(days=1),
):
    return build_constituent_set_evidence(
        provider=provider,
        index_code=index_code,
        index_name=index_name,
        announced_at=announced_at,
        effective_from=effective_from,
        effective_to=effective_to,
        source_date=source_date,
        expected_count=len(items),
        as_of=AS_OF,
        evidence_url="https://index.example/weights.xls",
        evidence_sha256=EVIDENCE_HASH,
        first_observed_at=AS_OF,
        fetched_at=AS_OF,
        items=items,
    )


def industry_record(
    symbol,
    *,
    division_code="63",
    division_name="电信、广播电视和卫星传输服务",
):
    return IndustryClassificationRecord(
        releasePeriod="2025H2",
        sourceSymbol=symbol,
        sourceName=f"证券{symbol}",
        securityIdentity=symbol,
        identityStatus=IndustryIdentityStatus.EXACT,
        categoryCode="I",
        categoryName="信息传输、软件和信息技术服务业",
        divisionCode=division_code,
        divisionName=division_name,
        recordStatus=IndustryRecordStatus.ACCEPTED,
    )


def classification_snapshot(
    records,
    *,
    gap_symbols=(),
    published_date=date(2026, 4, 3),
    first_observed_at=AS_OF,
    history_status=IndustryHistoryStatus.FORWARD_OBSERVED,
    formal_usable=True,
):
    identities = {
        record.security_identity
        for record in records
        if record.security_identity is not None
    }
    gaps = [
        IndustryClassificationGap(
            securityIdentity=symbol,
            symbol=symbol,
            name=f"证券{symbol}",
            listingDate=date(2026, 1, 2),
            issueCodes=("current_master_mapping_gap",),
        )
        for symbol in gap_symbols
    ]
    current_count = len(identities) + len(gaps)
    release = IndustryClassificationRelease(
        schemeVersion="capco-guideline-2023-shadow",
        releasePeriod="2025H2",
        sourcePageTitle="2025年下半年上市公司行业分类结果",
        publicationPageUrl="https://www.capco.org.cn/result.html",
        documentUrl="https://sp.capco.org.cn/result.pdf",
        documentSha256=EVIDENCE_HASH,
        publishedDate=published_date,
        firstObservedAt=first_observed_at,
        fetchedAt=max(first_observed_at, FETCHED_AT),
        knowledgeEffectiveFrom=first_observed_at,
        knowledgeEffectiveTo=None,
        classificationStartDate=date(2025, 12, 20),
        historyStatus=history_status,
        sourceRecordCount=len(records),
        uniqueSourceSymbolCount=len({
            record.source_symbol
            for record in records
        }),
        requiredFieldCoverage={"division_code": 1.0},
    )
    mapped_count = len(identities)
    return IndustryClassificationSnapshot(
        meta=RadarBatchMeta(
            radarRunId="stage5d-test",
            batchId="industry-map",
            source="capco_industry_classification",
            asOf=AS_OF,
            sourceTime=None,
            fetchedAt=max(first_observed_at, FETCHED_AT),
            expectedCount=len(records),
            returnedCount=len(records),
            rowCoverage=1.0 if records else 0.0,
            requiredFieldCoverage={
                "division_code": 1.0 if records else 0.0,
            },
            issues=[],
        ),
        status=SourceStatus.HEALTHY if not gaps else SourceStatus.DEGRADED,
        release=release,
        records=records,
        currentMasterGaps=gaps,
        completeness=IndustryClassificationCompleteness(
            sourceRecordCount=len(records),
            uniqueSourceSymbolCount=len({
                record.source_symbol
                for record in records
            }),
            currentMasterCount=current_count,
            mappedCount=mapped_count,
            unconfirmedCount=len(gaps),
            excludedSourceCount=0,
            mappingCoverage=(
                mapped_count / current_count
                if current_count
                else None
            ),
            requiredFieldCoverage={
                "division_code": 1.0 if records else 0.0,
            },
            shadowUsable=True,
            formalUsable=formal_usable,
            reasons=() if formal_usable else ("formal_use_not_approved",),
        ),
        issues=[],
    )


def relation(
    symbol,
    *,
    provider="cnindex",
    index_code="399006",
    index_name="创业板指数",
):
    return verify_etf_index_identity(
        symbol=symbol,
        management_style=EtfManagementStyle.PASSIVE_INDEX,
        fund_index_code=index_code,
        fund_index_name=index_name,
        provider=provider,
        provider_index_code=index_code,
        provider_index_name=index_name,
        published_at=AS_OF - timedelta(days=30),
        effective_from=AS_OF - timedelta(days=30),
        effective_to=None,
        as_of=AS_OF,
        fund_evidence_url=f"https://fund.example/{symbol}",
        fund_evidence_sha256=EVIDENCE_HASH,
        provider_evidence_url=f"https://index.example/{index_code}",
        provider_evidence_sha256=EVIDENCE_HASH,
        first_observed_at=AS_OF,
        fetched_at=AS_OF,
    )


class IndexIndustryExposureTests(unittest.TestCase):
    def test_unmapped_weight_is_preserved_without_redistribution(self):
        constituents = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 50.0},
            {"stockCode": "000002", "stockName": "乙", "weight": 30.0},
            {"stockCode": "000003", "stockName": "丙", "weight": 20.0},
        ])
        classification = classification_snapshot(
            [
                industry_record(
                    "000001",
                    division_code="63",
                    division_name="电信业",
                ),
                industry_record(
                    "000002",
                    division_code="64",
                    division_name="互联网业",
                ),
            ],
            gap_symbols=("000003",),
        )

        result = calculate_index_industry_exposure(
            constituents,
            classification,
            as_of=AS_OF,
            computed_at=FETCHED_AT,
        )

        by_code = {item.industry_code: item for item in result.exposures}
        self.assertEqual(result.total_weight, 100.0)
        self.assertEqual(result.mapped_weight, 80.0)
        self.assertEqual(result.unmapped_weight, 20.0)
        self.assertEqual(result.mapping_coverage, 0.8)
        self.assertEqual(by_code["63"].raw_weight, 50.0)
        self.assertEqual(by_code["64"].raw_weight, 30.0)
        self.assertAlmostEqual(sum(
            item.exposure_ratio
            for item in result.exposures
        ), 0.8)
        self.assertIn("industry_mapping_incomplete", result.reasons)
        self.assertFalse(result.formal_ready)

    def test_duplicate_industry_identity_becomes_unmapped_conflict(self):
        constituents = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ])
        classification = classification_snapshot([
            industry_record("000001", division_code="63", division_name="电信业"),
            industry_record("000001", division_code="64", division_name="互联网业"),
        ])

        result = calculate_index_industry_exposure(
            constituents,
            classification,
            as_of=AS_OF,
            computed_at=FETCHED_AT,
        )

        self.assertEqual(result.mapped_weight, 0.0)
        self.assertEqual(result.unmapped_weight, 100.0)
        self.assertIn("industry_mapping_conflict", result.reasons)
        self.assertEqual(result.unmapped_symbols, ("000001",))

    def test_future_classification_release_is_rejected(self):
        constituents = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ])
        classification = classification_snapshot(
            [industry_record("000001")],
            published_date=date(2026, 7, 1),
        )

        with self.assertRaisesRegex(ValueError, "行业分类发布日期"):
            calculate_index_industry_exposure(
                constituents,
                classification,
                as_of=AS_OF,
                computed_at=FETCHED_AT,
            )

    def test_retrospective_release_calculates_but_never_becomes_formal(self):
        constituents = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ])
        classification = classification_snapshot(
            [industry_record("000001")],
            first_observed_at=FETCHED_AT,
            history_status=IndustryHistoryStatus.RETROSPECTIVE_UNVERIFIED,
            formal_usable=False,
        )

        result = calculate_index_industry_exposure(
            constituents,
            classification,
            as_of=AS_OF,
            computed_at=FETCHED_AT,
        )

        self.assertEqual(result.mapping_coverage, 1.0)
        self.assertIn(
            "industry_mapping_retrospective_unverified",
            result.reasons,
        )
        self.assertIn("industry_mapping_not_formal", result.reasons)
        self.assertFalse(result.formal_ready)


class IndexProductGroupTests(unittest.TestCase):
    def test_same_index_products_consume_one_group_slot(self):
        result = build_index_product_groups([
            relation("159915"),
            relation("159949"),
            relation(
                "510310",
                provider="csindex",
                index_code="000300",
                index_name="沪深300指数",
            ),
        ])

        self.assertEqual(result.group_count, 2)
        self.assertEqual(result.candidate_slot_count, 2)
        group = {
            item.group_key: item
            for item in result.groups
        }["cnindex:399006"]
        self.assertEqual(group.member_symbols, ("159915", "159949"))
        self.assertIsNone(group.representative_symbol)
        self.assertIn("representative_selection_deferred", group.reasons)

    def test_one_symbol_cannot_belong_to_two_index_groups(self):
        result = build_index_product_groups([
            relation("159915"),
            relation(
                "159915",
                provider="csindex",
                index_code="000300",
                index_name="沪深300指数",
            ),
        ])

        self.assertEqual(result.group_count, 0)
        self.assertEqual(result.excluded_symbols, ("159915",))
        self.assertIn("index_group_identity_conflict", result.reasons)


class ConstituentOverlapTests(unittest.TestCase):
    def test_symbol_and_weighted_overlap_use_original_weights(self):
        left = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 60.0},
            {"stockCode": "000002", "stockName": "乙", "weight": 40.0},
        ])
        right = constituent_set(
            [
                {"stockCode": "000002", "stockName": "乙", "weight": 50.0},
                {"stockCode": "000003", "stockName": "丙", "weight": 50.0},
            ],
            provider="cnindex",
            index_code="399006",
            index_name="创业板指数",
        )

        result = calculate_constituent_overlap(
            left,
            right,
            as_of=AS_OF,
        )

        self.assertEqual(result.common_count, 1)
        self.assertEqual(result.union_count, 3)
        self.assertAlmostEqual(result.symbol_jaccard, 1 / 3)
        self.assertEqual(result.common_minimum_weight, 40.0)
        self.assertEqual(result.weighted_overlap, 0.4)
        self.assertTrue(result.formal_ready)

    def test_missing_weights_and_version_date_mismatch_remain_nonformal(self):
        left = constituent_set([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ])
        right = constituent_set(
            [{"stockCode": "000001", "stockName": "甲", "weight": None}],
            provider="cnindex",
            index_code="399006",
            index_name="创业板指数",
            source_date=date(2026, 6, 27),
        )

        result = calculate_constituent_overlap(
            left,
            right,
            as_of=AS_OF,
        )

        self.assertEqual(result.symbol_jaccard, 1.0)
        self.assertIsNone(result.weighted_overlap)
        self.assertIn("overlap_weight_missing", result.reasons)
        self.assertIn("constituent_version_time_mismatch", result.reasons)
        self.assertFalse(result.formal_ready)
