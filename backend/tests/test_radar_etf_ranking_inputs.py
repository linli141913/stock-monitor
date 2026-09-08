import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    EtfDailyFact,
    EtfMetricState,
    EtfShareObservation,
    QuoteSnapshot,
    UnitVerificationStatus,
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
from radar.sources.etf_turnover_history import (
    EtfTurnoverHistoryPoint,
    VerifiedEtfTurnoverWindow,
)
from radar.sources.etf_fund_size_history import (
    build_verified_sse_etf_fund_size_snapshot,
)
from radar.sources.etf_tracking_history import (
    build_csindex_price_history,
    build_huatai_pb_nav_history,
    build_verified_etf_tracking_window,
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


def quote(
    symbol="159915",
    *,
    price=3.6,
    turnover_amount=0.0,
    turnover_amount_cny=None,
    turnover_unit_status=UnitVerificationStatus.UNVERIFIED,
):
    return QuoteSnapshot(
        symbol=symbol,
        name="创业板ETF",
        sourceTime=datetime(2026, 7, 24, 15, 0, tzinfo=SHANGHAI_TZ),
        fetchedAt=FETCHED_AT,
        price=price,
        changePercent=0.0,
        turnoverAmountSource=turnover_amount,
        turnoverAmountCny=turnover_amount_cny,
        turnoverAmountUnitStatus=turnover_unit_status,
        turnoverRatePercent=0.0,
        volumeRatio=0.0,
        marketCapSource=0.0,
    )


def tracking_window(symbol="159915", report_date=REPORT_DATE):
    dates = tuple(
        report_date - timedelta(days=60 - index)
        for index in range(61)
    )
    nav_payload = {
        "dataList": [
            {
                "date": day.isoformat(),
                "fundcode": symbol,
                "netvalue": 1 + index * 0.001,
                "totalnetvalue": 1 + index * 0.001,
            }
            for index, day in enumerate(dates)
        ],
    }
    index_payload = {
        "code": "200",
        "data": [
            {
                "tradeDate": day.strftime("%Y%m%d"),
                "indexCode": "000300",
                "indexNameCnAll": "沪深300指数",
                "close": 1000 + index + (index % 2) * 0.1,
            }
            for index, day in enumerate(dates)
        ],
    }
    nav = build_huatai_pb_nav_history(
        symbol=symbol,
        expected_trade_dates=dates,
        payload=nav_payload,
        fetched_at=FETCHED_AT,
    )
    index = build_csindex_price_history(
        index_code="000300",
        index_name="沪深300指数",
        expected_trade_dates=dates,
        payload=index_payload,
        fetched_at=FETCHED_AT,
    )
    return build_verified_etf_tracking_window(
        symbol=symbol,
        index_code="000300",
        expected_trade_dates=dates,
        nav_history=nav,
        index_history=index,
        computed_at=FETCHED_AT,
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

    def test_verified_turnover_window_enters_daily_fact_without_promoting_other_fields(self):
        window_dates = tuple(
            date(2026, 7, day) for day in range(1, 21)
        )
        window = VerifiedEtfTurnoverWindow(
            symbol="159915",
            expected_trade_dates=window_dates,
            points=tuple(
                EtfTurnoverHistoryPoint(
                    trade_date=day,
                    turnover_amount_cny=100_000_000.0,
                )
                for day in window_dates
            ),
            average_turnover_20d=100_000_000.0,
            sample_count=20,
            source_contract_id=(
                "tencent-eastmoney-etf-turnover-20d-crosscheck-v1"
            ),
            tencent_content_sha256="a" * 64,
            eastmoney_content_sha256="b" * 64,
            fetched_at=FETCHED_AT,
            formal_usable=True,
            reasons=(),
        )

        fact = build_etf_daily_fact(
            observation(report_date=date(2026, 7, 20)),
            computed_at=FETCHED_AT,
            turnover_window=window,
        )

        self.assertEqual(fact.average_turnover_20d, 100_000_000.0)
        self.assertEqual(
            fact.field_states["averageTurnover20d"],
            EtfMetricState.VERIFIED,
        )
        self.assertEqual(
            fact.source_contract_ids["averageTurnover20d"],
            window.source_contract_id,
        )
        self.assertEqual(fact.sample_count, 20)
        self.assertFalse(fact.formal_usable)

    def test_verified_dated_fund_size_enters_daily_fact_in_cny(self):
        report_date = date(2026, 7, 24)
        size_snapshot = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=report_date,
            payload={
                "actionErrors": [],
                "fieldErrors": {},
                "result": [{
                    "FUND_CODE": "510300",
                    "TRADE_DATE": report_date.isoformat(),
                    "SCALE": "1077.3194",
                }],
            },
            fetched_at=FETCHED_AT,
        )

        fact = build_etf_daily_fact(
            observation(
                symbol="510300",
                report_date=report_date,
                source_contract_id="exchange-etf-shares-sse-v1",
            ),
            computed_at=FETCHED_AT,
            fund_size_snapshot=size_snapshot,
        )

        self.assertEqual(fact.fund_size, 107_731_940_000.0)
        self.assertEqual(fact.fund_size_unit, "CNY")
        self.assertEqual(
            fact.field_states["fundSize"],
            EtfMetricState.VERIFIED,
        )
        self.assertEqual(
            fact.source_contract_ids["fundSize"],
            "sse-etf-fund-size-history-cny-v1",
        )
        self.assertFalse(fact.formal_usable)

    def test_verified_tracking_window_enters_three_separate_fields_and_nav(self):
        window = tracking_window()

        fact = build_etf_daily_fact(
            observation(),
            computed_at=FETCHED_AT,
            tracking_window=window,
        )

        self.assertEqual(
            fact.field_states["trackingDifference"],
            EtfMetricState.VERIFIED,
        )
        self.assertEqual(
            fact.field_states["trackingError"],
            EtfMetricState.VERIFIED,
        )
        self.assertEqual(
            fact.field_states["indexCorrelation"],
            EtfMetricState.VERIFIED,
        )
        self.assertEqual(fact.field_states["nav"], EtfMetricState.VERIFIED)
        self.assertEqual(fact.nav, window.latest_unit_nav)
        self.assertEqual(fact.window_trading_days, 60)
        self.assertEqual(fact.sample_count, 60)
        self.assertNotEqual(fact.tracking_difference, fact.tracking_error)
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

    def test_verified_cny_turnover_is_audited_without_unit_warning(self):
        fact = build_etf_daily_fact(
            observation(),
            computed_at=FETCHED_AT,
        )
        audit = build_ranking_input_audit(
            quote(
                turnover_amount=12.34,
                turnover_amount_cny=123_400.0,
                turnover_unit_status=UnitVerificationStatus.VERIFIED,
            ),
            fact,
            as_of=FETCHED_AT,
        )

        self.assertEqual(audit.metric_values["turnoverAmountCny"], 123_400.0)
        self.assertEqual(
            audit.field_states["turnoverAmountCny"],
            EtfMetricState.VERIFIED,
        )
        self.assertNotIn("turnover_amount_unit_unverified", audit.reasons)
        self.assertFalse(audit.formal_ready)

    def test_all_verified_ranking_inputs_can_pass_a_ready_formal_gate(self):
        field_states = {
            field_name: EtfMetricState.VERIFIED
            for field_name in (
                "fundSize",
                "fundShares",
                "nav",
                "shareChange5d",
                "shareChange20d",
                "averageTurnover20d",
                "trackingDifference",
                "trackingError",
                "indexCorrelation",
            )
        }
        fact = EtfDailyFact(
            symbol="159915",
            tradeDate=REPORT_DATE,
            sourceReportDate=REPORT_DATE,
            fundSize=36_000_000_000.0,
            fundShares=10_000_000_000.0,
            fundSharesUnit="share",
            nav=3.6,
            navCurrency="CNY",
            shareChange5d=0.01,
            shareChange20d=0.03,
            averageTurnover20d=1_000_000_000.0,
            trackingDifference=-0.001,
            trackingError=0.002,
            indexCorrelation=0.999,
            windowTradingDays=20,
            sampleCount=20,
            formulaVersion="test-verified-etf-daily-fact-v1",
            fieldStates=field_states,
            sourceContractIds={
                field_name: "test-verified-source-v1"
                for field_name in field_states
            },
            fetchedAt=FETCHED_AT,
            computedAt=FETCHED_AT,
            formalUsable=True,
            reasons=(),
        )

        audit = build_ranking_input_audit(
            quote(
                turnover_amount=100_000.0,
                turnover_amount_cny=100_000.0,
                turnover_unit_status=UnitVerificationStatus.VERIFIED,
            ),
            fact,
            as_of=FETCHED_AT,
            formal_gate_ready=True,
        )

        self.assertTrue(audit.formal_ready)
        self.assertEqual(audit.reasons, ())
        self.assertEqual(
            audit.rankable_fields,
            (
                "fundSize",
                "averageTurnover20d",
                "trackingDifference",
                "trackingError",
                "indexCorrelation",
            ),
        )

    def test_display_only_intraday_turnover_does_not_block_verified_history(self):
        field_states = {
            field_name: EtfMetricState.VERIFIED
            for field_name in (
                "fundSize",
                "fundShares",
                "nav",
                "averageTurnover20d",
                "trackingDifference",
                "trackingError",
                "indexCorrelation",
            )
        }
        field_states.update({
            "shareChange5d": EtfMetricState.SOURCE_UNVERIFIED,
            "shareChange20d": EtfMetricState.SOURCE_UNVERIFIED,
        })
        fact = EtfDailyFact(
            symbol="159915",
            tradeDate=REPORT_DATE,
            sourceReportDate=REPORT_DATE,
            fundSize=36_000_000_000.0,
            fundShares=10_000_000_000.0,
            fundSharesUnit="share",
            nav=3.6,
            navCurrency="CNY",
            averageTurnover20d=1_000_000_000.0,
            trackingDifference=-0.001,
            trackingError=0.002,
            indexCorrelation=0.999,
            windowTradingDays=60,
            sampleCount=60,
            formulaVersion="test-verified-etf-daily-fact-v1",
            fieldStates=field_states,
            sourceContractIds={
                field_name: "test-verified-source-v1"
                for field_name in field_states
            },
            fetchedAt=FETCHED_AT,
            computedAt=FETCHED_AT,
            formalUsable=True,
            reasons=(),
        )

        audit = build_ranking_input_audit(
            quote(turnover_amount=100_000.0),
            fact,
            as_of=FETCHED_AT,
            formal_gate_ready=True,
        )

        self.assertTrue(audit.formal_ready)
        self.assertNotIn("turnoverAmountSource", audit.rankable_fields)
        self.assertIn("turnoverAmountSource", audit.excluded_fields)
        self.assertEqual(audit.reasons, ())

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
