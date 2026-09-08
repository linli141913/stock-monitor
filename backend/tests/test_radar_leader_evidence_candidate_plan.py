import unittest
from dataclasses import replace
from datetime import time, timedelta

from radar.leader_evidence_candidate_plan import (
    LEADER_EVIDENCE_CANDIDATE_PLAN_CONTRACT_ID,
    LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_SOURCE_CONTRACT_ID,
    _INDUSTRY_STATE_PRODUCER_TOKEN,
    _PREVIOUS_STATE_REPOSITORY_TOKEN,
    LeaderActiveIndustryState,
    LeaderEvidenceCandidateIndustryScope,
    LeaderEvidenceCandidateIndustryScopeItem,
    LeaderEvidenceCandidatePlanInput,
    LeaderEvidenceCandidatePlanStatus,
    LeaderEvidencePreviousStateSnapshot,
    LeaderEvidenceCandidateSelectionPolicy,
    build_leader_evidence_candidate_industry_scope,
    build_leader_evidence_candidate_runtime_inputs,
    build_leader_evidence_candidate_plan,
    build_leader_evidence_scope_research_assembly,
    is_leader_evidence_candidate_plan_valid,
    load_leader_evidence_previous_state_snapshot,
)
from radar.leader_research_runtime_provider import (
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_runtime_candidate_plan import (
    derive_leader_runtime_candidate_plan_subset,
    is_leader_runtime_candidate_plan_valid,
)
from radar.leader_state_machine import (
    LEADER_STATE_MACHINE_VERSION,
    LeaderState,
    LeaderStateRecord,
)
from radar.sources.leader_tradability_public_poc import (
    run_public_composite_tradability_poc,
)
from radar.sector_state_producer import (
    SECTOR_STATE_TRANSITION_POLICY_VERSION,
    SectorLifecycleState,
    build_initial_sector_state_snapshot,
    produce_sector_state_from_runtime,
    sector_threshold_record_sha256,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)
from tests import (
    test_radar_leader_research_source_admission as admission_helpers,
)
from tests import (
    test_radar_leader_history_production_collector as history_helpers,
)
from tests import (
    test_radar_leader_tradability_production_collector as tradability_helpers,
)
from tests import (
    test_radar_sector_rule_runtime_bridge as sector_helpers,
)
from tests import test_radar_leader_repository as repository_helpers
from tests import test_radar_sector_state_producer as state_helpers


class LeaderEvidenceCandidatePlanTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        self.preliminary_plan = self.f6.candidate_plan()
        symbols = tuple(
            item.symbol for item in self.preliminary_plan.items
        )
        runtime = self.f6.runtime
        self.assembly = runtime.build_full_research(
            history_inputs_by_symbol={
                symbol: runtime.history_input(symbol)
                for symbol in symbols
            },
            tradability_inputs_by_symbol={
                symbol: runtime.tradability_input(symbol)
                for symbol in symbols
            },
        )
        sector = sector_helpers.SectorRuleRuntimeBridgeTests(
            methodName=(
                "test_complete_versioned_evidence_returns_ready_contract"
            )
        )
        sector.setUp()
        self.sector_rule_bridge = sector.build(sector.source_batch())
        self.assertEqual(sector.plan, self.preliminary_plan)

    def industry_scope(self, *items):
        scope = LeaderEvidenceCandidateIndustryScope(
            candidate_plan_id=self.preliminary_plan.candidate_set_id,
            radar_run_id=self.preliminary_plan.radar_run_id,
            as_of=self.preliminary_plan.as_of,
            items=tuple(items),
            sector_rule_bridge=self.sector_rule_bridge,
        )
        object.__setattr__(
            scope,
            "_producer_token",
            _INDUSTRY_STATE_PRODUCER_TOKEN,
        )
        return scope

    def active_scope(self):
        return self.industry_scope(
            LeaderEvidenceCandidateIndustryScopeItem(
                industry_code="66",
                state=LeaderActiveIndustryState.OBSERVE,
            )
        )

    def produced_industry_scope(self):
        source_batch = sector_helpers.SectorRuleRuntimeBridgeTests(
            methodName=(
                "test_complete_versioned_evidence_returns_ready_contract"
            )
        )
        source_batch.setUp()
        frozen = source_batch.source_batch()
        code = self.preliminary_plan.items[0].industry_code
        coverage = next(
            item for item in frozen.history_evidence.rows
            if item.division_code == code
        )
        from radar.leader_phase6_live_prefreeze import (
            resolve_sector_comparable_time,
        )
        comparable_time = resolve_sector_comparable_time(
            frozen.feature_batch.source_time
        )
        approval = replace(
            state_helpers.approval(),
            threshold_set_id=(
                frozen.threshold_approval_evidence.threshold_set_id
            ),
            approval_id=frozen.threshold_approval_evidence.approval_id,
            approved_at=frozen.threshold_approval_evidence.approved_at,
        )
        approval = replace(
            approval,
            record_sha256=sector_threshold_record_sha256(approval),
        )
        analysis = replace(state_helpers.history(
            code,
            dates=coverage.same_minute_trading_dates,
        ), comparable_time=comparable_time)
        initial = build_initial_sector_state_snapshot(
            industry_codes=(code,),
            classification_document_sha256=(
                frozen.feature_batch.classification_document_sha256
            ),
            rule_version=frozen.rule_version,
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approval.threshold_set_id,
            approval_id=approval.approval_id,
            observed_before=self.preliminary_plan.as_of - timedelta(
                seconds=1
            ),
        )
        production = produce_sector_state_from_runtime(
            source_batch.context,
            source_batch=frozen,
            historical_analyses=(analysis,),
            approval_record=approval,
            comparable_time=comparable_time,
            previous_snapshot=initial,
        )
        scope = build_leader_evidence_candidate_industry_scope(
            plan=self.preliminary_plan,
            sector_rule_bridge=self.sector_rule_bridge,
            state_production=production,
        )
        return production, scope

    def previous_state(self, symbol, state=LeaderState.CONFIRMED):
        as_of = self.preliminary_plan.as_of
        return LeaderStateRecord(
            symbol=symbol,
            state=state,
            state_age_periods=3,
            rule_version=LEADER_STATE_MACHINE_VERSION,
            state_since=as_of,
            last_evaluated_at=as_of,
        )

    def previous_state_snapshot(
        self,
        *records,
        expected_record_count=None,
        complete=True,
    ):
        snapshot = LeaderEvidencePreviousStateSnapshot(
            candidate_plan_id=self.preliminary_plan.candidate_set_id,
            as_of=self.preliminary_plan.as_of,
            source_contract_id=(
                LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_SOURCE_CONTRACT_ID
            ),
            expected_record_count=(
                len(records)
                if expected_record_count is None
                else expected_record_count
            ),
            records=tuple(records),
            complete=complete,
        )
        object.__setattr__(
            snapshot,
            "_repository_token",
            _PREVIOUS_STATE_REPOSITORY_TOKEN,
        )
        return snapshot

    def build(self, **changes):
        values = {
            "preliminary_plan": self.preliminary_plan,
            "runtime_assembly": self.assembly,
            "industry_scope": self.active_scope(),
            "previous_state_snapshot": self.previous_state_snapshot(),
            "policy": LeaderEvidenceCandidateSelectionPolicy(
                maximum_new_candidates=2,
                maximum_incumbent_candidates=15,
            ),
        }
        values.update(changes)
        return build_leader_evidence_candidate_plan(
            LeaderEvidenceCandidatePlanInput(**values)
        )

    def test_research_ranking_derives_small_plan_with_parent_provenance(self):
        result = self.build()

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidatePlanStatus.READY,
        )
        self.assertEqual(
            result.contract_id,
            LEADER_EVIDENCE_CANDIDATE_PLAN_CONTRACT_ID,
        )
        self.assertEqual(
            result.preliminary_candidate_plan_id,
            self.preliminary_plan.candidate_set_id,
        )
        self.assertIsNotNone(result.candidate_plan)
        self.assertTrue(
            is_leader_runtime_candidate_plan_valid(result.candidate_plan)
        )
        self.assertNotEqual(
            result.candidate_plan.candidate_set_id,
            self.preliminary_plan.candidate_set_id,
        )
        self.assertEqual(
            result.candidate_plan.parent_candidate_set_id,
            self.preliminary_plan.candidate_set_id,
        )
        self.assertEqual(
            result.candidate_plan.derivation_policy_id,
            result.selection_policy_id,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            ("000001", "000002"),
        )
        self.assertEqual(
            tuple(
                round(item.research_partial_score, 6)
                for item in result.items
            ),
            (53.4, 44.729293),
        )
        self.assertEqual(result.preliminary_candidate_count, 5)
        self.assertEqual(result.candidate_count, 2)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_existing_state_is_retained_even_below_new_candidate_limit(self):
        result = self.build(
            previous_state_snapshot=self.previous_state_snapshot(
                self.previous_state("000005"),
            ),
            policy=LeaderEvidenceCandidateSelectionPolicy(
                maximum_new_candidates=1,
                maximum_incumbent_candidates=15,
            ),
        )

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidatePlanStatus.READY,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            ("000005", "000001"),
        )
        self.assertEqual(
            tuple(item.selection_kind for item in result.items),
            ("incumbent", "new_candidate"),
        )

    def test_no_active_industry_and_no_incumbent_is_a_legitimate_empty_scope(self):
        result = self.build(industry_scope=self.industry_scope(
            LeaderEvidenceCandidateIndustryScopeItem(
                industry_code="66",
                state=LeaderActiveIndustryState.INACTIVE,
            )
        ))

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidatePlanStatus.EMPTY,
        )
        self.assertEqual(result.candidate_count, 0)
        self.assertIsNone(result.candidate_plan)
        self.assertEqual(result.reasons, ("leader_evidence_scope_empty",))
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_incumbent_missing_from_preliminary_pool_blocks_instead_of_dropping(self):
        result = self.build(
            previous_state_snapshot=self.previous_state_snapshot(
                self.previous_state("000006"),
            )
        )

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidatePlanStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("leader_incumbent_outside_preliminary_scope",),
        )
        self.assertIsNone(result.candidate_plan)

    def test_cross_run_assembly_and_scope_identity_fail_closed(self):
        cross_run_assembly = replace(
            self.assembly,
            radar_run_id="another-run",
        )
        assembly_result = self.build(
            runtime_assembly=cross_run_assembly,
        )
        scope_result = self.build(
            industry_scope=replace(
                self.active_scope(),
                candidate_plan_id="another-plan",
            ),
        )

        self.assertEqual(
            assembly_result.status,
            LeaderEvidenceCandidatePlanStatus.BLOCKED,
        )
        self.assertEqual(
            assembly_result.reasons,
            ("leader_evidence_runtime_assembly_unverified",),
        )
        self.assertEqual(
            scope_result.status,
            LeaderEvidenceCandidatePlanStatus.BLOCKED,
        )
        self.assertEqual(
            scope_result.reasons,
            ("leader_evidence_industry_scope_unverified",),
        )

    def test_unknown_industry_and_nonready_sector_bridge_fail_closed(self):
        unknown = self.build(industry_scope=replace(
            self.active_scope(),
            items=(LeaderEvidenceCandidateIndustryScopeItem(
                industry_code="forged",
                state=LeaderActiveIndustryState.CONFIRMED,
            ),),
        ))
        nonready = self.build(industry_scope=replace(
            self.active_scope(),
            sector_rule_bridge=replace(
                self.sector_rule_bridge,
                status=self.sector_rule_bridge.status.MISSING,
            ),
        ))

        self.assertEqual(
            unknown.reasons,
            ("leader_evidence_industry_scope_unverified",),
        )
        self.assertEqual(
            nonready.reasons,
            ("leader_evidence_industry_scope_unverified",),
        )

    def test_public_scope_builder_only_accepts_real_state_producer(self):
        production, scope = self.produced_industry_scope()

        self.assertEqual(
            scope.items,
            (LeaderEvidenceCandidateIndustryScopeItem(
                industry_code="66",
                state=LeaderActiveIndustryState.OBSERVE,
            ),),
        )
        self.assertEqual(
            production.items[0].state,
            SectorLifecycleState.OBSERVE,
        )
        with self.assertRaisesRegex(
            ValueError,
            "industry_state_production_unverified",
        ):
            build_leader_evidence_candidate_industry_scope(
                plan=self.preliminary_plan,
                sector_rule_bridge=self.sector_rule_bridge,
                state_production=replace(production),
            )

    def test_incomplete_previous_state_snapshot_fails_closed(self):
        incomplete = self.build(
            previous_state_snapshot=self.previous_state_snapshot(
                expected_record_count=1,
            )
        )
        untrusted = self.build(previous_state_snapshot=replace(
            self.previous_state_snapshot(),
        ))

        self.assertEqual(
            incomplete.reasons,
            ("leader_evidence_previous_states_unverified",),
        )
        self.assertEqual(
            untrusted.reasons,
            ("leader_evidence_previous_states_unverified",),
        )

    def test_replacing_trusted_snapshot_cannot_resign_omitted_records(self):
        trusted = self.previous_state_snapshot(
            self.previous_state("000005"),
        )
        forged = replace(
            trusted,
            records=(),
            expected_record_count=0,
            snapshot_id=None,
        )

        self.assertIsNone(forged._repository_token)
        self.assertEqual(
            self.build(previous_state_snapshot=forged).reasons,
            ("leader_evidence_previous_states_unverified",),
        )

    def test_public_previous_state_snapshot_comes_from_existing_repository(self):
        helper = repository_helpers.LeaderRepositoryTests(
            methodName=(
                "test_previous_state_reader_excludes_current_and_future_records"
            )
        )
        helper.setUp()
        try:
            helper.repository.save_state_transition(helper.transition())
            snapshot = load_leader_evidence_previous_state_snapshot(
                helper.repository,
                plan=self.preliminary_plan,
            )
        finally:
            helper.tearDown()

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.expected_record_count, 1)
        self.assertEqual(
            tuple(record.symbol for record in snapshot.records),
            ("000001",),
        )
        self.assertEqual(
            self.build(previous_state_snapshot=snapshot).status,
            LeaderEvidenceCandidatePlanStatus.READY,
        )

    def test_child_plan_identity_binds_parent_and_selection_policy(self):
        alternate_parent = derive_leader_runtime_candidate_plan_subset(
            self.preliminary_plan,
            symbols=tuple(
                item.symbol for item in self.preliminary_plan.items[:-1]
            ),
            derivation_policy_id="alternate-parent-policy-v1",
        )
        first = derive_leader_runtime_candidate_plan_subset(
            self.preliminary_plan,
            symbols=("000001",),
            derivation_policy_id="same-child-policy-v1",
        )
        second = derive_leader_runtime_candidate_plan_subset(
            alternate_parent,
            symbols=("000001",),
            derivation_policy_id="same-child-policy-v1",
        )

        self.assertTrue(is_leader_runtime_candidate_plan_valid(first))
        self.assertTrue(is_leader_runtime_candidate_plan_valid(second))
        self.assertNotEqual(first.candidate_set_id, second.candidate_set_id)
        self.assertEqual(
            first.parent_candidate_set_id,
            self.preliminary_plan.candidate_set_id,
        )
        self.assertEqual(
            second.parent_candidate_set_id,
            alternate_parent.candidate_set_id,
        )

    def test_evidence_validator_rejects_valid_child_with_wrong_policy(self):
        result = self.build()
        wrong_policy_child = derive_leader_runtime_candidate_plan_subset(
            self.preliminary_plan,
            symbols=tuple(item.symbol for item in result.items),
            derivation_policy_id="wrong-policy-v1",
        )

        self.assertTrue(
            is_leader_runtime_candidate_plan_valid(wrong_policy_child)
        )
        self.assertFalse(is_leader_evidence_candidate_plan_valid(replace(
            result,
            candidate_plan=wrong_policy_child,
        )))

    def test_ready_scope_reuses_existing_runtime_context_for_five_sources(self):
        evidence_plan = self.build()
        raw = self.f6.raw_inputs()
        preliminary_runtime = replace(
            self.f6.orchestration_input(
                self.preliminary_plan,
                self.f6.provider_input(self.preliminary_plan),
            ),
        )
        from radar.leader_live_candidate_collection_batch import (
            LeaderLiveCandidateRuntimeInputs,
        )

        runtime = LeaderLiveCandidateRuntimeInputs(
            candidate_plan=self.preliminary_plan,
            source_context=preliminary_runtime.source_context,
            as_of=self.preliminary_plan.as_of,
            security_master_batch=raw["quote_batch"],
            quote_batch=raw["quote_batch"],
            index_batch=raw["quote_batch"],
            classification_snapshot=object(),
            quote_health=raw["quote_health"],
            market_snapshot=raw["market_snapshot"],
            sector_rows=raw["sector_rows"],
            sector_feature_batch=object(),
            industry_records=raw["industry_records"],
            industry_release=object(),
            security_records=raw["security_records"],
            etf_symbols=(),
        )

        narrowed = build_leader_evidence_candidate_runtime_inputs(
            runtime,
            evidence_plan,
        )

        self.assertEqual(
            tuple(item.symbol for item in narrowed.candidate_plan.items),
            ("000001", "000002"),
        )
        self.assertTrue(
            is_leader_research_runtime_source_context_valid(
                narrowed.source_context
            )
        )
        self.assertEqual(
            tuple(narrowed.source_context.quotes_by_symbol),
            ("000001", "000002"),
        )
        self.assertEqual(
            self.preliminary_plan.candidate_count,
            5,
        )

        forged_parent_index = replace(
            evidence_plan,
            items=(
                replace(evidence_plan.items[0], parent_index=1),
                *evidence_plan.items[1:],
            ),
        )
        with self.assertRaisesRegex(ValueError, "runtime_inputs_unverified"):
            build_leader_evidence_candidate_runtime_inputs(
                runtime,
                forged_parent_index,
            )

    def test_lightweight_assembly_replays_existing_history_and_tradability(self):
        helper = admission_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_"
                "in_one_batch"
            )
        )
        helper.setUp()
        raw = helper.raw
        from radar.leader_live_candidate_collection_batch import (
            LeaderLiveCandidateRuntimeInputs,
        )

        runtime = LeaderLiveCandidateRuntimeInputs(
            candidate_plan=helper.plan,
            source_context=helper.context,
            as_of=helper.plan.as_of,
            security_master_batch=raw["quote_batch"],
            quote_batch=raw["quote_batch"],
            index_batch=raw["quote_batch"],
            classification_snapshot=object(),
            quote_health=raw["quote_health"],
            market_snapshot=raw["market_snapshot"],
            sector_rows=raw["sector_rows"],
            sector_feature_batch=object(),
            industry_records=raw["industry_records"],
            industry_release=object(),
            security_records=raw["security_records"],
            etf_symbols=(),
        )
        history = history_helpers.LeaderHistoryProductionCollectorTests(
            methodName=(
                "test_complete_candidate_universe_replays_history_in_plan_order"
            )
        )
        history.setUp()
        tradability = (
            tradability_helpers.LeaderTradabilityProductionCollectorTests(
                methodName=(
                    "test_complete_candidate_set_replays_into_existing_provider"
                )
            )
        )
        tradability.setUp()
        self.assertEqual(history.context, helper.context)
        self.assertEqual(tradability.context, helper.context)

        assembly = build_leader_evidence_scope_research_assembly(
            runtime,
            history_batch=history.bundle(),
            tradability_batch=tradability.frozen(),
        )

        self.assertEqual(assembly.status, "ready")
        self.assertEqual(assembly.item_count, 5)
        first = assembly.research_component_items[0]
        self.assertEqual(first.history_features.status.value, "ready")
        self.assertEqual(
            first.tradability_features.status.value,
            "ready",
        )
        self.assertEqual(
            first.business_catalyst_features.status.value,
            "missing",
        )
        self.assertFalse(assembly.evidence_items[0].gates.risk_filter_passed)

    def test_lightweight_assembly_reuses_collector_clock_skew_contract(self):
        helper = admission_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_"
                "in_one_batch"
            )
        )
        helper.setUp()
        raw = helper.raw
        from radar.leader_live_candidate_collection_batch import (
            LeaderLiveCandidateRuntimeInputs,
        )

        runtime = LeaderLiveCandidateRuntimeInputs(
            candidate_plan=helper.plan,
            source_context=helper.context,
            as_of=helper.plan.as_of,
            security_master_batch=raw["quote_batch"],
            quote_batch=raw["quote_batch"],
            index_batch=raw["quote_batch"],
            classification_snapshot=object(),
            quote_health=raw["quote_health"],
            market_snapshot=raw["market_snapshot"],
            sector_rows=raw["sector_rows"],
            sector_feature_batch=object(),
            industry_records=raw["industry_records"],
            industry_release=object(),
            security_records=raw["security_records"],
            etf_symbols=(),
        )
        history = history_helpers.LeaderHistoryProductionCollectorTests(
            methodName=(
                "test_complete_candidate_universe_replays_history_in_plan_order"
            )
        )
        history.setUp()
        tradability = (
            tradability_helpers.LeaderTradabilityProductionCollectorTests(
                methodName=(
                    "test_official_ten_second_bucket_skew_is_accepted"
                )
            )
        )
        tradability.setUp()
        official = tuple(
            replace(
                item,
                source_time=item.fetched_at + timedelta(seconds=1),
            )
            for item in tradability.bundle.official_observations
        )
        report = run_public_composite_tradability_poc(
            query=tradability.bundle.query,
            quotes=tradability.bundle.quotes,
            official_observations=official,
            aggregator_observations=(
                tradability.bundle.aggregator_observations
            ),
            executed=True,
        )
        skewed = replace(
            tradability.bundle,
            official_observations=official,
            report=report,
        )

        assembly = build_leader_evidence_scope_research_assembly(
            runtime,
            history_batch=history.bundle(),
            tradability_batch=tradability.frozen(bundle=skewed),
        )

        self.assertEqual(assembly.status, "ready")
        self.assertEqual(assembly.item_count, helper.plan.candidate_count)

    def test_malformed_nested_research_features_fail_closed(self):
        malformed = replace(
            self.assembly,
            research_component_items=(
                replace(
                    self.assembly.research_component_items[0],
                    cross_sectional_features=object(),
                ),
                *self.assembly.research_component_items[1:],
            ),
        )

        result = self.build(runtime_assembly=malformed)

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidatePlanStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("leader_evidence_runtime_assembly_unverified",),
        )

    def test_forged_nested_items_fail_closed_without_validator_exception(self):
        result = self.build()
        forged = replace(
            result,
            items=(
                *result.items,
                replace(result.items[0], index=len(result.items)),
            ),
        )

        self.assertFalse(
            is_leader_evidence_candidate_plan_valid(forged)
        )


if __name__ == "__main__":
    unittest.main()
