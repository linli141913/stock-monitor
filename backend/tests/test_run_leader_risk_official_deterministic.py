import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from run_leader_risk_official_deterministic import run_cli
from radar.leader_risk_official_deterministic import (
    build_leader_official_deterministic_risk_batch,
)
from tests import test_radar_leader_risk_official_deterministic as helpers


class RunLeaderRiskOfficialDeterministicTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderOfficialDeterministicRiskTests(
            methodName="test_complete_seven_category_zero_hit_is_bounded_ready_evidence"
        )
        helper.setUp()
        self.helper = helper
        delivery = helper.deliver(
            lambda *args, **kwargs: helper.helper.payload([]),
        )
        self.result = build_leader_official_deterministic_risk_batch(
            candidate_plan=helper.plan,
            delivery=delivery,
            document_contents=(),
        )

    def test_confirmation_is_required_before_live_runner(self):
        stdout = io.StringIO()
        calls = []

        code = run_cli(
            ["--candidate-source", "/private/tmp/candidate.json"],
            stdout=stdout,
            live_runner=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(stdout.getvalue())["status"], "not_run")

    def test_output_and_candidate_paths_must_stay_inside_private_tmp(self):
        stdout = io.StringIO()

        code = run_cli(
            [
                "--candidate-source",
                "/tmp/candidate.json",
                "--output-dir",
                "/tmp",
                "--confirm-live-poc",
            ],
            stdout=stdout,
            live_runner=lambda **kwargs: self.result,
        )

        self.assertEqual(code, 3)
        self.assertEqual(
            json.loads(stdout.getvalue())["reason"],
            "risk_official_deterministic_path_unverified",
        )

    def test_ready_result_writes_only_bounded_evidence_under_private_tmp(self):
        stdout = io.StringIO()
        with TemporaryDirectory(dir="/private/tmp") as directory:
            candidate = Path(directory) / "candidate.json"
            candidate.write_text("{}", encoding="utf-8")

            code = run_cli(
                [
                    "--candidate-source",
                    str(candidate),
                    "--output-dir",
                    directory,
                    "--confirm-live-poc",
                ],
                stdout=stdout,
                live_runner=lambda **kwargs: self.result,
            )

            payload = json.loads(stdout.getvalue())
            artifact = Path(payload["artifactPath"])
            artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertTrue(payload["riskSourceReady"])
            self.assertEqual(
                artifact_payload["claimScope"],
                "bounded_official_query_window",
            )
            first_projection = artifact_payload["items"][0]["projection"]
            self.assertEqual(
                first_projection["evidenceKind"],
                "bounded_no_disclosure_in_window",
            )
            self.assertEqual(
                first_projection["sourceContractIds"],
                [
                    "radar-leader-risk-cninfo-discovery-v1",
                    "radar-leader-risk-cninfo-issuer-scope-v1",
                ],
            )
            artifact_text = artifact.read_text(encoding="utf-8").lower()
            self.assertEqual(first_projection["manualFactIds"], [])
            self.assertNotIn("manual-review", artifact_text)
            self.assertNotIn("reviewid", artifact_text)
            self.assertNotIn("mappingversion", artifact_text)
            self.assertFalse(artifact_payload["gate"]["formalUsable"])
            self.assertFalse(artifact_payload["gate"]["stateTransitionAllowed"])


if __name__ == "__main__":
    unittest.main()
