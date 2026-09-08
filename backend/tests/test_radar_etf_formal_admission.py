import copy
import math
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from radar.contracts import (
    EtfAssetClass,
    EtfIndexIdentityEvidence,
    EtfManagementStyle,
    EtfMetricState,
    EtfProductMasterRecord,
    EtfRankingInputAudit,
    EvidenceTemporalBasis,
    EvidenceVersionKind,
    IndexConstituentEvidenceItem,
    IndexConstituentSetEvidence,
    IndexEvidenceStatus,
    IndexIndustryExposureItem,
    IndexIndustryExposureResult,
    IndexMethodologyEvidence,
)
from radar.etf_formal_admission import (
    EtfFormalAdmissionStatus,
    EtfIndustryScopeEvidence,
    EtfIndustryScopeKind,
    EtfLifecycleEvidence,
    EtfLifecycleStatus,
    build_etf_industry_scope_evidence,
    build_etf_lifecycle_evidence_from_current_master,
    provide_etf_formal_admission_evidence,
)
from radar.etf_stage5_policy import (
    DEFAULT_ETF_RULE_POLICY,
    REQUIRED_RANKING_FIELDS,
    EtfRulePolicy,
)
import radar.etf_formal_admission as etf_formal_admission
from radar.sources.etf_index_evidence import (
    build_official_csindex_identity_resolution,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 15, 2, 30, tzinfo=UTC)
INDEX_CODE = "399006"
INDEX_NAME = "创业板指数"
INDEX_PROVIDER = "szse"


def product(
    *,
    management_style=EtfManagementStyle.PASSIVE_INDEX,
    asset_class=EtfAssetClass.DOMESTIC_EQUITY,
    classification_reasons=(),
):
    return EtfProductMasterRecord(
        symbol="159915",
        officialName="创业板ETF",
        exchange="szse",
        productType="etf",
        managementStyle=management_style,
        assetClass=asset_class,
        targetIndexName=INDEX_NAME,
        listingDate=date(2011, 12, 9),
        classificationMappingVersion=(
            "radar-etf-product-classification-v1"
        ),
        classificationReasons=classification_reasons,
        source="szse_official_fund_list",
        fetchedAt=AS_OF - timedelta(days=1),
    )


def lifecycle():
    return EtfLifecycleEvidence(
        symbol="159915",
        as_of=AS_OF,
        status=EtfLifecycleStatus.ACTIVE,
        listing_date=date(2011, 12, 9),
        termination_effective_at=None,
        source_url="https://www.szse.cn/market/product/fund/list/",
        source_sha256="1" * 64,
        fetched_at=AS_OF - timedelta(hours=1),
    )


def scope():
    return EtfIndustryScopeEvidence(
        symbol="159915",
        as_of=AS_OF,
        index_provider=INDEX_PROVIDER,
        index_code=INDEX_CODE,
        scope_kind=EtfIndustryScopeKind.INDUSTRY,
        classification_version="radar-etf-index-scope-v1",
        source_url="https://www.cnindex.com.cn/399006/methodology.pdf",
        source_sha256="2" * 64,
        fetched_at=AS_OF - timedelta(hours=1),
    )


def relation():
    return EtfIndexIdentityEvidence(
        symbol="159915",
        managementStyle=EtfManagementStyle.PASSIVE_INDEX,
        fundIndexCode=INDEX_CODE,
        fundIndexName=INDEX_NAME,
        indexProvider=INDEX_PROVIDER,
        providerIndexCode=INDEX_CODE,
        providerIndexName=INDEX_NAME,
        identityMatched=True,
        publishedAt=AS_OF - timedelta(days=100),
        effectiveFrom=AS_OF - timedelta(days=90),
        asOf=AS_OF,
        fundEvidenceUrl="https://fund.example/159915.pdf",
        fundEvidenceSha256="3" * 64,
        providerEvidenceUrl="https://index.example/399006.html",
        providerEvidenceSha256="4" * 64,
        firstObservedAt=AS_OF - timedelta(days=30),
        fetchedAt=AS_OF - timedelta(hours=1),
        status=IndexEvidenceStatus.VERIFIED,
        formalReady=True,
        reasons=(),
    )


def methodology():
    return IndexMethodologyEvidence(
        indexProvider=INDEX_PROVIDER,
        indexCode=INDEX_CODE,
        indexName=INDEX_NAME,
        providerVersion="2026-01",
        versionKind=EvidenceVersionKind.OFFICIAL,
        publishedAt=AS_OF - timedelta(days=100),
        effectiveFrom=AS_OF - timedelta(days=90),
        universeRule="深交所创业板上市股票",
        selectionRule="按官方方法选择样本",
        weightingMethod="自由流通市值加权",
        constituentCap=100,
        rebalanceFrequency="季度",
        asOf=AS_OF,
        evidenceUrl="https://index.example/399006-methodology.pdf",
        evidenceSha256="5" * 64,
        firstObservedAt=AS_OF - timedelta(days=30),
        fetchedAt=AS_OF - timedelta(hours=1),
        status=IndexEvidenceStatus.VERIFIED,
        formalReady=True,
        reasons=(),
    )


def constituents():
    return IndexConstituentSetEvidence(
        indexProvider=INDEX_PROVIDER,
        indexCode=INDEX_CODE,
        indexName=INDEX_NAME,
        announcedAt=AS_OF - timedelta(days=2),
        effectiveFrom=AS_OF - timedelta(days=1),
        sourceDate=(AS_OF - timedelta(days=1)).date(),
        expectedCount=2,
        returnedCount=2,
        weightCount=2,
        weightTotal=100.0,
        asOf=AS_OF,
        evidenceUrl="https://index.example/399006-constituents.csv",
        evidenceSha256="6" * 64,
        firstObservedAt=AS_OF - timedelta(days=1),
        fetchedAt=AS_OF - timedelta(hours=1),
        items=[
            IndexConstituentEvidenceItem(
                stockCode="000001",
                stockName="证券一",
                weight=60.0,
            ),
            IndexConstituentEvidenceItem(
                stockCode="000002",
                stockName="证券二",
                weight=40.0,
            ),
        ],
        status=IndexEvidenceStatus.VERIFIED,
        formalReady=True,
        reasons=(),
    )


def exposure():
    return IndexIndustryExposureResult(
        constituentSetId="szse:399006:2026-08-14:fixture",
        indexProvider=INDEX_PROVIDER,
        indexCode=INDEX_CODE,
        indexName=INDEX_NAME,
        constituentSourceDate=(AS_OF - timedelta(days=1)).date(),
        industryReleaseId="2025H2:fixture",
        industryReleasePeriod="2025H2",
        industryDocumentSha256="7" * 64,
        totalWeight=100.0,
        mappedWeight=100.0,
        unmappedWeight=0.0,
        mappingCoverage=1.0,
        unmappedSymbols=(),
        exposures=[
            IndexIndustryExposureItem(
                industryCode="35",
                industryName="专用设备制造业",
                rawWeight=60.0,
                exposureRatio=0.6,
            ),
            IndexIndustryExposureItem(
                industryCode="39",
                industryName="计算机、通信和其他电子设备制造业",
                rawWeight=40.0,
                exposureRatio=0.4,
            ),
        ],
        asOf=AS_OF,
        computedAt=AS_OF,
        calculationVersion="radar-etf-industry-exposure-v1",
        formalReady=True,
        reasons=(),
    )


def ranking_input():
    values = {
        "fundSize": 10_000_000_000.0,
        "averageTurnover20d": 800_000_000.0,
        "trackingDifference": -0.001,
        "trackingError": 0.002,
        "indexCorrelation": 0.995,
    }
    return EtfRankingInputAudit(
        symbol="159915",
        asOf=AS_OF,
        fetchedAt=AS_OF,
        metricValues=values,
        fieldStates={
            field_name: EtfMetricState.VERIFIED
            for field_name in REQUIRED_RANKING_FIELDS
        },
        rankableFields=REQUIRED_RANKING_FIELDS,
        excludedFields=(),
        formalReady=True,
        reasons=(),
    )


def enabled_policy():
    return EtfRulePolicy(
        ranking_enabled=True,
        weights=tuple(
            (field_name, 1 / len(REQUIRED_RANKING_FIELDS))
            for field_name in REQUIRED_RANKING_FIELDS
        ),
        thresholds=(("minimumScore", 0.5),),
        disabled_reasons=(),
    )


def provide(**overrides):
    values = {
        "product": product(),
        "as_of": AS_OF,
        "lifecycle_evidence": lifecycle(),
        "industry_scope_evidence": scope(),
        "index_relation_evidence": relation(),
        "methodology_evidence": methodology(),
        "constituent_evidence": constituents(),
        "industry_exposure_evidence": exposure(),
        "ranking_input_evidence": ranking_input(),
        "policy": enabled_policy(),
    }
    values.update(overrides)
    return provide_etf_formal_admission_evidence(**values)


class EtfFormalAdmissionTests(unittest.TestCase):
    def test_formal_admission_bundle_roundtrip_binds_sample_run_time_and_hash(self):
        builder = getattr(
            etf_formal_admission,
            "build_etf_formal_admission_bundle",
            None,
        )
        loader = getattr(
            etf_formal_admission,
            "load_etf_formal_admission_bundle",
            None,
        )
        self.assertTrue(callable(builder))
        self.assertTrue(callable(loader))
        if not callable(builder) or not callable(loader):
            return

        admission = provide(policy=DEFAULT_ETF_RULE_POLICY)
        bundle = builder(
            sample_id="sample-1",
            radar_run_id="run-1",
            as_of=AS_OF,
            admissions=(admission,),
        )
        restored = loader(bundle.to_evidence())

        self.assertEqual(restored.sample_id, "sample-1")
        self.assertEqual(restored.radar_run_id, "run-1")
        self.assertEqual(restored.as_of, AS_OF)
        self.assertEqual(restored.admissions, (admission,))
        self.assertEqual(len(restored.snapshot_sha256), 64)
        self.assertEqual(
            bundle.to_evidence()["admissions"][0]["monitoringStatus"],
            "ready",
        )
        self.assertEqual(
            bundle.to_evidence()["admissions"][0]["rankingStatus"],
            "missing",
        )

    def test_formal_admission_bundle_rejects_tampered_or_mismatched_evidence(self):
        builder = getattr(
            etf_formal_admission,
            "build_etf_formal_admission_bundle",
            None,
        )
        loader = getattr(
            etf_formal_admission,
            "load_etf_formal_admission_bundle",
            None,
        )
        self.assertTrue(callable(builder))
        self.assertTrue(callable(loader))
        if not callable(builder) or not callable(loader):
            return

        bundle = builder(
            sample_id="sample-1",
            radar_run_id="run-1",
            as_of=AS_OF,
            admissions=(provide(policy=DEFAULT_ETF_RULE_POLICY),),
        )
        tampered = copy.deepcopy(bundle.to_evidence())
        tampered["admissions"][0]["monitoringStatus"] = "missing"

        with self.assertRaisesRegex(
            ValueError,
            "etf_formal_admission_bundle_unverified",
        ):
            loader(tampered)

        with self.assertRaisesRegex(
            ValueError,
            "etf_formal_admission_bundle_identity_mismatch",
        ):
            builder(
                sample_id="sample-1",
                radar_run_id="run-1",
                as_of=AS_OF + timedelta(minutes=1),
                admissions=(provide(),),
            )

    def test_forward_current_official_index_evidence_enters_current_admission(self):
        current_methodology = methodology().model_copy(update={
            "temporal_basis": (
                EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION
            ),
            "effective_from": None,
            "first_observed_at": AS_OF - timedelta(hours=2),
            "fetched_at": AS_OF - timedelta(hours=1),
        })
        current_constituents = constituents().model_copy(update={
            "temporal_basis": (
                EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION
            ),
            "announced_at": None,
            "effective_from": None,
            "first_observed_at": AS_OF - timedelta(hours=2),
            "fetched_at": AS_OF - timedelta(hours=1),
        })

        result = provide(
            methodology_evidence=current_methodology,
            constituent_evidence=current_constituents,
        )

        self.assertEqual(
            result.item("index_methodology").status,
            EtfFormalAdmissionStatus.READY,
        )
        self.assertEqual(
            result.item("index_constituents").status,
            EtfFormalAdmissionStatus.READY,
        )

    def test_current_official_master_and_index_classification_build_evidence(self):
        official_product = product().model_copy(update={
            "source_fields": {
                "基金代码": "159915",
                "上市日期": "2011-12-09",
            },
            "fetched_at": AS_OF - timedelta(hours=1),
        })
        lifecycle_evidence = (
            build_etf_lifecycle_evidence_from_current_master(
                product=official_product,
                as_of=AS_OF,
            )
        )
        resolution = build_official_csindex_identity_resolution(
            requested_index_name="中证光伏产业指数",
            search_payload={
                "code": "200",
                "success": True,
                "data": [{
                    "indexCode": "931151",
                    "indexName": "光伏产业",
                    "indexClassify": "主题",
                    "publishDate": "2019-04-22",
                }],
            },
            basic_payloads={
                "931151": {
                    "code": "200",
                    "data": {
                        "indexCode": "931151",
                        "indexFullNameCn": "中证光伏产业指数",
                    },
                },
            },
            fetched_at=AS_OF - timedelta(hours=1),
        )
        scope_evidence = build_etf_industry_scope_evidence(
            symbol="515790",
            index_resolution=resolution,
            as_of=AS_OF,
        )

        self.assertEqual(lifecycle_evidence.status, EtfLifecycleStatus.ACTIVE)
        self.assertEqual(lifecycle_evidence.listing_date, date(2011, 12, 9))
        self.assertEqual(len(lifecycle_evidence.source_sha256), 64)
        self.assertEqual(scope_evidence.scope_kind, EtfIndustryScopeKind.THEME)
        self.assertEqual(scope_evidence.index_code, "931151")
        self.assertEqual(len(scope_evidence.source_sha256), 64)

    def test_unknown_index_classification_fails_closed(self):
        resolution = build_official_csindex_identity_resolution(
            requested_index_name="未知指数",
            search_payload={
                "code": "200",
                "success": True,
                "data": [{
                    "indexCode": "931999",
                    "indexName": "未知",
                    "indexClassify": "其他",
                }],
            },
            basic_payloads={
                "931999": {
                    "code": "200",
                    "data": {
                        "indexCode": "931999",
                        "indexFullNameCn": "未知指数",
                    },
                },
            },
            fetched_at=AS_OF,
        )

        with self.assertRaisesRegex(
            ValueError,
            "etf_industry_scope_classification_unverified",
        ):
            build_etf_industry_scope_evidence(
                symbol="515790",
                index_resolution=resolution,
                as_of=AS_OF,
            )

    def test_same_identity_complete_chain_returns_only_ready_evidence(self):
        result = provide()

        self.assertEqual(result.status, EtfFormalAdmissionStatus.READY)
        self.assertEqual(result.reasons, ())
        self.assertTrue(all(
            item.status == EtfFormalAdmissionStatus.READY
            for item in result.items
        ))
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["contractId"],
            "radar-etf-formal-admission-evidence-v1",
        )
        self.assertEqual(evidence["status"], "ready")
        self.assertNotIn("score", evidence)
        self.assertNotIn("rank", evidence)
        self.assertNotIn("state", evidence)
        self.assertNotIn("formalUsable", evidence)

    def test_default_policy_keeps_complete_source_chain_missing(self):
        result = provide(policy=DEFAULT_ETF_RULE_POLICY)

        self.assertEqual(result.status, EtfFormalAdmissionStatus.MISSING)
        self.assertEqual(
            result.monitoring_status,
            EtfFormalAdmissionStatus.READY,
        )
        self.assertEqual(
            result.ranking_status,
            EtfFormalAdmissionStatus.MISSING,
        )
        self.assertEqual(
            result.item("rule_policy").reasons,
            (
                "etf_rule_not_frozen",
                "ranking_calibration_sample_missing",
            ),
        )
        evidence = result.to_evidence()
        self.assertEqual(evidence["monitoringStatus"], "ready")
        self.assertEqual(evidence["rankingStatus"], "missing")

    def test_current_source_gaps_are_reported_without_filling_values(self):
        result = provide_etf_formal_admission_evidence(
            product=product(
                management_style=EtfManagementStyle.UNKNOWN,
                asset_class=EtfAssetClass.UNKNOWN,
                classification_reasons=(
                    "management_style_source_unverified",
                    "asset_class_source_unverified",
                ),
            ),
            as_of=AS_OF,
        )

        self.assertEqual(result.status, EtfFormalAdmissionStatus.MISSING)
        self.assertEqual(
            result.reasons,
            (
                "etf_management_style_not_verified_passive",
                "etf_asset_class_not_domestic_equity",
                "etf_product_classification_unverified",
                "etf_product_lifecycle_evidence_missing",
                "etf_industry_scope_evidence_missing",
                "etf_index_relation_evidence_missing",
                "etf_index_methodology_evidence_missing",
                "etf_index_constituent_evidence_missing",
                "etf_industry_exposure_evidence_missing",
                "etf_ranking_input_evidence_missing",
                "etf_rule_not_frozen",
                "formal_source_inputs_incomplete",
                "ranking_calibration_sample_missing",
            ),
        )

    def test_cross_product_and_cross_index_evidence_is_rejected(self):
        other_lifecycle = replace(lifecycle(), symbol="510300")
        other_scope = replace(scope(), index_code="000300")
        other_relation = relation().model_copy(
            update={"symbol": "510300"},
        )
        other_methodology = methodology().model_copy(
            update={"index_code": "000300"},
        )
        other_constituents = constituents().model_copy(
            update={"index_provider": "csi"},
        )
        other_exposure = exposure().model_copy(
            update={"index_code": "000300"},
        )
        other_ranking = ranking_input().model_copy(
            update={"symbol": "510300"},
        )

        result = provide(
            lifecycle_evidence=other_lifecycle,
            industry_scope_evidence=other_scope,
            index_relation_evidence=other_relation,
            methodology_evidence=other_methodology,
            constituent_evidence=other_constituents,
            industry_exposure_evidence=other_exposure,
            ranking_input_evidence=other_ranking,
        )

        self.assertEqual(result.status, EtfFormalAdmissionStatus.MISSING)
        for key in (
            "product_lifecycle",
            "industry_scope",
            "index_relation",
            "index_methodology",
            "index_constituents",
            "industry_exposure",
            "ranking_inputs",
        ):
            self.assertIn(
                "etf_evidence_identity_mismatch",
                result.item(key).reasons,
            )

    def test_inactive_or_broad_based_product_never_enters_industry_pool(self):
        result = provide(
            lifecycle_evidence=replace(
                lifecycle(),
                status=EtfLifecycleStatus.TERMINATED,
                termination_effective_at=AS_OF - timedelta(days=1),
            ),
            industry_scope_evidence=replace(
                scope(),
                scope_kind=EtfIndustryScopeKind.BROAD_BASED,
            ),
        )

        self.assertEqual(
            result.item("product_lifecycle").reasons,
            ("etf_product_lifecycle_not_active",),
        )
        self.assertEqual(
            result.item("industry_scope").reasons,
            ("etf_industry_scope_not_industry_or_theme",),
        )

    def test_product_target_index_conflict_blocks_relation(self):
        conflicting_relation = relation().model_copy(
            update={
                "fund_index_name": "沪深300指数",
                "provider_index_name": "沪深300指数",
            },
        )

        result = provide(index_relation_evidence=conflicting_relation)

        self.assertIn(
            "etf_product_index_identity_mismatch",
            result.item("index_relation").reasons,
        )

    def test_constituent_item_weights_must_equal_reported_total(self):
        inconsistent = constituents().model_copy(
            update={
                "items": [
                    IndexConstituentEvidenceItem(
                        stockCode="000001",
                        stockName="证券一",
                        weight=55.0,
                    ),
                    IndexConstituentEvidenceItem(
                        stockCode="000002",
                        stockName="证券二",
                        weight=35.0,
                    ),
                ],
            },
        )

        result = provide(constituent_evidence=inconsistent)

        self.assertEqual(
            result.item("index_constituents").reasons,
            ("etf_index_constituent_set_not_verified",),
        )

    def test_illegal_new_evidence_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "时区"):
            EtfLifecycleEvidence(
                symbol="159915",
                as_of=datetime(2026, 8, 15, 10, 30),
                status=EtfLifecycleStatus.ACTIVE,
                listing_date=date(2011, 12, 9),
                termination_effective_at=None,
                source_url="https://example.com/list",
                source_sha256="1" * 64,
                fetched_at=AS_OF,
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            EtfIndustryScopeEvidence(
                symbol="159915",
                as_of=AS_OF,
                index_provider=INDEX_PROVIDER,
                index_code=INDEX_CODE,
                scope_kind=EtfIndustryScopeKind.INDUSTRY,
                classification_version="scope-v1",
                source_url="https://example.com/methodology",
                source_sha256="not-a-sha",
                fetched_at=AS_OF,
            )
        broken_ranking = ranking_input().model_copy(
            update={
                "metric_values": {
                    **ranking_input().metric_values,
                    "fundSize": math.nan,
                },
            },
        )
        result = provide(ranking_input_evidence=broken_ranking)
        self.assertEqual(
            result.item("ranking_inputs").reasons,
            ("etf_ranking_input_not_verified",),
        )


if __name__ == "__main__":
    unittest.main()
