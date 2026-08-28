import hashlib
import unittest
from dataclasses import replace
from datetime import timedelta

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_catalyst_facts import (
    OfficialBusinessCatalystFactResult,
)
from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    build_leader_business_catalyst_features,
)
from radar.leader_business_catalyst_official_adapter import (
    OFFICIAL_BUSINESS_SOURCE_CONTRACTS,
    LeaderOfficialBusinessMaterialBatchEntry,
    LeaderOfficialBusinessProofArtifact,
    LeaderOfficialCatalystArtifact,
    OfficialDisclosurePlatform,
    build_leader_business_catalyst_inputs_from_official_artifacts_batch,
)
from radar.leader_business_deterministic_verification import (
    build_deterministic_official_business_verification,
)
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
    OfficialBusinessFactResult,
)
from radar.leader_business_official_verification_adapter import (
    LeaderOfficialBusinessDeterministicVerificationBatchEntry,
    LeaderOfficialBusinessVerificationBatchStatus,
    apply_official_business_verifications_batch,
)
from radar.leader_business_catalyst_manual_review import (
    LeaderOfficialBusinessManualReviewBatchEntry,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_business_catalyst_features import BusinessProofType
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystKind,
)
from tests import test_radar_leader_research_single_pass_orchestration as f6_helpers


def evidence_fragment(page, text):
    return OfficialBusinessEvidenceFragment(
        page_number=page,
        fragment_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


class LeaderBusinessOfficialVerificationAdapterTests(unittest.TestCase):
    def setUp(self):
        fixture = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        fixture.setUp()
        self.plan = fixture.candidate_plan()

    def material_entry(self, item):
        catalyst_id = f"catalyst-{item.symbol}-v1"
        catalyst_document_id = f"cninfo:catalyst-{item.symbol}"
        annual_document_id = f"cninfo:annual-{item.symbol}"
        return LeaderOfficialBusinessMaterialBatchEntry(
            symbol=item.symbol,
            catalyst_artifact=LeaderOfficialCatalystArtifact(
                platform=OfficialDisclosurePlatform.CNINFO,
                source_contract_id=OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
                    OfficialDisclosurePlatform.CNINFO
                ],
                catalyst_id=catalyst_id,
                industry_code=item.industry_code,
                industry_release_id=item.industry_release_id,
                document_id=catalyst_document_id,
                document_version="v1",
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/2026-07-01/"
                    f"catalyst-{item.symbol}.PDF"
                ),
                published_at=item.as_of - timedelta(days=2),
                effective_from=item.as_of - timedelta(days=2),
                effective_until=None,
                summary="官方催化事实",
            ),
            proof_artifacts=(LeaderOfficialBusinessProofArtifact(
                platform=OfficialDisclosurePlatform.CNINFO,
                source_contract_id=OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
                    OfficialDisclosurePlatform.CNINFO
                ],
                evidence_id=f"business-proof-{item.symbol}-v1",
                evidence_version="v1",
                symbol=item.symbol,
                proof_type=BusinessProofType.PRODUCT,
                document_id=annual_document_id,
                document_version="v1",
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/2026-04-30/"
                    f"annual-{item.symbol}.PDF"
                ),
                published_at=item.as_of - timedelta(days=100),
                effective_from=item.as_of - timedelta(days=100),
                effective_until=None,
                fact_summary="官方主营事实",
            ),),
            source_status=ResearchFeatureStatus.READY,
        )

    def verification_entry(self, item, **artifact_changes):
        validated_at = item.as_of - timedelta(hours=1)
        annual_text = f"{item.symbol}-annual-evidence"
        catalyst_text = f"{item.symbol}-catalyst-evidence"
        annual = OfficialBusinessFactResult(
            status=AutomaticBusinessEvidenceStatus.READY,
            symbol=item.symbol,
            industry_code=item.industry_code,
            industry_release_id=item.industry_release_id,
            document_id=f"cninfo:annual-{item.symbol}",
            document_version="v1",
            content_sha256="a" * 64,
            business_terms=("工业软件",),
            fragments=(evidence_fragment(20, annual_text),),
            source_time=item.as_of - timedelta(days=100),
            validated_at=validated_at - timedelta(minutes=2),
        )
        catalyst = OfficialBusinessCatalystFactResult(
            status=AutomaticBusinessEvidenceStatus.READY,
            document_id=f"cninfo:catalyst-{item.symbol}",
            document_version="v1",
            symbol=item.symbol,
            issuer_identity=f"cninfo-org:{item.symbol}",
            event_kind=OfficialBusinessCatalystKind.MAJOR_CONTRACT,
            content_sha256="b" * 64,
            business_terms=("工业软件",),
            fragments=(evidence_fragment(3, catalyst_text),),
            negative_event=False,
            source_time=item.as_of - timedelta(days=2),
            validated_at=validated_at - timedelta(minutes=1),
        )
        built = build_deterministic_official_business_verification(
            item,
            annual,
            (catalyst,),
            validated_at=validated_at,
        )
        artifact = replace(built.artifact, **artifact_changes)
        return LeaderOfficialBusinessDeterministicVerificationBatchEntry(
            symbol=item.symbol,
            verification_artifact=artifact,
        )

    def material_batch(self):
        return build_leader_business_catalyst_inputs_from_official_artifacts_batch(
            candidate_plan=self.plan,
            entries=tuple(self.material_entry(item) for item in self.plan.items),
        )

    def test_deterministic_artifacts_replay_to_existing_ready_inputs(self):
        result = apply_official_business_verifications_batch(
            self.material_batch(),
            deterministic_entries=tuple(
                self.verification_entry(item) for item in self.plan.items
            ),
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessVerificationBatchStatus.READY,
        )
        self.assertEqual(tuple(result.inputs_by_symbol), tuple(
            item.symbol for item in self.plan.items
        ))
        feature = build_leader_business_catalyst_features(
            result.items[0].input_value
        )
        self.assertEqual(feature.status, ResearchFeatureStatus.READY)
        self.assertEqual(feature.relation, BusinessCatalystRelation.DIRECT)
        self.assertEqual(
            result.items[0].input_value.reviews[0].review_method,
            "deterministic_official",
        )
        self.assertEqual(
            result.items[0].input_value.reviews[0].mapping_version,
            "radar-leader-business-deterministic-relation-v31",
        )
        self.assertFalse(result.formal_gate_ready)

        stale_version = (
            "radar-leader-business-deterministic-relation-v20"
        )
        stale_review = replace(
            result.items[0].input_value.reviews[0],
            mapping_version=stale_version,
            reviewer_key=stale_version,
        )
        stale_feature = build_leader_business_catalyst_features(replace(
            result.items[0].input_value,
            reviews=(stale_review,),
        ))
        self.assertEqual(
            stale_feature.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            stale_feature.reasons,
            ("business_review_method_unverified",),
        )

    def test_tampered_artifact_is_rejected_without_input(self):
        entries = tuple(
            self.verification_entry(
                item,
                matched_terms=("篡改词",),
            ) if index == 0 else self.verification_entry(item)
            for index, item in enumerate(self.plan.items)
        )

        result = apply_official_business_verifications_batch(
            self.material_batch(),
            deterministic_entries=entries,
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessVerificationBatchStatus.PARTIAL,
        )
        self.assertIsNone(result.items[0].input_value)
        self.assertEqual(
            result.items[0].reasons,
            ("business_deterministic_verification_unverified",),
        )

    def test_deterministic_entries_cannot_enter_manual_or_mixed_path(self):
        deterministic = tuple(
            self.verification_entry(item) for item in self.plan.items
        )
        forged_manual = tuple(
            LeaderOfficialBusinessManualReviewBatchEntry(
                symbol=entry.symbol,
                review_artifact=entry.verification_artifact,
            )
            for entry in deterministic
        )

        forged = apply_official_business_verifications_batch(
            self.material_batch(),
            manual_entries=forged_manual,
        )
        mixed = apply_official_business_verifications_batch(
            self.material_batch(),
            manual_entries=forged_manual,
            deterministic_entries=deterministic,
        )

        self.assertNotEqual(
            forged.status,
            LeaderOfficialBusinessVerificationBatchStatus.READY,
        )
        self.assertEqual(
            mixed.status,
            LeaderOfficialBusinessVerificationBatchStatus.BLOCKED,
        )


if __name__ == "__main__":
    unittest.main()
