import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_input_gate import (
    LeaderDimensionEvidence,
    LeaderInputEvidence,
    LeaderInputGatePolicy,
    LeaderSourceEvidence,
    LeaderSourceKind,
    build_leader_input_gate,
)
from radar.leader_repository import LeaderRepository
from radar.leader_scoring import (
    LeaderGateInput,
    LeaderMetricStatus,
    build_leader_scoring_audit,
)
from radar.leader_shadow_runner import LeaderShadowRunner
from radar.leader_state_machine import BusinessExposureStatus
from radar.migrations import (
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
)


UTC = timezone.utc
APPLIED_AT = datetime(2026, 7, 25, 5, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 27, 1, 45, tzinfo=UTC)
SOURCE_TIME = AS_OF - timedelta(seconds=10)


def complete_gates(**overrides):
    values = {
        "industry_gate_passed": True,
        "stock_gate_passed": True,
        "market_leadership_passed": True,
        "industry_contribution_passed": True,
        "liquidity_passed": True,
        "tradability_passed": True,
        "continuity_passed": True,
        "recovery_passed": True,
        "risk_filter_passed": True,
        "business_exposure_status": BusinessExposureStatus.VERIFIED,
    }
    values.update(overrides)
    return LeaderGateInput(**values)


def source(
    source_contract_id,
    kind,
    *,
    source_time=SOURCE_TIME,
    status=LeaderMetricStatus.VERIFIED,
    row_coverage=1.0,
    required_field_coverage=1.0,
):
    return LeaderSourceEvidence(
        source_contract_id=source_contract_id,
        source_kind=kind,
        source_name=f"{kind.value}-fixture",
        source_time=source_time,
        fetched_at=AS_OF,
        status=status,
        row_coverage=row_coverage,
        required_field_coverage=required_field_coverage,
    )


SOURCE_BY_FIELD = {
    "industry_strength": "sector-1",
    "market_leadership": "market-1",
    "relative_strength_continuity": "quote-1",
    "liquidity_tradability": "quote-1",
    "business_exposure": "business-1",
    "auxiliary": "etf-1",
}


def dimensions(**overrides):
    values = {
        "industry_strength": 25,
        "market_leadership": 25,
        "relative_strength_continuity": 20,
        "liquidity_tradability": 15,
        "business_exposure": 10,
        "auxiliary": 0,
    }
    values.update(overrides)
    return tuple(
        LeaderDimensionEvidence(
            field_name=field_name,
            score=score,
            source_contract_id=SOURCE_BY_FIELD[field_name],
        )
        for field_name, score in values.items()
    )


def sources(**overrides):
    values = {
        "market-1": source("market-1", LeaderSourceKind.MARKET),
        "sector-1": source("sector-1", LeaderSourceKind.SECTOR),
        "quote-1": source("quote-1", LeaderSourceKind.QUOTE),
        "etf-1": source("etf-1", LeaderSourceKind.ETF),
        "business-1": source(
            "business-1",
            LeaderSourceKind.BUSINESS_EXPOSURE,
        ),
    }
    values.update(overrides)
    return tuple(values.values())


def evidence_bundle(**overrides):
    values = {
        "symbol": "000001",
        "name": "测试证券",
        "as_of": AS_OF,
        "dimensions": dimensions(),
        "gates": complete_gates(),
        "sources": sources(),
        "industry_code": "C39",
        "industry_name": "计算机、通信和其他电子设备制造业",
        "business_exposure_source_contract_id": "business-1",
        "consecutive_signal_periods": 2,
        "evidence": {"fixture": True},
        "invalidation": {},
    }
    values.update(overrides)
    return LeaderInputEvidence(**values)


class LeaderInputGateTests(unittest.TestCase):
    def test_verified_sources_build_ready_input_and_preserve_real_zero(self):
        result = build_leader_input_gate(evidence_bundle())
        audit = build_leader_scoring_audit(result.scoring_input)

        self.assertTrue(result.input_ready)
        self.assertEqual(result.reasons, ())
        self.assertEqual(audit.score, 95)
        self.assertEqual(
            result.scoring_input.gates.business_exposure_status,
            BusinessExposureStatus.VERIFIED,
        )
        auxiliary = [
            item for item in result.scoring_input.dimensions
            if item.field_name == "auxiliary"
        ][0]
        self.assertEqual(auxiliary.score, 0)
        self.assertEqual(auxiliary.status, LeaderMetricStatus.VERIFIED)
        self.assertIn("sourceContracts", result.evidence)

    def test_stale_source_removes_dimension_score_without_silent_zero(self):
        stale_quote = source(
            "quote-1",
            LeaderSourceKind.QUOTE,
            source_time=AS_OF - timedelta(seconds=200),
        )
        result = build_leader_input_gate(
            evidence_bundle(sources=sources(**{"quote-1": stale_quote}))
        )
        audit = build_leader_scoring_audit(result.scoring_input)

        self.assertFalse(result.input_ready)
        self.assertIn("source_age_over_limit:quote-1", result.reasons)
        self.assertEqual(audit.data_status.value, "stale")
        continuity = [
            item for item in result.scoring_input.dimensions
            if item.field_name == "relative_strength_continuity"
        ][0]
        self.assertIsNone(continuity.score)
        self.assertEqual(continuity.status, LeaderMetricStatus.STALE)

    def test_low_coverage_marks_source_unverified(self):
        weak_market = source(
            "market-1",
            LeaderSourceKind.MARKET,
            row_coverage=0.90,
        )
        result = build_leader_input_gate(
            evidence_bundle(sources=sources(**{"market-1": weak_market}))
        )

        self.assertFalse(result.input_ready)
        self.assertIn("row_coverage_below_limit:market-1", result.reasons)
        leadership = [
            item for item in result.scoring_input.dimensions
            if item.field_name == "market_leadership"
        ][0]
        self.assertIsNone(leadership.score)
        self.assertEqual(
            leadership.status,
            LeaderMetricStatus.SOURCE_UNVERIFIED,
        )

    def test_field_coverage_marks_source_unverified(self):
        weak_sector = source(
            "sector-1",
            LeaderSourceKind.SECTOR,
            required_field_coverage=0.80,
        )
        result = build_leader_input_gate(
            evidence_bundle(sources=sources(**{"sector-1": weak_sector}))
        )

        self.assertFalse(result.input_ready)
        self.assertIn("field_coverage_below_limit:sector-1", result.reasons)
        industry_strength = [
            item for item in result.scoring_input.dimensions
            if item.field_name == "industry_strength"
        ][0]
        self.assertIsNone(industry_strength.score)
        self.assertEqual(
            industry_strength.status,
            LeaderMetricStatus.SOURCE_UNVERIFIED,
        )

    def test_source_failure_has_highest_metric_severity(self):
        failed_market = source(
            "market-1",
            LeaderSourceKind.MARKET,
            status=LeaderMetricStatus.SOURCE_FAILED,
        )
        result = build_leader_input_gate(
            evidence_bundle(sources=sources(**{"market-1": failed_market}))
        )
        audit = build_leader_scoring_audit(result.scoring_input)

        self.assertFalse(result.input_ready)
        self.assertEqual(audit.data_status.value, "source_failed")
        self.assertIn("source_failed:market-1", result.reasons)

    def test_verified_business_exposure_without_source_is_downgraded(self):
        result = build_leader_input_gate(
            evidence_bundle(business_exposure_source_contract_id=None)
        )
        audit = build_leader_scoring_audit(result.scoring_input)

        self.assertFalse(result.input_ready)
        self.assertIn("business_exposure_source_unverified", result.reasons)
        self.assertEqual(
            result.scoring_input.gates.business_exposure_status,
            BusinessExposureStatus.UNCONFIRMED,
        )
        self.assertEqual(
            audit.first_rejection_reason,
            "business_exposure_unconfirmed",
        )

    def test_missing_dimension_and_source_identity_are_not_invented(self):
        result = build_leader_input_gate(
            evidence_bundle(
                dimensions=tuple(
                    item for item in dimensions()
                    if item.field_name != "business_exposure"
                )
            )
        )

        self.assertFalse(result.input_ready)
        self.assertIn("dimension_missing:business_exposure", result.reasons)
        business = [
            item for item in result.scoring_input.dimensions
            if item.field_name == "business_exposure"
        ][0]
        self.assertIsNone(business.score)
        self.assertEqual(business.status, LeaderMetricStatus.MISSING)

    def test_duplicate_source_contracts_are_rejected(self):
        with self.assertRaises(ValueError):
            evidence_bundle(sources=(
                source("market-1", LeaderSourceKind.MARKET),
                source("market-1", LeaderSourceKind.MARKET),
            ))

    def test_run_evidence_once_persists_gated_shadow_result(self):
        connection = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
                clock=lambda: APPLIED_AT,
            )
            connection.execute(
                """
                INSERT INTO radar_runs (
                    radar_run_id, as_of, status, shadow_mode,
                    started_at, created_at
                ) VALUES (?, ?, 'succeeded', 1, ?, ?)
                """,
                (
                    "run-6e",
                    AS_OF.isoformat(),
                    AS_OF.isoformat(),
                    AS_OF.isoformat(),
                ),
            )
            connection.commit()
            repository = LeaderRepository(
                connection,
                clock=lambda: APPLIED_AT,
            )
            runner = LeaderShadowRunner(
                repository,
                clock=lambda: APPLIED_AT,
            )

            result = runner.run_evidence_once(
                "run-6e",
                AS_OF,
                [evidence_bundle(business_exposure_source_contract_id=None)],
                input_gate_policy=LeaderInputGatePolicy(),
            )

            self.assertEqual(result.preliminary_count, 1)
            self.assertEqual(
                result.audits[0].first_rejection_reason,
                "business_exposure_unconfirmed",
            )
            loaded = repository.get_candidate_snapshot("run-6e")
            entry = loaded["entries"][0]
            self.assertEqual(entry["state"], "preliminary")
            self.assertEqual(
                entry["firstRejectionReason"],
                "business_exposure_unconfirmed",
            )
            self.assertIn(
                "businessExposureStatus",
                entry["invalidation"],
            )
            self.assertEqual(
                len(repository.list_state_history("000001")),
                1,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
