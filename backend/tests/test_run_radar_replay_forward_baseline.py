import unittest
from unittest.mock import patch


class RunRadarReplayForwardBaselineTests(unittest.TestCase):
    def test_cli_passes_explicit_cninfo_pdf_cache_directory(self):
        from pathlib import Path
        from types import SimpleNamespace

        from run_radar_replay_forward_baseline import main

        result = SimpleNamespace(
            report=SimpleNamespace(
                status="not_ready",
                sample_counts={"development": 1},
                missing_partitions=["calibration", "holdout"],
                missing_domains=[],
                unverifiable_count=0,
                failed_count=0,
            ),
            replay=SimpleNamespace(replay_run_id="replay-test"),
            output_dir=Path("/private/tmp/stage9-test"),
        )
        with patch(
            "run_radar_replay_forward_baseline.collect_forward_replay_baseline",
            return_value=result,
        ) as collect:
            exit_code = main([
                "--confirm-live-baseline",
                "--output-dir", "/private/tmp/stage9-test",
                "--cninfo-pdf-cache-dir", "/private/tmp/stage9-pdf-cache",
                "--radar-run-id", "stage6-prefreeze-20260902T130000000000",
                "--formal-etf", "515790",
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            collect.call_args.kwargs["cninfo_pdf_cache_dir"],
            Path("/private/tmp/stage9-pdf-cache"),
        )
        self.assertEqual(
            collect.call_args.kwargs["radar_run_id"],
            "stage6-prefreeze-20260902T130000000000",
        )
        self.assertEqual(
            collect.call_args.kwargs["formal_etf_symbols"],
            ("515790",),
        )

    def test_cli_without_explicit_confirmation_returns_usage_error(self):
        from run_radar_replay_forward_baseline import main

        with patch(
            "run_radar_replay_forward_baseline.collect_forward_replay_baseline",
            side_effect=AssertionError("未确认时不得采集"),
        ):
            exit_code = main([
                "--output-dir",
                "/private/tmp/stage9-no-confirm",
            ])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
