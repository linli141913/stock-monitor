import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from radar.contracts import (
    ETF_DAILY_FACT_FIELDS,
    EtfAssetClass,
    EtfManagementStyle,
    EtfMetricState,
    EtfProductMasterRecord,
    EvidenceVersionKind,
)
from radar.etf_repository import (
    EtfRepository,
    RepositoryConflictError,
    RepositoryStateError,
)
from radar.migrations import (
    RADAR_MIGRATIONS,
    STAGE5_RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)
from radar.sources.etf_index_evidence import (
    build_constituent_set_evidence,
    build_methodology_evidence,
    verify_etf_index_identity,
)
from radar.contracts import EtfDailyFact, IndexIndustryExposureResult


UTC = timezone.utc
APPLIED_AT = datetime(2026, 7, 25, 13, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 24, 7, 0, 2, tzinfo=UTC)
EVIDENCE_HASH = "a" * 64


class EtfRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(
            self.connection,
            migrations=STAGE5_RADAR_MIGRATIONS,
            clock=lambda: APPLIED_AT,
        )
        self.repository = EtfRepository(
            self.connection,
            clock=lambda: APPLIED_AT,
        )

    def tearDown(self):
        self.connection.close()

    def insert_run(self, radar_run_id="run-1", as_of=AS_OF):
        as_of_text = as_of.isoformat()
        self.connection.execute(
            """
            INSERT INTO radar_runs (
                radar_run_id, as_of, status, shadow_mode,
                started_at, created_at
            ) VALUES (?, ?, 'succeeded', 1, ?, ?)
            """,
            (radar_run_id, as_of_text, as_of_text, as_of_text),
        )
        self.connection.commit()

    @staticmethod
    def product(symbol="159915"):
        return EtfProductMasterRecord(
            symbol=symbol,
            officialName="创业板ETF",
            exchange="szse",
            productType="etf",
            managementStyle=EtfManagementStyle.PASSIVE_INDEX,
            assetClass=EtfAssetClass.DOMESTIC_EQUITY,
            sourceCategoryCode="stock",
            sourceCategoryName="股票型",
            sourceInvestmentType="被动指数型",
            targetIndexName="创业板指数",
            listingDate=date(2011, 12, 9),
            manager="测试基金管理人",
            classificationMappingVersion="test-v1",
            classificationReasons=(),
            source="official_fixture",
            fetchedAt=FETCHED_AT,
            sourceFields={"fixture": True},
        )

    @staticmethod
    def identity():
        return verify_etf_index_identity(
            symbol="159915",
            management_style=EtfManagementStyle.PASSIVE_INDEX,
            fund_index_code="399006",
            fund_index_name="创业板指数",
            provider="szse",
            provider_index_code="399006",
            provider_index_name="创业板指数",
            published_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_from=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_to=None,
            as_of=AS_OF,
            fund_evidence_url="https://example.test/fund",
            fund_evidence_sha256=EVIDENCE_HASH,
            provider_evidence_url="https://example.test/index",
            provider_evidence_sha256="b" * 64,
            first_observed_at=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
            fetched_at=FETCHED_AT,
        )

    @staticmethod
    def methodology():
        return build_methodology_evidence(
            provider="szse",
            index_code="399006",
            index_name="创业板指数",
            provider_version="2026-07",
            published_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_from=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_to=None,
            universe_rule="创业板上市证券",
            selection_rule="按规则选取成分股",
            weighting_method="自由流通市值加权",
            constituent_cap=100,
            rebalance_frequency="季度",
            as_of=AS_OF,
            evidence_url="https://example.test/methodology",
            evidence_sha256="c" * 64,
            first_observed_at=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
            fetched_at=FETCHED_AT,
            version_kind=EvidenceVersionKind.OFFICIAL,
        )

    @staticmethod
    def constituents():
        return build_constituent_set_evidence(
            provider="szse",
            index_code="399006",
            index_name="创业板指数",
            announced_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_from=datetime(2026, 7, 20, 1, tzinfo=UTC),
            effective_to=None,
            expected_count=1,
            as_of=AS_OF,
            evidence_url="https://example.test/constituents",
            evidence_sha256="d" * 64,
            first_observed_at=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
            fetched_at=FETCHED_AT,
            source_date=date(2026, 7, 23),
            items=[
                {
                    "stockCode": "300750",
                    "stockName": "宁德时代",
                    "weight": 100.0,
                    "weightUnit": "percent",
                },
            ],
        )

    @staticmethod
    def daily_fact():
        field_states = {
            field: EtfMetricState.MISSING
            for field in ETF_DAILY_FACT_FIELDS
        }
        field_states["fundShares"] = EtfMetricState.VERIFIED
        return EtfDailyFact(
            symbol="159915",
            tradeDate=date(2026, 7, 23),
            sourceReportDate=date(2026, 7, 23),
            fundSize=None,
            fundSizeUnit=None,
            fundShares=123456.0,
            fundSharesUnit="万份",
            nav=None,
            navCurrency=None,
            shareChange5d=None,
            shareChange20d=None,
            averageTurnover20d=None,
            trackingDifference=None,
            trackingError=None,
            indexCorrelation=None,
            windowTradingDays=None,
            sampleCount=None,
            formulaVersion="test-v1",
            fieldStates=field_states,
            sourceContractIds={"fundShares": "official_fixture"},
            fetchedAt=FETCHED_AT,
            computedAt=FETCHED_AT,
            formalUsable=False,
            reasons=("turnover_unverified",),
        )

    def seed_profile(self):
        return self.repository.upsert_product_profile(
            self.product(),
            profile_id="profile-159915-v1",
            source_contract_id="official_fixture",
            first_observed_at=datetime(2026, 7, 24, 6, tzinfo=UTC),
            official_effective_from=datetime(2026, 7, 20, 1, tzinfo=UTC),
            source_time=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
            evidence_url="https://example.test/product",
            evidence_sha256=EVIDENCE_HASH,
        )

    def seed_industry_release(self):
        self.connection.execute(
            """
            INSERT INTO industry_classification_releases (
                industry_release_id, classification_system, scheme_version,
                release_period, source_page_title, publication_page_url,
                document_url, document_sha256, published_date,
                first_observed_at, fetched_at, knowledge_effective_from,
                classification_start_date, history_status,
                source_record_count, unique_source_symbol_count,
                required_field_coverage_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "release-csrc-2026h1",
                "csrc",
                "2021",
                "2026H1",
                "测试行业分类",
                "https://example.test/release",
                "https://example.test/release.csv",
                "e" * 64,
                "2026-07-01",
                "2026-07-02T01:00:00+00:00",
                "2026-07-24T06:00:02+00:00",
                "2026-07-02T01:00:00+00:00",
                "2026-07-01",
                "forward_observed",
                1,
                1,
                '{"sourceSymbol":1.0}',
                "2026-07-24T06:00:02+00:00",
            ),
        )
        self.connection.commit()

    def test_optional_version_four_is_explicit_and_default_runtime_stays_at_three(self):
        self.assertEqual(
            validate_applied_migrations(
                self.connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
            ),
            [1, 2, 3, 4],
        )
        self.assertEqual(
            validate_applied_migrations(self.connection),
            [1, 2, 3, 4],
        )
        self.assertEqual(
            apply_pending_migrations(
                self.connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
                clock=lambda: APPLIED_AT,
            ),
            [],
        )

        default_connection = sqlite3.connect(":memory:")
        try:
            self.assertEqual(
                apply_pending_migrations(
                    default_connection,
                    migrations=RADAR_MIGRATIONS,
                    clock=lambda: APPLIED_AT,
                ),
                [1, 2, 3],
            )
            with self.assertRaises(RepositoryStateError):
                EtfRepository(default_connection)
        finally:
            default_connection.close()

    def test_versioned_evidence_and_daily_fact_are_idempotent_and_as_of_safe(self):
        self.assertTrue(self.seed_profile())
        self.assertFalse(self.seed_profile())
        with self.assertRaises(RepositoryConflictError):
            self.repository.upsert_product_profile(
                self.product().model_copy(update={"official_name": "冲突名称"}),
                profile_id="profile-159915-v1",
                source_contract_id="official_fixture",
                first_observed_at=datetime(2026, 7, 24, 6, tzinfo=UTC),
                official_effective_from=datetime(
                    2026, 7, 20, 1, tzinfo=UTC
                ),
            )
        with self.assertRaises(RepositoryConflictError):
            self.repository.upsert_product_profile(
                self.product(),
                profile_id="profile-159915-v2",
                source_contract_id="official_fixture",
                first_observed_at=datetime(2026, 7, 24, 6, tzinfo=UTC),
                official_effective_from=datetime(
                    2026, 7, 21, 1, tzinfo=UTC
                ),
            )

        identity = self.identity()
        methodology = self.methodology()
        constituents = self.constituents()
        self.assertTrue(
            self.repository.upsert_index_relation(
                identity,
                relation_id="relation-159915-399006",
                profile_id="profile-159915-v1",
                source_contract_id="official_fixture",
            )
        )
        self.assertFalse(
            self.repository.upsert_index_relation(
                identity,
                relation_id="relation-159915-399006",
                profile_id="profile-159915-v1",
                source_contract_id="official_fixture",
            )
        )
        self.assertTrue(
            self.repository.upsert_methodology(
                methodology,
                methodology_version_id="method-399006-v1",
            )
        )
        self.assertTrue(
            self.repository.insert_constituent_set(
                constituents,
                constituent_set_id="constituents-399006-20260723",
            )
        )
        self.assertFalse(
            self.repository.insert_constituent_set(
                constituents,
                constituent_set_id="constituents-399006-20260723",
            )
        )

        fact = self.daily_fact()
        self.assertTrue(self.repository.upsert_daily_fact(fact))
        self.assertFalse(self.repository.upsert_daily_fact(fact))
        self.assertIsNone(
            self.repository.get_daily_fact_as_of(
                "159915",
                datetime(2026, 7, 22, tzinfo=UTC),
            )
        )
        loaded = self.repository.get_daily_fact_as_of("159915", AS_OF)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.fund_shares, 123456.0)
        self.assertEqual(loaded.source_report_date, date(2026, 7, 23))

    def test_product_profile_transition_is_atomic_idempotent_and_as_of_safe(self):
        self.assertTrue(self.seed_profile())
        unchanged_refresh = self.product().model_copy(update={
            "fetched_at": datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
        })

        self.assertFalse(
            self.repository.transition_product_profile(
                unchanged_refresh,
                profile_id="profile-159915-refresh",
                source_contract_id="official_fixture",
                first_observed_at=datetime(2026, 7, 25, 6, tzinfo=UTC),
                official_effective_from=datetime(
                    2026, 7, 20, 1, tzinfo=UTC
                ),
                evidence_sha256=EVIDENCE_HASH,
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_profiles"
            ).fetchone()[0],
            1,
        )

        changed = self.product().model_copy(update={
            "official_name": "创业板ETF新简称",
            "fetched_at": datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
        })
        transition_at = datetime(2026, 7, 21, 1, tzinfo=UTC)
        self.assertTrue(
            self.repository.transition_product_profile(
                changed,
                profile_id="profile-159915-v2",
                source_contract_id="official_fixture",
                first_observed_at=datetime(2026, 7, 25, 6, tzinfo=UTC),
                official_effective_from=transition_at,
                source_time=transition_at,
                evidence_url="https://example.test/product-v2",
                evidence_sha256="f" * 64,
            )
        )

        before = self.repository.get_product_profile_as_of(
            "159915",
            datetime(2026, 7, 20, 12, tzinfo=UTC),
        )
        after = self.repository.get_product_profile_as_of(
            "159915",
            datetime(2026, 7, 21, 2, tzinfo=UTC),
        )
        self.assertEqual(before["profileId"], "profile-159915-v1")
        self.assertEqual(before["product"].official_name, "创业板ETF")
        self.assertEqual(before["effectiveTo"], transition_at)
        self.assertEqual(after["profileId"], "profile-159915-v2")
        self.assertEqual(
            after["product"].official_name,
            "创业板ETF新简称",
        )
        self.assertEqual(
            after["product"].classification_mapping_version,
            "test-v1",
        )
        self.assertIsNone(after["effectiveTo"])

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE radar_etf_product_profiles SET effective_to=? "
                "WHERE profile_id='profile-159915-v1'",
                ("2026-07-22T01:00:00+00:00",),
            )
        self.connection.rollback()

        conflicting = changed.model_copy(update={
            "official_name": "回填冲突",
        })
        with self.assertRaises(RepositoryConflictError):
            self.repository.transition_product_profile(
                conflicting,
                profile_id="profile-159915-backfill",
                source_contract_id="official_fixture",
                first_observed_at=datetime(2026, 7, 25, 6, tzinfo=UTC),
                official_effective_from=datetime(
                    2026, 7, 20, 23, tzinfo=UTC
                ),
            )
        current = self.repository.get_product_profile_as_of(
            "159915",
            datetime(2026, 7, 21, 2, tzinfo=UTC),
        )
        self.assertEqual(current["profileId"], "profile-159915-v2")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_product_profiles"
            ).fetchone()[0],
            2,
        )

    def test_exposure_snapshot_and_candidate_snapshot_roll_back_on_constraint_failure(self):
        self.seed_profile()
        self.repository.insert_constituent_set(
            self.constituents(),
            constituent_set_id="constituents-399006-20260723",
        )
        self.seed_industry_release()
        exposure = IndexIndustryExposureResult(
            constituentSetId="constituents-399006-20260723",
            indexProvider="szse",
            indexCode="399006",
            indexName="创业板指数",
            constituentSourceDate=date(2026, 7, 23),
            industryReleaseId="release-csrc-2026h1",
            industryReleasePeriod="2026H1",
            industryDocumentSha256="e" * 64,
            totalWeight=100.0,
            mappedWeight=100.0,
            unmappedWeight=0.0,
            mappingCoverage=1.0,
            unmappedSymbols=(),
            exposures=[
                {
                    "industryCode": "37",
                    "industryName": "计算机",
                    "rawWeight": 100.0,
                    "exposureRatio": 1.0,
                },
            ],
            asOf=AS_OF,
            computedAt=FETCHED_AT,
            calculationVersion="test-v1",
            formalReady=True,
            reasons=(),
        )
        self.assertTrue(
            self.repository.insert_industry_exposure(
                exposure,
                exposure_version_id="exposure-399006-20260723",
            )
        )
        self.assertFalse(
            self.repository.insert_industry_exposure(
                exposure,
                exposure_version_id="exposure-399006-20260723",
            )
        )

        self.insert_run()
        feature = {
            "symbol": "159915",
            "asOf": AS_OF,
            "sourceTime": datetime(2026, 7, 24, 6, 59, tzinfo=UTC),
            "fetchedAt": FETCHED_AT,
            "price": 2.1,
            "changePercent": 1.2,
            "turnoverVolume": None,
            "turnoverAmount": None,
            "bid1": 2.09,
            "ask1": 2.10,
            "spreadBps": 4.7,
            "iopv": None,
            "premiumDiscountRate": None,
            "fieldStates": {"price": "verified"},
            "formalUsable": True,
            "reasonCodes": [],
        }
        self.assertEqual(
            self.repository.insert_feature_snapshot_batch("run-1", [feature]),
            1,
        )
        self.assertEqual(
            self.repository.insert_feature_snapshot_batch("run-1", [feature]),
            0,
        )
        loaded_feature = self.repository.get_feature_snapshot("run-1", "159915")
        self.assertEqual(loaded_feature["price"], 2.1)
        with self.assertRaises(RepositoryConflictError):
            self.repository.insert_feature_snapshot_batch(
                "run-1",
                [{**feature, "price": 2.2}],
            )

        snapshot = {
            "radarRunId": "run-1",
            "asOf": AS_OF,
            "ruleVersionId": None,
            "registryCount": 1,
            "etfCount": 1,
            "eligibleProductCount": 1,
            "industryThemeCount": 1,
            "computedCount": 1,
            "staleCount": 0,
            "missingCount": 0,
            "excludedCount": 0,
            "candidateGroupCount": 1,
            "coverage": 1.0,
            "quality": "complete",
            "reasonCounts": {},
        }
        entry = {
            "industryCode": "37",
            "indexGroupKey": "szse:399006",
            "rank": 1,
            "representativeSymbol": "159915",
            "alternativeSymbols": [],
            "industryExposures": [{"industryCode": "37", "ratio": 1.0}],
            "rankingComponents": {"shareChange20d": 1.0},
            "entryReasons": [],
            "riskReasons": [],
            "exitConditions": ["source_stale"],
            "formalUsable": True,
        }
        self.assertTrue(self.repository.save_candidate_snapshot(snapshot, [entry]))
        self.assertFalse(self.repository.save_candidate_snapshot(snapshot, [entry]))
        loaded_candidate = self.repository.get_candidate_snapshot("run-1")
        self.assertEqual(loaded_candidate["entries"][0]["representativeSymbol"], "159915")
        with self.assertRaises(RepositoryConflictError):
            self.repository.save_candidate_snapshot(
                {**snapshot, "candidateGroupCount": 1},
                [{**entry, "formalUsable": True, "representativeSymbol": None}],
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_etf_candidate_snapshots "
                "WHERE radar_run_id='run-1'"
            ).fetchone()[0],
            1,
        )

    def test_file_backed_database_survives_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "stage5f.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                apply_pending_migrations(
                    connection,
                    migrations=STAGE5_RADAR_MIGRATIONS,
                    clock=lambda: APPLIED_AT,
                )
                repository = EtfRepository(
                    connection,
                    clock=lambda: APPLIED_AT,
                )
                repository.upsert_product_profile(
                    self.product(),
                    profile_id="profile-file",
                    source_contract_id="official_fixture",
                    first_observed_at=datetime(
                        2026, 7, 24, 6, tzinfo=UTC
                    ),
                    official_effective_from=datetime(
                        2026, 7, 20, 1, tzinfo=UTC
                    ),
                )
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.close()

                reopened = sqlite3.connect(database_path)
                try:
                    self.assertEqual(
                        validate_applied_migrations(
                            reopened,
                            migrations=STAGE5_RADAR_MIGRATIONS,
                        ),
                        [1, 2, 3, 4],
                    )
                    self.assertEqual(
                        reopened.execute(
                            "PRAGMA integrity_check"
                        ).fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        reopened.execute(
                            "SELECT symbol FROM radar_etf_product_profiles"
                        ).fetchone()[0],
                        "159915",
                    )
                finally:
                    reopened.close()
            finally:
                if connection:
                    try:
                        connection.close()
                    except sqlite3.ProgrammingError:
                        pass


if __name__ == "__main__":
    unittest.main()
