import sqlite3
import unittest
from dataclasses import replace

from radar.leader_risk_invalidation_features import RiskCategory
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleRealPocStatus,
    deliver_leader_risk_lifecycle,
)
from radar.leader_risk_review_persistence import (
    LeaderRiskReviewPersistenceStatus,
    persist_verified_leader_risk_review_batch,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from tests import test_radar_leader_risk_lifecycle_delivery as delivery_helpers


class LeaderRiskReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        helper = delivery_helpers.LeaderRiskLifecycleDeliveryTests(
            methodName=(
                "test_complete_fixture_pages_replay_human_versions_into_lifecycle"
            )
        )
        helper.setUp()
        self.helper = helper
        self.input_value = helper.input_value()
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            clock=lambda: self.input_value.collected_at,
        )
        as_of = self.input_value.candidate_plan.as_of.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES (?, ?, 'succeeded', 1, ?, ?)
            """,
            (
                self.input_value.candidate_plan.radar_run_id,
                as_of,
                as_of,
                as_of,
            ),
        )
        self.connection.commit()
        self.repository = LeaderRiskReviewRepository(
            self.connection,
            clock=lambda: self.input_value.collected_at,
        )

    def tearDown(self):
        self.connection.close()

    def delivery(self, transport=None):
        result = deliver_leader_risk_lifecycle(
            self.input_value,
            transport=transport or self.helper.complete_transport,
        )
        return replace(
            result,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.PARTIAL,
        )

    def test_verified_real_delivery_persists_idempotent_batch_without_d8(self):
        delivery = self.delivery()

        first = persist_verified_leader_risk_review_batch(
            self.input_value,
            delivery,
            self.repository,
        )
        second = persist_verified_leader_risk_review_batch(
            self.input_value,
            delivery,
            self.repository,
        )

        self.assertEqual(
            first.status,
            LeaderRiskReviewPersistenceStatus.PERSISTED,
        )
        self.assertEqual(
            second.status,
            LeaderRiskReviewPersistenceStatus.UNCHANGED,
        )
        self.assertEqual(first.review_batch_id, second.review_batch_id)
        batch = self.repository.get_review_batch(first.review_batch_id)
        self.assertEqual(
            batch["candidateCount"],
            self.input_value.candidate_plan.candidate_count,
        )
        self.assertEqual(batch["documentCount"], 1)
        self.assertEqual(len(batch["documents"]), 1)
        self.assertTrue(batch["queryCategoriesComplete"])
        self.assertTrue(batch["queryPagesComplete"])
        self.assertTrue(batch["queryWindowContinuous"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM "
                "radar_leader_risk_manual_review_versions"
            ).fetchone()[0],
            0,
        )

    def test_same_document_in_two_categories_persists_two_links_once(self):
        input_value = replace(
            self.input_value,
            entries=tuple(
                replace(entry, versions=())
                for entry in self.input_value.entries
            ),
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] in {
                "立案告知书",
                "监管措施决定书",
            }:
                return self.helper.payload([self.helper.document_row()])
            return self.helper.payload([])

        delivery = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )
        result = persist_verified_leader_risk_review_batch(
            input_value,
            replace(
                delivery,
                real_poc_status=LeaderRiskLifecycleRealPocStatus.PARTIAL,
            ),
            self.repository,
        )

        self.assertEqual(result.document_count, 1)
        self.assertEqual(result.document_link_count, 2)
        batch = self.repository.get_review_batch(result.review_batch_id)
        self.assertEqual(
            {item.candidate_category for item in batch["documents"]},
            {RiskCategory.REGULATORY, RiskCategory.INVESTIGATION},
        )

    def test_incomplete_or_tampered_delivery_is_blocked_before_write(self):
        delivery = self.delivery()
        lifecycle = delivery.lifecycle_result
        discovery = lifecycle.discovery_result
        first_item = discovery.items[0]
        first_document = first_item.documents[0]
        cases = (
            replace(
                delivery,
                lifecycle_result=replace(
                    lifecycle,
                    query_pages_complete=False,
                ),
            ),
            replace(delivery, candidate_plan_id="other-plan"),
            replace(
                delivery,
                lifecycle_result=replace(
                    lifecycle,
                    discovery_result=replace(
                        discovery,
                        items=(replace(
                            first_item,
                            documents=(replace(
                                first_document,
                                matched_query_keys=("错误关键词",),
                            ),),
                        ),),
                    ),
                ),
            ),
        )

        for value in cases:
            with self.subTest(reasons=value.reasons):
                result = persist_verified_leader_risk_review_batch(
                    self.input_value,
                    value,
                    self.repository,
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskReviewPersistenceStatus.BLOCKED,
                )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_leader_risk_review_batches"
            ).fetchone()[0],
            0,
        )

    def test_fixture_delivery_without_real_poc_marker_is_blocked(self):
        delivery = deliver_leader_risk_lifecycle(
            self.input_value,
            transport=self.helper.complete_transport,
        )

        result = persist_verified_leader_risk_review_batch(
            self.input_value,
            delivery,
            self.repository,
        )

        self.assertEqual(
            result.status,
            LeaderRiskReviewPersistenceStatus.BLOCKED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_review_persistence_delivery_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
