import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import import_leader_business_material_review as cli

from radar.leader_business_material_review_submission import (
    build_leader_business_material_review_source_packet,
)
from tests.test_radar_leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSubmissionTests,
)


class ImportLeaderBusinessMaterialReviewTests(unittest.TestCase):
    def test_cli_validates_filled_review_and_writes_sanitized_receipt(self):
        helper = LeaderBusinessMaterialReviewSubmissionTests(
            methodName=(
                "test_complete_real_human_submission_builds_existing_extraction_artifact"
            )
        )
        helper.setUp()
        source_packet = build_leader_business_material_review_source_packet(
            helper.plan,
            helper.queue,
        )
        submission = helper.filled_payload()
        symbol = helper.plan.items[0].symbol

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            source_path = Path(directory) / "source.json"
            submission_path = Path(directory) / "review.json"
            receipt_path = Path(directory) / "verified.json"
            source_path.write_text(
                json.dumps(source_packet, ensure_ascii=False),
                encoding="utf-8",
            )
            submission_path.write_text(
                json.dumps(submission, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch("sys.stdout", new_callable=StringIO) as output:
                exit_code = cli.main(
                    source_path=source_path,
                    submission_path=submission_path,
                    receipt_path=receipt_path,
                    clock=lambda: helper.imported_at,
                )

            evidence = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(evidence["status"], "ready")
            self.assertEqual(evidence["receiptPath"], str(receipt_path))
            self.assertNotIn(symbol, output.getvalue())
            self.assertNotIn("年度报告披露", output.getvalue())
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")),
                evidence,
            )
            self.assertFalse(evidence["gate"]["formalGateReady"])


if __name__ == "__main__":
    unittest.main()
