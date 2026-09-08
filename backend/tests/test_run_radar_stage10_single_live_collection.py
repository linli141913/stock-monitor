import unittest
import json
import hashlib
import tempfile
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


class Stage10SingleLiveCollectionCliTests(unittest.TestCase):
    def test_cli_module_exposes_explicit_confirmation_entrypoint(self):
        import run_radar_stage10_single_live_collection as module

        self.assertTrue(callable(module.run_cli))

    def test_cli_dry_preflight_happens_before_default_binding_validation(self):
        import run_radar_stage10_single_live_collection as module

        cases = (
            ([], datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), "dry_confirmation_required"),
            (["--confirm-live-stage10-collection"], datetime(2026, 9, 6, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), "dry_not_trading"),
            (["--confirm-live-stage10-collection"], datetime(2026, 9, 8, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")), "dry_not_continuous_session"),
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            for extra, current, expected in cases:
                with self.subTest(expected=expected):
                    attempt = Path(directory) / expected
                    output = StringIO()
                    calendar_calls = []
                    with patch.object(
                        module,
                        "_default_bindings",
                        side_effect=AssertionError("dry must not build defaults"),
                    ):
                        code = module.run_cli(
                            [*extra, "--attempt-root", str(attempt)],
                            stdout=output,
                            now=lambda current=current: current,
                            local_calendar_verifier=lambda day: calendar_calls.append(day) or "full",
                        )
                    self.assertNotEqual(code, 0)
                    self.assertEqual(
                        json.loads(output.getvalue())["preflight"]["status"],
                        expected,
                    )
                    self.assertEqual(calendar_calls, [])
                    self.assertFalse(attempt.exists())

    def test_cli_rejects_slot_not_in_frozen_manifest_before_bindings(self):
        import run_radar_stage10_single_live_collection as module
        from tests.test_radar_stage10_live_collection import (
            Stage10LiveCollectionWorkflowTests,
        )

        now = datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            schedule = Stage10LiveCollectionWorkflowTests._schedule_arguments(root)
            output = StringIO()
            with patch.object(
                module,
                "_default_bindings",
                side_effect=AssertionError("invalid slot must not build defaults"),
            ):
                code = module.run_cli(
                    [
                        "--confirm-live-stage10-collection",
                        "--attempt-root", str(root / "attempt"),
                        "--schedule-store-root", str(schedule["schedule_store_root"]),
                        "--schedule-manifest-sha256", schedule["schedule_manifest_sha256"],
                        "--slot-id", "missing-slot",
                    ],
                    stdout=output,
                    now=lambda: now,
                    local_calendar_verifier=lambda _day: self.fail(
                        "invalid slot must fail before calendar"
                    ),
                )

            self.assertNotEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["reason"],
                "stage10_frozen_schedule_unverified",
            )
            self.assertFalse((root / "attempt").exists())

    def test_default_bindings_reach_existing_stage9_shadow_and_etf_entries(self):
        import run_radar_stage10_single_live_collection as module
        from tests.test_radar_stage10_live_collection import (
            Stage10LiveCollectionWorkflowTests,
        )

        now = datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir(mode=0o700)
            formal_root = root / "formal"
            trend = formal_root / "published" / "trendRotation"
            leader = formal_root / "published" / "leaderObservation"
            Stage10LiveCollectionWorkflowTests._make_input(trend, "trendRotation")
            Stage10LiveCollectionWorkflowTests._make_input(leader, "leaderObservation")
            admission = root / "cohort" / "baseline" / "admission.json"
            admission.parent.mkdir(parents=True)
            admission.write_text("{}")
            manifest = root / "cohort" / "formal-cohort-manifest.json"
            manifest.write_text(json.dumps({
                "contractId": "radar-replay-formal-cohort-run-v1",
                "role": "development",
                "sampleId": "sample-1",
                "radarRunId": "run-1",
            }))
            manifest.chmod(0o600)
            schedule = Stage10LiveCollectionWorkflowTests._schedule_arguments(root)
            output = StringIO()
            calls = []

            def stage9(argv, *, stdout, **_kwargs):
                calls.append("stage9")
                self.assertIn("--formal-shadow-input-root", argv)
                stdout.write(json.dumps({
                    "status": "registered",
                    "role": "development",
                    "sampleId": "sample-1",
                    "radarRunId": "run-1",
                    "formalShadowInputDirs": {
                        "trendRotation": str(trend),
                        "leaderObservation": str(leader),
                    },
                    "etfFormalAdmissionPath": "baseline/admission.json",
                    "manifestPath": "formal-cohort-manifest.json",
                    "manifestRelativePath": str(manifest.relative_to(Path("/private/tmp"))),
                    "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                }))
                return 0

            def register(module_name, _input, _ledger, *, evaluated_at):
                calls.append(module_name)
                return SimpleNamespace(
                    status="available",
                    content_sha256="a" * 64,
                    relative_path="ledger/%s.json" % module_name,
                )

            etf_input = root / "etf-output" / "published"
            Stage10LiveCollectionWorkflowTests._make_input(
                etf_input, "etfObservation",
            )

            def etf_entry(argv, *, hooks):
                calls.append("etf")
                hooks.register(
                    "etfObservation",
                    etf_input,
                    root / "ledger",
                    evaluated_at=now,
                )
                return {
                    "status": "available",
                    "contentSha256": "e" * 64,
                    "ledgerRelativePath": "ledger/etf.json",
                }

            with patch.object(module, "run_stage9_cli", stage9), patch.object(
                module, "run_shadow_observation", register,
            ), patch.object(
                module,
                "run_etf_cli",
                etf_entry,
            ):
                code = module.run_cli(
                    [
                        "--confirm-live-stage10-collection",
                        "--attempt-root", str(root / "attempt"),
                        "--schedule-store-root", str(schedule["schedule_store_root"]),
                        "--schedule-manifest-sha256", schedule["schedule_manifest_sha256"],
                        "--slot-id", schedule["slot_id"],
                        "--attempt-universe-root", str(root / "attempt-universe"),
                        "--campaign-dir", str(campaign),
                        "--stage6-output-dir", str(root / "stage6"),
                        "--cohort-output-dir", str(root / "cohort"),
                        "--formal-shadow-input-root", str(formal_root),
                        "--initialize-sector-state",
                        "--formal-etf", "515050",
                        "--ledger-dir", str(root / "ledger"),
                        "--etf-output-root", str(root / "etf-output"),
                    ],
                    stdout=output,
                    now=lambda: now,
                    local_calendar_verifier=lambda _day: "full",
                )
                self.assertTrue(
                    (root / "attempt-universe" / "latest.json").is_file()
                )

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            ["stage9", "trendRotation", "leaderObservation", "etf", "etfObservation"],
        )

    def test_default_etf_binding_collects_independent_admission_when_stage9_is_partial(self):
        import run_radar_stage10_single_live_collection as module
        from radar.stage10_live_collection import Stage10Stage9Result
        from tests.test_radar_etf_live_shadow_capture import admission_bundle

        now = datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            trend = root / "trend"
            trend.mkdir(mode=0o700)
            for name in (
                module.CALENDAR_INPUT_FILENAME,
                module.CALENDAR_DOCUMENT_FILENAME,
                module.COLLECTION_POLICY_FILENAME,
            ):
                (trend / name).write_text("{}")
            admission = root / "independent" / "etf-formal-admission.json"
            admission.parent.mkdir(mode=0o700)
            admission.write_text(json.dumps(admission_bundle(
                ("515050",), as_of=now, run_id="run-1",
            )))
            admission.chmod(0o600)
            arguments = SimpleNamespace(
                campaign_dir=root / "campaign",
                stage6_output_dir=root / "stage6",
                cohort_output_dir=root / "cohort",
                formal_shadow_input_root=root / "formal",
                ledger_dir=root / "ledger",
                etf_output_root=root / "etf-output",
                schedule_store_root=root / "schedule",
                schedule_manifest_sha256="a" * 64,
                slot_id="slot-1",
                attempt_universe_root=root / "universe",
                initialize_sector_state=True,
                previous_sector_state=None,
                cninfo_pdf_cache_dir=None,
                formal_etf=["515050"],
            )
            stage9 = Stage10Stage9Result(
                status="failed",
                reason="leader_business_unverified",
                radarRunId="run-1",
                trendInputRelativePath=str(trend.relative_to(Path("/private/tmp"))),
            )
            seen = {}

            def collect(**kwargs):
                seen["collect"] = kwargs
                return SimpleNamespace(etf_formal_admission_path=admission)

            def capture(argv, *, hooks):
                seen["argv"] = argv
                return {"status": "skipped", "reason": "quote_missing"}

            with patch.object(
                module, "collect_forward_replay_baseline", collect,
            ), patch.object(module, "run_etf_cli", capture):
                _, _, etf = module._default_bindings(
                    arguments,
                    now=lambda: now,
                    local_calendar_verifier=lambda _day: "full",
                )
                result = etf(stage9)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(seen["collect"]["radar_run_id"], "run-1")
        self.assertEqual(seen["collect"]["formal_etf_symbols"], ("515050",))
        admission_index = seen["argv"].index("--admission-file")
        self.assertEqual(Path(seen["argv"][admission_index + 1]), admission)

    def test_failed_stage_returns_nonzero_and_is_not_listed_as_effect(self):
        import run_radar_stage10_single_live_collection as module

        output = StringIO()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            from tests.test_radar_stage10_live_collection import (
                Stage10LiveCollectionWorkflowTests,
            )
            root = Path(directory)
            schedule = Stage10LiveCollectionWorkflowTests._schedule_arguments(root)
            code = module.run_cli(
                [
                    "--confirm-live-stage10-collection",
                    "--attempt-root", str(root / "attempt"),
                    "--schedule-store-root", str(schedule["schedule_store_root"]),
                    "--schedule-manifest-sha256", schedule["schedule_manifest_sha256"],
                    "--slot-id", schedule["slot_id"],
                ],
                stdout=output,
                now=lambda: datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                local_calendar_verifier=lambda _day: "full",
                stage9_runner=lambda: {"status": "failed", "reason": "stage9_closed"},
                shadow_registrar=lambda *_args: self.fail("no registration"),
                etf_runner=lambda *_args: self.fail("no ETF"),
            )
        payload = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertNotIn("stage9", payload["effects"])

    def test_default_stage9_stdout_rejects_non_strict_json(self):
        import run_radar_stage10_single_live_collection as module
        from tests.test_radar_stage10_live_collection import (
            Stage10LiveCollectionWorkflowTests,
        )

        malicious_payloads = (
            '{"status":"failed","status":"registered"}',
            '{"status":"registered","nested":{"x":1,"x":2}}',
            '{"status":"registered","value":NaN}',
            '{"status":"registered","value":Infinity}',
            '{"status":"registered","value":-Infinity}',
        )
        now = datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        for malicious in malicious_payloads:
            with self.subTest(payload=malicious), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                campaign = root / "campaign"
                campaign.mkdir(mode=0o700)
                schedule = Stage10LiveCollectionWorkflowTests._schedule_arguments(root)

                def stage9(_argv, *, stdout, **_kwargs):
                    stdout.write(malicious)
                    return 0

                arguments = module._parser().parse_args([
                    "--confirm-live-stage10-collection",
                    "--attempt-root", str(root / "attempt"),
                    "--schedule-store-root", str(schedule["schedule_store_root"]),
                    "--schedule-manifest-sha256", schedule["schedule_manifest_sha256"],
                    "--slot-id", schedule["slot_id"],
                    "--attempt-universe-root", str(root / "universe"),
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(root / "stage6"),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--formal-shadow-input-root", str(root / "formal"),
                    "--initialize-sector-state",
                    "--formal-etf", "515050",
                    "--ledger-dir", str(root / "ledger"),
                    "--etf-output-root", str(root / "etf"),
                ])
                with patch.object(module, "run_stage9_cli", stage9):
                    default_stage9 = module._default_bindings(
                        arguments,
                        now=lambda: now,
                        local_calendar_verifier=lambda _day: "full",
                    )[0]
                    with self.assertRaisesRegex(
                        ValueError,
                        "stage10_live_collection_stage9_unverified",
                    ):
                        default_stage9()

    def test_default_stage9_stdout_rejects_invalid_utf8(self):
        import run_radar_stage10_single_live_collection as module

        arguments = SimpleNamespace(
            campaign_dir=Path("/private/tmp/campaign"),
            stage6_output_dir=Path("/private/tmp/stage6"),
            cohort_output_dir=Path("/private/tmp/cohort"),
            formal_shadow_input_root=Path("/private/tmp/formal"),
            ledger_dir=Path("/private/tmp/ledger"),
            etf_output_root=Path("/private/tmp/etf"),
            schedule_store_root=Path("/private/tmp/schedule"),
            schedule_manifest_sha256="a" * 64,
            slot_id="slot-1",
            attempt_universe_root=Path("/private/tmp/universe"),
            initialize_sector_state=True,
            previous_sector_state=None,
            formal_etf=["515050"],
            cninfo_pdf_cache_dir=None,
        )

        def stage9(_argv, *, stdout, **_kwargs):
            stdout.write(b"\xff")
            return 0

        with patch.object(module, "StringIO", BytesIO), patch.object(
            module, "run_stage9_cli", stage9,
        ):
            default_stage9 = module._default_bindings(
                arguments,
                now=lambda: datetime.now(ZoneInfo("Asia/Shanghai")),
                local_calendar_verifier=lambda _day: "full",
            )[0]
            with self.assertRaisesRegex(
                ValueError,
                "stage10_live_collection_stage9_unverified",
            ):
                default_stage9()

    def test_default_etf_registration_runtime_error_preserves_input_for_recovery(self):
        import run_radar_stage10_single_live_collection as module
        from tests.test_radar_stage10_live_collection import (
            Stage10LiveCollectionWorkflowTests,
        )

        now = datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir(mode=0o700)
            formal = root / "formal"
            trend = formal / "published" / "trendRotation"
            leader = formal / "published" / "leaderObservation"
            etf_input = root / "etf-output" / "published"
            for path, module_name in (
                (trend, "trendRotation"),
                (leader, "leaderObservation"),
                (etf_input, "etfObservation"),
            ):
                Stage10LiveCollectionWorkflowTests._make_input(path, module_name)
            admission = root / "cohort" / "baseline" / "admission.json"
            admission.parent.mkdir(parents=True)
            admission.write_text("{}")
            manifest = root / "cohort" / "formal-cohort-manifest.json"
            manifest.write_text(json.dumps({
                "contractId": "radar-replay-formal-cohort-run-v1",
                "role": "development",
                "sampleId": "sample-1",
                "radarRunId": "run-1",
            }))
            manifest.chmod(0o600)
            schedule = Stage10LiveCollectionWorkflowTests._schedule_arguments(root)
            stage9_calls = []
            etf_calls = []
            registrations = []

            def stage9(_argv, *, stdout, **_kwargs):
                stage9_calls.append(True)
                stdout.write(json.dumps({
                    "status": "registered",
                    "role": "development",
                    "sampleId": "sample-1",
                    "radarRunId": "run-1",
                    "formalShadowInputDirs": {
                        "trendRotation": str(trend),
                        "leaderObservation": str(leader),
                    },
                    "etfFormalAdmissionPath": "baseline/admission.json",
                    "manifestPath": "formal-cohort-manifest.json",
                    "manifestRelativePath": str(manifest.relative_to(Path("/private/tmp"))),
                    "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                }))
                return 0

            def register(module_name, _input, _ledger, *, evaluated_at):
                registrations.append(module_name)
                if (
                    module_name == "etfObservation"
                    and registrations.count(module_name) == 1
                ):
                    raise RuntimeError("registrar engineering failure")
                return SimpleNamespace(
                    status="unchanged",
                    content_sha256="a" * 64,
                    relative_path="ledger/%s.json" % module_name,
                )

            def etf_entry(_argv, *, hooks):
                etf_calls.append(True)
                hooks.register(
                    "etfObservation",
                    etf_input,
                    root / "ledger",
                    evaluated_at=now,
                )
                raise AssertionError("unreachable after registrar error")

            argv = [
                "--confirm-live-stage10-collection",
                "--attempt-id", "attempt-recover-1",
                "--attempt-root", str(root / "attempt"),
                "--schedule-store-root", str(schedule["schedule_store_root"]),
                "--schedule-manifest-sha256", schedule["schedule_manifest_sha256"],
                "--slot-id", schedule["slot_id"],
                "--attempt-universe-root", str(root / "attempt-universe"),
                "--campaign-dir", str(campaign),
                "--stage6-output-dir", str(root / "stage6"),
                "--cohort-output-dir", str(root / "cohort"),
                "--formal-shadow-input-root", str(formal),
                "--initialize-sector-state",
                "--formal-etf", "515050",
                "--ledger-dir", str(root / "ledger"),
                "--etf-output-root", str(root / "etf-output"),
            ]
            with patch.object(module, "run_stage9_cli", stage9), patch.object(
                module, "run_shadow_observation", register,
            ), patch.object(module, "run_etf_cli", etf_entry):
                first = module.run_cli(
                    argv,
                    stdout=StringIO(),
                    now=lambda: now,
                    local_calendar_verifier=lambda _day: "full",
                )
                second = module.run_cli(
                    argv,
                    stdout=StringIO(),
                    now=lambda: now,
                    local_calendar_verifier=lambda _day: "full",
                )

        self.assertNotEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(len(stage9_calls), 1)
        self.assertEqual(len(etf_calls), 1)
        self.assertEqual(registrations.count("etfObservation"), 2)


if __name__ == "__main__":
    unittest.main()
