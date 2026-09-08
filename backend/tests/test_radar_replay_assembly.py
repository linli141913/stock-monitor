import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.replay_forward_baseline import collect_forward_replay_baseline
from tests.test_radar_replay_forward_baseline import Clock, source_functions


UTC = timezone.utc


class RadarReplayAssemblyTests(unittest.TestCase):
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

    def test_immutable_forward_artifacts_are_assembled_without_relabeling(self):
        from radar.replay_assembly import assemble_replay_artifacts

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            runs = [
                self.collect(root, "development", 2),
                self.collect(root, "calibration", 3),
                self.collect(root, "holdout", 4),
            ]
            result = assemble_replay_artifacts(
                input_dirs=[item.output_dir for item in runs],
                output_dir=root / "assembled",
                clock=lambda: datetime(2026, 9, 5, tzinfo=UTC),
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(result.report.sample_counts, {
            "development": 1,
            "calibration": 1,
            "holdout": 1,
        })
        self.assertEqual(result.report.missing_partitions, [])
        self.assertEqual(
            [sample.role for sample in result.replay.samples],
            ["development", "calibration", "holdout"],
        )
        self.assertEqual(len(manifest["inputArtifacts"]), 3)
        self.assertIn(
            "replay_objective_outcomes_missing",
            result.report.reason_codes,
        )

    def test_tampered_forward_input_is_rejected(self):
        from radar.replay_assembly import assemble_replay_artifacts

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            run = self.collect(root, "development", 2)
            replay_path = run.replay_input_path
            replay_path.write_text(
                replay_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
                assemble_replay_artifacts(
                    input_dirs=[run.output_dir],
                    output_dir=root / "assembled",
                    clock=lambda: datetime(2026, 9, 5, tzinfo=UTC),
                )

    def test_versioned_label_bundle_is_attached_by_exact_sample_identity(self):
        from radar.replay_assembly import assemble_replay_artifacts

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            run = self.collect(root, "development", 2)
            sample = run.replay.samples[0]
            label_path = root / "labels.json"
            label_path.write_text(json.dumps({
                "contractId": "radar-replay-label-bundle-v1",
                "bundleId": "independent-labels-v1",
                "createdAt": "2026-09-05T00:00:00+00:00",
                "samples": [{
                    "sampleId": sample.sample_id,
                    "radarRunId": sample.radar_run_id,
                    "asOf": sample.as_of.isoformat(),
                    "labels": [{
                        "labelId": "leader-000001-v1",
                        "domain": "leader",
                        "targetId": "000001",
                        "expectedState": "candidate",
                        "labeledBy": "independent-official-review-v1",
                        "labeledAt": "2026-09-05T00:00:00+00:00",
                        "sourceIds": ["cninfo:1225000001"],
                        "reviewStatus": "verified",
                        "independentFromRule": True,
                        "outcomeMetrics": {},
                    }],
                }],
            }), encoding="utf-8")
            output_path = root / "outputs.json"
            output_path.write_text(json.dumps({
                "contractId": "radar-replay-output-bundle-v1",
                "bundleId": "deterministic-outputs-v1",
                "createdAt": "2026-09-05T00:00:00+00:00",
                "samples": [{
                    "sampleId": sample.sample_id,
                    "radarRunId": sample.radar_run_id,
                    "asOf": sample.as_of.isoformat(),
                    "evidence": [{
                        "evidenceId": "leader-output-000001-v1",
                        "domain": "leader",
                        "sourceId": "deterministic-leader-run-v1",
                        "source": "三级龙头确定性回放",
                        "sourceTime": sample.as_of.isoformat(),
                        "fetchedAt": sample.as_of.isoformat(),
                        "effectiveFrom": None,
                        "status": "ready",
                        "payload": {
                            "states": [{
                                "targetId": "000001",
                                "state": "candidate",
                            }],
                        },
                    }],
                }],
            }), encoding="utf-8")

            base = assemble_replay_artifacts(
                input_dirs=[run.output_dir],
                output_dir=root / "base-assembled",
                clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
            )
            result = assemble_replay_artifacts(
                input_dirs=[run.output_dir],
                label_bundle_paths=[label_path],
                output_bundle_paths=[output_path],
                output_dir=root / "assembled",
                clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            result.replay.samples[0].expected_labels[0].label_id,
            "leader-000001-v1",
        )
        self.assertEqual(manifest["labelBundles"][0]["bundleId"], "independent-labels-v1")
        self.assertEqual(manifest["outputBundles"][0]["bundleId"], "deterministic-outputs-v1")
        self.assertEqual(result.report.comparable_label_count, 1)
        self.assertEqual(result.report.incomparable_label_count, 0)
        self.assertEqual(result.report.metrics["leader"]["exactMatchRate"], 1.0)
        self.assertNotEqual(result.replay.replay_run_id, base.replay.replay_run_id)


if __name__ == "__main__":
    unittest.main()
