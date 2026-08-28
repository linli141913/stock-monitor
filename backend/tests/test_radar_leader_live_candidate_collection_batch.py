import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationGap,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    IndexQuoteSnapshot,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateCollectionRequest,
    LeaderLiveCandidateCollectionSources,
    LeaderLiveCandidateCollectionStatus,
    build_default_leader_live_candidate_collection_sources,
    build_repository_classification_release_loader,
    collect_leader_live_candidate_batch,
)
from radar.sources.market_indices import MARKET_INDEX_IDENTITIES


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DISCOVERY_AS_OF = datetime(
    2026, 8, 17, 14, 0, tzinfo=SHANGHAI_TZ
)
COLLECTION_AS_OF = DISCOVERY_AS_OF + timedelta(seconds=2)
COLLECTION_COMPLETED_AT = COLLECTION_AS_OF + timedelta(milliseconds=100)


def _meta(
    run_id,
    batch_id,
    source,
    as_of,
    count,
    *,
    source_time=None,
    expected_count=None,
):
    expected = count if expected_count is None else expected_count
    return RadarBatchMeta(
        radarRunId=run_id,
        batchId=batch_id,
        source=source,
        asOf=as_of,
        sourceTime=source_time,
        fetchedAt=as_of + timedelta(milliseconds=100),
        expectedCount=expected,
        returnedCount=count,
        rowCoverage=count / expected if expected else 0.0,
        requiredFieldCoverage={
            "price": 1.0 if count else 0.0,
            "source_time": 1.0 if count else 0.0,
            "change_percent": 1.0 if count else 0.0,
            "turnover_amount_source": 1.0 if count else 0.0,
        },
        issues=[],
    )


def _security(symbol):
    exchange = "sse" if symbol.startswith("6") else "szse"
    return SecurityMasterRecord(
        symbol=symbol,
        name=f"证券{symbol}",
        exchange=exchange,
        board="主板A股" if exchange == "sse" else "主板",
        listingDate=date(2010, 1, 1),
        source=exchange,
        fetchedAt=DISCOVERY_AS_OF,
        sourceFields={"证券代码" if exchange == "sse" else "A股代码": symbol},
    )


def _quote(symbol, as_of, change_percent=1.0):
    return QuoteSnapshot(
        symbol=symbol,
        name=f"证券{symbol}",
        sourceTime=as_of,
        fetchedAt=as_of + timedelta(milliseconds=100),
        price=10.0,
        changePercent=change_percent,
        turnoverAmountSource=100.0,
        turnoverRatePercent=1.0,
        volumeRatio=1.0,
        marketCapSource=100.0,
    )


def _classification(
    run_id,
    batch_id,
    as_of,
    *,
    first_observed_at=None,
    sha="a" * 64,
):
    first_observed_at = first_observed_at or as_of
    records = [
        IndustryClassificationRecord(
            releasePeriod="2025H2",
            sourceSymbol=symbol,
            sourceName=f"证券{symbol}",
            securityIdentity=symbol,
            identityStatus=IndustryIdentityStatus.EXACT,
            categoryCode="A",
            categoryName="农、林、牧、渔业",
            divisionCode="01",
            divisionName="农业",
            recordStatus=IndustryRecordStatus.ACCEPTED,
        )
        for symbol in ("000001", "000002")
    ]
    release = IndustryClassificationRelease(
        schemeVersion="test-v1",
        releasePeriod="2025H2",
        sourcePageTitle="测试行业发布",
        publicationPageUrl="https://www.capco.org.cn/test.html",
        documentUrl="https://www.capco.org.cn/test.pdf",
        documentSha256=sha,
        publishedDate=date(2026, 4, 1),
        firstObservedAt=first_observed_at,
        fetchedAt=max(as_of, first_observed_at),
        knowledgeEffectiveFrom=max(as_of, first_observed_at),
        classificationStartDate=date(2025, 1, 1),
        historyStatus=IndustryHistoryStatus.FORWARD_OBSERVED,
        sourceRecordCount=len(records),
        uniqueSourceSymbolCount=len(records),
        requiredFieldCoverage={"division_code": 1.0},
    )
    return IndustryClassificationSnapshot(
        meta=_meta(
            run_id,
            batch_id,
            "capco_industry_classification",
            as_of,
            len(records),
        ),
        status=SourceStatus.HEALTHY,
        release=release,
        records=records,
        completeness=IndustryClassificationCompleteness(
            sourceRecordCount=len(records),
            uniqueSourceSymbolCount=len(records),
            currentMasterCount=len(records),
            mappedCount=len(records),
            unconfirmedCount=0,
            excludedSourceCount=0,
            mappingCoverage=1.0,
            requiredFieldCoverage={"division_code": 1.0},
            shadowUsable=True,
            formalUsable=False,
            reasons=("formal_use_not_approved",),
        ),
        issues=[],
    )


def _index_batch(run_id, batch_id, as_of):
    items = [
        IndexQuoteSnapshot(
            indexKey=identity.index_key,
            symbol=identity.symbol,
            name=identity.name,
            exchange=identity.exchange,
            sourceSymbol=identity.source_symbol,
            sourceTime=as_of,
            fetchedAt=as_of + timedelta(milliseconds=100),
            price=1000.0,
            changePercent=1.0,
        )
        for identity in MARKET_INDEX_IDENTITIES
    ]
    return SourceBatch(
        meta=RadarBatchMeta(
            radarRunId=run_id,
            batchId=batch_id,
            source="tencent_finance_indices",
            asOf=as_of,
            sourceTime=as_of,
            fetchedAt=as_of + timedelta(milliseconds=100),
            expectedCount=len(items),
            returnedCount=len(items),
            rowCoverage=1.0,
            requiredFieldCoverage={
                "price": 1.0,
                "change_percent": 1.0,
                "source_time": 1.0,
            },
            issues=[],
        ),
        items=items,
    )


class LeaderLiveCandidateCollectionBatchTests(unittest.TestCase):
    def request(self):
        return LeaderLiveCandidateCollectionRequest(
            radar_run_id="run-live-1",
            security_master_batch_id="security-discovery-1",
            discovery_classification_batch_id="classification-discovery-1",
            collection_classification_batch_id="classification-collection-1",
            quote_batch_id="quotes-collection-1",
            index_batch_id="indices-collection-1",
            etf_symbols=(),
        )

    def sources(self, quote_source=None, classification_source=None, classification_release_source=None):
        securities = tuple(_security(symbol) for symbol in ("000001", "000002"))
        quote_source = quote_source or (
            lambda symbols, run_id, batch_id, as_of: SourceBatch(
                meta=_meta(
                    run_id,
                    batch_id,
                    "tencent_finance",
                    as_of,
                    len(symbols),
                    source_time=as_of,
                ),
                items=[
                    _quote(symbol, as_of, 2.0 if symbol == "000001" else 1.0)
                    for symbol in symbols
                ],
            )
        )
        classification_source = classification_source or (
            lambda run_id, batch_id, as_of, records=None, **kwargs: _classification(
                run_id,
                batch_id,
                as_of,
                first_observed_at=kwargs.get("first_observed_at") or as_of,
            )
        )
        return LeaderLiveCandidateCollectionSources(
            security_master_loader=lambda run_id, batch_id, as_of: SourceBatch(
                meta=_meta(
                    run_id,
                    batch_id,
                    "official_exchange_security_master",
                    as_of,
                    len(securities),
                ),
                items=list(securities),
            ),
            classification_loader=classification_source,
            quote_loader=quote_source,
            index_loader=lambda run_id, batch_id, as_of: _index_batch(
                run_id, batch_id, as_of
            ),
            classification_release_loader=classification_release_source,
        )

    def sources_with_singleton(self):
        base = self.sources()
        singleton_symbol = "300021"

        def security_loader(run_id, batch_id, as_of):
            items = [
                _security("000001"),
                _security("000002"),
                _security(singleton_symbol),
            ]
            return SourceBatch(
                meta=_meta(
                    run_id,
                    batch_id,
                    "official_exchange_security_master",
                    as_of,
                    len(items),
                ),
                items=items,
            )

        def classification_loader(
            run_id,
            batch_id,
            as_of,
            records=None,
            **kwargs,
        ):
            del records
            snapshot = _classification(
                run_id,
                batch_id,
                as_of,
                first_observed_at=(
                    kwargs.get("first_observed_at") or as_of
                ),
            )
            singleton = snapshot.records[0].model_copy(update={
                "source_symbol": singleton_symbol,
                "source_name": f"证券{singleton_symbol}",
                "security_identity": singleton_symbol,
                "division_code": "02",
                "division_name": "林业",
            })
            all_records = [*snapshot.records, singleton]
            return snapshot.model_copy(update={
                "meta": _meta(
                    run_id,
                    batch_id,
                    "capco_industry_classification",
                    as_of,
                    len(all_records),
                ),
                "release": snapshot.release.model_copy(update={
                    "source_record_count": len(all_records),
                    "unique_source_symbol_count": len(all_records),
                }),
                "records": all_records,
                "completeness": snapshot.completeness.model_copy(update={
                    "source_record_count": len(all_records),
                    "unique_source_symbol_count": len(all_records),
                    "current_master_count": len(all_records),
                    "mapped_count": len(all_records),
                }),
            })

        return LeaderLiveCandidateCollectionSources(
            security_master_loader=security_loader,
            classification_loader=classification_loader,
            quote_loader=base.quote_loader,
            index_loader=base.index_loader,
            classification_release_loader=base.classification_release_loader,
        )

    def _run(self, sources=None, clock_values=(DISCOVERY_AS_OF, COLLECTION_AS_OF)):
        return collect_leader_live_candidate_batch(
            self.request(),
            sources or self.sources(),
            clock=iter(clock_values).__next__,
        )

    def test_candidate_as_of_freezes_after_slow_source_collection(self):
        completed_at = COLLECTION_AS_OF + timedelta(seconds=6)

        def slow_quotes(symbols, run_id, batch_id, as_of):
            return SourceBatch(
                meta=RadarBatchMeta(
                    radarRunId=run_id,
                    batchId=batch_id,
                    source="tencent_finance",
                    asOf=as_of,
                    sourceTime=completed_at,
                    fetchedAt=completed_at,
                    expectedCount=len(symbols),
                    returnedCount=len(symbols),
                    rowCoverage=1.0,
                    requiredFieldCoverage={
                        "price": 1.0,
                        "source_time": 1.0,
                        "change_percent": 1.0,
                        "turnover_amount_source": 1.0,
                    },
                    issues=[],
                ),
                items=[_quote(symbol, completed_at, 2.0 if symbol == "000001" else 1.0) for symbol in symbols],
            )

        result = self._run(self.sources(quote_source=slow_quotes))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.READY)
        self.assertEqual(result.as_of, completed_at)
        self.assertIsNotNone(result.runtime_inputs)
        self.assertEqual(result.runtime_inputs.quote_batch.meta.as_of, completed_at)
        self.assertEqual(result.candidate_plan.as_of, completed_at)

    def test_ready_batch_freezes_completion_time_and_hides_raw_payload(self):
        result = self._run()
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.READY)
        self.assertEqual(result.discovery_as_of, DISCOVERY_AS_OF)
        self.assertEqual(result.as_of, COLLECTION_COMPLETED_AT)
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(
            result.runtime_inputs.classification_snapshot.meta.as_of,
            result.as_of,
        )
        self.assertEqual(
            result.runtime_inputs.index_batch.meta.as_of,
            result.as_of,
        )
        self.assertEqual(result.runtime_inputs.etf_symbols, ())
        evidence = result.to_evidence()
        self.assertEqual(evidence["candidateCount"], 2)
        self.assertTrue(evidence["runtimeInputsReady"])
        self.assertNotIn("000001", repr(evidence))
        self.assertNotIn("QuoteSnapshot", repr(evidence))
        self.assertFalse(result.formal_usable)

    def test_unusable_single_member_sector_is_not_frozen_into_candidate_plan(self):
        singleton_symbol = "300021"
        result = self._run(self.sources_with_singleton())

        self.assertEqual(
            result.status,
            LeaderLiveCandidateCollectionStatus.READY,
            result.reasons,
        )
        self.assertEqual(result.candidate_count, 2)
        self.assertNotIn(
            singleton_symbol,
            tuple(item.symbol for item in result.candidate_plan.items),
        )
        sector_by_code = {
            item.division_code: item
            for item in result.runtime_inputs.sector_feature_batch.sectors
        }
        self.assertFalse(sector_by_code["02"].shadow_usable)

    def test_unconfirmed_stock_unit_gap_does_not_taint_mapped_sector_units(self):
        base = self.sources()
        gap_symbol = "000003"

        def security_loader(run_id, batch_id, as_of):
            items = [_security(symbol) for symbol in (
                "000001", "000002", gap_symbol
            )]
            return SourceBatch(
                meta=_meta(
                    run_id,
                    batch_id,
                    "official_exchange_security_master",
                    as_of,
                    len(items),
                ),
                items=items,
            )

        def classification_loader(
            run_id, batch_id, as_of, records=None, **kwargs
        ):
            del records
            snapshot = _classification(
                run_id,
                batch_id,
                as_of,
                first_observed_at=(
                    kwargs.get("first_observed_at") or as_of
                ),
            )
            return snapshot.model_copy(update={
                "status": SourceStatus.DEGRADED,
                "current_master_gaps": [IndustryClassificationGap(
                    securityIdentity=gap_symbol,
                    symbol=gap_symbol,
                    name=f"证券{gap_symbol}",
                    listingDate=date(2026, 8, 1),
                    issueCodes=("new_listing_after_classification_start",),
                )],
                "completeness": snapshot.completeness.model_copy(update={
                    "current_master_count": 3,
                    "unconfirmed_count": 1,
                    "mapping_coverage": 2 / 3,
                    "reasons": ("classification_mapping_incomplete",),
                }),
            })

        def quote_loader(symbols, run_id, batch_id, as_of):
            items = []
            for symbol in symbols:
                quote = _quote(symbol, as_of)
                if symbol != gap_symbol:
                    quote = quote.model_copy(update={
                        "turnover_amount_cny": 1_000_000.0,
                        "turnover_amount_unit_status": (
                            UnitVerificationStatus.VERIFIED
                        ),
                        "market_cap_cny": 10_000_000_000.0,
                        "market_cap_unit_status": (
                            UnitVerificationStatus.VERIFIED
                        ),
                        "total_shares_source": 1_000_000_000.0,
                        "currency": "CNY",
                    })
                items.append(quote)
            return SourceBatch(
                meta=_meta(
                    run_id,
                    batch_id,
                    "tencent_finance",
                    as_of,
                    len(items),
                    source_time=as_of,
                ),
                items=items,
            )

        sources = LeaderLiveCandidateCollectionSources(
            security_master_loader=security_loader,
            classification_loader=classification_loader,
            quote_loader=quote_loader,
            index_loader=base.index_loader,
            classification_release_loader=base.classification_release_loader,
        )

        result = self._run(sources)

        self.assertEqual(
            result.status,
            LeaderLiveCandidateCollectionStatus.READY,
            result.reasons,
        )
        sector = result.runtime_inputs.sector_feature_batch.sectors[0]
        self.assertEqual(
            sector.returns.market_cap_unit_status,
            UnitVerificationStatus.VERIFIED,
        )
        self.assertEqual(
            sector.turnover.unit_status,
            UnitVerificationStatus.VERIFIED,
        )

    def test_release_identity_drift_blocks_before_quote_collection(self):
        quote_calls = []

        def classification_loader(run_id, batch_id, as_of, records=None, **kwargs):
            return _classification(
                run_id,
                batch_id,
                as_of,
                first_observed_at=kwargs.get("first_observed_at") or as_of,
                sha="b" * 64 if batch_id.endswith("collection-1") else "a" * 64,
            )

        def quote_loader(*args):
            quote_calls.append(args)
            return self.sources().quote_loader(*args)

        result = self._run(self.sources(quote_source=quote_loader, classification_source=classification_loader))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.NOT_READY)
        self.assertIn("classification_release_identity_mismatch", result.reasons)
        self.assertEqual(quote_calls, [])

    def test_first_observation_may_follow_discovery_but_precedes_collection(self):
        first_observed_at = DISCOVERY_AS_OF + timedelta(seconds=1)

        def classification_loader(run_id, batch_id, as_of, records=None, **kwargs):
            return _classification(
                run_id,
                batch_id,
                as_of,
                first_observed_at=kwargs.get("first_observed_at") or first_observed_at,
            )

        result = self._run(self.sources(classification_source=classification_loader))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.READY)
        self.assertEqual(result.runtime_inputs.as_of, COLLECTION_COMPLETED_AT)

    def test_stored_release_preserves_original_first_observation(self):
        original_first_observed_at = DISCOVERY_AS_OF - timedelta(days=21)
        stored_release = _classification(
            "stored-run",
            "stored-batch",
            DISCOVERY_AS_OF - timedelta(days=1),
            first_observed_at=original_first_observed_at,
        ).release
        release_calls = []

        def release_loader(classification_system, release_period):
            release_calls.append((classification_system, release_period))
            return stored_release

        result = self._run(self.sources(classification_release_source=release_loader))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.READY)
        normalized_first_observed_at = original_first_observed_at.astimezone(timezone.utc)
        self.assertEqual(result.runtime_inputs.industry_release.first_observed_at, normalized_first_observed_at)
        self.assertEqual(result.runtime_inputs.to_evidence()["industryFirstObservedAt"], normalized_first_observed_at.isoformat())
        self.assertEqual(release_calls, [("capco_listed_company_industry", "2025H2")])

    def test_mismatched_stored_release_blocks_before_quote_collection(self):
        quote_calls = []
        stored_release = _classification(
            "stored-run",
            "stored-batch",
            DISCOVERY_AS_OF - timedelta(days=1),
            first_observed_at=DISCOVERY_AS_OF - timedelta(days=21),
            sha="b" * 64,
        ).release

        def quote_loader(*args):
            quote_calls.append(args)
            return self.sources().quote_loader(*args)

        result = self._run(self.sources(quote_source=quote_loader, classification_release_source=lambda *_: stored_release))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.NOT_READY)
        self.assertIn("classification_observation_evidence_mismatch", result.reasons)
        self.assertEqual(quote_calls, [])

    def test_stored_release_loader_failure_is_redacted(self):
        def fail(*_):
            raise RuntimeError("secret repository detail")

        result = self._run(self.sources(classification_release_source=fail))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.SOURCE_FAILED)
        self.assertIn("classification_observation_evidence_failed", result.reasons)
        self.assertNotIn("secret repository detail", repr(result.to_evidence()))

    def test_unhealthy_quotes_close_batch(self):
        def unhealthy(symbols, run_id, batch_id, as_of):
            return SourceBatch(
                meta=_meta(run_id, batch_id, "tencent_finance", as_of, 0, source_time=None, expected_count=len(symbols)),
                items=[],
            )

        result = self._run(self.sources(quote_source=unhealthy))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.NOT_READY)
        self.assertIn("quote_source_not_healthy", result.reasons)

    def test_source_exception_returns_stable_failure_without_detail(self):
        def fail(*_):
            raise RuntimeError("secret upstream detail")

        result = self._run(self.sources(quote_source=fail))
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.SOURCE_FAILED)
        self.assertIn("quote_source_failed", result.reasons)
        self.assertNotIn("secret upstream detail", repr(result.to_evidence()))

    def test_collection_time_must_follow_discovery_time(self):
        result = collect_leader_live_candidate_batch(
            self.request(),
            self.sources(),
            clock=lambda: DISCOVERY_AS_OF,
        )
        self.assertEqual(result.status, LeaderLiveCandidateCollectionStatus.NOT_READY)
        self.assertIn("collection_as_of_not_after_discovery", result.reasons)


class LeaderLiveCandidateCollectionRepositoryAdapterTests(unittest.TestCase):
    def test_default_sources_can_require_official_archive_verification(self):
        expected = object()
        with patch(
            "radar.leader_live_candidate_collection_batch."
            "fetch_industry_classification",
            return_value=expected,
        ) as fetcher:
            try:
                sources = build_default_leader_live_candidate_collection_sources(
                    classification_publication_page_url="https://example.com",
                    verify_official_classification_archive=True,
                )
            except TypeError as exc:
                self.fail(
                    "official classification archive verification cannot be "
                    f"required: {exc}"
                )
            result = sources.classification_loader(
                "run-1",
                "classification-1",
                DISCOVERY_AS_OF,
                (_security("000001"),),
            )

        self.assertIs(result, expected)
        self.assertTrue(fetcher.call_args.kwargs["verify_official_archive"])

    def test_repository_adapter_delegates_read_only_release_lookup(self):
        calls = []
        expected = object()

        class Repository:
            def get_industry_classification_release(self, system, period):
                calls.append((system, period))
                return expected

        loader = build_repository_classification_release_loader(Repository())
        self.assertIs(loader("capco_listed_company_industry", "2025H2"), expected)
        self.assertEqual(calls, [("capco_listed_company_industry", "2025H2")])

    def test_repository_adapter_rejects_unverified_repository(self):
        with self.assertRaisesRegex(ValueError, "leader_classification_release_repository_unverified"):
            build_repository_classification_release_loader(object())

    def test_default_sources_reject_ambiguous_release_inputs(self):
        with self.assertRaisesRegex(ValueError, "leader_classification_release_loader_ambiguous"):
            build_default_leader_live_candidate_collection_sources(
                classification_publication_page_url="https://example.com",
                classification_release_loader=lambda *_: None,
                classification_release_repository=object(),
            )


if __name__ == "__main__":
    unittest.main()
