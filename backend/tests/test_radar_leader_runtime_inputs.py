import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from radar.contracts import (
    IndustryClassificationRecord,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.leader_history_features import (
    AdjustedHistoryPoint,
    AdjustedHistorySeries,
    HistoryAdjustmentBasis,
    HistorySeriesRole,
    LeaderHistoryFeatureInput,
)
from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    BusinessEvidenceSourceKind,
    BusinessProofType,
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessCatalystReview,
    LeaderBusinessProof,
    LeaderCatalystReference,
)
from radar.leader_runtime_inputs import build_leader_runtime_evidence
from radar.leader_research_features import (
    ResearchFeatureStatus,
    build_leader_research_market_context,
)
from radar.leader_scoring import LeaderMetricStatus
from radar.leader_state_machine import BusinessExposureStatus
from radar.leader_tradability_features import (
    LeaderSecurityLifecycleEvidence,
    LeaderTradabilityFeatureInput,
    LeaderTradingRuleEvidence,
    LeaderTradingStatusEvidence,
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import (
    OfficialDailyTradabilityReference,
    PriceLimitSpecialSession,
    TradabilitySourceGrade,
    build_leader_tradability_source_input,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 27, 2, 30, tzinfo=UTC)


class LeaderRuntimeInputsTests(unittest.TestCase):
    @staticmethod
    def quote_batch():
        changes = (4.0, 3.0, 2.0, 1.0, 0.0, -1.0)
        items = [
            QuoteSnapshot(
                symbol=f"00000{index}",
                name=f"证券{index}",
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=AS_OF,
                price=10.0,
                changePercent=change,
                turnoverAmountSource=0.0,
                turnoverRatePercent=0.0,
                volumeRatio=0.0,
                marketCapSource=0.0,
            )
            for index, change in enumerate(changes, start=1)
        ]
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="market-run",
                batchId="market-run:tencent-quotes",
                source="tencent_finance",
                asOf=AS_OF,
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=AS_OF,
                expectedCount=6,
                returnedCount=6,
                rowCoverage=1.0,
                requiredFieldCoverage={
                    "price": 1.0,
                    "source_time": 1.0,
                    "change_percent": 1.0,
                    "turnover_amount_source": 1.0,
                },
            ),
            items=items,
        )

    @staticmethod
    def quote_health(
        *,
        status=SourceStatus.HEALTHY,
        allows_new_state=True,
        reasons=(),
    ):
        return SourceHealthResult(
            status=status,
            allowsNewState=allows_new_state,
            reasons=reasons,
            ageSeconds=20,
        )

    @staticmethod
    def market_snapshot(*, as_of=AS_OF):
        return {
            "radarRunId": "market-run",
            "asOf": as_of,
            "sourceTime": as_of - timedelta(seconds=20),
            "fetchedAt": as_of,
            "indexCompleteness": {
                "rowCoverage": 1.0,
                "requiredFieldCoverage": {
                    "price": 1.0,
                    "change_percent": 1.0,
                    "source_time": 1.0,
                },
                "isComplete": True,
                "reasons": (),
            },
            "breadth": {
                "completeness": {
                    "rowCoverage": 1.0,
                    "requiredFieldCoverage": {
                        "change_percent": 1.0,
                    },
                    "isComplete": True,
                    "reasons": (),
                },
            },
            "duplicateSymbolCount": 0,
            "unknownSymbolCount": 0,
            "indices": (
                {
                    "indexKey": "sse_composite",
                    "changePercent": 0.0,
                },
                {
                    "indexKey": "szse_component",
                    "changePercent": 0.0,
                },
                {
                    "indexKey": "chinext",
                    "changePercent": 0.0,
                },
                {
                    "indexKey": "star50",
                    "changePercent": 0.0,
                },
            ),
        }

    @staticmethod
    def sector_rows(*, as_of=AS_OF):
        return ({
            "radarRunId": "sector-run",
            "industryReleaseId": "release-1",
            "divisionCode": "66",
            "divisionName": "货币金融服务",
            "asOf": as_of,
            "sourceTime": as_of - timedelta(seconds=30),
            "fetchedAt": as_of,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {"change_percent": 1.0},
            "isComplete": True,
            "shadowUsable": True,
            "reasons": (),
            "equalReturn": 0.0,
            "capWeightedReturn": 0.0,
            "exTopReturn": 0.0,
            "upRatio": 0.0,
            "topContributorSymbol": "000001",
            "topContributionPercentPoints": 0.0,
        },)

    @staticmethod
    def industry_records():
        return tuple(
            IndustryClassificationRecord(
                releasePeriod="2025H2",
                sourceSymbol=f"00000{index}",
                sourceName=f"证券{index}",
                securityIdentity=f"00000{index}",
                identityStatus="exact",
                categoryCode="J",
                categoryName="金融业",
                divisionCode="66",
                divisionName="货币金融服务",
                recordStatus="accepted",
            )
            for index in range(1, 7)
        )

    @staticmethod
    def security_records():
        return tuple(
            SecurityMasterRecord(
                symbol=f"00000{index}",
                name=f"证券{index}",
                exchange="szse",
                board="主板",
                listingDate="1991-04-03",
                sourceReportDate=AS_OF.date(),
                source="szse",
                fetchedAt=AS_OF,
            )
            for index in range(1, 7)
        )

    def build(
        self,
        *,
        market_snapshot=None,
        sector_rows=None,
        security_records=None,
    ):
        return build_leader_runtime_evidence(
            as_of=AS_OF,
            quote_batch=self.quote_batch(),
            quote_health=self.quote_health(),
            market_snapshot=(
                self.market_snapshot()
                if market_snapshot is None
                else market_snapshot
            ),
            sector_rows=(
                self.sector_rows()
                if sector_rows is None
                else sector_rows
            ),
            industry_records=self.industry_records(),
            security_records=(
                self.security_records()
                if security_records is None
                else security_records
            ),
        )

    @staticmethod
    def full_research_quote_batch(*, include_bse=False):
        items = []
        limit = 101 if include_bse else 100
        for index in range(1, limit + 1):
            items.append(QuoteSnapshot(
                symbol=f"{index:06d}",
                name=f"证券{index}",
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=AS_OF,
                price=10.0,
                changePercent=(
                    6.0 - index
                    if index <= 6
                    else 0.1
                ),
                turnoverAmountSource=1000.0,
                turnoverAmountCny=10000000.0,
                turnoverAmountUnitStatus=(
                    UnitVerificationStatus.VERIFIED
                ),
                turnoverRatePercent=1.0,
                volumeRatio=2.0 if index == 1 else 1.0,
                marketCapSource=100.0,
            ))
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="market-run",
                batchId="market-run:tencent-quotes",
                source="tencent_finance",
                asOf=AS_OF,
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=AS_OF,
                expectedCount=limit,
                returnedCount=limit,
                rowCoverage=1.0,
                requiredFieldCoverage={
                    "price": 1.0,
                    "source_time": 1.0,
                    "change_percent": 1.0,
                    "turnover_amount_source": 1.0,
                    "turnover_rate_percent": 1.0,
                    "volume_ratio": 1.0,
                    "market_cap_source": 1.0,
                },
            ),
            items=items,
        )

    @staticmethod
    def full_research_security_records(*, include_bse=False):
        limit = 101 if include_bse else 100
        return tuple(
            SecurityMasterRecord(
                symbol=f"{index:06d}",
                name=f"证券{index}",
                exchange=(
                    "bse"
                    if include_bse and index == 101
                    else "szse"
                ),
                board="主板",
                listingDate="2000-01-01",
                source="exchange",
                fetchedAt=AS_OF,
            )
            for index in range(1, limit + 1)
        )

    @staticmethod
    def full_research_sector_rows():
        rows = []
        for index in range(20):
            rows.append({
                "radarRunId": "sector-run",
                "industryReleaseId": "release-1",
                "divisionCode": "66" if index == 0 else f"{index:02d}",
                "divisionName": f"行业{index}",
                "asOf": AS_OF,
                "sourceTime": AS_OF - timedelta(seconds=20),
                "fetchedAt": AS_OF,
                "rowCoverage": 1.0,
                "requiredFieldCoverage": {
                    "change_percent": 1.0,
                },
                "isComplete": True,
                "shadowUsable": True,
                "reasons": (),
                "equalReturn": 3.0 if index == 0 else index / 10,
                "capWeightedReturn": 0.0,
                "exTopReturn": 2.0 if index == 0 else index / 20,
                "upRatio": 0.8 if index == 0 else 0.5,
                "topContributorSymbol": "000001",
                "topContributionPercentPoints": 0.0,
            })
        return tuple(rows)

    @staticmethod
    def history_input(symbol="000001"):
        current = date(2026, 6, 26)
        trade_dates = []
        while len(trade_dates) < 21:
            if current.weekday() < 5:
                trade_dates.append(current)
            current += timedelta(days=1)

        def history_series(*, role, adjustment_basis, prices, identity):
            return AdjustedHistorySeries(
                role=role,
                symbol=identity,
                source_contract_id=f"history-source:{identity}",
                adjustment_basis=adjustment_basis,
                return_basis="price_return",
                status=ResearchFeatureStatus.READY,
                source_time=datetime(
                    2026, 7, 24, 7, 0, tzinfo=UTC
                ),
                fetched_at=AS_OF - timedelta(minutes=1),
                points=tuple(
                    AdjustedHistoryPoint(
                        trade_date=trade_date,
                        close=close,
                    )
                    for trade_date, close in zip(trade_dates, prices)
                ),
            )

        return LeaderHistoryFeatureInput(
            as_of=AS_OF,
            expected_trade_dates=tuple(trade_dates),
            candidate=history_series(
                role=HistorySeriesRole.CANDIDATE_SECURITY,
                adjustment_basis=(
                    HistoryAdjustmentBasis.FORWARD_ADJUSTED
                ),
                prices=tuple(100.0 + index for index in range(21)),
                identity=symbol,
            ),
            industry_benchmark=history_series(
                role=HistorySeriesRole.INDUSTRY_BENCHMARK,
                adjustment_basis=(
                    HistoryAdjustmentBasis
                    .POINT_IN_TIME_EQUAL_WEIGHT_PRICE_RETURN
                ),
                prices=tuple(
                    100.0 + index * 0.5
                    for index in range(21)
                ),
                identity="66",
            ),
            board_index=history_series(
                role=HistorySeriesRole.BOARD_INDEX,
                adjustment_basis=(
                    HistoryAdjustmentBasis.CONTINUOUS_INDEX
                ),
                prices=tuple(
                    100.0 + index * 0.25
                    for index in range(21)
                ),
                identity="szse_component",
            ),
        )

    @staticmethod
    def business_catalyst_input():
        catalyst = LeaderCatalystReference(
            catalyst_id="catalyst-66-20260727",
            industry_code="66",
            industry_release_id="release-1",
            source_kind=(
                BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE
            ),
            source_name="深圳证券交易所",
            source_url=(
                "https://www.szse.cn/disclosure/catalyst-1"
            ),
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
                BusinessEvidenceSourceKind
                .DESIGNATED_DISCLOSURE_PLATFORM
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
            relation=BusinessCatalystRelation.HIGHLY_RELATED,
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

    @staticmethod
    def tradability_input(symbol="000001", as_of=AS_OF):
        return LeaderTradabilityFeatureInput(
            as_of=as_of,
            quote=QuoteSnapshot(
                symbol=symbol,
                name="证券1",
                sourceTime=as_of - timedelta(seconds=20),
                fetchedAt=as_of,
                price=10.0,
                previousClose=9.5,
                openPrice=9.8,
                highPrice=10.2,
                lowPrice=9.7,
                changePercent=5.26,
            ),
            quote_source_contract_id=(
                "tencent-full-market-quote-v1:"
                "market-run:tencent-quotes"
            ),
            quote_source_status=ResearchFeatureStatus.READY,
            lifecycle=LeaderSecurityLifecycleEvidence(
                symbol=symbol,
                exchange="szse",
                board="主板",
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                listed_trading_day_count=100,
                source_contract_id=(
                    f"exchange-security-lifecycle-v1:{symbol}"
                ),
                source_name="深圳证券交易所",
                source_url=(
                    "https://www.szse.cn/market/product/stock/list/"
                ),
                document_id=f"security-lifecycle-{symbol}",
                published_at=as_of - timedelta(days=1),
                effective_from=as_of - timedelta(days=1),
                effective_until=None,
                fetched_at=as_of - timedelta(minutes=5),
            ),
            trading_status=LeaderTradingStatusEvidence(
                symbol=symbol,
                trading_date=as_of.date(),
                status=TradingSessionStatus.TRADING,
                source_contract_id=(
                    "exchange-trading-status-v1:"
                    f"{symbol}:20260727"
                ),
                source_name="深圳证券交易所",
                source_time=as_of - timedelta(seconds=20),
                fetched_at=as_of,
            ),
            trading_rule=LeaderTradingRuleEvidence(
                symbol=symbol,
                trading_date=as_of.date(),
                rule_version="cn-equity-price-limit-rule-v1",
                price_limit_mode=PriceLimitMode.BOUNDED,
                upper_limit_price=10.45,
                lower_limit_price=8.55,
                source_contract_id=(
                    f"exchange-price-limit-v1:{symbol}:20260727"
                ),
                source_name="深圳证券交易所",
                source_url=(
                    "https://www.szse.cn/lawrules/rule/stock/"
                ),
                published_at=as_of - timedelta(days=30),
                effective_from=as_of - timedelta(days=20),
                effective_until=None,
            ),
        )

    def tradability_source_input(self):
        direct_input = self.tradability_input()
        resolved = build_leader_tradability_source_input(
            as_of=AS_OF,
            quote=direct_input.quote,
            quote_source_contract_id=(
                direct_input.quote_source_contract_id
            ),
            quote_source_status=ResearchFeatureStatus.READY,
            lifecycle=direct_input.lifecycle,
            official_reference=OfficialDailyTradabilityReference(
                symbol="000001",
                exchange="szse",
                board="主板",
                trading_date=AS_OF.date(),
                lifecycle_status=SecurityLifecycleStatus.NORMAL,
                trading_status=TradingSessionStatus.TRADING,
                special_session=PriceLimitSpecialSession.NONE,
                price_limit_mode=PriceLimitMode.BOUNDED,
                upper_limit_price=10.45,
                lower_limit_price=8.55,
                source_grade=(
                    TradabilitySourceGrade.OFFICIAL_PRIMARY
                ),
                source_contract_id=(
                    "szse-daily-tradability-v1:"
                    "000001:20260727"
                ),
                source_name="深圳证券交易所",
                source_url=(
                    "https://www.szse.cn/marketServices/"
                    "technicalservice/interface/"
                ),
                document_id="cashauctionparams-20260727",
                source_time=AS_OF - timedelta(seconds=20),
                fetched_at=AS_OF,
            ),
        )
        self.assertEqual(
            resolved.status,
            ResearchFeatureStatus.READY,
        )
        self.assertIsNotNone(resolved.feature_input)
        return resolved.feature_input

    def build_full_research(
        self,
        *,
        include_bse=False,
        quote_batch=None,
        quote_health=None,
        market_snapshot=None,
        history_inputs_by_symbol=None,
        business_catalyst_inputs_by_symbol=None,
        tradability_inputs_by_symbol=None,
    ):
        return build_leader_runtime_evidence(
            as_of=AS_OF,
            quote_batch=(
                self.full_research_quote_batch(
                    include_bse=include_bse
                )
                if quote_batch is None
                else quote_batch
            ),
            quote_health=(
                self.quote_health()
                if quote_health is None
                else quote_health
            ),
            market_snapshot=(
                self.market_snapshot()
                if market_snapshot is None
                else market_snapshot
            ),
            sector_rows=self.full_research_sector_rows(),
            industry_records=self.industry_records(),
            security_records=self.full_research_security_records(
                include_bse=include_bse
            ),
            history_inputs_by_symbol=history_inputs_by_symbol,
            business_catalyst_inputs_by_symbol=(
                business_catalyst_inputs_by_symbol
            ),
            tradability_inputs_by_symbol=(
                tradability_inputs_by_symbol
            ),
        )

    def test_future_market_snapshot_is_rejected_before_candidate_build(self):
        result = self.build(
            market_snapshot=self.market_snapshot(
                as_of=AS_OF + timedelta(seconds=1)
            )
        )

        self.assertEqual(result.status, "not_ready")
        self.assertEqual(
            result.gate_reasons,
            ("market_snapshot_from_future",),
        )
        self.assertEqual(result.evidence_items, ())

    def test_top_five_keep_real_zero_without_inventing_scores(self):
        result = self.build()

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.item_count, 5)
        self.assertEqual(
            [item.symbol for item in result.evidence_items],
            ["000001", "000002", "000003", "000004", "000005"],
        )
        last = result.evidence_items[-1]
        self.assertEqual(last.evidence["quote"]["changePercent"], 0.0)
        self.assertEqual(last.evidence["sector"]["equalReturn"], 0.0)
        self.assertEqual(
            last.gates.business_exposure_status,
            BusinessExposureStatus.MISSING,
        )
        self.assertTrue(
            all(
                dimension.score is None
                and dimension.status != LeaderMetricStatus.VERIFIED
                for dimension in last.dimensions
            )
        )

    def test_beijing_exchange_is_excluded_from_stage6_candidate_inputs(self):
        security_records = list(self.security_records())
        security_records[0] = security_records[0].model_copy(
            update={"exchange": "bse"}
        )

        result = self.build(security_records=security_records)

        self.assertEqual(result.status, "ready")
        self.assertEqual(
            [item.symbol for item in result.evidence_items],
            ["000002", "000003", "000004", "000005", "000006"],
        )

    def test_runtime_attaches_research_features_without_formal_score(self):
        result = self.build_full_research()

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"]

        self.assertFalse(research["scoreReady"])
        self.assertEqual(research["participatingWeight"], 55.0)
        self.assertEqual(
            research["dimensions"]["market_leadership"]
            ["components"]["industry_return_rank"]["populationSize"],
            6,
        )
        self.assertTrue(
            all(dimension.score is None for dimension in first.dimensions)
        )

    def test_runtime_keeps_history_missing_when_input_is_absent(self):
        result = self.build_full_research()

        first = result.evidence_items[0]
        history = first.evidence["researchFeatures"][
            "historyContinuity"
        ]
        continuity = next(
            dimension
            for dimension in first.dimensions
            if dimension.field_name == "relative_strength_continuity"
        )

        self.assertEqual(history["status"], "missing")
        self.assertEqual(
            history["reasons"],
            ["continuity_history_missing"],
        )
        self.assertEqual(
            continuity.status,
            LeaderMetricStatus.MISSING,
        )
        self.assertIn(
            "continuity_history",
            first.invalidation["missingPrerequisites"],
        )

    def test_ready_history_is_research_only_and_keeps_formal_gate_closed(self):
        result = self.build_full_research(
            history_inputs_by_symbol={
                "000001": self.history_input(),
            },
        )

        first = result.evidence_items[0]
        history = first.evidence["researchFeatures"][
            "historyContinuity"
        ]
        continuity = next(
            dimension
            for dimension in first.dimensions
            if dimension.field_name == "relative_strength_continuity"
        )

        self.assertEqual(history["status"], "ready")
        self.assertFalse(history["scoreReady"])
        self.assertIsNone(history["researchScore"])
        self.assertAlmostEqual(
            history["metrics"]["excessReturns"][
                "vsIndustry"
            ]["20d"],
            0.1,
            places=6,
        )
        self.assertIsNone(continuity.score)
        self.assertEqual(
            continuity.status,
            LeaderMetricStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            continuity.reasons,
            ("continuity_research_only",),
        )
        self.assertFalse(first.gates.continuity_passed)
        self.assertIn(
            "continuity_formal_rule",
            first.invalidation["missingPrerequisites"],
        )
        self.assertNotIn(
            "continuity_history",
            first.invalidation["missingPrerequisites"],
        )

    def test_verified_turnover_is_research_only_and_keeps_gates_closed(self):
        result = self.build_full_research()

        first = result.evidence_items[0]
        liquidity = first.evidence["researchFeatures"][
            "liquidityTradability"
        ]
        dimension = next(
            item
            for item in first.dimensions
            if item.field_name == "liquidity_tradability"
        )

        self.assertEqual(
            liquidity["metrics"]["turnoverAmountCny"],
            {
                "value": 10000000.0,
                "unit": "CNY",
                "status": "ready",
                "reasons": [],
            },
        )
        self.assertEqual(
            liquidity["metrics"]["turnoverRatePercent"]["status"],
            "ready",
        )
        self.assertEqual(liquidity["status"], "source_unverified")
        self.assertFalse(liquidity["scoreReady"])
        self.assertFalse(liquidity["formalUsable"])
        self.assertIsNone(dimension.score)
        self.assertEqual(
            dimension.status,
            LeaderMetricStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "same_time_turnover_history_missing",
            dimension.reasons,
        )
        self.assertFalse(first.gates.liquidity_passed)
        self.assertFalse(first.gates.tradability_passed)

    def test_runtime_keeps_business_catalyst_missing_without_input(self):
        result = self.build_full_research()

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "businessCatalyst"
        ]

        self.assertEqual(research["status"], "missing")
        self.assertEqual(
            research["reasons"],
            ["business_exposure_evidence_missing"],
        )
        self.assertEqual(
            first.gates.business_exposure_status,
            BusinessExposureStatus.MISSING,
        )
        self.assertIsNone(first.business_exposure_source_contract_id)

    def test_ready_business_catalyst_is_research_only(self):
        result = self.build_full_research(
            business_catalyst_inputs_by_symbol={
                "000001": self.business_catalyst_input(),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "businessCatalyst"
        ]
        dimension = next(
            item
            for item in first.dimensions
            if item.field_name == "business_exposure"
        )

        self.assertEqual(research["status"], "ready")
        self.assertEqual(research["relation"], "highly_related")
        self.assertFalse(research["formalUsable"])
        self.assertFalse(research["scoreReady"])
        self.assertIsNone(dimension.score)
        self.assertEqual(
            dimension.status,
            LeaderMetricStatus.MISSING,
        )
        self.assertEqual(
            first.gates.business_exposure_status,
            BusinessExposureStatus.MISSING,
        )
        self.assertIsNone(first.business_exposure_source_contract_id)

    def test_business_catalyst_identity_mismatch_is_research_only(self):
        value = self.business_catalyst_input()
        result = self.build_full_research(
            business_catalyst_inputs_by_symbol={
                "000001": replace(
                    value,
                    industry_release_id="release-other",
                ),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "businessCatalyst"
        ]

        self.assertEqual(research["status"], "source_unverified")
        self.assertIn(
            "business_evidence_identity_mismatch",
            research["reasons"],
        )
        self.assertEqual(
            first.gates.business_exposure_status,
            BusinessExposureStatus.MISSING,
        )

    def test_disproved_business_catalyst_does_not_change_formal_gate(self):
        value = self.business_catalyst_input()
        review = replace(
            value.reviews[0],
            relation=BusinessCatalystRelation.DISPROVED,
        )
        result = self.build_full_research(
            business_catalyst_inputs_by_symbol={
                "000001": replace(value, reviews=(review,)),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "businessCatalyst"
        ]

        self.assertEqual(research["status"], "ready")
        self.assertEqual(research["relation"], "disproved")
        self.assertEqual(
            first.gates.business_exposure_status,
            BusinessExposureStatus.MISSING,
        )
        self.assertIsNone(first.business_exposure_source_contract_id)

    def test_runtime_keeps_security_tradability_missing_without_input(self):
        result = self.build_full_research()

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "securityTradability"
        ]

        self.assertEqual(research["status"], "missing")
        self.assertIsNone(research["researchEligible"])
        self.assertFalse(first.gates.stock_gate_passed)
        self.assertFalse(first.gates.tradability_passed)

    def test_ready_security_tradability_is_research_only(self):
        result = self.build_full_research(
            tradability_inputs_by_symbol={
                "000001": self.tradability_input(),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "securityTradability"
        ]
        dimension = next(
            item
            for item in first.dimensions
            if item.field_name == "liquidity_tradability"
        )

        self.assertEqual(research["status"], "ready")
        self.assertTrue(research["researchEligible"])
        self.assertFalse(research["formalUsable"])
        self.assertFalse(research["scoreReady"])
        self.assertIsNone(dimension.score)
        self.assertFalse(first.gates.stock_gate_passed)
        self.assertFalse(first.gates.liquidity_passed)
        self.assertFalse(first.gates.tradability_passed)

    def test_c3_source_input_uses_existing_research_only_runtime_path(self):
        result = self.build_full_research(
            tradability_inputs_by_symbol={
                "000001": self.tradability_source_input(),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "securityTradability"
        ]

        self.assertEqual(research["status"], "ready")
        self.assertTrue(research["researchEligible"])
        self.assertFalse(research["scoreReady"])
        self.assertFalse(research["formalUsable"])
        self.assertFalse(first.gates.stock_gate_passed)
        self.assertFalse(first.gates.liquidity_passed)
        self.assertFalse(first.gates.tradability_passed)

    def test_security_tradability_identity_mismatch_is_research_only(self):
        result = self.build_full_research(
            tradability_inputs_by_symbol={
                "000001": self.tradability_input(symbol="000002"),
            },
        )

        first = result.evidence_items[0]
        research = first.evidence["researchFeatures"][
            "securityTradability"
        ]

        self.assertEqual(research["status"], "source_unverified")
        self.assertIn(
            "tradability_evidence_identity_mismatch",
            research["reasons"],
        )
        self.assertFalse(first.gates.stock_gate_passed)
        self.assertFalse(first.gates.tradability_passed)

    def test_security_tradability_batch_identity_must_match_runtime(self):
        cases = (
            (
                self.tradability_input(
                    as_of=AS_OF - timedelta(minutes=1)
                ),
                "tradability_evidence_as_of_mismatch",
            ),
            (
                replace(
                    self.tradability_input(),
                    quote_source_contract_id=(
                        "tencent-full-market-quote-v1:other-batch"
                    ),
                ),
                "tradability_evidence_identity_mismatch",
            ),
        )
        for item, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.build_full_research(
                    tradability_inputs_by_symbol={"000001": item},
                )

                research = result.evidence_items[0].evidence[
                    "researchFeatures"
                ]["securityTradability"]

                self.assertEqual(
                    research["status"],
                    "source_unverified",
                )
                self.assertIn(expected_reason, research["reasons"])

    def test_bse_quote_is_excluded_from_research_populations(self):
        result = self.build_full_research(include_bse=True)

        research = result.evidence_items[0].evidence["researchFeatures"]

        self.assertEqual(
            research["dimensions"]["market_leadership"]
            ["components"]["board_excess_rank"]["populationSize"],
            100,
        )
        self.assertEqual(
            research["dimensions"]["auxiliary"]
            ["components"]["volume_ratio_rank"]["populationSize"],
            100,
        )

    def test_degraded_quote_source_cannot_create_research_candidates(self):
        result = self.build_full_research(
            quote_health=self.quote_health(
                status=SourceStatus.DEGRADED,
                allows_new_state=True,
                reasons=("quote_source_degraded",),
            )
        )

        self.assertEqual(result.status, "not_ready")
        self.assertIn("quote_source_not_healthy", result.gate_reasons)

    def test_incomplete_market_source_cannot_mark_leadership_ready(self):
        market_snapshot = self.market_snapshot()
        market_snapshot["indexCompleteness"] = {
            **market_snapshot["indexCompleteness"],
            "isComplete": False,
            "reasons": ("index_batch_incomplete",),
        }

        result = self.build_full_research(
            market_snapshot=market_snapshot
        )

        research = result.evidence_items[0].evidence["researchFeatures"]
        self.assertEqual(
            research["dimensions"]["market_leadership"]["status"],
            "source_unverified",
        )
        self.assertIsNone(
            research["dimensions"]["market_leadership"][
                "researchScore"
            ]
        )

    def test_invalid_top_quote_is_removed_before_top_five_selection(self):
        for invalid_quote in (
            {"change_percent": float("inf")},
            {"source_time": AS_OF - timedelta(seconds=91)},
        ):
            with self.subTest(invalid_quote=invalid_quote):
                batch = self.full_research_quote_batch()
                items = list(batch.items)
                items[0] = items[0].model_copy(update=invalid_quote)
                batch = batch.model_copy(update={"items": items})

                result = self.build_full_research(quote_batch=batch)

                self.assertEqual(
                    [item.symbol for item in result.evidence_items],
                    ["000002", "000003", "000004", "000005", "000006"],
                )

    def test_market_research_context_is_built_once_per_quote_batch(self):
        with patch(
            "radar.leader_runtime_inputs."
            "build_leader_research_market_context",
            wraps=build_leader_research_market_context,
        ) as context_builder:
            result = self.build_full_research()

        self.assertEqual(result.item_count, 5)
        self.assertEqual(context_builder.call_count, 1)


if __name__ == "__main__":
    unittest.main()
