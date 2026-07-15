import importlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import alert_repository
import database
import main
from fastapi.testclient import TestClient


def optional_import(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


risk_engine = optional_import("risk_engine")


def history(rate, amount=100.0, count=20):
    return [
        {"turnover_rate": rate, "turnover_amount": amount}
        for _ in range(count)
    ]


def snapshot(**overrides):
    data = {
        "symbol": "601988",
        "market": "cn",
        "source_time": "2026-07-13 10:30:00",
        "fetched_at": "2026-07-13T10:30:02+08:00",
        "change_percent": 0.8,
        "high": 5.10,
        "low": 5.00,
        "previous_close": 5.00,
        "volume_ratio": 1.1,
        "turnover_rate": 1.0,
        "turnover_amount": 120.0,
    }
    data.update(overrides)
    return data


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(risk_engine, "risk_engine 模块尚未实现")

    def test_large_cap_low_absolute_turnover_can_be_warning(self):
        result = risk_engine.evaluate_market_risk(
            snapshot(change_percent=-5.5, turnover_amount=600.0),
            history(0.18, amount=100.0),
        )

        self.assertEqual(result["turnoverRisk"]["status"], "warning")
        self.assertEqual(result["turnoverRisk"]["label"], "警惕")
        self.assertAlmostEqual(result["turnoverRisk"]["multiple"], 5.56, places=2)
        self.assertEqual(result["priority"], "P2")
        self.assertEqual(result["direction"], "negative")

    def test_small_cap_high_absolute_turnover_can_remain_normal(self):
        result = risk_engine.evaluate_market_risk(
            snapshot(symbol="300001", turnover_rate=9.0),
            history(8.0, amount=100.0),
        )

        self.assertEqual(result["turnoverRisk"]["status"], "normal")
        self.assertEqual(result["turnoverRisk"]["label"], "正常")

    def test_turnover_boundaries_and_auxiliary_condition(self):
        active = risk_engine.evaluate_market_risk(
            snapshot(turnover_rate=1.5),
            history(1.0),
        )
        high_without_auxiliary = risk_engine.evaluate_market_risk(
            snapshot(turnover_rate=2.5),
            history(1.0),
        )
        warning = risk_engine.evaluate_market_risk(
            snapshot(turnover_rate=2.5, volume_ratio=2.0),
            history(1.0),
        )

        self.assertEqual(active["turnoverRisk"]["status"], "active")
        self.assertEqual(high_without_auxiliary["turnoverRisk"]["status"], "active")
        self.assertEqual(warning["turnoverRisk"]["status"], "warning")

    def test_missing_and_insufficient_turnover_history_are_truthful(self):
        missing = risk_engine.evaluate_market_risk(
            snapshot(turnover_rate=None),
            history(1.0),
        )
        insufficient = risk_engine.evaluate_market_risk(
            snapshot(),
            history(0.2, count=19),
        )
        hk = risk_engine.evaluate_market_risk(
            snapshot(market="hk"),
            history(0.2),
        )

        self.assertEqual(missing["turnoverRisk"]["status"], "unavailable")
        self.assertEqual(insufficient["turnoverRisk"]["status"], "insufficient")
        self.assertEqual(hk["turnoverRisk"]["status"], "unavailable")

    def test_one_signal_is_p3_two_signals_are_p2_and_limit_is_p1(self):
        one_signal = risk_engine.evaluate_market_risk(
            snapshot(volume_ratio=2.2),
            history(1.0),
        )
        two_signals = risk_engine.evaluate_market_risk(
            snapshot(volume_ratio=2.2, change_percent=-5.2),
            history(1.0),
        )
        limit_move = risk_engine.evaluate_market_risk(
            snapshot(change_percent=-10.0),
            history(1.0),
        )

        self.assertEqual(one_signal["priority"], "P3")
        self.assertEqual(two_signals["priority"], "P2")
        self.assertEqual(limit_move["priority"], "P1")

    def test_risk_alert_title_names_rules_and_summary_keeps_actual_values(self):
        current = snapshot(
            stock_name="中兵红箭",
            change_percent=-5.5,
            high=5.5,
            low=5.0,
            previous_close=5.0,
            volume_ratio=2.3,
            turnover_rate=2.5,
        )
        result = risk_engine.evaluate_market_risk(current, history(1.0))

        event = risk_engine.build_risk_alert_event(current, result)

        self.assertIsNotNone(event)
        self.assertIn("触发P2强提醒", event["title"])
        self.assertIn("涨跌幅≥5%", event["title"])
        self.assertIn("振幅≥8%", event["title"])
        self.assertIn("量比≥2", event["title"])
        self.assertIn("涨跌幅 -5.50%", event["summary"])
        self.assertIn("量比 2.30", event["summary"])
        self.assertIn("振幅 10.00%", event["summary"])
        self.assertEqual(
            event["source_event_id"],
            "risk:2026-07-13:extreme_price_move,high_amplitude,high_volume_ratio,turnover_warning",
        )

    def test_complete_fund_history_enables_consecutive_flow_and_divergence(self):
        verified_history = [
            {"trade_date": "2026-07-09", "close": 10.0, "fund_flow": -1.0},
            {"trade_date": "2026-07-10", "close": 10.2, "fund_flow": -2.0},
            {"trade_date": "2026-07-13", "close": 10.6, "fund_flow": -3.0},
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=10.6),
            history(1.0),
            verified_history=verified_history,
        )

        codes = {item["code"] for item in result["signals"]}
        self.assertIn("consecutive_fund_outflow", codes)
        self.assertIn("price_fund_divergence", codes)
        self.assertEqual(result["fundFlowRisk"]["status"], "triggered")
        self.assertEqual(result["priority"], "P2")
        self.assertEqual(result["direction"], "negative")

    def test_fund_rules_use_latest_complete_history_when_today_flow_is_delayed(self):
        verified_history = [
            {
                "trade_date": "2026-07-10",
                "close": 10.0,
                "fund_close": 10.0,
                "fund_flow": -1.0,
            },
            {
                "trade_date": "2026-07-13",
                "close": 10.2,
                "fund_close": 10.2,
                "fund_flow": -2.0,
            },
            {
                "trade_date": "2026-07-14",
                "close": 10.6,
                "fund_close": 10.6,
                "fund_flow": -3.0,
                "ma5": 10.0,
                "ma10": 9.9,
                "ma20": 9.8,
            },
            {
                "trade_date": "2026-07-15",
                "close": 9.7,
                "fund_close": None,
                "fund_flow": None,
                "ma5": 9.5,
                "ma10": 9.4,
                "ma20": 9.3,
            },
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=9.7),
            history(1.0),
            verified_history=verified_history,
        )

        self.assertIn(
            "consecutive_fund_outflow",
            {item["code"] for item in result["signals"]},
        )
        self.assertEqual(result["fundFlowRisk"]["status"], "triggered")
        self.assertEqual(result["movingAverageRisk"]["status"], "no_signal")

    def test_fund_rules_reject_a_gap_inside_latest_three_history_rows(self):
        verified_history = [
            {
                "trade_date": "2026-07-10",
                "close": 10.0,
                "fund_close": 10.0,
                "fund_flow": -1.0,
            },
            {
                "trade_date": "2026-07-13",
                "close": 10.2,
                "fund_close": None,
                "fund_flow": None,
            },
            {
                "trade_date": "2026-07-14",
                "close": 10.3,
                "fund_close": 10.3,
                "fund_flow": -3.0,
            },
            {
                "trade_date": "2026-07-15",
                "close": 10.1,
                "fund_close": 10.1,
                "fund_flow": -4.0,
            },
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=10.1),
            history(1.0),
            verified_history=verified_history,
        )

        self.assertEqual(result["fundFlowRisk"]["status"], "unavailable")
        self.assertNotIn(
            "consecutive_fund_outflow",
            {item["code"] for item in result["signals"]},
        )

    def test_incomplete_fund_history_stays_unavailable_without_fake_signal(self):
        incomplete = [
            {"trade_date": "2026-07-10", "close": 10.0, "fund_flow": -1.0},
            {"trade_date": "2026-07-13", "close": 10.3, "fund_flow": None},
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=10.3),
            history(1.0),
            verified_history=incomplete,
        )

        self.assertEqual(result["fundFlowRisk"]["status"], "unavailable")
        self.assertEqual(result["fundFlowRisk"]["label"], "暂无判断")
        self.assertNotIn(
            "consecutive_fund_outflow",
            {item["code"] for item in result["signals"]},
        )

    def test_moving_average_break_requires_a_verified_cross_below(self):
        verified_break = [
            {
                "trade_date": "2026-07-10",
                "close": 10.2,
                "ma5": 10.0,
                "ma10": 9.9,
                "ma20": 9.8,
                "fund_flow": 1.0,
            },
            {
                "trade_date": "2026-07-13",
                "close": 9.7,
                "ma5": 9.9,
                "ma10": 9.8,
                "ma20": 9.6,
                "fund_flow": 1.0,
            },
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=9.7),
            history(1.0),
            verified_history=verified_break,
        )

        self.assertIn("ma_breakdown", {item["code"] for item in result["signals"]})
        self.assertEqual(result["movingAverageRisk"]["status"], "triggered")
        self.assertEqual(result["movingAverageRisk"]["periods"], ["MA5", "MA10"])

    def test_moving_average_missing_or_already_below_is_not_a_new_break(self):
        already_below = [
            {"trade_date": "2026-07-10", "close": 9.8, "ma5": 10.0},
            {"trade_date": "2026-07-13", "close": 9.7, "ma5": 9.9},
        ]

        result = risk_engine.evaluate_market_risk(
            snapshot(close=9.7),
            history(1.0),
            verified_history=already_below,
        )

        self.assertEqual(result["movingAverageRisk"]["status"], "no_signal")
        self.assertNotIn("ma_breakdown", {item["code"] for item in result["signals"]})


class LinkageRiskTests(unittest.TestCase):
    def test_overseas_mapping_uses_exact_industry_and_business_not_tech_similarity(self):
        panel_mapping = risk_engine.build_exact_overseas_mappings(
            {
                "industry_name": "光学光电子",
                "search_terms": ["显示面板", "AI"],
            },
            "液晶显示面板与柔性OLED研发制造",
        )
        foundry_mapping = risk_engine.build_exact_overseas_mappings(
            {
                "industry_name": "半导体",
                "search_terms": ["半导体", "集成电路"],
            },
            "集成电路晶圆代工与制造",
        )

        self.assertEqual(panel_mapping, [])
        self.assertEqual(
            {item["symbol"] for item in foundry_mapping},
            {"SOXX", "TSM"},
        )
        self.assertTrue(all(item["mapping_verified"] for item in foundry_mapping))

    def test_market_history_merge_requires_same_real_dates_and_current_date(self):
        merged = risk_engine.merge_verified_market_history(
            [
                {"trade_date": "2026-07-14", "close": 10.0, "fund_flow": -1.0},
                {"trade_date": "2026-07-15", "close": 9.8, "fund_flow": -2.0},
            ],
            [
                {"trade_date": "2026-07-14", "close": 10.0, "ma5": 9.9},
                {"trade_date": "2026-07-15", "close": 9.8, "ma5": 9.95},
            ],
            expected_trade_date="2026-07-15",
        )
        stale = risk_engine.merge_verified_market_history(
            [{"trade_date": "2026-07-14", "close": 10.0, "fund_flow": -1.0}],
            [{"trade_date": "2026-07-14", "close": 10.0, "ma5": 9.9}],
            expected_trade_date="2026-07-15",
        )

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[-1]["fund_flow"], -2.0)
        self.assertEqual(merged[-1]["ma5"], 9.95)
        self.assertEqual(stale, [])

    def test_market_history_keeps_verified_kline_when_fund_source_is_missing(self):
        merged = risk_engine.merge_verified_market_history(
            [],
            [
                {"trade_date": "2026-07-14", "close": 10.2, "ma5": 10.0},
                {"trade_date": "2026-07-15", "close": 9.7, "ma5": 9.9},
            ],
            expected_trade_date="2026-07-15",
        )

        result = risk_engine.evaluate_market_risk(
            snapshot(close=9.7),
            history(1.0),
            verified_history=merged,
        )

        self.assertEqual(len(merged), 2)
        self.assertIsNone(merged[-1]["fund_flow"])
        self.assertEqual(result["fundFlowRisk"]["label"], "暂无判断")
        self.assertEqual(result["movingAverageRisk"]["status"], "triggered")
        self.assertEqual(result["movingAverageRisk"]["periods"], ["MA5"])

    def test_eastmoney_fund_history_uses_direct_http_and_parses_fields(self):
        self.assertTrue(hasattr(main, "fetch_eastmoney_fund_history"))
        response = MagicMock()
        response.json.return_value = {
            "data": {
                "klines": [
                    "2026-07-15,-123,1,2,3,4,5,6,7,8,9,9.70,-4.90,0,0",
                ],
            },
        }
        session = MagicMock()
        session.get.return_value = response

        with patch.object(main.requests, "Session", return_value=session):
            rows = main.fetch_eastmoney_fund_history("000725")

        self.assertIs(session.trust_env, False)
        request_url = session.get.call_args.args[0]
        self.assertEqual(
            request_url,
            "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        )
        self.assertEqual(rows, [{
            "trade_date": "2026-07-15",
            "fund_close": 9.7,
            "fund_flow": -123.0,
        }])

    def test_verified_history_keeps_kline_when_fund_fetch_fails(self):
        kline_payload = {
            "data": [
                {"time": "2026-07-14", "close": 10.2, "ma5": 10.0},
                {"time": "2026-07-15", "close": 9.7, "ma5": 9.9},
            ],
        }
        with patch(
            "akshare.stock_individual_fund_flow",
            side_effect=ConnectionError("fund source unavailable"),
        ), patch.object(
            main,
            "fetch_eastmoney_fund_history",
            create=True,
            side_effect=ConnectionError("fund source unavailable"),
        ), patch.object(
            main,
            "get_stock_kline",
            return_value=kline_payload,
        ):
            rows = main.get_verified_market_history("000725", "2026-07-15")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["close"], 9.7)
        self.assertIsNone(rows[-1]["fund_flow"])

    def test_sector_snapshot_uses_complete_constituents_and_real_flow_ranking(self):
        sector_rows = [
            {"行业": "汽车整车", "行业-涨跌幅": 1.0, "净额": "5.0亿"},
            {"行业": "光学光电子", "行业-涨跌幅": -3.2, "净额": "-12.0亿"},
            {"行业": "消费电子", "行业-涨跌幅": -1.0, "净额": "-6.0亿"},
        ]
        quotes = [
            {"symbol": "000725", "name": "京东方A", "change_percent": -2.0, "market_cap": 1000},
            {"symbol": "000100", "name": "TCL科技", "change_percent": -8.2, "market_cap": 1200},
            {"symbol": "600707", "name": "彩虹股份", "change_percent": -1.0, "market_cap": 300},
        ]

        complete = risk_engine.build_verified_sector_snapshot(
            "光学光电子",
            sector_rows,
            quotes,
            expected_constituents=3,
        )
        incomplete = risk_engine.build_verified_sector_snapshot(
            "光学光电子",
            sector_rows,
            quotes[:2],
            expected_constituents=3,
        )

        self.assertEqual(complete["change_percent"], -3.2)
        self.assertEqual(complete["advancers"], 0)
        self.assertEqual(complete["total"], 3)
        self.assertEqual(complete["leader"]["symbol"], "000100")
        self.assertEqual(complete["fund_flow"]["direction"], "outflow")
        self.assertEqual(complete["fund_flow"]["rank"], 1)
        self.assertIsNone(incomplete["advancers"])
        self.assertIsNone(incomplete["leader"])

        incomplete_result = risk_engine.evaluate_linkage_risk({
            "sector": incomplete,
            "overseas": [],
        })
        self.assertIn("dimensions", incomplete_result["sectorRisk"])
        dimensions = incomplete_result["sectorRisk"]["dimensions"]
        self.assertEqual(dimensions["decline"]["status"], "triggered")
        self.assertEqual(dimensions["breadth"]["status"], "unavailable")
        self.assertEqual(dimensions["leader"]["status"], "unavailable")
        self.assertEqual(dimensions["fundFlow"]["status"], "triggered")
        self.assertIs(incomplete_result["sectorRisk"]["dataComplete"], False)

    def test_sector_rules_use_verified_decline_breadth_leader_and_flow_rank(self):
        linkage = {
            "symbol": "000725",
            "stock_name": "京东方A",
            "source_time": "2026-07-15 10:30:00",
            "fetched_at": "2026-07-15T10:30:02+08:00",
            "sector": {
                "status": "available",
                "name": "光学光电子",
                "change_percent": -3.2,
                "advancers": 3,
                "total": 20,
                "leader": {
                    "symbol": "000100",
                    "name": "TCL科技",
                    "change_percent": -8.3,
                    "is_limit_down": False,
                },
                "fund_flow": {
                    "value": -12.4,
                    "direction": "outflow",
                    "rank": 2,
                    "total": 86,
                    "verified": True,
                },
            },
            "overseas": [],
        }

        result = risk_engine.evaluate_linkage_risk(linkage)

        self.assertEqual(result["priority"], "P2")
        self.assertEqual(result["direction"], "negative")
        self.assertEqual(
            {item["code"] for item in result["signals"]},
            {
                "sector_decline",
                "sector_breadth_weak",
                "sector_leader_decline",
                "sector_fund_outflow_top",
            },
        )
        self.assertEqual(result["sectorRisk"]["status"], "triggered")
        self.assertIn("dimensions", result["sectorRisk"])
        self.assertEqual(
            {
                key: state["status"]
                for key, state in result["sectorRisk"]["dimensions"].items()
            },
            {
                "decline": "triggered",
                "breadth": "triggered",
                "leader": "triggered",
                "fundFlow": "triggered",
            },
        )
        self.assertIs(result["sectorRisk"]["dataComplete"], True)

    def test_unverified_or_merely_tech_overseas_relation_is_never_used(self):
        result = risk_engine.evaluate_linkage_risk({
            "symbol": "000725",
            "stock_name": "京东方A",
            "source_time": "2026-07-15 10:30:00",
            "sector": {"status": "unavailable"},
            "overseas": [{
                "symbol": "NVDA",
                "name": "英伟达",
                "kind": "company",
                "change_percent": -12.0,
                "mapping_verified": False,
                "mapping_basis": "同属科技股",
            }],
        })

        self.assertIsNone(result["priority"])
        self.assertEqual(result["sectorRisk"]["label"], "暂无判断")
        self.assertEqual(result["overseasRisk"]["label"], "暂无判断")
        self.assertEqual(result["signals"], [])

    def test_exact_overseas_business_mapping_can_trigger_but_uses_kind_threshold(self):
        result = risk_engine.evaluate_linkage_risk({
            "symbol": "688981",
            "stock_name": "中芯国际",
            "source_time": "2026-07-15 10:30:00",
            "sector": {"status": "unavailable"},
            "overseas": [
                {
                    "symbol": "SOXX",
                    "name": "费城半导体指数",
                    "kind": "index",
                    "change_percent": -3.1,
                    "mapping_verified": True,
                    "mapping_basis": "公司官方所属行业为半导体",
                },
                {
                    "symbol": "NVDA",
                    "name": "英伟达",
                    "kind": "company",
                    "change_percent": -7.9,
                    "mapping_verified": True,
                    "mapping_basis": "同属GPU业务",
                },
            ],
        })

        self.assertEqual(
            [item["code"] for item in result["signals"]],
            ["overseas_index_extreme"],
        )
        self.assertEqual(result["priority"], "P3")

    def test_new_linkage_alert_reuses_delivery_and_event_ai_chain(self):
        linkage = {
            "symbol": "000725",
            "stock_name": "京东方A",
            "source_time": "2026-07-15 10:30:00",
            "sector": {
                "status": "available",
                "name": "光学光电子",
                "change_percent": -3.2,
                "advancers": 2,
                "total": 20,
            },
            "overseas": [],
        }

        saved_alert = {
            "id": "linkage-alert",
            "eventType": "linkage_risk",
            "priority": "P2",
        }
        with patch.object(
            alert_repository,
            "save_alert_event",
            return_value=(saved_alert, True),
        ), patch("notification_service.process_new_alert", create=True) as mock_process:
            result = risk_engine.process_linkage_snapshot(
                linkage,
                create_alert=True,
                persist_snapshot=False,
            )

        self.assertEqual(result["priority"], "P2")
        mock_process.assert_called_once()
        self.assertEqual(mock_process.call_args.args[0]["eventType"], "linkage_risk")


class BatchOverviewRiskTests(unittest.TestCase):
    def test_current_limit_move_is_critical_without_saved_history(self):
        fields = [""] * 38
        fields[1] = "深科技"
        fields[2] = "000021"
        fields[3] = "52.12"
        fields[32] = "-10.00"
        fields[36] = "100"
        fields[37] = "115.97"

        class FakeResponse:
            encoding = "gbk"
            text = f'v_sz000021="{"~".join(fields)}";'

        with patch.object(main.requests, "get", return_value=FakeResponse()), patch.object(
            main,
            "get_sina_stock_fund_flow",
            return_value=None,
        ), patch.object(
            alert_repository,
            "get_latest_signal_state",
            return_value=None,
        ):
            response = TestClient(main.app).get(
                "/api/stock/batch_overview?symbols=000021"
            )

        self.assertEqual(response.status_code, 200)
        risk = response.json()["data"][0]["risk"]
        self.assertEqual(risk["riskStatus"], "critical")
        self.assertEqual(risk["priority"], "P1")
        self.assertIn("极端涨跌区间", risk["reason"])


class SignalSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(
            database,
            "DB_PATH",
            f"{self.temp_dir.name}/signals.db",
        )
        self.db_patcher.start()
        database.init_db()
        alert_repository.init_alert_tables()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _linkage_snapshot(source_time, *, decline=-3.2, advancers=10, fund_rank=10):
        return {
            "symbol": "000725",
            "stock_name": "京东方A",
            "source_time": source_time,
            "fetched_at": source_time.replace(" ", "T") + "+08:00",
            "sector": {
                "status": "available",
                "name": "光学光电子",
                "change_percent": decline,
                "advancers": advancers,
                "total": 20,
                "leader": {
                    "symbol": "000100",
                    "name": "TCL科技",
                    "change_percent": -1.0,
                    "is_limit_down": False,
                },
                "fund_flow": {
                    "value": -12.4,
                    "direction": "outflow",
                    "rank": fund_rank,
                    "total": 86,
                    "verified": True,
                },
            },
            "overseas": [],
        }

    def test_recent_risk_states_read_market_and_linkage_from_existing_snapshots(self):
        source_time = "2026-07-15 10:00:00"
        current = snapshot(
            symbol="000725",
            stock_name="京东方A",
            source_time=source_time,
            fetched_at="2026-07-15T10:00:02+08:00",
            change_percent=-5.5,
        )
        market_risk = risk_engine.evaluate_market_risk(current, history(1.0))
        linkage = self._linkage_snapshot(
            source_time,
            decline=-3.2,
            fund_rank=2,
        )
        linkage_risk = risk_engine.evaluate_linkage_risk(linkage)

        alert_repository.save_signal_snapshot({**current, "risk": market_risk})
        alert_repository.save_linkage_snapshot(linkage, linkage_risk)

        market_states = alert_repository.get_recent_risk_states(
            "000725",
            "2026-07-15 10:05:00",
            "market",
        )
        linkage_states = alert_repository.get_recent_risk_states(
            "000725",
            "2026-07-15 10:05:00",
            "linkage",
        )

        self.assertEqual(market_states[0]["priority"], "P3")
        self.assertEqual(market_states[0]["direction"], "negative")
        self.assertEqual(linkage_states[0]["priority"], "P2")
        self.assertEqual(linkage_states[0]["direction"], "negative")

    def test_linkage_signal_changes_do_not_repeat_inside_same_risk_episode(self):
        first = self._linkage_snapshot(
            "2026-07-15 10:00:00",
            decline=-3.2,
            advancers=10,
            fund_rank=2,
        )
        changed = self._linkage_snapshot(
            "2026-07-15 10:05:00",
            decline=-2.5,
            advancers=2,
            fund_rank=2,
        )

        with patch("notification_service.process_new_alert") as mock_process:
            risk_engine.process_linkage_snapshot(first, create_alert=True)
            risk_engine.process_linkage_snapshot(changed, create_alert=True)

        self.assertEqual(len(alert_repository.list_alerts()), 1)
        mock_process.assert_called_once()

    def test_linkage_priority_upgrade_updates_same_episode_alert(self):
        p3 = self._linkage_snapshot(
            "2026-07-15 10:00:00",
            decline=-2.5,
            advancers=10,
            fund_rank=2,
        )
        p2 = self._linkage_snapshot(
            "2026-07-15 10:05:00",
            decline=-3.2,
            advancers=10,
            fund_rank=2,
        )

        with patch("notification_service.process_new_alert") as mock_process:
            risk_engine.process_linkage_snapshot(p3, create_alert=True)
            risk_engine.process_linkage_snapshot(p2, create_alert=True)

        alerts = alert_repository.list_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["priority"], "P2")
        self.assertEqual(mock_process.call_count, 2)

    def test_linkage_reentry_requires_two_cleared_snapshot_buckets(self):
        active = self._linkage_snapshot(
            "2026-07-15 10:00:00",
            decline=-3.2,
            fund_rank=2,
        )
        first_clear = self._linkage_snapshot(
            "2026-07-15 10:05:00",
            decline=0.2,
            advancers=12,
            fund_rank=10,
        )
        flicker = self._linkage_snapshot(
            "2026-07-15 10:10:00",
            decline=-3.2,
            fund_rank=2,
        )
        second_clear = self._linkage_snapshot(
            "2026-07-15 10:15:00",
            decline=0.2,
            advancers=12,
            fund_rank=10,
        )
        third_clear = self._linkage_snapshot(
            "2026-07-15 10:20:00",
            decline=0.2,
            advancers=12,
            fund_rank=10,
        )
        reentered = self._linkage_snapshot(
            "2026-07-15 10:25:00",
            decline=-3.2,
            fund_rank=2,
        )

        with patch("notification_service.process_new_alert") as mock_process:
            for item in (
                active,
                first_clear,
                flicker,
                second_clear,
                third_clear,
                reentered,
            ):
                risk_engine.process_linkage_snapshot(item, create_alert=True)

        self.assertEqual(len(alert_repository.list_alerts()), 2)
        self.assertEqual(mock_process.call_count, 2)

    def test_market_signal_changes_do_not_repeat_inside_same_risk_episode(self):
        first = snapshot(
            symbol="000519",
            stock_name="中兵红箭",
            source_time="2026-07-15 10:00:00",
            fetched_at="2026-07-15T10:00:02+08:00",
            change_percent=-5.5,
            high=5.5,
            low=5.0,
            previous_close=5.0,
            volume_ratio=1.0,
        )
        changed = snapshot(
            symbol="000519",
            stock_name="中兵红箭",
            source_time="2026-07-15 10:05:00",
            fetched_at="2026-07-15T10:05:02+08:00",
            change_percent=-5.5,
            high=5.1,
            low=5.0,
            previous_close=5.0,
            volume_ratio=2.2,
        )

        with patch("notification_service.process_new_alert") as mock_process:
            risk_engine.process_market_snapshot(first, create_alert=True)
            risk_engine.process_market_snapshot(changed, create_alert=True)

        self.assertEqual(len(alert_repository.list_alerts()), 1)
        mock_process.assert_called_once()

    def test_snapshot_history_uses_same_time_bucket_and_excludes_current_day(self):
        self.assertTrue(hasattr(alert_repository, "save_signal_snapshot"))
        self.assertTrue(hasattr(alert_repository, "get_signal_history"))
        start = datetime(2026, 6, 1, 10, 30)
        for index in range(21):
            source_time = (start + timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S")
            alert_repository.save_signal_snapshot(snapshot(
                source_time=source_time,
                fetched_at=f"{source_time.replace(' ', 'T')}+08:00",
                turnover_rate=0.2 + index / 100,
            ))

        current_time = (start + timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        result = alert_repository.get_signal_history("601988", current_time, limit_days=20)

        self.assertEqual(len(result), 20)
        self.assertNotIn(0.4, [item["turnover_rate"] for item in result])

    def test_turnover_sample_reason_is_separate_from_overall_limit_risk(self):
        current = snapshot(symbol="000519", change_percent=-9.99)
        risk = risk_engine.evaluate_market_risk(current, history(1.0, count=19))
        alert_repository.save_signal_snapshot({**current, "risk": risk})

        state = alert_repository.get_latest_signal_state("000519")

        self.assertIn("极端涨跌区间", state["reason"])
        self.assertIn("20 个交易日", state["turnoverRisk"]["reason"])
        self.assertNotIn("极端涨跌区间", state["turnoverRisk"]["reason"])

    def test_latest_state_preserves_verified_fund_and_ma_status_without_new_columns(self):
        current = snapshot(symbol="000725")
        risk = risk_engine.evaluate_market_risk(current, history(1.0, count=19))
        risk["fundFlowRisk"] = {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "资金历史数据不完整",
        }
        risk["movingAverageRisk"] = {
            "status": "triggered",
            "label": "已触发",
            "periods": ["MA20"],
            "reason": "已验证跌破MA20",
        }

        alert_repository.save_signal_snapshot({**current, "risk": risk})
        state = alert_repository.get_latest_signal_state("000725")

        self.assertEqual(state["fundFlowRisk"]["label"], "暂无判断")
        self.assertEqual(state["movingAverageRisk"]["status"], "triggered")
        self.assertEqual(state["movingAverageRisk"]["periods"], ["MA20"])

    def test_linkage_snapshot_merges_without_overwriting_market_risk(self):
        self.assertTrue(hasattr(alert_repository, "save_linkage_snapshot"))
        self.assertTrue(hasattr(alert_repository, "get_latest_linkage_state"))

        def linkage_snapshot(symbol):
            return {
                "symbol": symbol,
                "stock_name": "测试股票",
                "source_time": "2026-07-13 10:30:00",
                "fetched_at": "2026-07-13T10:30:02+08:00",
                "sector": {
                    "status": "available",
                    "name": "测试板块",
                    "change_percent": -3.2,
                    "advancers": 2,
                    "total": 20,
                    "leader": {
                        "symbol": "000001",
                        "name": "测试龙头",
                        "change_percent": -8.1,
                    },
                    "fund_flow": {
                        "verified": True,
                        "direction": "outflow",
                        "rank": 2,
                        "total": 31,
                    },
                },
                "overseas": [],
            }

        current = snapshot(symbol="000725")
        market_risk = risk_engine.evaluate_market_risk(current, history(1.0, count=19))
        market_risk["fundFlowRisk"] = {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "资金历史数据不完整",
        }
        market_risk["movingAverageRisk"] = {
            "status": "triggered",
            "label": "已触发",
            "periods": ["MA20"],
            "reason": "已验证跌破MA20",
        }
        linkage = linkage_snapshot("000725")
        linkage_risk = risk_engine.evaluate_linkage_risk(linkage)

        alert_repository.save_signal_snapshot({**current, "risk": market_risk})
        alert_repository.save_linkage_snapshot(linkage, linkage_risk)

        market_state = alert_repository.get_latest_signal_state("000725")
        linkage_state = alert_repository.get_latest_linkage_state(
            "000725",
            "2026-07-13",
        )
        self.assertEqual(market_state["fundFlowRisk"]["label"], "暂无判断")
        self.assertEqual(market_state["movingAverageRisk"]["periods"], ["MA20"])
        self.assertEqual(linkage_state["priority"], "P2")
        self.assertEqual(
            linkage_state["sectorRisk"]["dimensions"]["leader"]["status"],
            "triggered",
        )
        self.assertIsNone(
            alert_repository.get_latest_linkage_state("000725", "2026-07-14")
        )

        reverse_linkage = linkage_snapshot("000519")
        reverse_risk = risk_engine.evaluate_linkage_risk(reverse_linkage)
        reverse_market = snapshot(symbol="000519")
        reverse_market_risk = risk_engine.evaluate_market_risk(
            reverse_market,
            history(1.0, count=19),
        )
        alert_repository.save_linkage_snapshot(reverse_linkage, reverse_risk)
        alert_repository.save_signal_snapshot({**reverse_market, "risk": reverse_market_risk})

        self.assertEqual(
            alert_repository.get_latest_linkage_state("000519", "2026-07-13")[
                "sectorRisk"
            ]["dimensions"]["fundFlow"]["status"],
            "triggered",
        )

    def test_new_p1_risk_alert_uses_the_event_ai_trigger_chain(self):
        with patch(
            "notification_service.process_new_alert",
            create=True,
        ) as mock_process:
            risk_engine.process_market_snapshot(
                snapshot(symbol="000519", change_percent=-10.0),
                persist_snapshot=False,
                create_alert=True,
            )

        mock_process.assert_called_once()
        self.assertEqual(mock_process.call_args.args[0]["priority"], "P1")


if __name__ == "__main__":
    unittest.main()
