import unittest
from datetime import datetime, timezone

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfMetricState,
    EtfProductMasterRecord,
    EtfRankingInputAudit,
)
from radar.etf_stage5_policy import (
    DEFAULT_ETF_RETENTION_POLICY,
    DEFAULT_ETF_RULE_POLICY,
    REQUIRED_RANKING_FIELDS,
    EtfFormalGateEvidence,
    EtfLowFrequencyReadiness,
    EtfRulePolicy,
    evaluate_etf_formal_gate,
    evaluate_low_frequency_readiness,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)


class EtfStage5PolicyTests(unittest.TestCase):
    @staticmethod
    def product(
        *,
        management_style=EtfManagementStyle.PASSIVE_INDEX,
        asset_class=EtfAssetClass.DOMESTIC_EQUITY,
    ):
        return EtfProductMasterRecord(
            symbol="159915",
            officialName="创业板ETF",
            exchange="szse",
            productType="etf",
            managementStyle=management_style,
            assetClass=asset_class,
            targetIndexName="创业板指数",
            classificationMappingVersion="test-v1",
            source="fixture",
            fetchedAt=AS_OF,
        )

    @staticmethod
    def ranking_input(*, formal_ready):
        states = {
            field_name: EtfMetricState.VERIFIED
            for field_name in REQUIRED_RANKING_FIELDS
        }
        values = {
            "fundSize": 10.0,
            "averageTurnover20d": 20.0,
            "trackingDifference": 0.0,
            "trackingError": 0.01,
            "indexCorrelation": 0.99,
        }
        return EtfRankingInputAudit(
            symbol="159915",
            asOf=AS_OF,
            fetchedAt=AS_OF,
            metricValues=values,
            fieldStates=states,
            rankableFields=REQUIRED_RANKING_FIELDS,
            excludedFields=(),
            formalReady=formal_ready,
            reasons=(
                ()
                if formal_ready
                else ("ranking_rule_not_enabled",)
            ),
        )

    def evidence(self, **overrides):
        values = {
            "product": self.product(),
            "lifecycle_active": True,
            "index_relation_ready": True,
            "methodology_ready": True,
            "constituent_set_ready": True,
            "industry_exposure_ready": True,
            "industry_mapping_coverage": 1.0,
            "ranking_input": self.ranking_input(formal_ready=True),
        }
        values.update(overrides)
        return EtfFormalGateEvidence(**values)

    def test_default_rule_is_explicitly_disabled_without_invented_weights(self):
        self.assertFalse(DEFAULT_ETF_RULE_POLICY.ranking_enabled)
        self.assertEqual(DEFAULT_ETF_RULE_POLICY.weights, ())
        self.assertEqual(DEFAULT_ETF_RULE_POLICY.thresholds, ())
        self.assertEqual(
            DEFAULT_ETF_RULE_POLICY.required_fields,
            REQUIRED_RANKING_FIELDS,
        )

    def test_retention_freezes_60_day_shadow_detail_and_long_term_summaries(self):
        policy = DEFAULT_ETF_RETENTION_POLICY

        self.assertEqual(policy.intraday_detail_trading_days, 60)
        self.assertEqual(policy.minimum_shadow_trading_days, 20)
        self.assertTrue(policy.keep_daily_facts)
        self.assertTrue(policy.keep_candidate_summaries)
        self.assertFalse(policy.keep_raw_upstream_payloads)
        self.assertFalse(policy.automatic_cleanup_enabled)

    def test_enabled_rule_requires_complete_normalized_weights_and_thresholds(self):
        with self.assertRaises(ValueError):
            EtfRulePolicy(
                ranking_enabled=True,
                disabled_reasons=(),
            )
        with self.assertRaises(ValueError):
            EtfRulePolicy(
                ranking_enabled=True,
                weights=(("fundSize", 1.0),),
                thresholds=(("minimumScore", 0.5),),
                disabled_reasons=(),
            )

        policy = EtfRulePolicy(
            ranking_enabled=True,
            weights=tuple(
                (field_name, 1 / len(REQUIRED_RANKING_FIELDS))
                for field_name in REQUIRED_RANKING_FIELDS
            ),
            thresholds=(("minimumScore", 0.5),),
            disabled_reasons=(),
        )
        self.assertTrue(policy.ranking_enabled)

    def test_default_rule_blocks_formal_candidate_even_when_fixture_is_complete(self):
        decision = evaluate_etf_formal_gate(self.evidence())

        self.assertFalse(decision.formal_ready)
        self.assertIn("etf_rule_not_frozen", decision.reasons)
        self.assertIn(
            "formal_source_inputs_incomplete",
            decision.reasons,
        )

    def test_gate_reports_each_real_missing_or_unverified_dimension(self):
        evidence = self.evidence(
            product=self.product(
                management_style=EtfManagementStyle.UNKNOWN,
                asset_class=EtfAssetClass.UNKNOWN,
            ),
            lifecycle_active=False,
            index_relation_ready=False,
            methodology_ready=False,
            constituent_set_ready=False,
            industry_exposure_ready=False,
            industry_mapping_coverage=0.8,
            ranking_input=self.ranking_input(formal_ready=False),
        )

        decision = evaluate_etf_formal_gate(evidence)

        self.assertFalse(decision.formal_ready)
        self.assertIn(
            "management_style_not_verified_passive",
            decision.reasons,
        )
        self.assertIn(
            "asset_class_not_domestic_equity",
            decision.reasons,
        )
        self.assertIn(
            "industry_mapping_coverage_below_100_percent",
            decision.reasons,
        )
        self.assertIn("ranking_input_not_ready", decision.reasons)

    def test_low_frequency_task_stays_blocked_until_all_contracts_are_ready(self):
        blocked = evaluate_low_frequency_readiness(
            EtfLowFrequencyReadiness(
                product_effective_time_available=False,
                product_version_transition_supported=False,
                full_index_refresh_available=False,
                daily_fact_universe_available=True,
                retention_policy_approved=True,
            )
        )
        self.assertFalse(blocked.ready)
        self.assertEqual(
            blocked.reasons,
            (
                "product_effective_time_unavailable",
                "product_version_transition_not_supported",
                "full_index_refresh_unavailable",
            ),
        )

        ready = evaluate_low_frequency_readiness(
            EtfLowFrequencyReadiness(
                product_effective_time_available=True,
                product_version_transition_supported=True,
                full_index_refresh_available=True,
                daily_fact_universe_available=True,
                retention_policy_approved=True,
            )
        )
        self.assertTrue(ready.ready)
        self.assertEqual(ready.reasons, ())


if __name__ == "__main__":
    unittest.main()
