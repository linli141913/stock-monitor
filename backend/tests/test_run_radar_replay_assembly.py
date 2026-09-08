import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class RunRadarReplayAssemblyTests(unittest.TestCase):
    def test_cli_passes_explicit_artifacts_without_relabeling(self):
        from run_radar_replay_assembly import main

        result = SimpleNamespace(
            replay=SimpleNamespace(replay_run_id="replay-assembly-test"),
            report=SimpleNamespace(
                status="not_ready",
                pipeline_status="ready",
                effectiveness_status="collecting",
                sample_counts={
                    "development": 1,
                    "calibration": 1,
                    "holdout": 1,
                },
                comparable_label_count=0,
                missing_label_domains=["market", "sector", "etf", "leader"],
                reason_codes=["replay_objective_outcomes_missing"],
            ),
            output_dir=Path("/private/tmp/assembled"),
        )
        with patch(
            "run_radar_replay_assembly.assemble_replay_artifacts",
            return_value=result,
        ) as assemble:
            exit_code = main([
                "--input-dir", "/private/tmp/dev",
                "--input-dir", "/private/tmp/cal",
                "--label-bundle", "/private/tmp/labels.json",
                "--output-bundle", "/private/tmp/outputs.json",
                "--output-dir", "/private/tmp/assembled",
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            assemble.call_args.kwargs["input_dirs"],
            [Path("/private/tmp/dev"), Path("/private/tmp/cal")],
        )
        self.assertEqual(
            assemble.call_args.kwargs["label_bundle_paths"],
            [Path("/private/tmp/labels.json")],
        )


if __name__ == "__main__":
    unittest.main()
