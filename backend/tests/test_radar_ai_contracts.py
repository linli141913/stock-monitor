import unittest
from copy import deepcopy


class RadarAiContractTests(unittest.TestCase):
    def evidence_payload(self, *, scope_type="leader"):
        return {
            "radarRunId": "radar-formal-20260814T010000Z",
            "batchId": "leader-batch-1",
            "asOf": "2026-08-14T01:00:00Z",
            "ruleVersion": "radar-leader-v1",
            "coverage": 1.0,
            "scopeType": scope_type,
            "scopeId": "000725" if scope_type == "leader" else "73",
            "formalState": "candidate",
            "formalScoreBreakdown": {"marketLeadership": 82.0},
            "stateHistory": [
                {
                    "fromState": "preliminary",
                    "toState": "candidate",
                    "asOf": "2026-08-14T01:00:00Z",
                }
            ],
            "evidence": [
                {
                    "evidenceId": "ev-1",
                    "statement": "相对强度门槛通过",
                    "sourceIds": ["quote-1"],
                    "factKind": "verified",
                }
            ],
            "counterEvidence": [],
            "firstRejectionReason": None,
            "unknowns": [],
            "sourceCatalog": [
                {
                    "sourceId": "quote-1",
                    "source": "tencent_finance",
                    "sourceTime": "2026-08-14T00:59:55Z",
                    "fetchedAt": "2026-08-14T01:00:00Z",
                    "status": "healthy",
                }
            ],
            "dataCompleteness": {
                "minimumCoverage": 0.99,
                "isComplete": True,
                "missingFields": [],
            },
            "formalStateEnabled": True,
        }

    def valid_output(self):
        return {
            "analysisStatus": "success",
            "confirmedFacts": [
                {
                    "text": "相对强度门槛通过",
                    "sourceIds": ["quote-1"],
                }
            ],
            "inferences": ["当前状态延续依赖行业强度"],
            "unknowns": [],
            "counterEvidence": [],
            "conditionalScenarios": ["若行业强度下降，候选状态可能失效"],
            "plainEnglishSummary": "这是规则结果的解释，不是投资建议。",
            "sourceIds": ["quote-1"],
            "invalidatingConditions": ["行业强度门槛失效"],
        }

    def test_four_scopes_have_independent_prompt_versions(self):
        from radar.ai.prompts import prompt_version_for_scope

        self.assertEqual(prompt_version_for_scope("market"), "radar-market-v1")
        self.assertEqual(prompt_version_for_scope("sector"), "radar-sector-v1")
        self.assertEqual(prompt_version_for_scope("etf"), "radar-etf-v1")
        self.assertEqual(prompt_version_for_scope("leader"), "radar-leader-v1")

    def test_evidence_fingerprint_is_stable_for_equivalent_payloads(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import validate_evidence_package

        first = FrozenRadarEvidencePackage.model_validate(self.evidence_payload())
        reordered = deepcopy(self.evidence_payload())
        reordered["formalScoreBreakdown"] = {"marketLeadership": 82.0}
        second = FrozenRadarEvidencePackage.model_validate(reordered)

        self.assertEqual(
            validate_evidence_package(first),
            validate_evidence_package(second),
        )
        self.assertEqual(len(validate_evidence_package(first)), 64)

    def test_evidence_rejects_non_formal_or_incomplete_input(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import RadarAiEvidenceError, validate_evidence_package

        disabled = self.evidence_payload()
        disabled["formalStateEnabled"] = False
        with self.assertRaisesRegex(RadarAiEvidenceError, "formal_state_not_enabled"):
            validate_evidence_package(
                FrozenRadarEvidencePackage.model_validate(disabled)
            )

        incomplete = self.evidence_payload()
        incomplete["coverage"] = 0.8
        with self.assertRaisesRegex(RadarAiEvidenceError, "coverage_below_minimum"):
            validate_evidence_package(
                FrozenRadarEvidencePackage.model_validate(incomplete)
            )

    def test_evidence_rejects_unknown_source_reference(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import RadarAiEvidenceError, validate_evidence_package

        payload = self.evidence_payload()
        payload["evidence"][0]["sourceIds"] = ["missing-source"]
        package = FrozenRadarEvidencePackage.model_validate(payload)

        with self.assertRaisesRegex(RadarAiEvidenceError, "unknown_source_id"):
            validate_evidence_package(package)

    def test_evidence_rejects_empty_or_only_unconfirmed_facts(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import RadarAiEvidenceError, validate_evidence_package

        for evidence in ([], [{
            "evidenceId": "ev-unconfirmed",
            "statement": "仅是待核验线索",
            "sourceIds": ["quote-1"],
            "factKind": "unconfirmed",
        }]):
            with self.subTest(evidence=evidence):
                payload = self.evidence_payload()
                payload["evidence"] = evidence
                package = FrozenRadarEvidencePackage.model_validate(payload)
                with self.assertRaisesRegex(
                    RadarAiEvidenceError,
                    "verified_evidence_required",
                ):
                    validate_evidence_package(package)

    def test_source_time_can_remain_truthfully_missing(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import validate_evidence_package

        payload = self.evidence_payload()
        payload["sourceCatalog"][0]["sourceTime"] = None

        fingerprint = validate_evidence_package(
            FrozenRadarEvidencePackage.model_validate(payload)
        )

        self.assertEqual(len(fingerprint), 64)

    def test_model_output_rejects_unknown_sources_and_privileged_fields(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import RadarAiOutputError, validate_model_output

        package = FrozenRadarEvidencePackage.model_validate(self.evidence_payload())
        unknown = self.valid_output()
        unknown["sourceIds"] = ["invented-source"]
        with self.assertRaisesRegex(RadarAiOutputError, "unknown_source_id"):
            validate_model_output(unknown, package)

        privileged = self.valid_output()
        privileged["leaderState"] = "confirmed"
        with self.assertRaisesRegex(RadarAiOutputError, "forbidden_output_field"):
            validate_model_output(privileged, package)

    def test_valid_output_keeps_facts_inferences_and_unknowns_separate(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import validate_model_output

        package = FrozenRadarEvidencePackage.model_validate(self.evidence_payload())
        output = validate_model_output(self.valid_output(), package)

        self.assertEqual(output.analysis_status, "success")
        self.assertEqual(output.confirmed_facts[0].source_ids, ["quote-1"])
        self.assertEqual(output.inferences, ["当前状态延续依赖行业强度"])
        self.assertEqual(output.unknowns, [])

    def test_model_output_rejects_target_price_and_return_promises(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage
        from radar.ai.validator import RadarAiOutputError, validate_model_output

        package = FrozenRadarEvidencePackage.model_validate(self.evidence_payload())
        cases = (
            "目标价为20元",
            "预计收益率30%",
            "建议立即买入",
            "可以给出买入建议",
            "保证上涨",
        )
        for text in cases:
            with self.subTest(text=text):
                payload = self.valid_output()
                payload["plainEnglishSummary"] = text
                with self.assertRaisesRegex(
                    RadarAiOutputError,
                    "prohibited_investment_claim",
                ):
                    validate_model_output(payload, package)


if __name__ == "__main__":
    unittest.main()
