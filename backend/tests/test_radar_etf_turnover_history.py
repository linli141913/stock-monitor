import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.sources.etf_turnover_history import (
    build_verified_sse_etf_turnover_window,
    build_verified_etf_turnover_window,
    fetch_verified_sse_etf_turnover_window,
    fetch_verified_etf_turnover_window,
    promote_verified_tencent_unadjusted_etf_history,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FETCHED_AT = datetime(2026, 9, 2, 16, 0, tzinfo=SHANGHAI_TZ)


def dates(count=20):
    start = date(2026, 8, 3)
    values = []
    while len(values) < count:
        if start.weekday() < 5:
            values.append(start)
        start += timedelta(days=1)
    return tuple(values)


def tencent_payload(amounts):
    rows = []
    for day, amount_cny in zip(dates(len(amounts)), amounts):
        rows.append([
            day.isoformat(), "1", "1", "1", "1", "100", {}, "1",
            f"{amount_cny / 10_000:.4f}", "",
        ])
    return {"code": 0, "data": {"sh510300": {"qfqday": rows}}}


def eastmoney_payload(amounts):
    return {
        "data": {
            "code": "510300",
            "market": 1,
            "klines": [
                (
                    f"{day.isoformat()},1,1,1,1,100,{amount:.3f},"
                    "0,0,0,0"
                )
                for day, amount in zip(dates(len(amounts)), amounts)
            ],
        },
    }


def sse_payloads(amounts):
    return {
        day: {
            "actionErrors": [],
            "fieldErrors": {},
            "result": [{
                "SEC_CODE": "510300",
                "TX_DATE": day.strftime("%Y%m%d"),
                "TRADE_AMT": f"{amount / 10_000:.2f}",
            }],
        }
        for day, amount in zip(dates(len(amounts)), amounts)
    }


class EtfTurnoverHistoryTests(unittest.TestCase):
    def test_exact_completed_day_equivalence_can_supply_missing_qfqday(self):
        query_symbol = "sh512880"
        rows = tencent_payload([100_000.0] * 20)["data"]["sh510300"][
            "qfqday"
        ]
        adjusted = {"code": 0, "data": {query_symbol: {"day": rows}}}
        raw = {"code": 0, "data": {query_symbol: {"day": list(rows)}}}

        promoted = promote_verified_tencent_unadjusted_etf_history(
            query_symbol=query_symbol,
            adjusted_payload=adjusted,
            raw_payload=raw,
            current_date=FETCHED_AT.date(),
        )

        self.assertEqual(
            promoted["data"][query_symbol]["qfqday"],
            rows,
        )
        self.assertEqual(
            promoted["data"][query_symbol]["qfqDayEquivalenceContractId"],
            "tencent-etf-qfq-day-completed-exact-equivalence-v1",
        )

    def test_mismatched_unadjusted_day_is_not_promoted(self):
        query_symbol = "sh512880"
        rows = tencent_payload([100_000.0] * 20)["data"]["sh510300"][
            "qfqday"
        ]
        raw_rows = [list(row) for row in rows]
        raw_rows[-1][8] = "999.0"

        promoted = promote_verified_tencent_unadjusted_etf_history(
            query_symbol=query_symbol,
            adjusted_payload={
                "code": 0,
                "data": {query_symbol: {"day": rows}},
            },
            raw_payload={
                "code": 0,
                "data": {query_symbol: {"day": raw_rows}},
            },
            current_date=FETCHED_AT.date(),
        )

        self.assertNotIn("qfqday", promoted["data"][query_symbol])

    def test_sse_fetcher_uses_exact_dates_and_injected_corroborating_source(self):
        official = [float((index + 1) * 100_000_000) for index in range(20)]
        official_by_date = dict(zip(dates(), official))
        sse_calls = []

        def sse(symbol, trade_date):
            sse_calls.append((symbol, trade_date))
            return sse_payloads([official_by_date[trade_date]])[
                dates(1)[0]
            ] | {
                "result": [{
                    "SEC_CODE": symbol,
                    "TX_DATE": trade_date.strftime("%Y%m%d"),
                    "TRADE_AMT": (
                        f"{official_by_date[trade_date] / 10_000:.2f}"
                    ),
                }],
            }

        result = fetch_verified_sse_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            sse_provider=sse,
            tencent_provider=(
                lambda _symbol, _dates: tencent_payload(official)
            ),
            clock=lambda: FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(set(sse_calls), {("510300", day) for day in dates()})

    def test_sse_primary_and_tencent_verify_official_turnover_window(self):
        official = [float((index + 1) * 100_000_000) for index in range(20)]
        tencent = [amount * 0.999 for amount in official]

        result = build_verified_sse_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            sse_payloads_by_date=sse_payloads(official),
            tencent_payload=tencent_payload(tencent),
            fetched_at=FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(result.sample_count, 20)
        self.assertEqual(result.points[0].turnover_amount_cny, official[0])
        self.assertEqual(result.average_turnover_20d, sum(official) / 20)
        self.assertEqual(
            result.source_contract_id,
            "sse-tencent-etf-turnover-20d-crosscheck-v1",
        )
        self.assertIsNotNone(result.sse_content_sha256)
        self.assertIsNone(result.eastmoney_content_sha256)
        self.assertAlmostEqual(
            result.maximum_cross_source_relative_difference,
            0.001,
        )

    def test_sse_primary_material_cross_source_difference_fails_closed(self):
        official = [100_000_000.0] * 20
        tencent = list(official)
        tencent[-1] = 90_000_000.0

        result = build_verified_sse_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            sse_payloads_by_date=sse_payloads(official),
            tencent_payload=tencent_payload(tencent),
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIsNone(result.average_turnover_20d)
        self.assertIn("turnover_cross_source_mismatch", result.reasons)

    def test_fetcher_uses_two_injected_sources_for_the_exact_window(self):
        calls = []
        amounts = [100_000.0] * 20

        def tencent(symbol, expected_dates):
            calls.append(("tencent", symbol, tuple(expected_dates)))
            return tencent_payload(amounts)

        def eastmoney(symbol, expected_dates):
            calls.append(("eastmoney", symbol, tuple(expected_dates)))
            return eastmoney_payload(amounts)

        result = fetch_verified_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            tencent_provider=tencent,
            eastmoney_provider=eastmoney,
            clock=lambda: FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(
            calls,
            [
                ("tencent", "510300", dates()),
                ("eastmoney", "510300", dates()),
            ],
        )

    def test_fetcher_classifies_one_source_failure_without_fabricating_average(self):
        def tencent(_symbol, _expected_dates):
            raise TimeoutError("upstream timeout")

        result = fetch_verified_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            tencent_provider=tencent,
            eastmoney_provider=(
                lambda _symbol, _dates: eastmoney_payload([100_000.0] * 20)
            ),
            clock=lambda: FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIsNone(result.average_turnover_20d)
        self.assertIn("tencent_turnover_window_incomplete", result.reasons)

    def test_two_sources_verify_exact_20_day_average_and_true_zero(self):
        amounts = [float(index * 10_000) for index in range(20)]

        result = build_verified_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            tencent_payload=tencent_payload(amounts),
            eastmoney_payload=eastmoney_payload(amounts),
            fetched_at=FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(result.sample_count, 20)
        self.assertEqual(result.points[0].turnover_amount_cny, 0.0)
        self.assertEqual(
            result.average_turnover_20d,
            sum(amounts) / 20,
        )
        self.assertEqual(result.reasons, ())
        self.assertEqual(
            result.source_contract_id,
            "tencent-eastmoney-etf-turnover-20d-crosscheck-v1",
        )

    def test_material_cross_source_mismatch_fails_closed(self):
        primary = [100_000.0] * 20
        secondary = list(primary)
        secondary[-1] = 300_000.0

        result = build_verified_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            tencent_payload=tencent_payload(primary),
            eastmoney_payload=eastmoney_payload(secondary),
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIsNone(result.average_turnover_20d)
        self.assertIn("turnover_cross_source_mismatch", result.reasons)

    def test_incomplete_window_does_not_average_available_days(self):
        amounts = [100_000.0] * 19

        result = build_verified_etf_turnover_window(
            symbol="510300",
            expected_trade_dates=dates(),
            tencent_payload=tencent_payload(amounts),
            eastmoney_payload=eastmoney_payload(amounts),
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertEqual(result.sample_count, 19)
        self.assertIsNone(result.average_turnover_20d)
        self.assertIn("turnover_window_incomplete", result.reasons)

    def test_invalid_symbol_and_future_trade_date_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "etf_symbol_invalid"):
            build_verified_etf_turnover_window(
                symbol="000725",
                expected_trade_dates=dates(),
                tencent_payload=tencent_payload([1.0] * 20),
                eastmoney_payload=eastmoney_payload([1.0] * 20),
                fetched_at=FETCHED_AT,
            )

        future_dates = (*dates()[:-1], date(2026, 9, 3))
        with self.assertRaisesRegex(ValueError, "turnover_trade_date_future"):
            build_verified_etf_turnover_window(
                symbol="510300",
                expected_trade_dates=future_dates,
                tencent_payload=tencent_payload([1.0] * 20),
                eastmoney_payload=eastmoney_payload([1.0] * 20),
                fetched_at=FETCHED_AT,
            )


if __name__ == "__main__":
    unittest.main()
