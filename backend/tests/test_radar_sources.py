import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    QuoteSnapshot,
    QuoteTradingStatus,
    RadarBatchMeta,
    SourceBatch,
    UnitVerificationStatus,
)
from radar.sources.etf_registry import EtfRegistryProviders, fetch_etf_registry
from radar.sources.security_master import (
    SecurityMasterProviders,
    fetch_security_master,
)
from radar.sources import tencent_quotes as tencent_quotes_module
from radar.sources.tencent_quotes import (
    fetch_tencent_quotes,
    fetch_tencent_quotes_concurrent,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 17, 10, 0, 2, tzinfo=SHANGHAI_TZ)


def tencent_line(
    code,
    name="测试证券",
    price="10.00",
    previous_close="9.90",
    open_price="9.95",
    change_percent="1.20",
    high_price="10.20",
    low_price="9.80",
    source_time="20260717100000",
    turnover_amount="12345.60",
    packed_amount=None,
    turnover_rate="2.30",
    market_cap="456.70",
    total_shares="4567000000",
    currency="CNY",
    upper_limit_price="10.89",
    lower_limit_price="8.91",
    volume_ratio="1.50",
    trading_status="",
):
    fields = [""] * 83
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[4] = previous_close
    fields[5] = open_price
    fields[30] = source_time
    fields[32] = change_percent
    fields[33] = high_price
    fields[34] = low_price
    if packed_amount is not None:
        fields[35] = f"{price}/0/{packed_amount}"
    fields[37] = turnover_amount
    fields[38] = turnover_rate
    fields[40] = trading_status
    fields[45] = market_cap
    fields[47] = upper_limit_price
    fields[48] = lower_limit_price
    fields[49] = volume_ratio
    fields[73] = total_shares
    fields[82] = currency
    return f'v_test_{code}="{"~".join(fields)}";'


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
        result = self.responder(url, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class SecurityMasterSourceTests(unittest.TestCase):
    def build_providers(self):
        def sse(symbol):
            name = "沪市主板" if symbol == "主板A股" else "科创公司"
            code = "600001" if symbol == "主板A股" else "688001"
            return pd.DataFrame([{
                "证券代码": code,
                "证券简称": name,
                "证券全称": f"{name}股份有限公司",
                "公司简称": name,
                "公司全称": f"{name}股份有限公司",
                "上市日期": "2020-01-02",
            }])

        return SecurityMasterProviders(
            sse=sse,
            szse=lambda _symbol: pd.DataFrame([{
                "板块": "主板",
                "A股代码": "000001",
                "A股简称": "深市公司",
                "A股上市日期": "1991-04-03",
                "A股总股本": 100.0,
                "A股流通股本": 80.0,
                "所属行业": "银行业",
            }]),
            bse=lambda: pd.DataFrame([{
                "证券代码": "920001",
                "证券简称": "北交公司",
                "总股本": 50.0,
                "流通股本": 40.0,
                "上市日期": "2025-01-06",
                "所属行业": "制造业",
                "地区": "北京",
                "报告日期": "2026-07-17",
            }]),
        )

    def test_official_boards_are_combined_without_inventing_unified_industry(self):
        batch = fetch_security_master(
            radar_run_id="run-1",
            batch_id="master-1",
            as_of=AS_OF,
            providers=self.build_providers(),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.returned_count, 4)
        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual(batch.meta.issues, [])
        by_symbol = {item.symbol: item for item in batch.items}
        self.assertEqual(by_symbol["688001"].board, "科创板")
        self.assertEqual(by_symbol["000001"].source_industry, "银行业")
        self.assertEqual(by_symbol["920001"].source_report_date, date(2026, 7, 17))
        self.assertNotIn("industry", by_symbol["000001"].model_fields_set)
        self.assertEqual(by_symbol["000001"].source_fields["所属行业"], "银行业")

    def test_provider_failure_is_preserved_as_issue(self):
        master_providers = self.build_providers()
        failing = SecurityMasterProviders(
            sse=master_providers.sse,
            szse=Mock(side_effect=RuntimeError("upstream unavailable")),
            bse=master_providers.bse,
        )

        batch = fetch_security_master(
            radar_run_id="run-1",
            batch_id="master-1",
            as_of=AS_OF,
            providers=failing,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.returned_count, 3)
        self.assertIsNone(batch.meta.row_coverage)
        self.assertEqual(batch.meta.issues[0].code, "source_request_failed")
        self.assertEqual(batch.meta.issues[0].source, "szse")


class TencentQuoteSourceTests(unittest.TestCase):
    def test_transient_request_failure_retries_same_batch_once(self):
        def responder(_url, call_number):
            if call_number == 1:
                return TimeoutError("request timed out")
            return tencent_line("000001")

        session = FakeSession(responder)
        batch = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[0]["url"].startswith("https://"))
        self.assertEqual(batch.meta.returned_count, 1)
        self.assertNotIn(
            "batch_request_failed",
            {issue.code for issue in batch.meta.issues},
        )
        self.assertNotIn(
            "batch_retry_failed",
            {issue.code for issue in batch.meta.issues},
        )

    def test_incomplete_quote_batch_is_retried_once(self):
        def responder(_url, call_number):
            if call_number == 1:
                return tencent_line("000001")
            return tencent_line("000001") + tencent_line("000002")

        session = FakeSession(responder)
        batch = fetch_tencent_quotes(
            ["000001", "000002"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(batch.meta.returned_count, 2)
        self.assertNotIn(
            "missing_symbols",
            {issue.code for issue in batch.meta.issues},
        )

    def test_same_response_preserves_ohlc_without_extra_request(self):
        session = FakeSession(lambda _url, _call: tencent_line("000001"))

        batch = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        quote = batch.items[0]
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(quote.previous_close, 9.90)
        self.assertEqual(quote.open_price, 9.95)
        self.assertEqual(quote.high_price, 10.20)
        self.assertEqual(quote.low_price, 9.80)
        self.assertEqual(quote.upper_limit_price_source, 10.89)
        self.assertEqual(quote.lower_limit_price_source, 8.91)
        self.assertNotIn("previous_close", quote.REQUIRED_FIELDS)
        self.assertNotIn(
            "upper_limit_price_source",
            quote.REQUIRED_FIELDS,
        )
        self.assertNotIn(
            "lower_limit_price_source",
            quote.REQUIRED_FIELDS,
        )

    def test_same_response_preserves_explicit_non_trading_status(self):
        for raw_status, expected in (
            ("S", QuoteTradingStatus.SUSPENDED),
            ("D", QuoteTradingStatus.DELISTED),
            ("U", QuoteTradingStatus.UNLISTED),
            ("", None),
        ):
            with self.subTest(raw_status=raw_status):
                session = FakeSession(
                    lambda _url, _call, status=raw_status: tencent_line(
                        "000001",
                        trading_status=status,
                    )
                )

                quote = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=session,
                    clock=lambda: FETCHED_AT,
                ).items[0]

                self.assertEqual(quote.trading_status, expected)

    def test_ohlc_preserves_true_zero_and_rejects_invalid_values(self):
        cases = (
            ("0", 0.0),
            ("", None),
            ("nan", None),
            ("inf", None),
            ("-1", None),
        )
        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                session = FakeSession(
                    lambda _url, _call, value=raw_value: tencent_line(
                        "000001",
                        previous_close=value,
                        open_price=value,
                        high_price=value,
                        low_price=value,
                        upper_limit_price=value,
                        lower_limit_price=value,
                    )
                )

                quote = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=session,
                    clock=lambda: FETCHED_AT,
                ).items[0]

                self.assertEqual(quote.previous_close, expected)
                self.assertEqual(quote.open_price, expected)
                self.assertEqual(quote.high_price, expected)
                self.assertEqual(quote.low_price, expected)
                self.assertEqual(
                    quote.upper_limit_price_source,
                    expected,
                )
                self.assertEqual(
                    quote.lower_limit_price_source,
                    expected,
                )

    def test_quotes_are_batched_at_100_and_preserve_true_zero(self):
        symbols = [f"{number:06d}" for number in range(1, 102)]

        def responder(url, _call_number):
            query = url.split("q=", 1)[1]
            codes = [item[2:] for item in query.split(",")]
            rows = []
            for code in codes:
                if code == "000001":
                    rows.append(tencent_line(
                        code,
                        price="0",
                        change_percent="0",
                        turnover_amount="0",
                        packed_amount="0",
                        turnover_rate="0",
                        market_cap="0",
                        volume_ratio="0",
                    ))
                else:
                    rows.append(tencent_line(code))
            return "".join(rows)

        session = FakeSession(responder)
        batch = fetch_tencent_quotes(
            symbols,
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(len(session.calls), 2)
        self.assertLessEqual(
            len(session.calls[0]["url"].split("q=", 1)[1].split(",")),
            100,
        )
        self.assertEqual(batch.meta.expected_count, 101)
        self.assertEqual(batch.meta.returned_count, 101)
        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual(batch.meta.required_field_coverage["price"], 1.0)
        first = {item.symbol: item for item in batch.items}["000001"]
        self.assertEqual(first.price, 0.0)
        self.assertEqual(first.turnover_amount_cny, 0.0)
        self.assertEqual(
            first.turnover_amount_unit_status,
            UnitVerificationStatus.VERIFIED,
        )
        self.assertEqual(first.missing_fields(), ())
        self.assertFalse(session.trust_env)

    def test_concurrent_quotes_keep_original_source_times(self):
        calls = []

        def fake_fetch(symbols, **kwargs):
            calls.append((tuple(symbols), kwargs["as_of"]))
            index = len(calls)
            source_time = AS_OF.replace(second=AS_OF.second + index)
            fetched_at = FETCHED_AT.replace(second=FETCHED_AT.second + index)
            items = [QuoteSnapshot(
                symbol=symbol,
                name=f"证券{symbol}",
                sourceTime=source_time,
                fetchedAt=fetched_at,
                price=10.0,
                changePercent=1.0,
            ) for symbol in symbols]
            return SourceBatch[QuoteSnapshot](
                meta=RadarBatchMeta(
                    radarRunId=kwargs["radar_run_id"],
                    batchId=kwargs["batch_id"],
                    source="tencent_finance",
                    asOf=kwargs["as_of"],
                    sourceTime=source_time,
                    fetchedAt=fetched_at,
                    expectedCount=len(items),
                    returnedCount=len(items),
                    rowCoverage=1.0,
                ),
                items=items,
            )

        with patch.object(
            tencent_quotes_module,
            "fetch_tencent_quotes",
            side_effect=fake_fetch,
        ):
            batch = fetch_tencent_quotes_concurrent(
                ["000001", "000002", "000003"],
                radar_run_id="run-1",
                batch_id="quote-1",
                as_of=AS_OF,
                chunk_size=2,
                max_workers=2,
                clock=lambda: FETCHED_AT,
            )

        self.assertEqual(batch.meta.as_of, AS_OF)
        self.assertEqual(
            batch.meta.source_time,
            AS_OF.replace(second=AS_OF.second + 2),
        )
        self.assertEqual(
            batch.meta.fetched_at,
            FETCHED_AT.replace(second=FETCHED_AT.second + 2),
        )
        self.assertEqual(batch.meta.returned_count, 3)
        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual({as_of for _symbols, as_of in calls}, {AS_OF})

    def test_concurrent_quotes_fail_closed_when_child_batch_raises(self):
        def fake_fetch(symbols, **_kwargs):
            if "000003" in symbols:
                raise TimeoutError("request timed out")
            items = [QuoteSnapshot(
                symbol=symbol,
                name=f"证券{symbol}",
                sourceTime=AS_OF,
                fetchedAt=FETCHED_AT,
                price=10.0,
                changePercent=1.0,
            ) for symbol in symbols]
            return SourceBatch[QuoteSnapshot](
                meta=RadarBatchMeta(
                    radarRunId="run-1",
                    batchId="quote-child",
                    source="tencent_finance",
                    asOf=AS_OF,
                    sourceTime=AS_OF,
                    fetchedAt=FETCHED_AT,
                    expectedCount=len(items),
                    returnedCount=len(items),
                    rowCoverage=1.0,
                ),
                items=items,
            )

        with patch.object(
            tencent_quotes_module,
            "fetch_tencent_quotes",
            side_effect=fake_fetch,
        ):
            batch = fetch_tencent_quotes_concurrent(
                ["000001", "000002", "000003", "000004"],
                radar_run_id="run-1",
                batch_id="quote-1",
                as_of=AS_OF,
                chunk_size=2,
                max_workers=2,
                clock=lambda: FETCHED_AT,
            )

        issue_codes = {issue.code for issue in batch.meta.issues}
        self.assertIn("parallel_batch_failed", issue_codes)
        self.assertIn("missing_symbols", issue_codes)
        self.assertEqual(batch.meta.expected_count, 4)
        self.assertEqual(batch.meta.returned_count, 2)

    def test_turnover_amount_unit_is_verified_by_same_response_amount(self):
        session = FakeSession(lambda _url, _call: tencent_line(
            "000001",
            turnover_amount="12345.60",
            packed_amount="123456789",
        ))

        batch = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        quote = batch.items[0]
        self.assertEqual(quote.turnover_amount_source, 12345.6)
        self.assertEqual(quote.turnover_amount_cny, 123456789.0)
        self.assertEqual(
            quote.turnover_amount_unit_status,
            UnitVerificationStatus.VERIFIED,
        )

    def test_market_cap_unit_is_verified_by_same_response_total_shares(self):
        session = FakeSession(lambda _url, _call: tencent_line(
            "000001",
            price="10.00",
            market_cap="456.70",
            total_shares="4567000000",
            currency="CNY",
        ))

        batch = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )
        quote = batch.items[0]
        payload = quote.model_dump(by_alias=True)

        self.assertEqual(payload.get("marketCapCny"), 45_670_000_000.0)
        self.assertEqual(payload.get("marketCapUnitStatus"), "verified")
        self.assertEqual(payload.get("totalSharesSource"), 4_567_000_000.0)
        self.assertEqual(payload.get("currency"), "CNY")
        self.assertEqual(
            batch.meta.required_field_coverage.get("market_cap_cny"),
            1.0,
        )
        self.assertEqual(
            batch.meta.required_field_coverage.get("total_shares_source"),
            1.0,
        )

    def test_market_cap_cross_field_mismatch_stays_unverified(self):
        quote = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=FakeSession(lambda _url, _call: tencent_line(
                "000001",
                price="10.00",
                market_cap="456.70",
                total_shares="1",
                currency="CNY",
            )),
            clock=lambda: FETCHED_AT,
        ).items[0]

        self.assertIsNone(quote.market_cap_cny)
        self.assertEqual(
            quote.market_cap_unit_status,
            UnitVerificationStatus.UNVERIFIED,
        )

    def test_market_cap_rounding_tolerance_accepts_first_party_snapshot(self):
        quote = fetch_tencent_quotes(
            ["000001"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=FakeSession(lambda _url, _call: tencent_line(
                "000001",
                price="11.19",
                market_cap="2171.52",
                total_shares="19405918198",
                currency="CNY",
            )),
            clock=lambda: FETCHED_AT,
        ).items[0]

        self.assertEqual(
            quote.market_cap_unit_status,
            UnitVerificationStatus.VERIFIED,
        )
        self.assertEqual(quote.market_cap_cny, 217_152_000_000.0)

    def test_market_cap_exact_half_unit_rounding_boundary_is_not_rejected(self):
        cases = (
            ("20.67", "31.00", "150000000"),
            ("33.85", "37.23", "110000000"),
        )
        for price, market_cap, total_shares in cases:
            with self.subTest(price=price):
                quote = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=FakeSession(
                        lambda _url, _call: tencent_line(
                            "000001",
                            price=price,
                            market_cap=market_cap,
                            total_shares=total_shares,
                            currency="CNY",
                        )
                    ),
                    clock=lambda: FETCHED_AT,
                ).items[0]

                self.assertEqual(
                    quote.market_cap_unit_status,
                    UnitVerificationStatus.VERIFIED,
                )

    def test_missing_or_non_cny_market_cap_evidence_stays_unverified(self):
        cases = (
            {"total_shares": "", "currency": "CNY"},
            {"total_shares": "4567000000", "currency": "USD"},
            {"market_cap": "0", "total_shares": "0", "currency": "CNY"},
        )
        for values in cases:
            with self.subTest(values=values):
                quote = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=FakeSession(
                        lambda _url, _call, row=values: tencent_line(
                            "000001",
                            **row,
                        )
                    ),
                    clock=lambda: FETCHED_AT,
                ).items[0]

                self.assertIsNone(quote.market_cap_cny)
                self.assertEqual(
                    quote.market_cap_unit_status,
                    UnitVerificationStatus.UNVERIFIED,
                )

    def test_invalid_market_cap_source_never_enters_quote_contract(self):
        for raw_value in ("nan", "inf", "-1"):
            with self.subTest(raw_value=raw_value):
                quote = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=FakeSession(
                        lambda _url, _call, value=raw_value: tencent_line(
                            "000001",
                            market_cap=value,
                        )
                    ),
                    clock=lambda: FETCHED_AT,
                ).items[0]

                self.assertIsNone(quote.market_cap_source)
                self.assertIsNone(quote.market_cap_cny)
                self.assertEqual(
                    quote.market_cap_unit_status,
                    UnitVerificationStatus.UNVERIFIED,
                )

    def test_unverifiable_turnover_amount_keeps_cny_missing(self):
        cases = (
            {
                "turnover_amount": "100",
                "packed_amount": None,
            },
            {
                "turnover_amount": "100",
                "packed_amount": "1",
            },
            {
                "turnover_amount": "-1",
                "packed_amount": "-10000",
            },
        )
        for case in cases:
            with self.subTest(case=case):
                session = FakeSession(
                    lambda _url, _call, values=case: tencent_line(
                        "000001",
                        **values,
                    )
                )

                batch = fetch_tencent_quotes(
                    ["000001"],
                    radar_run_id="run-1",
                    batch_id="quote-1",
                    as_of=AS_OF,
                    session=session,
                    clock=lambda: FETCHED_AT,
                )

                quote = batch.items[0]
                self.assertIsNone(quote.turnover_amount_cny)
                self.assertEqual(
                    quote.turnover_amount_unit_status,
                    UnitVerificationStatus.UNVERIFIED,
                )

    def test_existing_market_prefix_rules_cover_shenzhen_shanghai_bse_and_etf(self):
        captured_queries = []

        def responder(url, _call_number):
            query = url.split("q=", 1)[1]
            captured_queries.extend(query.split(","))
            return "".join(
                tencent_line(item[2:])
                for item in query.split(",")
            )

        batch = fetch_tencent_quotes(
            ["000001", "600001", "920001", "510300", "159915"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=FakeSession(responder),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.returned_count, 5)
        self.assertEqual(
            captured_queries,
            ["sz000001", "sh600001", "bj920001", "sh510300", "sz159915"],
        )

    def test_partial_batch_failure_and_missing_symbol_are_explicit(self):
        def responder(_url, call_number):
            if call_number == 1:
                return tencent_line("000001")
            return TimeoutError("request timed out")

        session = FakeSession(responder)
        batch = fetch_tencent_quotes(
            ["000001", "000002", "000003"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            batch_size=2,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.expected_count, 3)
        self.assertEqual(batch.meta.returned_count, 1)
        self.assertAlmostEqual(batch.meta.row_coverage, 1 / 3)
        codes = {issue.code for issue in batch.meta.issues}
        self.assertIn("batch_request_failed", codes)
        self.assertIn("missing_symbols", codes)

    def test_invalid_symbols_are_rejected_before_request(self):
        session = FakeSession(lambda _url, _call: tencent_line("000001"))

        batch = fetch_tencent_quotes(
            ["000001", "bad-code", "hk00700"],
            radar_run_id="run-1",
            batch_id="quote-1",
            as_of=AS_OF,
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(batch.meta.expected_count, 1)
        self.assertEqual(batch.meta.returned_count, 1)
        self.assertEqual(batch.meta.issues[0].code, "invalid_symbols")


class EtfRegistrySourceTests(unittest.TestCase):
    @staticmethod
    def official_cn_calendar(_market, day):
        return Mock(kind="closed" if day.weekday() >= 5 else "full")

    def test_exchange_fields_are_preserved_without_fake_unified_classification(self):
        providers = EtfRegistryProviders(
            sse=lambda _date: pd.DataFrame([{
                "序号": 1,
                "基金代码": "510300",
                "基金简称": "沪深300ETF",
                "ETF类型": "股票ETF",
                "统计日期": "2026-07-17",
                "基金份额": 100.0,
            }]),
            szse=lambda: pd.DataFrame([{
                "基金代码": "159915",
                "基金简称": "创业板ETF",
                "基金类别": "ETF",
                "投资类别": "股票型",
                "上市日期": "2011-12-09",
                "基金份额": 80.0,
                "基金管理人": "测试基金公司",
                "基金发起人": "测试发起人",
                "基金托管人": "测试托管人",
                "净值": 2.0,
            }]),
        )

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 10, 0, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 17),
            providers=providers,
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertEqual(batch.meta.returned_count, 2)
        by_symbol = {item.symbol: item for item in batch.items}
        self.assertEqual(by_symbol["510300"].source_type, "股票ETF")
        self.assertIsNone(by_symbol["510300"].investment_type)
        self.assertEqual(by_symbol["159915"].source_type, "ETF")
        self.assertEqual(by_symbol["159915"].investment_type, "股票型")
        self.assertEqual(by_symbol["159915"].manager, "测试基金公司")
        self.assertNotIn("active_or_passive", by_symbol["159915"].model_fields_set)

    def test_sse_uses_latest_completed_official_trading_day_before_as_of(self):
        requested_dates = []

        def sse(report_date):
            requested_dates.append(report_date)
            return pd.DataFrame([{
                "基金代码": "510300",
                "基金简称": "沪深300ETF",
                "ETF类型": "股票ETF",
                "统计日期": "2026-07-17",
                "基金份额": 100.0,
            }])

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=EtfRegistryProviders(
                sse=sse,
                szse=lambda: pd.DataFrame(),
            ),
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertEqual(requested_dates, ["20260717"])
        self.assertEqual(batch.meta.expected_count, 1)
        self.assertEqual(batch.meta.issues, [])
        self.assertEqual(batch.items[0].source_report_date, date(2026, 7, 17))

    def test_sse_future_report_date_is_rejected(self):
        providers = EtfRegistryProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "基金简称": "沪深300ETF",
                "ETF类型": "股票ETF",
                "统计日期": "2026-07-20",
                "基金份额": 100.0,
            }]),
            szse=lambda: pd.DataFrame(),
        )

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=providers,
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertEqual(batch.meta.expected_count, None)
        self.assertEqual(batch.meta.returned_count, 0)
        self.assertIn(
            "future_source_report_date",
            {issue.code for issue in batch.meta.issues},
        )

    def test_sse_stale_report_date_is_rejected(self):
        providers = EtfRegistryProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "基金简称": "沪深300ETF",
                "ETF类型": "股票ETF",
                "统计日期": "2026-06-30",
                "基金份额": 100.0,
            }]),
            szse=lambda: pd.DataFrame(),
        )

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=providers,
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertEqual(batch.meta.expected_count, None)
        self.assertEqual(batch.meta.returned_count, 0)
        self.assertIn(
            "stale_source_report_date",
            {issue.code for issue in batch.meta.issues},
        )

    def test_sse_mismatched_report_date_is_rejected(self):
        providers = EtfRegistryProviders(
            sse=lambda _date: pd.DataFrame([{
                "基金代码": "510300",
                "基金简称": "沪深300ETF",
                "ETF类型": "股票ETF",
                "统计日期": "2026-07-16",
                "基金份额": 100.0,
            }]),
            szse=lambda: pd.DataFrame(),
        )

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=providers,
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertEqual(batch.meta.expected_count, None)
        self.assertEqual(batch.meta.returned_count, 0)
        self.assertIn(
            "mismatched_source_report_date",
            {issue.code for issue in batch.meta.issues},
        )

    def test_sse_empty_result_cannot_be_reported_as_healthy(self):
        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=EtfRegistryProviders(
                sse=lambda _date: pd.DataFrame(),
                szse=lambda: pd.DataFrame(),
            ),
            clock=lambda: FETCHED_AT,
            calendar_day_provider=self.official_cn_calendar,
        )

        self.assertIsNone(batch.meta.expected_count)
        self.assertIn(
            "empty_source_result",
            {issue.code for issue in batch.meta.issues},
        )

    def test_sse_request_is_blocked_when_official_calendar_is_unknown(self):
        sse = Mock(return_value=pd.DataFrame())

        batch = fetch_etf_registry(
            radar_run_id="run-1",
            batch_id="etf-1",
            as_of=datetime(2026, 7, 20, 15, 18, tzinfo=SHANGHAI_TZ),
            snapshot_date=date(2026, 7, 20),
            providers=EtfRegistryProviders(
                sse=sse,
                szse=lambda: pd.DataFrame(),
            ),
            clock=lambda: FETCHED_AT,
            calendar_day_provider=lambda _market, _day: Mock(kind="unknown"),
        )

        sse.assert_not_called()
        self.assertIsNone(batch.meta.expected_count)
        self.assertIn(
            "source_request_failed",
            {issue.code for issue in batch.meta.issues},
        )


if __name__ == "__main__":
    unittest.main()
