import unittest


class RunRadarReplayLabelTasksTests(unittest.TestCase):
    def test_cli_requires_at_least_one_forward_artifact(self):
        from run_radar_replay_label_tasks import main

        with self.assertRaises(SystemExit) as context:
            main(["--output-dir", "/private/tmp/stage9-label-tasks"])

        self.assertEqual(context.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
