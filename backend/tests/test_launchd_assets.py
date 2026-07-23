import os
import plistlib
import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHD_DIR = PROJECT_ROOT / "ops" / "launchd"
BACKEND_PLIST = LAUNCHD_DIR / "com.linjian.stock-monitor.fastapi.plist"
BACKEND_RADAR_DISABLED_PLIST = (
    LAUNCHD_DIR / "com.linjian.stock-monitor.fastapi.radar-disabled.plist"
)
BACKEND_SECTOR_ENABLED_PLIST = (
    LAUNCHD_DIR
    / "com.linjian.stock-monitor.fastapi.sector-shadow-enabled.plist"
)
BACKEND_MARKET_ENABLED_PLIST = (
    LAUNCHD_DIR
    / "com.linjian.stock-monitor.fastapi.market-shadow-enabled.plist"
)
NGROK_PLIST = LAUNCHD_DIR / "com.linjian.stock-monitor.ngrok.plist"
BACKEND_RUNNER = LAUNCHD_DIR / "run-backend.sh"
NGROK_RUNNER = LAUNCHD_DIR / "run-ngrok.sh"
MANAGER = LAUNCHD_DIR / "manage.sh"
RUNTIME_DIR = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/launchd"
)
RADAR_RUNTIME_DIR = Path(
    "/Users/linjian/Library/Application Support/stock-monitor/runtime"
)
BACKEND_RUNTIME_RUNNER = RUNTIME_DIR / "run-backend.sh"
NGROK_RUNTIME_RUNNER = RUNTIME_DIR / "run-ngrok.sh"


class LaunchdAssetTests(unittest.TestCase):
    def load_plist(self, path):
        with path.open("rb") as handle:
            return plistlib.load(handle)

    def test_plists_define_independent_single_service_jobs(self):
        backend = self.load_plist(BACKEND_PLIST)
        backend_radar_disabled = self.load_plist(BACKEND_RADAR_DISABLED_PLIST)
        backend_sector_enabled = self.load_plist(BACKEND_SECTOR_ENABLED_PLIST)
        backend_market_enabled = self.load_plist(BACKEND_MARKET_ENABLED_PLIST)
        ngrok = self.load_plist(NGROK_PLIST)

        self.assertEqual(
            backend["Label"],
            "com.linjian.stock-monitor.fastapi",
        )
        self.assertEqual(
            ngrok["Label"],
            "com.linjian.stock-monitor.ngrok",
        )
        self.assertNotEqual(backend["Label"], ngrok["Label"])

        for payload in (backend, ngrok):
            self.assertIs(payload["RunAtLoad"], True)
            self.assertIs(payload["KeepAlive"], True)
            self.assertEqual(payload["ThrottleInterval"], 30)
            self.assertEqual(payload["ProcessType"], "Background")
            self.assertEqual(payload["ProgramArguments"][0], "/bin/zsh")
            self.assertTrue(payload["StandardOutPath"].startswith(
                "/Users/linjian/Library/Logs/stock-monitor/"
            ))
            self.assertTrue(payload["StandardErrorPath"].startswith(
                "/Users/linjian/Library/Logs/stock-monitor/"
            ))

        self.assertEqual(
            backend["WorkingDirectory"],
            str(PROJECT_ROOT / "backend"),
        )
        self.assertEqual(ngrok["WorkingDirectory"], str(PROJECT_ROOT))
        self.assertEqual(
            backend["ProgramArguments"][1],
            str(BACKEND_RUNTIME_RUNNER),
        )
        self.assertEqual(
            ngrok["ProgramArguments"][1],
            str(NGROK_RUNTIME_RUNNER),
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["PYTHONUNBUFFERED"],
            "1",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_ENABLED"],
            "true",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_SHADOW_MODE"],
            "true",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_SCAN_INTERVAL_SECONDS"],
            "180",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_ETF_SCAN_INTERVAL_SECONDS"],
            "300",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_SECTOR_SHADOW_ENABLED"],
            "false",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_SECTOR_SCAN_INTERVAL_SECONDS"],
            "180",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_MARKET_SHADOW_ENABLED"],
            "false",
        )
        self.assertEqual(
            backend["EnvironmentVariables"]["RADAR_MARKET_SCAN_INTERVAL_SECONDS"],
            "180",
        )
        self.assertNotIn("RADAR_LLM_API_KEY", backend["EnvironmentVariables"])
        self.assertEqual(
            backend_radar_disabled["EnvironmentVariables"]["RADAR_ENABLED"],
            "false",
        )
        expected_disabled_environment = dict(backend["EnvironmentVariables"])
        expected_disabled_environment["RADAR_ENABLED"] = "false"
        self.assertEqual(
            backend_radar_disabled["EnvironmentVariables"],
            expected_disabled_environment,
        )
        expected_sector_environment = dict(backend["EnvironmentVariables"])
        expected_sector_environment["RADAR_SECTOR_SHADOW_ENABLED"] = "true"
        self.assertEqual(
            backend_sector_enabled["EnvironmentVariables"],
            expected_sector_environment,
        )
        expected_market_environment = dict(expected_sector_environment)
        expected_market_environment["RADAR_MARKET_SHADOW_ENABLED"] = "true"
        self.assertEqual(
            backend_market_enabled["EnvironmentVariables"],
            expected_market_environment,
        )
        self.assertEqual(
            backend_radar_disabled["ProgramArguments"],
            backend["ProgramArguments"],
        )
        self.assertEqual(
            backend_radar_disabled["Label"],
            backend["Label"],
        )
        self.assertEqual(
            backend_sector_enabled["ProgramArguments"],
            backend["ProgramArguments"],
        )
        self.assertEqual(backend_sector_enabled["Label"], backend["Label"])
        self.assertEqual(
            backend_market_enabled["ProgramArguments"],
            backend["ProgramArguments"],
        )
        self.assertEqual(backend_market_enabled["Label"], backend["Label"])

    def test_launchd_assets_do_not_embed_secrets_or_browser_variables(self):
        assets = [
            BACKEND_PLIST,
            BACKEND_RADAR_DISABLED_PLIST,
            BACKEND_SECTOR_ENABLED_PLIST,
            BACKEND_MARKET_ENABLED_PLIST,
            NGROK_PLIST,
            BACKEND_RUNNER,
            NGROK_RUNNER,
            MANAGER,
        ]
        serialized = "\n".join(
            path.read_text(encoding="utf-8") for path in assets
        )

        self.assertNotIn("BACKEND_API_TOKEN", serialized)
        self.assertNotIn("RADAR_LLM_API_KEY", serialized)
        self.assertNotIn("NEXT_PUBLIC_", serialized)
        self.assertNotRegex(serialized.lower(), r"authtoken\s*[:=]")
        self.assertNotRegex(serialized, r"sk-[A-Za-z0-9_-]{16,}")

    def test_scripts_are_executable_and_pass_zsh_syntax_check(self):
        for script in (BACKEND_RUNNER, NGROK_RUNNER, MANAGER):
            with self.subTest(script=script.name):
                self.assertTrue(os.access(script, os.X_OK))
                result = subprocess.run(
                    ["/bin/zsh", "-n", str(script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stderr or result.stdout,
                )

    def test_manager_validate_is_read_only_and_succeeds(self):
        result = subprocess.run(
            [str(MANAGER), "validate"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("launchd资产验证通过", result.stdout)

    def test_manager_has_explicit_lifecycle_commands_without_auto_kill(self):
        content = MANAGER.read_text(encoding="utf-8")

        for command in (
            "validate",
            "preflight",
            "install",
            "reload-backend",
            "enable-radar",
            "disable-radar",
            "enable-sector-shadow",
            "disable-sector-shadow",
            "enable-market-shadow",
            "disable-market-shadow",
            "status",
            "uninstall",
            "rollback-screen",
        ):
            self.assertRegex(content, rf"\b{re.escape(command)}\b")

        self.assertNotRegex(
            content,
            r"(^|\s)(kill|pkill|rm)\s",
        )
        self.assertNotIn("screen -X quit", content)
        self.assertIn("preflight_install", content)
        self.assertIn("archive_installed_assets", content)
        self.assertIn("reload_backend", content)
        self.assertIn("rollback_backend_reload", content)
        self.assertIn("bootstrap_with_retry", content)
        self.assertIn("BACKEND_RADAR_DISABLED_SOURCE", content)
        self.assertIn("BACKEND_SECTOR_ENABLED_SOURCE", content)
        self.assertIn("BACKEND_MARKET_ENABLED_SOURCE", content)
        self.assertIn(
            'disable-market-shadow)\n    reload_backend "$BACKEND_SECTOR_ENABLED_SOURCE"',
            content,
        )
        self.assertIn(str(RUNTIME_DIR), content)
        self.assertIn(str(RADAR_RUNTIME_DIR), content)
        self.assertIn('/bin/chmod 700 "$RADAR_RUNTIME_DIR"', content)
        self.assertIn(
            '/usr/bin/install -m 700 "$BACKEND_RUNNER" "$BACKEND_RUNTIME_RUNNER"',
            content,
        )
        self.assertIn(
            '/usr/bin/install -m 700 "$NGROK_RUNNER" "$NGROK_RUNTIME_RUNNER"',
            content,
        )
        self.assertIn("stock-monitor-backend", content)
        self.assertIn("stock-monitor-ngrok", content)

        reload_body = content.split("reload_backend()", 1)[1].split(
            "\n}\n",
            1,
        )[0]
        self.assertIn('bootout_if_loaded "$BACKEND_LABEL"', reload_body)
        self.assertIn(
            'bootstrap_with_retry "$GUI_DOMAIN" "$BACKEND_DEST" "$BACKEND_LABEL"',
            reload_body,
        )
        self.assertNotIn('bootout_if_loaded "$NGROK_LABEL"', reload_body)

        rollback_body = content.split("rollback_backend_reload()", 1)[1].split(
            "\n}\n",
            1,
        )[0]
        self.assertIn(
            'bootstrap_with_retry "$GUI_DOMAIN" "$BACKEND_DEST" "$BACKEND_LABEL"',
            rollback_body,
        )

    def test_runners_keep_fixed_paths_ports_and_existing_ngrok_contract(self):
        backend = BACKEND_RUNNER.read_text(encoding="utf-8")
        ngrok = NGROK_RUNNER.read_text(encoding="utf-8")

        self.assertIn(str(PROJECT_ROOT / "backend"), backend)
        self.assertIn(str(PROJECT_ROOT / "backend" / "venv" / "bin" / "python"), backend)
        self.assertIn("-iTCP:8001", backend)
        self.assertIn("main.py", backend)
        self.assertLess(
            backend.index('cd "$BACKEND_DIR"'),
            backend.index("-iTCP:8001"),
        )

        self.assertIn(str(PROJECT_ROOT / "ngrok"), ngrok)
        self.assertIn("127.0.0.1:8001", ngrok)
        self.assertIn("banister-drilling-jawless.ngrok-free.dev", ngrok)
        self.assertIn(
            "/Users/linjian/Library/Application Support/ngrok/ngrok.yml",
            ngrok,
        )
        self.assertLess(
            ngrok.index('cd "$PROJECT_ROOT"'),
            ngrok.index("pgrep -x ngrok"),
        )


if __name__ == "__main__":
    unittest.main()
