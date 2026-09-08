import unittest
import tempfile
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import ValidationError
from unittest.mock import patch


SHANGHAI = ZoneInfo("Asia/Shanghai")


class Stage10LiveCollectionPreflightTests(unittest.TestCase):
    def test_frozen_schedule_store_round_trip_is_content_addressed(self):
        from radar.stage10_live_collection import (
            Stage10FrozenSchedule,
            Stage10FrozenScheduleSlot,
            load_stage10_frozen_schedule,
            publish_stage10_frozen_schedule,
        )
        from radar.formal_operational_evidence_collector import (
            ScheduleManifestStoreBinding,
            load_schedule_manifest_store,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "schedule-store"
            schedule = Stage10FrozenSchedule(
                contractVersion="radar-stage10-schedule-manifest-v1",
                scheduleId="stage10-week-2026-09-08",
                evidenceScope="live",
                officialMarket="cn",
                policyVersion="radar-stage10-schedule-policy-v1",
                sourceEvidence=({
                    "year": 2026,
                    "sourceDocumentSha256": "c" * 64,
                    "observedThrough": "2026-12-31",
                },),
                officialTradingDates=("2026-09-08",),
                slots=(Stage10FrozenScheduleSlot(
                    slotId="cn-20260908-1000",
                    tradeDate="2026-09-08",
                    scheduleSlot="2026-09-08T10:00:00+08:00",
                    effectiveFrom="2026-09-08T09:59:00+08:00",
                    effectiveUntil="2026-09-08T10:01:00+08:00",
                    moduleScope=(
                        "stage9", "trendRotation",
                        "leaderObservation", "etfObservation",
                    ),
                ),),
            )
            manifest_sha = publish_stage10_frozen_schedule(root, schedule)
            loaded = load_stage10_frozen_schedule(root, manifest_sha)
            task2_loaded = load_schedule_manifest_store(
                Path("/private/tmp"),
                ScheduleManifestStoreBinding(
                    relativePath=str(root.relative_to("/private/tmp")),
                    expectedContentSha256=manifest_sha,
                ),
            )
            latest = json.loads((root / "latest.json").read_text())

        self.assertEqual(loaded, schedule)
        self.assertEqual(
            task2_loaded.model_dump(mode="json", by_alias=True),
            schedule.model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(latest["contentSha256"], manifest_sha)
        self.assertEqual(
            latest["relativePath"],
            f"manifests/{manifest_sha}.json",
        )

    def test_frozen_schedule_rejects_duplicate_or_noncontinuous_slots(self):
        from radar.stage10_live_collection import (
            Stage10FrozenSchedule,
            Stage10FrozenScheduleSlot,
        )

        common = {
            "slotId": "cn-20260908-lunch",
            "tradeDate": "2026-09-08",
            "scheduleSlot": "2026-09-08T12:00:00+08:00",
            "effectiveFrom": "2026-09-08T11:59:00+08:00",
            "effectiveUntil": "2026-09-08T12:01:00+08:00",
            "moduleScope": (
                "stage9", "trendRotation",
                "leaderObservation", "etfObservation",
            ),
        }
        with self.assertRaises(ValidationError):
            Stage10FrozenScheduleSlot.model_validate(common)
        valid = dict(common)
        valid.update({
            "slotId": "cn-20260908-1000",
            "scheduleSlot": "2026-09-08T10:00:00+08:00",
            "effectiveFrom": "2026-09-08T09:59:00+08:00",
            "effectiveUntil": "2026-09-08T10:01:00+08:00",
        })
        with self.assertRaises(ValidationError):
            Stage10FrozenSchedule.model_validate({
                "contractVersion": "radar-stage10-schedule-manifest-v1",
                "scheduleId": "duplicate",
                "evidenceScope": "live",
                "officialMarket": "cn",
                "policyVersion": "radar-stage10-schedule-policy-v1",
                "sourceEvidence": ({
                    "year": 2026,
                    "sourceDocumentSha256": "c" * 64,
                    "observedThrough": "2026-12-31",
                },),
                "officialTradingDates": ("2026-09-08",),
                "slots": (valid, valid),
            })
        second_same_day = dict(valid)
        second_same_day.update({
            "slotId": "cn-20260908-1010",
            "scheduleSlot": "2026-09-08T10:10:00+08:00",
            "effectiveFrom": "2026-09-08T10:09:00+08:00",
            "effectiveUntil": "2026-09-08T10:11:00+08:00",
        })
        with self.assertRaises(ValidationError):
            Stage10FrozenSchedule.model_validate({
                "contractVersion": "radar-stage10-schedule-manifest-v1",
                "scheduleId": "duplicate-day",
                "evidenceScope": "live",
                "officialMarket": "cn",
                "policyVersion": "radar-stage10-schedule-policy-v1",
                "sourceEvidence": ({
                    "year": 2026,
                    "sourceDocumentSha256": "c" * 64,
                    "observedThrough": "2026-12-31",
                },),
                "officialTradingDates": ("2026-09-08",),
                "slots": (valid, second_same_day),
            })

    def test_contract_models_reject_unknown_status_contract_and_enabled_true(self):
        from radar.stage10_live_collection import (
            Stage10LiveCollectionResult,
            Stage10ModuleResult,
            Stage10Preflight,
        )

        for model, payload in (
            (Stage10Preflight, {"status": "invented"}),
            (Stage10ModuleResult, {"status": "invented"}),
        ):
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.model_validate(payload)
        base = self._result_payload()
        for key, value in (
            ("contractId", "forged-contract"),
            ("formalEnabled", True),
            ("formalEnabled", 0),
        ):
            with self.subTest(key=key):
                forged = dict(base)
                forged[key] = value
                with self.assertRaises(ValidationError):
                    Stage10LiveCollectionResult.model_validate(forged)

    @staticmethod
    def _result_payload():
        common = {"status": "not_attempted"}
        return {
            "contractId": "radar-stage10-single-live-collection-v1",
            "attemptId": "attempt-1",
            "startedAt": "2026-09-08T10:00:00+08:00",
            "finishedAt": "2026-09-08T10:00:00+08:00",
            "tradeDate": "2026-09-08",
            "preflight": {"status": "ready"},
            "stage9": common,
            "trendRotation": common,
            "leaderObservation": common,
            "etfObservation": common,
            "formalEnabled": False,
        }
    def test_unconfirmed_request_has_no_side_effects(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        calls = []
        result = run_stage10_live_collection(
            confirmed=False,
            attempt_root=Path("/private/tmp/stage10-test-unconfirmed"),
            now=lambda: datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI),
            local_calendar_verifier=lambda _day: calls.append("calendar") or "full",
            stage9_runner=lambda: calls.append("stage9"),
            shadow_registrar=lambda *_args, **_kwargs: calls.append("register"),
            etf_runner=lambda: calls.append("etf"),
        )

        self.assertEqual(result.preflight.status, "dry_confirmation_required")
        self.assertFalse(result.formal_enabled)
        self.assertEqual(calls, [])

    def test_weekend_never_calls_calendar_or_creates_attempt_root(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        calls = []
        root = Path("/private/tmp/stage10-test-weekend-must-not-exist")
        self.assertFalse(root.exists())
        result = run_stage10_live_collection(
            confirmed=True,
            attempt_root=root,
            now=lambda: datetime(2026, 9, 6, 10, 0, tzinfo=SHANGHAI),
            local_calendar_verifier=lambda _day: calls.append("calendar") or "full",
            stage9_runner=lambda: calls.append("stage9"),
            shadow_registrar=lambda *_args, **_kwargs: calls.append("register"),
            etf_runner=lambda: calls.append("etf"),
        )

        self.assertEqual(result.preflight.status, "dry_not_trading")
        self.assertEqual(calls, [])
        self.assertFalse(root.exists())

    def test_unverified_calendar_and_midday_are_zero_side_effect_dry_runs(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        for current, calendar_result, expected in (
            (datetime(2026, 9, 8, 10, 0, tzinfo=SHANGHAI), None, "dry_calendar_unverified"),
            (datetime(2026, 9, 8, 12, 0, tzinfo=SHANGHAI), "full", "dry_not_continuous_session"),
        ):
            with self.subTest(expected=expected):
                calls = []
                root = Path("/private/tmp/stage10-test-dry-%s" % expected)
                result = run_stage10_live_collection(
                    confirmed=True,
                    attempt_root=root,
                    now=lambda current=current: current,
                    local_calendar_verifier=lambda _day, result=calendar_result: calls.append("calendar") or result,
                    stage9_runner=lambda: calls.append("stage9"),
                    shadow_registrar=lambda *_args, **_kwargs: calls.append("register"),
                    etf_runner=lambda: calls.append("etf"),
                )
                self.assertEqual(result.preflight.status, expected)
                self.assertEqual(
                    calls,
                    ["calendar"] if expected == "dry_calendar_unverified" else [],
                )
                self.assertFalse(root.exists())


class Stage10LiveCollectionWorkflowTests(unittest.TestCase):
    @staticmethod
    def _schedule_arguments(root, *, slot_id="cn-20260908-1000"):
        from radar.stage10_live_collection import (
            Stage10FrozenSchedule,
            publish_stage10_frozen_schedule,
        )

        store = root / "schedule-store"
        schedule = Stage10FrozenSchedule.model_validate({
            "contractVersion": "radar-stage10-schedule-manifest-v1",
            "scheduleId": "stage10-week-2026-09-08",
            "evidenceScope": "live",
            "officialMarket": "cn",
            "policyVersion": "radar-stage10-schedule-policy-v1",
            "sourceEvidence": ({
                "year": 2026,
                "sourceDocumentSha256": "c" * 64,
                "observedThrough": "2026-12-31",
            },),
            "officialTradingDates": ("2026-09-08",),
            "slots": ({
                "slotId": slot_id,
                "tradeDate": "2026-09-08",
                "scheduleSlot": "2026-09-08T10:00:00+08:00",
                "effectiveFrom": "2026-09-08T09:59:00+08:00",
                "effectiveUntil": "2026-09-08T10:01:00+08:00",
                "moduleScope": (
                    "stage9", "trendRotation",
                    "leaderObservation", "etfObservation",
                ),
            },),
        })
        return {
            "schedule_store_root": store,
            "schedule_manifest_sha256": publish_stage10_frozen_schedule(
                store, schedule
            ),
            "slot_id": slot_id,
        }

    @staticmethod
    def _make_input(path, module, *, run_id="run-1"):
        from radar.formal_shadow_input_bundle import (
            CALENDAR_DOCUMENT_FILENAME,
            CALENDAR_INPUT_FILENAME,
            COLLECTION_POLICY_FILENAME,
            ETF_ADMISSION_FILENAME,
            RECEIPT_FILENAME,
            SOURCE_ARTIFACT_FILENAME,
            TREND_SUPPORTING_FILENAME,
        )
        from radar.formal_shadow_observation_collector import (
            FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
            MODULE_SOURCE_CONTRACTS,
        )

        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        files = {
            RECEIPT_FILENAME: json.dumps({
                "contractId": FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
                "module": module,
                "sourceContractId": MODULE_SOURCE_CONTRACTS[module],
                "runId": run_id,
            }).encode(),
            SOURCE_ARTIFACT_FILENAME: b'{"payload":true}',
            COLLECTION_POLICY_FILENAME: b'{"policy":true}',
            CALENDAR_INPUT_FILENAME: b'{"calendar":true}',
            CALENDAR_DOCUMENT_FILENAME: b"official-calendar",
        }
        if module == "trendRotation":
            files[TREND_SUPPORTING_FILENAME] = b'{"sector":true}'
        if module == "etfObservation":
            files[ETF_ADMISSION_FILENAME] = b'{"admission":true}'
        for name, payload in files.items():
            target = path / name
            target.write_bytes(payload)
            target.chmod(0o600)

    def _live_arguments(self, root, calls, *, stage9_payload=None, registrar=None, etf=None):
        input_root = root / "inputs"
        trend = input_root / "trend"
        leader = input_root / "leader"
        self._make_input(trend, "trendRotation")
        self._make_input(leader, "leaderObservation")
        manifest = root / "cohort" / "formal-cohort-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "contractId": "radar-replay-formal-cohort-run-v1",
            "role": "development",
            "sampleId": "sample-1",
            "radarRunId": "run-1",
        }))
        manifest.chmod(0o600)
        import hashlib
        stage9_payload = stage9_payload or {
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
        }
        etf_input = root / "inputs" / "etf"
        self._make_input(etf_input, "etfObservation")
        return {
            "confirmed": True,
            "attempt_root": root / "attempt",
            "attempt_id": "attempt-1",
            "now": lambda: datetime(2026, 9, 8, 10, 0, tzinfo=SHANGHAI),
            "local_calendar_verifier": lambda _day: "full",
            "stage9_runner": lambda: calls.append("stage9") or stage9_payload,
            "shadow_registrar": registrar or (
                lambda module, _input: calls.append(module) or {
                    "status": "available",
                    "contentSha256": "a" * 64,
                    "ledgerRelativePath": "ledger/%s.json" % module,
                }
            ),
            "etf_runner": etf or (
                lambda _stage9: calls.append("etf") or {
                    "status": "available",
                    "contentSha256": "e" * 64,
                    "ledgerRelativePath": "ledger/etf.json",
                    "inputPath": str(etf_input),
                }
            ),
        }

    def test_successful_etf_without_published_input_reference_is_rejected(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            calls = []
            result = run_stage10_live_collection(**self._live_arguments(
                Path(directory),
                calls,
                etf=lambda _stage9: {
                    "status": "available",
                    "contentSha256": "e" * 64,
                    "ledgerRelativePath": "ledger/etf.json",
                },
            ))
        self.assertEqual(result.etf_observation.status, "failed")

    def test_success_commits_each_independent_module_with_formal_disabled(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            calls = []
            result = run_stage10_live_collection(**self._live_arguments(Path(directory), calls))

        self.assertEqual(calls, ["stage9", "trendRotation", "leaderObservation", "etf"])
        self.assertEqual(result.stage9.status, "available")
        self.assertEqual(result.trend_rotation.status, "available")
        self.assertEqual(result.leader_observation.status, "available")
        self.assertEqual(result.etf_observation.status, "available")
        self.assertFalse(result.formal_enabled)

    def test_collection_uses_actual_invocation_time_as_started_at(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            arguments.update(self._schedule_arguments(root))
            arguments["now"] = lambda: datetime(
                2026, 9, 8, 9, 59, 30, tzinfo=SHANGHAI
            )
            result = run_stage10_live_collection(**arguments)

        self.assertEqual(result.started_at.isoformat(), "2026-09-08T09:59:30+08:00")
        self.assertEqual(result.trade_date.isoformat(), "2026-09-08")

    def test_missing_or_expired_frozen_slot_fails_before_calendar_and_writes(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            for slot_id, current in (
                ("missing-slot", datetime(2026, 9, 8, 10, 0, tzinfo=SHANGHAI)),
                ("cn-20260908-1000", datetime(2026, 9, 8, 10, 2, tzinfo=SHANGHAI)),
            ):
                with self.subTest(slot_id=slot_id, current=current):
                    calls = []
                    arguments = self._live_arguments(root / slot_id, calls)
                    schedule = self._schedule_arguments(root / slot_id)
                    schedule["slot_id"] = slot_id
                    arguments.update(schedule)
                    arguments["now"] = lambda current=current: current
                    arguments["local_calendar_verifier"] = (
                        lambda _day: calls.append("calendar") or "full"
                    )
                    with self.assertRaisesRegex(
                        ValueError, "frozen_schedule_slot_unverified"
                    ):
                        run_stage10_live_collection(**arguments)
                    self.assertEqual(calls, [])
                    self.assertFalse(arguments["attempt_root"].exists())

    def test_frozen_schedule_official_day_failure_is_dry_before_writes(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            arguments.update(self._schedule_arguments(root))
            arguments["local_calendar_verifier"] = (
                lambda day: calls.append(day) or None
            )
            result = run_stage10_live_collection(**arguments)

            self.assertEqual(result.preflight.status, "dry_calendar_unverified")
            self.assertEqual(calls, [datetime(2026, 9, 8).date()])
            self.assertFalse(arguments["attempt_root"].exists())

    def test_trend_failure_does_not_rollback_or_block_leader_and_etf(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            calls = []

            def registrar(module, _input):
                calls.append(module)
                if module == "trendRotation":
                    raise RuntimeError("network-like path must not leak")
                return {
                    "status": "available",
                    "contentSha256": "b" * 64,
                    "ledgerRelativePath": "ledger/leader.json",
                }

            result = run_stage10_live_collection(**self._live_arguments(
                Path(directory), calls, registrar=registrar,
            ))

        self.assertEqual(result.trend_rotation.status, "failed")
        self.assertEqual(result.leader_observation.status, "available")
        self.assertEqual(result.etf_observation.status, "available")
        self.assertEqual(calls, ["stage9", "trendRotation", "leaderObservation", "etf"])
        self.assertNotIn("trendRotation", result.effects)

    def test_stage9_failure_never_calls_etf_or_shadow_registration(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            calls = []
            result = run_stage10_live_collection(**self._live_arguments(
                Path(directory),
                calls,
                stage9_payload={"status": "failed", "reason": "stage9_closed"},
            ))

        self.assertEqual(result.stage9.status, "failed")
        self.assertEqual(result.etf_observation.status, "not_attempted")
        self.assertEqual(calls, ["stage9"])

    def test_stage9_failure_with_verified_trend_input_keeps_etf_independent(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            normal = arguments["stage9_runner"]()
            calls.clear()
            partial = {
                "status": "failed",
                "reason": "etf_source_failed",
                "radarRunId": normal["radarRunId"],
                "formalShadowInputDirs": normal["formalShadowInputDirs"],
            }
            arguments["stage9_runner"] = (
                lambda: calls.append("stage9") or partial
            )
            result = run_stage10_live_collection(**arguments)

        self.assertEqual(result.stage9.status, "failed")
        self.assertEqual(result.stage9.reason, "etf_source_failed")
        self.assertEqual(result.trend_rotation.status, "available")
        self.assertEqual(result.leader_observation.status, "available")
        self.assertEqual(result.etf_observation.status, "available")
        self.assertEqual(
            calls, ["stage9", "trendRotation", "leaderObservation", "etf"]
        )

    def test_stage9_failure_with_only_trend_input_registers_trend_independently(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            normal = arguments["stage9_runner"]()
            calls.clear()
            partial = {
                "status": "stage6_not_ready",
                "reason": "leader_business_evidence_qualification_empty",
                "radarRunId": normal["radarRunId"],
                "formalShadowInputDirs": {
                    "trendRotation": normal["formalShadowInputDirs"][
                        "trendRotation"
                    ],
                },
            }
            arguments["stage9_runner"] = (
                lambda: calls.append("stage9") or partial
            )
            result = run_stage10_live_collection(**arguments)

        self.assertEqual(result.stage9.status, "failed")
        self.assertEqual(
            result.stage9.reason,
            "leader_business_evidence_qualification_empty",
        )
        self.assertEqual(result.trend_rotation.status, "available")
        self.assertEqual(result.leader_observation.status, "skipped")
        self.assertEqual(
            result.leader_observation.reason,
            "stage10_input_unavailable",
        )
        self.assertEqual(result.etf_observation.status, "available")
        self.assertEqual(calls, ["stage9", "trendRotation", "etf"])

    def test_recovery_replays_published_etf_input_without_rerunning_stage9_or_etf_capture(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            etf_input = root / "etf-published"
            self._make_input(etf_input, "etfObservation")

            def etf_runner(_stage9):
                calls.append("etf-capture")
                return {
                    "status": "failed",
                    "reason": "registration_failed",
                    "inputPath": str(etf_input),
                }

            first = run_stage10_live_collection(**self._live_arguments(
                root, calls, etf=etf_runner,
            ))
            self.assertEqual(first.etf_observation.status, "failed")

            def registrar(module, _input):
                calls.append("recover-" + module)
                return {
                    "status": "unchanged",
                    "contentSha256": "c" * 64,
                    "ledgerRelativePath": "ledger/%s.json" % module,
                }

            second = run_stage10_live_collection(**self._live_arguments(
                root, calls, registrar=registrar,
                etf=lambda _stage9: self.fail("ETF capture must not rerun"),
            ))

        self.assertEqual(second.etf_observation.status, "unchanged")
        self.assertEqual(calls.count("stage9"), 1)
        self.assertEqual(calls.count("etf-capture"), 1)
        self.assertIn("recover-etfObservation", calls)

    def test_symlinked_stage9_input_is_rejected_before_registration(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            real_trend = root / "inputs" / "trend"
            alias = root / "trend-alias"
            os.symlink(real_trend, alias)
            payload = dict(arguments["stage9_runner"]())
            calls.clear()
            payload["formalShadowInputDirs"] = dict(payload["formalShadowInputDirs"])
            payload["formalShadowInputDirs"]["trendRotation"] = str(alias)
            arguments["stage9_runner"] = lambda: calls.append("stage9") or payload
            result = run_stage10_live_collection(**arguments)

        self.assertEqual(result.stage9.status, "failed")
        self.assertEqual(calls, ["stage9"])

    def test_recovery_rejects_same_path_with_different_published_content(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            etf_input = root / "etf-published"
            self._make_input(etf_input, "etfObservation")
            first = run_stage10_live_collection(**self._live_arguments(
                root,
                calls,
                etf=lambda _stage9: {
                    "status": "failed",
                    "reason": "registration_failed",
                    "inputPath": str(etf_input),
                },
            ))
            self.assertEqual(first.etf_observation.status, "failed")
            source = etf_input / "radar-formal-shadow-source-artifact-v1.json"
            source.write_bytes(b'{"payload":"replaced"}')
            source.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "input_reference_unverified"):
                run_stage10_live_collection(**self._live_arguments(
                    root,
                    calls,
                    registrar=lambda *_args: self.fail("replaced input must not register"),
                    etf=lambda _stage9: self.fail("ETF capture must not rerun"),
                ))

    def test_completed_attempt_revalidates_all_published_input_hashes(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            run_stage10_live_collection(**arguments)
            source = (
                root / "inputs" / "trend"
                / "radar-formal-shadow-source-artifact-v1.json"
            )
            source.write_bytes(b'{"payload":"changed-after-commit"}')
            source.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "input_reference_unverified"):
                run_stage10_live_collection(**arguments)

    def test_recovery_rejects_formal_manifest_content_replacement(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            run_stage10_live_collection(**arguments)
            manifest = root / "cohort" / "formal-cohort-manifest.json"
            manifest.write_text('{"contractId":"radar-replay-formal-cohort-run-v1"}')
            manifest.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "manifest_reference_unverified"):
                run_stage10_live_collection(**arguments)

    def test_checkpoint_store_is_content_addressed_and_rejects_world_readable_pointer(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            run_stage10_live_collection(**arguments)
            attempt = root / "attempt"
            pointer = attempt / "latest.json"
            self.assertTrue(pointer.is_file())
            pointer.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "checkpoint_unverified"):
                run_stage10_live_collection(**arguments)

    def test_stage9_run_id_must_match_both_published_receipts(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            self._make_input(
                root / "inputs" / "leader",
                "leaderObservation",
                run_id="different-run",
            )
            result = run_stage10_live_collection(**arguments)
        self.assertEqual(result.stage9.status, "failed")
        self.assertEqual(calls, ["stage9"])

    def test_lock_acquire_root_replacement_is_rejected_before_stage9(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            attempt = root / "attempt"

            class ReplacingLock:
                def __init__(self, _path):
                    pass

                def acquire(self, blocking=False):
                    attempt.rename(root / "renamed-attempt")
                    attempt.mkdir(mode=0o700)
                    return True

                def release(self):
                    calls.append("released")

            arguments["lock_factory"] = ReplacingLock
            with self.assertRaisesRegex(ValueError, "attempt_root_unverified"):
                run_stage10_live_collection(**arguments)
        self.assertEqual(calls, ["released"])

    def test_lock_identity_is_rechecked_before_every_external_runner(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        expected_calls = {
            1: [],
            3: ["stage9"],
            5: ["stage9", "trendRotation"],
            7: ["stage9", "trendRotation", "leaderObservation"],
        }
        for fail_on in expected_calls:
            with self.subTest(fail_on=fail_on):
                with tempfile.TemporaryDirectory(
                    dir="/private/tmp",
                ) as directory:
                    calls = []

                    class ReplacedLock:
                        def __init__(self, _path):
                            self.checks = 0

                        def acquire(self, blocking=False):
                            return True

                        def assert_still_held(self):
                            self.checks += 1
                            if self.checks == fail_on:
                                raise ValueError(
                                    "formal_shadow_input_bundle_lock_unverified"
                                )

                        def release(self):
                            calls.append("released")

                    arguments = self._live_arguments(
                        Path(directory),
                        calls,
                    )
                    arguments["lock_factory"] = ReplacedLock
                    with self.assertRaisesRegex(
                        ValueError,
                        "stage10_live_collection_lock_unverified",
                    ):
                        run_stage10_live_collection(**arguments)
                self.assertEqual(
                    calls,
                    expected_calls[fail_on] + ["released"],
                )

    def test_recreated_lock_inode_blocks_stage9_even_when_second_lock_acquires(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            lock_root = root / "collection-lock-root"
            lock_root.mkdir(mode=0o700)
            lock_path = lock_root / "collection.lock"
            calls = []
            competing = {"acquired": False}

            class RecreatedInodeLock:
                def __init__(self, _path):
                    self.first = PrivateTmpNoFollowFileLock(
                        lock_path,
                        protect_root=True,
                    )
                    self.second = None

                def acquire(self, blocking=False):
                    if not self.first.acquire(blocking=blocking):
                        return False
                    os.chflags(lock_root, 0, follow_symlinks=False)
                    lock_root.chmod(0o700)
                    os.chflags(lock_path, 0, follow_symlinks=False)
                    lock_path.unlink()
                    self.second = PrivateTmpNoFollowFileLock(
                        lock_path,
                        protect_root=True,
                    )
                    competing["acquired"] = self.second.acquire(
                        blocking=False,
                    )
                    return True

                def assert_still_held(self):
                    self.first.assert_still_held()

                def release(self):
                    if self.second is not None:
                        self.second.release()
                    self.first.release()
                    if lock_path.exists():
                        os.chflags(lock_path, 0, follow_symlinks=False)
                    os.chflags(lock_root, 0, follow_symlinks=False)
                    lock_root.chmod(0o700)

            arguments = self._live_arguments(root, calls)
            arguments["lock_factory"] = RecreatedInodeLock
            with self.assertRaisesRegex(
                ValueError,
                "stage10_live_collection_lock_unverified",
            ):
                run_stage10_live_collection(**arguments)

        self.assertTrue(competing["acquired"])
        self.assertEqual(calls, [])

    def test_different_attempt_roots_share_one_global_collection_lock(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first_calls = []
            second_calls = []
            first_arguments = self._live_arguments(root / "first", first_calls)
            second_arguments = self._live_arguments(root / "second", second_calls)
            entered = threading.Event()
            release = threading.Event()
            first_result = []
            original_stage9 = first_arguments["stage9_runner"]

            def blocking_stage9():
                entered.set()
                self.assertTrue(release.wait(3))
                return original_stage9()

            first_arguments["stage9_runner"] = blocking_stage9
            worker = threading.Thread(
                target=lambda: first_result.append(
                    run_stage10_live_collection(**first_arguments)
                ),
            )
            worker.start()
            self.assertTrue(entered.wait(3))
            second = run_stage10_live_collection(**second_arguments)
            release.set()
            worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(second.preflight.status, "dry_attempt_contended")
        self.assertEqual(second_calls, [])
        self.assertEqual(first_result[0].stage9.status, "available")

    def test_attempt_id_rejects_paths_and_parent_segments_before_writes(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            for attempt_id in ("../secret", "/private/tmp/secret", "含密钥"):
                with self.subTest(attempt_id=attempt_id):
                    calls = []
                    arguments = self._live_arguments(root, calls)
                    arguments["attempt_id"] = attempt_id
                    with self.assertRaisesRegex(ValueError, "attempt_id_unverified"):
                        run_stage10_live_collection(**arguments)
                    self.assertFalse((root / "attempt").exists())
                    self.assertEqual(calls, [])

    def test_success_publishes_task2_attempt_universe_from_real_checkpoint(self):
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            arguments.update(self._schedule_arguments(root))
            arguments.update({
                "attempt_universe_root": root / "attempt-universe",
            })
            run_stage10_live_collection(**arguments)
            store = root / "attempt-universe"
            latest = json.loads((store / "latest.json").read_text())
            manifest = json.loads(
                (store / latest["manifestRelativePath"]).read_text()
            )
            binding = AttemptUniverseStoreBinding(
                relativePath=str(store.relative_to(Path("/private/tmp"))),
                expectedContentSha256=manifest["contentSha256"],
            )
            universe = load_attempt_universe_store(Path("/private/tmp"), binding)

        self.assertEqual(len(universe.expected_attempts), 1)
        entry = universe.expected_attempts[0]
        self.assertEqual(entry.attempt_id, "attempt-1")
        self.assertEqual(entry.slot_id, "cn-20260908-1000")
        self.assertEqual(
            universe.schedule_manifest_sha256,
            arguments["schedule_manifest_sha256"],
        )
        self.assertEqual(entry.expected_run_id, "run-1")
        self.assertEqual(entry.expected_lock_state, "acquired")
        self.assertEqual(entry.module_outcomes.stage9, "success")
        self.assertEqual(entry.module_outcomes.etf_observation, "success")

    def test_same_slot_appends_every_success_and_failed_invocation(self):
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            schedule = self._schedule_arguments(root)
            universe_root = root / "attempt-universe"
            for index, stage9_payload in (
                (1, None),
                (2, None),
                (3, {"status": "failed", "reason": "stage9_closed"}),
            ):
                calls = []
                arguments = self._live_arguments(
                    root / f"attempt-{index}",
                    calls,
                    stage9_payload=stage9_payload,
                )
                arguments.update(schedule)
                arguments.update({
                    "attempt_id": f"attempt-{index}",
                    "attempt_universe_root": universe_root,
                })
                run_stage10_live_collection(**arguments)
            latest = json.loads((universe_root / "latest.json").read_text())
            manifest = json.loads(
                (universe_root / latest["manifestRelativePath"]).read_text()
            )
            universe = load_attempt_universe_store(
                Path("/private/tmp"),
                AttemptUniverseStoreBinding(
                    relativePath=str(universe_root.relative_to("/private/tmp")),
                    expectedContentSha256=manifest["contentSha256"],
                ),
            )

        self.assertEqual(
            tuple(item.attempt_id for item in universe.expected_attempts),
            ("attempt-1", "attempt-2", "attempt-3"),
        )
        self.assertEqual(
            {item.slot_id for item in universe.expected_attempts},
            {"cn-20260908-1000"},
        )
        self.assertEqual(
            universe.expected_attempts[-1].module_outcomes.stage9,
            "failed",
        )

    def test_final_universe_publish_failure_leaves_pending_and_retry_upgrades_once(self):
        import radar.stage10_live_collection as module
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            arguments.update(self._schedule_arguments(root))
            universe_root = root / "attempt-universe"
            arguments["attempt_universe_root"] = universe_root
            original_publish = module._publish_attempt_universe
            publish_statuses = []

            def fail_final_publish(**kwargs):
                publish_statuses.append(kwargs["result"].stage9.status)
                if kwargs["result"].stage9.status != "not_attempted":
                    raise ValueError("injected_final_publish_failure")
                return original_publish(**kwargs)

            with patch.object(
                module,
                "_publish_attempt_universe",
                side_effect=fail_final_publish,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "injected_final_publish_failure",
                ):
                    module.run_stage10_live_collection(**arguments)

            latest = json.loads((universe_root / "latest.json").read_text())
            manifest = json.loads(
                (universe_root / latest["manifestRelativePath"]).read_text()
            )
            binding = AttemptUniverseStoreBinding(
                relativePath=str(universe_root.relative_to("/private/tmp")),
                expectedContentSha256=manifest["contentSha256"],
            )
            pending = load_attempt_universe_store(Path("/private/tmp"), binding)
            self.assertEqual(len(pending.expected_attempts), 1)
            self.assertEqual(
                pending.expected_attempts[0].module_outcomes.stage9,
                "not_attempted",
            )
            pending_sha = pending.expected_attempts[0].checkpoint_sha256

            retry_calls = []
            retry_arguments = self._live_arguments(root, retry_calls)
            retry_arguments.update(self._schedule_arguments(root))
            retry_arguments.update({
                "attempt_universe_root": universe_root,
                "attempt_id": "attempt-1",
            })
            result = module.run_stage10_live_collection(**retry_arguments)
            latest = json.loads((universe_root / "latest.json").read_text())
            manifest = json.loads(
                (universe_root / latest["manifestRelativePath"]).read_text()
            )
            final = load_attempt_universe_store(
                Path("/private/tmp"),
                AttemptUniverseStoreBinding(
                    relativePath=str(universe_root.relative_to("/private/tmp")),
                    expectedContentSha256=manifest["contentSha256"],
                ),
            )

        self.assertEqual(publish_statuses, ["not_attempted", "available"])
        self.assertEqual(retry_calls, [])
        self.assertEqual(result.stage9.status, "available")
        self.assertEqual(len(final.expected_attempts), 1)
        self.assertNotEqual(final.expected_attempts[0].checkpoint_sha256, pending_sha)
        self.assertEqual(final.expected_attempts[0].module_outcomes.stage9, "success")

    def test_second_same_slot_success_cannot_hide_when_final_publish_fails(self):
        import radar.stage10_live_collection as module
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            schedule = self._schedule_arguments(root)
            universe_root = root / "attempt-universe"
            first = self._live_arguments(root / "first", [])
            first.update(schedule)
            first.update({
                "attempt_id": "attempt-1",
                "attempt_universe_root": universe_root,
            })
            module.run_stage10_live_collection(**first)

            second = self._live_arguments(root / "second", [])
            second.update(schedule)
            second.update({
                "attempt_id": "attempt-2",
                "attempt_universe_root": universe_root,
            })
            original_publish = module._publish_attempt_universe

            def fail_second_final(**kwargs):
                if (
                    kwargs["result"].attempt_id == "attempt-2"
                    and kwargs["result"].stage9.status != "not_attempted"
                ):
                    raise ValueError("injected_final_publish_failure")
                return original_publish(**kwargs)

            with patch.object(
                module,
                "_publish_attempt_universe",
                side_effect=fail_second_final,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "injected_final_publish_failure",
                ):
                    module.run_stage10_live_collection(**second)

            latest = json.loads((universe_root / "latest.json").read_text())
            manifest = json.loads(
                (universe_root / latest["manifestRelativePath"]).read_text()
            )
            universe = load_attempt_universe_store(
                Path("/private/tmp"),
                AttemptUniverseStoreBinding(
                    relativePath=str(universe_root.relative_to("/private/tmp")),
                    expectedContentSha256=manifest["contentSha256"],
                ),
            )

        self.assertEqual(
            tuple(item.attempt_id for item in universe.expected_attempts),
            ("attempt-1", "attempt-2"),
        )
        self.assertEqual(
            tuple(item.module_outcomes.stage9 for item in universe.expected_attempts),
            ("success", "not_attempted"),
        )

    def test_contended_attempt_uses_independent_universe_lock_without_stage9(self):
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )
        from radar.stage10_live_collection import run_stage10_live_collection

        class Contended:
            def __init__(self, _path):
                pass

            def acquire(self, blocking=False):
                return False

            def release(self):
                raise AssertionError("not acquired")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            arguments = self._live_arguments(root, calls)
            arguments.update(self._schedule_arguments(root))
            arguments.update({
                "attempt_universe_root": root / "attempt-universe",
                "lock_factory": Contended,
            })
            result = run_stage10_live_collection(**arguments)
            store = root / "attempt-universe"
            latest = json.loads((store / "latest.json").read_text())
            manifest = json.loads((store / latest["manifestRelativePath"]).read_text())
            universe = load_attempt_universe_store(
                Path("/private/tmp"),
                AttemptUniverseStoreBinding(
                    relativePath=str(store.relative_to(Path("/private/tmp"))),
                    expectedContentSha256=manifest["contentSha256"],
                ),
            )

        self.assertEqual(result.preflight.status, "dry_attempt_contended")
        self.assertEqual(calls, [])
        self.assertEqual(
            universe.expected_attempts[0].expected_lock_state,
            "contended",
        )

    def test_same_attempt_root_contender_never_overwrites_winner_checkpoint(self):
        from radar.stage10_live_collection import run_stage10_live_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            entered = threading.Event()
            release = threading.Event()
            calls = []
            arguments = self._live_arguments(root, calls)
            original_stage9 = arguments["stage9_runner"]

            def blocking_stage9():
                entered.set()
                self.assertTrue(release.wait(3))
                return original_stage9()

            arguments.update({
                "stage9_runner": blocking_stage9,
                "attempt_universe_root": root / "attempt-universe",
            })
            arguments.update(self._schedule_arguments(root))
            first_result = []
            worker = threading.Thread(
                target=lambda: first_result.append(
                    run_stage10_live_collection(**arguments)
                )
            )
            worker.start()
            self.assertTrue(entered.wait(3))
            attempt_latest = (root / "attempt" / "latest.json").read_bytes()
            universe_latest = (
                root / "attempt-universe" / "latest.json"
            ).read_bytes()
            try:
                second = run_stage10_live_collection(**arguments)

                self.assertEqual(second.preflight.status, "dry_attempt_contended")
                self.assertEqual(
                    (root / "attempt" / "latest.json").read_bytes(),
                    attempt_latest,
                )
                self.assertEqual(
                    (root / "attempt-universe" / "latest.json").read_bytes(),
                    universe_latest,
                )
            finally:
                release.set()
                worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(first_result[0].preflight.status, "ready")

    def test_replaced_per_attempt_lock_never_allows_dry_checkpoint_overwrite(self):
        import radar.stage10_live_collection as module

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            calls = []
            etf_input = root / "inputs" / "etf"
            arguments = self._live_arguments(
                root,
                calls,
                etf=lambda _stage9: {
                    "status": "failed",
                    "reason": "registration_failed",
                    "inputPath": str(etf_input),
                },
            )
            arguments.update(self._schedule_arguments(root))
            arguments.update({
                "attempt_universe_root": root / "attempt-universe",
            })
            initial = module.run_stage10_live_collection(**arguments)
            self.assertEqual(initial.etf_observation.status, "failed")
            initial_checkpoint = json.loads(
                (root / "attempt" / "latest.json").read_text()
            )["contentSha256"]
            initial_universe = (
                root / "attempt-universe" / "latest.json"
            ).read_bytes()

            entered = threading.Event()
            release = threading.Event()
            winner_errors = []

            def blocking_register(module_name, _input):
                self.assertEqual(module_name, "etfObservation")
                entered.set()
                self.assertTrue(release.wait(3))
                return {
                    "status": "available",
                    "contentSha256": "b" * 64,
                    "ledgerRelativePath": "ledger/etf.json",
                }

            arguments["shadow_registrar"] = blocking_register

            def run_winner():
                try:
                    module.run_stage10_live_collection(**arguments)
                except Exception as error:
                    winner_errors.append(error)

            worker = threading.Thread(target=run_winner)
            worker.start()
            self.assertTrue(entered.wait(3))
            per_attempt = module._per_attempt_lock(root / "attempt")
            lock_path = per_attempt._path
            os.chflags(lock_path.parent, 0, follow_symlinks=False)
            lock_path.parent.chmod(0o700)
            os.chflags(lock_path, 0, follow_symlinks=False)
            lock_path.unlink()

            contender = module.run_stage10_live_collection(**arguments)
            checkpoint_during_contention = json.loads(
                (root / "attempt" / "latest.json").read_text()
            )["contentSha256"]
            universe_during_contention = (
                root / "attempt-universe" / "latest.json"
            ).read_bytes()
            release.set()
            worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(contender.preflight.status, "dry_attempt_contended")
        self.assertEqual(checkpoint_during_contention, initial_checkpoint)
        self.assertEqual(universe_during_contention, initial_universe)
        self.assertTrue(winner_errors)


if __name__ == "__main__":
    unittest.main()
