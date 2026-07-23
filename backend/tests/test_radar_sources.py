import unittest
from datetime import date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from radar.sources.etf_registry import EtfRegistryProviders, fetch_etf_registry
from radar.sources.security_master import (
    SecurityMasterProviders,
    fetch_security_master,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 17, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 17, 10, 0, 2, tzinfo=SHANGHAI_TZ)


def tencent_line(
    code,
    name="测试证券",
    price="10.00",
    change_percent="1.20",
    source_time="20260717100000",
    turnover_amount="12345.60",
    turnover_rate="2.30",
    market_cap="456.70",
    volume_ratio="1.50",
):
    fields = [""] * 50
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[30] = source_time
    fields[32] = change_percent
    fields[37] = turnover_amount
    fields[38] = turnover_rate
    fields[45] = market_cap
    fields[49] = volume_ratio
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
        self.assertEqual(first.missing_fields(), ())
        self.assertFalse(session.trust_env)

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
