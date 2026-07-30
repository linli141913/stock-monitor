import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_invalidation_features import (
    LeaderRiskEvidenceCoverage,
    LeaderRiskEventEvidence,
    LeaderRiskInvalidationFeatureInput,
    LeaderRiskResolutionEvidence,
    RiskCategory,
    RiskEvidenceSourceKind,
    RiskEventSubtype,
    RiskOfficialStatus,
    RiskResolutionKind,
    build_leader_risk_invalidation_features,
)


AS_OF = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
SYMBOL = "000001"
ISSUER_IDENTITY = "issuer-cn-000001"


def make_coverage(
    *,
    covered_categories=tuple(RiskCategory),
    coverage_complete=True,
):
    return LeaderRiskEvidenceCoverage(
        coverage_id="risk-coverage-20260728-000001",
        source_contract_id=(
            "radar-leader-risk-official-coverage-v1"
        ),
        adapter_contract_id=(
            "radar-leader-risk-official-coverage-adapter-v1"
        ),
        symbol=SYMBOL,
        issuer_identity=ISSUER_IDENTITY,
        issuer_listed_at=AS_OF - timedelta(days=3650),
        covered_categories=covered_categories,
        window_from=AS_OF - timedelta(days=3650),
        window_until=AS_OF - timedelta(minutes=10),
        checked_at=AS_OF - timedelta(minutes=5),
        effective_until=AS_OF + timedelta(hours=23),
        source_names=("巨潮资讯", "中国证监会"),
        source_urls=(
            "https://www.cninfo.com.cn/",
            "https://www.csrc.gov.cn/",
        ),
        coverage_complete=coverage_complete,
        open_event_carry_forward_complete=True,
        reasons=(),
    )


def make_event(
    category=RiskCategory.INVESTIGATION,
    *,
    suffix=None,
    effective_until=None,
    reporting_period=None,
    official_status=RiskOfficialStatus.ACTIVE,
    event_subtype=None,
    case_id=None,
):
    suffix = suffix or category.value
    subtype_by_category = {
        RiskCategory.REDUCTION: RiskEventSubtype.REDUCTION_PLAN,
        RiskCategory.UNLOCK: RiskEventSubtype.SHARE_UNLOCK,
        RiskCategory.REGULATORY: (
            RiskEventSubtype.REGULATORY_MEASURE
        ),
        RiskCategory.INVESTIGATION: (
            RiskEventSubtype.FORMAL_INVESTIGATION
        ),
        RiskCategory.LITIGATION: (
            RiskEventSubtype.MATERIAL_LITIGATION
        ),
        RiskCategory.EARNINGS: RiskEventSubtype.EARNINGS_LOSS,
        RiskCategory.AUDIT: RiskEventSubtype.AUDIT_QUALIFIED,
    }
    if event_subtype is None:
        event_subtype = subtype_by_category[category]
    if (
        case_id is None
        and category in {
            RiskCategory.INVESTIGATION,
            RiskCategory.LITIGATION,
        }
    ):
        case_id = f"official-case-{suffix}"
    return LeaderRiskEventEvidence(
        event_id=f"risk-event-{suffix}",
        event_version=f"risk-event-{suffix}-v1",
        symbol=SYMBOL,
        issuer_identity=ISSUER_IDENTITY,
        category=category,
        event_subtype=event_subtype,
        case_id=case_id,
        source_kind=RiskEvidenceSourceKind.COMPANY_DISCLOSURE,
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            f"2026-07-27/risk-event-{suffix}.PDF"
        ),
        document_id=f"risk-document-{suffix}",
        published_at=AS_OF - DAY,
        effective_from=AS_OF - DAY,
        effective_until=effective_until,
        reporting_period=reporting_period,
        fact_summary=f"{category.value}完整事实摘要不应进入输出",
        official_status=official_status,
    )


def make_resolution(
    target_event_id,
    *,
    suffix="1",
    resolution_kind=RiskResolutionKind.OFFICIALLY_CLEARED,
):
    return LeaderRiskResolutionEvidence(
        resolution_id=f"risk-resolution-{suffix}",
        resolution_version=f"risk-resolution-{suffix}-v1",
        target_event_id=target_event_id,
        target_event_version=(
            f"{target_event_id}-v1"
        ),
        symbol=SYMBOL,
        issuer_identity=ISSUER_IDENTITY,
        resolution_kind=resolution_kind,
        source_kind=RiskEvidenceSourceKind.REGULATOR_DISCLOSURE,
        source_name="中国证监会",
        source_url=(
            "https://www.csrc.gov.cn/resolution/"
            f"risk-resolution-{suffix}.html"
        ),
        document_id=f"risk-resolution-document-{suffix}",
        published_at=AS_OF - HOUR,
        effective_from=AS_OF - HOUR,
        resolution_summary="完整解除事实摘要不应进入输出",
    )


def make_input(
    *,
    coverage=None,
    events=(),
    resolutions=(),
    source_status=ResearchFeatureStatus.READY,
):
    return LeaderRiskInvalidationFeatureInput(
        as_of=AS_OF,
        symbol=SYMBOL,
        issuer_identity=ISSUER_IDENTITY,
        coverage=coverage or make_coverage(),
        events=events,
        resolutions=resolutions,
        source_status=source_status,
    )


class LeaderRiskInvalidationFeatureTests(unittest.TestCase):
    def test_complete_coverage_can_report_no_active_risk(self):
        result = build_leader_risk_invalidation_features(make_input())
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.coverage_state, "complete")
        self.assertTrue(result.no_active_risk_observed)
        self.assertEqual(result.active_risk_categories, ())
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertIsNone(evidence["researchScore"])

    def test_empty_events_without_all_categories_never_claims_no_risk(
        self,
    ):
        coverage = make_coverage(
            covered_categories=(RiskCategory.REDUCTION,),
        )

        result = build_leader_risk_invalidation_features(
            make_input(coverage=coverage)
        )

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertFalse(result.no_active_risk_observed)
        self.assertIn(
            "risk_coverage_categories_incomplete",
            result.reasons,
        )

    def test_source_failure_has_priority_over_coverage_validation(self):
        coverage = replace(
            make_coverage(),
            source_urls=("http://invalid.example/risk",),
        )

        result = build_leader_risk_invalidation_features(
            make_input(
                coverage=coverage,
                source_status=ResearchFeatureStatus.SOURCE_FAILED,
            )
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_source_source_failed",),
        )

    def test_expired_coverage_is_stale(self):
        coverage = replace(
            make_coverage(),
            effective_until=AS_OF - timedelta(seconds=1),
        )

        result = build_leader_risk_invalidation_features(
            make_input(coverage=coverage)
        )

        self.assertEqual(result.status, ResearchFeatureStatus.STALE)
        self.assertEqual(result.coverage_state, "stale")
        self.assertIn("risk_coverage_expired", result.reasons)

    def test_all_seven_official_risk_categories_are_preserved(self):
        events = (
            make_event(
                RiskCategory.REDUCTION,
                effective_until=AS_OF + DAY,
            ),
            make_event(
                RiskCategory.UNLOCK,
                effective_until=AS_OF + DAY,
            ),
            make_event(RiskCategory.REGULATORY),
            make_event(RiskCategory.INVESTIGATION),
            make_event(RiskCategory.LITIGATION),
            make_event(
                RiskCategory.EARNINGS,
                reporting_period="2026Q2",
            ),
            make_event(
                RiskCategory.AUDIT,
                reporting_period="2025FY",
            ),
        )

        result = build_leader_risk_invalidation_features(
            make_input(events=events)
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            set(result.active_risk_categories),
            set(RiskCategory),
        )
        self.assertFalse(result.no_active_risk_observed)

    def test_category_specific_required_fields_are_enforced(self):
        cases = (
            (
                make_event(RiskCategory.REDUCTION),
                "risk_reduction_effective_until_missing",
            ),
            (
                make_event(RiskCategory.UNLOCK),
                "risk_unlock_effective_until_missing",
            ),
            (
                make_event(RiskCategory.EARNINGS),
                "risk_earnings_reporting_period_missing",
            ),
            (
                make_event(RiskCategory.AUDIT),
                "risk_audit_reporting_period_missing",
            ),
        )

        for event, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(events=(event,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_event_contract_rejects_unverified_inputs(self):
        event = make_event(RiskCategory.INVESTIGATION)
        cases = (
            (
                replace(event, source_url="http://example.test/risk"),
                "risk_event_source_url_unverified",
            ),
            (
                replace(
                    event,
                    published_at=AS_OF + timedelta(seconds=1),
                ),
                "risk_event_published_in_future",
            ),
            (
                replace(event, symbol="000002"),
                "risk_event_symbol_mismatch",
            ),
            (
                replace(event, issuer_identity="issuer-other"),
                "risk_event_issuer_mismatch",
            ),
            (
                replace(
                    event,
                    effective_from=event.effective_from.replace(
                        tzinfo=None
                    ),
                ),
                "risk_event_timestamp_timezone_missing",
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(events=(candidate,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_expired_or_officially_closed_events_are_not_active(self):
        expired = make_event(
            RiskCategory.REDUCTION,
            suffix="expired",
            effective_until=AS_OF - timedelta(seconds=1),
        )
        completed = make_event(
            RiskCategory.REGULATORY,
            suffix="completed",
            official_status=RiskOfficialStatus.COMPLETED,
        )

        result = build_leader_risk_invalidation_features(
            make_input(events=(expired, completed))
        )
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.active_risk_categories, ())
        self.assertTrue(result.no_active_risk_observed)
        self.assertEqual(
            sum(
                item["kind"] == "risk_event"
                for item in evidence["references"]
            ),
            2,
        )

    def test_open_ended_risks_remain_active_without_resolution(self):
        for category in (
            RiskCategory.REGULATORY,
            RiskCategory.INVESTIGATION,
            RiskCategory.LITIGATION,
        ):
            with self.subTest(category=category.value):
                result = build_leader_risk_invalidation_features(
                    make_input(events=(make_event(category),))
                )
                self.assertEqual(
                    result.active_risk_categories,
                    (category,),
                )
                self.assertFalse(result.no_active_risk_observed)

    def test_matching_official_resolution_closes_open_ended_event(self):
        event = make_event(RiskCategory.INVESTIGATION)
        resolution = make_resolution(event.event_id)

        result = build_leader_risk_invalidation_features(
            make_input(
                events=(event,),
                resolutions=(resolution,),
            )
        )
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.active_risk_categories, ())
        self.assertTrue(result.no_active_risk_observed)
        self.assertTrue(any(
            item["kind"] == "risk_resolution"
            for item in evidence["references"]
        ))

    def test_invalid_resolution_never_silently_closes_event(self):
        event = make_event(RiskCategory.INVESTIGATION)
        resolution = make_resolution(event.event_id)
        cases = (
            (
                replace(
                    resolution,
                    target_event_id="missing-event",
                ),
                "risk_resolution_target_missing",
            ),
            (
                replace(resolution, symbol="000002"),
                "risk_resolution_symbol_mismatch",
            ),
            (
                replace(
                    resolution,
                    published_at=event.published_at - timedelta(
                        seconds=1
                    ),
                ),
                "risk_resolution_precedes_event",
            ),
            (
                replace(
                    resolution,
                    effective_from=AS_OF + timedelta(seconds=1),
                ),
                "risk_resolution_effective_in_future",
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(
                        events=(event,),
                        resolutions=(candidate,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_duplicate_and_conflicting_versions_are_rejected(self):
        event = make_event(RiskCategory.INVESTIGATION)
        cases = (
            (
                (event, event),
                (),
                "risk_event_identity_duplicate",
            ),
            (
                (
                    event,
                    replace(
                        event,
                        event_version="risk-event-investigation-v2",
                        official_status=RiskOfficialStatus.COMPLETED,
                    ),
                ),
                (),
                "risk_event_version_conflict",
            ),
            (
                (
                    event,
                    replace(
                        make_event(
                            RiskCategory.LITIGATION,
                            suffix="litigation-other",
                        ),
                        document_id=event.document_id,
                    ),
                ),
                (),
                "risk_document_identity_duplicate",
            ),
            (
                (event,),
                (
                    make_resolution(event.event_id, suffix="1"),
                    make_resolution(
                        event.event_id,
                        suffix="2",
                        resolution_kind=RiskResolutionKind.CORRECTED,
                    ),
                ),
                "risk_resolution_version_conflict",
            ),
        )

        for events, resolutions, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(
                        events=events,
                        resolutions=resolutions,
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_coverage_requires_listing_history_and_bounded_freshness(
        self,
    ):
        coverage = make_coverage()
        cases = (
            (
                replace(
                    coverage,
                    window_from=coverage.issuer_listed_at + DAY,
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "risk_coverage_history_incomplete",
            ),
            (
                replace(
                    coverage,
                    window_until=AS_OF - timedelta(hours=25),
                    checked_at=AS_OF - HOUR,
                ),
                ResearchFeatureStatus.STALE,
                "risk_coverage_window_stale",
            ),
            (
                replace(
                    coverage,
                    effective_until=(
                        coverage.checked_at + timedelta(hours=25)
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "risk_coverage_effective_window_unbounded",
            ),
            (
                replace(
                    coverage,
                    open_event_carry_forward_complete=False,
                ),
                ResearchFeatureStatus.MISSING,
                "risk_coverage_carry_forward_incomplete",
            ),
        )

        for candidate, expected_status, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(coverage=candidate)
                )
                self.assertEqual(result.status, expected_status)
                self.assertFalse(result.no_active_risk_observed)
                self.assertIn(expected_reason, result.reasons)

    def test_unregistered_source_contract_or_domain_is_rejected(self):
        coverage = make_coverage()
        event = make_event(RiskCategory.INVESTIGATION)
        cases = (
            (
                replace(
                    coverage,
                    source_contract_id="self-reported-contract",
                ),
                (),
                "risk_coverage_source_contract_unverified",
            ),
            (
                replace(
                    coverage,
                    source_urls=("https://attacker.example/",),
                    source_names=("中国证监会",),
                ),
                (),
                "risk_coverage_source_domain_untrusted",
            ),
            (
                coverage,
                (
                    replace(
                        event,
                        source_url=(
                            "https://attacker.example/risk.pdf"
                        ),
                    ),
                ),
                "risk_event_source_domain_untrusted",
            ),
        )

        for candidate_coverage, events, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(
                        coverage=candidate_coverage,
                        events=events,
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_correction_never_closes_risk_without_replacement_version(
        self,
    ):
        event = make_event(RiskCategory.INVESTIGATION)
        cases = (
            (
                replace(
                    event,
                    official_status=RiskOfficialStatus.CORRECTED,
                ),
                (),
                "risk_event_correction_replacement_missing",
            ),
            (
                event,
                (
                    make_resolution(
                        event.event_id,
                        resolution_kind=RiskResolutionKind.CORRECTED,
                    ),
                ),
                "risk_resolution_correction_replacement_missing",
            ),
        )

        for candidate_event, resolutions, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(
                        events=(candidate_event,),
                        resolutions=resolutions,
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.no_active_risk_observed)
                self.assertIn(expected_reason, result.reasons)

    def test_resolution_targets_exact_version_and_distinct_document(
        self,
    ):
        event = make_event(RiskCategory.INVESTIGATION)
        resolution = make_resolution(event.event_id)
        cases = (
            (
                replace(
                    resolution,
                    target_event_version="missing-version",
                ),
                "risk_resolution_target_version_missing",
            ),
            (
                replace(
                    resolution,
                    document_id=event.document_id,
                ),
                "risk_document_identity_cross_duplicate",
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(
                        events=(event,),
                        resolutions=(candidate,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_category_subtype_and_case_identity_are_structured(self):
        cases = (
            (
                make_event(
                    RiskCategory.LITIGATION,
                    case_id="",
                ),
                "risk_litigation_case_id_missing",
            ),
            (
                make_event(
                    RiskCategory.INVESTIGATION,
                    case_id="",
                ),
                "risk_investigation_case_id_missing",
            ),
            (
                make_event(
                    RiskCategory.REGULATORY,
                    event_subtype=RiskEventSubtype.EARNINGS_LOSS,
                ),
                "risk_event_subtype_category_mismatch",
            ),
            (
                make_event(
                    RiskCategory.AUDIT,
                    event_subtype=(
                        RiskEventSubtype.REGULATORY_MEASURE
                    ),
                    reporting_period="2025FY",
                ),
                "risk_event_subtype_category_mismatch",
            ),
        )

        for event, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(events=(event,))
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(expected_reason, result.reasons)

    def test_public_urls_reject_credentials_and_strip_query_data(self):
        event = make_event(RiskCategory.INVESTIGATION)
        credentialed = replace(
            event,
            source_url=(
                "https://user:password@static.cninfo.com.cn/"
                "risk.pdf?name=private"
            ),
        )

        rejected = build_leader_risk_invalidation_features(
            make_input(events=(credentialed,))
        )

        self.assertEqual(
            rejected.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "risk_event_source_url_credentials_forbidden",
            rejected.reasons,
        )

        query_url = (
            "https://static.cninfo.com.cn/risk.pdf"
            "?name=private&id=credential#fragment"
        )
        accepted = build_leader_risk_invalidation_features(
            make_input(events=(
                replace(event, source_url=query_url),
            ))
        ).to_evidence()

        self.assertEqual(accepted["status"], "ready")
        self.assertNotIn("?name=", str(accepted))
        self.assertNotIn("credential", str(accepted))
        self.assertNotIn("#fragment", str(accepted))
        self.assertNotIn(event.document_id, str(accepted))

    def test_missing_collections_and_invalid_source_status_are_stable(
        self,
    ):
        cases = (
            (
                replace(make_input(), events=None),
                ResearchFeatureStatus.MISSING,
                "risk_events_missing",
            ),
            (
                replace(make_input(), resolutions=None),
                ResearchFeatureStatus.MISSING,
                "risk_resolutions_missing",
            ),
            (
                make_input(coverage=replace(
                    make_coverage(),
                    covered_categories=None,
                )),
                ResearchFeatureStatus.MISSING,
                "risk_coverage_categories_missing",
            ),
            (
                replace(make_input(), source_status="ready"),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "risk_source_status_unverified",
            ),
        )

        for candidate, expected_status, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    candidate
                )
                self.assertEqual(result.status, expected_status)
                self.assertFalse(result.no_active_risk_observed)
                self.assertIn(expected_reason, result.reasons)

    def test_invalid_nested_field_types_return_stable_status(self):
        event = make_event(RiskCategory.INVESTIGATION)
        resolution = make_resolution(event.event_id)
        cases = (
            (
                make_input(events=(
                    replace(event, fact_summary=123),
                )),
                "risk_event_identity_missing",
            ),
            (
                make_input(
                    events=(event,),
                    resolutions=(
                        replace(
                            resolution,
                            resolution_summary=123,
                        ),
                    ),
                ),
                "risk_resolution_identity_missing",
            ),
            (
                make_input(events=(
                    replace(event, category=[]),
                )),
                "risk_event_category_unverified",
            ),
            (
                make_input(events=(
                    replace(event, event_id=[]),
                )),
                "risk_event_identity_missing",
            ),
            (
                make_input(
                    events=(event,),
                    resolutions=(
                        replace(
                            resolution,
                            resolution_kind=[],
                        ),
                    ),
                ),
                "risk_resolution_kind_unverified",
            ),
            (
                make_input(
                    events=(event,),
                    resolutions=(
                        replace(
                            resolution,
                            target_event_id=[],
                        ),
                    ),
                ),
                "risk_resolution_identity_missing",
            ),
            (
                make_input(
                    events=(event,),
                    resolutions=(
                        replace(
                            resolution,
                            document_id=[],
                        ),
                    ),
                ),
                "risk_resolution_identity_missing",
            ),
            (
                make_input(
                    coverage=replace(
                        make_coverage(),
                        source_urls=([],),
                        source_names=("巨潮资讯",),
                    ),
                ),
                "risk_coverage_source_url_unverified",
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    candidate
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.no_active_risk_observed)
                self.assertIn(expected_reason, result.reasons)

    def test_coverage_completion_flags_require_real_booleans(self):
        cases = (
            (
                replace(
                    make_coverage(),
                    coverage_complete="true",
                ),
                "risk_coverage_complete_flag_unverified",
            ),
            (
                replace(
                    make_coverage(),
                    open_event_carry_forward_complete="true",
                ),
                "risk_coverage_carry_forward_flag_unverified",
            ),
        )

        for coverage, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = build_leader_risk_invalidation_features(
                    make_input(coverage=coverage)
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertFalse(result.no_active_risk_observed)
                self.assertIn(expected_reason, result.reasons)

    def test_output_is_compressed_and_never_contains_full_summaries(self):
        event = make_event(RiskCategory.INVESTIGATION)
        resolution = make_resolution(event.event_id)

        evidence = build_leader_risk_invalidation_features(
            make_input(
                events=(event,),
                resolutions=(resolution,),
            )
        ).to_evidence()
        serialized = str(evidence)

        self.assertNotIn(event.fact_summary, serialized)
        self.assertNotIn(resolution.resolution_summary, serialized)
        self.assertNotIn("severity", serialized.lower())
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertIsNone(evidence["researchScore"])


if __name__ == "__main__":
    unittest.main()
