import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from radar.migrations import apply_pending_migrations
from radar.trading_day_validation import (
    RadarValidationBaseline,
    capture_validation_baseline,
    validate_trading_day,
)


UTC = timezone.utc
TARGET_DATE = date(2026, 7, 20)


class RadarTradingDayValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "radar-validation.db"
        self.lock_path = Path(self.temp_dir.name) / "radar-shadow.lock"
        with sqlite3.connect(self.database_path) as connection:
            apply_pending_migrations(connection)

    def tearDown(self):
        self.temp_dir.cleanup()

    def insert_run(
        self,
        prefix,
        as_of,
        *,
        duration=20,
        status="succeeded",
        healthy=True,
    ):
        run_id = f"{prefix}-{as_of.strftime('%Y%m%dT%H%M%S000000Z')}"
        if prefix.endswith("registry"):
            stock_counts = (None, None, None)
            etf_counts = (None, None, None)
        elif prefix.endswith("stock-quotes"):
            stock_counts = (100, 100, 1.0)
            etf_counts = (None, None, None)
        else:
            stock_counts = (None, None, None)
            etf_counts = (20, 20, 1.0)
        completed_at = as_of + timedelta(seconds=duration)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO radar_runs ("
                "radar_run_id, as_of, status, shadow_mode, "
                "expected_stock_count, returned_stock_count, stock_coverage, "
                "expected_etf_count, returned_etf_count, etf_coverage, "
                "started_at, completed_at, created_at"
                ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    as_of.isoformat(),
                    status,
                    *stock_counts,
                    *etf_counts,
                    as_of.isoformat(),
                    completed_at.isoformat(),
                    as_of.isoformat(),
                ),
            )
            if prefix.endswith("registry"):
                sources = (
                    ("security-master", "official_exchange_security_master", 100),
                    ("etf-registry", "official_exchange_etf_registry", 20),
                )
                for suffix, source, count in sources:
                    self._insert_source(
                        connection,
                        run_id,
                        f"{run_id}:{suffix}",
                        source,
                        as_of,
                        count,
                        healthy=healthy,
                        quote=False,
                    )
            else:
                suffix = "stock-quotes" if prefix.endswith("stock-quotes") else "etf-quotes"
                count = 100 if suffix == "stock-quotes" else 20
                self._insert_source(
                    connection,
                    run_id,
                    f"{run_id}:{suffix}",
                    "tencent_finance",
                    as_of,
                    count,
                    healthy=healthy,
                    quote=True,
                )

    @staticmethod
    def _insert_source(
        connection,
        run_id,
        batch_id,
        source,
        as_of,
        count,
        *,
        healthy,
        quote,
    ):
        source_time = as_of - timedelta(seconds=30) if quote and healthy else None
        status = "healthy" if healthy else "degraded"
        field_coverage = (
            {"price": 1.0, "source_time": 1.0}
            if quote and healthy
            else ({"price": 1.0, "source_time": 0.0} if quote else {
                "symbol": 1.0,
                "name": 1.0,
                "listing_date": 1.0,
                "source_type": 1.0,
            })
        )
        issues = {
            "allowsNewState": healthy,
            "healthReasons": [] if healthy else ["source_time_missing"],
            "ageSeconds": 30.0 if quote and healthy else None,
            "sourceIssues": [],
        }
        connection.execute(
            "INSERT INTO radar_source_status ("
            "radar_run_id, batch_id, source, as_of, source_time, fetched_at, "
            "status, expected_count, returned_count, row_coverage, "
            "required_field_coverage_json, issues_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                batch_id,
                source,
                as_of.isoformat(),
                source_time.isoformat() if source_time else None,
                (as_of + timedelta(seconds=5)).isoformat(),
                status,
                count,
                count,
                1.0,
                json.dumps(field_coverage),
                json.dumps(issues),
                as_of.isoformat(),
            ),
        )

    def seed_passing_day(self):
        self.insert_run(
            "radar-shadow-registry",
            datetime(2026, 7, 20, 0, 48, tzinfo=UTC),
        )
        for session_start in (
            datetime(2026, 7, 20, 1, 30, tzinfo=UTC),
            datetime(2026, 7, 20, 5, 0, tzinfo=UTC),
        ):
            for offset in range(0, 7201, 180):
                self.insert_run(
                    "radar-shadow-stock-quotes",
                    session_start + timedelta(seconds=offset),
                )
            for offset in range(30, 7200, 300):
                self.insert_run(
                    "radar-shadow-etf-quotes",
                    session_start + timedelta(seconds=offset),
                )
        self.lock_path.touch(mode=0o600)
        os.chmod(self.lock_path, 0o600)

    def test_capture_baseline_reads_counts_without_changing_database(self):
        modified_before = self.database_path.stat().st_mtime_ns

        baseline = capture_validation_baseline(
            self.database_path,
            captured_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        )

        self.assertEqual(baseline.schema_version, 1)
        self.assertEqual(baseline.counts["radarRuns"], 0)
        self.assertEqual(baseline.counts["radarSourceStatus"], 0)
        self.assertEqual(self.database_path.stat().st_mtime_ns, modified_before)

    def test_realistic_trading_day_passes_all_required_checks(self):
        baseline = capture_validation_baseline(
            self.database_path,
            captured_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        )
        self.seed_passing_day()

        report = validate_trading_day(
            self.database_path,
            TARGET_DATE,
            baseline=baseline,
            lock_path=self.lock_path,
            now=datetime(2026, 7, 20, 7, 10, tzinfo=UTC),
        )

        self.assertEqual(report.overall_status, "passed")
        statuses = {item.key: item.status for item in report.checks}
        self.assertEqual(statuses["trading_day_complete"], "passed")
        self.assertEqual(statuses["registry_daily_once"], "passed")
        self.assertEqual(statuses["stock_quote_cadence"], "passed")
        self.assertEqual(statuses["etf_quote_cadence"], "passed")
        self.assertEqual(statuses["quote_source_contract"], "passed")
        self.assertEqual(statuses["single_instance"], "passed")
        self.assertEqual(statuses["database_increment"], "passed")
        self.assertEqual(statuses["lock_file_contract"], "passed")

    def test_intraday_evidence_remains_provisional_until_market_close(self):
        baseline = capture_validation_baseline(
            self.database_path,
            captured_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        )
        self.insert_run(
            "radar-shadow-registry",
            datetime(2026, 7, 20, 0, 48, tzinfo=UTC),
        )
        for minute in (30, 33, 36):
            self.insert_run(
                "radar-shadow-stock-quotes",
                datetime(2026, 7, 20, 1, minute, tzinfo=UTC),
            )
        for minute in (30, 35):
            self.insert_run(
                "radar-shadow-etf-quotes",
                datetime(2026, 7, 20, 1, minute, 30, tzinfo=UTC),
            )
        self.lock_path.touch(mode=0o600)
        os.chmod(self.lock_path, 0o600)

        report = validate_trading_day(
            self.database_path,
            TARGET_DATE,
            baseline=baseline,
            lock_path=self.lock_path,
            now=datetime(2026, 7, 20, 1, 38, tzinfo=UTC),
        )

        statuses = {item.key: item.status for item in report.checks}
        self.assertEqual(report.overall_status, "pending")
        self.assertEqual(statuses["trading_day_complete"], "pending")

    def test_overfrequent_and_degraded_quotes_fail_without_fake_success(self):
        baseline = capture_validation_baseline(
            self.database_path,
            captured_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        )
        self.insert_run(
            "radar-shadow-registry",
            datetime(2026, 7, 20, 0, 48, tzinfo=UTC),
        )
        for second in (0, 100, 280):
            self.insert_run(
                "radar-shadow-stock-quotes",
                datetime(2026, 7, 20, 1, 30, tzinfo=UTC) + timedelta(seconds=second),
                healthy=second != 100,
            )
        self.lock_path.touch(mode=0o600)
        os.chmod(self.lock_path, 0o600)

        report = validate_trading_day(
            self.database_path,
            TARGET_DATE,
            baseline=baseline,
            lock_path=self.lock_path,
            now=datetime(2026, 7, 20, 1, 45, tzinfo=UTC),
        )

        self.assertEqual(report.overall_status, "failed")
        statuses = {item.key: item.status for item in report.checks}
        self.assertEqual(statuses["stock_quote_cadence"], "failed")
        self.assertEqual(statuses["quote_source_contract"], "failed")
        self.assertEqual(statuses["etf_quote_cadence"], "pending")

    def test_future_day_stays_pending_instead_of_passing_empty_data(self):
        baseline = RadarValidationBaseline(
            schema_version=1,
            captured_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
            counts={
                "radarRuns": 0,
                "radarSourceStatus": 0,
                "securityMasterHistory": 0,
                "etfProductRegistry": 0,
            },
        )

        report = validate_trading_day(
            self.database_path,
            TARGET_DATE,
            baseline=baseline,
            lock_path=self.lock_path,
            now=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        )

        self.assertEqual(report.overall_status, "pending")
        self.assertTrue(all(item.status != "passed" for item in report.checks if item.key in {
            "registry_daily_once",
            "stock_quote_cadence",
            "etf_quote_cadence",
            "quote_source_contract",
            "database_increment",
        }))


if __name__ == "__main__":
    unittest.main()
