import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_research_source_admission import (
    LeaderResearchRiskAdmissionBundle,
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionStatus,
    build_leader_research_source_admission,
)
from radar.leader_research_readiness_runtime_batch import (
    is_leader_research_runtime_risk_batch_valid,
)
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    RiskCategory,
    RiskOfficialStatus,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchEntry,
    LeaderRiskLifecycleBatchInput,
    LeaderRiskLifecycleBatchStatus,
    LeaderRiskLifecycleReviewVersion,
    build_leader_risk_lifecycle_batch,
)
from radar.leader_risk_review_artifacts import (
    ManualRiskReviewArtifactInput,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlanInput,
    LeaderRuntimeCandidatePlanStatus,
    build_leader_runtime_candidate_plan,
)
from radar.sources.leader_risk_document_content import (
    build_risk_document_review_candidate,
)
from radar.sources.leader_risk_official import (
    CninfoRiskIssuerScope,
    CninfoRiskDiscoveryQuery,
    OfficialRiskDiscoveryBatch,
    OfficialRiskSourceStatus,
)
from tests import test_radar_leader_risk_review_artifacts as d5_helpers
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)


class LeaderRiskLifecycleBatchTests(unittest.TestCase):
    def setUp(self):
        f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        f6.setUp()
        raw = f6.raw_inputs()
        raw["quote_batch"].items[0].symbol = "300081"
        raw["quote_batch"].items[0].name = "ST恒信"
        securities = list(raw["security_records"])
        securities[0] = securities[0].model_copy(update={
            "symbol": "300081",
            "name": "ST恒信",
        })
        raw["security_records"] = tuple(securities)
        industries = list(raw["industry_records"])
        industries[0] = industries[0].model_copy(update={
            "source_symbol": "300081",
            "source_name": "ST恒信",
            "security_identity": "300081",
        })
        raw["industry_records"] = tuple(industries)
        sector_rows = list(raw["sector_rows"])
        sector_rows[0] = dict(
            sector_rows[0],
            topContributorSymbol="300081",
        )
        raw["sector_rows"] = tuple(sector_rows)
        self.raw = raw
        self.plan = build_leader_runtime_candidate_plan(
            LeaderRuntimeCandidatePlanInput(**raw)
        )
        self.assertEqual(
            self.plan.status,
            LeaderRuntimeCandidatePlanStatus.READY,
        )
        self.assertEqual(self.plan.items[0].symbol, "300081")
        self.issuer_identity = d5_helpers.ISSUER_IDENTITY
        self.document, self.content, self.facts, self.candidate, self.event = (
            self._review_context()
        )

    def _review_context(self):
        published_at = self.plan.as_of - timedelta(days=10)
        fetched_at = self.plan.as_of - timedelta(days=3)
        document = d5_helpers.make_document(
            published_at=published_at,
        )
        content = d5_helpers.make_content(
            fetched_at=fetched_at,
        )
        event = d5_helpers.make_event(
            published_at=self.plan.as_of - timedelta(days=30),
            effective_from=self.plan.as_of - timedelta(days=30),
        )
        facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=self.plan.as_of - timedelta(days=2),
                document=document,
                content_sha256=content.content_sha256,
                pages=content.pages,
                extracted_at=content.fetched_at,
                source_status=content.status,
                event_versions=(event,),
                reviews=(),
            )
        )
        candidate = build_risk_document_review_candidate(
            content,
            facts,
        ).candidate
        return document, content, facts, candidate, event

    def _review_version(
        self,
        number,
        *,
        previous_artifacts=(),
        event=None,
        facts=None,
        candidate=None,
    ):
        as_of = self.plan.as_of - (
            timedelta(days=2)
            if number == 1
            else (
                timedelta(hours=6)
                if number == 2
                else timedelta(0)
            )
        )
        active_event = event or self.event
        active_facts = facts or self.facts
        active_candidate = candidate or self.candidate
        previous_version = (
            f"manual-review-v{number - 1}" if number > 1 else None
        )
        submission = d5_helpers.make_submission(
            active_candidate,
            review_version=f"manual-review-v{number}",
            supersedes_review_version=previous_version,
            reviewed_at=as_of - timedelta(hours=2),
        )
        artifact_input = ManualRiskReviewArtifactInput(
            as_of=as_of,
            document=self.document,
            content=self.content,
            facts=active_facts,
            candidate=active_candidate,
            event_versions=(active_event,),
            submission=submission,
            previous_artifacts=tuple(previous_artifacts),
        )
        artifact = build_manual_risk_review_artifact(
            artifact_input
        ).artifact
        replay_input = RiskDocumentResearchReplayInput(
            as_of=as_of,
            document=self.document,
            content=self.content,
            facts=active_facts,
            event_versions=(active_event,),
            artifacts=(*previous_artifacts, artifact),
        )
        replay = replay_risk_document_research_evidence(
            replay_input
        )
        relation_review = RiskDocumentVersionReview(
            review_id=f"supplemented-review-{number}",
            mapping_version=f"supplemented-map-v{number}",
            relation_kind=RiskDocumentRelationKind.RESOLVES,
            review_method="manual",
            reviewer_key="reviewer-local-2",
            reviewed_at=as_of - timedelta(hours=1),
            effective_until=None,
            source_document_id=self.document.document_id,
            target_event_id=active_event.event_id,
            target_event_version=active_event.event_version,
            target_document_id=active_event.document_id,
            replacement_event_version=None,
            basis_fact_ids=tuple(
                fact.fact_id for fact in replay.manual_facts
            ),
            decision_summary="人工复核公告与既有风险事件的精确关系。",
        )
        version = LeaderRiskLifecycleReviewVersion(
            as_of=as_of,
            document=self.document,
            content=self.content,
            facts=active_facts,
            candidate=active_candidate,
            event_versions=(active_event,),
            submission=submission,
            relation_review=relation_review,
        )
        return version, artifact

    def _versions(self):
        first, first_artifact = self._review_version(1)
        second, _ = self._review_version(
            2,
            previous_artifacts=(first_artifact,),
        )
        return first, second

    def _discovery_batches(self):
        trade_date = self.plan.as_of.date()
        candidate_scopes = tuple(
            CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=(
                    self.issuer_identity
                    if index == 0
                    else f"cninfo-org:fixture{item.symbol}"
                ),
                resolved_at=self.plan.as_of,
            )
            for index, item in enumerate(self.plan.items)
        )
        search_keys = {
            RiskCategory.REDUCTION: "减持计划",
            RiskCategory.UNLOCK: "解除限售",
            RiskCategory.REGULATORY: "监管措施决定书",
            RiskCategory.INVESTIGATION: "立案告知书",
            RiskCategory.LITIGATION: "重大诉讼",
            RiskCategory.EARNINGS: "业绩预告",
            RiskCategory.AUDIT: "审计报告",
        }
        values = []
        for category in ALL_RISK_CATEGORIES:
            documents = (
                (replace(
                    self.document,
                    candidate_category=category,
                ),)
                if category == RiskCategory.INVESTIGATION
                else ()
            )
            values.append(OfficialRiskDiscoveryBatch(
                status=OfficialRiskSourceStatus.PARTIAL,
                query=CninfoRiskDiscoveryQuery(
                    search_key=search_keys[category],
                    candidate_category=category,
                    window_from=trade_date - timedelta(days=365),
                    window_until=trade_date,
                    page_number=1,
                    page_size=30,
                    candidate_scopes=candidate_scopes,
                    candidate_plan_id=self.plan.candidate_set_id,
                    shard_index=0,
                    shard_count=1,
                ),
                fetched_at=self.plan.as_of,
                total_records=len(documents),
                total_pages=1 if documents else 0,
                reported_total_pages=1 if documents else 0,
                has_more=False,
                documents=documents,
                reasons=(
                    "cninfo_keyword_discovery_not_coverage_proof",
                ),
            ))
        return tuple(values)

    def test_unscoped_batches_cannot_claim_complete_candidate_coverage(self):
        batches = tuple(
            replace(
                batch,
                query=replace(
                    batch.query,
                    candidate_scopes=(),
                    candidate_plan_id=None,
                    shard_index=None,
                    shard_count=None,
                ),
            )
            for batch in self._discovery_batches()
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(batches=batches)
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertFalse(result.query_pages_complete)

    def test_discovery_page_from_another_candidate_plan_is_rejected(self):
        batches = list(self._discovery_batches())
        batches[0] = replace(
            batches[0],
            query=replace(
                batches[0].query,
                candidate_plan_id="candidate-plan:other",
            ),
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(batches=tuple(batches))
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertFalse(result.query_pages_complete)

    def _entries(self, *, versions=None):
        active_versions = self._versions() if versions is None else versions
        return tuple(
            LeaderRiskLifecycleBatchEntry(
                symbol=item.symbol,
                issuer_identity=(
                    self.issuer_identity if index == 0 else None
                ),
                versions=(active_versions if index == 0 else ()),
            )
            for index, item in enumerate(self.plan.items)
        )

    def _input(self, *, batches=None, entries=None):
        return LeaderRiskLifecycleBatchInput(
            candidate_plan=self.plan,
            discovery_batches=(
                self._discovery_batches()
                if batches is None
                else batches
            ),
            entries=(self._entries() if entries is None else entries),
        )

    def test_two_human_versions_replay_into_partial_runtime_batch(self):
        result = build_leader_risk_lifecycle_batch(self._input())

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleBatchStatus.PARTIAL,
        )
        self.assertTrue(result.query_categories_complete)
        self.assertTrue(result.query_pages_complete)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.items[0].status, ResearchFeatureStatus.READY)
        self.assertEqual(result.items[0].version_count, 2)
        self.assertTrue(result.items[0].open_events_continuous)
        self.assertTrue(result.items[0].correction_links_consistent)
        self.assertEqual(result.projection_batch.ready_count, 1)
        self.assertTrue(is_leader_research_runtime_risk_batch_valid(
            result.projection_batch,
            as_of=self.plan.as_of,
            candidate_symbols=tuple(
                item.symbol for item in self.plan.items
            ),
        ))
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_lifecycle_projection_enters_existing_unified_admission(self):
        lifecycle = build_leader_risk_lifecycle_batch(self._input())
        context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=self.raw["quote_batch"],
            quote_health=self.raw["quote_health"],
            security_records=self.raw["security_records"],
            industry_records=self.raw["industry_records"],
        )

        admitted = build_leader_research_source_admission(
            LeaderResearchSourceAdmissionInput(
                context=context,
                history_entries=None,
                business_review_batch=None,
                tradability_bundle=None,
                risk_projection_bundle=LeaderResearchRiskAdmissionBundle(
                    candidate_plan_id=self.plan.candidate_set_id,
                    radar_run_id=self.plan.radar_run_id,
                    quote_batch_id=self.plan.quote_batch_id,
                    batch=lifecycle.projection_batch,
                ),
            )
        )

        self.assertEqual(
            admitted.status,
            LeaderResearchSourceAdmissionStatus.PARTIAL,
        )
        self.assertIsNotNone(admitted.provider_input)
        self.assertEqual(
            admitted.provider_result.risk_projection_batch,
            lifecycle.projection_batch,
        )
        risk_component = next(
            item for item in admitted.components if item.name == "risk"
        )
        self.assertEqual(risk_component.status.value, "partial")
        self.assertEqual(risk_component.ready_count, 1)

    def test_single_version_remains_missing(self):
        first, _ = self._review_version(1)
        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(versions=(first,)))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.MISSING,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_version_history_insufficient",),
        )
        self.assertEqual(result.projection_batch.ready_count, 0)

    def test_query_category_or_page_gap_blocks_before_review_replay(self):
        categories_missing = self._discovery_batches()[:-1]
        page_missing = list(self._discovery_batches())
        investigation_index = ALL_RISK_CATEGORIES.index(
            RiskCategory.INVESTIGATION
        )
        page_missing[investigation_index] = replace(
            page_missing[investigation_index],
            total_records=31,
            total_pages=2,
            has_more=True,
        )

        for batches, reason in (
            (
                categories_missing,
                "risk_lifecycle_query_categories_incomplete",
            ),
            (
                tuple(page_missing),
                "risk_lifecycle_query_pages_incomplete",
            ),
        ):
            with self.subTest(reason=reason):
                result = build_leader_risk_lifecycle_batch(
                    self._input(batches=batches)
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)
                self.assertIsNone(result.projection_batch)

    def test_query_keyword_and_record_accounting_cannot_be_forged(self):
        wrong_keyword = list(self._discovery_batches())
        wrong_keyword[0] = replace(
            wrong_keyword[0],
            query=replace(
                wrong_keyword[0].query,
                search_key="任意公告",
            ),
        )
        investigation_index = ALL_RISK_CATEGORIES.index(
            RiskCategory.INVESTIGATION
        )
        duplicate_pages = list(self._discovery_batches())
        first_page = replace(
            duplicate_pages[investigation_index],
            query=replace(
                duplicate_pages[investigation_index].query,
                page_number=1,
                page_size=1,
            ),
            total_records=2,
            total_pages=2,
            has_more=True,
        )
        second_page = replace(
            first_page,
            query=replace(first_page.query, page_number=2),
            has_more=False,
        )
        duplicate_pages[investigation_index] = first_page
        duplicate_pages.append(second_page)

        for batches in (wrong_keyword, duplicate_pages):
            with self.subTest(batch_count=len(batches)):
                result = build_leader_risk_lifecycle_batch(
                    self._input(batches=tuple(batches))
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.query_pages_complete)
                self.assertIsNone(result.projection_batch)

    def test_any_discovery_source_failure_is_preserved(self):
        batches = list(self._discovery_batches())
        batches[-1] = replace(
            batches[-1],
            status=OfficialRiskSourceStatus.SOURCE_FAILED,
            total_records=None,
            total_pages=None,
            reported_total_pages=None,
            has_more=None,
            documents=(),
            reasons=("cninfo_source_request_failed",),
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(batches=tuple(batches))
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleBatchStatus.SOURCE_FAILED,
        )
        self.assertIsNone(result.projection_batch)

    def test_non_manual_review_is_rejected_by_internal_d5_replay(self):
        first, second = self._versions()
        tampered = replace(
            second,
            submission=replace(
                second.submission,
                review_method="ai",
            ),
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(
                versions=(first, tampered),
            ))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "manual_risk_review_submission_unverified",
            result.items[0].reasons,
        )
        self.assertEqual(result.projection_batch.ready_count, 0)

    def test_review_document_must_match_replayed_d2_identity(self):
        first, second = self._versions()
        tampered = replace(
            second,
            document=replace(
                second.document,
                title="调用方改写后的公告标题",
            ),
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(
                versions=(first, tampered),
            ))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_version_identity_unverified",),
        )

    def test_open_event_must_be_carried_into_next_version(self):
        first, second = self._versions()
        missing_event = replace(second, event_versions=())

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(
                versions=(first, missing_event),
            ))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_open_event_not_carried_forward",),
        )
        self.assertFalse(result.items[0].open_events_continuous)

    def test_open_event_identity_and_open_status_cannot_be_replaced(self):
        first, second = self._versions()
        wrong_identity = replace(
            second,
            event_versions=(replace(
                self.event,
                issuer_identity="cninfo-org:wrong-issuer",
            ),),
        )
        silently_closed = replace(
            second,
            event_versions=(replace(
                self.event,
                official_status=RiskOfficialStatus.COMPLETED,
            ),),
        )
        silently_expired = replace(
            second,
            event_versions=(replace(
                self.event,
                effective_until=second.as_of - timedelta(seconds=1),
            ),),
        )

        for current in (
            wrong_identity,
            silently_closed,
            silently_expired,
        ):
            with self.subTest(status=current.event_versions[0].official_status):
                result = build_leader_risk_lifecycle_batch(
                    self._input(entries=self._entries(
                        versions=(first, current),
                    ))
                )
                self.assertEqual(
                    result.items[0].reasons,
                    ("risk_lifecycle_open_event_not_carried_forward",),
                )
                self.assertFalse(result.items[0].open_events_continuous)

    def test_corrected_event_requires_superseding_relation(self):
        first, second = self._versions()
        corrected_event = replace(
            self.event,
            event_version="v2",
            official_status=RiskOfficialStatus.CORRECTED,
        )
        invalid_correction = replace(
            second,
            event_versions=(corrected_event,),
            relation_review=replace(
                second.relation_review,
                target_event_version="v2",
                relation_kind=RiskDocumentRelationKind.RESOLVES,
                replacement_event_version=None,
            ),
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(
                versions=(first, invalid_correction),
            ))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_correction_link_unverified",),
        )
        self.assertFalse(result.items[0].correction_links_consistent)

    def test_correction_replacement_must_exist_and_cover_every_event(self):
        first, second = self._versions()
        corrected_event = replace(
            self.event,
            event_version="v2",
            official_status=RiskOfficialStatus.CORRECTED,
        )
        fake_replacement = replace(
            second,
            event_versions=(corrected_event,),
            relation_review=replace(
                second.relation_review,
                target_event_version="v2",
                relation_kind=RiskDocumentRelationKind.SUPERSEDES,
                replacement_event_version="v999",
            ),
        )
        unreviewed_correction = replace(
            corrected_event,
            event_id="risk-event-unreviewed",
            event_version="v1",
            document_id="cninfo:unreviewed-correction",
        )
        multiple_corrections = replace(
            second,
            event_versions=(corrected_event, unreviewed_correction),
            relation_review=replace(
                second.relation_review,
                target_event_version="v2",
                relation_kind=RiskDocumentRelationKind.SUPERSEDES,
                replacement_event_version="v3",
            ),
        )

        for current in (fake_replacement, multiple_corrections):
            with self.subTest(event_count=len(current.event_versions)):
                result = build_leader_risk_lifecycle_batch(
                    self._input(entries=self._entries(
                        versions=(first, current),
                    ))
                )
                self.assertEqual(
                    result.items[0].reasons,
                    ("risk_lifecycle_correction_link_unverified",),
                )
                self.assertFalse(
                    result.items[0].correction_links_consistent
                )

    def test_correction_replacement_cannot_point_backward_or_to_future_fact(self):
        first, second = self._versions()
        backward_event = replace(
            self.event,
            event_version="v2",
        )
        backward = replace(
            second,
            event_versions=(backward_event,),
            relation_review=replace(
                second.relation_review,
                target_event_version="v2",
                relation_kind=RiskDocumentRelationKind.SUPERSEDES,
                replacement_event_version="v1",
            ),
        )
        future_replacement = replace(
            self.event,
            event_version="v3",
            published_at=second.as_of + timedelta(hours=1),
        )
        third, _ = self._review_version(3, event=future_replacement)
        future = replace(
            second,
            relation_review=replace(
                second.relation_review,
                relation_kind=RiskDocumentRelationKind.SUPERSEDES,
                replacement_event_version="v3",
            ),
        )

        for versions in ((first, backward), (first, future, third)):
            with self.subTest(version_count=len(versions)):
                result = build_leader_risk_lifecycle_batch(
                    self._input(entries=self._entries(versions=versions))
                )
                self.assertEqual(
                    result.items[0].reasons,
                    ("risk_lifecycle_correction_link_unverified",),
                )

    def test_forward_correction_chain_replays_without_false_rejection(self):
        first, first_artifact = self._review_version(1)
        second, second_artifact = self._review_version(
            2,
            previous_artifacts=(first_artifact,),
        )
        second = replace(
            second,
            relation_review=replace(
                second.relation_review,
                relation_kind=RiskDocumentRelationKind.SUPERSEDES,
                replacement_event_version="v2",
            ),
        )
        replacement_event = replace(
            self.event,
            event_version="v2",
            published_at=self.document.published_at - timedelta(hours=1),
        )
        replacement_facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=self.plan.as_of,
                document=self.document,
                content_sha256=self.content.content_sha256,
                pages=self.content.pages,
                extracted_at=self.content.fetched_at,
                source_status=self.content.status,
                event_versions=(replacement_event,),
                reviews=(),
            )
        )
        replacement_candidate = build_risk_document_review_candidate(
            self.content,
            replacement_facts,
        ).candidate
        third, _ = self._review_version(
            3,
            previous_artifacts=(first_artifact, second_artifact),
            event=replacement_event,
            facts=replacement_facts,
            candidate=replacement_candidate,
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=self._entries(
                versions=(first, second, third),
            ))
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.READY,
        )
        self.assertEqual(result.items[0].version_count, 3)
        self.assertTrue(result.items[0].open_events_continuous)
        self.assertTrue(result.items[0].correction_links_consistent)

    def test_stale_latest_review_is_preserved_at_item_and_batch_level(self):
        first, second = self._versions()
        stale_versions = (
            first,
            replace(
                second,
                submission=replace(
                    second.submission,
                    reviewed_at=self.plan.as_of - timedelta(hours=36),
                ),
                relation_review=replace(
                    second.relation_review,
                    reviewed_at=self.plan.as_of - timedelta(hours=35),
                ),
            ),
        )
        entries = tuple(
            replace(
                entry,
                issuer_identity=(
                    self.issuer_identity if index == 0 else None
                ),
                versions=(stale_versions if index == 0 else ()),
            )
            for index, entry in enumerate(self._entries(versions=()))
        )

        result = build_leader_risk_lifecycle_batch(
            self._input(entries=entries)
        )

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.STALE,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_latest_review_stale",),
        )
        self.assertEqual(
            result.status,
            LeaderRiskLifecycleBatchStatus.STALE,
        )
        self.assertEqual(
            result.projection_batch.items[0].status,
            ResearchFeatureStatus.STALE,
        )

    def test_candidate_order_and_stage6_scope_fail_closed(self):
        reversed_entries = tuple(reversed(self._entries()))
        reversed_result = build_leader_risk_lifecycle_batch(
            self._input(entries=reversed_entries)
        )
        out_of_scope = replace(
            self._entries()[0],
            symbol="920023",
        )
        out_of_scope_result = build_leader_risk_lifecycle_batch(
            self._input(entries=(out_of_scope, *self._entries()[1:]))
        )

        self.assertEqual(
            reversed_result.status,
            LeaderRiskLifecycleBatchStatus.BLOCKED,
        )
        self.assertEqual(
            out_of_scope_result.status,
            LeaderRiskLifecycleBatchStatus.BLOCKED,
        )


if __name__ == "__main__":
    unittest.main()
