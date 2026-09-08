import tempfile
import unittest
from datetime import timedelta
import inspect
import json
from pathlib import Path

from tests.test_radar_sector_state_producer import AS_OF, observation


class RadarReplayOutputBridgeTests(unittest.TestCase):
    def sector_snapshot(self, root: Path):
        from radar.sector_state_producer import (
            SECTOR_STATE_TRANSITION_POLICY_VERSION,
            build_initial_sector_state_snapshot,
            produce_sector_state_snapshot,
            write_sector_state_snapshot,
        )

        value = observation(quote_batch_id="run-1-quotes")
        previous = build_initial_sector_state_snapshot(
            industry_codes=("01",),
            classification_document_sha256="c" * 64,
            rule_version="radar-sector-v0-shadow",
            transition_policy_version=SECTOR_STATE_TRANSITION_POLICY_VERSION,
            threshold_set_id=value.approval_record.threshold_set_id,
            approval_id=value.approval_record.approval_id,
            observed_before=AS_OF - timedelta(microseconds=1),
        )
        produced = produce_sector_state_snapshot(
            value,
            previous_snapshot=previous,
        )
        path = root / "sector-state.json"
        write_sector_state_snapshot(produced.next_snapshot, path)
        return path

    def etf_replay_input(self, root: Path):
        from radar.replay_source_adapters import _with_hash

        fetched_at = AS_OF - timedelta(seconds=5)
        records = [
            {
                "symbol": "510300",
                "officialName": "沪深300ETF",
                "exchange": "sse",
                "productType": "etf",
                "managementStyle": "passive_index",
                "assetClass": "domestic_equity",
                "sourceCategoryCode": "F112",
                "sourceCategoryName": "跨市场股票（沪深京）ETF",
                "sourceInvestmentType": None,
                "targetIndexName": "沪深300指数",
                "listingDate": "2012-05-28",
                "manager": "测试基金公司",
                "classificationMappingVersion": (
                    "radar-etf-product-classification-v2"
                ),
                "classificationReasons": [],
                "source": "sse_official_fund_list",
                "fetchedAt": fetched_at.isoformat(),
                "sourceFields": {"INDEX_NAME": "沪深300指数"},
            },
            {
                "symbol": "159999",
                "officialName": "测试主动ETF",
                "exchange": "szse",
                "productType": "etf",
                "managementStyle": "active",
                "assetClass": "unknown",
                "sourceCategoryCode": None,
                "sourceCategoryName": "ETF",
                "sourceInvestmentType": "股票基金",
                "targetIndexName": None,
                "listingDate": "2026-07-01",
                "manager": "测试基金公司",
                "classificationMappingVersion": (
                    "radar-etf-product-classification-v2"
                ),
                "classificationReasons": ["equity_region_unverified"],
                "source": "szse_official_fund_list",
                "fetchedAt": fetched_at.isoformat(),
                "sourceFields": {"基金简称": "测试主动ETF"},
            },
        ]
        payload = _with_hash({
            "observationKind": "forward_observed",
            "recordCount": 2,
            "expectedCount": 2,
            "rowCoverage": 1.0,
            "records": records,
            "issues": [],
            "reasons": [],
        })
        path = root / "replay-input.json"
        path.write_text(json.dumps({
            "replayRunId": "replay-run-1",
            "createdAt": (AS_OF + timedelta(minutes=2)).isoformat(),
            "samples": [{
                "sampleId": "sample-1",
                "role": "calibration",
                "asOf": (AS_OF + timedelta(minutes=1)).isoformat(),
                "radarRunId": "run-1",
                "ruleVersion": "radar-replay-forward-v1",
                "evidence": [{
                    "evidenceId": "etf-product-master:test",
                    "domain": "etf",
                    "sourceId": "official:test",
                    "source": (
                        "official_exchange_listed_fund_product_master"
                    ),
                    "sourceTime": None,
                    "fetchedAt": fetched_at.isoformat(),
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": payload,
                }],
                "expectedLabels": [],
            }],
        }), encoding="utf-8")
        return path

    def etf_formal_admission_bundle(self, root: Path):
        from radar.etf_formal_admission import (
            ETF_FORMAL_ADMISSION_ITEM_KEYS,
            EtfFormalAdmissionEvidence,
            EtfFormalAdmissionItem,
            EtfFormalAdmissionStatus,
            build_etf_formal_admission_bundle,
        )

        items = tuple(
            EtfFormalAdmissionItem(
                key=key,
                status=(
                    EtfFormalAdmissionStatus.MISSING
                    if key == "rule_policy"
                    else EtfFormalAdmissionStatus.READY
                ),
                reasons=(
                    (
                        "etf_rule_not_frozen",
                        "ranking_calibration_sample_missing",
                    )
                    if key == "rule_policy"
                    else ()
                ),
            )
            for key in ETF_FORMAL_ADMISSION_ITEM_KEYS
        )
        admission = EtfFormalAdmissionEvidence(
            symbol="510300",
            as_of=AS_OF + timedelta(minutes=1),
            rule_version="radar-etf-rule-v1",
            status=EtfFormalAdmissionStatus.MISSING,
            monitoring_status=EtfFormalAdmissionStatus.READY,
            ranking_status=EtfFormalAdmissionStatus.MISSING,
            items=items,
            reasons=(
                "etf_rule_not_frozen",
                "ranking_calibration_sample_missing",
            ),
        )
        bundle = build_etf_formal_admission_bundle(
            sample_id="sample-1",
            radar_run_id="run-1",
            as_of=AS_OF + timedelta(minutes=1),
            admissions=(admission,),
        )
        path = root / "etf-formal-admission.json"
        path.write_text(
            json.dumps(bundle.to_evidence()),
            encoding="utf-8",
        )
        return path

    def test_exports_verified_sector_states_for_exact_sample_identity(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            result = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=snapshot_path,
                output_dir=root / "output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )

            self.assertTrue(result.output_bundle_path.is_file())
            output_set = result.bundle.samples[0]
            self.assertEqual(output_set.sample_id, "sample-1")
            self.assertEqual(output_set.radar_run_id, "run-1")
            evidence = output_set.evidence[0]
            self.assertEqual(evidence.domain, "sector")
            self.assertEqual(evidence.status, "ready")
            self.assertEqual(evidence.payload["states"], [{
                "targetId": "01",
                "state": "observe",
            }])
            self.assertEqual(
                evidence.payload["snapshotSha256"],
                result.snapshot_sha256,
            )

    def test_exports_same_run_market_research_state_without_marking_formal(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            stage6_path = root / "stage6.json"
            stage6_path.write_text(json.dumps({
                "marketResearchState": {
                    "contractId": "radar-market-research-state-v1",
                    "status": "ready",
                    "radarRunId": "run-1",
                    "asOf": AS_OF.isoformat(),
                    "state": "strong",
                    "metrics": {"advancerRatio": 0.7},
                    "reasons": [],
                    "snapshotSha256": "d" * 64,
                    "ruleVersion": "radar-market-research-rule-v1",
                    "researchUsable": True,
                    "formalUsable": False,
                },
            }), encoding="utf-8")
            sector_only = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=snapshot_path,
                output_dir=root / "sector-only-output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )
            result = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=snapshot_path,
                stage6_artifact_path=stage6_path,
                output_dir=root / "output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )

            self.assertNotEqual(
                result.bundle.bundle_id,
                sector_only.bundle.bundle_id,
            )

            market = next(
                item for item in result.bundle.samples[0].evidence
                if item.domain == "market"
            )
            self.assertEqual(market.status, "ready")
            self.assertEqual(market.payload["states"], [{
                "targetId": "a-share",
                "state": "strong",
            }])
            self.assertTrue(market.payload["researchOnly"])
            self.assertFalse(market.payload["formalUsable"])

    def test_exports_same_run_leader_research_qualification_without_formal_state(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            stage6_path = root / "stage6.json"
            stage6_path.write_text(json.dumps({
                "collection": {
                    "contractId": "radar-leader-phase6-live-source-collection-v1",
                    "status": "ready_for_review",
                    "validEmptyResult": False,
                    "qualification": {
                        "contractId": "radar-leader-evidence-qualification-v1",
                        "status": "ready",
                        "qualificationId": "a" * 64,
                        "parentCandidatePlanId": "parent-plan",
                        "parentCandidateCount": 2,
                        "qualifiedCandidatePlanId": "qualified-plan",
                        "qualifiedCandidateCount": 1,
                        "excludedCandidateCount": 1,
                        "gate": {
                            "formalScoreReady": False,
                            "formalGateReady": False,
                            "formalUsable": False,
                            "stateTransitionAllowed": False,
                        },
                    },
                    "stateDecisionReview": {
                        "contractId": "radar-leader-phase6-state-decision-review-v1",
                        "status": "ready_for_review",
                        "radarRunId": "run-1",
                        "asOf": AS_OF.isoformat(),
                        "parentCandidatePlanId": "parent-plan",
                        "parentCandidateCount": 2,
                        "qualifiedCandidatePlanId": "qualified-plan",
                        "qualifiedCandidateCount": 1,
                        "excludedCandidateCount": 1,
                        "qualificationId": "a" * 64,
                        "items": [
                            {
                                "index": 0,
                                "symbol": "000001",
                                "industryCode": "01",
                                "qualificationStatus": "qualified",
                                "qualifiedCandidateIndex": 0,
                                "reviewEligible": True,
                                "firstRejectionReason": "formal_rule_not_ready",
                                "reasons": ["formal_rule_not_ready"],
                            },
                            {
                                "index": 1,
                                "symbol": "000002",
                                "industryCode": "01",
                                "qualificationStatus": "excluded",
                                "qualifiedCandidateIndex": None,
                                "reviewEligible": False,
                                "firstRejectionReason": "business_missing",
                                "reasons": ["business_missing"],
                            },
                        ],
                        "gate": {
                            "formalScoreReady": False,
                            "formalGateReady": False,
                            "formalUsable": False,
                            "stateTransitionAllowed": False,
                        },
                    },
                },
            }), encoding="utf-8")
            result = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=snapshot_path,
                stage6_artifact_path=stage6_path,
                output_dir=root / "output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )

            leader = next(
                item for item in result.bundle.samples[0].evidence
                if item.domain == "leader"
            )
            self.assertEqual(leader.status, "ready")
            self.assertEqual(leader.payload["states"], [{
                "targetId": "000001",
                "state": "research_qualified",
                "industryCode": "01",
                "firstRejectionReason": "formal_rule_not_ready",
            }])
            self.assertEqual(leader.payload["evaluatedCount"], 2)
            self.assertEqual(leader.payload["qualifiedCount"], 1)
            self.assertTrue(leader.payload["researchOnly"])
            self.assertFalse(leader.payload["formalUsable"])
            self.assertFalse(leader.payload["stateTransitionAllowed"])
            self.assertEqual(
                leader.payload["snapshotSha256"],
                result.leader_snapshot_sha256,
            )

    def test_exports_etf_product_research_states_without_fake_ranking(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            result = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=self.sector_snapshot(root),
                etf_replay_input_path=self.etf_replay_input(root),
                output_dir=root / "output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )

            etf = next(
                item for item in result.bundle.samples[0].evidence
                if item.domain == "etf"
            )
            self.assertEqual(etf.status, "ready")
            self.assertEqual(etf.payload["productCount"], 2)
            self.assertEqual(etf.payload["indexResearchReadyCount"], 1)
            self.assertEqual(etf.payload["activeSeparateTrackCount"], 1)
            self.assertEqual(etf.payload["rankingReadyCount"], 0)
            self.assertEqual(etf.payload["states"], [
                {
                    "targetId": "159999",
                    "state": "active_product_separate_track",
                },
                {
                    "targetId": "510300",
                    "state": "product_ready_for_index_research",
                    "targetIndexName": "沪深300指数",
                },
            ])
            self.assertTrue(etf.payload["researchOnly"])
            self.assertFalse(etf.payload["formalUsable"])
            self.assertFalse(etf.payload["rankingReady"])
            self.assertEqual(
                etf.payload["snapshotSha256"],
                result.etf_snapshot_sha256,
            )

    def test_exports_explicit_etf_monitoring_and_ranking_readiness(self):
        from radar.replay_output_bridge import export_sector_replay_output

        supports_formal_admission = (
            "etf_formal_admission_path"
            in inspect.signature(export_sector_replay_output).parameters
        )
        self.assertTrue(supports_formal_admission)
        if not supports_formal_admission:
            return

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            result = export_sector_replay_output(
                sample_id="sample-1",
                radar_run_id="run-1",
                sample_as_of=AS_OF + timedelta(minutes=1),
                sector_snapshot_path=self.sector_snapshot(root),
                etf_replay_input_path=self.etf_replay_input(root),
                etf_formal_admission_path=(
                    self.etf_formal_admission_bundle(root)
                ),
                output_dir=root / "output",
                clock=lambda: AS_OF + timedelta(minutes=2),
            )

            etf = next(
                item for item in result.bundle.samples[0].evidence
                if item.domain == "etf"
            )
            self.assertEqual(
                etf.source_id.split(":", 1)[0],
                "radar-etf-product-research-output-v2",
            )
            self.assertEqual(etf.payload["monitoringReadyCount"], 1)
            self.assertEqual(etf.payload["monitoringMissingCount"], 0)
            self.assertEqual(etf.payload["rankingPolicyReadyCount"], 0)
            self.assertEqual(etf.payload["rankingPolicyMissingCount"], 1)
            state = next(
                item for item in etf.payload["states"]
                if item["targetId"] == "510300"
            )
            self.assertEqual(state["monitoringStatus"], "ready")
            self.assertEqual(state["rankingStatus"], "missing")
            self.assertEqual(state["monitoringReasons"], [])
            self.assertEqual(state["rankingReasons"], [
                "etf_rule_not_frozen",
                "ranking_calibration_sample_missing",
            ])
            self.assertFalse(etf.payload["rankingReady"])
            self.assertFalse(etf.payload["formalUsable"])

    def test_rejects_tampered_etf_formal_admission_bundle(self):
        from radar.replay_output_bridge import export_sector_replay_output

        supports_formal_admission = (
            "etf_formal_admission_path"
            in inspect.signature(export_sector_replay_output).parameters
        )
        self.assertTrue(supports_formal_admission)
        if not supports_formal_admission:
            return

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            admission_path = self.etf_formal_admission_bundle(root)
            raw = json.loads(admission_path.read_text(encoding="utf-8"))
            raw["admissions"][0]["monitoringStatus"] = "missing"
            admission_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "etf_formal_admission_artifact_unverified",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="run-1",
                    sample_as_of=AS_OF + timedelta(minutes=1),
                    sector_snapshot_path=self.sector_snapshot(root),
                    etf_replay_input_path=self.etf_replay_input(root),
                    etf_formal_admission_path=admission_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )

    def test_rejects_tampered_etf_product_snapshot(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            etf_path = self.etf_replay_input(root)
            payload = json.loads(etf_path.read_text(encoding="utf-8"))
            payload["samples"][0]["evidence"][0]["payload"][
                "recordCount"
            ] = 3
            etf_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "etf_output_artifact_unverified",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="run-1",
                    sample_as_of=AS_OF + timedelta(minutes=1),
                    sector_snapshot_path=self.sector_snapshot(root),
                    etf_replay_input_path=etf_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )

    def test_rejects_leader_research_qualification_count_mismatch(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            stage6_path = root / "stage6.json"
            stage6_path.write_text(json.dumps({
                "collection": {
                    "contractId": "radar-leader-phase6-live-source-collection-v1",
                    "status": "ready_for_review",
                    "validEmptyResult": False,
                    "qualification": {
                        "contractId": "radar-leader-evidence-qualification-v1",
                        "status": "ready",
                        "qualificationId": "a" * 64,
                        "parentCandidatePlanId": "parent-plan",
                        "parentCandidateCount": 1,
                        "qualifiedCandidatePlanId": "qualified-plan",
                        "qualifiedCandidateCount": 1,
                        "excludedCandidateCount": 0,
                        "gate": {
                            "formalScoreReady": False,
                            "formalGateReady": False,
                            "formalUsable": False,
                            "stateTransitionAllowed": False,
                        },
                    },
                    "stateDecisionReview": {
                        "contractId": "radar-leader-phase6-state-decision-review-v1",
                        "status": "ready_for_review",
                        "radarRunId": "run-1",
                        "asOf": AS_OF.isoformat(),
                        "parentCandidatePlanId": "parent-plan",
                        "parentCandidateCount": 1,
                        "qualifiedCandidatePlanId": "qualified-plan",
                        "qualifiedCandidateCount": 2,
                        "excludedCandidateCount": 0,
                        "qualificationId": "a" * 64,
                        "items": [],
                        "gate": {
                            "formalScoreReady": False,
                            "formalGateReady": False,
                            "formalUsable": False,
                            "stateTransitionAllowed": False,
                        },
                    },
                },
            }), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "leader_output_artifact_unverified",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="run-1",
                    sample_as_of=AS_OF + timedelta(minutes=1),
                    sector_snapshot_path=snapshot_path,
                    stage6_artifact_path=stage6_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )

    def test_rejects_market_research_state_from_another_run(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            stage6_path = root / "stage6.json"
            stage6_path.write_text(json.dumps({
                "marketResearchState": {
                    "contractId": "radar-market-research-state-v1",
                    "status": "ready",
                    "radarRunId": "wrong-run",
                    "asOf": AS_OF.isoformat(),
                    "state": "strong",
                    "metrics": {},
                    "reasons": [],
                    "snapshotSha256": "d" * 64,
                    "ruleVersion": "radar-market-research-rule-v1",
                    "researchUsable": True,
                    "formalUsable": False,
                },
            }), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "market_output_radar_run_mismatch",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="run-1",
                    sample_as_of=AS_OF + timedelta(minutes=1),
                    sector_snapshot_path=snapshot_path,
                    stage6_artifact_path=stage6_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )

    def test_rejects_snapshot_from_another_radar_run(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            with self.assertRaisesRegex(
                ValueError,
                "sector_output_radar_run_mismatch",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="another-run",
                    sample_as_of=AS_OF + timedelta(minutes=1),
                    sector_snapshot_path=snapshot_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )

    def test_rejects_output_observed_after_sample(self):
        from radar.replay_output_bridge import export_sector_replay_output

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            snapshot_path = self.sector_snapshot(root)
            with self.assertRaisesRegex(
                ValueError,
                "sector_output_from_future",
            ):
                export_sector_replay_output(
                    sample_id="sample-1",
                    radar_run_id="run-1",
                    sample_as_of=AS_OF - timedelta(seconds=1),
                    sector_snapshot_path=snapshot_path,
                    output_dir=root / "output",
                    clock=lambda: AS_OF + timedelta(minutes=2),
                )


if __name__ == "__main__":
    unittest.main()
