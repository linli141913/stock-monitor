import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from run_sector_threshold_review import run_cli
from tests.test_radar_sector_threshold_review import (
    policies,
    publish_history,
)


UTC = timezone.utc


class SectorThresholdReviewCliTests(unittest.TestCase):
    def test_default_command_writes_unapproved_review_template(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_history(root)
            output = io.StringIO()

            exit_code = run_cli(
                ["--store-dir", str(root)],
                stdout=output,
            )

            payload = json.loads(output.getvalue())
            review = json.loads(
                (root / "threshold-review-draft.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "review_ready")
            self.assertFalse(payload["formalApproval"])
            self.assertEqual(
                payload["calibrationIdentity"],
                review["calibrationIdentity"],
            )
            self.assertTrue(all(
                all(value is None for value in policy.values())
                for policy in review["statePolicies"].values()
            ))

    def test_approval_requires_exact_identity_and_complete_policy(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            publish_history(root)
            run_cli(["--store-dir", str(root)], stdout=io.StringIO())
            path = root / "threshold-review-draft.json"
            review = json.loads(path.read_text(encoding="utf-8"))
            review["statePolicies"] = policies()
            path.write_text(json.dumps(review), encoding="utf-8")

            rejected = io.StringIO()
            rejected_code = run_cli(
                [
                    "--store-dir", str(root),
                    "--approve-file", str(path),
                    "--approved-by", "user",
                    "--approved-at", "2026-08-22T02:00:00+00:00",
                    "--confirm-calibration-identity", "wrong",
                ],
                stdout=rejected,
                now_provider=lambda: datetime(
                    2026, 8, 22, 2, 1, tzinfo=UTC
                ),
            )
            self.assertEqual(rejected_code, 2)
            self.assertFalse((root / "threshold-approval.json").exists())

            accepted = io.StringIO()
            accepted_code = run_cli(
                [
                    "--store-dir", str(root),
                    "--approve-file", str(path),
                    "--approved-by", "user",
                    "--approved-at", "2026-08-22T02:00:00+00:00",
                    "--confirm-calibration-identity",
                    review["calibrationIdentity"],
                ],
                stdout=accepted,
                now_provider=lambda: datetime(
                    2026, 8, 22, 2, 1, tzinfo=UTC
                ),
            )
            self.assertEqual(accepted_code, 0)
            self.assertEqual(json.loads(accepted.getvalue())["status"], "approved")
            self.assertTrue((root / "threshold-approval.json").is_file())


if __name__ == "__main__":
    unittest.main()
