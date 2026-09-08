import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.replay_forward_baseline import collect_forward_replay_baseline
from tests.test_radar_replay_forward_baseline import Clock, source_functions


UTC = timezone.utc


class RadarReplayLabelTaskTests(unittest.TestCase):
    def collect(self, root: Path, role: str, day: int):
        started = datetime(2026, 9, day, 8, 0, tzinfo=UTC)
        return collect_forward_replay_baseline(
            confirm_live_baseline=True,
            output_dir=root / role,
            sample_role=role,
            sources=source_functions(),
            clock=Clock([
                started,
                started + timedelta(minutes=1),
                started + timedelta(minutes=1, seconds=1),
            ]),
        )

    def test_exports_blind_tasks_for_all_frozen_partitions(self):
        from radar.replay_label_tasks import export_replay_label_tasks

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            runs = [
                self.collect(root, "development", 2),
                self.collect(root, "calibration", 3),
                self.collect(root, "holdout", 4),
            ]
            result = export_replay_label_tasks(
                input_dirs=[item.output_dir for item in runs],
                output_dir=root / "label-tasks",
                clock=lambda: datetime(2026, 9, 5, tzinfo=UTC),
            )
            payload = json.loads(
                result.task_bundle_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            [item.role for item in result.bundle.samples],
            ["development", "calibration", "holdout"],
        )
        self.assertEqual(payload["contractId"], "radar-replay-label-task-v1")
        self.assertEqual(len(payload["samples"]), 3)
        self.assertEqual(
            payload["samples"][0]["requiredLabelDomains"],
            ["market", "sector", "etf", "leader"],
        )
        self.assertFalse(payload["samples"][0]["ruleOutputIncluded"])
        self.assertEqual(
            payload["samples"][0]["evaluationMode"],
            "automatic_objective_outcome",
        )
        self.assertFalse(payload["samples"][0]["manualApprovalRequired"])
        self.assertEqual(
            payload["samples"][0]["minimumMaturityTradingDays"],
            5,
        )
        self.assertNotIn("expectedLabels", payload["samples"][0])
        self.assertTrue(
            payload["samples"][0]["sourceSnapshots"]["path"].endswith(
                "/source-snapshots.json"
            )
        )

    def test_tampered_source_snapshot_is_rejected(self):
        from radar.replay_label_tasks import export_replay_label_tasks

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            run = self.collect(root, "development", 2)
            run.source_snapshots_path.write_text(
                run.source_snapshots_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "label_task_source_snapshot_hash_mismatch",
            ):
                export_replay_label_tasks(
                    input_dirs=[run.output_dir],
                    output_dir=root / "label-tasks",
                    clock=lambda: datetime(2026, 9, 5, tzinfo=UTC),
                )

    def test_creation_before_sample_time_is_rejected(self):
        from radar.replay_label_tasks import export_replay_label_tasks

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            run = self.collect(root, "development", 2)
            with self.assertRaisesRegex(ValueError, "label_task_createdAt_future"):
                export_replay_label_tasks(
                    input_dirs=[run.output_dir],
                    output_dir=root / "label-tasks",
                    clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
                )


if __name__ == "__main__":
    unittest.main()
