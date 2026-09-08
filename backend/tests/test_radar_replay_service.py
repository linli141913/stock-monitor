import unittest
from copy import deepcopy

from radar.replay_contracts import RadarReplayInput


REQUIRED_DOMAINS = (
    "security_universe",
    "trading_rule",
    "industry",
    "index",
    "etf",
    "corporate_action",
)


class RadarReplayServiceTests(unittest.TestCase):
    def payload(self):
        evidence = []
        for index, domain in enumerate(REQUIRED_DOMAINS):
            evidence.append({
                "evidenceId": f"{domain}-1",
                "domain": domain,
                "sourceId": f"official-{domain}-20260803",
                "source": f"{domain}历史官方来源",
                "sourceTime": "2026-08-03T06:00:00+00:00",
                "fetchedAt": "2026-08-03T06:10:00+00:00",
                "effectiveFrom": "2026-08-03T00:00:00+00:00",
                "status": "ready",
                "payload": {"recordCount": index + 1},
            })
        evidence.append({
            "evidenceId": "leader-output-1",
            "domain": "leader",
            "sourceId": "deterministic-leader-run-20260803",
            "source": "确定性龙头历史运行",
            "sourceTime": "2026-08-03T06:30:00+00:00",
            "fetchedAt": "2026-08-03T06:30:00+00:00",
            "effectiveFrom": None,
            "status": "ready",
            "payload": {
                "states": [{"symbol": "000001", "state": "candidate"}],
            },
        })
        return {
            "replayRunId": "replay-20260803-1430",
            "createdAt": "2026-09-01T03:00:00+00:00",
            "samples": [{
                "sampleId": "20260803-1430-market",
                "role": "holdout",
                "asOf": "2026-08-03T06:30:00+00:00",
                "radarRunId": "historical-radar-20260803-1430",
                "ruleVersion": "radar-replay-rules-v1",
                "evidence": evidence,
                "expectedLabels": [],
            }],
        }

    def report(self, payload=None):
        from radar.replay_service import build_replay_quality_report

        parsed = RadarReplayInput.model_validate(payload or self.payload())
        return build_replay_quality_report(parsed)

    def partitioned_payload(self):
        payload = self.payload()
        holdout = payload["samples"][0]
        development = deepcopy(holdout)
        development.update({
            "sampleId": "20260801-1430-development",
            "role": "development",
            "asOf": "2026-08-01T06:30:00+00:00",
            "radarRunId": "historical-radar-20260801-1430",
        })
        for evidence in development["evidence"]:
            evidence["evidenceId"] += "-development"
            evidence["sourceTime"] = "2026-08-01T06:00:00+00:00"
            evidence["fetchedAt"] = "2026-08-01T06:10:00+00:00"
            if evidence["effectiveFrom"] is not None:
                evidence["effectiveFrom"] = "2026-08-01T00:00:00+00:00"
        calibration = deepcopy(holdout)
        calibration.update({
            "sampleId": "20260802-1430-calibration",
            "role": "calibration",
            "asOf": "2026-08-02T06:30:00+00:00",
            "radarRunId": "historical-radar-20260802-1430",
        })
        for evidence in calibration["evidence"]:
            evidence["evidenceId"] += "-calibration"
            evidence["sourceTime"] = "2026-08-02T06:00:00+00:00"
            evidence["fetchedAt"] = "2026-08-02T06:10:00+00:00"
            if evidence["effectiveFrom"] is not None:
                evidence["effectiveFrom"] = "2026-08-02T00:00:00+00:00"
        payload["samples"] = [development, calibration, holdout]
        return payload

    def test_complete_historical_inputs_without_golden_labels_are_not_ready(self):
        report = self.report(self.partitioned_payload())

        self.assertEqual(report.status, "not_ready")
        self.assertIn("replay_objective_outcomes_missing", report.reason_codes)
        self.assertEqual(
            report.missing_label_domains,
            ["market", "sector", "etf", "leader"],
        )
        self.assertEqual(report.comparable_label_count, 0)
        self.assertEqual(report.output_counts, {
            "market": 0,
            "sector": 0,
            "etf": 0,
            "leader": 3,
        })
        self.assertEqual(
            report.missing_output_domains,
            ["market", "sector", "etf"],
        )
        self.assertIn("replay_rule_outputs_missing", report.reason_codes)

    def test_etf_monitoring_and_ranking_readiness_counts_are_separate(self):
        payload = self.payload()
        sample = payload["samples"][0]
        sample["evidence"].append({
            "evidenceId": "etf-output-v2",
            "domain": "etf",
            "sourceId": (
                "radar-etf-product-research-output-v2:" + "a" * 64
            ),
            "source": "ETF正式监测准入输出",
            "sourceTime": sample["asOf"],
            "fetchedAt": sample["asOf"],
            "effectiveFrom": None,
            "status": "ready",
            "payload": {
                "snapshotSha256": "a" * 64,
                "formalAdmissionSnapshotSha256": "b" * 64,
                "formalAdmissionCount": 2,
                "monitoringReadyCount": 1,
                "monitoringMissingCount": 1,
                "rankingPolicyReadyCount": 0,
                "rankingPolicyMissingCount": 2,
                "states": [
                    {
                        "targetId": "510300",
                        "state": "product_ready_for_index_research",
                        "monitoringStatus": "ready",
                        "rankingStatus": "missing",
                        "monitoringReasons": [],
                        "rankingReasons": ["etf_rule_not_frozen"],
                    },
                    {
                        "targetId": "515790",
                        "state": "product_ready_for_index_research",
                        "monitoringStatus": "missing",
                        "rankingStatus": "missing",
                        "monitoringReasons": ["etf_index_relation_missing"],
                        "rankingReasons": ["etf_rule_not_frozen"],
                    },
                ],
            },
        })

        report = self.report(payload)

        self.assertTrue(hasattr(report, "etf_readiness_counts"))
        self.assertEqual(report.etf_readiness_counts, {
            "formalAdmissionCount": 2,
            "monitoringReadyCount": 1,
            "monitoringMissingCount": 1,
            "rankingPolicyReadyCount": 0,
            "rankingPolicyMissingCount": 2,
        })

    def test_tampered_etf_readiness_counts_make_output_unverifiable(self):
        payload = self.payload()
        sample = payload["samples"][0]
        sample["evidence"].append({
            "evidenceId": "etf-output-v2-tampered",
            "domain": "etf",
            "sourceId": (
                "radar-etf-product-research-output-v2:" + "a" * 64
            ),
            "source": "ETF正式监测准入输出",
            "sourceTime": sample["asOf"],
            "fetchedAt": sample["asOf"],
            "effectiveFrom": None,
            "status": "ready",
            "payload": {
                "snapshotSha256": "a" * 64,
                "formalAdmissionSnapshotSha256": "b" * 64,
                "formalAdmissionCount": 1,
                "monitoringReadyCount": 0,
                "monitoringMissingCount": 0,
                "rankingPolicyReadyCount": 0,
                "rankingPolicyMissingCount": 1,
                "states": [{
                    "targetId": "510300",
                    "state": "product_ready_for_index_research",
                    "monitoringStatus": "ready",
                    "rankingStatus": "missing",
                    "monitoringReasons": [],
                    "rankingReasons": ["etf_rule_not_frozen"],
                }],
            },
        })

        report = self.report(payload)

        self.assertIn("etf", report.unverifiable_output_domains)
        self.assertIn("replay_rule_outputs_unverifiable", report.reason_codes)

    def test_partitioned_independent_labels_publish_literal_metrics(self):
        payload = self.partitioned_payload()
        domains = ("market", "sector", "etf", "leader")
        targets = {
            "market": "a-share",
            "sector": "C39",
            "etf": "510300",
            "leader": "000001",
        }
        states = {
            "market": "strong",
            "sector": "accelerating",
            "etf": "observed",
            "leader": "candidate",
        }
        outcome_names = {
            "market": "environmentCorrect",
            "sector": "mainlineDurationDays",
            "etf": "return5d",
            "leader": "relativeSectorReturn3d",
        }
        for sample_index, sample in enumerate(payload["samples"]):
            for domain in domains:
                if domain != "leader":
                    sample["evidence"].append({
                        "evidenceId": f"{domain}-output-{sample_index}",
                        "domain": domain,
                        "sourceId": f"deterministic-{domain}-run-{sample_index}",
                        "source": f"{domain}确定性历史运行",
                        "sourceTime": sample["asOf"],
                        "fetchedAt": sample["asOf"],
                        "effectiveFrom": None,
                        "status": "ready",
                        "payload": {
                            "states": [{
                                "targetId": targets[domain],
                                "state": states[domain],
                            }],
                        },
                    })
                sample["expectedLabels"].append({
                    "labelId": f"{domain}-label-{sample_index}",
                    "domain": domain,
                    "targetId": targets[domain],
                    "expectedState": states[domain],
                    "labeledBy": "independent-official-review-v1",
                    "labeledAt": "2026-08-20T00:00:00+00:00",
                    "sourceIds": [f"official-label-source-{domain}-{sample_index}"],
                    "reviewStatus": "verified",
                    "independentFromRule": True,
                    "outcomeMetrics": {
                        outcome_names[domain]: float(sample_index + 1),
                    },
                })

        report = self.report(payload)

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.pipeline_status, "ready")
        self.assertEqual(report.effectiveness_status, "ready")
        self.assertEqual(report.sample_counts, {
            "development": 1,
            "calibration": 1,
            "holdout": 1,
        })
        self.assertEqual(report.included_count, 3)
        self.assertEqual(report.excluded_count, 0)
        self.assertEqual(report.missing_count, 0)
        self.assertEqual(report.unverifiable_count, 0)
        self.assertEqual(report.future_violation_count, 0)
        self.assertEqual(report.multi_state_violation_count, 0)
        self.assertEqual(report.missing_partitions, [])
        self.assertEqual(report.missing_label_domains, [])
        self.assertEqual(report.missing_label_partitions, [])
        self.assertEqual(report.missing_output_domains, [])
        self.assertEqual(report.comparable_label_count, 12)
        self.assertEqual(report.metrics["leader"]["exactMatchRate"], 1.0)
        self.assertEqual(
            report.metrics["leader"]["relativeSectorReturn3dMean"],
            2.0,
        )

    def test_complete_pipeline_can_be_ready_while_effectiveness_collects(self):
        payload = self.partitioned_payload()
        for sample_index, sample in enumerate(payload["samples"]):
            for domain in ("market", "sector", "etf"):
                sample["evidence"].append({
                    "evidenceId": f"{domain}-output-{sample_index}",
                    "domain": domain,
                    "sourceId": f"deterministic-{domain}-{sample_index}",
                    "source": f"{domain}确定性输出",
                    "sourceTime": sample["asOf"],
                    "fetchedAt": sample["asOf"],
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {"states": []},
                })

        report = self.report(payload)

        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.pipeline_status, "ready")
        self.assertEqual(report.effectiveness_status, "collecting")
        self.assertIn("replay_objective_outcomes_missing", report.reason_codes)

    def test_objective_outcomes_can_validate_without_human_state_approval(self):
        payload = self.partitioned_payload()
        domains = ("market", "sector", "etf", "leader")
        targets = {
            "market": "a-share",
            "sector": "C39",
            "etf": "510300",
            "leader": "000001",
        }
        metrics = {
            "market": ("environmentCorrect", True),
            "sector": ("mainlineDurationDays", 3.0),
            "etf": ("return5d", 0.04),
            "leader": ("relativeSectorReturn3d", 0.02),
        }
        for sample_index, sample in enumerate(payload["samples"]):
            for domain in domains:
                if domain != "leader":
                    sample["evidence"].append({
                        "evidenceId": f"{domain}-output-{sample_index}",
                        "domain": domain,
                        "sourceId": f"deterministic-{domain}-{sample_index}",
                        "source": f"{domain}确定性输出",
                        "sourceTime": sample["asOf"],
                        "fetchedAt": sample["asOf"],
                        "effectiveFrom": None,
                        "status": "ready",
                        "payload": {
                            "states": [{
                                "targetId": targets[domain],
                                "state": "research_observed",
                            }],
                        },
                    })
                metric_name, metric_value = metrics[domain]
                sample["expectedLabels"].append({
                    "labelId": f"{domain}-outcome-{sample_index}",
                    "domain": domain,
                    "targetId": targets[domain],
                    "comparisonMode": "objective_outcome",
                    "expectedState": None,
                    "labeledBy": "automatic-official-outcome-v1",
                    "labeledAt": "2026-08-20T00:00:00+00:00",
                    "sourceIds": [
                        f"official-outcome-{domain}-{sample_index}"
                    ],
                    "reviewStatus": "verified",
                    "independentFromRule": True,
                    "outcomeMetrics": {metric_name: metric_value},
                })

        report = self.report(payload)

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.pipeline_status, "ready")
        self.assertEqual(report.effectiveness_status, "ready")
        self.assertEqual(report.comparable_label_count, 12)
        self.assertEqual(
            report.metrics["leader"]["objectiveOutcomeCount"],
            3.0,
        )
        self.assertNotIn("exactMatchRate", report.metrics["leader"])

    def test_objective_outcomes_must_cover_every_frozen_output_target(self):
        payload = self.partitioned_payload()
        domains = ("market", "sector", "etf", "leader")
        targets = {
            "market": "a-share",
            "sector": "C39",
            "etf": "510300",
            "leader": "000001",
        }
        metrics = {
            "market": ("environmentCorrect", True),
            "sector": ("mainlineDurationDays", 3.0),
            "etf": ("return5d", 0.04),
            "leader": ("relativeSectorReturn3d", 0.02),
        }
        for sample_index, sample in enumerate(payload["samples"]):
            for domain in domains:
                if domain != "leader":
                    states = [{
                        "targetId": targets[domain],
                        "state": "research_observed",
                    }]
                    if domain == "sector":
                        states.append({
                            "targetId": "C40",
                            "state": "research_observed",
                        })
                    sample["evidence"].append({
                        "evidenceId": f"{domain}-output-{sample_index}",
                        "domain": domain,
                        "sourceId": f"deterministic-{domain}-{sample_index}",
                        "source": f"{domain}确定性输出",
                        "sourceTime": sample["asOf"],
                        "fetchedAt": sample["asOf"],
                        "effectiveFrom": None,
                        "status": "ready",
                        "payload": {"states": states},
                    })
                metric_name, metric_value = metrics[domain]
                sample["expectedLabels"].append({
                    "labelId": f"{domain}-outcome-{sample_index}",
                    "domain": domain,
                    "targetId": targets[domain],
                    "comparisonMode": "objective_outcome",
                    "expectedState": None,
                    "labeledBy": "automatic-official-outcome-v1",
                    "labeledAt": "2026-08-20T00:00:00+00:00",
                    "sourceIds": [
                        f"official-outcome-{domain}-{sample_index}"
                    ],
                    "reviewStatus": "verified",
                    "independentFromRule": True,
                    "outcomeMetrics": {metric_name: metric_value},
                })

        report = self.report(payload)

        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.pipeline_status, "ready")
        self.assertEqual(report.effectiveness_status, "collecting")
        self.assertEqual(report.unlabeled_output_target_count, 3)
        self.assertEqual(report.unlabeled_output_target_counts, {
            "market": 0,
            "sector": 3,
            "etf": 0,
            "leader": 0,
        })
        self.assertIn(
            "replay_output_target_outcomes_incomplete",
            report.reason_codes,
        )

    def test_single_development_sample_reports_missing_partitions(self):
        payload = self.payload()
        payload["samples"][0]["role"] = "development"

        report = self.report(payload)

        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.missing_partitions, ["calibration", "holdout"])
        self.assertIn("replay_partition_missing", report.reason_codes)

    def test_partition_roles_on_the_same_calendar_day_are_not_independent(self):
        payload = self.partitioned_payload()
        payload["samples"][0]["asOf"] = "2026-08-03T06:30:00+00:00"
        payload["samples"][1]["asOf"] = "2026-08-03T06:31:00+00:00"
        payload["samples"][2]["asOf"] = "2026-08-03T06:32:00+00:00"

        report = self.report(payload)

        self.assertEqual(report.missing_partitions, [])
        self.assertEqual(report.status, "not_ready")
        self.assertIn(
            "replay_partition_trade_date_overlap",
            report.reason_codes,
        )

    def test_partition_roles_must_be_in_chronological_order(self):
        payload = self.partitioned_payload()
        payload["samples"][0]["asOf"] = "2026-08-04T06:30:00+00:00"

        report = self.report(payload)

        self.assertFalse(report.partition_chronology_valid)
        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.pipeline_status, "not_ready")
        self.assertIn(
            "replay_partition_chronology_violation",
            report.reason_codes,
        )

    def test_ready_empty_rule_outputs_are_present_not_missing(self):
        payload = self.partitioned_payload()
        for sample_index, sample in enumerate(payload["samples"]):
            sample["evidence"][-1]["payload"]["states"] = []
            for domain in ("market", "sector", "etf"):
                sample["evidence"].append({
                    "evidenceId": f"{domain}-empty-output-{sample_index}",
                    "domain": domain,
                    "sourceId": (
                        f"deterministic-{domain}-empty-{sample_index}"
                    ),
                    "source": f"{domain}确定性空输出",
                    "sourceTime": sample["asOf"],
                    "fetchedAt": sample["asOf"],
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {"states": []},
                })

        report = self.report(payload)

        self.assertEqual(report.output_counts, {
            "market": 0,
            "sector": 0,
            "etf": 0,
            "leader": 0,
        })
        self.assertEqual(report.missing_output_domains, [])
        self.assertNotIn("replay_rule_outputs_missing", report.reason_codes)

    def test_empty_leader_output_requires_explicit_correct_empty_outcome(self):
        payload = self.partitioned_payload()
        for sample_index, sample in enumerate(payload["samples"]):
            sample["evidence"][-1]["payload"]["states"] = []
            for domain, target, metric in (
                ("market", "a-share", {"environmentCorrect": True}),
                ("sector", "C39", {"mainlineDurationDays": 0.0}),
                ("etf", "510300", {"return5d": 0.0}),
            ):
                sample["evidence"].append({
                    "evidenceId": f"{domain}-output-{sample_index}",
                    "domain": domain,
                    "sourceId": f"rule-{domain}-{sample_index}",
                    "source": f"{domain}确定性输出",
                    "sourceTime": sample["asOf"],
                    "fetchedAt": sample["asOf"],
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {"states": [{
                        "targetId": target,
                        "state": "research_observed",
                    }]},
                })
                sample["expectedLabels"].append({
                    "labelId": f"{domain}-label-{sample_index}",
                    "domain": domain,
                    "targetId": target,
                    "comparisonMode": "objective_outcome",
                    "expectedState": None,
                    "labeledBy": "automatic-official-outcome-v1",
                    "labeledAt": "2026-08-20T00:00:00+00:00",
                    "sourceIds": [f"official-{domain}-{sample_index}"],
                    "reviewStatus": "verified",
                    "independentFromRule": True,
                    "outcomeMetrics": metric,
                })
            sample["expectedLabels"].append({
                "labelId": f"leader-empty-{sample_index}",
                "domain": "leader",
                "targetId": "__empty__",
                "comparisonMode": "objective_outcome",
                "expectedState": None,
                "labeledBy": "automatic-official-outcome-v1",
                "labeledAt": "2026-08-20T00:00:00+00:00",
                "sourceIds": [f"official-leader-empty-{sample_index}"],
                "reviewStatus": "verified",
                "independentFromRule": True,
                "outcomeMetrics": {"correctEmpty": True},
            })

        report = self.report(payload)

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.unlabeled_output_target_count, 0)
        self.assertEqual(report.comparable_label_count, 12)
        self.assertEqual(
            report.metrics["leader"]["correctEmptyMean"],
            1.0,
        )

    def test_unverifiable_and_failed_rule_outputs_are_not_reported_as_missing(self):
        payload = self.partitioned_payload()
        for sample_index, sample in enumerate(payload["samples"]):
            sample["evidence"].append({
                "evidenceId": f"market-unverifiable-{sample_index}",
                "domain": "market",
                "sourceId": f"market-producer-{sample_index}",
                "source": "确定性市场状态生产器",
                "sourceTime": sample["asOf"],
                "fetchedAt": sample["asOf"],
                "effectiveFrom": None,
                "status": "unverifiable",
                "payload": {
                    "states": None,
                    "reasons": ["market_thresholds_unavailable"],
                },
            })
            sample["evidence"].append({
                "evidenceId": f"etf-failed-{sample_index}",
                "domain": "etf",
                "sourceId": f"etf-producer-{sample_index}",
                "source": "ETF确定性状态生产器",
                "sourceTime": sample["asOf"],
                "fetchedAt": sample["asOf"],
                "effectiveFrom": None,
                "status": "failed",
                "payload": {
                    "states": None,
                    "reasons": ["etf_source_failed"],
                },
            })

        report = self.report(payload)

        self.assertEqual(report.status, "failed")
        self.assertEqual(report.pipeline_status, "failed")
        self.assertEqual(report.effectiveness_status, "collecting")
        self.assertEqual(report.missing_output_domains, ["sector"])
        self.assertEqual(report.unverifiable_output_domains, ["market"])
        self.assertEqual(report.failed_output_domains, ["etf"])
        self.assertEqual(report.ready_output_domains, ["leader"])
        self.assertIn("replay_rule_outputs_missing", report.reason_codes)
        self.assertIn("replay_rule_outputs_unverifiable", report.reason_codes)
        self.assertIn("replay_rule_outputs_failed", report.reason_codes)

    def test_output_domain_requires_every_sample_not_one_convenient_sample(self):
        payload = self.partitioned_payload()
        for domain in ("market", "sector", "etf"):
            payload["samples"][0]["evidence"].append({
                "evidenceId": f"{domain}-development-only",
                "domain": domain,
                "sourceId": f"{domain}-development-only",
                "source": f"{domain}确定性输出",
                "sourceTime": payload["samples"][0]["asOf"],
                "fetchedAt": payload["samples"][0]["asOf"],
                "effectiveFrom": None,
                "status": "ready",
                "payload": {"states": []},
            })

        report = self.report(payload)

        self.assertEqual(
            report.missing_output_domains,
            ["market", "sector", "etf"],
        )
        self.assertEqual(report.ready_output_domains, ["leader"])
        self.assertIn("replay_rule_outputs_missing", report.reason_codes)

    def test_missing_required_historical_domain_is_not_ready_not_zero(self):
        payload = self.payload()
        payload["samples"][0]["evidence"] = [
            item for item in payload["samples"][0]["evidence"]
            if item["domain"] != "corporate_action"
        ]
        report = self.report(payload)

        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.included_count, 0)
        self.assertEqual(report.missing_count, 1)
        self.assertIn("corporate_action", report.missing_domains)
        self.assertIsNone(report.metrics["leader"])

    def test_unverifiable_source_is_reported_separately(self):
        payload = self.payload()
        payload["samples"][0]["evidence"][2]["status"] = "unverifiable"
        report = self.report(payload)

        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.unverifiable_count, 1)
        self.assertEqual(report.missing_count, 0)
        self.assertIn("replay_evidence_unverifiable", report.reason_codes)

    def test_ready_scoped_exclusions_are_counted_without_excluding_sample(self):
        payload = self.payload()
        industry = next(
            item for item in payload["samples"][0]["evidence"]
            if item["domain"] == "industry"
        )
        industry["payload"].update({
            "scopedReplayReady": True,
            "excludedCurrentMasterCount": 115,
        })
        corporate_action = next(
            item for item in payload["samples"][0]["evidence"]
            if item["domain"] == "corporate_action"
        )
        corporate_action["payload"].update({
            "scopedReplayReady": True,
            "excludedDocumentCount": 3,
            "excludedOutOfScopeCount": 1,
        })

        report = self.report(payload)

        self.assertEqual(report.included_count, 1)
        self.assertEqual(report.excluded_count, 0)
        self.assertEqual(report.scoped_exclusion_count, 119)
        self.assertEqual(report.scoped_exclusion_counts, {
            "corporate_action": 4,
            "industry": 115,
        })
        self.assertIn("replay_scoped_exclusions_present", report.reason_codes)

    def test_same_symbol_with_multiple_states_fails_closed(self):
        payload = deepcopy(self.payload())
        payload["samples"][0]["evidence"][-1]["payload"]["states"].append({
            "symbol": "000001",
            "state": "confirmed",
        })
        report = self.report(payload)

        self.assertEqual(report.status, "failed")
        self.assertEqual(report.multi_state_violation_count, 1)
        self.assertIn("replay_multi_state_violation", report.reason_codes)


if __name__ == "__main__":
    unittest.main()
