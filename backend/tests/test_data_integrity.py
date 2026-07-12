import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import ai_analysis
import main
import news_api
from real_data_fetcher import RealDataFetcher


class NewsIntegrityTests(unittest.TestCase):
    @patch.object(news_api, "get_real_news_from_db", return_value=[])
    def test_news_api_returns_empty_when_no_real_news_exists(self, _mock_get_real_news):
        self.assertEqual(news_api.get_integrated_news("all"), [])

    def test_news_source_classification_is_explicitly_heuristic(self):
        cases = (
            ("巨潮公告", "S", "official_announcement"),
            ("某证券研报", "A", "institution_research"),
            ("新浪财经", "B", "media_report"),
            ("未知来源", "C", "other"),
        )

        for source, level, content_type in cases:
            with self.subTest(source=source):
                result = news_api.classify_news_source(source)

                self.assertEqual(result["credibility_level"], level)
                self.assertEqual(result["content_type"], content_type)
                self.assertEqual(result["credibility_method"], "source_rule")
                self.assertEqual(result["verification_status"], "未独立交叉验证")

    def test_news_contract_keeps_traceable_source_fields_without_ai_claims(self):
        item = {
            "id": "real-news-1",
            "title": "公司公告标题",
            "content": "公告原文摘要",
            "source": "巨潮公告",
            "url": "https://example.com/notice",
            "ctime": 1_700_000_000,
            "symbol": "000725",
        }

        with patch.object(news_api.database, "get_latest_crawled_news", return_value=[item]):
            result = news_api.get_real_news_from_db("all")[0]

        self.assertEqual(result["source"], "巨潮公告")
        self.assertEqual(result["original_link"], item["url"])
        self.assertTrue(result["publish_time"])
        self.assertEqual(result["source_summary"], item["content"])
        self.assertEqual(result["impact_method"], "heuristic")
        self.assertNotIn("ai_summary", result)
        self.assertNotIn("ai_impact", result)
        self.assertNotIn("ai_verification_status", result)

    def test_frontend_news_contract_marks_unanalysed_content_truthfully(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        route = (root / "stock-monitor/src/app/api/news/route.ts").read_text(
            encoding="utf-8"
        )
        stock_types = (root / "stock-monitor/src/types/stock.ts").read_text(
            encoding="utf-8"
        )
        radar_card = (
            root / "stock-monitor/src/components/industry/RadarNewsCard.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("sentiment: '未分析'", route)
        self.assertIn('"未分析"', stock_types)
        self.assertIn("来源规则评级", radar_card)
        self.assertIn("规则影响分析", radar_card)
        self.assertNotIn("AI 交叉验证与解析", radar_card)

    def test_frontend_separates_market_times_and_uses_dynamic_calendar_date(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        home_page = (root / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )
        stock_types = (root / "stock-monitor/src/types/stock.ts").read_text(
            encoding="utf-8"
        )
        overview_card = (
            root / "stock-monitor/src/components/stock/StockOverviewCard.tsx"
        ).read_text(encoding="utf-8")
        attribution_tab = (
            root / "stock-monitor/src/components/stock/AiAttributionTab.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("sourceTime", home_page)
        self.assertIn("fetchedAt", home_page)
        self.assertIn("marketStatusCode", stock_types)
        self.assertIn("数据源时间", overview_card)
        self.assertIn("后端抓取", overview_card)
        self.assertIn("页面刷新", overview_card)
        self.assertNotIn("useState<number>(2026)", attribution_tab)
        self.assertIn("交易日历暂不可用", attribution_tab)
        self.assertNotIn("前一交易日 15:30 至", attribution_tab)

    @patch.object(ai_analysis.database, "get_latest_crawled_news", return_value=[])
    @patch.object(ai_analysis.database, "get_cached_dynamics", return_value=None)
    def test_industry_dynamics_returns_empty_when_no_real_news_exists(
        self,
        _mock_get_cache,
        _mock_get_news,
    ):
        result = ai_analysis.fetch_real_industry_dynamics("000725", "光学光电子")

        self.assertEqual(result["policies"], [])
        self.assertEqual(result["upstreamDownstream"], [])

    def test_production_data_modules_do_not_fill_missing_finance_with_zero(self):
        production_modules = (
            "backend/main.py",
            "backend/real_data_fetcher.py",
        )
        root = __import__("pathlib").Path(__file__).resolve().parents[2]

        for relative_path in production_modules:
            content = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("fillna(0)", content)

    def test_production_analysis_does_not_use_untraceable_static_peer_maps(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        ai_source = (root / "backend/ai_analysis.py").read_text(encoding="utf-8")
        main_source = (root / "backend/main.py").read_text(encoding="utf-8")

        self.assertNotIn("CHAIN_MAP", ai_source)
        self.assertNotIn("hk_sector_map", main_source)


class AiIntegrityTests(unittest.TestCase):
    @patch.object(main.database, "get_today_analysis_history", return_value=[])
    @patch.object(
        main.database,
        "get_trading_session_bounds_for_symbol",
        return_value=(None, None),
    )
    def test_ai_history_exposes_unknown_calendar_without_fake_bounds(
        self,
        _mock_bounds,
        _mock_history,
    ):
        result = main.get_ai_history("000725")

        self.assertIsNone(result["bounds"])
        self.assertEqual(result["calendarStatus"], "unknown")

    @patch.object(ai_analysis.database, "is_in_watchlist", return_value=False)
    @patch.object(
        ai_analysis.fetcher,
        "get_stock_quote",
        return_value={"name": "京东方A", "change_pct": 1.2},
    )
    def test_ai_score_is_null_when_analysis_did_not_run(
        self,
        _mock_quote,
        _mock_is_in_watchlist,
    ):
        result = ai_analysis.get_ai_attribution("000725")

        self.assertIsNone(result["score"])
        self.assertEqual(result["credibility"], "无")

    def test_ai_analysis_excludes_trading_directives_and_raw_output_logging(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        source = (root / "backend/ai_analysis.py").read_text(encoding="utf-8")

        forbidden_phrases = (
            "操作建议",
            "加仓",
            "减仓",
            "买入",
            "卖出",
            "目标价",
            "未来走势深度分析",
            "RAW_LLM_OUTPUT",
        )
        for phrase in forbidden_phrases:
            self.assertNotIn(phrase, source)


class MarketIntegrityTests(unittest.TestCase):
    def test_industry_fund_flow_uses_explicit_market_status(self):
        self.assertEqual(
            main.format_industry_fund_flow("lunch_break", False, None, False),
            "暂无资金流数据（午间休市）",
        )
        self.assertEqual(
            main.format_industry_fund_flow("unknown", False, None, False),
            "暂无资金流数据（市场状态未知）",
        )
        self.assertEqual(
            main.format_industry_fund_flow("closed", True, 1.2, False),
            "今日收盘 +1.2 亿元",
        )

    def test_sina_money_flow_uses_reported_main_net_amount(self):
        payload = {
            "name": "京东方Ａ",
            "netamount": "17368109.0900",
        }

        self.assertEqual(main.parse_sina_money_flow(payload), 17368109.09)

    def test_sina_money_flow_preserves_missing_value(self):
        self.assertIsNone(main.parse_sina_money_flow({"name": "京东方Ａ"}))

    def test_sina_industry_nodes_match_real_industry_name(self):
        nodes = [
            "申万二级",
            [["光学光电子", "", "sw2_270300", ""]],
        ]

        self.assertEqual(
            main.find_sina_industry_node(nodes, "光学光电子"),
            "sw2_270300",
        )

    def test_sina_constituents_exclude_current_stock(self):
        rows = [
            {"code": "000725", "name": "京东方Ａ"},
            {"code": "600001", "name": "真实成分股"},
        ]

        self.assertEqual(
            main.parse_sina_peer_codes(rows, "000725"),
            ["600001"],
        )

    @staticmethod
    def _quote_response():
        fields = [""] * 40
        fields[1] = "真实测试股票"
        fields[2] = "600001"
        fields[3] = "10.00"
        fields[30] = "20260711150000"
        fields[32] = "6.50"
        response = MagicMock()
        response.text = f'v_sh600001="{"~".join(fields)}";'
        return response

    @staticmethod
    def _missing_quote_response():
        fields = [""] * 46
        fields[1] = "缺失值测试股票"
        fields[2] = "000725"
        fields[30] = "20260711150000"
        response = MagicMock()
        response.text = f'v_sz000725="{"~".join(fields)}";'
        return response

    @staticmethod
    def _hk_quote_response():
        fields = [""] * 60
        fields[1] = "腾讯控股"
        fields[2] = "00700"
        fields[3] = "500.00"
        fields[4] = "495.00"
        fields[5] = "496.00"
        fields[30] = "2026/07/11 16:08:11"
        fields[31] = "5.00"
        fields[32] = "1.01"
        fields[33] = "502.00"
        fields[34] = "493.00"
        fields[38] = "0"
        fields[59] = "1.17"
        response = MagicMock()
        response.text = f'v_hk00700="{"~".join(fields)}";'
        return response

    @patch.object(main, "get_em_data")
    @patch.object(main.requests, "get")
    def test_hk_overview_keeps_hk_prefix_for_tencent_quote(
        self,
        mock_get,
        mock_get_em_data,
    ):
        invalid_response = MagicMock()
        invalid_response.text = 'v_pv_none_match="1";'

        def quote_response(url, **_kwargs):
            if url == "http://qt.gtimg.cn/q=hk00700":
                return self._hk_quote_response()
            return invalid_response

        mock_get.side_effect = quote_response
        em_response = MagicMock()
        em_response.status_code = 200
        em_response.json.return_value = {"data": None}
        mock_get_em_data.return_value = em_response

        try:
            result = main.get_stock_overview("hk00700")
        except Exception as exc:
            self.fail(f"港股概览应返回真实行情，实际异常: {exc}")

        self.assertEqual(result["code"], "hk00700")
        self.assertEqual(result["name"], "腾讯控股")
        self.assertEqual(result["details"]["turnoverRate"], 1.17)
        self.assertEqual(result["fundFlow"], "暂无港股资金流数据")
        self.assertEqual(
            mock_get.call_args_list[0].args[0],
            "http://qt.gtimg.cn/q=hk00700",
        )

    @patch.object(main, "get_sina_stock_fund_flow", return_value=None)
    @patch.object(
        main,
        "get_market_status_for_symbol",
        return_value={
            "marketStatus": "午间休市",
            "marketStatusCode": "lunch_break",
            "market": "cn",
            "calendarSource": "https://www.sse.com.cn/",
            "calendarCheckedAt": "2027-03-01T12:00:00+08:00",
            "calendarError": None,
        },
    )
    @patch.object(main.requests, "get")
    def test_a_share_overview_separates_market_and_time_semantics(
        self,
        mock_get,
        _mock_market_status,
        _mock_get_fund_flow,
    ):
        fields = [""] * 46
        fields[1] = "京东方Ａ"
        fields[2] = "000725"
        fields[3] = "7.59"
        fields[30] = "20260710161412"
        fields[38] = "10.76"
        response = MagicMock()
        response.text = f'v_sz000725="{"~".join(fields)}";'
        mock_get.return_value = response

        result = main.get_stock_overview("000725")

        self.assertEqual(result["details"]["turnoverRate"], 10.76)
        self.assertEqual(result["marketStatus"], "午间休市")
        self.assertEqual(result["marketStatusCode"], "lunch_break")
        self.assertEqual(result["sourceTime"], "2026-07-10 16:14:12")
        self.assertTrue(result["fetchedAt"].endswith("+08:00"))
        self.assertNotIn("updateTime", result)

    @patch("akshare.stock_fund_flow_industry")
    @patch.object(main.database, "get_latest_crawled_news", return_value=[])
    @patch.object(main.database, "is_in_watchlist", return_value=False)
    @patch.object(main, "get_company_info")
    def test_hk_industry_does_not_use_a_share_sector_metrics(
        self,
        mock_get_company_info,
        _mock_is_in_watchlist,
        _mock_get_news,
        mock_a_share_industry,
    ):
        mock_get_company_info.return_value = {
            "companyInfo": {"industryTags": ["汽车"]}
        }

        result = main.get_industry_monitor("09863")

        mock_a_share_industry.assert_not_called()
        self.assertIsNone(result["heatScore"])
        self.assertIsNone(result["sectorChangePercent"])
        self.assertEqual(result["fundFlow"], "暂无港股行业资金流数据")

    @patch(
        "akshare.stock_individual_fund_flow_rank",
        return_value=pd.DataFrame(columns=["代码", "今日-主力净流入-净额"]),
    )
    @patch.object(main, "get_em_data")
    @patch.object(main.requests, "get")
    def test_overview_preserves_missing_numbers_as_null(
        self,
        mock_get,
        mock_get_em_data,
        _mock_fund_flow_rank,
    ):
        mock_get.return_value = self._missing_quote_response()
        em_response = MagicMock()
        em_response.status_code = 200
        em_response.json.return_value = {"data": {"diff": []}}
        mock_get_em_data.return_value = em_response

        result = main.get_stock_overview("000725")

        self.assertIsNone(result["latestPrice"])
        self.assertIsNone(result["changeAmount"])
        self.assertIsNone(result["changePercent"])
        self.assertIsNone(result["details"]["open"])
        self.assertIsNone(result["details"]["volume"])
        self.assertIsNone(result["details"]["turnoverAmount"])

    @patch.object(main, "get_sina_stock_fund_flow", return_value=None)
    @patch.object(main, "get_a_share_industry_peer_codes", return_value=["600001"])
    @patch.object(main, "get_company_info")
    @patch.object(main.requests, "get")
    def test_abnormal_peers_do_not_invent_unavailable_metrics(
        self,
        mock_get,
        mock_get_company_info,
        _mock_get_peer_codes,
        _mock_get_fund_flow,
    ):
        mock_get_company_info.return_value = {
            "companyInfo": {"industryTags": ["半导体"]}
        }
        mock_get.return_value = self._quote_response()

        result = main.get_abnormal_peers("000725")["data"]

        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsNone(item["twentyDayChange"])
        self.assertIsNone(item["volumeRatio"])
        self.assertIsNone(item["fundFlow"])
        self.assertIsNone(item["reason"])
        self.assertIsNone(item["riskNote"])
        self.assertEqual(item["updateTime"], "2026-07-11 15:00:00")

    @patch.object(main, "get_a_share_industry_peer_codes")
    @patch.object(main, "get_company_info")
    @patch.object(main.requests, "get")
    def test_related_a_share_candidates_come_from_real_industry_constituents(
        self,
        mock_get,
        mock_get_company_info,
        mock_get_peer_codes,
    ):
        mock_get_company_info.return_value = {
            "companyInfo": {"industryTags": ["半导体"]}
        }
        mock_get_peer_codes.return_value = ["600001", "000002"]
        empty_response = MagicMock()
        empty_response.text = ""
        mock_get.return_value = empty_response

        main.get_related_stocks("000725")

        mock_get_peer_codes.assert_called_once_with("000725", "半导体")

    @patch("builtins.print")
    @patch("requests.get", side_effect=RuntimeError("upstream unavailable"))
    def test_ai_quote_fetcher_does_not_invent_numbers_on_failure(
        self,
        _mock_get,
        _mock_print,
    ):
        result = RealDataFetcher().get_stock_quote("000725")

        self.assertEqual(result["name"], "000725")
        self.assertIsNone(result["price"])
        self.assertIsNone(result["change_pct"])
        self.assertIsNone(result["volume_ratio"])
        self.assertIsNone(result["turnover_rate"])


class ApiSecurityTests(unittest.TestCase):
    def test_cors_does_not_allow_every_origin(self):
        cors_middleware = next(
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls is CORSMiddleware
        )

        self.assertNotIn("*", cors_middleware.kwargs["allow_origins"])

    def test_cors_defaults_match_fixed_frontend_port(self):
        self.assertEqual(
            main.get_allowed_origins(),
            ["http://localhost:4000", "http://127.0.0.1:4000"],
        )

    @patch.dict(
        "os.environ",
        {"BACKEND_API_TOKEN": "test-token"},
        clear=False,
    )
    @patch.object(main.database, "get_watchlist", return_value=[{"stockCode": "000725"}])
    @patch.object(main.requests, "get")
    def test_auto_analysis_forwards_backend_token(
        self,
        mock_get,
        _mock_get_watchlist,
    ):
        asyncio.run(main.auto_analyze_watchlist())

        mock_get.assert_called_once_with(
            "http://127.0.0.1:8001/api/stock/ai_attribution/000725?trigger=auto",
            headers={"X-Backend-Token": "test-token"},
            timeout=60,
        )

    def test_direct_backend_start_binds_to_localhost(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        source = (root / "backend/main.py").read_text(encoding="utf-8")

        self.assertIn('uvicorn.run(app, host="127.0.0.1", port=8001)', source)
        self.assertNotIn('uvicorn.run(app, host="0.0.0.0", port=8001)', source)

    @patch.dict("os.environ", {"BACKEND_API_TOKEN": "test-token"})
    @patch.object(main.database, "get_watchlist", return_value=[])
    @patch.object(main.database, "replace_watchlist", return_value=True)
    def test_watchlist_write_requires_backend_token(
        self,
        _mock_replace_watchlist,
        _mock_get_watchlist,
    ):
        client = TestClient(main.app)

        read_response = client.get("/api/watchlist")
        response = client.post("/api/watchlist", json={"items": []})
        authorized_read_response = client.get(
            "/api/watchlist",
            headers={"X-Backend-Token": "test-token"},
        )
        authorized_response = client.post(
            "/api/watchlist",
            json={"items": []},
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(read_response.status_code, 401)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(authorized_read_response.status_code, 200)
        self.assertEqual(authorized_response.status_code, 200)


class SchedulerResponsivenessTests(unittest.TestCase):
    @patch.object(main.database, "get_watchlist", return_value=[])
    @patch.object(main.asyncio, "to_thread", new_callable=AsyncMock)
    def test_startup_industry_update_runs_in_background_thread(
        self,
        mock_to_thread,
        _mock_get_watchlist,
    ):
        asyncio.run(main.auto_update_watchlist_industry())

        mock_to_thread.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
