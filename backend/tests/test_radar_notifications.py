import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import alert_repository
import database
import notification_service
import alerts_api
from radar.migrations import STAGE8_RADAR_MIGRATIONS, apply_pending_migrations
from radar.notifications import RadarStateChange, process_radar_state_change


UTC = timezone.utc
NOW = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)


class RadarNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = f"{self.temp_dir.name}/notifications.db"
        self.db_patcher = patch.object(database, "DB_PATH", self.path)
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()
        connection = sqlite3.connect(self.path)
        apply_pending_migrations(
            connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
            clock=lambda: NOW,
        )
        connection.close()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def change(self, **overrides):
        values = {
            "scope_type": "leader",
            "scope_id": "000725",
            "scope_name": "京东方A",
            "radar_run_id": "formal-run-1",
            "as_of": NOW,
            "from_state": "preliminary",
            "to_state": "candidate",
            "action": "upgrade",
            "rule_version": "radar-leader-rule-v1",
            "formal_usable": True,
            "shadow_mode": False,
            "first_rejection_reason": None,
        }
        values.update(overrides)
        return RadarStateChange(**values)

    def test_leader_sector_and_etf_transitions_map_to_expected_priorities(self):
        cases = (
            (self.change(from_state="out", to_state="preliminary", action="enter"), "P3", "radar_leader_preliminary"),
            (self.change(to_state="candidate"), "P2", "radar_leader_candidate"),
            (self.change(to_state="confirmed"), "P2", "radar_leader_confirmed"),
            (self.change(scope_type="sector", scope_id="73", scope_name="研究和试验发展", from_state="startup", to_state="confirmed"), "P3", "radar_sector_confirmed"),
            (self.change(scope_type="etf", scope_id="510300", scope_name="沪深300ETF", from_state="out", to_state="candidate", action="enter"), "P3", "radar_etf_candidate"),
        )

        for change, priority, event_type in cases:
            with self.subTest(event_type=event_type):
                result = process_radar_state_change(change)
                self.assertEqual(result.status, "created")
                self.assertEqual(result.alert["priority"], priority)
                self.assertEqual(result.alert["eventType"], event_type)

    def test_shadow_non_formal_and_unchanged_states_never_create_alerts(self):
        cases = (
            self.change(shadow_mode=True),
            self.change(formal_usable=False),
            self.change(from_state="candidate", to_state="candidate", action="hold"),
        )

        for change in cases:
            with self.subTest(change=change):
                result = process_radar_state_change(change)
                self.assertEqual(result.status, "suppressed")

        self.assertEqual(alert_repository.list_alerts(), [])

    def test_same_formal_transition_is_deduplicated_before_delivery(self):
        change = self.change()

        first = process_radar_state_change(change)
        second = process_radar_state_change(change)

        self.assertEqual(first.status, "created")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(len(alert_repository.list_alerts()), 1)
        self.assertEqual(
            len(alert_repository.list_deliveries(first.alert["id"])),
            1,
        )

    def test_radar_email_disabled_keeps_site_delivery_and_never_calls_old_ai(self):
        alert_repository.save_radar_notification_preferences(
            site_enabled=True,
            email_enabled=False,
            p2_email=True,
            p3_email=False,
        )
        with patch.object(notification_service, "send_alert_email") as send_email, patch.object(
            notification_service,
            "trigger_event_ai_analysis",
        ) as old_ai:
            result = process_radar_state_change(self.change())

        deliveries = alert_repository.list_deliveries(result.alert["id"])
        self.assertEqual([item["channel"] for item in deliveries], ["site"])
        send_email.assert_not_called()
        old_ai.assert_not_called()

    def test_radar_p3_email_requires_explicit_preference(self):
        alert_repository.save_radar_notification_preferences(
            site_enabled=True,
            email_enabled=True,
            p2_email=True,
            p3_email=True,
        )
        with patch.object(
            notification_service,
            "send_alert_email",
            return_value={"status": "sent", "error": None},
        ):
            result = process_radar_state_change(
                self.change(from_state="out", to_state="preliminary", action="enter")
            )

        deliveries = {
            item["channel"]: item
            for item in alert_repository.list_deliveries(result.alert["id"])
        }
        self.assertEqual(deliveries["site"]["status"], "sent")
        self.assertEqual(deliveries["email"]["status"], "sent")

    def test_radar_alert_is_visible_without_adding_scope_to_watchlist(self):
        result = process_radar_state_change(
            self.change(
                scope_type="sector",
                scope_id="73",
                scope_name="研究和试验发展",
                from_state="startup",
                to_state="confirmed",
            )
        )

        visible = alerts_api._watchlist_alerts(today_only=True)

        self.assertEqual(result.status, "created")
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["source"], "mainline_radar")
        self.assertEqual(visible[0]["symbol"], "73")


if __name__ == "__main__":
    unittest.main()
