import unittest
from pathlib import Path
import tempfile
import json


class RunOperationalEvidenceCollectorTests(unittest.TestCase):
    def test_cli_only_accepts_private_tmp_input_and_output_roots(self):
        from run_radar_formal_operational_evidence_collector import run

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "operational_evidence_paths_overlap"):
                run(root, root, root)

    def test_cli_project_asset_root_is_repository_root_not_backend_directory(self):
        import run_radar_formal_operational_evidence_collector as module

        self.assertEqual(module._PROJECT_ROOT, Path(__file__).resolve().parents[2])

    def test_cli_input_and_output_must_not_be_ancestor_descendant(self):
        from run_radar_formal_operational_evidence_collector import run

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            with self.assertRaisesRegex(ValueError, "operational_evidence_paths_overlap"):
                run(root, child, root)

    def test_cli_rejects_oversized_input_before_json_parsing(self):
        from run_radar_formal_operational_evidence_collector import (
            INPUT_FILENAME,
            _read_evidence_input,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            (root / INPUT_FILENAME).write_bytes(b"{" + b"x" * (8 * 1024 * 1024))
            with self.assertRaisesRegex(ValueError, "operational_evidence_input_too_large"):
                _read_evidence_input(root)

    def test_run_returns_only_stable_error_code_without_secret_or_path(self):
        from run_radar_formal_operational_evidence_collector import INPUT_FILENAME, run

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            input_root, output_root = root / "input", root / "output"
            input_root.mkdir()
            output_root.mkdir()
            (input_root / INPUT_FILENAME).write_text(json.dumps({"unexpected": "ultra-secret-value"}))
            with self.assertRaises(ValueError) as captured:
                run(input_root, output_root, root)
        self.assertEqual(str(captured.exception), "operational_evidence_collection_failed")
        self.assertNotIn("ultra-secret-value", str(captured.exception))
        self.assertNotIn(str(root), str(captured.exception))

    def test_cli_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        from run_radar_formal_operational_evidence_collector import (
            INPUT_FILENAME,
            _read_evidence_input,
        )

        for payload in (b'{"contractVersion":"a","contractVersion":"b"}', b'{"value":NaN}', b'{"value":Infinity}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                root = Path(directory)
                (root / INPUT_FILENAME).write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "operational_evidence_input_invalid"):
                    _read_evidence_input(root)


if __name__ == "__main__":
    unittest.main()
