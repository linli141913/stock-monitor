import dataclasses
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest.mock import patch

from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureResult,
)
from radar.leader_history_features import LeaderHistoryFeatureResult
from radar.leader_liquidity_features import LeaderLiquidityFeatureResult
from radar.leader_research_features import (
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
)
from radar.leader_research_readiness_audit_batch import (
    LeaderResearchReadinessAuditBatchStatus,
)
from radar.leader_research_readiness_runtime_batch import (
    LEADER_RESEARCH_READINESS_RUNTIME_BATCH_CONTRACT_ID,
    LeaderResearchReadinessRuntimeBatchInput,
    LeaderResearchReadinessRuntimeBatchStatus,
    build_leader_research_readiness_runtime_batch,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.leader_runtime_inputs import LeaderResearchComponentBatchItem
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureResult,
)
from tests import (
    test_radar_leader_research_readiness_audit as audit_test_helpers,
)
from tests import test_radar_leader_runtime_inputs as runtime_test_helpers


AS_OF = runtime_test_helpers.AS_OF


class LeaderResearchReadinessRuntimeBatchTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_test_helpers.LeaderRuntimeInputsTests(
            methodName=(
                "test_future_market_snapshot_is_rejected_before_candidate_build"
            )
        )

    def risk_batch(
        self,
        symbols,
        *,
        as_of=AS_OF,
        missing_symbols=(),
    ):
        missing_symbols = set(missing_symbols)
        items = tuple(
            LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=symbol,
                input_symbol=symbol,
                status=(
                    ResearchFeatureStatus.MISSING
                    if symbol in missing_symbols
                    else ResearchFeatureStatus.READY
                ),
                reasons=(
                    ("risk_evidence_bundle_missing",)
                    if symbol in missing_symbols
                    else ()
                ),
                projection=(
                    None
                    if symbol in missing_symbols
                    else self.runtime.risk_candidate_projection(
                        symbol=symbol,
                        as_of=as_of,
                    )
                ),
            )
            for index, symbol in enumerate(symbols)
        )
        ready_count = sum(item.included for item in items)
        if not items:
            status = LeaderRiskCandidateProjectionBatchStatus.MISSING
            reasons = ("risk_candidate_projection_batch_empty",)
        elif ready_count == len(items):
            status = LeaderRiskCandidateProjectionBatchStatus.READY
            reasons = ()
        elif ready_count:
            status = LeaderRiskCandidateProjectionBatchStatus.PARTIAL
            reasons = ("risk_candidate_projection_batch_partial",)
        else:
            status = LeaderRiskCandidateProjectionBatchStatus.MISSING
            reasons = (
                "risk_candidate_projection_batch_no_ready_items",
            )
        return LeaderRiskCandidateProjectionBatchResult(
            status=status,
            as_of=as_of,
            input_count=len(items),
            items=items,
            reasons=reasons,
        )

    def runtime_assembly(self):
        initial = self.runtime.build_full_research()
        symbols = tuple(item.symbol for item in initial.evidence_items)
        risk_batch = self.risk_batch(symbols)
        assembly = self.runtime.build_full_research(
            risk_candidate_projections_by_symbol=(
                risk_batch.projections_by_symbol
            )
        )
        return assembly, risk_batch

    def test_runtime_keeps_ordered_typed_components_bound_to_identity_and_time(self):
        assembly, _ = self.runtime_assembly()

        self.assertEqual(
            len(assembly.research_component_items),
            len(assembly.evidence_items),
        )
        for index, (component, evidence) in enumerate(zip(
            assembly.research_component_items,
            assembly.evidence_items,
        )):
            self.assertIsInstance(
                component,
                LeaderResearchComponentBatchItem,
            )
            self.assertEqual(component.index, index)
            self.assertEqual(component.symbol, evidence.symbol)
            self.assertEqual(component.as_of, evidence.as_of)
            self.assertIsInstance(
                component.cross_sectional_features,
                LeaderResearchFeatureResult,
            )
            self.assertIsInstance(
                component.history_features,
                LeaderHistoryFeatureResult,
            )
            self.assertIsInstance(
                component.liquidity_features,
                LeaderLiquidityFeatureResult,
            )
            self.assertIsInstance(
                component.business_catalyst_features,
                LeaderBusinessCatalystFeatureResult,
            )
            self.assertIsInstance(
                component.tradability_features,
                LeaderTradabilityFeatureResult,
            )
        self.assertTrue(
            dataclasses.is_dataclass(
                assembly.research_component_items[0]
            )
        )
        with self.assertRaises(FrozenInstanceError):
            assembly.research_component_items[0].symbol = "000999"
        with self.assertRaises(TypeError):
            assembly.research_component_items[0].history_features.metrics[
                "forged"
            ] = True

    def test_all_ready_components_build_ready_f2_and_keep_flags_exact_false(self):
        assembly, risk_batch = self.runtime_assembly()
        ready_components = tuple(
            replace(
                component,
                cross_sectional_features=(
                    audit_test_helpers.cross_sectional_features()
                ),
                history_features=audit_test_helpers.history_features(),
                liquidity_features=(
                    audit_test_helpers.liquidity_features()
                ),
                business_catalyst_features=(
                    audit_test_helpers.business_features()
                ),
                tradability_features=(
                    audit_test_helpers.tradability_features()
                ),
            )
            for component in assembly.research_component_items
        )
        result = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=replace(
                    assembly,
                    research_component_items=ready_components,
                ),
                risk_projection_batch=risk_batch,
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessRuntimeBatchStatus.READY,
        )
        self.assertEqual(
            result.audit_batch.status,
            LeaderResearchReadinessAuditBatchStatus.READY,
        )
        self.assertTrue(all(
            item.audit.status.value == "ready"
            for item in result.audit_batch.items
        ))
        self.assertIs(result.formal_score_ready, False)
        self.assertIs(result.formal_gate_ready, False)
        self.assertIs(result.formal_usable, False)
        self.assertIs(result.state_transition_allowed, False)
        for item in result.runtime_assembly.evidence_items:
            readiness = item.evidence["researchFeatures"][
                "researchReadinessAudit"
            ]
            self.assertEqual(readiness["status"], "ready")
            self.assertIs(readiness["formalScoreReady"], False)
            self.assertIs(readiness["formalGateReady"], False)
            self.assertIs(readiness["formalUsable"], False)
            self.assertIs(readiness["stateTransitionAllowed"], False)

    def test_builds_f2_and_attaches_f3_without_recomputing_components(self):
        assembly, risk_batch = self.runtime_assembly()
        original_evidence = assembly.evidence_items

        patches = tuple(
            patch(
                f"radar.leader_runtime_inputs.{name}",
                side_effect=AssertionError(
                    f"F4 must not recompute {name}"
                ),
            )
            for name in (
                "build_leader_research_features",
                "build_leader_history_features",
                "build_leader_liquidity_features",
                "build_leader_business_catalyst_features",
                "build_leader_tradability_features",
            )
        )
        started = []
        try:
            for mock_patch in patches:
                started.append(mock_patch.start())
            result = build_leader_research_readiness_runtime_batch(
                LeaderResearchReadinessRuntimeBatchInput(
                    assembly=assembly,
                    risk_projection_batch=risk_batch,
                )
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertEqual(
            result.status,
            LeaderResearchReadinessRuntimeBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.audit_batch.status,
            LeaderResearchReadinessAuditBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.component_count,
            len(assembly.evidence_items),
        )
        self.assertTrue(all(mock.call_count == 0 for mock in started))
        self.assertIsNot(result.runtime_assembly, assembly)
        self.assertEqual(
            result.runtime_assembly.research_component_items,
            assembly.research_component_items,
        )
        for before, after in zip(
            original_evidence,
            result.runtime_assembly.evidence_items,
        ):
            self.assertIsNot(before, after)
            self.assertEqual(before.dimensions, after.dimensions)
            self.assertEqual(before.gates, after.gates)
            self.assertEqual(before.sources, after.sources)
            self.assertEqual(before.invalidation, after.invalidation)
            self.assertEqual(
                before.evidence["researchFeatures"][
                    "researchReadinessAudit"
                ]["status"],
                "missing",
            )
            self.assertEqual(
                after.evidence["researchFeatures"][
                    "researchReadinessAudit"
                ]["status"],
                "partial",
            )
            self.assertEqual(
                after.evidence["researchFeatures"][
                    "riskCandidateProjection"
                ]["status"],
                "ready",
            )
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_f4_attaches_e3_risk_without_rebuilding_runtime_components(self):
        assembly = self.runtime.build_full_research()
        symbols = tuple(item.symbol for item in assembly.evidence_items)
        risk_batch = self.risk_batch(symbols)
        self.assertTrue(all(
            item.evidence["researchFeatures"][
                "riskCandidateProjection"
            ]["status"] == "missing"
            for item in assembly.evidence_items
        ))

        patches = tuple(
            patch(
                f"radar.leader_runtime_inputs.{name}",
                side_effect=AssertionError(
                    f"F4 risk attach must not recompute {name}"
                ),
            )
            for name in (
                "build_leader_research_features",
                "build_leader_history_features",
                "build_leader_liquidity_features",
                "build_leader_business_catalyst_features",
                "build_leader_tradability_features",
            )
        )
        mocks = []
        try:
            for mock_patch in patches:
                mocks.append(mock_patch.start())
            result = build_leader_research_readiness_runtime_batch(
                LeaderResearchReadinessRuntimeBatchInput(
                    assembly=assembly,
                    risk_projection_batch=risk_batch,
                )
            )
        finally:
            for mock_patch in reversed(patches):
                mock_patch.stop()

        self.assertTrue(all(mock.call_count == 0 for mock in mocks))
        self.assertTrue(all(
            item.evidence["researchFeatures"][
                "riskCandidateProjection"
            ]["status"] == "ready"
            for item in result.runtime_assembly.evidence_items
        ))
        self.assertTrue(all(
            item.evidence["researchFeatures"][
                "riskCandidateProjection"
            ]["status"] == "missing"
            for item in assembly.evidence_items
        ))

    def test_structured_missing_e3_evidence_is_partial_but_absence_blocks(self):
        assembly, full_risk_batch = self.runtime_assembly()
        first_symbol = assembly.evidence_items[0].symbol
        expected_symbols = tuple(
            item.symbol for item in assembly.evidence_items
        )
        risk_batch = self.risk_batch(
            expected_symbols,
            missing_symbols=(expected_symbols[1],),
        )

        result = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=risk_batch,
            )
        )

        self.assertEqual(
            tuple(item.symbol for item in result.audit_batch.items),
            expected_symbols,
        )
        self.assertEqual(
            tuple(result.audit_batch.audits_by_symbol),
            expected_symbols,
        )
        self.assertEqual(
            result.audit_batch.items[1].audit.item(
                "risk_projection"
            ).status,
            ResearchFeatureStatus.MISSING,
        )
        self.assertIn(
            "risk_projection",
            result.audit_batch.items[1].audit.missing_items,
        )
        self.assertEqual(
            tuple(full_risk_batch.projections_by_symbol),
            expected_symbols,
        )
        self.assertFalse(
            result.runtime_assembly.evidence_items[1].gates.risk_filter_passed
        )

        absent_item = replace(
            risk_batch,
            input_count=len(risk_batch.items) - 1,
            items=risk_batch.items[:-1],
        )
        blocked = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=absent_item,
            )
        )
        self.assertEqual(
            blocked.status,
            LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
        )

    def test_e3_input_order_cannot_reorder_candidates_and_nested_mismatch_is_local(self):
        assembly, risk_batch = self.runtime_assembly()
        reversed_items = tuple(
            replace(item, index=index)
            for index, item in enumerate(reversed(risk_batch.items))
        )
        reversed_batch = replace(risk_batch, items=reversed_items)

        ordered = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=reversed_batch,
            )
        )
        expected_symbols = tuple(
            item.symbol for item in assembly.evidence_items
        )
        self.assertEqual(
            tuple(item.symbol for item in ordered.audit_batch.items),
            expected_symbols,
        )

        malformed_items = list(risk_batch.items)
        malformed_items[1] = replace(
            malformed_items[1],
            projection=replace(
                malformed_items[1].projection,
                symbol="000999",
            ),
        )
        malformed_batch = replace(
            risk_batch,
            items=tuple(malformed_items),
        )
        isolated = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=malformed_batch,
            )
        )

        self.assertEqual(
            isolated.status,
            LeaderResearchReadinessRuntimeBatchStatus.PARTIAL,
        )
        self.assertEqual(
            isolated.audit_batch.items[1].status.value,
            "blocked",
        )
        self.assertEqual(
            isolated.audit_batch.items[1].reasons,
            ("leader_research_audit_identity_mismatch",),
        )
        self.assertEqual(
            isolated.runtime_assembly.evidence_items[0].evidence[
                "researchFeatures"
            ]["researchReadinessAudit"]["status"],
            "partial",
        )
        self.assertEqual(
            isolated.runtime_assembly.evidence_items[1].evidence[
                "researchFeatures"
            ]["researchReadinessAudit"]["status"],
            "missing",
        )

    def test_component_identity_or_time_mismatch_is_isolated(self):
        assembly, risk_batch = self.runtime_assembly()
        components = list(assembly.research_component_items)
        components[1] = replace(
            components[1],
            symbol="000999",
            as_of=AS_OF - timedelta(minutes=1),
        )
        malformed_assembly = replace(
            assembly,
            research_component_items=tuple(components),
        )

        result = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=malformed_assembly,
                risk_projection_batch=risk_batch,
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessRuntimeBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.audit_batch.items[1].status.value,
            "blocked",
        )
        self.assertEqual(
            result.audit_batch.items[1].reasons,
            (
                "leader_research_readiness_audit_"
                "batch_item_contract_unverified",
            ),
        )
        self.assertEqual(
            result.runtime_assembly.evidence_items[1].evidence[
                "researchFeatures"
            ]["researchReadinessAudit"]["status"],
            "missing",
        )
        self.assertEqual(
            result.runtime_assembly.evidence_items[0].evidence[
                "researchFeatures"
            ]["researchReadinessAudit"]["status"],
            "partial",
        )

    def test_invalid_outer_or_e3_batch_contract_blocks_without_mutating_runtime(self):
        assembly, risk_batch = self.runtime_assembly()
        invalid_outer = build_leader_research_readiness_runtime_batch(None)
        invalid_risk = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=replace(
                    risk_batch,
                    as_of=AS_OF.replace(tzinfo=None),
                ),
            )
        )

        self.assertEqual(
            invalid_outer.status,
            LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
        )
        self.assertIsNone(invalid_outer.runtime_assembly)
        self.assertEqual(
            invalid_outer.reasons,
            (
                "leader_research_readiness_"
                "runtime_batch_contract_unverified",
            ),
        )
        self.assertEqual(
            invalid_risk.status,
            LeaderResearchReadinessRuntimeBatchStatus.BLOCKED,
        )
        self.assertIs(invalid_risk.runtime_assembly, assembly)
        self.assertEqual(invalid_risk.audit_batch.audit_count, 0)
        self.assertEqual(
            invalid_risk.reasons,
            (
                "leader_research_readiness_"
                "runtime_batch_risk_contract_unverified",
            ),
        )
        self.assertEqual(
            assembly.evidence_items[0].evidence[
                "researchFeatures"
            ]["researchReadinessAudit"]["status"],
            "missing",
        )

    def test_result_contract_is_frozen_and_keeps_formal_flags_closed(self):
        assembly, risk_batch = self.runtime_assembly()
        result = build_leader_research_readiness_runtime_batch(
            LeaderResearchReadinessRuntimeBatchInput(
                assembly=assembly,
                risk_projection_batch=risk_batch,
            )
        )

        self.assertEqual(
            result.contract_id,
            LEADER_RESEARCH_READINESS_RUNTIME_BATCH_CONTRACT_ID,
        )
        with self.assertRaises(FrozenInstanceError):
            result.formal_usable = True


if __name__ == "__main__":
    unittest.main()
