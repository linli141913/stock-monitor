import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


UTC = timezone.utc
CHECKED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FormalOperationalChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "rollback.md").write_text("rollback-v1", encoding="utf-8")
        (self.assets / "runbook.md").write_text("runbook-v1", encoding="utf-8")
        source_root = self.assets / "backend" / "radar"
        source_root.mkdir(parents=True)
        (source_root / "config.py").write_text("enabled: bool = False\n", encoding="utf-8")
        (source_root / "formal_execution_guard.py").write_text(
            "def guard():\n    return False\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def raw(self, **changes):
        from radar.formal_operational_checks import OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION

        performance_policy = {
            "contractVersion": "operational-performance-policy-v1",
            "policyId": "performance-policy-1",
            "evidenceScope": "live",
            "scheduleIntervalMs": 100,
            "minSampleCount": 5,
            "minCoverage": 1.0,
            "maxP95DurationMs": 50,
            "maxFailedRate": 0.0,
            "maxLockContentionCount": 0,
        }
        performance_policy["policySha256"] = sha(
            json.dumps(
                performance_policy,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        security_sources = [
                {
                    "role": "radar_config_source",
                    "path": "backend/radar/config.py",
                    "version": "v1",
                    "contentSha256": sha(b"enabled: bool = False\n"),
                },
                {
                    "role": "formal_execution_guard_source",
                    "path": "backend/radar/formal_execution_guard.py",
                    "version": "v1",
                    "contentSha256": sha(b"def guard():\n    return False\n"),
                },
            ]
        security_policy_payload = {
            "contractVersion": "radar-formal-security-policy-v1",
            "policyId": "security-policy-1",
            "evidenceScope": "static_only",
            "requiredAssets": security_sources,
        }
        security_identity = {
            "policyId": security_policy_payload["policyId"],
            "policySha256": sha(json.dumps(
                security_policy_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()),
            "policyContractVersion": security_policy_payload["contractVersion"],
            "evidenceScope": security_policy_payload["evidenceScope"],
            "automaticCollection": True,
            "sourceAssets": security_sources,
        }
        static_identity = {
            "policyId": "static-policy-1",
            "policySha256": "c" * 64,
            "policyContractVersion": "radar-formal-static-assets-policy-v1",
            "evidenceScope": "static_only",
            "automaticCollection": True,
        }
        data = {
            "contractVersion": OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION,
            "checkedAt": CHECKED_AT.isoformat(),
            "subjectId": "stage9-run-1",
            "performance": {
                "sampleCount": 5, "p50DurationMs": 20, "p95DurationMs": 50,
                "maxDurationMs": 80, "scheduleIntervalMs": 100,
                "lockContentionCount": 0, "reentryCount": 0, "failedCount": 0,
                "coverage": 1.0,
                "policy": performance_policy,
                "automaticCollection": True,
                "reentryProofComplete": True,
                "executionEvidenceComplete": True,
                "evidenceScope": "live",
            },
            "security": {
                "scanComplete": True, "findings": [],
                "managementInterfacesDefaultOff": True, "aiAuthorityIsolated": True,
                **security_identity,
            },
            "rollback": {"assets": [{
                "path": "rollback.md", "contentSha256": sha(b"rollback-v1"), "version": "v1", "role": "rollback_asset",
            }], **static_identity},
            "runbook": {"assets": [{
                "path": "runbook.md", "contentSha256": sha(b"runbook-v1"), "version": "v1", "role": "runbook_asset",
            }], **static_identity},
        }
        data.update(changes)
        return data

    def build(self, **changes):
        from radar.formal_operational_checks import (
            OperationalCollectorInputRef,
            _build_operational_checks,
            _COLLECTOR_BUILD_CAPABILITY,
        )
        return _build_operational_checks(
            self.raw(**changes),
            self.assets,
            capability=_COLLECTOR_BUILD_CAPABILITY,
            collector_input_ref=OperationalCollectorInputRef(
                contractVersion="radar-formal-operational-evidence-input-v1",
                relativePath="collector-input.json",
                contentSha256="d" * 64,
            ),
        )

    def test_contract_rejects_extras_coercion_paths_and_forged_state(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import RadarFormalOperationalChecks

        report = self.build()
        payload = report.model_dump(mode="json", by_alias=True)
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            RadarFormalOperationalChecks.model_validate(payload)
        payload = report.model_dump(mode="json", by_alias=True)
        payload["state"] = "collecting"
        with self.assertRaisesRegex(ValidationError, "operational_state_gates_conflict"):
            RadarFormalOperationalChecks.model_validate(payload)
        raw = self.raw()
        raw["security"]["scanComplete"] = "true"
        with self.assertRaises(ValidationError):
            self.build(**raw)
        raw = self.raw()
        raw["security"]["findings"] = [{"path": "../.env", "ruleCode": "secret"}]
        with self.assertRaisesRegex(ValidationError, "operational_relative_path_invalid"):
            self.build(**raw)

        for invalid_path in ("a/./b", "a//b", "a/"):
            with self.subTest(invalid_path=invalid_path):
                raw = self.raw()
                raw["rollback"]["assets"][0]["path"] = invalid_path
                with self.assertRaisesRegex(
                    ValidationError,
                    "operational_relative_path_invalid",
                ):
                    self.build(**raw)

    def test_performance_missing_policy_collects_and_explicit_failures_are_stable(self):
        missing = self.raw()
        missing["performance"].pop("policy")
        report = self.build(**missing)
        by_gate = {item.gate: item for item in report.gates}
        self.assertEqual(by_gate["performance"].state, "collecting")
        self.assertEqual(by_gate["performance"].reason_codes, ("performance_threshold_policy_missing",))

        incomplete = self.raw()
        incomplete["performance"]["policy"].pop("maxP95DurationMs")
        gate = next(item for item in self.build(**incomplete).gates if item.gate == "performance")
        self.assertEqual(gate.state, "collecting")
        self.assertEqual(gate.reason_codes, ("performance_threshold_policy_incomplete",))

        for updates, expected_state, expected_reason in (
            ({"maxDurationMs": 101}, "not_ready", "performance_duration_exceeds_interval"),
            ({"reentryCount": 1}, "failed", "performance_reentry_detected"),
            ({"sampleCount": 0}, "not_ready", "performance_sample_count_insufficient"),
        ):
            with self.subTest(updates=updates):
                payload = self.raw()
                payload["performance"].update(updates)
                report = self.build(**payload)
                gate = next(item for item in report.gates if item.gate == "performance")
                self.assertEqual(gate.state, expected_state)
                self.assertIn(expected_reason, gate.reason_codes)

    def test_manual_policy_contract_remains_compatible_without_frozen_identity(self):
        """旧人工合同保持可解析，但不能生成可进入正式门的 ready。"""
        from radar.formal_operational_checks import build_operational_checks

        payload = self.raw()
        # 即使旧调用方伪造全部自动、冻结策略和静态结论，
        # 序列化中间对象仍只能得到 rehearsal/collecting。
        report = build_operational_checks(payload, self.assets)
        self.assertEqual(report.state, "collecting")
        self.assertTrue(all(item.state == "collecting" for item in report.gates))
        self.assertTrue(all(
            item.evidence_scope == "rehearsal" for item in report.evidence
        ))

    def test_automatic_ready_requires_frozen_identities_roles_and_source_assets(self):
        payload = self.raw()
        payload["performance"]["policy"].pop("policySha256")
        payload["security"]["sourceAssets"] = []
        payload["rollback"]["assets"][0].pop("role")
        payload["runbook"].pop("policyId")
        report = self.build(**payload)
        by_gate = {item.gate: item for item in report.gates}
        self.assertEqual(by_gate["performance"].state, "collecting")
        self.assertEqual(by_gate["security"].state, "collecting")
        self.assertEqual(by_gate["rollback"].state, "collecting")
        self.assertEqual(by_gate["runbook"].state, "collecting")

    def test_automatic_missing_policy_has_priority_over_unverified_execution_context(self):
        payload = self.raw()
        payload["performance"].update({
            "automaticCollection": True,
            "reentryProofComplete": False,
            "reentryCount": None,
            "evidenceScope": "rehearsal",
        })
        payload["performance"].pop("policy")
        gate = next(item for item in self.build(**payload).gates if item.gate == "performance")
        self.assertEqual(gate.state, "collecting")
        self.assertEqual(gate.reason_codes, ("performance_threshold_policy_missing",))

    def test_automatic_reentry_count_is_unknown_until_universe_proof_is_complete(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import PerformanceInput

        payload = self.raw()
        payload["performance"].update({
            "automaticCollection": True,
            "reentryProofComplete": False,
            "reentryCount": None,
            "evidenceScope": "live",
        })
        gate = next(item for item in self.build(**payload).gates if item.gate == "performance")
        self.assertEqual(gate.state, "collecting")
        self.assertEqual(gate.reason_codes, ("performance_reentry_evidence_incomplete",))

        forged = dict(payload["performance"])
        forged["reentryCount"] = 0
        with self.assertRaisesRegex(ValidationError, "performance_reentry_claim_without_proof"):
            PerformanceInput.model_validate(forged)

    def test_performance_policy_uses_strict_numbers_and_positive_sample_threshold(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import PerformancePolicy

        base = self.raw()["performance"]["policy"]
        for changes in ({"minSampleCount": 0}, {"maxP95DurationMs": True}, {"minCoverage": "1.0"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                PerformancePolicy.model_validate({**base, **changes})

    def test_performance_metrics_reject_bool_and_numeric_strings(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import PerformanceInput

        base = self.raw()["performance"]
        for changes in ({"sampleCount": True}, {"p50DurationMs": "1"}, {"coverage": "1.0"}):
            payload = dict(base)
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                PerformanceInput.model_validate(payload)

    def test_static_only_scope_is_bound_into_static_evidence(self):
        payload = self.raw()
        payload["rollback"]["evidenceScope"] = "static_only"
        payload["runbook"]["evidenceScope"] = "static_only"
        report = self.build(**payload)
        by_gate = {item.gate: item for item in report.evidence}
        self.assertEqual(by_gate["rollback"].evidence_scope, "static_only")
        self.assertEqual(by_gate["runbook"].evidence_scope, "static_only")

    def test_policy_identity_is_retained_on_automatic_evidence_references(self):
        payload = self.raw()
        identity = {
            "policyId": "static-policy-1",
            "policySha256": "a" * 64,
            "policyContractVersion": "static-policy-v1",
            "evidenceScope": "static_only",
        }
        payload["security"].update(identity)
        payload["rollback"].update(identity)
        payload["runbook"].update(identity)
        report = self.build(**payload)
        by_gate = {item.gate: item for item in report.evidence}
        for gate in ("security", "rollback", "runbook"):
            self.assertEqual(by_gate[gate].policy_id, "static-policy-1")
            self.assertEqual(by_gate[gate].policy_sha256, "a" * 64)

    def test_security_redacts_to_relative_path_and_fails_closed(self):
        raw = self.raw()
        raw["security"]["findings"] = [{"path": "src/config.py", "ruleCode": "secret_like"}]
        report = self.build(**raw)
        security = next(item for item in report.gates if item.gate == "security")
        self.assertEqual(security.state, "failed")
        self.assertEqual(security.reason_codes, ("security_sensitive_content_detected",))
        serialized = json.dumps(report.model_dump(mode="json", by_alias=True))
        self.assertNotIn("src/config.py", serialized)
        self.assertNotIn("secret_like", serialized)

        for field, reason in (
            ("managementInterfacesDefaultOff", "security_management_interface_not_default_off"),
            ("aiAuthorityIsolated", "security_ai_authority_violation"),
        ):
            raw = self.raw()
            raw["security"][field] = False
            gate = next(item for item in self.build(**raw).gates if item.gate == "security")
            self.assertEqual(gate.state, "failed")
            self.assertIn(reason, gate.reason_codes)

    def test_empty_security_scan_is_collecting_not_a_claimed_policy_violation(self):
        raw = self.raw()
        raw["security"] = {
            "scanComplete": False,
            "findings": [],
            "managementInterfacesDefaultOff": False,
            "aiAuthorityIsolated": False,
        }
        gate = next(item for item in self.build(**raw).gates if item.gate == "security")
        self.assertEqual(gate.state, "collecting")
        self.assertEqual(gate.reason_codes, ("security_scan_incomplete",))

    def test_complete_security_scan_with_unprovable_ai_boundary_is_not_ready(self):
        raw = self.raw()
        raw["security"] = {
            "scanComplete": True,
            "findings": [],
            "managementInterfacesDefaultOff": True,
            "aiAuthorityIsolated": None,
        }
        gate = next(item for item in self.build(**raw).gates if item.gate == "security")
        self.assertEqual(gate.state, "not_ready")
        self.assertEqual(gate.reason_codes, ("security_ai_authority_unproven",))

    def test_static_assets_require_real_hashes_and_do_not_accept_empty_templates(self):
        for gate_name in ("rollback", "runbook"):
            with self.subTest(gate=gate_name, kind="missing"):
                raw = self.raw()
                raw[gate_name] = {"assets": []}
                gate = next(item for item in self.build(**raw).gates if item.gate == gate_name)
                self.assertEqual(gate.state, "not_ready")
                self.assertIn(f"{gate_name}_assets_missing", gate.reason_codes)
            with self.subTest(gate=gate_name, kind="hash"):
                raw = self.raw()
                raw[gate_name]["assets"][0]["contentSha256"] = "0" * 64
                gate = next(item for item in self.build(**raw).gates if item.gate == gate_name)
                self.assertEqual(gate.state, "failed")
                self.assertIn(f"{gate_name}_asset_hash_mismatch", gate.reason_codes)

    def test_report_has_exactly_four_bound_evidence_records_and_external_hash(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import (
            RadarFormalOperationalChecks,
            canonical_operational_checks_sha256,
        )

        report = self.build()
        self.assertEqual(report.state, "ready")
        self.assertEqual({item.gate for item in report.gates}, {"performance", "security", "rollback", "runbook"})
        self.assertEqual({item.gate for item in report.evidence}, {"performance", "security", "rollback", "runbook"})
        self.assertTrue(all(item.subject_id == "stage9-run-1" for item in report.evidence))
        self.assertRegex(canonical_operational_checks_sha256(report), r"^[0-9a-f]{64}$")
        for field, invalid in (
            ("evidenceType", "arbitrary-v0"),
            ("contractVersion", "arbitrary-v0"),
        ):
            with self.subTest(field=field):
                payload = report.model_dump(mode="json", by_alias=True)
                payload["evidence"][0][field] = invalid
                with self.assertRaisesRegex(
                    ValidationError,
                    "operational_evidence_binding_mismatch",
                ):
                    RadarFormalOperationalChecks.model_validate(payload)

    def test_asset_lstat_then_symlink_swap_cannot_produce_ready(self):
        nested = self.assets / "nested"
        nested.mkdir()
        candidate = nested / "asset.md"
        candidate.write_text("trusted-but-wrong", encoding="utf-8")
        external = self.root / "external.md"
        external.write_text("approved-content", encoding="utf-8")
        detached = nested / "detached.md"
        raw = self.raw()
        raw["rollback"] = {"assets": [{
            "path": "nested/asset.md",
            "contentSha256": sha(b"approved-content"),
            "version": "v1",
        }]}
        original_lstat = Path.lstat
        swapped = False

        def lstat_and_swap(path):
            nonlocal swapped
            metadata = original_lstat(path)
            if not swapped and path == candidate:
                swapped = True
                os.replace(candidate, detached)
                candidate.symlink_to(external)
            return metadata

        with patch(
            "radar.formal_operational_checks.Path.lstat",
            autospec=True,
            side_effect=lstat_and_swap,
        ):
            report = self.build(**raw)
        rollback = next(item for item in report.gates if item.gate == "rollback")
        self.assertEqual(rollback.state, "failed")

    def test_opened_asset_fd_is_rechecked_after_entry_becomes_symlink(self):
        nested = self.assets / "nested"
        nested.mkdir()
        candidate = nested / "asset.md"
        candidate.write_text("approved-content", encoding="utf-8")
        external = self.root / "external.md"
        external.write_text("approved-content", encoding="utf-8")
        raw = self.raw()
        raw["rollback"] = {"assets": [{
            "path": "nested/asset.md",
            "contentSha256": sha(b"approved-content"),
            "version": "v1",
        }]}
        original_stat = os.stat
        swapped = False

        def stat_and_swap(path, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and path == "asset.md"
                and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False
            ):
                swapped = True
                detached = nested / "detached.md"
                os.replace(candidate, detached)
                candidate.symlink_to(external)
            return original_stat(path, *args, **kwargs)

        with patch("radar.formal_operational_checks.os.stat", side_effect=stat_and_swap):
            report = self.build(**raw)
        rollback = next(item for item in report.gates if item.gate == "rollback")
        self.assertTrue(swapped)
        self.assertEqual(rollback.state, "failed")
        self.assertIn("rollback_asset_unverified", rollback.reason_codes)

    def test_asset_reader_rejects_oversize_and_ancestor_replacement(self):
        nested = self.assets / "nested"
        inner = nested / "inner"
        inner.mkdir(parents=True)
        candidate = inner / "asset.md"
        candidate.write_bytes(b"approved-content")
        raw = self.raw()
        raw["rollback"] = {"assets": [{
            "path": "nested/inner/asset.md",
            "contentSha256": sha(b"approved-content"),
            "version": "v1",
        }]}
        detached = self.assets / "detached"
        replacement = self.assets / "replacement"
        replacement.mkdir()
        original_stat = os.stat
        swapped = False

        def stat_and_swap(path, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and path == "asset.md"
                and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False
            ):
                swapped = True
                os.replace(nested, detached)
                nested.symlink_to(replacement, target_is_directory=True)
            return original_stat(path, *args, **kwargs)

        with patch("radar.formal_operational_checks.os.stat", side_effect=stat_and_swap):
            gate = next(item for item in self.build(**raw).gates if item.gate == "rollback")
        self.assertTrue(swapped)
        self.assertEqual(gate.state, "failed")
        self.assertIn("rollback_asset_unverified", gate.reason_codes)

        oversized = self.assets / "oversized.md"
        oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        raw = self.raw()
        raw["rollback"] = {"assets": [{
            "path": "oversized.md",
            "contentSha256": sha(oversized.read_bytes()),
            "version": "v1",
        }]}
        gate = next(item for item in self.build(**raw).gates if item.gate == "rollback")
        self.assertEqual(gate.state, "failed")
        self.assertIn("rollback_asset_unverified", gate.reason_codes)


class RunFormalOperationalChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.input_dir, self.output_dir = self.root / "input", self.root / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        (self.input_dir / "rollback.md").write_text("rollback-v1", encoding="utf-8")
        (self.input_dir / "runbook.md").write_text("runbook-v1", encoding="utf-8")
        source_root = self.input_dir / "backend" / "radar"
        source_root.mkdir(parents=True)
        (source_root / "config.py").write_text("enabled: bool = False\n", encoding="utf-8")
        (source_root / "formal_execution_guard.py").write_text(
            "def guard():\n    return False\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        return FormalOperationalChecksTests.raw(self)  # same deterministic input schema

    def write(self, payload=None):
        from run_radar_formal_operational_checks import INPUT_FILENAME
        (self.input_dir / INPUT_FILENAME).write_text(json.dumps(payload or self.payload()), encoding="utf-8")

    def test_cli_fixed_io_contract_and_no_sqlite_network_environment_access(self):
        from run_radar_formal_operational_checks import OUTPUT_FILENAME, run

        self.write()
        with patch.object(sqlite3, "connect", side_effect=AssertionError("sqlite")), patch.object(
            urllib.request, "urlopen", side_effect=AssertionError("network")
        ), patch.dict(os.environ, {"SHOULD_NOT_READ": "secret"}, clear=True):
            report = run(self.input_dir, self.output_dir)
        self.assertEqual(report.state, "collecting")
        output = json.loads((self.output_dir / OUTPUT_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(output["contractVersion"], "radar-formal-operational-checks-v1")
        self.assertNotIn(str(self.root), json.dumps(output, ensure_ascii=False))

    def test_cli_rejects_production_overlap_and_symlink(self):
        from run_radar_formal_operational_checks import run
        self.write()
        with self.assertRaisesRegex(ValueError, "operational_checks_paths_overlap"):
            run(self.input_dir, self.input_dir)
        linked = self.root / "linked-output"
        linked.symlink_to(self.output_dir, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "operational_checks_symlink_forbidden"):
            run(self.input_dir, linked)

    def test_cli_directory_fd_rejects_input_root_swap_before_use(self):
        from run_radar_formal_operational_checks import run

        self.write()
        external = self.root / "external"
        external.mkdir()
        (external / "sentinel.txt").write_text("unchanged", encoding="utf-8")
        detached = self.root / "detached"
        real_open = os.open
        swapped = False

        def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = real_open(path, flags, mode) if dir_fd is None else real_open(path, flags, mode, dir_fd=dir_fd)
            if not swapped and dir_fd is None and Path(path) == self.input_dir:
                swapped = True
                os.replace(self.input_dir, detached)
                os.symlink(external, self.input_dir)
            return descriptor

        with patch("run_radar_formal_operational_checks.os.open", side_effect=open_and_swap):
            with self.assertRaisesRegex(ValueError, "operational_checks_path_unverified"):
                run(self.input_dir, self.output_dir)
        self.assertTrue(swapped)
        self.assertEqual(tuple(path.name for path in external.iterdir()), ("sentinel.txt",))

    def test_cli_rejects_oversized_input_and_redacts_validation_details(self):
        from run_radar_formal_operational_checks import INPUT_FILENAME, _read_input, run

        (self.input_dir / INPUT_FILENAME).write_bytes(
            b'{"value":"' + b"x" * (8 * 1024 * 1024) + b'"}'
        )
        with self.assertRaisesRegex(ValueError, "operational_checks_input_too_large"):
            _read_input(self.input_dir)

        (self.input_dir / INPUT_FILENAME).write_text(
            json.dumps({"unexpected": "ultra-secret-value"}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as captured:
            run(self.input_dir, self.output_dir)
        self.assertEqual(str(captured.exception), "operational_checks_collection_failed")
        self.assertNotIn("ultra-secret-value", str(captured.exception))

    def test_cli_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        from run_radar_formal_operational_checks import INPUT_FILENAME, _read_input

        for payload in (b'{"contractVersion":"a","contractVersion":"b"}', b'{"value":NaN}', b'{"value":-Infinity}'):
            with self.subTest(payload=payload):
                (self.input_dir / INPUT_FILENAME).write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "operational_checks_input_invalid"):
                    _read_input(self.input_dir)


if __name__ == "__main__":
    unittest.main()
