import importlib
import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_runtime_batch import (
    is_leader_research_runtime_risk_batch_valid,
)
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    RiskDocumentRelationKind,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskResolutionEvidence,
    RiskEvidenceSourceKind,
    RiskResolutionKind,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchEntry,
    build_leader_risk_lifecycle_batch,
)
from radar.leader_risk_lifecycle_delivery import (
    deliver_leader_risk_lifecycle,
)
from radar.leader_risk_official_deterministic import (
    LeaderOfficialDeterministicRiskStatus,
    build_leader_official_deterministic_risk_batch,
    is_leader_official_deterministic_risk_batch_valid,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
)
from tests import test_radar_leader_risk_review_artifacts as d8_helpers
from tests import test_radar_leader_risk_lifecycle_delivery as delivery_helpers


try:
    lifecycle_module = importlib.import_module(
        "radar.leader_risk_official_lifecycle"
    )
except ModuleNotFoundError:
    lifecycle_module = None


UTC = timezone.utc


class LeaderOfficialRiskLifecycleTests(unittest.TestCase):
    def setUp(self):
        delivery = delivery_helpers.LeaderRiskLifecycleDeliveryTests(
            methodName=(
                "test_large_candidate_plan_is_sharded_without_"
                "changing_plan_identity"
            )
        )
        delivery.setUp()
        self.delivery = delivery
        self.helper = delivery.helper
        self.plan = delivery.plan
        self.baseline_input = self.helper._input()
        self.baseline_result = build_leader_risk_lifecycle_batch(
            self.baseline_input
        )
        self.open_event = self.helper.event
        self.issuer_listed_at = datetime.combine(
            self.plan.as_of.date() - timedelta(days=365),
            time.min,
            tzinfo=UTC,
        )

    def official_window(
        self,
        plan,
        *,
        window_from,
        window_until,
        transport=None,
        document_contents=(),
        issuer_identity_overrides=None,
    ):
        issuer_identity_overrides = issuer_identity_overrides or {}
        scopes = tuple(
            replace(
                scope,
                issuer_identity=issuer_identity_overrides.get(
                    scope.symbol,
                    scope.issuer_identity,
                ),
            )
            for scope in self.delivery.candidate_scopes(plan)
        )
        input_value = replace(
            self.delivery.input_value(),
            candidate_plan=plan,
            entries=tuple(
                LeaderRiskLifecycleBatchEntry(
                    symbol=item.symbol,
                    issuer_identity=scopes[index].issuer_identity,
                    versions=(),
                )
                for index, item in enumerate(plan.items)
            ),
            candidate_scopes=scopes,
            window_from=window_from,
            window_until=window_until,
            collected_at=plan.as_of,
        )
        delivered = deliver_leader_risk_lifecycle(
            input_value,
            transport=(
                transport
                or (lambda *args, **kwargs: self.delivery.payload([]))
            ),
        )
        batch = build_leader_official_deterministic_risk_batch(
            candidate_plan=plan,
            delivery=delivered,
            document_contents=document_contents,
        )
        self.assertEqual(
            batch.status,
            LeaderOfficialDeterministicRiskStatus.READY,
        )
        return batch

    def official_relation_window(self, plan):
        document_id = "cninfo:1225999999"
        published_at = plan.as_of - timedelta(hours=1)
        source_path = "finalpage/2026-08-29/1225999999.PDF"
        source_url = f"https://static.cninfo.com.cn/{source_path}"

        def transport(url, *, data, headers, timeout):
            del url, headers, timeout
            if data["searchkey"] != "立案告知书":
                return self.delivery.payload([])
            return self.delivery.payload([
                self.delivery.document_row(
                    announcementId="1225999999",
                    announcementTitle="关于立案事项后续处理的公告",
                    announcementTime=int(published_at.timestamp() * 1000),
                    adjunctUrl=source_path,
                )
            ])

        page_text = (
            f"案号：{d8_helpers.RAW_CASE_ID}\n"
            "原公告编号：1224000001\n"
            "生效日期：2026年8月29日\n"
        )
        content = OfficialRiskDocumentContentResult(
            status=ResearchFeatureStatus.READY,
            document_id=document_id,
            symbol=self.open_event.symbol,
            issuer_identity=self.open_event.issuer_identity,
            content_sha256=hashlib.sha256(
                page_text.encode("utf-8")
            ).hexdigest(),
            byte_count=len(page_text.encode("utf-8")),
            page_count=1,
            pages=(
                d8_helpers.OfficialRiskDocumentPage(
                    page_number=1,
                    text=page_text,
                ),
            ),
            fetched_at=plan.as_of,
            reasons=(),
        )
        batch = self.official_window(
            plan,
            window_from=plan.as_of.date(),
            window_until=plan.as_of.date(),
            transport=transport,
            document_contents=(content,),
        )
        projection = batch.projection_batch.items[0].projection
        document = replace(
            self.helper.document,
            document_id=document_id,
            title="关于立案事项后续处理的公告",
            published_at=published_at,
            source_url=source_url,
        )
        facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=plan.as_of,
                document=document,
                content_sha256=content.content_sha256,
                pages=content.pages,
                extracted_at=content.fetched_at,
                source_status=content.status,
                event_versions=(),
                reviews=(),
            )
        )
        self.assertEqual(facts.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            tuple(fact.fact_id for fact in facts.facts),
            projection.deterministic_fact_ids,
        )
        return batch, document, facts

    def window_entries(self, *, carry_open_event=True):
        entry_type = getattr(
            lifecycle_module,
            "LeaderOfficialRiskLifecycleWindowEntry",
            None,
        )
        self.assertTrue(callable(entry_type))
        return tuple(
            entry_type(
                symbol=item.symbol,
                issuer_listed_at=self.issuer_listed_at,
                events=(
                    (self.open_event,)
                    if index == 0 and carry_open_event
                    else ()
                ),
                resolutions=(),
                document_fact_results=(),
                relations=(),
            )
            for index, item in enumerate(self.plan.items)
        )

    def windows(self, *, carry_in_second=True):
        window_type = getattr(
            lifecycle_module,
            "LeaderOfficialRiskLifecycleWindow",
            None,
        )
        self.assertTrue(callable(window_type))
        first_date = self.plan.as_of.date() - timedelta(days=365)
        first = self.official_window(
            self.plan,
            window_from=first_date,
            window_until=self.plan.as_of.date(),
        )
        second_plan = self.delivery.plan_at(
            self.plan.as_of + timedelta(days=1)
        )
        second = self.official_window(
            second_plan,
            window_from=second_plan.as_of.date(),
            window_until=second_plan.as_of.date(),
        )
        return (
            window_type(
                candidate_plan=self.plan,
                official_batch=first,
                entries=self.window_entries(),
            ),
            window_type(
                candidate_plan=second_plan,
                official_batch=second,
                entries=self.window_entries(
                    carry_open_event=carry_in_second
                ),
            ),
        )

    def relation_windows(
        self,
        *,
        relation_kind,
        wrong_target_version=False,
        facts_present=True,
    ):
        window_type = lifecycle_module.LeaderOfficialRiskLifecycleWindow
        entry_type = lifecycle_module.LeaderOfficialRiskLifecycleWindowEntry
        relation_type = lifecycle_module.LeaderOfficialRiskLifecycleRelation
        first_date = self.plan.as_of.date() - timedelta(days=365)
        first = self.official_window(
            self.plan,
            window_from=first_date,
            window_until=self.plan.as_of.date(),
        )
        second_plan = self.delivery.plan_at(
            self.plan.as_of + timedelta(days=1)
        )
        second, document, facts = self.official_relation_window(second_plan)
        fact_ids = tuple(fact.fact_id for fact in facts.facts)
        relation = relation_type(
            relation_kind=relation_kind,
            source_document_id=document.document_id,
            target_event_id=self.open_event.event_id,
            target_event_version=(
                "v0" if wrong_target_version else self.open_event.event_version
            ),
            target_document_id=self.open_event.document_id,
            replacement_event_version=(
                "v2"
                if relation_kind == RiskDocumentRelationKind.SUPERSEDES
                else None
            ),
            basis_fact_ids=fact_ids,
            effective_from=document.published_at,
        )
        corrected = replace(
            self.open_event,
            event_version="v2",
            document_id=document.document_id,
            source_url=document.source_url,
            published_at=document.published_at,
        )
        resolution = LeaderRiskResolutionEvidence(
            resolution_id="risk-resolution-1",
            resolution_version="v1",
            target_event_id=self.open_event.event_id,
            target_event_version=self.open_event.event_version,
            symbol=self.open_event.symbol,
            issuer_identity=self.open_event.issuer_identity,
            resolution_kind=RiskResolutionKind.OFFICIALLY_CLEARED,
            source_kind=(
                RiskEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
            ),
            source_name="巨潮资讯",
            source_url=document.source_url,
            document_id=document.document_id,
            published_at=document.published_at,
            effective_from=document.published_at,
            resolution_summary="官方公告明确说明对应事项已经解除。",
        )
        first_entries = self.window_entries()
        second_entries = tuple(
            entry_type(
                symbol=item.symbol,
                issuer_listed_at=self.issuer_listed_at,
                events=(
                    (
                        (corrected,)
                        if relation_kind
                        == RiskDocumentRelationKind.SUPERSEDES
                        else (self.open_event,)
                    )
                    if index == 0
                    else ()
                ),
                resolutions=(
                    (resolution,)
                    if index == 0
                    and relation_kind == RiskDocumentRelationKind.RESOLVES
                    else ()
                ),
                document_fact_results=(
                    (facts,) if index == 0 and facts_present else ()
                ),
                relations=((relation,) if index == 0 else ()),
            )
            for index, item in enumerate(second_plan.items)
        )
        return (
            window_type(
                candidate_plan=self.plan,
                official_batch=first,
                entries=first_entries,
            ),
            window_type(
                candidate_plan=second_plan,
                official_batch=second,
                entries=second_entries,
            ),
        )

    def build(self, windows):
        builder = getattr(
            lifecycle_module,
            "build_leader_official_risk_lifecycle",
            None,
        )
        self.assertTrue(callable(builder))
        return builder(
            baseline_input=self.baseline_input,
            baseline_result=self.baseline_result,
            windows=windows,
        )

    def test_open_d8_event_is_carried_across_continuous_official_windows(self):
        result = self.build(self.windows())

        item = result.items[0]
        self.assertEqual(item.status, ResearchFeatureStatus.READY)
        self.assertFalse(item.coverage_complete)
        self.assertTrue(item.open_event_carry_forward_complete)
        self.assertTrue(item.correction_links_complete)
        self.assertTrue(item.resolution_links_complete)
        self.assertEqual(
            item.active_risk_categories,
            (self.open_event.category,),
        )
        self.assertFalse(item.risk_filter_passed)
        self.assertFalse(item.formal_gate_ready)
        self.assertFalse(item.formal_usable)
        self.assertFalse(item.state_transition_allowed)

    def test_open_event_cannot_disappear_without_official_relation(self):
        result = self.build(self.windows(carry_in_second=False))

        item = result.items[0]
        self.assertEqual(
            item.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            item.reasons,
            ("risk_official_open_event_not_carried_forward",),
        )
        self.assertFalse(item.open_event_carry_forward_complete)
        self.assertFalse(item.risk_filter_passed)
        self.assertFalse(item.formal_usable)

    def test_d2_windows_without_replayable_d8_baseline_fail_closed(self):
        builder = getattr(
            lifecycle_module,
            "build_leader_official_risk_lifecycle",
            None,
        )
        self.assertTrue(callable(builder))

        result = builder(
            baseline_input=None,
            baseline_result=None,
            windows=self.windows(),
        )

        self.assertEqual(result.status.value, "source_unverified")
        self.assertEqual(
            result.reasons,
            ("risk_official_d8_baseline_unverified",),
        )
        self.assertFalse(result.risk_filter_passed)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_exact_official_correction_replaces_open_event_version(self):
        result = self.build(self.relation_windows(
            relation_kind=RiskDocumentRelationKind.SUPERSEDES,
        ))

        item = result.items[0]
        self.assertEqual(item.status, ResearchFeatureStatus.READY)
        self.assertTrue(item.open_event_carry_forward_complete)
        self.assertTrue(item.correction_links_complete)
        self.assertEqual(
            item.active_risk_categories,
            (self.open_event.category,),
        )

    def test_exact_official_resolution_closes_known_open_event(self):
        result = self.build(self.relation_windows(
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        ))

        item = result.items[0]
        self.assertEqual(item.status, ResearchFeatureStatus.READY)
        self.assertTrue(item.open_event_carry_forward_complete)
        self.assertTrue(item.resolution_links_complete)
        self.assertEqual(item.active_risk_categories, ())
        self.assertFalse(item.risk_filter_passed)
        self.assertIn(
            "risk_official_formal_coverage_not_proven",
            item.reasons,
        )

    def test_resolution_remains_closed_in_later_official_window(self):
        windows = self.relation_windows(
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )
        third_plan = self.delivery.plan_at(
            windows[-1].candidate_plan.as_of + timedelta(days=1)
        )
        third_batch = self.official_window(
            third_plan,
            window_from=third_plan.as_of.date(),
            window_until=third_plan.as_of.date(),
        )
        third = lifecycle_module.LeaderOfficialRiskLifecycleWindow(
            candidate_plan=third_plan,
            official_batch=third_batch,
            entries=self.window_entries(),
        )

        result = self.build((*windows, third))

        self.assertEqual(result.items[0].status, ResearchFeatureStatus.READY)
        self.assertEqual(result.items[0].active_risk_categories, ())
        self.assertTrue(result.items[0].resolution_links_complete)

    def test_resolved_event_may_be_absent_from_later_window(self):
        windows = self.relation_windows(
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )
        third_plan = self.delivery.plan_at(
            windows[-1].candidate_plan.as_of + timedelta(days=1)
        )
        third_batch = self.official_window(
            third_plan,
            window_from=third_plan.as_of.date(),
            window_until=third_plan.as_of.date(),
        )
        third = lifecycle_module.LeaderOfficialRiskLifecycleWindow(
            candidate_plan=third_plan,
            official_batch=third_batch,
            entries=self.window_entries(carry_open_event=False),
        )

        result = self.build((*windows, third))

        self.assertEqual(result.items[0].status, ResearchFeatureStatus.READY)
        self.assertEqual(result.items[0].active_risk_categories, ())
        self.assertTrue(result.items[0].resolution_links_complete)

    def test_relation_requires_exact_official_body_facts_and_target_version(self):
        cases = (
            (
                self.relation_windows(
                    relation_kind=RiskDocumentRelationKind.RESOLVES,
                    facts_present=False,
                ),
                "risk_official_relation_document_facts_unverified",
            ),
            (
                self.relation_windows(
                    relation_kind=RiskDocumentRelationKind.RESOLVES,
                    wrong_target_version=True,
                ),
                "risk_official_relation_target_version_unverified",
            ),
        )
        for windows, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.build(windows)
                item = result.items[0]
                self.assertEqual(
                    item.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(item.reasons, (expected_reason,))
                self.assertFalse(item.risk_filter_passed)
                self.assertFalse(item.formal_usable)

    def test_query_history_must_start_no_later_than_issuer_listing(self):
        windows = self.windows()
        too_early_listing = self.issuer_listed_at - timedelta(days=1)
        first = replace(
            windows[0],
            entries=tuple(
                replace(entry, issuer_listed_at=too_early_listing)
                for entry in windows[0].entries
            ),
        )
        second = replace(
            windows[1],
            entries=tuple(
                replace(entry, issuer_listed_at=too_early_listing)
                for entry in windows[1].entries
            ),
        )

        result = self.build((first, second))

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_official_lifecycle_listing_history_incomplete",),
        )

    def test_future_issuer_listing_time_fails_closed(self):
        windows = self.windows()
        future_listing = (
            windows[-1].candidate_plan.as_of + timedelta(days=1)
        )
        changed = tuple(
            replace(
                window,
                entries=tuple(
                    replace(entry, issuer_listed_at=future_listing)
                    for entry in window.entries
                ),
            )
            for window in windows
        )

        result = self.build(changed)

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_official_lifecycle_listing_identity_unverified",),
        )

    def test_official_window_issuer_identity_cannot_drift(self):
        windows = self.windows()
        second_plan = windows[1].candidate_plan
        drifted_batch = self.official_window(
            second_plan,
            window_from=second_plan.as_of.date(),
            window_until=second_plan.as_of.date(),
            issuer_identity_overrides={
                self.open_event.symbol: "cninfo-org:issuer-drift",
            },
        )
        second = replace(windows[1], official_batch=drifted_batch)

        result = self.build((windows[0], second))

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_official_lifecycle_issuer_identity_unverified",),
        )

    def test_correction_url_must_match_bound_official_document(self):
        windows = self.relation_windows(
            relation_kind=RiskDocumentRelationKind.SUPERSEDES,
        )
        entry = windows[1].entries[0]
        corrected = replace(
            entry.events[0],
            source_url="https://example.invalid/correction.pdf",
        )
        second = replace(
            windows[1],
            entries=(
                replace(entry, events=(corrected,)),
                *windows[1].entries[1:],
            ),
        )

        result = self.build((windows[0], second))

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_official_correction_replacement_unverified",),
        )

    def test_resolution_identity_must_match_d8_issuer(self):
        windows = self.relation_windows(
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )
        entry = windows[1].entries[0]
        resolution = replace(
            entry.resolutions[0],
            issuer_identity="cninfo-org:other",
        )
        second = replace(
            windows[1],
            entries=(
                replace(entry, resolutions=(resolution,)),
                *windows[1].entries[1:],
            ),
        )

        result = self.build((windows[0], second))

        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_official_resolution_identity_unverified",),
        )

    def test_ready_lifecycle_is_bound_to_current_official_projection(self):
        windows = self.relation_windows(
            relation_kind=RiskDocumentRelationKind.RESOLVES,
        )
        lifecycle = self.build(windows)
        binder = getattr(
            lifecycle_module,
            "bind_leader_official_risk_lifecycle_evidence",
            None,
        )
        self.assertTrue(callable(binder))

        bound = binder(
            windows[-1].official_batch,
            lifecycle=lifecycle,
            candidate_plan=windows[-1].candidate_plan,
        )

        self.assertTrue(is_leader_official_deterministic_risk_batch_valid(
            bound,
            candidate_plan=windows[-1].candidate_plan,
        ))
        self.assertTrue(is_leader_research_runtime_risk_batch_valid(
            bound.projection_batch,
            as_of=windows[-1].candidate_plan.as_of,
            candidate_symbols=tuple(
                item.symbol for item in windows[-1].candidate_plan.items
            ),
        ))
        projection = bound.projection_batch.items[0].projection
        self.assertTrue(projection.open_event_carry_forward_complete)
        self.assertTrue(projection.correction_links_complete)
        self.assertTrue(projection.resolution_links_complete)
        self.assertNotIn(
            "risk_official_open_event_carry_forward_not_proven",
            projection.formal_gate_gaps,
        )
        self.assertNotIn(
            "risk_official_correction_links_not_proven",
            projection.formal_gate_gaps,
        )
        self.assertIn(
            "risk_official_formal_coverage_not_proven",
            projection.formal_gate_gaps,
        )
        self.assertFalse(projection.risk_filter_passed)
        self.assertFalse(bound.formal_usable)
        self.assertFalse(bound.state_transition_allowed)
        evidence = projection.to_evidence()
        self.assertEqual(
            evidence["lifecycle"]["contractId"],
            "radar-leader-official-risk-lifecycle-v1",
        )
        self.assertFalse(evidence["lifecycle"]["formalCoverageComplete"])
        self.assertFalse(evidence["gate"]["riskFilterPassed"])

    def test_replaced_lifecycle_result_loses_producer_identity(self):
        windows = self.windows()
        lifecycle = self.build(windows)
        validator = getattr(
            lifecycle_module,
            "is_leader_official_risk_lifecycle_valid",
            None,
        )
        self.assertTrue(callable(validator))
        self.assertTrue(validator(
            lifecycle,
            candidate_plan=windows[-1].candidate_plan,
        ))
        self.assertFalse(validator(
            replace(lifecycle),
            candidate_plan=windows[-1].candidate_plan,
        ))


if __name__ == "__main__":
    unittest.main()
