import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.leader_business_catalyst_manual_review import (
    apply_official_business_manual_reviews_batch,
)
from radar.leader_history_features import (
    AdjustedHistoryPoint,
    HistoryAdjustmentBasis,
)
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchStatus,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_research_single_pass_orchestration import (
    SOURCE_MISMATCH,
    LeaderResearchSinglePassInput,
    LeaderResearchSinglePassStatus,
    build_leader_research_single_pass,
)
from radar.leader_research_source_admission import (
    LEADER_RESEARCH_SOURCE_ADMISSION_CONTRACT_ID,
    LeaderResearchHistoryAdmissionEntry,
    LeaderResearchRiskAdmissionBundle,
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionStatus,
    LeaderResearchTradabilityAdmissionBundle,
    build_leader_research_source_admission,
)
from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlanInput,
    build_leader_runtime_candidate_plan,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.sources.leader_history_public_poc import (
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    PublicHistorySeries,
    TENCENT_HISTORY_URL,
    run_public_history_input_poc,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositeTradabilityQuery,
    PublicQuoteBatchEvidence,
    PublicSecurityContext,
    PublicSourceKind,
    PublicTradingCalendarEvidence,
    PublicTradabilityObservation,
    quote_batch_content_sha256,
    run_public_composite_tradability_poc,
)
from tests import (
    test_radar_leader_business_catalyst_manual_review as business_helpers,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class LeaderResearchSourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        raw = self.f6.raw_inputs()
        for quote in raw["quote_batch"].items:
            quote.previous_close = 10.0
            quote.open_price = 10.0
            quote.high_price = 10.2
            quote.low_price = 9.8
            quote.upper_limit_price_source = 11.0
            quote.lower_limit_price_source = 9.0
        self.raw = raw
        self.plan = build_leader_runtime_candidate_plan(
            LeaderRuntimeCandidatePlanInput(**raw)
        )
        self.context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        self.business = (
            business_helpers.LeaderBusinessCatalystManualReviewTests(
                methodName=(
                    "test_reviewed_batch_enters_existing_provider_"
                    "as_analysis_only"
                )
            )
        )
        self.business.setUp()

    def trading_dates(self):
        current = self.plan.as_of.date() - timedelta(days=35)
        values = []
        while len(values) < 21:
            if current.weekday() < 5:
                values.append(current)
            current += timedelta(days=1)
        return tuple(values)

    def history_payload(self, item):
        dates = self.trading_dates()
        board_symbol = (
            "sh000001" if item.symbol.startswith("6") else "sz399001"
        )
        fetched_at = self.plan.as_of - timedelta(minutes=1)

        def series(symbol, *, board=False):
            return PublicHistorySeries(
                symbol=symbol,
                source_contract_id=(
                    "tencent-continuous-index-daily-v1"
                    if board
                    else "tencent-qfq-daily-history-v1"
                ),
                source_url=TENCENT_HISTORY_URL,
                adjustment_basis=(
                    HistoryAdjustmentBasis.CONTINUOUS_INDEX
                    if board
                    else HistoryAdjustmentBasis.FORWARD_ADJUSTED
                ),
                fetched_at=fetched_at,
                content_sha256="sha256:" + "a" * 64,
                points=tuple(
                    AdjustedHistoryPoint(
                        trade_date=trade_date,
                        close=100.0 + index,
                    )
                    for index, trade_date in enumerate(dates)
                ),
            )

        query = PublicHistoryPocQuery(
            as_of=self.plan.as_of,
            expected_trade_dates=dates,
            candidate_symbol=item.symbol,
            board_index_symbol=board_symbol,
            membership=PointInTimeIndustryMembership(
                release_id=item.industry_release_id,
                source_contract_id="capco-industry-classification-v1",
                industry_code=item.industry_code,
                candidate_symbol=item.symbol,
                member_symbols=(
                    self.context.industry_constituent_symbols_by_code[
                        item.industry_code
                    ]
                ),
                published_date=dates[0] - timedelta(days=30),
                classification_start_date=dates[0] - timedelta(days=30),
                first_observed_at=datetime.combine(
                    dates[0] - timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=SHANGHAI_TZ,
                ),
                fetched_at=fetched_at,
                document_sha256="sha256:" + "b" * 64,
            ),
            series_by_symbol={
                **{
                    symbol: series(symbol)
                    for symbol in (
                        self.context.industry_constituent_symbols_by_code[
                            item.industry_code
                        ]
                    )
                },
                board_symbol: series(board_symbol, board=True),
            },
        )
        result = run_public_history_input_poc(query)
        self.assertEqual(
            result.resolution_status,
            "ready",
            result.reasons,
        )
        return query, result

    def history_entries(self):
        def entry(item):
            query, result = self.history_payload(item)
            return LeaderResearchHistoryAdmissionEntry(
                symbol=item.symbol,
                candidate_plan_id=self.plan.candidate_set_id,
                radar_run_id=self.plan.radar_run_id,
                quote_batch_id=self.plan.quote_batch_id,
                query=query,
                result=result,
            )

        return tuple(
            entry(item)
            for item in self.plan.items
        )

    def business_batch(self):
        return apply_official_business_manual_reviews_batch(
            self.business.material_batch(),
            tuple(
                business_helpers.batch_review_entry(item)
                for item in self.plan.items
            ),
        )

    def tradability_bundle(self, *, include_official=True):
        trading_date = self.plan.as_of.date()
        calendar_dates = []
        current = trading_date - timedelta(days=8)
        while current <= trading_date:
            if current.weekday() < 5:
                calendar_dates.append(current)
            current += timedelta(days=1)
        calendar_dates = tuple(calendar_dates[-6:])
        symbols = tuple(item.symbol for item in self.plan.items)
        quotes = tuple(
            self.context.quotes_by_symbol[symbol]
            for symbol in symbols
        )
        securities = tuple(
            PublicSecurityContext(
                symbol=symbol,
                exchange="szse",
                board="主板",
                listing_date=date(2000, 1, 1),
                identity_source_contract_id=(
                    "szse-public-security-list-v1"
                ),
                identity_source_name="深圳证券交易所",
                identity_source_url=(
                    "https://www.szse.cn/market/product/stock/list/"
                ),
                identity_document_id=f"szse-security-list-{symbol}",
                identity_source_time=self.plan.as_of - timedelta(days=1),
                identity_fetched_at=self.plan.as_of - timedelta(minutes=5),
                identity_content_sha256="sha256:" + "c" * 64,
            )
            for symbol in symbols
        )
        query = PublicCompositeTradabilityQuery(
            trading_date=trading_date,
            as_of=self.plan.as_of,
            securities=securities,
            trading_calendars=(PublicTradingCalendarEvidence(
                exchange="szse",
                trading_dates=calendar_dates,
                source_contract_id="szse-public-trading-calendar-v1",
                source_name="深圳证券交易所",
                source_url=(
                    "https://www.szse.cn/market/stockdealdata/overview/"
                ),
                document_id="szse-calendar-current",
                source_time=self.plan.as_of - timedelta(minutes=10),
                fetched_at=self.plan.as_of - timedelta(minutes=5),
                content_sha256="sha256:" + "d" * 64,
            ),),
            quote_batch_evidence=PublicQuoteBatchEvidence(
                source_contract_id="tencent-quote-snapshot-v1",
                source_name="腾讯财经",
                source_url="https://qt.gtimg.cn/",
                batch_id=self.plan.quote_batch_id,
                content_sha256=quote_batch_content_sha256(
                    batch_id=self.plan.quote_batch_id,
                    quotes=quotes,
                ),
            ),
        )

        def observation(symbol, *, aggregator=False):
            values = {
                "symbol": symbol,
                "exchange": "szse",
                "board": "主板",
                "trading_date": trading_date,
                "source_kind": PublicSourceKind.EXCHANGE_OFFICIAL,
                "source_contract_id": "szse-public-status-v1",
                "source_name": "深圳证券交易所",
                "source_url": (
                    "https://www.szse.cn/disclosure/notice/temp/"
                ),
                "document_id": f"szse-status-{symbol}",
                "source_time": self.plan.as_of - timedelta(seconds=20),
                "fetched_at": self.plan.as_of,
                "content_sha256": "sha256:" + "a" * 64,
                "effective_from": trading_date,
                "effective_until": trading_date,
                "lifecycle_status": SecurityLifecycleStatus.NORMAL,
                "trading_status": TradingSessionStatus.TRADING,
                "special_session": PriceLimitSpecialSession.NONE,
                "price_limit_mode": PriceLimitMode.BOUNDED,
                "upper_limit_price": 11.0,
                "lower_limit_price": 9.0,
            }
            if aggregator:
                values.update({
                    "source_kind": PublicSourceKind.PUBLIC_AGGREGATOR,
                    "source_contract_id": "akshare-public-status-v1",
                    "source_name": "AKShare公开数据聚合",
                    "source_url": (
                        "https://akshare.akfamily.xyz/data/stock/stock.html"
                    ),
                    "document_id": f"akshare-status-{symbol}",
                    "content_sha256": "sha256:" + "b" * 64,
                    "upstream_source_name": "东方财富公开页面",
                    "upstream_source_url": (
                        "https://data.eastmoney.com/tfpxx/"
                    ),
                    "upstream_document_id": f"eastmoney-status-{symbol}",
                    "upstream_source_time": (
                        self.plan.as_of - timedelta(seconds=30)
                    ),
                    "price_limit_mode": None,
                    "upper_limit_price": None,
                    "lower_limit_price": None,
                })
            return PublicTradabilityObservation(**values)

        official = (
            tuple(observation(symbol) for symbol in symbols)
            if include_official
            else ()
        )
        aggregator = tuple(
            observation(symbol, aggregator=True)
            for symbol in symbols
        )
        report = run_public_composite_tradability_poc(
            query=query,
            quotes=quotes,
            official_observations=official,
            aggregator_observations=aggregator,
            executed=True,
        )
        return LeaderResearchTradabilityAdmissionBundle(
            candidate_plan_id=self.plan.candidate_set_id,
            radar_run_id=self.plan.radar_run_id,
            quote_batch_id=self.plan.quote_batch_id,
            query=query,
            quotes=quotes,
            official_observations=official,
            aggregator_observations=aggregator,
            report=report,
        )

    def risk_bundle(self):
        return LeaderResearchRiskAdmissionBundle(
            candidate_plan_id=self.plan.candidate_set_id,
            radar_run_id=self.plan.radar_run_id,
            quote_batch_id=self.plan.quote_batch_id,
            batch=self.f6.f5.f4.risk_batch(
                tuple(item.symbol for item in self.plan.items),
                as_of=self.plan.as_of,
            ),
        )

    def input_value(self, **changes):
        values = {
            "context": self.context,
            "history_entries": self.history_entries(),
            "business_review_batch": self.business_batch(),
            "tradability_bundle": self.tradability_bundle(),
            "risk_projection_bundle": self.risk_bundle(),
        }
        values.update(changes)
        return LeaderResearchSourceAdmissionInput(**values)

    def test_all_verified_sources_enter_existing_provider_in_one_batch(self):
        result = build_leader_research_source_admission(
            self.input_value()
        )

        symbols = tuple(item.symbol for item in self.plan.items)
        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.READY,
        )
        self.assertEqual(
            result.provider_result.status,
            LeaderResearchInputProviderBatchStatus.READY,
        )
        self.assertEqual(
            tuple(result.provider_result.history_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            tuple(result.provider_result.business_catalyst_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            tuple(result.provider_result.tradability_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            tuple(result.provider_result.risk_projection_batch.projections_by_symbol),
            symbols,
        )
        self.assertEqual(
            tuple(item.name for item in result.components),
            ("history", "business_catalyst", "tradability", "risk"),
        )
        self.assertTrue(all(
            item.status.value == "ready" for item in result.components
        ))
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.plan,
                provider_input=result.provider_input,
                source_context=self.context,
                **self.raw,
            )
        )
        self.assertEqual(
            single_pass.status,
            LeaderResearchSinglePassStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(
                item.symbol
                for item in single_pass.runtime_assembly.evidence_items
            ),
            symbols,
        )
        self.assertFalse(single_pass.formal_usable)
        self.assertFalse(single_pass.state_transition_allowed)

    def test_partial_and_failed_sources_remain_explicit_without_forgery(self):
        history = self.history_entries()
        series = dict(history[1].query.series_by_symbol)
        candidate_series = series[history[1].symbol]
        series[history[1].symbol] = replace(
            candidate_series,
            points=candidate_series.points[:-1],
        )
        partial_query = replace(
            history[1].query,
            series_by_symbol=series,
        )
        partial_history = run_public_history_input_poc(partial_query)
        self.assertEqual(partial_history.resolution_status, "partial")

        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=(
                    history[0],
                    replace(
                        history[1],
                        query=partial_query,
                        result=partial_history,
                    ),
                    *history[2:],
                ),
                tradability_bundle=self.tradability_bundle(
                    include_official=False
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertEqual(
            result.provider_result.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(result.provider_result.history_inputs_by_symbol),
            tuple(
                item.symbol
                for index, item in enumerate(self.plan.items)
                if index != 1
            ),
        )
        self.assertEqual(
            result.provider_result.tradability_inputs_by_symbol,
            {},
        )
        self.assertEqual(
            tuple(item.status.value for item in result.components),
            ("partial", "ready", "partial", "ready"),
        )

    def test_forged_failed_history_is_blocked_not_downgraded_to_missing(self):
        history = self.history_entries()
        failed = replace(
            history[0].result,
            resolution_status="failed",
            reasons=("history_public_source_failed",),
            member_count=0,
            expected_series_count=0,
            complete_series_count=0,
            series_coverage=0.0,
            history_input=None,
        )

        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=(
                    replace(history[0], result=failed),
                    *history[1:],
                )
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )
        self.assertIsNone(result.provider_result)

    def test_all_absent_sources_build_explicit_missing_provider_batch(self):
        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=None,
                business_review_batch=None,
                tradability_bundle=None,
                risk_projection_bundle=None,
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.MISSING,
        )
        self.assertEqual(
            result.provider_result.status,
            LeaderResearchInputProviderBatchStatus.MISSING,
        )
        self.assertTrue(all(
            item.status.value == "missing" for item in result.components
        ))

    def test_history_candidate_order_or_result_contract_drift_blocks_batch(self):
        entries = self.history_entries()
        malformed_cases = (
            tuple(reversed(entries)),
            entries[:-1],
            (
                entries[0],
                replace(
                    entries[1],
                    result=replace(
                        entries[1].result,
                        real_poc_status="completed",
                    ),
                ),
                *entries[2:],
            ),
            (
                replace(
                    entries[0],
                    candidate_plan_id="other-plan",
                ),
                *entries[1:],
            ),
        )

        for malformed in malformed_cases:
            with self.subTest(size=len(malformed)):
                result = build_leader_research_source_admission(
                    self.input_value(history_entries=malformed)
                )
                self.assertEqual(
                    result.status,
                    LeaderResearchSourceAdmissionStatus.BLOCKED,
                )
                self.assertIsNone(result.provider_result)

    def test_history_requires_complete_context_industry_membership(self):
        entries = self.history_entries()
        first = entries[0]
        membership = replace(
            first.query.membership,
            member_symbols=(first.symbol,),
        )
        series = {
            symbol: value
            for symbol, value in first.query.series_by_symbol.items()
            if symbol in {first.symbol, first.query.board_index_symbol}
        }
        incomplete_query = replace(
            first.query,
            membership=membership,
            series_by_symbol=series,
        )
        incomplete_result = run_public_history_input_poc(
            incomplete_query
        )
        self.assertEqual(incomplete_result.resolution_status, "ready")

        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=(
                    replace(
                        first,
                        query=incomplete_query,
                        result=incomplete_result,
                    ),
                    *entries[1:],
                )
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )
        self.assertIsNone(result.provider_input)

    def test_business_contract_relabel_and_tradability_forgery_are_blocked(self):
        cases = (
            {
                "business_review_batch": replace(
                    self.business_batch(),
                    contract_id="caller-business-review-v1",
                ),
            },
            {
                "tradability_bundle": replace(
                    self.tradability_bundle(),
                    report=replace(
                        self.tradability_bundle().report,
                        reasons=("caller-declared-ready",),
                    ),
                ),
            },
        )

        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                result = build_leader_research_source_admission(
                    self.input_value(**changes)
                )
                self.assertEqual(
                    result.status,
                    LeaderResearchSourceAdmissionStatus.BLOCKED,
                )
                self.assertIsNone(result.provider_input)

    def test_tradability_must_replay_same_runtime_quote_batch(self):
        bundle = self.tradability_bundle()
        altered_quotes = (
            bundle.quotes[0].model_copy(update={"price": 999.0}),
            *bundle.quotes[1:],
        )
        malformed_cases = (
            replace(bundle, candidate_plan_id="other-plan"),
            replace(bundle, quotes=altered_quotes),
            replace(
                bundle,
                query=replace(
                    bundle.query,
                    quote_batch_evidence=replace(
                        bundle.query.quote_batch_evidence,
                        batch_id="other-quote-batch",
                    ),
                ),
            ),
        )

        for malformed in malformed_cases:
            result = build_leader_research_source_admission(
                self.input_value(tradability_bundle=malformed)
            )
            self.assertEqual(
                result.status,
                LeaderResearchSourceAdmissionStatus.BLOCKED,
            )
            self.assertIsNone(result.provider_input)

    def test_tradability_quote_mutation_is_copied_and_f6_rechecks_source(self):
        bundle = self.tradability_bundle()
        result = build_leader_research_source_admission(
            self.input_value(tradability_bundle=bundle)
        )
        provider_quote = (
            result.provider_input.entries[0].tradability_input.quote
        )
        bundle.quotes[0].price = 777.0
        self.assertNotEqual(provider_quote.price, 777.0)

        provider_quote.price = 888.0
        single_pass = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=self.plan,
                provider_input=result.provider_input,
                source_context=self.context,
                **self.raw,
            )
        )

        self.assertEqual(
            single_pass.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(single_pass.reasons, (SOURCE_MISMATCH,))

    def test_malformed_nested_history_and_sensitive_reason_fail_closed(self):
        entries = self.history_entries()
        malformed_history = replace(
            entries[0].result.history_input,
            candidate=None,
        )
        malformed_result = replace(
            entries[0].result,
            reasons=(
                "https://example.invalid/?token=secret-value",
            ),
            history_input=malformed_history,
        )

        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=(
                    replace(entries[0], result=malformed_result),
                    *entries[1:],
                )
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )
        evidence = str(result.to_evidence())
        self.assertNotIn("secret-value", evidence)
        self.assertNotIn("example.invalid", evidence)

    def test_risk_identity_drift_is_blocked_before_provider_execution(self):
        bundle = self.risk_bundle()
        risk = bundle.batch
        reversed_items = tuple(
            replace(item, index=index)
            for index, item in enumerate(reversed(risk.items))
        )
        malformed_cases = (
            replace(
                bundle,
                candidate_plan_id="other-plan",
            ),
            replace(
                bundle,
                batch=replace(
                    risk,
                    as_of=risk.as_of + timedelta(seconds=1),
                ),
            ),
            replace(
                bundle,
                batch=replace(risk, items=reversed_items),
            ),
        )

        for malformed in malformed_cases:
            result = build_leader_research_source_admission(
                self.input_value(risk_projection_bundle=malformed)
            )

            self.assertEqual(
                result.status,
                LeaderResearchSourceAdmissionStatus.BLOCKED,
            )
            self.assertIsNone(result.provider_input)
            self.assertIsNone(result.provider_result)

    def test_risk_failure_stale_and_unverified_statuses_are_preserved(self):
        cases = (
            (
                ResearchFeatureStatus.SOURCE_FAILED,
                LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED,
                "source_failed",
            ),
            (
                ResearchFeatureStatus.STALE,
                LeaderRiskCandidateProjectionBatchStatus.STALE,
                "stale",
            ),
            (
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
                "source_unverified",
            ),
        )

        for item_status, batch_status, expected in cases:
            bundle = self.risk_bundle()
            items = tuple(
                replace(
                    item,
                    status=item_status,
                    reasons=(f"risk_projection_{expected}",),
                    projection=None,
                )
                for item in bundle.batch.items
            )
            failed_batch = replace(
                bundle.batch,
                status=batch_status,
                items=items,
                reasons=(
                    "risk_candidate_projection_batch_no_ready_items",
                ),
            )

            result = build_leader_research_source_admission(
                self.input_value(
                    risk_projection_bundle=replace(
                        bundle,
                        batch=failed_batch,
                    )
                )
            )

            self.assertEqual(
                result.status,
                LeaderResearchSourceAdmissionStatus.PARTIAL,
            )
            self.assertEqual(result.components[3].status.value, expected)

    def test_history_exclusion_count_cannot_be_relabelled(self):
        history = self.history_entries()
        result = build_leader_research_source_admission(
            self.input_value(
                history_entries=(
                    replace(
                        history[0],
                        result=replace(
                            history[0].result,
                            excluded_out_of_scope_count=-1,
                        ),
                    ),
                    *history[1:],
                )
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSourceAdmissionStatus.BLOCKED,
        )
        self.assertIsNone(result.provider_input)
        self.assertIsNone(result.provider_result)

    def test_result_is_frozen_and_evidence_does_not_expose_raw_inputs(self):
        result = build_leader_research_source_admission(
            self.input_value()
        )

        with self.assertRaises(FrozenInstanceError):
            result.formal_usable = True
        self.assertEqual(
            result.contract_id,
            LEADER_RESEARCH_SOURCE_ADMISSION_CONTRACT_ID,
        )
        text = repr(result)
        evidence = str(result.to_evidence())
        self.assertNotIn("AdjustedHistoryPoint", text)
        self.assertNotIn("LeaderBusinessProof", text)
        self.assertNotIn("source_url", evidence)
        self.assertNotIn("decision_summary", evidence)


if __name__ == "__main__":
    unittest.main()
