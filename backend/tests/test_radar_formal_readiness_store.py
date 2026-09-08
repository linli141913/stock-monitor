import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import ValidationError


UTC = timezone.utc
CHECKED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


class FormalReadinessStoreTests(unittest.TestCase):
    def report(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        gates = [
            {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
            for gate in (
                "stage9_quality", "rule_version", "calibration", "shadow_ledger",
                "data_quality", "performance", "security", "rollback", "runbook",
            )
        ]
        return RadarFormalReadiness.model_validate({
            "checkedAt": CHECKED_AT,
            "freshnessPolicy": {
                "policyVersion": "radar-formal-freshness-policy-v1",
                "reportMaxAgeSeconds": 300,
                "evidenceMaxAgeSeconds": 600,
                "operationalChecksMaxAgeSeconds": 120,
            },
            "state": "ready_to_enable",
            "anyFormalEnabled": False,
            "allModulesFormalEnabled": False,
            "stage9ReplayRunId": "stage9-run-1",
            "stage9QualitySha256": "a" * 64,
            "stage9QualityState": "ready",
            "modules": [
                {"module": module, "state": "ready_to_enable", "requested": False,
                 "configuredEnabled": False, "formalEnabled": False, "observedTradingDays": days,
                 "requiredTradingDays": days, "lastObservedTradingDate": date(2026, 9, 4),
                 "gates": gates, "reasonCodes": []}
                for module, days in (("trendRotation", 20), ("etfObservation", 5), ("leaderObservation", 20))
            ],
            "evidence": [
                {
                    "evidenceType": "stage9_quality",
                    "contractVersion": "radar-replay-quality-v2",
                    "contentSha256": "a" * 64,
                    "subjectId": "stage9-run-1",
                    "generatedAt": CHECKED_AT,
                    "sourceTime": CHECKED_AT,
                    "fetchedAt": CHECKED_AT,
                },
                {
                    "evidenceType": "formal_operational_checks",
                    "contractVersion": "radar-formal-operational-checks-v1",
                    "contentSha256": "b" * 64,
                    "subjectId": "stage9-run-1",
                    "generatedAt": CHECKED_AT,
                    "sourceTime": CHECKED_AT,
                    "fetchedAt": CHECKED_AT,
                },
            ],
            "reasonCodes": [],
        })

    def test_save_is_content_addressed_with_relative_manifest_and_loads(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))

            self.assertEqual(ref.status, "available")
            self.assertEqual(manifest["reportRelativePath"], f"reports/{ref.content_sha256}.json")
            self.assertEqual(manifest["stage9ReplayRunId"], "stage9-run-1")
            self.assertFalse(Path(manifest["reportRelativePath"]).is_absolute())
            self.assertTrue((root / manifest["reportRelativePath"]).is_file())
            self.assertFalse(list(root.glob("**/*.tmp")))
            self.assertEqual(load_latest_formal_readiness(root).report, self.report())

    def test_default_temporary_directory_supports_missing_save_and_load(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "nested" / "store"
            self.assertEqual(load_latest_formal_readiness(root).status, "missing")

            saved = save_formal_readiness(self.report(), root)
            loaded = load_latest_formal_readiness(root)

        self.assertEqual(saved.status, "available")
        self.assertEqual(loaded.status, "available")
        self.assertEqual(loaded.report, self.report())

    def test_verified_macos_system_root_alias_is_equivalent_when_present(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        exercised = False

        def exercise(alias, expected, directory):
            nonlocal exercised
            exercised = True
            with self.subTest(alias=alias):
                alias_root = Path(directory) / "store"
                relative = alias_root.relative_to(alias)
                canonical_root = expected / relative

                self.assertEqual(
                    load_latest_formal_readiness(alias_root).status,
                    "missing",
                )
                save_formal_readiness(self.report(), alias_root)
                self.assertEqual(
                    load_latest_formal_readiness(canonical_root).report,
                    self.report(),
                )
                self.assertEqual(
                    load_latest_formal_readiness(alias_root).report,
                    self.report(),
                )

        var_alias = Path("/var")
        var_target = Path("/private/var")
        with tempfile.TemporaryDirectory() as directory:
            if (
                var_alias.is_symlink()
                and Path(os.path.realpath(var_alias)) == var_target
                and str(directory).startswith("/var/")
            ):
                exercise(var_alias, var_target, directory)

        tmp_alias = Path("/tmp")
        tmp_target = Path("/private/tmp")
        if tmp_alias.is_symlink() and Path(os.path.realpath(tmp_alias)) == tmp_target:
            with tempfile.TemporaryDirectory(dir="/tmp") as directory:
                exercise(tmp_alias, tmp_target, directory)
        if not exercised:
            self.skipTest("macOS fixed /var or /tmp alias is not present")

    def test_load_recomputes_canonical_sha_and_rejects_tampered_report(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            report_path = root / "reports" / f"{ref.content_sha256}.json"
            report_path.write_text('{"changed":true}', encoding="utf-8")

            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_report_hash_mismatch",))

    def test_load_rejects_manifest_identity_or_time_mismatch(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_formal_readiness(self.report(), root)
            manifest_path = root / "latest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["checkedAt"] = "2026-09-04T08:00:01+00:00"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_manifest_identity_mismatch",))

            save_formal_readiness(self.report(), root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["stage9ReplayRunId"] = "another-stage9-run"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_manifest_identity_mismatch",))

    def test_load_rejects_absolute_or_escaping_manifest_path(self):
        from radar.formal_readiness_store import load_latest_formal_readiness

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-formal-readiness-manifest-v1",
                "reportRelativePath": "/tmp/other.json",
                "contentSha256": "a" * 64,
                "checkedAt": CHECKED_AT.isoformat(),
            }), encoding="utf-8")

            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_manifest_unverified",))

            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-formal-readiness-manifest-v1",
                "reportRelativePath": "../escaped.json",
                "contentSha256": "a" * 64,
                "checkedAt": CHECKED_AT.isoformat(),
            }), encoding="utf-8")
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_manifest_unverified",))

    def test_load_requires_canonical_content_addressed_report_path(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            archive = root / "archive"
            archive.mkdir()
            renamed = archive / "renamed.json"
            os.replace(root / ref.relative_path, renamed)
            manifest_path = root / "latest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["reportRelativePath"] = "archive/renamed.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason_codes,
            ("formal_readiness_manifest_unverified",),
        )

    def test_missing_and_damaged_manifest_have_distinct_closed_semantics(self):
        from radar.formal_readiness_store import load_latest_formal_readiness

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(load_latest_formal_readiness(root).status, "missing")
            (root / "latest.json").write_text("not json", encoding="utf-8")
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_manifest_unverified",))

    def test_existing_non_regular_or_symlink_latest_is_failed_not_missing(self):
        from radar.formal_readiness_store import load_latest_formal_readiness

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "latest.json").mkdir()
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")
            (root / "latest.json").rmdir()
            os.symlink("missing-manifest.json", root / "latest.json")
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")

    def test_non_standard_json_number_returns_failed(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            (root / ref.relative_path).write_text('{"number":NaN}', encoding="utf-8")
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason_codes, ("formal_readiness_report_unverified",))

    def test_failed_report_publish_does_not_replace_previous_latest(self):
        from unittest.mock import patch
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.report()
            save_formal_readiness(old, root)
            newer = old.model_copy(update={"checked_at": CHECKED_AT.replace(minute=1)})
            with patch("radar.formal_readiness_store.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    save_formal_readiness(newer, root)
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "available")
            self.assertEqual(result.report, old)
            self.assertEqual(len(list((root / "reports").glob("*.json"))), 1)

    def test_failed_report_write_does_not_replace_previous_latest(self):
        from unittest.mock import patch
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.report()
            save_formal_readiness(old, root)
            newer = old.model_copy(update={"checked_at": CHECKED_AT.replace(minute=1)})
            with patch("radar.formal_readiness_store.json.dump", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    save_formal_readiness(newer, root)
            result = load_latest_formal_readiness(root)
            self.assertEqual(result.status, "available")
            self.assertEqual(result.report, old)
            self.assertEqual(len(list((root / "reports").glob("*.json"))), 1)

    def test_store_revalidates_and_rejects_model_copy_forged_ready_report(self):
        from radar.formal_readiness_store import save_formal_readiness

        report = self.report()
        forged = report.model_copy(update={"evidence": ()})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValidationError, "stage9_ready_evidence_missing"):
                save_formal_readiness(forged, Path(directory))
            forged_subject = report.model_copy(update={
                "evidence": (
                    report.evidence[0].model_copy(update={"subject_id": "other-run"}),
                ),
            })
            with self.assertRaisesRegex(ValidationError, "stage9_ready_evidence_subject_mismatch"):
                save_formal_readiness(forged_subject, Path(directory))

    def test_store_revalidates_and_rejects_forged_boolean(self):
        from radar.formal_readiness_store import save_formal_readiness

        report = self.report()
        forged_module = report.modules[0].model_copy(
            update={"requested": "false"},
        )
        forged = report.model_copy(update={
            "modules": (forged_module,) + report.modules[1:],
        })
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaises(ValidationError):
                save_formal_readiness(forged, Path(directory))

    def test_second_replace_failure_keeps_old_latest_unpublished_new_report(self):
        from unittest.mock import patch
        import radar.formal_readiness_store as store
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.report()
            old_ref = save_formal_readiness(old, root)
            newer = old.model_copy(update={"checked_at": CHECKED_AT.replace(minute=1)})
            real_replace = store.os.replace
            calls = []

            def replace_report_then_fail_latest(source, destination, **kwargs):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("injected_latest_replace_failure")
                return real_replace(source, destination, **kwargs)

            with patch("radar.formal_readiness_store.os.replace", side_effect=replace_report_then_fail_latest):
                with self.assertRaises(OSError):
                    save_formal_readiness(newer, root)
            result = load_latest_formal_readiness(root)
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(result.status, "available")
            self.assertEqual(result.report, old)
            self.assertEqual(manifest["contentSha256"], old_ref.content_sha256)

    def test_report_file_or_parent_symlink_is_rejected(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            report_path = root / ref.relative_path
            replacement = root / "replacement.json"
            os.replace(report_path, replacement)
            os.symlink(f"../{replacement.name}", report_path)
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")
            report_path.unlink()
            os.symlink("missing-report.json", report_path)
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")
            reports_real = root / "reports-real"
            os.replace(root / "reports", reports_real)
            os.symlink(reports_real.name, root / "reports")
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")

    def test_save_rejects_symlink_root_without_touching_external_store(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory) / "store-link"
            external = Path(external_directory)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            os.symlink(external, root)

            with self.assertRaises(ValueError):
                save_formal_readiness(self.report(), root)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(tuple(path.name for path in external.iterdir()), ("sentinel.txt",))
            self.assertEqual(load_latest_formal_readiness(root).status, "failed")

    def test_save_rejects_reports_symlink_or_nondirectory_before_writing(self):
        from radar.formal_readiness_store import save_formal_readiness

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory)
            external = Path(external_directory)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            os.symlink(external, root / "reports")

            with self.assertRaises(ValueError):
                save_formal_readiness(self.report(), root)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(tuple(path.name for path in external.iterdir()), ("sentinel.txt",))
            self.assertFalse((root / "latest.json").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").write_text("not-a-directory", encoding="utf-8")
            with self.assertRaises(ValueError):
                save_formal_readiness(self.report(), root)
            self.assertFalse((root / "latest.json").exists())

    def test_save_rejects_nonregular_latest_before_creating_report(self):
        from radar.formal_readiness_store import save_formal_readiness

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory)
            external_latest = Path(external_directory) / "latest.json"
            external_latest.write_text("sentinel", encoding="utf-8")
            os.symlink(external_latest, root / "latest.json")

            with self.assertRaises(ValueError):
                save_formal_readiness(self.report(), root)

            self.assertEqual(external_latest.read_text(encoding="utf-8"), "sentinel")
            self.assertFalse((root / "reports").exists())

    def test_save_rejects_nonregular_report_target_before_manifest_write(self):
        from radar.formal_readiness_store import save_formal_readiness

        with tempfile.TemporaryDirectory() as staging_directory:
            ref = save_formal_readiness(self.report(), Path(staging_directory))

        for target_kind in ("directory", "symlink"):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
                root = Path(directory)
                report_path = root / ref.relative_path
                report_path.parent.mkdir()
                external = Path(external_directory) / "sentinel.json"
                external.write_text("unchanged", encoding="utf-8")
                if target_kind == "directory":
                    report_path.mkdir()
                else:
                    os.symlink(external, report_path)

                with self.assertRaises(ValueError):
                    save_formal_readiness(self.report(), root)

                self.assertEqual(external.read_text(encoding="utf-8"), "unchanged")
                self.assertFalse((root / "latest.json").exists())

    def test_save_directory_fds_prevent_root_or_reports_symlink_swap(self):
        from unittest.mock import patch

        from radar.formal_readiness_store import save_formal_readiness

        real_open = os.open
        for target in ("root", "reports"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(
                dir="/private/tmp",
            ) as directory, tempfile.TemporaryDirectory(
                dir="/private/tmp",
            ) as external_directory:
                parent = Path(directory)
                root = parent / "store"
                root.mkdir()
                if target == "reports":
                    (root / "reports").mkdir()
                external = Path(external_directory)
                sentinel = external / "sentinel.txt"
                sentinel.write_text("unchanged", encoding="utf-8")
                detached = parent / f"detached-{target}"
                swapped = False

                def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal swapped
                    if dir_fd is None:
                        descriptor = real_open(path, flags, mode)
                    else:
                        descriptor = real_open(
                            path,
                            flags,
                            mode,
                            dir_fd=dir_fd,
                        )
                    is_target = (
                        target == "root"
                        and (
                            (dir_fd is None and Path(path) == root)
                            or (dir_fd is not None and path == root.name)
                        )
                    ) or (
                        target == "reports"
                        and dir_fd is not None
                        and path == "reports"
                    )
                    if not swapped and is_target:
                        swapped = True
                        source = root if target == "root" else root / "reports"
                        os.replace(source, detached)
                        os.symlink(external, source)
                    return descriptor

                with patch(
                    "radar.formal_readiness_store.os.open",
                    side_effect=open_and_swap,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "formal_readiness_store_path_unverified",
                    ):
                        save_formal_readiness(self.report(), root)

                self.assertTrue(swapped)
                self.assertEqual(
                    tuple(path.name for path in external.iterdir()),
                    ("sentinel.txt",),
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_save_fails_closed_without_directory_nofollow_flags(self):
        from unittest.mock import patch

        from radar.formal_readiness_store import save_formal_readiness

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            with patch("radar.formal_readiness_store.os.O_NOFOLLOW", None):
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_readiness_secure_flags_unavailable",
                ):
                    save_formal_readiness(self.report(), root)
            self.assertFalse((root / "latest.json").exists())

    def test_load_directory_fds_reject_path_component_symlink_swaps(self):
        from unittest.mock import patch

        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        real_open = os.open
        for target in ("root", "reports", "latest", "report"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(
                dir="/private/tmp",
            ) as directory, tempfile.TemporaryDirectory(
                dir="/private/tmp",
            ) as external_directory:
                parent = Path(directory)
                root = parent / "store"
                ref = save_formal_readiness(self.report(), root)
                external = Path(external_directory)
                sentinel = external / "sentinel.json"
                sentinel.write_text('{"sentinel":"unchanged"}', encoding="utf-8")
                detached = parent / f"detached-{target}"
                swapped = False

                def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal swapped
                    if dir_fd is None:
                        descriptor = real_open(path, flags, mode)
                    else:
                        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                    is_target = (
                        target == "root"
                        and (
                            (dir_fd is None and Path(path) == root)
                            or (dir_fd is not None and path == root.name)
                        )
                    ) or (
                        target == "reports"
                        and dir_fd is not None
                        and path == "reports"
                    ) or (
                        target == "latest"
                        and dir_fd is not None
                        and path == "latest.json"
                    ) or (
                        target == "report"
                        and dir_fd is not None
                        and path == f"{ref.content_sha256}.json"
                    )
                    if not swapped and is_target:
                        swapped = True
                        if target == "root":
                            source = root
                            replacement = external
                        elif target == "reports":
                            source = root / "reports"
                            replacement = external
                        elif target == "latest":
                            source = root / "latest.json"
                            replacement = sentinel
                        else:
                            source = root / ref.relative_path
                            replacement = sentinel
                        os.replace(source, detached)
                        os.symlink(replacement, source)
                    return descriptor

                with patch(
                    "radar.formal_readiness_store.os.open",
                    side_effect=open_and_swap,
                ):
                    result = load_latest_formal_readiness(root)

                self.assertTrue(swapped)
                self.assertEqual(result.status, "failed")
                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    '{"sentinel":"unchanged"}',
                )

    def test_load_fails_closed_without_directory_nofollow_flags(self):
        from unittest.mock import patch

        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            save_formal_readiness(self.report(), root)
            with patch("radar.formal_readiness_store.os.O_NOFOLLOW", None):
                result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason_codes,
            ("formal_readiness_manifest_unverified",),
        )

    def test_save_creates_previously_missing_root_and_reports_directory(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "nested" / "store"
            ref = save_formal_readiness(self.report(), root)

            self.assertEqual(ref.status, "available")
            self.assertTrue((root / ref.relative_path).is_file())
            self.assertEqual(load_latest_formal_readiness(root).status, "available")

    def test_missing_root_rejects_symlink_ancestor_for_save(self):
        from radar.formal_readiness_store import save_formal_readiness

        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
        ) as directory, tempfile.TemporaryDirectory(
            dir="/private/tmp",
        ) as external_directory:
            parent = Path(directory)
            external = Path(external_directory)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            link = parent / "external-link"
            os.symlink(external, link)
            missing_root = link / "nested" / "store"

            with self.assertRaisesRegex(
                ValueError,
                "formal_readiness_store_path_unverified",
            ):
                save_formal_readiness(self.report(), missing_root)

            self.assertEqual(
                tuple(path.name for path in external.iterdir()),
                ("sentinel.txt",),
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_load_rejects_symlink_ancestor(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
        ) as directory, tempfile.TemporaryDirectory(
            dir="/private/tmp",
        ) as external_directory:
            parent = Path(directory)
            external = Path(external_directory)
            external_store = external / "existing" / "store"
            save_formal_readiness(self.report(), external_store)
            link = parent / "external-link"
            os.symlink(external, link)

            result = load_latest_formal_readiness(link / "existing" / "store")

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason_codes,
            ("formal_readiness_manifest_unverified",),
        )

    def test_missing_root_rejects_non_directory_ancestor(self):
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            blocker = Path(directory) / "not-a-directory"
            blocker.write_text("unchanged", encoding="utf-8")
            root = blocker / "nested" / "store"

            with self.assertRaisesRegex(
                ValueError,
                "formal_readiness_store_path_unverified",
            ):
                save_formal_readiness(self.report(), root)
            result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason_codes,
            ("formal_readiness_manifest_unverified",),
        )

    def test_manifest_rejects_non_hex_digest_before_escaping_report_read(self):
        from unittest.mock import patch
        import radar.formal_readiness_store as store
        from radar.formal_readiness_store import load_latest_formal_readiness

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            malicious = "../" + "a" * 61
            self.assertEqual(len(malicious), 64)
            (root / f"{'a' * 61}.json").write_text(
                '{"sensitive":"must-not-be-read"}',
                encoding="utf-8",
            )
            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-formal-readiness-manifest-v1",
                "reportRelativePath": f"reports/{malicious}.json",
                "contentSha256": malicious,
                "checkedAt": CHECKED_AT.isoformat(),
                "state": "ready_to_enable",
                "stage9ReplayRunId": "stage9-run-1",
            }), encoding="utf-8")
            real_open = store.os.open

            def reject_escape_read(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None and path == f"{malicious}.json":
                    raise AssertionError("escaping report read attempted")
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with patch(
                "radar.formal_readiness_store.os.open",
                side_effect=reject_escape_read,
            ):
                result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason_codes,
            ("formal_readiness_manifest_unverified",),
        )

    def test_oversized_latest_is_rejected_before_json_decode(self):
        from unittest.mock import patch
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            save_formal_readiness(self.report(), root)
            (root / "latest.json").write_bytes(b" " * 200_000)
            with patch(
                "radar.formal_readiness_store.json.load",
                side_effect=AssertionError("oversized latest was decoded"),
            ):
                result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")

    def test_oversized_report_is_rejected_before_second_json_decode(self):
        from unittest.mock import patch
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        real_json_load = json.load
        calls = 0

        def decode_manifest_only(file):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise AssertionError("oversized report was decoded")
            return real_json_load(file)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            (root / ref.relative_path).write_bytes(b" " * 9_000_000)
            with patch(
                "radar.formal_readiness_store.json.load",
                side_effect=decode_manifest_only,
            ):
                result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, 1)

    def test_report_metadata_change_after_read_is_rejected(self):
        from unittest.mock import patch
        from radar.formal_readiness_store import (
            load_latest_formal_readiness,
            save_formal_readiness,
        )

        real_json_load = json.load
        calls = 0
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            ref = save_formal_readiness(self.report(), root)
            report_path = root / ref.relative_path
            original = report_path.stat()

            def decode_then_touch_report(file):
                nonlocal calls
                calls += 1
                payload = real_json_load(file)
                if calls == 2:
                    os.utime(
                        report_path,
                        ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000),
                    )
                return payload

            with patch(
                "radar.formal_readiness_store.json.load",
                side_effect=decode_then_touch_report,
            ):
                result = load_latest_formal_readiness(root)

        self.assertEqual(result.status, "failed")
