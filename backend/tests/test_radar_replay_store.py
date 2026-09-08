import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.replay_service import RadarReplayQualityReport


UTC = timezone.utc
NOW = datetime(2026, 9, 1, 3, 30, tzinfo=UTC)


class RadarReplayStoreTests(unittest.TestCase):
    def report(self, *, created_at=NOW):
        return RadarReplayQualityReport(
            replayRunId="replay-1",
            createdAt=created_at,
            status="not_ready",
            sampleCounts={"development": 0, "calibration": 0, "holdout": 0},
            includedCount=0,
            excludedCount=0,
            missingCount=1,
            unverifiableCount=0,
            failedCount=0,
            futureViolationCount=0,
            duplicateStateViolationCount=0,
            multiStateViolationCount=0,
            missingDomains=["security_universe"],
            reasonCodes=["replay_required_domain_missing"],
            metrics={"market": None, "sector": None, "etf": None, "leader": None},
        )

    def test_missing_store_is_not_ready(self):
        from radar.replay_store import load_latest_replay_report

        with tempfile.TemporaryDirectory() as directory:
            result = load_latest_replay_report(Path(directory), checked_at=NOW)

        self.assertEqual(result.status, "not_ready")
        self.assertEqual(result.reasons, ("radar_replay_report_missing",))

    def test_published_report_round_trips_with_semantic_hash(self):
        from radar.replay_store import (
            load_latest_replay_report,
            publish_replay_report,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published = publish_replay_report(self.report(), root)
            loaded = load_latest_replay_report(root, checked_at=NOW)

        self.assertEqual(loaded.status, "available")
        self.assertEqual(loaded.evidence_sha256, published.evidence_sha256)
        self.assertEqual(loaded.report.replay_run_id, "replay-1")

    def test_tampered_report_fails_closed(self):
        from radar.replay_store import (
            load_latest_replay_report,
            publish_replay_report,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_replay_report(self.report(), root)
            manifest = json.loads((root / "latest.json").read_text())
            evidence = root / manifest["evidenceRelativePath"]
            payload = json.loads(evidence.read_text())
            payload["includedCount"] = 9
            evidence.write_text(json.dumps(payload))
            result = load_latest_replay_report(root, checked_at=NOW)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reasons, ("radar_replay_report_hash_mismatch",))

    def test_future_report_is_rejected(self):
        from radar.replay_store import (
            load_latest_replay_report,
            publish_replay_report,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_replay_report(self.report(created_at=NOW + timedelta(seconds=6)), root)
            result = load_latest_replay_report(root, checked_at=NOW)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reasons, ("radar_replay_report_from_future",))

    def test_legacy_quality_contract_cannot_bypass_current_label_gate(self):
        from radar.replay_store import _canonical, load_latest_replay_report

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self.report().model_dump(mode="json", by_alias=True)
            payload["contractId"] = "radar-replay-quality-v1"
            digest = hashlib.sha256(_canonical(payload)).hexdigest()
            evidence = root / "snapshots" / f"{digest}.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(json.dumps(payload), encoding="utf-8")
            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-replay-quality-manifest-v1",
                "evidenceRelativePath": f"snapshots/{digest}.json",
                "evidenceSha256": digest,
                "replayRunId": payload["replayRunId"],
                "createdAt": NOW.isoformat(),
            }), encoding="utf-8")

            result = load_latest_replay_report(root, checked_at=NOW)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reasons, ("radar_replay_report_unverified",))


if __name__ == "__main__":
    unittest.main()
