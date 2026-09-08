import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.replay_contracts import RadarReplayOutputBundle
from radar.replay_etf_research_store import (
    load_latest_replay_etf_research,
    publish_replay_etf_research,
)


UTC = timezone.utc
AS_OF = datetime(2026, 9, 7, 6, 45, tzinfo=UTC)


class RadarReplayEtfResearchStoreTests(unittest.TestCase):
    @staticmethod
    def bundle():
        source_sha = "b" * 64
        states = [{
            "targetId": "159999",
            "state": "active_product_separate_track",
        }, {
            "targetId": "510300",
            "state": "product_ready_for_index_research",
            "targetIndexName": "沪深300指数",
            "monitoringStatus": "ready",
            "rankingStatus": "missing",
            "monitoringReasons": [],
            "rankingReasons": ["etf_rule_not_frozen"],
        }]
        semantic = {
            "contractId": "radar-etf-product-research-output-v2",
            "sampleId": "sample-etf-1",
            "radarRunId": "run-etf-1",
            "asOf": AS_OF.isoformat(),
            "sourceSnapshotSha256": source_sha,
            "classificationMappingVersion": (
                "radar-etf-product-classification-v1"
            ),
            "states": states,
            "indexResearchReadyCount": 1,
            "activeSeparateTrackCount": 1,
            "outOfScopeAssetCount": 0,
            "evidenceIncompleteCount": 0,
            "formalAdmissionSnapshotSha256": "c" * 64,
            "formalAdmissionCount": 1,
            "monitoringReadyCount": 1,
            "monitoringMissingCount": 0,
            "rankingPolicyReadyCount": 0,
            "rankingPolicyMissingCount": 1,
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
            "bundleId": "output-bundle-etf-1",
            "createdAt": (AS_OF + timedelta(minutes=1)).isoformat(),
            "samples": [{
                "sampleId": "sample-etf-1",
                "radarRunId": "run-etf-1",
                "asOf": AS_OF.isoformat(),
                "evidence": [{
                    "evidenceId": f"etf-output:{snapshot_sha}",
                    "domain": "etf",
                    "sourceId": (
                        "radar-etf-product-research-output-v2:"
                        f"{snapshot_sha}"
                    ),
                    "source": "沪深交易所官方ETF产品分类与正式监测准入输出",
                    "sourceTime": AS_OF.isoformat(),
                    "fetchedAt": AS_OF.isoformat(),
                    "effectiveFrom": None,
                    "status": "ready",
                    "payload": {
                        "snapshotSha256": snapshot_sha,
                        "sourceSnapshotSha256": source_sha,
                        "ruleVersion": "radar-etf-product-research-rule-v1",
                        "classificationMappingVersion": (
                            "radar-etf-product-classification-v1"
                        ),
                        "productCount": 2,
                        "indexResearchReadyCount": 1,
                        "activeSeparateTrackCount": 1,
                        "outOfScopeAssetCount": 0,
                        "evidenceIncompleteCount": 0,
                        "rankingReadyCount": 0,
                        "states": states,
                        "researchOnly": True,
                        "rankingReady": False,
                        "formalUsable": False,
                        "stateTransitionAllowed": False,
                        "formalAdmissionSnapshotSha256": "c" * 64,
                        "formalAdmissionCount": 1,
                        "monitoringReadyCount": 1,
                        "monitoringMissingCount": 0,
                        "rankingPolicyReadyCount": 0,
                        "rankingPolicyMissingCount": 1,
                    },
                }],
            }],
        })

    def test_publish_and_load_preserves_four_state_research_semantics(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            published = publish_replay_etf_research(self.bundle(), root)
            loaded = load_latest_replay_etf_research(
                root,
                checked_at=AS_OF + timedelta(minutes=2),
            )

        self.assertEqual(published.status, "available")
        self.assertEqual(loaded.status, "available")
        snapshot = loaded.snapshot
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.product_count, 2)
        self.assertEqual(snapshot.index_research_ready_count, 1)
        self.assertEqual(snapshot.active_separate_track_count, 1)
        self.assertTrue(snapshot.research_only)
        self.assertFalse(snapshot.ranking_ready)
        self.assertFalse(snapshot.formal_usable)
        self.assertFalse(snapshot.state_transition_allowed)
        self.assertEqual(snapshot.items[1].monitoring_status, "ready")
        self.assertEqual(snapshot.items[1].ranking_status, "missing")

    def test_missing_store_is_not_ready(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            loaded = load_latest_replay_etf_research(Path(directory))

        self.assertEqual(loaded.status, "not_ready")
        self.assertEqual(
            loaded.reasons,
            ("radar_replay_etf_research_missing",),
        )

    def test_tampered_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_replay_etf_research(self.bundle(), root)
            manifest = json.loads(
                (root / "etf-latest.json").read_text(encoding="utf-8")
            )
            evidence = root / manifest["evidenceRelativePath"]
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            payload["productCount"] = 99
            evidence.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_latest_replay_etf_research(root)

        self.assertEqual(loaded.status, "failed")
        self.assertEqual(
            loaded.reasons,
            ("radar_replay_etf_research_hash_mismatch",),
        )

    def test_duplicate_symbol_or_unknown_state_is_rejected(self):
        bundle = self.bundle()
        evidence = bundle.samples[0].evidence[0]
        evidence.payload["states"][1]["targetId"] = "159999"
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                ValueError,
                "radar_replay_etf_research_unverified",
            ):
                publish_replay_etf_research(bundle, Path(directory))

    def test_internal_output_snapshot_hash_mismatch_is_rejected(self):
        bundle = self.bundle()
        evidence = bundle.samples[0].evidence[0]
        evidence.payload["snapshotSha256"] = "d" * 64
        object.__setattr__(
            evidence,
            "source_id",
            "radar-etf-product-research-output-v2:" + "d" * 64,
        )
        object.__setattr__(evidence, "evidence_id", "etf-output:" + "d" * 64)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                ValueError,
                "radar_replay_etf_research_unverified",
            ):
                publish_replay_etf_research(bundle, Path(directory))


if __name__ == "__main__":
    unittest.main()
