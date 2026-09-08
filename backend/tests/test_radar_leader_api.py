import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import market_calendar
from fastapi import FastAPI
from fastapi.testclient import TestClient
from radar.api import _service
from radar.api import router as radar_router
from radar.config import RadarSettings
from radar.leader_repository import LeaderRepository
from radar.leader_observation_store import (
    LeaderObservationItem,
    LeaderObservationSnapshot,
    LeaderObservationStoreReadResult,
)
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


class FakeRiskReviewRepository:
    def get_latest_review_batch_summary(self):
        return {
            "reviewBatchId": "risk-review-batch-1",
            "candidatePlanId": "candidate-plan-1",
            "asOf": NOW - timedelta(seconds=15),
            "windowFrom": (NOW - timedelta(days=365)).date(),
            "windowUntil": NOW.date(),
            "candidateCount": 385,
            "documentCount": 1919,
            "documentLinkCount": 1919,
            "contentSnapshotCount": 0,
            "reviewedDocumentCount": 0,
            "reviewVersionCount": 0,
            "queryCategoriesComplete": True,
            "queryPagesComplete": True,
            "queryWindowContinuous": True,
        }

    def list_review_batch_documents(self, batch_id, *, limit, offset):
        return {
            "reviewBatchId": batch_id,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "items": (),
        }

    def get_review_batch_document(self, batch_id, document_id, category):
        return {
            "document": SimpleNamespace(
                document_id=document_id,
                symbol="000725",
                issuer_name="京东方A",
                title="官方风险公告",
                published_at=NOW - timedelta(minutes=30),
                source_name="巨潮资讯",
                source_url="https://static.cninfo.com.cn/finalpage/2026-07-27/123.PDF",
                candidate_category=SimpleNamespace(value=category),
            ),
            "hasContentSnapshot": False,
            "contentSnapshotCount": 0,
            "contentStatus": "not_fetched",
            "contentFetchedAt": None,
            "reviewVersionCount": 0,
        }


class MatchingRiskReviewRepository(FakeRiskReviewRepository):
    def get_latest_review_batch_summary(self):
        summary = super().get_latest_review_batch_summary()
        summary["candidatePlanId"] = (
            "radar-leader-runtime-candidate-plan-v1:" + "b" * 64
        )
        return summary


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
    def service(
        self,
        settings,
        leader_repository=None,
        risk_review_repository=None,
        leader_observation_loader=None,
    ):
        arguments = {
            "settings": settings,
            "clock": lambda: NOW,
            "market_status_provider": market_status_provider,
        }
        if leader_repository is not None:
            arguments["leader_repository"] = leader_repository
        if risk_review_repository is not None:
            arguments["risk_review_repository"] = risk_review_repository
        if leader_observation_loader is not None:
            arguments["leader_observation_loader"] = (
                leader_observation_loader
            )
        try:
            return RadarReadService(
                UnusedRadarRepository(),
                **arguments,
            )
        except TypeError as exc:
            self.fail(f"阶段6G仓储注入尚未实现: {exc}")

    @staticmethod
    def observation_result(*, as_of=None, status="available"):
        as_of = as_of or (NOW - timedelta(seconds=30))
        if status != "available":
            return LeaderObservationStoreReadResult(
                status=status,
                reasons=(f"leader_observation_{status}",),
            )
        snapshot = LeaderObservationSnapshot(
            radar_run_id="market-run-observation",
            candidate_plan_id=(
                "radar-leader-runtime-candidate-plan-v1:" + "b" * 64
            ),
            as_of=as_of,
            published_at=max(as_of, NOW - timedelta(seconds=20)),
            scanned_count=5157,
            mapped_count=383,
            items=(LeaderObservationItem(
                symbol="000725",
                name="京东方A",
                industry_code="C39",
                industry_name="计算机、通信和其他电子设备制造业",
                within_industry_rank=2,
                price=4.21,
                change_percent=2.43,
                source_time=as_of,
                quote_source_contract_id="tencent-full-market-quote-v1:batch-1",
                sector_source_contract_id="radar-sector-aggregate-v1:sector:C39",
            ),),
        )
        return LeaderObservationStoreReadResult(
            status="available",
            reasons=(),
            snapshot=snapshot,
            evidence_sha256="c" * 64,
        )

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

    def test_real_observation_is_visible_without_formal_snapshot_or_approval(self):
        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_observation_loader=lambda: self.observation_result(),
        ).build_leaders().model_dump(mode="json", by_alias=True)

        module = payload["module"]
        self.assertEqual(module["state"], "available")
        self.assertEqual(module["quality"], "partial")
        self.assertEqual(module["summary"]["eligibleCount"], 1)
        self.assertEqual(module["preliminary"], [])
        self.assertEqual(module["candidates"], [])
        self.assertEqual(module["confirmed"], [])
        self.assertEqual(module["observation"]["status"], "available")
        self.assertTrue(module["observation"]["displayAllowed"])
        self.assertFalse(module["observation"]["humanApprovalRequired"])
        self.assertFalse(module["observation"]["formalUsable"])
        self.assertEqual(
            module["observation"]["items"][0]["symbol"],
            "000725",
        )
        self.assertNotIn("score", module["observation"]["items"][0])

    def test_leader_source_summary_uses_only_real_bound_observation_sources(self):
        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            risk_review_repository=MatchingRiskReviewRepository(),
            leader_observation_loader=lambda: self.observation_result(),
        ).build_leaders().model_dump(mode="json", by_alias=True)

        module = payload["module"]
        summary = {
            item["domain"]: item
            for item in module["sourceSummary"]
        }
        self.assertEqual(module["sources"], [])
        self.assertEqual(set(summary), {
            "quote", "sector", "business", "announcement",
        })
        self.assertEqual(summary["quote"]["status"], "available")
        self.assertEqual(summary["quote"]["sourceContractIds"], [
            "tencent-full-market-quote-v1:batch-1",
        ])
        self.assertEqual(summary["quote"]["coveredCount"], 1)
        self.assertEqual(summary["quote"]["expectedCount"], 1)
        self.assertEqual(summary["sector"]["status"], "available")
        self.assertEqual(summary["sector"]["sourceContractIds"], [
            "radar-sector-aggregate-v1:sector:C39",
        ])
        self.assertEqual(summary["business"]["status"], "missing")
        self.assertIn(
            "leader_business_source_summary_missing",
            summary["business"]["reasonCodes"],
        )
        self.assertEqual(summary["announcement"]["status"], "available")
        self.assertEqual(summary["announcement"]["coveredCount"], 385)
        self.assertEqual(
            summary["announcement"]["scope"],
            "current_observation_candidates",
        )

    def test_leader_announcement_source_is_unverified_when_plan_does_not_match(self):
        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            risk_review_repository=FakeRiskReviewRepository(),
            leader_observation_loader=lambda: self.observation_result(),
        ).build_leaders().model_dump(mode="json", by_alias=True)

        announcement = next(
            item for item in payload["module"]["sourceSummary"]
            if item["domain"] == "announcement"
        )
        self.assertEqual(announcement["status"], "unverified")
        self.assertEqual(announcement["coveredCount"], 0)
        self.assertIn(
            "leader_announcement_candidate_plan_mismatch",
            announcement["reasonCodes"],
        )

    def test_observation_stale_and_failed_states_are_explicit(self):
        settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            leader_stage6_enabled=True,
        )
        stale = self.service(
            settings,
            leader_observation_loader=lambda: self.observation_result(
                as_of=NOW - timedelta(seconds=391),
            ),
        ).build_leaders().model_dump(mode="json", by_alias=True)
        failed = self.service(
            settings,
            leader_observation_loader=lambda: self.observation_result(
                status="failed",
            ),
        ).build_leaders().model_dump(mode="json", by_alias=True)

        self.assertEqual(stale["module"]["state"], "stale")
        self.assertTrue(stale["module"]["observation"]["displayAllowed"])
        self.assertTrue(stale["module"]["observation"]["freshness"]["isStale"])
        self.assertEqual(failed["module"]["state"], "failed")
        self.assertFalse(failed["module"]["observation"]["displayAllowed"])
        self.assertIn(
            "leader_observation_failed",
            failed["module"]["reasonCodes"],
        )

    def test_successful_empty_observation_is_not_a_source_failure(self):
        snapshot = LeaderObservationSnapshot(
            radar_run_id="market-run-empty",
            candidate_plan_id="candidate-plan-empty",
            as_of=NOW - timedelta(seconds=30),
            published_at=NOW - timedelta(seconds=20),
            scanned_count=5157,
            mapped_count=0,
            items=(),
        )
        result = LeaderObservationStoreReadResult(
            status="available",
            reasons=(),
            snapshot=snapshot,
            evidence_sha256="d" * 64,
        )

        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_observation_loader=lambda: result,
        ).build_leaders().model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["module"]["state"], "empty")
        self.assertEqual(payload["module"]["observation"]["status"], "empty")
        self.assertEqual(payload["module"]["observation"]["items"], [])

    def test_observation_from_future_fails_closed(self):
        result = self.observation_result(as_of=NOW + timedelta(seconds=6))

        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_observation_loader=lambda: result,
        ).build_leaders().model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["module"]["state"], "failed")
        self.assertFalse(payload["module"]["observation"]["displayAllowed"])
        self.assertIn(
            "leader_observation_from_future",
            payload["module"]["reasonCodes"],
        )

    def test_stock_radar_reports_real_observation_without_leader_rating(self):
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_observation_loader=lambda: self.observation_result(),
        )

        matched = service.build_stock("000725").model_dump(
            mode="json",
            by_alias=True,
        )
        absent = service.build_stock("000001").model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(matched["status"], "observed")
        self.assertIsNone(matched["leader"])
        self.assertEqual(matched["observationItem"]["symbol"], "000725")
        self.assertNotIn("score", matched["observationItem"])
        self.assertEqual(absent["status"], "not_listed")
        self.assertIsNone(absent["observationItem"])
        self.assertEqual(
            absent["reasonCodes"],
            ["stock_not_in_observation_candidates"],
        )

    def test_stock_radar_matches_latest_public_leader_tier(self):
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(self.snapshot(
                as_of=NOW - timedelta(seconds=30),
                entries=[self.entry("000725", "candidate", 91)],
            )),
        )

        payload = service.build_stock("000725").model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["schemaVersion"], "radar-stock-v1")
        self.assertEqual(payload["status"], "matched")
        self.assertEqual(payload["symbol"], "000725")
        self.assertEqual(payload["leader"]["state"], "candidate")
        self.assertEqual(payload["leader"]["industryCode"], "C39")
        self.assertEqual(payload["leader"]["firstRejectionReason"], None)
        self.assertEqual(
            payload["snapshot"]["ruleVersion"],
            "radar-leader-state-machine-v1",
        )

    def test_stock_radar_distinguishes_not_listed_from_d2_review_queue(self):
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(self.snapshot(
                as_of=NOW - timedelta(seconds=30),
                entries=[self.entry("000001", "preliminary", 88)],
            )),
            risk_review_repository=FakeRiskReviewRepository(),
        )

        payload = service.build_stock("000725").model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["status"], "not_listed")
        self.assertIsNone(payload["leader"])
        self.assertEqual(payload["reasonCodes"], ["stock_not_in_public_tiers"])

    def test_stock_radar_reports_no_snapshot(self):
        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(None),
        ).build_stock("000725").model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["status"], "no_snapshot")
        self.assertIsNone(payload["snapshot"])
        self.assertEqual(payload["reasonCodes"], ["candidate_snapshot_missing"])

    def test_stock_radar_reports_waiting_when_snapshot_is_not_ready(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[self.entry("000725", "preliminary", 88)],
            quality="unavailable",
        )
        snapshot["reasonCounts"] = {"data_status_missing": 1}

        payload = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(snapshot),
        ).build_stock("000725").model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["status"], "no_snapshot")
        self.assertIsNone(payload["snapshot"])
        self.assertEqual(payload["reasonCodes"], ["data_status_missing"])

    def test_stock_radar_preserves_stale_and_failed_semantics(self):
        stale = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(self.snapshot(
                as_of=NOW - timedelta(seconds=391),
                entries=[self.entry("000725", "candidate", 91)],
            )),
        ).build_stock("000725").model_dump(mode="json", by_alias=True)
        failed = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=BrokenLeaderRepository(),
        ).build_stock("000725").model_dump(mode="json", by_alias=True)

        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["leader"]["state"], "candidate")
        self.assertTrue(stale["freshness"]["isStale"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["leader"])

    def test_stock_radar_reports_disabled_stage(self):
        payload = self.service(RadarSettings()).build_stock(
            "000725",
        ).model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["status"], "not_enabled")
        self.assertEqual(payload["reasonCodes"], ["stage_not_enabled"])

    def test_stock_radar_route_rejects_invalid_symbol_before_storage(self):
        app = FastAPI()
        app.include_router(radar_router)

        response = TestClient(app).get("/api/radar/stocks/not-a-code")

        self.assertEqual(response.status_code, 422)

    def test_verified_d2_queue_is_visible_without_claiming_leader_ready(self):
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(None),
            risk_review_repository=FakeRiskReviewRepository(),
        )

        module = service.build_leaders().model_dump(
            mode="json",
            by_alias=True,
        )["module"]

        self.assertEqual(module["state"], "not_ready")
        self.assertEqual(module["reviewQueue"]["status"], "ready")
        self.assertEqual(module["reviewQueue"]["documentCount"], 1919)
        self.assertEqual(module["reviewQueue"]["reviewVersionCount"], 0)
        self.assertFalse(module["reviewQueue"]["formalUsable"])
        self.assertFalse(module["reviewQueue"]["humanApprovalRequired"])
        self.assertEqual(
            module["reviewQueue"]["purpose"],
            "official_announcement_scan",
        )
        self.assertEqual(
            module["reviewQueue"]["semanticCoverageStatus"],
            "partial",
        )
        self.assertIn(
            "关键词发现不等于完整语义审查",
            module["reviewQueue"]["coverageStatement"],
        )
        self.assertIn("official_risk_scan_ready", module["reviewQueue"]["reasonCodes"])
        self.assertNotIn("d8_review_versions_missing", module["reviewQueue"]["reasonCodes"])

        queue = service.build_leader_review_queue(
            limit=25,
            offset=0,
        ).model_dump(mode="json", by_alias=True)
        self.assertEqual(
            queue["schemaVersion"],
            "radar-leader-review-queue-v1",
        )
        self.assertEqual(queue["summary"]["status"], "ready")
        self.assertEqual(queue["limit"], 25)
        self.assertEqual(queue["items"], [])

        detail = service.build_leader_review_document(
            review_batch_id="risk-review-batch-1",
            document_id="cninfo:123",
            candidate_category="regulatory",
        ).model_dump(mode="json", by_alias=True)
        self.assertEqual(
            detail["schemaVersion"],
            "radar-leader-review-document-v1",
        )
        self.assertEqual(detail["item"]["contentStatus"], "not_fetched")
        self.assertEqual(detail["item"]["reviewVersionCount"], 0)
        self.assertFalse(detail["item"]["formalUsable"])

        with self.assertRaises(ValueError):
            service.build_leader_review_document(
                review_batch_id="old-risk-review-batch",
                document_id="cninfo:123",
                candidate_category="regulatory",
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

    def test_real_observation_survives_malformed_formal_snapshot(self):
        snapshot = self.snapshot(
            as_of=NOW - timedelta(seconds=30),
            entries=[self.entry("000001", "preliminary", 95)],
        )
        snapshot.pop("createdAt")
        service = self.service(
            RadarSettings(
                enabled=True,
                shadow_mode=True,
                leader_stage6_enabled=True,
            ),
            leader_repository=FakeLeaderRepository(snapshot),
            leader_observation_loader=lambda: self.observation_result(),
        )

        payload = service.build_leaders().model_dump(
            mode="json",
            by_alias=True,
        )

        self.assertEqual(payload["module"]["state"], "available")
        self.assertEqual(
            payload["module"]["observation"]["items"][0]["symbol"],
            "000725",
        )
        self.assertIn(
            "stage6_snapshot_invalid",
            payload["module"]["reasonCodes"],
        )

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
            ), patch(
                "radar.api.load_latest_leader_observation",
                return_value=LeaderObservationStoreReadResult(
                    status="not_ready",
                    reasons=("leader_observation_snapshot_missing",),
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
            ), patch(
                "radar.api.load_latest_leader_observation",
                return_value=LeaderObservationStoreReadResult(
                    status="not_ready",
                    reasons=("leader_observation_snapshot_missing",),
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
