import unittest
from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

from radar.leader_risk_invalidation_features import RiskCategory
from radar.sources.leader_risk_candidate_discovery import (
    LeaderRiskOfficialCandidateDiscoveryStatus,
    build_leader_risk_official_candidate_discovery_batch,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    CninfoRiskDiscoveryQuery,
    CninfoRiskIssuerScope,
    OfficialRiskDiscoveryBatch,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
)
from tests import (
    test_radar_leader_research_single_pass_orchestration as f6_helpers,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def document(
    *,
    symbol,
    category,
    document_id,
    issuer_identity=None,
    published_at,
):
    raw_id = document_id.removeprefix("cninfo:")
    return OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=document_id,
        symbol=symbol,
        issuer_identity=(
            issuer_identity or f"cninfo-org:{symbol}"
        ),
        issuer_name=f"发行人{symbol}",
        title=f"官方风险公告{document_id}",
        published_at=published_at,
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            f"2026-07-19/{raw_id}.PDF"
        ),
        candidate_category=category,
    )


class LeaderRiskOfficialCandidateDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.f6 = f6_helpers.LeaderResearchSinglePassOrchestrationTests(
            methodName=(
                "test_candidate_plan_is_frozen_ordered_and_"
                "matches_existing_runtime"
            )
        )
        self.f6.setUp()
        self.plan = self.f6.candidate_plan()
        self.trade_date = self.plan.as_of.astimezone(
            SHANGHAI_TZ
        ).date()

    def batch(
        self,
        category,
        *documents,
        search_key=None,
        status=OfficialRiskSourceStatus.PARTIAL,
        page_number=1,
        reasons=("cninfo_keyword_discovery_not_coverage_proof",),
        candidate_scopes=None,
    ):
        resolved_candidate_scopes = (
            self.candidate_scopes()
            if candidate_scopes is None
            else candidate_scopes
        )
        source_failed = status == OfficialRiskSourceStatus.SOURCE_FAILED
        return OfficialRiskDiscoveryBatch(
            status=status,
            query=CninfoRiskDiscoveryQuery(
                search_key=search_key or category.value,
                candidate_category=category,
                window_from=self.trade_date - timedelta(days=365),
                window_until=self.trade_date,
                page_number=page_number,
                page_size=30,
                candidate_scopes=resolved_candidate_scopes,
                candidate_plan_id=(
                    self.plan.candidate_set_id
                    if resolved_candidate_scopes
                    else None
                ),
                shard_index=(0 if resolved_candidate_scopes else None),
                shard_count=(1 if resolved_candidate_scopes else None),
            ),
            fetched_at=self.plan.as_of,
            total_records=None if source_failed else len(documents),
            total_pages=(
                None if source_failed else (1 if documents else 0)
            ),
            reported_total_pages=(
                None if source_failed else (1 if documents else 0)
            ),
            has_more=None if source_failed else False,
            documents=tuple(documents),
            reasons=reasons,
        )

    def test_partial_batch_requires_original_reported_page_count(self):
        batch = replace(
            self.batch(RiskCategory.AUDIT),
            reported_total_pages=None,
        )

        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(batch,),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )

    def test_unscoped_batch_cannot_claim_frozen_candidate_coverage(self):
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.AUDIT,
                candidate_scopes=(),
            ),),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )
        self.assertIn(
            "risk_official_candidate_discovery_scope_mismatch",
            result.reasons,
        )

    def candidate_scopes(self):
        return tuple(
            CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=f"cninfo-org:{item.symbol}",
                resolved_at=self.plan.as_of,
            )
            for item in self.plan.items
        )

    def test_scoped_batches_must_match_the_frozen_candidate_plan(self):
        scopes = self.candidate_scopes()
        symbol = self.plan.items[0].symbol
        valid = self.batch(
            RiskCategory.AUDIT,
            document(
                symbol=symbol,
                category=RiskCategory.AUDIT,
                document_id="cninfo:scoped",
                published_at=self.plan.as_of - timedelta(days=1),
            ),
            candidate_scopes=scopes,
        )

        accepted = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(valid,),
        )
        mismatched = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(
                valid,
                self.batch(
                    RiskCategory.REDUCTION,
                    candidate_scopes=tuple(reversed(scopes)),
                ),
            ),
        )

        self.assertEqual(
            accepted.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
        )
        self.assertEqual(
            mismatched.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )
        self.assertIn(
            "risk_official_candidate_discovery_scope_mismatch",
            mismatched.reasons,
        )

    def test_scoped_batches_allow_multiple_non_overlapping_date_windows(self):
        original = self.batch(RiskCategory.AUDIT)
        midpoint = self.trade_date - timedelta(days=180)
        first = replace(
            original,
            query=replace(
                original.query,
                window_until=midpoint - timedelta(days=1),
            ),
        )
        second = replace(
            original,
            query=replace(
                original.query,
                window_from=midpoint,
            ),
        )

        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(first, second),
        )

        self.assertNotEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )
        self.assertNotIn(
            "risk_official_candidate_discovery_scope_mismatch",
            result.reasons,
        )

    def test_scoped_batch_rejects_document_outside_issuer_scope(self):
        scopes = self.candidate_scopes()
        batch = self.batch(
            RiskCategory.INVESTIGATION,
            document(
                symbol=self.plan.items[0].symbol,
                issuer_identity="cninfo-org:wrongissuer",
                category=RiskCategory.INVESTIGATION,
                document_id="cninfo:wrong-issuer",
                published_at=self.plan.as_of - timedelta(days=1),
            ),
            candidate_scopes=scopes,
        )

        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(batch,),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )

    def test_official_html_document_is_preserved_by_candidate_merge(self):
        symbol = self.plan.items[0].symbol
        html_document = replace(
            document(
                symbol=symbol,
                category=RiskCategory.LITIGATION,
                document_id="cninfo:official-html",
                published_at=self.plan.as_of - timedelta(days=1),
            ),
            source_url=(
                "https://static.cninfo.com.cn/finalpage/"
                "2026-07-19/official-html.html"
            ),
        )

        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.LITIGATION,
                html_document,
            ),),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
        )
        self.assertEqual(
            result.documents_by_symbol[symbol][0].document_id,
            "cninfo:official-html",
        )

    def test_malformed_scope_fields_are_blocked_without_exception(self):
        scopes = self.candidate_scopes()
        for malformed in (
            replace(scopes[0], symbol=300081),
            replace(scopes[0], issuer_identity=9900012108),
        ):
            with self.subTest(scope=malformed):
                result = build_leader_risk_official_candidate_discovery_batch(
                    candidate_plan=self.plan,
                    batches=(self.batch(
                        RiskCategory.AUDIT,
                        candidate_scopes=(malformed, *scopes[1:]),
                    ),),
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
                )

    def test_candidate_order_is_preserved_for_scoped_documents(self):
        first = self.plan.items[0].symbol
        third = self.plan.items[2].symbol
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(
                self.batch(
                    RiskCategory.INVESTIGATION,
                    document(
                        symbol=first,
                        category=RiskCategory.INVESTIGATION,
                        document_id="cninfo:doc-1",
                        published_at=self.plan.as_of - timedelta(days=1),
                    ),
                ),
                self.batch(
                    RiskCategory.AUDIT,
                    document(
                        symbol=third,
                        category=RiskCategory.AUDIT,
                        document_id="cninfo:doc-3",
                        published_at=self.plan.as_of - timedelta(days=2),
                    ),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            tuple(item.symbol for item in self.plan.items),
        )
        self.assertEqual(tuple(result.documents_by_symbol), (first, third))
        self.assertEqual(result.ignored_document_count, 0)
        self.assertFalse(result.coverage_complete)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        with self.assertRaises(TypeError):
            result.documents_by_symbol["000999"] = ()

    def test_same_document_across_keywords_is_merged_without_losing_context(self):
        symbol = self.plan.items[0].symbol
        base = document(
            symbol=symbol,
            category=RiskCategory.REGULATORY,
            document_id="cninfo:shared-doc",
            published_at=self.plan.as_of - timedelta(days=1),
        )
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(
                self.batch(
                    RiskCategory.REGULATORY,
                    base,
                    search_key="监管措施决定书",
                ),
                self.batch(
                    RiskCategory.INVESTIGATION,
                    replace(
                        base,
                        candidate_category=RiskCategory.INVESTIGATION,
                    ),
                    search_key="立案告知书",
                ),
            ),
        )

        merged = result.documents_by_symbol[symbol][0]
        self.assertEqual(len(result.documents_by_symbol[symbol]), 1)
        self.assertEqual(
            merged.candidate_categories,
            (RiskCategory.REGULATORY, RiskCategory.INVESTIGATION),
        )
        self.assertEqual(
            merged.matched_query_keys,
            ("监管措施决定书", "立案告知书"),
        )

    def test_issuer_conflict_blocks_only_affected_candidate(self):
        first = self.plan.items[0].symbol
        second = self.plan.items[1].symbol
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.LITIGATION,
                document(
                    symbol=first,
                    category=RiskCategory.LITIGATION,
                    document_id="cninfo:first-a",
                    issuer_identity="cninfo-org:first-a",
                    published_at=self.plan.as_of - timedelta(days=1),
                ),
                document(
                    symbol=first,
                    category=RiskCategory.LITIGATION,
                    document_id="cninfo:first-b",
                    issuer_identity="cninfo-org:first-b",
                    published_at=self.plan.as_of - timedelta(days=1),
                ),
                document(
                    symbol=second,
                    category=RiskCategory.LITIGATION,
                    document_id="cninfo:second",
                    published_at=self.plan.as_of - timedelta(days=1),
                ),
            ),),
        )

        self.assertEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )
        self.assertEqual(result.items, ())

    def test_source_failure_and_real_empty_result_do_not_claim_no_risk(self):
        failed = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.REDUCTION,
                status=OfficialRiskSourceStatus.SOURCE_FAILED,
                reasons=("cninfo_source_request_failed",),
            ),),
        )
        empty = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(RiskCategory.REDUCTION),),
        )

        self.assertEqual(
            failed.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED,
        )
        self.assertTrue(all(
            item.status
            == LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED
            for item in failed.items
        ))
        self.assertEqual(
            empty.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.MISSING,
        )
        self.assertFalse(empty.coverage_complete)
        self.assertEqual(
            empty.queried_categories,
            (RiskCategory.REDUCTION,),
        )
        self.assertEqual(len(empty.missing_query_categories), 6)
        self.assertIn(
            "risk_official_candidate_discovery_documents_missing",
            empty.reasons,
        )
        self.assertIn(
            "risk_official_candidate_discovery_categories_incomplete",
            empty.reasons,
        )

    def test_invalid_plan_batch_identity_and_future_data_fail_closed(self):
        symbol = self.plan.items[0].symbol
        valid_document = document(
            symbol=symbol,
            category=RiskCategory.AUDIT,
            document_id="cninfo:valid",
            published_at=self.plan.as_of - timedelta(days=1),
        )
        cases = (
            (
                replace(self.plan, contract_id="caller-plan-v1"),
                (self.batch(RiskCategory.AUDIT, valid_document),),
            ),
            (
                self.plan,
                (
                    self.batch(RiskCategory.AUDIT, valid_document),
                    self.batch(RiskCategory.AUDIT, valid_document),
                ),
            ),
            (
                self.plan,
                (replace(
                    self.batch(RiskCategory.AUDIT, valid_document),
                    fetched_at=(
                        self.plan.as_of
                        + timedelta(hours=24, seconds=1)
                    ),
                ),),
            ),
        )

        for plan, batches in cases:
            with self.subTest(batch_count=len(batches)):
                result = build_leader_risk_official_candidate_discovery_batch(
                    candidate_plan=plan,
                    batches=batches,
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
                )
                self.assertEqual(result.documents_by_symbol, {})

    def test_collection_one_second_after_plan_is_not_future_data(self):
        symbol = self.plan.items[0].symbol
        valid_document = document(
            symbol=symbol,
            category=RiskCategory.AUDIT,
            document_id="cninfo:collected-after-plan",
            published_at=self.plan.as_of - timedelta(days=1),
        )
        batch = replace(
            self.batch(RiskCategory.AUDIT, valid_document),
            fetched_at=self.plan.as_of + timedelta(seconds=1),
        )

        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(batch,),
        )

        self.assertNotEqual(
            result.status,
            LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
        )

    def test_compressed_evidence_and_repr_hide_titles_and_urls(self):
        symbol = self.plan.items[0].symbol
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.EARNINGS,
                document(
                    symbol=symbol,
                    category=RiskCategory.EARNINGS,
                    document_id="cninfo:hidden",
                    published_at=self.plan.as_of - timedelta(days=1),
                ),
            ),),
        )
        evidence = result.to_evidence()

        self.assertNotIn("官方风险公告", repr(result))
        self.assertNotIn("static.cninfo.com.cn", repr(result))
        self.assertNotIn("sourceUrl", str(evidence))
        self.assertIn("cninfo:hidden", str(evidence))

    def test_unverified_page_lineage_is_preserved_on_valid_document(self):
        symbol = self.plan.items[0].symbol
        result = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=self.plan,
            batches=(self.batch(
                RiskCategory.REGULATORY,
                document(
                    symbol=symbol,
                    category=RiskCategory.REGULATORY,
                    document_id="cninfo:valid-on-dirty-page",
                    published_at=self.plan.as_of - timedelta(days=1),
                ),
                status=OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
                reasons=("cninfo_document_contract_unverified",),
            ),),
        )

        candidate_document = result.documents_by_symbol[symbol][0]
        self.assertEqual(
            candidate_document.source_statuses,
            (OfficialRiskSourceStatus.SOURCE_UNVERIFIED,),
        )
        self.assertIn(
            "risk_official_candidate_discovery_source_unverified",
            result.reasons,
        )
        self.assertIn(
            "source_unverified",
            str(result.to_evidence()),
        )

    def test_malformed_query_and_failed_batch_with_documents_are_blocked(self):
        symbol = self.plan.items[0].symbol
        valid_document = document(
            symbol=symbol,
            category=RiskCategory.AUDIT,
            document_id="cninfo:invalid-container",
            published_at=self.plan.as_of - timedelta(days=1),
        )
        malformed_query = replace(
            self.batch(RiskCategory.AUDIT, valid_document),
            query=replace(
                self.batch(RiskCategory.AUDIT).query,
                window_from="bad-date",
            ),
        )
        failed_with_document = self.batch(
            RiskCategory.AUDIT,
            valid_document,
            status=OfficialRiskSourceStatus.SOURCE_FAILED,
            reasons=("cninfo_source_request_failed",),
        )

        for batch in (malformed_query, failed_with_document):
            with self.subTest(status=batch.status):
                result = build_leader_risk_official_candidate_discovery_batch(
                    candidate_plan=self.plan,
                    batches=(batch,),
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
                )
                self.assertEqual(result.documents_by_symbol, {})


if __name__ == "__main__":
    unittest.main()
