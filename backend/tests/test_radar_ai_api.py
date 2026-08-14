import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import main
from radar.ai.config import RadarAiSettings
from radar.ai.api import router
from radar.ai.contracts import RadarAiStructuredOutput
from radar.ai.repository import RadarAiRepository
from radar.ai.service import RadarAiAnalysisResult, RadarAiUsage
from radar.migrations import STAGE8_RADAR_MIGRATIONS, apply_pending_migrations


UTC = timezone.utc
NOW = datetime(2026, 8, 14, 4, 0, tzinfo=UTC)


class RadarAiApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = f"{self.temp_dir.name}/radar-ai-api.db"
        connection = sqlite3.connect(self.path)
        apply_pending_migrations(
            connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
            clock=lambda: NOW,
        )
        connection.close()
        app = FastAPI()
        app.include_router(router)
        self.path_patcher = patch("radar.ai.api._database_path", return_value=self.path)
        self.path_patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    def seed_success(self):
        connection = sqlite3.connect(self.path)
        repository = RadarAiRepository(connection, clock=lambda: NOW)
        repository.start_run({
            "reuseKey": "reuse-api-1",
            "radarRunId": "formal-run-api-1",
            "asOf": NOW,
            "scopeType": "leader",
            "scopeId": "000725",
            "evidenceFingerprint": "f" * 64,
            "promptVersion": "radar-leader-v1",
            "model": "radar-model",
            "analysisAt": NOW,
        })
        output = RadarAiStructuredOutput.model_validate({
            "analysisStatus": "success",
            "confirmedFacts": [{
                "text": "正式证据通过",
                "sourceIds": ["source-1"],
            }],
            "inferences": [],
            "unknowns": [],
            "counterEvidence": [],
            "conditionalScenarios": [],
            "plainEnglishSummary": "仅解释正式规则结果。",
            "sourceIds": ["source-1"],
            "invalidatingConditions": [],
        })
        repository.finish_success(
            "reuse-api-1",
            RadarAiAnalysisResult(
                radar_run_id="formal-run-api-1",
                as_of=NOW,
                scope_type="leader",
                scope_id="000725",
                analysis_status="success",
                evidence_fingerprint="f" * 64,
                prompt_version="radar-leader-v1",
                model="radar-model",
                analysis_at=NOW,
                duration_ms=30,
                usage=RadarAiUsage(10, 5),
                output=output,
            ),
        )
        connection.close()

    def test_missing_analysis_returns_stable_not_run_contract(self):
        response = self.client.get("/api/radar/ai/leaders/000725")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schemaVersion"], "radar-ai-v1")
        self.assertEqual(payload["scopeType"], "leader")
        self.assertEqual(payload["scopeId"], "000725")
        self.assertEqual(payload["analysisStatus"], "not_run")
        self.assertEqual(payload["errorCategory"], "analysis_not_available")
        self.assertIsNone(payload["radarRunId"])

    def test_enabled_but_unconfigured_model_has_distinct_read_state(self):
        with patch(
            "radar.ai.api.load_radar_ai_settings",
            return_value=RadarAiSettings(enabled=True),
        ):
            response = self.client.get("/api/radar/ai/leaders/000725")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysisStatus"], "not_configured")
        self.assertEqual(
            response.json()["errorCategory"],
            "radar_ai_not_configured",
        )

    def test_health_exposes_only_independent_readiness_flags(self):
        settings = RadarAiSettings(
            enabled=True,
            manual_enabled=True,
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="radar-model",
            daily_call_limit=12,
            daily_token_limit=3456,
        )
        with patch(
            "radar.ai.api.load_radar_ai_settings",
            return_value=settings,
        ):
            response = self.client.get("/api/radar/ai/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "schemaVersion": "radar-ai-health-v1",
            "checkedAt": response.json()["checkedAt"],
            "status": "ready",
            "enabled": True,
            "manualEnabled": True,
            "configured": True,
            "storageReady": True,
            "dailyCallLimit": 12,
            "dailyTokenLimit": 3456,
        })

    def test_health_distinguishes_disabled_unconfigured_and_storage_missing(self):
        cases = (
            (RadarAiSettings(), self.path, "disabled"),
            (RadarAiSettings(enabled=True), self.path, "not_configured"),
            (
                RadarAiSettings(
                    enabled=True,
                    api_key="test-key",
                    base_url="https://llm.example/v1",
                    model="radar-model",
                ),
                f"{self.temp_dir.name}/missing.db",
                "storage_not_ready",
            ),
        )
        for settings, path, expected in cases:
            with self.subTest(expected=expected), patch(
                "radar.ai.api.load_radar_ai_settings",
                return_value=settings,
            ), patch("radar.ai.api._database_path", return_value=path):
                response = self.client.get("/api/radar/ai/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], expected)

    def test_latest_and_history_return_only_radar_ai_records(self):
        self.seed_success()

        latest = self.client.get("/api/radar/ai/leaders/000725")
        history = self.client.get(
            "/api/radar/ai/history?scope_type=leader&scope_id=000725"
        )

        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["analysisStatus"], "success")
        self.assertEqual(latest.json()["radarRunId"], "formal-run-api-1")
        self.assertEqual(
            latest.json()["output"]["plainEnglishSummary"],
            "仅解释正式规则结果。",
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["total"], 1)
        self.assertEqual(history.json()["items"][0]["scopeType"], "leader")

    def test_four_scope_routes_keep_distinct_prompt_contracts(self):
        cases = (
            ("/api/radar/ai/overview", "market", "overview", "radar-market-v1"),
            ("/api/radar/ai/sectors/73", "sector", "73", "radar-sector-v1"),
            ("/api/radar/ai/etfs/510300", "etf", "510300", "radar-etf-v1"),
            ("/api/radar/ai/leaders/000725", "leader", "000725", "radar-leader-v1"),
        )
        for path, scope_type, scope_id, prompt_version in cases:
            with self.subTest(path=path):
                payload = self.client.get(path).json()
                self.assertEqual(payload["scopeType"], scope_type)
                self.assertEqual(payload["scopeId"], scope_id)
                self.assertEqual(payload["promptVersion"], prompt_version)

    def test_invalid_etf_and_leader_codes_are_rejected(self):
        self.assertEqual(
            self.client.get("/api/radar/ai/etfs/not-code").status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/api/radar/ai/leaders/123").status_code,
            422,
        )

    def test_manual_analysis_is_default_closed_before_evidence_resolution(self):
        response = self.client.post(
            "/api/radar/ai/analyze",
            json={"scopeType": "leader", "scopeId": "000725"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "雷达AI手动核对未启用")

    def test_enabled_manual_analysis_uses_server_frozen_evidence(self):
        from radar.ai.contracts import FrozenRadarEvidencePackage

        package = FrozenRadarEvidencePackage.model_validate({
            "radarRunId": "formal-run-manual-1",
            "batchId": "leader-batch-manual-1",
            "asOf": "2026-08-14T04:00:00Z",
            "ruleVersion": "radar-leader-rule-v1",
            "coverage": 1.0,
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
                "sourceTime": "2026-08-14T03:59:00Z",
                "fetchedAt": "2026-08-14T04:00:00Z",
                "status": "healthy",
            }],
            "dataCompleteness": {
                "minimumCoverage": 0.99,
                "isComplete": True,
                "missingFields": [],
            },
            "formalStateEnabled": True,
        })
        expected = RadarAiAnalysisResult(
            radar_run_id="formal-run-manual-1",
            as_of=NOW,
            scope_type="leader",
            scope_id="000725",
            analysis_status="success",
            evidence_fingerprint="f" * 64,
            prompt_version="radar-leader-v1",
            model="radar-model",
            analysis_at=NOW,
            duration_ms=20,
            usage=RadarAiUsage(10, 5),
            output=RadarAiStructuredOutput.model_validate({
                "analysisStatus": "success",
                "confirmedFacts": [],
                "inferences": [],
                "unknowns": [],
                "counterEvidence": [],
                "conditionalScenarios": [],
                "plainEnglishSummary": "服务端冻结证据解读完成。",
                "sourceIds": [],
                "invalidatingConditions": [],
            }),
        )
        with patch("radar.ai.api.load_radar_ai_settings") as settings, patch(
            "radar.ai.api.resolve_frozen_evidence",
            return_value=package,
        ), patch(
            "radar.ai.api.analyze_frozen_evidence",
            return_value=expected,
        ):
            settings.return_value.enabled = True
            settings.return_value.manual_enabled = True
            settings.return_value.configured = True
            response = self.client.post(
                "/api/radar/ai/analyze",
                json={"scopeType": "leader", "scopeId": "000725"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysisStatus"], "success")
        self.assertEqual(response.json()["radarRunId"], "formal-run-manual-1")

    def test_manual_and_preference_writes_require_backend_token(self):
        self.assertTrue(
            main.request_requires_backend_token(
                type("Request", (), {
                    "method": "POST",
                    "url": type("Url", (), {"path": "/api/radar/ai/analyze"})(),
                })()
            )
        )
        self.assertTrue(
            main.request_requires_backend_token(
                type("Request", (), {
                    "method": "PUT",
                    "url": type("Url", (), {"path": "/api/radar/alerts/preferences"})(),
                })()
            )
        )

    def test_radar_notification_preferences_are_independent_and_persisted(self):
        with patch.object(database, "DB_PATH", self.path):
            initial = self.client.get("/api/radar/alerts/preferences")
            updated = self.client.put(
                "/api/radar/alerts/preferences",
                json={
                    "siteEnabled": True,
                    "emailEnabled": True,
                    "p2Email": True,
                    "p3Email": False,
                },
            )
            fetched = self.client.get("/api/radar/alerts/preferences")

        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.json()["data"]["emailEnabled"])
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(fetched.json()["data"]["emailEnabled"])
        self.assertFalse(fetched.json()["data"]["p3Email"])


if __name__ == "__main__":
    unittest.main()
