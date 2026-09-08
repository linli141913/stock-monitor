import unittest
from datetime import timedelta


class _Executor:
    def __init__(
        self,
        module="trendRotation",
        executor_id="trend-executor-v1",
        contract_version="trend-executor-contract-v1",
    ):
        self.module = module
        self.executor_id = executor_id
        self.contract_version = contract_version

    def __call__(self, context):
        raise AssertionError("pure_guard_must_not_call_executor")


class FormalExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        from tests.test_radar_scheduler import FORMAL_CHECKED_AT

        self.now = FORMAL_CHECKED_AT
        self.config_sha256 = "c" * 64

    def binding(self, **changes):
        from radar.formal_execution_contracts import (
            build_formal_execution_binding,
        )

        values = {
            "module": "trendRotation",
            "job_id": "radar-formal-trend-rotation",
            "executor_id": "trend-executor-v1",
            "executor_contract_version": "trend-executor-contract-v1",
            "config_sha256": self.config_sha256,
            "generated_at": self.now - timedelta(minutes=1),
            "expires_at": self.now + timedelta(minutes=5),
        }
        values.update(changes)
        return build_formal_execution_binding(**values)

    def load_result(self, binding=None, status="available"):
        from radar.formal_execution_guard import (
            FormalExecutionBindingLoadResult,
        )

        if binding is None and status == "available":
            binding = self.binding()
        return FormalExecutionBindingLoadResult(
            status=status,
            binding=binding,
            reasonCodes=(() if status == "available" else ("fixture_unavailable",)),
        )

    def decision(self, **changes):
        from radar.formal_execution_guard import (
            evaluate_formal_execution_guard,
        )
        from tests.test_radar_scheduler import _formal_load_result, _formal_report

        values = {
            "module": "trendRotation",
            "job_id": "radar-formal-trend-rotation",
            "executor": _Executor(),
            "config_sha256": self.config_sha256,
            "requested": True,
            "binding_loader": lambda: self.load_result(),
            "readiness_loader": lambda: _formal_load_result(_formal_report()),
            "now": self.now,
            "invocation_id": "formal-invocation-1",
        }
        values.update(changes)
        return evaluate_formal_execution_guard(**values)

    def test_valid_independent_binding_and_nine_gate_report_build_context(self):
        decision = self.decision()

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, "")
        self.assertEqual(decision.context.module, "trendRotation")
        self.assertEqual(
            decision.context.binding.content_sha256,
            self.binding().content_sha256,
        )
        self.assertEqual(len(decision.context.gates), 9)

    def test_binding_missing_damaged_future_expired_and_hash_mismatch_fail_closed(self):
        valid = self.binding()
        forged = valid.model_copy(update={"content_sha256": "d" * 64})
        damaged_load = self.load_result().model_copy(update={"binding": forged})
        cases = (
            (
                lambda: self.load_result(status="missing"),
                self.now,
                "formal_execution_binding_missing",
            ),
            (
                lambda: (_ for _ in ()).throw(ValueError("damaged")),
                self.now,
                "formal_execution_binding_unverified",
            ),
            (
                lambda: self.load_result(self.binding(
                    generated_at=self.now + timedelta(seconds=1),
                    expires_at=self.now + timedelta(minutes=5),
                )),
                self.now,
                "formal_execution_binding_future",
            ),
            (
                lambda: self.load_result(self.binding(
                    generated_at=self.now - timedelta(minutes=5),
                    expires_at=self.now,
                )),
                self.now,
                "formal_execution_binding_expired",
            ),
            (
                lambda: damaged_load,
                self.now,
                "formal_execution_binding_hash_mismatch",
            ),
        )
        for loader, now, reason in cases:
            with self.subTest(reason=reason):
                decision = self.decision(binding_loader=loader, now=now)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, reason)
                self.assertIsNone(decision.context)

    def test_module_job_executor_and_config_identity_mismatch_fail_closed(self):
        cases = (
            (
                {"job_id": "radar-shadow-scan"},
                "formal_execution_job_identity_mismatch",
            ),
            (
                {"executor": _Executor(module="etfObservation")},
                "formal_executor_module_mismatch",
            ),
            (
                {"executor": _Executor(executor_id="other-executor")},
                "formal_executor_identity_mismatch",
            ),
            (
                {"executor": _Executor(contract_version="other-contract")},
                "formal_executor_contract_mismatch",
            ),
            (
                {"config_sha256": "e" * 64},
                "formal_execution_binding_config_mismatch",
            ),
            (
                {
                    "binding_loader": lambda: self.load_result(self.binding(
                        module="etfObservation",
                        job_id="radar-formal-etf-observation",
                    )),
                },
                "formal_execution_binding_module_mismatch",
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                decision = self.decision(**changes)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, reason)

    def test_unrequested_or_offline_formal_enabled_report_cannot_authorize(self):
        from tests.test_radar_scheduler import _formal_load_result, _formal_report

        self.assertEqual(
            self.decision(requested=False).reason_code,
            "formal_request_not_enabled",
        )
        ready = _formal_report()
        forged_module = ready.modules[0].model_copy(update={
            "state": "formal_enabled",
            "requested": True,
            "configured_enabled": True,
            "formal_enabled": True,
        })
        forged_report = ready.model_copy(update={
            "state": "formal_enabled",
            "any_formal_enabled": True,
            "modules": (forged_module,) + ready.modules[1:],
        })
        decision = self.decision(
            readiness_loader=lambda: _formal_load_result(forged_report),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.reason_code,
            "formal_readiness_runtime_proof_missing",
        )


if __name__ == "__main__":
    unittest.main()
