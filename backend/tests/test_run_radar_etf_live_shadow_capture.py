import json
import hashlib
import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.test_radar_etf_live_shadow_capture import (
    admission_bundle,
    batch,
    collection_policy,
    health,
    quote,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
OBSERVED_AT = datetime(2026, 9, 7, 10, 0, 3, tzinfo=SHANGHAI)


class EtfLiveShadowCaptureCliTests(unittest.TestCase):
    def test_input_parent_replacement_after_read_is_rejected(self):
        import run_radar_etf_live_shadow_capture as cli

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(b'{"ok":true}')
            calls = 0
            original = cli._verify_root_path

            def swapped(path, descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("ancestor_replaced")
                return original(path, descriptor)

            with patch.object(cli, "_verify_root_path", side_effect=swapped):
                with self.assertRaisesRegex(ValueError, "input_unverified"):
                    cli._read_regular_file(source, maximum_bytes=1024)

    def test_verified_macos_tmp_alias_normalizes_to_private_tmp(self):
        from run_radar_etf_live_shadow_capture import _private_tmp_path

        self.assertEqual(
            _private_tmp_path("/tmp/stage10-etf-alias/input.json"),
            Path("/private/tmp/stage10-etf-alias/input.json"),
        )

    def test_one_shot_cli_reads_explicit_inputs_then_publishes_and_registers(self):
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            admission = root / "admission.json"
            calendar_input = root / "calendar.json"
            calendar_document = root / "calendar.html"
            policy = root / "policy.json"
            for path, payload in (
                (admission, b'{"admission":true}'),
                (calendar_input, b'{"calendar":true}'),
                (calendar_document, b"<html>official</html>"),
                (policy, b'{"policy":true}'),
            ):
                path.write_bytes(payload)
            output_root = root / "inputs"
            ledger_dir = root / "ledger"
            calls = []
            capture = SimpleNamespace(
                artifact=SimpleNamespace(lock_state="acquired"),
                receipt=SimpleNamespace(
                    observed_at=OBSERVED_AT,
                    lock_state="acquired",
                ),
            )

            def capture_runner(**kwargs):
                calls.append(("capture", kwargs))
                from radar.formal_shadow_input_bundle import (
                    PrivateTmpNoFollowFileLock,
                )

                self.assertTrue(output_root.is_dir())
                self.assertEqual(stat.S_IMODE(output_root.stat().st_mode), 0o700)
                self.assertIs(kwargs["lock_factory"], PrivateTmpNoFollowFileLock)
                return capture

            def publisher(**kwargs):
                calls.append(("publish", kwargs))
                self.assertEqual(kwargs["capture"], capture)
                self.assertEqual(kwargs["formal_admission_json_bytes"], b'{"admission":true}')
                self.assertEqual(kwargs["calendar_document_bytes"], b"<html>official</html>")
                return SimpleNamespace(
                    module="etfObservation",
                    input_dir=output_root / "published" / "etfObservation",
                )

            def registrar(module, input_dir, ledger_root, *, evaluated_at):
                calls.append(("register", {
                    "module": module,
                    "input_dir": input_dir,
                    "ledger_dir": ledger_root,
                    "evaluated_at": evaluated_at,
                }))
                return SimpleNamespace(
                    status="available",
                    content_sha256="a" * 64,
                    relative_path="ledgers/aa/report.json",
                )

            result = run_cli(
                [
                    "--confirm-live-etf-shadow",
                    "--symbols", "510300", "515050",
                    "--run-id", "stage10-etf-20260907-1",
                    "--admission-file", str(admission),
                    "--calendar-envelope-file", str(calendar_input),
                    "--calendar-document-file", str(calendar_document),
                    "--collection-policy-file", str(policy),
                    "--output-root", str(output_root),
                    "--ledger-dir", str(ledger_dir),
                ],
                hooks=EtfCaptureCliHooks(
                    capture=capture_runner,
                    publish=publisher,
                    register=registrar,
                ),
            )

        self.assertEqual([name for name, _ in calls], ["capture", "publish", "register"])
        self.assertEqual(calls[0][1]["symbols"], ("510300", "515050"))
        self.assertTrue(str(calls[0][1]["lock_path"]).startswith("/private/tmp/"))
        self.assertEqual(result, {
            "contentSha256": "a" * 64,
            "ledgerRelativePath": "ledgers/aa/report.json",
            "module": "etfObservation",
            "status": "available",
        })
        self.assertNotIn("/private/tmp", json.dumps(result))

    def test_confirmation_and_private_tmp_paths_fail_before_capture(self):
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        calls = []
        hooks = EtfCaptureCliHooks(
            capture=lambda **kwargs: calls.append(kwargs),
            publish=lambda **kwargs: None,
            register=lambda *args, **kwargs: None,
        )
        common = [
            "--symbols", "510300",
            "--run-id", "run-1",
            "--admission-file", "/private/tmp/a.json",
            "--calendar-envelope-file", "/private/tmp/c.json",
            "--calendar-document-file", "/private/tmp/c.html",
            "--collection-policy-file", "/private/tmp/p.json",
            "--output-root", "/private/tmp/output",
            "--ledger-dir", "/private/tmp/ledger",
        ]
        with self.assertRaisesRegex(ValueError, "confirmation_required"):
            run_cli(common, hooks=hooks)
        outside = [
            "--confirm-live-etf-shadow",
            *common[:-2],
            "--ledger-dir", "/var/tmp/ledger",
        ]
        with self.assertRaisesRegex(ValueError, "private_tmp_required"):
            run_cli(outside, hooks=hooks)
        self.assertEqual(calls, [])

    def test_symlinked_output_root_is_rejected_before_capture(self):
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            for name in ("a.json", "c.json", "c.html", "p.json"):
                (root / name).write_bytes(b"x")
            real = root / "real-output"
            real.mkdir(mode=0o700)
            alias = root / "output"
            os.symlink(real, alias)
            calls = []
            with self.assertRaisesRegex(ValueError, "output_root_unverified"):
                run_cli(
                    [
                        "--confirm-live-etf-shadow",
                        "--symbols", "510300",
                        "--run-id", "run-1",
                        "--admission-file", str(root / "a.json"),
                        "--calendar-envelope-file", str(root / "c.json"),
                        "--calendar-document-file", str(root / "c.html"),
                        "--collection-policy-file", str(root / "p.json"),
                        "--output-root", str(alias),
                        "--ledger-dir", str(root / "ledger"),
                    ],
                    hooks=EtfCaptureCliHooks(
                        capture=lambda **kwargs: calls.append(kwargs),
                        publish=lambda **kwargs: None,
                        register=lambda *args, **kwargs: None,
                    ),
                )
        self.assertEqual(calls, [])

    def test_calendar_larger_than_ledger_limit_fails_before_capture(self):
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            for name in ("a.json", "c.json", "p.json"):
                (root / name).write_bytes(b"x")
            (root / "c.html").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            calls = []
            with self.assertRaisesRegex(ValueError, "input_unverified"):
                run_cli(
                    [
                        "--confirm-live-etf-shadow",
                        "--symbols", "510300",
                        "--run-id", "run-1",
                        "--admission-file", str(root / "a.json"),
                        "--calendar-envelope-file", str(root / "c.json"),
                        "--calendar-document-file", str(root / "c.html"),
                        "--collection-policy-file", str(root / "p.json"),
                        "--output-root", str(root / "output"),
                        "--ledger-dir", str(root / "ledger"),
                    ],
                    hooks=EtfCaptureCliHooks(
                        capture=lambda **kwargs: calls.append(kwargs),
                        publish=lambda **kwargs: None,
                        register=lambda *args, **kwargs: None,
                    ),
                )
        self.assertEqual(calls, [])

    def test_default_publish_and_register_complete_real_private_tmp_e2e(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        symbols = ("510300", "515050")
        capture = build_etf_live_shadow_capture(
            symbols=symbols,
            radar_run_id="live-run-1",
            as_of=OBSERVED_AT.replace(second=0),
            quote_batch=batch(quote(symbols[0]), quote(symbols[1])),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=3,
        )
        calendar_document = (
            b"<html><strong>2026\xe5\xb9\xb4\xe4\xbc\x91\xe5\xb8\x82\xe5\xae\x89\xe6\x8e\x92</strong>"
            b"<table><tr><td>\xe5\x9b\xbd\xe5\xba\x86\xe8\x8a\x82</td><td>10\xe6\x9c\x881\xe6\x97\xa5\xe8\x87\xb37\xe6\x97\xa5\xe4\xbc\x91\xe5\xb8\x82</td></tr></table></html>"
        )
        calendar_envelope = {
            "contractVersion": "radar-formal-shadow-calendar-input-v1",
            "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
            "year": 2026,
            "fetchedAt": "2026-09-07T09:59:30+08:00",
            "observedThrough": "2026-09-07",
            "sourceDocumentSha256": hashlib.sha256(calendar_document).hexdigest(),
        }

        def encoded(value):
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            files = {
                "admission.json": encoded(admission_bundle(symbols)),
                "calendar.json": encoded(calendar_envelope),
                "calendar.html": calendar_document,
                "policy.json": encoded(collection_policy()),
            }
            for name, payload in files.items():
                (root / name).write_bytes(payload)
            output_root = root / "output-not-created"
            ledger_root = root / "ledger-not-created"
            result = run_cli(
                [
                    "--confirm-live-etf-shadow",
                    "--symbols", *symbols,
                    "--run-id", "live-run-1",
                    "--admission-file", str(root / "admission.json"),
                    "--calendar-envelope-file", str(root / "calendar.json"),
                    "--calendar-document-file", str(root / "calendar.html"),
                    "--collection-policy-file", str(root / "policy.json"),
                    "--output-root", str(output_root),
                    "--ledger-dir", str(ledger_root),
                ],
                hooks=EtfCaptureCliHooks(capture=lambda **kwargs: capture),
            )

            self.assertEqual(result["status"], "available")
            self.assertEqual(len(result["contentSha256"]), 64)
            self.assertTrue((ledger_root / "latest.json").is_file())
            self.assertTrue(output_root.is_dir())
            self.assertTrue(ledger_root.is_dir())
            self.assertEqual(stat.S_IMODE(output_root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(ledger_root.stat().st_mode), 0o700)
            self.assertNotIn("/private/tmp", json.dumps(result))

    def test_contended_capture_does_not_publish_or_claim_the_day(self):
        from radar.etf_live_shadow_capture import (
            build_etf_live_shadow_capture,
            run_live_etf_shadow_capture,
        )
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        symbols = ("510300", "515050")
        calendar_document = (
            b"<html><strong>2026\xe5\xb9\xb4\xe4\xbc\x91\xe5\xb8\x82\xe5\xae\x89\xe6\x8e\x92</strong>"
            b"<table><tr><td>\xe5\x9b\xbd\xe5\xba\x86\xe8\x8a\x82</td><td>10\xe6\x9c\x881\xe6\x97\xa5\xe8\x87\xb37\xe6\x97\xa5\xe4\xbc\x91\xe5\xb8\x82</td></tr></table></html>"
        )
        calendar_envelope = {
            "contractVersion": "radar-formal-shadow-calendar-input-v1",
            "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
            "year": 2026,
            "fetchedAt": "2026-09-07T09:59:30+08:00",
            "observedThrough": "2026-09-07",
            "sourceDocumentSha256": hashlib.sha256(calendar_document).hexdigest(),
        }

        def encoded(value):
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            files = {
                "admission.json": encoded(admission_bundle(symbols)),
                "calendar.json": encoded(calendar_envelope),
                "calendar.html": calendar_document,
                "policy.json": encoded(collection_policy()),
            }
            for name, payload in files.items():
                (root / name).write_bytes(payload)
            output_root = root / "output"
            ledger_root = root / "ledger"
            arguments = [
                "--confirm-live-etf-shadow",
                "--symbols", *symbols,
                "--run-id", "live-run-1",
                "--admission-file", str(root / "admission.json"),
                "--calendar-envelope-file", str(root / "calendar.json"),
                "--calendar-document-file", str(root / "calendar.html"),
                "--collection-policy-file", str(root / "policy.json"),
                "--output-root", str(output_root),
                "--ledger-dir", str(ledger_root),
            ]
            quote_calls = []

            class ContendedLock:
                def acquire(self, blocking=False):
                    return False

                def release(self):
                    raise AssertionError("contended lock cannot be released")

            def contended_capture(**_kwargs):
                wall_times = iter((OBSERVED_AT.replace(second=0), OBSERVED_AT))
                monotonic_times = iter((100.0, 100.01))
                return run_live_etf_shadow_capture(
                    symbols=symbols,
                    radar_run_id="live-run-1",
                    lock_path=output_root / ".stage10-etf-live-shadow.lock",
                    quote_fetcher=lambda *_args, **_kwargs: quote_calls.append(True),
                    clock=lambda: next(wall_times),
                    monotonic_clock=lambda: next(monotonic_times),
                    lock_factory=lambda _path: ContendedLock(),
                )

            contended = run_cli(
                arguments,
                hooks=EtfCaptureCliHooks(capture=contended_capture),
            )
            self.assertEqual(contended, {
                "contentSha256": None,
                "ledgerRelativePath": None,
                "module": "etfObservation",
                "status": "contended",
            })
            self.assertEqual(quote_calls, [])
            self.assertFalse(ledger_root.exists())
            self.assertEqual(
                [item.name for item in output_root.iterdir()],
                [],
            )

            ready_capture = build_etf_live_shadow_capture(
                symbols=symbols,
                radar_run_id="live-run-1",
                as_of=OBSERVED_AT.replace(second=0),
                quote_batch=batch(quote(symbols[0]), quote(symbols[1])),
                quote_health=health(),
                observed_at=OBSERVED_AT,
                lock_state="acquired",
                duration_ms=3,
            )
            ready = run_cli(
                arguments,
                hooks=EtfCaptureCliHooks(capture=lambda **kwargs: ready_capture),
            )
            self.assertEqual(ready["status"], "available")
            self.assertTrue((ledger_root / "latest.json").is_file())

    def test_duplicate_symbols_fail_before_capture(self):
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        calls = []
        with self.assertRaisesRegex(ValueError, "symbols_invalid"):
            run_cli(
                [
                    "--confirm-live-etf-shadow",
                    "--symbols", "510300", "510300",
                    "--run-id", "run-1",
                    "--admission-file", "/private/tmp/a.json",
                    "--calendar-envelope-file", "/private/tmp/c.json",
                    "--calendar-document-file", "/private/tmp/c.html",
                    "--collection-policy-file", "/private/tmp/p.json",
                    "--output-root", "/private/tmp/output",
                    "--ledger-dir", "/private/tmp/ledger",
                ],
                hooks=EtfCaptureCliHooks(
                    capture=lambda **kwargs: calls.append(kwargs),
                    publish=lambda **kwargs: None,
                    register=lambda *args, **kwargs: None,
                ),
            )
        self.assertEqual(calls, [])

    def test_symlinked_input_parent_is_rejected_before_capture(self):
        import os
        from run_radar_etf_live_shadow_capture import EtfCaptureCliHooks, run_cli

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            for name in ("a.json", "c.json", "c.html", "p.json"):
                (real / name).write_bytes(b"x")
            alias = root / "alias"
            os.symlink(real, alias)
            output = root / "output"
            ledger = root / "ledger"
            calls = []
            with self.assertRaisesRegex(ValueError, "input_unverified"):
                run_cli(
                    [
                        "--confirm-live-etf-shadow",
                        "--symbols", "510300",
                        "--run-id", "run-1",
                        "--admission-file", str(alias / "a.json"),
                        "--calendar-envelope-file", str(alias / "c.json"),
                        "--calendar-document-file", str(alias / "c.html"),
                        "--collection-policy-file", str(alias / "p.json"),
                        "--output-root", str(output),
                        "--ledger-dir", str(ledger),
                    ],
                    hooks=EtfCaptureCliHooks(
                        capture=lambda **kwargs: calls.append(kwargs),
                        publish=lambda **kwargs: None,
                        register=lambda *args, **kwargs: None,
                    ),
                )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
