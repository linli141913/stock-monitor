import unittest
from datetime import datetime, timezone

from radar.contracts import (
    QuoteSnapshot,
    QuoteTradingStatus,
    UnitVerificationStatus,
)
from radar.turnover_unit_evidence import (
    TURNOVER_UNIT_EVIDENCE_CONTRACT_ID,
    build_turnover_unit_evidence,
)


AS_OF = datetime(2026, 8, 14, 2, 30, tzinfo=timezone.utc)


def quote(
    symbol,
    *,
    raw_amount=12_345.6,
    cny_amount=123_456_789.0,
    status=UnitVerificationStatus.VERIFIED,
    trading_status=None,
):
    return QuoteSnapshot(
        symbol=symbol,
        name=symbol,
        sourceTime=AS_OF,
        fetchedAt=AS_OF,
        tradingStatus=trading_status,
        price=10.0,
        changePercent=0.0,
        turnoverAmountSource=raw_amount,
        turnoverAmountCny=cny_amount if status == UnitVerificationStatus.VERIFIED else None,
        turnoverAmountUnitStatus=status,
        source="tencent_finance",
    )


class TurnoverUnitEvidenceTests(unittest.TestCase):
    def test_same_response_amounts_and_true_zero_form_verified_evidence(self):
        result = build_turnover_unit_evidence(
            (
                quote("000001"),
                quote("000002", raw_amount=0.0, cny_amount=0.0),
                quote(
                    "000003",
                    status=UnitVerificationStatus.UNVERIFIED,
                    trading_status=QuoteTradingStatus.SUSPENDED,
                ),
            ),
            stock_symbols=("000001", "000002", "000003"),
        )

        self.assertEqual(result.contract_id, TURNOVER_UNIT_EVIDENCE_CONTRACT_ID)
        self.assertEqual(result.status, UnitVerificationStatus.VERIFIED)
        self.assertEqual(result.active_stock_count, 2)
        self.assertEqual(result.verified_stock_count, 2)
        self.assertEqual(result.reasons, ())

    def test_cross_field_mismatch_fails_closed(self):
        result = build_turnover_unit_evidence(
            (quote("000001", cny_amount=1.0),),
            stock_symbols=("000001",),
        )

        self.assertEqual(result.status, UnitVerificationStatus.UNVERIFIED)
        self.assertEqual(result.verified_stock_count, 0)
        self.assertEqual(
            result.reasons,
            ("turnover_unit_value_unverified",),
        )

    def test_bse_a_share_codes_are_inside_the_unit_evidence_scope(self):
        result = build_turnover_unit_evidence(
            (quote("920001"), quote("430001")),
            stock_symbols=("920001", "430001"),
        )

        self.assertEqual(result.status, UnitVerificationStatus.VERIFIED)
        self.assertEqual(result.verified_stock_count, 2)
        self.assertEqual(result.reasons, ())

    def test_missing_or_duplicate_stock_quotes_never_verify_the_batch(self):
        cases = (
            (
                (quote("000001"),),
                ("000001", "000002"),
                "turnover_unit_quote_missing",
            ),
            (
                (quote("000001"), quote("000001")),
                ("000001",),
                "turnover_unit_quote_duplicate",
            ),
        )
        for quotes, symbols, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_turnover_unit_evidence(
                    quotes,
                    stock_symbols=symbols,
                )

                self.assertEqual(
                    result.status,
                    UnitVerificationStatus.UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)


if __name__ == "__main__":
    unittest.main()
