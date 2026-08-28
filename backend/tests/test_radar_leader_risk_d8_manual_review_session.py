import copy
import sqlite3
import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_risk_d8_manual_review_session import (
    LeaderRiskD8ManualReviewSelection,
    LeaderRiskD8ManualReviewSessionStatus,
    prepare_leader_risk_d8_manual_review_session,
)
from radar.leader_risk_d8_manual_worklist import (
    build_leader_risk_d8_manual_worklist,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from tests import test_radar_leader_risk_review_artifacts as review_helpers
from tests import test_radar_leader_risk_d8_manual_worklist as worklist_helpers


class LeaderRiskD8ManualReviewSessionTests(unittest.TestCase):
    def setUp(self):
        helper = worklist_helpers.LeaderRiskD8ManualWorklistTests(
            methodName=(
                "test_complete_d2_builds_pending_human_worklist_without_d8_conclusion"
            )
        )
        helper.setUp()
        delivery = helper.d2_delivery()
        self.prepared_at = delivery.as_of + timedelta(minutes=2)
        worklist = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=delivery.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256=(
                helper.CANDIDATE_SOURCE_SHA256
            ),
        )
        self.packet = worklist.to_review_packet()
        document = self.packet["items"][0]["documents"][0]
        self.selection = LeaderRiskD8ManualReviewSelection(
            document_id=document["documentId"],
            candidate_category=document["candidateCategories"][0],
        )

        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: self.prepared_at,
        )
        as_of = self.packet["asOf"]
        self.connection.execute(
            "INSERT INTO radar_runs (radar_run_id, as_of, status, "
            "shadow_mode, started_at, created_at) "
            "VALUES (?, ?, 'succeeded', 1, ?, ?)",
            (self.packet["radarRunId"], as_of, as_of, as_of),
        )
        self.connection.commit()
        self.repository = LeaderRiskReviewRepository(
            self.connection,
            clock=lambda: self.prepared_at,
        )

    def tearDown(self):
        self.connection.close()

    def _fetcher(self, document, **kwargs):
        return replace(
            review_helpers.make_content(),
            document_id=document.document_id,
            symbol=document.symbol,
            issuer_identity=document.issuer_identity,
            content_sha256="e" * 64,
            fetched_at=self.prepared_at,
        )

    def test_real_content_builds_pending_material_without_review_or_version(self):
        result = prepare_leader_risk_d8_manual_review_session(
            self.packet,
            (self.selection,),
            repository=self.repository,
            prepared_at=self.prepared_at,
            confirmed=True,
            fetcher=self._fetcher,
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualReviewSessionStatus.PENDING_HUMAN_REVIEW,
        )
        packet = result.to_packet()
        self.assertEqual(packet["selectedCount"], 1)
        self.assertEqual(packet["d8VersionCount"], 0)
        self.assertFalse(packet["d8SubmissionReady"])
        self.assertFalse(packet["gate"]["formalUsable"])
        self.assertFalse(packet["gate"]["stateTransitionAllowed"])
        item = packet["items"][0]
        self.assertEqual(item["documentId"], self.selection.document_id)
        self.assertEqual(item["contentSha256"], "e" * 64)
        self.assertRegex(item["candidateId"], r"^[0-9a-f]{64}$")
        self.assertTrue(item["requiredFactKinds"])
        self.assertEqual(item["pages"][0]["text"], review_helpers.MANUAL_PAGE_TEXT)
        self.assertIsNone(item["review"])
        self.assertFalse(item["d8SubmissionReady"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM "
                "radar_leader_risk_manual_review_versions"
            ).fetchone()[0],
            0,
        )

    def test_tampered_or_prefilled_packet_is_blocked_before_fetch(self):
        cases = []
        tampered_hash = copy.deepcopy(self.packet)
        tampered_hash["sourcePacketSha256"] = "0" * 64
        cases.append(tampered_hash)
        prefilled_review = copy.deepcopy(self.packet)
        prefilled_review["items"][0]["review"] = {
            "decision": "no_relevant_event"
        }
        cases.append(prefilled_review)

        for packet in cases:
            calls = []
            with self.subTest(packet=packet["sourcePacketSha256"]):
                result = prepare_leader_risk_d8_manual_review_session(
                    packet,
                    (self.selection,),
                    repository=self.repository,
                    prepared_at=self.prepared_at,
                    confirmed=True,
                    fetcher=lambda *args, **kwargs: calls.append(args),
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskD8ManualReviewSessionStatus.BLOCKED,
                )
                self.assertEqual(calls, [])

    def test_selection_is_explicit_unique_and_bounded_to_three_documents(self):
        repeated = tuple(self.selection for _ in range(4))
        calls = []

        result = prepare_leader_risk_d8_manual_review_session(
            self.packet,
            repeated,
            repository=self.repository,
            prepared_at=self.prepared_at,
            confirmed=True,
            fetcher=lambda *args, **kwargs: calls.append(args),
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualReviewSessionStatus.BLOCKED,
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
