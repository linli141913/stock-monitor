import hashlib
import unittest
from dataclasses import replace
from datetime import time, timedelta
from pathlib import Path
import tempfile

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_automatic_evidence import (
    LeaderBusinessAutomaticEvidenceSources,
    LeaderBusinessAutomaticEvidenceBatchResult,
    run_leader_business_automatic_evidence,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueItem,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSourcePacketStatus,
    load_leader_business_material_review_source_packet,
)
from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeComponentStatus,
)
from radar.leader_evidence_candidate_plan import (
    LeaderEvidenceCandidateAcceptanceStatus,
    LeaderEvidenceCandidateSelectionPolicy,
    build_leader_phase6_evidence_candidate_acceptance,
    derive_leader_evidence_candidate_tradability_acceptance,
)
from radar.leader_evidence_qualification import (
    LeaderEvidenceQualificationResult,
    LeaderEvidenceQualificationStatus,
    build_leader_business_evidence_qualification,
)
from radar.leader_history_production_collector import (
    collect_leader_history_production_source,
)
from radar.leader_phase6_live_prefreeze import LeaderPhase6PrefrozenInputs
from radar.leader_phase6_live_source_readiness import (
    LeaderPhase6LiveSourceReadinessStatus,
    build_leader_phase6_live_source_readiness,
    run_leader_phase6_live_source_collection,
)
from radar.leader_risk_official_live_delivery import (
    LeaderRiskOfficialLiveDelivery,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceResult,
    LeaderTradabilityLiveAcceptanceStatus,
)
from radar.leader_tradability_production_collector import (
    collect_leader_tradability_production_source,
)
from radar.sector_rule_production_collector import (
    collect_sector_rule_production_source,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentPage,
)
from radar.sources.leader_tradability_public_live_poc import (
    run_public_composite_tradability_poc,
)
from tests import test_radar_leader_phase6_production_readiness as helpers
from tests import test_radar_leader_evidence_candidate_plan as evidence_helpers
from tests.test_radar_leader_business_automatic_evidence import (
    VALIDATED_AT,
    ready_sources,
    source_packet,
)


class LeaderPhase6LiveSourceReadinessTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderPhase6ProductionReadinessTests(
            methodName=(
                "test_official_deterministic_risk_completes_"
                "five_source_review_gate"
            )
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.inputs = helper.inputs(risk=helper.risk_frozen())
        plan = self.context.candidate_plan
        self.tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            radar_run_id=plan.radar_run_id,
            as_of=plan.as_of,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            candidate_plan=plan,
            source_context=self.context,
            frozen_batch=self.inputs.tradability,
            phase6_prefrozen_inputs=LeaderPhase6PrefrozenInputs(
                history=self.inputs.history,
                sector_rule=self.inputs.sector_rule,
                sector_historical_analyses=(),
                sector_comparable_time=time(10, 0),
                threshold_approval_record=object(),
            ),
        )
        self.business = LeaderBusinessAutomaticEvidenceBatchResult(
            status=AutomaticBusinessEvidenceStatus.READY,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            production_frozen_batch=self.inputs.business_catalyst,
        )
        self.risk = LeaderRiskOfficialLiveDelivery(
            candidate_plan=plan,
            delivery=self.inputs.risk.delivery,
            candidate_source_packet_sha256="a" * 64,
        )
        tradability_bundle = self.inputs.tradability.source_bundle
        self.tradability = replace(
            self.tradability,
            report=run_public_composite_tradability_poc(
                query=tradability_bundle.query,
                quotes=tradability_bundle.quotes,
                official_observations=(
                    tradability_bundle.official_observations
                ),
                aggregator_observations=(
                    tradability_bundle.aggregator_observations
                ),
                executed=True,
            ),
        )

    def build(self, *, business=None, risk=None):
        return build_leader_phase6_live_source_readiness(
            self.tradability,
            business_automatic=(business or self.business),
            risk_live_delivery=(risk or self.risk),
            repository=helpers._ReviewRepositoryMustNotBeUsed(),
            sector_threshold_approval_binder=(
                self.helper.approved_binder
            ),
        )

    def material_acceptance(self):
        plan = self.context.candidate_plan
        queue = LeaderBusinessMaterialReviewQueue(
            status=LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            items=tuple(
                LeaderBusinessMaterialReviewQueueItem(
                    index=item.index,
                    symbol=item.symbol,
                    industry_code=item.industry_code,
                    industry_release_id=item.industry_release_id,
                    status=(
                        LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
                    ),
                    documents=(OfficialBusinessMaterialDocument(
                        document_id=f"cninfo:report-{item.symbol}",
                        document_version=f"annual-{item.symbol}-v1",
                        symbol=item.symbol,
                        issuer_identity=f"cninfo-org:{item.symbol}",
                        title="2025年年度报告",
                        published_at=plan.as_of - timedelta(days=100),
                        source_url=(
                            "https://static.cninfo.com.cn/finalpage/"
                            f"2026-04-30/{item.symbol}.PDF"
                        ),
                    ),),
                )
                for item in plan.items
            ),
        )
        return LeaderBusinessMaterialLiveAcceptanceResult(
            status=LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
            radar_run_id=plan.radar_run_id,
            as_of=plan.as_of,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            issuer_status="ready",
            queue_status=queue.status.value,
            review_queue=queue,
        )

    def automatic_empty_inputs(self, *, missing: bool):
        packet = source_packet(1)
        loaded = load_leader_business_material_review_source_packet(packet)
        base_sources = ready_sources(missing_index=0 if missing else None)
        if missing:
            sources = base_sources
        else:
            def content(document, *, kind, fetched_at):
                result = base_sources.fetch_document_content(
                    document,
                    kind=kind,
                    fetched_at=fetched_at,
                )
                if kind is not OfficialBusinessDocumentKind.CATALYST:
                    return result
                text = "公司终止工业软件项目合同。"
                return replace(
                    result,
                    content_sha256=hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    pages=(OfficialBusinessDocumentPage(1, text),),
                )

            sources = LeaderBusinessAutomaticEvidenceSources(
                discover_catalysts=base_sources.discover_catalysts,
                fetch_document_content=content,
            )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            business = run_leader_business_automatic_evidence(
                packet,
                artifact_dir=Path(directory),
                sources=sources,
                clock=lambda: VALIDATED_AT,
            )
        qualification = build_leader_business_evidence_qualification(
            loaded.candidate_plan,
            business,
        )
        plan = loaded.candidate_plan
        tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            radar_run_id=plan.radar_run_id,
            as_of=plan.as_of,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            candidate_plan=plan,
        )
        material = LeaderBusinessMaterialLiveAcceptanceResult(
            status=LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
            radar_run_id=plan.radar_run_id,
            as_of=plan.as_of,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=plan.candidate_count,
            issuer_status="ready",
            queue_status=loaded.review_queue.status.value,
            review_queue=loaded.review_queue,
        )
        return tradability, material, business, qualification

    def evidence_parent_acceptance(self):
        evidence = evidence_helpers.LeaderEvidenceCandidatePlanTests(
            methodName=(
                "test_research_ranking_derives_small_plan_with_"
                "parent_provenance"
            )
        )
        evidence.setUp()
        from radar.leader_live_candidate_collection_batch import (
            LeaderLiveCandidateRuntimeInputs,
        )
        from radar.leader_research_runtime_provider import (
            build_leader_research_runtime_source_context,
        )
        raw = evidence.f6.raw_inputs()
        parent_context = build_leader_research_runtime_source_context(
            candidate_plan=evidence.preliminary_plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        parent_runtime = LeaderLiveCandidateRuntimeInputs(
            candidate_plan=evidence.preliminary_plan,
            source_context=parent_context,
            as_of=evidence.preliminary_plan.as_of,
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
        return evidence, replace(
            self.tradability,
            candidate_plan=evidence.preliminary_plan,
            source_context=parent_context,
            runtime_inputs=parent_runtime,
        )

    def test_same_plan_inputs_reach_existing_five_source_review_gate(self):
        result = self.build()

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.READY_FOR_REVIEW,
        )
        self.assertEqual(result.radar_run_id, self.context.radar_run_id)
        self.assertEqual(
            result.candidate_plan_id,
            self.context.candidate_plan.candidate_set_id,
        )
        self.assertEqual(result.as_of, self.context.as_of)
        self.assertTrue(all(
            item.status == LeaderFormalResearchRuntimeComponentStatus.READY
            for item in result.readiness.assembly.components
        ))
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.state_transition_allowed)

    def test_evidence_plan_derives_subplan_acceptance_for_five_sources(self):
        evidence, parent_acceptance = self.evidence_parent_acceptance()
        evidence_plan = evidence.build()

        narrowed = derive_leader_evidence_candidate_tradability_acceptance(
            parent_acceptance,
            evidence_plan,
            sector_threshold_approval_binder=(
                self.helper.approved_binder
            ),
        )

        self.assertEqual(
            narrowed.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        )
        self.assertEqual(narrowed.candidate_count, 2)
        self.assertEqual(
            narrowed.candidate_plan.parent_candidate_set_id,
            parent_acceptance.candidate_plan.candidate_set_id,
        )
        self.assertEqual(
            narrowed.source_context.candidate_plan,
            narrowed.candidate_plan,
        )
        self.assertEqual(
            narrowed.runtime_inputs.candidate_plan,
            narrowed.candidate_plan,
        )
        self.assertEqual(
            narrowed.phase6_prefrozen_inputs.sector_rule.source_batch
            .candidate_plan_id,
            narrowed.candidate_plan.candidate_set_id,
        )
        expected_symbols = tuple(
            item.symbol for item in narrowed.candidate_plan.items
        )
        sources = (
            collect_leader_history_production_source(
                narrowed.source_context,
                narrowed.phase6_prefrozen_inputs.history,
            ),
            collect_leader_tradability_production_source(
                narrowed.source_context,
                narrowed.frozen_batch,
            ),
            collect_sector_rule_production_source(
                narrowed.source_context,
                narrowed.phase6_prefrozen_inputs.sector_rule,
                threshold_approval_binder=self.helper.approved_binder,
            ),
        )
        self.assertTrue(all(
            source.status.value == "completed"
            and source.symbols == expected_symbols
            for source in sources
        ))
        self.assertEqual(narrowed.to_evidence()["returnedCount"], 2)

    def test_second_evidence_subset_keeps_all_three_prefrozen_sources_ready(self):
        from radar.leader_evidence_candidate_plan import (
            LeaderEvidenceCandidatePlan,
            LeaderEvidenceCandidatePlanItem,
            LeaderEvidenceCandidatePlanStatus,
        )
        from radar.leader_runtime_candidate_plan import (
            derive_leader_runtime_candidate_plan_subset,
        )

        evidence, parent_acceptance = self.evidence_parent_acceptance()
        first_plan = evidence.build()
        first = derive_leader_evidence_candidate_tradability_acceptance(
            parent_acceptance,
            first_plan,
            sector_threshold_approval_binder=self.helper.approved_binder,
        )
        symbol = first.candidate_plan.items[0].symbol
        policy_id = "radar-leader-evidence-second-subset-test-v1"
        child_plan = derive_leader_runtime_candidate_plan_subset(
            first.candidate_plan,
            symbols=(symbol,),
            derivation_policy_id=policy_id,
        )
        second_plan = LeaderEvidenceCandidatePlan(
            status=LeaderEvidenceCandidatePlanStatus.READY,
            preliminary_candidate_plan_id=(
                first.candidate_plan.candidate_set_id
            ),
            preliminary_candidate_count=first.candidate_count,
            candidate_plan=child_plan,
            items=(LeaderEvidenceCandidatePlanItem(
                index=0,
                symbol=symbol,
                industry_code=child_plan.items[0].industry_code,
                parent_index=0,
                selection_kind="new_candidate",
                research_partial_score=None,
            ),),
            selection_policy_id=policy_id,
        )

        second = derive_leader_evidence_candidate_tradability_acceptance(
            first,
            second_plan,
            sector_threshold_approval_binder=self.helper.approved_binder,
        )

        self.assertEqual(
            second.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        )
        self.assertEqual(second.candidate_count, 1)
        self.assertEqual(
            second.candidate_plan.parent_candidate_set_id,
            first.candidate_plan.candidate_set_id,
        )

    def test_single_exchange_subset_drops_the_other_exchange_calendar(self):
        from radar.leader_evidence_candidate_plan import (
            _narrow_tradability_calendars,
        )

        _evidence, parent_acceptance = self.evidence_parent_acceptance()
        query = parent_acceptance.frozen_batch.source_bundle.query
        szse_calendar = query.trading_calendars[0]
        calendars = (
            szse_calendar,
            replace(szse_calendar, exchange="sse"),
        )

        narrowed = _narrow_tradability_calendars(
            calendars,
            query.securities[:1],
        )

        self.assertEqual(
            tuple(item.exchange for item in narrowed),
            ("szse",),
        )

    def test_phase6_evidence_candidate_acceptance_narrows_before_sources(self):
        evidence, parent_acceptance = self.evidence_parent_acceptance()
        _production, industry_scope = evidence.produced_industry_scope()
        previous_state_snapshot = evidence.previous_state_snapshot()

        result = build_leader_phase6_evidence_candidate_acceptance(
            parent_acceptance,
            industry_scope=industry_scope,
            previous_state_snapshot=previous_state_snapshot,
            policy=LeaderEvidenceCandidateSelectionPolicy(
                maximum_new_candidates=2,
                maximum_incumbent_candidates=15,
            ),
            sector_threshold_approval_binder=(
                self.helper.approved_binder
            ),
        )

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidateAcceptanceStatus.READY,
        )
        self.assertEqual(result.preliminary_candidate_count, 5)
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(
            result.tradability.candidate_plan,
            result.evidence_plan.candidate_plan,
        )
        self.assertEqual(
            result.tradability.candidate_plan.parent_candidate_set_id,
            parent_acceptance.candidate_plan.candidate_set_id,
        )
        self.assertIs(getattr(result, "industry_scope", None), industry_scope)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        result_evidence = result.to_evidence()
        self.assertEqual(
            result_evidence["industryScopeSnapshotId"],
            industry_scope.state_snapshot_id,
        )
        self.assertEqual(
            result_evidence["previousStateSnapshotId"],
            previous_state_snapshot.snapshot_id,
        )

    def test_evidence_candidate_acceptance_exposes_safe_failing_boundary(self):
        evidence, parent_acceptance = self.evidence_parent_acceptance()
        _production, industry_scope = evidence.produced_industry_scope()
        parent_acceptance = replace(
            parent_acceptance,
            phase6_prefrozen_inputs=replace(
                parent_acceptance.phase6_prefrozen_inputs,
                history=object(),
            ),
        )

        result = build_leader_phase6_evidence_candidate_acceptance(
            parent_acceptance,
            industry_scope=industry_scope,
            previous_state_snapshot=evidence.previous_state_snapshot(),
            policy=LeaderEvidenceCandidateSelectionPolicy(),
            sector_threshold_approval_binder=(
                self.helper.approved_binder
            ),
        )

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("leader_evidence_scope_sources_unverified",),
        )

        missing_runtime = build_leader_phase6_evidence_candidate_acceptance(
            replace(parent_acceptance, runtime_inputs=None),
            industry_scope=industry_scope,
            previous_state_snapshot=evidence.previous_state_snapshot(),
            policy=LeaderEvidenceCandidateSelectionPolicy(),
            sector_threshold_approval_binder=(
                self.helper.approved_binder
            ),
        )
        self.assertEqual(
            missing_runtime.reasons,
            ("leader_evidence_candidate_runtime_unverified",),
        )

    def test_initial_shadow_selection_uses_frozen_sector_state_before_sources(
        self,
    ):
        from radar.leader_phase6_live_prefreeze import (
            resolve_sector_comparable_time,
        )
        from radar.sector_state_producer import (
            sector_threshold_record_sha256,
        )
        from run_leader_phase6_live_five_source_acceptance import (
            _build_initial_shadow_evidence_candidate_acceptance,
        )
        from tests import test_radar_sector_state_producer as state_helpers

        _evidence, parent_acceptance = self.evidence_parent_acceptance()
        prefrozen = parent_acceptance.phase6_prefrozen_inputs
        source_batch = prefrozen.sector_rule.source_batch
        code = parent_acceptance.candidate_plan.items[0].industry_code
        coverage = next(
            item for item in source_batch.history_evidence.rows
            if item.division_code == code
        )
        comparable_time = resolve_sector_comparable_time(
            source_batch.feature_batch.source_time
        )
        approval = replace(
            state_helpers.approval(),
            threshold_set_id=(
                source_batch.threshold_approval_evidence.threshold_set_id
            ),
            approval_id=(
                source_batch.threshold_approval_evidence.approval_id
            ),
            approved_at=(
                source_batch.threshold_approval_evidence.approved_at
            ),
        )
        approval = replace(
            approval,
            record_sha256=sector_threshold_record_sha256(approval),
        )
        analysis = replace(
            state_helpers.history(
                code,
                dates=coverage.same_minute_trading_dates,
            ),
            comparable_time=comparable_time,
        )
        parent_acceptance = replace(
            parent_acceptance,
            phase6_prefrozen_inputs=replace(
                prefrozen,
                sector_historical_analyses=(analysis,),
                sector_comparable_time=comparable_time,
                threshold_approval_record=approval,
            ),
        )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            next_sector_state_path = (
                Path(directory) / "next-sector-state.json"
            )
            result = _build_initial_shadow_evidence_candidate_acceptance(
                parent_acceptance,
                sector_threshold_approval_binder=(
                    self.helper.approved_binder
                ),
                next_sector_state_path=next_sector_state_path,
            )
            next_sector_state_exists = next_sector_state_path.is_file()

        self.assertEqual(
            result.status,
            LeaderEvidenceCandidateAcceptanceStatus.READY,
        )
        self.assertEqual(result.preliminary_candidate_count, 5)
        self.assertGreater(result.candidate_count, 0)
        self.assertLessEqual(result.candidate_count, 15)
        self.assertEqual(
            result.tradability.candidate_plan.parent_candidate_set_id,
            parent_acceptance.candidate_plan.candidate_set_id,
        )
        self.assertIsNotNone(result.industry_scope_snapshot_id)
        self.assertIsNotNone(result.previous_state_snapshot_id)
        self.assertTrue(next_sector_state_exists)
        self.assertFalse(result.state_transition_allowed)

    def test_partial_business_is_visible_without_partial_payload(self):
        business = replace(
            self.business,
            status=AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            production_frozen_batch=None,
        )

        result = self.build(business=business)

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.NOT_READY,
        )
        components = {
            item.name: item.status
            for item in result.readiness.assembly.components
        }
        self.assertEqual(
            components["business_catalyst"],
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            components["risk"],
            LeaderFormalResearchRuntimeComponentStatus.READY,
        )
        self.assertFalse(result.formal_usable)

    def test_cross_plan_business_is_rejected_before_five_source_replay(self):
        result = self.build(business=replace(
            self.business,
            candidate_plan_id="other-plan",
        ))

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.readiness)
        self.assertIn(
            "leader_phase6_live_business_identity_unverified",
            result.reasons,
        )

    def test_single_process_collection_uses_one_candidate_packet(self):
        material = self.material_acceptance()
        qualification = LeaderEvidenceQualificationResult(
            status=LeaderEvidenceQualificationStatus.READY,
            parent_candidate_plan_id=(
                self.context.candidate_plan.candidate_set_id
            ),
            parent_candidate_count=self.context.candidate_plan.candidate_count,
            candidate_plan=self.context.candidate_plan,
            evidence_plan=object(),
            business_automatic=self.business,
        )

        def risk_runner(packet, *, collected_at):
            del collected_at
            loaded = load_leader_business_material_review_source_packet(
                packet
            )
            if (
                loaded.status
                is not LeaderBusinessMaterialReviewSourcePacketStatus.READY
                or loaded.candidate_plan != self.context.candidate_plan
            ):
                raise AssertionError("同进程候选包身份未保持")
            return self.risk

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            result = run_leader_phase6_live_source_collection(
                self.tradability,
                artifact_dir=Path(directory),
                collected_at=self.context.as_of + timedelta(hours=1),
                repository=helpers._ReviewRepositoryMustNotBeUsed(),
                business_material_runner=lambda *args, **kwargs: material,
                business_automatic_runner=(
                    lambda *args, **kwargs: self.business
                ),
                risk_live_runner=risk_runner,
                qualification_runner=(
                    lambda *args, **kwargs: qualification
                ),
                qualified_tradability_runner=(
                    lambda *args, **kwargs: self.tradability
                ),
                qualified_material_runner=(
                    lambda *args, **kwargs: material
                ),
                sector_threshold_approval_binder=(
                    self.helper.approved_binder
                ),
            )

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.READY_FOR_REVIEW,
        )
        self.assertEqual(len(result.candidate_source_packet_sha256), 64)
        self.assertEqual(
            result.verification_candidate_source_packet_sha256,
            result.candidate_source_packet_sha256,
        )
        loaded_packet = load_leader_business_material_review_source_packet(
            result.candidate_source_packet
        )
        self.assertEqual(
            loaded_packet.status,
            LeaderBusinessMaterialReviewSourcePacketStatus.READY,
        )
        self.assertEqual(
            loaded_packet.candidate_plan,
            self.context.candidate_plan,
        )
        self.assertEqual(
            result.readiness.candidate_plan_id,
            self.context.candidate_plan.candidate_set_id,
        )
        self.assertTrue(hasattr(result, "state_decision_review"))
        self.assertIsNotNone(result.state_decision_review)
        self.assertFalse(
            result.state_decision_review.state_transition_allowed
        )
        self.assertIn(
            "stateDecisionReview",
            result.to_evidence(),
        )

    def test_complete_business_qualification_with_no_matches_is_valid_empty(
        self,
    ):
        (
            tradability,
            material,
            business,
            qualification,
        ) = self.automatic_empty_inputs(missing=False)
        self.assertEqual(
            business.status,
            AutomaticBusinessEvidenceStatus.READY,
        )
        self.assertEqual(
            qualification.status,
            LeaderEvidenceQualificationStatus.EMPTY,
        )
        risk_calls = []

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            result = run_leader_phase6_live_source_collection(
                tradability,
                artifact_dir=Path(directory),
                collected_at=VALIDATED_AT,
                repository=helpers._ReviewRepositoryMustNotBeUsed(),
                business_material_runner=lambda *args, **kwargs: material,
                business_automatic_runner=(
                    lambda *args, **kwargs: business
                ),
                qualification_runner=(
                    lambda *args, **kwargs: qualification
                ),
                risk_live_runner=lambda *args, **kwargs: risk_calls.append(
                    "risk"
                ),
                sector_threshold_approval_binder=(
                    self.helper.approved_binder
                ),
            )

        self.assertEqual(risk_calls, [])
        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.EMPTY,
        )
        self.assertEqual(result.qualification, qualification)
        self.assertIsNone(result.risk_live_delivery)
        self.assertIsNone(result.readiness)
        self.assertEqual(
            result.reasons,
            ("leader_business_evidence_qualification_empty",),
        )
        evidence = result.to_evidence()
        self.assertTrue(evidence["validEmptyResult"])
        self.assertFalse(evidence["gate"]["formalGateReady"])
        self.assertFalse(evidence["gate"]["stateTransitionAllowed"])

    def test_incomplete_business_qualification_empty_stays_not_ready(self):
        (
            tradability,
            material,
            business,
            qualification,
        ) = self.automatic_empty_inputs(missing=True)
        self.assertEqual(
            business.status,
            AutomaticBusinessEvidenceStatus.MISSING,
        )
        self.assertEqual(
            qualification.status,
            LeaderEvidenceQualificationStatus.EMPTY,
        )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            result = run_leader_phase6_live_source_collection(
                tradability,
                artifact_dir=Path(directory),
                collected_at=VALIDATED_AT,
                repository=helpers._ReviewRepositoryMustNotBeUsed(),
                business_material_runner=lambda *args, **kwargs: material,
                business_automatic_runner=(
                    lambda *args, **kwargs: business
                ),
                qualification_runner=(
                    lambda *args, **kwargs: qualification
                ),
                risk_live_runner=lambda *args, **kwargs: self.fail(
                    "主营证据不齐时不应进入风险采集"
                ),
                sector_threshold_approval_binder=(
                    self.helper.approved_binder
                ),
            )

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.NOT_READY,
        )
        self.assertFalse(result.to_evidence()["validEmptyResult"])
        self.assertIsNone(result.risk_live_delivery)
        self.assertIsNone(result.readiness)

    def test_collection_rejects_output_outside_private_tmp_before_sources(self):
        calls = []

        result = run_leader_phase6_live_source_collection(
            self.tradability,
            artifact_dir=Path(
                "/Volumes/HermesSSD/AntigravityData/量化监测-股票"
            ),
            collected_at=self.context.as_of + timedelta(hours=1),
            repository=helpers._ReviewRepositoryMustNotBeUsed(),
            business_material_runner=lambda *args, **kwargs: calls.append(
                "material"
            ),
            business_automatic_runner=lambda *args, **kwargs: calls.append(
                "automatic"
            ),
            risk_live_runner=lambda *args, **kwargs: calls.append("risk"),
            sector_threshold_approval_binder=self.helper.approved_binder,
        )

        self.assertEqual(
            result.status,
            LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
