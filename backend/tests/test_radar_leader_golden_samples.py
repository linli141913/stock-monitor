import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from radar.leader_board import build_leader_board_projection
except ImportError:
    build_leader_board_projection = None

from radar.leader_input_gate import (
    LeaderDimensionEvidence,
    LeaderInputEvidence,
    LeaderSourceEvidence,
    LeaderSourceKind,
)
from radar.leader_repository import LeaderRepository
from radar.leader_scoring import LeaderGateInput, LeaderMetricStatus
from radar.leader_shadow_runner import LeaderShadowRunner
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LEADER_STATE_MACHINE_VERSION,
    LeaderState,
    LeaderStateRecord,
)
from radar.migrations import (
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
)


UTC = timezone.utc
APPLIED_AT = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 27, 1, 45, tzinfo=UTC)
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "radar_leader_stage6_golden_cases.jsonl"
)

SOURCE_KIND_BY_ID = {
    "sector-1": LeaderSourceKind.SECTOR,
    "market-1": LeaderSourceKind.MARKET,
    "quote-1": LeaderSourceKind.QUOTE,
    "business-1": LeaderSourceKind.BUSINESS_EXPOSURE,
    "etf-1": LeaderSourceKind.ETF,
}
SOURCE_BY_FIELD = {
    "industry_strength": "sector-1",
    "market_leadership": "market-1",
    "relative_strength_continuity": "quote-1",
    "liquidity_tradability": "quote-1",
    "business_exposure": "business-1",
    "auxiliary": "etf-1",
}


def load_golden_cases():
    with FIXTURE_PATH.open(encoding="utf-8") as fixture:
        return tuple(
            json.loads(line)
            for line in fixture
            if line.strip()
        )


def complete_gates(overrides=None):
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
    values.update(overrides or {})
    return LeaderGateInput(**values)


def source_evidence(case):
    overrides = case.get("sourceOverrides", {})
    items = []
    for source_id, source_kind in SOURCE_KIND_BY_ID.items():
        source_override = overrides.get(source_id, {})
        source_time = AS_OF + timedelta(
            seconds=source_override.get(
                "sourceTimeOffsetSeconds",
                -10,
            )
        )
        items.append(
            LeaderSourceEvidence(
                source_contract_id=source_id,
                source_kind=source_kind,
                source_name=f"{source_kind.value}-golden-fixture",
                source_time=source_time,
                fetched_at=AS_OF,
                status=LeaderMetricStatus(
                    source_override.get("status", "verified")
                ),
            )
        )
    return tuple(items)


def input_evidence(case):
    dimensions = tuple(
        LeaderDimensionEvidence(
            field_name=field_name,
            score=score,
            source_contract_id=SOURCE_BY_FIELD[field_name],
        )
        for field_name, score in case["scores"].items()
    )
    return LeaderInputEvidence(
        symbol=case["symbol"],
        name=case["name"],
        as_of=AS_OF,
        dimensions=dimensions,
        gates=complete_gates(case.get("gateOverrides")),
        sources=source_evidence(case),
        industry_code="C39",
        industry_name="计算机、通信和其他电子设备制造业",
        business_exposure_source_contract_id=(
            "business-1"
            if case.get("businessExposureLinked", True)
            else None
        ),
        consecutive_signal_periods=case.get("consecutive", 1),
        evidence={
            "fixtureId": case["id"],
            **case.get("evidence", {}),
        },
    )


def previous_state(case):
    previous = case.get("previous")
    if previous is None:
        return None
    age = previous["age"]
    return LeaderStateRecord(
        symbol=case["symbol"],
        state=LeaderState(previous["state"]),
        state_age_periods=age,
        rule_version=LEADER_STATE_MACHINE_VERSION,
        state_since=AS_OF - timedelta(minutes=max(age, 1)),
        last_evaluated_at=AS_OF - timedelta(minutes=1),
    )


class LeaderGoldenSampleTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.repository = LeaderRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )
        self.runner = LeaderShadowRunner(
            self.repository,
            clock=lambda: APPLIED_AT,
        )

    def tearDown(self):
        self.connection.close()

    def insert_run(self, radar_run_id):
        as_of_text = AS_OF.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES (?, ?, 'succeeded', 1, ?, ?)
            """,
            (radar_run_id, as_of_text, as_of_text, as_of_text),
        )
        self.connection.commit()

    def test_golden_cases_cover_state_and_data_boundaries(self):
        cases = load_golden_cases()
        self.assertEqual(len(cases), 10)
        self.insert_run("stage6f-golden")
        previous_states = {
            case["symbol"]: record
            for case in cases
            if (record := previous_state(case)) is not None
        }

        result = self.runner.run_evidence_once(
            "stage6f-golden",
            AS_OF,
            [input_evidence(case) for case in cases],
            previous_states=previous_states,
        )

        self.assertEqual(result.eligible_count, 10)
        self.assertEqual(result.preliminary_count, 3)
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(result.confirmed_count, 1)
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(result.blocked_count, 3)
        self.assertEqual(result.quality, "degraded")
        self.assertAlmostEqual(result.coverage, 0.7)
        self.assertFalse(result.formal_usable)

        decisions = {
            item.symbol: item
            for item in result.decisions
        }
        audits = {
            item.symbol: item
            for item in result.audits
        }
        self.assertEqual(len(decisions), len(cases))
        for case in cases:
            expected = case["expected"]
            decision = decisions[case["symbol"]]
            audit = audits[case["symbol"]]
            self.assertEqual(
                decision.to_state.value,
                expected["state"],
                case["id"],
            )
            self.assertEqual(
                decision.action.value,
                expected["action"],
                case["id"],
            )
            self.assertEqual(
                audit.data_status.value,
                expected["dataStatus"],
                case["id"],
            )
            self.assertEqual(
                audit.first_rejection_reason
                or decision.first_rejection_reason,
                expected["firstRejectionReason"],
                case["id"],
            )

        self.assertEqual(audits["000009"].score, 95)
        self.assertEqual(audits["000009"].data_status.value, "healthy")
        self.assertEqual(audits["000010"].score, 95)
        self.assertEqual(audits["000010"].data_status.value, "missing")

        snapshot = self.repository.get_candidate_snapshot(
            "stage6f-golden"
        )
        entries = {
            item["symbol"]: item
            for item in snapshot["entries"]
        }
        self.assertEqual(
            entries["000006"]["evidence"]["aiSuggestedState"],
            "confirmed",
        )
        self.assertEqual(entries["000006"]["state"], "preliminary")
        self.assertEqual(entries["000010"]["state"], "out")
        self.assertTrue(
            all(not item["formalUsable"] for item in entries.values())
        )

    def test_board_projection_caps_each_state_without_changing_audit(self):
        self.assertIsNotNone(
            build_leader_board_projection,
            "阶段6F榜单投影尚未实现",
        )
        self.insert_run("stage6f-cap")
        cases = []
        for group, previous in (
            (1, None),
            (2, {"state": "preliminary", "age": 1}),
            (3, {"state": "candidate", "age": 2}),
        ):
            for index, auxiliary_score in enumerate(
                range(5, -1, -1),
                1,
            ):
                case = {
                    "id": f"capacity-{group}-{index}",
                    "symbol": f"{group * 100 + index:06d}",
                    "name": f"容量样本-{group}-{index}",
                    "scores": {
                        "industry_strength": 25,
                        "market_leadership": 25,
                        "relative_strength_continuity": 20,
                        "liquidity_tradability": 15,
                        "business_exposure": 10,
                        "auxiliary": auxiliary_score,
                    },
                    "consecutive": 2,
                }
                if previous is not None:
                    case["previous"] = previous
                cases.append(case)
        previous_states = {
            case["symbol"]: record
            for case in cases
            if (record := previous_state(case)) is not None
        }

        result = self.runner.run_evidence_once(
            "stage6f-cap",
            AS_OF,
            [input_evidence(case) for case in cases],
            previous_states=previous_states,
        )
        snapshot = self.repository.get_candidate_snapshot("stage6f-cap")
        board = build_leader_board_projection(snapshot)

        self.assertEqual(result.preliminary_count, 6)
        self.assertEqual(result.candidate_count, 6)
        self.assertEqual(result.confirmed_count, 6)
        self.assertEqual(len(snapshot["entries"]), 18)
        for group, state in (
            (1, LeaderState.PRELIMINARY),
            (2, LeaderState.CANDIDATE),
            (3, LeaderState.CONFIRMED),
        ):
            selected = board.entries_for(state)
            self.assertEqual(len(selected), 5)
            self.assertEqual(
                [item.symbol for item in selected],
                [
                    f"{group * 100 + index:06d}"
                    for index in range(1, 6)
                ],
            )
            self.assertEqual(board.overflow_count(state), 1)
        self.assertFalse(board.formal_usable)
        for case in cases:
            self.assertEqual(
                len(self.repository.list_state_history(case["symbol"])),
                1,
            )

    def test_empty_board_is_preserved_without_filling_slots(self):
        self.assertIsNotNone(
            build_leader_board_projection,
            "阶段6F榜单投影尚未实现",
        )
        self.insert_run("stage6f-empty")

        result = self.runner.run_evidence_once(
            "stage6f-empty",
            AS_OF,
            [],
        )
        snapshot = self.repository.get_candidate_snapshot("stage6f-empty")
        board = build_leader_board_projection(snapshot)

        self.assertEqual(result.quality, "empty")
        for state in (
            LeaderState.PRELIMINARY,
            LeaderState.CANDIDATE,
            LeaderState.CONFIRMED,
        ):
            self.assertEqual(board.entries_for(state), ())
            self.assertEqual(board.overflow_count(state), 0)
        self.assertFalse(board.formal_usable)


if __name__ == "__main__":
    unittest.main()
