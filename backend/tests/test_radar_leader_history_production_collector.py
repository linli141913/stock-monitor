import unittest
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionSourceStatus,
    resolve_leader_formal_research_production_delivery,
)
from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_history_production_collector import (
    LeaderHistoryProductionFrozenBatch,
    build_leader_history_production_loader,
    collect_leader_history_production_source,
    fetch_leader_history_series_batch,
    _default_eastmoney_qfq_requester,
    _default_history_requester,
)
from radar.leader_history_features import HistoryAdjustmentBasis
from radar.sector_history_trading_presence import (
    HistoricalTradingPresenceBatch,
    TRADING_PRESENCE_SOURCE_CONTRACT_ID,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.sources.leader_tradability_public_poc import (
    PublicTradingCalendarEvidence,
)
from tests import test_radar_leader_research_source_admission as helpers


class LeaderHistoryProductionCollectorTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.entries = helper.history_entries()

    def bundle(self, *, entries=None):
        entries = self.entries if entries is None else entries
        memberships = {}
        series = {}
        for entry in entries:
            memberships[entry.query.membership.industry_code] = entry.query.membership
            series.update(entry.query.series_by_symbol)
        return LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=entries[0].query.expected_trade_dates,
            memberships_by_industry=memberships,
            series_by_symbol=series,
            calendar_evidence=PublicTradingCalendarEvidence(
                exchange="sse",
                trading_dates=entries[0].query.expected_trade_dates,
                source_contract_id="sse-a-share-trading-calendar-v1",
                source_name="上海证券交易所",
                source_url="https://www.sse.com.cn/market/stockdata/overview/",
                document_id="sse-calendar-window-test",
                source_time=self.context.as_of - timedelta(minutes=2),
                fetched_at=self.context.as_of - timedelta(minutes=1),
                content_sha256="sha256:" + "c" * 64,
            ),
        )

    def test_complete_candidate_universe_replays_history_in_plan_order(self):
        source = collect_leader_history_production_source(
            self.context,
            self.bundle(),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(
            source.symbols,
            tuple(item.symbol for item in self.context.candidate_plan.items),
        )
        self.assertEqual(
            tuple(entry.symbol for entry in source.payload),
            source.symbols,
        )
        self.assertNotIn("PublicHistorySeries", repr(source.to_evidence()))
        self.assertNotIn("000001", repr(source.to_evidence()))

    def test_completed_batch_enters_existing_validated_provider(self):
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                history_loader=build_leader_history_production_loader(
                    self.bundle()
                ),
            )
        )

        delivery = providers.history_provider(self.context)
        resolution = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="history",
            value=delivery,
        )

        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )
        self.assertEqual(
            delivery.proof.returned_count,
            self.context.candidate_plan.candidate_count,
        )

    def test_missing_shared_series_blocks_whole_batch_and_removes_payload(self):
        bundle = self.bundle()
        series = dict(bundle.series_by_symbol)
        series.pop(next(iter(series)))
        source = collect_leader_history_production_source(
            self.context,
            replace(bundle, series_by_symbol=series),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)

    def test_industry_release_drift_is_not_relabelled_ready(self):
        changed = tuple(
            replace(
                entry,
                query=replace(
                    entry.query,
                    membership=replace(
                        entry.query.membership,
                        release_id="other",
                    ),
                ),
            )
            for entry in self.entries
        )
        source = collect_leader_history_production_source(
            self.context,
            self.bundle(entries=changed),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_bse_member_is_explicitly_excluded_without_cropping_sh_sz(self):
        target = self.context.candidate_plan.items[0]
        template = self.helper.raw["industry_records"][0]
        extra = template.model_copy(update={
            "source_symbol": "920001",
            "security_identity": "920001",
            "division_code": target.industry_code,
        })
        context = build_leader_research_runtime_source_context(
            candidate_plan=self.helper.plan,
            quote_batch=self.helper.raw["quote_batch"],
            quote_health=self.helper.raw["quote_health"],
            security_records=self.helper.raw["security_records"],
            industry_records=(*self.helper.raw["industry_records"], extra),
        )
        bundle = self.bundle()
        membership = bundle.memberships_by_industry[target.industry_code]
        memberships = {
            **bundle.memberships_by_industry,
            target.industry_code: replace(
                membership,
                excluded_out_of_scope_count=1,
            ),
        }

        source = collect_leader_history_production_source(
            context,
            replace(bundle, memberships_by_industry=memberships),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        target_entry = next(
            entry for entry in source.payload if entry.symbol == target.symbol
        )
        self.assertEqual(
            target_entry.query.membership.excluded_out_of_scope_count,
            1,
        )
        self.assertNotIn(
            "920001",
            target_entry.query.membership.member_symbols,
        )

    def test_series_after_candidate_as_of_plus_skew_requires_new_prefetch(self):
        bundle = self.bundle()
        symbol = next(iter(bundle.series_by_symbol))
        stale = replace(
            bundle.series_by_symbol[symbol],
            fetched_at=self.context.as_of + timedelta(seconds=6),
        )
        source = collect_leader_history_production_source(
            self.context,
            replace(
                bundle,
                series_by_symbol={**bundle.series_by_symbol, symbol: stale},
            ),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_calendar_window_mismatch_cannot_enter_history_source(self):
        bundle = self.bundle()
        calendar = replace(
            bundle.calendar_evidence,
            trading_dates=bundle.expected_trade_dates[:-1],
        )

        source = collect_leader_history_production_source(
            self.context,
            replace(bundle, calendar_evidence=calendar),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)

    def test_source_failure_is_stable_and_does_not_expose_exception(self):
        source = collect_leader_history_production_source(
            self.context,
            object(),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(source.payload)
        self.assertNotIn("Traceback", repr(source.to_evidence()))

    def test_series_fetch_deduplicates_full_universe_and_keeps_contract(self):
        dates = self.entries[0].query.expected_trade_dates
        calls = []

        def requester(query_symbol):
            calls.append(query_symbol)
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates)
            ]
            key = query_symbol
            field = "day" if query_symbol == "sh000001" else "qfqday"
            return {"code": 0, "data": {key: {field: rows}}}

        batch = fetch_leader_history_series_batch(
            ("000001", "000001", "sh000001"),
            dates,
            requester=requester,
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(tuple(sorted(batch.series_by_symbol)), (
            "000001",
            "sh000001",
        ))
        self.assertEqual(tuple(sorted(calls)), ("sh000001", "sz000001"))
        self.assertEqual(
            batch.series_by_symbol["sh000001"].adjustment_basis,
            HistoryAdjustmentBasis.CONTINUOUS_INDEX,
        )

    def test_default_requester_uses_active_tencent_history_endpoint(self):
        payload = {"code": 0, "data": {"sz000001": {"qfqday": []}}}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class Session:
            trust_env = True

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def get(self, url, **_):
                if not url.endswith("/newfqkline/get"):
                    raise AssertionError("retired_tencent_history_endpoint")
                return Response()

        with patch(
            "radar.leader_history_production_collector.requests.Session",
            return_value=Session(),
        ):
            result = _default_history_requester("sz000001")

        self.assertEqual(result, payload)

    def test_default_requester_only_aliases_exact_raw_equivalent_qfq_day(self):
        qfq_rows = [
            ["2026-07-27", "10", "11", "12", "9", "100"],
            ["2026-07-28", "11", "12", "13", "10", "110"],
        ]

        def request_with(raw_rows):
            class Response:
                def __init__(self, rows):
                    self.rows = rows

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "code": 0,
                        "data": {"sh688031": {"day": self.rows}},
                    }

            class Session:
                trust_env = True

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return None

                def get(self, url, *, params, **_):
                    if not url.endswith("/newfqkline/get"):
                        raise AssertionError("retired_tencent_history_endpoint")
                    rows = (
                        qfq_rows
                        if params["param"].endswith(",qfq")
                        else raw_rows
                    )
                    return Response(rows)

            with patch(
                "radar.leader_history_production_collector.requests.Session",
                return_value=Session(),
            ):
                return _default_history_requester("sh688031")

        verified = request_with(qfq_rows)
        mismatched = request_with([
            ["2026-07-27", "10", "10.5", "12", "9", "100"],
            qfq_rows[1],
        ])

        self.assertEqual(
            verified["data"]["sh688031"].get("qfqday"),
            qfq_rows,
        )
        self.assertEqual(
            verified["data"]["sh688031"].get(
                "qfqDayEquivalenceContractId"
            ),
            "tencent-qfq-day-exact-equivalence-v1",
        )
        self.assertNotIn("qfqday", mismatched["data"]["sh688031"])

    def test_default_tencent_equivalence_ignores_only_current_incomplete_day(self):
        current_day = self.context.as_of.date()
        completed_day = current_day - timedelta(days=1)
        qfq_rows = [
            [completed_day.isoformat(), "10", "11", "12", "9", "100"],
            [current_day.isoformat(), "11", "12", "13", "10", "110"],
        ]
        raw_rows = [
            qfq_rows[0],
            [current_day.isoformat(), "11", "12.1", "13", "10", "110"],
        ]

        class Response:
            def __init__(self, rows):
                self.rows = rows

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "code": 0,
                    "data": {"sh688382": {"day": self.rows}},
                }

        class Session:
            trust_env = True

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def get(self, _url, *, params, **_):
                return Response(
                    qfq_rows
                    if params["param"].endswith(",qfq")
                    else raw_rows
                )

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = self.context.as_of
                return value.astimezone(tz) if tz is not None else value

        with (
            patch(
                "radar.leader_history_production_collector.requests.Session",
                return_value=Session(),
            ),
            patch(
                "radar.leader_history_production_collector.datetime",
                FrozenDateTime,
            )
        ):
            result = _default_history_requester("sh688382")

        self.assertEqual(
            result["data"]["sh688382"].get("qfqday"),
            qfq_rows,
        )
        self.assertEqual(
            result["data"]["sh688382"].get(
                "qfqDayEquivalenceContractId"
            ),
            "tencent-qfq-completed-day-exact-equivalence-v1",
        )

    def test_default_eastmoney_fallback_ignores_environment_proxy(self):
        dates = self.entries[0].query.expected_trade_dates

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "klines": [
                            "2026-07-27,10,11,12,9,100,1000,3,1,0.1,2",
                            "2026-07-28,11,12,13,10,110,1200,3,1,0.1,2",
                        ]
                    }
                }

        class Session:
            trust_env = True

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def get(self, url, *, params, timeout, headers):
                if self.trust_env:
                    raise AssertionError("eastmoney_environment_proxy_used")
                if (
                    url
                    != "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                    or params.get("secid") != "1.688031"
                    or params.get("fqt") != "1"
                    or params.get("beg") != dates[0].strftime("%Y%m%d")
                    or params.get("end") != dates[-1].strftime("%Y%m%d")
                    or timeout != 8
                    or not headers.get("User-Agent")
                ):
                    raise AssertionError("eastmoney_qfq_request_unverified")
                return Response()

        with (
            patch(
                "radar.leader_history_production_collector.requests.Session",
                return_value=Session(),
            ),
            patch(
                "akshare.stock_zh_a_hist",
                return_value=SimpleNamespace(
                    to_dict=lambda **_: [],
                ),
            ),
        ):
            rows = _default_eastmoney_qfq_requester(
                "688031",
                dates[0],
                dates[-1],
            )

        self.assertEqual(rows, (
            {"日期": "2026-07-27", "股票代码": "688031", "收盘": "11"},
            {"日期": "2026-07-28", "股票代码": "688031", "收盘": "12"},
        ))

    def test_tencent_missing_qfq_rows_uses_explicit_eastmoney_qfq_fallback(self):
        dates = self.entries[0].query.expected_trade_dates
        fallback_calls = []

        def requester(query_symbol):
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates)
            ]
            if query_symbol == "sh000001":
                return {"code": 0, "data": {query_symbol: {"day": rows}}}
            return {"code": 0, "data": {query_symbol: {"day": rows}}}

        def fallback(symbol, start_date, end_date):
            fallback_calls.append((symbol, start_date, end_date))
            return tuple({
                "日期": day,
                "股票代码": symbol,
                "收盘": 100.0 + index,
            } for index, day in enumerate(dates))

        try:
            batch = fetch_leader_history_series_batch(
                ("000001", "sh000001"),
                dates,
                requester=requester,
                fallback_requester=fallback,
                clock=lambda: self.context.as_of,
            )
        except TypeError as exc:
            self.fail(f"verified qfq fallback unsupported: {exc}")

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(batch.failure_count, 0)
        self.assertEqual(
            batch.series_by_symbol["000001"].source_contract_id,
            "eastmoney-qfq-daily-history-v1",
        )
        self.assertEqual(
            batch.series_by_symbol["000001"].source_url,
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        )
        self.assertEqual(
            fallback_calls,
            [("000001", dates[0], dates[-1])],
        )

    def test_tencent_request_failure_uses_qfq_fallback_for_security(self):
        dates = self.entries[0].query.expected_trade_dates
        fallback_calls = []

        def requester(query_symbol):
            if query_symbol == "sz000001":
                raise TimeoutError("bounded primary failure")
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates)
            ]
            return {"code": 0, "data": {query_symbol: {"day": rows}}}

        def fallback(symbol, start_date, end_date):
            fallback_calls.append((symbol, start_date, end_date))
            return tuple({
                "日期": day,
                "股票代码": symbol,
                "收盘": 100.0 + index,
            } for index, day in enumerate(dates))

        batch = fetch_leader_history_series_batch(
            ("000001", "sh000001"),
            dates,
            requester=requester,
            fallback_requester=fallback,
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(batch.failure_count, 0)
        self.assertEqual(
            batch.series_by_symbol["000001"].source_contract_id,
            "eastmoney-qfq-daily-history-v1",
        )
        self.assertEqual(
            fallback_calls,
            [("000001", dates[0], dates[-1])],
        )

    def test_incomplete_primary_and_fallback_series_fail_closed(self):
        dates = self.entries[0].query.expected_trade_dates

        def requester(query_symbol):
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates[:-1])
            ]
            return {"code": 0, "data": {query_symbol: {"qfqday": rows}}}

        def fallback(symbol, _start_date, _end_date):
            return tuple({
                "日期": day,
                "股票代码": symbol,
                "收盘": 100.0 + index,
            } for index, day in enumerate(dates[:-1]))

        batch = fetch_leader_history_series_batch(
            ("000001",),
            dates,
            requester=requester,
            fallback_requester=fallback,
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "source_failed")
        self.assertEqual(batch.series_by_symbol, {})
        self.assertEqual(batch.failure_symbols, ("000001",))

    def test_verified_non_trading_gap_is_zero_return_aligned_not_failed(self):
        dates = self.entries[0].query.expected_trade_dates
        missing_day = dates[10]
        primary_rows = [
            [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
            for index, day in enumerate(dates)
            if day != missing_day
        ]
        presence_calls = []

        def requester(query_symbol):
            return {
                "code": 0,
                "data": {query_symbol: {"qfqday": primary_rows}},
            }

        def presence_loader(
            gaps,
            *,
            expected_trade_dates,
            terminal_non_trading_symbols,
            requester,
        ):
            presence_calls.append((
                gaps,
                expected_trade_dates,
                terminal_non_trading_symbols,
                requester("sz000001"),
            ))
            return HistoricalTradingPresenceBatch(
                verified_trading_dates_by_symbol={"000001": ()},
                verified_non_trading_dates_by_symbol={
                    "000001": (missing_day,),
                },
                source_hashes_by_symbol={"000001": "sha256:" + "d" * 64},
                source_contract_ids_by_symbol={
                    "000001": TRADING_PRESENCE_SOURCE_CONTRACT_ID,
                },
                source_status="ready",
                requested_count=1,
                failure_count=0,
            )

        batch = fetch_leader_history_series_batch(
            ("000001",),
            dates,
            requester=requester,
            fallback_requester=lambda *_args: (),
            presence_loader=presence_loader,
            independent_presence_requester=lambda _symbol: (
                dates[0] - timedelta(days=1),
                dates[-1] + timedelta(days=1),
            ),
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(batch.failure_count, 0)
        series = batch.series_by_symbol["000001"]
        self.assertEqual(
            tuple(point.trade_date for point in series.points),
            dates,
        )
        self.assertEqual(
            series.points[10].close,
            series.points[9].close,
        )
        self.assertEqual(
            series.verified_non_trading_dates,
            (missing_day,),
        )
        self.assertEqual(
            series.trading_presence_source_contract_id,
            (
                "tencent-qfq-daily-trading-presence-v1+"
                "sina-daily-trading-presence-v1"
            ),
        )
        self.assertEqual(
            series.upstream_content_sha256,
            "sha256:" + __import__("hashlib").sha256(
                json.dumps(
                    {
                        "code": 0,
                        "data": {"sz000001": {"qfqday": primary_rows}},
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(len(presence_calls), 1)
        self.assertEqual(
            presence_calls[0][:3],
            (
                {"000001": (missing_day,)},
                dates,
                (),
            ),
        )

    def test_past_terminal_gap_accepts_primary_recovery_after_independent_absence(self):
        dates = self.entries[0].query.expected_trade_dates
        missing_day = dates[-1]
        recovery_day = missing_day + timedelta(days=1)
        primary_rows = [
            [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
            for index, day in enumerate(dates[:-1])
        ] + [[recovery_day.isoformat(), "1", "150", "1", "1", "1"]]

        def requester(query_symbol):
            return {
                "code": 0,
                "data": {query_symbol: {"qfqday": primary_rows}},
            }

        def presence_loader(gaps, **_kwargs):
            return HistoricalTradingPresenceBatch(
                verified_trading_dates_by_symbol={"000001": ()},
                verified_non_trading_dates_by_symbol={
                    "000001": (missing_day,),
                },
                source_hashes_by_symbol={"000001": "sha256:" + "d" * 64},
                source_contract_ids_by_symbol={
                    "000001": TRADING_PRESENCE_SOURCE_CONTRACT_ID,
                },
                source_status="ready",
                requested_count=1,
                failure_count=0,
            )

        batch = fetch_leader_history_series_batch(
            ("000001",),
            dates,
            requester=requester,
            fallback_requester=lambda *_args: (),
            presence_loader=presence_loader,
            independent_presence_requester=lambda _symbol: (
                dates[0] - timedelta(days=1),
                dates[-2],
            ),
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(batch.failure_count, 0)
        series = batch.series_by_symbol["000001"]
        self.assertEqual(
            series.verified_non_trading_dates,
            (missing_day,),
        )
        self.assertEqual(series.points[-1].close, series.points[-2].close)

    def test_independent_daily_trade_blocks_non_trading_alignment(self):
        dates = self.entries[0].query.expected_trade_dates
        missing_day = dates[10]
        rows = [
            [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
            for index, day in enumerate(dates)
            if day != missing_day
        ]

        def requester(query_symbol):
            return {"code": 0, "data": {query_symbol: {"qfqday": rows}}}

        def presence_loader(gaps, **_kwargs):
            return HistoricalTradingPresenceBatch(
                verified_trading_dates_by_symbol={"000001": ()},
                verified_non_trading_dates_by_symbol={
                    "000001": (missing_day,),
                },
                source_hashes_by_symbol={"000001": "sha256:" + "d" * 64},
                source_contract_ids_by_symbol={
                    "000001": TRADING_PRESENCE_SOURCE_CONTRACT_ID,
                },
                source_status="ready",
                requested_count=1,
                failure_count=0,
            )

        batch = fetch_leader_history_series_batch(
            ("000001",),
            dates,
            requester=requester,
            fallback_requester=lambda *_args: (),
            presence_loader=presence_loader,
            independent_presence_requester=lambda _symbol: dates,
            clock=lambda: self.context.as_of,
        )

        self.assertEqual(batch.source_status, "source_failed")
        self.assertEqual(batch.failure_symbols, ("000001",))
        self.assertEqual(batch.series_by_symbol, {})

    def test_eastmoney_fallback_requests_are_serialized(self):
        dates = self.entries[0].query.expected_trade_dates
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        def requester(query_symbol):
            return {"code": 0, "data": {query_symbol: {"day": []}}}

        def fallback(symbol, start_date, end_date):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return tuple({
                "日期": day,
                "股票代码": symbol,
                "收盘": 100.0 + index,
            } for index, day in enumerate(dates))

        batch = fetch_leader_history_series_batch(
            ("000001", "000002"),
            dates,
            requester=requester,
            fallback_requester=fallback,
            clock=lambda: self.context.as_of,
            max_workers=2,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(maximum_active, 1)

    def test_failed_batch_checkpoints_successes_and_retries_only_missing(self):
        dates = self.entries[0].query.expected_trade_dates
        first_calls = []

        def payload(query_symbol):
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates)
            ]
            field = "day" if query_symbol == "sh000001" else "qfqday"
            return {"code": 0, "data": {query_symbol: {field: rows}}}

        def first_requester(query_symbol):
            first_calls.append(query_symbol)
            if query_symbol == "sz000002":
                raise TimeoutError("bounded fixture failure")
            return payload(query_symbol)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first = fetch_leader_history_series_batch(
                ("000001", "000002", "sh000001"),
                dates,
                requester=first_requester,
                clock=lambda: self.context.as_of,
                checkpoint_dir=root,
                classification_document_sha256="a" * 64,
            )
            second_calls = []

            def second_requester(query_symbol):
                second_calls.append(query_symbol)
                return payload(query_symbol)

            second = fetch_leader_history_series_batch(
                ("000001", "000002", "sh000001"),
                dates,
                requester=second_requester,
                clock=lambda: self.context.as_of,
                checkpoint_dir=root,
                classification_document_sha256="a" * 64,
            )

            checkpoints = tuple(root.rglob("series-*.json"))
            checkpoint_payload = json.loads(
                checkpoints[0].read_text(encoding="utf-8")
            )
            self.assertEqual(first.source_status, "source_failed")
            self.assertEqual(first.series_by_symbol, {})
            self.assertEqual(first.failure_count, 1)
            self.assertEqual(first.checkpoint_saved_count, 2)
            self.assertEqual(
                tuple(sorted(first_calls)),
                ("sh000001", "sz000001", "sz000002"),
            )
            self.assertEqual(second.source_status, "ready")
            self.assertEqual(second.failure_count, 0)
            self.assertEqual(second.checkpoint_reused_count, 2)
            self.assertEqual(second.checkpoint_saved_count, 1)
            self.assertEqual(second_calls, ["sz000002"])
            self.assertEqual(len(checkpoints), 3)
            self.assertEqual(
                checkpoint_payload["scope"][
                    "classificationDocumentSha256"
                ],
                "a" * 64,
            )
            self.assertEqual(
                checkpoint_payload["scope"]["expectedTradeDates"],
                [day.isoformat() for day in dates],
            )
            self.assertEqual(
                set(checkpoint_payload["scope"]["sourceContracts"]),
                {
                    "tencent-qfq-daily-history-v1",
                    "tencent-continuous-index-daily-v1",
                    "eastmoney-qfq-daily-history-v1",
                },
            )
            self.assertTrue(all(
                os.stat(path).st_mode & 0o777 == 0o600
                for path in checkpoints
            ))

    def test_tampered_checkpoint_is_refetched_not_relabelled(self):
        dates = self.entries[0].query.expected_trade_dates

        def payload(query_symbol):
            rows = [
                [day.isoformat(), "1", str(100 + index), "1", "1", "1"]
                for index, day in enumerate(dates)
            ]
            return {"code": 0, "data": {query_symbol: {"qfqday": rows}}}

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first = fetch_leader_history_series_batch(
                ("000001",),
                dates,
                requester=payload,
                clock=lambda: self.context.as_of,
                checkpoint_dir=root,
                classification_document_sha256="b" * 64,
            )
            checkpoint = next(root.rglob("series-000001.json"))
            stored = json.loads(checkpoint.read_text(encoding="utf-8"))
            stored["payload"]["points"][0][1] = 999999.0
            checkpoint.write_text(json.dumps(stored), encoding="utf-8")
            calls = []

            def requester(query_symbol):
                calls.append(query_symbol)
                return payload(query_symbol)

            second = fetch_leader_history_series_batch(
                ("000001",),
                dates,
                requester=requester,
                clock=lambda: self.context.as_of,
                checkpoint_dir=root,
                classification_document_sha256="b" * 64,
            )

            self.assertEqual(first.source_status, "ready")
            self.assertEqual(second.source_status, "ready")
            self.assertEqual(second.checkpoint_reused_count, 0)
            self.assertEqual(second.checkpoint_saved_count, 1)
            self.assertEqual(calls, ["sz000001"])
            self.assertEqual(
                second.series_by_symbol["000001"].points[0].close,
                100.0,
            )

    def test_checkpoint_path_outside_private_tmp_fails_before_request(self):
        dates = self.entries[0].query.expected_trade_dates
        requester = Mock()

        with tempfile.TemporaryDirectory() as directory:
            batch = fetch_leader_history_series_batch(
                ("000001",),
                dates,
                requester=requester,
                clock=lambda: self.context.as_of,
                checkpoint_dir=Path(directory),
                classification_document_sha256="c" * 64,
            )

        self.assertEqual(batch.source_status, "source_unverified")
        self.assertEqual(batch.series_by_symbol, {})
        requester.assert_not_called()

    def test_series_fetch_failure_closes_batch_without_partial_delivery(self):
        dates = self.entries[0].query.expected_trade_dates

        def requester(query_symbol):
            if query_symbol == "sz000001":
                raise TimeoutError("private upstream detail")
            return {"code": 0, "data": {}}

        def fallback(*_):
            raise TimeoutError("private fallback detail")

        batch = fetch_leader_history_series_batch(
            ("000001", "sh000001"),
            dates,
            requester=requester,
            fallback_requester=fallback,
            clock=lambda: self.context.as_of,
        )
        source = collect_leader_history_production_source(
            self.context,
            batch,
        )

        self.assertEqual(batch.source_status, "source_failed")
        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
        )
        self.assertIsNone(source.payload)
        self.assertEqual(
            getattr(batch, "failure_symbols", None),
            ("000001", "sh000001"),
        )
        self.assertNotIn("private upstream detail", repr(batch.to_evidence()))
        self.assertNotIn("private fallback detail", repr(batch.to_evidence()))

    def test_default_batch_is_data_only_and_does_not_call_network(self):
        self.assertEqual(
            LeaderHistoryProductionFrozenBatch(
                expected_trade_dates=(),
                memberships_by_industry={},
                series_by_symbol={},
            ).to_evidence()["returnedCount"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
