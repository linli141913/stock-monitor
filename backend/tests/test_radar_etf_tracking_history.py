import math
import statistics
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from unittest.mock import patch

from radar.sources.etf_tracking_history import (
    build_chinaamc_nav_history,
    build_csindex_price_history,
    build_huatai_pb_nav_history,
    build_verified_etf_tracking_window,
    fetch_chinaamc_nav_history,
    fetch_csindex_price_history,
    fetch_huatai_pb_nav_history,
    fetch_supported_manager_nav_history,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FETCHED_AT = datetime(2026, 9, 2, 16, 30, tzinfo=SHANGHAI_TZ)


class EtfTrackingHistoryTests(unittest.TestCase):
    def dates(self):
        return tuple(date(2026, 6, 1) + timedelta(days=i) for i in range(61))

    def nav_payload(self, dates=None):
        selected = dates or self.dates()
        values = [100.0]
        for index in range(1, len(selected)):
            values.append(values[-1] * (1.0 + 0.001 + (index % 3) * 0.0001))
        return {
            "currentPage": 1,
            "totalPage": 1,
            "dataList": [
                {
                    "date": day.isoformat(),
                    "fundcode": "510300",
                    "netvalue": f"{value:.8f}",
                    "totalnetvalue": f"{value:.8f}",
                }
                for day, value in zip(selected, values)
            ],
        }

    def chinaamc_payload(self, dates=None):
        selected = dates or self.dates()
        return {
            "ShowData": [day.isoformat() for day in selected],
            "danweijingzhiName": [
                f"{3.0 + index * 0.001:.4f}"
                for index, _day in enumerate(selected)
            ],
            "leijiJingzhiName": [
                f"{4.5 + index * 0.0012:.4f}"
                for index, _day in enumerate(selected)
            ],
        }

    @staticmethod
    def chinaamc_product_page(symbol="510050"):
        return (
            '<div id="codetext" class="t">基金代码：'
            f'{symbol}</div><a href="/fund/{symbol}/index.shtml">基金概要</a>'
        )

    def index_payload(self, dates=None):
        selected = dates or self.dates()
        values = [1000.0]
        for index in range(1, len(selected)):
            values.append(values[-1] * (1.0 + 0.0008 + (index % 2) * 0.0001))
        return {
            "code": "200",
            "data": [
                {
                    "tradeDate": day.strftime("%Y%m%d"),
                    "indexCode": "000300",
                    "indexNameCnAll": "沪深300指数",
                    "close": value,
                }
                for day, value in zip(selected, values)
            ],
        }

    def sources(self):
        dates = self.dates()
        nav = build_huatai_pb_nav_history(
            symbol="510300",
            expected_trade_dates=dates,
            payload=self.nav_payload(),
            fetched_at=FETCHED_AT,
        )
        index = build_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=dates,
            payload=self.index_payload(),
            fetched_at=FETCHED_AT,
        )
        return nav, index

    def test_exact_61_point_window_computes_three_distinct_metrics(self):
        nav, index = self.sources()

        result = build_verified_etf_tracking_window(
            symbol="510300",
            index_code="000300",
            expected_trade_dates=self.dates(),
            nav_history=nav,
            index_history=index,
            computed_at=FETCHED_AT,
        )

        nav_values = [item.accumulated_nav for item in nav.points]
        index_values = [item.close for item in index.points]
        nav_returns = [
            nav_values[i] / nav_values[i - 1] - 1
            for i in range(1, len(nav_values))
        ]
        index_returns = [
            index_values[i] / index_values[i - 1] - 1
            for i in range(1, len(index_values))
        ]
        active = [a - b for a, b in zip(nav_returns, index_returns)]

        self.assertTrue(nav.formal_usable)
        self.assertTrue(index.formal_usable)
        self.assertTrue(result.formal_usable)
        self.assertEqual(result.window_trading_days, 60)
        self.assertEqual(result.sample_count, 60)
        self.assertAlmostEqual(
            result.tracking_difference,
            nav_values[-1] / nav_values[0]
            - index_values[-1] / index_values[0],
        )
        self.assertAlmostEqual(
            result.tracking_error,
            statistics.stdev(active) * math.sqrt(250),
        )
        self.assertIsNotNone(result.index_correlation)
        self.assertEqual(result.reasons, ())

    def test_missing_nav_day_keeps_metrics_null(self):
        dates = self.dates()
        nav = build_huatai_pb_nav_history(
            symbol="510300",
            expected_trade_dates=dates,
            payload=self.nav_payload(dates[:-1]),
            fetched_at=FETCHED_AT,
        )
        _, index = self.sources()

        result = build_verified_etf_tracking_window(
            symbol="510300",
            index_code="000300",
            expected_trade_dates=dates,
            nav_history=nav,
            index_history=index,
            computed_at=FETCHED_AT,
        )

        self.assertFalse(nav.formal_usable)
        self.assertIn("fund_nav_window_incomplete", nav.reasons)
        self.assertFalse(result.formal_usable)
        self.assertIsNone(result.tracking_difference)
        self.assertIsNone(result.tracking_error)
        self.assertIsNone(result.index_correlation)

    def test_identity_mismatch_duplicate_and_future_rows_fail_closed(self):
        dates = self.dates()
        wrong = self.nav_payload()
        wrong["dataList"][0]["fundcode"] = "510500"
        duplicate = self.index_payload()
        duplicate["data"].append(dict(duplicate["data"][0]))

        nav = build_huatai_pb_nav_history(
            symbol="510300",
            expected_trade_dates=dates,
            payload=wrong,
            fetched_at=FETCHED_AT,
        )
        index = build_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=dates,
            payload=duplicate,
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(nav.formal_usable)
        self.assertIn("fund_nav_identity_conflict", nav.reasons)
        self.assertFalse(index.formal_usable)
        self.assertIn("index_price_duplicate_date", index.reasons)
        with self.assertRaisesRegex(ValueError, "tracking_trade_date_future"):
            build_huatai_pb_nav_history(
                symbol="510300",
                expected_trade_dates=(date(2026, 9, 3),),
                payload={"dataList": []},
                fetched_at=FETCHED_AT,
            )

    def test_exact_price_index_name_and_positive_values_are_required(self):
        payload = self.index_payload()
        payload["data"][0]["indexNameCnAll"] = "中证500指数"
        payload["data"][1]["close"] = 0

        result = build_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=self.dates(),
            payload=payload,
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIn("index_price_identity_conflict", result.reasons)
        self.assertIn("index_price_invalid", result.reasons)

    def test_tracking_window_requires_60_returns_not_60_prices(self):
        dates = self.dates()[:-1]
        nav = build_huatai_pb_nav_history(
            symbol="510300",
            expected_trade_dates=dates,
            payload=self.nav_payload(dates),
            fetched_at=FETCHED_AT,
        )
        index = build_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=dates,
            payload=self.index_payload(dates),
            fetched_at=FETCHED_AT,
        )

        result = build_verified_etf_tracking_window(
            symbol="510300",
            index_code="000300",
            expected_trade_dates=dates,
            nav_history=nav,
            index_history=index,
            computed_at=FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIn("tracking_expected_window_not_60_returns", result.reasons)

    def test_official_fetchers_preserve_request_scope_and_hashes(self):
        outer = self

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "huatai-pb.com" in url:
                    return Response(outer.nav_payload())
                return Response(outer.index_payload())

        session = Session()
        nav = fetch_huatai_pb_nav_history(
            symbol="510300",
            expected_trade_dates=self.dates(),
            session=session,
            clock=lambda: FETCHED_AT,
        )
        index = fetch_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=self.dates(),
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertTrue(nav.formal_usable)
        self.assertTrue(index.formal_usable)
        self.assertEqual(len(nav.content_sha256), 64)
        self.assertEqual(len(index.content_sha256), 64)
        self.assertEqual(session.calls[0][1]["params"]["fundcode"], "510300")
        self.assertEqual(session.calls[1][1]["params"]["indexCode"], "000300")

    def test_official_fetcher_stops_after_one_timeout_retry(self):
        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                raise requests.ReadTimeout("stable")

        session = Session()
        result = fetch_csindex_price_history(
            index_code="000300",
            index_name="沪深300指数",
            expected_trade_dates=self.dates(),
            session=session,
            clock=lambda: FETCHED_AT,
        )

        self.assertFalse(result.formal_usable)
        self.assertIn("index_price_source_failed", result.reasons)
        self.assertEqual(session.calls, 2)

    def test_chinaamc_official_arrays_require_page_identity_and_exact_dates(self):
        result = build_chinaamc_nav_history(
            symbol="510050",
            expected_trade_dates=self.dates(),
            payload=self.chinaamc_payload(),
            product_page=self.chinaamc_product_page(),
            fetched_at=FETCHED_AT,
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(len(result.points), 61)
        self.assertEqual(result.points[0].trade_date, self.dates()[0])
        self.assertEqual(result.points[-1].unit_nav, 3.06)
        self.assertEqual(
            result.source_contract_id,
            "chinaamc-official-fund-nav-history-v1",
        )

        wrong_identity = build_chinaamc_nav_history(
            symbol="510050",
            expected_trade_dates=self.dates(),
            payload=self.chinaamc_payload(),
            product_page=self.chinaamc_product_page("510330"),
            fetched_at=FETCHED_AT,
        )
        malformed = self.chinaamc_payload()
        malformed["leijiJingzhiName"].pop()
        wrong_length = build_chinaamc_nav_history(
            symbol="510050",
            expected_trade_dates=self.dates(),
            payload=malformed,
            product_page=self.chinaamc_product_page(),
            fetched_at=FETCHED_AT,
        )

        self.assertFalse(wrong_identity.formal_usable)
        self.assertIn("fund_nav_identity_conflict", wrong_identity.reasons)
        self.assertFalse(wrong_length.formal_usable)
        self.assertIn("fund_nav_source_failed", wrong_length.reasons)

    def test_chinaamc_fetcher_uses_official_product_and_history_paths(self):
        outer = self

        class Response:
            def __init__(self, payload=None, text=""):
                self.payload = payload
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if url.endswith("index.shtml"):
                    return Response(text=outer.chinaamc_product_page())
                return Response(payload=outer.chinaamc_payload())

        timestamps = iter((
            FETCHED_AT - timedelta(seconds=3),
            FETCHED_AT,
        ))
        session = Session()
        result = fetch_chinaamc_nav_history(
            symbol="510050",
            expected_trade_dates=self.dates(),
            session=session,
            clock=lambda: next(timestamps),
        )

        self.assertTrue(result.formal_usable)
        self.assertEqual(result.fetched_at, FETCHED_AT)
        self.assertEqual(
            [url for url, _kwargs in session.calls],
            [
                "https://fund.chinaamc.com/fund/510050/index.shtml",
                "https://fund.chinaamc.com/fund/510050/zoust_all.js",
            ],
        )

    def test_manager_dispatch_only_accepts_explicit_official_adapters(self):
        expected = self.dates()
        with (
            patch(
                "radar.sources.etf_tracking_history."
                "fetch_chinaamc_nav_history"
            ) as chinaamc,
            patch(
                "radar.sources.etf_tracking_history."
                "fetch_huatai_pb_nav_history"
            ) as huatai,
        ):
            fetch_supported_manager_nav_history(
                manager="华夏基金管理有限公司",
                symbol="510050",
                expected_trade_dates=expected,
            )
            fetch_supported_manager_nav_history(
                manager="华泰柏瑞基金管理有限公司",
                symbol="510300",
                expected_trade_dates=expected,
            )

        chinaamc.assert_called_once()
        huatai.assert_called_once()
        with self.assertRaisesRegex(
            ValueError, "fund_nav_provider_not_supported"
        ):
            fetch_supported_manager_nav_history(
                manager="未知基金管理有限公司",
                symbol="510500",
                expected_trade_dates=expected,
            )
