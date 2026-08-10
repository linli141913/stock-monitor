import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_candidate_projection import (
    LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
    LeaderRiskCandidateProjectionInput,
    build_leader_risk_candidate_projection,
)
from radar.leader_risk_document_facts import (
    RiskDocumentRelationKind,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
    FormalRiskGateGap,
    RiskResearchEvidenceBundle,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
    RiskResearchEvidenceBundleAuditInput,
    audit_risk_research_evidence_bundle_versions,
)
from radar.leader_risk_invalidation_features import (
    RiskOfficialStatus,
)


UTC = timezone.utc
BASE_TIME = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
AS_OF = BASE_TIME + timedelta(hours=2)
SYMBOL = "300081"
ISSUER_IDENTITY = "cninfo-org:9900012108"
ALL_GAPS = (
    FormalRiskGateGap.SOURCE_DOCUMENT_NOT_FORMAL,
    FormalRiskGateGap.CONTENT_NOT_FORMAL,
    FormalRiskGateGap.FACT_RESULT_NOT_FORMAL,
    FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE,
    FormalRiskGateGap.RESEARCH_REPLAY_NOT_FORMAL,
    FormalRiskGateGap.RESEARCH_RELATION_NOT_FORMAL,
    FormalRiskGateGap.D3_APPLICATION_MISSING,
    FormalRiskGateGap.D1_RESOLUTION_EVIDENCE_MISSING,
)


def make_bundle(version=1, **changes):
    value = RiskResearchEvidenceBundle(
        bundle_id=f"risk-research-evidence-bundle:bundle-{version}",
        built_at=BASE_TIME + timedelta(hours=version - 1),
        document_source_contract_id=(
            "radar-leader-risk-official-cninfo-v1"
        ),
        document_id="cninfo:1225443882",
        symbol=SYMBOL,
        issuer_identity=ISSUER_IDENTITY,
        content_contract_id=(
            "radar-leader-risk-document-content-v1"
        ),
        content_sha256="d" * 64,
        content_fetched_at=BASE_TIME - timedelta(hours=2),
        deterministic_fact_ids=("fact-deterministic-1",),
        manual_fact_ids=("fact-manual-1",),
        merged_fact_ids=(
            "fact-deterministic-1",
            "fact-manual-1",
        ),
        active_artifact_id="artifact-1",
        active_artifact_version="manual-review-v1",
        artifact_contract_version=(
            "radar-leader-manual-risk-document-review-v1"
        ),
        replay_version=(
            "radar-leader-risk-document-research-replay-v1"
        ),
        relation_id="supplemented-relation-1",
        review_id="supplemented-review-1",
        mapping_version="supplemented-map-v1",
        relation_kind=RiskDocumentRelationKind.RESOLVES,
        target_event_id="risk-event-1",
        target_event_version="v1",
        target_document_id="cninfo:1224000001",
        replacement_event_version=None,
        basis_fact_ids=(
            "fact-deterministic-1",
            "fact-manual-1",
        ),
        manual_basis_fact_ids=("fact-manual-1",),
        target_event_official_status=RiskOfficialStatus.ACTIVE,
        target_event_published_at=BASE_TIME - timedelta(days=30),
        formal_gate_gaps=ALL_GAPS,
    )
    return replace(value, **changes)


def make_second_bundle(**changes):
    value = make_bundle(
        2,
        deterministic_fact_ids=(
            "fact-deterministic-1",
            "fact-deterministic-2",
        ),
        merged_fact_ids=(
            "fact-deterministic-1",
            "fact-deterministic-2",
            "fact-manual-1",
        ),
        active_artifact_id="artifact-2",
        active_artifact_version="manual-review-v2",
        relation_id="supplemented-relation-2",
        review_id="supplemented-review-2",
        mapping_version="supplemented-map-v2",
        relation_kind=RiskDocumentRelationKind.SUPERSEDES,
        target_event_version="v2",
        replacement_event_version="v2",
        basis_fact_ids=(
            "fact-deterministic-1",
            "fact-deterministic-2",
            "fact-manual-1",
        ),
        target_event_official_status=RiskOfficialStatus.COMPLETED,
        formal_gate_gaps=tuple(
            gap
            for gap in ALL_GAPS
            if gap != FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE
        ),
    )
    return replace(value, **changes)


def make_input(*bundles, as_of=AS_OF, audit_result=None, **changes):
    audit_input = RiskResearchEvidenceBundleAuditInput(
        bundles=tuple(bundles),
    )
    result = (
        audit_risk_research_evidence_bundle_versions(audit_input)
        if audit_result is None
        else audit_result
    )
    values = {
        "symbol": SYMBOL,
        "issuer_identity": ISSUER_IDENTITY,
        "as_of": as_of,
        "audit_input": audit_input,
        "audit_result": result,
    }
    values.update(changes)
    return LeaderRiskCandidateProjectionInput(**values)


class LeaderRiskCandidateProjectionTests(unittest.TestCase):
    def test_projects_current_bundle_diffs_and_formal_gaps(self):
        input_value = make_input(make_bundle(), make_second_bundle())

        result = build_leader_risk_candidate_projection(input_value)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.projection)
        projection = result.projection
        self.assertEqual(
            projection.projection_contract_id,
            LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
        )
        self.assertEqual(
            projection.audit_contract_id,
            RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
        )
        self.assertEqual(
            projection.bundle_contract_id,
            RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
        )
        self.assertEqual(
            projection.bundle_ids,
            (
                "risk-research-evidence-bundle:bundle-1",
                "risk-research-evidence-bundle:bundle-2",
            ),
        )
        self.assertEqual(
            projection.current_bundle_id,
            "risk-research-evidence-bundle:bundle-2",
        )
        self.assertFalse(projection.risk_filter_passed)
        self.assertFalse(projection.formal_gate_ready)
        self.assertFalse(projection.formal_usable)
        self.assertFalse(projection.applied_to_d3)
        self.assertFalse(projection.applied_to_d1)

        evidence = projection.to_evidence()
        self.assertEqual(
            evidence["candidate"],
            {
                "symbol": SYMBOL,
                "issuerIdentity": ISSUER_IDENTITY,
                "asOf": AS_OF.isoformat(),
            },
        )
        self.assertEqual(
            evidence["history"]["versionDiffs"][0]["facts"][
                "deterministicAdded"
            ],
            ["fact-deterministic-2"],
        )
        self.assertEqual(
            evidence["current"]["formalGateGaps"],
            [
                gap.value
                for gap in make_second_bundle().formal_gate_gaps
            ],
        )
        self.assertEqual(
            evidence["gate"],
            {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "appliedToD3": False,
                "appliedToD1": False,
            },
        )

    def test_forged_d9_result_is_rejected(self):
        audit_input = RiskResearchEvidenceBundleAuditInput(
            bundles=(make_bundle(), make_second_bundle()),
        )
        audit_result = audit_risk_research_evidence_bundle_versions(
            audit_input
        )
        forged = replace(
            audit_result,
            current_bundle_id="risk-research-evidence-bundle:forged",
        )
        input_value = LeaderRiskCandidateProjectionInput(
            symbol=SYMBOL,
            issuer_identity=ISSUER_IDENTITY,
            as_of=AS_OF,
            audit_input=audit_input,
            audit_result=forged,
        )

        result = build_leader_risk_candidate_projection(input_value)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.projection)
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_audit_replay_mismatch",),
        )

    def test_candidate_identity_must_match_current_bundle(self):
        for field_name, value in (
            ("symbol", "300999"),
            ("issuer_identity", "cninfo-org:other"),
        ):
            with self.subTest(field_name=field_name):
                input_value = make_input(
                    make_bundle(),
                    make_second_bundle(),
                    **{field_name: value},
                )

                result = build_leader_risk_candidate_projection(
                    input_value
                )

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.projection)
                self.assertEqual(
                    result.reasons,
                    (
                        "risk_candidate_projection_"
                        "candidate_identity_mismatch",
                    ),
                )

    def test_future_or_naive_candidate_time_is_rejected(self):
        cases = (
            (
                BASE_TIME + timedelta(minutes=30),
                "risk_candidate_projection_future_evidence",
            ),
            (
                AS_OF.replace(tzinfo=None),
                "risk_candidate_projection_as_of_timezone_missing",
            ),
        )
        for as_of, reason in cases:
            with self.subTest(reason=reason):
                input_value = make_input(
                    make_bundle(),
                    make_second_bundle(),
                    as_of=as_of,
                )

                result = build_leader_risk_candidate_projection(
                    input_value
                )

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.projection)
                self.assertEqual(result.reasons, (reason,))

    def test_d9_missing_or_unverified_status_is_propagated(self):
        missing_input = make_input(make_bundle())
        unverified_second = replace(
            make_second_bundle(),
            symbol="300999",
        )
        unverified_input = make_input(
            make_bundle(),
            unverified_second,
        )

        missing = build_leader_risk_candidate_projection(
            missing_input
        )
        unverified = build_leader_risk_candidate_projection(
            unverified_input
        )

        self.assertEqual(missing.status, ResearchFeatureStatus.MISSING)
        self.assertEqual(
            missing.reasons,
            ("risk_evidence_bundle_audit_history_insufficient",),
        )
        self.assertIsNone(missing.projection)
        self.assertEqual(
            unverified.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            unverified.reasons,
            ("risk_evidence_bundle_audit_identity_mismatch",),
        )
        self.assertIsNone(unverified.projection)

    def test_contract_is_frozen_and_hides_nested_inputs(self):
        input_value = make_input(make_bundle(), make_second_bundle())

        result = build_leader_risk_candidate_projection(input_value)

        with self.assertRaises(FrozenInstanceError):
            result.projection.current_bundle_id = "forged"
        self.assertNotIn(
            "risk-research-evidence-bundle:bundle-1",
            repr(input_value),
        )
        self.assertFalse(
            hasattr(result.projection, "source_url")
        )
        self.assertFalse(
            hasattr(result.projection, "fact_summary")
        )

    def test_malformed_input_returns_stable_failure(self):
        result = build_leader_risk_candidate_projection(None)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.projection)
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_contract_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
