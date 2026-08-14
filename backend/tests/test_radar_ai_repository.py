import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from radar.ai.repository import RadarAiRepository
from radar.ai.service import (
    RadarAiAnalysisResult,
    RadarAiRunConflict,
    RadarAiUsage,
)
from radar.ai.contracts import RadarAiStructuredOutput
from radar.migrations import STAGE8_RADAR_MIGRATIONS, apply_pending_migrations


UTC = timezone.utc
NOW = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)


class RadarAiRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = f"{self.temp_dir.name}/radar-ai.db"
        self.connection = sqlite3.connect(self.path)
        apply_pending_migrations(
            self.connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
            clock=lambda: NOW,
        )
        self.repository = RadarAiRepository(self.connection, clock=lambda: NOW)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def result(self, *, status="success", fingerprint="f" * 64, prompt="radar-leader-v1", model="m1"):
        output = None
        if status == "success":
            output = RadarAiStructuredOutput.model_validate({
                "analysisStatus": "success",
                "confirmedFacts": [{
                    "text": "规则门槛通过",
                    "sourceIds": ["source-1"],
                }],
                "inferences": [],
                "unknowns": [],
                "counterEvidence": [],
                "conditionalScenarios": [],
                "plainEnglishSummary": "仅解释规则结果。",
                "sourceIds": ["source-1"],
                "invalidatingConditions": [],
            })
        return RadarAiAnalysisResult(
            radar_run_id="run-1",
            as_of=NOW,
            scope_type="leader",
            scope_id="000725",
            analysis_status=status,
            evidence_fingerprint=fingerprint,
            prompt_version=prompt,
            model=model,
            analysis_at=NOW,
            duration_ms=25,
            usage=RadarAiUsage(12, 8),
            output=output,
            error_category=None if status == "success" else "model_call_failed",
        )

    def record(self, reuse_key="reuse-1", **overrides):
        value = {
            "reuseKey": reuse_key,
            "radarRunId": "run-1",
            "scopeType": "leader",
            "scopeId": "000725",
            "evidenceFingerprint": "f" * 64,
            "promptVersion": "radar-leader-v1",
            "model": "m1",
            "analysisAt": NOW,
        }
        value.update(overrides)
        return value

    def test_success_is_reopened_and_reused_after_connection_restart(self):
        self.repository.start_run(self.record())
        self.repository.finish_success("reuse-1", self.result())
        self.connection.close()

        self.connection = sqlite3.connect(self.path)
        reopened = RadarAiRepository(self.connection, clock=lambda: NOW)
        result = reopened.find_success("reuse-1")

        self.assertEqual(result.analysis_status, "success")
        self.assertEqual(result.output.plain_english_summary, "仅解释规则结果。")
        self.assertEqual(result.usage.total_tokens, 20)

    def test_failed_run_never_appears_as_success(self):
        self.repository.start_run(self.record("reuse-failed"))
        self.repository.finish_failure(
            "reuse-failed",
            self.result(status="failed"),
        )

        self.assertIsNone(self.repository.find_success("reuse-failed"))
        history = self.repository.list_history("leader", "000725")
        self.assertEqual(history[0].analysis_status, "failed")
        self.assertIsNone(history[0].output)

    def test_fingerprint_prompt_and_model_have_independent_reuse_keys(self):
        cases = (
            ("key-fingerprint", {"evidenceFingerprint": "a" * 64}),
            ("key-prompt", {"promptVersion": "radar-leader-v2"}),
            ("key-model", {"model": "m2"}),
        )
        for reuse_key, overrides in cases:
            record = self.record(reuse_key, **overrides)
            result = self.result(
                fingerprint=record["evidenceFingerprint"],
                prompt=record["promptVersion"],
                model=record["model"],
            )
            self.repository.start_run(record)
            self.repository.finish_success(reuse_key, result)

        self.assertEqual(
            {item.model for item in self.repository.list_history("leader", "000725")},
            {"m1", "m2"},
        )
        self.assertIsNotNone(self.repository.find_success("key-fingerprint"))
        self.assertIsNotNone(self.repository.find_success("key-prompt"))
        self.assertIsNotNone(self.repository.find_success("key-model"))

    def test_usage_counts_only_started_model_attempts(self):
        self.repository.start_run(self.record())
        self.repository.finish_success("reuse-1", self.result())

        self.assertEqual(self.repository.usage_for_day("2026-08-14"), (1, 20))

    def test_running_reuse_key_conflict_has_stable_domain_error(self):
        self.repository.start_run(self.record())

        with self.assertRaisesRegex(RadarAiRunConflict, "radar_ai_reuse_conflict"):
            self.repository.start_run(self.record())


if __name__ == "__main__":
    unittest.main()
