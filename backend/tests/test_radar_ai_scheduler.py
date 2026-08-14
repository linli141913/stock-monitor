import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from radar.ai.config import RadarAiSettings
from radar.ai.contracts import FrozenRadarEvidencePackage
from radar.ai.scheduler import (
    RADAR_AI_AUTO_JOB_ID,
    RADAR_AI_TASK_NAME,
    RadarAiAutomaticJob,
    _default_analyzer,
    register_radar_ai_job,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 14, 5, 0, tzinfo=UTC)


def package():
    return FrozenRadarEvidencePackage.model_validate({
        "radarRunId": "formal-run-scheduler-1",
        "batchId": "leader-batch-scheduler-1",
        "asOf": "2026-08-14T05:00:00Z",
        "ruleVersion": "radar-leader-rule-v1",
        "coverage": 1.0,
        "scopeType": "leader",
        "scopeId": "000725",
        "formalState": "candidate",
        "formalScoreBreakdown": {},
        "stateHistory": [],
        "evidence": [{
            "evidenceId": "ev-1",
            "statement": "正式状态发生变化",
            "sourceIds": ["source-1"],
            "factKind": "verified",
        }],
        "counterEvidence": [],
        "unknowns": [],
        "sourceCatalog": [{
            "sourceId": "source-1",
            "source": "official_source",
            "sourceTime": "2026-08-14T04:59:00Z",
            "fetchedAt": "2026-08-14T05:00:00Z",
            "status": "healthy",
        }],
        "dataCompleteness": {
            "minimumCoverage": 0.99,
            "isComplete": True,
            "missingFields": [],
        },
        "formalStateEnabled": True,
    })


class RadarAiSchedulerTests(unittest.TestCase):
    def settings(self, *, enabled):
        return RadarAiSettings(
            enabled=enabled,
            manual_enabled=False,
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="radar-model",
        )

    def test_disabled_ai_does_not_register_scheduler_job(self):
        scheduler = Mock()

        registration = register_radar_ai_job(
            scheduler,
            settings=self.settings(enabled=False),
        )

        self.assertEqual(registration.state, "disabled")
        self.assertEqual(registration.job_id, RADAR_AI_AUTO_JOB_ID)
        scheduler.add_job.assert_not_called()

    def test_enabled_ai_registers_single_non_overlapping_job(self):
        scheduler = Mock()

        registration = register_radar_ai_job(
            scheduler,
            settings=self.settings(enabled=True),
            package_provider=lambda: (),
            analyzer=Mock(),
        )

        self.assertEqual(registration.state, "registered")
        call = scheduler.add_job.call_args
        self.assertEqual(call.args[1], "interval")
        self.assertEqual(call.kwargs["id"], RADAR_AI_AUTO_JOB_ID)
        self.assertEqual(call.kwargs["max_instances"], 1)
        self.assertTrue(call.kwargs["coalesce"])

    def test_empty_formal_evidence_skips_without_model_call(self):
        analyzer = Mock()
        job = RadarAiAutomaticJob(
            package_provider=lambda: (),
            analyzer=analyzer,
        )
        with patch("radar.ai.scheduler.monitoring_health") as health:
            outcome = job()

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.reason, "formal_evidence_not_available")
        analyzer.assert_not_called()
        health.record_task_skipped.assert_called_once_with(
            RADAR_AI_TASK_NAME,
            "formal_evidence_not_available",
        )

    def test_each_frozen_package_is_analyzed_once(self):
        analyzer = Mock()
        analyzer.return_value.analysis_status = "success"
        job = RadarAiAutomaticJob(
            package_provider=lambda: (package(),),
            analyzer=analyzer,
        )
        with patch("radar.ai.scheduler.monitoring_health") as health:
            outcome = job()

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.analyzed_count, 1)
        self.assertEqual(outcome.failed_count, 0)
        analyzer.assert_called_once()
        health.record_task_success.assert_called_once_with(
            RADAR_AI_TASK_NAME,
            item_count=1,
        )

    def test_process_lock_suppresses_overlapping_run(self):
        lock = threading.Lock()
        lock.acquire()
        job = RadarAiAutomaticJob(
            package_provider=lambda: (package(),),
            analyzer=Mock(),
            run_lock=lock,
        )
        try:
            with patch("radar.ai.scheduler.monitoring_health") as health:
                outcome = job()
        finally:
            lock.release()

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.reason, "analysis_job_already_running")
        health.record_task_skipped.assert_called_once()

    def test_default_automatic_analyzer_does_not_require_manual_switch(self):
        expected = object()
        with patch(
            "radar.ai.api.analyze_frozen_evidence",
            return_value=expected,
        ) as analyze:
            result = _default_analyzer(package())

        self.assertIs(result, expected)
        analyze.assert_called_once_with(package(), manual=False)


if __name__ == "__main__":
    unittest.main()
