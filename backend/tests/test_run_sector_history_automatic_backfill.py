import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from run_sector_history_automatic_backfill import (
    DEFAULT_SECTOR_HISTORY_STORE_DIR,
    run_cli,
)
from tests.test_radar_sector_history_automatic_backfill import request


class SectorHistoryAutomaticBackfillCliTests(unittest.TestCase):
    def test_live_backfill_requires_explicit_confirmation(self):
        calls = []
        output = io.StringIO()

        exit_code = run_cli(
            ["--artifact-dir", "/private/tmp/sector-history-test"],
            stdout=output,
            bootstrap=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reasons"],
            ["sector_history_live_confirmation_missing"],
        )

    def test_confirmed_run_forwards_history_window_and_prints_safe_summary(self):
        value, _ = request()
        output = io.StringIO()
        captured = []
        published = []

        def runner(source, *, artifact_dir):
            captured.append((source, artifact_dir))
            return SimpleNamespace(
                status="ready",
                evidence_path=Path("/private/tmp/evidence.json"),
                to_evidence=lambda: {
                    "status": "ready",
                    "requestedCount": 40,
                    "fetchedCount": 40,
                    "reusedCount": 0,
                    "failureCount": 0,
                    "gate": {"formalGateReady": False},
                },
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            exit_code = run_cli(
                [
                    "--artifact-dir", directory,
                    "--history-days", "61",
                    "--comparable-time", "10:30",
                    "--confirm-live-backfill",
                ],
                stdout=output,
                bootstrap=lambda **kwargs: value,
                runner=runner,
                publisher=lambda path, **kwargs: published.append(
                    (path, kwargs)
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(captured[0][1], Path(directory))
        self.assertFalse(payload["gate"]["formalGateReady"])
        self.assertNotIn("seriesBySymbol", output.getvalue())
        self.assertEqual(published[0][0], Path("/private/tmp/evidence.json"))

    def test_default_store_is_project_runtime_data_and_is_published(self):
        value, _ = request()
        output = io.StringIO()
        captured = []

        result = SimpleNamespace(
            status="ready",
            evidence_path=Path("/private/tmp/evidence.json"),
            to_evidence=lambda: {
                "status": "ready",
                "gate": {"formalGateReady": False},
            },
        )

        exit_code = run_cli(
            ["--confirm-live-backfill"],
            stdout=output,
            bootstrap=lambda **kwargs: value,
            runner=lambda source, *, artifact_dir: (
                captured.append(artifact_dir) or result
            ),
            publisher=lambda path, **kwargs: captured.append(
                kwargs["store_dir"]
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            captured,
            [DEFAULT_SECTOR_HISTORY_STORE_DIR] * 2,
        )

    def test_bootstrap_failure_is_sanitized(self):
        output = io.StringIO()

        exit_code = run_cli(
            [
                "--artifact-dir", "/private/tmp/sector-history-test",
                "--confirm-live-backfill",
            ],
            stdout=output,
            bootstrap=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("https://upstream/?token=secret")
            ),
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(
            json.loads(output.getvalue())["reasons"],
            ["sector_history_live_bootstrap_failed"],
        )
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
