import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import market_calendar
from radar.api import _service
from radar.config import RadarSettings
from radar.leader_repository import LeaderRepository
from radar.migrations import (
    MigrationDriftError,
    STAGE5_RADAR_MIGRATIONS,
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from radar.read_service import RadarReadService


UTC = timezone.utc
NOW = datetime(2026, 7, 27, 1, 45, tzinfo=UTC)


class UnusedRadarRepository:
    def get_latest_market_feature_row(self):
        return None

    def list_latest_sector_feature_rows(self):
        return ()

    def get_latest_run_row(self, prefix):
        return None

    def list_source_status_rows(self, radar_run_id):
        return ()


class FakeLeaderRepository:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def get_latest_candidate_snapshot(self):
        return self.snapshot


class BrokenLeaderRepository:
    def get_latest_candidate_snapshot(self):
        raise sqlite3.OperationalError("leader read failed")


def market_status_provider(market, now):
    return (
        market_calendar.MarketStatus("trading", "交易中"),
        market_calendar.CalendarDay(
            "full",
            "https://example.com/calendar",
            now.isoformat(),
        ),
    )


class RadarLeaderReadServiceTests(unittest.TestCase):
    def service(self, settings, leader_repository=None):
        arguments = {
            "settings": settings,
            "clock": lambda: NOW,
            "market_status_provider": market_status_provider,
        }
        if leader_repository is not None:
            arguments["leader_repository"] = leader_repository
        try:
            return RadarReadService(
                UnusedRadarRepository(),
                **arguments,
            )
        except TypeError as exc:
            self.fail(f"阶段6G仓储注入尚未实现: {exc}")

    def build_payload(self, settings):
        service = self.service(settings)
        builder = getattr(service, "build_leaders", None)
        self.assertIsNotNone(builder, "阶段6G只读服务尚未实现")
        return builder().model_dump(mode="json", by_alias=True)

    def test_default_disabled_response_is_explicit_and_empty(self):
        payload = self.build_payload(RadarSettings())

        self.assertEqual(payload["schemaVersion"], "radar-leaders-v1")
        self.assertEqual(payload["mode"], "disabled")
        self.assertEqual(payload["module"]["state"], "not_enabled")
        self.assertEqual(payload["module"]["quality"], "unavailable")
        self.assertEqual(
            payload["module"]["reasonCodes"],
            ["stage_not_enabled"],
        )
        self.assertEqual(payload["module"]["preliminary"], [])
        self.assertEqual(payload["module"]["candidates"], [])
        self.assertEqual(payload["module"]["confirmed"], [])
        self.assertFalse(
            payload["module"]["summary"]["formalStateEnabled"]
        )

    def test_enabled_without_stage6_storage_is_not_ready(self):
        payload = self.build_payload(RadarSettings(
            enabled=True,
            shadow_mode=True,
            leader_stage6_enabled=True,
        ))

        self.assertEqual(payload["mode"], "shadow")
        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertEqual(payload["module"]["quality"], "unavailable")
        self.assertIn(
            "stage6_storage_not_ready",
            payload["module"]["reasonCodes"],
        )
        self.assertEqual(
            payload["module"]["freshness"]["reasonCodes"],
            ["stage6_storage_not_ready"],
        )
        self.assertEqual(
            payload["module"]["summary"]["eligibleCount"],
            0,
        )
        self.assertEqual(
            payload["module"]["summary"]["formalUsableCount"],
            0,
        )

    def test_available_snapshot_is_capped_sorted_and_shadow_only(self):
        as_of = NOW - timedelta(seconds=30)
        entries = [
            self.entry(
                f"{100 + index:06d}",
                "preliminary",
                101 - index,
            )
            for index in range(1, 7)
        ]
        entries.extend([
            self.entry("000201", "candidate", 90),
            self.entry("000301", "confirmed", 95),
        ])
        payload = self.build_payload_with_repository(
            self.snapshot(as_of=as_of, entries=entries),
        )

        module = payload["module"]
        self.assertEqual(module["state"], "available")
        self.assertEqual(module["quality"], "complete")
        self.assertEqual(
            [item["symbol"] for item in module["preliminary"]],
            ["000101", "000102", "000103", "000104", "000105"],
        )
        self.assertEqual(
            [item["symbol"] for item in module["candidates"]],
            ["000201"],
        )
        self.assertEqual(
            [item["symbol"] for item in module["confirmed"]],
            ["000301"],
        )
        self.assertEqual(
            module["summary"]["overflowCounts"],
            {"preliminary": 1, "candidate": 0, "confirmed": 0},
        )
        self.assertEqual(module["summary"]["preliminaryCount"], 5)
        self.assertEqual(module["summary"]["candidateCount"], 1)
        self.assertEqual(module["summary"]["confirmedCount"], 1)
        self.assertEqual(module["freshness"]["ageSeconds"], 30)
        self.assertTrue(
            all(
                not item["formalUsable"]
                for group in ("preliminary", "candidates", "confirmed")
                for item in module[group]
            )
        )

    def test_successful_empty_snapshot_stays_empty(self):
        payload = self.build_payload_with_repository(
            self.snapshot(
                as_of=NOW - timedelta(seconds=30),
                entries=[],
                quality="empty",
            ),
        )

        self.assertEqual(payload["module"]["state"], "empty")
        self.assertEqual(payload["module"]["quality"], "complete")
        self.assertEqual(payload["module"]["preliminary"], [])
        self.assertEqual(payload["module"]["candidates"], [])
        self.assertEqual(payload["module"]["confirmed"], [])

    def test_partial_research_evidence_stays_not_ready_and_hidden(self):
        as_of = NOW - timedelta(seconds=30)
        entry = self.entry("000725", "out", 0)
        entry["dataStatus"] = "missing"
        entry["firstRejectionReason"] = "data_status_missing"
        entry["evidence"]["researchFeatures"] = {
            "formulaVersion": "radar-leader-research-feature-v1",
            "researchPartialScore": 55.0,
            "participatingWeight": 55.0,
            "requiredFormalWeight": 95.0,
            "scoreReady": False,
        }
        snapshot = self.snapshot(
            as_of=as_of,
            entries=[entry],
            quality="degraded",
        )
        snapshot["coverage"] = 0.0
        snapshot["reasonCounts"] = {
            "data_status_missing": 1,
        }

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertEqual(payload["module"]["preliminary"], [])
        self.assertEqual(payload["module"]["candidates"], [])
        self.assertEqual(payload["module"]["confirmed"], [])
        self.assertNotIn("researchFeatures", str(payload))

    def test_all_inputs_unavailable_is_not_ready_not_a_true_empty_board(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[self.entry("000001", "out", 0)],
            quality="degraded",
        )
        snapshot["coverage"] = 0.0
        snapshot["reasonCounts"] = {
            "data_status_missing": 1,
        }

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertEqual(payload["module"]["quality"], "unavailable")
        self.assertIn(
            "data_status_missing",
            payload["module"]["reasonCodes"],
        )
        self.assertEqual(payload["module"]["preliminary"], [])
        self.assertEqual(payload["module"]["candidates"], [])
        self.assertEqual(payload["module"]["confirmed"], [])

    def test_zero_candidate_degraded_snapshot_is_not_a_true_empty_board(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[],
            quality="degraded",
        )
        snapshot["reasonCounts"] = {
            "sector_snapshot_missing": 1,
        }

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertEqual(payload["module"]["quality"], "unavailable")
        self.assertIn(
            "sector_snapshot_missing",
            payload["module"]["reasonCodes"],
        )

    def test_old_business_as_of_is_stale_even_when_created_recently(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=391),
            entries=[self.entry("000001", "preliminary", 95)],
        )
        snapshot["createdAt"] = NOW - timedelta(seconds=1)

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "stale")
        self.assertEqual(
            payload["module"]["freshness"]["ageSeconds"],
            391,
        )

    def test_future_business_as_of_is_rejected(self):
        snapshot = self.snapshot(
            as_of=NOW + timedelta(seconds=6),
            entries=[self.entry("000001", "preliminary", 95)],
        )

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "failed")
        self.assertIn(
            "stage6_snapshot_from_future",
            payload["module"]["reasonCodes"],
        )

    def test_enabled_storage_without_snapshot_is_not_ready(self):
        payload = self.build_payload_with_repository(None)

        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertIn(
            "candidate_snapshot_missing",
            payload["module"]["reasonCodes"],
        )

    def test_trading_snapshot_older_than_two_cycles_plus_grace_is_stale(self):
        payload = self.build_payload_with_repository(
            self.snapshot(
                as_of=NOW - timedelta(seconds=391),
                entries=[self.entry("000001", "preliminary", 95)],
            ),
        )

        self.assertEqual(payload["module"]["state"], "stale")
        self.assertTrue(payload["module"]["usingLastSuccess"])
        self.assertTrue(payload["module"]["freshness"]["isStale"])
        self.assertEqual(
            payload["module"]["freshness"]["staleAfterSeconds"],
            390,
        )
        self.assertEqual(
            payload["module"]["preliminary"][0]["symbol"],
            "000001",
        )

    def test_repository_read_failure_is_failed_not_empty(self):
        payload = self.build_payload_with_repository(
            repository=BrokenLeaderRepository(),
        )

        self.assertEqual(payload["module"]["state"], "failed")
        self.assertEqual(payload["module"]["quality"], "unavailable")
        self.assertIn(
            "stage6_read_failed",
            payload["module"]["reasonCodes"],
        )
        self.assertEqual(payload["module"]["preliminary"], [])

    def test_formal_usable_snapshot_is_refused(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[self.entry("000201", "candidate", 90)],
        )
        snapshot["formalUsable"] = True
        snapshot["entries"][0]["formalUsable"] = True

        payload = self.build_payload_with_repository(snapshot)

        self.assertEqual(payload["module"]["state"], "not_ready")
        self.assertIn(
            "stage6_formal_state_forbidden",
            payload["module"]["reasonCodes"],
        )
        self.assertEqual(payload["module"]["candidates"], [])

    def test_malformed_snapshot_fails_only_the_leader_module(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[self.entry("000001", "preliminary", 95)],
        )
        snapshot.pop("createdAt")

        try:
            payload = self.build_payload_with_repository(snapshot)
        except KeyError as exc:
            self.fail(f"损坏快照不应击穿只读模块: {exc}")

        self.assertEqual(payload["module"]["state"], "failed")
        self.assertIn(
            "stage6_snapshot_invalid",
            payload["module"]["reasonCodes"],
        )
        self.assertEqual(payload["module"]["preliminary"], [])

    def test_overview_uses_leader_module_only_when_explicitly_enabled(self):
        as_of = NOW - timedelta(seconds=30)
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(self.snapshot(
                as_of=as_of,
                entries=[self.entry("000001", "preliminary", 95)],
            )),
        )

        payload = service.build_overview().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["modules"]["leaders"]["state"], "available")
        self.assertEqual(
            payload["modules"]["leaders"]["preliminary"][0]["symbol"],
            "000001",
        )
        self.assertNotIn(
            "enabledStage",
            payload["modules"]["leaders"],
        )

    def test_api_service_injects_stage6_repository_only_for_v5_storage(self):
        connection = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
                clock=lambda: NOW,
            )
            with patch(
                "radar.api.load_radar_settings",
                return_value=RadarSettings(
                    enabled=True,
                    shadow_mode=True,
                    leader_stage6_enabled=True,
                ),
            ):
                try:
                    service = _service(connection)
                except MigrationDriftError as exc:
                    self.fail(
                        f"阶段6G API仍拒绝版本5迁移合同: {exc}"
                    )

            self.assertIsInstance(
                service.leader_repository,
                LeaderRepository,
            )
        finally:
            connection.close()

    def test_api_service_is_not_ready_when_flag_precedes_v5_storage(self):
        connection = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
                clock=lambda: NOW,
            )
            with patch(
                "radar.api.load_radar_settings",
                return_value=RadarSettings(
                    enabled=True,
                    shadow_mode=True,
                    leader_stage6_enabled=True,
                ),
            ):
                try:
                    service = _service(connection)
                except MigrationDriftError as exc:
                    self.fail(
                        f"阶段6G缺少版本5时应返回not_ready: {exc}"
                    )

            payload = service.build_leaders().model_dump(
                mode="json",
                by_alias=True,
            )
            self.assertIsNone(service.leader_repository)
            self.assertEqual(payload["module"]["state"], "not_ready")
            self.assertIn(
                "stage6_storage_not_ready",
                payload["module"]["reasonCodes"],
            )
        finally:
            connection.close()

    def build_payload_with_repository(
        self,
        snapshot=None,
        *,
        repository=None,
    ):
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=(
                repository
                if repository is not None
                else FakeLeaderRepository(snapshot)
            ),
        )
        return service.build_leaders().model_dump(
            mode="json",
            by_alias=True,
        )

    @staticmethod
    def entry(symbol, state, score):
        return {
            "symbol": symbol,
            "name": f"样本{symbol}",
            "industryCode": "C39",
            "industryName": "计算机、通信和其他电子设备制造业",
            "state": state,
            "score": score,
            "businessExposureStatus": "verified",
            "dataStatus": "healthy",
            "firstRejectionReason": None,
            "reasons": ["state_maintained"],
            "evidence": {"source": "stage6g-fixture"},
            "invalidation": {"action": "hold"},
            "stateAgePeriods": 2,
            "formalUsable": False,
        }

    @staticmethod
    def snapshot(*, as_of, entries, quality="complete"):
        state_counts = {
            state: sum(item["state"] == state for item in entries)
            for state in ("preliminary", "candidate", "confirmed")
        }
        return {
            "radarRunId": "stage6g-snapshot",
            "asOf": as_of,
            "ruleVersion": "radar-leader-state-machine-v1",
            "ruleVersionId": None,
            "eligibleCount": len(entries),
            "preliminaryCount": state_counts["preliminary"],
            "candidateCount": state_counts["candidate"],
            "confirmedCount": state_counts["confirmed"],
            "removedCount": 0,
            "coverage": 1.0 if entries else 0.0,
            "quality": quality,
            "reasonCounts": {},
            "formalUsable": False,
            "createdAt": as_of + timedelta(seconds=2),
            "entries": entries,
        }


if __name__ == "__main__":
    unittest.main()
