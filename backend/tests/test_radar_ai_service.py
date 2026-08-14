import unittest
from copy import deepcopy
from datetime import datetime, timezone
import requests


UTC = timezone.utc


class MemoryRunStore:
    def __init__(self):
        self.successes = {}
        self.daily_calls = 0
        self.daily_tokens = 0
        self.started = []
        self.failed = []

    def find_success(self, reuse_key):
        return self.successes.get(reuse_key)

    def usage_for_day(self, day):
        return self.daily_calls, self.daily_tokens

    def start_run(self, record):
        self.started.append(record)

    def finish_success(self, reuse_key, result):
        self.successes[reuse_key] = result
        self.daily_calls += 1
        self.daily_tokens += result.usage.total_tokens

    def finish_failure(self, reuse_key, result):
        self.failed.append((reuse_key, result))
        self.daily_calls += 1


class ConflictingRunStore(MemoryRunStore):
    def start_run(self, record):
        from radar.ai.service import RadarAiRunConflict

        raise RadarAiRunConflict("radar_ai_reuse_conflict")


class FailingFinishStore(MemoryRunStore):
    def __init__(self, *, fail_success=True, fail_failure=False):
        super().__init__()
        self.fail_success = fail_success
        self.fail_failure = fail_failure

    def finish_success(self, reuse_key, result):
        if self.fail_success:
            raise OSError("storage unavailable")
        super().finish_success(reuse_key, result)

    def finish_failure(self, reuse_key, result):
        if self.fail_failure:
            raise OSError("storage unavailable")
        super().finish_failure(reuse_key, result)


class RecordingClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def analyze(self, *, package, system_prompt, prompt_version, model):
        self.calls.append((package.scope_id, prompt_version, model))
        if self.error is not None:
            raise self.error
        from radar.ai.client import RadarAiClientResponse

        return RadarAiClientResponse(
            payload=deepcopy(self.payload),
            model=model,
            prompt_tokens=120,
            completion_tokens=80,
        )


class RadarAiServiceTests(unittest.TestCase):
    def package(self, *, formal=True, coverage=1.0):
        from radar.ai.contracts import FrozenRadarEvidencePackage

        return FrozenRadarEvidencePackage.model_validate({
            "radarRunId": "radar-formal-1",
            "batchId": "leader-batch-1",
            "asOf": "2026-08-14T01:00:00Z",
            "ruleVersion": "radar-leader-rule-v1",
            "coverage": coverage,
            "scopeType": "leader",
            "scopeId": "000725",
            "formalState": "candidate",
            "formalScoreBreakdown": {},
            "stateHistory": [],
            "evidence": [{
                "evidenceId": "ev-1",
                "statement": "正式门槛通过",
                "sourceIds": ["source-1"],
                "factKind": "verified",
            }],
            "counterEvidence": [],
            "unknowns": [],
            "sourceCatalog": [{
                "sourceId": "source-1",
                "source": "official_source",
                "sourceTime": "2026-08-14T00:59:00Z",
                "fetchedAt": "2026-08-14T01:00:00Z",
                "status": "healthy",
            }],
            "dataCompleteness": {
                "minimumCoverage": 0.99,
                "isComplete": True,
                "missingFields": [],
            },
            "formalStateEnabled": formal,
        })

    def output(self):
        return {
            "analysisStatus": "success",
            "confirmedFacts": [{
                "text": "正式门槛通过",
                "sourceIds": ["source-1"],
            }],
            "inferences": [],
            "unknowns": [],
            "counterEvidence": [],
            "conditionalScenarios": [],
            "plainEnglishSummary": "仅解释冻结规则结果。",
            "sourceIds": ["source-1"],
            "invalidatingConditions": [],
        }

    def settings(self, **overrides):
        from radar.ai.config import RadarAiSettings

        values = {
            "enabled": True,
            "manual_enabled": False,
            "api_key": "test-key",
            "base_url": "https://llm.example/v1",
            "model": "radar-model",
            "daily_call_limit": 10,
            "daily_token_limit": 10000,
        }
        values.update(overrides)
        return RadarAiSettings(**values)

    def service(self, settings, client, store):
        from radar.ai.service import RadarAiService

        return RadarAiService(
            settings=settings,
            client=client,
            store=store,
            clock=lambda: datetime(2026, 8, 14, 1, 2, tzinfo=UTC),
        )

    def test_disabled_service_returns_not_run_without_calling_model(self):
        client = RecordingClient(self.output())
        service = self.service(
            self.settings(enabled=False),
            client,
            MemoryRunStore(),
        )

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "not_run")
        self.assertEqual(result.error_category, "radar_ai_disabled")
        self.assertEqual(client.calls, [])

    def test_unconfigured_service_returns_distinct_state(self):
        client = RecordingClient(self.output())
        service = self.service(
            self.settings(api_key=None),
            client,
            MemoryRunStore(),
        )

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "not_configured")
        self.assertEqual(result.error_category, "radar_ai_not_configured")
        self.assertEqual(client.calls, [])

    def test_non_formal_evidence_is_rejected_before_model_call(self):
        client = RecordingClient(self.output())
        service = self.service(self.settings(), client, MemoryRunStore())

        result = service.analyze(self.package(formal=False))

        self.assertEqual(result.analysis_status, "evidence_insufficient")
        self.assertEqual(result.error_category, "formal_state_not_enabled")
        self.assertEqual(client.calls, [])

    def test_model_failure_keeps_failure_separate_from_rule_result(self):
        client = RecordingClient(error=TimeoutError("upstream timeout"))
        store = MemoryRunStore()
        service = self.service(self.settings(), client, store)

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "failed")
        self.assertEqual(result.error_category, "model_timeout")
        self.assertIsNone(result.output)
        self.assertEqual(len(store.failed), 1)

    def test_http_timeout_has_distinct_failure_category(self):
        client = RecordingClient(error=requests.Timeout("upstream timeout"))
        service = self.service(self.settings(), client, MemoryRunStore())

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "failed")
        self.assertEqual(result.error_category, "model_timeout")

    def test_success_is_validated_and_reused_without_second_model_call(self):
        client = RecordingClient(self.output())
        store = MemoryRunStore()
        service = self.service(self.settings(), client, store)

        first = service.analyze(self.package())
        second = service.analyze(self.package())

        self.assertEqual(first.analysis_status, "success")
        self.assertEqual(first.output.plain_english_summary, "仅解释冻结规则结果。")
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(first.evidence_fingerprint, second.evidence_fingerprint)

    def test_quota_exhaustion_stops_new_model_call(self):
        client = RecordingClient(self.output())
        store = MemoryRunStore()
        store.daily_calls = 10
        service = self.service(self.settings(daily_call_limit=10), client, store)

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "failed")
        self.assertEqual(result.error_category, "daily_call_quota_exceeded")
        self.assertEqual(client.calls, [])

    def test_manual_call_requires_independent_manual_switch(self):
        client = RecordingClient(self.output())
        service = self.service(self.settings(), client, MemoryRunStore())

        result = service.analyze(self.package(), manual=True)

        self.assertEqual(result.analysis_status, "not_run")
        self.assertEqual(result.error_category, "radar_ai_manual_disabled")
        self.assertEqual(client.calls, [])

    def test_cross_process_reuse_conflict_returns_running_and_releases_local_lock(self):
        client = RecordingClient(self.output())
        service = self.service(self.settings(), client, ConflictingRunStore())

        first = service.analyze(self.package())
        second = service.analyze(self.package())

        self.assertEqual(first.analysis_status, "not_run")
        self.assertEqual(first.error_category, "analysis_already_running")
        self.assertEqual(second.analysis_status, "not_run")
        self.assertEqual(second.error_category, "analysis_already_running")
        self.assertEqual(client.calls, [])

    def test_success_persistence_failure_returns_stable_storage_failure(self):
        client = RecordingClient(self.output())
        store = FailingFinishStore()
        service = self.service(self.settings(), client, store)

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "failed")
        self.assertEqual(result.error_category, "analysis_storage_failed")
        self.assertEqual(len(store.failed), 1)
        self.assertEqual(len(client.calls), 1)

    def test_failure_persistence_error_never_escapes_to_rule_caller(self):
        client = RecordingClient(error=TimeoutError("upstream timeout"))
        store = FailingFinishStore(fail_failure=True)
        service = self.service(self.settings(), client, store)

        result = service.analyze(self.package())

        self.assertEqual(result.analysis_status, "failed")
        self.assertEqual(result.error_category, "analysis_storage_failed")


if __name__ == "__main__":
    unittest.main()
