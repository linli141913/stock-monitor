import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationGap,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.sector_features import build_sector_features


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 21, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = AS_OF + timedelta(seconds=3)


def industry_record(
    symbol,
    *,
    division_code="35",
    division_name="专用设备制造业",
):
    return IndustryClassificationRecord(
        releasePeriod="2025H2",
        sourceSymbol=symbol,
        sourceName=f"证券{symbol}",
        securityIdentity=symbol,
        identityStatus=IndustryIdentityStatus.EXACT,
        categoryCode="C",
        categoryName="制造业",
        divisionCode=division_code,
        divisionName=division_name,
        manufacturingSubclassCode="CG",
        manufacturingSubclassName="专用、通用及交通运输设备",
        recordStatus=IndustryRecordStatus.ACCEPTED,
    )


def classification_snapshot(
    records,
    *,
    gap_symbols=(),
    radar_run_id="run-sector-1",
    as_of=AS_OF,
    knowledge_effective_from=None,
    knowledge_effective_to=None,
):
    knowledge_from = knowledge_effective_from or (as_of - timedelta(days=1))
    gaps = [
        IndustryClassificationGap(
            securityIdentity=symbol,
            symbol=symbol,
            name=f"证券{symbol}",
            listingDate=date(2026, 1, 5),
            issueCodes=("listed_on_or_after_classification_start",),
        )
        for symbol in gap_symbols
    ]
    mapped_identities = {
        record.security_identity
        for record in records
        if record.record_status == IndustryRecordStatus.ACCEPTED
    }
    current_count = len(mapped_identities) + len(gaps)
    release = IndustryClassificationRelease(
        schemeVersion="capco-guideline-2023-shadow",
        releasePeriod="2025H2",
        sourcePageTitle="2025年下半年上市公司行业分类结果",
        publicationPageUrl="https://www.capco.org.cn/result.html",
        documentUrl="https://sp.capco.org.cn:82/result.pdf",
        documentSha256="1" * 64,
        publishedDate=date(2026, 4, 3),
        firstObservedAt=knowledge_from,
        fetchedAt=knowledge_from,
        knowledgeEffectiveFrom=knowledge_from,
        knowledgeEffectiveTo=knowledge_effective_to,
        classificationStartDate=date(2025, 12, 20),
        historyStatus=IndustryHistoryStatus.FORWARD_OBSERVED,
        sourceRecordCount=len(records),
        uniqueSourceSymbolCount=len({record.source_symbol for record in records}),
        requiredFieldCoverage={"division_code": 1.0},
    )
    status = SourceStatus.DEGRADED if gaps else SourceStatus.HEALTHY
    return IndustryClassificationSnapshot(
        meta=RadarBatchMeta(
            radarRunId=radar_run_id,
            batchId="industry-1",
            source="capco_industry_classification",
            asOf=as_of,
            sourceTime=None,
            fetchedAt=knowledge_from,
            expectedCount=len(records),
            returnedCount=len(records),
            rowCoverage=1.0 if records else 0.0,
            requiredFieldCoverage={"division_code": 1.0 if records else 0.0},
            issues=[],
        ),
        status=status,
        release=release,
        records=records,
        currentMasterGaps=gaps,
        completeness=IndustryClassificationCompleteness(
            sourceRecordCount=len(records),
            uniqueSourceSymbolCount=len({r.source_symbol for r in records}),
            currentMasterCount=current_count,
            mappedCount=len(mapped_identities),
            unconfirmedCount=len(gaps),
            excludedSourceCount=(len(records) - len(mapped_identities)),
            mappingCoverage=(
                len(mapped_identities) / current_count
                if current_count
                else None
            ),
            requiredFieldCoverage={"division_code": 1.0 if records else 0.0},
            shadowUsable=True,
            formalUsable=False,
            reasons=("formal_use_not_approved",),
        ),
        issues=[],
    )


def quote(
    symbol,
    *,
    change_percent=1.0,
    turnover_amount=100.0,
    market_cap=100.0,
    source_time=AS_OF,
):
    return QuoteSnapshot(
        symbol=symbol,
        name=f"证券{symbol}",
        sourceTime=source_time,
        fetchedAt=FETCHED_AT,
        price=10.0,
        changePercent=change_percent,
        turnoverAmountSource=turnover_amount,
        turnoverRatePercent=1.0,
        volumeRatio=1.0,
        marketCapSource=market_cap,
    )


def quote_batch(
    items,
    *,
    radar_run_id="run-sector-1",
    as_of=AS_OF,
    expected_count=None,
    source_time=AS_OF,
):
    expected = len(items) if expected_count is None else expected_count
    returned = len(items)
    row_coverage = returned / expected if expected else 0.0
    fields = (
        "source_time",
        "change_percent",
        "turnover_amount_source",
        "market_cap_source",
    )
    coverage = {
        field_name: (
            sum(getattr(item, field_name) is not None for item in items) / returned
            if returned
            else 0.0
        )
        for field_name in fields
    }
    return SourceBatch[QuoteSnapshot](
        meta=RadarBatchMeta(
            radarRunId=radar_run_id,
            batchId="stock-quotes-1",
            source="tencent_finance",
            asOf=as_of,
            sourceTime=source_time,
            fetchedAt=FETCHED_AT,
            expectedCount=expected,
            returnedCount=returned,
            rowCoverage=row_coverage,
            requiredFieldCoverage=coverage,
            issues=[],
        ),
        items=items,
    )


class SectorFeatureTests(unittest.TestCase):
    def build(self, records, quotes, *, stock_symbols=None, **kwargs):
        stocks = stock_symbols or [record.security_identity for record in records]
        return build_sector_features(
            classification_snapshot(records),
            quote_batch(quotes),
            stock_symbols=stocks,
            etf_symbols=[],
            **kwargs,
        )

    def test_deterministic_current_sector_features_follow_frozen_formulas(self):
        records = [
            industry_record("000001"),
            industry_record("000002"),
            industry_record("000003"),
        ]
        result = self.build(records, [
            quote("000001", change_percent=10, turnover_amount=10, market_cap=100),
            quote("000002", change_percent=-2, turnover_amount=20, market_cap=200),
            quote("000003", change_percent=0, turnover_amount=30, market_cap=700),
        ])

        sector = result.sectors[0]
        self.assertAlmostEqual(sector.returns.equal_return.raw_value, 8 / 3)
        self.assertAlmostEqual(sector.returns.cap_weighted_return.raw_value, 0.6)
        self.assertAlmostEqual(sector.returns.ex_top_return.raw_value, -4 / 9)
        self.assertEqual(sector.returns.top_contributor_symbol, "000001")
        self.assertAlmostEqual(
            sector.returns.top_contribution_percent_points,
            1.0,
        )
        self.assertEqual(sector.breadth.advancers, 1)
        self.assertEqual(sector.breadth.decliners, 1)
        self.assertEqual(sector.breadth.flat, 1)
        self.assertAlmostEqual(sector.breadth.up_ratio.raw_value, 1 / 3)
        self.assertEqual(sector.turnover.raw_value, 60.0)
        self.assertTrue(sector.completeness.is_complete)
        self.assertTrue(sector.shadow_usable)
        self.assertFalse(sector.formal_usable)
        self.assertEqual(
            sector.returns.market_cap_unit_status,
            UnitVerificationStatus.UNVERIFIED,
        )

    def test_true_zero_is_preserved_for_returns_breadth_and_turnover(self):
        records = [industry_record("000001"), industry_record("000002")]
        result = self.build(records, [
            quote("000001", change_percent=0, turnover_amount=0, market_cap=100),
            quote("000002", change_percent=0, turnover_amount=0, market_cap=200),
        ])

        sector = result.sectors[0]
        self.assertEqual(sector.returns.equal_return.raw_value, 0.0)
        self.assertEqual(sector.returns.cap_weighted_return.raw_value, 0.0)
        self.assertEqual(sector.returns.ex_top_return.raw_value, 0.0)
        self.assertEqual(sector.breadth.flat, 2)
        self.assertEqual(sector.breadth.up_ratio.raw_value, 0.0)
        self.assertEqual(sector.turnover.raw_value, 0.0)

    def test_divisions_are_calculated_as_separate_constituent_sets(self):
        records = [
            industry_record("000001", division_code="35"),
            industry_record("000002", division_code="35"),
            industry_record(
                "000003",
                division_code="36",
                division_name="汽车制造业",
            ),
            industry_record(
                "000004",
                division_code="36",
                division_name="汽车制造业",
            ),
        ]
        result = self.build(records, [
            quote("000001", change_percent=1, turnover_amount=10),
            quote("000002", change_percent=3, turnover_amount=20),
            quote("000003", change_percent=-2, turnover_amount=30),
            quote("000004", change_percent=-4, turnover_amount=40),
        ])

        by_division = {sector.division_code: sector for sector in result.sectors}
        self.assertEqual(set(by_division), {"35", "36"})
        self.assertEqual(
            by_division["35"].constituent_symbols,
            ("000001", "000002"),
        )
        self.assertEqual(by_division["35"].returns.equal_return.raw_value, 2.0)
        self.assertEqual(by_division["35"].turnover.raw_value, 30.0)
        self.assertEqual(by_division["36"].returns.equal_return.raw_value, -3.0)
        self.assertEqual(by_division["36"].turnover.raw_value, 70.0)

    def test_unconfirmed_current_stock_is_excluded_and_kept_in_batch_counts(self):
        records = [industry_record("000001"), industry_record("000002")]
        classification = classification_snapshot(records, gap_symbols=("000003",))
        quotes = quote_batch([
            quote("000001", turnover_amount=10),
            quote("000002", turnover_amount=20),
            quote("000003", turnover_amount=999),
        ])

        result = build_sector_features(
            classification,
            quotes,
            stock_symbols=["000001", "000002", "000003"],
            etf_symbols=[],
        )

        self.assertEqual(result.mapped_constituent_count, 2)
        self.assertEqual(result.unconfirmed_stock_count, 1)
        self.assertEqual(result.sectors[0].turnover.raw_value, 30.0)
        self.assertNotIn("000003", result.sectors[0].constituent_symbols)
        self.assertEqual(result.status, SourceStatus.DEGRADED)

    def test_missing_constituent_quote_makes_metrics_unavailable(self):
        records = [industry_record("000001"), industry_record("000002")]
        result = build_sector_features(
            classification_snapshot(records),
            quote_batch([quote("000001")], expected_count=2),
            stock_symbols=["000001", "000002"],
            etf_symbols=[],
        )

        sector = result.sectors[0]
        self.assertFalse(sector.returns.equal_return.available)
        self.assertFalse(sector.returns.cap_weighted_return.available)
        self.assertFalse(sector.breadth.up_ratio.available)
        self.assertFalse(sector.turnover.available)
        self.assertIn("constituent_quote_missing", sector.reasons)

    def test_duplicate_quote_is_not_double_counted(self):
        records = [industry_record("000001"), industry_record("000002")]
        result = build_sector_features(
            classification_snapshot(records),
            quote_batch([
                quote("000001", turnover_amount=10),
                quote("000001", turnover_amount=999),
                quote("000002", turnover_amount=20),
            ]),
            stock_symbols=["000001", "000002"],
            etf_symbols=[],
        )

        self.assertEqual(result.duplicate_quote_symbols, ("000001",))
        self.assertIsNone(result.sectors[0].turnover.raw_value)
        self.assertIn("duplicate_constituent_quote", result.sectors[0].reasons)

    def test_missing_change_percent_is_not_converted_to_flat(self):
        records = [industry_record("000001"), industry_record("000002")]
        result = self.build(records, [
            quote("000001", change_percent=0),
            quote("000002", change_percent=None),
        ])

        breadth = result.sectors[0].breadth
        self.assertEqual(breadth.flat, 1)
        self.assertEqual(breadth.unavailable, 1)
        self.assertFalse(breadth.up_ratio.available)
        self.assertIn("change_percent_missing", result.sectors[0].reasons)

    def test_stale_and_future_constituents_are_rejected_individually(self):
        records = [industry_record("000001"), industry_record("000002")]
        for source_time, reason in (
            (AS_OF - timedelta(seconds=91), "source_time_stale"),
            (AS_OF + timedelta(seconds=6), "source_time_in_future"),
        ):
            with self.subTest(reason=reason):
                result = self.build(records, [
                    quote("000001", source_time=source_time),
                    quote("000002"),
                ])

                sector = result.sectors[0]
                self.assertFalse(sector.returns.equal_return.available)
                self.assertIn(reason, sector.reasons)

    def test_missing_or_zero_market_cap_only_blocks_cap_weighted_metrics(self):
        records = [industry_record("000001"), industry_record("000002")]
        for market_cap, reason in (
            (None, "market_cap_missing"),
            (0, "market_cap_not_positive"),
        ):
            with self.subTest(reason=reason):
                result = self.build(records, [
                    quote("000001", market_cap=market_cap),
                    quote("000002", market_cap=100),
                ])

                sector = result.sectors[0]
                self.assertTrue(sector.returns.equal_return.available)
                self.assertTrue(sector.breadth.up_ratio.available)
                self.assertTrue(sector.turnover.available)
                self.assertFalse(sector.returns.cap_weighted_return.available)
                self.assertFalse(sector.returns.ex_top_return.available)
                self.assertIn(reason, sector.reasons)

    def test_single_constituent_cannot_produce_ex_top_or_shadow_ready_sector(self):
        result = self.build(
            [industry_record("000001")],
            [quote("000001")],
        )

        sector = result.sectors[0]
        self.assertFalse(sector.returns.equal_return.available)
        self.assertFalse(sector.returns.ex_top_return.available)
        self.assertFalse(sector.shadow_usable)
        self.assertIn("insufficient_constituents", sector.reasons)

    def test_classification_and_quotes_must_share_run_and_as_of(self):
        records = [industry_record("000001"), industry_record("000002")]
        with self.assertRaises(ValueError):
            build_sector_features(
                classification_snapshot(records),
                quote_batch(
                    [quote("000001"), quote("000002")],
                    radar_run_id="different-run",
                ),
                stock_symbols=["000001", "000002"],
                etf_symbols=[],
            )
        with self.assertRaises(ValueError):
            build_sector_features(
                classification_snapshot(records),
                quote_batch(
                    [quote("000001"), quote("000002")],
                    as_of=AS_OF + timedelta(seconds=1),
                ),
                stock_symbols=["000001", "000002"],
                etf_symbols=[],
            )

    def test_future_or_expired_classification_version_is_rejected(self):
        records = [industry_record("000001"), industry_record("000002")]
        for classification in (
            classification_snapshot(
                records,
                knowledge_effective_from=AS_OF + timedelta(seconds=1),
            ),
            classification_snapshot(
                records,
                knowledge_effective_to=AS_OF,
            ),
        ):
            with self.assertRaises(ValueError):
                build_sector_features(
                    classification,
                    quote_batch([quote("000001"), quote("000002")]),
                    stock_symbols=["000001", "000002"],
                    etf_symbols=[],
                )

    def test_duplicate_classification_identity_is_rejected(self):
        first = industry_record("000001")
        duplicate = industry_record("000002")
        duplicate.security_identity = "000001"
        classification = classification_snapshot([first, duplicate])

        with self.assertRaises(ValueError):
            build_sector_features(
                classification,
                quote_batch([quote("000001")]),
                stock_symbols=["000001"],
                etf_symbols=[],
            )

    def test_etf_and_unknown_quotes_never_enter_sector_denominators(self):
        records = [industry_record("000001"), industry_record("000002")]
        result = build_sector_features(
            classification_snapshot(records),
            quote_batch([
                quote("000001", turnover_amount=10),
                quote("000002", turnover_amount=20),
                quote("510300", turnover_amount=900),
                quote("999999", turnover_amount=800),
            ]),
            stock_symbols=["000001", "000002"],
            etf_symbols=["510300"],
        )

        self.assertEqual(result.excluded_etf_count, 1)
        self.assertEqual(result.unknown_quote_symbols, ("999999",))
        self.assertEqual(result.sectors[0].turnover.raw_value, 30.0)
        self.assertEqual(result.status, SourceStatus.DEGRADED)


if __name__ == "__main__":
    unittest.main()
