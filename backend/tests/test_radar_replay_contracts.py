import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError


UTC = timezone.utc
AS_OF = datetime(2026, 8, 3, 6, 30, tzinfo=UTC)


class RadarReplayContractTests(unittest.TestCase):
    def payload(self):
        return {
            "replayRunId": "replay-20260803-1430",
            "createdAt": "2026-09-01T03:00:00+00:00",
            "samples": [
                {
                    "sampleId": "20260803-1430-market",
                    "role": "development",
                    "asOf": AS_OF.isoformat(),
                    "radarRunId": "historical-radar-20260803-1430",
                    "ruleVersion": "radar-replay-rules-v1",
                    "evidence": [
                        {
                            "evidenceId": "security-universe-1",
                            "domain": "security_universe",
                            "sourceId": "exchange-master-20260803",
                            "source": "交易所历史证券名册",
                            "sourceTime": "2026-08-03T06:00:00+00:00",
                            "fetchedAt": "2026-08-03T06:10:00+00:00",
                            "effectiveFrom": "2026-08-03T00:00:00+00:00",
                            "status": "ready",
                            "payload": {"includedCount": 5200},
                        }
                    ],
                    "expectedLabels": [],
                }
            ],
        }

    def test_future_source_fetch_or_effective_time_is_rejected(self):
        from radar.replay_contracts import RadarReplayInput

        for field in ("sourceTime", "fetchedAt", "effectiveFrom"):
            with self.subTest(field=field):
                payload = self.payload()
                payload["samples"][0]["evidence"][0][field] = (
                    AS_OF + timedelta(microseconds=1)
                ).isoformat()
                if field == "sourceTime":
                    payload["samples"][0]["evidence"][0]["fetchedAt"] = (
                        AS_OF + timedelta(microseconds=2)
                    ).isoformat()
                with self.assertRaisesRegex(
                    ValidationError,
                    f"future_{field}",
                ):
                    RadarReplayInput.model_validate(payload)

    def test_source_time_cannot_be_later_than_fetch_time(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        evidence = payload["samples"][0]["evidence"][0]
        evidence["sourceTime"] = "2026-08-03T06:20:00+00:00"
        evidence["fetchedAt"] = "2026-08-03T06:10:00+00:00"

        with self.assertRaisesRegex(
            ValidationError,
            "sourceTime_after_fetchedAt",
        ):
            RadarReplayInput.model_validate(payload)

    def test_duplicate_evidence_ids_and_empty_source_are_rejected(self):
        from radar.replay_contracts import RadarReplayInput

        duplicate = self.payload()
        duplicate["samples"][0]["evidence"].append(
            deepcopy(duplicate["samples"][0]["evidence"][0])
        )
        with self.assertRaisesRegex(ValidationError, "duplicate_evidence_id"):
            RadarReplayInput.model_validate(duplicate)

        empty_source = self.payload()
        empty_source["samples"][0]["evidence"][0]["source"] = "  "
        with self.assertRaises(ValidationError):
            RadarReplayInput.model_validate(empty_source)

    def test_same_historical_identity_cannot_cross_sample_partitions(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        calibration = deepcopy(payload["samples"][0])
        calibration["sampleId"] = "20260803-1430-calibration-copy"
        calibration["role"] = "calibration"
        payload["samples"].append(calibration)

        with self.assertRaisesRegex(
            ValidationError,
            "sample_partition_overlap",
        ):
            RadarReplayInput.model_validate(payload)

    def test_valid_contract_keeps_missing_source_time_truthful(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["evidence"][0]["sourceTime"] = None
        parsed = RadarReplayInput.model_validate(payload)

        self.assertEqual(parsed.samples[0].role, "development")
        self.assertIsNone(parsed.samples[0].evidence[0].source_time)
        self.assertEqual(
            parsed.samples[0].evidence[0].payload,
            {"includedCount": 5200},
        )

    def test_replay_report_cannot_predate_its_historical_sample(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["createdAt"] = (AS_OF - timedelta(microseconds=1)).isoformat()

        with self.assertRaisesRegex(ValidationError, "future_sample_asOf"):
            RadarReplayInput.model_validate(payload)

    def test_golden_label_requires_independent_versioned_provenance(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["expectedLabels"] = [{
            "labelId": "leader-000001-1",
            "domain": "leader",
            "targetId": "000001",
            "expectedState": "candidate",
            "labeledBy": "independent-official-review-v1",
            "labeledAt": "2026-08-04T06:30:00+00:00",
            "sourceIds": ["cninfo:1225000001"],
            "reviewStatus": "verified",
            "independentFromRule": False,
            "outcomeMetrics": {},
        }]

        with self.assertRaisesRegex(
            ValidationError,
            "independentFromRule",
        ):
            RadarReplayInput.model_validate(payload)

    def test_golden_label_cannot_be_created_after_replay_report(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["expectedLabels"] = [{
            "labelId": "leader-000001-1",
            "domain": "leader",
            "targetId": "000001",
            "expectedState": "candidate",
            "labeledBy": "independent-official-review-v1",
            "labeledAt": "2026-09-01T03:00:00.000001+00:00",
            "sourceIds": ["cninfo:1225000001"],
            "reviewStatus": "verified",
            "independentFromRule": True,
            "outcomeMetrics": {},
        }]

        with self.assertRaisesRegex(ValidationError, "future_labeledAt"):
            RadarReplayInput.model_validate(payload)

    def test_label_cannot_predate_its_replay_sample(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["expectedLabels"] = [{
            "labelId": "leader-000001-before-sample",
            "domain": "leader",
            "targetId": "000001",
            "expectedState": "candidate",
            "labeledBy": "independent-official-review-v1",
            "labeledAt": (AS_OF - timedelta(microseconds=1)).isoformat(),
            "sourceIds": ["cninfo:1225000001"],
            "reviewStatus": "verified",
            "independentFromRule": True,
            "outcomeMetrics": {},
        }]

        with self.assertRaisesRegex(ValidationError, "label_before_sample_asOf"):
            RadarReplayInput.model_validate(payload)

    def test_objective_outcome_does_not_require_human_state_approval(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["expectedLabels"] = [{
            "labelId": "leader-000001-outcome-1",
            "domain": "leader",
            "targetId": "000001",
            "comparisonMode": "objective_outcome",
            "expectedState": None,
            "labeledBy": "automatic-official-outcome-v1",
            "labeledAt": "2026-08-04T06:30:00+00:00",
            "sourceIds": ["tencent-qfq:000001:20260804"],
            "reviewStatus": "verified",
            "independentFromRule": True,
            "outcomeMetrics": {"relativeSectorReturn3d": 0.031},
        }]

        parsed = RadarReplayInput.model_validate(payload)

        label = parsed.samples[0].expected_labels[0]
        self.assertEqual(label.comparison_mode, "objective_outcome")
        self.assertIsNone(label.expected_state)

    def test_objective_outcome_requires_a_real_non_null_metric(self):
        from radar.replay_contracts import RadarReplayInput

        payload = self.payload()
        payload["samples"][0]["expectedLabels"] = [{
            "labelId": "leader-000001-outcome-1",
            "domain": "leader",
            "targetId": "000001",
            "comparisonMode": "objective_outcome",
            "expectedState": None,
            "labeledBy": "automatic-official-outcome-v1",
            "labeledAt": "2026-08-04T06:30:00+00:00",
            "sourceIds": ["tencent-qfq:000001:20260804"],
            "reviewStatus": "verified",
            "independentFromRule": True,
            "outcomeMetrics": {"relativeSectorReturn3d": None},
        }]

        with self.assertRaisesRegex(
            ValidationError,
            "objective_outcome_metric_required",
        ):
            RadarReplayInput.model_validate(payload)

    def test_label_bundle_identity_and_creation_time_are_strict(self):
        from radar.replay_contracts import RadarReplayLabelBundle

        payload = {
            "bundleId": "labels-20260803-v1",
            "createdAt": "2026-08-05T00:00:00+00:00",
            "samples": [{
                "sampleId": "20260803-1430-market",
                "radarRunId": "historical-radar-20260803-1430",
                "asOf": AS_OF.isoformat(),
                "labels": [{
                    "labelId": "leader-000001-1",
                    "domain": "leader",
                    "targetId": "000001",
                    "expectedState": "candidate",
                    "labeledBy": "independent-official-review-v1",
                    "labeledAt": "2026-08-06T00:00:00+00:00",
                    "sourceIds": ["cninfo:1225000001"],
                    "reviewStatus": "verified",
                    "independentFromRule": True,
                    "outcomeMetrics": {},
                }],
            }],
        }

        with self.assertRaisesRegex(ValidationError, "future_labeledAt"):
            RadarReplayLabelBundle.model_validate(payload)

    def test_label_bundle_rejects_duplicate_target_and_pre_sample_label(self):
        from radar.replay_contracts import RadarReplayLabelBundle

        label = {
            "labelId": "leader-000001-1",
            "domain": "leader",
            "targetId": "000001",
            "expectedState": "candidate",
            "labeledBy": "independent-official-review-v1",
            "labeledAt": "2026-08-04T00:00:00+00:00",
            "sourceIds": ["cninfo:1225000001"],
            "reviewStatus": "verified",
            "independentFromRule": True,
            "outcomeMetrics": {},
        }
        payload = {
            "bundleId": "labels-20260803-v1",
            "createdAt": "2026-08-05T00:00:00+00:00",
            "samples": [{
                "sampleId": "20260803-1430-market",
                "radarRunId": "historical-radar-20260803-1430",
                "asOf": AS_OF.isoformat(),
                "labels": [label, {
                    **label,
                    "labelId": "leader-000001-duplicate",
                }],
            }],
        }
        with self.assertRaisesRegex(ValidationError, "duplicate_label_target"):
            RadarReplayLabelBundle.model_validate(payload)

        payload["samples"][0]["labels"] = [{
            **label,
            "labeledAt": (AS_OF - timedelta(microseconds=1)).isoformat(),
        }]
        with self.assertRaisesRegex(ValidationError, "label_before_sample_asOf"):
            RadarReplayLabelBundle.model_validate(payload)

    def test_output_bundle_cannot_predate_its_sample(self):
        from radar.replay_contracts import RadarReplayOutputBundle

        evidence_time = AS_OF - timedelta(microseconds=2)
        payload = {
            "bundleId": "outputs-20260803-v1",
            "createdAt": (AS_OF - timedelta(microseconds=1)).isoformat(),
            "samples": [{
                "sampleId": "20260803-1430-market",
                "radarRunId": "historical-radar-20260803-1430",
                "asOf": AS_OF.isoformat(),
                "evidence": [{
                    "evidenceId": "leader-output-1",
                    "domain": "leader",
                    "sourceId": "deterministic-leader-run-v1",
                    "source": "三级龙头确定性输出",
                    "sourceTime": evidence_time.isoformat(),
                    "fetchedAt": evidence_time.isoformat(),
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {"states": []},
                }],
            }],
        }

        with self.assertRaisesRegex(ValidationError, "future_output_sample_asOf"):
            RadarReplayOutputBundle.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
