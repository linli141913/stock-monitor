import copy
import unittest
from datetime import timedelta

from radar.leader_business_material_human_extraction import (
    LeaderBusinessMaterialHumanExtractionStatus,
)
from radar.leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewDecisionStatus,
    LeaderBusinessMaterialReviewSourcePacketStatus,
    LeaderBusinessMaterialReviewSubmissionStatus,
    build_leader_business_material_review_source_packet,
    build_leader_business_material_review_template,
    load_leader_business_material_review_source_packet,
    parse_leader_business_material_review_submission,
)
from tests.test_radar_leader_business_material_human_extraction import (
    LeaderBusinessMaterialHumanExtractionTests,
)


class LeaderBusinessMaterialReviewSubmissionTests(unittest.TestCase):
    def setUp(self):
        helper = LeaderBusinessMaterialHumanExtractionTests(
            methodName=(
                "test_complete_human_extraction_enters_existing_material_adapter"
            )
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan
        self.queue = helper.queue
        self.imported_at = self.plan.as_of + timedelta(minutes=20)

    def filled_payload(self):
        payload = build_leader_business_material_review_template(
            self.plan,
            self.queue,
        )
        payload.update({
            "reviewBatchId": "business-facts-20260820-a",
            "reviewVersion": "manual-business-facts-v1",
            "supersedesReviewVersion": None,
            "reviewerKey": "human-reviewer-1",
            "reviewedAt": (
                self.plan.as_of + timedelta(minutes=10)
            ).isoformat(),
        })
        for item, plan_item in zip(payload["entries"], self.plan.items):
            published_at = self.plan.as_of - timedelta(days=10)
            item["review"] = {
                "decision": "confirmed",
                "decisionSummary": "已逐页核对主营事实与行业催化材料",
                "proofDocumentId": f"cninfo:report-{plan_item.symbol}",
                "proofType": "revenue",
                "factSummary": "年度报告披露的主营收入事实",
                "catalyst": {
                    "catalystId": f"catalyst-{plan_item.industry_code}-v1",
                    "documentId": (
                        f"cninfo:catalyst-{plan_item.industry_code}"
                    ),
                    "documentVersion": (
                        f"cninfo:catalyst-{plan_item.industry_code}:v1"
                    ),
                    "sourceUrl": (
                        "https://static.cninfo.com.cn/finalpage/"
                        "2026-07-01/catalyst.PDF"
                    ),
                    "publishedAt": published_at.isoformat(),
                    "effectiveFrom": published_at.isoformat(),
                    "effectiveUntil": None,
                    "summary": "人工核对的行业催化事实",
                },
            }
        return payload

    def test_template_freezes_candidate_and_documents_without_prefilling_judgment(self):
        payload = build_leader_business_material_review_template(
            self.plan,
            self.queue,
        )

        self.assertEqual(
            payload["contractId"],
            "radar-leader-business-material-review-submission-v1",
        )
        self.assertEqual(payload["candidatePlanId"], self.plan.candidate_set_id)
        self.assertEqual(payload["candidateCount"], self.plan.candidate_count)
        self.assertEqual(
            [item["symbol"] for item in payload["entries"]],
            [item.symbol for item in self.plan.items],
        )
        first = payload["entries"][0]
        self.assertEqual(
            first["documents"][0]["documentId"],
            f"cninfo:report-{self.plan.items[0].symbol}",
        )
        self.assertTrue(all(
            value is None
            for value in first["review"].values()
        ))
        self.assertIsNone(payload["reviewerKey"])
        self.assertNotIn("主营收入事实", repr(payload))

    def test_source_packet_round_trip_rebuilds_the_exact_verified_scope(self):
        packet = build_leader_business_material_review_source_packet(
            self.plan,
            self.queue,
        )

        loaded = load_leader_business_material_review_source_packet(packet)

        self.assertEqual(
            loaded.status,
            LeaderBusinessMaterialReviewSourcePacketStatus.READY,
        )
        self.assertEqual(
            loaded.candidate_plan.candidate_set_id,
            self.plan.candidate_set_id,
        )
        self.assertEqual(
            loaded.review_queue.items[0].documents[0].document_id,
            f"cninfo:report-{self.plan.items[0].symbol}",
        )
        self.assertFalse(loaded.formal_usable)

    def test_source_packet_digest_detects_accidental_identity_tampering(self):
        packet = build_leader_business_material_review_source_packet(
            self.plan,
            self.queue,
        )
        packet["payload"]["candidatePlan"]["items"][0][
            "symbol"
        ] = "000999"

        loaded = load_leader_business_material_review_source_packet(packet)

        self.assertEqual(
            loaded.status,
            LeaderBusinessMaterialReviewSourcePacketStatus.BLOCKED,
        )
        self.assertIsNone(loaded.candidate_plan)
        self.assertIsNone(loaded.review_queue)

    def test_complete_real_human_submission_builds_existing_extraction_artifact(self):
        payload = self.filled_payload()

        result = parse_leader_business_material_review_submission(
            self.plan,
            self.queue,
            payload,
            clock=lambda: self.imported_at,
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewSubmissionStatus.READY,
        )
        self.assertEqual(result.review_batch_id, "business-facts-20260820-a")
        self.assertEqual(result.review_version, "manual-business-facts-v1")
        self.assertEqual(result.confirmed_count, self.plan.candidate_count)
        self.assertEqual(result.not_confirmed_count, 0)
        self.assertEqual(
            result.extraction_batch.status,
            LeaderBusinessMaterialHumanExtractionStatus.READY,
        )
        self.assertTrue(all(
            decision.status
            is LeaderBusinessMaterialReviewDecisionStatus.CONFIRMED
            for decision in result.decisions
        ))
        evidence = result.to_evidence()
        self.assertNotIn(self.plan.items[0].symbol, repr(evidence))
        self.assertNotIn("年度报告披露", repr(evidence))
        self.assertFalse(evidence["gate"]["formalGateReady"])

    def test_not_confirmed_decision_stays_missing_without_fabricated_artifact(self):
        payload = self.filled_payload()
        payload["entries"][0]["review"] = {
            "decision": "not_confirmed",
            "decisionSummary": "官方材料不足以确认主营关系",
            "proofDocumentId": None,
            "proofType": None,
            "factSummary": None,
            "catalyst": None,
        }

        result = parse_leader_business_material_review_submission(
            self.plan,
            self.queue,
            payload,
            clock=lambda: self.imported_at,
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialReviewSubmissionStatus.MISSING,
        )
        self.assertEqual(result.not_confirmed_count, 1)
        self.assertEqual(
            result.extraction_batch.status,
            LeaderBusinessMaterialHumanExtractionStatus.MISSING,
        )
        self.assertIsNone(
            result.extraction_batch.material_entries[0].catalyst_artifact
        )

    def test_tampered_scope_document_and_non_human_identity_fail_closed(self):
        cases = []
        changed_plan = self.filled_payload()
        changed_plan["candidatePlanId"] = "other-plan"
        cases.append(changed_plan)
        changed_symbol = self.filled_payload()
        changed_symbol["entries"][0]["symbol"] = "000999"
        cases.append(changed_symbol)
        changed_document = self.filled_payload()
        changed_document["entries"][0]["documents"][0][
            "documentId"
        ] = "cninfo:forged"
        cases.append(changed_document)
        non_human = self.filled_payload()
        non_human["reviewerKey"] = 7
        cases.append(non_human)

        for payload in cases:
            result = parse_leader_business_material_review_submission(
                self.plan,
                self.queue,
                payload,
                clock=lambda: self.imported_at,
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialReviewSubmissionStatus.BLOCKED,
            )
            self.assertIsNone(result.extraction_batch)

    def test_review_time_and_official_catalyst_source_are_strict(self):
        before_source = self.filled_payload()
        before_source["reviewedAt"] = (
            self.plan.as_of - timedelta(days=40)
        ).isoformat()
        future = self.filled_payload()
        future["reviewedAt"] = (
            self.imported_at + timedelta(seconds=1)
        ).isoformat()
        untrusted = self.filled_payload()
        untrusted["entries"][0]["review"]["catalyst"][
            "sourceUrl"
        ] = "https://example.com/catalyst.pdf"

        for payload in (before_source, future, untrusted):
            result = parse_leader_business_material_review_submission(
                self.plan,
                self.queue,
                payload,
                clock=lambda: self.imported_at,
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialReviewSubmissionStatus.BLOCKED,
            )
            self.assertIsNone(result.extraction_batch)

    def test_incomplete_or_unknown_fields_are_rejected_not_silently_ignored(self):
        missing = self.filled_payload()
        del missing["entries"][0]["review"]["factSummary"]
        unknown = self.filled_payload()
        unknown["entries"][0]["review"]["aiSuggestion"] = "自动确认"
        duplicate = self.filled_payload()
        duplicate["entries"][1] = copy.deepcopy(duplicate["entries"][0])
        ambiguous_identity = self.filled_payload()
        ambiguous_identity["reviewerKey"] = " human-reviewer-1 "

        for payload in (missing, unknown, duplicate, ambiguous_identity):
            result = parse_leader_business_material_review_submission(
                self.plan,
                self.queue,
                payload,
                clock=lambda: self.imported_at,
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialReviewSubmissionStatus.BLOCKED,
            )


if __name__ == "__main__":
    unittest.main()
