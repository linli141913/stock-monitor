import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_risk_d8_manual_worklist import (
    LeaderRiskD8ManualWorklistStatus,
    build_leader_risk_d8_manual_worklist,
)
from radar.leader_risk_lifecycle_batch import LeaderRiskLifecycleBatchEntry
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleRealPocStatus,
    deliver_leader_risk_lifecycle,
)
from tests import test_radar_leader_risk_lifecycle_delivery as helpers


class LeaderRiskD8ManualWorklistTests(unittest.TestCase):
    CANDIDATE_SOURCE_SHA256 = "d" * 64

    def setUp(self):
        helper = helpers.LeaderRiskLifecycleDeliveryTests(
            methodName=(
                "test_complete_fixture_pages_replay_human_versions_into_lifecycle"
            )
        )
        helper.setUp()
        self.helper = helper

    def d2_delivery(self):
        input_value = self.helper.input_value(
            collected_at=self.helper.plan.as_of + timedelta(minutes=1),
        )
        self.d2_input = input_value
        entries = tuple(
            LeaderRiskLifecycleBatchEntry(
                symbol=entry.symbol,
                issuer_identity=entry.issuer_identity,
                versions=(),
            )
            for entry in input_value.entries
        )
        fixture_delivery = deliver_leader_risk_lifecycle(
            replace(input_value, entries=entries),
            transport=self.helper.complete_transport,
        )
        return replace(
            fixture_delivery,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.PARTIAL,
        )

    def fixture_delivery(self, *, with_versions=False):
        input_value = self.helper.input_value()
        if not with_versions:
            input_value = replace(
                input_value,
                entries=tuple(
                    LeaderRiskLifecycleBatchEntry(
                        symbol=entry.symbol,
                        issuer_identity=entry.issuer_identity,
                        versions=(),
                    )
                    for entry in input_value.entries
                ),
            )
        return deliver_leader_risk_lifecycle(
            input_value,
            transport=self.helper.complete_transport,
        )

    def test_complete_d2_builds_pending_human_worklist_without_d8_conclusion(self):
        delivery = self.d2_delivery()
        created_at = delivery.as_of + timedelta(minutes=1)

        result = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=created_at,
            candidate_source_packet_sha256=(
                self.CANDIDATE_SOURCE_SHA256
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW,
        )
        self.assertEqual(result.candidate_count, delivery.candidate_scope_count)
        self.assertEqual(len(result.items), delivery.candidate_scope_count)
        self.assertEqual(
            tuple(item.index for item in result.items),
            tuple(range(delivery.candidate_scope_count)),
        )
        self.assertEqual(sum(len(item.documents) for item in result.items), 1)
        self.assertTrue(all(item.review is None for item in result.items))
        source = result.to_source_packet()
        review = result.to_review_packet()
        self.assertEqual(
            source["candidateSourcePacketSha256"],
            self.CANDIDATE_SOURCE_SHA256,
        )
        self.assertEqual(
            review["candidateSourcePacketSha256"],
            self.CANDIDATE_SOURCE_SHA256,
        )
        self.assertEqual(source["d2Audit"], review["d2Audit"])
        self.assertEqual(
            source["d2Audit"],
            {
                "deliveryStatus": delivery.status.value,
                "realPocStatus": delivery.real_poc_status.value,
                "requestCount": delivery.request_count,
                "fetchedPageCount": delivery.fetched_page_count,
                "categoryCount": delivery.category_count,
                "shardCount": delivery.shard_count,
                "queryCount": (
                    delivery.lifecycle_result.discovery_result.query_count
                ),
                "ignoredDocumentCount": (
                    delivery.lifecycle_result.discovery_result
                    .ignored_document_count
                ),
                "queryCategoriesComplete": True,
                "queryPagesComplete": True,
                "queryWindowContinuous": True,
            },
        )
        self.assertEqual(review["sourcePacketSha256"], source["packetSha256"])
        self.assertEqual(
            source.get("d2WindowFrom"),
            self.d2_input.window_from.isoformat(),
        )
        self.assertEqual(
            source.get("d2WindowUntil"),
            self.d2_input.window_until.isoformat(),
        )
        self.assertEqual(review["candidatePlanId"], delivery.candidate_plan_id)
        self.assertEqual(
            review["asOf"],
            delivery.lifecycle_result.as_of.isoformat(),
        )
        self.assertEqual(
            review["d2CollectedAt"],
            delivery.as_of.isoformat(),
        )
        self.assertEqual(
            result.d2_collected_at,
            delivery.as_of,
        )
        self.assertEqual(review["d8VersionCount"], 0)
        self.assertFalse(review["d8SubmissionReady"])
        self.assertTrue(all(
            item["review"] is None for item in review["items"]
        ))
        serialized = str(review)
        self.assertNotIn("manual-review-v1", serialized)
        self.assertNotIn("manual-review-v2", serialized)
        self.assertNotIn("no_relevant_event", serialized)
        self.assertFalse(review["gate"]["formalUsable"])
        self.assertFalse(review["gate"]["stateTransitionAllowed"])

        discovered = (
            delivery.lifecycle_result.discovery_result.items[0]
            .documents[0].document
        )
        packet_document = source["items"][0]["documents"][0]
        self.assertEqual(
            packet_document.get("issuerName"),
            discovered.issuer_name,
        )
        self.assertEqual(
            packet_document.get("sourceName"),
            discovered.source_name,
        )
        self.assertEqual(
            packet_document.get("rawColumnIds"),
            list(discovered.raw_column_ids),
        )
        self.assertEqual(
            packet_document.get("rawAnnouncementTypes"),
            list(discovered.raw_announcement_types),
        )
        self.assertEqual(
            packet_document.get("rawPageColumn"),
            discovered.raw_page_column,
        )
        self.assertEqual(
            packet_document.get("associationReported"),
            discovered.association_reported,
        )

    def test_sanitized_d2_summary_cannot_be_used_as_manual_worklist_source(self):
        result = build_leader_risk_d8_manual_worklist(
            self.d2_delivery().to_evidence(),
            created_at=self.helper.plan.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256=(
                self.CANDIDATE_SOURCE_SHA256
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_d8_manual_worklist_d2_source_unverified",),
        )
        self.assertEqual(result.items, ())

    def test_fixture_not_run_cannot_be_relabelled_as_real_d2_by_builder(self):
        delivery = self.fixture_delivery()

        result = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=delivery.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256=(
                self.CANDIDATE_SOURCE_SHA256
            ),
        )

        self.assertIs(
            delivery.real_poc_status,
            LeaderRiskLifecycleRealPocStatus.NOT_RUN,
        )
        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.BLOCKED,
        )

    def test_existing_d8_versions_cannot_enter_pure_d2_worklist(self):
        fixture = self.fixture_delivery(with_versions=True)
        self.assertTrue(any(
            item.version_count > 0
            for item in fixture.lifecycle_result.items
        ))
        delivery = replace(
            fixture,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.PARTIAL,
        )

        result = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=delivery.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256=(
                self.CANDIDATE_SOURCE_SHA256
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_d8_manual_worklist_d2_source_unverified",),
        )

    def test_hidden_d8_projection_cannot_enter_pure_d2_worklist(self):
        delivery = self.d2_delivery()
        version_delivery = self.fixture_delivery(with_versions=True)
        d2_projection = delivery.lifecycle_result.projection_batch
        version_projection = (
            version_delivery.lifecycle_result.projection_batch
        )
        contaminated_item = replace(
            d2_projection.items[0],
            projection=version_projection.items[0].projection,
        )
        contaminated_projection = replace(
            d2_projection,
            items=(contaminated_item, *d2_projection.items[1:]),
        )
        contaminated_lifecycle = replace(
            delivery.lifecycle_result,
            projection_batch=contaminated_projection,
        )
        contaminated_delivery = replace(
            delivery,
            lifecycle_result=contaminated_lifecycle,
        )

        result = build_leader_risk_d8_manual_worklist(
            contaminated_delivery,
            created_at=delivery.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256=(
                self.CANDIDATE_SOURCE_SHA256
            ),
        )

        self.assertEqual(
            contaminated_projection.ready_count,
            0,
        )
        self.assertIsNotNone(contaminated_item.projection)
        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.BLOCKED,
        )

    def test_candidate_source_packet_hash_is_required(self):
        delivery = self.d2_delivery()

        result = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=delivery.as_of + timedelta(minutes=1),
            candidate_source_packet_sha256="not-a-sha256",
        )

        self.assertEqual(
            result.status,
            LeaderRiskD8ManualWorklistStatus.BLOCKED,
        )


if __name__ == "__main__":
    unittest.main()
