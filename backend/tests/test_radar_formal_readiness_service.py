import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


UTC = timezone.utc
CHECKED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
SHA = "c" * 64
MODULES = ("trendRotation", "etfObservation", "leaderObservation")


class FormalReadinessServiceTests(unittest.TestCase):
    def setUp(self):
        self.collector_temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.collector_input_path = Path(self.collector_temp.name) / "input.json"
        self.collector_input_ref = self._write_collector_input(CHECKED_AT)
        # 本测试类多数用例只隔离验证正式门组合规则；真实collector重放由
        # 专项用例和collector测试覆盖，避免每个门测试重复构建整套工件仓。
        self.rebuild_patcher = patch(
            "radar.formal_readiness_service._rebuild_operational_checks",
            side_effect=lambda checks: checks,
        )
        self.rebuild_patcher.start()

    def _write_collector_input(self, checked_at):
        payload = {
            "contractVersion": "radar-formal-operational-evidence-input-v1",
            "subjectId": "stage9-run-1",
            "checkedAt": checked_at.isoformat(),
            "ledgerStore": {
                "relativePath": "unused-ledger-store",
                "expectedContentSha256": "a" * 64,
            },
            "attempts": [],
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.collector_input_path.write_bytes(raw)
        return {
            "contractVersion": "radar-formal-operational-evidence-input-v1",
            "relativePath": str(
                self.collector_input_path.relative_to(Path("/private/tmp"))
            ),
            "contentSha256": hashlib.sha256(raw).hexdigest(),
        }

    def tearDown(self):
        self.rebuild_patcher.stop()
        self.collector_temp.cleanup()

    def shadow_ledger(self, counts=None):
        from radar.formal_shadow_calendar import (
            FormalShadowCalendarEvidence,
            OfficialSseCalendarProvider,
        )
        from radar.formal_shadow_ledger import (
            FormalShadowObservation,
            build_shadow_ledger,
        )

        counts = counts or {
            "trendRotation": 20,
            "etfObservation": 5,
            "leaderObservation": 20,
        }
        observations = []
        for module, count in counts.items():
            cursor = CHECKED_AT
            emitted = 0
            while emitted < count:
                if cursor.astimezone(timezone(timedelta(hours=8))).weekday() >= 5:
                    cursor -= timedelta(days=1)
                    continue
                observed_at = cursor
                observations.append(FormalShadowObservation(
                    module=module,
                    runId=f"{module}-{emitted}",
                    observedAt=observed_at,
                    sourceTime=observed_at - timedelta(minutes=2),
                    fetchedAt=observed_at - timedelta(minutes=1),
                    coverage=1.0,
                    missingCount=0,
                    failedCount=0,
                    staleCount=0,
                    lockState="acquired",
                    durationMs=0,
                    evidenceSha256=SHA,
                    observationStatus="ready",
                ))
                emitted += 1
                cursor -= timedelta(days=1)

        evidence = FormalShadowCalendarEvidence(
            year=2026,
            fetchedAt=CHECKED_AT - timedelta(minutes=55),
            observedThrough=date(2026, 9, 4),
            sourceDocumentSha256="f" * 64,
            closedDays=(date(2026, 1, 1),),
        )
        calendar = OfficialSseCalendarProvider((evidence,))

        return build_shadow_ledger(
            observations,
            calendar_provider=calendar,
            calendar_evidence=(evidence,),
        )

    def inputs(self, **changes):
        from radar.formal_readiness_contracts import (
            FormalEvidenceRef,
            RadarFormalFreshnessPolicy,
        )
        from radar.formal_operational_checks import (
            OperationalEvidenceRef,
            OperationalGateResult,
            RadarFormalOperationalChecks,
            canonical_operational_checks_sha256,
        )
        from radar.formal_readiness_service import FormalReadinessInputs

        stage9_evidence = FormalEvidenceRef(
            evidenceType="stage9_quality", contractVersion="radar-replay-quality-v2",
            contentSha256=SHA, subjectId="stage9-run-1", sourceTime=CHECKED_AT - timedelta(minutes=3),
            fetchedAt=CHECKED_AT - timedelta(minutes=2), generatedAt=CHECKED_AT - timedelta(minutes=1),
        )
        operational = RadarFormalOperationalChecks(
            checkedAt=CHECKED_AT,
            subjectId="stage9-run-1",
            state="ready",
            gates=tuple(
                OperationalGateResult(gate=gate, state="ready")
                for gate in ("performance", "security", "rollback", "runbook")
            ),
            evidence=tuple(
                OperationalEvidenceRef(
                    gate=gate,
                    evidenceType=f"formal_operational_{gate}",
                    contractVersion="radar-formal-operational-checks-input-v1",
                    contentSha256=SHA,
                    subjectId="stage9-run-1",
                    sourceTime=CHECKED_AT - timedelta(minutes=1),
                    fetchedAt=CHECKED_AT - timedelta(minutes=1),
                    generatedAt=CHECKED_AT - timedelta(minutes=1),
                )
                for gate in ("performance", "security", "rollback", "runbook")
            ),
            collectorInputRef=self.collector_input_ref,
        )
        ledger = changes.get("shadow_ledger", self.shadow_ledger())
        from radar.formal_shadow_ledger import FormalShadowLedgerV2
        from radar.formal_shadow_ledger_store import formal_shadow_ledger_content_sha256

        shadow_sha = (
            formal_shadow_ledger_content_sha256(ledger)
            if isinstance(ledger, FormalShadowLedgerV2) else None
        )
        shadow_evidence = ()
        if shadow_sha is not None and ledger.calendar_evidence:
            from radar.formal_readiness_service import formal_shadow_ledger_evidence_ref
            shadow_evidence = (
                formal_shadow_ledger_evidence_ref(ledger, shadow_sha),
            )
        value = FormalReadinessInputs(
            checked_at=CHECKED_AT,
            stage9_replay_run_id="stage9-run-1",
            stage9_quality_sha256=SHA,
            stage9_quality_state="ready",
            stage9_domain_states={module: "ready" for module in MODULES},
            shadow_ledger=ledger,
            shadow_ledger_sha256=shadow_sha,
            rule_version_states={module: "ready" for module in MODULES},
            calibration_states={module: "ready" for module in MODULES},
            data_quality_states={module: "ready" for module in MODULES},
            freshness_policy=RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds=300,
                evidenceMaxAgeSeconds=600,
                operationalChecksMaxAgeSeconds=120,
            ),
            operational_checks=operational,
            operational_checks_sha256=canonical_operational_checks_sha256(operational),
            requested_by_module={module: False for module in MODULES},
            enabled_by_module={module: False for module in MODULES},
            evidence=(stage9_evidence,) + shadow_evidence,
        )
        remaining = {key: item for key, item in changes.items() if key != "shadow_ledger"}
        if "evidence" in remaining:
            supplied = tuple(remaining.pop("evidence"))
            if shadow_evidence and not any(item.evidence_type == "formal_shadow_ledger" for item in supplied):
                supplied += shadow_evidence
            remaining["evidence"] = supplied
        return value.model_copy(update=remaining)

    def test_ready_shadow_gate_requires_exact_content_hash_unique_ref_and_calendar_cutoff(self):
        from radar.formal_readiness_service import build_formal_readiness

        original = self.inputs()
        shadow_ref = next(
            item for item in original.evidence
            if item.evidence_type == "formal_shadow_ledger"
        )
        stage9_ref = next(
            item for item in original.evidence if item.evidence_type == "stage9_quality"
        )
        cases = (
            (original.model_copy(update={"shadow_ledger_sha256": None}), "shadow_ledger_sha256_missing"),
            (original.model_copy(update={"shadow_ledger_sha256": "0" * 64}), "shadow_ledger_sha256_mismatch"),
            (original.model_copy(update={"evidence": (stage9_ref,)}), "shadow_ledger_evidence_missing"),
            (original.model_copy(update={"evidence": (stage9_ref, shadow_ref, shadow_ref)}), "shadow_ledger_evidence_not_unique"),
            (original.model_copy(update={"evidence": (stage9_ref, shadow_ref.model_copy(update={"content_sha256": "0" * 64}))}), "shadow_ledger_evidence_sha256_mismatch"),
            (original.model_copy(update={"evidence": (stage9_ref, shadow_ref.model_copy(update={"subject_id": "fake"}))}), "shadow_ledger_evidence_subject_mismatch"),
        )
        for inputs, reason in cases:
            with self.subTest(reason=reason):
                report = build_formal_readiness(inputs)
                self.assertTrue(all(reason in item.reason_codes for item in report.modules))

        payload = original.shadow_ledger.model_dump(mode="json", by_alias=True)
        payload["calendarObservedThrough"] = "2026-09-03"
        # 模型层本身拒绝被篡改的截止日，不能进入就绪门。
        from pydantic import ValidationError
        from radar.formal_shadow_ledger import FormalShadowLedgerV2
        with self.assertRaises(ValidationError):
            FormalShadowLedgerV2.model_validate(payload)

    def test_calendar_observed_through_gap_blocks_a_new_weekday_but_not_weekend(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=864000,
            evidenceMaxAgeSeconds=864000,
            operationalChecksMaxAgeSeconds=864000,
        )
        weekend = build_formal_readiness(self.inputs(
            checked_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
            freshness_policy=policy,
        ))
        self.assertTrue(all(item.state == "ready_to_enable" for item in weekend.modules))

        monday = build_formal_readiness(self.inputs(
            checked_at=datetime(2026, 9, 7, 8, tzinfo=UTC),
            freshness_policy=policy,
        ))
        self.assertTrue(all(
            "shadow_calendar_observed_through_gap" in item.reason_codes
            for item in monday.modules
        ))

    def test_shadow_ledger_evidence_participates_in_freshness_gate(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness

        original = self.inputs()
        stage9 = next(item for item in original.evidence if item.evidence_type == "stage9_quality")
        shadow = next(item for item in original.evidence if item.evidence_type == "formal_shadow_ledger")
        recent_stage9 = stage9.model_copy(update={
            "source_time": CHECKED_AT - timedelta(seconds=30),
            "fetched_at": CHECKED_AT - timedelta(seconds=20),
            "generated_at": CHECKED_AT - timedelta(seconds=10),
        })
        report = build_formal_readiness(self.inputs(
            freshness_policy=RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds=300,
                evidenceMaxAgeSeconds=60,
                operationalChecksMaxAgeSeconds=120,
            ),
            evidence=(recent_stage9, shadow),
        ))
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertIn("shadow_evidence_expired", report.reason_codes)

    def test_latest_verified_trading_day_must_be_ready_for_every_module(self):
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider
        from radar.formal_readiness_service import build_formal_readiness
        from radar.formal_shadow_ledger import build_shadow_ledger

        baseline = self.shadow_ledger()
        for module, field in (
            ("trendRotation", "failedCount"),
            ("leaderObservation", "missingCount"),
        ):
            with self.subTest(module=module):
                replaced = []
                for item in baseline.observations:
                    payload = item.model_dump(mode="python", by_alias=True)
                    if item.module == module and item.observed_date == date(2026, 9, 4):
                        payload[field] = 1
                        payload["observationStatus"] = "pending"
                    replaced.append(payload)
                provider = OfficialSseCalendarProvider(baseline.calendar_evidence)
                ledger = build_shadow_ledger(
                    replaced,
                    calendar_provider=provider,
                    calendar_evidence=baseline.calendar_evidence,
                )
                report = build_formal_readiness(self.inputs(shadow_ledger=ledger))
                current = next(item for item in report.modules if item.module == module)
                self.assertNotIn(current.state, {"ready_to_enable", "formal_enabled"})
                self.assertIn("shadow_latest_trading_day_not_ready", current.reason_codes)

    def test_shadow_freshness_is_derived_per_module_from_latest_ready_observation(self):
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness
        from radar.formal_shadow_ledger import build_shadow_ledger

        baseline = self.shadow_ledger()
        changed = []
        for item in baseline.observations:
            payload = item.model_dump(mode="python", by_alias=True)
            if item.module == "trendRotation" and item.observed_date == date(2026, 9, 4):
                payload.update({
                    "sourceTime": CHECKED_AT - timedelta(hours=2),
                    "fetchedAt": CHECKED_AT - timedelta(hours=1),
                    "observationStatus": "pending",
                })
            changed.append(payload)
        ledger = build_shadow_ledger(
            changed,
            calendar_provider=OfficialSseCalendarProvider(baseline.calendar_evidence),
            calendar_evidence=baseline.calendar_evidence,
        )
        report = build_formal_readiness(self.inputs(
            shadow_ledger=ledger,
            freshness_policy=RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds=300,
                evidenceMaxAgeSeconds=600,
                operationalChecksMaxAgeSeconds=120,
            ),
        ))
        by_module = {item.module: item for item in report.modules}
        self.assertEqual(by_module["trendRotation"].state, "failed")
        self.assertIn("shadow_evidence_expired", by_module["trendRotation"].reason_codes)
        self.assertEqual(by_module["etfObservation"].state, "ready_to_enable")
        self.assertEqual(by_module["leaderObservation"].state, "ready_to_enable")

    def test_v1_shadow_ledger_cannot_pass_v2_shadow_gate(self):
        from radar.formal_readiness_service import build_formal_readiness
        from radar.formal_shadow_ledger import FormalShadowLedger

        current = self.shadow_ledger()
        legacy = FormalShadowLedger(
            observations=current.observations,
            readyTradingDaysByModule=current.ready_trading_days_by_module,
        )
        report = build_formal_readiness(self.inputs(shadow_ledger=legacy))

        self.assertTrue(all(item.state == "not_ready" for item in report.modules))
        self.assertTrue(all(
            "shadow_ledger_v2_required" in item.reason_codes
            for item in report.modules
        ))

    def test_etf_non_contiguous_five_ready_days_is_not_ready(self):
        from radar.formal_readiness_service import build_formal_readiness
        from radar.formal_shadow_ledger import build_shadow_ledger

        class Calendar:
            def is_trading_day(self, value):
                return date(2026, 8, 29) <= value <= date(2026, 9, 4)

        observations = []
        for module, days in (
            ("trendRotation", tuple(range(20))),
            ("leaderObservation", tuple(range(20))),
            ("etfObservation", (0, 1, 2, 4, 5)),
        ):
            for offset in days:
                observed_at = CHECKED_AT - timedelta(days=offset)
                observations.append({
                    "module": module,
                    "runId": f"{module}-{offset}",
                    "observedAt": observed_at,
                    "sourceTime": observed_at - timedelta(minutes=2),
                    "fetchedAt": observed_at - timedelta(minutes=1),
                    "coverage": 1.0,
                    "missingCount": 0,
                    "failedCount": 0,
                    "staleCount": 0,
                    "lockState": "acquired",
                    "durationMs": 0,
                    "evidenceSha256": SHA,
                })
        ledger = build_shadow_ledger(observations, calendar_provider=Calendar())

        report = build_formal_readiness(self.inputs(shadow_ledger=ledger))
        by_module = {item.module: item for item in report.modules}
        self.assertEqual(by_module["etfObservation"].observed_trading_days, 3)
        self.assertEqual(by_module["etfObservation"].state, "not_ready")
        self.assertIn(
            "shadow_days_insufficient",
            by_module["etfObservation"].reason_codes,
        )

    def test_etf_latest_failed_observation_cannot_pass_on_older_ready_days(self):
        from radar.formal_readiness_service import build_formal_readiness
        from radar.formal_shadow_ledger import build_shadow_ledger

        class Calendar:
            def is_trading_day(self, value):
                return date(2026, 8, 30) <= value <= date(2026, 9, 4)

        observations = []
        for offset in range(1, 6):
            observed_at = CHECKED_AT - timedelta(days=offset)
            observations.append({
                "module": "etfObservation",
                "runId": f"etf-ready-{offset}",
                "observedAt": observed_at,
                "sourceTime": observed_at - timedelta(minutes=2),
                "fetchedAt": observed_at - timedelta(minutes=1),
                "coverage": 1.0,
                "missingCount": 0,
                "failedCount": 0,
                "staleCount": 0,
                "lockState": "acquired",
                "durationMs": 0,
                "evidenceSha256": SHA,
            })
        observations.append({
            **observations[0],
            "runId": "etf-latest-failed",
            "observedAt": CHECKED_AT,
            "sourceTime": CHECKED_AT - timedelta(minutes=2),
            "fetchedAt": CHECKED_AT - timedelta(minutes=1),
            "failedCount": 1,
        })
        ledger = build_shadow_ledger(observations, calendar_provider=Calendar())

        report = build_formal_readiness(self.inputs(shadow_ledger=ledger))
        etf = next(item for item in report.modules if item.module == "etfObservation")
        self.assertEqual(etf.observed_trading_days, 0)
        self.assertEqual(etf.state, "not_ready")
        self.assertIn("shadow_days_insufficient", etf.reason_codes)

    def test_last_observed_date_is_derived_from_v2_ledger_not_input(self):
        from radar.formal_readiness_service import build_formal_readiness

        report = build_formal_readiness(self.inputs(
            last_observed_trading_date_by_module={
                module: date(2099, 1, 1) for module in MODULES
            },
        ))

        self.assertTrue(all(
            module.last_observed_trading_date == date(2026, 9, 4)
            for module in report.modules
        ))

    def test_missing_stage9_identity_blocks_every_module(self):
        from radar.formal_readiness_service import build_formal_readiness

        for state in ("collecting", "not_ready", "ready"):
            with self.subTest(state=state):
                inputs = self.inputs(
                    stage9_quality_state=state,
                    stage9_replay_run_id=None,
                    stage9_quality_sha256=None,
                )
                report = build_formal_readiness(inputs)
                self.assertEqual(
                    report.stage9_quality_state,
                    "not_ready" if state == "ready" else state,
                )
                self.assertTrue(all(module.state == "not_ready" for module in report.modules))
                self.assertTrue(all(
                    "stage9_quality_identity_missing" in module.reason_codes
                    for module in report.modules
                ))

    def test_missing_freshness_policy_closes_data_quality_gate(self):
        from radar.formal_readiness_service import build_formal_readiness

        report = build_formal_readiness(self.inputs(freshness_policy=None))
        for module in report.modules:
            gate = next(item for item in module.gates if item.gate == "data_quality")
            self.assertEqual(gate.state, "not_ready")
            self.assertIn("formal_freshness_policy_missing", gate.reason_codes)
            self.assertNotIn(module.state, {"ready_to_enable", "formal_enabled"})

    def test_stale_regular_evidence_and_operational_checks_fail_closed(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        original = self.inputs()
        stale_evidence = original.evidence[0].model_copy(update={
            "source_time": CHECKED_AT - timedelta(seconds=600, microseconds=1),
        })
        report = build_formal_readiness(self.inputs(
            freshness_policy=policy,
            evidence=(stale_evidence,),
        ))
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertIn("formal_evidence_expired", report.reason_codes)

        stale_operational_time = CHECKED_AT - timedelta(
            seconds=120,
            microseconds=1,
        )
        checks = original.operational_checks.model_copy(update={
            "checked_at": stale_operational_time,
            "evidence": tuple(
                item.model_copy(update={
                    "source_time": stale_operational_time,
                    "fetched_at": stale_operational_time,
                    "generated_at": stale_operational_time,
                })
                for item in original.operational_checks.evidence
            ),
        })
        report = build_formal_readiness(self.inputs(
            freshness_policy=policy,
            operational_checks=checks,
        ))
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertIn("formal_operational_checks_expired", report.reason_codes)

        nested_stale_time = CHECKED_AT - timedelta(
            seconds=120,
            microseconds=1,
        )
        nested_stale_checks = original.operational_checks.model_copy(update={
            "evidence": tuple(
                item.model_copy(update={"source_time": nested_stale_time})
                for item in original.operational_checks.evidence
            ),
        })
        from radar.formal_operational_checks import canonical_operational_checks_sha256
        report = build_formal_readiness(self.inputs(
            freshness_policy=policy,
            operational_checks=nested_stale_checks,
            operational_checks_sha256=canonical_operational_checks_sha256(
                nested_stale_checks,
            ),
        ))
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertIn("formal_operational_checks_expired", report.reason_codes)

    def test_freshness_exact_boundaries_are_valid_and_operational_ref_is_bound(self):
        from radar.formal_operational_checks import canonical_operational_checks_sha256
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        original = self.inputs()
        evidence = original.evidence[0].model_copy(update={
            "source_time": CHECKED_AT - timedelta(seconds=600),
        })
        boundary_operational_time = CHECKED_AT - timedelta(seconds=120)
        from radar.formal_operational_checks import OperationalCollectorInputRef
        checks = original.operational_checks.model_copy(update={
            "checked_at": boundary_operational_time,
            "collector_input_ref": OperationalCollectorInputRef.model_validate(
                self._write_collector_input(boundary_operational_time),
            ),
            "evidence": tuple(
                item.model_copy(update={
                    "source_time": boundary_operational_time,
                    "fetched_at": boundary_operational_time,
                    "generated_at": boundary_operational_time,
                })
                for item in original.operational_checks.evidence
            ),
        })
        report = build_formal_readiness(self.inputs(
            freshness_policy=policy,
            evidence=(evidence,),
            operational_checks=checks,
            operational_checks_sha256=canonical_operational_checks_sha256(checks),
        ))
        self.assertTrue(all(item.state == "ready_to_enable" for item in report.modules))
        operational_refs = tuple(
            item for item in report.evidence
            if item.evidence_type == "formal_operational_checks"
        )
        self.assertEqual(len(operational_refs), 1)
        self.assertEqual(
            operational_refs[0].contract_version,
            "radar-formal-operational-checks-v1",
        )
        self.assertEqual(
            operational_refs[0].content_sha256,
            canonical_operational_checks_sha256(checks),
        )
        self.assertEqual(operational_refs[0].subject_id, checks.subject_id)
        self.assertEqual(operational_refs[0].source_time, checks.checked_at)

    def test_input_cannot_spoof_or_duplicate_operational_summary_evidence(self):
        from radar.formal_readiness_service import build_formal_readiness

        original = self.inputs()
        forged = original.evidence[0].model_copy(update={
            "evidence_type": "formal_operational_checks",
            "contract_version": "radar-formal-operational-checks-v1",
        })
        with self.assertRaisesRegex(
            ValueError,
            "formal_operational_checks_evidence_reserved",
        ):
            build_formal_readiness(self.inputs(evidence=(original.evidence[0], forged)))

    def test_input_enablement_maps_reject_strings_and_numbers(self):
        from pydantic import ValidationError
        from radar.formal_readiness_service import FormalReadinessInputs

        for field, invalid in (
            ("requested_by_module", "false"),
            ("requested_by_module", 1),
            ("enabled_by_module", "true"),
            ("enabled_by_module", 0),
        ):
            with self.subTest(field=field, invalid=invalid):
                payload = self.inputs().model_dump(mode="python")
                payload[field] = dict(payload[field])
                payload[field]["trendRotation"] = invalid
                with self.assertRaises(ValidationError):
                    FormalReadinessInputs.model_validate(payload)

    def test_build_revalidates_model_copy_forged_boolean_map(self):
        from pydantic import ValidationError
        from radar.formal_readiness_service import build_formal_readiness

        inputs = self.inputs()
        forged_requested = dict(inputs.requested_by_module)
        forged_requested["trendRotation"] = "false"
        forged = inputs.model_copy(update={
            "requested_by_module": forged_requested,
        })

        with self.assertRaises(ValidationError):
            build_formal_readiness(forged)

    def test_stage9_requires_exactly_one_matching_quality_evidence(self):
        from radar.formal_readiness_service import build_formal_readiness

        original = self.inputs().evidence[0]
        cases = (
            ((), "stage9_evidence_missing"),
            ((original, original), "stage9_evidence_not_unique"),
            ((original.model_copy(update={"evidence_type": "other"}),), "stage9_evidence_type_mismatch"),
            ((original.model_copy(update={"contract_version": "radar-replay-quality-v1"}),), "stage9_evidence_contract_mismatch"),
            ((original.model_copy(update={"content_sha256": "d" * 64}),), "stage9_evidence_sha256_mismatch"),
            ((original.model_copy(update={"subject_id": "another-run"}),), "stage9_evidence_subject_mismatch"),
        )
        for evidence, reason in cases:
            with self.subTest(reason=reason):
                report = build_formal_readiness(self.inputs(evidence=evidence))
                self.assertTrue(all(item.state == "not_ready" for item in report.modules))
                self.assertTrue(all(reason in item.reason_codes for item in report.modules))

    def test_stage9_ready_claim_without_identity_or_hash_is_not_ready(self):
        from radar.formal_readiness_service import build_formal_readiness

        report = build_formal_readiness(self.inputs(
            stage9_replay_run_id=None,
            stage9_quality_sha256=None,
        ))
        self.assertTrue(all(item.state == "not_ready" for item in report.modules))
        self.assertTrue(all("stage9_quality_identity_missing" in item.reason_codes for item in report.modules))

    def test_overall_ready_with_one_domain_not_ready_closes_only_that_module(self):
        from radar.formal_readiness_service import build_formal_readiness

        domain_states = {module: "ready" for module in MODULES}
        domain_states["leaderObservation"] = "not_ready"
        report = build_formal_readiness(self.inputs(stage9_domain_states=domain_states))
        by_module = {item.module: item for item in report.modules}
        self.assertEqual(by_module["trendRotation"].state, "ready_to_enable")
        self.assertEqual(by_module["etfObservation"].state, "ready_to_enable")
        self.assertEqual(by_module["leaderObservation"].state, "not_ready")
        self.assertIn("stage9_domain_not_ready", by_module["leaderObservation"].reason_codes)

    def test_nonready_overall_summary_with_valid_evidence_allows_ready_domain(self):
        from radar.formal_readiness_service import build_formal_readiness

        domain_states = {module: "not_ready" for module in MODULES}
        domain_states["trendRotation"] = "ready"
        for overall_state in ("collecting", "not_ready"):
            with self.subTest(overall_state=overall_state):
                report = build_formal_readiness(self.inputs(
                    stage9_quality_state=overall_state,
                    stage9_domain_states=domain_states,
                ))
                by_module = {item.module: item for item in report.modules}
                self.assertEqual(report.stage9_quality_state, overall_state)
                self.assertEqual(by_module["trendRotation"].state, "ready_to_enable")
                self.assertEqual(by_module["etfObservation"].state, "not_ready")
                self.assertEqual(by_module["leaderObservation"].state, "not_ready")

    def test_failed_overall_summary_closes_every_module(self):
        from radar.formal_readiness_service import build_formal_readiness

        report = build_formal_readiness(self.inputs(stage9_quality_state="failed"))

        self.assertEqual(report.stage9_quality_state, "failed")
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertTrue(all(
            "stage9_quality_failed" in item.reason_codes
            for item in report.modules
        ))

    def test_invalid_stage9_binding_closes_ready_domains_under_not_ready_summary(self):
        from radar.formal_readiness_service import build_formal_readiness

        evidence = self.inputs().evidence[0]
        cases = (
            (
                {"stage9_replay_run_id": None, "stage9_quality_sha256": None},
                "stage9_quality_identity_missing",
            ),
            (
                {"evidence": (evidence.model_copy(update={"content_sha256": "d" * 64}),)},
                "stage9_evidence_sha256_mismatch",
            ),
            (
                {"evidence": (evidence.model_copy(update={"subject_id": "another-run"}),)},
                "stage9_evidence_subject_mismatch",
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                report = build_formal_readiness(self.inputs(
                    stage9_quality_state="not_ready",
                    **changes,
                ))
                self.assertTrue(all(item.state == "not_ready" for item in report.modules))
                self.assertTrue(all(reason in item.reason_codes for item in report.modules))

    def test_each_required_gate_missing_fails_closed_without_hiding_other_module_readiness(self):
        from radar.formal_readiness_service import build_formal_readiness

        for field in ("rule_version_states", "calibration_states", "data_quality_states"):
            with self.subTest(field=field):
                values = {module: "ready" for module in MODULES}
                values["trendRotation"] = "not_ready"
                report = build_formal_readiness(self.inputs(**{field: values}))
                by_module = {item.module: item for item in report.modules}
                self.assertEqual(by_module["trendRotation"].state, "not_ready")
                self.assertEqual(by_module["etfObservation"].state, "ready_to_enable")

        from radar.formal_operational_checks import (
            OperationalGateResult,
            canonical_operational_checks_sha256,
        )
        for gate_name in ("performance", "security", "rollback", "runbook"):
            with self.subTest(gate=gate_name):
                checks = self.inputs().operational_checks
                gates = tuple(
                    OperationalGateResult(
                        gate=gate.gate,
                        state="not_ready" if gate.gate == gate_name else gate.state,
                        reasonCodes=(f"{gate_name}_not_ready",)
                        if gate.gate == gate_name else gate.reason_codes,
                    )
                    for gate in checks.gates
                )
                checks = checks.model_copy(update={"gates": gates, "state": "not_ready"})
                report = build_formal_readiness(self.inputs(
                    operational_checks=checks,
                    operational_checks_sha256=canonical_operational_checks_sha256(checks),
                ))
                self.assertTrue(all(item.state == "not_ready" for item in report.modules))

    def test_operational_package_is_the_only_source_for_platform_gates(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import canonical_operational_checks_sha256
        from radar.formal_readiness_service import FormalReadinessInputs, build_formal_readiness

        payload = self.inputs().model_dump(mode="python")
        payload["performance_state"] = "ready"
        with self.assertRaises(ValidationError):
            FormalReadinessInputs.model_validate(payload)

        missing = build_formal_readiness(self.inputs(
            operational_checks=None,
            operational_checks_sha256=None,
        ))
        self.assertTrue(all(
            "operational_checks_missing" in item.reason_codes
            for item in missing.modules
        ))
        original = self.inputs().operational_checks
        from radar.formal_operational_checks import OperationalEvidenceRef
        forged = original.model_copy(update={
            "subject_id": "other",
            "evidence": tuple(
                OperationalEvidenceRef(
                    gate=item.gate,
                    evidenceType=item.evidence_type,
                    contractVersion=item.contract_version,
                    contentSha256=item.content_sha256,
                    subjectId="other",
                    sourceTime=item.source_time,
                    fetchedAt=item.fetched_at,
                    generatedAt=item.generated_at,
                )
                for item in original.evidence
            ),
        })
        failed = build_formal_readiness(self.inputs(
            operational_checks=forged,
            operational_checks_sha256=canonical_operational_checks_sha256(forged),
        ))
        self.assertTrue(all(item.state == "failed" for item in failed.modules))
        self.assertTrue(all(
            "operational_checks_unverified" in item.reason_codes
            for item in failed.modules
        ))

    def test_operational_package_hash_or_future_time_fails_closed(self):
        from pydantic import ValidationError
        from radar.formal_operational_checks import (
            OperationalEvidenceRef,
            canonical_operational_checks_sha256,
        )
        from radar.formal_readiness_service import build_formal_readiness

        bad_hash = build_formal_readiness(self.inputs(
            operational_checks_sha256="0" * 64,
        ))
        self.assertTrue(all(item.state == "failed" for item in bad_hash.modules))

        original = self.inputs().operational_checks
        future_time = CHECKED_AT + timedelta(seconds=1)
        future = original.model_copy(update={
            "checked_at": future_time,
            "evidence": tuple(
                OperationalEvidenceRef(
                    gate=item.gate,
                    evidenceType=item.evidence_type,
                    contractVersion=item.contract_version,
                    contentSha256=item.content_sha256,
                    subjectId=item.subject_id,
                    sourceTime=future_time,
                    fetchedAt=future_time,
                    generatedAt=future_time,
                )
                for item in original.evidence
            ),
        })
        failed = build_formal_readiness(self.inputs(
            operational_checks=future,
            operational_checks_sha256=canonical_operational_checks_sha256(future),
        ))
        self.assertTrue(all(item.state == "failed" for item in failed.modules))
        self.assertTrue(all(
            "operational_checks_unverified" in item.reason_codes
            for item in failed.modules
        ))

        forged = self.inputs().operational_checks
        bad_evidence = forged.evidence[0].model_copy(
            update={"evidence_type": "arbitrary-v0"},
        )
        forged = forged.model_copy(update={
            "evidence": (bad_evidence,) + forged.evidence[1:],
        })
        with self.assertRaisesRegex(
            ValidationError,
            "operational_evidence_binding_mismatch",
        ):
            build_formal_readiness(self.inputs(
                operational_checks=forged,
                operational_checks_sha256=canonical_operational_checks_sha256(forged),
            ))

    def test_operational_collector_input_reference_is_reloaded_and_verified(self):
        from radar.formal_readiness_service import build_formal_readiness

        original = self.inputs()
        self.collector_input_path.write_bytes(b'{"subjectId":"tampered"}')
        report = build_formal_readiness(original)
        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertTrue(all(
            "operational_checks_unverified" in item.reason_codes
            for item in report.modules
        ))

    def test_operational_collector_input_is_replayed_and_must_match_submitted_report(self):
        """有效原始引用不能替一份脱离collector计算的全绿报告背书。"""
        from radar.formal_readiness_service import build_formal_readiness

        self.rebuild_patcher.stop()
        report = build_formal_readiness(self.inputs())

        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertTrue(all(
            "operational_checks_unverified" in item.reason_codes
            for item in report.modules
        ))

    def test_replayed_operational_report_must_match_every_submitted_field(self):
        from radar.formal_operational_checks import (
            canonical_operational_checks_sha256,
        )
        from radar.formal_readiness_service import build_formal_readiness

        original_inputs = self.inputs()
        original = original_inputs.operational_checks
        self.assertIsNotNone(original)
        forged_evidence = original.evidence[0].model_copy(
            update={"content_sha256": "d" * 64},
        )
        forged = original.model_copy(
            update={"evidence": (forged_evidence,) + original.evidence[1:]},
        )
        self.rebuild_patcher.stop()
        with patch(
            "radar.formal_readiness_service._rebuild_operational_checks",
            return_value=original,
        ):
            report = build_formal_readiness(self.inputs(
                operational_checks=forged,
                operational_checks_sha256=canonical_operational_checks_sha256(forged),
            ))

        self.assertTrue(all(item.state == "failed" for item in report.modules))
        self.assertTrue(all(
            "operational_checks_unverified" in item.reason_codes
            for item in report.modules
        ))

    def test_requested_switch_is_not_enablement_and_evidence_insufficiency_revokes_module(self):
        from radar.formal_readiness_service import build_formal_readiness

        requested = {module: False for module in MODULES}
        requested["trendRotation"] = True
        report = build_formal_readiness(self.inputs(requested_by_module=requested))
        trend = next(item for item in report.modules if item.module == "trendRotation")
        self.assertEqual(trend.state, "ready_to_enable")
        self.assertFalse(trend.formal_enabled)

        inadequate = self.inputs(evidence=())
        revoked = build_formal_readiness(inadequate)
        self.assertTrue(all(item.state == "not_ready" for item in revoked.modules))
        self.assertTrue(all("stage9_evidence_missing" in item.reason_codes for item in revoked.modules))

    def test_offline_requested_and_enabled_cannot_self_assert_formal_enablement(self):
        from radar.formal_readiness_service import build_formal_readiness

        requested = {module: False for module in MODULES}
        enabled = {module: False for module in MODULES}
        requested["etfObservation"] = True
        enabled["etfObservation"] = True
        report = build_formal_readiness(self.inputs(requested_by_module=requested, enabled_by_module=enabled))
        by_module = {item.module: item for item in report.modules}
        self.assertEqual(by_module["etfObservation"].state, "ready_to_enable")
        self.assertFalse(by_module["etfObservation"].formal_enabled)
        self.assertTrue(by_module["etfObservation"].configured_enabled)
        self.assertEqual(by_module["trendRotation"].state, "ready_to_enable")
        self.assertEqual(report.state, "ready_to_enable")
        self.assertFalse(report.any_formal_enabled)
        self.assertFalse(report.all_modules_formal_enabled)

        disabled_evidence = build_formal_readiness(self.inputs(enabled_by_module=enabled))
        etf = next(item for item in disabled_evidence.modules if item.module == "etfObservation")
        self.assertEqual(etf.state, "ready_to_enable")
        self.assertFalse(etf.formal_enabled)
        self.assertTrue(etf.configured_enabled)

        revoked_quality = {module: "ready" for module in MODULES}
        revoked_quality["etfObservation"] = "not_ready"
        revoked = build_formal_readiness(self.inputs(
            requested_by_module=requested,
            enabled_by_module=enabled,
            data_quality_states=revoked_quality,
        ))
        etf = next(item for item in revoked.modules if item.module == "etfObservation")
        self.assertEqual(etf.state, "not_ready")
        self.assertFalse(etf.formal_enabled)

    def test_future_evidence_is_rejected_before_report_construction(self):
        from pydantic import ValidationError
        from radar.formal_readiness_contracts import FormalEvidenceRef
        from radar.formal_readiness_service import build_formal_readiness

        future = FormalEvidenceRef(
            evidenceType="stage9_quality", contractVersion="radar-replay-quality-v2",
            contentSha256=SHA, subjectId="stage9-run-1", sourceTime=CHECKED_AT - timedelta(minutes=3),
            fetchedAt=CHECKED_AT - timedelta(minutes=2), generatedAt=CHECKED_AT + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "evidence_after_checkedAt"):
            build_formal_readiness(self.inputs(evidence=(future,)))

    def test_build_revalidates_model_copy_forged_ledger(self):
        from pydantic import ValidationError
        from radar.formal_readiness_service import build_formal_readiness

        forged_ledger = self.inputs().shadow_ledger.model_copy(update={
            "ready_trading_days_by_module": {
                "trendRotation": 0,
                "etfObservation": 0,
                "leaderObservation": 0,
            },
        })
        forged_inputs = self.inputs().model_copy(update={"shadow_ledger": forged_ledger})
        with self.assertRaisesRegex(ValidationError, "ready_trading_days_observations_conflict"):
            build_formal_readiness(forged_inputs)

    def test_build_revalidates_model_copy_forged_v2_streak(self):
        from pydantic import ValidationError
        from radar.formal_readiness_service import build_formal_readiness

        ledger = self.inputs().shadow_ledger
        forged_streaks = dict(ledger.latest_ready_streak_by_module)
        forged_streaks["etfObservation"] = 4
        forged_ledger = ledger.model_copy(update={
            "latest_ready_streak_by_module": forged_streaks,
        })

        with self.assertRaisesRegex(
            ValidationError,
            "latest_ready_streak_observations_conflict",
        ):
            build_formal_readiness(
                self.inputs().model_copy(update={"shadow_ledger": forged_ledger}),
            )

    def test_build_revalidates_model_copy_forged_nested_observation(self):
        from pydantic import ValidationError
        from radar.formal_readiness_service import build_formal_readiness

        ledger = self.inputs().shadow_ledger
        forged_observation = ledger.observations[0].model_copy(
            update={"missing_count": 1},
        )
        forged_ledger = ledger.model_copy(update={
            "observations": (forged_observation,) + ledger.observations[1:],
        })

        with self.assertRaisesRegex(
            ValidationError,
            "observation_status_fields_conflict",
        ):
            build_formal_readiness(
                self.inputs().model_copy(update={"shadow_ledger": forged_ledger}),
            )

    def test_future_shadow_timestamps_fail_closed_with_stable_reason(self):
        from radar.formal_readiness_service import build_formal_readiness

        cases = (
            {"observed_at": CHECKED_AT + timedelta(microseconds=1)},
            {
                "fetched_at": CHECKED_AT + timedelta(microseconds=1),
                "observed_at": CHECKED_AT + timedelta(microseconds=2),
            },
            {
                "source_time": CHECKED_AT + timedelta(microseconds=1),
                "fetched_at": CHECKED_AT + timedelta(microseconds=2),
                "observed_at": CHECKED_AT + timedelta(microseconds=3),
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                ledger = self.inputs().shadow_ledger
                future = ledger.observations[0].model_copy(update=changes)
                forged_ledger = ledger.model_copy(update={
                    "observations": (future,) + ledger.observations[1:],
                })

                report = build_formal_readiness(
                    self.inputs().model_copy(update={"shadow_ledger": forged_ledger}),
                )
                trend = next(
                    item for item in report.modules
                    if item.module == "trendRotation"
                )
                self.assertEqual(trend.state, "failed")
                self.assertIn(
                    "shadow_observation_after_checkedAt",
                    trend.reason_codes,
                )

    def test_last_observed_date_uses_v2_derived_shanghai_day_at_timezone_boundary(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.formal_readiness_service import build_formal_readiness

        checked_at = datetime(2026, 9, 4, 16, 30, tzinfo=UTC)
        report = build_formal_readiness(self.inputs(
            checked_at=checked_at,
            freshness_policy=RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds=86400,
                evidenceMaxAgeSeconds=86400,
                operationalChecksMaxAgeSeconds=86400,
            ),
        ))

        self.assertTrue(all(
            module.state == "ready_to_enable" for module in report.modules
        ))
        self.assertTrue(all(
            module.last_observed_trading_date == date(2026, 9, 4)
            for module in report.modules
        ))
