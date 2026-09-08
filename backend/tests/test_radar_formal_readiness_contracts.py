import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError


UTC = timezone.utc
CHECKED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64
REQUIRED_GATES = (
    "stage9_quality",
    "rule_version",
    "calibration",
    "shadow_ledger",
    "data_quality",
    "performance",
    "security",
    "rollback",
    "runbook",
)


class FormalReadinessContractTests(unittest.TestCase):
    @staticmethod
    def satisfy_shadow_days(module_payload):
        module_payload["observedTradingDays"] = module_payload["requiredTradingDays"]
        module_payload["lastObservedTradingDate"] = date(2026, 9, 4)

    def payload(self):
        return {
            "contractVersion": "radar-formal-readiness-v1",
            "checkedAt": CHECKED_AT,
            "freshnessPolicy": {
                "policyVersion": "radar-formal-freshness-policy-v1",
                "reportMaxAgeSeconds": 300,
                "evidenceMaxAgeSeconds": 600,
                "operationalChecksMaxAgeSeconds": 120,
            },
            "state": "collecting",
            "anyFormalEnabled": False,
            "allModulesFormalEnabled": False,
            "stage9ReplayRunId": "stage9-live-20260904",
            "stage9QualitySha256": SHA256,
            "stage9QualityState": "not_ready",
            "modules": [
                {
                    "module": "trendRotation",
                    "state": "collecting",
                    "requested": False,
                    "configuredEnabled": False,
                    "formalEnabled": False,
                    "observedTradingDays": 3,
                    "requiredTradingDays": 20,
                    "lastObservedTradingDate": date(2026, 9, 4),
                    "gates": [
                        {
                            "gate": "stage9_quality",
                            "state": "not_ready",
                            "required": True,
                            "reasonCodes": ["stage9_quality_not_ready"],
                        }
                    ],
                    "reasonCodes": ["shadow_days_insufficient"],
                },
                {
                    "module": "etfObservation",
                    "state": "collecting",
                    "requested": False,
                    "configuredEnabled": False,
                    "formalEnabled": False,
                    "observedTradingDays": 2,
                    "requiredTradingDays": 5,
                    "lastObservedTradingDate": date(2026, 9, 4),
                    "gates": [
                        {
                            "gate": "stage9_quality",
                            "state": "not_ready",
                            "required": True,
                            "reasonCodes": ["stage9_quality_not_ready"],
                        }
                    ],
                    "reasonCodes": ["shadow_days_insufficient"],
                },
                {
                    "module": "leaderObservation",
                    "state": "collecting",
                    "requested": False,
                    "configuredEnabled": False,
                    "formalEnabled": False,
                    "observedTradingDays": 1,
                    "requiredTradingDays": 20,
                    "lastObservedTradingDate": date(2026, 9, 4),
                    "gates": [
                        {
                            "gate": "stage9_quality",
                            "state": "not_ready",
                            "required": True,
                            "reasonCodes": ["stage9_quality_not_ready"],
                        }
                    ],
                    "reasonCodes": ["shadow_days_insufficient"],
                },
            ],
            "evidence": [
                {
                    "evidenceType": "stage9_quality",
                    "contractVersion": "radar-replay-quality-v2",
                    "contentSha256": SHA256,
                    "subjectId": "stage9-live-20260904",
                    "generatedAt": CHECKED_AT - timedelta(minutes=2),
                    "sourceTime": CHECKED_AT - timedelta(minutes=3),
                    "fetchedAt": CHECKED_AT - timedelta(minutes=2),
                },
                {
                    "evidenceType": "formal_operational_checks",
                    "contractVersion": "radar-formal-operational-checks-v1",
                    "contentSha256": "b" * 64,
                    "subjectId": "stage9-live-20260904",
                    "generatedAt": CHECKED_AT,
                    "sourceTime": CHECKED_AT,
                    "fetchedAt": CHECKED_AT,
                },
            ],
            "reasonCodes": ["shadow_days_insufficient"],
        }

    def test_invalid_module_and_extra_fields_are_rejected(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["modules"][0]["module"] = "unknown"
        with self.assertRaises(ValidationError):
            RadarFormalReadiness.model_validate(payload)

        payload = self.payload()
        payload["unapproved"] = True
        with self.assertRaises(ValidationError):
            RadarFormalReadiness.model_validate(payload)

    def test_all_enablement_booleans_reject_strings_and_numbers(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        mutations = (
            ("top_any_string", lambda value: value.__setitem__("anyFormalEnabled", "false")),
            ("top_all_number", lambda value: value.__setitem__("allModulesFormalEnabled", 0)),
            ("module_requested_string", lambda value: value["modules"][0].__setitem__("requested", "false")),
            ("module_configured_number", lambda value: value["modules"][0].__setitem__("configuredEnabled", 0)),
            ("module_enabled_string", lambda value: value["modules"][0].__setitem__("formalEnabled", "false")),
            ("gate_required_number", lambda value: value["modules"][0]["gates"][0].__setitem__("required", 1)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                payload = self.payload()
                mutate(payload)
                with self.assertRaises(ValidationError):
                    RadarFormalReadiness.model_validate(payload)

    def test_evidence_rejects_future_source_or_fetched_time(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        for field in ("sourceTime", "fetchedAt"):
            with self.subTest(field=field):
                payload = self.payload()
                payload["evidence"][0][field] = (
                    CHECKED_AT + timedelta(microseconds=1)
                )
                if field == "sourceTime":
                    payload["evidence"][0]["fetchedAt"] = (
                        CHECKED_AT + timedelta(microseconds=2)
                    )
                with self.assertRaises(ValidationError):
                    RadarFormalReadiness.model_validate(payload)

    def test_evidence_rejects_time_inversion_and_invalid_sha256(self):
        from radar.formal_readiness_contracts import FormalEvidenceRef

        payload = {
            "evidenceType": "shadow_ledger",
            "contractVersion": "radar-formal-shadow-ledger-v1",
            "contentSha256": SHA256,
            "subjectId": "ledger-1",
            "generatedAt": CHECKED_AT,
            "sourceTime": CHECKED_AT - timedelta(seconds=1),
            "fetchedAt": CHECKED_AT - timedelta(seconds=2),
        }
        with self.assertRaisesRegex(ValidationError, "sourceTime_after_fetchedAt"):
            FormalEvidenceRef.model_validate(payload)

        invalid_hash = deepcopy(payload)
        invalid_hash["contentSha256"] = "ABC"
        with self.assertRaises(ValidationError):
            FormalEvidenceRef.model_validate(invalid_hash)

    def test_evidence_cannot_be_generated_before_fetch(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["evidence"][0]["fetchedAt"] = CHECKED_AT - timedelta(minutes=1)
        payload["evidence"][0]["generatedAt"] = CHECKED_AT - timedelta(minutes=2)
        with self.assertRaisesRegex(ValidationError, "fetchedAt_after_generatedAt"):
            RadarFormalReadiness.model_validate(payload)

    def test_duplicate_modules_are_rejected(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["modules"][1] = deepcopy(payload["modules"][0])
        with self.assertRaisesRegex(ValidationError, "duplicate_module"):
            RadarFormalReadiness.model_validate(payload)

    def test_report_requires_all_product_modules(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["modules"].pop()
        with self.assertRaisesRegex(ValidationError, "modules_incomplete"):
            RadarFormalReadiness.model_validate(payload)

    def test_aggregate_enable_flags_must_match_module_enablement(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["anyFormalEnabled"] = True
        with self.assertRaisesRegex(ValidationError, "anyFormalEnabled_conflict"):
            RadarFormalReadiness.model_validate(payload)

        payload = self.payload()
        payload["allModulesFormalEnabled"] = True
        with self.assertRaisesRegex(
            ValidationError,
            "allModulesFormalEnabled_conflict",
        ):
            RadarFormalReadiness.model_validate(payload)

    def test_formal_enabled_requires_request_and_all_required_gates_ready(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        payload = self.payload()["modules"][0]
        payload["state"] = "formal_enabled"
        payload["formalEnabled"] = True
        payload["configuredEnabled"] = True
        self.satisfy_shadow_days(payload)
        payload["gates"] = [
            {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
            for gate in REQUIRED_GATES
        ]
        with self.assertRaisesRegex(ValidationError, "formal_enabled_requires_requested"):
            FormalModuleReadiness.model_validate(payload)

        payload["requested"] = True
        payload["gates"][-1] = {
            "gate": "runbook",
            "state": "not_ready",
            "required": True,
            "reasonCodes": ["runbook_gate_not_ready"],
        }
        with self.assertRaisesRegex(ValidationError, "formal_enabled_gate_not_ready"):
            FormalModuleReadiness.model_validate(payload)

    def test_formal_enabled_rejects_empty_or_incomplete_frozen_gate_set(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        payload = self.payload()["modules"][0]
        payload.update({"state": "formal_enabled", "requested": True, "configuredEnabled": True, "formalEnabled": True, "gates": []})
        with self.assertRaisesRegex(ValidationError, "formal_enabled_gates_empty"):
            FormalModuleReadiness.model_validate(payload)

        payload["gates"] = [
            {"gate": "stage9_quality", "state": "ready", "required": True, "reasonCodes": []},
        ]
        with self.assertRaisesRegex(ValidationError, "formal_enabled_required_gates_incomplete"):
            FormalModuleReadiness.model_validate(payload)

    def test_ready_module_requires_versioned_freshness_policy(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        for module in payload["modules"]:
            module["state"] = "ready_to_enable"
            self.satisfy_shadow_days(module)
            module["gates"] = [
                {
                    "gate": gate,
                    "state": "ready",
                    "required": True,
                    "reasonCodes": [],
                }
                for gate in REQUIRED_GATES
            ]
            module["reasonCodes"] = []
        payload["state"] = "ready_to_enable"
        payload["stage9QualityState"] = "ready"
        payload["freshnessPolicy"] = None

        with self.assertRaisesRegex(
            ValidationError,
            "formal_freshness_policy_missing",
        ):
            RadarFormalReadiness.model_validate(payload)

    def test_freshness_policy_is_strict_positive_and_frozen(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        self.assertEqual(policy.policy_version, "radar-formal-freshness-policy-v1")
        with self.assertRaises(ValidationError):
            RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds=0,
                evidenceMaxAgeSeconds=600,
                operationalChecksMaxAgeSeconds=120,
            )
        with self.assertRaises(ValidationError):
            RadarFormalFreshnessPolicy(
                reportMaxAgeSeconds="300",
                evidenceMaxAgeSeconds=600,
                operationalChecksMaxAgeSeconds=120,
            )
        with self.assertRaises(ValidationError):
            policy.report_max_age_seconds = 1

    def test_module_required_trading_days_are_fixed_by_module(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        for module, required_days in (
            ("trendRotation", 20),
            ("etfObservation", 5),
            ("leaderObservation", 20),
        ):
            with self.subTest(module=module):
                payload = self.payload()["modules"][0]
                payload["module"] = module
                payload["requiredTradingDays"] = required_days - 1
                with self.assertRaisesRegex(ValidationError, "requiredTradingDays_contract_conflict"):
                    FormalModuleReadiness.model_validate(payload)

    def test_top_level_state_matches_module_states_and_enablement(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["state"] = "formal_enabled"
        with self.assertRaisesRegex(ValidationError, "state_modules_conflict"):
            RadarFormalReadiness.model_validate(payload)

        payload = self.payload()
        payload["stage9QualityState"] = "ready"
        for module in payload["modules"]:
            module["gates"][0]["state"] = "ready"
        payload["modules"][0]["state"] = "ready_to_enable"
        self.satisfy_shadow_days(payload["modules"][0])
        payload["modules"][0]["gates"] = [
            {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
            for gate in REQUIRED_GATES
        ]
        with self.assertRaisesRegex(ValidationError, "state_modules_conflict"):
            RadarFormalReadiness.model_validate(payload)

        payload = self.payload()
        payload["modules"][0]["state"] = "failed"
        with self.assertRaisesRegex(ValidationError, "state_modules_conflict"):
            RadarFormalReadiness.model_validate(payload)

    def test_valid_contract_serializes_camel_case_aliases(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        value = RadarFormalReadiness.model_validate(self.payload())
        serialized = value.model_dump(by_alias=True)
        self.assertEqual(serialized["checkedAt"], CHECKED_AT)
        self.assertIn("anyFormalEnabled", serialized)
        self.assertNotIn("any_formal_enabled", serialized)
        self.assertIn("configuredEnabled", serialized["modules"][0])

    def test_formal_enabled_requires_configured_enabled_fact(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        payload = self.payload()["modules"][0]
        payload.update({
            "state": "formal_enabled",
            "requested": True,
            "configuredEnabled": False,
            "formalEnabled": True,
            "gates": [
                {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
                for gate in REQUIRED_GATES
            ],
        })
        self.satisfy_shadow_days(payload)
        with self.assertRaisesRegex(ValidationError, "formal_enabled_requires_configuredEnabled"):
            FormalModuleReadiness.model_validate(payload)

    def test_ready_stage9_requires_exact_unique_bound_evidence(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        cases = (
            ([], "stage9_ready_evidence_missing"),
            (None, "stage9_ready_evidence_not_unique"),
            ([{"evidenceType": "other", "contractVersion": "radar-replay-quality-v2", "contentSha256": SHA256, "subjectId": "stage9-live-20260904",
               "generatedAt": CHECKED_AT, "sourceTime": CHECKED_AT - timedelta(minutes=1), "fetchedAt": CHECKED_AT}], "stage9_ready_evidence_type_mismatch"),
            ([{"evidenceType": "stage9_quality", "contractVersion": "radar-replay-quality-v1", "contentSha256": SHA256, "subjectId": "stage9-live-20260904",
               "generatedAt": CHECKED_AT, "sourceTime": CHECKED_AT - timedelta(minutes=1), "fetchedAt": CHECKED_AT}], "stage9_ready_evidence_contract_mismatch"),
            ([{"evidenceType": "stage9_quality", "contractVersion": "radar-replay-quality-v2", "contentSha256": "b" * 64, "subjectId": "stage9-live-20260904",
               "generatedAt": CHECKED_AT, "sourceTime": CHECKED_AT - timedelta(minutes=1), "fetchedAt": CHECKED_AT}], "stage9_ready_evidence_sha256_mismatch"),
        )
        for evidence, reason in cases:
            with self.subTest(reason=reason):
                payload = self.payload()
                payload["stage9QualityState"] = "ready"
                payload["modules"][0]["gates"][0]["state"] = "ready"
                payload["evidence"] = self.payload()["evidence"] * 2 if evidence is None else evidence
                with self.assertRaisesRegex(ValidationError, reason):
                    RadarFormalReadiness.model_validate(payload)

    def test_ready_stage9_rejects_subject_mismatch(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["stage9QualityState"] = "ready"
        payload["modules"][0]["gates"][0]["state"] = "ready"
        payload["evidence"][0]["subjectId"] = "other-run"
        with self.assertRaisesRegex(ValidationError, "stage9_ready_evidence_subject_mismatch"):
            RadarFormalReadiness.model_validate(payload)

    def test_overall_ready_summary_rejects_invalid_binding(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["stage9QualityState"] = "ready"
        payload["stage9ReplayRunId"] = None
        payload["stage9QualitySha256"] = None
        payload["evidence"] = []

        with self.assertRaisesRegex(ValidationError, "stage9_ready_identity_missing"):
            RadarFormalReadiness.model_validate(payload)

    def test_module_stage9_gate_can_be_ready_when_overall_summary_is_not_ready(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["state"] = "ready_to_enable"
        self.satisfy_shadow_days(payload["modules"][0])
        payload["modules"][0].update({
            "state": "ready_to_enable",
            "gates": [
                {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
                for gate in REQUIRED_GATES
            ],
        })

        report = RadarFormalReadiness.model_validate(payload)

        self.assertEqual(report.stage9_quality_state, "not_ready")
        self.assertEqual(report.modules[0].state, "ready_to_enable")

    def test_failed_overall_stage9_summary_rejects_ready_module_gate(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["stage9QualityState"] = "failed"
        payload["modules"][0]["gates"][0]["state"] = "ready"
        with self.assertRaisesRegex(ValidationError, "stage9_failed_gate_ready_conflict"):
            RadarFormalReadiness.model_validate(payload)

    def test_module_ready_stage9_gate_requires_complete_report_binding(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["state"] = "ready_to_enable"
        payload["stage9ReplayRunId"] = None
        payload["stage9QualitySha256"] = None
        payload["evidence"] = []
        self.satisfy_shadow_days(payload["modules"][0])
        payload["modules"][0].update({
            "state": "ready_to_enable",
            "gates": [
                {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
                for gate in REQUIRED_GATES
            ],
        })
        with self.assertRaisesRegex(ValidationError, "stage9_ready_identity_missing"):
            RadarFormalReadiness.model_validate(payload)

    def test_ready_states_require_full_frozen_gate_set(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        payload = self.payload()["modules"][0]
        self.satisfy_shadow_days(payload)
        payload.update({"state": "ready_to_enable", "gates": []})
        with self.assertRaisesRegex(ValidationError, "ready_to_enable_gates_empty"):
            FormalModuleReadiness.model_validate(payload)

        payload["gates"] = [
            {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
            for gate in REQUIRED_GATES[:-1]
        ]
        with self.assertRaisesRegex(ValidationError, "ready_to_enable_required_gates_incomplete"):
            FormalModuleReadiness.model_validate(payload)

        payload["gates"] = [
            {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
            for gate in REQUIRED_GATES
        ]
        payload["gates"][-1]["state"] = "not_ready"
        with self.assertRaisesRegex(ValidationError, "ready_to_enable_gate_not_ready"):
            FormalModuleReadiness.model_validate(payload)

    def test_ready_shadow_gate_requires_observed_day_threshold_and_last_date(self):
        from radar.formal_readiness_contracts import FormalModuleReadiness

        payload = self.payload()["modules"][0]
        payload.update({
            "state": "ready_to_enable",
            "observedTradingDays": 0,
            "gates": [
                {"gate": gate, "state": "ready", "required": True, "reasonCodes": []}
                for gate in REQUIRED_GATES
            ],
        })
        with self.assertRaisesRegex(ValidationError, "shadow_ready_days_insufficient"):
            FormalModuleReadiness.model_validate(payload)

        payload["observedTradingDays"] = payload["requiredTradingDays"]
        payload["lastObservedTradingDate"] = None
        with self.assertRaisesRegex(ValidationError, "shadow_ready_last_date_missing"):
            FormalModuleReadiness.model_validate(payload)

    def test_required_trading_day_contract_is_immutable(self):
        from radar.formal_readiness_contracts import FORMAL_MODULE_REQUIRED_TRADING_DAYS

        for module, value in (("trendRotation", 1), ("etfObservation", 1), ("leaderObservation", 1)):
            with self.subTest(module=module):
                with self.assertRaises(TypeError):
                    FORMAL_MODULE_REQUIRED_TRADING_DAYS[module] = value

    def test_report_rejects_future_last_observed_date_by_shanghai_day(self):
        from radar.formal_readiness_contracts import RadarFormalReadiness

        payload = self.payload()
        payload["checkedAt"] = datetime(2026, 9, 4, 16, 30, tzinfo=UTC)
        for module in payload["modules"]:
            module["lastObservedTradingDate"] = date(2026, 9, 5)

        valid = RadarFormalReadiness.model_validate(payload)
        self.assertTrue(all(
            module.last_observed_trading_date == date(2026, 9, 5)
            for module in valid.modules
        ))

        payload["modules"][0]["lastObservedTradingDate"] = date(2026, 9, 6)
        with self.assertRaisesRegex(
            ValidationError,
            "lastObservedTradingDate_after_checkedAt",
        ):
            RadarFormalReadiness.model_validate(payload)
