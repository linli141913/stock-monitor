import contextlib
import hashlib
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar.formal_readiness_store import load_latest_formal_readiness


MODULES = ("trendRotation", "etfObservation", "leaderObservation")


def _input_payload():
    return {
        "contractVersion": "radar-formal-readiness-input-v1",
        "checked_at": "2026-09-04T08:00:00Z",
        "stage9_replay_run_id": None,
        "stage9_quality_sha256": None,
        "stage9_quality_state": "not_ready",
        "stage9_domain_states": {module: "not_ready" for module in MODULES},
        "shadow_ledger": None,
        "rule_version_states": {module: "not_ready" for module in MODULES},
        "calibration_states": {module: "not_ready" for module in MODULES},
        "data_quality_states": {module: "not_ready" for module in MODULES},
        "operational_checks": None,
        "operational_checks_sha256": None,
        "requested_by_module": {module: False for module in MODULES},
        "enabled_by_module": {module: False for module in MODULES},
        "evidence": [],
        "last_observed_trading_date_by_module": {
            module: None for module in MODULES
        },
    }


class RunRadarFormalReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp_dir.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_input(self, payload=None):
        from run_radar_formal_readiness import FORMAL_READINESS_INPUT_FILENAME

        path = self.input_dir / FORMAL_READINESS_INPUT_FILENAME
        path.write_text(
            json.dumps(payload or _input_payload()),
            encoding="utf-8",
        )
        return path

    def save_shadow_ledger(self):
        from radar.formal_shadow_calendar import (
            FormalShadowCalendarEvidence,
            OfficialSseCalendarProvider,
        )
        from radar.formal_shadow_ledger import build_shadow_ledger
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        calendar_raw = (
            "<strong>2026年休市安排</strong><table>"
            "<tr><td>元旦</td><td>1月1日休市</td></tr></table>"
        ).encode("utf-8")
        calendar_sha256 = hashlib.sha256(calendar_raw).hexdigest()
        evidence = FormalShadowCalendarEvidence(
            year=2026,
            fetchedAt=datetime(2026, 9, 4, 15, tzinfo=timezone(timedelta(hours=8))),
            observedThrough=date(2026, 9, 4),
            sourceDocumentSha256=calendar_sha256,
            closedDays=(date(2026, 1, 1),),
        )
        observed = datetime(2026, 9, 4, 7, tzinfo=timezone.utc)
        ledger = build_shadow_ledger(({
            "module": "trendRotation",
            "runId": "trend-1",
            "observedAt": observed,
            "sourceTime": observed - timedelta(minutes=2),
            "fetchedAt": observed - timedelta(minutes=1),
            "coverage": 1,
            "missingCount": 0,
            "failedCount": 0,
            "staleCount": 0,
            "lockState": "acquired",
            "durationMs": 1,
            "evidenceSha256": "a" * 64,
        },), calendar_provider=OfficialSseCalendarProvider((evidence,)), calendar_evidence=(evidence,))
        root = self.root / "shadow-store"
        ref = save_formal_shadow_ledger(
            ledger,
            root,
            calendar_documents_by_sha256={calendar_sha256: calendar_raw},
        )
        return root, ledger, ref

    def test_explicit_shadow_store_binds_exact_ledger_hash_and_rejects_forged_input_ref(self):
        from run_radar_formal_readiness import run

        shadow_root, ledger, stored = self.save_shadow_ledger()
        payload = _input_payload()
        payload["shadow_ledger"] = ledger.model_dump(mode="json", by_alias=True)
        payload["shadow_ledger_sha256"] = stored.content_sha256
        payload["evidence"] = [{
            "evidenceType": "formal_shadow_ledger",
            "contractVersion": "radar-formal-shadow-ledger-v2",
            "contentSha256": "0" * 64,
            "subjectId": "radar-formal-shadow-ledger-v2",
            "sourceTime": "2026-09-04T06:58:00Z",
            "fetchedAt": "2026-09-04T06:59:00Z",
            "generatedAt": "2026-09-04T07:00:00Z",
        }]
        self.write_input(payload)
        with self.assertRaisesRegex(ValueError, "formal_readiness_shadow_store_mismatch"):
            run(self.input_dir, self.output_dir, shadow_ledger_dir=shadow_root)

        payload["evidence"] = []
        self.write_input(payload)
        result = run(self.input_dir, self.output_dir, shadow_ledger_dir=shadow_root)
        self.assertEqual(result.status, "available")
        loaded = load_latest_formal_readiness(self.output_dir)
        shadow_refs = tuple(
            item for item in loaded.report.evidence
            if item.evidence_type == "formal_shadow_ledger"
        )
        self.assertEqual(len(shadow_refs), 1)
        self.assertEqual(shadow_refs[0].content_sha256, stored.content_sha256)

    def test_direct_ledger_even_with_true_hash_cannot_bypass_missing_store(self):
        from radar.formal_readiness_service import formal_shadow_ledger_evidence_ref
        from run_radar_formal_readiness import run

        _shadow_root, ledger, stored = self.save_shadow_ledger()
        payload = _input_payload()
        payload["shadow_ledger"] = ledger.model_dump(mode="json", by_alias=True)
        payload["shadow_ledger_sha256"] = stored.content_sha256
        payload["evidence"] = [formal_shadow_ledger_evidence_ref(
            ledger,
            stored.content_sha256,
        ).model_dump(mode="json", by_alias=True)]
        self.write_input(payload)

        with self.assertRaisesRegex(ValueError, "formal_readiness_shadow_store_required"):
            run(self.input_dir, self.output_dir)

    def test_shadow_store_ancestor_swap_is_rejected_by_store_fd_revalidation(self):
        import radar.formal_shadow_ledger_store as store_module
        from run_radar_formal_readiness import run

        shadow_root, _ledger, _stored = self.save_shadow_ledger()
        ancestor = shadow_root.parent
        original_name = shadow_root.name
        detached = self.root / "detached-shadow-store"
        external_parent = self.root / "external-parent"
        external_root = external_parent / original_name
        external_root.mkdir(parents=True)
        (external_root / "sentinel").write_text("unchanged", encoding="utf-8")
        self.write_input()
        real_open = store_module.os.open
        swapped = False

        def open_then_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = (
                real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is not None
                else real_open(path, flags, mode)
            )
            if not swapped and dir_fd is not None and path == original_name:
                swapped = True
                os.replace(shadow_root, detached)
                shadow_root.symlink_to(external_root, target_is_directory=True)
            return descriptor

        with patch("radar.formal_shadow_ledger_store.os.open", side_effect=open_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_readiness_input_path_unverified"):
                run(self.input_dir, self.output_dir, shadow_ledger_dir=shadow_root)
        self.assertTrue(swapped)
        self.assertEqual((external_root / "sentinel").read_text(encoding="utf-8"), "unchanged")

    def test_input_rejects_duplicate_keys_non_finite_and_oversized_file(self):
        from run_radar_formal_readiness import (
            FORMAL_READINESS_INPUT_FILENAME,
            run,
        )

        path = self.input_dir / FORMAL_READINESS_INPUT_FILENAME
        path.write_text('{"contractVersion":"radar-formal-readiness-input-v1","contractVersion":"x"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_invalid"):
            run(self.input_dir, self.output_dir)
        path.write_text('{"contractVersion":"radar-formal-readiness-input-v1","x":NaN}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_invalid"):
            run(self.input_dir, self.output_dir)
        path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_too_large"):
            run(self.input_dir, self.output_dir)

    def test_input_ancestor_swap_after_open_is_rejected(self):
        import run_radar_formal_readiness as runner
        from run_radar_formal_readiness import run

        parent = self.root / "safe-parent"
        safe_input = parent / "input"
        safe_input.mkdir(parents=True)
        original_input = self.input_dir
        self.input_dir = safe_input
        self.write_input()
        self.input_dir = original_input
        external_parent = self.root / "external-parent-input"
        (external_parent / "input").mkdir(parents=True)
        (external_parent / "input" / "sentinel").write_text("unchanged", encoding="utf-8")
        detached = self.root / "detached-input-parent"
        real_open = runner.os.open
        swapped = False

        def open_then_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)
            if not swapped and dir_fd is not None and path == "safe-parent":
                swapped = True
                os.replace(parent, detached)
                parent.symlink_to(external_parent, target_is_directory=True)
            return descriptor

        with patch("run_radar_formal_readiness.os.open", side_effect=open_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_readiness_input_path_unverified"):
                run(safe_input, self.output_dir)
        self.assertTrue(swapped)

    def test_fixed_input_file_swap_after_open_is_rejected(self):
        import run_radar_formal_readiness as runner
        from run_radar_formal_readiness import FORMAL_READINESS_INPUT_FILENAME, run

        self.write_input()
        original = self.input_dir / FORMAL_READINESS_INPUT_FILENAME
        detached = self.root / "detached-readiness-input.json"
        external = self.root / "external-readiness-input.json"
        external.write_text('{"secret":"unchanged"}', encoding="utf-8")
        real_read = runner.os.read
        swapped = False

        def read_then_swap(descriptor, size):
            nonlocal swapped
            chunk = real_read(descriptor, size)
            if not swapped:
                swapped = True
                os.replace(original, detached)
                original.symlink_to(external)
            return chunk

        with patch("run_radar_formal_readiness.os.read", side_effect=read_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_readiness_input_invalid"):
                run(self.input_dir, self.output_dir)
        self.assertTrue(swapped)
        self.assertEqual(external.read_text(encoding="utf-8"), '{"secret":"unchanged"}')

    def test_output_ancestor_swap_after_open_is_rejected(self):
        import run_radar_formal_readiness as runner
        from run_radar_formal_readiness import run

        self.write_input()
        parent = self.root / "safe-output-parent"
        output = parent / "output"
        output.mkdir(parents=True)
        external_parent = self.root / "external-output-parent"
        (external_parent / "output").mkdir(parents=True)
        sentinel = external_parent / "output" / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        detached = self.root / "detached-output-parent"
        real_open = runner.os.open
        swapped = False

        def open_then_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)
            if not swapped and dir_fd is not None and path == "safe-output-parent":
                swapped = True
                os.replace(parent, detached)
                parent.symlink_to(external_parent, target_is_directory=True)
            return descriptor

        with patch("run_radar_formal_readiness.os.open", side_effect=open_then_swap):
            with self.assertRaisesRegex(ValueError, "formal_readiness_input_path_unverified"):
                run(self.input_dir, output)
        self.assertTrue(swapped)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_help_documents_fixed_filename_and_contract_version(self):
        from run_radar_formal_readiness import (
            FORMAL_READINESS_INPUT_CONTRACT_VERSION,
            FORMAL_READINESS_INPUT_FILENAME,
            main,
        )

        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stdout(stdout):
                main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertIn(FORMAL_READINESS_INPUT_FILENAME, help_text)
        self.assertIn(FORMAL_READINESS_INPUT_CONTRACT_VERSION, help_text)
        self.assertIn("--input-dir", help_text)
        self.assertIn("--output-dir", help_text)
        self.assertIn("--shadow-ledger-dir", help_text)

    def test_success_publishes_only_to_explicit_output_without_external_helpers(self):
        from run_radar_formal_readiness import run

        input_path = self.write_input()
        with (
            patch("sqlite3.connect", side_effect=AssertionError("no SQLite")) as db,
            patch(
                "socket.create_connection",
                side_effect=AssertionError("no network"),
            ) as network,
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("no network"),
            ) as urlopen,
            patch(
                "radar.config.load_radar_settings",
                side_effect=AssertionError("no config"),
            ) as config,
            patch.dict(os.environ, {"RADAR_FORMAL_TREND_REQUESTED": "true"}),
        ):
            result = run(self.input_dir, self.output_dir)

        self.assertEqual(result.status, "available")
        self.assertTrue((self.output_dir / "latest.json").is_file())
        loaded = load_latest_formal_readiness(self.output_dir)
        self.assertEqual(loaded.status, "available")
        self.assertEqual(loaded.report.state, "not_ready")
        self.assertFalse(loaded.report.any_formal_enabled)
        self.assertEqual(
            tuple(item.module for item in loaded.report.modules),
            MODULES,
        )
        self.assertEqual(tuple(self.input_dir.iterdir()), (input_path,))
        db.assert_not_called()
        network.assert_not_called()
        urlopen.assert_not_called()
        config.assert_not_called()

    def test_missing_or_malformed_versioned_input_is_rejected(self):
        from run_radar_formal_readiness import (
            FORMAL_READINESS_INPUT_FILENAME,
            run,
        )

        with self.assertRaisesRegex(ValueError, "formal_readiness_input_missing"):
            run(self.input_dir, self.output_dir)

        input_path = self.input_dir / FORMAL_READINESS_INPUT_FILENAME
        input_path.write_text("{bad-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_invalid"):
            run(self.input_dir, self.output_dir)

        wrong_contract = _input_payload()
        wrong_contract["contractVersion"] = "radar-formal-readiness-input-v0"
        input_path.write_text(json.dumps(wrong_contract), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_contract_invalid"):
            run(self.input_dir, self.output_dir)

        malformed_contract = _input_payload()
        malformed_contract["unexpected"] = True
        input_path.write_text(json.dumps(malformed_contract), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "formal_readiness_input_invalid"):
            run(self.input_dir, self.output_dir)

    def test_input_boolean_maps_reject_strings_and_numbers(self):
        from run_radar_formal_readiness import run

        for field, invalid in (
            ("requested_by_module", "false"),
            ("requested_by_module", 1),
            ("enabled_by_module", "true"),
            ("enabled_by_module", 0),
        ):
            with self.subTest(field=field, invalid=invalid):
                payload = _input_payload()
                payload[field]["trendRotation"] = invalid
                self.write_input(payload)
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_readiness_input_invalid",
                ):
                    run(self.input_dir, self.output_dir)

    def test_explicit_freshness_policy_is_preserved_in_output(self):
        from run_radar_formal_readiness import run

        payload = _input_payload()
        payload["freshness_policy"] = {
            "policyVersion": "radar-formal-freshness-policy-v1",
            "reportMaxAgeSeconds": 300,
            "evidenceMaxAgeSeconds": 600,
            "operationalChecksMaxAgeSeconds": 120,
        }
        self.write_input(payload)

        run(self.input_dir, self.output_dir)

        loaded = load_latest_formal_readiness(self.output_dir)
        self.assertEqual(loaded.status, "available")
        self.assertEqual(
            loaded.report.freshness_policy.report_max_age_seconds,
            300,
        )

    def test_equal_nested_missing_and_production_paths_are_rejected(self):
        from run_radar_formal_readiness import run

        self.write_input()
        nested = self.input_dir / "nested-output"
        nested.mkdir()
        outside_missing = self.root / "missing"
        production_data = Path(__file__).resolve().parents[1] / "data"
        cases = (
            (self.input_dir, self.input_dir, "formal_readiness_paths_overlap"),
            (self.input_dir, nested, "formal_readiness_paths_overlap"),
            (self.root, self.input_dir, "formal_readiness_paths_overlap"),
            (outside_missing, self.output_dir, "formal_readiness_directory_missing"),
            (production_data, self.output_dir, "formal_readiness_production_path_forbidden"),
        )
        for input_dir, output_dir, reason in cases:
            with self.subTest(reason=reason, input_dir=input_dir, output_dir=output_dir):
                with self.assertRaisesRegex(ValueError, reason):
                    run(input_dir, output_dir)

    def test_all_cli_directories_must_be_inside_private_tmp(self):
        from run_radar_formal_readiness import (
            FORMAL_READINESS_INPUT_FILENAME,
            run,
        )

        project_root = Path(__file__).resolve().parents[2]
        stock_monitor = project_root / "stock-monitor"
        ops = project_root / "ops"

        with self.assertRaisesRegex(
            ValueError,
            "formal_readiness_private_tmp_required",
        ):
            run(stock_monitor, self.output_dir)

        (self.input_dir / FORMAL_READINESS_INPUT_FILENAME).write_text(
            "{bad-json",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "formal_readiness_private_tmp_required",
        ):
            run(self.input_dir, ops)

        self.write_input()
        with self.assertRaisesRegex(
            ValueError,
            "formal_readiness_private_tmp_required",
        ):
            run(
                self.input_dir,
                self.output_dir,
                shadow_ledger_dir=stock_monitor,
            )

    def test_macos_tmp_alias_is_normalized_to_private_tmp(self):
        from run_radar_formal_readiness import run

        self.write_input()
        relative_root = self.root.relative_to("/private/tmp")
        alias_input = Path("/tmp") / relative_root / "input"
        alias_output = Path("/tmp") / relative_root / "output"

        result = run(alias_input, alias_output)

        self.assertEqual(result.status, "available")
        self.assertTrue((self.output_dir / "latest.json").is_file())

    def test_symlink_input_or_output_root_is_rejected(self):
        from run_radar_formal_readiness import run

        self.write_input()
        symlink_input = self.root / "input-link"
        symlink_output = self.root / "output-link"
        symlink_input.symlink_to(self.input_dir, target_is_directory=True)
        symlink_output.symlink_to(self.output_dir, target_is_directory=True)

        for input_dir, output_dir in (
            (symlink_input, self.output_dir),
            (self.input_dir, symlink_output),
        ):
            with self.subTest(input_dir=input_dir, output_dir=output_dir):
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_readiness_symlink_forbidden",
                ):
                    run(input_dir, output_dir)

    def test_input_directory_fd_prevents_root_symlink_swap(self):
        from run_radar_formal_readiness import run

        self.write_input()
        external = self.root / "external-input"
        external.mkdir()
        (external / "sentinel.txt").write_text("unchanged", encoding="utf-8")
        detached = self.root / "detached-input"
        real_open = os.open
        swapped = False

        def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if dir_fd is None:
                descriptor = real_open(path, flags, mode)
            else:
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if (
                not swapped
                and dir_fd is not None
                and path == self.input_dir.name
            ):
                swapped = True
                os.replace(self.input_dir, detached)
                os.symlink(external, self.input_dir)
            return descriptor

        with patch(
            "run_radar_formal_readiness.os.open",
            side_effect=open_and_swap,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "formal_readiness_input_path_unverified",
            ):
                run(self.input_dir, self.output_dir)

        self.assertTrue(swapped)
        self.assertEqual(
            tuple(path.name for path in external.iterdir()),
            ("sentinel.txt",),
        )
        self.assertFalse((self.output_dir / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
