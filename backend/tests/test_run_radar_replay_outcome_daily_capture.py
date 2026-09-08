import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


class RunRadarReplayOutcomeDailyCaptureTests(unittest.TestCase):
    def test_cli_requires_explicit_live_confirmation(self):
        from run_radar_replay_outcome_daily_capture import main

        with self.assertRaises(SystemExit):
            main([
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                "--output-dir", "/private/tmp/daily",
            ])

    def test_cli_passes_exact_paths_and_trade_date(self):
        from run_radar_replay_outcome_daily_capture import main

        with patch(
            "run_radar_replay_outcome_daily_capture."
            "capture_replay_outcome_day_from_files"
        ) as capture:
            capture.return_value.snapshot.status = "ready"
            capture.return_value.snapshot.trade_date = date(2026, 9, 2)
            capture.return_value.snapshot.ready_security_count = 7087
            capture.return_value.snapshot.expected_security_count = 7087
            capture.return_value.snapshot.market_indices = [1, 2, 3, 4]
            capture.return_value.snapshot_path = Path(
                "/private/tmp/daily/daily-outcome-snapshot.json"
            )
            capture.return_value.manifest_path = Path(
                "/private/tmp/daily/manifest.json"
            )
            code = main([
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                "--output-dir", "/private/tmp/daily",
                "--trade-date", "2026-09-02",
                "--confirm-live-close-capture",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(capture.call_args.kwargs["trade_date"], date(2026, 9, 2))
        self.assertEqual(
            capture.call_args.kwargs["task_bundle_path"],
            Path("/private/tmp/tasks/label-tasks.json"),
        )


if __name__ == "__main__":
    unittest.main()
