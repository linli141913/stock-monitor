import unittest
from datetime import datetime, timezone

from radar.contracts import MarketFeatureSnapshot
from radar.market_research_state import (
    MarketResearchState,
    MarketResearchStateStatus,
    produce_market_research_state,
)


UTC = timezone.utc


class MarketResearchStateTests(unittest.TestCase):
    @staticmethod
    def snapshot(*, changes=(1.0, 0.9, 1.2, 0.8), advancers=70,
                 decliners=25, flat=5, complete=True, verified=True):
        as_of = datetime(2026, 9, 2, 5, 10, tzinfo=UTC)
        identities = (
            ("sse_composite", "000001", "sse", "sh000001", "上证指数"),
            ("szse_component", "399001", "szse", "sz399001", "深证成指"),
            ("chinext", "399006", "szse", "sz399006", "创业板指"),
            ("star50", "000688", "sse", "sh000688", "科创50"),
        )
        completeness = {
            "expectedCount": 100,
            "returnedCount": 100,
            "validCount": 100,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {"change_percent": 1.0},
            "isComplete": complete,
            "reasons": [] if complete else ["source_time_stale"],
        }
        return MarketFeatureSnapshot.model_validate({
            "radarRunId": "market-run-1",
            "indexBatchId": "market-run-1-indices",
            "quoteBatchId": "market-run-1-quotes",
            "asOf": as_of,
            "sourceTime": as_of,
            "fetchedAt": as_of,
            "indices": [{
                "indexKey": key,
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "sourceSymbol": source_symbol,
                "sourceTime": as_of,
                "fetchedAt": as_of,
                "price": 1000 + index,
                "changePercent": changes[index],
                "source": "tencent_finance",
            } for index, (key, symbol, exchange, source_symbol, name)
                in enumerate(identities)],
            "indexCompleteness": {
                **completeness,
                "expectedCount": 4,
                "returnedCount": 4,
                "validCount": 4,
                "rowCoverage": 1.0,
            },
            "breadth": {
                "advancers": advancers,
                "decliners": decliners,
                "flat": flat,
                "unavailable": 0,
                "completeness": completeness,
            },
            "turnover": {
                "rawValue": 12345.0,
                "contributingCount": 100,
                "unitStatus": "verified" if verified else "unverified",
                "formalUsable": bool(complete and verified),
                "completeness": completeness,
                "reasons": [],
            },
            "excludedEtfCount": 10,
            "duplicateSymbols": [],
            "unknownSymbols": [],
        })

    def test_complete_strong_snapshot_produces_versioned_research_state(self):
        result = produce_market_research_state(self.snapshot())

        self.assertEqual(result.status, MarketResearchStateStatus.READY)
        self.assertEqual(result.state, MarketResearchState.STRONG)
        self.assertTrue(result.research_usable)
        self.assertFalse(result.formal_usable)
        self.assertEqual(result.metrics.positive_index_count, 4)
        self.assertEqual(result.metrics.advancer_ratio, 0.7)
        self.assertRegex(result.snapshot_sha256, r"^[0-9a-f]{64}$")

    def test_complete_negative_snapshot_distinguishes_risk_and_retreat(self):
        risk = produce_market_research_state(self.snapshot(
            changes=(-1.5, -1.2, -1.8, -1.0),
            advancers=18,
            decliners=80,
            flat=2,
        ))
        retreat = produce_market_research_state(self.snapshot(
            changes=(-0.4, -0.3, -0.6, 0.1),
            advancers=35,
            decliners=60,
            flat=5,
        ))

        self.assertEqual(risk.state, MarketResearchState.RISK)
        self.assertEqual(retreat.state, MarketResearchState.RETREAT)

    def test_complete_mixed_snapshot_is_oscillation_not_missing_default(self):
        result = produce_market_research_state(self.snapshot(
            changes=(0.2, -0.1, 0.1, -0.2),
            advancers=50,
            decliners=45,
            flat=5,
        ))

        self.assertEqual(result.status, MarketResearchStateStatus.READY)
        self.assertEqual(result.state, MarketResearchState.OSCILLATION)

    def test_incomplete_or_unverified_snapshot_fails_closed(self):
        incomplete = produce_market_research_state(
            self.snapshot(complete=False),
        )
        unverified = produce_market_research_state(
            self.snapshot(verified=False),
        )

        self.assertEqual(incomplete.status, MarketResearchStateStatus.BLOCKED)
        self.assertIsNone(incomplete.state)
        self.assertIn("market_features_incomplete", incomplete.reasons)
        self.assertEqual(unverified.status, MarketResearchStateStatus.BLOCKED)
        self.assertIn("market_turnover_unit_unverified", unverified.reasons)


if __name__ == "__main__":
    unittest.main()
