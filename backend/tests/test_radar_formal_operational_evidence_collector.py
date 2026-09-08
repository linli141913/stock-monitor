import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError


UTC = timezone.utc


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def frozen_policy(**changes):
    value = {
        "contractVersion": "operational-performance-policy-v1",
        "policyId": "performance-policy-2026-09-05",
        "evidenceScope": "live",
        "scheduleIntervalMs": 1000,
        "minSampleCount": 2,
        "minCoverage": 0.5,
        "maxP95DurationMs": 500,
        "maxFailedRate": 0.5,
        "maxLockContentionCount": 1,
    }
    value.update(changes)
    value["policySha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return value


def frozen_security_policy(**changes):
    value = {
        "contractVersion": "radar-formal-security-policy-v1",
        "policyId": "security-policy-1",
        "evidenceScope": "static_only",
        "requiredAssets": [
            {"role": "radar_config_source", "path": "backend/radar/config.py", "version": "v1", "contentSha256": "1" * 64},
            {"role": "formal_execution_guard_source", "path": "backend/radar/formal_execution_guard.py", "version": "v1", "contentSha256": "2" * 64},
        ],
    }
    value.update(changes)
    value["policySha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return value


class OperationalEvidenceCollectorReviewTests(unittest.TestCase):
    def _write_universe_store(self, root, manifest, *, name="universe-store"):
        manifest = json.loads(json.dumps(manifest))
        slots = []
        for index, attempt in enumerate(manifest["expectedAttempts"]):
            attempt.setdefault("slotId", f"slot-{index + 1}")
            scheduled = datetime.fromisoformat(attempt["scheduleSlot"])
            slots.append({
                "slotId": attempt["slotId"],
                "tradeDate": attempt["tradeDate"],
                "scheduleSlot": attempt["scheduleSlot"],
                "effectiveFrom": (scheduled - timedelta(minutes=1)).isoformat(),
                "effectiveUntil": (scheduled + timedelta(minutes=1)).isoformat(),
                "moduleScope": ["stage9", "trendRotation", "leaderObservation", "etfObservation"],
            })
        calendar_raw = ("<html><strong>2026年休市安排</strong><table>"
                        "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
                        "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
                        "</table></html>").encode()
        schedule = {
            "contractVersion": "radar-stage10-schedule-manifest-v1",
            "policyVersion": "radar-stage10-schedule-policy-v1",
            "scheduleId": "stage10-live-cn",
            "evidenceScope": "live",
            "officialMarket": "cn",
            "officialTradingDates": sorted({
                attempt["tradeDate"] for attempt in manifest["expectedAttempts"]
            }),
            "sourceEvidence": [{
                "year": 2026,
                "sourceDocumentSha256": hashlib.sha256(calendar_raw).hexdigest(),
                "observedThrough": "2026-09-04",
            }],
            "slots": slots,
        }
        schedule_store = root / f"{name}-schedule"
        (schedule_store / "manifests").mkdir(parents=True)
        schedule_raw = canonical(schedule)
        schedule_sha = hashlib.sha256(schedule_raw).hexdigest()
        (schedule_store / "manifests" / f"{schedule_sha}.json").write_bytes(
            schedule_raw,
        )
        (schedule_store / "latest.json").write_bytes(canonical({
            "contractId": "radar-stage10-schedule-manifest-ref-v1",
            "contentSha256": schedule_sha,
            "relativePath": f"manifests/{schedule_sha}.json",
        }))
        manifest["scheduleManifestSha256"] = schedule_sha
        store = root / name
        (store / "reports").mkdir(parents=True)
        (store / "manifests").mkdir()
        report_raw = canonical(manifest)
        report_sha = hashlib.sha256(report_raw).hexdigest()
        (store / "reports" / f"{report_sha}.json").write_bytes(report_raw)
        index_raw = canonical({
            "contractId": "radar-formal-operational-attempt-universe-manifest-v1",
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "contentSha256": report_sha,
            "reportRelativePath": f"reports/{report_sha}.json",
        })
        index_sha = hashlib.sha256(index_raw).hexdigest()
        (store / "manifests" / f"{index_sha}.json").write_bytes(index_raw)
        (store / "latest.json").write_bytes(canonical({
            "contractId": "radar-formal-operational-attempt-universe-ref-v1",
            "manifestSha256": index_sha,
            "manifestRelativePath": f"manifests/{index_sha}.json",
        }))
        universe_binding = {
            "relativePath": str(store.relative_to(Path("/private/tmp"))),
            "expectedContentSha256": report_sha,
        }
        schedule_binding = {
            "relativePath": str(schedule_store.relative_to(Path("/private/tmp"))),
            "expectedContentSha256": schedule_sha,
        }
        return universe_binding, schedule_binding

    def _complete_real_store_and_attempt(
        self,
        root,
        *,
        run_id="run-1",
        observed_at=None,
        attempt_name="attempt",
        attempt_id="attempt-1",
    ):
        if observed_at is not None:
            import tests.test_radar_formal_shadow_observation_collector as fixture

            with patch.multiple(
                fixture,
                AS_OF=observed_at - timedelta(seconds=3),
                FETCHED_AT=observed_at - timedelta(seconds=1),
                OBSERVED_AT=observed_at,
            ):
                return self._complete_real_store_and_attempt(
                    root,
                    run_id=run_id,
                    attempt_name=attempt_name,
                    attempt_id=attempt_id,
                )
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider, load_official_sse_calendar_evidence
        from radar.formal_shadow_ledger import build_shadow_ledger
        from radar.formal_shadow_ledger_store import save_formal_shadow_ledger
        from radar.formal_shadow_observation_collector import adapt_formal_shadow_observation
        from radar.stage10_live_collection import (
            Stage10LiveCollectionResult, Stage10ModuleResult, Stage10Preflight,
            Stage10Stage9Result, _snapshot_input, _write_checkpoint,
        )
        from tests.test_radar_formal_shadow_observation_collector import (
            AS_OF, OBSERVED_AT, collection_policy, etf_admission_artifact,
            etf_artifact, json_bytes, leader_artifact, receipt, sector_snapshot,
            trend_artifact,
        )

        calendar_raw = ("<html><strong>2026年休市安排</strong><table>"
                        "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
                        "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
                        "</table></html>").encode()
        calendar_envelope = json.dumps({
            "contractVersion": "radar-formal-shadow-calendar-input-v1", "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
            "year": 2026, "fetchedAt": "2026-09-04T11:09:08+08:00",
            "observedThrough": "2026-09-04",
            "sourceDocumentSha256": hashlib.sha256(calendar_raw).hexdigest(),
        }, ensure_ascii=False).encode()
        evidence = load_official_sse_calendar_evidence(calendar_envelope, calendar_raw)
        policy_raw = json_bytes(collection_policy())
        modules = {}
        observations = []
        input_parent = root / "inputs"
        for module in ("trendRotation", "leaderObservation", "etfObservation"):
            directory = input_parent / module
            directory.mkdir(parents=True, mode=0o700)
            source_value = trend_artifact(run_id) if module == "trendRotation" else leader_artifact(run_id) if module == "leaderObservation" else etf_artifact(run_id)
            source_raw = json_bytes(source_value)
            receipt_raw = json_bytes(receipt(module, source_raw, runId=run_id))
            files = {
                "radar-formal-shadow-run-receipt-v1.json": receipt_raw,
                "radar-formal-shadow-source-artifact-v1.json": source_raw,
                "radar-formal-shadow-collection-policy-v1.json": policy_raw,
                "radar-formal-shadow-calendar-input-v1.json": calendar_envelope,
                "sse-official-calendar.html": calendar_raw,
            }
            support = admission = None
            if module == "trendRotation":
                support = json_bytes(sector_snapshot(run_id))
                files["radar-formal-shadow-trend-supporting-v1.json"] = support
            if module == "etfObservation":
                admission = json_bytes(etf_admission_artifact(run_id))
                files["radar-etf-formal-admission-v2.json"] = admission
            for name, raw in files.items():
                path = directory / name
                path.write_bytes(raw)
                path.chmod(0o600)
            observation = adapt_formal_shadow_observation(
                receipt_raw, source_raw, evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_raw,
                supporting_artifact_json_bytes=support,
                formal_admission_json_bytes=admission,
            )
            observations.append(observation)
            modules[module] = (directory, *_snapshot_input(directory, module))
        provider = OfficialSseCalendarProvider((evidence,))
        store = root / "ledger-store"
        stored_by_module = {}
        for index, module in enumerate((
            "trendRotation",
            "leaderObservation",
            "etfObservation",
        )):
            ledger = build_shadow_ledger(
                observations[:index + 1],
                calendar_provider=provider,
                calendar_evidence=(evidence,),
            )
            stored_by_module[module] = save_formal_shadow_ledger(
                ledger,
                store,
                calendar_documents_by_sha256={
                    evidence.source_document_sha256: calendar_raw,
                },
            )
        stored = stored_by_module["etfObservation"]

        def state(module):
            directory, package_sha, contract, input_run_id = modules[module]
            module_ledger = stored_by_module[module]
            return Stage10ModuleResult(
                status="available", ledgerSha256=module_ledger.content_sha256,
                ledgerRelativePath=module_ledger.relative_path,
                inputRelativePath=str(directory.relative_to(Path("/private/tmp"))),
                inputSha256=package_sha, inputContractId=contract,
                inputRunId=input_run_id,
            )

        trend_dir, trend_sha, trend_contract, _trend_run = modules["trendRotation"]
        leader_dir, leader_sha, leader_contract, _leader_run = modules["leaderObservation"]
        attempt = root / attempt_name
        attempt.mkdir(mode=0o700)
        checkpoint = Stage10LiveCollectionResult(
            attemptId=attempt_id, startedAt=OBSERVED_AT, finishedAt=OBSERVED_AT,
            tradeDate=OBSERVED_AT.astimezone().date(), preflight=Stage10Preflight(status="ready"),
            stage9=Stage10Stage9Result(
                status="available", role="development", sampleId="sample-1", radarRunId=run_id,
                etfFormalAdmissionPath="baseline/admission.json",
                trendInputRelativePath=str(trend_dir.relative_to(Path("/private/tmp"))),
                leaderInputRelativePath=str(leader_dir.relative_to(Path("/private/tmp"))),
                trendInputSha256=trend_sha, leaderInputSha256=leader_sha,
                trendInputContractId=trend_contract, leaderInputContractId=leader_contract,
            ),
            trendRotation=state("trendRotation"), leaderObservation=state("leaderObservation"),
            etfObservation=state("etfObservation"),
            effects=("stage9", "trendRotation", "leaderObservation", "etfObservation"), formalEnabled=False,
        )
        from radar.formal_shadow_ledger_store import _open_root_directory
        attempt_fd = _open_root_directory(attempt, create_missing=False)
        try:
            _write_checkpoint(attempt, checkpoint, root_fd=attempt_fd)
        finally:
            os.close(attempt_fd)
        pointer = json.loads((attempt / "latest.json").read_text())
        return stored, pointer["contentSha256"], OBSERVED_AT

    def test_two_trading_days_accept_historical_ledger_versions_but_require_observations_in_latest(self):
        from radar.formal_operational_evidence_collector import (
            collect_and_build_operational_checks,
            collect_operational_evidence,
        )
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider
        from radar.formal_shadow_ledger import build_shadow_ledger
        from radar.formal_shadow_ledger_store import load_latest_formal_shadow_ledger, save_formal_shadow_ledger
        from tests.test_radar_formal_shadow_observation_collector import OBSERVED_AT

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            day1_root, day2_root = root / "day1", root / "day2"
            day1_root.mkdir()
            day2_root.mkdir()
            day1_time = OBSERVED_AT - timedelta(days=1)
            stored1, checkpoint1, _ = self._complete_real_store_and_attempt(
                day1_root, run_id="run-day1", observed_at=day1_time,
                attempt_id="attempt-day1",
            )
            stored2, checkpoint2, checked_at = self._complete_real_store_and_attempt(
                day2_root, run_id="run-day2", observed_at=OBSERVED_AT,
                attempt_id="attempt-day2",
            )
            self.assertNotEqual(stored1.content_sha256, stored2.content_sha256)
            ledger1 = load_latest_formal_shadow_ledger(day1_root / "ledger-store", now=checked_at).ledger
            ledger2 = load_latest_formal_shadow_ledger(day2_root / "ledger-store", now=checked_at).ledger
            self.assertIsNotNone(ledger1)
            self.assertIsNotNone(ledger2)
            provider = OfficialSseCalendarProvider(ledger2.calendar_evidence)
            combined = build_shadow_ledger(
                (*ledger1.observations, *ledger2.observations),
                calendar_provider=provider,
                calendar_evidence=ledger2.calendar_evidence,
            )
            current_store = root / "current-ledger-store"
            calendar_sha = ledger2.calendar_evidence[0].source_document_sha256
            calendar_raw = (day2_root / "ledger-store" / "calendar" / f"{calendar_sha}.html").read_bytes()
            current = save_formal_shadow_ledger(
                combined,
                current_store,
                calendar_documents_by_sha256={calendar_sha: calendar_raw},
            )
            historical_report_bytes = {
                path.stem: path.read_bytes()
                for attempt_root in (day1_root, day2_root)
                for path in (attempt_root / "ledger-store" / "reports").iterdir()
            }
            for sha256, payload in historical_report_bytes.items():
                (current_store / "reports" / f"{sha256}.json").write_bytes(payload)
            attempts = [
                (day1_root, "attempt-day1", "run-day1", day1_time.date(), checkpoint1, stored1),
                (day2_root, "attempt-day2", "run-day2", checked_at.date(), checkpoint2, stored2),
            ]
            bindings = [{
                "attemptRootRelativePath": str((attempt_root / "attempt").relative_to(Path("/private/tmp"))),
                "checkpointSha256": checkpoint,
            } for attempt_root, _attempt_id, _run, _date, checkpoint, _stored in attempts]
            manifest = {
                "contractVersion": "radar-formal-operational-attempt-universe-v1",
                "universeId": "two-day-universe",
                "evidenceScope": "live",
                "expectedAttempts": [{
                    "attemptId": attempt_id,
                    "tradeDate": trade_date.isoformat(),
                    "scheduleSlot": trade_date.isoformat() + "T11:09:08+08:00",
                    "attemptRootRelativePath": binding["attemptRootRelativePath"],
                    "checkpointSha256": checkpoint,
                    "expectedRunId": run_id,
                    "expectedLockState": "acquired",
                    "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"},
                } for binding, (_attempt_root, attempt_id, run_id, trade_date, checkpoint, _stored) in zip(bindings, attempts)],
                "globalLockEvidence": {
                    "contractVersion": "radar-stage10-global-lock-evidence-v1",
                    "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                    "lockName": "collection.lock",
                    "acquisitionMode": "exclusive_nonblocking",
                    "identityCheckMode": "before_each_external_runner",
                    "coveredAttemptIds": ["attempt-day1", "attempt-day2"],
                },
            }
            universe_binding, schedule_binding = self._write_universe_store(root, manifest)
            policy_raw = canonical(frozen_policy(minSampleCount=2))
            (root / "performance-policy.json").write_bytes(policy_raw)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-two-days", "checkedAt": checked_at.isoformat(),
                "ledgerStore": {
                    "relativePath": str(current_store.relative_to(Path("/private/tmp"))),
                    "expectedContentSha256": current.content_sha256,
                },
                "attemptUniverseStore": universe_binding,
                "scheduleManifestStore": schedule_binding,
                "attempts": bindings,
                "performancePolicy": {
                    "path": "performance-policy.json",
                    "expectedContentSha256": hashlib.sha256(policy_raw).hexdigest(),
                },
            }
            inputs = collect_operational_evidence(raw, snapshot_root=Path("/private/tmp"), asset_root=root)
            collector_input = root / "collector-input.json"
            collector_input.write_bytes(canonical(raw))
            report = collect_and_build_operational_checks({
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "relativePath": str(collector_input.relative_to(Path("/private/tmp"))),
                "contentSha256": hashlib.sha256(collector_input.read_bytes()).hexdigest(),
            }, snapshot_root=Path("/private/tmp"), asset_root=root)
            performance = next(item for item in report.gates if item.gate == "performance")
            self.assertEqual(inputs.performance.sample_count, 2)
            self.assertEqual(inputs.performance.reentry_count, 0)
            self.assertEqual(performance.state, "ready")

            (current_store / "reports" / f"{stored1.content_sha256}.json").unlink()
            with self.assertRaisesRegex(
                ValueError,
                "operational_evidence_ledger_checkpoint_mismatch",
            ):
                collect_operational_evidence(
                    raw,
                    snapshot_root=Path("/private/tmp"),
                    asset_root=root,
                )
            (current_store / "reports" / f"{stored1.content_sha256}.json").write_bytes(
                historical_report_bytes[stored1.content_sha256],
            )

            missing_history = dict(raw)
            for sha256, payload in historical_report_bytes.items():
                target = day2_root / "ledger-store" / "reports" / f"{sha256}.json"
                if not target.exists():
                    target.write_bytes(payload)
            missing_history["ledgerStore"] = {
                "relativePath": str((day2_root / "ledger-store").relative_to(Path("/private/tmp"))),
                "expectedContentSha256": stored2.content_sha256,
            }
            with self.assertRaisesRegex(ValueError, "operational_evidence_ledger_receipt_mismatch"):
                collect_operational_evidence(missing_history, snapshot_root=Path("/private/tmp"), asset_root=root)

            changed_day1 = ledger1.observations[0].model_copy(update={
                "duration_ms": ledger1.observations[0].duration_ms + 1,
            })
            changed_observations = (
                changed_day1,
                *ledger1.observations[1:],
                *ledger2.observations,
            )
            changed = build_shadow_ledger(
                changed_observations,
                calendar_provider=provider,
                calendar_evidence=ledger2.calendar_evidence,
            )
            changed_store = root / "changed-ledger-store"
            changed_ref = save_formal_shadow_ledger(
                changed,
                changed_store,
                calendar_documents_by_sha256={calendar_sha: calendar_raw},
            )
            for sha256, payload in historical_report_bytes.items():
                (changed_store / "reports" / f"{sha256}.json").write_bytes(payload)
            changed_history = dict(raw)
            changed_history["ledgerStore"] = {
                "relativePath": str(changed_store.relative_to(Path("/private/tmp"))),
                "expectedContentSha256": changed_ref.content_sha256,
            }
            with self.assertRaisesRegex(ValueError, "operational_evidence_ledger_receipt_mismatch"):
                collect_operational_evidence(changed_history, snapshot_root=Path("/private/tmp"), asset_root=root)

    def test_collector_replays_store_calendar_checkpoint_and_exact_receipt_universe(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1", "checkedAt": checked_at.isoformat(),
                "ledgerStore": {"relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))), "expectedContentSha256": stored.content_sha256},
                "attempts": [{"attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))), "checkpointSha256": checkpoint_sha}],
            }
            result = collect_operational_evidence(raw, snapshot_root=Path("/private/tmp"), asset_root=root)
        self.assertEqual(result.performance.sample_count, 1)
        self.assertEqual(result.performance.max_duration_ms, 0)
        self.assertEqual(result.performance.coverage, 1.0)
        self.assertFalse(result.performance.reentry_proof_complete)
        self.assertIsNone(result.performance.reentry_count)
        self.assertFalse(result.security.scan_complete)
        self.assertEqual(result.rollback.evidence_scope, "static_only")

    def test_checkpoint_ledger_reference_must_resolve_to_verified_historical_report(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence
        from radar.formal_shadow_ledger_store import _open_root_directory
        from radar.stage10_live_collection import _load_checkpoint, _write_checkpoint

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, _checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                checkpoint = _load_checkpoint(root / "attempt", root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            forged_sha = "f" * 64
            forged_reference = {
                "ledger_sha256": forged_sha,
                "ledger_relative_path": f"reports/{forged_sha}.json",
            }
            forged = checkpoint.model_copy(update={
                "trend_rotation": checkpoint.trend_rotation.model_copy(update=forged_reference),
                "leader_observation": checkpoint.leader_observation.model_copy(update=forged_reference),
                "etf_observation": checkpoint.etf_observation.model_copy(update=forged_reference),
            })
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                _write_checkpoint(root / "attempt", forged, root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            pointer = json.loads((root / "attempt" / "latest.json").read_text())
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1",
                "checkedAt": checked_at.isoformat(),
                "ledgerStore": {
                    "relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))),
                    "expectedContentSha256": stored.content_sha256,
                },
                "attempts": [{
                    "attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))),
                    "checkpointSha256": pointer["contentSha256"],
                }],
            }
            with self.assertRaisesRegex(
                ValueError,
                "operational_evidence_ledger_checkpoint_mismatch",
            ):
                collect_operational_evidence(
                    raw,
                    snapshot_root=Path("/private/tmp"),
                    asset_root=root,
                )

    def test_attempt_binding_reads_immutable_checkpoint_not_newer_latest(self):
        from radar.formal_operational_evidence_collector import (
            collect_operational_evidence,
        )
        from radar.formal_shadow_ledger_store import _open_root_directory
        from radar.stage10_live_collection import (
            Stage10LiveCollectionResult,
            Stage10ModuleResult,
            Stage10Preflight,
            Stage10Stage9Result,
            _write_checkpoint,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, immutable_sha, checked_at = self._complete_real_store_and_attempt(root)
            newer = Stage10LiveCollectionResult(
                attemptId="attempt-newer",
                startedAt=checked_at,
                finishedAt=checked_at,
                tradeDate=checked_at.date(),
                preflight=Stage10Preflight(status="ready"),
                stage9=Stage10Stage9Result(status="not_attempted"),
                trendRotation=Stage10ModuleResult(status="not_attempted"),
                leaderObservation=Stage10ModuleResult(status="not_attempted"),
                etfObservation=Stage10ModuleResult(status="not_attempted"),
                formalEnabled=False,
            )
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                newer_sha = _write_checkpoint(
                    root / "attempt", newer, root_fd=attempt_fd
                )
            finally:
                os.close(attempt_fd)
            self.assertNotEqual(newer_sha, immutable_sha)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1",
                "checkedAt": checked_at.isoformat(),
                "ledgerStore": {
                    "relativePath": str(
                        (root / "ledger-store").relative_to(Path("/private/tmp"))
                    ),
                    "expectedContentSha256": stored.content_sha256,
                },
                "attempts": [{
                    "attemptRootRelativePath": str(
                        (root / "attempt").relative_to(Path("/private/tmp"))
                    ),
                    "checkpointSha256": immutable_sha,
                }],
            }
            result = collect_operational_evidence(
                raw,
                snapshot_root=Path("/private/tmp"),
                asset_root=root,
            )
            raw["attempts"][0]["checkpointSha256"] = newer_sha
            pending_result = collect_operational_evidence(
                raw,
                snapshot_root=Path("/private/tmp"),
                asset_root=root,
            )

        self.assertEqual(result.performance.sample_count, 1)
        self.assertEqual(result.performance.failed_count, 0)
        self.assertEqual(pending_result.performance.sample_count, 1)
        self.assertEqual(pending_result.performance.failed_count, 1)
        self.assertFalse(pending_result.performance.execution_evidence_complete)

    def test_checkpoint_three_module_ledgers_must_form_monotonic_chain(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence
        from radar.formal_shadow_ledger_store import _open_root_directory
        from radar.stage10_live_collection import _load_checkpoint, _write_checkpoint

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, _checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                checkpoint = _load_checkpoint(root / "attempt", root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            forged = checkpoint.model_copy(update={
                "trend_rotation": checkpoint.trend_rotation.model_copy(update={
                    "ledger_sha256": checkpoint.etf_observation.ledger_sha256,
                    "ledger_relative_path": checkpoint.etf_observation.ledger_relative_path,
                }),
            })
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                _write_checkpoint(root / "attempt", forged, root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            pointer = json.loads((root / "attempt" / "latest.json").read_text())
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1",
                "checkedAt": checked_at.isoformat(),
                "ledgerStore": {
                    "relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))),
                    "expectedContentSha256": stored.content_sha256,
                },
                "attempts": [{
                    "attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))),
                    "checkpointSha256": pointer["contentSha256"],
                }],
            }
            with self.assertRaisesRegex(
                ValueError,
                "operational_evidence_ledger_receipt_mismatch",
            ):
                collect_operational_evidence(
                    raw,
                    snapshot_root=Path("/private/tmp"),
                    asset_root=root,
                )

    def test_checkpoint_stage9_run_identity_must_match_every_receipt(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence
        from radar.stage10_live_collection import _load_checkpoint, _write_checkpoint

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, _checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            from radar.formal_shadow_ledger_store import _open_root_directory
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                checkpoint = _load_checkpoint(root / "attempt", root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            forged = checkpoint.model_copy(update={
                "stage9": checkpoint.stage9.model_copy(update={"radar_run_id": "other-run"}),
            })
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                _write_checkpoint(root / "attempt", forged, root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            pointer = json.loads((root / "attempt" / "latest.json").read_text())
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1", "checkedAt": checked_at.isoformat(),
                "ledgerStore": {"relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))), "expectedContentSha256": stored.content_sha256},
                "attempts": [{"attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))), "checkpointSha256": pointer["contentSha256"]}],
            }
            with self.assertRaisesRegex(ValueError, "operational_evidence_receipt_identity_mismatch"):
                collect_operational_evidence(raw, snapshot_root=Path("/private/tmp"), asset_root=root)

    def test_dry_checkpoint_with_zero_receipts_remains_collecting(self):
        from radar.formal_operational_evidence_collector import (
            collect_and_build_operational_checks,
            collect_operational_evidence,
        )
        from radar.stage10_live_collection import _empty_result, _write_checkpoint
        from radar.formal_shadow_ledger_store import _open_root_directory

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, _checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            dry = _empty_result(
                attempt_id="dry-attempt",
                now=checked_at,
                status="dry_not_continuous_session",
            )
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                _write_checkpoint(root / "attempt", dry, root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            checkpoint_sha = json.loads((root / "attempt" / "latest.json").read_text())["contentSha256"]
            policy_raw = canonical(frozen_policy(minSampleCount=1))
            (root / "performance-policy.json").write_bytes(policy_raw)
            universe = {
                "contractVersion": "radar-formal-operational-attempt-universe-v1",
                "universeId": "dry-universe",
                "evidenceScope": "live",
                "expectedAttempts": [{
                    "attemptId": "dry-attempt",
                    "tradeDate": checked_at.astimezone().date().isoformat(),
                    "scheduleSlot": checked_at.isoformat(),
                    "attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))),
                    "checkpointSha256": checkpoint_sha,
                    "expectedRunId": None,
                    "expectedLockState": "not_attempted",
                    "moduleOutcomes": {"stage9": "not_attempted", "trendRotation": "not_attempted", "leaderObservation": "not_attempted", "etfObservation": "not_attempted"},
                }],
                "globalLockEvidence": {
                    "contractVersion": "radar-stage10-global-lock-evidence-v1",
                    "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                    "lockName": "collection.lock",
                    "acquisitionMode": "exclusive_nonblocking",
                    "identityCheckMode": "before_each_external_runner",
                    "coveredAttemptIds": ["dry-attempt"],
                },
            }
            universe_binding, schedule_binding = self._write_universe_store(root, universe)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1",
                "checkedAt": checked_at.isoformat(),
                "ledgerStore": {
                    "relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))),
                    "expectedContentSha256": stored.content_sha256,
                },
                "attempts": [{
                    "attemptRootRelativePath": str((root / "attempt").relative_to(Path("/private/tmp"))),
                    "checkpointSha256": checkpoint_sha,
                }],
                "attemptUniverseStore": universe_binding,
                "scheduleManifestStore": schedule_binding,
                "performancePolicy": {
                    "path": "performance-policy.json",
                    "expectedContentSha256": hashlib.sha256(policy_raw).hexdigest(),
                },
            }
            inputs = collect_operational_evidence(
                raw,
                snapshot_root=Path("/private/tmp"),
                asset_root=root,
            )
            collector_input = root / "collector-input.json"
            collector_input.write_bytes(canonical(raw))
            report = collect_and_build_operational_checks({
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "relativePath": str(collector_input.relative_to(Path("/private/tmp"))),
                "contentSha256": hashlib.sha256(collector_input.read_bytes()).hexdigest(),
            }, snapshot_root=Path("/private/tmp"), asset_root=root)
        self.assertEqual(inputs.performance.sample_count, 1)
        self.assertEqual(inputs.performance.failed_count, 1)
        self.assertTrue(inputs.performance.reentry_proof_complete)
        self.assertEqual(inputs.performance.reentry_count, 0)
        self.assertFalse(inputs.performance.execution_evidence_complete)
        performance = next(item for item in report.gates if item.gate == "performance")
        self.assertEqual(performance.state, "collecting")
        self.assertEqual(performance.reason_codes, ("performance_execution_evidence_incomplete",))

    def test_collector_rejects_attempt_subset_not_matching_universe(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, checkpoint_sha, checked_at = self._complete_real_store_and_attempt(root)
            attempt_relative = str((root / "attempt").relative_to(Path("/private/tmp")))
            universe = {
                "contractVersion": "radar-formal-operational-attempt-universe-v1",
                "universeId": "subset-proof",
                "evidenceScope": "live",
                "expectedAttempts": [
                    {"attemptId": "attempt-1", "tradeDate": "2026-09-04", "scheduleSlot": checked_at.isoformat(), "attemptRootRelativePath": attempt_relative, "checkpointSha256": checkpoint_sha, "expectedRunId": "run-1", "expectedLockState": "acquired", "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"}},
                    {"attemptId": "attempt-missing", "tradeDate": "2026-09-03", "scheduleSlot": "2026-09-03T11:09:08+08:00", "attemptRootRelativePath": "missing-attempt", "checkpointSha256": "2" * 64, "expectedRunId": None, "expectedLockState": "contended", "moduleOutcomes": {"stage9": "not_attempted", "trendRotation": "not_attempted", "leaderObservation": "not_attempted", "etfObservation": "not_attempted"}},
                ],
                "globalLockEvidence": {
                    "contractVersion": "radar-stage10-global-lock-evidence-v1",
                    "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                    "lockName": "collection.lock",
                    "acquisitionMode": "exclusive_nonblocking",
                    "identityCheckMode": "before_each_external_runner",
                    "coveredAttemptIds": ["attempt-1", "attempt-missing"],
                },
            }
            universe_binding, schedule_binding = self._write_universe_store(root, universe)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1", "checkedAt": checked_at.isoformat(),
                "ledgerStore": {"relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))), "expectedContentSha256": stored.content_sha256},
                "attemptUniverseStore": universe_binding,
                "scheduleManifestStore": schedule_binding,
                "attempts": [{"attemptRootRelativePath": attempt_relative, "checkpointSha256": checkpoint_sha}],
            }
            with self.assertRaisesRegex(ValueError, "operational_evidence_attempt_universe_mismatch"):
                collect_operational_evidence(raw, snapshot_root=Path("/private/tmp"), asset_root=root)

    def test_failed_checkpoint_is_preserved_as_unsuccessful_in_exact_universe(self):
        from radar.formal_operational_evidence_collector import collect_operational_evidence
        from radar.formal_shadow_ledger_store import _open_root_directory
        from radar.stage10_live_collection import Stage10ModuleResult, _load_checkpoint, _write_checkpoint

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            stored, _checkpoint, checked_at = self._complete_real_store_and_attempt(root)
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                checkpoint = _load_checkpoint(root / "attempt", root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            failed = checkpoint.model_copy(update={
                "etf_observation": Stage10ModuleResult(status="failed", reason="stage10_etf_failed"),
                "effects": ("stage9", "trendRotation", "leaderObservation"),
            })
            attempt_fd = _open_root_directory(root / "attempt", create_missing=False)
            try:
                _write_checkpoint(root / "attempt", failed, root_fd=attempt_fd)
            finally:
                os.close(attempt_fd)
            checkpoint_sha = json.loads((root / "attempt" / "latest.json").read_text())["contentSha256"]
            attempt_relative = str((root / "attempt").relative_to(Path("/private/tmp")))
            universe_binding, schedule_binding = self._write_universe_store(root, {
                "contractVersion": "radar-formal-operational-attempt-universe-v1",
                "universeId": "failed-universe", "evidenceScope": "live",
                "expectedAttempts": [{
                    "attemptId": "attempt-1", "tradeDate": checked_at.date().isoformat(),
                    "scheduleSlot": checked_at.isoformat(), "attemptRootRelativePath": attempt_relative,
                    "checkpointSha256": checkpoint_sha, "expectedRunId": "run-1",
                    "expectedLockState": "acquired",
                    "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "failed"},
                }],
                "globalLockEvidence": {
                    "contractVersion": "radar-stage10-global-lock-evidence-v1",
                    "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                    "lockName": "collection.lock", "acquisitionMode": "exclusive_nonblocking",
                    "identityCheckMode": "before_each_external_runner", "coveredAttemptIds": ["attempt-1"],
                },
            })
            policy_raw = canonical(frozen_policy(minSampleCount=1))
            (root / "performance-policy.json").write_bytes(policy_raw)
            raw = {
                "contractVersion": "radar-formal-operational-evidence-input-v1",
                "subjectId": "subject-1", "checkedAt": checked_at.isoformat(),
                "ledgerStore": {"relativePath": str((root / "ledger-store").relative_to(Path("/private/tmp"))), "expectedContentSha256": stored.content_sha256},
                "attemptUniverseStore": universe_binding,
                "scheduleManifestStore": schedule_binding,
                "attempts": [{"attemptRootRelativePath": attempt_relative, "checkpointSha256": checkpoint_sha}],
                "performancePolicy": {"path": "performance-policy.json", "expectedContentSha256": hashlib.sha256(policy_raw).hexdigest()},
            }
            inputs = collect_operational_evidence(raw, snapshot_root=Path("/private/tmp"), asset_root=root)
        self.assertEqual(inputs.performance.sample_count, 1)
        self.assertEqual(inputs.performance.failed_count, 1)
        self.assertFalse(inputs.performance.execution_evidence_complete)

    def test_input_rejects_legacy_bare_ledger_receipts_and_self_reported_reentry(self):
        from radar.formal_operational_evidence_collector import OperationalEvidenceInput

        legacy = {
            "contractVersion": "radar-formal-operational-evidence-input-v1",
            "subjectId": "subject-1",
            "checkedAt": "2026-09-04T08:00:00+00:00",
            "ledger": {"path": "ledger.json", "contentSha256": "0" * 64},
            "receipts": [],
            "executionContext": {"scheduleIntervalMs": 1, "reentryProofComplete": True, "evidenceScope": "live"},
            "security": {"scanComplete": True, "findings": [], "managementInterfacesDefaultOff": True, "aiAuthorityIsolated": True},
            "securityScanFiles": [], "rollback": {"assets": []}, "runbook": {"assets": []},
        }
        with self.assertRaises(ValidationError):
            OperationalEvidenceInput.model_validate(legacy)

    def test_zero_attempts_and_unproven_context_cannot_be_ready(self):
        from radar.formal_operational_evidence_collector import (
            FrozenPerformancePolicy,
            derive_performance_input,
        )
        from radar.formal_operational_checks import _performance_result

        policy = FrozenPerformancePolicy.model_validate(frozen_policy(minSampleCount=1))
        performance = derive_performance_input(attempts=(), policy=policy, reentry_proven=False)
        self.assertEqual(performance.sample_count, 0)
        self.assertFalse(performance.reentry_proof_complete)
        self.assertIsNotNone(performance.policy)
        gate = _performance_result(performance)
        self.assertEqual(gate.state, "collecting")
        self.assertEqual(gate.reason_codes, ("performance_reentry_evidence_incomplete",))

    def test_performance_uses_unique_attempts_weighted_coverage_and_unsuccessful_statuses(self):
        from radar.formal_operational_evidence_collector import (
            AttemptPerformanceEvidence,
            FrozenPerformancePolicy,
            derive_performance_input,
        )

        attempts = (
            AttemptPerformanceEvidence(attemptId="a", runId="run-a", tradeDate="2026-09-01", durationMs=0, expectedCount=2, observedCount=2, unsuccessful=True, lockContentionCount=0),
            AttemptPerformanceEvidence(attemptId="b", runId="run-b", tradeDate="2026-09-02", durationMs=100, expectedCount=4, observedCount=1, unsuccessful=True, lockContentionCount=1),
            AttemptPerformanceEvidence(attemptId="c", runId="run-c", tradeDate="2026-09-03", durationMs=200, expectedCount=2, observedCount=2, unsuccessful=False, lockContentionCount=0),
        )
        result = derive_performance_input(
            attempts=attempts,
            policy=FrozenPerformancePolicy.model_validate(frozen_policy(minSampleCount=3)),
            reentry_proven=True,
        )
        self.assertEqual((result.sample_count, result.p50_duration_ms, result.p95_duration_ms, result.max_duration_ms), (3, 100, 200, 200))
        self.assertEqual(result.coverage, 5 / 8)
        self.assertEqual(result.failed_count, 2)
        self.assertEqual(result.lock_contention_count, 1)

    def test_duplicate_attempt_identity_is_rejected(self):
        from radar.formal_operational_evidence_collector import AttemptPerformanceEvidence, derive_performance_input

        attempts = tuple(AttemptPerformanceEvidence(attemptId="same", runId=f"run-{index}", tradeDate=f"2026-09-0{index + 1}", durationMs=0, expectedCount=1, observedCount=1, unsuccessful=False, lockContentionCount=0) for index in range(2))
        with self.assertRaisesRegex(ValueError, "operational_evidence_attempt_duplicate"):
            derive_performance_input(attempts=attempts, policy=None, reentry_proven=False)

    def test_same_runtime_identity_cannot_be_counted_as_two_attempts(self):
        from radar.formal_operational_evidence_collector import AttemptPerformanceEvidence, derive_performance_input

        attempts = tuple(AttemptPerformanceEvidence(attemptId=f"attempt-{index}", runId="same-run", tradeDate="2026-09-04", durationMs=0, expectedCount=1, observedCount=1, unsuccessful=False, lockContentionCount=0) for index in range(2))
        with self.assertRaisesRegex(ValueError, "operational_evidence_runtime_duplicate"):
            derive_performance_input(attempts=attempts, policy=None, reentry_proven=False)

    def test_attempt_universe_is_content_addressed_and_exact(self):
        from radar.formal_operational_evidence_collector import (
            AttemptCheckpointBinding,
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
            validate_attempt_universe_bindings,
        )

        manifest = {
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "universeId": "universe-2026-09-04",
            "evidenceScope": "live",
            "scheduleManifestSha256": "9" * 64,
            "expectedAttempts": [{
                "attemptId": "attempt-1",
                "slotId": "slot-1",
                "tradeDate": "2026-09-04",
                "scheduleSlot": "2026-09-04T11:09:08+08:00",
                "attemptRootRelativePath": "attempt-1",
                "checkpointSha256": "1" * 64,
                "expectedRunId": "run-1",
                "expectedLockState": "acquired",
                "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"},
            }],
            "globalLockEvidence": {
                "contractVersion": "radar-stage10-global-lock-evidence-v1",
                "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                "lockName": "collection.lock",
                "acquisitionMode": "exclusive_nonblocking",
                "identityCheckMode": "before_each_external_runner",
                "coveredAttemptIds": ["attempt-1"],
            },
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            store = root / "universe-store"
            (store / "reports").mkdir(parents=True)
            (store / "manifests").mkdir()
            report_raw = canonical(manifest)
            report_sha = hashlib.sha256(report_raw).hexdigest()
            (store / "reports" / f"{report_sha}.json").write_bytes(report_raw)
            index = {
                "contractId": "radar-formal-operational-attempt-universe-manifest-v1",
                "contractVersion": "radar-formal-operational-attempt-universe-v1",
                "contentSha256": report_sha,
                "reportRelativePath": f"reports/{report_sha}.json",
            }
            index_raw = canonical(index)
            index_sha = hashlib.sha256(index_raw).hexdigest()
            (store / "manifests" / f"{index_sha}.json").write_bytes(index_raw)
            (store / "latest.json").write_bytes(canonical({
                "contractId": "radar-formal-operational-attempt-universe-ref-v1",
                "manifestSha256": index_sha,
                "manifestRelativePath": f"manifests/{index_sha}.json",
            }))
            reference = AttemptUniverseStoreBinding(
                relativePath=str(store.relative_to(Path("/private/tmp"))),
                expectedContentSha256=report_sha,
            )
            universe = load_attempt_universe_store(Path("/private/tmp"), reference)
            matching = (AttemptCheckpointBinding(
                attemptRootRelativePath="attempt-1",
                checkpointSha256="1" * 64,
            ),)
            validate_attempt_universe_bindings(universe, matching)
            with self.assertRaisesRegex(ValueError, "operational_evidence_attempt_universe_mismatch"):
                validate_attempt_universe_bindings(universe, ())
            with self.assertRaisesRegex(ValueError, "operational_evidence_asset_hash_mismatch"):
                load_attempt_universe_store(
                    Path("/private/tmp"),
                    AttemptUniverseStoreBinding(
                        relativePath=str(store.relative_to(Path("/private/tmp"))),
                        expectedContentSha256="0" * 64,
                    ),
                )

    def test_attempt_universe_rejects_latest_version_drift_during_chain_read(self):
        from radar.formal_operational_evidence_collector import (
            AttemptUniverseStoreBinding,
            load_attempt_universe_store,
        )
        from radar.formal_shadow_ledger_store import _read_bytes_at

        manifest = {
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "universeId": "universe-2026-09-04",
            "evidenceScope": "live",
            "expectedAttempts": [{
                "attemptId": "attempt-1",
                "tradeDate": "2026-09-04",
                "scheduleSlot": "2026-09-04T11:09:08+08:00",
                "attemptRootRelativePath": "attempt-1",
                "checkpointSha256": "1" * 64,
                "expectedRunId": "run-1",
                "expectedLockState": "acquired",
                "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"},
            }],
            "globalLockEvidence": {
                "contractVersion": "radar-stage10-global-lock-evidence-v1",
                "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                "lockName": "collection.lock",
                "acquisitionMode": "exclusive_nonblocking",
                "identityCheckMode": "before_each_external_runner",
                "coveredAttemptIds": ["attempt-1"],
            },
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            binding, _schedule_binding = self._write_universe_store(root, manifest)
            store = root / "universe-store"
            latest_path = store / "latest.json"
            original = _read_bytes_at
            swapped = False

            def read_then_drift(parent_fd, name, *, max_bytes):
                nonlocal swapped
                value = original(parent_fd, name, max_bytes=max_bytes)
                if not swapped and name.endswith(".json") and max_bytes == 8 * 1024 * 1024:
                    swapped = True
                    latest_path.write_bytes(canonical({
                        "contractId": "radar-formal-operational-attempt-universe-ref-v1",
                        "manifestSha256": "0" * 64,
                        "manifestRelativePath": f"manifests/{'0' * 64}.json",
                    }))
                return value

            with patch(
                "radar.formal_operational_evidence_collector._read_bytes_at",
                side_effect=read_then_drift,
            ), self.assertRaisesRegex(
                ValueError,
                "operational_evidence_attempt_universe_unverified",
            ):
                load_attempt_universe_store(
                    Path("/private/tmp"),
                    AttemptUniverseStoreBinding.model_validate(binding),
                )
            self.assertTrue(swapped)

    def test_attempt_universe_requires_schedule_slot_and_all_module_outcomes(self):
        from radar.formal_operational_evidence_collector import AttemptUniverse

        base = {
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "universeId": "universe-1",
            "evidenceScope": "live",
            "expectedAttempts": [{
                "attemptId": "attempt-1", "tradeDate": "2026-09-04",
                "attemptRootRelativePath": "attempt-1", "checkpointSha256": "1" * 64,
                "expectedRunId": "run-1", "expectedLockState": "acquired",
            }],
            "globalLockEvidence": {
                "contractVersion": "radar-stage10-global-lock-evidence-v1",
                "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                "lockName": "collection.lock", "acquisitionMode": "exclusive_nonblocking",
                "identityCheckMode": "before_each_external_runner", "coveredAttemptIds": ["attempt-1"],
            },
        }
        with self.assertRaises(ValidationError):
            AttemptUniverse.model_validate(base)

    def test_independent_schedule_manifest_defines_exact_universe_slots(self):
        from radar.formal_operational_evidence_collector import (
            AttemptUniverse,
            ScheduleManifestStoreBinding,
            load_schedule_manifest_store,
            validate_schedule_universe_bindings,
        )
        from radar.formal_shadow_calendar import (
            FormalShadowCalendarEvidence,
            OfficialSseCalendarProvider,
        )

        evidence = FormalShadowCalendarEvidence(
            year=2026,
            fetchedAt="2026-09-04T00:00:00+08:00",
            observedThrough="2026-09-04",
            sourceDocumentSha256="a" * 64,
            closedDays=("2026-01-01",),
        )

        schedule = {
            "contractVersion": "radar-stage10-schedule-manifest-v1",
            "policyVersion": "radar-stage10-schedule-policy-v1",
            "scheduleId": "stage10-live-cn",
            "evidenceScope": "live",
            "officialMarket": "cn",
            "officialTradingDates": ["2026-09-04"],
            "sourceEvidence": [{
                "year": 2026,
                "sourceDocumentSha256": "a" * 64,
                "observedThrough": "2026-09-04",
            }],
            "slots": [{
                "slotId": "cn-20260904-110908",
                "tradeDate": "2026-09-04",
                "scheduleSlot": "2026-09-04T11:09:08+08:00",
                "effectiveFrom": "2026-09-04T11:00:00+08:00",
                "effectiveUntil": "2026-09-04T11:30:00+08:00",
                "moduleScope": ["stage9", "trendRotation", "leaderObservation", "etfObservation"],
            }],
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            store = root / "schedule-store"
            (store / "manifests").mkdir(parents=True)
            raw = canonical(schedule)
            digest = hashlib.sha256(raw).hexdigest()
            (store / "manifests" / f"{digest}.json").write_bytes(raw)
            (store / "latest.json").write_bytes(canonical({
                "contractId": "radar-stage10-schedule-manifest-ref-v1",
                "contentSha256": digest,
                "relativePath": f"manifests/{digest}.json",
            }))
            loaded = load_schedule_manifest_store(
                Path("/private/tmp"),
                ScheduleManifestStoreBinding(
                    relativePath=str(store.relative_to(Path("/private/tmp"))),
                    expectedContentSha256=digest,
                ),
            )
        universe = AttemptUniverse.model_validate({
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "universeId": "universe-1",
            "evidenceScope": "live",
            "scheduleManifestSha256": digest,
            "expectedAttempts": [{
                "attemptId": "attempt-1",
                "slotId": "cn-20260904-110908",
                "tradeDate": "2026-09-04",
                "scheduleSlot": "2026-09-04T11:09:08+08:00",
                "attemptRootRelativePath": "attempt-1",
                "checkpointSha256": "1" * 64,
                "expectedRunId": "run-1",
                "expectedLockState": "acquired",
                "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"},
            }],
            "globalLockEvidence": {
                "contractVersion": "radar-stage10-global-lock-evidence-v1",
                "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                "lockName": "collection.lock",
                "acquisitionMode": "exclusive_nonblocking",
                "identityCheckMode": "before_each_external_runner",
                "coveredAttemptIds": ["attempt-1"],
            },
        })
        # 独立 schedule 是权威全集；universe 只能引用并精确兑现。
        validate_schedule_universe_bindings(
            loaded,
            digest,
            universe,
            OfficialSseCalendarProvider((evidence,)),
        )
        with self.assertRaisesRegex(
            ValueError,
            "operational_evidence_schedule_universe_mismatch",
        ):
            validate_schedule_universe_bindings(
                loaded.model_copy(update={"slots": ()}),
                digest,
                universe,
                OfficialSseCalendarProvider((evidence,)),
            )

    def test_schedule_binding_replays_published_manifest_after_latest_advances(self):
        from radar.formal_operational_evidence_collector import (
            ScheduleManifestStoreBinding,
            Stage10ScheduleManifest,
            load_schedule_manifest_store,
        )
        from radar.stage10_live_collection import publish_stage10_frozen_schedule

        old_schedule = {
            "contractVersion": "radar-stage10-schedule-manifest-v1",
            "policyVersion": "radar-stage10-schedule-policy-v1",
            "scheduleId": "stage10-old",
            "evidenceScope": "live",
            "officialMarket": "cn",
            "officialTradingDates": ["2026-09-04"],
            "sourceEvidence": [{
                "year": 2026,
                "sourceDocumentSha256": "a" * 64,
                "observedThrough": "2026-09-04",
            }],
            "slots": [{
                "slotId": "cn-20260904-am",
                "tradeDate": "2026-09-04",
                "scheduleSlot": "2026-09-04T10:00:00+08:00",
                "effectiveFrom": "2026-09-04T09:30:00+08:00",
                "effectiveUntil": "2026-09-04T11:30:00+08:00",
                "moduleScope": [
                    "stage9", "trendRotation",
                    "leaderObservation", "etfObservation",
                ],
            }],
        }
        new_schedule = {
            **old_schedule,
            "scheduleId": "stage10-new",
            "officialTradingDates": ["2026-09-07"],
            "sourceEvidence": [{
                "year": 2026,
                "sourceDocumentSha256": "b" * 64,
                "observedThrough": "2026-09-07",
            }],
            "slots": [{
                **old_schedule["slots"][0],
                "slotId": "cn-20260907-am",
                "tradeDate": "2026-09-07",
                "scheduleSlot": "2026-09-07T10:00:00+08:00",
                "effectiveFrom": "2026-09-07T09:30:00+08:00",
                "effectiveUntil": "2026-09-07T11:30:00+08:00",
            }],
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            store = root / "schedule-store"
            old_sha = publish_stage10_frozen_schedule(
                store,
                Stage10ScheduleManifest.model_validate(old_schedule),
            )
            publish_stage10_frozen_schedule(
                store,
                Stage10ScheduleManifest.model_validate(new_schedule),
            )

            try:
                replayed = load_schedule_manifest_store(
                    Path("/private/tmp"),
                    ScheduleManifestStoreBinding(
                        relativePath=str(store.relative_to(Path("/private/tmp"))),
                        expectedContentSha256=old_sha,
                    ),
                )
            except ValueError as error:
                self.fail(f"published schedule could not be replayed: {error}")

        self.assertEqual(replayed.schedule_id, "stage10-old")

    def test_universe_allows_multiple_invocations_of_one_authoritative_slot(self):
        from radar.formal_operational_evidence_collector import AttemptUniverse

        first = {
            "attemptId": "attempt-1",
            "slotId": "cn-20260904-110908",
            "tradeDate": "2026-09-04",
            "scheduleSlot": "2026-09-04T11:09:08+08:00",
            "attemptRootRelativePath": "attempt-1",
            "checkpointSha256": "1" * 64,
            "expectedRunId": "run-1",
            "expectedLockState": "acquired",
            "moduleOutcomes": {"stage9": "success", "trendRotation": "success", "leaderObservation": "success", "etfObservation": "success"},
        }
        second = {
            **first,
            "attemptId": "attempt-2",
            "attemptRootRelativePath": "attempt-2",
            "checkpointSha256": "2" * 64,
            "expectedRunId": "run-2",
        }
        universe = AttemptUniverse.model_validate({
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "universeId": "universe-1",
            "evidenceScope": "live",
            "scheduleManifestSha256": "9" * 64,
            "expectedAttempts": [first, second],
            "globalLockEvidence": {
                "contractVersion": "radar-stage10-global-lock-evidence-v1",
                "lockRootName": ".radar-stage10-single-live-collection-lock-root",
                "lockName": "collection.lock",
                "acquisitionMode": "exclusive_nonblocking",
                "identityCheckMode": "before_each_external_runner",
                "coveredAttemptIds": ["attempt-1", "attempt-2"],
            },
        })
        self.assertEqual(
            tuple(item.slot_id for item in universe.expected_attempts),
            ("cn-20260904-110908", "cn-20260904-110908"),
        )

    def test_checkpoint_pointer_uses_unified_strict_json_decoder(self):
        from radar.formal_operational_evidence_collector import (
            _checkpoint_pointer_sha,
        )

        invalid_payloads = (
            b'{"contractId":"a","contractId":"radar-stage10-live-collection-ref-v1","contentSha256":"' + b"1" * 64 + b'","relativePath":"checkpoints/x.json"}',
            b'{"contractId":"radar-stage10-live-collection-ref-v1","contentSha256":"' + b"1" * 64 + b'","relativePath":"checkpoints/x.json","value":NaN}',
            b'\xff{"contractId":"radar-stage10-live-collection-ref-v1"}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(
                dir="/private/tmp",
            ) as directory:
                root = Path(directory)
                attempt = root / "attempt"
                attempt.mkdir()
                (attempt / "latest.json").write_bytes(payload)
                with self.assertRaisesRegex(
                    ValueError,
                    "operational_evidence_checkpoint_unverified",
                ):
                    _checkpoint_pointer_sha(
                        Path("/private/tmp"),
                        str(attempt.relative_to(Path("/private/tmp"))),
                    )

    def test_two_healthy_attempts_on_same_trade_date_are_reentry_failure(self):
        from radar.formal_operational_evidence_collector import (
            AttemptPerformanceEvidence,
            FrozenPerformancePolicy,
            derive_performance_input,
        )
        from radar.formal_operational_checks import _performance_result

        attempts = tuple(
            AttemptPerformanceEvidence(
                attemptId=f"attempt-{index}",
                runId=f"run-{index}",
                tradeDate="2026-09-04",
                durationMs=10,
                expectedCount=1,
                observedCount=1,
                unsuccessful=False,
                lockContentionCount=0,
            )
            for index in range(2)
        )
        performance = derive_performance_input(
            attempts=attempts,
            policy=FrozenPerformancePolicy.model_validate(frozen_policy(minSampleCount=2)),
            reentry_proven=True,
        )
        gate = _performance_result(performance)
        self.assertEqual(performance.reentry_count, 1)
        self.assertEqual(gate.state, "failed")
        self.assertEqual(gate.reason_codes, ("performance_reentry_detected",))

    def test_execution_manifest_accepts_exact_recovery_sequence_but_rejects_duplicates(self):
        from radar.formal_operational_evidence_collector import execution_manifest_complete

        self.assertTrue(execution_manifest_complete(("stage9", "trendRotation", "leaderObservation", "etfObservation_published", "etfObservation_replay")))
        self.assertFalse(execution_manifest_complete(("stage9", "trendRotation", "leaderObservation", "etfObservation", "etfObservation")))

    def test_frozen_performance_policy_rejects_missing_identity_bad_sha_and_coercion(self):
        from radar.formal_operational_evidence_collector import FrozenPerformancePolicy

        missing = frozen_policy()
        missing.pop("policyId")
        with self.assertRaises(ValidationError):
            FrozenPerformancePolicy.model_validate(missing)
        bad = frozen_policy()
        bad["policySha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "operational_evidence_policy_sha_mismatch"):
            FrozenPerformancePolicy.model_validate(bad)
        with self.assertRaises(ValidationError):
            FrozenPerformancePolicy.model_validate(frozen_policy(scheduleIntervalMs=True))

    def test_security_policy_requires_frozen_identity_nonempty_roles_and_preserves_findings(self):
        from radar.formal_operational_evidence_collector import FrozenSecurityPolicy

        value = frozen_security_policy()
        parsed = FrozenSecurityPolicy.model_validate(value)
        self.assertEqual(
            {item.role for item in parsed.required_assets},
            {"radar_config_source", "formal_execution_guard_source"},
        )
        value["requiredAssets"] = []
        value["policySha256"] = hashlib.sha256(canonical({k: v for k, v in value.items() if k != "policySha256"})).hexdigest()
        with self.assertRaises(ValidationError):
            FrozenSecurityPolicy.model_validate(value)

    def test_security_scanner_reads_whitelist_fixes_whitespace_regex_and_redacts_output(self):
        from radar.formal_operational_evidence_collector import (
            AssetBinding,
            FrozenSecurityPolicy,
            scan_security_assets,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            source_root = root / "backend" / "radar"
            source_root.mkdir(parents=True)
            source = source_root / "config.py"
            source.write_bytes(b"class RadarSettings:\n    enabled: bool = False\n\ndef load(values):\n    return RadarSettings(enabled=_read_bool(values, 'RADAR_ENABLED', False))\n\nAPI_KEY = ultra_secret_value\n")
            guard = source_root / "formal_execution_guard.py"
            guard.write_bytes(b"def evaluate_formal_execution_guard():\n    return None\n")
            bindings = tuple(
                AssetBinding(role=role, path=str(path.relative_to(root)), version="v1", contentSha256=hashlib.sha256(path.read_bytes()).hexdigest())
                for role, path in (
                    ("radar_config_source", source),
                    ("formal_execution_guard_source", guard),
                )
            )
            policy_raw = frozen_security_policy(requiredAssets=[item.model_dump(mode="json", by_alias=True) for item in bindings])
            result = scan_security_assets(
                root,
                FrozenSecurityPolicy.model_validate(policy_raw),
                bindings,
                known_findings=({"path": "prior.py", "ruleCode": "known_finding"},),
            )
        self.assertTrue(result.scan_complete)
        self.assertEqual({item.rule_code for item in result.findings}, {"known_finding", "security_sensitive_assignment"})
        serialized = json.dumps(result.model_dump(mode="json", by_alias=True))
        self.assertNotIn("ultra-secret-value", serialized)
        self.assertNotIn(str(root), serialized)
        self.assertEqual(result.policy_id, "security-policy-1")
        self.assertEqual(result.evidence_scope, "static_only")
        self.assertTrue(result.management_interfaces_default_off)
        self.assertIsNone(result.ai_authority_isolated)

    def test_security_roles_cannot_self_prove_management_or_ai_flags(self):
        from radar.formal_operational_evidence_collector import AssetBinding, FrozenSecurityPolicy, scan_security_assets

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            source_root = root / "backend" / "radar"
            source_root.mkdir(parents=True)
            files = {
                "config.py": canonical({"contractVersion": "radar-management-interface-static-proof-v1", "defaultOff": True}),
                "formal_execution_guard.py": canonical({"contractVersion": "radar-ai-authority-static-proof-v1", "stateMutationAllowed": False}),
            }
            bindings = []
            for role, name in zip(("radar_config_source", "formal_execution_guard_source"), files):
                path = source_root / name
                path.write_bytes(files[name])
                bindings.append(AssetBinding(role=role, path=str(path.relative_to(root)), version="v1", contentSha256=hashlib.sha256(files[name]).hexdigest()))
            policy = FrozenSecurityPolicy.model_validate(frozen_security_policy(requiredAssets=[item.model_dump(mode="json", by_alias=True) for item in bindings]))
            result = scan_security_assets(root, policy, tuple(bindings))
        self.assertIsNone(result.management_interfaces_default_off)
        self.assertIsNone(result.ai_authority_isolated)

    def test_policy_file_requires_independent_expected_sha(self):
        from radar.formal_operational_evidence_collector import FrozenPerformancePolicy, PolicyReference, load_frozen_policy

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            (root / "performance.json").write_bytes(canonical(frozen_policy()))
            reference = PolicyReference(path="performance.json", expectedContentSha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "operational_evidence_asset_hash_mismatch"):
                load_frozen_policy(root, reference, FrozenPerformancePolicy)

    def test_raw_path_normalization_tricks_are_rejected(self):
        from radar.formal_operational_evidence_collector import AssetBinding

        for path in ("a//b", "a/./b", "a/"):
            with self.subTest(path=path), self.assertRaisesRegex(ValidationError, "operational_evidence_path_invalid"):
                AssetBinding(role="x", path=path, version="v1", contentSha256="0" * 64)

    def test_security_and_static_assets_reject_protected_paths_and_reused_roles(self):
        from radar.formal_operational_evidence_collector import AssetBinding, OperationalEvidenceInput

        with self.assertRaisesRegex(ValidationError, "operational_evidence_path_forbidden"):
            AssetBinding(role="x", path="backend/data/private.db", version="v1", contentSha256="0" * 64)
        with self.assertRaisesRegex(ValidationError, "operational_evidence_path_forbidden"):
            AssetBinding(role="x", path=".env.production", version="v1", contentSha256="0" * 64)

    def test_static_policy_requires_unique_roles_exact_hashes_and_static_only_scope(self):
        from radar.formal_operational_evidence_collector import FrozenStaticAssetPolicy

        asset = {"gate": "rollback", "role": "rollback_script", "path": "ops/rollback.sh", "version": "v1", "contentSha256": "1" * 64}
        value = {"contractVersion": "radar-formal-static-assets-policy-v1", "policyId": "static-1", "evidenceScope": "static_only", "requiredAssets": [asset, {**asset, "gate": "runbook"}]}
        value["policySha256"] = hashlib.sha256(canonical(value)).hexdigest()
        with self.assertRaisesRegex(ValidationError, "operational_evidence_asset_role_reused"):
            FrozenStaticAssetPolicy.model_validate(value)


if __name__ == "__main__":
    unittest.main()
