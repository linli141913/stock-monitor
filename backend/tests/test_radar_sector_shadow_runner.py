import json
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
)
from radar.migrations import apply_pending_migrations
from radar.repository import RadarRepository
from radar.sector_shadow_runner import (
    SectorShadowRunAlreadyExistsError,
    SectorShadowRunExecutionError,
    SectorShadowRunInProgressError,
    SectorShadowRunner,
)


UTC = timezone.utc
UTC_PLUS_8 = timezone(timedelta(hours=8))
MASTER_AS_OF = datetime(2026, 7, 21, 9, 30, tzinfo=UTC_PLUS_8)
AS_OF = datetime(2026, 7, 22, 9, 55, tzinfo=UTC_PLUS_8)
WRITTEN_AT = datetime(2026, 7, 22, 1, 55, 10, tzinfo=UTC)


class RadarSectorShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: WRITTEN_AT,
        )
        self.quote_calls = []
        self.seed_security_master()

    def tearDown(self):
        self.connection.close()

    @staticmethod
    def security_record(symbol, name):
        return SecurityMasterRecord(
            symbol=symbol,
            name=name,
            exchange="szse",
            board="主板",
            listingDate="1991-04-03",
            totalShares=1_000_000.0,
            circulatingShares=800_000.0,
            sourceIndustry="测试行业",
            sourceReportDate="2026-07-21",
            source="szse",
            fetchedAt=MASTER_AS_OF,
            sourceFields={"证券代码": symbol, "证券简称": name},
        )

    def seed_security_master(self):
        items = [
            self.security_record("000001", "平安银行"),
            self.security_record("000002", "万科A"),
        ]
        self.repository.sync_security_master(SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="master-run",
                batchId="master-batch",
                source="official_exchange_security_master",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=2,
                returnedCount=2,
                rowCoverage=1.0,
                requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
                issues=[],
            ),
            items=items,
        ))

    @staticmethod
    def classification_record(
        symbol,
        name,
        *,
        division_code="66",
        division_name="货币金融服务",
    ):
        return IndustryClassificationRecord(
            releasePeriod="2025H2",
            sourceSymbol=symbol,
            sourceName=name,
            securityIdentity=symbol,
            identityStatus="exact",
            categoryCode="J",
            categoryName="金融业",
            divisionCode=division_code,
            divisionName=division_name,
            recordStatus="accepted",
            issueCodes=(),
            sourceFields={"证券代码": symbol, "证券简称": name},
        )

    def seed_classification(self, records=None):
        records = list(records or [
            self.classification_record("000001", "平安银行"),
            self.classification_record("000002", "万科A"),
        ])
        observed_at = MASTER_AS_OF - timedelta(days=1)
        release = IndustryClassificationRelease(
            schemeVersion="capco-guideline-2023",
            releasePeriod="2025H2",
            sourcePageTitle="2025年下半年上市公司行业分类结果",
            publicationPageUrl="https://www.capco.org.cn/release.html",
            documentUrl="https://www.capco.org.cn/result.pdf",
            documentSha256="a" * 64,
            publishedDate="2026-04-03",
            firstObservedAt=observed_at,
            fetchedAt=observed_at + timedelta(seconds=1),
            knowledgeEffectiveFrom=observed_at,
            classificationStartDate="2025-12-20",
            historyStatus="retrospective_unverified",
            sourceRecordCount=len(records),
            uniqueSourceSymbolCount=len(records),
            requiredFieldCoverage={"divisionCode": 1.0},
        )
        snapshot = IndustryClassificationSnapshot(
            meta=RadarBatchMeta(
                radarRunId="classification-run",
                batchId="classification-batch",
                source="capco_industry_classification",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=len(records),
                returnedCount=len(records),
                rowCoverage=1.0,
                requiredFieldCoverage={"divisionCode": 1.0},
                issues=[],
            ),
            status=SourceStatus.HEALTHY,
            release=release,
            records=records,
            currentMasterGaps=[],
            completeness=IndustryClassificationCompleteness(
                sourceRecordCount=len(records),
                uniqueSourceSymbolCount=len(records),
                currentMasterCount=len(records),
                mappedCount=len(records),
                unconfirmedCount=0,
                excludedSourceCount=0,
                mappingCoverage=1.0,
                requiredFieldCoverage={"divisionCode": 1.0},
                shadowUsable=True,
                formalUsable=False,
                reasons=("formal_use_not_approved",),
            ),
            issues=[],
        )
        self.repository.record_industry_classification(snapshot)

    @staticmethod
    def quote(symbol, *, source_time, change_percent=0.0, market_cap=100.0):
        return QuoteSnapshot(
            symbol=symbol,
            name=symbol,
            sourceTime=source_time,
            fetchedAt=AS_OF + timedelta(seconds=1),
            price=10.0,
            changePercent=change_percent,
            turnoverAmountSource=0.0,
            turnoverRatePercent=0.0,
            volumeRatio=0.0,
            marketCapSource=market_cap,
        )

    def quote_batch(
        self,
        symbols,
        radar_run_id,
        batch_id,
        as_of,
        *,
        source_time=None,
        market_cap_missing=False,
        wrong_run_id=False,
    ):
        self.quote_calls.append((tuple(symbols), radar_run_id, batch_id, as_of))
        source_time = source_time or as_of - timedelta(seconds=1)
        items = [
            self.quote(
                symbol,
                source_time=source_time,
                market_cap=(None if market_cap_missing and index == 0 else 100.0),
            )
            for index, symbol in enumerate(symbols)
        ]
        market_cap_coverage = (
            sum(item.market_cap_source is not None for item in items) / len(items)
            if items
            else 0.0
        )
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=("wrong-run" if wrong_run_id else radar_run_id),
                batchId=batch_id,
                source="tencent_finance",
                asOf=as_of,
                sourceTime=source_time,
                fetchedAt=as_of + timedelta(seconds=1),
                expectedCount=len(symbols),
                returnedCount=len(items),
                rowCoverage=1.0 if items else 0.0,
                requiredFieldCoverage={
                    "price": 1.0,
                    "source_time": 1.0,
                    "change_percent": 1.0,
                    "turnover_amount_source": 1.0,
                    "market_cap_source": market_cap_coverage,
                },
                issues=[],
            ),
            items=items,
        )

    def runner(self, fetcher=None, run_lock=None):
        return SectorShadowRunner(
            self.repository,
            quote_fetcher=fetcher or self.quote_batch,
            clock=lambda: WRITTEN_AT,
            run_lock=run_lock,
        )

    def test_success_persists_only_sector_aggregate_and_preserves_real_zero(self):
        self.seed_classification()

        result = self.runner().run_once("sector-run", AS_OF)

        self.assertTrue(result.gate_passed)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.stock_count, 2)
        self.assertEqual(result.sector_count, 1)
        self.assertEqual(result.eligible_sector_count, 1)
        self.assertEqual(result.shadow_usable_sector_count, 1)
        self.assertEqual(result.persisted_sector_count, 1)
        self.assertEqual(self.quote_calls[0][0], ("000001", "000002"))

        rows = self.repository.list_sector_feature_rows("sector-run")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["equalReturn"], 0.0)
        self.assertEqual(rows[0]["capWeightedReturn"], 0.0)
        self.assertEqual(rows[0]["exTopReturn"], 0.0)
        self.assertEqual(rows[0]["upRatio"], 0.0)
        self.assertEqual(rows[0]["turnoverRawValue"], 0.0)
        self.assertNotIn("constituentSymbols", rows[0]["evidenceSummary"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_source_status "
                "WHERE radar_run_id='sector-run'"
            ).fetchone()[0],
            1,
        )

    def test_stale_quote_batch_records_degraded_run_without_sector_snapshot(self):
        self.seed_classification()
        fetcher = lambda symbols, run_id, batch_id, as_of: self.quote_batch(
            symbols,
            run_id,
            batch_id,
            as_of,
            source_time=as_of - timedelta(seconds=91),
        )

        result = self.runner(fetcher).run_once("stale-run", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertIn("quote_health:source_time_stale", result.gate_reasons)
        self.assertEqual(result.persisted_sector_count, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT status, error_code FROM radar_runs "
                "WHERE radar_run_id='stale-run'"
            ).fetchone(),
            ("degraded", "sector_feature_gate_rejected"),
        )
        diagnostics = json.loads(self.connection.execute(
            "SELECT issues_json FROM radar_source_status "
            "WHERE radar_run_id='stale-run'"
        ).fetchone()[0])["diagnostics"]
        self.assertFalse(diagnostics["gatePassed"])
        self.assertEqual(
            diagnostics["gateReasons"],
            [
                "quote_health:source_time_stale",
                "quote_item_source_time_stale",
                "eligible_sector_features_incomplete",
            ],
        )
        self.assertEqual(
            diagnostics["quoteItemTimeSummary"],
            {
                "missingCount": 0,
                "staleCount": 2,
                "futureCount": 0,
            },
        )
        self.assertEqual(diagnostics["stockCount"], 2)
        self.assertEqual(diagnostics["quoteCount"], 2)
        self.assertEqual(diagnostics["sectorCount"], 1)
        self.assertEqual(diagnostics["eligibleSectorCount"], 1)
        self.assertEqual(diagnostics["shadowUsableSectorCount"], 0)
        self.assertEqual(
            diagnostics["incompleteEligibleSectorReasonCounts"][
                "source_time_stale"
            ],
            1,
        )
        self.assertNotIn("000001", json.dumps(diagnostics))
        self.assertNotIn("000002", json.dumps(diagnostics))

    def test_single_constituent_sector_is_saved_but_does_not_block_eligible_sector(self):
        third = self.security_record("000003", "测试证券")
        self.repository.sync_security_master(SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="master-run-2",
                batchId="master-batch-2",
                source="official_exchange_security_master",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=1,
                returnedCount=1,
                rowCoverage=1.0,
                requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
                issues=[],
            ),
            items=[third],
        ))
        self.seed_classification(records=[
            self.classification_record("000001", "平安银行"),
            self.classification_record("000002", "万科A"),
            self.classification_record(
                "000003",
                "测试证券",
                division_code="67",
                division_name="资本市场服务",
            ),
        ])

        result = self.runner().run_once("single-sector-run", AS_OF)

        self.assertTrue(result.gate_passed)
        self.assertEqual(result.sector_count, 2)
        self.assertEqual(result.eligible_sector_count, 1)
        self.assertEqual(result.shadow_usable_sector_count, 1)
        self.assertEqual(result.persisted_sector_count, 2)
        rows = self.repository.list_sector_feature_rows("single-sector-run")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["shadowUsable"] for row in rows), 1)

    def test_current_unmapped_security_is_quoted_but_does_not_enter_sector(self):
        self.seed_classification()
        third = self.security_record("000003", "新上市证券")
        self.repository.sync_security_master(SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="master-run-gap",
                batchId="master-batch-gap",
                source="official_exchange_security_master",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=1,
                returnedCount=1,
                rowCoverage=1.0,
                requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
                issues=[],
            ),
            items=[third],
        ))

        result = self.runner().run_once("mapping-gap-run", AS_OF)

        self.assertTrue(result.gate_passed)
        self.assertEqual(result.stock_count, 3)
        self.assertEqual(self.quote_calls[0][0], ("000001", "000002", "000003"))
        self.assertEqual(result.sector_count, 1)
        self.assertEqual(result.persisted_sector_count, 1)
        row = self.repository.list_sector_feature_rows("mapping-gap-run")[0]
        self.assertEqual(row["unconfirmedStockCount"], 1)

    def test_future_quote_and_missing_required_field_are_both_rejected(self):
        self.seed_classification()
        future_fetcher = (
            lambda symbols, run_id, batch_id, as_of: self.quote_batch(
                symbols,
                run_id,
                batch_id,
                as_of,
                source_time=as_of + timedelta(seconds=6),
            )
        )
        missing_fetcher = (
            lambda symbols, run_id, batch_id, as_of: self.quote_batch(
                symbols,
                run_id,
                batch_id,
                as_of,
                market_cap_missing=True,
            )
        )

        future = self.runner(future_fetcher).run_once("future-run", AS_OF)
        missing = self.runner(missing_fetcher).run_once("missing-run", AS_OF)

        self.assertFalse(future.gate_passed)
        self.assertIn("quote_health:source_time_in_future", future.gate_reasons)
        self.assertFalse(missing.gate_passed)
        self.assertIn(
            "quote_health:required_field_coverage_below_threshold:market_cap_source",
            missing.gate_reasons,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_source_exception_is_audited_but_does_not_write_sector_snapshot(self):
        self.seed_classification()

        def failed_fetcher(*_args):
            raise TimeoutError("upstream timeout")

        result = self.runner(failed_fetcher).run_once("failed-source", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertIn("quote_health:source_returned_no_rows", result.gate_reasons)
        self.assertEqual(result.persisted_sector_count, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT returned_count FROM radar_source_status "
                "WHERE radar_run_id='failed-source'"
            ).fetchone()[0],
            0,
        )

    def test_missing_stored_classification_stops_before_quote_fetch(self):
        result = self.runner().run_once("missing-classification", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertEqual(result.gate_reasons, ("classification_release_missing",))
        self.assertEqual(self.quote_calls, [])
        self.assertEqual(result.persisted_sector_count, 0)

    def test_batch_identity_mismatch_marks_run_failed_without_sector_snapshot(self):
        self.seed_classification()
        fetcher = lambda symbols, run_id, batch_id, as_of: self.quote_batch(
            symbols,
            run_id,
            batch_id,
            as_of,
            wrong_run_id=True,
        )

        with self.assertRaises(SectorShadowRunExecutionError):
            self.runner(fetcher).run_once("identity-run", AS_OF)

        self.assertEqual(
            self.connection.execute(
                "SELECT status, error_code FROM radar_runs "
                "WHERE radar_run_id='identity-run'"
            ).fetchone(),
            ("failed", "sector_shadow_batch_identity"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_duplicate_run_id_is_rejected_without_duplicate_rows(self):
        self.seed_classification()
        runner = self.runner()
        runner.run_once("duplicate-run", AS_OF)

        with self.assertRaises(SectorShadowRunAlreadyExistsError):
            runner.run_once("duplicate-run", AS_OF)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_runs "
                "WHERE radar_run_id='duplicate-run'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots "
                "WHERE radar_run_id='duplicate-run'"
            ).fetchone()[0],
            1,
        )

    def test_in_process_lock_rejects_reentry_before_database_write(self):
        self.seed_classification()
        run_lock = threading.Lock()
        run_lock.acquire()
        try:
            with self.assertRaises(SectorShadowRunInProgressError):
                self.runner(run_lock=run_lock).run_once("locked-run", AS_OF)
        finally:
            run_lock.release()

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_runs "
                "WHERE radar_run_id='locked-run'"
            ).fetchone()[0],
            0,
        )

    def test_historical_as_of_is_rejected_without_backfill_or_quote_fetch(self):
        self.seed_classification()

        with self.assertRaises(ValueError):
            self.runner().run_once(
                "historical-run",
                AS_OF - timedelta(minutes=10),
            )

        self.assertEqual(self.quote_calls, [])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_runs "
                "WHERE radar_run_id='historical-run'"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
