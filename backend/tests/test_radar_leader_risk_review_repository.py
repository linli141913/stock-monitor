import sqlite3
import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleReviewVersion,
)
from radar.leader_risk_review_artifacts import (
    ManualRiskReviewArtifactInput,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_review_repository import (
    LeaderRiskReviewRepository,
)
from radar.etf_repository import EtfRepository
from radar.leader_repository import LeaderRepository
from radar.migrations import (
    LEADER_RISK_REVIEW_STORAGE_MIGRATION,
    LEADER_STORAGE_MIGRATION,
    STAGE5_RADAR_MIGRATIONS,
    STAGE6_RADAR_MIGRATIONS,
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)
from radar.repository import (
    RepositoryConflictError,
    RepositoryStateError,
)
from radar.sources.leader_risk_document_content import (
    build_risk_document_review_candidate,
)
from tests import test_radar_leader_risk_review_artifacts as d5_helpers


UTC = d5_helpers.UTC
AS_OF = d5_helpers.AS_OF
APPLIED_AT = AS_OF + timedelta(hours=1)


class LeaderRiskReviewRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.insert_run()
        self.repository = LeaderRiskReviewRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )
        self.document = d5_helpers.make_document()
        self.version = self.make_version(1)

    def tearDown(self):
        self.connection.close()

    def insert_run(self):
        as_of = AS_OF.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES ('run-review-1', ?, 'succeeded', 1, ?, ?)
            """,
            (as_of, as_of, as_of),
        )
        self.connection.commit()

    @staticmethod
    def batch(**changes):
        value = {
            "reviewBatchId": "risk-review-batch-1",
            "candidatePlanId": "candidate-plan-1",
            "radarRunId": "run-review-1",
            "asOf": AS_OF,
            "windowFrom": AS_OF.date() - timedelta(days=365),
            "windowUntil": AS_OF.date(),
            "candidateCount": 1,
            "shardCount": 1,
            "categoryCount": 7,
            "documentCount": 1,
            "queryCategoriesComplete": True,
            "queryPagesComplete": True,
            "queryWindowContinuous": True,
            "sourceContractId": "radar-leader-risk-cninfo-discovery-v1",
        }
        value.update(changes)
        return value

    @staticmethod
    def make_version(number, previous_artifacts=()):
        as_of = AS_OF + timedelta(minutes=number)
        document = d5_helpers.make_document()
        content = d5_helpers.make_content()
        event = d5_helpers.make_event()
        facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=as_of,
                document=document,
                content_sha256=content.content_sha256,
                pages=content.pages,
                extracted_at=content.fetched_at,
                source_status=content.status,
                event_versions=(event,),
                reviews=(),
            )
        )
        candidate = build_risk_document_review_candidate(
            content,
            facts,
        ).candidate
        submission = d5_helpers.make_submission(
            candidate,
            review_version=f"manual-review-v{number}",
            supersedes_review_version=(
                f"manual-review-v{number - 1}"
                if number > 1
                else None
            ),
            reviewed_at=as_of - timedelta(seconds=30),
        )
        artifact = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=as_of,
                document=document,
                content=content,
                facts=facts,
                candidate=candidate,
                event_versions=(event,),
                submission=submission,
                previous_artifacts=tuple(previous_artifacts),
            )
        ).artifact
        replay = replay_risk_document_research_evidence(
            RiskDocumentResearchReplayInput(
                as_of=as_of,
                document=document,
                content=content,
                facts=facts,
                event_versions=(event,),
                artifacts=(*previous_artifacts, artifact),
            )
        )
        fact_ids = {
            fact.fact_kind: fact.fact_id
            for fact in replay.manual_facts
        }
        relation_review = RiskDocumentVersionReview(
            review_id=f"supplemented-review-{number}",
            mapping_version=f"supplemented-map-v{number}",
            relation_kind=RiskDocumentRelationKind.RESOLVES,
            review_method="manual",
            reviewer_key="reviewer-local-2",
            reviewed_at=as_of - timedelta(seconds=10),
            effective_until=None,
            source_document_id=document.document_id,
            target_event_id=event.event_id,
            target_event_version=event.event_version,
            target_document_id=event.document_id,
            replacement_event_version=None,
            basis_fact_ids=(
                fact_ids[RiskDocumentFactKind.REFERENCED_DOCUMENT_ID],
                fact_ids[RiskDocumentFactKind.CASE_ID],
            ),
            decision_summary="人工复核公告与既有风险事件的精确关系。",
        )
        return LeaderRiskLifecycleReviewVersion(
            as_of=as_of,
            document=document,
            content=content,
            facts=facts,
            candidate=candidate,
            event_versions=(event,),
            submission=submission,
            relation_review=relation_review,
        )

    def save_batch(self):
        return self.repository.save_review_batch(
            self.batch(),
            (self.document,),
        )

    def test_migration_six_is_optional_and_preserves_migration_five(self):
        self.assertEqual(LEADER_STORAGE_MIGRATION.version, 5)
        self.assertEqual(
            LEADER_STORAGE_MIGRATION.name,
            "leader_state_storage",
        )
        self.assertEqual(LEADER_RISK_REVIEW_STORAGE_MIGRATION.version, 6)
        self.assertEqual(
            [item.version for item in STAGE6_RADAR_MIGRATIONS],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            ),
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
            ),
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
            ),
            [1, 2, 3, 4, 5, 6],
        )
        EtfRepository(self.connection)
        LeaderRepository(self.connection)
        objects = {
            (row[0], row[1])
            for row in self.connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'trigger')"
            )
        }
        self.assertIn(
            ("table", "radar_leader_risk_manual_review_versions"),
            objects,
        )
        self.assertIn(
            ("trigger", "trg_leader_risk_manual_review_no_update"),
            objects,
        )
        self.assertIn(
            ("trigger", "trg_leader_risk_manual_review_no_delete"),
            objects,
        )

    def test_repository_requires_optional_migration_six(self):
        legacy = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                legacy,
                migrations=STAGE6_RADAR_MIGRATIONS,
                clock=lambda: APPLIED_AT,
            )
            with self.assertRaises(RepositoryStateError):
                LeaderRiskReviewRepository(legacy)
        finally:
            legacy.close()

    def test_batch_and_review_version_round_trip_rebuild_typed_inputs(self):
        self.assertTrue(self.save_batch())
        self.assertTrue(
            self.repository.save_review_version(
                "risk-review-batch-1",
                self.version,
            )
        )
        self.assertFalse(
            self.repository.save_review_version(
                "risk-review-batch-1",
                self.version,
            )
        )

        batch = self.repository.get_review_batch(
            "risk-review-batch-1"
        )
        versions = self.repository.list_review_versions(
            "risk-review-batch-1",
            self.document.document_id,
        )

        self.assertEqual(batch["documentCount"], 1)
        self.assertEqual(batch["documents"], (self.document,))
        self.assertEqual(versions, (self.version,))
        self.assertEqual(
            versions[0].content.pages[0].text,
            d5_helpers.MANUAL_PAGE_TEXT,
        )
        self.assertEqual(versions[0].facts, self.version.facts)
        self.assertEqual(versions[0].candidate, self.version.candidate)
        self.assertFalse(versions[0].candidate.formal_usable)

    def test_latest_review_queue_summary_and_page_are_read_only(self):
        self.save_batch()

        summary = self.repository.get_latest_review_batch_summary()
        page = self.repository.list_review_batch_documents(
            "risk-review-batch-1",
            limit=1,
            offset=0,
        )
        empty_page = self.repository.list_review_batch_documents(
            "risk-review-batch-1",
            limit=1,
            offset=1,
        )

        self.assertEqual(summary["reviewBatchId"], "risk-review-batch-1")
        self.assertEqual(summary["candidateCount"], 1)
        self.assertEqual(summary["documentCount"], 1)
        self.assertEqual(summary["documentLinkCount"], 1)
        self.assertEqual(summary["contentSnapshotCount"], 0)
        self.assertEqual(summary["reviewedDocumentCount"], 0)
        self.assertEqual(summary["reviewVersionCount"], 0)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["document"], self.document)
        self.assertFalse(page["items"][0]["hasContentSnapshot"])
        self.assertEqual(page["items"][0]["contentSnapshotCount"], 0)
        self.assertEqual(page["items"][0]["contentStatus"], "not_fetched")
        self.assertIsNone(page["items"][0]["contentFetchedAt"])
        self.assertEqual(page["items"][0]["reviewVersionCount"], 0)
        self.assertEqual(empty_page["items"], ())

        detail = self.repository.get_review_batch_document(
            "risk-review-batch-1",
            self.document.document_id,
            self.document.candidate_category.value,
        )
        self.assertEqual(detail["document"], self.document)
        self.assertEqual(detail["contentStatus"], "not_fetched")
        self.assertEqual(detail["reviewVersionCount"], 0)

    def test_document_detail_rejects_wrong_category_or_batch(self):
        self.save_batch()
        with self.assertRaises(RepositoryStateError):
            self.repository.get_review_batch_document(
                "risk-review-batch-1",
                self.document.document_id,
                "not-a-risk-category",
            )
        with self.assertRaises(RepositoryStateError):
            self.repository.get_review_batch_document(
                "other-batch",
                self.document.document_id,
                self.document.candidate_category.value,
            )

    def test_review_versions_are_append_only_and_require_direct_predecessor(self):
        self.save_batch()
        self.repository.save_review_version(
            "risk-review-batch-1",
            self.version,
        )
        first_artifact = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=self.version.as_of,
                document=self.version.document,
                content=self.version.content,
                facts=self.version.facts,
                candidate=self.version.candidate,
                event_versions=self.version.event_versions,
                submission=self.version.submission,
                previous_artifacts=(),
            )
        ).artifact
        second = self.make_version(2, (first_artifact,))
        self.assertTrue(
            self.repository.save_review_version(
                "risk-review-batch-1",
                second,
            )
        )
        self.assertEqual(
            [item.submission.review_version for item in
             self.repository.list_review_versions(
                 "risk-review-batch-1",
                 self.document.document_id,
             )],
            ["manual-review-v1", "manual-review-v2"],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE radar_leader_risk_manual_review_versions "
                "SET reviewer_key='tampered'"
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "DELETE FROM radar_leader_risk_manual_review_versions"
            )
        self.connection.rollback()

        skipped = replace(
            second,
            submission=replace(
                second.submission,
                review_version="manual-review-v4",
                supersedes_review_version="manual-review-v3",
            ),
        )
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_review_version(
                "risk-review-batch-1",
                skipped,
            )

    def test_review_version_chain_reader_preserves_symbol_order_and_is_read_only(self):
        self.save_batch()
        self.repository.save_review_version(
            "risk-review-batch-1",
            self.version,
        )
        first_artifact = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=self.version.as_of,
                document=self.version.document,
                content=self.version.content,
                facts=self.version.facts,
                candidate=self.version.candidate,
                event_versions=self.version.event_versions,
                submission=self.version.submission,
                previous_artifacts=(),
            )
        ).artifact
        second = self.make_version(2, (first_artifact,))
        self.repository.save_review_version(
            "risk-review-batch-1",
            second,
        )
        changes_before = self.connection.total_changes

        first_only = self.repository.list_review_version_chains(
            (self.document.symbol, "300082"),
            self.version.as_of,
        )
        complete = self.repository.list_review_version_chains(
            ("300082", self.document.symbol),
            second.as_of,
        )

        self.assertEqual(self.connection.total_changes, changes_before)
        self.assertEqual(len(first_only), 1)
        self.assertEqual(first_only[0].symbol, self.document.symbol)
        self.assertEqual(first_only[0].versions, (self.version,))
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0].symbol, self.document.symbol)
        self.assertEqual(complete[0].versions, (self.version, second))

    def test_invalid_content_rolls_back_all_review_rows(self):
        self.save_batch()
        oversized = replace(
            self.version,
            content=replace(
                self.version.content,
                pages=(replace(
                    self.version.content.pages[0],
                    text="x" * 100_001,
                ),),
            ),
        )

        with self.assertRaises(ValueError):
            self.repository.save_review_version(
                "risk-review-batch-1",
                oversized,
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_risk_document_contents"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM "
                "radar_leader_risk_manual_review_versions"
            ).fetchone()[0],
            0,
        )

    def test_conflicting_version_and_tampered_payload_are_rejected(self):
        self.save_batch()
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_review_batch(
                self.batch(),
                (replace(self.document, title="冲突公告标题"),),
            )
        self.repository.save_review_version(
            "risk-review-batch-1",
            self.version,
        )
        conflicting = replace(
            self.version,
            relation_review=replace(
                self.version.relation_review,
                decision_summary="冲突版本。",
            ),
        )
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_review_version(
                "risk-review-batch-1",
                conflicting,
            )

        altered_content = replace(
            self.version.content,
            pages=(replace(
                self.version.content.pages[0],
                text=self.version.content.pages[0].text + " ",
            ),),
        )
        altered_facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=self.version.as_of,
                document=self.version.document,
                content_sha256=altered_content.content_sha256,
                pages=altered_content.pages,
                extracted_at=altered_content.fetched_at,
                source_status=altered_content.status,
                event_versions=self.version.event_versions,
                reviews=(),
            )
        )
        altered_candidate = build_risk_document_review_candidate(
            altered_content,
            altered_facts,
        ).candidate
        altered_version = replace(
            self.version,
            content=altered_content,
            facts=altered_facts,
            candidate=altered_candidate,
        )
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_review_version(
                "risk-review-batch-1",
                altered_version,
            )

        self.connection.execute(
            "DROP TRIGGER trg_leader_risk_manual_review_no_update"
        )
        self.connection.execute(
            "UPDATE radar_leader_risk_manual_review_versions "
            "SET event_versions_json='[]'"
        )
        self.connection.commit()
        with self.assertRaises(RepositoryStateError):
            self.repository.list_review_versions(
                "risk-review-batch-1",
                self.document.document_id,
            )


if __name__ == "__main__":
    unittest.main()
