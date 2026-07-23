import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    UnitVerificationStatus,
)
from radar.market_features import build_market_features
from radar.sources.market_indices import (
    MARKET_INDEX_IDENTITIES,
    fetch_market_indices,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 21, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = AS_OF + timedelta(seconds=3)


def tencent_index_line(
    source_symbol,
    code,
    *,
    name="测试指数",
    price="1000.00",
    change_percent="1.25",
    source_time="20260721100000",
):
    fields = [""] * 50
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[30] = source_time
    fields[32] = change_percent
    return f'v_{source_symbol}="{"~".join(fields)}";'


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.trust_env = True

    def get(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        result = self.responder(url)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def quote(
    symbol,
    *,
    change_percent=1.0,
    turnover_amount=100.0,
    source_time=AS_OF,
):
    return QuoteSnapshot(
        symbol=symbol,
        name=f"证券{symbol}",
        sourceTime=source_time,
        fetchedAt=FETCHED_AT,
        price=10.0,
        changePercent=change_percent,
        turnoverAmountSource=turnover_amount,
        turnoverRatePercent=1.0,
        volumeRatio=1.0,
        marketCapSource=100.0,
    )


def quote_batch(items, *, expected_count=None, source_time=AS_OF):
    expected = len(items) if expected_count is None else expected_count
    returned = len(items)
    row_coverage = returned / expected if expected else 0.0
    field_names = (
        "price",
        "source_time",
        "change_percent",
        "turnover_amount_source",
    )
    coverage = {
        field_name: (
            sum(getattr(item, field_name) is not None for item in items) / returned
            if returned
            else 0.0
        )
        for field_name in field_names
    }
    return SourceBatch[QuoteSnapshot](
        meta=RadarBatchMeta(
            radarRunId="run-market-1",
            batchId="stock-quotes-1",
            source="tencent_finance",
            asOf=AS_OF,
            sourceTime=source_time,
            fetchedAt=FETCHED_AT,
            expectedCount=expected,
            returnedCount=returned,
            rowCoverage=row_coverage,
            requiredFieldCoverage=coverage,
            issues=[],
        ),
        items=items,
    )


def index_batch(*, source_time=AS_OF):
    rows = [
        tencent_index_line(
            identity.source_symbol,
            identity.symbol,
            name=identity.name,
            source_time=source_time.strftime("%Y%m%d%H%M%S"),
        )
        for identity in MARKET_INDEX_IDENTITIES
    ]
    return fetch_market_indices(
        radar_run_id="run-market-1",
        batch_id="indices-1",
        as_of=AS_OF,
        session=FakeSession(lambda _url: "".join(rows)),
        clock=lambda: FETCHED_AT,
    )


class MarketIndexSourceTests(unittest.TestCase):
    def test_four_index_identities_use_explicit_exchange_symbols(self):
        captured = []

        def responder(url):
            query = url.split("q=", 1)[1]
            captured.extend(query.split(","))
            return "".join(
                tencent_index_line(
                    identity.source_symbol,
                    identity.symbol,
                    name=identity.name,
                )
                for identity in MARKET_INDEX_IDENTITIES
            )

        session = FakeSession(responder)
        batch = fetch_market_indices(
            radar_run_id="run-market-1",
            batch_id="indices-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(
            captured,
            ["sh000001", "sz399001", "sz399006", "sh000688"],
        )
        self.assertEqual(batch.meta.expected_count, 4)
        self.assertEqual(batch.meta.returned_count, 4)
        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual(
            [item.index_key for item in batch.items],
            ["sse_composite", "szse_component", "chinext", "star50"],
        )
        self.assertEqual(batch.items[0].source_symbol, "sh000001")
        self.assertEqual(batch.items[0].exchange, "sse")
        self.assertFalse(session.trust_env)

    def test_true_zero_is_preserved_and_not_treated_as_missing(self):
        rows = [
            tencent_index_line(
                identity.source_symbol,
                identity.symbol,
                name=identity.name,
                price="0",
                change_percent="0",
            )
            for identity in MARKET_INDEX_IDENTITIES
        ]
        batch = fetch_market_indices(
            radar_run_id="run-market-1",
            batch_id="indices-1",
            as_of=AS_OF,
            session=FakeSession(lambda _url: "".join(rows)),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.items[0].price, 0.0)
        self.assertEqual(batch.items[0].change_percent, 0.0)
        self.assertEqual(batch.meta.required_field_coverage["price"], 1.0)

    def test_missing_index_is_explicit_and_breaks_complete_coverage(self):
        rows = [
            tencent_index_line(
                identity.source_symbol,
                identity.symbol,
                name=identity.name,
            )
            for identity in MARKET_INDEX_IDENTITIES[:-1]
        ]
        batch = fetch_market_indices(
            radar_run_id="run-market-1",
            batch_id="indices-1",
            as_of=AS_OF,
            session=FakeSession(lambda _url: "".join(rows)),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.returned_count, 3)
        self.assertEqual(batch.meta.row_coverage, 0.75)
        self.assertIn("missing_indices", {issue.code for issue in batch.meta.issues})


class MarketFeatureTests(unittest.TestCase):
    def test_stock_market_breadth_and_turnover_exclude_etf_pool(self):
        batch = quote_batch([
            quote("000001", change_percent=1.0, turnover_amount=100.0),
            quote("510300", change_percent=-2.0, turnover_amount=900.0),
        ])

        result = build_market_features(
            index_batch(),
            batch,
            stock_symbols=["000001"],
            etf_symbols=["510300"],
        )

        self.assertEqual(result.breadth.advancers, 1)
        self.assertEqual(result.breadth.decliners, 0)
        self.assertEqual(result.breadth.flat, 0)
        self.assertEqual(result.breadth.unavailable, 0)
        self.assertEqual(result.turnover.raw_value, 100.0)
        self.assertEqual(result.excluded_etf_count, 1)
        self.assertTrue(result.breadth.completeness.is_complete)
        self.assertEqual(
            result.turnover.unit_status,
            UnitVerificationStatus.UNVERIFIED,
        )
        self.assertFalse(result.turnover.formal_usable)
        self.assertIn("turnover_unit_unverified", result.turnover.reasons)

    def test_true_zero_market_features_remain_zero(self):
        result = build_market_features(
            index_batch(),
            quote_batch([quote("000001", change_percent=0, turnover_amount=0)]),
            stock_symbols=["000001"],
            etf_symbols=[],
        )

        self.assertEqual(result.breadth.flat, 1)
        self.assertEqual(result.breadth.unavailable, 0)
        self.assertEqual(result.turnover.raw_value, 0.0)

    def test_duplicate_quotes_are_not_double_counted_and_fail_closed(self):
        result = build_market_features(
            index_batch(),
            quote_batch([
                quote("000001", turnover_amount=100),
                quote("000001", turnover_amount=999),
                quote("000002", turnover_amount=200),
            ]),
            stock_symbols=["000001", "000002"],
            etf_symbols=[],
        )

        self.assertEqual(result.duplicate_symbols, ("000001",))
        self.assertEqual(result.turnover.raw_value, 200.0)
        self.assertFalse(result.breadth.completeness.is_complete)
        self.assertIn("duplicate_quote_symbols", result.breadth.completeness.reasons)

    def test_row_coverage_below_995_percent_is_not_complete(self):
        stock_symbols = [f"{number:06d}" for number in range(1, 201)]
        items = [quote(symbol) for symbol in stock_symbols[:198]]
        result = build_market_features(
            index_batch(),
            quote_batch(items, expected_count=200),
            stock_symbols=stock_symbols,
            etf_symbols=[],
        )

        self.assertAlmostEqual(result.breadth.completeness.row_coverage, 0.99)
        self.assertFalse(result.breadth.completeness.is_complete)
        self.assertIn(
            "row_coverage_below_threshold",
            result.breadth.completeness.reasons,
        )

    def test_missing_change_percent_is_not_converted_to_flat(self):
        missing = quote("000002")
        missing.change_percent = None
        result = build_market_features(
            index_batch(),
            quote_batch([quote("000001", change_percent=0), missing]),
            stock_symbols=["000001", "000002"],
            etf_symbols=[],
        )

        self.assertEqual(result.breadth.flat, 1)
        self.assertEqual(result.breadth.unavailable, 1)
        self.assertFalse(result.breadth.completeness.is_complete)

    def test_stale_and_future_source_times_are_rejected(self):
        stale_time = AS_OF - timedelta(seconds=91)
        future_time = AS_OF + timedelta(seconds=6)

        for source_time, expected_reason in (
            (stale_time, "source_time_stale"),
            (future_time, "source_time_in_future"),
        ):
            with self.subTest(expected_reason=expected_reason):
                result = build_market_features(
                    index_batch(source_time=source_time),
                    quote_batch(
                        [quote("000001", source_time=source_time)],
                        source_time=source_time,
                    ),
                    stock_symbols=["000001"],
                    etf_symbols=[],
                )

                self.assertFalse(result.index_completeness.is_complete)
                self.assertFalse(result.breadth.completeness.is_complete)
                self.assertIn(
                    expected_reason,
                    result.index_completeness.reasons,
                )
                self.assertIn(
                    expected_reason,
                    result.breadth.completeness.reasons,
                )

    def test_one_stale_index_cannot_hide_behind_a_fresh_batch_max_time(self):
        rows = []
        for position, identity in enumerate(MARKET_INDEX_IDENTITIES):
            source_time = (
                AS_OF - timedelta(seconds=91)
                if position == 0
                else AS_OF
            )
            rows.append(tencent_index_line(
                identity.source_symbol,
                identity.symbol,
                name=identity.name,
                source_time=source_time.strftime("%Y%m%d%H%M%S"),
            ))
        indices = fetch_market_indices(
            radar_run_id="run-market-1",
            batch_id="indices-1",
            as_of=AS_OF,
            session=FakeSession(lambda _url: "".join(rows)),
            clock=lambda: FETCHED_AT,
        )

        result = build_market_features(
            indices,
            quote_batch([quote("000001")]),
            stock_symbols=["000001"],
            etf_symbols=[],
        )

        self.assertEqual(indices.meta.source_time, AS_OF)
        self.assertFalse(result.index_completeness.is_complete)
        self.assertIn("source_time_stale", result.index_completeness.reasons)

    def test_one_stale_stock_is_excluded_from_breadth_and_turnover(self):
        result = build_market_features(
            index_batch(),
            quote_batch([
                quote(
                    "000001",
                    change_percent=2.0,
                    turnover_amount=100.0,
                    source_time=AS_OF - timedelta(seconds=91),
                ),
                quote(
                    "000002",
                    change_percent=-1.0,
                    turnover_amount=200.0,
                    source_time=AS_OF,
                ),
            ]),
            stock_symbols=["000001", "000002"],
            etf_symbols=[],
        )

        self.assertEqual(result.breadth.advancers, 0)
        self.assertEqual(result.breadth.decliners, 1)
        self.assertEqual(result.breadth.unavailable, 1)
        self.assertEqual(result.turnover.raw_value, 200.0)
        self.assertIn("source_time_stale", result.breadth.completeness.reasons)

    def test_stock_and_etf_universe_overlap_is_rejected(self):
        with self.assertRaises(ValueError):
            build_market_features(
                index_batch(),
                quote_batch([quote("510300")]),
                stock_symbols=["510300"],
                etf_symbols=["510300"],
            )


if __name__ == "__main__":
    unittest.main()
