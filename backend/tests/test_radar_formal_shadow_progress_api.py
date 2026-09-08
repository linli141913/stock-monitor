import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.formal_shadow_ledger import build_shadow_ledger
from radar.formal_shadow_ledger_store import save_formal_shadow_ledger


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)


class _Calendar:
    def is_trading_day(self, value):
        return value.weekday() < 5


def _observation(module, day, suffix):
    observed_at = datetime(2026, 9, day, 8, 0, tzinfo=UTC)
    return {
        "module": module,
        "runId": f"{module}-{suffix}",
        "observedAt": observed_at,
        "sourceTime": observed_at - timedelta(minutes=2),
        "fetchedAt": observed_at - timedelta(minutes=1),
        "coverage": 1.0,
        "missingCount": 0,
        "failedCount": 0,
        "staleCount": 0,
        "lockState": "acquired",
        "durationMs": 10,
        "sourceReady": True,
        "evidenceSha256": suffix * 64,
    }


def _ledger():
    return build_shadow_ledger(
        (
            _observation("trendRotation", 7, "a"),
            _observation("trendRotation", 8, "b"),
            _observation("etfObservation", 7, "c"),
            _observation("etfObservation", 8, "d"),
            _observation("leaderObservation", 8, "e"),
        ),
        calendar_provider=_Calendar(),
    )


class RadarFormalShadowProgressApiTests(unittest.TestCase):
    def setUp(self):
        from radar.api import router

        self.temp_dir = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.store_dir = Path(self.temp_dir.name) / "shadow-ledger"
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.clock_patch = patch("radar.api._utc_now", return_value=NOW)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.temp_dir.cleanup()

    def get_without_sqlite(self, store_path):
        with (
            patch(
                "radar.api._formal_shadow_ledger_store_path",
                return_value=store_path,
                create=True,
            ),
            patch(
                "radar.api._database_path",
                side_effect=AssertionError("shadow progress must not resolve SQLite"),
            ),
            patch(
                "radar.api.open_radar_read_connection",
                side_effect=AssertionError("shadow progress must not open SQLite"),
            ),
        ):
            return self.client.get("/api/radar/formal-shadow-progress")

    def test_available_verified_ledger_returns_exact_real_progress(self):
        stored = save_formal_shadow_ledger(_ledger(), self.store_dir)

        response = self.get_without_sqlite(self.store_dir)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        payload = response.json()
        self.assertEqual(payload["contractVersion"], "radar-formal-shadow-progress-v1")
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["ledgerSha256"], stored.content_sha256)
        self.assertEqual(payload["reasonCodes"], [])
        by_module = {item["module"]: item for item in payload["modules"]}
        self.assertEqual(
            {
                module: (
                    item["observedTradingDays"],
                    item["requiredTradingDays"],
                    item["latestReadyStreak"],
                    item["latestReadyTradingDate"],
                )
                for module, item in by_module.items()
            },
            {
                "trendRotation": (2, 20, 2, "2026-09-08"),
                "etfObservation": (2, 5, 2, "2026-09-08"),
                "leaderObservation": (1, 20, 1, "2026-09-08"),
            },
        )
        self.assertNotIn("observations", payload)
        self.assertNotIn("/private/", response.text)

    def test_unconfigured_store_returns_missing_without_numeric_modules(self):
        response = self.get_without_sqlite(None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "contractVersion": "radar-formal-shadow-progress-v1",
            "checkedAt": NOW.isoformat().replace("+00:00", "Z"),
            "state": "missing",
            "modules": [],
            "ledgerSha256": None,
            "reasonCodes": ["formal_shadow_progress_store_unconfigured"],
        })

    def test_tampered_ledger_fails_closed_without_path_or_numeric_modules(self):
        stored = save_formal_shadow_ledger(_ledger(), self.store_dir)
        report_path = self.store_dir / stored.relative_path
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["readyTradingDaysByModule"]["leaderObservation"] = 20
        report_path.write_text(json.dumps(payload), encoding="utf-8")

        response = self.get_without_sqlite(self.store_dir)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["modules"], [])
        self.assertIsNone(payload["ledgerSha256"])
        self.assertEqual(
            payload["reasonCodes"],
            ["formal_shadow_ledger_hash_mismatch"],
        )
        self.assertNotIn("/private/", response.text)

    def test_store_configuration_accepts_only_explicit_private_tmp_paths(self):
        from radar.api import _formal_shadow_ledger_store_path

        with patch.dict(
            os.environ,
            {"RADAR_FORMAL_SHADOW_LEDGER_DIR": str(self.store_dir)},
            clear=False,
        ):
            self.assertEqual(
                _formal_shadow_ledger_store_path(),
                self.store_dir.resolve(strict=False),
            )
        for invalid in ("relative/store", "/var/tmp/not-the-shadow-store"):
            with self.subTest(invalid=invalid), patch.dict(
                os.environ,
                {"RADAR_FORMAL_SHADOW_LEDGER_DIR": invalid},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_shadow_progress_store_unverified",
                ):
                    _formal_shadow_ledger_store_path()


if __name__ == "__main__":
    unittest.main()
