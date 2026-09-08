import json
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import radar.sources.leader_tradability_public_poc as public_poc_module
from radar.contracts import QuoteSnapshot, QuoteTradingStatus
from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
    build_leader_tradability_features,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.sources.leader_tradability_public_poc import (
    PublicCompositePocStatus,
    PublicCompositeTradabilityQuery,
    PublicQuoteBatchEvidence,
    PublicSecurityContext,
    PublicSourceKind,
    PublicTradingCalendarEvidence,
    PublicTradabilityObservation,
    build_public_tradability_runtime_inputs,
    quote_batch_content_sha256,
    run_public_composite_tradability_poc,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PublicCompositeTradabilityPocTests(unittest.TestCase):
    def field_candidate_payload(self):
        quote = self.quote()
        query = self.query()
        query = replace(
            query,
            quote_batch_evidence=replace(
                query.quote_batch_evidence,
                content_sha256=quote_batch_content_sha256(
                    batch_id=query.quote_batch_evidence.batch_id,
                    quotes=(quote,),
                ),
            ),
        )
        official = self.complete_official()
        aggregator = self.aggregator(
            lifecycle_status=SecurityLifecycleStatus.NORMAL,
            trading_status=TradingSessionStatus.TRADING,
            special_session=PriceLimitSpecialSession.NONE,
        )
        report = run_public_composite_tradability_poc(
            query=query,
            quotes=(quote,),
            official_observations=(official,),
            aggregator_observations=(aggregator,),
            executed=True,
        )
        return query, (quote,), (official,), (aggregator,), report

    def test_field_candidate_builds_replayed_runtime_input(self):
        query, quotes, official, aggregator, report = (
            self.field_candidate_payload()
        )

        result = build_public_tradability_runtime_inputs(
            query=query,
            quotes=quotes,
            official_observations=official,
            aggregator_observations=aggregator,
            report=report,
        )

        self.assertEqual(result.status.value, "ready")
        self.assertEqual(tuple(result.inputs_by_symbol), ("000725",))
        feature_result = build_leader_tradability_features(
            result.inputs_by_symbol["000725"]
        )
        self.assertEqual(feature_result.status.value, "ready")
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_sina_quote_status_crosschecks_lifecycle_and_trading(self):
        quote = self.quote()
        official = self.complete_official()
        aggregator = self.aggregator(
            source_contract_id="sina-public-quote-status-v1",
            source_name="新浪财经公开行情",
            source_url="https://finance.sina.com.cn/realstock/",
            upstream_source_name="新浪财经A股实时行情",
            upstream_source_url="https://hq.sinajs.cn/",
            upstream_document_id="sina-quote-status-20260803T095930",
            lifecycle_status=SecurityLifecycleStatus.NORMAL,
            trading_status=TradingSessionStatus.TRADING,
            special_session=None,
        )

        report = self.run_poc(
            quotes=(quote,),
            official_observations=(official,),
            aggregator_observations=(aggregator,),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        self.assertEqual(report.status, PublicCompositePocStatus.NOT_RUN)
        self.assertNotIn("public_source_contract_mismatch", report.reasons)
        self.assertNotIn("public_aggregator_upstream_untrusted", report.reasons)
        self.assertNotIn(
            "public_trading_status_crosscheck_missing",
            report.reasons,
        )
        self.assertNotIn(
            "public_special_session_crosscheck_missing",
            report.reasons,
        )

    def test_runtime_input_rejects_forged_or_partial_report(self):
        query, quotes, official, aggregator, report = (
            self.field_candidate_payload()
        )
        forged = replace(report, reasons=("forged",))
        forged_result = build_public_tradability_runtime_inputs(
            query=query,
            quotes=quotes,
            official_observations=official,
            aggregator_observations=aggregator,
            report=forged,
        )
        partial = run_public_composite_tradability_poc(
            query=query,
            quotes=quotes,
            official_observations=(),
            aggregator_observations=aggregator,
            executed=True,
        )
        partial_result = build_public_tradability_runtime_inputs(
            query=query,
            quotes=quotes,
            official_observations=(),
            aggregator_observations=aggregator,
            report=partial,
        )

        self.assertEqual(forged_result.status.value, "source_unverified")
        self.assertEqual(
            forged_result.reasons,
            ("public_runtime_input_report_replay_mismatch",),
        )
        self.assertEqual(forged_result.inputs_by_symbol, {})
        self.assertNotEqual(partial_result.status.value, "ready")
        self.assertEqual(partial_result.inputs_by_symbol, {})

    def test_beijing_exchange_symbol_is_outside_stage6_scope(self):
        query = self.query()
        result = self.run_poc(
            query=replace(
                query,
                securities=(
                    replace(query.securities[0], symbol="920023"),
                ),
            ),
        )

        self.assertEqual(
            result.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_query_symbol_out_of_scope",
            result.reasons,
        )

    def test_static_identity_accepts_verified_capture_without_source_time(self):
        query = self.query()
        security = replace(
            query.securities[0],
            identity_source_time=None,
        )

        result = self.run_poc(
            query=replace(query, securities=(security,)),
        )

        self.assertNotIn(
            "public_query_identity_timezone_missing",
            result.reasons,
        )

    def test_static_identity_still_requires_aware_fetch_time(self):
        query = self.query()
        security = replace(
            query.securities[0],
            identity_source_time=None,
            identity_fetched_at=None,
        )

        result = self.run_poc(
            query=replace(query, securities=(security,)),
        )

        self.assertIn(
            "public_query_identity_timezone_missing",
            result.reasons,
        )

    def query(self):
        return PublicCompositeTradabilityQuery(
            trading_date=date(2026, 8, 3),
            as_of=datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ),
            securities=(
                PublicSecurityContext(
                    symbol="000725",
                    exchange="szse",
                    board="主板",
                    listing_date=date(2001, 1, 12),
                    identity_source_contract_id=(
                        "szse-public-security-list-v1"
                    ),
                    identity_source_name="深圳证券交易所",
                    identity_source_url=(
                        "https://www.szse.cn/market/product/stock/list/"
                    ),
                    identity_document_id="szse-security-list-20260803",
                    identity_source_time=datetime(
                        2026, 8, 3, 8, 30, tzinfo=SHANGHAI_TZ
                    ),
                    identity_fetched_at=datetime(
                        2026, 8, 3, 8, 31, tzinfo=SHANGHAI_TZ
                    ),
                    identity_content_sha256="sha256:" + "c" * 64,
                ),
            ),
            trading_calendars=(PublicTradingCalendarEvidence(
                exchange="szse",
                trading_dates=(
                    date(2026, 7, 27),
                    date(2026, 7, 28),
                    date(2026, 7, 29),
                    date(2026, 7, 30),
                    date(2026, 7, 31),
                    date(2026, 8, 3),
                ),
                source_contract_id=(
                    "szse-public-trading-calendar-v1"
                ),
                source_name="深圳证券交易所",
                source_url=(
                    "https://www.szse.cn/market/stockdealdata/overview/"
                ),
                document_id="szse-trading-calendar-20260803",
                source_time=datetime(
                    2026, 8, 3, 8, 20, tzinfo=SHANGHAI_TZ
                ),
                fetched_at=datetime(
                    2026, 8, 3, 8, 21, tzinfo=SHANGHAI_TZ
                ),
                content_sha256="sha256:" + "d" * 64,
            ),),
            quote_batch_evidence=PublicQuoteBatchEvidence(
                source_contract_id="tencent-quote-snapshot-v1",
                source_name="腾讯财经",
                source_url="https://qt.gtimg.cn/",
                batch_id="tencent-quote-20260803T095945",
                content_sha256="sha256:" + "e" * 64,
            ),
        )

    def test_composite_replay_accepts_bounded_production_scope(self):
        query = self.query()
        securities = tuple(
            replace(
                query.securities[0],
                symbol=f"{index:06d}",
                identity_document_id=f"szse-security-{index:06d}",
            )
            for index in range(1, 386)
        )

        reasons = public_poc_module._query_reasons(
            replace(query, securities=securities)
        )

        self.assertNotIn("public_query_sample_count_invalid", reasons)

    def official(self, **overrides):
        values = {
            "symbol": "000725",
            "exchange": "szse",
            "board": "主板",
            "trading_date": date(2026, 8, 3),
            "source_kind": PublicSourceKind.EXCHANGE_OFFICIAL,
            "source_contract_id": "szse-public-status-v1",
            "source_name": "深圳证券交易所",
            "source_url": "https://www.szse.cn/disclosure/notice/temp/",
            "document_id": "szse-status-20260803",
            "source_time": datetime(
                2026, 8, 3, 9, 0, tzinfo=SHANGHAI_TZ
            ),
            "fetched_at": datetime(
                2026, 8, 3, 9, 1, tzinfo=SHANGHAI_TZ
            ),
            "content_sha256": "sha256:" + "a" * 64,
            "effective_from": date(2026, 8, 3),
            "effective_until": date(2026, 8, 3),
        }
        values.update(overrides)
        return PublicTradabilityObservation(**values)

    def aggregator(self, **overrides):
        values = {
            "source_kind": PublicSourceKind.PUBLIC_AGGREGATOR,
            "source_contract_id": "akshare-public-status-v1",
            "source_name": "AKShare公开数据聚合",
            "source_url": "https://akshare.akfamily.xyz/data/stock/stock.html",
            "document_id": "akshare-status-20260803",
            "content_sha256": "sha256:" + "b" * 64,
            "source_time": datetime(
                2026, 8, 3, 9, 59, 30, tzinfo=SHANGHAI_TZ
            ),
            "fetched_at": datetime(
                2026, 8, 3, 9, 59, 35, tzinfo=SHANGHAI_TZ
            ),
            "upstream_source_name": "东方财富公开页面",
            "upstream_source_url": (
                "https://data.eastmoney.com/tfpxx/"
            ),
            "upstream_document_id": "eastmoney-status-20260803",
            "upstream_source_time": datetime(
                2026, 8, 3, 9, 59, 20, tzinfo=SHANGHAI_TZ
            ),
        }
        values.update(overrides)
        return replace(self.official(), **values)

    def complete_official(self, **overrides):
        values = {
            "source_time": datetime(
                2026, 8, 3, 9, 59, 20, tzinfo=SHANGHAI_TZ
            ),
            "fetched_at": datetime(
                2026, 8, 3, 9, 59, 30, tzinfo=SHANGHAI_TZ
            ),
            "lifecycle_status": SecurityLifecycleStatus.NORMAL,
            "trading_status": TradingSessionStatus.TRADING,
            "special_session": PriceLimitSpecialSession.NONE,
            "price_limit_mode": PriceLimitMode.BOUNDED,
            "upper_limit_price": 11.0,
            "lower_limit_price": 9.0,
        }
        values.update(overrides)
        return self.official(**values)

    def quote(self, **overrides):
        values = {
            "symbol": "000725",
            "name": "京东方A",
            "source_time": datetime(
                2026, 8, 3, 9, 59, 40, tzinfo=SHANGHAI_TZ
            ),
            "fetched_at": datetime(
                2026, 8, 3, 9, 59, 45, tzinfo=SHANGHAI_TZ
            ),
            "trading_status": None,
            "price": 10.2,
            "previous_close": 10.0,
            "open_price": 10.1,
            "high_price": 10.3,
            "low_price": 10.0,
            "upper_limit_price_source": 11.0,
            "lower_limit_price_source": 9.0,
        }
        values.update(overrides)
        return QuoteSnapshot(**values)

    def run_poc(self, **overrides):
        preserve_quote_batch_digest = overrides.pop(
            "preserve_quote_batch_digest",
            False,
        )
        values = {
            "query": self.query(),
            "quotes": (),
            "official_observations": (),
            "aggregator_observations": (),
            "executed": True,
        }
        values.update(overrides)
        quotes = tuple(values["quotes"])
        query = values["query"]
        if (
            quotes
            and query.quote_batch_evidence is not None
            and not preserve_quote_batch_digest
        ):
            evidence = query.quote_batch_evidence
            query = replace(
                query,
                quote_batch_evidence=replace(
                    evidence,
                    content_sha256=quote_batch_content_sha256(
                        batch_id=evidence.batch_id,
                        quotes=quotes,
                    ),
                ),
            )
        values["quotes"] = quotes
        values["query"] = query
        return run_public_composite_tradability_poc(**values)

    def test_not_executed_report_is_nonformal_and_redacted(self):
        report = run_public_composite_tradability_poc(
            query=self.query(),
            quotes=(),
            official_observations=(),
            aggregator_observations=(),
            executed=False,
        )

        self.assertEqual(report.status, PublicCompositePocStatus.NOT_RUN)
        self.assertEqual(report.real_poc_status, "not_run")
        self.assertFalse(report.formal_score_ready)
        self.assertFalse(report.formal_gate_ready)
        self.assertFalse(report.formal_usable)
        self.assertFalse(report.state_transition_allowed)
        evidence = report.to_evidence()
        self.assertNotIn("records", evidence)
        self.assertNotIn("quotes", evidence)
        self.assertEqual(evidence["expectedCount"], 1)

    def test_attempted_input_without_rows_is_no_data_but_not_real_poc(self):
        report = self.run_poc()

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.NO_DATA,
        )
        self.assertEqual(report.real_poc_status, "not_run")
        self.assertEqual(report.returned_count, 0)

    def test_sensitive_source_url_blocks_batch_without_leaking_value(self):
        report = self.run_poc(
            official_observations=(
                self.official(
                    source_url=(
                        "https://www.szse.cn/status?token=secret-value"
                    )
                ),
            ),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_source_url_sensitive", report.reasons)
        self.assertNotIn("secret-value", str(report.to_evidence()))

    def test_duplicate_observation_blocks_batch(self):
        observation = self.official()

        report = self.run_poc(
            official_observations=(observation, observation),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_batch_duplicate_observation", report.reasons)

    def test_wrong_trading_date_blocks_batch(self):
        report = self.run_poc(
            official_observations=(
                self.official(trading_date=date(2026, 7, 31)),
            ),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_batch_trading_date_mismatch", report.reasons)

    def test_duplicate_query_symbol_and_naive_as_of_are_blocked(self):
        security = self.query().securities[0]
        query = replace(
            self.query(),
            as_of=datetime(2026, 8, 3, 10, 0),
            securities=(security, security),
        )

        report = self.run_poc(query=query)

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_query_as_of_timezone_missing", report.reasons)
        self.assertIn("public_query_duplicate_symbol", report.reasons)

    def test_contract_objects_are_frozen(self):
        observation = self.official()

        with self.assertRaises(FrozenInstanceError):
            observation.symbol = "000001"

    def test_quote_without_explicit_status_does_not_infer_trading(self):
        report = self.run_poc(quotes=(self.quote(),))

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn("public_trading_status_missing", report.reasons)
        self.assertIsNone(report.records[0].trading_status)

    def test_complete_official_and_quote_are_field_candidate_only(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        self.assertEqual(report.real_poc_status, "not_run")
        self.assertEqual(report.returned_count, 1)
        self.assertTrue(all(
            value == 1.0
            for value in report.field_coverage.values()
        ))
        self.assertFalse(report.formal_usable)

    def test_source_clock_skew_within_contract_tolerance_is_accepted(self):
        fetched_at = datetime(
            2026, 8, 3, 9, 59, 40, tzinfo=SHANGHAI_TZ
        )
        source_time = datetime(
            2026, 8, 3, 9, 59, 44, tzinfo=SHANGHAI_TZ
        )
        report = self.run_poc(
            quotes=(self.quote(
                source_time=source_time,
                fetched_at=fetched_at,
            ),),
            official_observations=(self.complete_official(
                source_time=source_time,
                fetched_at=fetched_at,
            ),),
        )

        self.assertNotIn("public_quote_fetch_before_source", report.reasons)
        self.assertNotIn("public_source_fetch_before_source", report.reasons)

    def test_exchange_bucket_skew_does_not_relax_quote_clock_contract(self):
        fetched_at = datetime(
            2026, 8, 3, 9, 59, 40, tzinfo=SHANGHAI_TZ
        )
        source_time = datetime(
            2026, 8, 3, 9, 59, 46, tzinfo=SHANGHAI_TZ
        )
        report = self.run_poc(
            quotes=(self.quote(
                source_time=source_time,
                fetched_at=fetched_at,
            ),),
            official_observations=(self.complete_official(
                source_time=source_time,
                fetched_at=fetched_at,
            ),),
        )

        self.assertIn("public_quote_fetch_before_source", report.reasons)
        self.assertNotIn("public_source_fetch_before_source", report.reasons)

    def test_exchange_source_clock_skew_beyond_ten_seconds_is_rejected(self):
        fetched_at = datetime(
            2026, 8, 3, 9, 59, 40, tzinfo=SHANGHAI_TZ
        )
        source_time = datetime(
            2026, 8, 3, 9, 59, 51, tzinfo=SHANGHAI_TZ
        )
        report = self.run_poc(
            official_observations=(self.complete_official(
                source_time=source_time,
                fetched_at=fetched_at,
            ),),
        )

        self.assertIn("public_source_fetch_before_source", report.reasons)

    def test_explicit_suspension_conflict_blocks_record(self):
        report = self.run_poc(
            quotes=(self.quote(
                trading_status=QuoteTradingStatus.SUSPENDED,
            ),),
            official_observations=(self.complete_official(),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_trading_status_conflict", report.reasons)

    def test_official_and_aggregator_lifecycle_conflict_blocks(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.ST,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_lifecycle_status_conflict", report.reasons)

    def test_stale_quote_keeps_batch_partial(self):
        report = self.run_poc(
            quotes=(self.quote(
                source_time=datetime(
                    2026, 8, 3, 9, 58, 0, tzinfo=SHANGHAI_TZ
                ),
            ),),
            official_observations=(self.complete_official(),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn("public_quote_stale", report.reasons)

    def test_lunch_break_keeps_the_1130_exchange_snapshot_current(self):
        lunch_time = datetime(
            2026, 8, 3, 12, 42, tzinfo=SHANGHAI_TZ
        )
        boundary = datetime(
            2026, 8, 3, 11, 30, tzinfo=SHANGHAI_TZ
        )
        report = self.run_poc(
            query=replace(self.query(), as_of=lunch_time),
            quotes=(self.quote(
                source_time=boundary,
                fetched_at=boundary,
            ),),
            official_observations=(self.complete_official(
                source_time=boundary,
                fetched_at=boundary,
            ),),
            aggregator_observations=(self.aggregator(
                source_time=boundary,
                fetched_at=boundary,
                upstream_source_time=boundary,
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        self.assertNotIn("public_quote_stale", report.reasons)
        self.assertNotIn("public_trading_status_stale", report.reasons)

    def test_1130_snapshot_is_stale_again_after_afternoon_open(self):
        afternoon = datetime(
            2026, 8, 3, 13, 2, tzinfo=SHANGHAI_TZ
        )
        boundary = datetime(
            2026, 8, 3, 11, 30, tzinfo=SHANGHAI_TZ
        )
        report = self.run_poc(
            query=replace(self.query(), as_of=afternoon),
            quotes=(self.quote(
                source_time=boundary,
                fetched_at=boundary,
            ),),
            official_observations=(self.complete_official(
                source_time=boundary,
                fetched_at=boundary,
            ),),
            aggregator_observations=(self.aggregator(
                source_time=boundary,
                fetched_at=boundary,
                upstream_source_time=boundary,
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn("public_quote_stale", report.reasons)
        self.assertIn("public_trading_status_stale", report.reasons)

    def test_static_suspension_evidence_does_not_use_90_second_window(self):
        aggregate = self.aggregator(
            source_contract_id="akshare-public-static-status-v1",
            source_time=datetime(
                2026, 8, 3, 8, 0, tzinfo=SHANGHAI_TZ
            ),
            fetched_at=datetime(
                2026, 8, 3, 8, 1, tzinfo=SHANGHAI_TZ
            ),
            upstream_source_time=datetime(
                2026, 8, 3, 7, 59, tzinfo=SHANGHAI_TZ
            ),
            trading_status=TradingSessionStatus.SUSPENDED,
        )

        report = self.run_poc(
            quotes=(self.quote(
                trading_status=QuoteTradingStatus.SUSPENDED,
            ),),
            aggregator_observations=(aggregate,),
        )

        self.assertEqual(
            report.records[0].trading_status,
            TradingSessionStatus.SUSPENDED,
        )
        self.assertNotIn("public_trading_status_stale", report.reasons)
        trading_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "trading_status"
        )
        aggregate_evidence = next(
            item
            for item in trading_evidence.sources
            if item.source_kind == "public_aggregator"
        )
        self.assertEqual(aggregate_evidence.freshness, "effective")

    def test_price_limit_mismatch_with_rule_blocks(self):
        report = self.run_poc(
            quotes=(self.quote(
                upper_limit_price_source=12.0,
                lower_limit_price_source=8.0,
            ),),
            official_observations=(self.complete_official(),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_price_limit_rule_conflict", report.reasons)
        evidence = {
            item.field_name: item
            for item in report.records[0].field_evidence
        }
        self.assertEqual(
            evidence["upper_limit_price"].resolution,
            "conflict",
        )
        self.assertEqual(
            evidence["lower_limit_price"].resolution,
            "conflict",
        )

    def test_missing_previous_close_is_unverified_not_conflict(self):
        report = self.run_poc(
            quotes=(self.quote(previous_close=None),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn(
            "public_price_limit_rule_unavailable",
            report.reasons,
        )
        self.assertNotIn(
            "public_price_limit_rule_conflict",
            report.reasons,
        )
        upper_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "upper_limit_price"
        )
        self.assertEqual(upper_evidence.resolution, "unverified")

    def test_aggregator_only_complete_record_stays_partial(self):
        aggregate = self.aggregator(
            source_time=datetime(
                2026, 8, 3, 9, 59, 20, tzinfo=SHANGHAI_TZ
            ),
            fetched_at=datetime(
                2026, 8, 3, 9, 59, 30, tzinfo=SHANGHAI_TZ
            ),
            lifecycle_status=SecurityLifecycleStatus.NORMAL,
            trading_status=TradingSessionStatus.TRADING,
            special_session=PriceLimitSpecialSession.NONE,
        )

        report = self.run_poc(
            quotes=(self.quote(),),
            aggregator_observations=(aggregate,),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn("public_official_observation_missing", report.reasons)

    def test_no_limit_special_day_accepts_missing_limit_prices(self):
        query = replace(
            self.query(),
            securities=(replace(
                self.query().securities[0],
                listing_date=date(2026, 7, 30),
            ),),
        )
        official = self.complete_official(
            special_session=PriceLimitSpecialSession.NONE,
            price_limit_mode=PriceLimitMode.NO_LIMIT,
            upper_limit_price=None,
            lower_limit_price=None,
        )

        report = self.run_poc(
            query=query,
            quotes=(self.quote(
                previous_close=None,
                upper_limit_price_source=None,
                lower_limit_price_source=None,
            ),),
            official_observations=(official,),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )

    def test_public_module_does_not_import_rqdata_adapter(self):
        source = Path(public_poc_module.__file__).read_text(
            encoding="utf-8"
        )

        self.assertNotIn("leader_tradability_rqdata_poc", source)

    def test_observation_repr_and_report_evidence_are_redacted(self):
        observation = self.complete_official(
            source_url="https://example.com/status?token=secret-value",
        )
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(observation,),
        )

        self.assertNotIn("secret-value", repr(observation))
        evidence_text = json.dumps(
            report.to_evidence(),
            ensure_ascii=False,
        ).lower()
        self.assertNotIn("records", report.to_evidence())
        self.assertNotIn("token", evidence_text)
        self.assertNotIn("secret-value", evidence_text)

    def test_invalid_observation_enum_is_blocked(self):
        report = self.run_poc(
            official_observations=(self.official(
                lifecycle_status="normal",
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_lifecycle_status_invalid", report.reasons)

    def test_valid_unknown_enums_remain_partial(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(
                lifecycle_status=SecurityLifecycleStatus.UNKNOWN,
                trading_status=TradingSessionStatus.UNKNOWN,
                price_limit_mode=PriceLimitMode.UNKNOWN,
            ),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIsNone(report.records[0].lifecycle_status)
        self.assertIsNone(report.records[0].trading_status)
        self.assertIn("public_lifecycle_status_unknown", report.reasons)

    def test_future_quote_time_blocks_batch(self):
        report = self.run_poc(
            quotes=(self.quote(
                source_time=datetime(
                    2026, 8, 3, 10, 0, 6, tzinfo=SHANGHAI_TZ
                ),
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_quote_future_time", report.reasons)

    def test_quote_batch_cannot_impersonate_tencent_source(self):
        query = replace(
            self.query(),
            quote_batch_evidence=replace(
                self.query().quote_batch_evidence,
                source_url="https://example.net/quote",
            ),
        )
        report = self.run_poc(
            query=query,
            quotes=(self.quote(),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_quote_batch_contract_mismatch",
            report.reasons,
        )

    def test_quote_row_source_and_batch_digest_are_bound(self):
        wrong_source = self.run_poc(
            quotes=(self.quote(source="non_tencent_source"),),
        )
        wrong_digest = self.run_poc(
            quotes=(self.quote(),),
            preserve_quote_batch_digest=True,
        )

        self.assertEqual(
            wrong_source.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_quote_source_mismatch", wrong_source.reasons)
        self.assertEqual(
            wrong_digest.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_quote_batch_digest_mismatch",
            wrong_digest.reasons,
        )

    def test_unregistered_official_domain_is_blocked(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(
                source_url="https://example.net/fake/status",
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_source_contract_mismatch", report.reasons)

    def test_same_kind_multiple_observations_cannot_overwrite(self):
        normal = self.complete_official()
        st = self.complete_official(
            source_contract_id="szse-public-announcement-v1",
            document_id="szse-risk-warning-20260803",
            lifecycle_status=SecurityLifecycleStatus.ST,
        )

        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(normal, st),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_batch_multiple_kind_observations", report.reasons)

    def test_static_observation_contract_has_effective_window(self):
        names = {
            item.name for item in fields(PublicTradabilityObservation)
        }

        self.assertIn("effective_from", names)
        self.assertIn("effective_until", names)

    def test_static_observation_outside_effective_window_is_blocked(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(
                effective_from=date(2026, 7, 1),
                effective_until=date(2026, 7, 31),
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_source_effective_window_inapplicable",
            report.reasons,
        )

    def test_listing_days_require_bound_exchange_identity_evidence(self):
        security = replace(
            self.query().securities[0],
            identity_source_url="https://example.net/security-list",
        )
        report = self.run_poc(
            query=replace(self.query(), securities=(security,)),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_query_identity_contract_mismatch",
            report.reasons,
        )

    def test_status_contract_cannot_impersonate_security_master(self):
        security = replace(
            self.query().securities[0],
            identity_source_contract_id="szse-public-status-v1",
        )
        report = self.run_poc(
            query=replace(self.query(), securities=(security,)),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_query_identity_contract_mismatch",
            report.reasons,
        )

    def test_listed_days_are_derived_from_calendar_not_declared(self):
        names = {item.name for item in fields(PublicSecurityContext)}
        self.assertNotIn("listed_trading_day_count", names)

        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        self.assertEqual(
            report.records[0].price_limit_mode,
            PriceLimitMode.BOUNDED,
        )

    def test_aggregator_must_preserve_trusted_upstream(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                upstream_source_url="https://example.net/source",
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_aggregator_upstream_untrusted",
            report.reasons,
        )

    def test_aggregator_upstream_cannot_postdate_wrapper(self):
        report = self.run_poc(
            aggregator_observations=(self.aggregator(
                upstream_source_time=datetime(
                    2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ
                ),
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn(
            "public_aggregator_upstream_after_wrapper",
            report.reasons,
        )

    def test_stale_aggregator_upstream_cannot_crosscheck_dynamic_status(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                upstream_source_time=datetime(
                    2026, 7, 1, 9, 0, tzinfo=SHANGHAI_TZ
                ),
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.SUSPENDED,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn("public_trading_status_stale", report.reasons)
        trading_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "trading_status"
        )
        aggregate_source = next(
            item
            for item in trading_evidence.sources
            if item.source_kind == "public_aggregator"
        )
        self.assertEqual(aggregate_source.freshness, "stale")

    def test_special_session_single_source_stays_partial(self):
        report = self.run_poc(
            quotes=(self.quote(
                upper_limit_price_source=None,
                lower_limit_price_source=None,
            ),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn(
            "public_special_session_crosscheck_missing",
            report.reasons,
        )
        special_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "special_session"
        )
        self.assertEqual(special_evidence.resolution, "single_source")

    def test_bounded_quote_crosschecks_no_special_session(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        self.assertNotIn(
            "public_special_session_crosscheck_missing",
            report.reasons,
        )
        special_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "special_session"
        )
        self.assertEqual(special_evidence.resolution, "consistent")
        self.assertEqual(
            {item.source_kind for item in special_evidence.sources},
            {"exchange_official", "tencent_quote"},
        )

    def test_aggregator_cannot_replace_missing_official_primary_fields(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(
                lifecycle_status=None,
                trading_status=None,
            ),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIsNone(report.records[0].lifecycle_status)
        self.assertIsNone(report.records[0].trading_status)
        self.assertIn(
            "public_lifecycle_status_official_missing",
            report.reasons,
        )

    def test_aggregator_cannot_create_special_no_limit_day(self):
        report = self.run_poc(
            quotes=(self.quote(
                previous_close=None,
                upper_limit_price_source=None,
                lower_limit_price_source=None,
            ),),
            official_observations=(self.complete_official(
                special_session=None,
                price_limit_mode=None,
                upper_limit_price=None,
                lower_limit_price=None,
            ),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=(
                    PriceLimitSpecialSession.RELISTING_FIRST_DAY
                ),
            ),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIsNone(report.records[0].special_session)
        self.assertIsNone(report.records[0].price_limit_mode)

    def test_stale_quote_is_preserved_as_stale_audit_evidence(self):
        report = self.run_poc(
            quotes=(self.quote(source_time=datetime(
                2026, 8, 3, 9, 58, tzinfo=SHANGHAI_TZ
            )),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        upper_evidence = next(
            item
            for item in report.records[0].field_evidence
            if item.field_name == "upper_limit_price"
        )
        quote_source = next(
            item
            for item in upper_evidence.sources
            if item.source_kind == "tencent_quote"
        )
        self.assertEqual(quote_source.freshness, "stale")
        self.assertEqual(upper_evidence.resolution, "stale")

    def test_matching_risk_warning_status_is_candidate(self):
        for lifecycle in (
            SecurityLifecycleStatus.ST,
            SecurityLifecycleStatus.STAR_ST,
        ):
            with self.subTest(lifecycle=lifecycle.value):
                report = self.run_poc(
                    quotes=(self.quote(),),
                    official_observations=(self.complete_official(
                        lifecycle_status=lifecycle,
                    ),),
                    aggregator_observations=(self.aggregator(
                        lifecycle_status=lifecycle,
                        trading_status=TradingSessionStatus.TRADING,
                        special_session=PriceLimitSpecialSession.NONE,
                    ),),
                )

                self.assertEqual(
                    report.fixture_resolution_status,
                    PublicCompositePocStatus.FIELD_CANDIDATE,
                )
                self.assertEqual(
                    report.records[0].lifecycle_status,
                    lifecycle,
                )

    def test_matching_suspension_and_delisting_are_candidates(self):
        cases = (
            (
                SecurityLifecycleStatus.NORMAL,
                TradingSessionStatus.SUSPENDED,
                QuoteTradingStatus.SUSPENDED,
            ),
            (
                SecurityLifecycleStatus.DELISTING,
                TradingSessionStatus.ABNORMAL,
                QuoteTradingStatus.DELISTED,
            ),
        )
        for lifecycle, trading_status, quote_status in cases:
            with self.subTest(
                lifecycle=lifecycle.value,
                trading_status=trading_status.value,
            ):
                report = self.run_poc(
                    quotes=(self.quote(trading_status=quote_status),),
                    official_observations=(self.complete_official(
                        lifecycle_status=lifecycle,
                        trading_status=trading_status,
                    ),),
                    aggregator_observations=(self.aggregator(
                        lifecycle_status=lifecycle,
                        trading_status=trading_status,
                        special_session=PriceLimitSpecialSession.NONE,
                    ),),
                )

                self.assertEqual(
                    report.fixture_resolution_status,
                    PublicCompositePocStatus.FIELD_CANDIDATE,
                )

    def test_official_only_complete_record_is_partial(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.PARTIAL,
        )
        self.assertIn(
            "public_aggregator_observation_missing",
            report.reasons,
        )

    def test_fixture_resolution_is_distinct_from_real_poc_status(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        self.assertEqual(report.status, PublicCompositePocStatus.NOT_RUN)
        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.FIELD_CANDIDATE,
        )
        evidence = report.to_evidence()
        self.assertEqual(evidence["status"], "not_run")
        self.assertEqual(
            evidence["fixtureResolutionStatus"],
            "field_candidate",
        )

    def test_resolved_fields_keep_source_provenance(self):
        report = self.run_poc(
            quotes=(self.quote(),),
            official_observations=(self.complete_official(),),
            aggregator_observations=(self.aggregator(
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
            ),),
        )

        evidence_by_field = {
            item.field_name: item
            for item in report.records[0].field_evidence
        }
        self.assertEqual(
            set(evidence_by_field),
            {
                "lifecycle_status",
                "trading_status",
                "special_session",
                "price_limit_mode",
                "upper_limit_price",
                "lower_limit_price",
            },
        )
        self.assertIn(
            "szse-public-status-v1",
            evidence_by_field["lifecycle_status"].source_contract_ids,
        )
        self.assertIn(
            "akshare-public-status-v1",
            evidence_by_field["lifecycle_status"].source_contract_ids,
        )
        lifecycle_sources = evidence_by_field[
            "lifecycle_status"
        ].sources
        self.assertEqual(
            {item.observed_value for item in lifecycle_sources},
            {"normal"},
        )

    def test_trading_date_comparison_uses_shanghai_timezone(self):
        utc_as_of = datetime.fromisoformat(
            "2026-08-03T23:00:00+00:00"
        )
        report = self.run_poc(
            query=replace(self.query(), as_of=utc_as_of),
        )

        self.assertEqual(
            report.fixture_resolution_status,
            PublicCompositePocStatus.BLOCKED,
        )
        self.assertIn("public_query_as_of_date_mismatch", report.reasons)


if __name__ == "__main__":
    unittest.main()
