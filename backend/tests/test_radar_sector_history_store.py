import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.api import router as radar_router
from radar.sector_history_store import (
    load_latest_sector_history_evidence,
    publish_sector_history_evidence,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)


def evidence_payload():
    return {
        "contractId": "radar-sector-history-automatic-backfill-v1",
        "status": "ready",
        "reasons": [],
        "radarRunId": "sector-history-real-1",
        "asOf": "2026-08-21T22:30:00+08:00",
        "requestIdentity": "a" * 64,
        "requestedCount": 5153,
        "fetchedCount": 5153,
        "reusedCount": 0,
        "failureCount": 0,
        "tradingPresence": {
            "contractId": "radar-sector-trading-presence-v1",
            "sourceContractIds": [
                "sina-daily-trading-presence-v1",
                "tencent-qfq-daily-trading-presence-v1",
            ],
            "sourceStatus": "ready",
            "requestedCount": 76,
            "returnedCount": 76,
            "failureCount": 0,
            "failureReasonCounts": {},
        },
        "analysis": {
            "contractId": "radar-sector-history-backfill-v1",
            "status": "ready",
            "reasons": [],
            "sectorCount": 81,
            "marketSampleCount": 32,
            "historyCoverageReady": True,
            "calibrationProposal": {
                "status": "proposal_ready",
                "observationDateCount": 20,
                "industryCount": 81,
                "marketRegimes": ["non_positive", "positive"],
                "trainEndDate": "2026-08-12",
                "holdoutStartDate": "2026-08-13",
                "metricQuantiles": {
                    "turnoverRatio20d": {
                        "q25": 0.8,
                        "q50": 0.95,
                        "q75": 1.12,
                    },
                    "relativeReturn": {
                        "q25": -0.008,
                        "q50": 0.0,
                        "q75": 0.006,
                    },
                    "persistencePositiveRatio5d": {
                        "q25": 0.4,
                        "q50": 0.4,
                        "q75": 0.6,
                    },
                },
                "metricSampleCounts": {
                    "turnoverRatio20d": 1134,
                    "relativeReturn": 1134,
                    "persistencePositiveRatio5d": 1134,
                },
                "trainObservationDateCount": 14,
                "holdoutObservationDateCount": 6,
                "holdoutMetricSampleCounts": {
                    "turnoverRatio20d": 486,
                    "relativeReturn": 486,
                    "persistencePositiveRatio5d": 486,
                },
                "reasons": [],
                "formalApproval": False,
            },
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
            "marketLatest": {
                "tradeDate": "2026-08-20",
                "equalWeightedReturn": 0.001,
                "marketCapWeightedReturn": 0.002,
            },
            "sectors": [],
        },
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }


class SectorHistoryStoreTests(unittest.TestCase):
    def test_publish_then_new_reader_loads_verified_real_summary(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            evidence = root / "sector-history-a" / "evidence.json"
            evidence.parent.mkdir()
            evidence.write_text(
                json.dumps(evidence_payload(), ensure_ascii=False),
                encoding="utf-8",
            )

            published = publish_sector_history_evidence(
                evidence,
                store_dir=root,
                published_at=NOW,
            )
            loaded = load_latest_sector_history_evidence(store_dir=root)

            self.assertEqual(published.status, "available")
            self.assertEqual(loaded.status, "available")
            self.assertEqual(loaded.payload["requestedCount"], 5153)
            self.assertEqual(
                loaded.payload["analysis"]["sectorCount"],
                81,
            )
            self.assertFalse(
                loaded.payload["analysis"]["calibrationProposal"]
                ["formalApproval"]
            )
            manifest = json.loads(
                (root / "latest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(Path(manifest["evidenceRelativePath"]).is_absolute())
            self.assertNotIn("seriesBySymbol", json.dumps(manifest))

    def test_modified_evidence_is_rejected_instead_of_served(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            evidence = root / "sector-history-a" / "evidence.json"
            evidence.parent.mkdir()
            evidence.write_text(json.dumps(evidence_payload()), encoding="utf-8")
            publish_sector_history_evidence(
                evidence,
                store_dir=root,
                published_at=NOW,
            )
            evidence.write_text("{}", encoding="utf-8")

            loaded = load_latest_sector_history_evidence(store_dir=root)

            self.assertEqual(loaded.status, "failed")
            self.assertEqual(
                loaded.reasons,
                ("sector_history_store_evidence_hash_mismatch",),
            )
            self.assertIsNone(loaded.payload)

    def test_api_exposes_persisted_history_without_reading_sqlite(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            evidence = root / "sector-history-a" / "evidence.json"
            evidence.parent.mkdir()
            evidence.write_text(json.dumps(evidence_payload()), encoding="utf-8")
            publish_sector_history_evidence(
                evidence,
                store_dir=root,
                published_at=NOW,
            )
            app = FastAPI()
            app.include_router(radar_router)

            with patch(
                "radar.api._sector_history_store_path",
                return_value=root,
            ), patch(
                "radar.api.open_radar_read_connection",
                side_effect=AssertionError("历史接口不应读取生产SQLite"),
            ):
                response = TestClient(app).get(
                    "/api/radar/sector-history"
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["schemaVersion"], "radar-sector-history-v1")
            self.assertEqual(payload["state"], "available")
            self.assertEqual(payload["requestedCount"], 5153)
            self.assertEqual(payload["sectorCount"], 81)
            self.assertEqual(payload["metricSampleCounts"]["relativeReturn"], 1134)
            self.assertEqual(payload["holdoutMetricSampleCounts"]["relativeReturn"], 486)
            self.assertEqual(payload["thresholdReviewState"], "review_ready")
            self.assertEqual(len(payload["calibrationIdentity"]), 64)
            self.assertEqual(
                payload["thresholdReviewReasonCodes"],
                ["sector_threshold_approval_snapshot_missing"],
            )
            self.assertFalse(payload["formalApproval"])
            self.assertFalse(payload["gate"]["formalGateReady"])
            self.assertNotIn("evidencePath", payload)

    def test_api_returns_not_ready_when_store_has_no_snapshot(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            app = FastAPI()
            app.include_router(radar_router)
            with patch(
                "radar.api._sector_history_store_path",
                return_value=Path(directory),
            ):
                response = TestClient(app).get(
                    "/api/radar/sector-history"
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "not_ready")
            self.assertEqual(
                response.json()["reasonCodes"],
                ["sector_history_store_snapshot_missing"],
            )


if __name__ == "__main__":
    unittest.main()
