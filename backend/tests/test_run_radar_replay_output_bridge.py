import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class RunRadarReplayOutputBridgeTests(unittest.TestCase):
    def test_cli_passes_exact_sample_and_snapshot_identity(self):
        from run_radar_replay_output_bridge import main

        result = SimpleNamespace(
            bundle=SimpleNamespace(bundle_id="stage9-sector-output-test"),
            snapshot_sha256="a" * 64,
            market_snapshot_sha256="b" * 64,
            leader_snapshot_sha256="c" * 64,
            etf_snapshot_sha256="d" * 64,
            output_dir=Path("/private/tmp/stage9-sector-output"),
        )
        with patch(
            "run_radar_replay_output_bridge.export_sector_replay_output",
            return_value=result,
        ) as export:
            exit_code = main([
                "--sample-id", "sample-1",
                "--radar-run-id", "run-1",
                "--sample-as-of", "2026-09-02T13:05:00+08:00",
                "--sector-snapshot", "/private/tmp/sector-state.json",
                "--stage6-artifact", "/private/tmp/stage6.json",
                "--etf-replay-input", "/private/tmp/replay-input.json",
                "--etf-formal-admission",
                "/private/tmp/etf-formal-admission.json",
                "--output-dir", "/private/tmp/stage9-sector-output",
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(export.call_args.kwargs["sample_id"], "sample-1")
        self.assertEqual(export.call_args.kwargs["radar_run_id"], "run-1")
        self.assertEqual(
            export.call_args.kwargs["sector_snapshot_path"],
            Path("/private/tmp/sector-state.json"),
        )
        self.assertEqual(
            export.call_args.kwargs["stage6_artifact_path"],
            Path("/private/tmp/stage6.json"),
        )
        self.assertEqual(
            export.call_args.kwargs["etf_replay_input_path"],
            Path("/private/tmp/replay-input.json"),
        )
        self.assertEqual(
            export.call_args.kwargs["etf_formal_admission_path"],
            Path("/private/tmp/etf-formal-admission.json"),
        )


if __name__ == "__main__":
    unittest.main()
