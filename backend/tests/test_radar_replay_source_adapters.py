import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    ListedFundProductType,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
)


AS_OF = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 9, 1, 6, 59, tzinfo=timezone.utc)


def batch_meta(*, source: str, expected: Optional[int] = 1, returned: int = 1):
    return RadarBatchMeta(
        radarRunId="stage9-forward-20260901",
        batchId=f"{source}-batch-1",
        source=source,
        asOf=AS_OF,
        sourceTime=FETCHED_AT,
        fetchedAt=FETCHED_AT,
        expectedCount=expected,
        returnedCount=returned,
        rowCoverage=(returned / expected if expected else None),
        requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
        issues=[],
    )


class RadarReplaySourceAdapterTests(unittest.TestCase):
    def test_code_change_contract_requires_distinct_new_symbol(self):
        from pydantic import ValidationError

        from radar.replay_source_adapters import CorporateActionEvidenceItem

        common = {
            "symbol": "300114",
            "exchange": "szse",
            "actionType": "code_change",
            "announcedAt": FETCHED_AT,
            "effectiveOn": date(2025, 2, 17),
            "sourceName": "巨潮资讯（深交所官方公告）",
            "sourceUrl": "https://static.cninfo.com.cn/example.PDF",
            "sourceSha256": "a" * 64,
            "documentId": "cninfo:example",
        }

        item = CorporateActionEvidenceItem(**common, newSymbol="302132")
        self.assertEqual(item.new_symbol, "302132")
        with self.assertRaises(ValidationError):
            CorporateActionEvidenceItem(**common)
        with self.assertRaises(ValidationError):
            CorporateActionEvidenceItem(**common, newSymbol="300114")

        non_change = dict(common)
        non_change["actionType"] = "cash_dividend"
        with self.assertRaises(ValidationError):
            CorporateActionEvidenceItem(**non_change, newSymbol="302132")

    def test_security_universe_full_official_batch_is_ready_and_hashed(self):
        from radar.replay_source_adapters import adapt_security_universe

        batch = SourceBatch[SecurityMasterRecord](
            meta=batch_meta(source="sse-szse-bse-official"),
            items=[SecurityMasterRecord(
                symbol="000001",
                name="平安银行",
                exchange="szse",
                board="main",
                listingDate=date(1991, 4, 3),
                source="深圳证券交易所",
                fetchedAt=FETCHED_AT,
                sourceFields={"officialCode": "000001"},
            )],
        )

        evidence = adapt_security_universe(batch, sample_as_of=AS_OF)

        self.assertEqual(evidence.domain, "security_universe")
        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 1)
        self.assertEqual(len(evidence.payload["snapshotSha256"]), 64)
        self.assertEqual(evidence.payload["observationKind"], "forward_observed")

    def test_security_universe_incomplete_batch_is_unverifiable(self):
        from radar.replay_source_adapters import adapt_security_universe

        batch = SourceBatch[SecurityMasterRecord](
            meta=batch_meta(
                source="sse-szse-bse-official",
                expected=2,
                returned=1,
            ),
            items=[SecurityMasterRecord(
                symbol="000001",
                name="平安银行",
                exchange="szse",
                board="main",
                source="深圳证券交易所",
                fetchedAt=FETCHED_AT,
            )],
        )

        evidence = adapt_security_universe(batch, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertEqual(evidence.payload["rowCoverage"], 0.5)
        self.assertIn("security_universe_incomplete", evidence.payload["reasons"])

    def test_trading_rules_include_bse_official_catalog(self):
        from radar.replay_source_adapters import adapt_trading_rules

        evidence = adapt_trading_rules(
            sample_as_of=AS_OF,
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(evidence.domain, "trading_rule")
        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["coveredBoards"], [
            "bse:main", "sse:main", "sse:star", "szse:chinext",
            "szse:main",
        ])
        self.assertEqual(evidence.payload["missingBoards"], [])
        self.assertEqual(evidence.payload["reasons"], [])
        self.assertEqual(len(evidence.payload["rules"]), 5)
        bse = next(
            item
            for item in evidence.payload["rules"]
            if item["boardIdentity"] == "bse:main"
        )
        self.assertEqual(bse["limit_rate"], 0.30)

    def test_industry_full_forward_snapshot_is_ready(self):
        from radar.replay_source_adapters import adapt_industry

        release = SimpleNamespace(
            first_observed_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
            knowledge_effective_from=FETCHED_AT,
            document_sha256="a" * 64,
        )
        snapshot = SimpleNamespace(
            meta=batch_meta(source="capco-official"),
            status=SourceStatus.HEALTHY,
            release=release,
            records=[SimpleNamespace(symbol="000001")],
            current_master_gaps=[],
            completeness=SimpleNamespace(
                mapping_coverage=1.0,
                formal_usable=True,
                reasons=(),
            ),
            issues=[],
        )

        evidence = adapt_industry(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 1)
        self.assertEqual(evidence.effective_from, FETCHED_AT)

    def test_industry_forward_replay_does_not_require_human_approval(self):
        from radar.replay_source_adapters import adapt_industry

        release = SimpleNamespace(
            first_observed_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
            knowledge_effective_from=FETCHED_AT,
            document_sha256="a" * 64,
        )
        snapshot = SimpleNamespace(
            meta=batch_meta(source="capco-official"),
            status=SourceStatus.HEALTHY,
            release=release,
            records=[SimpleNamespace(symbol="000001")],
            current_master_gaps=[],
            completeness=SimpleNamespace(
                mapping_coverage=1.0,
                formal_usable=False,
                reasons=("formal_use_not_approved",),
            ),
            issues=[],
        )

        evidence = adapt_industry(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["reasons"], [])

    def test_industry_supplement_cannot_be_used_before_its_observation(self):
        from radar.replay_source_adapters import adapt_industry

        release = SimpleNamespace(
            first_observed_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
            knowledge_effective_from=FETCHED_AT,
            document_sha256="a" * 64,
        )
        snapshot = SimpleNamespace(
            meta=batch_meta(source="capco-official"),
            status=SourceStatus.HEALTHY,
            release=release,
            records=[SimpleNamespace(
                symbol="920001",
                knowledge_effective_from=AS_OF + timedelta(seconds=1),
            )],
            current_master_gaps=[],
            completeness=SimpleNamespace(
                mapping_coverage=1.0,
                formal_usable=True,
                reasons=(),
            ),
            issues=[],
        )

        with self.assertRaises(ValueError):
            adapt_industry(snapshot, sample_as_of=AS_OF)

    def test_industry_new_listings_are_explicitly_excluded_not_global_blocker(self):
        from radar.replay_source_adapters import adapt_industry

        release = SimpleNamespace(
            first_observed_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
            knowledge_effective_from=FETCHED_AT,
            document_sha256="a" * 64,
        )
        snapshot = SimpleNamespace(
            meta=batch_meta(source="capco-official"),
            status=SourceStatus.DEGRADED,
            release=release,
            records=[SimpleNamespace(symbol="000001")],
            current_master_gaps=[SimpleNamespace(
                security_identity="920001",
                symbol="920001",
                issue_codes=("listed_on_or_after_classification_start",),
            )],
            completeness=SimpleNamespace(
                mapping_coverage=0.98,
                mapped_count=98,
                unconfirmed_count=1,
                shadow_usable=True,
                formal_usable=False,
                reasons=(
                    "current_master_mapping_incomplete",
                    "formal_use_not_approved",
                ),
            ),
            issues=[],
        )

        evidence = adapt_industry(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertTrue(evidence.payload["scopedReplayReady"])
        self.assertEqual(evidence.payload["excludedCurrentMasterCount"], 1)
        self.assertEqual(evidence.payload["excludedSymbols"], ["920001"])
        self.assertIn(
            "industry_new_listing_explicitly_excluded",
            evidence.payload["reasons"],
        )

    def test_industry_unexplained_mapping_gap_remains_unverifiable(self):
        from radar.replay_source_adapters import adapt_industry

        release = SimpleNamespace(
            first_observed_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
            knowledge_effective_from=FETCHED_AT,
            document_sha256="a" * 64,
        )
        snapshot = SimpleNamespace(
            meta=batch_meta(source="capco-official"),
            status=SourceStatus.DEGRADED,
            release=release,
            records=[SimpleNamespace(symbol="000001")],
            current_master_gaps=[SimpleNamespace(
                security_identity="600001",
                symbol="600001",
                issue_codes=("current_master_mapping_gap",),
            )],
            completeness=SimpleNamespace(
                mapping_coverage=0.5,
                mapped_count=1,
                unconfirmed_count=1,
                shadow_usable=True,
                formal_usable=False,
                reasons=("current_master_mapping_incomplete",),
            ),
            issues=[],
        )

        evidence = adapt_industry(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertFalse(evidence.payload["scopedReplayReady"])
        self.assertIn("industry_mapping_incomplete", evidence.payload["reasons"])

    def test_index_pending_official_evidence_is_unverifiable(self):
        from radar.replay_source_adapters import adapt_index

        methodology = SimpleNamespace(
            formal_ready=False,
            fetched_at=FETCHED_AT,
            effective_from=None,
            reasons=("index_methodology_effective_date_missing",),
        )
        result = SimpleNamespace(
            provider="csindex",
            index_code="000300",
            index_name="沪深300",
            identity_evidence_url="https://www.csindex.com.cn/",
            identity_evidence_sha256="b" * 64,
            methodology=methodology,
            constituent_sets=(),
            fetched_at=FETCHED_AT,
        )

        evidence = adapt_index(result, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn("index_formal_evidence_incomplete", evidence.payload["reasons"])

    def test_index_complete_official_forward_observation_is_ready_without_faking_dates(self):
        from radar.replay_source_adapters import adapt_index

        methodology = SimpleNamespace(
            formal_ready=False,
            fetched_at=FETCHED_AT,
            first_observed_at=FETCHED_AT,
            effective_from=None,
            published_at=None,
            provider_version="2026-07",
            universe_rule="上市证券样本空间",
            selection_rule="选取规模和流动性代表性样本",
            weighting_method="调整市值加权",
            evidence_url="https://oss-ch.csindex.com.cn/methodology.pdf",
            evidence_sha256="c" * 64,
            reasons=(
                "index_methodology_published_at_missing",
                "index_methodology_effective_date_missing",
            ),
        )
        constituents = SimpleNamespace(
            formal_ready=False,
            announced_at=None,
            effective_from=None,
            fetched_at=FETCHED_AT,
            first_observed_at=FETCHED_AT,
            source_date=AS_OF.date(),
            expected_count=1,
            returned_count=1,
            weight_count=1,
            weight_total=100.0,
            evidence_url="https://oss-ch.csindex.com.cn/closeweight.xls",
            evidence_sha256="d" * 64,
            items=[SimpleNamespace(stock_code="000001", weight=100.0)],
            reasons=(
                "constituent_announced_at_missing",
                "constituent_effective_from_missing",
            ),
        )
        result = SimpleNamespace(
            provider="csindex",
            index_code="000300",
            index_name="沪深300",
            identity_evidence_url="https://www.csindex.com.cn/",
            identity_evidence_sha256="b" * 64,
            methodology=methodology,
            constituent_sets=(constituents,),
            fetched_at=FETCHED_AT,
        )

        evidence = adapt_index(result, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertTrue(evidence.payload["forwardReady"])
        self.assertFalse(evidence.payload["retrospectiveReady"])
        self.assertEqual(evidence.payload["reasons"], [])
        self.assertIn(
            "index_methodology_effective_date_missing",
            evidence.payload["retrospectiveReasons"],
        )
        self.assertIsNone(evidence.payload["methodology"]["effective_from"])

    def test_index_forward_observation_without_weight_snapshot_is_unverifiable(self):
        from radar.replay_source_adapters import adapt_index

        methodology = SimpleNamespace(
            formal_ready=False,
            fetched_at=FETCHED_AT,
            first_observed_at=FETCHED_AT,
            effective_from=None,
            provider_version="2026-07",
            universe_rule="样本空间",
            selection_rule="选样规则",
            weighting_method="加权规则",
            evidence_url="https://oss-ch.csindex.com.cn/methodology.pdf",
            evidence_sha256="c" * 64,
            reasons=("index_methodology_effective_date_missing",),
        )
        constituents = SimpleNamespace(
            formal_ready=False,
            announced_at=None,
            effective_from=None,
            fetched_at=FETCHED_AT,
            first_observed_at=FETCHED_AT,
            source_date=AS_OF.date(),
            expected_count=1,
            returned_count=1,
            weight_count=0,
            weight_total=None,
            evidence_url="https://oss-ch.csindex.com.cn/cons.xls",
            evidence_sha256="d" * 64,
            items=[SimpleNamespace(stock_code="000001", weight=None)],
            reasons=("constituent_weight_missing",),
        )
        result = SimpleNamespace(
            provider="csindex",
            index_code="000300",
            index_name="沪深300",
            identity_evidence_url="https://www.csindex.com.cn/",
            identity_evidence_sha256="b" * 64,
            methodology=methodology,
            constituent_sets=(constituents,),
            fetched_at=FETCHED_AT,
        )

        evidence = adapt_index(result, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertFalse(evidence.payload["forwardReady"])
        self.assertIn("index_forward_weight_snapshot_missing", evidence.payload["reasons"])

    def test_etf_full_official_batch_is_ready(self):
        from radar.replay_source_adapters import adapt_etf

        batch = SourceBatch[EtfProductMasterRecord](
            meta=batch_meta(source="sse-szse-etf-official"),
            items=[EtfProductMasterRecord(
                symbol="510300",
                officialName="沪深300ETF",
                exchange="sse",
                productType=ListedFundProductType.ETF,
                managementStyle=EtfManagementStyle.PASSIVE_INDEX,
                assetClass=EtfAssetClass.DOMESTIC_EQUITY,
                classificationMappingVersion="etf-product-classification-v1",
                source="上海证券交易所",
                fetchedAt=FETCHED_AT,
            )],
        )

        evidence = adapt_etf(batch, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 1)

    def test_corporate_action_without_versioned_snapshot_is_missing(self):
        from radar.replay_source_adapters import adapt_corporate_actions

        evidence = adapt_corporate_actions(None, sample_as_of=AS_OF)

        self.assertEqual(evidence.domain, "corporate_action")
        self.assertEqual(evidence.status, "missing")
        self.assertEqual(
            evidence.payload["reasons"],
            ["corporate_action_versioned_source_missing"],
        )

    def test_corporate_action_verified_three_exchange_empty_snapshot_is_ready(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-20260901",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=0,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageFromByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 0)
        self.assertEqual(evidence.payload["coveredExchanges"], [
            "bse", "sse", "szse",
        ])
        self.assertEqual(evidence.payload["reasons"], [])

    def test_corporate_action_scopes_non_a_share_rows_to_security_universe(self):
        from radar.replay_source_adapters import (
            CorporateActionEvidenceItem,
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        common = {
            "exchange": "szse",
            "actionType": "cash_dividend",
            "effectiveOn": date(2026, 8, 21),
            "sourceName": "深圳证券交易所统计月报",
            "sourceUrl": "https://docs.static.szse.cn/month.html",
            "sourceSha256": "a" * 64,
            "documentId": "szse-monthly:2026-08",
        }
        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-with-b-share",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=2,
            returnedCountByExchange={"sse": 0, "szse": 2, "bse": 0},
            coverageFromByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            coverageReasonsByExchange={
                "szse": ["szse_company_announcement_time_missing"],
            },
            items=[
                CorporateActionEvidenceItem(
                    **common,
                    symbol="300176",
                    announcedAt=FETCHED_AT,
                ),
                CorporateActionEvidenceItem(
                    **common,
                    symbol="200429",
                    announcedAt=None,
                ),
            ],
        )

        evidence = adapt_corporate_actions(
            snapshot,
            sample_as_of=AS_OF,
            allowed_symbols={"300176"},
        )

        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 1)
        self.assertEqual(evidence.payload["excludedOutOfScopeCount"], 1)
        self.assertEqual(evidence.payload["excludedSymbols"], ["200429"])
        self.assertEqual(
            evidence.payload["reasons"],
            ["corporate_action_objects_out_of_scope"],
        )
        self.assertNotIn(
            "szse",
            evidence.payload["coverageReasonsByExchange"],
        )

    def test_corporate_action_window_start_must_be_explicit(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-without-window-start",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=0,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn(
            "corporate_action_coverage_window_start_missing",
            evidence.payload["reasons"],
        )

    def test_corporate_action_lagged_or_partial_type_coverage_is_unverifiable(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-lagged",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=0,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": date(2026, 7, 31),
                "bse": AS_OF.date(),
            },
            coverageReasonsByExchange={
                "bse": ["bse_corporate_action_types_incomplete"],
            },
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn(
            "corporate_action_coverage_window_incomplete",
            evidence.payload["reasons"],
        )
        self.assertIn(
            "corporate_action_exchange_scope_incomplete",
            evidence.payload["reasons"],
        )
        self.assertEqual(
            evidence.payload["coverageThroughByExchange"]["szse"],
            "2026-07-31",
        )

    def test_corporate_action_coverage_date_uses_shanghai_market_day(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        sample_as_of = datetime(2026, 9, 1, 16, 30, tzinfo=timezone.utc)
        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-timezone-boundary",
            source="沪深北交易所官方公司行为",
            fetchedAt=datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc),
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=0,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageThroughByExchange={
                "sse": date(2026, 9, 1),
                "szse": date(2026, 9, 1),
                "bse": date(2026, 9, 1),
            },
            items=[],
        )

        evidence = adapt_corporate_actions(
            snapshot,
            sample_as_of=sample_as_of,
        )

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn(
            "corporate_action_coverage_window_incomplete",
            evidence.payload["reasons"],
        )

    def test_corporate_action_partial_exchange_coverage_is_unverifiable(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-partial",
            source="上海证券交易所公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse"],
            missingExchanges=["szse", "bse"],
            expectedCount=0,
            returnedCountByExchange={"sse": 0},
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn(
            "corporate_action_exchange_coverage_incomplete",
            evidence.payload["reasons"],
        )
        self.assertEqual(evidence.payload["missingExchanges"], ["bse", "szse"])

    def test_corporate_action_event_without_effective_date_is_unverifiable(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-incomplete-event",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=1,
            returnedCountByExchange={"sse": 1, "szse": 0, "bse": 0},
            items=[{
                "symbol": "600000",
                "exchange": "sse",
                "actionType": "cash_dividend",
                "announcedAt": FETCHED_AT,
                "effectiveOn": None,
                "sourceName": "上海证券交易所",
                "sourceUrl": "https://www.sse.com.cn/disclosure/example.html",
                "sourceSha256": "e" * 64,
                "documentId": "sse-example",
            }],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertIn(
            "corporate_action_effective_date_missing",
            evidence.payload["reasons"],
        )

    def test_corporate_action_identified_undated_document_is_scoped_exclusion(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-unresolved",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=1,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageFromByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            unresolvedDocuments=[{
                "exchange": "sse",
                "queryName": "equity_distribution",
                "symbol": "600000",
                "announcementId": "1225000001",
                "title": "2025年年度权益分派实施公告",
                "announcedAt": FETCHED_AT,
                "sourceUrl": "https://static.cninfo.com.cn/example.PDF",
                "sourceSha256": "a" * 64,
                "reason": "cninfo_equity_distribution_effective_date_missing",
            }],
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "ready")
        self.assertIn(
            "corporate_action_documents_explicitly_excluded",
            evidence.payload["reasons"],
        )
        self.assertTrue(evidence.payload["scopedReplayReady"])
        self.assertEqual(evidence.payload["excludedSymbols"], ["600000"])
        self.assertEqual(
            evidence.payload["unresolvedDocuments"][0]["announcementId"],
            "1225000001",
        )

    def test_corporate_action_unidentified_document_remains_unverifiable(self):
        from radar.replay_source_adapters import (
            CorporateActionForwardSnapshot,
            adapt_corporate_actions,
        )

        snapshot = CorporateActionForwardSnapshot(
            sourceId="official-corporate-action-unidentified",
            source="沪深北交易所官方公司行为",
            fetchedAt=FETCHED_AT,
            coveredExchanges=["sse", "szse", "bse"],
            missingExchanges=[],
            expectedCount=1,
            returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
            coverageFromByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            coverageThroughByExchange={
                "sse": AS_OF.date(),
                "szse": AS_OF.date(),
                "bse": AS_OF.date(),
            },
            unresolvedDocuments=[{
                "exchange": "sse",
                "queryName": "equity_distribution",
                "reason": "cninfo_equity_distribution_effective_date_missing",
            }],
            items=[],
        )

        evidence = adapt_corporate_actions(snapshot, sample_as_of=AS_OF)

        self.assertEqual(evidence.status, "unverifiable")
        self.assertFalse(evidence.payload["scopedReplayReady"])
        self.assertIn(
            "corporate_action_documents_unresolved",
            evidence.payload["reasons"],
        )

    def test_adapter_rejects_source_fetched_after_sample(self):
        from radar.replay_source_adapters import adapt_security_universe

        future = batch_meta(source="official").model_copy(
            update={
                "fetched_at": datetime(
                    2026, 9, 1, 7, 1, tzinfo=timezone.utc
                ),
            },
        )
        batch = SourceBatch[SecurityMasterRecord](meta=future, items=[
            SecurityMasterRecord(
                symbol="000001",
                name="平安银行",
                exchange="szse",
                board="main",
                source="深圳证券交易所",
                fetchedAt=FETCHED_AT,
            ),
        ])

        with self.assertRaisesRegex(ValueError, "future_fetchedAt"):
            adapt_security_universe(batch, sample_as_of=AS_OF)

    def test_adapter_rejects_nested_record_observed_after_sample(self):
        from radar.replay_source_adapters import adapt_security_universe

        batch = SourceBatch[SecurityMasterRecord](
            meta=batch_meta(source="official"),
            items=[SecurityMasterRecord(
                symbol="000001",
                name="平安银行",
                exchange="szse",
                board="main",
                source="深圳证券交易所",
                fetchedAt=datetime(
                    2026, 9, 1, 7, 1, tzinfo=timezone.utc
                ),
            )],
        )

        with self.assertRaisesRegex(ValueError, "future_recordFetchedAt"):
            adapt_security_universe(batch, sample_as_of=AS_OF)


if __name__ == "__main__":
    unittest.main()
