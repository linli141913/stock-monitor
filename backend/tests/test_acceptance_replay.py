import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import alert_repository
import database

try:
    import acceptance_replay
except ImportError:
    acceptance_replay = None


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_alert_cases.jsonl"
HISTORICAL_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "verified_historical_alert_cases.jsonl"
)


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

    def test_verified_history_computes_sample_metrics_but_keeps_small_sample_unmeasured(self):
        cases = [
            {
                "id": "history-market-risk",
                "scope": "historical_verified",
                "kind": "market_risk",
                "as_of": "2026-07-15T10:02:00+08:00",
                "source_url": "https://qt.gtimg.cn/q=sz000725",
                "expected_rule": "extreme_price_move+high_amplitude",
                "latest_alert_at": "2026-07-15T10:02:00+08:00",
                "observations": [{
                    "available_at": "2026-07-15T10:00:04+08:00",
                    "symbol": "000725",
                    "stock_name": "京东方A",
                    "market": "cn",
                    "source_time": "2026-07-15 10:00:00",
                    "fetched_at": "2026-07-15T10:00:04+08:00",
                    "change_percent": -6.0,
                    "high": 10.8,
                    "low": 9.0,
                    "previous_close": 10.0,
                    "volume_ratio": 1.2,
                    "turnover_rate": 2.0,
                    "turnover_amount": 100000000,
                }],
                "turnover_history": [],
                "expected_alerts": [{
                    "symbol": "000725",
                    "event_type": "market_risk",
                    "direction": "negative",
                    "priority": "P2",
                }],
                "market_reaction": {
                    "source_url": "https://push2his.eastmoney.com/verified-market-history",
                    "entry": {
                        "at": "2026-07-15T10:00:00+08:00",
                        "stock_price": 10.0,
                        "index_price": 100.0,
                        "sector_price": None,
                    },
                    "horizons": {
                        "5m": {
                            "at": "2026-07-15T10:05:00+08:00",
                            "stock_price": 9.5,
                            "index_price": 99.8,
                            "sector_price": None,
                            "sector_reason": "缺少当时板块成分口径",
                        },
                        "30m": {
                            "at": "2026-07-15T10:30:00+08:00",
                            "stock_price": 9.0,
                            "index_price": 99.0,
                            "sector_price": None,
                            "sector_reason": "缺少当时板块成分口径",
                        },
                        "close": {
                            "at": "2026-07-15T15:00:00+08:00",
                            "stock_price": 8.8,
                            "index_price": 98.5,
                            "sector_price": None,
                            "sector_reason": "缺少当时板块成分口径",
                        },
                        "next_day": {
                            "status": "not_measured",
                            "reason": "下一交易日尚未结束",
                        },
                        "5_day": {
                            "status": "not_measured",
                            "reason": "尚未满5个交易日",
                        },
                    },
                    "window_high": 10.1,
                    "window_low": 8.5,
                    "tradability": {
                        "status": "tradable",
                        "suspended": False,
                        "limit_locked": False,
                        "reason": "提醒后存在连续成交价格",
                    },
                },
            },
            {
                "id": "history-date-only-official",
                "scope": "historical_verified",
                "kind": "official_event",
                "as_of": "2026-07-13T21:02:00+08:00",
                "source_url": "https://static.cninfo.com.cn/finalpage/2026-07-14/example.PDF",
                "expected_rule": "earnings_growth",
                "latest_alert_at": "2026-07-13T21:02:54+08:00",
                "observations": [{
                    "available_at": "2026-07-13T21:00:54+08:00",
                    "id": "verified-official-example",
                    "symbol": "000519",
                    "stock_name": "中兵红箭",
                    "title": "2026年半年度业绩预增公告",
                    "source": "深交所公告",
                    "url": "https://static.cninfo.com.cn/finalpage/2026-07-14/example.PDF",
                    "published_at": "2026-07-14",
                    "published_at_precision": "date",
                }],
                "expected_alerts": [{
                    "symbol": "000519",
                    "event_type": "earnings_growth",
                    "direction": "positive",
                    "priority": "P2",
                }],
            },
        ]

        report = acceptance_replay.replay_cases(cases)

        historical = report["historicalMetrics"]
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["historicalVerifiedCases"], 2)
        self.assertEqual(historical["status"], "not_measured")
        self.assertEqual(historical["sampleRecall"], 1.0)
        self.assertEqual(historical["dataMissingRate"], 0.0)
        self.assertEqual(historical["measuredLatencySamples"], 1)
        self.assertEqual(historical["p95DiscoveryDelaySeconds"], 4.0)
        self.assertEqual(historical["deadlineMisses"], 0)
        self.assertIn("样本不足", historical["reason"])

        event_study = historical["eventStudy"]
        self.assertEqual(event_study["status"], "not_measured")
        self.assertEqual(event_study["measuredSamples"], 1)
        self.assertEqual(event_study["horizons"]["5m"]["sampleSize"], 1)
        self.assertAlmostEqual(
            event_study["horizons"]["5m"]["medianStockReturnPct"],
            -5.0,
        )
        self.assertAlmostEqual(
            event_study["horizons"]["5m"]["medianExcessIndexPct"],
            -4.8,
        )
        self.assertEqual(
            event_study["horizons"]["5m"]["sectorStatus"],
            "not_measured",
        )
        self.assertEqual(
            event_study["horizons"]["next_day"]["status"],
            "not_measured",
        )
        self.assertEqual(len(event_study["groups"]), 2)

    def test_historical_missing_market_values_are_counted_and_never_coerced_to_zero(self):
        case = {
            "id": "history-missing-market-values",
            "scope": "historical_verified",
            "kind": "market_risk",
            "as_of": "2026-07-15T10:02:00+08:00",
            "source_url": "https://qt.gtimg.cn/q=sz000725",
            "expected_rule": "market_data_missing",
            "latest_alert_at": "2026-07-15T10:02:00+08:00",
            "observations": [{
                "available_at": "2026-07-15T10:00:04+08:00",
                "symbol": "000725",
                "stock_name": "京东方A",
                "market": "cn",
                "source_time": "2026-07-15 10:00:00",
                "fetched_at": "2026-07-15T10:00:04+08:00",
                "change_percent": None,
                "high": None,
                "low": None,
                "previous_close": None,
                "volume_ratio": None,
                "turnover_rate": None,
                "turnover_amount": None,
            }],
            "turnover_history": [],
            "expected_alerts": [],
        }

        report = acceptance_replay.replay_cases([case])

        historical = report["historicalMetrics"]
        self.assertEqual(historical["dataMissingCases"], 1)
        self.assertEqual(historical["dataMissingRate"], 1.0)
        self.assertEqual(historical["measuredLatencySamples"], 1)
        self.assertEqual(report["generatedAlerts"], 0)

        markdown = acceptance_replay.render_markdown(report)
        self.assertIn("样本内数据缺失率：100.0%", markdown)
        self.assertNotIn("真实历史覆盖率：100%", markdown)

    def test_verified_historical_fixture_replays_real_rules_and_partial_market_reaction(self):
        cases = acceptance_replay.load_cases(FIXTURE_PATH)
        cases.extend(acceptance_replay.load_cases(HISTORICAL_FIXTURE_PATH))

        report = acceptance_replay.replay_cases(cases)

        historical = report["historicalMetrics"]
        event_study = historical["eventStudy"]
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["scope"], "fixed_and_historical_replay")
        self.assertEqual(report["fixedFixtureCases"], 6)
        self.assertEqual(report["historicalVerifiedCases"], 8)
        self.assertEqual(historical["status"], "not_measured")
        self.assertEqual(historical["sampleRecall"], 1.0)
        self.assertEqual(historical["dataMissingCases"], 0)
        self.assertEqual(historical["measuredLatencySamples"], 6)
        self.assertEqual(historical["p95DiscoveryDelaySeconds"], 3.75)
        self.assertEqual(historical["deadlineMisses"], 0)
        self.assertEqual(event_study["measuredSamples"], 8)
        self.assertEqual(event_study["horizons"]["5m"]["sampleSize"], 8)
        self.assertEqual(event_study["horizons"]["30m"]["sampleSize"], 8)
        self.assertEqual(event_study["horizons"]["close"]["sampleSize"], 8)
        self.assertEqual(event_study["horizons"]["next_day"]["sampleSize"], 5)
        self.assertEqual(event_study["horizons"]["5_day"]["status"], "not_measured")
        self.assertEqual(len(event_study["groups"]), 5)
        self.assertTrue(all(
            item["sectorStatus"] == "not_measured"
            for item in event_study["horizons"].values()
        ))
        groups = {
            (item["rule"], item["priority"]): item
            for item in event_study["groups"]
        }
        volume_group = groups[("high_volume_ratio", "P3")]
        self.assertEqual(volume_group["horizons"]["5m"]["sampleSize"], 2)
        self.assertEqual(volume_group["tradability"]["tradable"], 2)
        limit_group = groups[("limit_move+high_amplitude", "P1")]
        self.assertEqual(limit_group["tradability"]["restricted"], 1)
        self.assertIsNotNone(limit_group["medianMaxFavorableMovePct"])
        self.assertIsNotNone(limit_group["medianMaxAdverseMovePct"])

        limit_case = next(
            item for item in report["cases"]
            if item["id"] == "history-20260715-deeptech-limit-move"
        )
        self.assertEqual(limit_case["eventStudy"]["tradability"]["status"], "restricted")
        self.assertIn("sz399001", limit_case["eventStudy"]["indexSourceUrl"])
        self.assertIsNotNone(limit_case["eventStudy"]["maxFavorableMovePct"])
        self.assertIsNotNone(limit_case["eventStudy"]["maxAdverseMovePct"])
        boe_case = next(
            item for item in report["cases"]
            if item["id"] == "history-20260709-boe-earnings-growth"
        )
        self.assertEqual(
            boe_case["sourceUrl"],
            "https://static.cninfo.com.cn/finalpage/2026-07-09/1225415802.PDF",
        )
        self.assertEqual(boe_case["expectedRule"], "earnings_growth")
        self.assertEqual(
            boe_case["evidenceTimes"][0],
            {
                "eventTime": "2026-07-09",
                "eventTimePrecision": "date",
                "availableAt": "2026-07-09T07:37:45+08:00",
            },
        )

        markdown = acceptance_replay.render_markdown(report)
        self.assertIn("真实历史样本：暂无判断（8 条，覆盖 4 个日期）", markdown)
        self.assertIn("P95 发现延迟：3.750 秒", markdown)
        self.assertIn("5_day | 未测量 | 0", markdown)
        self.assertIn("按规则与优先级分组", markdown)
        self.assertIn("limit_move+high_amplitude | P1", markdown)
        self.assertIn("暂无判断（样本不足）", markdown)

    def test_enough_rule_samples_do_not_make_missing_event_study_measured(self):
        cases = []
        start = datetime.fromisoformat("2026-06-01T10:00:00+08:00")
        for index in range(20):
            current = start + timedelta(days=index // 2)
            day = current.date().isoformat()
            cases.append({
                "id": f"history-no-reaction-{index}",
                "scope": "historical_verified",
                "kind": "official_event",
                "as_of": current.isoformat(),
                "source_url": f"https://static.cninfo.com.cn/{index}.PDF",
                "expected_rule": "official_announcement",
                "latest_alert_at": current.isoformat(),
                "observations": [{
                    "available_at": current.isoformat(),
                    "id": f"official-{index}",
                    "symbol": f"{index:06d}",
                    "stock_name": f"样本{index}",
                    "title": "关于召开临时股东大会的公告",
                    "source": "深交所公告",
                    "url": f"https://static.cninfo.com.cn/{index}.PDF",
                    "published_at": day,
                    "published_at_precision": "date",
                }],
                "expected_alerts": [{
                    "symbol": f"{index:06d}",
                    "event_type": "official_announcement",
                    "direction": "neutral",
                    "priority": "P3",
                }],
                "market_reaction": {
                    "source_url": f"https://quotes.sina.cn/{index}",
                    "entry": {"stock_price": None},
                    "horizons": {
                        name: {
                            "status": "not_measured",
                            "reason": "真实历史行情缺失",
                        }
                        for name in ("5m", "30m", "close", "next_day", "5_day")
                    },
                    "tradability": {
                        "status": "not_measured",
                        "suspended": None,
                        "limit_locked": None,
                        "reason": "真实成交状态缺失",
                    },
                },
            })

        report = acceptance_replay.replay_cases(cases)

        self.assertEqual(report["historicalMetrics"]["status"], "measured")
        self.assertEqual(
            report["historicalMetrics"]["eventStudy"]["status"],
            "not_measured",
        )
        self.assertEqual(
            report["historicalMetrics"]["eventStudy"]["measuredSamples"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
