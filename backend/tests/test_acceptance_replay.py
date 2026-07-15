import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alert_repository
import database

try:
    import acceptance_replay
except ImportError:
    acceptance_replay = None


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_alert_cases.jsonl"


class AcceptanceReplayTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(acceptance_replay, "acceptance_replay 模块尚未实现")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(
            database,
            "DB_PATH",
            f"{self.temp_dir.name}/acceptance.db",
        )
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_fixed_fixtures_replay_without_miss_false_positive_or_duplicate(self):
        report = acceptance_replay.replay_cases(
            acceptance_replay.load_cases(FIXTURE_PATH),
            save_event=alert_repository.save_alert_event,
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["scope"], "deterministic_fixture")
        self.assertEqual(report["totalCases"], 6)
        self.assertEqual(report["passedCases"], 6)
        self.assertEqual(report["missedAlerts"], 0)
        self.assertEqual(report["falsePositiveAlerts"], 0)
        self.assertEqual(report["duplicateAlerts"], 0)
        self.assertEqual(report["suppressedDuplicates"], 1)
        self.assertEqual(report["futureEvidenceBlocked"], 1)
        self.assertEqual(report["staleSourceItemsRejected"], 1)
        self.assertEqual(report["historicalVerifiedCases"], 0)
        self.assertEqual(report["historicalMetrics"]["status"], "not_measured")

    def test_wrong_expected_event_fails_instead_of_being_counted_as_covered(self):
        case = {
            "id": "wrong-expectation",
            "scope": "fixed_fixture",
            "kind": "official_event",
            "as_of": "2026-07-15T10:00:00+08:00",
            "observations": [{
                "available_at": "2026-07-15T09:59:00+08:00",
                "id": "notice-wrong-expectation",
                "symbol": "000021",
                "stock_name": "深科技",
                "title": "股票交易异常波动公告",
                "source": "巨潮公告",
                "published_at": "2026-07-15T09:55:00+08:00",
            }],
            "expected_alerts": [{
                "symbol": "000725",
                "event_type": "risk_warning",
                "direction": "negative",
                "priority": "P2",
            }],
        }

        report = acceptance_replay.replay_cases(
            [case],
            save_event=alert_repository.save_alert_event,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["missedAlerts"], 1)
        self.assertEqual(report["falsePositiveAlerts"], 1)

    def test_report_does_not_present_fixed_fixtures_as_live_history_metrics(self):
        report = acceptance_replay.replay_cases(
            acceptance_replay.load_cases(FIXTURE_PATH),
            save_event=alert_repository.save_alert_event,
        )

        markdown = acceptance_replay.render_markdown(report)

        self.assertIn("固定 Fixture：通过", markdown)
        self.assertIn("真实历史样本：暂无判断", markdown)
        self.assertIn("没有独立核验过的真实历史事件集", markdown)
        self.assertNotIn("真实历史覆盖率：100%", markdown)


if __name__ == "__main__":
    unittest.main()
