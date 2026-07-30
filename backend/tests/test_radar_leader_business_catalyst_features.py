import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    BusinessEvidenceSourceKind,
    BusinessProofType,
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessCatalystReview,
    LeaderBusinessProof,
    LeaderCatalystReference,
    build_leader_business_catalyst_features,
)
from radar.leader_research_features import ResearchFeatureStatus


AS_OF = datetime(2026, 7, 27, 2, 0, tzinfo=timezone.utc)


def reviewed_input(
    relation=BusinessCatalystRelation.HIGHLY_RELATED,
):
    catalyst = LeaderCatalystReference(
        catalyst_id="catalyst-66-20260727",
        industry_code="66",
        industry_release_id="release-1",
        source_kind=BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE,
        source_name="深圳证券交易所",
        source_url="https://www.szse.cn/disclosure/catalyst-1",
        document_id="catalyst-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=AS_OF + timedelta(days=30),
        summary="行业催化结构化摘要",
    )
    proof = LeaderBusinessProof(
        evidence_id="business-proof-1",
        evidence_version="business-proof-v1",
        symbol="000001",
        proof_type=BusinessProofType.PRODUCT,
        source_kind=(
            BusinessEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
        ),
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-26/business-proof-1.PDF"
        ),
        document_id="business-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=None,
        related_catalyst_ids=(),
        fact_summary="主营产品结构化摘要",
    )
    review = LeaderBusinessCatalystReview(
        review_id="review-1",
        mapping_version="mapping-v1",
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst_id=catalyst.catalyst_id,
        relation=relation,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=AS_OF - timedelta(hours=1),
        effective_until=AS_OF + timedelta(days=30),
        basis_evidence_ids=(proof.evidence_id,),
        basis_catalyst_id=catalyst.catalyst_id,
        decision_summary="人工审核映射摘要",
    )
    return LeaderBusinessCatalystFeatureInput(
        as_of=AS_OF,
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst=catalyst,
        business_proofs=(proof,),
        reviews=(review,),
        source_status=ResearchFeatureStatus.READY,
    )


def official_direct_input():
    value = reviewed_input()
    proof = replace(
        value.business_proofs[0],
        related_catalyst_ids=(value.catalyst.catalyst_id,),
    )
    return replace(
        value,
        business_proofs=(proof,),
        reviews=(),
    )


class LeaderBusinessCatalystFeatureTests(unittest.TestCase):
    def test_official_disclosure_direct_relation_is_ready_research_only(
        self,
    ):
        result = build_leader_business_catalyst_features(
            official_direct_input()
        )
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.relation,
            BusinessCatalystRelation.DIRECT,
        )
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertIsNone(evidence["researchScore"])
        self.assertNotIn("factSummary", str(evidence))
        self.assertNotIn("主营产品结构化摘要", str(evidence))

    def test_manual_reviewed_mapping_can_be_highly_related(self):
        result = build_leader_business_catalyst_features(
            reviewed_input(
                relation=BusinessCatalystRelation.HIGHLY_RELATED
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.relation,
            BusinessCatalystRelation.HIGHLY_RELATED,
        )

    def test_contract_failures_keep_stable_status_and_reason(self):
        value = reviewed_input()
        proof = value.business_proofs[0]
        review = value.reviews[0]
        cases = (
            (
                replace(
                    value,
                    catalyst=replace(
                        value.catalyst,
                        published_at=AS_OF + timedelta(seconds=1),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "catalyst_published_at_future",
            ),
            (
                replace(
                    value,
                    business_proofs=(
                        replace(
                            proof,
                            effective_from=(
                                AS_OF + timedelta(seconds=1)
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_evidence_not_effective",
            ),
            (
                replace(
                    value,
                    business_proofs=(
                        replace(
                            proof,
                            effective_until=(
                                AS_OF - timedelta(seconds=1)
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.STALE,
                "business_evidence_expired",
            ),
            (
                replace(value, symbol="000002"),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_identity_mismatch",
            ),
            (
                replace(
                    value,
                    catalyst=replace(
                        value.catalyst,
                        industry_code="67",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_catalyst_identity_mismatch",
            ),
            (
                replace(value, business_proofs=(proof, proof)),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_evidence_identity_duplicate",
            ),
            (
                replace(
                    value,
                    business_proofs=(
                        replace(proof, document_id=""),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_source_identity_missing",
            ),
            (
                replace(
                    value,
                    business_proofs=(
                        replace(
                            proof,
                            published_at=proof.published_at.replace(
                                tzinfo=None
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_timestamp_timezone_missing",
            ),
            (
                replace(
                    value,
                    reviews=(
                        replace(
                            review,
                            basis_evidence_ids=(
                                "missing-evidence",
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_review_reference_missing",
            ),
            (
                replace(
                    value,
                    reviews=(
                        replace(
                            review,
                            reviewed_at=(
                                AS_OF + timedelta(seconds=1)
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_reviewed_at_future",
            ),
            (
                replace(
                    value,
                    reviews=(
                        review,
                        replace(
                            review,
                            review_id="review-2",
                            mapping_version="mapping-v2",
                            relation=(
                                BusinessCatalystRelation.DISPROVED
                            ),
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_review_conflict",
            ),
            (
                replace(value, reviews=(review, review)),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_review_identity_duplicate",
            ),
            (
                replace(
                    value,
                    business_proofs=(
                        replace(
                            proof,
                            source_url="http://example.test/a",
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_source_url_unverified",
            ),
            (
                replace(
                    value,
                    reviews=(
                        replace(review, review_method="ai"),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "business_review_method_unverified",
            ),
        )
        for input_value, expected_status, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_business_catalyst_features(
                    input_value
                )
                self.assertEqual(result.status, expected_status)
                self.assertIn(expected_reason, result.reasons)

    def test_missing_business_proofs_is_missing(self):
        result = build_leader_business_catalyst_features(replace(
            reviewed_input(),
            business_proofs=(),
            reviews=(),
        ))

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIn("business_evidence_missing", result.reasons)
        self.assertEqual(result.references, ())

    def test_source_failure_has_priority(self):
        result = build_leader_business_catalyst_features(replace(
            reviewed_input(),
            source_status=ResearchFeatureStatus.SOURCE_FAILED,
        ))

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.references, ())

    def test_unconfirmed_and_disproved_remain_distinct(self):
        value = reviewed_input()
        review = value.reviews[0]
        unconfirmed = build_leader_business_catalyst_features(replace(
            value,
            reviews=(
                replace(
                    review,
                    relation=(
                        BusinessCatalystRelation.UNCONFIRMED
                    ),
                ),
            ),
        ))
        disproved = build_leader_business_catalyst_features(replace(
            value,
            reviews=(
                replace(
                    review,
                    relation=BusinessCatalystRelation.DISPROVED,
                ),
            ),
        ))

        self.assertEqual(
            unconfirmed.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            unconfirmed.relation,
            BusinessCatalystRelation.UNCONFIRMED,
        )
        self.assertEqual(
            disproved.status,
            ResearchFeatureStatus.READY,
        )
        self.assertEqual(
            disproved.relation,
            BusinessCatalystRelation.DISPROVED,
        )

    def test_expired_versions_do_not_override_current_mapping(self):
        value = reviewed_input()
        current_proof = value.business_proofs[0]
        current_review = value.reviews[0]
        expired_proof = replace(
            current_proof,
            evidence_id="business-proof-old",
            evidence_version="business-proof-old-v1",
            document_id="business-document-old",
            effective_from=AS_OF - timedelta(days=30),
            effective_until=AS_OF - timedelta(seconds=1),
        )
        expired_review = replace(
            current_review,
            review_id="review-old",
            mapping_version="mapping-old-v1",
            relation=BusinessCatalystRelation.DISPROVED,
            reviewed_at=AS_OF - timedelta(days=10),
            effective_until=AS_OF - timedelta(seconds=1),
            basis_evidence_ids=(expired_proof.evidence_id,),
        )

        result = build_leader_business_catalyst_features(replace(
            value,
            business_proofs=(expired_proof, current_proof),
            reviews=(expired_review, current_review),
        ))

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.relation,
            BusinessCatalystRelation.HIGHLY_RELATED,
        )

    def test_current_review_cannot_use_expired_business_proof(self):
        value = reviewed_input()
        expired_proof = replace(
            value.business_proofs[0],
            effective_until=AS_OF - timedelta(seconds=1),
        )
        active_proof = replace(
            value.business_proofs[0],
            evidence_id="business-proof-active-2",
            evidence_version="business-proof-active-v2",
            document_id="business-document-active-2",
        )

        result = build_leader_business_catalyst_features(replace(
            value,
            business_proofs=(expired_proof, active_proof),
        ))

        self.assertEqual(result.status, ResearchFeatureStatus.STALE)
        self.assertIn(
            "business_review_basis_expired",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
