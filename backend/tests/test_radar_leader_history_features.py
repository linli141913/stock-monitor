import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from radar.leader_history_features import (
    AdjustedHistoryPoint,
    AdjustedHistorySeries,
    HistoryAdjustmentBasis,
    HistorySeriesRole,
    LeaderHistoryFeatureInput,
    build_leader_history_features,
)
from radar.leader_research_features import ResearchFeatureStatus


UTC = timezone.utc


def trading_dates(count=21):
    current = date(2026, 6, 26)
    values = []
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def series(
    *,
    role,
    adjustment_basis,
    prices,
    status=ResearchFeatureStatus.READY,
    source_time=datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
    fetched_at=datetime(2026, 7, 27, 1, 50, tzinfo=UTC),
):
    symbol_by_role = {
        HistorySeriesRole.CANDIDATE_SECURITY: "000001",
        HistorySeriesRole.INDUSTRY_BENCHMARK: "C39",
        HistorySeriesRole.BOARD_INDEX: "szse_component",
    }
    role_name = role.value
    return AdjustedHistorySeries(
        role=role,
        symbol=symbol_by_role[role],
        source_contract_id=f"history-source:{role_name}",
        adjustment_basis=adjustment_basis,
        return_basis="price_return",
        status=status,
        source_time=source_time,
        fetched_at=fetched_at,
        points=tuple(
            AdjustedHistoryPoint(trade_date=trade_date, close=close)
            for trade_date, close in zip(trading_dates(), prices)
        ),
    )


def feature_input(
    *,
    candidate_prices=None,
    industry_prices=None,
    board_prices=None,
):
    candidate_prices = candidate_prices or tuple(
        100.0 + index
        for index in range(21)
    )
    industry_prices = industry_prices or tuple(
        100.0 + index * 0.5
        for index in range(21)
    )
    board_prices = board_prices or tuple(
        100.0 + index * 0.25
        for index in range(21)
    )
    return LeaderHistoryFeatureInput(
        as_of=datetime(2026, 7, 27, 2, 0, tzinfo=UTC),
        expected_trade_dates=trading_dates(),
        candidate=series(
            role=HistorySeriesRole.CANDIDATE_SECURITY,
            adjustment_basis=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
            prices=candidate_prices,
        ),
        industry_benchmark=series(
            role=HistorySeriesRole.INDUSTRY_BENCHMARK,
            adjustment_basis=(
                HistoryAdjustmentBasis.POINT_IN_TIME_EQUAL_WEIGHT_PRICE_RETURN
            ),
            prices=industry_prices,
        ),
        board_index=series(
            role=HistorySeriesRole.BOARD_INDEX,
            adjustment_basis=HistoryAdjustmentBasis.CONTINUOUS_INDEX,
            prices=board_prices,
        ),
    )


class LeaderHistoryFeatureTests(unittest.TestCase):
    def test_ready_history_computes_compressed_research_metrics(self):
        input_value = feature_input()

        result = build_leader_history_features(input_value)
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertFalse(evidence["scoreReady"])
        self.assertIsNone(evidence["researchScore"])
        self.assertEqual(evidence["observationCount"], 21)
        self.assertEqual(
            evidence["historyEndDate"],
            trading_dates()[-1].isoformat(),
        )
        candidate_return = 120.0 / 100.0 - 1
        industry_return = 110.0 / 100.0 - 1
        self.assertAlmostEqual(
            evidence["metrics"]["priceReturns"]["candidate"]["20d"],
            candidate_return,
            places=6,
        )
        self.assertAlmostEqual(
            evidence["metrics"]["excessReturns"]["vsIndustry"]["20d"],
            candidate_return - industry_return,
            places=6,
        )
        self.assertEqual(
            evidence["metrics"]["outperformanceDays"]["last5"][
                "vsIndustry"
            ],
            5,
        )
        self.assertEqual(
            evidence["metrics"]["maximumDrawdown20d"]["candidate"],
            0.0,
        )
        self.assertIsNone(
            evidence["metrics"]["recoveryFromTrough20d"]["candidate"]
        )
        self.assertIn("recovery_event_absent", evidence["reasons"])
        self.assertNotIn("points", evidence)
        self.assertNotIn("prices", str(evidence).lower())

    def test_real_zero_returns_and_zero_outperformance_are_preserved(self):
        constant = (100.0,) * 21

        evidence = build_leader_history_features(feature_input(
            candidate_prices=constant,
            industry_prices=constant,
            board_prices=constant,
        )).to_evidence()

        self.assertEqual(
            evidence["metrics"]["priceReturns"]["candidate"]["20d"],
            0.0,
        )
        self.assertEqual(
            evidence["metrics"]["excessReturns"]["vsIndustry"]["20d"],
            0.0,
        )
        self.assertEqual(
            evidence["metrics"]["outperformanceDays"]["last10"][
                "vsBoard"
            ],
            0,
        )
        self.assertEqual(
            evidence["metrics"]["drawdownAdvantage20d"]["vsIndustry"],
            0.0,
        )

    def test_actual_drawdown_keeps_drawdown_and_recovery_values(self):
        candidate = (
            100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 105.0,
            100.0, 95.0, 90.0, 92.0, 94.0, 96.0, 98.0, 100.0,
            102.0, 104.0, 106.0, 108.0, 110.0, 112.0,
        )

        evidence = build_leader_history_features(feature_input(
            candidate_prices=candidate,
        )).to_evidence()

        self.assertAlmostEqual(
            evidence["metrics"]["maximumDrawdown20d"]["candidate"],
            90.0 / 110.0 - 1,
            places=6,
        )
        self.assertAlmostEqual(
            evidence["metrics"]["recoveryFromTrough20d"]["candidate"],
            112.0 / 90.0 - 1,
            places=6,
        )

    def test_date_misalignment_stays_missing(self):
        input_value = feature_input()
        points = input_value.candidate.points[:-1]
        candidate = replace(input_value.candidate, points=points)

        result = build_leader_history_features(replace(
            input_value,
            candidate=candidate,
        ))

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIn("history_dates_misaligned", result.reasons)
        self.assertEqual(result.metrics, {})

    def test_wrong_adjustment_basis_stays_source_unverified(self):
        input_value = feature_input()
        candidate = replace(
            input_value.candidate,
            adjustment_basis=HistoryAdjustmentBasis.CONTINUOUS_INDEX,
        )

        result = build_leader_history_features(replace(
            input_value,
            candidate=candidate,
        ))

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "candidate_adjustment_basis_unverified",
            result.reasons,
        )

    def test_future_source_time_stays_source_unverified(self):
        input_value = feature_input()
        candidate = replace(
            input_value.candidate,
            source_time=input_value.as_of + timedelta(seconds=6),
            fetched_at=input_value.as_of + timedelta(seconds=6),
        )

        result = build_leader_history_features(replace(
            input_value,
            candidate=candidate,
        ))

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("history_source_time_future", result.reasons)

    def test_source_failure_has_priority_and_no_metrics(self):
        input_value = feature_input()
        industry = replace(
            input_value.industry_benchmark,
            status=ResearchFeatureStatus.SOURCE_FAILED,
        )

        result = build_leader_history_features(replace(
            input_value,
            industry_benchmark=industry,
        ))

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertIn("industry_source_source_failed", result.reasons)
        self.assertEqual(result.metrics, {})

    def test_non_positive_price_stays_missing(self):
        input_value = feature_input()
        prices = list(point.close for point in input_value.candidate.points)
        prices[-1] = 0.0
        candidate = replace(
            input_value.candidate,
            points=tuple(
                AdjustedHistoryPoint(trade_date=trade_date, close=close)
                for trade_date, close in zip(trading_dates(), prices)
            ),
        )

        result = build_leader_history_features(replace(
            input_value,
            candidate=candidate,
        ))

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIn("history_price_invalid", result.reasons)
        self.assertEqual(result.metrics, {})


if __name__ == "__main__":
    unittest.main()
