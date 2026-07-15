import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import ai_analysis
import alert_repository
import database
import main
import monitoring_health
import news_api
import news_collector
import risk_engine
from real_data_fetcher import RealDataFetcher


def _today_timestamp(offset_seconds: int = 0) -> int:
    return int(datetime.now(news_api.market_calendar.SHANGHAI_TZ).timestamp()) + offset_seconds


class NewsIntegrityTests(unittest.TestCase):
    @patch.object(news_collector.requests, "Session")
    def test_cninfo_request_does_not_inherit_broken_local_proxy(self, mock_session):
        response = MagicMock()
        mock_session.return_value.post.return_value = response

        result = news_collector.post_cninfo(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            data={"pageNum": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )

        self.assertIs(result, response)
        self.assertIs(mock_session.return_value.trust_env, False)

    @patch.object(news_collector.time, "sleep")
    @patch.object(news_collector, "exists_in_database", return_value=False)
    @patch.object(news_collector, "parse_cninfo_pdf", return_value="公告摘要")
    @patch.object(news_collector, "post_cninfo")
    def test_cninfo_target_query_rejects_other_security_codes(
        self,
        mock_post,
        _mock_parse_pdf,
        _mock_exists,
        _mock_sleep,
    ):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "announcements": [
                {
                    "announcementTitle": "深科技股票交易异常波动公告",
                    "announcementTime": 1_700_000_000_000,
                    "secCode": "000021",
                    "adjunctUrl": "finalpage/target.pdf",
                },
                {
                    "announcementTitle": "其他公司公告",
                    "announcementTime": 1_700_000_000_000,
                    "secCode": "000999",
                    "adjunctUrl": "finalpage/other.pdf",
                },
            ]
        }
        mock_post.return_value = response

        result = news_collector.fetch_cninfo_announcements("000021", "000021")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "000021")
        self.assertIn("target.pdf", result[0]["url"])
        self.assertEqual(result[0]["category"], "company")

    def test_stock_news_excludes_global_items_while_global_query_keeps_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/news.db"
            with patch.object(news_api.database, "DB_PATH", db_path):
                news_api.database.init_db()
                news_api.database.save_crawled_news([
                    {
                        "id": "stock-news",
                        "symbol": "000725",
                        "title": "京东方A公告",
                        "url": "https://example.com/000725",
                        "ctime": 3,
                        "source": "巨潮公告",
                        "content": "公司公告",
                        "category": "company",
                    },
                    {
                        "id": "global-news",
                        "symbol": "",
                        "title": "全球市场新闻",
                        "url": "https://example.com/global",
                        "ctime": 2,
                        "source": "新浪财经",
                        "content": "全球资讯",
                        "category": "global",
                    },
                    {
                        "id": "other-stock-news",
                        "symbol": "000021",
                        "title": "深科技公告",
                        "url": "https://example.com/000021",
                        "ctime": 1,
                        "source": "巨潮公告",
                        "content": "其他公司公告",
                        "category": "company",
                    },
                ])

                stock_items = news_api.database.get_latest_crawled_news("000725")
                all_items = news_api.database.get_latest_crawled_news("")

        self.assertEqual([item["title"] for item in stock_items], ["京东方A公告"])
        self.assertEqual(len(all_items), 3)

    @patch.object(news_api, "get_real_news_from_db", return_value=[])
    def test_news_api_returns_empty_when_no_real_news_exists(self, _mock_get_real_news):
        self.assertEqual(news_api.get_integrated_news("all"), [])

    def test_news_source_classification_uses_traceability_and_real_verification_status(self):
        cases = (
            ("巨潮公告", "https://static.cninfo.com.cn/notice.pdf", 1, "S", "来源已核验"),
            ("新浪财经", "https://example.com/a", 2, "A", "多源印证"),
            ("某证券研报", "https://example.com/report", 1, "B", "单一来源"),
            ("产业观察", "https://example.com/clue", 1, "C", "线索级，待核实"),
            ("未知来源", "", 1, None, "未评级/拒绝"),
        )

        for source, link, source_count, level, verification_status in cases:
            with self.subTest(source=source):
                result = news_api.classify_news_source(
                    source,
                    original_link=link,
                    independent_source_count=source_count,
                )

                self.assertEqual(result["credibility_level"], level)
                self.assertEqual(result["credibility_method"], "source_rule")
                self.assertEqual(result["verification_status"], verification_status)

    def test_hkex_s_grade_requires_a_real_hkex_original_link(self):
        sina_repost = news_api.classify_news_source(
            "港交所公告",
            original_link="https://finance.sina.com.cn/stock/hkstock/example.shtml",
        )
        hkex_original = news_api.classify_news_source(
            "港交所公告",
            original_link="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/example.pdf",
        )

        self.assertNotEqual(sina_repost["credibility_level"], "S")
        self.assertNotEqual(sina_repost["verification_status"], "来源已核验")
        self.assertEqual(hkex_original["credibility_level"], "S")

    def test_a_share_s_grade_requires_an_official_disclosure_domain(self):
        repost = news_api.classify_news_source(
            "深交所公告",
            original_link="https://finance.example.com/repost.html",
        )
        original = news_api.classify_news_source(
            "深交所公告",
            original_link="https://static.cninfo.com.cn/finalpage/notice.pdf",
        )

        self.assertNotEqual(repost["credibility_level"], "S")
        self.assertEqual(original["credibility_level"], "S")

    @patch.object(
        news_api.asset_context,
        "get_watchlist_search_terms",
        return_value=["半导体", "显示面板", "AI"],
    )
    @patch.object(news_api.database, "get_latest_crawled_news")
    def test_industry_news_rejects_items_below_c_level(
        self,
        mock_get_news,
        _mock_terms,
    ):
        mock_get_news.return_value = [
            {
                "id": "traceable",
                "title": "可追溯半导体产业动态",
                "content": "半导体产业链动态",
                "source": "产业观察",
                "url": "https://example.com/traceable",
                "ctime": _today_timestamp(-20),
                "symbol": "",
                "category": "industry",
            },
            {
                "id": "unrated",
                "title": "无法追溯的传闻",
                "content": "来源不明",
                "source": "未知来源",
                "url": "",
                "ctime": _today_timestamp(-10),
                "symbol": "",
                "category": "industry",
            },
        ]

        result = news_api.get_real_news_from_db("all")

        self.assertEqual([item["id"] for item in result], ["traceable"])
        self.assertEqual(result[0]["credibility_level"], "C")
        self.assertEqual(result[0]["verification_status"], "线索级，待核实")

    @patch.object(
        news_api.asset_context,
        "get_watchlist_search_terms",
        return_value=["半导体", "显示面板", "AI"],
    )
    @patch.object(news_api.database, "get_latest_crawled_news")
    def test_industry_news_uses_the_five_confirmed_categories(
        self,
        mock_get_news,
        _mock_terms,
    ):
        mock_get_news.return_value = [
            {
                "id": "company",
                "title": "半导体公司重大合同公告",
                "content": "半导体公告原文",
                "source": "巨潮公告",
                "url": "https://static.cninfo.com.cn/company.pdf",
                "ctime": _today_timestamp(-40),
                "symbol": "000725",
                "category": "company",
            },
            {
                "id": "policy",
                "title": "半导体产业支持政策发布",
                "content": "半导体政策原文",
                "source": "产业观察",
                "url": "https://example.com/policy",
                "ctime": _today_timestamp(-30),
                "symbol": "",
                "category": "policy",
            },
            {
                "id": "industry",
                "title": "显示面板产业供需变化",
                "content": "显示产业动态",
                "source": "新浪财经",
                "url": "https://example.com/industry",
                "ctime": _today_timestamp(-20),
                "symbol": "",
                "category": "industry",
            },
            {
                "id": "overseas",
                "title": "美国扩大芯片出口管制",
                "content": "海外半导体限制措施",
                "source": "新浪财经",
                "url": "https://example.com/overseas",
                "ctime": _today_timestamp(-10),
                "symbol": "",
                "category": "global",
            },
        ]

        expected = {
            "all": {"company", "policy", "industry", "overseas"},
            "company-announcements": {"company"},
            "industry-policy": {"policy"},
            "industry-dynamics": {"industry"},
            "overseas-controls": {"overseas"},
        }

        for category, ids in expected.items():
            with self.subTest(category=category):
                result = news_api.get_real_news_from_db(category)
                self.assertEqual({item["id"] for item in result}, ids)

    def test_category_rules_ignore_stale_collector_policy_hints(self):
        meeting = news_api.classify_news_item({
            "title": "半导体行业供需交流会议召开",
            "content": "企业交流行业供需变化",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/example",
            "category": "policy",
        })
        policy = news_api.classify_news_item({
            "title": "工信部发布集成电路产业发展新规",
            "content": "支持集成电路产业发展",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/policy",
            "category": "industry",
        })
        company_story = news_api.classify_news_item({
            "title": "金徽酒回应监管问询",
            "content": "公司回复交易所问询",
            "source": "市场资讯",
            "url": "https://finance.sina.com.cn/company",
            "category": "industry",
        })
        overseas = news_api.classify_news_item({
            "title": "印度通胀率超过央行目标水平",
            "content": "海外宏观动态",
            "source": "环球市场播报",
            "url": "https://finance.sina.com.cn/global",
            "category": "industry",
        })
        foreign_company = news_api.classify_news_item({
            "title": "Meta加码AI数据中心建设",
            "content": "公司追加基础设施投入",
            "source": "滚动播报",
            "url": "https://finance.sina.com.cn/meta",
            "category": "industry",
        })

        self.assertEqual(meeting["category_key"], "industry-dynamics")
        self.assertEqual(policy["category_key"], "industry-policy")
        self.assertEqual(company_story["category_key"], "industry-dynamics")
        self.assertEqual(overseas["category_key"], "overseas-controls")
        self.assertEqual(foreign_company["category_key"], "overseas-controls")

    def test_domestic_industry_policy_is_not_changed_to_overseas_by_body_mentions(self):
        result = news_api.classify_news_item({
            "title": "工信部发布集成电路产业支持政策",
            "content": "文件同时梳理美国、英伟达等海外产业情况。",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/policy",
            "category": "industry",
        })

        self.assertEqual(result["region"], "国内")
        self.assertEqual(result["category_key"], "industry-policy")

    @patch.object(news_api.database, "get_latest_crawled_news")
    def test_date_only_announcement_uses_source_date_even_if_discovered_yesterday(
        self,
        mock_get_news,
    ):
        today = datetime.now(news_api.market_calendar.SHANGHAI_TZ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        mock_get_news.return_value = [{
            "id": "official-date-only",
            "title": "半导体公司2026年半年度业绩预告",
            "content": "半导体业务官方公告",
            "source": "深交所公告",
            "url": "https://static.cninfo.com.cn/finalpage/2026-07-14/example.PDF",
            "ctime": int(today.timestamp()),
            "created_at": int((today - timedelta(days=1)).timestamp()),
            "symbol": "000519",
            "category": "company",
        }]

        result = news_api.get_real_news_from_db("all")

        self.assertEqual([item["id"] for item in result], ["official-date-only"])

    @patch.object(
        news_api.asset_context,
        "get_watchlist_search_terms",
        return_value=["半导体", "显示面板", "AI", "服务器"],
    )
    @patch.object(
        news_api.database,
        "get_watchlist",
        return_value=[{"stockCode": "000725", "stockName": "京东方A"}],
    )
    @patch.object(news_api.database, "get_latest_crawled_news")
    def test_industry_feed_includes_full_market_news_without_fake_chain_tags(
        self,
        mock_get_news,
        _mock_watchlist,
        _mock_terms,
    ):
        today = datetime.now(news_api.market_calendar.SHANGHAI_TZ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        mock_get_news.return_value = [
            {
                "id": "oil-news",
                "title": "阿联酋退出欧佩克后原油产量大幅飙升",
                "content": "国际原油市场动态",
                "source": "环球市场播报",
                "url": "https://finance.sina.com.cn/oil",
                "ctime": _today_timestamp(-180),
                "created_at": _today_timestamp(-120),
                "symbol": "",
                "category": "industry",
            },
            {
                "id": "chip-news",
                "title": "全球半导体设备需求回升",
                "content": "晶圆厂扩大设备采购",
                "source": "新浪财经",
                "url": "https://finance.sina.com.cn/chip",
                "ctime": _today_timestamp(-170),
                "created_at": _today_timestamp(-110),
                "symbol": "",
                "category": "industry",
            },
            {
                "id": "company-no-chain",
                "title": "AI服务器项目第二次临时股东会会议资料",
                "content": "官方会议资料",
                "source": "上交所公告",
                "url": "https://static.cninfo.com.cn/company.PDF",
                "ctime": _today_timestamp(-160),
                "created_at": _today_timestamp(-100),
                "symbol": "688001",
                "category": "company",
            },
            {
                "id": "unrelated-company",
                "title": "农业公司2026年半年度业绩预告",
                "content": "农业种植业务",
                "source": "上交所公告",
                "url": "https://static.cninfo.com.cn/agriculture.PDF",
                "ctime": int((today - timedelta(seconds=100)).timestamp()),
                "created_at": _today_timestamp(-90),
                "symbol": "000021",
                "category": "company",
            },
        ]

        result = news_api.get_real_news_from_db("all")

        self.assertEqual(
            {item["id"] for item in result},
            {"oil-news", "chip-news", "company-no-chain"},
        )
        oil = next(item for item in result if item["id"] == "oil-news")
        self.assertEqual(oil["related_chains"], [])
        company = next(item for item in result if item["id"] == "company-no-chain")
        self.assertEqual(company["related_chains"], [])
        chip = next(item for item in result if item["id"] == "chip-news")
        self.assertEqual(chip["related_chains"], ["半导体设备", "晶圆代工"])

    @patch.dict("os.environ", {"LLM_API_KEY": ""})
    @patch.object(ai_analysis.database, "get_latest_crawled_news")
    def test_home_dynamics_uses_real_categories_and_excludes_company_announcements(
        self,
        mock_get_news,
    ):
        company = {
            "id": "company",
            "symbol": "000725",
            "title": "关于调整股票期权行权价格的公告",
            "content": "公司公告",
            "source": "深交所公告",
            "url": "https://static.cninfo.com.cn/company.PDF",
            "ctime": _today_timestamp(-30),
            "created_at": 1_783_553_865,
            "category": "policy",
        }
        policy = {
            "id": "policy",
            "symbol": "",
            "title": "工信部发布集成电路产业发展新规",
            "content": "产业政策",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/policy",
            "ctime": _today_timestamp(-20),
            "created_at": 1_783_947_700,
            "category": "policy",
        }
        industry = {
            "id": "industry",
            "symbol": "",
            "title": "面板产业供需出现新变化",
            "content": "产业动态",
            "source": "新浪财经",
            "url": "https://finance.sina.com.cn/industry",
            "ctime": _today_timestamp(-10),
            "created_at": 1_783_947_710,
            "category": "industry",
        }

        mock_get_news.side_effect = lambda symbol, limit=100: (
            [company] if symbol else [company, policy, industry]
        )

        result = ai_analysis.fetch_real_industry_dynamics(
            "000725",
            "光学光电子",
            force_refresh=True,
            search_terms=["集成电路", "面板"],
        )

        self.assertEqual([item["title"] for item in result["policies"]], [policy["title"]])
        self.assertEqual(
            [item["title"] for item in result["upstreamDownstream"]],
            [industry["title"]],
        )
        self.assertNotIn(company["title"], str(result))

    def test_industry_news_reuses_alert_direction_and_priority_rules(self):
        result = news_api.classify_news_item({
            "id": "official-risk",
            "symbol": "000725",
            "title": "股票交易异常波动风险提示公告",
            "content": "风险提示",
            "source": "巨潮公告",
            "url": "https://static.cninfo.com.cn/risk.pdf",
        })

        self.assertEqual(result["credibility_level"], "S")
        self.assertEqual(result["direction"], "negative")
        self.assertEqual(result["priority"], "P2")

    def test_news_contract_keeps_traceable_source_fields_without_ai_claims(self):
        item = {
            "id": "real-news-1",
            "title": "半导体公司公告标题",
            "content": "半导体公告原文摘要",
            "source": "巨潮公告",
            "url": "https://static.cninfo.com.cn/notice.pdf",
            "ctime": _today_timestamp(-10),
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

    def test_frontend_uses_confirmed_industry_categories_and_three_dimensions(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        industry_page = (root / "stock-monitor/src/app/industry/page.tsx").read_text(
            encoding="utf-8"
        )
        radar_card = (
            root / "stock-monitor/src/components/industry/RadarNewsCard.tsx"
        ).read_text(encoding="utf-8")
        radar_styles = (
            root / "stock-monitor/src/components/industry/RadarNewsCard.module.css"
        ).read_text(encoding="utf-8")
        monitor_card = (
            root / "stock-monitor/src/components/industry/IndustryMonitorCard.tsx"
        ).read_text(encoding="utf-8")
        monitor_styles = (
            root / "stock-monitor/src/components/industry/IndustryMonitorCard.module.css"
        ).read_text(encoding="utf-8")
        alerts_page = (
            root / "stock-monitor/src/app/alerts/page.tsx"
        ).read_text(encoding="utf-8")
        industry_types = (
            root / "stock-monitor/src/types/industry.ts"
        ).read_text(encoding="utf-8")

        for label in ("全部", "公司公告", "行业政策", "产业动态", "海外与管制"):
            self.assertIn(label, industry_page)
        for removed_label in ("只看半导体", "龙头公司"):
            self.assertNotIn(removed_label, industry_page)
        self.assertIn("news.direction", radar_card)
        self.assertIn("news.priority", radar_card)
        self.assertIn("verification_status", radar_card)
        self.assertIn("item.evidenceLevel", monitor_card)
        self.assertIn("item.direction", monitor_card)
        self.assertIn("item.priority", monitor_card)
        self.assertIn("uncertain: '影响待判断'", monitor_card)
        self.assertNotIn("uncertain: '待核验'", monitor_card)
        for frontend_source in (radar_card, monitor_card, alerts_page):
            self.assertIn("公告日期", frontend_source)
            self.assertIn("具体时刻未提供", frontend_source)
            self.assertIn("系统发现", frontend_source)
        for class_name in (
            "evidenceS",
            "evidenceA",
            "evidenceB",
            "evidenceC",
            "directionUncertain",
            "priorityP1",
            "priorityP2",
            "priorityP3",
        ):
            self.assertIn(f"styles.{class_name}", monitor_card)
            self.assertIn(f".{class_name}", monitor_styles)

        import re

        def css_properties(css: str, class_name: str) -> dict[str, str]:
            for selectors, body in re.findall(r"([^{}]+)\{([^{}]+)\}", css):
                if f".{class_name}" not in {item.strip() for item in selectors.split(",")}:
                    continue
                return {
                    key.strip(): value.strip()
                    for key, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body)
                }
            self.fail(f"missing CSS class: {class_name}")

        for monitor_class, radar_class in (
            ("evidenceS", "credS"),
            ("evidenceA", "credA"),
            ("evidenceB", "credB"),
            ("evidenceC", "credC"),
            ("directionPositive", "directionpositive"),
            ("directionNegative", "directionnegative"),
            ("directionNeutral", "directionneutral"),
            ("directionUncertain", "directionuncertain"),
            ("priorityP1", "priorityTag"),
            ("priorityP2", "priorityTag"),
            ("priorityP3", "priorityTag"),
        ):
            monitor_rule = css_properties(monitor_styles, monitor_class)
            radar_rule = css_properties(radar_styles, radar_class)
            for property_name in ("background", "color", "border"):
                self.assertEqual(
                    monitor_rule[property_name],
                    radar_rule[property_name],
                    f"{monitor_class} should match {radar_class} {property_name}",
                )
        self.assertIn("evidenceLevel", industry_types)
        self.assertIn("verificationStatus", industry_types)

    def test_frontend_directly_shows_insufficient_turnover_reason(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        overview_card = (
            root / "stock-monitor/src/components/stock/StockOverviewCard.tsx"
        ).read_text(encoding="utf-8")
        overview_styles = (
            root / "stock-monitor/src/components/stock/StockOverviewCard.module.css"
        ).read_text(encoding="utf-8")

        self.assertIn("turnoverStatus === 'insufficient'", overview_card)
        self.assertIn("turnoverRisk?.reason", overview_card)
        self.assertIn("styles.turnoverReason", overview_card)
        self.assertIn(".turnoverReason", overview_styles)

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

    @patch.dict(ai_analysis.os.environ, {"LLM_API_KEY": ""}, clear=False)
    @patch.object(ai_analysis.database, "get_cached_dynamics", return_value=None)
    @patch.object(ai_analysis.database, "get_latest_crawled_news")
    def test_industry_dynamics_excludes_unrated_sources_and_adds_three_dimensions(
        self,
        mock_get_news,
        _mock_get_cache,
    ):
        mock_get_news.return_value = [
            {
                "id": "industry-policy",
                "title": "工信部发布集成电路产业支持新规",
                "content": "政策原文",
                "source": "新浪财经",
                "url": "https://finance.sina.com.cn/policy",
                "ctime": _today_timestamp(-20),
                "symbol": "",
                "category": "policy",
            },
            {
                "id": "unrated",
                "title": "来源不明的产业传闻",
                "content": "无法核实",
                "source": "未知来源",
                "url": "",
                "ctime": _today_timestamp(-10),
                "symbol": "000725",
                "category": "industry",
            },
        ]

        result = ai_analysis.fetch_real_industry_dynamics("000725", "光学光电子")
        items = result["policies"] + result["upstreamDownstream"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidenceLevel"], "B")
        self.assertIn(items[0]["direction"], {"positive", "negative", "neutral", "uncertain"})
        self.assertIn(items[0]["priority"], {"P1", "P2", "P3"})
        self.assertEqual(items[0]["verificationStatus"], "单一来源")

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
    def setUp(self):
        ai_analysis._AI_SUCCESS_CACHE = {}

    def test_successful_ai_result_is_reused_for_twenty_minutes_only(self):
        payload = {"stockCode": "000725", "plainEnglishSummary": "已完成分析"}
        store_result = getattr(ai_analysis, "store_success_result", lambda *args, **kwargs: None)
        get_result = getattr(ai_analysis, "get_cached_success_result", lambda *args, **kwargs: None)

        store_result("000725", payload, now=100.0)

        self.assertEqual(get_result("000725", now=1_299.0), payload)
        self.assertIsNone(get_result("000725", now=1_301.0))

    def test_ai_cache_is_not_reused_across_quote_source_dates(self):
        matcher = getattr(ai_analysis, "cached_result_matches_source_session", None)
        self.assertIsNotNone(matcher, "AI缓存尚未校验行情所属日期")

        self.assertTrue(matcher(
            {"sourceDate": "2026-07-14"},
            {"source_date": "2026-07-14"},
        ))
        self.assertFalse(matcher(
            {"sourceDate": "2026-07-13"},
            {"source_date": "2026-07-14"},
        ))
        self.assertFalse(matcher({}, {"source_date": "2026-07-14"}))

    def test_ai_endpoint_checks_success_cache_before_new_analysis(self):
        source = __import__("inspect").getsource(ai_analysis.get_ai_attribution)

        self.assertIn("get_cached_success_result(symbol)", source)

    def test_manual_refresh_reuses_success_from_existing_history_table(self):
        payload = {
            "stockName": "京东方A",
            "stockCode": "000725",
            "changePercent": 1.2,
            "score": 70,
            "evidenceChain": {},
            "futureTrendPrediction": "情景分析",
            "plainEnglishSummary": "已完成分析",
            "aiJudgment": "关注证据变化",
            "credibility": "高",
            "riskNotice": "注意风险",
            "sourceDate": "2026-07-14",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            database,
            "DB_PATH",
            f"{temp_dir}/history.db",
        ):
            database.init_db()
            database.save_analysis_history("000725", "manual", "已完成分析", payload)
            ai_analysis._AI_SUCCESS_CACHE = {}
            with patch.dict(ai_analysis.os.environ, {"LLM_API_KEY": ""}, clear=False), patch.object(
                ai_analysis.database,
                "is_in_watchlist",
                return_value=True,
            ), patch.object(
                ai_analysis.fetcher,
                "get_stock_quote",
                return_value={
                    "name": "京东方A",
                    "change_pct": 1.2,
                    "source_date": "2026-07-14",
                    "source_time": "2026-07-14 10:30:00",
                },
            ) as mock_quote, patch.object(
                ai_analysis.fetcher,
                "get_stock_news",
                return_value=[],
            ), patch.object(
                ai_analysis.fetcher,
                "get_macro_environment",
                return_value={},
            ), patch.object(
                ai_analysis.fetcher,
                "get_industry_news_dehydrated",
                return_value=[],
            ), patch.object(
                ai_analysis.fetcher,
                "get_finance_summary",
                return_value={},
            ), patch.object(
                ai_analysis,
                "fetch_real_industry_dynamics",
                return_value={"policies": [], "upstreamDownstream": []},
            ):
                result = ai_analysis.get_ai_attribution("000725")

        self.assertEqual(result["sourceDate"], payload["sourceDate"])
        self.assertTrue(result["resultReused"])
        mock_quote.assert_called_once_with("000725")

    def test_p1_p2_event_bypasses_recent_cache_and_failure_is_not_retried(self):
        cached_payload = {
            "stockName": "京东方A",
            "stockCode": "000725",
            "changePercent": 1.2,
            "score": 70,
            "evidenceChain": {},
            "futureTrendPrediction": "旧情景分析",
            "plainEnglishSummary": "旧分析",
            "aiJudgment": "旧结论",
            "credibility": "高",
            "riskNotice": "旧风险",
        }
        trigger = "event:alert-001"
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            database,
            "DB_PATH",
            f"{temp_dir}/event-history.db",
        ):
            database.init_db()
            database.save_analysis_history("000725", "manual", "旧分析", cached_payload)
            ai_analysis._AI_SUCCESS_CACHE = {}
            with patch.dict(ai_analysis.os.environ, {"LLM_API_KEY": ""}, clear=False), patch.object(
                ai_analysis.database,
                "is_in_watchlist",
                return_value=True,
            ), patch.object(
                ai_analysis.fetcher,
                "get_stock_quote",
                return_value={"name": "京东方A", "change_pct": 1.2},
            ) as mock_quote, patch.object(
                ai_analysis.fetcher,
                "get_stock_news",
                return_value=[],
            ), patch.object(
                ai_analysis.fetcher,
                "get_macro_environment",
                return_value={},
            ), patch.object(
                ai_analysis.fetcher,
                "get_industry_news_dehydrated",
                return_value=[],
            ), patch.object(
                ai_analysis.fetcher,
                "get_finance_summary",
                return_value={},
            ), patch.object(
                ai_analysis,
                "fetch_real_industry_dynamics",
                return_value={"policies": [], "upstreamDownstream": []},
            ):
                first = ai_analysis.get_ai_attribution("000725", trigger=trigger)
                second = ai_analysis.get_ai_attribution("000725", trigger=trigger)
            history_lookup = getattr(
                database,
                "get_analysis_history_by_trigger",
                lambda *_args, **_kwargs: None,
            )
            saved = history_lookup("000725", trigger)

        self.assertNotEqual(first, cached_payload)
        self.assertEqual(second, first)
        self.assertEqual(mock_quote.call_count, 1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["full_json"], first)

    def test_existing_history_table_atomically_claims_and_completes_one_trigger(self):
        trigger = "event:atomic-alert"
        payload = {
            "stockName": "京东方A",
            "stockCode": "000725",
            "changePercent": 1.2,
            "score": 70,
            "evidenceChain": {},
            "futureTrendPrediction": "情景分析",
            "plainEnglishSummary": "事件分析完成",
            "aiJudgment": "关注证据变化",
            "credibility": "高",
            "riskNotice": "注意风险",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            database,
            "DB_PATH",
            f"{temp_dir}/atomic-history.db",
        ):
            database.init_db()
            claim = getattr(
                database,
                "claim_analysis_trigger",
                lambda *_args, **_kwargs: False,
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _index: claim("000725", trigger),
                    range(2),
                ))
            recent_while_running = database.get_recent_successful_analysis(
                "000725",
                20 * 60,
            )
            complete = getattr(
                database,
                "complete_analysis_trigger",
                lambda *_args, **_kwargs: False,
            )
            completed = complete("000725", trigger, "事件分析完成", payload)
            saved = database.get_analysis_history_by_trigger("000725", trigger)
            conn = sqlite3.connect(database.DB_PATH)
            row_count = conn.execute(
                "SELECT COUNT(*) FROM ai_analysis_history WHERE symbol=? AND trigger_type=?",
                ("000725", trigger),
            ).fetchone()[0]
            conn.close()

        self.assertEqual(sorted(results), [False, True])
        self.assertIsNone(recent_while_running)
        self.assertTrue(completed)
        self.assertEqual(saved["full_json"], payload)
        self.assertEqual(row_count, 1)

    def test_unique_trigger_is_claimed_before_fetching_analysis_data(self):
        running = {
            "stockName": "000725",
            "stockCode": "000725",
            "changePercent": None,
            "score": None,
            "evidenceChain": {},
            "futureTrendPrediction": "分析任务执行中",
            "plainEnglishSummary": "分析任务执行中",
            "aiJudgment": "分析任务执行中",
            "credibility": "待生成",
            "riskNotice": "",
            "analysisStatus": "running",
        }
        with patch.object(
            ai_analysis.database,
            "is_in_watchlist",
            return_value=True,
        ), patch.object(
            ai_analysis.database,
            "get_analysis_history_by_trigger",
            side_effect=[None, {"full_json": running}],
        ), patch.object(
            ai_analysis.database,
            "claim_analysis_trigger",
            return_value=False,
            create=True,
        ) as mock_claim, patch.object(
            ai_analysis.fetcher,
            "get_stock_quote",
            return_value={"name": "京东方A", "change_pct": 1.2},
        ) as mock_quote, patch.object(
            ai_analysis.fetcher,
            "get_stock_news",
            return_value=[],
        ), patch.object(
            ai_analysis.fetcher,
            "get_macro_environment",
            return_value={},
        ), patch.object(
            ai_analysis.fetcher,
            "get_industry_news_dehydrated",
            return_value=[],
        ), patch.object(
            ai_analysis.fetcher,
            "get_finance_summary",
            return_value={},
        ), patch.object(
            ai_analysis,
            "fetch_real_industry_dynamics",
            return_value={"policies": [], "upstreamDownstream": []},
        ), patch.dict(ai_analysis.os.environ, {"LLM_API_KEY": ""}, clear=False):
            result = ai_analysis.get_ai_attribution(
                "000725",
                trigger="event:atomic-alert",
            )

        self.assertEqual(result, running)
        mock_claim.assert_called_once_with("000725", "event:atomic-alert")
        mock_quote.assert_not_called()

    def test_event_trigger_context_contains_the_saved_alert_evidence(self):
        alert = {
            "id": "alert-context",
            "title": "半年度业绩预增公告",
            "direction": "positive",
            "priority": "P2",
            "evidenceLevel": "S",
            "summary": "官方披露业绩预增，仍需核对原文。",
            "source": "深交所公告",
            "sourceUrl": "https://example.com/notice",
        }
        build_context = getattr(
            ai_analysis,
            "build_trigger_event_context",
            lambda _trigger: "",
        )
        with patch.object(alert_repository, "get_alert", return_value=alert):
            context = build_context("event:alert-context")

        self.assertIn(alert["title"], context)
        self.assertIn("P2", context)
        self.assertIn("S", context)
        self.assertIn(alert["summary"], context)
        self.assertIn(alert["sourceUrl"], context)

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
            main.format_industry_fund_flow("trading", False, None, False),
            "暂无行业资金流数据",
        )
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
            "最近一次行业资金流（上游未提供数据日期） +1.2 亿元",
        )
        self.assertEqual(
            main.format_industry_fund_flow("pre_open", True, -1.2, False),
            "盘前参考（上游未提供数据日期） -1.2 亿元",
        )

    def test_undated_stock_fund_flow_exposes_its_time_scope(self):
        self.assertEqual(
            main.describe_undated_fund_flow_scope("trading"),
            "交易时段参考（上游未提供数据日期）",
        )
        self.assertEqual(
            main.describe_undated_fund_flow_scope("pre_open"),
            "上一交易时段参考（上游未提供数据日期）",
        )
        self.assertEqual(
            main.describe_undated_fund_flow_scope("closed"),
            "最近交易时段（上游未提供数据日期）",
        )

    def test_financial_formatters_preserve_real_zero_values(self):
        money_formatter = getattr(main, "format_financial_money", None)
        percent_formatter = getattr(main, "format_financial_percent", None)
        self.assertIsNotNone(money_formatter, "财务金额格式化尚未区分0与缺失")
        self.assertIsNotNone(percent_formatter, "财务百分比格式化尚未区分0与缺失")

        self.assertEqual(money_formatter(0), "0.00")
        self.assertEqual(percent_formatter(0), "0.00%")
        self.assertEqual(money_formatter(None), "-")
        self.assertEqual(percent_formatter(None), "-")

    def test_hk_report_type_uses_report_date(self):
        classifier = getattr(main, "classify_report_type_by_date", None)
        self.assertIsNotNone(classifier, "港股报告期类型尚未按日期判断")
        self.assertEqual(classifier("2025-12-31"), ("2025年报", "年报"))
        self.assertEqual(classifier("2025-06-30"), ("2025中报", "中报"))

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
        fields = [""] * 50
        fields[1] = "真实测试股票"
        fields[2] = "600001"
        fields[3] = "10.00"
        fields[30] = "20260711150000"
        fields[32] = "6.50"
        fields[49] = "1.23"
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
    @patch.object(main.database, "is_in_watchlist", return_value=True)
    @patch.object(risk_engine, "process_market_snapshot", create=True)
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
        mock_process_market_snapshot,
        _mock_is_in_watchlist,
        _mock_get_fund_flow,
    ):
        mock_process_market_snapshot.return_value = {
            "riskStatus": "normal",
            "priority": None,
            "direction": "positive",
            "signals": [],
            "turnoverRisk": {
                "status": "insufficient",
                "label": "样本不足",
                "baseline": None,
                "multiple": None,
                "reason": "同一时点有效历史不足20个交易日",
            },
            "reason": "当前未触发量价风险规则",
            "dataComplete": False,
        }
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
        self.assertIs(result.get("isMonitored"), True)
        self.assertEqual(result.get("monitoringStatus"), "active")
        self.assertEqual(result["details"].get("turnoverRisk", {}).get("label"), "样本不足")
        self.assertEqual(result.get("risk", {}).get("riskStatus"), "normal")
        self.assertNotIn("updateTime", result)

    @patch.object(alert_repository, "get_latest_signal_state")
    @patch.object(main, "get_sina_stock_fund_flow", return_value=None)
    @patch.object(main.requests, "get")
    def test_batch_overview_includes_latest_risk_state(
        self,
        mock_get,
        _mock_fund_flow,
        mock_get_risk,
    ):
        fields = [""] * 50
        fields[1] = "京东方Ａ"
        fields[2] = "000725"
        fields[3] = "7.59"
        fields[30] = "20260713103000"
        fields[32] = "-5.20"
        response = MagicMock()
        response.text = f'v_sz000725="{"~".join(fields)}";'
        mock_get.return_value = response
        mock_get_risk.return_value = {
            "riskStatus": "warning",
            "priority": "P2",
            "reason": "量价风险升高",
        }

        result = main.get_batch_overview("000725")["data"][0]

        self.assertEqual(result.get("risk", {}).get("priority"), "P2")
        self.assertEqual(result.get("sourceTime"), "2026-07-13 10:30:00")
        self.assertTrue(result.get("fetchedAt", "").endswith("+08:00"))
        mock_get_risk.assert_called_once_with("000725")

    @patch.object(main.requests, "get")
    def test_hk_kline_normalizes_prefixed_symbol_once(self, mock_get):
        response = MagicMock()
        response.json.return_value = {
            "code": 0,
            "data": {
                "hk00700": {
                    "day": [["2026-07-13", "463.2", "457.6", "473.8", "456.2", "24291842"]]
                }
            },
        }
        mock_get.return_value = response

        result = main.get_stock_kline("hk00700", period="day")

        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["close"], 457.6)
        self.assertIn("param=hk00700,day", mock_get.call_args.args[0])
        self.assertNotIn("hkhk00700", mock_get.call_args.args[0])

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
        self.assertEqual(result["industryDataStatus"], "not_applicable")
        self.assertEqual(result["heatScoreMethod"], "unavailable")

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
    def test_abnormal_peers_use_real_tencent_volume_ratio_without_inventing_other_metrics(
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
        self.assertEqual(item["volumeRatio"], 1.23)
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
    @staticmethod
    def _request(path: str) -> Request:
        return Request({
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("127.0.0.1", 8001),
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        })

    def test_read_only_ai_history_is_public_but_ai_generation_stays_protected(self):
        self.assertFalse(main.request_requires_backend_token(
            self._request("/api/stock/ai_history/000725")
        ))
        self.assertFalse(main.request_requires_backend_token(
            self._request("/api/stock/ai_history_all/000725")
        ))
        self.assertTrue(main.request_requires_backend_token(
            self._request("/api/stock/ai_attribution/000725")
        ))

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
    def setUp(self):
        if hasattr(main, "_AI_ANALYSIS_COMPLETED_ROUNDS"):
            main._AI_ANALYSIS_COMPLETED_ROUNDS.clear()
        monitoring_health.reset_runtime_health()

    @patch.object(news_collector, "run_collection", return_value=7)
    def test_general_news_collection_records_runtime_health(self, _mock_collection):
        main.run_collection_sync()

        state = monitoring_health.get_task_states()["generalNews"]
        self.assertEqual(state["status"], "healthy")
        self.assertEqual(state["itemCount"], 7)

    def test_ai_schedule_keeps_the_four_confirmed_slots(self):
        self.assertEqual(
            main.AI_ANALYSIS_SLOTS,
            ("10:30", "11:30", "15:00", "22:00"),
        )

    @patch.object(main, "build_ai_analysis_round_id", return_value="2026-07-13:10:30")
    @patch.object(main.asyncio, "to_thread", new_callable=AsyncMock)
    def test_auto_analysis_runs_in_background_without_self_http(
        self,
        mock_to_thread,
        _mock_round_id,
    ):
        asyncio.run(main.auto_analyze_watchlist("10:30"))

        mock_to_thread.assert_awaited_once_with(
            main.run_ai_analysis_round_sync,
            "2026-07-13:10:30",
        )

    @patch.object(main.database, "get_watchlist", return_value=[{"stockCode": "000725"}])
    @patch.object(main, "get_ai_attribution")
    def test_ai_round_runs_only_once(self, mock_analyze, _mock_get_watchlist):
        mock_analyze.return_value = {"credibility": "高"}
        first = main.run_ai_analysis_round_sync("2026-07-13:10:30")
        second = main.run_ai_analysis_round_sync("2026-07-13:10:30")

        self.assertEqual(first, "completed")
        self.assertEqual(second, "skipped_duplicate")
        mock_analyze.assert_called_once_with(
            "000725",
            trigger="auto:000725-20260713-1030",
        )

    @patch.object(main.database, "get_watchlist", return_value=[{"stockCode": "000725"}])
    @patch.object(main, "get_ai_attribution", side_effect=RuntimeError("LLM unavailable"))
    def test_failed_ai_round_is_not_automatically_retried(
        self,
        mock_analyze,
        _mock_get_watchlist,
    ):
        trigger = "auto:000725-20260713-1130"
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            database,
            "DB_PATH",
            f"{temp_dir}/failed-round.db",
        ):
            database.init_db()
            first = main.run_ai_analysis_round_sync("2026-07-13:11:30")
            second = main.run_ai_analysis_round_sync("2026-07-13:11:30")
            saved = database.get_analysis_history_by_trigger("000725", trigger)

        self.assertEqual(first, "completed_with_errors")
        self.assertEqual(second, "skipped_duplicate")
        self.assertEqual(mock_analyze.call_count, 1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["full_json"]["analysisStatus"], "failed")

    def test_ai_round_skips_when_previous_round_is_still_running(self):
        self.assertTrue(main._AI_ANALYSIS_LOCK.acquire(blocking=False))
        try:
            result = main.run_ai_analysis_round_sync("2026-07-13:15:00")
        finally:
            main._AI_ANALYSIS_LOCK.release()

        self.assertEqual(result, "skipped_busy")

    @patch.object(main.database, "get_watchlist", return_value=[])
    @patch.object(main.asyncio, "to_thread", new_callable=AsyncMock)
    def test_startup_industry_update_runs_in_background_thread(
        self,
        mock_to_thread,
        _mock_get_watchlist,
    ):
        asyncio.run(main.auto_update_watchlist_industry())

        mock_to_thread.assert_awaited_once()

    @patch.object(main.asyncio, "to_thread", new_callable=AsyncMock)
    def test_official_alert_collection_runs_in_background_thread(self, mock_to_thread):
        asyncio.run(main.auto_collect_official_alerts())

        mock_to_thread.assert_awaited_once_with(
            main.run_official_alert_collection_sync
        )

    @patch.object(main.asyncio, "to_thread", new_callable=AsyncMock)
    def test_market_risk_collection_runs_in_background_thread(self, mock_to_thread):
        self.assertTrue(hasattr(main, "auto_collect_market_risk"))
        asyncio.run(main.auto_collect_market_risk())

        mock_to_thread.assert_awaited_once_with(
            main.run_market_risk_collection_sync
        )


if __name__ == "__main__":
    unittest.main()
