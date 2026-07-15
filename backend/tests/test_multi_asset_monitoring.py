import importlib
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd


def optional_import(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


asset_context = optional_import("asset_context")
news_collector = optional_import("news_collector")
news_api = optional_import("news_api")
ai_analysis = optional_import("ai_analysis")
main = optional_import("main")
real_data_fetcher = optional_import("real_data_fetcher")
notification_service = optional_import("notification_service")


class AssetContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(asset_context, "asset_context 模块尚未实现")

    def test_builds_distinct_contexts_for_a_share_hk_stock_and_domestic_etf(self):
        a_share = asset_context.build_asset_context(
            "002594",
            "比亚迪",
            ["汽车整车"],
        )
        hk_stock = asset_context.build_asset_context(
            "00700",
            "腾讯控股",
            ["软件服务"],
        )
        bse_new_code = asset_context.build_asset_context("920001", "纬达光电")
        sh_etf = asset_context.build_asset_context("510300", "沪深300ETF")
        sz_etf = asset_context.build_asset_context("159915", "创业板ETF")

        self.assertEqual(a_share["asset_type"], "a_stock")
        self.assertEqual(a_share["quote_prefix"], "sz")
        self.assertEqual(a_share["industry_name"], "汽车整车")
        self.assertIn("汽车整车", a_share["search_terms"])

        self.assertEqual(hk_stock["asset_type"], "hk_stock")
        self.assertEqual(hk_stock["quote_prefix"], "hk")
        self.assertEqual(hk_stock["industry_name"], "软件服务")

        self.assertEqual(bse_new_code["asset_type"], "a_stock")
        self.assertEqual(bse_new_code["quote_prefix"], "bj")

        self.assertEqual(sh_etf["asset_type"], "domestic_etf")
        self.assertEqual(sh_etf["quote_prefix"], "sh")
        self.assertEqual(sh_etf["industry_name"], "沪深300")
        self.assertNotIn("半导体", sh_etf["search_terms"])

        self.assertEqual(sz_etf["asset_type"], "domestic_etf")
        self.assertEqual(sz_etf["quote_prefix"], "sz")
        self.assertEqual(sz_etf["industry_name"], "创业板")

    def test_unknown_industry_never_falls_back_to_semiconductor(self):
        a_share = asset_context.build_asset_context("600000", "浦发银行")
        hk_stock = asset_context.build_asset_context("00939", "建设银行")

        self.assertEqual(a_share["industry_name"], "行业待确认")
        self.assertEqual(hk_stock["industry_name"], "港股行业待确认")
        self.assertNotIn("半导体", a_share["search_terms"])
        self.assertNotIn("半导体", hk_stock["search_terms"])

    def test_resolves_real_industry_for_company_assets_and_skips_lookup_for_etf(self):
        resolver = getattr(asset_context, "resolve_asset_context", None)
        self.assertIsNotNone(resolver, "资产行业解析尚未实现")
        asset_context._CONTEXT_CACHE.clear()
        with patch.object(
            asset_context,
            "_fetch_industry_tags",
            return_value=["汽车整车"],
            create=True,
        ) as mock_fetch:
            a_share = resolver("002594", "比亚迪", refresh=True)
            etf = resolver("510300", "沪深300ETF", refresh=True)

        self.assertEqual(a_share["industry_name"], "汽车整车")
        self.assertEqual(etf["industry_name"], "沪深300")
        mock_fetch.assert_called_once()


class MultiAssetCollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(news_collector, "news_collector 模块导入失败")

    @patch.object(news_collector.time, "sleep", return_value=None)
    @patch.object(news_collector.requests, "get")
    def test_sina_market_feed_reads_two_latest_pages(self, mock_get, _mock_sleep):
        def response_for_page(_url, **kwargs):
            page = kwargs.get("params", {}).get("page")
            response = MagicMock(status_code=200)
            response.json.return_value = {
                "result": {
                    "data": [{
                        "title": f"第{page}页市场资讯",
                        "url": f"https://finance.sina.com.cn/page-{page}.shtml",
                        "ctime": "1783990904",
                        "media_name": "新浪财经",
                    }]
                }
            }
            return response

        mock_get.side_effect = response_for_page

        result = news_collector.fetch_sina_roll_news()

        self.assertEqual(len(result), 2)
        self.assertEqual([call.kwargs["params"]["page"] for call in mock_get.call_args_list], [1, 2])

    @patch.object(news_collector.requests, "get")
    def test_market_disclosures_cover_a_share_and_hk_without_watchlist_scope(self, mock_get):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "list": [{
                    "art_code": "AN202607140001",
                    "title": "上市公司当日公告",
                    "display_time": "2026-07-14 09:14:03:679",
                    "codes": [{"stock_code": "000725"}],
                }]
            }
        }
        mock_get.return_value = response

        result = news_collector.fetch_market_disclosures()

        self.assertEqual(len(result), 2)
        self.assertEqual({item["market"] for item in result}, {"cn", "hk"})
        self.assertTrue(all(item["source"] == "东方财富公告汇总" for item in result))
        self.assertTrue(all("stock_list" not in call.kwargs["params"] for call in mock_get.call_args_list))

    @patch.object(news_collector.ak, "stock_news_em")
    def test_fetches_exact_code_news_for_hk_stock_and_domestic_etf(self, mock_news):
        fetch_asset_news = getattr(news_collector, "fetch_watchlist_asset_news", None)
        self.assertIsNotNone(fetch_asset_news, "定向资产资讯抓取尚未实现")
        mock_news.return_value = pd.DataFrame([{
            "新闻标题": "资产定向资讯",
            "新闻内容": "与目标证券直接关联",
            "发布时间": "2026-07-14 09:15:00",
            "文章来源": "证券时报网",
            "新闻链接": "https://finance.eastmoney.com/a/example.html",
        }])

        with patch.object(
            news_collector.asset_context,
            "resolve_asset_context",
            side_effect=lambda code, name: asset_context.build_asset_context(code, name),
        ) as mock_resolve:
            hk_items = fetch_asset_news({
                "stockCode": "00700",
                "stockName": "腾讯控股",
            })
            etf_items = fetch_asset_news({
                "stockCode": "510300",
                "stockName": "沪深300ETF",
            })

        self.assertEqual(mock_news.call_args_list[0].kwargs["symbol"], "00700")
        self.assertEqual(mock_news.call_args_list[1].kwargs["symbol"], "510300")
        self.assertEqual(hk_items[0]["symbol"], "00700")
        self.assertEqual(etf_items[0]["symbol"], "510300")
        self.assertEqual(hk_items[0]["category"], "industry")
        self.assertTrue(hk_items[0]["url"].endswith("#asset=00700"))
        self.assertTrue(etf_items[0]["url"].endswith("#asset=510300"))
        self.assertEqual(mock_resolve.call_count, 2)

    @patch.object(news_collector.database, "get_watchlist")
    def test_disclosure_collection_routes_a_share_hk_stock_and_etf_separately(
        self,
        mock_watchlist,
    ):
        mock_watchlist.return_value = [
            {"stockCode": "002594", "stockName": "比亚迪"},
            {"stockCode": "00700", "stockName": "腾讯控股"},
            {"stockCode": "510300", "stockName": "沪深300ETF"},
        ]
        with patch.object(
            news_collector,
            "fetch_cninfo_announcements",
            return_value=[{"id": "a"}],
        ) as mock_a, patch.object(
            news_collector,
            "fetch_hk_disclosures",
            return_value=[{"id": "hk"}],
            create=True,
        ) as mock_hk, patch.object(
            news_collector,
            "fetch_etf_disclosures",
            return_value=[{"id": "etf"}],
            create=True,
        ) as mock_etf:
            result = news_collector.collect_watchlist_official_news()

        self.assertEqual({item["id"] for item in result}, {"a", "hk", "etf"})
        mock_a.assert_called_once_with("002594", "002594")
        mock_hk.assert_called_once_with("00700", "腾讯控股")
        mock_etf.assert_called_once_with("510300", "沪深300ETF")

    @patch.object(news_collector.time, "sleep", return_value=None)
    @patch.object(news_collector.requests, "get")
    def test_sina_hk_media_story_is_not_relabelled_as_hkex_announcement(
        self,
        mock_get,
        _mock_sleep,
    ):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "result": {
                "data": [{
                    "title": "某公司获股东增持100万股",
                    "url": "https://finance.sina.com.cn/stock/hkstock/example.shtml",
                    "ctime": "1783990904",
                    "media_name": "新浪港股",
                }]
            }
        }
        mock_get.return_value = response

        result = news_collector.fetch_sina_hk_news()

        self.assertEqual(result[0]["source"], "港股快讯")
        self.assertNotEqual(result[0]["source"], "港交所公告")


class MultiAssetClassificationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(news_api, "news_api 模块导入失败")

    @patch.object(news_api.database, "is_in_watchlist", return_value=True)
    def test_exact_watchlist_news_is_relevant_outside_semiconductor(self, _mock_monitored):
        item = {
            "symbol": "002594",
            "title": "比亚迪发布新一代混合动力平台",
            "content": "整车技术更新",
            "source": "证券时报网",
            "url": "https://finance.eastmoney.com/a/byd.html#asset=002594",
        }

        self.assertTrue(news_api.is_industry_relevant(item))

    def test_dynamic_industry_terms_do_not_keep_unrelated_semiconductor_news(self):
        semiconductor_item = {
            "symbol": "",
            "title": "晶圆厂扩大先进制程产能",
            "content": "半导体设备采购增加",
        }
        automotive_item = {
            "symbol": "",
            "title": "新能源汽车产业政策发布",
            "content": "支持汽车整车与动力电池产业链",
        }

        self.assertFalse(news_api.is_industry_relevant(
            semiconductor_item,
            watchlist_symbols={"002594"},
            relevance_keywords=["比亚迪", "汽车整车", "新能源汽车"],
        ))
        self.assertTrue(news_api.is_industry_relevant(
            automotive_item,
            watchlist_symbols={"002594"},
            relevance_keywords=["比亚迪", "汽车整车", "新能源汽车"],
        ))

    def test_disclosure_aggregators_are_company_announcements_but_not_s_grade(self):
        result = news_api.classify_news_item({
            "symbol": "510300",
            "title": "沪深300ETF基金产品资料概要更新",
            "content": "基金公告",
            "source": "天天基金公告",
            "url": "https://data.eastmoney.com/notices/detail/510300/example.html",
            "category": "company",
        })

        self.assertEqual(result["category_key"], "company-announcements")
        self.assertEqual(result["content_type"], "security_announcement")
        self.assertEqual(result["credibility_level"], "B")
        self.assertEqual(result["verification_status"], "单一来源")

    def test_etf_disclosure_midnight_is_displayed_as_date_only(self):
        result = news_api.get_source_time_metadata({
            "source": "天天基金公告",
            "ctime": 1_783_958_400,
        })

        self.assertEqual(result["publish_time"], "2026-07-14")
        self.assertEqual(result["publish_time_precision"], "date")

    def test_same_day_gate_uses_source_publish_date_not_discovery_date(self):
        checker = getattr(news_api, "is_source_published_today", None)
        self.assertIsNotNone(checker, "当天资讯判断尚未实现")
        now = datetime.now(news_api.market_calendar.SHANGHAI_TZ)
        yesterday = now - timedelta(days=1)

        self.assertTrue(checker({"ctime": now.timestamp()}, now=now))
        self.assertFalse(checker({
            "ctime": yesterday.timestamp(),
            "created_at": now.timestamp(),
        }, now=now))
        self.assertTrue(checker({
            "source": "深交所公告",
            "ctime": now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),
            "created_at": yesterday.replace(hour=21, minute=0, second=0).timestamp(),
        }, now=now))
        tomorrow = now + timedelta(days=1)
        self.assertFalse(checker({
            "source": "深交所公告",
            "ctime": tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),
            "created_at": now.replace(hour=21, minute=0, second=0).timestamp(),
        }, now=now))

    @patch.object(news_api.asset_context, "get_watchlist_search_terms", return_value=["比亚迪", "汽车"])
    @patch.object(news_api.database, "get_watchlist")
    @patch.object(news_api.database, "get_latest_crawled_news")
    def test_industry_feed_merges_exact_asset_rows_and_excludes_yesterday(
        self,
        mock_get_news,
        mock_watchlist,
        _mock_terms,
    ):
        now = datetime.now(news_api.market_calendar.SHANGHAI_TZ)
        today_item = {
            "symbol": "002594",
            "title": "比亚迪发布新能源汽车产业新动态",
            "content": "汽车产业链更新",
            "source": "证券时报网",
            "url": "https://example.com/today#asset=002594",
            "ctime": now.timestamp(),
            "created_at": now.timestamp(),
            "category": "industry",
        }
        yesterday_item = {
            **today_item,
            "title": "比亚迪昨日产业动态",
            "url": "https://example.com/yesterday#asset=002594",
            "ctime": (now - timedelta(days=1)).timestamp(),
        }
        market_wide_item = {
            **today_item,
            "symbol": "",
            "title": "化工有色产业链涨价驱动中报业绩预增",
            "content": "中国证券市场多个行业出现盈利改善",
            "source": "中国证券报-中证网",
            "url": "https://example.com/market-wide",
        }
        unrelated_global = {
            **today_item,
            "symbol": "",
            "title": "调查显示美国人购买食品杂货",
            "content": "普通家庭食品消费调查",
            "source": "环球市场播报",
            "url": "https://example.com/unrelated-global",
        }
        mock_watchlist.return_value = [{
            "stockCode": "002594",
            "stockName": "比亚迪",
        }]
        mock_get_news.side_effect = lambda symbol, limit=100: (
            [today_item, yesterday_item]
            if symbol == "002594"
            else [market_wide_item, unrelated_global]
        )

        result = news_api.get_real_news_from_db("all")

        self.assertEqual(
            [item["title"] for item in result],
            [today_item["title"], market_wide_item["title"]],
        )
        self.assertIn("002594", [call.args[0] for call in mock_get_news.call_args_list])

    def test_cached_home_dynamics_excludes_yesterday(self):
        now = datetime.now(news_api.market_calendar.SHANGHAI_TZ)
        shared = {
            "source": "新浪财经",
            "url": "https://example.com/dynamic",
            "category": "industry",
        }
        cached = {
            "policies": [],
            "upstreamDownstream": [
                {
                    **shared,
                    "title": "比亚迪今日产业动态",
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                {
                    **shared,
                    "title": "比亚迪昨日产业动态",
                    "url": "https://example.com/yesterday-dynamic",
                    "time": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                },
            ],
        }

        result = ai_analysis._normalize_cached_dynamics(cached)

        self.assertEqual(
            [item["title"] for item in result["upstreamDownstream"]],
            ["比亚迪今日产业动态"],
        )


class MultiAssetRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(main, "main 模块导入失败")
        self.assertIsNotNone(real_data_fetcher, "real_data_fetcher 模块导入失败")

    def test_quote_prefixes_cover_sh_etf_sz_etf_and_hk_stock(self):
        self.assertEqual(main.get_prefix("510300"), "sh")
        self.assertEqual(main.get_em_prefix("510300"), "1.")
        self.assertEqual(main.get_prefix("159915"), "sz")
        self.assertEqual(main.get_prefix("00700"), "hk")

    @patch("requests.get")
    def test_ai_quote_fetcher_uses_market_aware_prefixes(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.text = "~".join(["v", "名称", "代码", "10"] + ["0"] * 50)
        mock_get.return_value = response
        fetcher = real_data_fetcher.RealDataFetcher()

        fetcher.get_stock_quote("00700")
        fetcher.get_stock_quote("510300")

        self.assertIn("q=hk00700", mock_get.call_args_list[0].args[0])
        self.assertIn("q=sh510300", mock_get.call_args_list[1].args[0])

    @patch("requests.get")
    def test_ai_quote_fetcher_keeps_source_time_and_source_date(self, mock_get):
        fields = [""] * 60
        fields[1] = "京东方A"
        fields[2] = "000725"
        fields[3] = "6.80"
        fields[30] = "20260714091939"
        fields[32] = "-0.44"
        response = MagicMock()
        response.status_code = 200
        response.text = f'v_sz000725="{"~".join(fields)}";'
        mock_get.return_value = response

        result = real_data_fetcher.RealDataFetcher().get_stock_quote("000725")

        self.assertEqual(result["source_time"], "2026-07-14 09:19:39")
        self.assertEqual(result["source_date"], "2026-07-14")

    @patch("requests.get")
    def test_ai_industry_news_excludes_previous_day_items(self, mock_get):
        now = datetime.now(ai_analysis.news_api.market_calendar.SHANGHAI_TZ)
        response = MagicMock()
        response.json.return_value = {
            "result": {
                "data": [
                    {
                        "title": "汽车行业今日动态",
                        "url": "https://example.com/today",
                        "ctime": str(int(now.timestamp())),
                    },
                    {
                        "title": "汽车行业昨日动态",
                        "url": "https://example.com/yesterday",
                        "ctime": str(int((now - timedelta(days=1)).timestamp())),
                    },
                ]
            }
        }
        mock_get.return_value = response

        result = real_data_fetcher.RealDataFetcher().get_industry_news_dehydrated(
            "002594",
            "汽车",
            ["汽车"],
        )

        self.assertIn("今日动态", result)
        self.assertNotIn("昨日动态", result)

    @patch("requests.get")
    def test_ai_macro_snapshot_does_not_claim_realtime_without_market_time(self, mock_get):
        fields = [
            "英伟达", "203.5300", "-3.52", "2026-07-14 09:42:15",
            "-7.4300", "208.5400", "210.5700", "203.0000",
        ] + [""] * 17 + ["Jul 13 07:59PM EDT", "Jul 13 04:00PM EDT"]
        response = MagicMock()
        response.text = f'var hq_str_gb_nvda="{",".join(fields)}";'
        mock_get.return_value = response

        result = real_data_fetcher.RealDataFetcher().get_macro_environment({
            "industry_name": "半导体",
            "search_terms": ["半导体"],
        })

        self.assertNotIn("海外实时宏观环境", result)
        self.assertIn("数据源更新：2026-07-14 09:42:15", result)
        self.assertIn("市场时点：Jul 13 04:00PM EDT", result)

    @patch.object(real_data_fetcher.ak, "stock_news_em")
    def test_ai_stock_news_uses_today_only(self, mock_news):
        now = datetime.now(ai_analysis.news_api.market_calendar.SHANGHAI_TZ)
        mock_news.return_value = pd.DataFrame([
            {
                "新闻标题": "昨日旧闻",
                "发布时间": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                "文章来源": "证券时报",
                "新闻链接": "https://example.com/old",
            },
            {
                "新闻标题": "今日新消息",
                "发布时间": now.strftime("%Y-%m-%d %H:%M:%S"),
                "文章来源": "证券时报",
                "新闻链接": "https://example.com/today",
            },
        ])

        result = real_data_fetcher.RealDataFetcher().get_stock_news("000725")

        self.assertIn("今日新消息", result)
        self.assertNotIn("昨日旧闻", result)

    @patch.object(main, "get_stock_overview")
    @patch.object(main.database, "get_watchlist")
    def test_market_risk_scheduler_includes_five_digit_hk_stock(
        self,
        mock_watchlist,
        mock_overview,
    ):
        mock_watchlist.return_value = [{
            "stockCode": "00700",
            "stockName": "腾讯控股",
        }]

        main.run_market_risk_collection_sync()

        mock_overview.assert_called_once_with("00700")

    @patch.object(news_collector, "fetch_etf_disclosures", return_value=[])
    @patch.object(main.database, "get_watchlist")
    def test_domestic_etf_company_panel_uses_theme_not_company_financials(
        self,
        mock_watchlist,
        _mock_disclosures,
    ):
        mock_watchlist.return_value = [{
            "stockCode": "510300",
            "stockName": "沪深300ETF",
        }]
        main._company_info_cache.clear()

        result = main.get_company_info("510300")

        self.assertEqual(result["companyInfo"]["industryTags"], ["沪深300"])
        self.assertEqual(result["companyInfo"]["mainBusiness"], "跟踪沪深300的国内ETF")
        self.assertEqual(result["financialData"]["reportPeriod"], "不适用")

    def test_ai_analysis_source_has_no_semiconductor_role_or_self_http_lookup(self):
        root = Path(__file__).resolve().parents[1]
        ai_source = (root / "ai_analysis.py").read_text(encoding="utf-8")
        fetcher_source = (root / "real_data_fetcher.py").read_text(encoding="utf-8")
        radar_source = (
            root.parent / "stock-monitor/src/components/industry/RadarNewsCard.tsx"
        ).read_text(encoding="utf-8")

        self.assertNotIn("半导体行业分析师", ai_source)
        self.assertNotIn("半导体与电子行业的顶尖分析助理", ai_source)
        self.assertNotIn("http://127.0.0.1:8001/api/stock/industry/", fetcher_source)
        self.assertIn("security_announcement", radar_source)
        self.assertIn("公告汇总", radar_source)


class MultiAssetAlertTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(notification_service, "notification_service 模块导入失败")

    @patch.object(notification_service, "process_new_alert")
    @patch.object(notification_service.alert_repository, "save_alert_event")
    @patch.object(notification_service.database, "get_watchlist")
    def test_exact_b_grade_p2_news_creates_alert_but_c_grade_clue_does_not(
        self,
        mock_watchlist,
        mock_save,
        mock_process,
    ):
        processor = getattr(notification_service, "process_news_items", None)
        self.assertIsNotNone(processor, "多资产资讯提醒处理尚未实现")
        mock_watchlist.return_value = [{
            "stockCode": "002594",
            "stockName": "比亚迪",
        }]
        saved_alert = {"id": "alert-1", "priority": "P2", "symbol": "002594"}
        mock_save.return_value = (saved_alert, True)
        now = datetime.now(news_api.market_calendar.SHANGHAI_TZ)
        news_items = [
            {
                "id": "trusted-major-contract",
                "symbol": "002594",
                "title": "比亚迪签订重大长期供货合同",
                "content": "公司披露新的长期订单",
                "source": "新浪财经",
                "url": "https://finance.sina.com.cn/byd#asset=002594",
                "ctime": int((now - timedelta(minutes=2)).timestamp()),
                "category": "industry",
            },
            {
                "id": "clue-major-contract",
                "symbol": "002594",
                "title": "网传比亚迪签订重大合同",
                "content": "未经独立核实",
                "source": "产业观察",
                "url": "https://example.com/clue#asset=002594",
                "ctime": int((now - timedelta(minutes=3)).timestamp()),
                "category": "industry",
            },
        ]

        created = processor(news_items)

        self.assertEqual(created, 1)
        event = mock_save.call_args.args[0]
        self.assertEqual(event["symbol"], "002594")
        self.assertEqual(event["priority"], "P2")
        self.assertEqual(event["evidence_level"], "B")
        self.assertEqual(event["direction"], "positive")
        mock_process.assert_called_once_with(saved_alert)

    @patch.object(notification_service, "process_new_alert")
    @patch.object(notification_service.alert_repository, "save_alert_event")
    @patch.object(notification_service.database, "get_watchlist")
    def test_yesterday_news_never_creates_today_alert(
        self,
        mock_watchlist,
        mock_save,
        mock_process,
    ):
        now = datetime.now(news_api.market_calendar.SHANGHAI_TZ)
        mock_watchlist.return_value = [{
            "stockCode": "002594",
            "stockName": "比亚迪",
        }]
        mock_save.return_value = ({"id": "unexpected-alert"}, True)
        old_item = {
            "id": "yesterday-major-contract",
            "symbol": "002594",
            "title": "比亚迪签订重大长期供货合同",
            "content": "公司披露新的长期订单",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/old#asset=002594",
            "ctime": (now - timedelta(days=1)).timestamp(),
            "created_at": now.timestamp(),
            "category": "industry",
        }

        created = notification_service.process_news_items([old_item])

        self.assertEqual(created, 0)
        mock_save.assert_not_called()
        mock_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
