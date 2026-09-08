import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.api import router as radar_router
from radar.replay_service import RadarReplayQualityReport
from radar.replay_contracts import RadarReplayOutputBundle
from radar.replay_etf_research_store import publish_replay_etf_research
from radar.replay_store import publish_replay_report


class RadarReplayApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(radar_router)
        self.client = TestClient(app)

    @staticmethod
    def report(
        *,
        status: str = "ready",
        pipeline_status: str = "ready",
    ) -> RadarReplayQualityReport:
        created_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return RadarReplayQualityReport(
            replayRunId="replay-api-1",
            createdAt=created_at,
            status=status,
            pipelineStatus=pipeline_status,
            effectivenessStatus=(
                "ready" if status == "ready" else "collecting"
            ),
            sampleCounts={"development": 2, "calibration": 1, "holdout": 1},
            includedCount=4,
            excludedCount=0,
            scopedExclusionCount=3,
            scopedExclusionCounts={"corporate_action": 3},
            missingCount=0,
            unverifiableCount=0,
            failedCount=0,
            futureViolationCount=0,
            duplicateStateViolationCount=0,
            multiStateViolationCount=0,
            labelCounts={"market": 1, "sector": 1, "etf": 1, "leader": 1},
            outputCounts={"market": 1, "sector": 2, "etf": 3, "leader": 4},
            etfReadinessCounts={
                "formalAdmissionCount": 2,
                "monitoringReadyCount": 1,
                "monitoringMissingCount": 1,
                "rankingPolicyReadyCount": 0,
                "rankingPolicyMissingCount": 2,
            },
            comparableLabelCount=4,
            incomparableLabelCount=0,
            disputedLabelCount=0,
            unverifiableLabelCount=0,
            unlabeledOutputTargetCount=0,
            unlabeledOutputTargetCounts={
                "market": 0,
                "sector": 0,
                "etf": 0,
                "leader": 0,
            },
            partitionChronologyValid=True,
            missingDomains=[],
            missingLabelDomains=[],
            missingOutputDomains=[],
            unverifiableOutputDomains=[],
            failedOutputDomains=[],
            readyOutputDomains=["market", "sector", "etf", "leader"],
            missingLabelPartitions=[],
            reasonCodes=[],
            metrics={"market": None, "sector": None, "etf": None, "leader": None},
        )

    @staticmethod
    def etf_output_bundle():
        as_of = datetime.now(timezone.utc) - timedelta(minutes=2)
        source_sha = "b" * 64
        states = [{
            "targetId": "510300",
            "state": "product_ready_for_index_research",
            "targetIndexName": "沪深300指数",
        }]
        semantic = {
            "contractId": "radar-etf-product-research-output-v1",
            "sampleId": "api-etf-sample-1",
            "radarRunId": "api-etf-run-1",
            "asOf": as_of.isoformat(),
            "sourceSnapshotSha256": source_sha,
            "classificationMappingVersion": "classification-v1",
            "states": states,
            "indexResearchReadyCount": 1,
            "activeSeparateTrackCount": 0,
            "outOfScopeAssetCount": 0,
            "evidenceIncompleteCount": 0,
        }
        snapshot_sha = hashlib.sha256(json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return RadarReplayOutputBundle.model_validate({
            "contractId": "radar-replay-output-bundle-v1",
            "bundleId": "api-etf-output-1",
            "createdAt": (as_of + timedelta(minutes=1)).isoformat(),
            "samples": [{
                "sampleId": "api-etf-sample-1",
                "radarRunId": "api-etf-run-1",
                "asOf": as_of.isoformat(),
                "evidence": [{
                    "evidenceId": f"etf-output:{snapshot_sha}",
                    "domain": "etf",
                    "sourceId": (
                        "radar-etf-product-research-output-v1:"
                        f"{snapshot_sha}"
                    ),
                    "source": "沪深交易所官方ETF产品分类研究输出",
                    "sourceTime": as_of.isoformat(),
                    "fetchedAt": as_of.isoformat(),
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {
                        "snapshotSha256": snapshot_sha,
                        "sourceSnapshotSha256": source_sha,
                        "ruleVersion": "radar-etf-product-research-rule-v1",
                        "classificationMappingVersion": "classification-v1",
                        "productCount": 1,
                        "indexResearchReadyCount": 1,
                        "activeSeparateTrackCount": 0,
                        "outOfScopeAssetCount": 0,
                        "evidenceIncompleteCount": 0,
                        "rankingReadyCount": 0,
                        "states": states,
                        "researchOnly": True,
                        "rankingReady": False,
                        "formalUsable": False,
                        "stateTransitionAllowed": False,
                    },
                }],
            }],
        })

    def test_latest_etf_research_is_read_only_and_explicit(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            published = publish_replay_etf_research(
                self.etf_output_bundle(),
                root,
            )
            with patch(
                "radar.api._replay_store_path",
                return_value=root,
            ), patch(
                "radar.api.open_radar_read_connection",
                side_effect=AssertionError("ETF研究状态接口不应读SQLite"),
            ):
                response = self.client.get("/api/radar/replays/latest/etfs")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        payload = response.json()
        self.assertEqual(payload["schemaVersion"], "radar-replay-etf-research-v1")
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["quality"], "complete")
        self.assertEqual(payload["evidenceSha256"], published.evidence_sha256)
        self.assertEqual(payload["snapshot"]["productCount"], 1)
        self.assertEqual(
            payload["snapshot"]["items"][0]["researchState"],
            "product_ready_for_index_research",
        )
        self.assertFalse(payload["snapshot"]["formalUsable"])

    def test_missing_latest_etf_research_is_explicit_not_ready(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, patch(
            "radar.api._replay_store_path",
            return_value=Path(directory),
        ):
            response = self.client.get("/api/radar/replays/latest/etfs")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "not_ready")
        self.assertEqual(payload["quality"], "unavailable")
        self.assertIsNone(payload["snapshot"])
        self.assertEqual(
            payload["reasonCodes"],
            ["radar_replay_etf_research_missing"],
        )

    def test_missing_report_is_explicit_not_ready_without_sqlite(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, patch(
            "radar.api._replay_store_path",
            return_value=Path(directory),
        ), patch(
            "radar.api.open_radar_read_connection",
            side_effect=AssertionError("回放质量接口不应读取SQLite"),
        ):
            response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        payload = response.json()
        self.assertEqual(payload["schemaVersion"], "radar-replay-quality-v1")
        self.assertEqual(payload["state"], "not_ready")
        self.assertEqual(payload["quality"], "unavailable")
        self.assertEqual(payload["engineeringState"], "not_ready")
        self.assertEqual(payload["validationState"], "not_started")
        self.assertFalse(payload["shadowCollectionAllowed"])
        self.assertFalse(payload["stage9QualityGatePassed"])
        self.assertIsNone(payload["replayRunId"])
        self.assertEqual(payload["reasonCodes"], ["radar_replay_report_missing"])

    def test_available_report_exposes_literal_quality_counts(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            published = publish_replay_report(self.report(), root)
            with patch("radar.api._replay_store_path", return_value=root):
                response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "ready")
        self.assertEqual(payload["quality"], "complete")
        self.assertEqual(payload["engineeringState"], "complete")
        self.assertEqual(payload["validationState"], "validated")
        self.assertTrue(payload["shadowCollectionAllowed"])
        self.assertTrue(payload["stage9QualityGatePassed"])
        self.assertEqual(payload["replayRunId"], "replay-api-1")
        self.assertEqual(payload["evidenceSha256"], published.evidence_sha256)
        self.assertEqual(payload["sampleCounts"]["holdout"], 1)
        self.assertEqual(payload["includedCount"], 4)
        self.assertEqual(payload["scopedExclusionCount"], 3)
        self.assertEqual(
            payload["scopedExclusionCounts"],
            {"corporate_action": 3},
        )
        self.assertEqual(payload["comparableLabelCount"], 4)
        self.assertEqual(payload["unlabeledOutputTargetCount"], 0)
        self.assertEqual(payload["unlabeledOutputTargetCounts"]["sector"], 0)
        self.assertTrue(payload["partitionChronologyValid"])
        self.assertEqual(payload["labelCounts"]["leader"], 1)
        self.assertEqual(payload["outputCounts"]["sector"], 2)
        self.assertEqual(payload["etfReadinessCounts"], {
            "formalAdmissionCount": 2,
            "monitoringReadyCount": 1,
            "monitoringMissingCount": 1,
            "rankingPolicyReadyCount": 0,
            "rankingPolicyMissingCount": 2,
        })
        self.assertEqual(payload["missingLabelDomains"], [])
        self.assertEqual(payload["missingOutputDomains"], [])
        self.assertEqual(payload["unverifiableOutputDomains"], [])
        self.assertEqual(payload["failedOutputDomains"], [])
        self.assertEqual(
            payload["readyOutputDomains"],
            ["market", "sector", "etf", "leader"],
        )
        self.assertIsNone(payload["metrics"]["leader"])

    def test_partial_report_completes_engineering_without_faking_validation(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_replay_report(self.report(status="not_ready"), root)
            with patch("radar.api._replay_store_path", return_value=root):
                response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "not_ready")
        self.assertEqual(payload["quality"], "partial")
        self.assertEqual(payload["engineeringState"], "complete")
        self.assertEqual(payload["validationState"], "collecting")
        self.assertTrue(payload["shadowCollectionAllowed"])
        self.assertFalse(payload["stage9QualityGatePassed"])

    def test_incomplete_pipeline_is_not_called_engineering_complete(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_replay_report(self.report(
                status="not_ready",
                pipeline_status="not_ready",
            ), root)
            with patch("radar.api._replay_store_path", return_value=root):
                response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["engineeringState"], "not_ready")
        self.assertEqual(payload["validationState"], "collecting")
        self.assertFalse(payload["shadowCollectionAllowed"])

    def test_failed_quality_run_does_not_erase_completed_collection_pipeline(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_replay_report(self.report(status="failed"), root)
            with patch("radar.api._replay_store_path", return_value=root):
                response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["engineeringState"], "complete")
        self.assertEqual(payload["validationState"], "failed")
        self.assertTrue(payload["shadowCollectionAllowed"])
        self.assertFalse(payload["stage9QualityGatePassed"])

    def test_tampered_report_fails_closed(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_replay_report(self.report(), root)
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
            evidence_path = root / manifest["evidenceRelativePath"]
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            payload["includedCount"] = 99
            evidence_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("radar.api._replay_store_path", return_value=root):
                response = self.client.get("/api/radar/replays/latest")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "failed")
        self.assertEqual(response.json()["quality"], "unavailable")
        self.assertEqual(response.json()["engineeringState"], "failed")
        self.assertEqual(response.json()["validationState"], "failed")
        self.assertFalse(response.json()["shadowCollectionAllowed"])
        self.assertFalse(response.json()["stage9QualityGatePassed"])
        self.assertEqual(
            response.json()["reasonCodes"],
            ["radar_replay_report_hash_mismatch"],
        )


if __name__ == "__main__":
    unittest.main()
