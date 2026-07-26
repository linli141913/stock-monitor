import unittest
from datetime import date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    EtfMetricState,
    EtfShareObservation,
    QuoteSnapshot,
)
from radar.etf_ranking_inputs import (
    build_etf_daily_fact,
    build_ranking_input_audit,
    calculate_share_change,
    formal_rankable_fields,
)
from radar.sources.etf_daily_facts import (
    EtfDailyShareProviders,
    fetch_etf_daily_share_observations,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FETCHED_AT = datetime(2026, 7, 25, 0, 45, tzinfo=SHANGHAI_TZ)
REPORT_DATE = date(2026, 7, 24)
PRIOR_DATE = date(2026, 7, 17)


def observation(
    symbol="159915",
    *,
    report_date=REPORT_DATE,
    shares=100.0,
    source_contract_id="exchange-etf-shares-szse-v1",
):
    return EtfShareObservation(
        symbol=symbol,
        sourceReportDate=report_date,
        fundShares=shares,
        fundSharesUnit="share",
        sourceContractId=source_contract_id,
        source="szse_official",
        fetchedAt=FETCHED_AT,
    )


def quote(symbol="159915", *, price=3.6, turnover_amount=0.0):
    return QuoteSnapshot(
        symbol=symbol,
        name="创业板ETF",
        sourceTime=datetime(2026, 7, 24, 15, 0, tzinfo=SHANGHAI_TZ),
        fetchedAt=FETCHED_AT,
        price=price,
        changePercent=0.0,
        turnoverAmountSource=turnover_amount,
        turnoverRatePercent=0.0,
        volumeRatio=0.0,
        marketCapSource=0.0,
    )


class EtfDailyShareSourceTests(unittest.TestCase):
    def test_true_zero_shares_are_preserved(self):
        providers = EtfDailyShareProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "统计日期": REPORT_DATE,
                "基金份额": 0,
            }]),
            szse=lambda _start, _end: pd.DataFrame(),
        )

        batch = fetch_etf_daily_share_observations(
            radar_run_id="run-1",
            batch_id="shares-1",
            as_of=FETCHED_AT,
            report_date=REPORT_DATE,
            sse_symbols=["510300"],
            providers=providers,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual(batch.items[0].fund_shares, 0.0)
        self.assertEqual(batch.items[0].source_report_date, REPORT_DATE)

    def test_date_mismatch_is_explicit_and_nonhealthy(self):
        providers = EtfDailyShareProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "统计日期": date(2026, 7, 23),
                "基金份额": 100,
            }]),
            szse=lambda _start, _end: pd.DataFrame(),
        )

        batch = fetch_etf_daily_share_observations(
            radar_run_id="run-1",
            batch_id="shares-1",
            as_of=FETCHED_AT,
            report_date=REPORT_DATE,
            sse_symbols=["510300"],
            providers=providers,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.items, [])
        self.assertIsNone(batch.meta.expected_count)
        self.assertIn(
            "mismatched_source_report_date",
            {issue.code for issue in batch.meta.issues},
        )

    def test_duplicate_symbol_is_not_silently_overwritten(self):
        providers = EtfDailyShareProviders(
            sse=lambda _date: pd.DataFrame([
                {
                    "基金代码": "510300",
                    "统计日期": REPORT_DATE,
                    "基金份额": 100,
                },
                {
                    "基金代码": "510300",
                    "统计日期": REPORT_DATE,
                    "基金份额": 110,
                },
            ]),
            szse=lambda _start, _end: pd.DataFrame(),
        )

        batch = fetch_etf_daily_share_observations(
            radar_run_id="run-1",
            batch_id="shares-1",
            as_of=FETCHED_AT,
            report_date=REPORT_DATE,
            sse_symbols=["510300"],
            providers=providers,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(len(batch.items), 1)
        self.assertIn(
            "duplicate_symbol",
            {issue.code for issue in batch.meta.issues},
        )
        self.assertIsNone(batch.meta.expected_count)

    def test_missing_symbol_is_explicit(self):
        providers = EtfDailyShareProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "统计日期": REPORT_DATE,
                "基金份额": 100,
            }]),
            szse=lambda _start, _end: pd.DataFrame(),
        )

        batch = fetch_etf_daily_share_observations(
            radar_run_id="run-1",
            batch_id="shares-1",
            as_of=FETCHED_AT,
            report_date=REPORT_DATE,
            sse_symbols=["510300", "510310"],
            providers=providers,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.returned_count, 1)
        self.assertIn(
            "missing_symbols",
            {issue.code for issue in batch.meta.issues},
        )


class EtfRankingInputTests(unittest.TestCase):
    def test_share_change_uses_original_shares_and_preserves_direction(self):
        result = calculate_share_change(
            observation(report_date=REPORT_DATE, shares=110.0),
            observation(
                report_date=PRIOR_DATE,
                shares=100.0,
            ),
            window_trading_days=5,
        )

        self.assertTrue(result.formal_usable)
        self.assertAlmostEqual(result.share_change, 0.1)
        self.assertEqual(result.formula_version, "radar-etf-share-change-v1")

    def test_zero_prior_shares_are_not_converted_to_a_zero_change(self):
        result = calculate_share_change(
            observation(report_date=REPORT_DATE, shares=0.0),
            observation(report_date=PRIOR_DATE, shares=0.0),
            window_trading_days=5,
        )

        self.assertIsNone(result.share_change)
        self.assertFalse(result.formal_usable)
        self.assertIn("prior_shares_zero", result.reasons)

    def test_daily_fact_marks_unknown_metrics_without_fabricating_values(self):
        share_change = calculate_share_change(
            observation(report_date=REPORT_DATE, shares=110.0),
            observation(report_date=PRIOR_DATE, shares=100.0),
            window_trading_days=5,
        )
        fact = build_etf_daily_fact(
            observation(report_date=REPORT_DATE, shares=110.0),
            computed_at=FETCHED_AT,
            share_changes={5: share_change},
        )

        self.assertEqual(fact.fund_shares, 110.0)
        self.assertEqual(fact.field_states["fundShares"], EtfMetricState.VERIFIED)
        self.assertAlmostEqual(fact.share_change_5d, 0.1)
        self.assertEqual(
            fact.field_states["fundSize"],
            EtfMetricState.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(fact.average_turnover_20d)
        self.assertFalse(fact.formal_usable)

    def test_quote_values_are_audited_but_unverified_turnover_cannot_rank(self):
        fact = build_etf_daily_fact(
            observation(),
            computed_at=FETCHED_AT,
        )
        audit = build_ranking_input_audit(
            quote(turnover_amount=0.0),
            fact,
            as_of=FETCHED_AT,
        )

        self.assertEqual(audit.metric_values["turnoverAmountSource"], 0.0)
        self.assertEqual(
            audit.field_states["turnoverAmountSource"],
            EtfMetricState.SOURCE_UNVERIFIED,
        )
        self.assertNotIn("turnoverAmountSource", audit.rankable_fields)
        self.assertIn("turnoverAmountSource", audit.excluded_fields)
        self.assertFalse(audit.formal_ready)

    def test_formal_rankable_fields_is_empty_when_everything_is_unverified(self):
        audit = build_ranking_input_audit(
            quote(),
            None,
            as_of=FETCHED_AT,
        )

        self.assertEqual(formal_rankable_fields([audit]), ())
        self.assertIn("daily_fact_missing", audit.reasons)


if __name__ == "__main__":
    unittest.main()
