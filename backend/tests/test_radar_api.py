import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import market_calendar
from radar.api import open_radar_read_connection
from radar.config import RadarSettings
from radar.read_service import (
    MARKET_RUN_PREFIX,
    SECTOR_RUN_PREFIX,
    RadarReadService,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)


def market_row(*, age_seconds: int = 30):
    source_time = NOW - timedelta(seconds=age_seconds)
    return {
        "radarRunId": "market-success",
        "asOf": source_time,
        "sourceTime": source_time,
        "fetchedAt": source_time + timedelta(seconds=2),
        "indexCompleteness": {
            "expectedCount": 4,
            "returnedCount": 4,
            "validCount": 4,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {
                "price": 1.0,
                "change_percent": 1.0,
                "source_time": 1.0,
            },
            "isComplete": True,
            "reasons": (),
        },
        "breadth": {
            "advancers": 3201,
            "decliners": 2058,
            "flat": 271,
            "unavailable": 0,
            "completeness": {
                "expectedCount": 5530,
                "returnedCount": 5530,
                "validCount": 5530,
                "rowCoverage": 1.0,
                "requiredFieldCoverage": {
                    "change_percent": 1.0,
                    "source_time": 1.0,
                },
                "isComplete": True,
                "reasons": (),
            },
        },
        "turnover": {
            "rawValue": 0,
            "contributingCount": 5530,
            "unitStatus": "unverified",
            "completeness": {
                "expectedCount": 5530,
                "returnedCount": 5530,
                "validCount": 5530,
                "rowCoverage": 1.0,
                "requiredFieldCoverage": {"turnover_amount_source": 1.0},
                "isComplete": True,
                "reasons": (),
            },
            "reasons": (),
        },
        "excludedEtfCount": 1895,
        "duplicateSymbolCount": 0,
        "unknownSymbolCount": 0,
        "evidenceSummary": {},
        "indices": (
            {
                "indexKey": "sse_composite",
                "symbol": "000001",
                "name": "上证指数",
                "exchange": "sse",
                "sourceSymbol": "sh000001",
                "sourceTime": source_time,
                "fetchedAt": source_time + timedelta(seconds=1),
                "price": 3500.0,
                "changePercent": 0.0,
                "source": "tencent_finance",
                "missingFields": (),
            },
        ),
    }


def sector_row(
    code: str,
    name: str,
    *,
    equal_return: Optional[float],
    shadow_usable: bool = True,
):
    source_time = NOW - timedelta(seconds=40)
    return {
        "radarRunId": "sector-success",
        "divisionCode": code,
        "divisionName": name,
        "categoryCode": "C",
        "categoryName": "制造业",
        "asOf": source_time,
        "sourceTime": source_time,
        "fetchedAt": source_time + timedelta(seconds=3),
        "classificationMappingCoverage": 1.0,
        "mappedConstituentCount": 100,
        "unconfirmedStockCount": 0,
        "expectedCount": 100,
        "returnedCount": 100,
        "freshCount": 100,
        "rowCoverage": 1.0,
        "requiredFieldCoverage": {"change_percent": 1.0},
        "isComplete": shadow_usable,
        "equalReturn": equal_return,
        "advancers": 60,
        "decliners": 30,
        "flat": 10,
        "unavailable": 0,
        "upRatio": 0.6 if equal_return is not None else None,
        "marketCapUnitStatus": "unverified",
        "turnoverUnitStatus": "unverified",
        "shadowUsable": shadow_usable,
        "reasons": () if shadow_usable else ("metric_missing",),
    }


class FakeRepository:
    def __init__(
        self,
        *,
        market=None,
        sectors=(),
        market_attempt=None,
        sector_attempt=None,
    ):
        self.market = market
        self.sectors = tuple(sectors)
        self.attempts = {
            MARKET_RUN_PREFIX: market_attempt,
            SECTOR_RUN_PREFIX: sector_attempt,
        }

    def get_latest_market_feature_row(self):
        return self.market

    def list_latest_sector_feature_rows(self):
        return self.sectors

    def get_latest_run_row(self, prefix):
        return self.attempts[prefix]

    def list_source_status_rows(self, radar_run_id):
        return ()


class BrokenMarketRepository(FakeRepository):
    def get_latest_market_feature_row(self):
        raise sqlite3.OperationalError("read failed")


def run_row(
    prefix: str,
    *,
    status: str,
    error_code: Optional[str],
    age_seconds: int,
):
    as_of = NOW - timedelta(seconds=age_seconds)
    return {
        "radarRunId": f"{prefix}test",
        "asOf": as_of,
        "status": status,
        "shadowMode": True,
        "ruleVersionId": None,
        "startedAt": as_of,
        "completedAt": as_of + timedelta(seconds=5),
        "errorCode": error_code,
    }


def market_status_provider(code="trading", label="交易中"):
    return lambda market, now: (
        market_calendar.MarketStatus(code, label),
        market_calendar.CalendarDay("full", "https://example.com", now.isoformat()),
    )


class RadarReadServiceTests(unittest.TestCase):
    def settings(self):
        return RadarSettings(
            enabled=True,
            shadow_mode=True,
            sector_shadow_enabled=True,
            market_shadow_enabled=True,
            sector_scan_interval_seconds=180,
            market_scan_interval_seconds=180,
        )

    def service(self, repository, *, market_code="trading"):
        return RadarReadService(
            repository,
            settings=self.settings(),
            clock=lambda: NOW,
            market_status_provider=market_status_provider(
                market_code,
                "交易中" if market_code == "trading" else "已收市",
            ),
        )

    def test_available_payload_preserves_true_zero_and_hides_unverified_amount(self):
        repository = FakeRepository(
            market=market_row(),
            sectors=(
                sector_row("39", "电子设备制造业", equal_return=1.84),
                sector_row("35", "专用设备制造业", equal_return=0.89),
            ),
            market_attempt=run_row(
                MARKET_RUN_PREFIX,
                status="degraded",
                error_code="market_features_shadow_unit_unverified",
                age_seconds=35,
            ),
            sector_attempt=run_row(
                SECTOR_RUN_PREFIX,
                status="degraded",
                error_code="sector_features_shadow_partial",
                age_seconds=45,
            ),
        )

        payload = self.service(repository).build_overview().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["modules"]["market"]["state"], "available")
        self.assertEqual(payload["modules"]["market"]["quality"], "partial")
        self.assertEqual(payload["modules"]["market"]["data"]["breadth"]["unavailable"], 0)
        self.assertNotIn(
            "rawValue",
            payload["modules"]["market"]["data"]["turnover"],
        )
        self.assertFalse(
            payload["modules"]["market"]["data"]["turnover"]["displayAllowed"]
        )
        self.assertEqual(payload["modules"]["sectors"]["summary"]["totalCount"], 2)
        self.assertEqual(
            payload["modules"]["sectors"]["items"][0]["divisionName"],
            "电子设备制造业",
        )
        self.assertEqual(payload["modules"]["etf"]["state"], "not_enabled")
        self.assertIsNone(payload["modules"]["etf"]["data"])

    def test_trading_snapshot_older_than_two_cycles_plus_grace_is_stale(self):
        repository = FakeRepository(
            market=market_row(age_seconds=391),
            market_attempt=run_row(
                MARKET_RUN_PREFIX,
                status="degraded",
                error_code="market_features_shadow_unit_unverified",
                age_seconds=391,
            ),
        )

        payload = self.service(repository).build_overview().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["modules"]["market"]["state"], "stale")
        self.assertEqual(
            payload["modules"]["market"]["freshness"]["staleAfterSeconds"],
            390,
        )
        self.assertIsNotNone(payload["modules"]["market"]["data"])

    def test_closed_market_does_not_age_a_valid_snapshot_into_stale(self):
        repository = FakeRepository(
            market=market_row(age_seconds=3600),
            market_attempt=run_row(
                MARKET_RUN_PREFIX,
                status="degraded",
                error_code="market_features_shadow_unit_unverified",
                age_seconds=3600,
            ),
        )

        payload = self.service(
            repository,
            market_code="closed",
        ).build_overview().model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["modules"]["market"]["state"], "available")
        self.assertFalse(payload["modules"]["market"]["freshness"]["isStale"])

    def test_successful_sector_run_without_items_is_true_empty_not_failure(self):
        repository = FakeRepository(
            sector_attempt=run_row(
                SECTOR_RUN_PREFIX,
                status="succeeded",
                error_code=None,
                age_seconds=30,
            ),
        )

        payload = self.service(repository).build_sectors().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["module"]["state"], "empty")
        self.assertEqual(payload["module"]["items"], [])

    def test_failed_latest_attempt_keeps_last_success_as_explicit_fallback(self):
        repository = FakeRepository(
            market=market_row(age_seconds=120),
            market_attempt=run_row(
                MARKET_RUN_PREFIX,
                status="failed",
                error_code="source_request_failed",
                age_seconds=30,
            ),
        )

        payload = self.service(repository).build_overview().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["modules"]["market"]["state"], "failed")
        self.assertTrue(payload["modules"]["market"]["usingLastSuccess"])
        self.assertIsNotNone(payload["modules"]["market"]["data"])
        self.assertEqual(
            payload["modules"]["market"]["lastAttempt"]["errorCode"],
            "source_request_failed",
        )

    def test_market_read_failure_does_not_hide_independent_sector_snapshot(self):
        repository = BrokenMarketRepository(
            sectors=(sector_row("39", "电子设备制造业", equal_return=1.84),),
            sector_attempt=run_row(
                SECTOR_RUN_PREFIX,
                status="degraded",
                error_code="sector_features_shadow_partial",
                age_seconds=45,
            ),
        )

        payload = self.service(repository).build_overview().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["modules"]["market"]["state"], "failed")
        self.assertIsNone(payload["modules"]["market"]["data"])
        self.assertEqual(payload["modules"]["sectors"]["state"], "available")


class RadarReadOnlyConnectionTests(unittest.TestCase):
    def test_router_exposes_get_only_endpoints(self):
        from radar.api import router

        routes = {
            route.path: route.methods
            for route in router.routes
        }
        self.assertEqual(routes["/api/radar/overview"], {"GET"})
        self.assertEqual(routes["/api/radar/sectors"], {"GET"})

    def test_connection_is_query_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "radar.db"
            with sqlite3.connect(database_path) as connection:
                connection.execute("CREATE TABLE sample (id INTEGER)")
                connection.commit()

            with open_radar_read_connection(database_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0],
                    0,
                )
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO sample (id) VALUES (1)")


if __name__ == "__main__":
    unittest.main()
