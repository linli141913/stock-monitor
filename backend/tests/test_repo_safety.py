import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RepositorySafetyTests(unittest.TestCase):

    def test_market_risk_card_does_not_claim_original_article_is_missing(self):
        alerts_page = (ROOT / "stock-monitor/src/app/alerts/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("行情规则提醒，无资讯原文", alerts_page)
        self.assertIn("alert.eventType === 'market_risk'", alerts_page)

    def test_root_gitignore_blocks_local_secrets_and_runtime_artifacts(self):
        gitignore_path = ROOT / ".gitignore"
        self.assertTrue(gitignore_path.is_file())
        content = gitignore_path.read_text(encoding="utf-8")

        for expected in (
            ".env",
            "backend/venv/",
            "*.db",
            "*.log",
            ".DS_Store",
        ):
            self.assertIn(expected, content)

    def test_safe_environment_templates_exist(self):
        self.assertTrue((ROOT / "backend/.env.example").is_file())
        self.assertTrue((ROOT / "stock-monitor/.env.example").is_file())

    def test_alert_email_template_lists_only_placeholder_configuration(self):
        content = (ROOT / "backend/.env.example").read_text(encoding="utf-8")

        for name in (
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "SMTP_FROM",
            "ALERT_EMAIL_TO",
        ):
            self.assertIn(name, content)
        self.assertNotIn("admin@example.com", content)

    def test_home_monitoring_card_uses_real_health_endpoint(self):
        component = (
            ROOT / "stock-monitor/src/components/alert/AlertSettingCard.tsx"
        ).read_text(encoding="utf-8")
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("admin@example.com", home_page)
        self.assertIn("/api/backend/api/monitoring/health", component)
        self.assertIn("监测运行状态", component)
        self.assertIn("邮件未配置", component)
        self.assertNotIn("提醒功能准备中", component)

    def test_frontend_uses_server_proxy_for_protected_backend_calls(self):
        frontend_files = (
            "src/app/page.tsx",
            "src/app/watchlist/page.tsx",
            "src/app/industry/page.tsx",
            "src/hooks/useWatchlist.ts",
            "src/components/stock/AiAttributionTab.tsx",
            "src/components/stock/FinancialSummaryTab.tsx",
        )
        frontend_root = ROOT / "stock-monitor"
        for relative_path in frontend_files:
            content = (frontend_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("NEXT_PUBLIC_API_BASE", content)
            self.assertIn("/api/backend", content)

        proxy_path = frontend_root / "src/app/api/backend/[...path]/route.ts"
        self.assertTrue(proxy_path.is_file())
        proxy_content = proxy_path.read_text(encoding="utf-8")
        self.assertIn("BACKEND_URL", proxy_content)
        self.assertIn("BACKEND_API_TOKEN", proxy_content)

    def test_home_polling_uses_separate_intervals_and_in_flight_guards(self):
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("const OVERVIEW_REFRESH_INTERVAL = 10 * 1000", home_page)
        self.assertIn("const SLOW_DATA_REFRESH_INTERVAL = 2 * 60 * 1000", home_page)
        self.assertNotIn("AUTO_REFRESH_INTERVAL = 5 * 1000", home_page)
        self.assertIn("overviewRefreshTimer", home_page)
        self.assertIn("slowDataRefreshTimer", home_page)
        self.assertIn("overviewRequestInFlight", home_page)
        self.assertIn("industryRequestInFlight", home_page)
        self.assertGreaterEqual(home_page.count("setInterval"), 2)

    def test_home_removes_noisy_cards_and_global_news_fallback(self):
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("RelatedStocksCard", home_page)
        self.assertNotIn("fetchRelatedPrices", home_page)
        self.assertNotIn("globalNews.length > 0 ? globalNews", home_page)
        self.assertIn("news={companyData?.news || []}", home_page)

    def test_ai_panel_keeps_explanation_without_score_or_prediction(self):
        component = (
            ROOT / "stock-monitor/src/components/stock/AiAttributionTab.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("事件与风险解释", component)
        self.assertNotIn("ReactECharts", component)
        self.assertNotIn("未来走势推演", component)
        self.assertNotIn("多头共振", component)

    def test_stock_overview_shows_backend_monitoring_state(self):
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )
        stock_types = (ROOT / "stock-monitor/src/types/stock.ts").read_text(
            encoding="utf-8"
        )
        overview_card = (
            ROOT / "stock-monitor/src/components/stock/StockOverviewCard.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("monitoringStatus", home_page)
        self.assertIn("monitoringStatus", stock_types)
        self.assertIn("后台监测已生效", overview_card)
        self.assertIn("后台监测未启用", overview_card)
        self.assertIn("监测状态未知", overview_card)

    def test_turnover_and_watchlist_risk_labels_use_text_and_color(self):
        overview_card = (
            ROOT / "stock-monitor/src/components/stock/StockOverviewCard.tsx"
        ).read_text(encoding="utf-8")
        overview_styles = (
            ROOT / "stock-monitor/src/components/stock/StockOverviewCard.module.css"
        ).read_text(encoding="utf-8")
        watchlist_page = (
            ROOT / "stock-monitor/src/app/watchlist/page.tsx"
        ).read_text(encoding="utf-8")

        for label in ("正常", "活跃", "警惕", "样本不足", "暂无判断"):
            self.assertIn(label, overview_card)
        for style_name in (
            "turnoverNormal",
            "turnoverActive",
            "turnoverWarning",
            "turnoverUnavailable",
        ):
            self.assertIn(style_name, overview_styles)
        self.assertIn("风险状态", watchlist_page)
        self.assertIn("riskStatus", watchlist_page)

    def test_alert_center_uses_server_proxy_and_shows_truthful_states(self):
        alert_page = (ROOT / "stock-monitor/src/app/alerts/page.tsx").read_text(
            encoding="utf-8"
        )
        header = (
            ROOT / "stock-monitor/src/components/layout/AppHeader.tsx"
        ).read_text(encoding="utf-8")
        proxy = (
            ROOT / "stock-monitor/src/app/api/backend/[...path]/route.ts"
        ).read_text(encoding="utf-8")
        alert_types = ROOT / "stock-monitor/src/types/alert-event.ts"

        self.assertTrue(alert_types.is_file())
        self.assertIn("/api/backend/api/alerts", alert_page)
        self.assertIn("暂无提醒", alert_page)
        self.assertIn("邮件未配置", alert_page)
        self.assertIn("/api/backend/api/alerts/unread-count", header)
        self.assertIn("提醒中心", header)
        self.assertIn("export const PUT", proxy)
        self.assertIn("export const PATCH", proxy)

    def test_alert_read_updates_header_badge_immediately(self):
        alert_page = (ROOT / "stock-monitor/src/app/alerts/page.tsx").read_text(
            encoding="utf-8"
        )
        header = (
            ROOT / "stock-monitor/src/components/layout/AppHeader.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("alerts:unread-changed", alert_page)
        self.assertIn("alerts:unread-changed", header)
        self.assertIn("addEventListener", header)

    def test_monitoring_card_has_replaceable_global_email_and_test_action(self):
        component = (
            ROOT / "stock-monitor/src/components/alert/AlertSettingCard.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/backend/api/alerts/email-settings", component)
        self.assertIn("保存收件邮箱", component)
        self.assertIn("发送测试邮件", component)
        self.assertIn("aria-live", component)

    def test_watchlist_shows_risk_trigger_reason_without_hover(self):
        page = (ROOT / "stock-monitor/src/app/watchlist/page.tsx").read_text(
            encoding="utf-8"
        )
        styles = (
            ROOT / "stock-monitor/src/app/watchlist/page.module.css"
        ).read_text(encoding="utf-8")

        self.assertIn("item.risk?.reason", page)
        self.assertIn("riskReason", page)
        self.assertIn(".riskReason", styles)

    def test_watchlist_exposes_real_per_stock_alert_preferences(self):
        page = (ROOT / "stock-monitor/src/app/watchlist/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("/api/alerts/preferences?symbol=", page)
        self.assertIn("method: 'PUT'", page)
        self.assertIn("启用提醒", page)
        self.assertIn("发送邮件", page)
        self.assertIn("P2即时邮件", page)
        self.assertIn("保存失败", page)

    def test_industry_polling_keeps_cached_card_visible_during_background_refresh(self):
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )
        card = (
            ROOT / "stock-monitor/src/components/industry/IndustryMonitorCard.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("const SLOW_DATA_REQUEST_TIMEOUT = 12 * 1000", home_page)
        self.assertIn("const fetchIndustry = useCallback", home_page)
        self.assertIn("const fetchAbnormalPeers = useCallback", home_page)
        self.assertIn("fetchIndustry(stockCode, true)", home_page)
        self.assertIn("setIndustryRefreshing(true)", home_page)
        self.assertIn("AbortController", home_page)
        self.assertIn("refreshing?: boolean", card)
        self.assertIn("statusMessage?: string", card)
        self.assertNotIn("AI 分析中...", card)

    def test_ai_empty_state_keeps_history_access_and_reports_load_failures(self):
        component = (
            ROOT / "stock-monitor/src/components/stock/AiAttributionTab.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("查看历史记录", component)
        self.assertIn("历史记录加载失败", component)
        self.assertIn("setShowHistoryModal(true)", component)
        self.assertIn("setData(historyItems[0].full_json)", component)
        self.assertIn("加载历史...", component)


if __name__ == "__main__":
    unittest.main()
