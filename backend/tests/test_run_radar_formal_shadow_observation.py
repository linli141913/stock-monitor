import contextlib
import hashlib
import io
import json
import os
import socket
import tempfile
import unittest
import urllib.request
import requests
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


UTC = timezone.utc
SHA = "a" * 64


def _calendar_html():
    return (
        "<strong>2026年休市安排</strong><table>"
        "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
        "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
        "</table>"
    ).encode()


class RunRadarFormalShadowObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.store = self.root / "store"
        self.input.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def observation(self, module, day=date(2026, 9, 4), *, evidence_sha=SHA):
        from radar.formal_shadow_ledger import FormalShadowObservation

        observed = datetime.combine(day, datetime.min.time(), UTC).replace(hour=7)
        return FormalShadowObservation(
            module=module,
            runId=f"{module}-{day.isoformat()}",
            observedAt=observed,
            sourceTime=observed - timedelta(minutes=2),
            fetchedAt=observed - timedelta(minutes=1),
            coverage=1,
            missingCount=0,
            failedCount=0,
            staleCount=0,
            lockState="acquired",
            durationMs=3,
            evidenceSha256=evidence_sha,
        )

    def write_inputs(self, module, day=date(2026, 9, 4)):
        from run_radar_formal_shadow_observation import (
            CALENDAR_DOCUMENT_FILENAME,
            CALENDAR_INPUT_FILENAME,
            COLLECTION_POLICY_FILENAME,
            ETF_ADMISSION_FILENAME,
            RECEIPT_FILENAME,
            SOURCE_ARTIFACT_FILENAME,
            TREND_SUPPORTING_FILENAME,
        )

        raw = _calendar_html()
        observed = datetime.combine(day, datetime.min.time(), timezone(timedelta(hours=8))).replace(hour=15)
        calendar = {
            "contractVersion": "radar-formal-shadow-calendar-input-v1",
            "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
            "year": day.year,
            "fetchedAt": observed.isoformat(),
            "observedThrough": day.isoformat(),
            "sourceDocumentSha256": hashlib.sha256(raw).hexdigest(),
        }
        receipt = {
            "contractId": "radar-formal-shadow-run-receipt-v1",
            "module": module,
            "runId": f"{module}-{day.isoformat()}",
            "asOf": observed.isoformat(),
            "sourceTime": (observed - timedelta(minutes=2)).isoformat(),
            "fetchedAt": (observed - timedelta(minutes=1)).isoformat(),
            "observedAt": observed.isoformat(),
            "sourceContractId": "placeholder-v1",
            "sourceArtifactSha256": SHA,
            "coverageScope": "full_snapshot",
            "expectedCount": 1,
            "observedCount": 1,
            "coverage": 1,
            "missingCount": 0,
            "failedCount": 0,
            "staleCount": 0,
            "lockState": "acquired",
            "durationMs": 3,
        }
        policy = {
            "contractId": "radar-formal-shadow-collection-policy-v1",
            "maximumSourceAgeSecondsByModule": {
                "trendRotation": 300,
                "etfObservation": 300,
                "leaderObservation": 300,
            },
            "maximumCollectionDelaySecondsByModule": {
                "trendRotation": 300,
                "etfObservation": 300,
                "leaderObservation": 300,
            },
        }
        (self.input / RECEIPT_FILENAME).write_text(json.dumps(receipt), encoding="utf-8")
        (self.input / SOURCE_ARTIFACT_FILENAME).write_text("{}", encoding="utf-8")
        (self.input / COLLECTION_POLICY_FILENAME).write_text(json.dumps(policy), encoding="utf-8")
        (self.input / CALENDAR_INPUT_FILENAME).write_text(json.dumps(calendar), encoding="utf-8")
        (self.input / CALENDAR_DOCUMENT_FILENAME).write_bytes(raw)
        if module == "trendRotation":
            (self.input / TREND_SUPPORTING_FILENAME).write_text("{}", encoding="utf-8")
        if module == "etfObservation":
            (self.input / ETF_ADMISSION_FILENAME).write_text("{}", encoding="utf-8")
        return observed

    def test_three_modules_merge_independently_and_same_content_is_unchanged(self):
        from radar.formal_shadow_ledger_store import load_latest_formal_shadow_ledger
        from run_radar_formal_shadow_observation import run

        days = {
            "trendRotation": date(2026, 9, 2),
            "leaderObservation": date(2026, 9, 3),
            "etfObservation": date(2026, 9, 4),
        }
        for module, day in days.items():
            for child in tuple(self.input.iterdir()):
                child.unlink()
            evaluated = self.write_inputs(module, day)
            with patch(
                "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
                return_value=self.observation(module, day),
            ):
                result = run(module, self.input, self.store, evaluated_at=evaluated)
            self.assertEqual(result.status, "available")

        loaded = load_latest_formal_shadow_ledger(self.store)
        self.assertEqual(loaded.status, "available")
        self.assertEqual(
            {(item.module, item.observed_date) for item in loaded.ledger.observations},
            {(module, day) for module, day in days.items()},
        )
        with patch(
            "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
            return_value=self.observation("etfObservation", days["etfObservation"]),
        ):
            unchanged = run("etfObservation", self.input, self.store, evaluated_at=evaluated)
        self.assertEqual(unchanged.status, "unchanged")

    def test_same_module_same_shanghai_day_different_content_conflicts(self):
        from run_radar_formal_shadow_observation import run

        evaluated = self.write_inputs("trendRotation")
        with patch(
            "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
            return_value=self.observation("trendRotation"),
        ):
            run("trendRotation", self.input, self.store, evaluated_at=evaluated)
        with patch(
            "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
            return_value=self.observation("trendRotation", evidence_sha="b" * 64),
        ):
            with self.assertRaisesRegex(ValueError, "shadow_observation_identity_conflict"):
                run("trendRotation", self.input, self.store, evaluated_at=evaluated)

    def test_trend_and_etf_require_their_binding_files(self):
        from run_radar_formal_shadow_observation import (
            ETF_ADMISSION_FILENAME,
            TREND_SUPPORTING_FILENAME,
            run,
        )

        for module, filename, reason in (
            ("trendRotation", TREND_SUPPORTING_FILENAME, "formal_shadow_input_missing"),
            ("etfObservation", ETF_ADMISSION_FILENAME, "formal_shadow_input_missing"),
        ):
            with self.subTest(module=module):
                for child in tuple(self.input.iterdir()):
                    child.unlink()
                evaluated = self.write_inputs(module)
                (self.input / filename).unlink()
                with self.assertRaisesRegex(ValueError, reason):
                    run(module, self.input, self.store, evaluated_at=evaluated)

    def test_calendar_input_limit_matches_frozen_ledger_limit(self):
        from run_radar_formal_shadow_observation import (
            CALENDAR_DOCUMENT_FILENAME,
            run,
        )

        evaluated = self.write_inputs("leaderObservation")
        (self.input / CALENDAR_DOCUMENT_FILENAME).write_bytes(
            b"x" * (2 * 1024 * 1024 + 1)
        )
        with self.assertRaisesRegex(ValueError, "formal_shadow_input_too_large"):
            run(
                "leaderObservation",
                self.input,
                self.store,
                evaluated_at=evaluated,
            )

    def test_calendar_cutoff_must_match_observation_shanghai_date(self):
        from run_radar_formal_shadow_observation import CALENDAR_INPUT_FILENAME, run

        evaluated = self.write_inputs("leaderObservation", date(2026, 9, 4))
        with patch(
            "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
            return_value=self.observation("leaderObservation", date(2026, 9, 3)),
        ):
            with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_observation_date_mismatch"):
                run("leaderObservation", self.input, self.store, evaluated_at=evaluated)

        envelope_path = self.input / CALENDAR_INPUT_FILENAME
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        envelope["fetchedAt"] = "2026-09-04T18:00:00+08:00"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_after_evaluated_at"):
            run("leaderObservation", self.input, self.store, evaluated_at=evaluated)

    def test_weekend_observation_is_preserved_as_non_trading_and_never_counted(self):
        from radar.formal_shadow_ledger_store import load_latest_formal_shadow_ledger
        from run_radar_formal_shadow_observation import run

        saturday = date(2026, 9, 5)
        evaluated = self.write_inputs("leaderObservation", saturday)
        with patch(
            "run_radar_formal_shadow_observation.adapt_formal_shadow_observation",
            return_value=self.observation("leaderObservation", saturday),
        ):
            run("leaderObservation", self.input, self.store, evaluated_at=evaluated)
        ledger = load_latest_formal_shadow_ledger(self.store, now=evaluated).ledger
        self.assertEqual(ledger.observations[0].observation_status, "non_trading_day")
        self.assertEqual(ledger.ready_trading_days_by_module["leaderObservation"], 0)

    def test_no_sqlite_network_environment_or_sensitive_path_output(self):
        from run_radar_formal_shadow_observation import main

        evaluated = self.write_inputs("leaderObservation")
        stdout = io.StringIO()
        with (
            patch("run_radar_formal_shadow_observation.adapt_formal_shadow_observation", return_value=self.observation("leaderObservation")),
            patch("sqlite3.connect", side_effect=AssertionError("no SQLite")) as db,
            patch("socket.create_connection", side_effect=AssertionError("no network")) as network,
            patch("urllib.request.urlopen", side_effect=AssertionError("no network")) as urlopen,
            patch("requests.sessions.Session.request", side_effect=AssertionError("no network")) as requests_call,
            patch.dict(os.environ, {"DATABASE_URL": "secret"}),
            contextlib.redirect_stdout(stdout),
        ):
            code = main([
                "--module", "leaderObservation",
                "--input-dir", str(self.input),
                "--ledger-dir", str(self.store),
                "--evaluated-at", evaluated.isoformat(),
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "available")
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.assertNotIn("secret", stdout.getvalue())
        db.assert_not_called(); network.assert_not_called(); urlopen.assert_not_called(); requests_call.assert_not_called()

    def test_main_reports_stable_failed_json_for_bad_time_json_and_module(self):
        from run_radar_formal_shadow_observation import main

        cases = (
            (["--module", "bad", "--input-dir", str(self.input), "--ledger-dir", str(self.store), "--evaluated-at", "bad"], "formal_shadow_module_invalid"),
            (["--module", "leaderObservation", "--input-dir", str(self.input), "--ledger-dir", str(self.store), "--evaluated-at", "bad"], "formal_shadow_evaluated_at_invalid"),
        )
        for argv, reason in cases:
            with self.subTest(reason=reason), contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = main(argv)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(code, 2)
                self.assertEqual(payload, {"reason": reason, "status": "failed"})

    def test_symlink_input_file_overlap_and_production_path_are_rejected(self):
        from run_radar_formal_shadow_observation import RECEIPT_FILENAME, run

        evaluated = self.write_inputs("leaderObservation")
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (self.input / RECEIPT_FILENAME).unlink()
        (self.input / RECEIPT_FILENAME).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "formal_shadow_input_unverified"):
            run("leaderObservation", self.input, self.store, evaluated_at=evaluated)

        production = Path(__file__).resolve().parents[1] / "data"
        with self.assertRaisesRegex(ValueError, "formal_shadow_production_path_forbidden"):
            run("leaderObservation", self.input, production, evaluated_at=evaluated)
        with self.assertRaisesRegex(ValueError, "formal_shadow_paths_overlap"):
            run("leaderObservation", self.input, self.input / "nested", evaluated_at=evaluated)

        project_root = Path(__file__).resolve().parents[2]
        with self.assertRaisesRegex(ValueError, "formal_shadow_private_tmp_required"):
            run("leaderObservation", project_root, project_root / "isolated-ledger", evaluated_at=evaluated)

        ancestor_target = self.root / "ancestor-target"
        ancestor_target.mkdir()
        (ancestor_target / "input").mkdir()
        ancestor_link = self.root / "ancestor-link"
        ancestor_link.symlink_to(ancestor_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "formal_shadow_input_directory_unverified"):
            run("leaderObservation", ancestor_link / "input", self.store, evaluated_at=evaluated)

    def test_ancestor_swap_after_open_is_detected_before_reading_replacement(self):
        import run_radar_formal_shadow_observation as runner
        from run_radar_formal_shadow_observation import run

        ancestor = self.root / "ancestor"
        safe_input = ancestor / "input"
        safe_input.mkdir(parents=True)
        external = self.root / "external"
        (external / "input").mkdir(parents=True)
        sentinel = external / "input" / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        detached = self.root / "detached"
        real_open = runner.os.open
        swapped = False

        def open_then_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = (
                real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is not None
                else real_open(path, flags, mode)
            )
            if not swapped and dir_fd is not None and path == "ancestor":
                swapped = True
                os.replace(ancestor, detached)
                ancestor.symlink_to(external, target_is_directory=True)
            return descriptor

        with patch("run_radar_formal_shadow_observation.os.open", side_effect=open_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_shadow_input_directory_unverified"):
                run("leaderObservation", safe_input, self.store, evaluated_at=datetime.now(UTC))
        self.assertTrue(swapped)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_fixed_input_file_swap_after_open_is_detected_by_entry_inode(self):
        import run_radar_formal_shadow_observation as runner
        from run_radar_formal_shadow_observation import RECEIPT_FILENAME, run

        evaluated = self.write_inputs("leaderObservation")
        external = self.root / "external-receipt.json"
        external.write_text('{"secret":"unchanged"}', encoding="utf-8")
        detached = self.root / "detached-receipt.json"
        real_read = runner.os.read
        swapped = False

        def read_then_swap(descriptor, size):
            nonlocal swapped
            chunk = real_read(descriptor, size)
            if not swapped:
                swapped = True
                os.replace(self.input / RECEIPT_FILENAME, detached)
                (self.input / RECEIPT_FILENAME).symlink_to(external)
            return chunk

        with patch("run_radar_formal_shadow_observation.os.read", side_effect=read_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_shadow_input_unverified"):
                run("leaderObservation", self.input, self.store, evaluated_at=evaluated)
        self.assertTrue(swapped)
        self.assertEqual(external.read_text(encoding="utf-8"), '{"secret":"unchanged"}')


if __name__ == "__main__":
    unittest.main()
