import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class RunRadarReplayCampaignTests(unittest.TestCase):
    def test_cli_registers_then_resumes_exact_private_tmp_campaign(self):
        from run_radar_replay_campaign import main

        state = SimpleNamespace(
            campaign_id="campaign-1",
            revision=2,
            cohorts=[1],
        )
        report = SimpleNamespace(
            status="pending_maturity",
            actions=("daily_snapshot_verified",),
            state=state,
        )
        with (
            patch("run_radar_replay_campaign.register_cohort") as register,
            patch("run_radar_replay_campaign.resume_campaign") as resume,
        ):
            register.return_value = state
            resume.return_value = report
            code = main([
                "--campaign-dir", "/private/tmp/campaign-1",
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                "--daily-snapshot", "/private/tmp/daily/snapshot.json",
                "--confirm-live-close-capture",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(
            register.call_args.kwargs["task_bundle_path"],
            Path("/private/tmp/tasks/label-tasks.json"),
        )
        self.assertEqual(
            register.call_args.kwargs["daily_snapshot_paths"],
            [Path("/private/tmp/daily/snapshot.json")],
        )
        self.assertTrue(
            resume.call_args.kwargs["confirm_live_close_capture"]
        )

    def test_cli_can_create_empty_formal_campaign_without_live_capture(self):
        from run_radar_replay_campaign import main

        state = SimpleNamespace(
            campaign_id="formal-1",
            revision=1,
            cohorts=[],
        )
        report = SimpleNamespace(
            status="empty",
            actions=(),
            state=state,
        )
        with (
            patch("run_radar_replay_campaign.create_campaign") as create,
            patch("run_radar_replay_campaign.resume_campaign") as resume,
        ):
            create.return_value = state
            resume.return_value = report
            code = main([
                "--campaign-dir", "/private/tmp/formal-1",
                "--create",
                "--campaign-id", "formal-1",
                "--mode", "formal_sequence",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.kwargs["campaign_id"], "formal-1")
        self.assertFalse(
            resume.call_args.kwargs["confirm_live_close_capture"]
        )

    def test_cli_rejects_create_and_direct_formal_registration_before_mutation(self):
        from run_radar_replay_campaign import main

        with (
            patch("run_radar_replay_campaign.create_campaign") as create,
            patch("run_radar_replay_campaign.register_cohort") as register,
            patch("run_radar_replay_campaign.resume_campaign") as resume,
        ):
            with self.assertRaises(SystemExit):
                main([
                    "--campaign-dir", "/private/tmp/formal-direct",
                    "--create",
                    "--campaign-id", "formal-direct",
                    "--mode", "formal_sequence",
                    "--task-bundle", "/private/tmp/tasks/label-tasks.json",
                    "--output-bundle", "/private/tmp/outputs/output-bundle.json",
                ])

        create.assert_not_called()
        register.assert_not_called()
        resume.assert_not_called()

    def test_cli_requires_task_and_output_bundle_as_pair(self):
        from run_radar_replay_campaign import main

        with self.assertRaises(SystemExit):
            main([
                "--campaign-dir", "/private/tmp/campaign-1",
                "--task-bundle", "/private/tmp/tasks/label-tasks.json",
            ])


if __name__ == "__main__":
    unittest.main()
