import unittest
from datetime import date, timedelta

from radar.leader_business_catalyst_features import BusinessProofType
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialCatalystArtifact,
    OfficialDisclosurePlatform,
    build_leader_business_catalyst_inputs_from_official_artifacts_batch,
)
from radar.leader_business_material_human_extraction import (
    LeaderBusinessMaterialHumanExtractionEntry,
    LeaderBusinessMaterialHumanExtractionStatus,
    build_leader_business_material_human_extraction_batch,
)
from radar.leader_business_material_review_queue import (
    build_leader_business_material_review_queue,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.sources.leader_business_official import (
    CninfoBusinessMaterialQuery,
    OfficialBusinessMaterialDiscoveryBatch,
    OfficialBusinessMaterialDiscoveryStatus,
    OfficialBusinessMaterialDocument,
)
from radar.sources.leader_risk_official import CninfoRiskIssuerScope
from tests import test_radar_leader_research_source_admission as helpers


class LeaderBusinessMaterialHumanExtractionTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderResearchSourceAdmissionTests(
            methodName="test_all_verified_sources_enter_existing_provider_in_one_batch"
        )
        helper.setUp()
        self.plan = helper.plan
        batches = []
        for item in self.plan.items:
            scope = CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=f"cninfo-org:org-{item.symbol}",
                resolved_at=self.plan.as_of - timedelta(minutes=2),
            )
            query = CninfoBusinessMaterialQuery(
                candidate_plan_id=self.plan.candidate_set_id,
                scope=scope,
                window_from=date(2025, 1, 1),
                window_until=self.plan.as_of.date(),
            )
            batches.append(OfficialBusinessMaterialDiscoveryBatch(
                status=OfficialBusinessMaterialDiscoveryStatus.READY,
                query=query,
                fetched_at=self.plan.as_of,
                documents=(OfficialBusinessMaterialDocument(
                    document_id=f"cninfo:report-{item.symbol}",
                    document_version=f"cninfo:report-{item.symbol}:v1",
                    symbol=item.symbol,
                    issuer_identity=scope.issuer_identity,
                    title="年度报告",
                    published_at=self.plan.as_of - timedelta(days=30),
                    source_url=(
                        "https://static.cninfo.com.cn/finalpage/2026-07-01/"
                        f"report-{item.symbol}.PDF"
                    ),
                ),),
                coverage_complete=True,
            ))
        self.queue = build_leader_business_material_review_queue(
            self.plan,
            tuple(batches),
        )

    def catalyst(self, item):
        return LeaderOfficialCatalystArtifact(
            platform=OfficialDisclosurePlatform.CNINFO,
            source_contract_id="radar-leader-business-official-cninfo-v1",
            catalyst_id=f"catalyst-{item.industry_code}-v1",
            industry_code=item.industry_code,
            industry_release_id=item.industry_release_id,
            document_id=f"cninfo:catalyst-{item.industry_code}",
            document_version=f"cninfo:catalyst-{item.industry_code}:v1",
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2026-07-01/"
                f"catalyst-{item.industry_code}.PDF"
            ),
            published_at=self.plan.as_of - timedelta(days=10),
            effective_from=self.plan.as_of - timedelta(days=10),
            effective_until=None,
            summary="人工核对的行业催化事实",
        )

    def extraction(self, item, **changes):
        value = LeaderBusinessMaterialHumanExtractionEntry(
            symbol=item.symbol,
            reviewer_key="human-reviewer-1",
            reviewed_at=self.plan.as_of - timedelta(minutes=1),
            catalyst_artifact=self.catalyst(item),
            proof_document_id=f"cninfo:report-{item.symbol}",
            proof_type=BusinessProofType.REVENUE,
            fact_summary="人工从年度报告核对的主营收入事实",
        )
        return LeaderBusinessMaterialHumanExtractionEntry(
            **{**value.__dict__, **changes}
        )

    def test_complete_human_extraction_enters_existing_material_adapter(self):
        extracted = build_leader_business_material_human_extraction_batch(
            self.plan,
            self.queue,
            tuple(self.extraction(item) for item in self.plan.items),
        )
        adapted = build_leader_business_catalyst_inputs_from_official_artifacts_batch(
            candidate_plan=self.plan,
            entries=extracted.material_entries,
        )

        self.assertEqual(
            extracted.status,
            LeaderBusinessMaterialHumanExtractionStatus.READY,
        )
        self.assertTrue(all(
            entry.source_status == ResearchFeatureStatus.READY
            for entry in extracted.material_entries
        ))
        self.assertEqual(adapted.status.value, "ready")
        self.assertFalse(extracted.formal_usable)
        self.assertFalse(adapted.formal_usable)

    def test_missing_human_entries_remain_missing_without_placeholder(self):
        extracted = build_leader_business_material_human_extraction_batch(
            self.plan,
            self.queue,
            (),
        )

        self.assertEqual(
            extracted.status,
            LeaderBusinessMaterialHumanExtractionStatus.MISSING,
        )
        self.assertTrue(all(
            entry.source_status == ResearchFeatureStatus.MISSING
            for entry in extracted.material_entries
        ))
        self.assertTrue(all(
            entry.catalyst_artifact is None
            and entry.proof_artifacts == ()
            for entry in extracted.material_entries
        ))

    def test_unknown_document_and_identity_drift_fail_closed(self):
        first = self.plan.items[0]
        cases = (
            self.extraction(first, proof_document_id="cninfo:unknown"),
            self.extraction(first, symbol="000999"),
            self.extraction(first, reviewer_key=""),
        )

        for malformed in cases:
            result = build_leader_business_material_human_extraction_batch(
                self.plan,
                self.queue,
                (malformed,),
            )
            self.assertEqual(
                result.status,
                LeaderBusinessMaterialHumanExtractionStatus.BLOCKED,
            )
            self.assertEqual(result.material_entries, ())


if __name__ == "__main__":
    unittest.main()
