import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.leader_history_features import (
    AdjustedHistoryPoint,
    HistoryAdjustmentBasis,
    build_leader_history_features,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.sources.leader_history_public_poc import (
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    PublicHistorySeries,
    parse_tencent_history_payload,
    run_public_history_input_poc,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 8, 20, 0, tzinfo=SHANGHAI_TZ)
TENCENT_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
)


def trading_dates():
    current = date(2026, 7, 10)
    values = []
    while len(values) < 21:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def history_series(
    symbol,
    prices,
    *,
    adjustment_basis=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
    points=None,
):
    return PublicHistorySeries(
        symbol=symbol,
        source_contract_id=(
            "tencent-continuous-index-daily-v1"
            if adjustment_basis == HistoryAdjustmentBasis.CONTINUOUS_INDEX
            else "tencent-qfq-daily-history-v1"
        ),
        source_url=TENCENT_URL,
        adjustment_basis=adjustment_basis,
        fetched_at=datetime(2026, 8, 8, 19, 55, tzinfo=SHANGHAI_TZ),
        content_sha256="sha256:" + "a" * 64,
        points=points or tuple(
            AdjustedHistoryPoint(trade_date=day, close=close)
            for day, close in zip(trading_dates(), prices)
        ),
    )


def membership(*, first_observed_at=None, excluded_out_of_scope_count=0):
    return PointInTimeIndustryMembership(
        release_id="capco-2025H2",
        source_contract_id="capco-industry-classification-v1",
        industry_code="15",
        candidate_symbol="600519",
        member_symbols=("600519", "000858", "002304"),
        published_date=date(2026, 4, 3),
        classification_start_date=date(2025, 12, 20),
        first_observed_at=first_observed_at or datetime(
            2026, 7, 1, 10, 0, tzinfo=SHANGHAI_TZ
        ),
        fetched_at=datetime(2026, 8, 8, 19, 50, tzinfo=SHANGHAI_TZ),
        document_sha256="sha256:" + "b" * 64,
        excluded_out_of_scope_count=excluded_out_of_scope_count,
    )


def query(*, membership_value=None, series_by_symbol=None):
    values = {
        "600519": history_series("600519", tuple(100 + i for i in range(21))),
        "000858": history_series("000858", tuple(100 + 2 * i for i in range(21))),
        "002304": history_series("002304", (100.0,) * 21),
        "sh000001": history_series(
            "sh000001",
            tuple(100 + 0.25 * i for i in range(21)),
            adjustment_basis=HistoryAdjustmentBasis.CONTINUOUS_INDEX,
        ),
    }
    if series_by_symbol is not None:
        values = series_by_symbol
    return PublicHistoryPocQuery(
        as_of=AS_OF,
        expected_trade_dates=trading_dates(),
        candidate_symbol="600519",
        board_index_symbol="sh000001",
        membership=membership_value or membership(),
        series_by_symbol=values,
    )


class PublicHistoryPocTests(unittest.TestCase):
    def test_complete_public_history_builds_existing_research_input(self):
        result = run_public_history_input_poc(query(
            membership_value=membership(excluded_out_of_scope_count=1),
        ))

        self.assertEqual(result.resolution_status, "ready")
        self.assertEqual(result.real_poc_status, "not_run")
        self.assertEqual(result.member_count, 3)
        self.assertEqual(result.to_evidence()["excludedOutOfScopeCount"], 1)
        self.assertEqual(result.series_coverage, 1.0)
        self.assertIsNotNone(result.history_input)
        feature_result = build_leader_history_features(result.history_input)
        self.assertEqual(feature_result.status, ResearchFeatureStatus.READY)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_late_membership_observation_cannot_build_point_in_time_input(self):
        late_membership = membership(first_observed_at=datetime(
            2026, 7, 20, 10, 0, tzinfo=SHANGHAI_TZ
        ))

        result = run_public_history_input_poc(query(
            membership_value=late_membership,
        ))

        self.assertEqual(result.resolution_status, "partial")
        self.assertIsNone(result.history_input)
        self.assertIn(
            "industry_membership_forward_window_incomplete",
            result.reasons,
        )

    def test_missing_one_member_day_blocks_equal_weight_benchmark(self):
        values = dict(query().series_by_symbol)
        member = values["000858"]
        values["000858"] = replace(member, points=member.points[:-1])

        result = run_public_history_input_poc(query(
            series_by_symbol=values,
        ))

        self.assertEqual(result.resolution_status, "partial")
        self.assertIsNone(result.history_input)
        self.assertLess(result.series_coverage, 1.0)
        self.assertIn("history_series_dates_incomplete", result.reasons)

    def test_real_zero_member_returns_are_preserved(self):
        values = dict(query().series_by_symbol)
        constant = (100.0,) * 21
        for symbol in ("600519", "000858", "002304"):
            values[symbol] = history_series(symbol, constant)

        result = run_public_history_input_poc(query(
            series_by_symbol=values,
        ))

        industry_points = result.history_input.industry_benchmark.points
        self.assertTrue(all(point.close == 1.0 for point in industry_points))

    def test_tencent_parser_requires_all_official_trade_dates(self):
        rows = [
            [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
            for index, day in enumerate(trading_dates()[:-1])
        ]
        payload = {
            "code": 0,
            "data": {"sh600519": {"qfqday": rows}},
        }

        series = parse_tencent_history_payload(
            symbol="600519",
            query_symbol="sh600519",
            payload=payload,
            expected_trade_dates=trading_dates(),
            fetched_at=datetime(
                2026, 8, 8, 19, 55, tzinfo=SHANGHAI_TZ
            ),
            adjustment_basis=HistoryAdjustmentBasis.FORWARD_ADJUSTED,
        )

        self.assertEqual(len(series.points), 20)
        result = run_public_history_input_poc(query(
            series_by_symbol={
                **dict(query().series_by_symbol),
                "600519": series,
            },
        ))
        self.assertEqual(result.resolution_status, "partial")
        self.assertIn("history_series_dates_incomplete", result.reasons)

    def test_index_parser_does_not_relabel_adjusted_rows_as_continuous(self):
        payload = {
            "code": 0,
            "data": {"sh000001": {"qfqday": []}},
        }

        with self.assertRaisesRegex(
            ValueError,
            "tencent_history_rows_missing",
        ):
            parse_tencent_history_payload(
                symbol="sh000001",
                query_symbol="sh000001",
                payload=payload,
                expected_trade_dates=trading_dates(),
                fetched_at=datetime(
                    2026, 8, 8, 19, 55, tzinfo=SHANGHAI_TZ
                ),
                adjustment_basis=HistoryAdjustmentBasis.CONTINUOUS_INDEX,
            )

    def test_beijing_exchange_symbol_cannot_enter_pure_history_contract(self):
        bse_membership = replace(
            membership(),
            candidate_symbol="920023",
            member_symbols=("920023",),
        )
        values = {
            "920023": history_series(
                "920023",
                tuple(100 + i for i in range(21)),
            ),
            "sh000001": query().series_by_symbol["sh000001"],
        }
        value = replace(
            query(
                membership_value=bse_membership,
                series_by_symbol=values,
            ),
            candidate_symbol="920023",
        )

        result = run_public_history_input_poc(value)

        self.assertEqual(result.resolution_status, "partial")
        self.assertIsNone(result.history_input)
        self.assertIn("history_query_symbol_invalid", result.reasons)
        self.assertIn(
            "industry_membership_identity_invalid",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
