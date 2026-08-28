import io
import json
import tempfile
import unittest
from pathlib import Path

from run_leader_business_automatic_evidence import run_cli
from tests.test_radar_leader_business_automatic_evidence import (
    VALIDATED_AT,
    object_missing_sources,
    ready_sources,
    relation_unconfirmed_sources,
    source_packet,
)


class RunLeaderBusinessAutomaticEvidenceTests(unittest.TestCase):
    def invoke(self, packet, directory, *, sources=None):
        source_path = Path(directory) / "source.json"
        source_path.write_text(
            json.dumps(packet, ensure_ascii=False),
            encoding="utf-8",
        )
        output = io.StringIO()
        code = run_cli(
            [str(source_path), "--artifact-dir", str(Path(directory) / "out")],
            stdout=output,
            sources=sources or ready_sources(),
            clock=lambda: VALIDATED_AT,
        )
        return code, json.loads(output.getvalue())

    def test_ready_cli_prints_only_aggregate_counts_paths_and_false_gate(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            code, payload = self.invoke(source_packet(2), directory)

            self.assertEqual(code, 0)
            self.assertEqual(payload["candidateCount"], 2)
            self.assertEqual(payload["readyCount"], 2)
            self.assertFalse(payload["gate"]["formalGateReady"])
            self.assertNotIn("items", payload)
            self.assertNotIn("主营业务原文", json.dumps(
                payload,
                ensure_ascii=False,
            ))
            self.assertNotIn("gapDiagnosticPath", payload)

    def test_not_ready_real_source_semantics_exit_two(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            code, payload = self.invoke(
                source_packet(2),
                directory,
                sources=ready_sources(missing_index=1),
            )

            self.assertEqual(code, 2)
            self.assertEqual(payload["missingCount"], 1)
            self.assertIsNone(payload["deliveryPacketPath"])

    def test_explicit_gap_diagnostic_option_reports_artifact_path(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            source_path = Path(directory) / "source.json"
            source_path.write_text(
                json.dumps(source_packet(1), ensure_ascii=False),
                encoding="utf-8",
            )
            output = io.StringIO()
            code = run_cli(
                [
                    str(source_path),
                    "--artifact-dir",
                    str(Path(directory) / "out"),
                    "--gap-diagnostic",
                ],
                stdout=output,
                sources=object_missing_sources(),
                clock=lambda: VALIDATED_AT,
            )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertTrue(Path(payload["gapDiagnosticPath"]).is_file())

    def test_gap_diagnostic_target_selects_relation_unconfirmed_corpus(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            source_path = Path(directory) / "source.json"
            source_path.write_text(
                json.dumps(source_packet(1), ensure_ascii=False),
                encoding="utf-8",
            )
            output = io.StringIO()
            code = run_cli(
                [
                    str(source_path),
                    "--artifact-dir",
                    str(Path(directory) / "out"),
                    "--gap-diagnostic",
                    "--gap-diagnostic-target",
                    "business_deterministic_relation_unconfirmed",
                ],
                stdout=output,
                sources=relation_unconfirmed_sources(),
                clock=lambda: VALIDATED_AT,
            )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            diagnostic = json.loads(
                Path(payload["gapDiagnosticPath"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                diagnostic["targetReason"],
                "business_deterministic_relation_unconfirmed",
            )
            self.assertEqual(diagnostic["candidateCount"], 1)

    def test_invalid_input_and_write_failure_exit_three_without_traceback(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            invalid_path = Path(directory) / "invalid.json"
            invalid_path.write_text("not json", encoding="utf-8")
            output = io.StringIO()
            invalid_code = run_cli(
                [str(invalid_path), "--artifact-dir", str(Path(directory) / "out")],
                stdout=output,
                sources=ready_sources(),
                clock=lambda: VALIDATED_AT,
            )
            write_blocker = Path(directory) / "blocked"
            write_blocker.write_text("file", encoding="utf-8")
            valid_path = Path(directory) / "source.json"
            valid_path.write_text(
                json.dumps(source_packet(1), ensure_ascii=False),
                encoding="utf-8",
            )
            blocked_output = io.StringIO()
            blocked_code = run_cli(
                [str(valid_path), "--artifact-dir", str(write_blocker)],
                stdout=blocked_output,
                sources=ready_sources(),
                clock=lambda: VALIDATED_AT,
            )

            self.assertEqual(invalid_code, 3)
            self.assertEqual(blocked_code, 3)
            self.assertNotIn("Traceback", output.getvalue())
            self.assertNotIn("Traceback", blocked_output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()
