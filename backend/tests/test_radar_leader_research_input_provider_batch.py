import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from unittest.mock import patch

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LEADER_RESEARCH_INPUT_PROVIDER_BATCH_CONTRACT_ID,
    LeaderResearchInputProviderBatchEntry,
    LeaderResearchInputProviderBatchInput,
    LeaderResearchInputProviderBatchStatus,
    LeaderResearchInputProviderItemStatus,
    build_leader_research_input_provider_batch,
)
from tests import (
    test_radar_leader_research_readiness_runtime_batch as f4_test_helpers,
)


AS_OF = f4_test_helpers.AS_OF


class LeaderResearchInputProviderBatchTests(unittest.TestCase):
    def setUp(self):
        self.f4 = (
            f4_test_helpers.LeaderResearchReadinessRuntimeBatchTests(
                methodName=(
                    "test_result_contract_is_frozen_and_"
                    "keeps_formal_flags_closed"
                )
            )
        )
        self.f4.setUp()
        self.runtime = self.f4.runtime
        self.assembly, self.risk_batch = self.f4.runtime_assembly()

    def business_input(self, symbol):
        value = self.runtime.business_catalyst_input()
        proof_id = f"business-proof-{symbol}"
        proofs = tuple(
            replace(
                proof,
                symbol=symbol,
                evidence_id=proof_id,
                document_id=f"business-document-{symbol}",
            )
            for proof in value.business_proofs
        )
        reviews = tuple(
            replace(
                review,
                symbol=symbol,
                review_id=f"review-{symbol}",
                basis_evidence_ids=(proof_id,),
            )
            for review in value.reviews
        )
        return replace(
            value,
            symbol=symbol,
            business_proofs=proofs,
            reviews=reviews,
        )

    def ready_entry(self, symbol):
        return LeaderResearchInputProviderBatchEntry(
            symbol=symbol,
            history_input=self.runtime.history_input(symbol),
            business_catalyst_input=self.business_input(symbol),
            tradability_input=self.runtime.tradability_input(symbol),
        )

    def explicit_missing_entry(self, symbol):
        return LeaderResearchInputProviderBatchEntry(
            symbol=symbol,
            history_input=None,
            business_catalyst_input=None,
            tradability_input=None,
        )

    def provider_input(
        self,
        *,
        entries=None,
        risk_batch=None,
        radar_run_id=None,
        as_of=None,
        provider_contract_id="fixture-research-provider-v1",
    ):
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )
        return LeaderResearchInputProviderBatchInput(
            assembly=self.assembly,
            radar_run_id=(
                self.assembly.radar_run_id
                if radar_run_id is None
                else radar_run_id
            ),
            as_of=self.assembly.as_of if as_of is None else as_of,
            provider_contract_id=provider_contract_id,
            entries=(
                tuple(self.ready_entry(symbol) for symbol in symbols)
                if entries is None
                else entries
            ),
            risk_projection_batch=(
                self.risk_batch
                if risk_batch is None
                else risk_batch
            ),
        )

    def test_ready_batch_keeps_candidate_order_and_read_only_typed_maps(self):
        result = build_leader_research_input_provider_batch(
            self.provider_input()
        )
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )

        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.READY,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            symbols,
        )
        self.assertEqual(tuple(result.history_inputs_by_symbol), symbols)
        self.assertEqual(
            tuple(result.business_catalyst_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            tuple(result.tradability_inputs_by_symbol),
            symbols,
        )
        self.assertEqual(
            result.contract_id,
            LEADER_RESEARCH_INPUT_PROVIDER_BATCH_CONTRACT_ID,
        )
        reversed_result = build_leader_research_input_provider_batch(
            self.provider_input(
                entries=tuple(reversed(self.provider_input().entries))
            )
        )
        self.assertEqual(
            tuple(item.symbol for item in reversed_result.items),
            symbols,
        )
        with self.assertRaises(TypeError):
            result.history_inputs_by_symbol["000999"] = (
                self.runtime.history_input("000999")
            )
        with self.assertRaises(FrozenInstanceError):
            result.formal_usable = True
        self.assertNotIn("AdjustedHistoryPoint", repr(result))
        self.assertNotIn("decision_summary", repr(result))
        self.assertIs(result.formal_score_ready, False)
        self.assertIs(result.formal_gate_ready, False)
        self.assertIs(result.formal_usable, False)
        self.assertIs(result.state_transition_allowed, False)

    def test_explicit_missing_inputs_and_non_ready_e3_are_partial_not_forged(self):
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )
        entries = (
            self.ready_entry(symbols[0]),
            *(self.explicit_missing_entry(symbol) for symbol in symbols[1:]),
        )
        risk_batch = self.f4.risk_batch(
            symbols,
            missing_symbols=(symbols[1],),
        )
        result = build_leader_research_input_provider_batch(
            self.provider_input(entries=entries, risk_batch=risk_batch)
        )

        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[0].status,
            LeaderResearchInputProviderItemStatus.READY,
        )
        self.assertEqual(
            result.items[1].status,
            LeaderResearchInputProviderItemStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].missing_inputs,
            (
                "history",
                "business_catalyst",
                "tradability",
                "risk_projection",
            ),
        )
        self.assertEqual(
            tuple(result.history_inputs_by_symbol),
            (symbols[0],),
        )
        self.assertIs(
            result.risk_projection_batch.items[1].status,
            ResearchFeatureStatus.MISSING,
        )

    def test_declared_failed_component_inputs_are_partial_not_ready(self):
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )
        base_entries = tuple(self.ready_entry(symbol) for symbol in symbols)
        second = base_entries[1]
        cases = (
            (
                replace(
                    second,
                    history_input=replace(
                        second.history_input,
                        candidate=replace(
                            second.history_input.candidate,
                            status=ResearchFeatureStatus.SOURCE_FAILED,
                        ),
                    ),
                ),
                "leader_research_history_source_not_ready",
            ),
            (
                replace(
                    second,
                    business_catalyst_input=replace(
                        second.business_catalyst_input,
                        source_status=ResearchFeatureStatus.SOURCE_FAILED,
                    ),
                ),
                "leader_research_business_source_not_ready",
            ),
            (
                replace(
                    second,
                    tradability_input=replace(
                        second.tradability_input,
                        quote_source_status=ResearchFeatureStatus.SOURCE_FAILED,
                    ),
                ),
                "leader_research_tradability_source_not_ready",
            ),
        )

        for malformed, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                entries = list(base_entries)
                entries[1] = malformed
                result = build_leader_research_input_provider_batch(
                    self.provider_input(entries=tuple(entries))
                )

                self.assertEqual(
                    result.status,
                    LeaderResearchInputProviderBatchStatus.PARTIAL,
                )
                self.assertEqual(
                    result.items[1].status,
                    LeaderResearchInputProviderItemStatus.PARTIAL,
                )
                self.assertEqual(
                    result.items[1].reasons,
                    (expected_reason,),
                )
                self.assertIn(symbols[1], result.history_inputs_by_symbol)

    def test_outer_run_time_candidate_and_container_errors_block_whole_batch(self):
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )
        valid_entries = tuple(self.ready_entry(symbol) for symbol in symbols)
        cases = (
            (
                None,
                "leader_research_input_provider_batch_contract_unverified",
            ),
            (
                self.provider_input(radar_run_id="other-run"),
                "leader_research_input_provider_batch_run_mismatch",
            ),
            (
                self.provider_input(
                    as_of=AS_OF - timedelta(minutes=1)
                ),
                "leader_research_input_provider_batch_as_of_mismatch",
            ),
            (
                self.provider_input(entries=list(valid_entries)),
                "leader_research_input_provider_batch_contract_unverified",
            ),
            (
                self.provider_input(entries=valid_entries[:-1]),
                "leader_research_input_provider_batch_candidate_mismatch",
            ),
            (
                self.provider_input(
                    entries=(*valid_entries, self.ready_entry("000999"))
                ),
                "leader_research_input_provider_batch_candidate_mismatch",
            ),
            (
                self.provider_input(
                    entries=(valid_entries[0], valid_entries[0], *valid_entries[2:])
                ),
                "leader_research_input_provider_batch_candidate_mismatch",
            ),
        )
        for input_value, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_research_input_provider_batch(
                    input_value
                )
                self.assertEqual(
                    result.status,
                    LeaderResearchInputProviderBatchStatus.BLOCKED,
                )
                self.assertEqual(result.reasons, (expected_reason,))
                self.assertEqual(result.history_inputs_by_symbol, {})
                self.assertEqual(
                    result.business_catalyst_inputs_by_symbol,
                    {},
                )
                self.assertEqual(result.tradability_inputs_by_symbol, {})

    def test_typed_identity_and_as_of_mismatches_are_isolated_per_candidate(self):
        symbols = tuple(
            item.symbol for item in self.assembly.evidence_items
        )
        base_entries = tuple(self.ready_entry(symbol) for symbol in symbols)
        second = base_entries[1]
        cases = (
            replace(
                second,
                history_input=replace(
                    second.history_input,
                    candidate=replace(
                        second.history_input.candidate,
                        symbol="000999",
                    ),
                ),
            ),
            replace(
                second,
                business_catalyst_input=replace(
                    second.business_catalyst_input,
                    symbol="000999",
                ),
            ),
            replace(
                second,
                tradability_input=replace(
                    second.tradability_input,
                    quote=second.tradability_input.quote.model_copy(
                        update={"symbol": "000999"},
                    ),
                ),
            ),
            replace(
                second,
                history_input=replace(
                    second.history_input,
                    as_of=AS_OF - timedelta(minutes=1),
                ),
            ),
        )
        for malformed in cases:
            with self.subTest(malformed=type(malformed)):
                entries = list(base_entries)
                entries[1] = malformed
                result = build_leader_research_input_provider_batch(
                    self.provider_input(entries=tuple(entries))
                )
                self.assertEqual(
                    result.status,
                    LeaderResearchInputProviderBatchStatus.PARTIAL,
                )
                self.assertEqual(
                    result.items[1].status,
                    LeaderResearchInputProviderItemStatus.BLOCKED,
                )
                self.assertNotIn(
                    symbols[1],
                    result.history_inputs_by_symbol,
                )
                self.assertIn(
                    symbols[0],
                    result.history_inputs_by_symbol,
                )

    def test_equivalent_timezone_is_accepted_but_e3_absence_blocks(self):
        equivalent = AS_OF.astimezone(timezone(timedelta(hours=8)))
        accepted = build_leader_research_input_provider_batch(
            self.provider_input(as_of=equivalent)
        )
        missing_e3 = replace(
            self.risk_batch,
            input_count=len(self.risk_batch.items) - 1,
            items=self.risk_batch.items[:-1],
        )
        blocked = build_leader_research_input_provider_batch(
            self.provider_input(risk_batch=missing_e3)
        )

        self.assertEqual(
            accepted.status,
            LeaderResearchInputProviderBatchStatus.READY,
        )
        self.assertEqual(
            blocked.status,
            LeaderResearchInputProviderBatchStatus.BLOCKED,
        )
        self.assertEqual(
            blocked.reasons,
            (
                "leader_research_input_provider_batch_"
                "risk_contract_unverified",
            ),
        )

    def test_provider_validation_does_not_run_any_research_feature_builder(self):
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
                    f"provider validation must not call {name}"
                ),
            )
            for name in names
        )
        mocks = []
        try:
            for mock_patch in patches:
                mocks.append(mock_patch.start())
            result = build_leader_research_input_provider_batch(
                self.provider_input()
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertEqual(
            result.status,
            LeaderResearchInputProviderBatchStatus.READY,
        )
        self.assertTrue(all(mock.call_count == 0 for mock in mocks))

if __name__ == "__main__":
    unittest.main()
