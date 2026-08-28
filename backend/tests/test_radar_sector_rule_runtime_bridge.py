import unittest
from dataclasses import replace
from datetime import timedelta

from radar.contracts import IndustryHistoryStatus
from radar.leader_formal_research_batch import (
    LeaderFormalResearchBatchStatus,
    provide_leader_formal_research_batch,
)
from radar.sector_rule_runtime_bridge import (
    SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID,
    SectorRuleRuntimeBridgeStatus,
    SectorRuleRuntimeSourceBatch,
    build_sector_rule_runtime_bridge,
)
from tests import test_radar_leader_formal_research_batch as formal_helpers
from tests import test_radar_leader_research_source_admission as plan_helpers
from tests import test_radar_sector_rule_readiness as sector_helpers


class SectorRuleRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        helper = plan_helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.context = helper.context
        self.plan = helper.plan

    def previous_trading_dates(self, count):
        values = []
        current = self.plan.as_of.date() - timedelta(days=1)
        while len(values) < count:
            if current.weekday() < 5:
                values.append(current)
            current -= timedelta(days=1)
        return tuple(reversed(values))

    def source_batch(self, *, with_gaps=False):
        base_feature_batch = sector_helpers.feature_batch()
        sectors = (
            base_feature_batch.sectors[0].model_copy(update={
                "division_code": self.plan.items[0].industry_code,
                "division_name": "候选行业",
            }),
            *base_feature_batch.sectors[1:],
        )
        feature_batch = base_feature_batch.model_copy(update={
            "radar_run_id": self.plan.radar_run_id,
            "quote_batch_id": self.plan.quote_batch_id,
            "as_of": self.plan.as_of,
            "source_time": self.plan.as_of - timedelta(seconds=2),
            "fetched_at": self.plan.as_of + timedelta(seconds=2),
            "sectors": sectors,
        })
        history = sector_helpers.history_evidence()
        same_minute_dates = self.previous_trading_dates(20)
        persistence_dates = same_minute_dates[-5:]
        history = replace(
            history,
            radar_run_id=self.plan.radar_run_id,
            as_of=self.plan.as_of,
            rows=tuple(
                replace(
                    row,
                    division_code=(
                        self.plan.items[0].industry_code
                        if index == 0
                        else row.division_code
                    ),
                    same_minute_trading_dates=same_minute_dates,
                    persistence_trading_dates=persistence_dates,
                )
                for index, row in enumerate(history.rows)
            ),
        )
        market = replace(
            sector_helpers.market_baseline(),
            radar_run_id=self.plan.radar_run_id,
            as_of=self.plan.as_of,
        )
        threshold = replace(
            sector_helpers.threshold_approval(),
            approved_at=self.plan.as_of - timedelta(days=1),
        )
        return SectorRuleRuntimeSourceBatch(
            candidate_plan_id=self.plan.candidate_set_id,
            radar_run_id=self.plan.radar_run_id,
            quote_batch_id=self.plan.quote_batch_id,
            industry_release_id=self.plan.items[0].industry_release_id,
            as_of=self.plan.as_of,
            feature_batch=feature_batch,
            classification_release=(
                sector_helpers.release(
                    history_status=(
                        IndustryHistoryStatus.RETROSPECTIVE_UNVERIFIED
                    ),
                )
                if with_gaps
                else sector_helpers.release()
            ),
            history_evidence=None if with_gaps else history,
            market_baseline_evidence=None if with_gaps else market,
            threshold_approval_evidence=None if with_gaps else threshold,
        )

    def build(self, source_batch=None):
        return build_sector_rule_runtime_bridge(
            self.context,
            source_batch=source_batch,
        )

    def test_complete_versioned_evidence_returns_ready_contract(self):
        result = self.build(self.source_batch())

        self.assertEqual(result.status, SectorRuleRuntimeBridgeStatus.READY)
        self.assertIsNotNone(result.readiness_result)
        self.assertEqual(result.readiness_result.status.value, "ready")
        self.assertEqual(len(result.readiness_result.items), 9)
        self.assertEqual(result.reasons, ())
        self.assertEqual(
            result.to_evidence()["contractId"],
            SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID,
        )
        self.assertNotIn("featureBatch", result.to_evidence())
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_unrelated_classification_gaps_do_not_block_bound_candidate_sector(self):
        source_batch = self.source_batch()
        candidate_sector = next(
            sector
            for sector in source_batch.feature_batch.sectors
            if sector.division_code == self.plan.items[0].industry_code
        )
        self.assertTrue(candidate_sector.shadow_usable)
        degraded_full_market_batch = source_batch.feature_batch.model_copy(
            update={
                "classification_mapping_coverage": 0.98,
                "unconfirmed_stock_count": 89,
                "shadow_usable": False,
                "reasons": (
                    "classification_source_degraded",
                    "classification_mapping_incomplete",
                    "sector_features_incomplete",
                    "formal_use_not_approved",
                ),
            }
        )

        result = self.build(replace(
            source_batch,
            feature_batch=degraded_full_market_batch,
        ))

        self.assertEqual(result.status, SectorRuleRuntimeBridgeStatus.READY)
        self.assertEqual(result.reasons, ())

    def test_missing_provider_stays_missing_without_placeholder_result(self):
        result = self.build()

        self.assertEqual(result.status, SectorRuleRuntimeBridgeStatus.MISSING)
        self.assertEqual(
            result.reasons,
            ("sector_rule_runtime_bridge_source_missing",),
        )
        self.assertIsNone(result.readiness_result)
        self.assertIsNone(result.sector_rule_admission_value)

    def test_valid_source_with_known_gaps_preserves_detailed_missing_reasons(self):
        result = self.build(self.source_batch(with_gaps=True))

        self.assertEqual(result.status, SectorRuleRuntimeBridgeStatus.MISSING)
        self.assertIsNotNone(result.readiness_result)
        self.assertIn(
            "sector_classification_history_unverified",
            result.reasons,
        )
        self.assertIn("sector_market_baseline_missing", result.reasons)
        self.assertIn("sector_history_coverage_missing", result.reasons)
        self.assertIn(
            "sector_state_threshold_approval_missing",
            result.reasons,
        )
        self.assertIs(
            result.sector_rule_admission_value,
            result.readiness_result,
        )

    def test_cross_run_or_quote_batch_is_source_unverified(self):
        source_batch = self.source_batch()
        cases = (
            replace(source_batch, radar_run_id="other-run"),
            replace(source_batch, quote_batch_id="other-quote-batch"),
            replace(source_batch, history_evidence="invalid-history"),
        )

        for malformed in cases:
            with self.subTest(run_id=malformed.radar_run_id):
                result = self.build(malformed)
                self.assertEqual(
                    result.reasons,
                    ("sector_rule_runtime_bridge_source_unverified",),
                )
                self.assertIsNone(result.readiness_result)
                self.assertIs(
                    result.sector_rule_admission_value,
                    malformed,
                )

    def test_candidate_industry_must_exist_in_current_feature_batch(self):
        source_batch = self.source_batch()
        original_features = sector_helpers.feature_batch().model_copy(update={
            "radar_run_id": self.plan.radar_run_id,
            "quote_batch_id": self.plan.quote_batch_id,
            "as_of": self.plan.as_of,
            "source_time": self.plan.as_of - timedelta(seconds=2),
            "fetched_at": self.plan.as_of + timedelta(seconds=2),
        })
        result = self.build(replace(
            source_batch,
            feature_batch=original_features,
        ))

        self.assertEqual(
            result.reasons,
            ("sector_rule_runtime_bridge_source_unverified",),
        )
        self.assertIsNone(result.readiness_result)

    def test_ready_result_reaches_existing_formal_research_batch(self):
        bridge = self.build(self.source_batch())
        formal = formal_helpers.LeaderFormalResearchBatchTests(
            methodName="test_complete_same_run_evidence_returns_ready_research_batch"
        )
        formal.setUp()
        self.assertEqual(
            formal.plan.candidate_set_id,
            self.plan.candidate_set_id,
        )
        result = provide_leader_formal_research_batch(
            candidate_plan=self.plan,
            sector_rule_readiness=bridge.sector_rule_admission_value,
            research_readiness_batch=formal.readiness_batch(),
        )

        self.assertEqual(result.status, LeaderFormalResearchBatchStatus.READY)
        self.assertEqual(
            result.sector_rule_version,
            bridge.readiness_result.rule_version,
        )
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)


if __name__ == "__main__":
    unittest.main()
