import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RepositorySafetyTests(unittest.TestCase):
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

    def test_alert_ui_does_not_fake_success_or_default_an_email(self):
        component = (
            ROOT / "stock-monitor/src/components/alert/AlertSettingCard.tsx"
        ).read_text(encoding="utf-8")
        home_page = (ROOT / "stock-monitor/src/app/page.tsx").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("setTimeout", component)
        self.assertNotIn("admin@example.com", home_page)
        self.assertIn("提醒功能准备中", component)

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
        self.assertIn("relatedRequestInFlight", home_page)
        self.assertIn("industryRequestInFlight", home_page)
        self.assertGreaterEqual(home_page.count("setInterval"), 2)


if __name__ == "__main__":
    unittest.main()
