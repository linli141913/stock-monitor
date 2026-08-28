import hashlib
import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_formal_research_runtime_bridge import (
    LeaderFormalResearchRuntimeBridgeStatus,
    build_leader_formal_research_runtime_bridge,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from radar.leader_risk_lifecycle_batch import LeaderRiskLifecycleBatchEntry
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleDeliveryStatus,
    deliver_leader_risk_lifecycle,
)
from radar.leader_risk_document_facts import OfficialRiskDocumentPage
from radar.leader_risk_official_deterministic import (
    LEADER_OFFICIAL_DETERMINISTIC_RISK_SOURCE_CONTRACT_ID,
    LeaderOfficialDeterministicRiskEvidenceKind,
    LeaderOfficialDeterministicRiskFrozenBatch,
    LeaderOfficialDeterministicRiskStatus,
    build_leader_official_deterministic_risk_batch,
    collect_leader_official_deterministic_risk_source,
    is_leader_official_deterministic_risk_batch_valid,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
)
from radar.sources.leader_risk_candidate_discovery import (
    LeaderRiskOfficialCandidateDocument,
    LeaderRiskOfficialCandidateDiscoveryStatus,
)
from radar.sources.leader_risk_official import OfficialRiskSourceStatus
from tests import test_radar_leader_risk_lifecycle_delivery as delivery_helpers


class _RepositoryMustNotBeUsed:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        raise AssertionError("官方确定性风险来源不得读取人工D8仓库")


class LeaderOfficialDeterministicRiskTests(unittest.TestCase):
    def setUp(self):
        helper = delivery_helpers.LeaderRiskLifecycleDeliveryTests(
            methodName="test_large_candidate_plan_is_sharded_without_changing_plan_identity"
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan
        self.input_value = replace(
            helper.input_value(),
            entries=tuple(
                LeaderRiskLifecycleBatchEntry(
                    symbol=item.symbol,
                    issuer_identity=helper.candidate_scopes()[index].issuer_identity,
                    versions=(),
                )
                for index, item in enumerate(self.plan.items)
            ),
        )
        self.context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=helper.helper.raw["quote_batch"],
            quote_health=helper.helper.raw["quote_health"],
            security_records=helper.helper.raw["security_records"],
            industry_records=helper.helper.raw["industry_records"],
        )

    def deliver(self, transport):
        return deliver_leader_risk_lifecycle(
            self.input_value,
            transport=transport,
        )

    def content(self, document, **changes):
        pages = (
            OfficialRiskDocumentPage(
                page_number=1,
                text=(
                    "案号：证监立案字0202026001号\n"
                    "报告期：2024年度\n"
                    "原公告编号：1224000001\n"
                ),
            ),
        )
        result = OfficialRiskDocumentContentResult(
            status=ResearchFeatureStatus.READY,
            document_id=document.document_id,
            symbol=document.symbol,
            issuer_identity=document.issuer_identity,
            content_sha256=hashlib.sha256(b"official-pdf").hexdigest(),
            byte_count=len(b"official-pdf"),
            page_count=len(pages),
            pages=pages,
            fetched_at=self.plan.as_of,
            reasons=(),
        )
        return replace(result, **changes)

    def test_complete_seven_category_zero_hit_is_bounded_ready_evidence(self):
        delivery = self.deliver(
            lambda *args, **kwargs: self.helper.payload([]),
        )

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(),
        )

        self.assertEqual(delivery.status, LeaderRiskLifecycleDeliveryStatus.MISSING)
        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        self.assertEqual(result.ready_count, self.plan.candidate_count)
        self.assertEqual(result.projection_batch.ready_count, self.plan.candidate_count)
        self.assertTrue(all(
            item.projection.evidence_kind
            == LeaderOfficialDeterministicRiskEvidenceKind.BOUNDED_NO_DISCLOSURE
            for item in result.projection_batch.items
        ))
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["claimScope"],
            "bounded_official_query_window",
        )
        self.assertNotIn("noRisk", str(evidence))
        self.assertFalse(result.risk_filter_passed)
        self.assertFalse(result.formal_usable)

    def test_source_fetch_time_may_follow_plan_as_of_without_relabeling_plan(self):
        collected_at = self.plan.as_of + timedelta(seconds=1)
        scopes = tuple(
            replace(scope, resolved_at=collected_at)
            for scope in self.input_value.candidate_scopes
        )
        delivery = deliver_leader_risk_lifecycle(
            replace(
                self.input_value,
                collected_at=collected_at,
                candidate_scopes=scopes,
            ),
            transport=lambda *args, **kwargs: self.helper.payload([]),
        )

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(),
        )

        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        self.assertEqual(result.as_of, self.plan.as_of)
        self.assertEqual(result.fetched_at, collected_at)
        self.assertTrue(all(
            item.projection.as_of == self.plan.as_of
            for item in result.projection_batch.items
        ))

    def test_official_document_and_replayed_exact_facts_are_ready(self):
        delivery = self.deliver(self.helper.complete_transport)
        document = self.helper.helper.document

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(self.content(document),),
        )

        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        projection = result.projection_batch.items[0].projection
        self.assertEqual(
            projection.evidence_kind,
            LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DOCUMENT_EVIDENCE,
        )
        self.assertEqual(projection.document_ids, (document.document_id,))
        self.assertTrue(projection.deterministic_fact_ids)
        self.assertEqual(projection.manual_fact_ids, ())
        self.assertFalse(projection.risk_filter_passed)

    def test_exact_official_content_is_traceable_even_without_labeled_facts(self):
        delivery = self.deliver(self.helper.complete_transport)
        document = self.helper.helper.document
        unlabeled = replace(
            self.content(document),
            pages=self.helper.helper.content.pages,
            page_count=len(self.helper.helper.content.pages),
        )

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(unlabeled,),
        )

        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        projection = result.projection_batch.items[0].projection
        self.assertEqual(
            projection.evidence_kind,
            LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DOCUMENT_EVIDENCE,
        )
        self.assertEqual(projection.document_ids, (document.document_id,))
        self.assertEqual(projection.deterministic_fact_ids, ())
        self.assertFalse(projection.risk_filter_passed)

    def test_actual_content_fetch_time_is_preserved_after_plan_cutoff(self):
        collected_at = self.plan.as_of + timedelta(seconds=1)
        delivery = deliver_leader_risk_lifecycle(
            replace(self.input_value, collected_at=collected_at),
            transport=self.helper.complete_transport,
        )
        document = self.helper.helper.document
        fetched_at = self.plan.as_of + timedelta(seconds=2)

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(
                self.content(document, fetched_at=fetched_at),
            ),
        )

        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        self.assertEqual(result.as_of, self.plan.as_of)
        self.assertEqual(result.fetched_at, fetched_at)
        self.assertLessEqual(result.source_time, self.plan.as_of)

    def test_document_metadata_is_traceable_and_never_becomes_zero_hit(self):
        delivery = self.deliver(self.helper.complete_transport)

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(),
        )

        self.assertEqual(result.status, LeaderOfficialDeterministicRiskStatus.READY)
        projection = result.projection_batch.items[0].projection
        self.assertEqual(
            projection.evidence_kind,
            LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DISCOVERY_METADATA,
        )
        self.assertNotEqual(
            projection.evidence_kind,
            LeaderOfficialDeterministicRiskEvidenceKind.BOUNDED_NO_DISCLOSURE,
        )
        self.assertEqual(
            projection.document_ids,
            (self.helper.helper.document.document_id,),
        )
        self.assertEqual(projection.content_sha256s, ())
        self.assertFalse(projection.risk_filter_passed)

    def test_identity_mismatch_and_incomplete_pagination_fail_closed(self):
        delivery = self.deliver(self.helper.complete_transport)
        document = self.helper.helper.document
        mismatch = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(self.content(document, symbol="000001"),),
        )
        self.assertNotEqual(
            mismatch.status,
            LeaderOfficialDeterministicRiskStatus.READY,
        )
        self.assertEqual(
            mismatch.projection_batch.items[0].status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )

        def pagination_transport(url, *, data, headers, timeout):
            del url, headers, timeout
            if data["searchkey"] == "减持计划" and data["pageNum"] == "1":
                return self.helper.payload([], total=31, total_pages=2, has_more=True)
            return self.helper.payload([])

        incomplete = self.deliver(pagination_transport)
        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=incomplete,
            document_contents=(),
        )
        self.assertEqual(
            result.status,
            LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.ready_count, 0)

    def test_replaced_discovery_result_cannot_invent_an_official_document(self):
        delivery = self.deliver(
            lambda *args, **kwargs: self.helper.payload([]),
        )
        lifecycle = delivery.lifecycle_result
        discovery = lifecycle.discovery_result
        forged_item = replace(
            discovery.items[0],
            status=LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
            issuer_identity=self.helper.helper.document.issuer_identity,
            documents=(
                LeaderRiskOfficialCandidateDocument(
                    document=self.helper.helper.document,
                    candidate_categories=(
                        self.helper.helper.document.candidate_category,
                    ),
                    matched_query_keys=("立案告知书",),
                    source_statuses=(OfficialRiskSourceStatus.PARTIAL,),
                ),
            ),
            reasons=("risk_official_candidate_discovery_partial",),
        )
        forged_discovery = replace(
            discovery,
            status=LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
            items=(forged_item, *discovery.items[1:]),
        )
        forged_delivery = replace(
            delivery,
            lifecycle_result=replace(
                lifecycle,
                discovery_result=forged_discovery,
            ),
        )

        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=forged_delivery,
            document_contents=(),
        )

        self.assertEqual(
            result.status,
            LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.ready_count, 0)

    def test_production_collection_and_runtime_bridge_do_not_require_manual_d8(self):
        delivery = self.deliver(
            lambda *args, **kwargs: self.helper.payload([]),
        )
        frozen = LeaderOfficialDeterministicRiskFrozenBatch(
            delivery=delivery,
            document_contents=(),
        )
        collected = collect_leader_official_deterministic_risk_source(
            self.context,
            frozen,
        )
        self.assertEqual(
            collected.source_contract_id,
            LEADER_OFFICIAL_DETERMINISTIC_RISK_SOURCE_CONTRACT_ID,
        )
        self.assertEqual(collected.status.value, "completed")

        bridge = build_leader_formal_research_runtime_bridge(
            self.context,
            repository=_RepositoryMustNotBeUsed(),
            official_risk_batch=collected.payload,
        )
        self.assertEqual(
            bridge.status,
            LeaderFormalResearchRuntimeBridgeStatus.READY,
        )
        self.assertEqual(bridge.ready_count, self.plan.candidate_count)
        self.assertEqual(bridge.review_chains, ())
        self.assertFalse(bridge.formal_usable)
        self.assertFalse(bridge.state_transition_allowed)

    def test_dataclass_replace_loses_trusted_producer_identity(self):
        delivery = self.deliver(
            lambda *args, **kwargs: self.helper.payload([]),
        )
        result = build_leader_official_deterministic_risk_batch(
            candidate_plan=self.plan,
            delivery=delivery,
            document_contents=(),
        )

        self.assertTrue(
            is_leader_official_deterministic_risk_batch_valid(
                result,
                candidate_plan=self.plan,
            )
        )
        self.assertFalse(
            is_leader_official_deterministic_risk_batch_valid(
                replace(result),
                candidate_plan=self.plan,
            )
        )


if __name__ == "__main__":
    unittest.main()
