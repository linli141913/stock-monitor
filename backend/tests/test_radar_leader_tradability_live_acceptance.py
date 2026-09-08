import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import run_leader_tradability_live_acceptance as cli
import radar.leader_phase6_live_prefreeze as phase6_prefreeze

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.contracts import (
    QuoteSnapshot,
    SecurityMasterRecord,
    SourceBatch,
    SourceIssue,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.leader_history_features import (
    AdjustedHistoryPoint,
    HistoryAdjustmentBasis,
)
from radar.leader_history_production_collector import (
    LeaderHistoryProductionFrozenBatch,
)
from radar.leader_phase6_live_prefreeze import (
    LeaderPhase6PlanPrefreezeEvidence,
    resolve_sector_comparable_time,
)
from radar.sector_threshold_review import SectorThresholdApprovalLoadResult
from radar.sector_state_producer import sector_threshold_record_sha256
from radar.sector_history_backfill import (
    SectorHistoryBackfillQuery,
    SectorHistoryBackfillResult,
)
from radar.sector_rule_readiness import (
    ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID,
    REQUIRED_STATE_IDS,
    REQUIRED_THRESHOLD_POLICY_FIELDS,
    SECTOR_RULE_VERSION,
    SectorHistoryCoverage,
    SectorHistoryCoverageEvidence,
    SectorMarketBaselineEvidence,
    SectorThresholdApprovalEvidence,
)
from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceSources,
    LeaderTradabilityLiveAcceptanceStatus,
    build_default_leader_tradability_live_acceptance_sources,
    run_leader_tradability_live_acceptance,
)
from radar.leader_live_candidate_collection_batch import (
    collect_leader_live_candidate_batch,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.sources.leader_tradability_exchange_official import (
    ExchangeOfficialObservationBatch,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
)
from radar.sources.leader_tradability_public_poc import (
    PublicSourceKind,
    PublicTradingCalendarEvidence,
    PublicTradabilityObservation,
)
from radar.sources.leader_history_public_poc import (
    PointInTimeIndustryMembership,
    PublicHistorySeries,
    TENCENT_HISTORY_URL,
)
from tests import test_radar_leader_live_candidate_collection_batch as helpers
from tests import test_radar_sector_state_producer as state_helpers


EVIDENCE_COMPLETED_AT = helpers.COLLECTION_COMPLETED_AT + timedelta(seconds=2)


class LeaderTradabilityLiveAcceptanceTests(unittest.TestCase):
    def test_default_official_loader_uses_safe_maximum_concurrency(self):
        contexts = (object(),)
        trading_date = date(2026, 8, 28)
        with patch(
            "radar.leader_tradability_live_acceptance."
            "collect_exchange_official_observations",
            return_value=object(),
        ) as collect:
            sources = build_default_leader_tradability_live_acceptance_sources()
            sources.official_loader(contexts, trading_date)

        collect.assert_called_once_with(
            contexts=contexts,
            trading_date=trading_date,
            max_workers=8,
        )

    def setUp(self):
        helper = helpers.LeaderLiveCandidateCollectionBatchTests(
            methodName=(
                "test_ready_batch_freezes_completion_time_and_hides_raw_payload"
            )
        )
        self.helper = helper

    def candidate_sources(self, sources=None):
        sources = sources or self.helper.sources()

        def quotes(symbols, run_id, batch_id, as_of):
            batch = sources.quote_loader(symbols, run_id, batch_id, as_of)
            return batch.model_copy(update={
                "items": [
                    item.model_copy(update={
                        "previous_close": 10.0,
                        "open_price": 10.0,
                        "high_price": 10.2,
                        "low_price": 9.9,
                        "upper_limit_price_source": 11.0,
                        "lower_limit_price_source": 9.0,
                    })
                    for item in batch.items
                ]
            })

        return sources.__class__(
            security_master_loader=sources.security_master_loader,
            classification_loader=sources.classification_loader,
            quote_loader=quotes,
            index_loader=sources.index_loader,
            classification_release_loader=(
                sources.classification_release_loader
            ),
        )

    def official_batch(self, contexts, trading_date):
        def observation(context):
            is_sse = context.exchange == "sse"
            values = {
                "symbol": context.symbol,
                "exchange": context.exchange,
                "board": context.board,
                "trading_date": trading_date,
                "source_kind": PublicSourceKind.EXCHANGE_OFFICIAL,
                "source_contract_id": (
                    "sse-public-status-v1"
                    if is_sse
                    else "szse-public-status-v1"
                ),
                "source_name": (
                    "上海证券交易所" if is_sse else "深圳证券交易所"
                ),
                "source_url": (
                    "https://yunhq.sse.com.cn:32042/v1/sh1/snap/"
                    f"{context.symbol}"
                    if is_sse
                    else "https://www.szse.cn/api/market/ssjjhq/getTimeData"
                ),
                "document_id": (
                    f"{context.exchange}-status-{context.symbol}"
                ),
                "source_time": (
                    EVIDENCE_COMPLETED_AT - timedelta(seconds=1)
                ),
                "fetched_at": EVIDENCE_COMPLETED_AT,
                "content_sha256": "sha256:" + "a" * 64,
                "effective_from": trading_date,
                "effective_until": trading_date,
                "lifecycle_status": SecurityLifecycleStatus.NORMAL,
                "trading_status": TradingSessionStatus.TRADING,
                "special_session": PriceLimitSpecialSession.NONE,
            }
            if is_sse:
                values.update({
                    "price_limit_mode": PriceLimitMode.BOUNDED,
                    "upper_limit_price": 11.0,
                    "lower_limit_price": 9.0,
                })
            return PublicTradabilityObservation(**values)

        observations = tuple(
            observation(context)
            for context in contexts
        )
        return ExchangeOfficialObservationBatch(
            status="completed",
            observations=observations,
            source_time=max(item.source_time for item in observations),
            fetched_at=max(item.fetched_at for item in observations),
        )

    def lifecycle_frame(self, contexts):
        return pd.DataFrame([
            {
                "代码": context.symbol,
                "名称": f"证券{context.symbol}",
                "上游时间": EVIDENCE_COMPLETED_AT - timedelta(seconds=1),
                "抓取时间": EVIDENCE_COMPLETED_AT,
                "状态代码": "00",
            }
            for context in contexts
        ])

    def calendar_document(self, as_of):
        return PublicCalendarDocument(
            source_url="https://www.sse.com.cn/services/tradingservice/",
            document_id="sse-a-share-calendar-2026",
            text="""
                <strong>2026年休市安排</strong><table>
                <tr><td>元旦：1月1日至1月3日休市</td></tr>
                <tr><td>春节：2月15日至2月23日休市</td></tr>
                <tr><td>清明节：4月4日至4月6日休市</td></tr>
                <tr><td>劳动节：5月1日至5月5日休市</td></tr>
                <tr><td>端午节：6月19日至6月21日休市</td></tr>
                <tr><td>中秋节：9月25日至9月27日休市</td></tr>
                <tr><td>国庆节：10月1日至10月7日休市</td></tr>
                </table>
            """,
            source_time=datetime(
                2025,
                12,
                30,
                tzinfo=helpers.SHANGHAI_TZ,
            ),
            fetched_at=EVIDENCE_COMPLETED_AT,
        )

    def evidence_sources(self, **changes):
        values = {
            "official_loader": self.official_batch,
            "lifecycle_loader": self.lifecycle_frame,
            "calendar_loader": self.calendar_document,
        }
        values.update(changes)
        return LeaderTradabilityLiveAcceptanceSources(**values)

    def run_acceptance(
        self,
        *,
        candidate_sources=None,
        evidence_sources=None,
        phase6_prefreeze_loader=None,
    ):
        clock_values = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        return run_leader_tradability_live_acceptance(
            self.helper.request(),
            candidate_sources or self.candidate_sources(),
            evidence_sources=evidence_sources or self.evidence_sources(),
            phase6_prefreeze_loader=phase6_prefreeze_loader,
            clock=lambda: next(clock_values),
        )

    def phase6_prefreeze_evidence(self, runtime, provisional_as_of):
        completed_at = provisional_as_of + timedelta(seconds=1)
        expected_dates = []
        current = provisional_as_of.date() - timedelta(days=1)
        while len(expected_dates) < 21:
            if current.weekday() < 5:
                expected_dates.append(current)
            current -= timedelta(days=1)
        expected_dates = tuple(reversed(expected_dates))
        member_symbols = runtime.source_context \
            .industry_constituent_symbols_by_code["01"]
        membership = PointInTimeIndustryMembership(
            release_id=runtime.candidate_plan.items[0].industry_release_id,
            source_contract_id="capco-industry-classification-v1",
            industry_code="01",
            candidate_symbol=runtime.candidate_plan.items[0].symbol,
            member_symbols=member_symbols,
            published_date=date(2026, 1, 1),
            classification_start_date=date(2026, 1, 1),
            first_observed_at=runtime.industry_release.first_observed_at,
            fetched_at=runtime.industry_release.fetched_at,
            document_sha256="sha256:" + "a" * 64,
        )

        def series(symbol):
            return PublicHistorySeries(
                symbol=symbol,
                source_contract_id=(
                    "tencent-continuous-index-daily-v1"
                    if symbol == "sz399001"
                    else "tencent-qfq-daily-history-v1"
                ),
                source_url=TENCENT_HISTORY_URL,
                adjustment_basis=(
                    HistoryAdjustmentBasis.CONTINUOUS_INDEX
                    if symbol == "sz399001"
                    else HistoryAdjustmentBasis.FORWARD_ADJUSTED
                ),
                fetched_at=completed_at,
                content_sha256="sha256:" + "b" * 64,
                points=tuple(
                    AdjustedHistoryPoint(
                        trade_date=trade_date,
                        close=100.0 + index,
                    )
                    for index, trade_date in enumerate(expected_dates)
                ),
            )

        history = LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=expected_dates,
            memberships_by_industry={"01": membership},
            series_by_symbol={
                symbol: series(symbol)
                for symbol in (*member_symbols, "sz399001")
            },
            calendar_evidence=PublicTradingCalendarEvidence(
                exchange="sse",
                trading_dates=expected_dates,
                source_contract_id="sse-a-share-trading-calendar-v1",
                source_name="上海证券交易所",
                source_url=(
                    "https://www.sse.com.cn/market/stockdata/overview/"
                ),
                document_id="sse-calendar-prefreeze-test",
                source_time=provisional_as_of - timedelta(minutes=1),
                fetched_at=completed_at,
                content_sha256="sha256:" + "c" * 64,
            ),
        )
        history_coverage = SectorHistoryCoverageEvidence(
            radar_run_id=runtime.candidate_plan.radar_run_id,
            rule_version=SECTOR_RULE_VERSION,
            classification_document_sha256=(
                runtime.industry_release.document_sha256
            ),
            as_of=completed_at,
            rows=(SectorHistoryCoverage(
                division_code="01",
                same_minute_trading_dates=expected_dates[-20:],
                persistence_trading_dates=expected_dates[-5:],
            ),),
            abnormal_day_filter_contract_id=(
                ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID
            ),
        )
        market_baseline = SectorMarketBaselineEvidence(
            radar_run_id=runtime.candidate_plan.radar_run_id,
            as_of=completed_at,
            equal_weighted_return=1.5,
            market_cap_weighted_return=1.4,
            source_batch_ids=(runtime.candidate_plan.quote_batch_id,),
        )
        threshold = SectorThresholdApprovalEvidence(
            rule_version=SECTOR_RULE_VERSION,
            threshold_set_id="threshold-set-1",
            approval_id="approval-1",
            approved_at=provisional_as_of - timedelta(days=1),
            state_ids=tuple(sorted(REQUIRED_STATE_IDS)),
            policy_fields=tuple(sorted(REQUIRED_THRESHOLD_POLICY_FIELDS)),
        )
        approval_record = replace(
            state_helpers.approval(),
            threshold_set_id=threshold.threshold_set_id,
            approval_id=threshold.approval_id,
            approved_at=threshold.approved_at,
        )
        approval_record = replace(
            approval_record,
            record_sha256=sector_threshold_record_sha256(approval_record),
        )
        return LeaderPhase6PlanPrefreezeEvidence(
            history=history,
            sector_history_evidence=history_coverage,
            market_baseline_evidence=market_baseline,
            threshold_approval_evidence=threshold,
            sector_historical_analyses=(state_helpers.history(
                "01",
                dates=expected_dates[-20:],
            ),),
            sector_comparable_time=time(15, 0),
            threshold_approval_record=approval_record,
            completed_at=completed_at,
        )

    def sector_replay_query(
        self,
        runtime,
        evidence,
        *,
        comparable_time=time(15, 0),
    ):
        memberships = {
            code: tuple(symbols)
            for code, symbols in runtime.source_context
            .industry_constituent_symbols_by_code.items()
        }
        return SectorHistoryBackfillQuery(
            radar_run_id=runtime.candidate_plan.radar_run_id,
            as_of=evidence.sector_history_evidence.as_of,
            comparable_time=comparable_time,
            expected_trade_dates=evidence.history.expected_trade_dates,
            classification_release=runtime.industry_release,
            memberships_by_division=memberships,
            series_by_symbol={
                symbol: object()
                for symbols in memberships.values()
                for symbol in symbols
            },
            total_shares_by_symbol={
                symbol: 1.0
                for symbols in memberships.values()
                for symbol in symbols
            },
            source_batch_ids=(runtime.candidate_plan.quote_batch_id,),
        )

    def test_sector_comparable_time_uses_last_completed_shanghai_5_minute(self):
        shanghai = timezone(timedelta(hours=8))
        cases = (
            (datetime(2026, 8, 25, 9, 37, tzinfo=shanghai), time(9, 35)),
            (datetime(2026, 8, 25, 11, 45, tzinfo=shanghai), time(11, 30)),
            (datetime(2026, 8, 25, 13, 2, tzinfo=shanghai), time(13, 0)),
            (datetime(2026, 8, 25, 15, 20, tzinfo=shanghai), time(15, 0)),
        )
        for observed_at, expected in cases:
            with self.subTest(observed_at=observed_at):
                self.assertEqual(
                    resolve_sector_comparable_time(observed_at),
                    expected,
                )
        with self.assertRaisesRegex(
            ValueError,
            "sector_comparable_time_unavailable",
        ):
            resolve_sector_comparable_time(
                datetime(2026, 8, 25, 9, 34, tzinfo=shanghai)
            )

    def test_complete_scope_rebases_once_and_enters_production_collector(self):
        result = self.run_acceptance()

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            result.reasons,
        )
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(result.as_of, EVIDENCE_COMPLETED_AT)
        self.assertIsNotNone(result.source_context)
        self.assertEqual(
            result.source_context.candidate_plan.candidate_set_id,
            result.candidate_plan_id,
        )
        self.assertEqual(result.source_context.as_of, result.as_of)
        self.assertIsNotNone(result.runtime_inputs)
        self.assertEqual(result.runtime_inputs.as_of, result.as_of)
        self.assertEqual(
            result.runtime_inputs.candidate_plan.candidate_set_id,
            result.candidate_plan_id,
        )
        self.assertIs(
            result.runtime_inputs.source_context,
            result.source_context,
        )
        self.assertTrue(all(
            {
                "isComplete",
                "shadowUsable",
                "sourceTime",
                "fetchedAt",
                "equalReturn",
                "capWeightedReturn",
                "exTopReturn",
                "upRatio",
            }.issubset(row)
            for row in result.runtime_inputs.sector_rows
        ))
        self.assertEqual(
            result.collected_source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(result.report.expected_count, 2)
        self.assertEqual(result.report.returned_count, 2)
        self.assertTrue(all(
            coverage == 1.0
            for coverage in result.report.field_coverage.values()
        ))
        self.assertFalse(result.formal_usable)
        self.assertNotIn("000001", repr(result.to_evidence()))

    def test_refreeze_keeps_unusable_single_member_sector_out_of_plan(self):
        result = self.run_acceptance(
            candidate_sources=self.candidate_sources(
                self.helper.sources_with_singleton()
            ),
            phase6_prefreeze_loader=self.phase6_prefreeze_evidence,
        )

        self.assertNotIn(
            "candidate_scope_changed_after_refreeze",
            result.reasons,
        )
        self.assertEqual(
            result.source_statuses.get("candidateRefreeze"),
            "completed",
        )
        self.assertEqual(result.candidate_count, 2)
        self.assertNotIn(
            "300021",
            tuple(item.symbol for item in result.candidate_plan.items),
        )

    def test_phase6_inputs_collected_before_final_plan_bind_to_final_identity(self):
        seen = {}

        def load(runtime, provisional_as_of):
            seen["candidatePlanId"] = runtime.candidate_plan.candidate_set_id
            seen["asOf"] = provisional_as_of
            evidence = self.phase6_prefreeze_evidence(
                runtime,
                provisional_as_of,
            )
            seen["history"] = evidence.history
            seen["sectorHistory"] = evidence.sector_history_evidence
            seen["marketBaseline"] = evidence.market_baseline_evidence
            seen["initialFeature"] = runtime.sector_feature_batch
            return evidence

        def completed_source(component_name, context, payload):
            return LeaderFormalResearchProductionCollectedSource(
                component_name=component_name,
                source_contract_id=f"test-{component_name}-source-v1",
                status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
                source_time=context.as_of - timedelta(seconds=1),
                fetched_at=context.as_of,
                symbols=tuple(
                    item.symbol for item in context.candidate_plan.items
                ),
                payload=payload,
            )

        def collect_history(context, batch):
            seen["historyCollectorContext"] = context
            seen["historyCollectorBatch"] = batch
            return completed_source("history", context, batch)

        def collect_sector(context, frozen, **_):
            seen["sectorCollectorContext"] = context
            seen["sectorCollectorFrozen"] = frozen
            return completed_source(
                "sector_rule",
                context,
                frozen.source_batch,
            )

        with (
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_leader_history_production_source",
                side_effect=collect_history,
            ),
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_sector_rule_production_source",
                side_effect=collect_sector,
            ),
        ):
            result = self.run_acceptance(phase6_prefreeze_loader=load)

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        )
        self.assertNotEqual(seen["candidatePlanId"], result.candidate_plan_id)
        self.assertEqual(seen["asOf"], EVIDENCE_COMPLETED_AT)
        self.assertEqual(
            result.as_of,
            EVIDENCE_COMPLETED_AT + timedelta(seconds=1),
        )
        prefrozen = result.phase6_prefrozen_inputs
        self.assertIsNotNone(prefrozen)
        sector = prefrozen.sector_rule.source_batch
        self.assertEqual(sector.radar_run_id, result.radar_run_id)
        self.assertEqual(sector.candidate_plan_id, result.candidate_plan_id)
        self.assertEqual(sector.as_of, result.as_of)
        self.assertEqual(sector.feature_batch.as_of, result.as_of)
        self.assertEqual(
            sector.feature_batch.source_time,
            seen["initialFeature"].source_time,
        )
        self.assertEqual(
            sector.feature_batch.fetched_at,
            seen["initialFeature"].fetched_at,
        )
        self.assertIsNot(
            sector.feature_batch,
            seen["initialFeature"],
        )
        self.assertEqual(sector.history_evidence.as_of, result.as_of)
        self.assertEqual(sector.market_baseline_evidence.as_of, result.as_of)
        self.assertIs(sector.history_evidence, seen["sectorHistory"])
        self.assertIs(sector.market_baseline_evidence, seen["marketBaseline"])
        self.assertIs(prefrozen.history, seen["history"])
        self.assertIs(
            seen["historyCollectorContext"],
            result.source_context,
        )
        self.assertIs(
            seen["historyCollectorBatch"],
            seen["history"],
        )
        self.assertIs(
            seen["sectorCollectorContext"],
            result.source_context,
        )
        self.assertTrue(result.to_evidence()["phase6PrefrozenInputsReady"])

    def test_prepared_history_is_rebuilt_for_current_run_before_final_freeze(self):
        captured = {}

        def load(runtime, provisional_as_of):
            prepared_type = getattr(
                phase6_prefreeze,
                "LeaderPhase6PreparedHistoricalInputs",
                None,
            )
            loader_builder = getattr(
                phase6_prefreeze,
                "build_leader_phase6_prepared_prefreeze_loader",
                None,
            )
            if prepared_type is None or loader_builder is None:
                self.fail("phase6 prepared historical input loader missing")
            source = self.phase6_prefreeze_evidence(
                runtime,
                provisional_as_of,
            )
            prepared = prepared_type(
                radar_run_id=runtime.candidate_plan.radar_run_id,
                classification_document_sha256=(
                    runtime.industry_release.document_sha256
                ),
                membership_symbols_by_industry={
                    key: tuple(value)
                    for key, value in runtime.source_context
                    .industry_constituent_symbols_by_code.items()
                },
                expected_trade_dates=source.history.expected_trade_dates,
                series_by_symbol=source.history.series_by_symbol,
                calendar_evidence=source.history.calendar_evidence,
                sector_history_rows=source.sector_history_evidence.rows,
                sector_historical_analyses=(state_helpers.history(
                    "01",
                    dates=source.history.expected_trade_dates[-20:],
                ),),
                sector_comparable_time=time(15, 0),
                sector_history_replay_query=replace(
                    self.sector_replay_query(runtime, source),
                    as_of=(
                        source.sector_history_evidence.as_of
                        - timedelta(minutes=1)
                    ),
                ),
                sector_history_source_as_of=(
                    source.sector_history_evidence.as_of
                    - timedelta(minutes=1)
                ),
                threshold_approval_evidence=(
                    source.threshold_approval_evidence
                ),
                threshold_approval_record=(
                    source.threshold_approval_record
                ),
                prepared_at=source.completed_at,
            )
            captured["prepared"] = prepared
            captured["runtime"] = runtime
            captured["provisionalAsOf"] = provisional_as_of
            captured["source"] = source
            def rebuild(query):
                captured["replayQuery"] = query
                analyses = tuple(
                    replace(item, comparable_time=query.comparable_time)
                    for item in source.sector_historical_analyses
                )
                return SectorHistoryBackfillResult(
                    status="ready",
                    reasons=(),
                    sector_analyses=analyses,
                    market_samples=(),
                    history_evidence=replace(
                        source.sector_history_evidence,
                        as_of=query.as_of,
                    ),
                )

            generated = loader_builder(
                prepared,
                clock=lambda: source.completed_at,
                sector_history_rebuilder=rebuild,
            )(runtime, provisional_as_of)
            captured["generated"] = generated
            captured["sourceAsOf"] = prepared.sector_history_source_as_of
            return generated

        def completed(component_name, context, payload):
            return LeaderFormalResearchProductionCollectedSource(
                component_name=component_name,
                source_contract_id=f"test-{component_name}-source-v1",
                status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
                source_time=context.as_of - timedelta(seconds=1),
                fetched_at=context.as_of,
                symbols=tuple(
                    item.symbol for item in context.candidate_plan.items
                ),
                payload=payload,
            )

        with (
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_leader_history_production_source",
                side_effect=lambda context, batch: completed(
                    "history", context, batch
                ),
            ),
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_sector_rule_production_source",
                side_effect=lambda context, frozen, **_: completed(
                    "sector_rule", context, frozen.source_batch
                ),
            ),
        ):
            result = self.run_acceptance(phase6_prefreeze_loader=load)

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            result.reasons,
        )
        generated = captured["generated"]
        self.assertLess(captured["sourceAsOf"], generated.completed_at)
        self.assertEqual(
            generated.sector_history_evidence.radar_run_id,
            result.radar_run_id,
        )
        self.assertEqual(
            generated.sector_history_evidence.as_of,
            result.as_of,
        )
        self.assertEqual(
            generated.market_baseline_evidence.source_batch_ids,
            (result.candidate_plan.quote_batch_id,),
        )
        self.assertEqual(
            generated.sector_comparable_time,
            resolve_sector_comparable_time(
                result.runtime_inputs.sector_feature_batch.source_time
            ),
        )
        self.assertEqual(
            captured["replayQuery"].as_of,
            result.as_of,
        )
        prepared = captured["prepared"]
        expanded_memberships = {
            **prepared.membership_symbols_by_industry,
            "02": ("000003", "000004"),
        }
        expanded_replay = replace(
            prepared.sector_history_replay_query,
            memberships_by_division={
                **prepared.sector_history_replay_query.memberships_by_division,
                "02": ("000003", "000004"),
            },
            series_by_symbol={
                **prepared.sector_history_replay_query.series_by_symbol,
                "000003": object(),
                "000004": object(),
            },
            total_shares_by_symbol={
                **prepared.sector_history_replay_query.total_shares_by_symbol,
                "000003": 1.0,
                "000004": 1.0,
            },
        )
        expanded_prepared = replace(
            prepared,
            membership_symbols_by_industry=expanded_memberships,
            sector_history_replay_query=expanded_replay,
        )
        expanded_rebuild_called = []

        def rebuild_expanded(query):
            expanded_rebuild_called.append(query)
            return SectorHistoryBackfillResult(
                status="ready",
                reasons=(),
                sector_analyses=tuple(
                    replace(
                        item,
                        comparable_time=query.comparable_time,
                    )
                    for item in captured["source"].sector_historical_analyses
                ),
                market_samples=(),
                history_evidence=replace(
                    captured["source"].sector_history_evidence,
                    as_of=query.as_of,
                ),
            )

        phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
            expanded_prepared,
            clock=lambda: captured["source"].completed_at,
            sector_history_rebuilder=rebuild_expanded,
        )(
            captured["runtime"],
            captured["provisionalAsOf"],
        )
        self.assertEqual(len(expanded_rebuild_called), 1)
        self.assertIn(
            "02",
            expanded_rebuild_called[0].memberships_by_division,
        )

        forged_query = replace(
            prepared.sector_history_replay_query,
            memberships_by_division={"01": ("000001", "000999")},
            series_by_symbol={"000001": object(), "000999": object()},
            total_shares_by_symbol={"000001": 1.0, "000999": 1.0},
        )
        forged = replace(
            prepared,
            sector_history_replay_query=forged_query,
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "sector_replay_member_symbol_scope_mismatch",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                forged,
                clock=lambda: captured["source"].completed_at,
                sector_history_rebuilder=lambda _: self.fail(
                    "forged membership reached rebuilder"
                ),
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

        expected_sector_memberships = phase6_prefreeze._sector_membership_scope(
            {
                key: tuple(value)
                for key, value in captured["runtime"].source_context
                .industry_constituent_symbols_by_code.items()
            }
        )
        first_code = next(iter(expected_sector_memberships))
        first_members = expected_sector_memberships[first_code]
        wrong_code = "99" if first_code != "99" else "98"
        wrong_scope_query = replace(
            prepared.sector_history_replay_query,
            memberships_by_division={wrong_code: first_members},
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "sector_replay_industry_scope_mismatch",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                replace(
                    prepared,
                    sector_history_replay_query=wrong_scope_query,
                ),
                clock=lambda: captured["source"].completed_at,
                sector_history_rebuilder=lambda _: self.fail(
                    "forged scope reached rebuilder"
                ),
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

        reversed_memberships = dict(expected_sector_memberships)
        reversed_memberships[first_code] = tuple(reversed(first_members))
        wrong_order_query = replace(
            prepared.sector_history_replay_query,
            memberships_by_division=reversed_memberships,
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "sector_replay_member_order_mismatch",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                replace(
                    prepared,
                    sector_history_replay_query=wrong_order_query,
                ),
                clock=lambda: captured["source"].completed_at,
                sector_history_rebuilder=lambda _: self.fail(
                    "forged order reached rebuilder"
                ),
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

        prepared_memberships = dict(
            prepared.membership_symbols_by_industry
        )
        first_prepared_code = next(iter(prepared_memberships))
        first_prepared_members = prepared_memberships[first_prepared_code]
        wrong_prepared_memberships = dict(prepared_memberships)
        wrong_prepared_memberships[first_prepared_code] = (
            *first_prepared_members[:-1],
            "000999",
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "industry_member_symbol_scope_mismatch",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                replace(
                    prepared,
                    membership_symbols_by_industry=(
                        wrong_prepared_memberships
                    ),
                ),
                clock=lambda: captured["source"].completed_at,
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

        wrong_prepared_order = dict(prepared_memberships)
        wrong_prepared_order[first_prepared_code] = tuple(
            reversed(first_prepared_members)
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "industry_member_order_mismatch",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                replace(
                    prepared,
                    membership_symbols_by_industry=wrong_prepared_order,
                ),
                clock=lambda: captured["source"].completed_at,
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

        with self.assertRaisesRegex(
            ValueError,
            "leader_phase6_prepared_binding_unverified:"
            "sector_rebuild_status_not_ready:"
            "sector_history_dates_incomplete",
        ):
            phase6_prefreeze.build_leader_phase6_prepared_prefreeze_loader(
                prepared,
                clock=lambda: captured["source"].completed_at,
                sector_history_rebuilder=lambda query: replace(
                    rebuild_expanded(query),
                    status="not_ready",
                    reasons=("sector_history_dates_incomplete",),
                ),
            )(
                captured["runtime"],
                captured["provisionalAsOf"],
            )

    def test_prepared_binding_mismatch_is_reported_as_unverifiable(self):
        reason = (
            "leader_phase6_prepared_binding_unverified:"
            "candidate_history_series_missing"
        )

        def load(_runtime, _provisional_as_of):
            raise ValueError(reason)

        result = self.run_acceptance(phase6_prefreeze_loader=load)

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.reasons, (reason,))
        self.assertEqual(
            result.source_statuses["phase6Prefreeze"],
            "source_unverified",
        )

    def test_public_history_prepare_reuses_read_only_store_before_quote_freeze(self):
        prepare = getattr(
            phase6_prefreeze,
            "prepare_leader_phase6_public_historical_inputs",
            None,
        )
        if prepare is None:
            self.fail("phase6 public historical preparer missing")
        clocks = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        candidate = collect_leader_live_candidate_batch(
            self.helper.request(),
            self.candidate_sources(),
            clock=lambda: next(clocks),
        )
        runtime = candidate.runtime_inputs
        security_master_batch = SourceBatch[SecurityMasterRecord].model_validate(
            runtime.security_master_batch.model_dump(by_alias=True)
        )
        singleton_symbol = "300021"
        singleton_security = security_master_batch.items[0].model_copy(
            update={
                "symbol": singleton_symbol,
                "name": f"证券{singleton_symbol}",
                "source_fields": {"A股代码": singleton_symbol},
            }
        )
        security_master_batch = security_master_batch.model_copy(update={
            "meta": security_master_batch.meta.model_copy(update={
                "expected_count": len(security_master_batch.items) + 1,
                "returned_count": len(security_master_batch.items) + 1,
                "row_coverage": 1.0,
            }),
            "items": [*security_master_batch.items, singleton_security],
        })
        singleton_record = runtime.classification_snapshot.records[0].model_copy(
            update={
                "source_symbol": singleton_symbol,
                "source_name": f"证券{singleton_symbol}",
                "security_identity": singleton_symbol,
                "division_code": "02",
                "division_name": "林业",
            }
        )
        classification_snapshot = runtime.classification_snapshot.model_copy(
            update={
                "meta": runtime.classification_snapshot.meta.model_copy(
                    update={
                        "expected_count": (
                            len(runtime.classification_snapshot.records) + 1
                        ),
                        "returned_count": (
                            len(runtime.classification_snapshot.records) + 1
                        ),
                        "row_coverage": 1.0,
                    }
                ),
                "records": [
                    *runtime.classification_snapshot.records,
                    singleton_record,
                ],
            }
        )
        share_quote_batch = runtime.quote_batch.model_copy(update={
            "items": [
                item.model_copy(update={
                    "market_cap_cny": item.market_cap_source,
                    "market_cap_unit_status": UnitVerificationStatus.VERIFIED,
                    "total_shares_source": (
                        item.market_cap_source / item.price
                    ),
                    "currency": "CNY",
                })
                for item in runtime.quote_batch.items
            ]
        })
        share_quote_batch = SourceBatch[QuoteSnapshot].model_validate(
            share_quote_batch.model_dump(by_alias=True)
        )
        singleton_quote = share_quote_batch.items[0].model_copy(update={
            "symbol": singleton_symbol,
            "name": f"证券{singleton_symbol}",
        })
        share_quote_batch = share_quote_batch.model_copy(update={
            "meta": share_quote_batch.meta.model_copy(update={
                "expected_count": len(share_quote_batch.items) + 1,
                "returned_count": len(share_quote_batch.items) + 1,
                "row_coverage": 1.0,
            }),
            "items": [*share_quote_batch.items, singleton_quote],
        })
        source = self.phase6_prefreeze_evidence(
            runtime,
            EVIDENCE_COMPLETED_AT,
        )
        calendar_document = self.calendar_document(runtime.as_of)
        # 阶段10必须绑定上交所实际响应字节；这里故意使用
        # GB18030，防止预冻结层又把 text 重编码为 UTF-8 后求哈希。
        calendar_raw_content = calendar_document.text.encode("gb18030")
        calendar_document = PublicCalendarDocument(
            source_url=calendar_document.source_url,
            document_id=calendar_document.document_id,
            text=calendar_document.text,
            source_time=calendar_document.source_time,
            fetched_at=calendar_document.fetched_at,
            raw_content=calendar_raw_content,
            text_encoding="gb18030",
        )
        source_history_series = dict(source.history.series_by_symbol)
        source_history_series[singleton_symbol] = replace(
            next(
                series
                for symbol, series in source_history_series.items()
                if symbol != "sz399001"
            ),
            symbol=singleton_symbol,
        )
        source_history = replace(
            source.history,
            series_by_symbol=source_history_series,
        )
        captured = {}

        def history_fetcher(symbols, dates, **kwargs):
            captured["historySymbols"] = symbols
            captured["historyDates"] = dates
            captured["historyKwargs"] = kwargs
            return source_history

        def sector_runner(request, **kwargs):
            captured["sectorRequest"] = request
            captured["sectorKwargs"] = kwargs
            return SimpleNamespace(
                status="ready",
                backfill_result=SimpleNamespace(
                    history_evidence=source.sector_history_evidence,
                    sector_analyses=source.sector_historical_analyses,
                ),
                evidence_path=Path(kwargs["artifact_dir"]) / "evidence.json",
                replay_query=SectorHistoryBackfillQuery(
                    radar_run_id=request.radar_run_id,
                    as_of=source.sector_history_evidence.as_of,
                    comparable_time=request.comparable_time,
                    expected_trade_dates=request.expected_trade_dates,
                    classification_release=request.classification_release,
                    memberships_by_division=request.memberships_by_division,
                    series_by_symbol={
                        symbol: object()
                        for symbols in request.memberships_by_division.values()
                        for symbol in symbols
                    },
                    total_shares_by_symbol=request.total_shares_by_symbol,
                    source_batch_ids=request.source_batch_ids,
                ),
            )

        with (
            tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir,
            tempfile.TemporaryDirectory(dir="/private/tmp") as reuse_dir,
        ):
            try:
                prepared = prepare(
                    radar_run_id=runtime.candidate_plan.radar_run_id,
                    security_master_batch=security_master_batch,
                    classification_snapshot=classification_snapshot,
                    share_quote_batch=share_quote_batch,
                    calendar_document=calendar_document,
                    artifact_dir=Path(output_dir),
                    history_checkpoint_dir=(
                        Path(output_dir).parent
                        / "shared-leader-history-checkpoints"
                    ),
                    sector_checkpoint_dir=(
                        Path(output_dir).parent
                        / "shared-sector-history-checkpoints"
                    ),
                    reuse_store_dir=Path(reuse_dir),
                    history_fetcher=history_fetcher,
                    sector_backfill_runner=sector_runner,
                    threshold_approval_loader=lambda: (
                        SectorThresholdApprovalLoadResult(
                            status="approved",
                            reasons=(),
                            evidence=source.threshold_approval_evidence,
                            record=source.threshold_approval_record,
                        )
                    ),
                    sector_comparable_time=time(15, 0),
                    clock=lambda: source.completed_at,
                )
            except (TypeError, ValueError) as exc:
                self.fail(f"real generic source batch rejected: {exc}")

        self.assertEqual(prepared.radar_run_id, candidate.radar_run_id)
        self.assertEqual(len(prepared.expected_trade_dates), 21)
        self.assertIn("sz399001", captured["historySymbols"])
        self.assertIn(singleton_symbol, captured["historySymbols"])
        self.assertNotIn(
            "02",
            captured["sectorRequest"].memberships_by_division,
        )
        self.assertNotIn(
            singleton_symbol,
            captured["sectorRequest"].total_shares_by_symbol,
        )
        self.assertEqual(
            captured["historyKwargs"]["checkpoint_dir"],
            Path(output_dir).parent
            / "shared-leader-history-checkpoints",
        )
        self.assertEqual(
            captured["historyKwargs"][
                "classification_document_sha256"
            ],
            runtime.industry_release.document_sha256,
        )
        self.assertEqual(
            captured["sectorKwargs"]["reuse_store_dir"],
            Path(reuse_dir),
        )
        self.assertEqual(
            captured["sectorKwargs"]["artifact_dir"],
            Path(output_dir).parent
            / "shared-sector-history-checkpoints",
        )
        self.assertEqual(
            prepared.sector_history_source_as_of,
            source.sector_history_evidence.as_of,
        )
        self.assertEqual(prepared.sector_comparable_time, time(15, 0))
        self.assertEqual(
            prepared.sector_historical_analyses,
            source.sector_historical_analyses,
        )
        self.assertEqual(
            prepared.threshold_approval_record,
            source.threshold_approval_record,
        )
        self.assertTrue(
            prepared.has_verified_calendar_document_raw_content
        )
        self.assertEqual(
            prepared.calendar_document_raw_content,
            calendar_raw_content,
        )
        self.assertEqual(
            prepared.calendar_document_raw_content_sha256,
            prepared.calendar_evidence.content_sha256,
        )

    def test_public_history_prepare_allows_complete_degraded_classification(self):
        clocks = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        candidate = collect_leader_live_candidate_batch(
            self.helper.request(),
            self.candidate_sources(),
            clock=lambda: next(clocks),
        )
        runtime = candidate.runtime_inputs
        security_master_batch = SourceBatch[SecurityMasterRecord].model_validate(
            runtime.security_master_batch.model_dump(by_alias=True)
        )
        degraded = runtime.classification_snapshot.model_copy(update={
            "status": SourceStatus.DEGRADED,
        })

        with tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir:
            with self.assertRaisesRegex(
                ValueError,
                "leader_phase6_public_prepare_history_unverified",
            ):
                phase6_prefreeze.prepare_leader_phase6_public_historical_inputs(
                    radar_run_id=runtime.candidate_plan.radar_run_id,
                    security_master_batch=security_master_batch,
                    classification_snapshot=degraded,
                    share_quote_batch=runtime.quote_batch,
                    calendar_document=self.calendar_document(runtime.as_of),
                    artifact_dir=Path(output_dir),
                    reuse_store_dir=None,
                    history_fetcher=lambda *_args, **_kwargs: None,
                    sector_backfill_runner=lambda *_args, **_kwargs: self.fail(
                        "missing history reached sector backfill"
                    ),
                    clock=lambda: EVIDENCE_COMPLETED_AT,
                )

    def test_public_history_prepare_rejects_partial_security_master(self):
        clocks = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        candidate = collect_leader_live_candidate_batch(
            self.helper.request(),
            self.candidate_sources(),
            clock=lambda: next(clocks),
        )
        runtime = candidate.runtime_inputs
        partial_meta = runtime.security_master_batch.meta.model_copy(update={
            "expected_count": None,
            "row_coverage": None,
            "issues": [
                SourceIssue(
                    code="source_request_failed",
                    source="szse",
                    message="深交所A股请求失败：TimeoutError",
                )
            ],
        })
        partial_security = SourceBatch[SecurityMasterRecord].model_validate({
            "meta": partial_meta.model_dump(by_alias=True),
            "items": [
                item.model_dump(by_alias=True)
                for item in runtime.security_master_batch.items[:1]
            ],
        })

        with tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir:
            with self.assertRaisesRegex(
                ValueError,
                "leader_phase6_public_prepare_security_master_unverified",
            ):
                phase6_prefreeze.prepare_leader_phase6_public_historical_inputs(
                    radar_run_id=runtime.candidate_plan.radar_run_id,
                    security_master_batch=partial_security,
                    classification_snapshot=runtime.classification_snapshot,
                    share_quote_batch=runtime.quote_batch,
                    calendar_document=self.calendar_document(runtime.as_of),
                    artifact_dir=Path(output_dir),
                    reuse_store_dir=None,
                    history_fetcher=lambda *_args, **_kwargs: self.fail(
                        "partial security master reached history"
                    ),
                    sector_backfill_runner=lambda *_args, **_kwargs: self.fail(
                        "partial security master reached sector backfill"
                    ),
                    clock=lambda: EVIDENCE_COMPLETED_AT,
                )

    def test_public_history_prepare_preserves_failed_batch_for_diagnosis(self):
        clocks = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        candidate = collect_leader_live_candidate_batch(
            self.helper.request(),
            self.candidate_sources(),
            clock=lambda: next(clocks),
        )
        runtime = candidate.runtime_inputs
        security_master_batch = SourceBatch[SecurityMasterRecord].model_validate(
            runtime.security_master_batch.model_dump(by_alias=True)
        )
        share_quote_batch = runtime.quote_batch.model_copy(update={
            "items": [
                item.model_copy(update={
                    "market_cap_cny": item.market_cap_source,
                    "market_cap_unit_status": UnitVerificationStatus.VERIFIED,
                    "total_shares_source": (
                        item.market_cap_source / item.price
                    ),
                    "currency": "CNY",
                })
                for item in runtime.quote_batch.items
            ]
        })
        share_quote_batch = SourceBatch[QuoteSnapshot].model_validate(
            share_quote_batch.model_dump(by_alias=True)
        )
        source = self.phase6_prefreeze_evidence(
            runtime,
            EVIDENCE_COMPLETED_AT,
        )
        failed_history = replace(
            source.history,
            series_by_symbol={},
            source_status="source_failed",
            failure_count=1,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir:
            with self.assertRaises(ValueError) as caught:
                phase6_prefreeze.prepare_leader_phase6_public_historical_inputs(
                    radar_run_id=runtime.candidate_plan.radar_run_id,
                    security_master_batch=security_master_batch,
                    classification_snapshot=runtime.classification_snapshot,
                    share_quote_batch=share_quote_batch,
                    calendar_document=self.calendar_document(runtime.as_of),
                    artifact_dir=Path(output_dir),
                    reuse_store_dir=None,
                    history_fetcher=lambda *_args, **_kwargs: failed_history,
                    sector_backfill_runner=lambda *_args, **_kwargs: self.fail(
                        "failed history reached sector backfill"
                    ),
                    clock=lambda: source.completed_at,
                )

        self.assertEqual(
            str(caught.exception),
            "leader_phase6_public_prepare_history_unverified",
        )
        self.assertIs(
            getattr(caught.exception, "history_batch", None),
            failed_history,
        )

    def test_public_history_prepare_identifies_unverified_share_basis(self):
        clocks = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        candidate = collect_leader_live_candidate_batch(
            self.helper.request(),
            self.candidate_sources(),
            clock=lambda: next(clocks),
        )
        runtime = candidate.runtime_inputs
        security_master_batch = SourceBatch[SecurityMasterRecord].model_validate(
            runtime.security_master_batch.model_dump(by_alias=True)
        )
        share_quote_batch = runtime.quote_batch.model_copy(update={
            "items": [
                item.model_copy(update={
                    "market_cap_cny": (
                        None if index == 0 else item.market_cap_source
                    ),
                    "market_cap_unit_status": (
                        UnitVerificationStatus.UNVERIFIED
                        if index == 0 else UnitVerificationStatus.VERIFIED
                    ),
                    "total_shares_source": (
                        None if index == 0 else (
                            item.market_cap_source / item.price
                        )
                    ),
                    "currency": "CNY",
                })
                for index, item in enumerate(runtime.quote_batch.items)
            ]
        })
        share_quote_batch = SourceBatch[QuoteSnapshot].model_validate(
            share_quote_batch.model_dump(by_alias=True)
        )
        source = self.phase6_prefreeze_evidence(
            runtime,
            EVIDENCE_COMPLETED_AT,
        )
        invalid_symbol = share_quote_batch.items[0].symbol

        with tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir:
            with self.assertRaises(ValueError) as caught:
                phase6_prefreeze.prepare_leader_phase6_public_historical_inputs(
                    radar_run_id=runtime.candidate_plan.radar_run_id,
                    security_master_batch=security_master_batch,
                    classification_snapshot=runtime.classification_snapshot,
                    share_quote_batch=share_quote_batch,
                    calendar_document=self.calendar_document(runtime.as_of),
                    artifact_dir=Path(output_dir),
                    reuse_store_dir=None,
                    history_fetcher=lambda *_args, **_kwargs: source.history,
                    sector_backfill_runner=lambda *_args, **_kwargs: self.fail(
                        "invalid share basis reached sector backfill"
                    ),
                    clock=lambda: source.completed_at,
                )

        self.assertEqual(
            str(caught.exception),
            "leader_phase6_public_prepare_share_basis_unverified",
        )
        self.assertEqual(
            getattr(caught.exception, "missing_symbols", None),
            (),
        )
        self.assertEqual(
            getattr(caught.exception, "invalid_symbols", None),
            (invalid_symbol,),
        )

    def test_phase6_prefreeze_fails_closed_when_existing_collectors_reject(self):
        result = self.run_acceptance(
            phase6_prefreeze_loader=self.phase6_prefreeze_evidence,
        )

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "phase6_prefreeze_final_binding_unverified",
            result.reasons,
        )
        self.assertIsNone(result.phase6_prefrozen_inputs)

    def test_phase6_prefreeze_malformed_collector_results_fail_closed(self):
        with (
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_leader_history_production_source",
                return_value=object(),
            ),
            patch(
                "radar.leader_phase6_live_prefreeze."
                "collect_sector_rule_production_source",
                return_value=object(),
            ),
        ):
            result = self.run_acceptance(
                phase6_prefreeze_loader=self.phase6_prefreeze_evidence,
            )

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "phase6_prefreeze_final_binding_unverified",
            result.reasons,
        )

    def test_official_source_failure_closes_the_whole_batch(self):
        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            official_loader=lambda *_: ExchangeOfficialObservationBatch(
                status="source_failed",
                reasons=("exchange_official_request_failed",),
            )
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
        )
        self.assertIn("exchange_official_request_failed", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_incomplete_lifecycle_crosscheck_is_source_unverified(self):
        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            lifecycle_loader=lambda contexts: self.lifecycle_frame(contexts).iloc[:1]
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("sina_lifecycle_scope_unverified", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_slow_evidence_collection_does_not_reuse_stale_quotes(self):
        slow_time = helpers.COLLECTION_COMPLETED_AT + timedelta(seconds=91)

        def slow_calendar(as_of):
            document = self.calendar_document(as_of)
            return PublicCalendarDocument(
                source_url=document.source_url,
                document_id=document.document_id,
                text=document.text,
                source_time=document.source_time,
                fetched_at=slow_time,
            )

        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            calendar_loader=slow_calendar
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("quote_source_not_healthy_after_evidence", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_cli_outputs_only_sanitized_evidence(self):
        completed = self.run_acceptance()
        with (
            patch.object(
                cli,
                "build_default_leader_live_candidate_collection_sources",
                return_value=object(),
            ),
            patch.object(
                cli,
                "run_leader_tradability_live_acceptance",
                return_value=completed,
            ),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        self.assertIn('"status": "completed"', output.getvalue())
        self.assertNotIn("000001", output.getvalue())


if __name__ == "__main__":
    unittest.main()
