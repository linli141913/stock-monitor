import json
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_leader_risk_d8_manual_review_session as cli
from tests import test_radar_leader_risk_d8_manual_review_session as session_helpers


class RunLeaderRiskD8ManualReviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.helper = session_helpers.LeaderRiskD8ManualReviewSessionTests(
            methodName=(
                "test_real_content_builds_pending_material_without_review_or_version"
            )
        )
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def test_cli_uses_private_temp_database_and_writes_unfilled_material(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            worklist = root / "review-worklist.json"
            worklist.write_text(
                json.dumps(self.helper.packet, ensure_ascii=False),
                encoding="utf-8",
            )
            selection = self.helper.selection
            with patch("sys.stdout", new_callable=StringIO) as output:
                exit_code = cli.main(
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
            databases = tuple(root.glob("stage6-d8-manual-review-session-*.sqlite"))
            packets = tuple(root.glob("stage6-d8-manual-review-session-*.json"))
            self.assertEqual(len(databases), 1)
            self.assertEqual(len(packets), 1)
            self.assertEqual(os.stat(databases[0]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(packets[0]).st_mode & 0o777, 0o600)
            packet = json.loads(packets[0].read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "pending_human_review")
            self.assertEqual(packet["d8VersionCount"], 0)
            self.assertIsNone(packet["items"][0]["review"])
            self.assertFalse(packet["d8SubmissionReady"])
            summary = output.getvalue()
            self.assertIn('"status": "pending_human_review"', summary)
            self.assertNotIn(selection.document_id, summary)
            self.assertNotIn("static.cninfo.com.cn", summary)

    def test_cli_rejects_non_private_tmp_paths_before_fetch_or_database(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worklist = root / "review-worklist.json"
            worklist.write_text(
                json.dumps(self.helper.packet, ensure_ascii=False),
                encoding="utf-8",
            )
            selection = self.helper.selection
            with patch("sys.stdout", new_callable=StringIO):
                exit_code = cli.main(
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
                    fetcher=lambda *args, **kwargs: calls.append(args),
                    clock=lambda: self.helper.prepared_at,
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(calls, [])
            self.assertEqual(tuple(root.glob("*.sqlite")), ())


if __name__ == "__main__":
    unittest.main()
