import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from radar.replay_contracts import RadarReplayOutputBundle
from radar.replay_label_tasks import RadarReplayLabelTaskBundle


UTC = timezone.utc
SAMPLE_AS_OF = datetime(2026, 9, 1, 6, 30, tzinfo=UTC)


class RadarReplayObjectiveOutcomeTests(unittest.TestCase):
    def task_bundle(self):
        return RadarReplayLabelTaskBundle.model_validate({
            "taskBundleId": "tasks-1",
            "createdAt": datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
            "samples": [{
                "sampleId": "sample-1",
                "radarRunId": "radar-1",
                "role": "development",
                "asOf": SAMPLE_AS_OF,
                "sourceSnapshots": {
                    "path": "/private/tmp/source-snapshots.json",
                    "sha256": "a" * 64,
                },
                "replayInput": {
                    "path": "/private/tmp/replay-input.json",
                    "sha256": "b" * 64,
                },
                "requiredLabelDomains": [
                    "market", "sector", "etf", "leader",
                ],
                "ruleOutputIncluded": False,
            }],
        })

    def output_bundle(self):
        return RadarReplayOutputBundle.model_validate({
            "bundleId": "outputs-1",
            "createdAt": SAMPLE_AS_OF,
            "samples": [{
                "sampleId": "sample-1",
                "radarRunId": "radar-1",
                "asOf": SAMPLE_AS_OF,
                "evidence": [
                    self.output("market", [{
                        "targetId": "a-share", "state": "retreat",
                    }]),
                    self.output("sector", [
                        {"targetId": "C39", "state": "accelerating"},
                        {"targetId": "C40", "state": "invalid"},
                    ]),
                    self.output("etf", [{
                        "targetId": "510300", "state": "observed",
                    }]),
                    self.output("leader", [{
                        "targetId": "000001", "state": "candidate",
                    }]),
                ],
            }],
        })

    @staticmethod
    def output(domain, states):
        return {
            "evidenceId": f"{domain}-output-1",
            "domain": domain,
            "sourceId": f"rule-output-{domain}-1",
            "source": f"{domain}规则输出",
            "sourceTime": SAMPLE_AS_OF,
            "fetchedAt": SAMPLE_AS_OF,
            "effectiveFrom": None,
            "status": "ready",
            "payload": {"states": states},
        }

    @staticmethod
    def weekday_calendar(day: date):
        return "closed" if day.weekday() >= 5 else "full"

    @staticmethod
    def ready_provider(request):
        from radar.replay_objective_outcomes import ObjectiveOutcomeObservation

        metrics = {
            "market": {"environmentCorrect": True},
            "sector": {"mainlineDurationDays": 3.0},
            "etf": {"return5d": 0.04},
            "leader": {"relativeSectorReturn5d": 0.02},
        }[request.domain]
        return ObjectiveOutcomeObservation(
            status="ready",
            observedThrough=datetime(2026, 9, 8, 7, 6, tzinfo=UTC),
            sourceIds=[f"official-{request.domain}-{request.target_id}"],
            outcomeMetrics=metrics,
            reasons=[],
        )

    def collect(self, root: Path, *, evaluated_at, providers=None):
        from radar.replay_objective_outcomes import (
            collect_replay_objective_outcomes,
        )

        return collect_replay_objective_outcomes(
            task_bundle=self.task_bundle(),
            output_bundle=self.output_bundle(),
            output_dir=root / "outcomes",
            evaluated_at=evaluated_at,
            day_kind_provider=self.weekday_calendar,
            providers=providers or {},
        )

    def test_immature_sample_does_not_call_outcome_sources(self):
        calls = []

        def forbidden(request):
            calls.append(request)
            raise AssertionError("未到期不得请求结果来源")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.collect(
                Path(directory),
                evaluated_at=datetime(2026, 9, 4, 8, 0, tzinfo=UTC),
                providers={domain: forbidden for domain in (
                    "market", "sector", "etf", "leader",
                )},
            )

        self.assertEqual(calls, [])
        self.assertEqual(result.bundle.status, "pending_maturity")
        self.assertEqual(result.bundle.pending_sample_count, 1)
        self.assertEqual(result.bundle.ready_observation_count, 0)
        self.assertIsNone(result.label_bundle_path)

    def test_mature_sample_collects_every_target_and_writes_label_bundle(self):
        calls = []

        def provider(request):
            calls.append((request.domain, request.target_id, request.maturity_date))
            return self.ready_provider(request)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            result = self.collect(
                root,
                evaluated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
                providers={domain: provider for domain in (
                    "market", "sector", "etf", "leader",
                )},
            )

            self.assertTrue(result.outcome_bundle_path.is_file())
            self.assertTrue(result.manifest_path.is_file())
            self.assertTrue(result.label_bundle_path.is_file())

        self.assertEqual(len(calls), 5)
        self.assertEqual({item[2] for item in calls}, {date(2026, 9, 8)})
        self.assertEqual(result.bundle.status, "ready")
        self.assertEqual(result.bundle.ready_observation_count, 5)
        self.assertEqual(result.bundle.missing_observation_count, 0)
        labels = result.label_bundle.samples[0].labels
        self.assertEqual(len(labels), 5)
        self.assertTrue(all(
            label.comparison_mode == "objective_outcome"
            and label.expected_state is None
            and label.independent_from_rule
            for label in labels
        ))

    def test_missing_fact_is_recorded_without_fake_label(self):
        from radar.replay_objective_outcomes import ObjectiveOutcomeObservation

        def provider(request):
            if request.target_id == "C40":
                return ObjectiveOutcomeObservation(
                    status="missing",
                    observedThrough=datetime(2026, 9, 8, 7, 6, tzinfo=UTC),
                    sourceIds=[],
                    outcomeMetrics={},
                    reasons=["official_outcome_missing"],
                )
            return self.ready_provider(request)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.collect(
                Path(directory),
                evaluated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
                providers={domain: provider for domain in (
                    "market", "sector", "etf", "leader",
                )},
            )

        self.assertEqual(result.bundle.status, "partial")
        self.assertEqual(result.bundle.ready_observation_count, 4)
        self.assertEqual(result.bundle.missing_observation_count, 1)
        labels = result.label_bundle.samples[0].labels
        self.assertNotIn("C40", {label.target_id for label in labels})

    def test_future_observation_is_rejected(self):
        from radar.replay_objective_outcomes import ObjectiveOutcomeObservation

        def provider(request):
            return ObjectiveOutcomeObservation(
                status="ready",
                observedThrough=datetime(2026, 9, 9, 7, 1, tzinfo=UTC),
                sourceIds=["official-future"],
                outcomeMetrics={"environmentCorrect": True},
                reasons=[],
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                ValueError,
                "objective_outcome_observation_from_future",
            ):
                self.collect(
                    Path(directory),
                    evaluated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
                    providers={domain: provider for domain in (
                        "market", "sector", "etf", "leader",
                    )},
                )

    def test_rule_output_source_cannot_be_reused_as_outcome_source(self):
        from radar.replay_objective_outcomes import ObjectiveOutcomeObservation

        def provider(request):
            return ObjectiveOutcomeObservation(
                status="ready",
                observedThrough=datetime(2026, 9, 8, 7, 6, tzinfo=UTC),
                sourceIds=[f"rule-output-{request.domain}-1"],
                outcomeMetrics={
                    "market": {"environmentCorrect": True},
                    "sector": {"mainlineDurationDays": 3.0},
                    "etf": {"return5d": 0.04},
                    "leader": {"relativeSectorReturn5d": 0.02},
                }[request.domain],
                reasons=[],
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                ValueError,
                "objective_outcome_rule_source_overlap",
            ):
                self.collect(
                    Path(directory),
                    evaluated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
                    providers={domain: provider for domain in (
                        "market", "sector", "etf", "leader",
                    )},
                )


if __name__ == "__main__":
    unittest.main()
