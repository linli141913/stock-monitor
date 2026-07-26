import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

from radar.config import RadarSettings
from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)
from radar.etf_product_master_runner import (
    PRODUCT_MASTER_SOURCE,
    PRODUCT_SOURCE_CONTRACTS,
    EtfProductMasterDisabledError,
    EtfProductMasterExecutionError,
    EtfProductMasterRunner,
    _profile_id,
)
from radar.etf_repository import EtfRepository
from radar.migrations import (
    STAGE5_RADAR_MIGRATIONS,
    apply_pending_migrations,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
FETCHED_AT = AS_OF + timedelta(seconds=2)
APPLIED_AT = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
COVERAGE = {
    "official_name": 1.0,
    "product_type_known": 1.0,
    "listing_date": 1.0,
    "etf_asset_class_confirmed": 0.0,
    "etf_management_style_confirmed": 0.0,
}


class EtfProductMasterRunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE5_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.repository = EtfRepository(
            self.connection,
            clock=lambda: FETCHED_AT,
        )
        self.settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=True,
        )

    def tearDown(self):
        self.connection.close()

    @staticmethod
    def product(
        symbol="159915",
        *,
        exchange="szse",
        official_name="创业板ETF",
        fetched_at=FETCHED_AT,
    ):
        source = {
            "sse": "sse_official_fund_list",
            "szse": "szse_official_fund_list",
        }[exchange]
        return EtfProductMasterRecord(
            symbol=symbol,
            officialName=official_name,
            exchange=exchange,
            productType="etf",
            managementStyle=EtfManagementStyle.UNKNOWN,
            assetClass=EtfAssetClass.UNKNOWN,
            sourceCategoryName="ETF",
            sourceInvestmentType="股票基金",
            listingDate=date(2011, 12, 9),
            manager="测试基金管理人",
            classificationMappingVersion="test-v1",
            classificationReasons=(
                "management_style_unverified",
                "asset_class_unverified",
            ),
            source=source,
            fetchedAt=fetched_at,
            sourceFields={"fixture": True},
        )

    @classmethod
    def batch(
        cls,
        run_id,
        *,
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
        items=None,
        expected_count=None,
        issues=None,
    ):
        records = (
            [
                cls.product(fetched_at=fetched_at),
                cls.product(
                    "510300",
                    exchange="sse",
                    official_name="沪深300ETF",
                    fetched_at=fetched_at,
                ),
            ]
            if items is None
            else list(items)
        )
        expected = len(records) if expected_count is None else expected_count
        row_coverage = (
            len(records) / expected
            if expected is not None and expected > 0
            else (0.0 if expected == 0 else None)
        )
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=run_id,
                batchId=f"{run_id}:etf-product-master",
                source=PRODUCT_MASTER_SOURCE,
                asOf=as_of,
                sourceTime=None,
                fetchedAt=fetched_at,
                expectedCount=expected,
                returnedCount=len(records),
                rowCoverage=row_coverage,
                requiredFieldCoverage=COVERAGE,
                issues=issues or [],
            ),
            items=records,
        )

    def runner(self, fetcher, settings=None):
        return EtfProductMasterRunner(
            self.repository,
            settings=settings or self.settings,
            fetcher=fetcher,
            clock=lambda: FETCHED_AT,
        )

    def test_disabled_returns_before_source_request_or_write(self):
        fetcher = Mock()
        settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=False,
        )

        with self.assertRaises(EtfProductMasterDisabledError):
            self.runner(fetcher, settings).run_once("product-run", AS_OF)

        fetcher.assert_not_called()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_master_runs"
            ).fetchone()[0],
            0,
        )

    def test_complete_batch_is_first_observed_only_and_not_historical(self):
        fetcher = Mock(side_effect=lambda run_id, batch_id, as_of: self.batch(
            run_id,
            as_of=as_of,
            fetched_at=FETCHED_AT,
        ))

        result = self.runner(fetcher).run_once("product-run", AS_OF)

        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.gate_passed)
        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(result.unchanged_count, 0)
        self.assertIsNone(
            self.repository.get_product_profile_as_of(
                "159915",
                FETCHED_AT - timedelta(microseconds=1),
            )
        )
        profile = self.repository.get_product_profile_as_of(
            "159915",
            FETCHED_AT,
        )
        self.assertEqual(profile["versionTimeKind"], "first_observed")
        self.assertEqual(profile["firstObservedAt"], FETCHED_AT)
        self.assertIsNone(profile["officialEffectiveFrom"])
        self.assertFalse(profile["historicalReplayReady"])
        self.assertEqual(
            self.repository.latest_product_master_attempt_at(),
            FETCHED_AT,
        )

    def test_same_content_next_day_is_audited_without_new_version(self):
        first_fetcher = Mock(return_value=self.batch("product-run-1"))
        self.runner(first_fetcher).run_once("product-run-1", AS_OF)
        next_as_of = AS_OF + timedelta(days=1)
        next_fetched_at = FETCHED_AT + timedelta(days=1)
        next_fetcher = Mock(return_value=self.batch(
            "product-run-2",
            as_of=next_as_of,
            fetched_at=next_fetched_at,
        ))

        result = self.runner(next_fetcher).run_once(
            "product-run-2",
            next_as_of,
        )

        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.unchanged_count, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_profiles"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.repository.latest_product_master_attempt_at(),
            next_fetched_at,
        )

    def test_changed_content_closes_previous_first_observed_version(self):
        self.runner(Mock(return_value=self.batch("product-run-1"))).run_once(
            "product-run-1",
            AS_OF,
        )
        next_as_of = AS_OF + timedelta(days=1)
        next_fetched_at = FETCHED_AT + timedelta(days=1)
        changed_items = [
            self.product(
                official_name="创业板ETF新简称",
                fetched_at=next_fetched_at,
            ),
            self.product(
                "510300",
                exchange="sse",
                official_name="沪深300ETF",
                fetched_at=next_fetched_at,
            ),
        ]
        changed_batch = self.batch(
            "product-run-2",
            as_of=next_as_of,
            fetched_at=next_fetched_at,
            items=changed_items,
        )

        result = self.runner(Mock(return_value=changed_batch)).run_once(
            "product-run-2",
            next_as_of,
        )

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.unchanged_count, 1)
        before = self.repository.get_product_profile_as_of(
            "159915",
            next_fetched_at - timedelta(microseconds=1),
        )
        after = self.repository.get_product_profile_as_of(
            "159915",
            next_fetched_at,
        )
        self.assertEqual(before["product"].official_name, "创业板ETF")
        self.assertEqual(
            after["product"].official_name,
            "创业板ETF新简称",
        )
        self.assertFalse(after["historicalReplayReady"])

    def test_incomplete_or_failed_batch_only_writes_attempt_audit(self):
        issue = SourceIssue(
            code="invalid_symbol",
            source="sse_product_list",
            message="fixture invalid symbol",
        )
        degraded = self.batch(
            "product-run-degraded",
            items=[self.product()],
            expected_count=2,
            issues=[issue],
        )

        result = self.runner(Mock(return_value=degraded)).run_once(
            "product-run-degraded",
            AS_OF,
        )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_profiles"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM radar_etf_product_master_runs"
            ).fetchone()[0],
            "degraded",
        )

        failed_at = FETCHED_AT + timedelta(days=1)
        failed_as_of = AS_OF + timedelta(days=1)
        failed = self.batch(
            "product-run-failed",
            as_of=failed_as_of,
            fetched_at=failed_at,
            items=[],
            expected_count=None,
            issues=[SourceIssue(
                code="source_request_failed",
                source="szse_product_list",
                message="fixture source failure",
            )],
        )
        result = self.runner(Mock(return_value=failed)).run_once(
            "product-run-failed",
            failed_as_of,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_master_runs"
            ).fetchone()[0],
            2,
        )

    def test_batch_conflict_rolls_back_all_new_profiles_and_audit(self):
        second = self.product(
            "510300",
            exchange="sse",
            official_name="沪深300ETF",
        )
        second_contract = PRODUCT_SOURCE_CONTRACTS["sse"]
        generated_id = _profile_id(
            second,
            source_contract_id=second_contract,
            first_observed_at=FETCHED_AT,
        )
        conflicting = second.model_copy(update={
            "official_name": "预置冲突名称",
            "fetched_at": FETCHED_AT - timedelta(days=1),
        })
        self.repository.upsert_product_profile(
            conflicting,
            profile_id=generated_id,
            source_contract_id=second_contract,
            first_observed_at=FETCHED_AT - timedelta(days=1),
        )
        batch = self.batch("product-run-conflict")

        with self.assertRaises(EtfProductMasterExecutionError):
            self.runner(Mock(return_value=batch)).run_once(
                "product-run-conflict",
                AS_OF,
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_profiles"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_master_runs"
            ).fetchone()[0],
            0,
        )

    def test_batch_identity_and_record_source_mismatch_are_rejected(self):
        wrong_batch = self.batch("other-run")
        with self.assertRaises(EtfProductMasterExecutionError):
            self.runner(Mock(return_value=wrong_batch)).run_once(
                "product-run",
                AS_OF,
            )

        wrong_source = self.product().model_copy(update={
            "source": "sse_official_fund_list",
        })
        batch = self.batch("product-run", items=[wrong_source])
        with self.assertRaises(EtfProductMasterExecutionError):
            self.runner(Mock(return_value=batch)).run_once(
                "product-run",
                AS_OF,
            )


if __name__ == "__main__":
    unittest.main()
