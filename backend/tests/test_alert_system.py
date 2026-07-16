import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import database
import main
import market_calendar
import news_collector
from fastapi.testclient import TestClient


def optional_import(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


event_classifier = optional_import("event_classifier")
alert_repository = optional_import("alert_repository")
notification_service = optional_import("notification_service")
alerts_api = optional_import("alerts_api")
monitoring_health = optional_import("monitoring_health")


class EventClassifierTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(event_classifier, "event_classifier 模块尚未实现")

    def test_classifies_official_negative_positive_and_neutral_events(self):
        cases = (
            ("股票交易异常波动及风险提示公告", "negative", "P2", "risk_warning"),
            ("2026年半年度业绩预增公告", "positive", "P2", "earnings_growth"),
            ("关于实施股份回购的公告", "positive", "P2", "share_buyback"),
            ("董事会换届选举公告", "neutral", "P2", "governance_change"),
        )

        for title, direction, priority, event_type in cases:
            with self.subTest(title=title):
                result = event_classifier.classify_official_event({
                    "id": title,
                    "symbol": "000725",
                    "title": title,
                    "source": "深交所公告",
                    "url": "https://example.com/notice",
                    "ctime": 1_700_000_000,
                    "content": "",
                })

                self.assertEqual(result["direction"], direction)
                self.assertEqual(result["priority"], priority)
                self.assertEqual(result["event_type"], event_type)
                self.assertEqual(result["evidence_level"], "S")

    def test_official_alert_keeps_company_name_and_source_summary(self):
        result = event_classifier.classify_official_event({
            "id": "notice-000519",
            "symbol": "000519",
            "stock_name": "中兵红箭",
            "title": "2026年半年度业绩预告",
            "source": "深交所公告",
            "url": "https://example.com/000519-notice",
            "ctime": datetime.now(market_calendar.SHANGHAI_TZ).timestamp(),
            "content": "中兵红箭预计上半年归母净利润3000万元至4500万元，同比扭亏为盈。",
        })

        self.assertEqual(result["stock_name"], "中兵红箭")
        self.assertEqual(result["title"], "中兵红箭：2026年半年度业绩预告")
        self.assertIn("归母净利润3000万元至4500万元", result["summary"])
        self.assertIn("影响判断", result["summary"])

    def test_non_official_source_cannot_trigger_high_priority_alert(self):
        result = event_classifier.classify_official_event({
            "id": "media-1",
            "symbol": "000725",
            "title": "网传公司获得重大订单",
            "source": "新浪财经",
            "url": "https://example.com/media",
            "ctime": 1_700_000_000,
            "content": "",
        })

        self.assertIsNone(result)

    def test_unmatched_official_announcement_is_neutral_p3(self):
        result = event_classifier.classify_official_event({
            "id": "official-other",
            "symbol": "000725",
            "title": "关于召开临时股东大会的公告",
            "source": "巨潮公告",
            "url": "https://example.com/other",
            "ctime": 1_700_000_000,
            "content": "",
        })

        self.assertEqual(result["direction"], "neutral")
        self.assertEqual(result["priority"], "P3")
        self.assertEqual(result["event_type"], "official_announcement")


class AlertRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(alert_repository, "alert_repository 模块尚未实现")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(
            database,
            "DB_PATH",
            f"{self.temp_dir.name}/alerts.db",
        )
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _event():
        return {
            "symbol": "000725",
            "stock_name": "京东方A",
            "event_type": "earnings_growth",
            "direction": "positive",
            "priority": "P2",
            "evidence_level": "S",
            "title": "半年度业绩预增公告",
            "summary": "官方披露业绩预增，具体影响仍需结合原文判断。",
            "source": "深交所公告",
            "source_url": "https://example.com/notice",
            "source_event_id": "notice-1",
            "published_at": datetime.now(
                market_calendar.SHANGHAI_TZ
            ).isoformat(timespec="seconds"),
        }

    def test_alert_event_is_deduplicated_and_can_be_marked_read(self):
        first, first_created = alert_repository.save_alert_event(self._event())
        second, second_created = alert_repository.save_alert_event(self._event())

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(alert_repository.get_unread_count(), 1)

        self.assertTrue(alert_repository.mark_alert_read(first["id"]))
        self.assertEqual(alert_repository.get_unread_count(), 0)

    def test_same_official_event_upgrades_without_creating_duplicate(self):
        neutral_event = {
            **self._event(),
            "event_type": "official_announcement",
            "direction": "neutral",
            "priority": "P3",
        }
        upgraded_event = {
            **self._event(),
            "event_type": "earnings_growth",
            "direction": "positive",
            "priority": "P2",
        }

        first, first_changed = alert_repository.save_alert_event(neutral_event)
        upgraded, upgraded_changed = alert_repository.save_alert_event(upgraded_event)

        self.assertTrue(first_changed)
        self.assertTrue(upgraded_changed)
        self.assertEqual(first["id"], upgraded["id"])
        self.assertEqual(upgraded["priority"], "P2")
        self.assertEqual(len(alert_repository.list_alerts()), 1)

    def test_delivery_state_is_persisted_truthfully(self):
        alert, _created = alert_repository.save_alert_event(self._event())

        alert_repository.record_delivery(
            alert["id"],
            channel="email",
            status="failed",
            error="SMTP unavailable",
            next_retry_at="2026-07-13T10:01:00+08:00",
        )
        delivery = alert_repository.list_deliveries(alert["id"])[0]

        self.assertEqual(delivery["status"], "failed")
        self.assertEqual(delivery["attemptCount"], 1)
        self.assertEqual(delivery["error"], "SMTP unavailable")
        self.assertIsNotNone(delivery["nextRetryAt"])

    def test_global_recipient_email_can_be_replaced(self):
        first = alert_repository.save_global_email_settings("first@example.com")
        second = alert_repository.save_global_email_settings("second@example.com")

        self.assertEqual(first["recipientEmail"], "first@example.com")
        self.assertEqual(second["recipientEmail"], "second@example.com")
        self.assertEqual(
            alert_repository.get_global_email_settings()["recipientEmail"],
            "second@example.com",
        )


class NotificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(alert_repository, "alert_repository 模块尚未实现")
        self.assertIsNotNone(notification_service, "notification_service 模块尚未实现")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(
            database,
            "DB_PATH",
            f"{self.temp_dir.name}/notifications.db",
        )
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()
        self.alert, _created = alert_repository.save_alert_event(
            AlertRepositoryTests._event()
        )
        pending = getattr(notification_service, "_EVENT_AI_PENDING", None)
        if pending is not None:
            pending.clear()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    @patch.dict(os.environ, {}, clear=True)
    def test_unconfigured_email_is_recorded_without_fake_success(self):
        notification_service.deliver_alert(self.alert)
        deliveries = {
            item["channel"]: item
            for item in alert_repository.list_deliveries(self.alert["id"])
        }

        self.assertEqual(deliveries["site"]["status"], "sent")
        self.assertEqual(deliveries["email"]["status"], "not_configured")
        self.assertEqual(deliveries["email"]["attemptCount"], 0)

    def test_email_failure_keeps_site_alert_and_schedules_retry(self):
        with patch.object(
            notification_service,
            "send_alert_email",
            return_value={"status": "failed", "error": "SMTP unavailable"},
        ):
            notification_service.deliver_alert(self.alert)

        deliveries = {
            item["channel"]: item
            for item in alert_repository.list_deliveries(self.alert["id"])
        }
        self.assertEqual(deliveries["site"]["status"], "sent")
        self.assertEqual(deliveries["email"]["status"], "failed")
        self.assertEqual(deliveries["email"]["attemptCount"], 1)
        self.assertIsNotNone(deliveries["email"]["nextRetryAt"])

    def test_final_email_retry_failure_creates_system_health_reminder(self):
        for _ in range(3):
            alert_repository.record_delivery(
                self.alert["id"],
                channel="email",
                status="failed",
                error="SMTP unavailable",
                next_retry_at="2000-01-01T00:00:00+08:00",
            )

        with patch.object(
            notification_service,
            "send_alert_email",
            return_value={"status": "failed", "error": "SMTP unavailable"},
        ), patch.object(
            monitoring_health,
            "record_email_final_failure",
            create=True,
        ) as record_final_failure:
            notification_service.retry_due_email_deliveries()

        delivery = next(
            item
            for item in alert_repository.list_deliveries(self.alert["id"])
            if item["channel"] == "email"
        )
        self.assertEqual(delivery["attemptCount"], 4)
        self.assertIsNone(delivery["nextRetryAt"])
        record_final_failure.assert_called_once_with(
            self.alert,
            "SMTP unavailable",
        )

    @patch.dict(
        os.environ,
        {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "sender@example.com"},
        clear=True,
    )
    def test_saved_global_recipient_is_used_by_email_config(self):
        alert_repository.save_global_email_settings("owner@example.com")

        config = notification_service.get_email_config()

        self.assertIsNotNone(config)
        self.assertEqual(config["recipient"], "owner@example.com")

    def test_unmonitored_official_news_is_ignored(self):
        monitored_item = {
            "id": "monitored-notice",
            "symbol": "000725",
            "title": "2026年半年度业绩预增公告",
            "source": "深交所公告",
            "url": "https://static.cninfo.com.cn/monitored.pdf",
            "ctime": datetime.now(market_calendar.SHANGHAI_TZ).timestamp(),
            "content": "",
        }
        unmonitored_item = {
            **monitored_item,
            "id": "unmonitored-notice",
            "symbol": "688233",
            "url": "https://example.com/unmonitored",
        }
        with patch.object(
            notification_service.database,
            "get_watchlist",
            return_value=[{"stockCode": "000725", "stockName": "京东方A"}],
        ), patch.object(
            notification_service,
            "trigger_event_ai_analysis",
            return_value="started",
        ):
            created = notification_service.process_official_news(
                [unmonitored_item, monitored_item]
            )

        alerts = alert_repository.list_alerts()
        self.assertEqual(created, 1)
        self.assertNotIn("688233", {item["symbol"] for item in alerts})
        created_alert = next(
            item for item in alerts if item["sourceEventId"] == "monitored-notice"
        )
        self.assertEqual(created_alert["symbol"], "000725")
        self.assertEqual(created_alert["stockName"], "京东方A")

    def test_p3_alert_does_not_start_event_ai_analysis(self):
        trigger = getattr(
            notification_service,
            "trigger_event_ai_analysis",
            lambda _alert: None,
        )

        result = trigger({**self.alert, "priority": "P3"})

        self.assertEqual(result, "skipped_priority")

    def test_p2_alert_starts_one_background_analysis_per_event(self):
        executor = getattr(notification_service, "_EVENT_AI_EXECUTOR", None)
        self.assertIsNotNone(executor, "事件 AI 有界执行器尚未实现")

        with patch.object(executor, "submit") as mock_submit:
            first = notification_service.trigger_event_ai_analysis(self.alert)
            second = notification_service.trigger_event_ai_analysis(self.alert)

        self.assertEqual(first, "started")
        self.assertEqual(second, "skipped_duplicate")
        mock_submit.assert_called_once()

    def test_event_ai_queue_is_bounded_without_blocking_alert_delivery(self):
        max_pending = getattr(notification_service, "EVENT_AI_MAX_PENDING", None)
        executor = getattr(notification_service, "_EVENT_AI_EXECUTOR", None)
        self.assertEqual(max_pending, 8)
        self.assertIsNotNone(executor)
        pending = getattr(notification_service, "_EVENT_AI_PENDING", set())
        pending.update({f"event:pending-{index}" for index in range(max_pending)})

        with patch.object(executor, "submit") as mock_submit:
            result = notification_service.trigger_event_ai_analysis(self.alert)

        self.assertEqual(result, "skipped_capacity")
        mock_submit.assert_not_called()

    def test_persisted_event_analysis_prevents_duplicate_after_restart(self):
        trigger = f"event:{self.alert['id']}"
        database.save_analysis_history(
            self.alert["symbol"],
            trigger,
            "事件已分析",
            {"stockCode": self.alert["symbol"], "credibility": "高"},
        )
        pending = getattr(notification_service, "_EVENT_AI_PENDING", None)
        if pending is not None:
            pending.clear()

        trigger_event = getattr(
            notification_service,
            "trigger_event_ai_analysis",
            lambda _alert: None,
        )
        result = trigger_event(self.alert)

        self.assertEqual(result, "skipped_duplicate")

    def test_new_official_p2_alert_is_delivered_and_triggers_ai(self):
        item = {
            "id": "new-p2-notice",
            "symbol": "000725",
            "title": "2026年半年度业绩预增公告",
            "source": "深交所公告",
            "url": "https://static.cninfo.com.cn/new-p2.pdf",
            "ctime": datetime.now(market_calendar.SHANGHAI_TZ).timestamp(),
            "content": "",
        }
        with patch.object(
            notification_service.database,
            "get_watchlist",
            return_value=[{"stockCode": "000725", "stockName": "京东方A"}],
        ), patch.object(
            notification_service,
            "deliver_alert",
        ) as mock_deliver, patch.object(
            notification_service,
            "trigger_event_ai_analysis",
            create=True,
        ) as mock_trigger:
            created = notification_service.process_official_news([item])

        self.assertEqual(created, 1)
        mock_deliver.assert_called_once()
        mock_trigger.assert_called_once_with(mock_deliver.call_args.args[0])

    def test_reposted_link_cannot_be_promoted_to_s_grade_alert(self):
        item = {
            "id": "reposted-notice",
            "symbol": "000725",
            "title": "2026年半年度业绩预增公告",
            "source": "深交所公告",
            "url": "https://finance.example.com/reposted-notice.html",
            "ctime": datetime.now(market_calendar.SHANGHAI_TZ).timestamp(),
            "content": "",
        }
        with patch.object(
            notification_service.database,
            "get_watchlist",
            return_value=[{"stockCode": "000725", "stockName": "京东方A"}],
        ):
            created = notification_service.process_official_news([item])

        self.assertEqual(created, 0)


class AlertsApiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(alert_repository, "alert_repository 模块尚未实现")
        self.assertIsNotNone(alerts_api, "alerts_api 模块尚未实现")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(
            database,
            "DB_PATH",
            f"{self.temp_dir.name}/api-alerts.db",
        )
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()
        database.replace_watchlist([{
            "stockCode": "000725",
            "stockName": "京东方A",
            "addedAt": "2026-01-01T00:00:00",
        }])
        self.alert, _created = alert_repository.save_alert_event(
            AlertRepositoryTests._event()
        )
        self.client = TestClient(main.app)

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_alert_list_unread_count_and_read_endpoint(self):
        auth_headers = {"X-Backend-Token": "test-token"}
        list_response = self.client.get("/api/alerts", headers=auth_headers)
        count_response = self.client.get("/api/alerts/unread-count", headers=auth_headers)
        denied_response = self.client.patch(
            f"/api/alerts/{self.alert['id']}/read"
        )
        read_response = self.client.patch(
            f"/api/alerts/{self.alert['id']}/read",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["data"]), 1)
        self.assertEqual(count_response.json()["count"], 1)
        self.assertEqual(denied_response.status_code, 401)
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(alert_repository.get_unread_count(), 0)

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_today_alert_center_includes_system_health_events(self):
        system_alert, _created = alert_repository.save_alert_event({
            "symbol": "SYSTEM",
            "stock_name": "系统监测",
            "event_type": "system_health",
            "direction": "negative",
            "priority": "P2",
            "evidence_level": "A",
            "title": "数据源连续失败",
            "summary": "官方公告采集连续两个周期失败。",
            "source": "系统健康审计",
            "source_url": None,
            "source_event_id": "health:official:1",
            "published_at": datetime.now(
                market_calendar.SHANGHAI_TZ
            ).isoformat(timespec="seconds"),
        })

        response = self.client.get(
            "/api/alerts",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            system_alert["id"],
            {item["id"] for item in response.json()["data"]},
        )

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    @patch.object(monitoring_health, "audit_watchlist_sync")
    def test_watchlist_sync_health_report_requires_token_and_audits(self, audit_sync):
        audit_sync.return_value = False
        payload = {"items": [{"stockCode": "000519", "stockName": "中兵红箭"}]}

        denied = self.client.post(
            "/api/monitoring/health/watchlist-sync",
            json=payload,
        )
        response = self.client.post(
            "/api/monitoring/health/watchlist-sync",
            json=payload,
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "mismatched")
        audit_sync.assert_called_once()

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_alert_preferences_write_requires_token(self):
        payload = {
            "symbol": "000725",
            "enabled": True,
            "emailEnabled": False,
            "p2Email": False,
        }

        denied_response = self.client.put("/api/alerts/preferences", json=payload)
        response = self.client.put(
            "/api/alerts/preferences",
            json=payload,
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(denied_response.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["emailEnabled"])

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_sensitive_alert_reads_require_backend_token(self):
        paths = (
            "/api/alerts",
            "/api/alerts/unread-count",
            "/api/alerts/preferences?symbol=000725",
            "/api/alerts/email-settings",
            "/api/monitoring/health",
            "/api/stock/risk/000725",
        )
        for path in paths:
            with self.subTest(path=path):
                denied = self.client.get(path)
                allowed = self.client.get(
                    path,
                    headers={"X-Backend-Token": "test-token"},
                )
                self.assertEqual(denied.status_code, 401)
                self.assertEqual(allowed.status_code, 200)

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_global_email_settings_validate_and_replace_recipient(self):
        denied_response = self.client.put(
            "/api/alerts/email-settings",
            json={"recipientEmail": "owner@example.com"},
        )
        invalid_response = self.client.put(
            "/api/alerts/email-settings",
            json={"recipientEmail": "not-an-email"},
            headers={"X-Backend-Token": "test-token"},
        )
        saved_response = self.client.put(
            "/api/alerts/email-settings",
            json={"recipientEmail": "owner@example.com"},
            headers={"X-Backend-Token": "test-token"},
        )
        replaced_response = self.client.put(
            "/api/alerts/email-settings",
            json={"recipientEmail": "new-owner@example.com"},
            headers={"X-Backend-Token": "test-token"},
        )
        get_response = self.client.get(
            "/api/alerts/email-settings",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(denied_response.status_code, 401)
        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(saved_response.status_code, 200)
        self.assertEqual(
            replaced_response.json()["data"]["recipientEmail"],
            "new-owner@example.com",
        )
        self.assertEqual(
            get_response.json()["data"]["recipientEmail"],
            "new-owner@example.com",
        )

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_email_test_endpoint_reports_real_send_result(self):
        alert_repository.save_global_email_settings("owner@example.com")
        with patch.object(
            notification_service,
            "send_test_email",
            return_value={"status": "sent", "error": None},
        ) as send_test:
            response = self.client.post(
                "/api/alerts/email-settings/test",
                headers={"X-Backend-Token": "test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "sent")
        send_test.assert_called_once_with("owner@example.com")

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=True)
    def test_email_test_endpoint_does_not_fake_success_without_sender(self):
        alert_repository.save_global_email_settings("owner@example.com")

        response = self.client.post(
            "/api/alerts/email-settings/test",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_configured")

    @patch.dict(os.environ, {}, clear=True)
    def test_monitoring_health_reports_real_runtime_state(self):
        self.assertIsNotNone(monitoring_health, "monitoring_health 模块尚未实现")
        monitoring_health.reset_runtime_health()
        monitoring_health.record_task_success("officialAnnouncements", item_count=2)

        response = self.client.get("/api/monitoring/health")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tasks"]["officialAnnouncements"]["status"], "healthy")
        self.assertEqual(data["tasks"]["officialAnnouncements"]["itemCount"], 2)
        self.assertEqual(data["tasks"]["generalNews"]["status"], "not_run")
        self.assertEqual(data["tasks"]["industryDynamics"]["status"], "not_run")
        self.assertEqual(data["tasks"]["aiAnalysis"]["status"], "not_run")
        self.assertEqual(data["email"]["status"], "not_configured")
        self.assertEqual(data["watchlistCount"], 1)

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    def test_stock_risk_endpoint_returns_truthful_missing_state(self):
        response = self.client.get(
            "/api/stock/risk/000725",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["data"])
        self.assertEqual(response.json()["status"], "unavailable")

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_alert_center_only_returns_events_published_today(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        mock_watchlist.return_value = [{
            "stockCode": "000725",
            "stockName": "京东方A",
        }]
        mock_list_alerts.return_value = [
            {
                "id": "today",
                "symbol": "000725",
                "publishedAt": now.isoformat(timespec="seconds"),
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
            {
                "id": "yesterday",
                "symbol": "000725",
                "publishedAt": (now - timedelta(days=1)).isoformat(timespec="seconds"),
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
        ]

        result = alerts_api._watchlist_alerts()

        self.assertEqual([item["id"] for item in result], ["today"])

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_market_risk_alerts_dedupe_same_day_signal_before_unread_filter(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        common = {
            "symbol": "000725",
            "stockName": "京东方A",
            "eventType": "market_risk",
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "publishedAt": now.isoformat(timespec="seconds"),
            "triggeredAt": now.isoformat(timespec="seconds"),
        }
        mock_watchlist.return_value = [{
            "stockCode": "000725",
            "stockName": "京东方A",
        }]
        mock_list_alerts.return_value = [
            {
                **common,
                "id": "latest-negative",
                "sourceEventId": "risk:2026-07-14:negative:high_volume_ratio",
                "isRead": True,
            },
            {
                **common,
                "id": "earlier-positive",
                "sourceEventId": "risk:2026-07-14:positive:high_volume_ratio",
                "isRead": False,
            },
        ]

        visible = alerts_api._watchlist_alerts()
        unread = alerts_api._watchlist_alerts(unread_only=True)

        self.assertEqual([item["id"] for item in visible], ["latest-negative"])
        self.assertEqual(unread, [])

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_legacy_market_risk_alert_exposes_rule_without_inventing_values(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        mock_watchlist.return_value = [{
            "stockCode": "000725",
            "stockName": "京东方A",
        }]
        mock_list_alerts.return_value = [{
            "id": "legacy-risk",
            "symbol": "000725",
            "stockName": "京东方A",
            "eventType": "market_risk",
            "direction": "negative",
            "priority": "P3",
            "title": "京东方A量价风险升高",
            "summary": "固定规则检测到：量比不低于2。",
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "sourceEventId": "risk:2026-07-14:negative:high_volume_ratio",
            "publishedAt": now.isoformat(timespec="seconds"),
            "triggeredAt": now.isoformat(timespec="seconds"),
            "isRead": False,
        }]

        result = alerts_api._watchlist_alerts()

        self.assertEqual(
            result[0]["title"],
            "京东方A触发P3观察提醒：量比≥2",
        )
        self.assertIn("旧提醒未保存触发瞬间的具体数值", result[0]["summary"])

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_episode_id_never_overwrites_human_readable_alert_copy(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        stored_title = "京东方Ａ触发P3观察提醒：量比≥2"
        expected_title = "京东方A触发P3观察提醒：量比≥2"
        original_summary = (
            f"{now:%Y-%m-%d} 09:31:54 腾讯财经行情显示：量比 2.20。"
            "固定规则触发：量比≥2。"
        )
        mock_watchlist.return_value = [{
            "stockCode": "000725",
            "stockName": "京东方A",
        }]
        mock_list_alerts.return_value = [{
            "id": "episode-risk",
            "symbol": "000725",
            "stockName": "京东方Ａ",
            "eventType": "market_risk",
            "direction": "negative",
            "priority": "P3",
            "title": stored_title,
            "summary": original_summary,
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "sourceEventId": f"risk:{now:%Y-%m-%d}:negative:episode:093154",
            "publishedAt": now.isoformat(timespec="seconds"),
            "triggeredAt": now.isoformat(timespec="seconds"),
            "isRead": False,
        }]

        result = alerts_api._watchlist_alerts()

        self.assertEqual(result[0]["title"], expected_title)
        self.assertEqual(result[0]["summary"], original_summary)
        self.assertNotIn("episode", result[0]["title"] + result[0]["summary"])
        self.assertNotIn("093154", result[0]["title"] + result[0]["summary"])

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_episode_alerts_in_same_snapshot_bucket_are_collapsed(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        day = f"{now:%Y-%m-%d}"
        common = {
            "symbol": "000725",
            "stockName": "京东方A",
            "direction": "negative",
            "priority": "P3",
            "title": "京东方A触发P3观察提醒：量比≥2",
            "summary": "腾讯财经行情显示：量比 2.20。",
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "isRead": False,
        }
        mock_watchlist.return_value = [{
            "stockCode": "000725",
            "stockName": "京东方A",
        }]
        mock_list_alerts.return_value = [
            {
                **common,
                "id": "latest",
                "eventType": "market_risk",
                "sourceEventId": f"risk:{day}:negative:episode:093154",
                "publishedAt": f"{day} 09:31:54",
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
            {
                **common,
                "id": "earlier",
                "eventType": "market_risk",
                "sourceEventId": f"risk:{day}:negative:episode:093054",
                "publishedAt": f"{day} 09:30:54",
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
            {
                **common,
                "id": "latest-linkage",
                "eventType": "linkage_risk",
                "direction": "positive",
                "title": "京东方A触发P3观察提醒：板块资金净流入前5",
                "sourceEventId": f"linkage:{day}:positive:episode:093154",
                "publishedAt": f"{day} 09:31:54",
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
            {
                **common,
                "id": "earlier-linkage",
                "eventType": "linkage_risk",
                "direction": "positive",
                "title": "京东方A触发P3观察提醒：板块资金净流入前5",
                "sourceEventId": f"linkage:{day}:positive:episode:093054",
                "publishedAt": f"{day} 09:30:54",
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
        ]

        result = alerts_api._watchlist_alerts()

        self.assertEqual(
            [item["id"] for item in result],
            ["latest", "latest-linkage"],
        )

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_legacy_same_rule_episode_alerts_are_collapsed_across_the_day(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        day = f"{now:%Y-%m-%d}"
        common = {
            "symbol": "000021",
            "stockName": "深科技",
            "eventType": "market_risk",
            "direction": "negative",
            "priority": "P3",
            "title": "深科技触发P3观察提醒：连续资金净流出",
            "summary": "连续3个交易日主力资金净流出。",
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "triggeredAt": now.isoformat(timespec="seconds"),
            "isRead": False,
        }
        mock_watchlist.return_value = [{
            "stockCode": "000021",
            "stockName": "深科技",
        }]
        mock_list_alerts.return_value = [
            {
                **common,
                "id": "latest",
                "sourceEventId": f"risk:{day}:negative:episode:105157",
                "publishedAt": f"{day} 10:51:57",
            },
            {
                **common,
                "id": "earlier",
                "sourceEventId": f"risk:{day}:negative:episode:101400",
                "publishedAt": f"{day} 10:14:00",
            },
        ]

        result = alerts_api._watchlist_alerts()

        self.assertEqual([item["id"] for item in result], ["latest"])

    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_new_episode_ids_keep_confirmed_same_day_reentry_visible(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        day = f"{now:%Y-%m-%d}"
        common = {
            "symbol": "000021",
            "stockName": "深科技",
            "eventType": "market_risk",
            "direction": "negative",
            "priority": "P3",
            "title": "深科技触发P3观察提醒：连续资金净流出",
            "summary": "连续3个交易日主力资金净流出。",
            "source": "腾讯财经行情规则",
            "sourceUrl": None,
            "triggeredAt": now.isoformat(timespec="seconds"),
            "isRead": False,
        }
        mock_watchlist.return_value = [{
            "stockCode": "000021",
            "stockName": "深科技",
        }]
        mock_list_alerts.return_value = [
            {
                **common,
                "id": "reentered",
                "sourceEventId": (
                    f"risk:{day}:negative:episode:105157:"
                    "consecutive_fund_outflow"
                ),
                "publishedAt": f"{day} 10:51:57",
            },
            {
                **common,
                "id": "first",
                "sourceEventId": (
                    f"risk:{day}:negative:episode:101400:"
                    "consecutive_fund_outflow"
                ),
                "publishedAt": f"{day} 10:14:00",
            },
        ]

        result = alerts_api._watchlist_alerts()

        self.assertEqual(
            [item["id"] for item in result],
            ["reentered", "first"],
        )

    @patch.dict(os.environ, {"BACKEND_API_TOKEN": "test-token"}, clear=False)
    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_alert_history_returns_saved_events_from_previous_days(
        self,
        mock_list_alerts,
        mock_watchlist,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ)
        mock_watchlist.return_value = []
        mock_list_alerts.return_value = [
            {
                "id": "today",
                "symbol": "000725",
                "stockName": "京东方A",
                "publishedAt": now.isoformat(timespec="seconds"),
                "triggeredAt": now.isoformat(timespec="seconds"),
            },
            {
                "id": "yesterday",
                "symbol": "000725",
                "stockName": "京东方A",
                "publishedAt": (now - timedelta(days=1)).isoformat(
                    timespec="seconds"
                ),
                "triggeredAt": (now - timedelta(days=1)).isoformat(
                    timespec="seconds"
                ),
            },
        ]

        response = self.client.get(
            "/api/alerts?scope=history",
            headers={"X-Backend-Token": "test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["data"]],
            ["today", "yesterday"],
        )

    @patch.object(alerts_api.database, "get_latest_crawled_news")
    @patch.object(alerts_api.database, "get_watchlist")
    @patch.object(alerts_api.alert_repository, "list_alerts")
    def test_alert_center_enriches_existing_alert_from_watchlist_and_source(
        self,
        mock_list_alerts,
        mock_watchlist,
        mock_get_news,
    ):
        now = datetime.now(alerts_api.market_calendar.SHANGHAI_TZ).replace(
            hour=9,
            minute=30,
            second=0,
            microsecond=0,
        )
        source_url = "https://example.com/000519-notice"
        mock_watchlist.return_value = [{
            "stockCode": "000519",
            "stockName": "中兵红箭",
        }]
        mock_list_alerts.return_value = [{
            "id": "existing-alert",
            "symbol": "000519",
            "stockName": "000519",
            "title": "2026年半年度业绩预告",
            "summary": "系统依据官方原文识别为正面事件。",
            "source": "深交所公告",
            "sourceUrl": source_url,
            "publishedAt": now.isoformat(timespec="seconds"),
            "triggeredAt": now.isoformat(timespec="seconds"),
        }]
        mock_get_news.return_value = [{
            "symbol": "000519",
            "title": "2026年半年度业绩预告",
            "url": source_url,
            "content": "中兵红箭预计上半年归母净利润3000万元至4500万元，同比扭亏为盈。",
            "ctime": now.timestamp(),
            "created_at": now.timestamp(),
            "source": "深交所公告",
        }]

        result = alerts_api._watchlist_alerts()

        self.assertEqual(result[0]["stockName"], "中兵红箭")
        self.assertEqual(result[0]["title"], "中兵红箭：2026年半年度业绩预告")
        self.assertIn("归母净利润3000万元至4500万元", result[0]["summary"])


class MonitoringHealthAlertTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(monitoring_health, "monitoring_health 模块尚未实现")
        monitoring_health.reset_runtime_health()

    @patch.object(monitoring_health, "_save_system_alert", create=True)
    def test_same_source_alerts_only_after_two_consecutive_failures(self, save_alert):
        monitoring_health.record_task_failure("officialAnnouncements", RuntimeError("first"))
        save_alert.assert_not_called()

        monitoring_health.record_task_failure("officialAnnouncements", RuntimeError("second"))

        save_alert.assert_called_once()
        event = save_alert.call_args.args[0]
        self.assertEqual(event["event_type"], "system_health")
        self.assertEqual(event["priority"], "P2")
        self.assertIn("连续两个周期失败", event["summary"])
        state = monitoring_health.get_task_states()["officialAnnouncements"]
        self.assertEqual(state["consecutiveFailures"], 2)

    @patch.object(monitoring_health, "_save_system_alert", create=True)
    def test_success_resets_the_consecutive_failure_counter(self, save_alert):
        monitoring_health.record_task_failure("generalNews", RuntimeError("first"))
        monitoring_health.record_task_success("generalNews", item_count=1)
        monitoring_health.record_task_failure("generalNews", RuntimeError("new episode"))

        save_alert.assert_not_called()
        self.assertEqual(
            monitoring_health.get_task_states()["generalNews"]["consecutiveFailures"],
            1,
        )

    @patch.object(monitoring_health, "_save_system_alert", create=True)
    @patch.object(monitoring_health, "alert_repository", create=True)
    def test_trading_stock_stale_update_creates_reminder(
        self,
        repository,
        save_alert,
    ):
        now = datetime(2026, 7, 15, 10, 30, tzinfo=market_calendar.SHANGHAI_TZ)
        repository.get_latest_signal_state.return_value = {
            "sourceTime": "2026-07-15 10:25:00",
        }

        count = monitoring_health.audit_stale_watchlist(
            [{"stockCode": "000725", "stockName": "京东方A"}],
            trading_symbols={"000725"},
            expected_seconds=180,
            now=now,
        )

        self.assertEqual(count, 1)
        self.assertEqual(save_alert.call_count, 1)
        self.assertIn("超过预期时间未更新", save_alert.call_args.args[0]["summary"])

    @patch.object(monitoring_health, "_save_system_alert", create=True)
    def test_frontend_backend_watchlists_mismatch_creates_reminder(self, save_alert):
        matched = monitoring_health.audit_watchlist_sync(
            [{"stockCode": "000725", "stockName": "京东方A"}],
            [{"stockCode": "000725", "stockName": "京东方A"}],
        )
        mismatched = monitoring_health.audit_watchlist_sync(
            [{"stockCode": "000725", "stockName": "京东方A"}],
            [{"stockCode": "000519", "stockName": "中兵红箭"}],
        )

        self.assertTrue(matched)
        self.assertFalse(mismatched)
        save_alert.assert_called_once()
        self.assertIn("监测列表不同步", save_alert.call_args.args[0]["title"])
        state = monitoring_health.get_watchlist_sync_state()
        self.assertEqual(state["status"], "mismatched")
        self.assertEqual(state["frontendCount"], 1)
        self.assertEqual(state["backendCount"], 1)
        self.assertIsNotNone(state["lastCheckedAt"])

    @patch.object(monitoring_health, "_save_system_alert", create=True)
    def test_unresolved_code_or_company_mapping_creates_reminder(self, save_alert):
        monitoring_health.record_mapping_failure(
            "09863",
            "",
            "公司名称缺失，无法建立精确映射",
        )

        save_alert.assert_called_once()
        event = save_alert.call_args.args[0]
        self.assertEqual(event["priority"], "P2")
        self.assertIn("无法精确映射", event["title"])


class AlertCollectionTests(unittest.TestCase):
    def test_watchlist_disclosure_collection_uses_exact_market_specific_codes(self):
        self.assertTrue(hasattr(news_collector, "collect_watchlist_official_news"))
        official_item = {
            "id": "notice-1",
            "symbol": "000725",
            "title": "业绩预增公告",
            "url": "https://example.com/notice",
            "ctime": 1_700_000_000,
            "source": "深交所公告",
            "content": "",
            "category": "policy",
        }
        hk_item = {
            "id": "hk-notice-1",
            "symbol": "00700",
            "title": "翌日披露报表",
            "url": "https://example.com/hk-notice",
            "ctime": 1_700_000_000,
            "source": "东方财富公告汇总",
            "content": "",
            "category": "company",
        }
        with patch.object(
            news_collector.database,
            "get_watchlist",
            return_value=[
                {"stockCode": "000725", "stockName": "京东方A"},
                {"stockCode": "00700", "stockName": "腾讯控股"},
            ],
        ), patch.object(
            news_collector,
            "fetch_cninfo_announcements",
            return_value=[official_item],
        ) as mock_fetch, patch.object(
            news_collector,
            "fetch_hk_disclosures",
            return_value=[hk_item],
        ) as mock_hk:
            result = news_collector.collect_watchlist_official_news()

        mock_fetch.assert_called_once_with("000725", "000725")
        mock_hk.assert_called_once_with("00700", "腾讯控股")
        self.assertEqual(result, [official_item, hk_item])

    def test_save_and_process_news_persists_before_creating_alerts(self):
        self.assertTrue(hasattr(news_collector, "save_and_process_news"))
        items = [{"id": "notice-1"}]
        call_order = []

        with patch.object(
            news_collector.database,
            "save_crawled_news",
            side_effect=lambda _items: call_order.append("saved"),
        ), patch.object(
            news_collector.notification_service,
            "process_official_news",
            side_effect=lambda _items: call_order.append("processed") or 1,
        ):
            created = news_collector.save_and_process_news(items)

        self.assertEqual(call_order, ["saved", "processed"])
        self.assertEqual(created, 1)


if __name__ == "__main__":
    unittest.main()
