import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from radar.sources.etf_fund_size_history import (
    build_verified_sse_etf_fund_size_snapshot,
    fetch_verified_sse_etf_fund_size_snapshot,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FETCHED_AT = datetime(2026, 9, 2, 14, 50, tzinfo=SHANGHAI_TZ)
REPORT_DATE = date(2026, 9, 1)


def payload(*rows):
    return {
        "actionErrors": [],
        "fieldErrors": {},
        "result": list(rows),
    }


class EtfFundSizeHistoryTests(unittest.TestCase):
    def test_fetcher_uses_injected_official_provider_and_exact_date(self):
        calls = []

        def provider(symbol):
            calls.append(symbol)
            return payload({
                "FUND_CODE": symbol,
                "TRADE_DATE": REPORT_DATE.isoformat(),
                "SCALE": "1077.3194",
            })

        result = fetch_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            provider=provider,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(calls, ["510300"])
        self.assertTrue(result.formal_usable)
        self.assertEqual(result.fund_size_cny, 107_731_940_000.0)

    def test_fetcher_classifies_provider_failure_without_fabricating_size(self):
        def provider(_symbol):
            raise TimeoutError("upstream timeout")

        result = fetch_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            provider=provider,
            clock=lambda: FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIsNone(result.fund_size_cny)
        self.assertIn("fund_size_source_failed", result.reasons)

    def test_exact_dated_official_scale_converts_yi_cny_and_preserves_zero(self):
        result = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload=payload({
                "FUND_CODE": "510300",
                "TRADE_DATE": REPORT_DATE.isoformat(),
                "SCALE": "0.0000",
            }),
            fetched_at=FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(result.trade_date, REPORT_DATE)
        self.assertEqual(result.fund_size_cny, 0.0)
        self.assertEqual(result.reasons, ())
        self.assertEqual(
            result.source_contract_id,
            "sse-etf-fund-size-history-cny-v1",
        )
        self.assertEqual(len(result.source_content_sha256), 64)

    def test_missing_or_duplicate_date_fails_closed(self):
        missing = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload=payload({
                "FUND_CODE": "510300",
                "TRADE_DATE": "2026-08-31",
                "SCALE": "100.0",
            }),
            fetched_at=FETCHED_AT,
        )
        duplicate = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload=payload(
                {
                    "FUND_CODE": "510300",
                    "TRADE_DATE": REPORT_DATE.isoformat(),
                    "SCALE": "100.0",
                },
                {
                    "FUND_CODE": "510300",
                    "TRADE_DATE": REPORT_DATE.isoformat(),
                    "SCALE": "101.0",
                },
            ),
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(missing.formal_usable)
        self.assertIn("fund_size_trade_date_missing", missing.reasons)
        self.assertFalse(duplicate.formal_usable)
        self.assertIn("fund_size_trade_date_duplicate", duplicate.reasons)

    def test_invalid_value_source_error_and_wrong_symbol_fail_closed(self):
        invalid = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload=payload({
                "FUND_CODE": "510300",
                "TRADE_DATE": REPORT_DATE.isoformat(),
                "SCALE": "--",
            }),
            fetched_at=FETCHED_AT,
        )
        failed = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload={"actionErrors": ["temporary error"], "result": []},
            fetched_at=FETCHED_AT,
        )
        wrong_symbol = build_verified_sse_etf_fund_size_snapshot(
            symbol="510300",
            expected_trade_date=REPORT_DATE,
            payload=payload({
                "FUND_CODE": "510310",
                "TRADE_DATE": REPORT_DATE.isoformat(),
                "SCALE": "100.0",
            }),
            fetched_at=FETCHED_AT,
        )

        self.assertIn("fund_size_value_invalid", invalid.reasons)
        self.assertIn("fund_size_source_failed", failed.reasons)
        self.assertIn("fund_size_symbol_mismatch", wrong_symbol.reasons)

    def test_invalid_symbol_timezone_and_future_date_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "sse_etf_symbol_invalid"):
            build_verified_sse_etf_fund_size_snapshot(
                symbol="159915",
                expected_trade_date=REPORT_DATE,
                payload=payload(),
                fetched_at=FETCHED_AT,
            )
        with self.assertRaisesRegex(ValueError, "fund_size_fetched_at_timezone_required"):
            build_verified_sse_etf_fund_size_snapshot(
                symbol="510300",
                expected_trade_date=REPORT_DATE,
                payload=payload(),
                fetched_at=FETCHED_AT.replace(tzinfo=None),
            )
        with self.assertRaisesRegex(ValueError, "fund_size_trade_date_future"):
            build_verified_sse_etf_fund_size_snapshot(
                symbol="510300",
                expected_trade_date=date(2026, 9, 3),
                payload=payload(),
                fetched_at=FETCHED_AT,
            )


if __name__ == "__main__":
    unittest.main()
