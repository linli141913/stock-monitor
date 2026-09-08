import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 2, 0, tzinfo=UTC)
SHA = "a" * 64


class FormalExecutionContractTests(unittest.TestCase):
    def binding(self, **changes):
        from radar.formal_execution_contracts import (
            build_formal_execution_binding,
        )

        values = {
            "module": "trendRotation",
            "job_id": "radar-formal-trend-rotation",
            "executor_id": "trend-executor-v1",
            "executor_contract_version": "trend-executor-contract-v1",
            "config_sha256": SHA,
            "generated_at": NOW - timedelta(minutes=1),
            "expires_at": NOW + timedelta(minutes=5),
        }
        values.update(changes)
        return build_formal_execution_binding(**values)

    def test_fixed_job_ids_are_complete_and_immutable(self):
        from radar.formal_execution_contracts import FORMAL_JOB_IDS

        self.assertEqual(dict(FORMAL_JOB_IDS), {
            "trendRotation": "radar-formal-trend-rotation",
            "etfObservation": "radar-formal-etf-observation",
            "leaderObservation": "radar-formal-leader-observation",
        })
        with self.assertRaises(TypeError):
            FORMAL_JOB_IDS["trendRotation"] = "radar-shadow-scan"

    def test_binding_binds_identity_config_time_and_its_own_content_hash(self):
        from radar.formal_execution_contracts import (
            FormalExecutionBinding,
            formal_execution_binding_content_sha256,
        )

        binding = self.binding()
        self.assertEqual(
            binding.content_sha256,
            formal_execution_binding_content_sha256(binding),
        )
        self.assertEqual(binding.contract_version, "radar-formal-execution-binding-v1")

        forged = binding.model_copy(update={"config_sha256": "b" * 64})
        with self.assertRaisesRegex(
            ValidationError,
            "formal_execution_binding_content_sha256_mismatch",
        ):
            FormalExecutionBinding.model_validate(
                forged.model_dump(mode="json", by_alias=True),
            )

    def test_binding_rejects_wrong_fixed_job_naive_time_and_invalid_window(self):
        cases = (
            (
                {"job_id": "radar-shadow-scan"},
                "formal_execution_binding_job_identity_mismatch",
            ),
            (
                {"generated_at": NOW.replace(tzinfo=None)},
                "formal_execution_time_timezone_required",
            ),
            (
                {
                    "generated_at": NOW + timedelta(minutes=1),
                    "expires_at": NOW + timedelta(minutes=1),
                },
                "formal_execution_binding_expiry_invalid",
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex((ValidationError, ValueError), reason):
                    self.binding(**changes)

    def test_execution_context_is_frozen_and_requires_exact_nine_ready_gates(self):
        from radar.formal_execution_contracts import (
            FROZEN_FORMAL_EXECUTION_GATES,
            FormalExecutionBindingReference,
            FormalExecutionContext,
            FormalExecutionGateSnapshot,
            FormalReadinessReference,
        )

        binding = self.binding()
        context = FormalExecutionContext(
            invocationId="formal-invocation-1",
            module="trendRotation",
            jobId=binding.job_id,
            executorId=binding.executor_id,
            executorContractVersion=binding.executor_contract_version,
            configSha256=binding.config_sha256,
            readiness=FormalReadinessReference(
                contentSha256="b" * 64,
                relativePath="reports/" + "b" * 64 + ".json",
                checkedAt=NOW,
            ),
            binding=FormalExecutionBindingReference(
                contentSha256=binding.content_sha256,
                generatedAt=binding.generated_at,
                expiresAt=binding.expires_at,
            ),
            gates=tuple(
                FormalExecutionGateSnapshot(gate=gate, state="ready")
                for gate in FROZEN_FORMAL_EXECUTION_GATES
            ),
        )
        self.assertEqual(context.module, "trendRotation")
        with self.assertRaises(ValidationError):
            context.invocation_id = "mutated"

        missing_gate = context.model_dump(mode="json", by_alias=True)
        missing_gate["gates"] = missing_gate["gates"][:-1]
        with self.assertRaisesRegex(
            ValidationError,
            "formal_execution_gates_incomplete",
        ):
            FormalExecutionContext.model_validate(missing_gate)

    def test_execution_result_rejects_formal_state_and_write_instructions(self):
        from radar.formal_execution_contracts import FormalExecutionResult

        valid = {
            "status": "completed",
            "startedAt": NOW,
            "finishedAt": NOW + timedelta(seconds=1),
            "reasonCodes": (),
            "candidateResultRefs": (),
        }
        result = FormalExecutionResult.model_validate(valid)
        self.assertEqual(result.status, "completed")

        forbidden = (
            ("formalEnabled", True),
            ("configuredEnabled", True),
            ("stateWriteInstructions", {"trendRotation": "formal_enabled"}),
        )
        for field, value in forbidden:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    FormalExecutionResult.model_validate({**valid, field: value})

    def test_candidate_result_reference_rejects_absolute_or_escaping_paths(self):
        from radar.formal_execution_contracts import FormalCandidateResultRef

        valid = FormalCandidateResultRef(
            contractVersion="radar-trend-candidates-v1",
            contentSha256=SHA,
            relativePath="candidates/result.json",
        )
        self.assertEqual(valid.relative_path, "candidates/result.json")

        for relative_path in (
            "/private/tmp/result.json",
            "../result.json",
            "candidates/../../result.json",
            "candidates//result.json",
            "candidates\\..\\result.json",
        ):
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(
                    ValidationError,
                    "formal_candidate_result_path_unverified",
                ):
                    FormalCandidateResultRef(
                        contractVersion="radar-trend-candidates-v1",
                        contentSha256=SHA,
                        relativePath=relative_path,
                    )


if __name__ == "__main__":
    unittest.main()
