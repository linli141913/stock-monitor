import unittest
from pathlib import Path
from unittest.mock import patch


class RunRadarReplayObjectiveOutcomesTests(unittest.TestCase):
    def test_cli_requires_explicit_confirmation(self):
        from run_radar_replay_objective_outcomes import main

        with self.assertRaises(SystemExit):
            main([
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                "--output-dir", "/private/tmp/outcomes",
            ])

    def test_cli_passes_explicit_private_tmp_inputs(self):
        from run_radar_replay_objective_outcomes import main

        with patch(
            "run_radar_replay_objective_outcomes."
            "collect_replay_objective_outcomes_from_files"
        ) as collect:
            collect.return_value.bundle.status = "pending_maturity"
            collect.return_value.outcome_bundle_path = Path(
                "/private/tmp/outcomes/objective-outcomes.json"
            )
            collect.return_value.label_bundle_path = None
            collect.return_value.manifest_path = Path(
                "/private/tmp/outcomes/manifest.json"
            )
            code = main([
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                "--output-dir", "/private/tmp/outcomes",
                "--confirm-objective-outcome-collection",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(
            collect.call_args.kwargs["task_bundle_path"],
            Path("/private/tmp/tasks/label-tasks.json"),
        )
        self.assertEqual(collect.call_args.kwargs["providers"], {})


if __name__ == "__main__":
    unittest.main()
