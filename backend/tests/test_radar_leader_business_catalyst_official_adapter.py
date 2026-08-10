import unittest
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

from radar.leader_business_catalyst_features import (
    BusinessProofType,
    build_leader_business_catalyst_features,
)
from radar.leader_business_catalyst_official_adapter import (
    OFFICIAL_BUSINESS_SOURCE_CONTRACTS,
    LeaderOfficialBusinessMaterialBatchEntry,
    LeaderOfficialBusinessMaterialBatchStatus,
    LeaderOfficialBusinessProofArtifact,
    LeaderOfficialCatalystArtifact,
    OfficialDisclosurePlatform,
    build_leader_business_catalyst_input_from_official_artifacts,
    build_leader_business_catalyst_inputs_from_official_artifacts_batch,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderBatchStatus,
    build_leader_research_input_provider_batch_from_plan,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
    build_verified_leader_research_provider_input,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)


AS_OF = datetime(2026, 8, 9, 0, 10, tzinfo=timezone.utc)


def catalyst(**changes):
    value = LeaderOfficialCatalystArtifact(
        platform=OfficialDisclosurePlatform.SZSE,
        source_contract_id=OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
            OfficialDisclosurePlatform.SZSE
        ],
        catalyst_id="catalyst-66-20260809-v1",
        industry_code="66",
        industry_release_id="industry-release-1",
        document_id="szse:catalyst-document-1",
        document_version="v1",
        source_url="https://www.szse.cn/disclosure/catalyst-1",
        published_at=AS_OF - timedelta(days=2),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=AS_OF + timedelta(days=30),
        summary="行业催化结构化事实",
    )
    return replace(value, **changes)


def proof(**changes):
    value = LeaderOfficialBusinessProofArtifact(
        platform=OfficialDisclosurePlatform.CNINFO,
        source_contract_id=OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
            OfficialDisclosurePlatform.CNINFO
        ],
        evidence_id="business-proof-000001-v1",
        evidence_version="v1",
        symbol="000001",
        proof_type=BusinessProofType.PRODUCT,
        document_id="cninfo:business-document-1",
        document_version="v1",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-08-07/business-document-1.PDF"
        ),
        published_at=AS_OF - timedelta(days=2),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=None,
        fact_summary="主营产品结构化事实",
    )
    return replace(value, **changes)


def build(**changes):
    values = {
        "as_of": AS_OF,
        "symbol": "000001",
        "industry_code": "66",
        "industry_release_id": "industry-release-1",
        "catalyst_artifact": catalyst(),
        "proof_artifacts": (proof(),),
        "source_status": ResearchFeatureStatus.READY,
    }
    values.update(changes)
    return build_leader_business_catalyst_input_from_official_artifacts(
        **values
    )


def batch_entry(item, *, source_status=ResearchFeatureStatus.READY):
    return LeaderOfficialBusinessMaterialBatchEntry(
        symbol=item.symbol,
        catalyst_artifact=catalyst(
            catalyst_id=(
                f"catalyst-{item.industry_code}-20260809-v1"
            ),
            industry_code=item.industry_code,
            industry_release_id=item.industry_release_id,
            document_id=(
                f"szse:catalyst-{item.industry_code}-document-1"
            ),
            published_at=item.as_of - timedelta(days=2),
            effective_from=item.as_of - timedelta(days=1),
            effective_until=item.as_of + timedelta(days=30),
        ),
        proof_artifacts=(proof(
            symbol=item.symbol,
            evidence_id=f"business-proof-{item.symbol}-v1",
            document_id=f"cninfo:business-document-{item.symbol}",
            published_at=item.as_of - timedelta(days=2),
            effective_from=item.as_of - timedelta(days=1),
        ),),
        source_status=source_status,
    )


class LeaderBusinessCatalystOfficialAdapterTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        self.plan = self.f6.candidate_plan()

    def test_verified_official_material_builds_unconfirmed_research_input(self):
        result = build()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.input_value)
        self.assertEqual(result.input_value.reviews, ())
        self.assertEqual(
            result.input_value.catalyst.source_contract_id,
            OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
                OfficialDisclosurePlatform.SZSE
            ],
        )
        self.assertEqual(result.input_value.catalyst.document_version, "v1")
        self.assertEqual(
            result.input_value.business_proofs[0].source_contract_id,
            OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
                OfficialDisclosurePlatform.CNINFO
            ],
        )
        self.assertEqual(
            result.input_value.business_proofs[0].related_catalyst_ids,
            (),
        )
        feature = build_leader_business_catalyst_features(
            result.input_value
        )
        self.assertEqual(
            feature.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            feature.reasons,
            ("business_relation_unconfirmed",),
        )
        self.assertEqual(
            feature.references[0]["sourceContractId"],
            OFFICIAL_BUSINESS_SOURCE_CONTRACTS[
                OfficialDisclosurePlatform.SZSE
            ],
        )
        self.assertEqual(feature.references[0]["documentVersion"], "v1")
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_usable)

    def test_platform_contract_and_domain_must_match_fixed_registry(self):
        cases = (
            catalyst(
                source_contract_id="caller-declared-contract-v1",
            ),
            catalyst(
                source_url="https://company.example.com/disclosure/1",
            ),
            catalyst(
                source_url=(
                    "https://www.szse.cn/disclosure/1?token=secret"
                ),
            ),
        )

        for artifact in cases:
            with self.subTest(artifact=artifact.document_id):
                result = build(catalyst_artifact=artifact)
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.input_value)
                self.assertNotIn("secret", repr(result))

    def test_source_failure_and_missing_material_never_create_placeholder(self):
        failed = build(source_status=ResearchFeatureStatus.SOURCE_FAILED)
        missing = build(proof_artifacts=())

        self.assertEqual(
            failed.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertIsNone(failed.input_value)
        self.assertEqual(missing.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(missing.input_value)

    def test_identity_time_and_stage_scope_fail_closed(self):
        cases = (
            (
                {"symbol": "920023"},
                "business_symbol_out_of_scope",
            ),
            (
                {"proof_artifacts": (proof(symbol="000002"),)},
                "official_business_material_identity_mismatch",
            ),
            (
                {"catalyst_artifact": catalyst(
                    published_at=AS_OF + timedelta(seconds=1),
                )},
                "catalyst_published_at_future",
            ),
            (
                {"proof_artifacts": (proof(
                    effective_until=AS_OF - timedelta(seconds=1),
                ),)},
                "business_evidence_expired",
            ),
        )

        for updates, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build(**updates)
                self.assertNotEqual(result.status, ResearchFeatureStatus.READY)
                self.assertIsNone(result.input_value)
                self.assertIn(expected_reason, result.reasons)

    def test_adapter_artifacts_expose_no_relation_or_ai_review_fields(self):
        catalyst_fields = {item.name for item in fields(
            LeaderOfficialCatalystArtifact
        )}
        proof_fields = {item.name for item in fields(
            LeaderOfficialBusinessProofArtifact
        )}

        self.assertNotIn("relation", catalyst_fields | proof_fields)
        self.assertNotIn("related_catalyst_ids", proof_fields)
        self.assertNotIn("review_method", catalyst_fields | proof_fields)
        self.assertNotIn("ai_analysis", catalyst_fields | proof_fields)

    def test_malformed_times_return_stable_status_instead_of_raising(self):
        cases = (
            {"as_of": "2026-08-09"},
            {"catalyst_artifact": catalyst(published_at="bad-time")},
            {"proof_artifacts": (proof(effective_from=None),)},
        )

        for updates in cases:
            with self.subTest(updates=tuple(updates)):
                result = build(**updates)
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(
                    result.reasons,
                    ("official_business_material_time_unverified",),
                )
                self.assertIsNone(result.input_value)

    def test_batch_preserves_plan_order_and_exposes_read_only_ready_map(self):
        result = (
            build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                candidate_plan=self.plan,
                entries=tuple(
                    batch_entry(item)
                    for item in reversed(self.plan.items)
                ),
            )
        )
        symbols = tuple(item.symbol for item in self.plan.items)

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessMaterialBatchStatus.READY,
        )
        self.assertEqual(tuple(result.inputs_by_symbol), symbols)
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            symbols,
        )
        with self.assertRaises(TypeError):
            result.inputs_by_symbol["000999"] = result.items[0].input_value
        self.assertTrue(all(
            build_leader_business_catalyst_features(input_value).reasons
            == ("business_relation_unconfirmed",)
            for input_value in result.inputs_by_symbol.values()
        ))
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_batch_isolates_failed_candidate_without_dropping_it(self):
        entries = tuple(
            batch_entry(
                item,
                source_status=(
                    ResearchFeatureStatus.SOURCE_FAILED
                    if index == 1
                    else ResearchFeatureStatus.READY
                ),
            )
            for index, item in enumerate(self.plan.items)
        )

        result = (
            build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                candidate_plan=self.plan,
                entries=entries,
            )
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessMaterialBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertNotIn(
            self.plan.items[1].symbol,
            result.inputs_by_symbol,
        )
        self.assertEqual(len(result.items), len(self.plan.items))

    def test_batch_preserves_explicit_all_missing_state(self):
        result = (
            build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                candidate_plan=self.plan,
                entries=tuple(
                    batch_entry(
                        item,
                        source_status=ResearchFeatureStatus.MISSING,
                    )
                    for item in self.plan.items
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderOfficialBusinessMaterialBatchStatus.MISSING,
        )
        self.assertEqual(result.inputs_by_symbol, {})
        self.assertTrue(all(
            item.status == ResearchFeatureStatus.MISSING
            for item in result.items
        ))

    def test_batch_requires_explicit_candidate_set_and_rejects_duplicates(self):
        entries = tuple(batch_entry(item) for item in self.plan.items)
        cases = (
            entries[:-1],
            (*entries, entries[0]),
            (*entries, replace(entries[0], symbol="000999")),
        )

        for malformed in cases:
            with self.subTest(size=len(malformed)):
                result = (
                    build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                        candidate_plan=self.plan,
                        entries=malformed,
                    )
                )
                self.assertEqual(
                    result.status,
                    LeaderOfficialBusinessMaterialBatchStatus.BLOCKED,
                )
                self.assertEqual(result.inputs_by_symbol, {})

    def test_batch_ready_map_enters_existing_provider_as_analysis_only(self):
        batch = (
            build_leader_business_catalyst_inputs_from_official_artifacts_batch(
                candidate_plan=self.plan,
                entries=tuple(batch_entry(item) for item in self.plan.items),
            )
        )
        raw = self.f6.raw_inputs()
        context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=raw["quote_batch"],
            quote_health=raw["quote_health"],
            security_records=raw["security_records"],
            industry_records=raw["industry_records"],
        )
        provider_input = build_verified_leader_research_provider_input(
            context,
            provider_contract_id="official-business-material-batch-v1",
            business_catalyst_inputs_by_symbol=batch.inputs_by_symbol,
        )
        provider = build_leader_research_input_provider_batch_from_plan(
            provider_input
        )

        self.assertEqual(
            provider.status,
            LeaderResearchInputProviderBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(provider.business_catalyst_inputs_by_symbol),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertFalse(provider.formal_usable)
        self.assertFalse(provider.state_transition_allowed)


if __name__ == "__main__":
    unittest.main()
