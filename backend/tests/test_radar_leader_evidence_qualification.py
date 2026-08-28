import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_automatic_evidence import (
    run_leader_business_automatic_evidence,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
)
from radar.leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSourcePacketStatus,
    build_leader_business_material_review_source_packet,
    load_leader_business_material_review_source_packet,
)
from radar.leader_evidence_qualification import (
    LeaderEvidenceQualificationStatus,
    build_leader_business_evidence_qualification,
    derive_leader_qualified_business_material_acceptance,
)
from radar.leader_runtime_candidate_plan import (
    is_leader_runtime_candidate_plan_valid,
)
from tests.test_radar_leader_business_automatic_evidence import (
    VALIDATED_AT,
    ready_sources,
    source_packet,
)


class LeaderEvidenceQualificationTests(unittest.TestCase):
    def run_business(self, packet, directory, *, missing_index):
        return run_leader_business_automatic_evidence(
            packet,
            artifact_dir=Path(directory),
            sources=ready_sources(missing_index=missing_index),
            clock=lambda: VALIDATED_AT,
        )

    def test_partial_batch_derives_identity_bound_business_ready_plan(self):
        packet = source_packet(3)
        loaded = load_leader_business_material_review_source_packet(packet)
        self.assertEqual(
            loaded.status,
            LeaderBusinessMaterialReviewSourcePacketStatus.READY,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            business = self.run_business(
                packet,
                directory,
                missing_index=1,
            )

        result = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            business,
        )

        self.assertEqual(result.status, LeaderEvidenceQualificationStatus.READY)
        self.assertEqual(result.parent_candidate_count, 3)
        self.assertEqual(result.qualified_candidate_count, 2)
        self.assertTrue(is_leader_runtime_candidate_plan_valid(
            result.candidate_plan
        ))
        self.assertEqual(
            result.candidate_plan.parent_candidate_set_id,
            loaded.candidate_plan.candidate_set_id,
        )
        self.assertIn(
            "radar-leader-business-deterministic-relation-v31",
            result.candidate_plan.derivation_policy_id,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.candidate_plan.items),
            ("000001", "000003"),
        )
        self.assertEqual(
            tuple(item.symbol for item in result.evidence_plan.items),
            ("000001", "000003"),
        )
        self.assertEqual(
            tuple(item.symbol for item in result.excluded_items),
            ("000002",),
        )
        self.assertEqual(
            result.excluded_items[0].reasons,
            ("business_catalyst_missing",),
        )
        self.assertEqual(
            result.business_automatic.status,
            AutomaticBusinessEvidenceStatus.READY,
        )
        self.assertEqual(
            result.business_automatic.candidate_plan_id,
            result.candidate_plan.candidate_set_id,
        )
        self.assertEqual(result.business_automatic.ready_count, 2)
        self.assertEqual(
            result.business_automatic.production_frozen_batch.source_batch
            .candidate_plan_id,
            result.candidate_plan.candidate_set_id,
        )
        evidence = result.to_evidence()
        self.assertEqual(evidence["qualifiedCandidateCount"], 2)
        self.assertEqual(evidence["excludedCandidateCount"], 1)
        self.assertFalse(evidence["gate"]["formalGateReady"])

    def test_ready_status_with_tampered_artifact_is_blocked(self):
        packet = source_packet(2)
        loaded = load_leader_business_material_review_source_packet(packet)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            business = self.run_business(
                packet,
                directory,
                missing_index=1,
            )
        ready_item = business.items[0]
        tampered_item = replace(
            ready_item,
            artifact=replace(ready_item.artifact, symbol="999999"),
        )
        tampered = replace(
            business,
            items=(tampered_item, business.items[1]),
        )

        result = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            tampered,
        )

        self.assertEqual(
            result.status,
            LeaderEvidenceQualificationStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("leader_business_evidence_qualification_unverified",),
        )

    def test_no_ready_business_candidate_is_an_explicit_empty_result(self):
        packet = source_packet(1)
        loaded = load_leader_business_material_review_source_packet(packet)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            business = self.run_business(
                packet,
                directory,
                missing_index=0,
            )

        result = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            business,
        )

        self.assertEqual(result.status, LeaderEvidenceQualificationStatus.EMPTY)
        self.assertIsNone(result.candidate_plan)
        self.assertIsNone(result.business_automatic)
        self.assertEqual(result.qualified_candidate_count, 0)
        self.assertEqual(result.excluded_candidate_count, 1)
        self.assertEqual(
            result.reasons,
            ("leader_business_evidence_qualification_empty",),
        )

    def test_any_source_failure_blocks_partial_qualification(self):
        packet = source_packet(2)
        loaded = load_leader_business_material_review_source_packet(packet)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            business = self.run_business(
                packet,
                directory,
                missing_index=1,
            )
        failed_item = replace(
            business.items[1],
            status=AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            reasons=("business_source_failed",),
        )
        failed = replace(
            business,
            status=AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            items=(business.items[0], failed_item),
        )

        result = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            failed,
        )

        self.assertEqual(result.status, LeaderEvidenceQualificationStatus.BLOCKED)
        self.assertEqual(
            result.reasons,
            ("leader_business_evidence_qualification_source_failed",),
        )

    def test_qualified_material_packet_rebinds_only_through_child_plan(self):
        packet = source_packet(3)
        loaded = load_leader_business_material_review_source_packet(packet)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            business = self.run_business(
                packet,
                directory,
                missing_index=1,
            )
        qualification = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            business,
        )
        material = LeaderBusinessMaterialLiveAcceptanceResult(
            status=LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
            radar_run_id=loaded.candidate_plan.radar_run_id,
            as_of=loaded.candidate_plan.as_of,
            candidate_plan_id=loaded.candidate_plan.candidate_set_id,
            candidate_count=loaded.candidate_plan.candidate_count,
            issuer_status="ready",
            queue_status=loaded.review_queue.status.value,
            review_queue=loaded.review_queue,
        )

        narrowed = derive_leader_qualified_business_material_acceptance(
            material,
            parent_plan=loaded.candidate_plan,
            qualification=qualification,
        )
        child_packet = build_leader_business_material_review_source_packet(
            qualification.candidate_plan,
            narrowed.review_queue,
        )
        child_loaded = load_leader_business_material_review_source_packet(
            child_packet
        )

        self.assertEqual(
            narrowed.status,
            LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
        )
        self.assertEqual(narrowed.candidate_count, 2)
        self.assertEqual(
            narrowed.candidate_plan_id,
            qualification.candidate_plan.candidate_set_id,
        )
        self.assertEqual(
            tuple(item.symbol for item in narrowed.review_queue.items),
            ("000001", "000003"),
        )
        self.assertEqual(
            child_loaded.status,
            LeaderBusinessMaterialReviewSourcePacketStatus.READY,
        )
        self.assertEqual(
            child_loaded.candidate_plan,
            qualification.candidate_plan,
        )


if __name__ == "__main__":
    unittest.main()
