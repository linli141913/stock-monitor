import sqlite3
import unittest
from datetime import timedelta

from radar.api_contracts import (
    RadarLeaderReviewReplayDiagnostic,
    RadarLeaderReviewVersionRequest,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_review_service import (
    build_manual_review_version,
    build_review_form_data,
    build_review_replay_diagnostic,
)
from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from tests import test_radar_leader_risk_review_artifacts as helpers
from tests.test_radar_leader_risk_review_repository import (
    LeaderRiskReviewRepositoryTests,
)


AS_OF = helpers.AS_OF


class LeaderRiskReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: AS_OF,
        )
        self.connection.execute(
            "INSERT INTO radar_runs (radar_run_id, as_of, status, "
            "shadow_mode, started_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("run-review-1", AS_OF.isoformat(), "succeeded", 1,
             AS_OF.isoformat(), AS_OF.isoformat()),
        )
        self.connection.commit()
        self.repository = LeaderRiskReviewRepository(
            self.connection,
            clock=lambda: AS_OF,
        )
        self.document = helpers.make_document()
        self.content = helpers.make_content()
        self.batch = LeaderRiskReviewRepositoryTests.batch()
        self.repository.save_review_batch(self.batch, (self.document,))
        self.repository.save_document_content_snapshots(
            self.batch["reviewBatchId"],
            ((self.document.candidate_category.value, self.content),),
        )

    def tearDown(self):
        self.connection.close()

    def request(self, form, **changes):
        payload = {
            "reviewBatchId": self.batch["reviewBatchId"],
            "documentId": self.document.document_id,
            "candidateCategory": self.document.candidate_category.value,
            "contentSha256": self.content.content_sha256,
            "candidateId": form["candidate"].candidate_id,
            "reviewerKey": "reviewer-local-1",
            "effectiveUntil": None,
            "factSupplements": [
                {
                    "factKind": fact.fact_kind.value,
                    "sourceValue": fact.source_value,
                    "pageNumber": fact.page_number,
                    "sourceFragment": fact.source_fragment,
                    "mappedDocumentId": (
                        helpers.TARGET_DOCUMENT_ID
                        if fact.fact_kind.value
                        == "referenced_document_id"
                        else None
                    ),
                }
                for fact in helpers.make_manual_facts()
            ],
            "targetEvent": {
                "eventVersion": "v1",
                "eventSubtype": "formal_investigation",
                "sourceUrl": (
                    "https://static.cninfo.com.cn/finalpage/"
                    "2026-01-01/1224000001.PDF"
                ),
                "documentId": helpers.TARGET_DOCUMENT_ID,
                "publishedAt": helpers.PUBLISHED_AT - timedelta(days=30),
                "effectiveFrom": helpers.PUBLISHED_AT - timedelta(days=30),
                "effectiveUntil": None,
                "factSummary": "人工核对的既有正式调查事件。",
                "officialStatus": "active",
            },
            "relationKind": "resolves",
            "replacementEventVersion": None,
            "decisionSummary": "人工核对当前公告与既有事件关系。",
            "confirmOfficialEvidence": True,
        }
        payload.update(changes)
        return RadarLeaderReviewVersionRequest.model_validate(payload)

    def form(self, as_of=AS_OF):
        return build_review_form_data(
            self.repository,
            review_batch_id=self.batch["reviewBatchId"],
            document_id=self.document.document_id,
            candidate_category=self.document.candidate_category.value,
            as_of=as_of,
            write_enabled=False,
        )

    def test_preview_exposes_real_pages_but_keeps_write_disabled(self):
        form = self.form()

        self.assertFalse(form["writeEnabled"])
        self.assertEqual(form["content"], self.content)
        self.assertEqual(form["candidate"].candidate_kind.value,
                         "fact_extraction_missing")
        self.assertEqual(form["versions"], ())
        self.assertEqual(
            form["requiredFactKinds"],
            ["referenced_document_id", "case_id"],
        )
        self.assertEqual(
            form["replayDiagnostic"],
            {
                "status": "missing",
                "reviewVersionCount": 0,
                "bundleCount": 0,
                "activeReviewVersion": None,
                "materialChangeRequired": True,
                "reasonCodes": [
                    "risk_lifecycle_version_history_insufficient"
                ],
                "formalUsable": False,
            },
        )

    def test_two_manual_versions_round_trip_as_a_continuous_chain(self):
        form = self.form()
        first_payload = self.request(form).model_dump(
            mode="json",
            by_alias=True,
        )
        first_payload["factSupplements"] = [
            first_payload["factSupplements"][0],
            first_payload["factSupplements"][3],
        ]
        first, _ = build_manual_review_version(
            self.repository,
            RadarLeaderReviewVersionRequest.model_validate(
                first_payload
            ),
            as_of=AS_OF,
        )
        self.assertTrue(self.repository.save_review_version(
            self.batch["reviewBatchId"], first,
        ))

        second_form = self.form(AS_OF + timedelta(minutes=2))
        second, _ = build_manual_review_version(
            self.repository,
            self.request(second_form),
            as_of=AS_OF + timedelta(minutes=2),
        )
        self.assertTrue(self.repository.save_review_version(
            self.batch["reviewBatchId"], second,
        ))

        versions = self.repository.list_review_versions(
            self.batch["reviewBatchId"],
            self.document.document_id,
        )
        self.assertEqual(
            [item.submission.review_version for item in versions],
            ["manual-review-v1", "manual-review-v2"],
        )
        self.assertEqual(
            versions[1].submission.supersedes_review_version,
            "manual-review-v1",
        )

    def test_second_version_without_material_change_is_rejected(self):
        form = self.form()
        first, _ = build_manual_review_version(
            self.repository,
            self.request(form),
            as_of=AS_OF,
        )
        self.assertTrue(self.repository.save_review_version(
            self.batch["reviewBatchId"], first,
        ))

        second_form = self.form(AS_OF + timedelta(minutes=2))
        with self.assertRaisesRegex(Exception, "缺少实质证据变化"):
            build_manual_review_version(
                self.repository,
                self.request(second_form),
                as_of=AS_OF + timedelta(minutes=2),
            )

    def test_replay_diagnostic_reports_one_version_as_history_missing(self):
        form = self.form()
        version, _ = build_manual_review_version(
            self.repository,
            self.request(form),
            as_of=AS_OF,
        )

        diagnostic = build_review_replay_diagnostic((version,))

        self.assertEqual(diagnostic["status"], "missing")
        self.assertEqual(diagnostic["reviewVersionCount"], 1)
        self.assertEqual(diagnostic["bundleCount"], 1)
        self.assertEqual(
            diagnostic["activeReviewVersion"],
            "manual-review-v1",
        )
        self.assertTrue(diagnostic["materialChangeRequired"])
        self.assertEqual(
            diagnostic["reasonCodes"],
            ["risk_evidence_bundle_audit_history_insufficient"],
        )
        self.assertFalse(diagnostic["formalUsable"])

        payload = RadarLeaderReviewReplayDiagnostic.model_validate(
            diagnostic
        ).model_dump(mode="json", by_alias=True)
        self.assertEqual(payload, diagnostic)

    def test_replay_diagnostic_accepts_two_materially_changed_versions(self):
        form = self.form()
        first_payload = self.request(form).model_dump(
            mode="json",
            by_alias=True,
        )
        first_payload["factSupplements"] = [
            first_payload["factSupplements"][0],
            first_payload["factSupplements"][3],
        ]
        first, _ = build_manual_review_version(
            self.repository,
            RadarLeaderReviewVersionRequest.model_validate(first_payload),
            as_of=AS_OF,
        )
        self.assertTrue(self.repository.save_review_version(
            self.batch["reviewBatchId"], first,
        ))
        second_form = self.form(AS_OF + timedelta(minutes=2))
        second, _ = build_manual_review_version(
            self.repository,
            self.request(second_form),
            as_of=AS_OF + timedelta(minutes=2),
        )

        diagnostic = build_review_replay_diagnostic((first, second))

        self.assertEqual(diagnostic["status"], "ready")
        self.assertEqual(diagnostic["reviewVersionCount"], 2)
        self.assertEqual(diagnostic["bundleCount"], 2)
        self.assertEqual(
            diagnostic["activeReviewVersion"],
            "manual-review-v2",
        )
        self.assertFalse(diagnostic["materialChangeRequired"])
        self.assertEqual(diagnostic["reasonCodes"], [])
        self.assertFalse(diagnostic["formalUsable"])

    def test_stale_content_or_forged_fragment_is_rejected(self):
        form = self.form()
        with self.assertRaisesRegex(Exception, "正文校验值已变化"):
            build_manual_review_version(
                self.repository,
                self.request(form, contentSha256="0" * 64),
                as_of=AS_OF,
            )

        payload = self.request(form).model_dump(mode="json", by_alias=True)
        payload["factSupplements"][0]["sourceFragment"] = "正文中不存在"
        with self.assertRaisesRegex(Exception, "人工事实未通过"):
            build_manual_review_version(
                self.repository,
                RadarLeaderReviewVersionRequest.model_validate(payload),
                as_of=AS_OF,
            )

    def test_company_announcement_number_can_map_to_cninfo_document_id(self):
        page_text = helpers.MANUAL_PAGE_TEXT.replace(
            "1224000001",
            "公告编号：2026-020",
        )
        self.connection.close()
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: AS_OF,
        )
        self.connection.execute(
            "INSERT INTO radar_runs (radar_run_id, as_of, status, "
            "shadow_mode, started_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("run-review-1", AS_OF.isoformat(), "succeeded", 1,
             AS_OF.isoformat(), AS_OF.isoformat()),
        )
        self.connection.commit()
        self.repository = LeaderRiskReviewRepository(self.connection)
        self.content = helpers.make_content(page_text=page_text)
        self.repository.save_review_batch(self.batch, (self.document,))
        self.repository.save_document_content_snapshots(
            self.batch["reviewBatchId"],
            ((self.document.candidate_category.value, self.content),),
        )
        form = self.form()
        payload = self.request(form).model_dump(mode="json", by_alias=True)
        referenced = next(
            item for item in payload["factSupplements"]
            if item["factKind"] == "referenced_document_id"
        )
        referenced.update({
            "sourceValue": "2026-020",
            "sourceFragment": "公告编号：2026-020",
            "mappedDocumentId": helpers.TARGET_DOCUMENT_ID,
        })

        version, _ = build_manual_review_version(
            self.repository,
            RadarLeaderReviewVersionRequest.model_validate(payload),
            as_of=AS_OF,
        )

        self.assertEqual(
            version.event_versions[0].document_id,
            helpers.TARGET_DOCUMENT_ID,
        )


if __name__ == "__main__":
    unittest.main()
