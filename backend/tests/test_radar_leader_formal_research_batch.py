import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

from radar.leader_formal_research_batch import (
    LEADER_FORMAL_RESEARCH_BATCH_CONTRACT_ID,
    LeaderFormalResearchBatchStatus,
    provide_leader_formal_research_batch,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_audit_batch import (
    LeaderResearchReadinessAuditBatchEntry,
    LeaderResearchReadinessAuditBatchInput,
    build_leader_research_readiness_audit_batch,
)
from radar.sector_rule_readiness import (
    SECTOR_RULE_VERSION,
    SectorRuleReadinessItem,
    SectorRuleReadinessResult,
    SectorRuleReadinessStatus,
)
from tests import (
    test_radar_leader_research_readiness_audit as audit_helpers,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as plan_helpers,
)


SECTOR_ITEM_KEYS = (
    "classification_history",
    "classification_mapping",
    "current_sector_features",
    "market_baseline",
    "history_coverage",
    "same_minute_turnover_history",
    "persistence_history",
    "comparable_industries",
    "threshold_approval",
)

RESEARCH_ITEM_KEYS = (
    "candidate_discovery",
    "sector_formal_gate",
    "security_tradability",
    "cross_section",
    "liquidity",
    "history_continuity",
    "business_catalyst",
    "risk_evidence",
)


class LeaderFormalResearchBatchTests(unittest.TestCase):
    def setUp(self):
        self.plan_fixture = (
            plan_helpers.LeaderResearchSinglePassOrchestrationTests(
                methodName=(
                    "test_candidate_plan_is_frozen_ordered_and_"
                    "matches_existing_runtime"
                )
            )
        )
        self.plan_fixture.setUp()
        self.plan = self.plan_fixture.candidate_plan()

    def sector_readiness(self, *, status=SectorRuleReadinessStatus.READY):
        reasons = (
            ()
            if status == SectorRuleReadinessStatus.READY
            else ("sector_state_threshold_approval_missing",)
        )
        items = tuple(
            SectorRuleReadinessItem(
                key=key,
                status=(
                    status
                    if key == "threshold_approval"
                    else SectorRuleReadinessStatus.READY
                ),
                reasons=(
                    reasons
                    if key == "threshold_approval"
                    else ()
                ),
            )
            for key in SECTOR_ITEM_KEYS
        )
        return SectorRuleReadinessResult(
            status=status,
            radar_run_id=self.plan.radar_run_id,
            rule_version=SECTOR_RULE_VERSION,
            as_of=self.plan.as_of,
            items=items,
            reasons=reasons,
        )

    def readiness_batch(self, *, missing_history_index=None):
        entries = []
        for index, item in enumerate(self.plan.items):
            history = audit_helpers.history_features()
            if index == missing_history_index:
                history = audit_helpers.history_features(
                    status=ResearchFeatureStatus.MISSING,
                    reasons=("history_source_missing",),
                )
            risk_projection = audit_helpers.risk_projection(
                symbol=item.symbol,
                as_of=self.plan.as_of,
            )
            audit_input = audit_helpers.audit_input(
                symbol=item.symbol,
                as_of=self.plan.as_of,
                history_features=history,
                risk_projection_item=replace(
                    audit_helpers.risk_item(
                        symbol=item.symbol,
                        projection=risk_projection,
                    ),
                    index=index,
                ),
            )
            entries.append(LeaderResearchReadinessAuditBatchEntry(
                symbol=item.symbol,
                audit_input=audit_input,
            ))
        result = build_leader_research_readiness_audit_batch(
            LeaderResearchReadinessAuditBatchInput(
                as_of=self.plan.as_of,
                entries=tuple(entries),
            )
        )
        return result

    def provide(self, **overrides):
        values = {
            "candidate_plan": self.plan,
            "sector_rule_readiness": self.sector_readiness(),
            "research_readiness_batch": self.readiness_batch(),
        }
        values.update(overrides)
        return provide_leader_formal_research_batch(**values)

    def test_complete_same_run_evidence_returns_ready_research_batch(self):
        result = self.provide()

        self.assertEqual(
            result.status,
            LeaderFormalResearchBatchStatus.READY,
        )
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(result.radar_run_id, self.plan.radar_run_id)
        self.assertEqual(result.as_of, self.plan.as_of)
        self.assertEqual(result.candidate_plan_id, self.plan.candidate_set_id)
        self.assertTrue(all(
            item.status == LeaderFormalResearchBatchStatus.READY
            for item in result.items
        ))
        self.assertTrue(all(
            tuple(value.key for value in item.items)
            == RESEARCH_ITEM_KEYS
            for item in result.items
        ))
        self.assertTrue(all(
            item.first_veto_reason
            == "leader_formal_industry_gate_unavailable"
            for item in result.items
        ))
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["contractId"],
            LEADER_FORMAL_RESEARCH_BATCH_CONTRACT_ID,
        )
        self.assertNotIn("researchScore", str(evidence))
        self.assertNotIn("leaderState", str(evidence))

    def test_one_candidate_history_gap_stays_local_and_batch_missing(self):
        result = self.provide(
            research_readiness_batch=self.readiness_batch(
                missing_history_index=1
            )
        )

        self.assertEqual(
            result.status,
            LeaderFormalResearchBatchStatus.MISSING,
        )
        self.assertEqual(result.ready_count, self.plan.candidate_count - 1)
        self.assertEqual(result.missing_count, 1)
        missing = result.items[1]
        self.assertEqual(
            missing.status,
            LeaderFormalResearchBatchStatus.MISSING,
        )
        self.assertEqual(
            missing.item("history_continuity").reasons,
            (
                "leader_history_evidence_unavailable",
                "history_source_missing",
            ),
        )
        self.assertEqual(
            missing.first_missing_reason,
            "leader_history_evidence_unavailable",
        )
        self.assertEqual(
            result.reasons,
            ("leader_formal_research_batch_missing",),
        )

    def test_sector_gate_missing_applies_to_every_candidate_without_state(self):
        result = self.provide(
            sector_rule_readiness=self.sector_readiness(
                status=SectorRuleReadinessStatus.MISSING
            )
        )

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertTrue(all(
            item.item("sector_formal_gate").reasons
            == ("sector_state_threshold_approval_missing",)
            for item in result.items
        ))
        self.assertTrue(all(
            item.first_missing_reason
            == "sector_state_threshold_approval_missing"
            for item in result.items
        ))

    def test_cross_run_or_as_of_evidence_never_enters_batch(self):
        cases = (
            {
                "sector_rule_readiness": replace(
                    self.sector_readiness(),
                    radar_run_id="other-run",
                ),
            },
            {
                "research_readiness_batch": replace(
                    self.readiness_batch(),
                    as_of=self.plan.as_of + timedelta(seconds=1),
                ),
            },
        )

        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                result = self.provide(**changes)
                self.assertEqual(
                    result.status,
                    LeaderFormalResearchBatchStatus.MISSING,
                )
                self.assertEqual(result.ready_count, 0)
                self.assertEqual(
                    result.missing_count,
                    self.plan.candidate_count,
                )
                self.assertTrue(result.reasons)

    def test_d2_queue_or_forged_result_cannot_be_relabelled_as_research(self):
        cases = (
            {"research_readiness_batch": {"status": "ready"}},
            {
                "sector_rule_readiness": replace(
                    self.sector_readiness(),
                    contract_id="caller-declared-ready",
                ),
            },
            {
                "candidate_plan": replace(
                    self.plan,
                    contract_id="d2-review-queue-v1",
                ),
            },
        )

        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                result = self.provide(**changes)
                self.assertEqual(
                    result.status,
                    LeaderFormalResearchBatchStatus.MISSING,
                )
                self.assertEqual(result.ready_count, 0)
                self.assertFalse(result.formal_usable)
                self.assertFalse(result.state_transition_allowed)

    def test_contracts_are_frozen_and_evidence_hides_raw_audits(self):
        result = self.provide()

        with self.assertRaises(FrozenInstanceError):
            result.status = LeaderFormalResearchBatchStatus.MISSING
        with self.assertRaises(FrozenInstanceError):
            result.items[0].symbol = "000999"
        text = repr(result)
        evidence = str(result.to_evidence())
        self.assertNotIn("LeaderResearchReadinessAuditResult", text)
        self.assertNotIn("LeaderRuntimeCandidatePlan", text)
        self.assertNotIn("adjusted_points", evidence)
        self.assertNotIn("decision_summary", evidence)


if __name__ == "__main__":
    unittest.main()
