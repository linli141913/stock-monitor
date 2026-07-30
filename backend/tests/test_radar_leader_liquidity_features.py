import unittest
from datetime import datetime, timedelta, timezone

from radar.contracts import QuoteSnapshot, UnitVerificationStatus
from radar.leader_liquidity_features import (
    build_leader_liquidity_features,
)
from radar.leader_research_features import ResearchFeatureStatus


UTC = timezone.utc
AS_OF = datetime(2026, 7, 27, 2, 30, tzinfo=UTC)


def quote(
    *,
    turnover_amount_cny=123456789.0,
    turnover_amount_unit_status=UnitVerificationStatus.VERIFIED,
    turnover_rate_percent=2.3,
    source_time=AS_OF - timedelta(seconds=20),
):
    return QuoteSnapshot(
        symbol="000001",
        name="测试证券",
        sourceTime=source_time,
        fetchedAt=AS_OF,
        price=10.0,
        changePercent=1.2,
        turnoverAmountSource=12345.6,
        turnoverAmountCny=turnover_amount_cny,
        turnoverAmountUnitStatus=turnover_amount_unit_status,
        turnoverRatePercent=turnover_rate_percent,
        volumeRatio=1.5,
        marketCapSource=456.7,
    )


class LeaderLiquidityFeatureTests(unittest.TestCase):
    def build(self, quote_value=None, source_status=None):
        return build_leader_liquidity_features(
            as_of=AS_OF,
            quote=quote_value or quote(),
            source_contract_id="quote-source:market-run",
            source_status=(
                ResearchFeatureStatus.READY
                if source_status is None
                else source_status
            ),
        )

    def test_verified_current_values_are_research_only(self):
        result = self.build()
        evidence = result.to_evidence()

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertIsNone(evidence["researchScore"])
        self.assertEqual(
            evidence["metrics"]["turnoverAmountCny"],
            {
                "value": 123456789.0,
                "unit": "CNY",
                "status": "ready",
                "reasons": [],
            },
        )
        self.assertEqual(
            evidence["metrics"]["turnoverRatePercent"]["value"],
            2.3,
        )
        self.assertEqual(
            evidence["metrics"]["sameTimeTurnoverBaseline"]["status"],
            "missing",
        )
        self.assertEqual(
            evidence["tradability"]["tradingStatus"]["status"],
            "missing",
        )
        self.assertEqual(
            evidence["tradability"]["spreadBps"]["status"],
            "source_unverified",
        )
        self.assertIn(
            "same_time_turnover_history_missing",
            evidence["reasons"],
        )
        self.assertIn("trading_status_missing", evidence["reasons"])

    def test_true_zero_current_values_are_preserved(self):
        evidence = self.build(quote(
            turnover_amount_cny=0.0,
            turnover_rate_percent=0.0,
        )).to_evidence()

        self.assertEqual(
            evidence["metrics"]["turnoverAmountCny"]["value"],
            0.0,
        )
        self.assertEqual(
            evidence["metrics"]["turnoverRatePercent"]["value"],
            0.0,
        )

    def test_unverified_unit_keeps_cny_metric_unavailable(self):
        evidence = self.build(quote(
            turnover_amount_cny=None,
            turnover_amount_unit_status=(
                UnitVerificationStatus.UNVERIFIED
            ),
        )).to_evidence()

        self.assertIsNone(
            evidence["metrics"]["turnoverAmountCny"]["value"]
        )
        self.assertEqual(
            evidence["metrics"]["turnoverAmountCny"]["status"],
            "source_unverified",
        )
        self.assertIn(
            "turnover_amount_unit_unverified",
            evidence["reasons"],
        )

    def test_invalid_turnover_rate_stays_missing(self):
        evidence = self.build(quote(
            turnover_rate_percent=-1.0,
        )).to_evidence()

        self.assertIsNone(
            evidence["metrics"]["turnoverRatePercent"]["value"]
        )
        self.assertEqual(
            evidence["metrics"]["turnoverRatePercent"]["status"],
            "missing",
        )
        self.assertIn(
            "turnover_rate_invalid",
            evidence["reasons"],
        )

    def test_source_failure_has_priority(self):
        result = self.build(
            source_status=ResearchFeatureStatus.SOURCE_FAILED
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.reasons,
            ("quote_source_source_failed",),
        )
        self.assertEqual(result.metrics, {})

    def test_stale_and_future_source_times_are_rejected(self):
        cases = (
            (
                quote(source_time=AS_OF - timedelta(seconds=91)),
                ResearchFeatureStatus.STALE,
                "quote_source_stale",
            ),
            (
                quote(source_time=AS_OF + timedelta(seconds=6)),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "quote_source_time_future",
            ),
        )
        for quote_value, status, reason in cases:
            with self.subTest(status=status):
                result = self.build(quote_value)

                self.assertEqual(result.status, status)
                self.assertIn(reason, result.reasons)
                self.assertEqual(result.metrics, {})


if __name__ == "__main__":
    unittest.main()
