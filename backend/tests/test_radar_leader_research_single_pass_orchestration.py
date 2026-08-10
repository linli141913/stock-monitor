import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchEntry,
    LeaderResearchInputProviderBatchStatus,
    LeaderResearchInputProviderPlanBatchInput,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_research_single_pass_orchestration import (
    SOURCE_MISMATCH,
    LeaderResearchSinglePassInput,
    LeaderResearchSinglePassStatus,
    build_leader_research_single_pass,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
    build_explicit_missing_leader_research_provider_input,
)
from radar.leader_runtime_candidate_plan import (
    LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID,
    LeaderRuntimeCandidatePlanInput,
    LeaderRuntimeCandidatePlanStatus,
    build_leader_runtime_candidate_plan,
)
from tests import (
    test_radar_leader_research_input_provider_batch as f5_helpers,
)
from tests import test_radar_leader_runtime_inputs as runtime_helpers


AS_OF = runtime_helpers.AS_OF


class LeaderResearchSinglePassOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_helpers.LeaderRuntimeInputsTests(
            methodName=(
                "test_future_market_snapshot_is_rejected_before_"
                "candidate_build"
            )
        )
        self.f5 = f5_helpers.LeaderResearchInputProviderBatchTests(
            methodName=(
                "test_ready_batch_keeps_candidate_order_and_"
                "read_only_typed_maps"
            )
        )
        self.f5.setUp()

    def raw_inputs(self):
        quote_batch = self.runtime.full_research_quote_batch()
        for quote in quote_batch.items:
            quote.previous_close = 10.0
            quote.open_price = 10.0
            quote.high_price = 10.2
            quote.low_price = 9.8
            quote.upper_limit_price_source = 11.0
            quote.lower_limit_price_source = 9.0
        return {
            "as_of": AS_OF,
            "quote_batch": quote_batch,
            "quote_health": self.runtime.quote_health(),
            "market_snapshot": self.runtime.market_snapshot(),
            "sector_rows": self.runtime.full_research_sector_rows(),
            "industry_records": self.runtime.industry_records(),
            "security_records": (
                self.runtime.full_research_security_records()
            ),
        }

    def candidate_plan(self):
        return build_leader_runtime_candidate_plan(
            LeaderRuntimeCandidatePlanInput(**self.raw_inputs())
        )

    def provider_entry(self, item):
        quote = next(
            value
            for value in self.raw_inputs()["quote_batch"].items
            if value.symbol == item.symbol
        )
        return LeaderResearchInputProviderBatchEntry(
            symbol=item.symbol,
            history_input=self.runtime.history_input(item.symbol),
            business_catalyst_input=self.f5.business_input(item.symbol),
            tradability_input=replace(
                self.runtime.tradability_input(item.symbol),
                quote=quote.model_copy(deep=True),
                quote_source_contract_id=item.quote_source_contract_id,
            ),
        )

    def provider_input(self, plan, *, entries=None, risk_batch=None):
        symbols = tuple(item.symbol for item in plan.items)
        return LeaderResearchInputProviderPlanBatchInput(
            candidate_plan=plan,
            radar_run_id=plan.radar_run_id,
            as_of=plan.as_of,
            provider_contract_id="fixture-research-provider-v1",
            entries=(
                tuple(self.provider_entry(item) for item in plan.items)
                if entries is None
                else entries
            ),
            risk_projection_batch=(
                self.f5.f4.risk_batch(symbols)
                if risk_batch is None
                else risk_batch
            ),
        )

    def orchestration_input(self, plan, provider_input):
        raw = self.raw_inputs()
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        return LeaderResearchSinglePassInput(
            candidate_plan=plan,
            provider_input=provider_input,
            source_context=source_context,
            **raw,
        )

    def test_candidate_plan_is_frozen_ordered_and_matches_existing_runtime(self):
        plan = self.candidate_plan()
        assembly = self.runtime.build_full_research()

        self.assertEqual(
            plan.status,
            LeaderRuntimeCandidatePlanStatus.READY,
        )
        self.assertEqual(
            tuple(item.symbol for item in plan.items),
            tuple(item.symbol for item in assembly.evidence_items),
        )
        self.assertEqual(plan.radar_run_id, assembly.radar_run_id)
        self.assertEqual(plan.quote_batch_id, assembly.quote_batch_id)
        self.assertEqual(
            plan.contract_id,
            LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID,
        )
        self.assertTrue(plan.candidate_set_id)
        with self.assertRaises(FrozenInstanceError):
            plan.formal_usable = True
        self.assertIs(plan.formal_score_ready, False)
        self.assertIs(plan.formal_gate_ready, False)
        self.assertIs(plan.formal_usable, False)
        self.assertIs(plan.state_transition_allowed, False)

    def test_candidate_plan_does_not_call_any_research_feature_builder(self):
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
                side_effect=AssertionError(f"plan must not call {name}"),
            )
            for name in names
        )
        mocks = []
        try:
            for mock_patch in patches:
                mocks.append(mock_patch.start())
            plan = self.candidate_plan()
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertEqual(
            plan.status,
            LeaderRuntimeCandidatePlanStatus.READY,
        )
        self.assertTrue(all(mock.call_count == 0 for mock in mocks))

    def test_plan_provider_preserves_authoritative_candidates_and_missing(self):
        plan = self.candidate_plan()
        entries = (
            self.provider_entry(plan.items[0]),
            *(
                LeaderResearchInputProviderBatchEntry(
                    symbol=item.symbol,
                    history_input=None,
                    business_catalyst_input=None,
                    tradability_input=None,
                )
                for item in plan.items[1:]
            ),
        )
        result = build_leader_research_input_provider_batch_from_plan(
            self.provider_input(plan, entries=entries)
        )

        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            tuple(item.symbol for item in plan.items),
        )
        self.assertEqual(
            tuple(result.history_inputs_by_symbol),
            (plan.items[0].symbol,),
        )
        self.assertEqual(
            result.candidate_plan_id,
            plan.candidate_set_id,
        )
        reversed_result = (
            build_leader_research_input_provider_batch_from_plan(
                self.provider_input(
                    plan,
                    entries=tuple(reversed(
                        tuple(
                            self.provider_entry(item)
                            for item in plan.items
                        )
                    )),
                )
            )
        )
        self.assertEqual(
            tuple(item.symbol for item in reversed_result.items),
            tuple(item.symbol for item in plan.items),
        )

    def test_single_pass_builds_assembly_once_and_each_feature_once_per_candidate(self):
        plan = self.candidate_plan()
        provider_input = self.provider_input(plan)
        names = (
            "build_leader_research_features",
            "build_leader_history_features",
            "build_leader_liquidity_features",
            "build_leader_business_catalyst_features",
            "build_leader_tradability_features",
        )
        import radar.leader_runtime_inputs as runtime_module
        import radar.leader_research_single_pass_orchestration as module

        with patch.object(
            module,
            "build_leader_runtime_evidence",
            wraps=module.build_leader_runtime_evidence,
        ) as assembly_builder:
            feature_patches = tuple(
                patch.object(
                    runtime_module,
                    name,
                    wraps=getattr(runtime_module, name),
                )
                for name in names
            )
            feature_mocks = []
            try:
                for mock_patch in feature_patches:
                    feature_mocks.append(mock_patch.start())
                result = build_leader_research_single_pass(
                    self.orchestration_input(plan, provider_input)
                )
            finally:
                for mock_patch in reversed(feature_patches):
                    mock_patch.stop()

        self.assertEqual(
            result.status,
            LeaderResearchSinglePassStatus.PARTIAL,
        )
        self.assertEqual(assembly_builder.call_count, 1)
        self.assertTrue(
            all(mock.call_count == plan.candidate_count for mock in feature_mocks)
        )
        self.assertEqual(
            tuple(
                item.symbol
                for item in result.runtime_assembly.evidence_items
            ),
            tuple(item.symbol for item in plan.items),
        )
        self.assertEqual(
            result.readiness_result.audit_batch.items[
                0
            ].audit.first_research_blocker_reason,
            "leader_liquidity_evidence_unavailable",
        )
        self.assertIs(result.formal_score_ready, False)
        self.assertIs(result.formal_gate_ready, False)
        self.assertIs(result.formal_usable, False)
        self.assertIs(result.state_transition_allowed, False)

    def test_invalid_provider_blocks_before_any_feature_builder_runs(self):
        plan = self.candidate_plan()
        invalid_provider = self.provider_input(
            plan,
            entries=self.provider_input(plan).entries[:-1],
        )
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
                    f"blocked provider must not call {name}"
                ),
            )
            for name in names
        )
        mocks = []
        try:
            for mock_patch in patches:
                mocks.append(mock_patch.start())
            result = build_leader_research_single_pass(
                self.orchestration_input(plan, invalid_provider)
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertEqual(
            result.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("leader_research_single_pass_provider_blocked",),
        )
        self.assertIsNone(result.runtime_assembly)
        self.assertTrue(all(mock.call_count == 0 for mock in mocks))

    def test_explicit_missing_provider_stops_before_feature_builders(self):
        plan = self.candidate_plan()
        raw = self.raw_inputs()
        context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        provider_input = (
            build_explicit_missing_leader_research_provider_input(context)
        )
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
            result = build_leader_research_single_pass(
                self.orchestration_input(plan, provider_input)
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertEqual(
            result.status,
            LeaderResearchSinglePassStatus.MISSING,
        )
        self.assertIn(
            "leader_research_single_pass_provider_missing",
            result.gate_reasons,
        )
        self.assertIsNone(result.runtime_assembly)
        self.assertTrue(all(mock.call_count == 0 for mock in mocks))

    def test_plan_tampering_or_raw_batch_mismatch_blocks_closed(self):
        plan = self.candidate_plan()
        malformed_plan = replace(
            plan,
            radar_run_id="other-run",
        )
        malformed_raw = self.raw_inputs()
        malformed_context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=malformed_raw["quote_batch"],
            quote_health=malformed_raw["quote_health"],
            security_records=malformed_raw["security_records"],
            industry_records=malformed_raw["industry_records"],
        )
        malformed = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=malformed_plan,
                provider_input=self.provider_input(malformed_plan),
                source_context=malformed_context,
                **malformed_raw,
            )
        )
        raw = self.raw_inputs()
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        raw["quote_batch"] = raw["quote_batch"].model_copy(
            update={
                "meta": raw["quote_batch"].meta.model_copy(
                    update={"batch_id": "other-batch"},
                ),
            },
        )
        mismatched = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=plan,
                provider_input=self.provider_input(plan),
                source_context=source_context,
                **raw,
            )
        )

        self.assertEqual(
            malformed.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(
            mismatched.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(
            mismatched.reasons,
            ("leader_research_single_pass_source_mismatch",),
        )

    def test_missing_provider_cannot_bypass_quote_content_binding(self):
        plan = self.candidate_plan()
        raw = self.raw_inputs()
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        provider_input = (
            build_explicit_missing_leader_research_provider_input(
                source_context
            )
        )
        raw["quote_batch"].items[0].price = 999.0

        result = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=plan,
                provider_input=provider_input,
                source_context=source_context,
                **raw,
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(result.reasons, (SOURCE_MISMATCH,))
        self.assertIsNone(result.runtime_assembly)

    def test_malformed_nested_quote_batch_fails_closed(self):
        plan = self.candidate_plan()
        raw = self.raw_inputs()
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        raw["quote_batch"] = raw["quote_batch"].model_copy(
            update={"meta": None},
        )

        result = build_leader_research_single_pass(
            LeaderResearchSinglePassInput(
                candidate_plan=plan,
                provider_input=self.provider_input(plan),
                source_context=source_context,
                **raw,
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchSinglePassStatus.BLOCKED,
        )
        self.assertEqual(result.reasons, (SOURCE_MISMATCH,))
        self.assertIsNone(result.runtime_assembly)


if __name__ == "__main__":
    unittest.main()
