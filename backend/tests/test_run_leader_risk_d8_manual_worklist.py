import json
from datetime import timedelta
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_leader_risk_d8_manual_worklist as cli
from radar.leader_business_material_review_submission import (
    build_leader_business_material_review_source_packet,
)
from tests.test_radar_leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSubmissionTests,
)
from tests.test_radar_leader_risk_d8_manual_worklist import (
    LeaderRiskD8ManualWorklistTests,
)


class RunLeaderRiskD8ManualWorklistTests(unittest.TestCase):
    def test_cli_writes_bound_source_and_empty_review_packets(self):
        helper = LeaderRiskD8ManualWorklistTests(
            methodName=(
                "test_complete_d2_builds_pending_human_worklist_without_d8_conclusion"
            )
        )
        helper.setUp()
        delivery = helper.d2_delivery()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            candidate_source = root / "candidate-source.json"
            candidate_source.write_text("{}", encoding="utf-8")
            with (
                patch.object(
                    cli,
                    "_build_live_delivery",
                    return_value=(
                        delivery,
                        helper.CANDIDATE_SOURCE_SHA256,
                    ),
                ),
                patch(
                    "sys.argv",
                    [
                        "run_leader_risk_d8_manual_worklist.py",
                        "--candidate-source",
                        str(candidate_source),
                        "--output-dir",
                        str(root),
                        "--confirm-live-poc",
                    ],
                ),
                patch("sys.stdout", new_callable=StringIO) as output,
            ):
                exit_code = cli.main()

            self.assertEqual(exit_code, 0)
            source_paths = tuple(
                root.glob("stage6-d8-manual-worklist-*-source.json")
            )
            review_paths = tuple(
                root.glob("stage6-d8-manual-worklist-*-review.json")
            )
            self.assertEqual(len(source_paths), 1)
            self.assertEqual(len(review_paths), 1)
            source = json.loads(source_paths[0].read_text(encoding="utf-8"))
            review = json.loads(review_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(
                review["sourcePacketSha256"],
                source["packetSha256"],
            )
            self.assertTrue(all(
                item["review"] is None for item in review["items"]
            ))
            self.assertFalse(review["d8SubmissionReady"])
            summary = output.getvalue()
            self.assertIn('"status": "pending_human_review"', summary)
            self.assertNotIn("000001", summary)
            self.assertNotIn("static.cninfo.com.cn", summary)

    def test_cli_real_builder_path_uses_only_default_public_transports(self):
        business = LeaderBusinessMaterialReviewSubmissionTests(
            methodName=(
                "test_source_packet_round_trip_rebuilds_the_exact_verified_scope"
            )
        )
        business.setUp()
        packet = build_leader_business_material_review_source_packet(
            business.plan,
            business.queue,
        )
        roster_request_count = 0
        discovery_request_count = 0

        def roster_transport(url, *, headers, timeout):
            nonlocal roster_request_count
            roster_request_count += 1
            return {"stockList": [
                {
                    "code": item.symbol,
                    "category": "A股",
                    "orgId": f"fixture-{item.symbol}",
                    "zwjc": item.symbol,
                }
                for item in business.plan.items
            ]}

        def discovery_transport(url, *, data, headers, timeout):
            nonlocal discovery_request_count
            discovery_request_count += 1
            return {
                "totalRecordNum": 0,
                "totalAnnouncement": 0,
                "totalpages": 0,
                "hasMore": False,
                "announcements": [],
            }

        class FixedDateTime:
            @staticmethod
            def now(tz=None):
                value = business.plan.as_of + timedelta(minutes=20)
                return value if tz is None else value.astimezone(tz)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            candidate_source = root / "candidate-source.json"
            candidate_source.write_text(
                json.dumps(packet, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch(
                    "radar.sources.leader_risk_official._default_issuer_roster_transport",
                    new=roster_transport,
                ),
                patch(
                    "radar.sources.leader_risk_official._default_transport",
                    new=discovery_transport,
                ),
                patch.object(cli, "datetime", FixedDateTime),
                patch(
                    "sys.argv",
                    [
                        "run_leader_risk_d8_manual_worklist.py",
                        "--candidate-source",
                        str(candidate_source),
                        "--output-dir",
                        str(root),
                        "--confirm-live-poc",
                    ],
                ),
                patch("sys.stdout", new_callable=StringIO) as output,
            ):
                exit_code = cli.main()

            summary = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0, summary)
            self.assertEqual(
                summary["status"],
                "pending_human_review",
                summary,
            )
            source_path = Path(summary["sourcePacketPath"])
            review_path = Path(summary["reviewPacketPath"])
            self.assertEqual(roster_request_count, 1)
            self.assertEqual(discovery_request_count, 7)
            self.assertEqual(
                summary["candidateCount"],
                business.plan.candidate_count,
            )
            self.assertTrue(source_path.is_file())
            self.assertTrue(review_path.is_file())
            self.assertEqual(os.stat(source_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(review_path).st_mode & 0o777, 0o600)
            source_packet = json.loads(
                source_path.read_text(encoding="utf-8")
            )
            self.assertTrue(all(
                item["issuerIdentity"] is not None
                for item in source_packet["items"]
            ))

    def test_cli_rejects_paths_outside_private_tmp_before_live_request(self):
        live_builder = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_source = root / "candidate-source.json"
            candidate_source.write_text("{}", encoding="utf-8")
            with (
                patch.object(cli, "_build_live_delivery", live_builder),
                patch(
                    "sys.argv",
                    [
                        "run_leader_risk_d8_manual_worklist.py",
                        "--candidate-source",
                        str(candidate_source),
                        "--output-dir",
                        str(root),
                        "--confirm-live-poc",
                    ],
                ),
                patch("sys.stdout", new_callable=StringIO) as output,
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 2)
        live_builder.assert_not_called()
        self.assertIn('"status": "source_unverified"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
