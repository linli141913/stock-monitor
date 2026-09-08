import json
import hashlib
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


UTC = timezone.utc
BASE_TIME = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


class _Calendar:
    def is_trading_day(self, value):
        return value.weekday() < 5


class FormalShadowLedgerStoreTests(unittest.TestCase):
    def ledger(self, *, observed_at=BASE_TIME):
        from radar.formal_shadow_ledger import build_shadow_ledger

        return build_shadow_ledger(({
            "module": "trendRotation",
            "runId": "trend-20260904",
            "observedAt": observed_at,
            "sourceTime": observed_at - timedelta(minutes=2),
            "fetchedAt": observed_at - timedelta(minutes=1),
            "coverage": 1.0,
            "missingCount": 0,
            "failedCount": 0,
            "staleCount": 0,
            "lockState": "acquired",
            "durationMs": 3,
            "evidenceSha256": "a" * 64,
        },), calendar_provider=_Calendar())

    def calendar_ledger(self):
        from radar.formal_shadow_calendar import (
            OfficialSseCalendarProvider,
            load_official_sse_calendar_evidence,
        )
        from radar.formal_shadow_ledger import build_shadow_ledger

        raw = (
            "<html><strong>2026年休市安排</strong><table>"
            "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
            "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
            "</table></html>"
        ).encode("utf-8")
        envelope = json.dumps({
            "contractVersion": "radar-formal-shadow-calendar-input-v1",
            "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
            "year": 2026,
            "fetchedAt": "2026-09-04T16:00:00+08:00",
            "observedThrough": "2026-09-04",
            "sourceDocumentSha256": hashlib.sha256(raw).hexdigest(),
        }, ensure_ascii=False).encode("utf-8")
        evidence = load_official_sse_calendar_evidence(envelope, raw)
        ledger = build_shadow_ledger(({
            "module": "trendRotation",
            "runId": "trend-calendar-20260904",
            "observedAt": BASE_TIME,
            "sourceTime": BASE_TIME - timedelta(minutes=2),
            "fetchedAt": BASE_TIME - timedelta(minutes=1),
            "coverage": 1.0,
            "missingCount": 0,
            "failedCount": 0,
            "staleCount": 0,
            "lockState": "acquired",
            "durationMs": 3,
            "evidenceSha256": "b" * 64,
        },), calendar_provider=OfficialSseCalendarProvider((evidence,)), calendar_evidence=(evidence,))
        return ledger, raw

    def test_save_is_content_addressed_and_same_ledger_is_unchanged(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            first = save_formal_shadow_ledger(self.ledger(), root)
            second = save_formal_shadow_ledger(self.ledger(), root)
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))

            self.assertEqual(first.status, "available")
            self.assertEqual(second.status, "unchanged")
            self.assertEqual(first.content_sha256, second.content_sha256)
            self.assertEqual(manifest["ledgerRelativePath"], f"reports/{first.content_sha256}.json")
            self.assertEqual(load_latest_formal_shadow_ledger(root).ledger, self.ledger())
            self.assertFalse(list(root.rglob("*.tmp")))

    def test_store_accepts_only_v2_and_keeps_modules_in_one_immutable_ledger(self):
        from radar.formal_shadow_ledger import FormalShadowLedger
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        ledger = self.ledger()
        legacy = FormalShadowLedger(
            observations=ledger.observations,
            readyTradingDaysByModule=ledger.ready_trading_days_by_module,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(TypeError, "formal_shadow_ledger_v2_required"):
                save_formal_shadow_ledger(legacy, Path(directory))

    def test_verified_macos_tmp_alias_uses_the_same_store_when_available(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        alias, expected = Path("/tmp"), Path("/private/tmp")
        if not (alias.is_symlink() and Path(os.path.realpath(alias)) == expected):
            self.skipTest("macOS fixed /tmp alias is not present")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            alias_root = Path(directory) / "store"
            canonical_root = expected / alias_root.relative_to(alias)
            saved = save_formal_shadow_ledger(self.ledger(), alias_root)
            self.assertEqual(load_latest_formal_shadow_ledger(canonical_root).stored_ref, saved)

    def test_concurrent_lock_returns_contended_without_writing(self):
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            root.mkdir()
            (root / ".formal-shadow-ledger.lock").write_text("lock", encoding="utf-8")
            result = save_formal_shadow_ledger(self.ledger(), root)

            self.assertEqual(result.status, "contended")
            self.assertFalse((root / "reports").exists())
            self.assertFalse((root / "latest.json").exists())

    def test_load_is_closed_for_missing_tamper_future_and_wrong_version(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            missing = load_latest_formal_shadow_ledger(root)
            self.assertEqual((missing.status, missing.reason_codes), (
                "missing", ("formal_shadow_ledger_missing",),
            ))
            ref = save_formal_shadow_ledger(self.ledger(), root)
            report = root / ref.relative_path
            report.write_text('{"changed":true}', encoding="utf-8")
            self.assertEqual(
                load_latest_formal_shadow_ledger(root).reason_codes,
                ("formal_shadow_ledger_hash_mismatch",),
            )

            wrong_version_root = Path(directory) / "wrong-version"
            save_formal_shadow_ledger(self.ledger(), wrong_version_root)
            manifest_path = wrong_version_root / "latest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["contractId"] = "radar-formal-shadow-ledger-manifest-v0"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(
                load_latest_formal_shadow_ledger(wrong_version_root).reason_codes,
                ("formal_shadow_ledger_manifest_unverified",),
            )

            future = self.ledger(observed_at=datetime.now(UTC) + timedelta(days=2))
            future_root = Path(directory) / "future"
            save_formal_shadow_ledger(future, future_root)
            self.assertEqual(
                load_latest_formal_shadow_ledger(future_root).reason_codes,
                ("formal_shadow_ledger_future_observation",),
            )

    def test_load_rejects_oversized_latest_and_report_before_json_decode(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            latest_root = Path(directory) / "latest-store"
            save_formal_shadow_ledger(self.ledger(), latest_root)
            (latest_root / "latest.json").write_bytes(b" " * 200_000)
            self.assertEqual(load_latest_formal_shadow_ledger(latest_root).status, "failed")

            report_root = Path(directory) / "report-store"
            ref = save_formal_shadow_ledger(self.ledger(), report_root)
            (report_root / ref.relative_path).write_bytes(b" " * 9_000_000)
            self.assertEqual(load_latest_formal_shadow_ledger(report_root).status, "failed")

    def test_load_rejects_duplicate_json_keys_in_manifest_and_nested_report(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            manifest_root = Path(directory) / "manifest-store"
            save_formal_shadow_ledger(self.ledger(), manifest_root)
            manifest_path = manifest_root / "latest.json"
            manifest_text = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                manifest_text.replace(
                    '"contractId":',
                    '"contractId":"radar-formal-shadow-ledger-manifest-v1","contractId":',
                    1,
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_latest_formal_shadow_ledger(manifest_root).status, "failed")

            report_root = Path(directory) / "report-store"
            ref = save_formal_shadow_ledger(self.ledger(), report_root)
            report_path = report_root / ref.relative_path
            report_text = report_path.read_text(encoding="utf-8")
            report_path.write_text(
                report_text.replace(
                    '"contractVersion":',
                    '"contractVersion":"radar-formal-shadow-ledger-v2","contractVersion":',
                    1,
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_latest_formal_shadow_ledger(report_root).status, "failed")

    def test_load_rejects_non_finite_json_constants_before_contract_validation(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant), tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                root = Path(directory) / "store"
                save_formal_shadow_ledger(self.ledger(), root)
                manifest_path = root / "latest.json"
                manifest_text = manifest_path.read_text(encoding="utf-8")
                manifest_path.write_text(
                    manifest_text.replace("{", f'{{"unexpected":{constant},', 1),
                    encoding="utf-8",
                )
                self.assertEqual(load_latest_formal_shadow_ledger(root).status, "failed")

    def test_calendar_raw_is_content_addressed_bound_and_replayed_on_every_load(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        ledger, raw = self.calendar_ledger()
        sha = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            ref = save_formal_shadow_ledger(
                ledger,
                root,
                calendar_documents_by_sha256={sha: raw},
            )
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
            calendar_path = root / "calendar" / f"{sha}.html"

            self.assertEqual(ref.status, "available")
            self.assertEqual(manifest["calendarDocuments"], [{
                "year": 2026,
                "sourceDocumentSha256": sha,
                "documentRelativePath": f"calendar/{sha}.html",
            }])
            self.assertEqual(calendar_path.read_bytes(), raw)
            self.assertEqual(load_latest_formal_shadow_ledger(root).ledger, ledger)

            calendar_path.write_bytes(raw.replace(
                "10月1日至10月7日".encode("utf-8"),
                "10月1日至10月6日".encode("utf-8"),
            ))
            self.assertEqual(load_latest_formal_shadow_ledger(root).status, "failed")

    def test_calendar_ledger_requires_real_matching_raw_and_rejects_forged_closed_days(self):
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        ledger, raw = self.calendar_ledger()
        sha = hashlib.sha256(raw).hexdigest()
        evidence = ledger.calendar_evidence[0]
        forged_evidence = evidence.model_copy(update={
            "closed_days": tuple(day for day in evidence.closed_days if day.month != 10),
        })
        forged = ledger.model_copy(update={"calendar_evidence": (forged_evidence,)})
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_document_missing"):
                save_formal_shadow_ledger(ledger, Path(directory) / "missing")
            with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_document_unverified"):
                save_formal_shadow_ledger(
                    ledger,
                    Path(directory) / "mismatch",
                    calendar_documents_by_sha256={sha: b"not the official document"},
                )
            with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_evidence_mismatch"):
                save_formal_shadow_ledger(
                    forged,
                    Path(directory) / "forged",
                    calendar_documents_by_sha256={sha: raw},
                )

    def test_calendar_orphan_is_reused_after_interrupted_publish(self):
        import radar.formal_shadow_ledger_store as store
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        ledger, raw = self.calendar_ledger()
        sha = hashlib.sha256(raw).hexdigest()
        real_replace = store.os.replace
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            calls = []

            def fail_report_after_calendar(source, destination, **kwargs):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("injected_report_replace_failure")
                return real_replace(source, destination, **kwargs)

            with patch("radar.formal_shadow_ledger_store.os.replace", side_effect=fail_report_after_calendar):
                with self.assertRaises(OSError):
                    save_formal_shadow_ledger(
                        ledger,
                        root,
                        calendar_documents_by_sha256={sha: raw},
                    )

            retry = save_formal_shadow_ledger(ledger, root)
            loaded = load_latest_formal_shadow_ledger(root)

        self.assertEqual(retry.status, "available")
        self.assertEqual(loaded.ledger, ledger)

    def test_calendar_directory_symlink_and_oversized_document_fail_closed(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        ledger, raw = self.calendar_ledger()
        sha = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
            root = Path(directory) / "store"
            root.mkdir()
            external = Path(external_directory)
            sentinel = external / "sentinel"
            sentinel.write_text("unchanged", encoding="utf-8")
            os.symlink(external, root / "calendar")
            with self.assertRaisesRegex(ValueError, "formal_shadow_ledger_store_path_unverified"):
                save_formal_shadow_ledger(
                    ledger,
                    root,
                    calendar_documents_by_sha256={sha: raw},
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            save_formal_shadow_ledger(
                ledger,
                root,
                calendar_documents_by_sha256={sha: raw},
            )
            (root / "calendar" / f"{sha}.html").write_bytes(b"x" * 3_000_000)
            self.assertEqual(load_latest_formal_shadow_ledger(root).status, "failed")

    def test_load_rejects_calendar_directory_toctou_swap_without_external_read(self):
        import radar.formal_shadow_ledger_store as store
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        ledger, raw = self.calendar_ledger()
        sha = hashlib.sha256(raw).hexdigest()
        real_open = store.os.open
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
            root = Path(directory) / "store"
            save_formal_shadow_ledger(
                ledger,
                root,
                calendar_documents_by_sha256={sha: raw},
            )
            external = Path(external_directory)
            sentinel = external / f"{sha}.html"
            sentinel.write_text("must-not-be-read", encoding="utf-8")
            detached = Path(directory) / "detached-calendar"
            swapped = False

            def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)
                if not swapped and dir_fd is not None and path == "calendar":
                    swapped = True
                    os.replace(root / "calendar", detached)
                    os.symlink(external, root / "calendar")
                return descriptor

            with patch("radar.formal_shadow_ledger_store.os.open", side_effect=open_and_swap):
                result = load_latest_formal_shadow_ledger(root)
            self.assertTrue(swapped)
            self.assertEqual(result.status, "failed")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must-not-be-read")

    def test_manifest_rejects_non_hex_digest_before_any_escaping_report_read(self):
        import radar.formal_shadow_ledger_store as store
        from radar.formal_shadow_ledger_store import load_latest_formal_shadow_ledger

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            reports = root / "reports"
            reports.mkdir(parents=True)
            malicious = "../" + "a" * 61
            self.assertEqual(len(malicious), 64)
            escaped = root / f"{'a' * 61}.json"
            escaped.write_text('{"sensitive":"must-not-be-read"}', encoding="utf-8")
            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-formal-shadow-ledger-manifest-v1",
                "ledgerRelativePath": f"reports/{malicious}.json",
                "contentSha256": malicious,
                "contractVersion": "radar-formal-shadow-ledger-v2",
            }), encoding="utf-8")
            real_open = store.os.open

            def reject_escape_read(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None and path == f"{malicious}.json":
                    raise AssertionError("escaping report read attempted")
                return real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)

            with patch("radar.formal_shadow_ledger_store.os.open", side_effect=reject_escape_read):
                result = load_latest_formal_shadow_ledger(root)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason_codes, ("formal_shadow_ledger_manifest_unverified",))

    def test_save_and_load_reject_root_reports_latest_and_report_symlink_escape(self):
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
            root = Path(directory) / "store"
            external = Path(external_directory)
            sentinel = external / "sentinel.json"
            sentinel.write_text('{"unchanged":true}', encoding="utf-8")
            os.symlink(external, root)
            with self.assertRaisesRegex(ValueError, "formal_shadow_ledger_store_path_unverified"):
                save_formal_shadow_ledger(self.ledger(), root)
            self.assertEqual(load_latest_formal_shadow_ledger(root).status, "failed")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"unchanged":true}')

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
            root = Path(directory) / "store"
            ref = save_formal_shadow_ledger(self.ledger(), root)
            external = Path(external_directory) / "sentinel.json"
            external.write_text('{"unchanged":true}', encoding="utf-8")
            os.replace(root / ref.relative_path, root / "detached.json")
            os.symlink(external, root / ref.relative_path)
            self.assertEqual(load_latest_formal_shadow_ledger(root).status, "failed")
            self.assertEqual(external.read_text(encoding="utf-8"), '{"unchanged":true}')

    def test_save_rejects_reports_or_latest_symlink_before_any_external_write(self):
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        for target in ("reports", "latest.json"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
                root = Path(directory) / "store"
                root.mkdir()
                external = Path(external_directory)
                sentinel = external / "sentinel"
                sentinel.write_text("unchanged", encoding="utf-8")
                os.symlink(external, root / target)
                with self.assertRaisesRegex(ValueError, "formal_shadow_ledger_store_path_unverified"):
                    save_formal_shadow_ledger(self.ledger(), root)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_failed_latest_replace_does_not_publish_a_new_latest(self):
        import radar.formal_shadow_ledger_store as store
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            old = self.ledger()
            save_formal_shadow_ledger(old, root)
            newer = self.ledger(observed_at=BASE_TIME + timedelta(days=1))
            real_replace = store.os.replace
            calls = []

            def replace_then_fail_latest(source, destination, **kwargs):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("injected_latest_replace_failure")
                return real_replace(source, destination, **kwargs)

            with patch("radar.formal_shadow_ledger_store.os.replace", side_effect=replace_then_fail_latest):
                with self.assertRaises(OSError):
                    save_formal_shadow_ledger(newer, root)
            loaded = load_latest_formal_shadow_ledger(root)
            self.assertEqual(loaded.status, "available")
            self.assertEqual(loaded.ledger, old)

    def test_retry_repairs_latest_after_report_publish_succeeded_but_latest_failed(self):
        import radar.formal_shadow_ledger_store as store
        from radar.formal_shadow_ledger_store import (
            load_latest_formal_shadow_ledger,
            save_formal_shadow_ledger,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "store"
            ledger = self.ledger()
            real_replace = store.os.replace
            calls = []

            def replace_report_then_fail_latest(source, destination, **kwargs):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("injected_latest_replace_failure")
                return real_replace(source, destination, **kwargs)

            with patch("radar.formal_shadow_ledger_store.os.replace", side_effect=replace_report_then_fail_latest):
                with self.assertRaises(OSError):
                    save_formal_shadow_ledger(ledger, root)

            self.assertFalse((root / "latest.json").exists())
            retry = save_formal_shadow_ledger(ledger, root)
            loaded = load_latest_formal_shadow_ledger(root)

        self.assertEqual(retry.status, "available")
        self.assertEqual(loaded.status, "available")
        self.assertEqual(loaded.ledger, ledger)

    def test_directory_fd_revalidation_rejects_toctou_reports_swap(self):
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        real_open = os.open
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as external_directory:
            root = Path(directory) / "store"
            root.mkdir()
            (root / "reports").mkdir()
            external = Path(external_directory)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            detached = Path(directory) / "detached"
            swapped = False

            def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = (real_open(path, flags, mode) if dir_fd is None else real_open(path, flags, mode, dir_fd=dir_fd))
                if not swapped and dir_fd is not None and path == "reports":
                    swapped = True
                    os.replace(root / "reports", detached)
                    os.symlink(external, root / "reports")
                return descriptor

            with patch("radar.formal_shadow_ledger_store.os.open", side_effect=open_and_swap):
                with self.assertRaisesRegex(ValueError, "formal_shadow_ledger_store_path_unverified"):
                    save_formal_shadow_ledger(self.ledger(), root)
            self.assertTrue(swapped)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_save_wraps_root_removed_after_open_without_leaking_absolute_path(self):
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger

        real_open = os.open
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            parent = Path(directory)
            root = parent / "store"
            root.mkdir()
            detached = parent / "detached-store"
            root_open_count = 0

            def open_then_remove_root_name(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal root_open_count
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)
                if dir_fd is not None and path == root.name:
                    root_open_count += 1
                    if root_open_count == 2:
                        os.replace(root, detached)
                return descriptor

            with patch("radar.formal_shadow_ledger_store.os.open", side_effect=open_then_remove_root_name):
                with self.assertRaises(ValueError) as captured:
                    save_formal_shadow_ledger(self.ledger(), root)

        self.assertEqual(str(captured.exception), "formal_shadow_ledger_store_path_unverified")
        self.assertNotIn(str(root), str(captured.exception))
