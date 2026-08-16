import unittest
from dataclasses import replace

from radar.leader_research_source_admission import (
    LeaderResearchSourceAdmissionStatus,
)
from radar.leader_formal_research_runtime_bridge import (
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_history_runtime_bridge import (
    LEADER_HISTORY_RUNTIME_BRIDGE_CONTRACT_ID,
    LeaderHistoryRuntimeBridgeStatus,
    build_leader_history_runtime_bridge,
)
from radar.sources.leader_history_public_poc import (
    run_public_history_input_poc,
)
from tests import test_radar_leader_research_source_admission as helpers


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        return ()


class LeaderHistoryRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.plan = helper.plan

    def build(self, history_entries=None):
        return build_leader_history_runtime_bridge(
            self.context,
            history_entries=history_entries,
        )

    def test_complete_history_batch_is_ready_in_plan_order(self):
        result = self.build(self.helper.history_entries())

        symbols = tuple(item.symbol for item in self.plan.items)
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            symbols,
        )
        self.assertEqual(result.status, LeaderHistoryRuntimeBridgeStatus.READY)
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(
            result.source_admission.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(result.history_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            result.to_evidence()["contractId"],
            LEADER_HISTORY_RUNTIME_BRIDGE_CONTRACT_ID,
        )
        self.assertNotIn("records", str(result.to_evidence()).lower())
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_missing_provider_is_explicitly_missing_without_placeholder_input(self):
        result = self.build()

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertEqual(
            result.reasons,
            ("leader_history_runtime_bridge_source_missing",),
        )
        self.assertEqual(result.history_inputs_by_symbol, {})
        self.assertTrue(all(
            item.reasons == ("leader_history_runtime_bridge_source_missing",)
            for item in result.items
        ))

    def test_partial_history_keeps_ready_items_and_localizes_gap(self):
        entries = self.helper.history_entries()
        series = dict(entries[1].query.series_by_symbol)
        candidate_series = series[entries[1].symbol]
        partial_query = replace(
            entries[1].query,
            series_by_symbol={
                **series,
                entries[1].symbol: replace(
                    candidate_series,
                    points=candidate_series.points[:-1],
                ),
            },
        )
        partial_result = run_public_history_input_poc(partial_query)
        result = self.build((
            entries[0],
            replace(entries[1], query=partial_query, result=partial_result),
            *entries[2:],
        ))

        self.assertEqual(result.ready_count, self.plan.candidate_count - 1)
        self.assertEqual(result.missing_count, 1)
        self.assertEqual(
            result.items[1].status,
            LeaderHistoryRuntimeBridgeStatus.MISSING,
        )
        self.assertIn(
            "history_series_dates_incomplete",
            result.items[1].reasons,
        )
        self.assertEqual(
            tuple(result.history_inputs_by_symbol),
            tuple(item.symbol for item in self.plan.items if item.index != 1),
        )

    def test_cross_run_history_is_blocked_not_relabelled_ready(self):
        entries = self.helper.history_entries()
        result = self.build((
            replace(entries[0], radar_run_id="other-run"),
            *entries[1:],
        ))

        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.missing_count, self.plan.candidate_count)
        self.assertEqual(
            result.reasons,
            ("leader_history_runtime_bridge_source_unverified",),
        )
        self.assertEqual(result.history_inputs_by_symbol, {})

    def test_history_entries_reach_existing_d8_source_admission(self):
        result = build_leader_formal_research_runtime_bridge(
            self.context,
            repository=_EmptyReviewRepository(),
            history_entries=self.helper.history_entries(),
        )

        history_component = next(
            item
            for item in result.source_admission.components
            if item.name == "history"
        )
        self.assertEqual(history_component.status.value, "ready")
        self.assertEqual(
            result.provider_input.entries[0].symbol,
            self.plan.items[0].symbol,
        )
        self.assertIsNotNone(
            result.source_admission.provider_result
        )
        self.assertEqual(
            tuple(result.source_admission.provider_result.history_inputs_by_symbol),
            tuple(item.symbol for item in self.plan.items),
        )


if __name__ == "__main__":
    unittest.main()
