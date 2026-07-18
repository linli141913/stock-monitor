import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEALTH_ROUTE = PROJECT_ROOT / "stock-monitor/src/app/api/health/route.ts"
PROBE_SCRIPT = PROJECT_ROOT / "ops/health-monitor/probe.py"
WORKFLOW = PROJECT_ROOT / ".github/workflows/external-health-monitor.yml"


def load_probe_module():
    spec = importlib.util.spec_from_file_location("external_health_probe", PROBE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载外部健康探针模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExternalHealthMonitorTests(unittest.TestCase):
    def test_public_health_route_is_sanitized_and_checks_full_chain(self):
        self.assertTrue(HEALTH_ROUTE.is_file())
        content = HEALTH_ROUTE.read_text(encoding="utf-8")

        for expected in (
            "process.env.BACKEND_URL",
            "/api/monitoring/health",
            "BACKEND_API_TOKEN",
            "vercel",
            "tunnel",
            "fastapi",
            "backgroundTasks",
            "Cache-Control",
        ):
            self.assertIn(expected, content)

        for forbidden in (
            "watchlistCount",
            "unreadCount",
            "recipientConfigured",
            "senderConfigured",
            "lastError",
        ):
            self.assertNotIn(forbidden, content)

    def test_probe_requires_two_consecutive_failures(self):
        self.assertTrue(PROBE_SCRIPT.is_file())
        probe = load_probe_module()
        calls = []
        sleeps = []
        output = []

        def always_failed(url, timeout_seconds):
            calls.append((url, timeout_seconds))
            return probe.ProbeResult(False, "health_not_healthy")

        exit_code = probe.run_probe(
            "https://example.invalid/api/health",
            attempts=2,
            delay_seconds=60,
            timeout_seconds=15,
            probe_func=always_failed,
            sleep_func=sleeps.append,
            output_func=output.append,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [60])
        self.assertEqual(len(output), 2)
        self.assertNotIn("example.invalid", "\n".join(output))

    def test_probe_recovers_without_alert_after_first_failure(self):
        self.assertTrue(PROBE_SCRIPT.is_file())
        probe = load_probe_module()
        results = iter(
            (
                probe.ProbeResult(False, "network_unavailable"),
                probe.ProbeResult(True, "healthy"),
            )
        )
        sleeps = []

        exit_code = probe.run_probe(
            "https://example.invalid/api/health",
            attempts=2,
            delay_seconds=60,
            timeout_seconds=15,
            probe_func=lambda _url, _timeout: next(results),
            sleep_func=sleeps.append,
            output_func=lambda _message: None,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(sleeps, [60])

    def test_probe_accepts_only_healthy_minimal_contract(self):
        self.assertTrue(PROBE_SCRIPT.is_file())
        probe = load_probe_module()
        healthy = {
            "status": "healthy",
            "components": {
                "vercel": "healthy",
                "tunnel": "healthy",
                "fastapi": "healthy",
                "backgroundTasks": "healthy",
            },
            "checkedAt": "2026-07-18T09:00:00Z",
        }
        degraded = {
            **healthy,
            "components": {**healthy["components"], "backgroundTasks": "degraded"},
        }

        self.assertEqual(probe.evaluate_payload(healthy), probe.ProbeResult(True, "healthy"))
        self.assertEqual(
            probe.evaluate_payload(degraded),
            probe.ProbeResult(False, "component_not_healthy"),
        )
        self.assertEqual(
            probe.evaluate_payload({"status": "healthy"}),
            probe.ProbeResult(False, "invalid_contract"),
        )

    def test_workflow_runs_outside_mac_and_deduplicates_failure_issue(self):
        self.assertTrue(WORKFLOW.is_file())
        content = WORKFLOW.read_text(encoding="utf-8")

        for expected in (
            "schedule:",
            "2-57/5 * * * *",
            "workflow_dispatch:",
            "issues: write",
            "--attempts 2",
            "--delay 60",
            "stock-monitor-murex-one.vercel.app/api/health",
            "gh issue create",
            "gh issue edit",
            "gh issue close",
        ):
            self.assertIn(expected, content)

        self.assertNotIn("BACKEND_API_TOKEN", content)
        self.assertNotIn("RADAR_LLM_API_KEY", content)
        self.assertNotIn("NEXT_PUBLIC_", content)


if __name__ == "__main__":
    unittest.main()
