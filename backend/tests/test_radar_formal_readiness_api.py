import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.formal_readiness_contracts import (
    FORMAL_MODULE_REQUIRED_TRADING_DAYS,
    FormalGateState,
    FormalModuleReadiness,
    RadarFormalReadiness,
)
from radar.formal_readiness_store import save_formal_readiness


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
MODULES = ("trendRotation", "etfObservation", "leaderObservation")


def _report(*, checked_at=NOW, reason="fixture_not_ready"):
    modules = tuple(
        FormalModuleReadiness(
            module=module,
            state="not_ready",
            requested=False,
            configuredEnabled=False,
            formalEnabled=False,
            observedTradingDays=0,
            requiredTradingDays=FORMAL_MODULE_REQUIRED_TRADING_DAYS[module],
            gates=(FormalGateState(
                gate="stage9_quality",
                state="not_ready",
                reasonCodes=(reason,),
            ),),
            reasonCodes=(reason,),
        )
        for module in MODULES
    )
    return RadarFormalReadiness(
        checkedAt=checked_at,
        state="not_ready",
        anyFormalEnabled=False,
        allModulesFormalEnabled=False,
        stage9QualityState="not_ready",
        modules=modules,
        reasonCodes=(reason,),
    )


class RadarFormalReadinessApiTests(unittest.TestCase):
    def setUp(self):
        from radar.api import router

        self.temp_dir = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.store_dir = Path(self.temp_dir.name) / "formal-readiness"
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.path_patch = patch(
            "radar.api._formal_readiness_store_path",
            return_value=self.store_dir,
            create=True,
        )
        self.clock_patch = patch(
            "radar.api._utc_now",
            return_value=NOW,
            create=True,
        )
        self.path_patch.start()
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def get_without_sqlite(self):
        with (
            patch(
                "radar.api._database_path",
                side_effect=AssertionError("formal readiness must not resolve SQLite"),
            ),
            patch(
                "radar.api.open_radar_read_connection",
                side_effect=AssertionError("formal readiness must not open SQLite"),
            ),
        ):
            return self.client.get("/api/radar/formal-readiness")

    def assert_complete_closed_modules(self, payload, expected_state):
        self.assertEqual(payload["state"], expected_state)
        self.assertFalse(payload["anyFormalEnabled"])
        self.assertFalse(payload["allModulesFormalEnabled"])
        self.assertEqual(
            {item["module"] for item in payload["modules"]},
            set(MODULES),
        )
        for item in payload["modules"]:
            self.assertEqual(item["state"], expected_state)
            self.assertFalse(item["requested"])
            self.assertFalse(item["configuredEnabled"])
            self.assertFalse(item["formalEnabled"])
            self.assertEqual(
                item["requiredTradingDays"],
                FORMAL_MODULE_REQUIRED_TRADING_DAYS[item["module"]],
            )

    def test_missing_report_returns_complete_not_ready_without_sqlite(self):
        response = self.get_without_sqlite()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["cache-control"],
            "no-store, max-age=0",
        )
        payload = response.json()
        self.assert_complete_closed_modules(payload, "not_ready")
        self.assertEqual(
            payload["reasonCodes"],
            ["formal_readiness_report_missing"],
        )

    def test_corrupt_and_tampered_reports_fail_closed_without_path_leakage(self):
        cases = ("corrupt", "tampered")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
                    store_dir = Path(temp_dir) / "formal-readiness"
                    with patch(
                        "radar.api._formal_readiness_store_path",
                        return_value=store_dir,
                    ):
                        if case == "corrupt":
                            store_dir.mkdir()
                            (store_dir / "latest.json").write_text(
                                '{"secret":"/private/tmp/DO_NOT_LEAK_TOKEN"',
                                encoding="utf-8",
                            )
                        else:
                            stored = save_formal_readiness(_report(), store_dir)
                            report_path = store_dir / stored.relative_path
                            payload = json.loads(report_path.read_text(encoding="utf-8"))
                            payload["reasonCodes"] = ["tampered"]
                            report_path.write_text(json.dumps(payload), encoding="utf-8")

                        response = self.get_without_sqlite()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["cache-control"],
                    "no-store, max-age=0",
                )
                payload = response.json()
                self.assert_complete_closed_modules(payload, "failed")
                serialized = response.text
                self.assertNotIn("/private/", serialized)
                self.assertNotIn("DO_NOT_LEAK_TOKEN", serialized)

    def test_future_checked_at_fails_closed(self):
        save_formal_readiness(
            _report(checked_at=NOW + timedelta(microseconds=1)),
            self.store_dir,
        )

        response = self.get_without_sqlite()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assert_complete_closed_modules(payload, "failed")
        self.assertEqual(
            payload["reasonCodes"],
            ["formal_readiness_checked_at_future"],
        )

    def test_available_report_is_returned_without_schema_drift(self):
        report = _report(reason="stored_fixture_reason")
        save_formal_readiness(report, self.store_dir)

        response = self.get_without_sqlite()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            report.model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(
            response.headers["cache-control"],
            "no-store, max-age=0",
        )

    def test_expired_report_fails_closed_instead_of_returning_stored_result(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        report = _report(checked_at=NOW - timedelta(seconds=300, microseconds=1))
        report = report.model_copy(update={"freshness_policy": policy})
        save_formal_readiness(report, self.store_dir)

        response = self.get_without_sqlite()

        payload = response.json()
        self.assert_complete_closed_modules(payload, "failed")
        self.assertEqual(
            payload["reasonCodes"],
            ["formal_readiness_report_expired"],
        )

    def test_report_at_exact_max_age_is_returned(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        report = _report(checked_at=NOW - timedelta(seconds=300))
        report = report.model_copy(update={"freshness_policy": policy})
        save_formal_readiness(report, self.store_dir)

        response = self.get_without_sqlite()

        self.assertEqual(
            response.json(),
            report.model_dump(mode="json", by_alias=True),
        )

    def test_stored_evidence_ages_are_rechecked_at_request_time(self):
        from radar.formal_readiness_contracts import (
            FormalEvidenceRef,
            RadarFormalFreshnessPolicy,
        )

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=120,
        )
        cases = (
            (
                FormalEvidenceRef(
                    evidenceType="stage9_quality",
                    contractVersion="radar-replay-quality-v2",
                    contentSha256="a" * 64,
                    subjectId="stage9-run",
                    sourceTime=NOW - timedelta(seconds=600, microseconds=1),
                    fetchedAt=NOW,
                    generatedAt=NOW,
                ),
                "formal_evidence_expired",
            ),
            (
                FormalEvidenceRef(
                    evidenceType="formal_operational_checks",
                    contractVersion="radar-formal-operational-checks-v1",
                    contentSha256="b" * 64,
                    subjectId="stage9-run",
                    sourceTime=NOW - timedelta(seconds=120, microseconds=1),
                    fetchedAt=NOW,
                    generatedAt=NOW,
                ),
                "formal_operational_checks_expired",
            ),
        )
        for evidence, reason in cases:
            with self.subTest(reason=reason):
                report = _report().model_copy(update={
                    "freshness_policy": policy,
                    "evidence": (evidence,),
                })
                save_formal_readiness(report, self.store_dir)
                response = self.get_without_sqlite()
                self.assertEqual(response.json()["reasonCodes"], [reason])

    def test_naive_api_clock_fails_closed(self):
        report = _report()
        save_formal_readiness(report, self.store_dir)

        with patch("radar.api._utc_now", return_value=NOW.replace(tzinfo=None)):
            response = self.get_without_sqlite()

        payload = response.json()
        self.assert_complete_closed_modules(payload, "failed")
        self.assertEqual(payload["reasonCodes"], ["formal_clock_unverified"])

    def test_naive_api_clock_without_report_still_returns_closed_contract(self):
        with patch("radar.api._utc_now", return_value=NOW.replace(tzinfo=None)):
            response = self.get_without_sqlite()

        payload = response.json()
        self.assert_complete_closed_modules(payload, "failed")
        self.assertEqual(payload["reasonCodes"], ["formal_clock_unverified"])

    def test_existing_replay_and_leader_get_contracts_remain_registered(self):
        routes = {
            route.path: (route.methods, route.response_model)
            for route in self.client.app.routes
            if hasattr(route, "response_model")
        }

        self.assertEqual(routes["/api/radar/leaders"][0], {"GET"})
        self.assertEqual(
            routes["/api/radar/leaders"][1].__name__,
            "RadarLeadersResponse",
        )
        self.assertEqual(routes["/api/radar/replays/latest"][0], {"GET"})
        self.assertEqual(
            routes["/api/radar/replays/latest"][1].__name__,
            "RadarReplayQualityResponse",
        )


if __name__ == "__main__":
    unittest.main()
