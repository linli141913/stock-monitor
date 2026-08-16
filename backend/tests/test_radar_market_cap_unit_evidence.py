import unittest
from datetime import datetime, timezone

from radar.contracts import (
    QuoteSnapshot,
    QuoteTradingStatus,
    UnitVerificationStatus,
)


AS_OF = datetime(2026, 8, 14, 2, 30, tzinfo=timezone.utc)


def quote(
    symbol,
    *,
    raw_market_cap=456.70,
    market_cap_cny=45_670_000_000.0,
    price=10.0,
    total_shares=4_567_000_000.0,
    unit_status=UnitVerificationStatus.VERIFIED,
    trading_status=None,
):
    return QuoteSnapshot(
        symbol=symbol,
        name=symbol,
        sourceTime=AS_OF,
        fetchedAt=AS_OF,
        tradingStatus=trading_status,
        price=price,
        changePercent=0.0,
        turnoverAmountSource=0.0,
        marketCapSource=raw_market_cap,
        marketCapCny=(
            market_cap_cny
            if unit_status == UnitVerificationStatus.VERIFIED
            else None
        ),
        marketCapUnitStatus=unit_status,
        totalSharesSource=total_shares,
        currency="CNY",
        source="tencent_finance",
    )


class MarketCapUnitEvidenceTests(unittest.TestCase):
    def build(self, quotes, symbols):
        try:
            from radar.market_cap_unit_evidence import (
                build_market_cap_unit_evidence,
            )
        except ModuleNotFoundError:
            self.fail("缺少市值单位批次证据实现")
        return build_market_cap_unit_evidence(
            quotes,
            stock_symbols=symbols,
        )

    def test_verified_active_quotes_and_suspended_exclusion_form_evidence(self):
        result = self.build(
            (
                quote("000001"),
                quote(
                    "000002",
                    raw_market_cap=100.0,
                    market_cap_cny=10_000_000_000.0,
                    total_shares=1_000_000_000.0,
                ),
                quote(
                    "000003",
                    unit_status=UnitVerificationStatus.UNVERIFIED,
                    trading_status=QuoteTradingStatus.SUSPENDED,
                ),
            ),
            ("000001", "000002", "000003"),
        )

        self.assertEqual(
            result.contract_id,
            "radar-market-cap-unit-evidence-v1",
        )
        self.assertEqual(result.status, UnitVerificationStatus.VERIFIED)
        self.assertEqual(result.active_stock_count, 2)
        self.assertEqual(result.verified_stock_count, 2)
        self.assertEqual(result.reasons, ())

    def test_cross_field_mismatch_fails_closed(self):
        result = self.build(
            (quote("000001", total_shares=1.0),),
            ("000001",),
        )

        self.assertEqual(result.status, UnitVerificationStatus.UNVERIFIED)
        self.assertEqual(result.verified_stock_count, 0)
        self.assertEqual(
            result.reasons,
            ("market_cap_unit_value_unverified",),
        )

    def test_missing_or_duplicate_stock_quotes_never_verify_the_batch(self):
        cases = (
            (
                (quote("000001"),),
                ("000001", "000002"),
                "market_cap_unit_quote_missing",
            ),
            (
                (quote("000001"), quote("000001")),
                ("000001",),
                "market_cap_unit_quote_duplicate",
            ),
        )
        for quotes, symbols, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.build(quotes, symbols)

                self.assertEqual(
                    result.status,
                    UnitVerificationStatus.UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)


if __name__ == "__main__":
    unittest.main()
