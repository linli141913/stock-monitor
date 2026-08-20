import unittest
from dataclasses import replace
from datetime import timedelta

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
)
from radar.leader_history_features import HistoryAdjustmentBasis
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

    def test_series_fetch_failure_closes_batch_without_partial_delivery(self):
        dates = self.entries[0].query.expected_trade_dates

        def requester(query_symbol):
            if query_symbol == "sz000001":
                raise TimeoutError("private upstream detail")
            return {"code": 0, "data": {}}

        batch = fetch_leader_history_series_batch(
            ("000001", "sh000001"),
            dates,
            requester=requester,
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
