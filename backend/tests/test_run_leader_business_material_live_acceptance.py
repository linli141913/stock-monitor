import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import run_leader_business_material_live_acceptance as cli

from tests.test_radar_leader_business_material_acceptance_report import (
    LeaderBusinessMaterialAcceptanceReportTests,
)


class RunLeaderBusinessMaterialLiveAcceptanceTests(unittest.TestCase):
    def test_cli_writes_local_workbench_but_stdout_stays_sanitized(self):
        helper = LeaderBusinessMaterialAcceptanceReportTests(
            methodName=(
                "test_report_shows_batch_counts_documents_and_gate_boundary"
            )
        )
        helper.setUp()
        result = helper.completed_result()
        symbol = result.review_queue.items[0].symbol

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            report_path = Path(directory) / "acceptance.html"
            with (
                patch.object(
                    cli,
                    "build_default_leader_live_candidate_collection_sources",
                    return_value=object(),
                ),
                patch.object(
                    cli,
                    "run_leader_tradability_live_acceptance",
                    return_value=helper.helper.tradability,
                ),
                patch.object(
                    cli,
                    "run_leader_business_material_live_acceptance",
                    return_value=result,
                ),
                patch("sys.stdout", new_callable=StringIO) as output,
            ):
                exit_code = cli.main(report_path=report_path)

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["reportPath"], str(report_path))
            template_path = Path(payload["reviewTemplatePath"])
            source_packet_path = Path(payload["sourcePacketPath"])
            self.assertNotIn(symbol, output.getvalue())
            self.assertTrue(report_path.is_file())
            self.assertTrue(template_path.is_file())
            self.assertTrue(source_packet_path.is_file())
            template = json.loads(template_path.read_text(encoding="utf-8"))
            source_packet = json.loads(
                source_packet_path.read_text(encoding="utf-8")
            )
            self.assertEqual(template["candidateCount"], result.candidate_count)
            self.assertIsNone(template["reviewerKey"])
            self.assertTrue(all(
                value is None
                for value in template["entries"][0]["review"].values()
            ))
            self.assertEqual(len(source_packet["packetSha256"]), 64)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn(symbol, report)
            self.assertIn("人工复核前不进入正式门", report)
            self.assertIn(template_path.name, report)
            self.assertIn(source_packet_path.name, report)


if __name__ == "__main__":
    unittest.main()
