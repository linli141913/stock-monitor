import json
from io import StringIO
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta

import run_leader_risk_d8_manual_review_preflight as cli
import run_leader_risk_d8_manual_review_session as session_cli
from tests import test_radar_leader_risk_review_artifacts as review_helpers
from tests import test_radar_leader_risk_d8_manual_review_session as session_helpers


class RunLeaderRiskD8ManualReviewPreflightTests(unittest.TestCase):
    def setUp(self):
        self.helper = session_helpers.LeaderRiskD8ManualReviewSessionTests(
            methodName=(
                "test_real_content_builds_pending_material_without_review_or_version"
            )
        )
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def _session(self, root):
        worklist = root / "review-worklist.json"
        worklist.write_text(
            json.dumps(self.helper.packet, ensure_ascii=False),
            encoding="utf-8",
        )
        selection = self.helper.selection
        with patch("sys.stdout", new_callable=StringIO):
            exit_code = session_cli.main(
                [
                    "--review-worklist", str(worklist),
                    "--output-dir", str(root),
                    "--document",
                    (
                        f"{selection.document_id}="
                        f"{selection.candidate_category}"
                    ),
                    "--confirm-live-poc",
                ],
                fetcher=self.helper._fetcher,
                clock=lambda: self.helper.prepared_at,
            )
        self.assertEqual(exit_code, 0)
        return (
            next(root.glob("stage6-d8-manual-review-session-*.sqlite")),
            next(root.glob("stage6-d8-manual-review-session-*.json")),
        )

    def _submission(self, material):
        item = material["items"][0]
        return {
            "reviewBatchId": material["reviewBatchId"],
            "documentId": item["documentId"],
            "candidateCategory": item["candidateCategory"],
            "contentSha256": item["contentSha256"],
            "candidateId": item["candidateId"],
            "reviewerKey": "human-reviewer-local-1",
            "effectiveUntil": None,
            "factSupplements": [
                {
                    "factKind": "referenced_document_id",
                    "sourceValue": "1224000001",
                    "pageNumber": 1,
                    "sourceFragment": "1224000001",
                    "mappedDocumentId": "cninfo:1224000001",
                },
                {
                    "factKind": "case_id",
                    "sourceValue": review_helpers.RAW_CASE_ID,
                    "pageNumber": 1,
                    "sourceFragment": review_helpers.RAW_CASE_ID,
                    "mappedDocumentId": None,
                },
            ],
            "targetEvent": {
                "eventVersion": "v1",
                "eventSubtype": "formal_investigation",
                "sourceUrl": (
                    "https://static.cninfo.com.cn/finalpage/"
                    "2026-06-01/1224000001.PDF"
                ),
                "documentId": "cninfo:1224000001",
                "publishedAt": "2026-06-01T00:00:00+00:00",
                "effectiveFrom": "2026-06-01T00:00:00+00:00",
                "effectiveUntil": None,
                "factSummary": "人工逐页核对的原立案事件。",
                "officialStatus": "active",
            },
            "relationKind": "resolves",
            "replacementEventVersion": None,
            "decisionSummary": "人工核对当前公告与原立案事件的关系。",
            "confirmOfficialEvidence": True,
        }

    def test_cli_runs_existing_preflight_read_only_and_persists_no_version(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            database, material_path = self._session(root)
            material = json.loads(material_path.read_text(encoding="utf-8"))
            submission_path = root / "human-submission.json"
            submission_path.write_text(
                json.dumps(self._submission(material), ensure_ascii=False),
                encoding="utf-8",
            )
            checked_at = self.helper.prepared_at + timedelta(seconds=2)
            with patch("sys.stdout", new_callable=StringIO) as output:
                exit_code = cli.main(
                    [
                        "--database", str(database),
                        "--material", str(material_path),
                        "--submission", str(submission_path),
                        "--output-dir", str(root),
                        "--confirm-human-review",
                    ],
                    clock=lambda: checked_at,
                )

            self.assertEqual(exit_code, 0)
            outputs = tuple(root.glob("stage6-d8-manual-review-preflight-*.json"))
            self.assertEqual(len(outputs), 1)
            self.assertEqual(os.stat(outputs[0]).st_mode & 0o777, 0o600)
            packet = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "validated_not_persisted")
            self.assertEqual(packet["preflight"]["proposedReviewVersion"],
                             "manual-review-v1")
            self.assertEqual(packet["preflight"]["existingReviewVersionCount"], 0)
            self.assertFalse(packet["preflight"]["submissionAllowed"])
            self.assertFalse(packet["d8VersionPersisted"])
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM "
                    "radar_leader_risk_manual_review_versions"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 0)
            self.assertNotIn(item := material["items"][0]["documentId"],
                             output.getvalue())

    def test_cli_rejects_submission_identity_mismatch(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            database, material_path = self._session(root)
            material = json.loads(material_path.read_text(encoding="utf-8"))
            submission = self._submission(material)
            submission["candidateId"] = "0" * 64
            submission_path = root / "human-submission.json"
            submission_path.write_text(
                json.dumps(submission, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch("sys.stdout", new_callable=StringIO):
                exit_code = cli.main(
                    [
                        "--database", str(database),
                        "--material", str(material_path),
                        "--submission", str(submission_path),
                        "--output-dir", str(root),
                        "--confirm-human-review",
                    ],
                    clock=lambda: self.helper.prepared_at,
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(
                tuple(root.glob("stage6-d8-manual-review-preflight-*.json")),
                (),
            )


if __name__ == "__main__":
    unittest.main()
