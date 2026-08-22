import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from radar.sector_history_store import (
    load_latest_sector_history_evidence,
    publish_sector_history_evidence,
)
from radar.sector_rule_readiness import (
    REQUIRED_STATE_IDS,
    REQUIRED_THRESHOLD_POLICY_FIELDS,
)
from radar.sector_threshold_review import (
    approve_sector_threshold_review,
    bind_latest_sector_threshold_approval,
    build_sector_threshold_review_draft,
    load_sector_threshold_approval,
    publish_sector_threshold_approval,
)
from tests import test_radar_sector_history_store as history_helpers
from tests import test_radar_sector_rule_runtime_bridge as bridge_helpers


UTC = timezone.utc
PUBLISHED_AT = datetime(2026, 8, 22, 1, 30, tzinfo=UTC)
APPROVED_AT = datetime(2026, 8, 22, 2, 0, tzinfo=UTC)


def policies():
    values = {}
    for index, state_id in enumerate(sorted(REQUIRED_STATE_IDS)):
        values[state_id] = {
            "entry": {
                "metric": "relativeReturn",
                "operator": "gte",
                "value": round(index / 100, 4),
            },
            "hold": {
                "metric": "persistencePositiveRatio5d",
                "operator": "gte",
                "value": 0.4,
            },
            "exit": {
                "metric": "relativeReturn",
                "operator": "lt",
                "value": -0.01,
            },
            "consecutive_observations": 2,
            "minimum_hold_time": 180,
            "cooldown": 300,
            "data_failure_behavior": "stop_evaluation",
        }
    return values


def publish_history(root: Path, *, payload=None):
    evidence = root / "sector-history-a" / "evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(
            payload or history_helpers.evidence_payload(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_sector_history_evidence(
        evidence,
        store_dir=root,
        published_at=PUBLISHED_AT,
    )
    return load_latest_sector_history_evidence(store_dir=root)


class SectorThresholdReviewTests(unittest.TestCase):
    def test_real_calibration_builds_deterministic_unapproved_review_draft(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            stored = publish_history(Path(directory))

            first = build_sector_threshold_review_draft(stored)
            second = build_sector_threshold_review_draft(stored)

            self.assertEqual(first.status, "review_ready")
            self.assertEqual(first.calibration_identity, second.calibration_identity)
            self.assertEqual(len(first.calibration_identity), 64)
            self.assertEqual(first.train_observation_date_count, 14)
            self.assertEqual(first.holdout_observation_date_count, 6)
            self.assertEqual(first.metric_sample_counts["relativeReturn"], 1134)
            self.assertEqual(first.holdout_metric_sample_counts["relativeReturn"], 486)
            self.assertEqual(set(first.required_state_ids), REQUIRED_STATE_IDS)
            self.assertEqual(
                set(first.required_policy_fields),
                REQUIRED_THRESHOLD_POLICY_FIELDS,
            )
            self.assertFalse(first.formal_approval)

    def test_incomplete_policy_cannot_be_approved(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            draft = build_sector_threshold_review_draft(
                publish_history(Path(directory))
            )
            incomplete = policies()
            del incomplete["observe"]["exit"]

            with self.assertRaisesRegex(ValueError, "scope_incomplete"):
                approve_sector_threshold_review(
                    draft,
                    state_policies=incomplete,
                    approved_by="user",
                    approved_at=APPROVED_AT,
                )

    def test_approval_time_must_follow_published_review_and_not_be_future(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            draft = build_sector_threshold_review_draft(
                publish_history(Path(directory))
            )

            with self.assertRaisesRegex(ValueError, "approval_before_review"):
                approve_sector_threshold_review(
                    draft,
                    state_policies=policies(),
                    approved_by="user",
                    approved_at=datetime(2026, 8, 22, 1, 29, tzinfo=UTC),
                    observed_at=APPROVED_AT,
                )
            with self.assertRaisesRegex(ValueError, "approval_in_future"):
                approve_sector_threshold_review(
                    draft,
                    state_policies=policies(),
                    approved_by="user",
                    approved_at=datetime(2026, 8, 22, 2, 1, tzinfo=UTC),
                    observed_at=APPROVED_AT,
                )

    def test_approved_record_round_trips_and_returns_readiness_evidence(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored = publish_history(root)
            draft = build_sector_threshold_review_draft(stored)
            approval = approve_sector_threshold_review(
                draft,
                state_policies=policies(),
                approved_by="user",
                approved_at=APPROVED_AT,
            )

            publish_sector_threshold_approval(approval, store_dir=root)
            loaded = load_sector_threshold_approval(
                store_dir=root,
                history_evidence=stored,
            )

            self.assertEqual(loaded.status, "approved")
            self.assertEqual(loaded.reasons, ())
            self.assertEqual(
                loaded.evidence.threshold_set_id,
                approval.threshold_set_id,
            )
            self.assertEqual(loaded.evidence.approval_id, approval.approval_id)
            self.assertEqual(loaded.record.approved_by, "user")
            self.assertNotIn("statePolicies", loaded.to_evidence())

    def test_tamper_or_new_calibration_fails_closed(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored = publish_history(root)
            approval = approve_sector_threshold_review(
                build_sector_threshold_review_draft(stored),
                state_policies=policies(),
                approved_by="user",
                approved_at=APPROVED_AT,
            )
            publish_sector_threshold_approval(approval, store_dir=root)

            path = root / "threshold-approval.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["approvedBy"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            tampered = load_sector_threshold_approval(
                store_dir=root,
                history_evidence=stored,
            )
            self.assertEqual(tampered.status, "failed")
            self.assertEqual(
                tampered.reasons,
                ("sector_threshold_approval_hash_mismatch",),
            )

            publish_sector_threshold_approval(approval, store_dir=root)
            changed = history_helpers.evidence_payload()
            changed["analysis"]["calibrationProposal"]["metricQuantiles"] \
                ["relativeReturn"]["q75"] = 0.009
            changed_store = publish_history(root, payload=changed)
            stale = load_sector_threshold_approval(
                store_dir=root,
                history_evidence=changed_store,
            )
            self.assertEqual(stale.status, "not_ready")
            self.assertEqual(
                stale.reasons,
                ("sector_threshold_approval_calibration_mismatch",),
            )

    def test_latest_approved_record_binds_existing_runtime_source_batch(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored = publish_history(root)
            approval = approve_sector_threshold_review(
                build_sector_threshold_review_draft(stored),
                state_policies=policies(),
                approved_by="user",
                approved_at=APPROVED_AT,
            )
            publish_sector_threshold_approval(approval, store_dir=root)
            helper = bridge_helpers.SectorRuleRuntimeBridgeTests(
                methodName="test_complete_versioned_evidence_returns_ready_contract"
            )
            helper.setUp()
            source = replace(
                helper.source_batch(),
                threshold_approval_evidence=None,
            )

            bound = bind_latest_sector_threshold_approval(
                source,
                store_dir=root,
                history_evidence=stored,
            )

            self.assertEqual(bound.status, "approved")
            self.assertEqual(
                bound.source_batch.threshold_approval_evidence.approval_id,
                approval.approval_id,
            )


if __name__ == "__main__":
    unittest.main()
