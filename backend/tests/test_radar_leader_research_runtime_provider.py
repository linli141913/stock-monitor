import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

import radar.leader_research_runtime_provider as runtime_provider_module
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchStatus,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_research_runtime_provider import (
    LEADER_RESEARCH_EXPLICIT_MISSING_PROVIDER_CONTRACT_ID,
    LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID,
    LeaderResearchRuntimeSourceContext,
    build_leader_research_runtime_source_context,
    build_explicit_missing_leader_research_provider_input,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)


class LeaderResearchRuntimeProviderTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        self.plan = self.f6.candidate_plan()

    def source_context(self, **updates):
        values = {
            "candidate_plan": self.plan,
            **self.f6.raw_inputs(),
        }
        values.pop("as_of")
        values.pop("market_snapshot")
        values.pop("sector_rows")
        values.update(updates)
        return build_leader_research_runtime_source_context(**values)

    def test_source_context_freezes_same_run_candidate_source_inputs(self):
        raw = self.f6.raw_inputs()
        context = self.source_context()

        self.assertIsInstance(context, LeaderResearchRuntimeSourceContext)
        self.assertEqual(
            context.contract_id,
            LEADER_RESEARCH_RUNTIME_SOURCE_CONTEXT_CONTRACT_ID,
        )
        self.assertEqual(context.radar_run_id, self.plan.radar_run_id)
        self.assertEqual(context.as_of, self.plan.as_of)
        self.assertEqual(context.quote_batch_id, self.plan.quote_batch_id)
        self.assertEqual(
            tuple(context.quotes_by_symbol),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertEqual(
            tuple(context.security_records_by_symbol),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertEqual(
            context.quotes_by_symbol[self.plan.items[0].symbol],
            raw["quote_batch"].items[0],
        )
        raw["quote_batch"].items[0].price = 999.0
        self.assertNotEqual(
            context.quotes_by_symbol[self.plan.items[0].symbol].price,
            999.0,
        )
        with self.assertRaises(FrozenInstanceError):
            context.quote_batch_id = "other-batch"
        self.assertNotIn("QuoteSnapshot", repr(context))
        self.assertNotIn("SecurityMasterRecord", repr(context))

    def test_source_context_rejects_batch_or_candidate_identity_drift(self):
        raw = self.f6.raw_inputs()
        mismatched_batch = raw["quote_batch"].model_copy(
            update={
                "meta": raw["quote_batch"].meta.model_copy(
                    update={"batch_id": "other-batch"},
                ),
            },
        )
        with self.assertRaisesRegex(
            ValueError,
            "leader_research_runtime_context_unverified",
        ):
            self.source_context(quote_batch=mismatched_batch)

        with self.assertRaisesRegex(
            ValueError,
            "leader_research_runtime_context_unverified",
        ):
            self.source_context(
                security_records=raw["security_records"][1:],
            )

    def test_explicit_missing_provider_covers_every_candidate_without_forgery(self):
        input_value = (
            build_explicit_missing_leader_research_provider_input(
                self.source_context()
            )
        )
        result = build_leader_research_input_provider_batch_from_plan(
            input_value
        )

        self.assertEqual(
            input_value.provider_contract_id,
            LEADER_RESEARCH_EXPLICIT_MISSING_PROVIDER_CONTRACT_ID,
        )
        self.assertEqual(
            tuple(entry.symbol for entry in input_value.entries),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertTrue(all(
            entry.history_input is None
            and entry.business_catalyst_input is None
            and entry.tradability_input is None
            for entry in input_value.entries
        ))
        self.assertTrue(all(
            item.status == ResearchFeatureStatus.MISSING
            and item.projection is None
            for item in input_value.risk_projection_batch.items
        ))
        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.MISSING,
        )
        self.assertEqual(
            result.candidate_plan_id,
            self.plan.candidate_set_id,
        )

    def test_provider_input_is_frozen_and_repr_hides_plan_and_raw_inputs(self):
        input_value = (
            build_explicit_missing_leader_research_provider_input(
                self.source_context()
            )
        )

        with self.assertRaises(FrozenInstanceError):
            input_value.radar_run_id = "other-run"
        self.assertNotIn("LeaderRuntimeCandidatePlanItem", repr(input_value))
        self.assertNotIn("LeaderRiskCandidateProjectionBatchItem", repr(input_value))

    def test_invalid_source_context_is_rejected_with_stable_error(self):
        with self.assertRaisesRegex(
            ValueError,
            "leader_research_runtime_context_unverified",
        ):
            build_explicit_missing_leader_research_provider_input(
                replace(self.source_context(), radar_run_id="other-run")
            )

    def test_explicit_missing_provider_calls_no_research_feature_builder(self):
        names = (
            "build_leader_research_features",
            "build_leader_history_features",
            "build_leader_liquidity_features",
            "build_leader_business_catalyst_features",
            "build_leader_tradability_features",
        )
        patches = tuple(
            patch(
                f"radar.leader_runtime_inputs.{name}",
                side_effect=AssertionError(
                    f"missing provider must not call {name}"
                ),
            )
            for name in names
        )
        mocks = []
        try:
            for mock_patch in patches:
                mocks.append(mock_patch.start())
            build_explicit_missing_leader_research_provider_input(
                self.source_context()
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertTrue(all(mock.call_count == 0 for mock in mocks))

    def test_verified_source_components_are_bound_in_candidate_order(self):
        self.assertTrue(hasattr(
            runtime_provider_module,
            "build_verified_leader_research_provider_input",
        ))
        fixture_input = self.f6.provider_input(self.plan)
        entries_by_symbol = {
            entry.symbol: entry
            for entry in fixture_input.entries
        }
        provider_input = (
            runtime_provider_module
            .build_verified_leader_research_provider_input(
            self.source_context(),
            provider_contract_id="verified-source-provider-v1",
            history_inputs_by_symbol={
                symbol: entry.history_input
                for symbol, entry in entries_by_symbol.items()
            },
            business_catalyst_inputs_by_symbol={
                symbol: entry.business_catalyst_input
                for symbol, entry in entries_by_symbol.items()
            },
            tradability_inputs_by_symbol={
                symbol: entry.tradability_input
                for symbol, entry in entries_by_symbol.items()
            },
            risk_projection_batch=fixture_input.risk_projection_batch,
            )
        )
        result = build_leader_research_input_provider_batch_from_plan(
            provider_input
        )

        self.assertEqual(
            tuple(entry.symbol for entry in provider_input.entries),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.READY,
        )
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_partial_verified_sources_keep_missing_components_explicit(self):
        self.assertTrue(hasattr(
            runtime_provider_module,
            "build_verified_leader_research_provider_input",
        ))
        fixture_input = self.f6.provider_input(self.plan)
        first_entry = fixture_input.entries[0]
        provider_input = (
            runtime_provider_module
            .build_verified_leader_research_provider_input(
            self.source_context(),
            provider_contract_id="verified-source-provider-v1",
            history_inputs_by_symbol={
                first_entry.symbol: first_entry.history_input,
            },
            )
        )
        result = build_leader_research_input_provider_batch_from_plan(
            provider_input
        )

        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(result.history_inputs_by_symbol),
            (first_entry.symbol,),
        )
        self.assertIn("business_catalyst", result.items[0].missing_inputs)
        self.assertIn("tradability", result.items[0].missing_inputs)
        self.assertIn("risk_projection", result.items[0].missing_inputs)

    def test_verified_sources_reject_symbols_outside_candidate_plan(self):
        self.assertTrue(hasattr(
            runtime_provider_module,
            "build_verified_leader_research_provider_input",
        ))
        fixture_input = self.f6.provider_input(self.plan)

        with self.assertRaisesRegex(
            ValueError,
            "leader_research_runtime_provider_input_unverified",
        ):
            runtime_provider_module.build_verified_leader_research_provider_input(
                self.source_context(),
                provider_contract_id="verified-source-provider-v1",
                history_inputs_by_symbol={
                    "920023": fixture_input.entries[0].history_input,
                },
            )


if __name__ == "__main__":
    unittest.main()
