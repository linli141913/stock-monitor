import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from radar.config import load_radar_settings
from radar.contracts import (
    QuoteSnapshot,
    RadarBatchMeta,
    SourceIssue,
    SourceStatus,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class RadarConfigTests(unittest.TestCase):
    def test_radar_and_shadow_are_disabled_by_default(self):
        settings = load_radar_settings({})

        self.assertFalse(settings.enabled)
        self.assertFalse(settings.shadow_mode)
        self.assertFalse(settings.sector_shadow_enabled)
        self.assertFalse(settings.market_shadow_enabled)
        self.assertEqual(settings.stock_scan_interval_seconds, 180)
        self.assertEqual(settings.etf_scan_interval_seconds, 300)
        self.assertEqual(settings.sector_scan_interval_seconds, 180)
        self.assertEqual(settings.market_scan_interval_seconds, 180)
        self.assertEqual(settings.quote_batch_size, 100)
        self.assertEqual(settings.quote_timeout_seconds, 5.0)
        self.assertEqual(settings.minimum_row_coverage, 0.995)
        self.assertEqual(settings.minimum_required_field_coverage, 0.99)
        self.assertEqual(settings.maximum_quote_age_seconds, 90)

    def test_settings_read_only_from_supplied_mapping(self):
        settings = load_radar_settings({
            "RADAR_ENABLED": "true",
            "RADAR_SHADOW_MODE": "1",
            "RADAR_SECTOR_SHADOW_ENABLED": "yes",
            "RADAR_MARKET_SHADOW_ENABLED": "on",
            "RADAR_SCAN_INTERVAL_SECONDS": "240",
            "RADAR_ETF_SCAN_INTERVAL_SECONDS": "360",
            "RADAR_SECTOR_SCAN_INTERVAL_SECONDS": "420",
            "RADAR_MARKET_SCAN_INTERVAL_SECONDS": "480",
            "RADAR_QUOTE_BATCH_SIZE": "50",
        })

        self.assertTrue(settings.enabled)
        self.assertTrue(settings.shadow_mode)
        self.assertTrue(settings.sector_shadow_enabled)
        self.assertTrue(settings.market_shadow_enabled)
        self.assertEqual(settings.stock_scan_interval_seconds, 240)
        self.assertEqual(settings.etf_scan_interval_seconds, 360)
        self.assertEqual(settings.sector_scan_interval_seconds, 420)
        self.assertEqual(settings.market_scan_interval_seconds, 480)
        self.assertEqual(settings.quote_batch_size, 50)

    def test_invalid_setting_is_rejected_instead_of_silently_clamped(self):
        with self.assertRaises(ValueError):
            load_radar_settings({"RADAR_QUOTE_BATCH_SIZE": "101"})

        with self.assertRaises(ValueError):
            load_radar_settings({"RADAR_ENABLED": "sometimes"})

        with self.assertRaises(ValueError):
            load_radar_settings({"RADAR_SECTOR_SCAN_INTERVAL_SECONDS": "59"})

        with self.assertRaises(ValueError):
            load_radar_settings({"RADAR_MARKET_SCAN_INTERVAL_SECONDS": "59"})


class RadarContractTests(unittest.TestCase):
    def test_true_zero_is_distinct_from_missing(self):
        timestamp = datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ)
        quote = QuoteSnapshot(
            symbol="000001",
            name="平安银行",
            sourceTime=timestamp,
            fetchedAt=timestamp + timedelta(seconds=2),
            price=0,
            changePercent=0,
            turnoverAmountSource=0,
            turnoverRatePercent=None,
            volumeRatio=None,
            marketCapSource=0,
        )

        self.assertEqual(quote.price, 0.0)
        self.assertEqual(quote.change_percent, 0.0)
        self.assertEqual(quote.turnover_amount_source, 0.0)
        self.assertEqual(
            quote.missing_fields(),
            ("turnover_rate_percent", "volume_ratio"),
        )

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValidationError):
            QuoteSnapshot(
                symbol="000001",
                name="平安银行",
                sourceTime=datetime(2026, 7, 17, 10, 0),
                fetchedAt=datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ),
                price=10,
            )

    def test_batch_contract_uses_stable_api_aliases(self):
        timestamp = datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ)
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="batch-1",
            source="tencent_finance",
            asOf=timestamp,
            sourceTime=timestamp,
            fetchedAt=timestamp + timedelta(seconds=2),
            expectedCount=2,
            returnedCount=1,
            rowCoverage=0.5,
            requiredFieldCoverage={"price": 1.0},
            issues=[SourceIssue(code="missing_symbols", message="缺少1只证券")],
        )

        dumped = meta.model_dump(by_alias=True)
        self.assertEqual(dumped["radarRunId"], "run-1")
        self.assertEqual(dumped["batchId"], "batch-1")
        self.assertEqual(dumped["asOf"], timestamp)
        self.assertEqual(dumped["expectedCount"], 2)
        self.assertEqual(dumped["rowCoverage"], 0.5)


class RadarSourceHealthTests(unittest.TestCase):
    def build_meta(self, **overrides):
        now = datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ)
        values = {
            "radarRunId": "run-1",
            "batchId": "batch-1",
            "source": "tencent_finance",
            "asOf": now,
            "sourceTime": now - timedelta(seconds=30),
            "fetchedAt": now,
            "expectedCount": 100,
            "returnedCount": 100,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {"price": 1.0, "source_time": 1.0},
            "issues": [],
        }
        values.update(overrides)
        return now, RadarBatchMeta(**values)

    def test_fresh_complete_batch_is_healthy(self):
        now, meta = self.build_meta()

        result = evaluate_source_health(meta, SourceHealthPolicy(), now=now)

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertTrue(result.allows_new_state)
        self.assertEqual(result.reasons, ())

    def test_stale_data_never_allows_new_state(self):
        now, meta = self.build_meta(
            sourceTime=datetime(2026, 7, 17, 9, 58, tzinfo=SHANGHAI_TZ)
        )

        result = evaluate_source_health(meta, SourceHealthPolicy(), now=now)

        self.assertEqual(result.status, SourceStatus.STALE)
        self.assertFalse(result.allows_new_state)
        self.assertIn("source_time_stale", result.reasons)

    def test_coverage_shortfall_is_degraded(self):
        now, meta = self.build_meta(
            returnedCount=99,
            rowCoverage=0.99,
            requiredFieldCoverage={"price": 0.98, "source_time": 1.0},
        )

        result = evaluate_source_health(meta, SourceHealthPolicy(), now=now)

        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertFalse(result.allows_new_state)
        self.assertIn("row_coverage_below_threshold", result.reasons)
        self.assertIn("required_field_coverage_below_threshold:price", result.reasons)

    def test_future_source_time_is_degraded(self):
        now, meta = self.build_meta(
            sourceTime=datetime(2026, 7, 17, 10, 0, 10, tzinfo=SHANGHAI_TZ)
        )

        result = evaluate_source_health(meta, SourceHealthPolicy(), now=now)

        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertFalse(result.allows_new_state)
        self.assertIn("source_time_in_future", result.reasons)

    def test_empty_failed_batch_is_failed(self):
        now, meta = self.build_meta(
            returnedCount=0,
            rowCoverage=0.0,
            requiredFieldCoverage={"price": 0.0},
            sourceTime=None,
            issues=[SourceIssue(code="batch_request_failed", message="上游失败")],
        )

        result = evaluate_source_health(meta, SourceHealthPolicy(), now=now)

        self.assertEqual(result.status, SourceStatus.FAILED)
        self.assertFalse(result.allows_new_state)
        self.assertIn("source_returned_no_rows", result.reasons)


if __name__ == "__main__":
    unittest.main()
