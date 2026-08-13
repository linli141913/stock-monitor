import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
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
BASE_TIME = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)
PUBLISHED_AT = BASE_TIME - timedelta(days=30)
FETCHED_AT = BASE_TIME - timedelta(days=2)
DOCUMENT_ID = "cninfo:1225443882"
TARGET_DOCUMENT_ID = "cninfo:1224000001"
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
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_contract_id=(
            "radar-leader-risk-document-content-v1"
        ),
        content_sha256="d" * 64,
        content_fetched_at=FETCHED_AT,
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
        target_document_id=TARGET_DOCUMENT_ID,
        replacement_event_version=None,
        basis_fact_ids=(
            "fact-deterministic-1",
            "fact-manual-1",
        ),
        manual_basis_fact_ids=("fact-manual-1",),
        target_event_official_status=RiskOfficialStatus.ACTIVE,
        target_event_published_at=PUBLISHED_AT,
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
        manual_fact_ids=(
            "fact-manual-1",
            "fact-manual-2",
        ),
        merged_fact_ids=(
            "fact-deterministic-1",
            "fact-deterministic-2",
            "fact-manual-1",
            "fact-manual-2",
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
            "fact-manual-2",
        ),
        manual_basis_fact_ids=(
            "fact-manual-1",
            "fact-manual-2",
        ),
        target_event_official_status=RiskOfficialStatus.COMPLETED,
        target_event_published_at=PUBLISHED_AT + timedelta(days=1),
        formal_gate_gaps=tuple(
            gap
            for gap in ALL_GAPS
            if gap != FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE
        ),
    )
    return replace(value, **changes)


def audit(*bundles):
    return audit_risk_research_evidence_bundle_versions(
        RiskResearchEvidenceBundleAuditInput(
            bundles=tuple(bundles),
        )
    )


class RiskResearchEvidenceBundleAuditTests(unittest.TestCase):
    def test_ready_chain_selects_latest_and_groups_differences(self):
        first = make_bundle()
        second = make_second_bundle()

        result = audit(first, second)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.audit_contract_id,
            RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
        )
        self.assertEqual(
            result.bundle_ids,
            (first.bundle_id, second.bundle_id),
        )
        self.assertIs(result.current_bundle, second)
        self.assertEqual(
            result.current_bundle_id,
            second.bundle_id,
        )
        self.assertEqual(len(result.diffs), 1)
        diff = result.diffs[0]
        self.assertEqual(diff.previous_bundle_id, first.bundle_id)
        self.assertEqual(diff.current_bundle_id, second.bundle_id)
        self.assertEqual(
            diff.facts.deterministic_added,
            ("fact-deterministic-2",),
        )
        self.assertEqual(
            diff.facts.manual_added,
            ("fact-manual-2",),
        )
        self.assertEqual(
            diff.facts.merged_added,
            ("fact-deterministic-2", "fact-manual-2"),
        )
        self.assertEqual(
            tuple(
                change.field_name
                for change in diff.artifact.field_changes
            ),
            ("active_artifact_id", "active_artifact_version"),
        )
        self.assertEqual(
            tuple(
                change.field_name
                for change in diff.relation.field_changes
            ),
            (
                "relation_id",
                "review_id",
                "mapping_version",
                "relation_kind",
                "target_event_version",
                "replacement_event_version",
                "target_event_official_status",
                "target_event_published_at",
            ),
        )
        self.assertEqual(
            diff.relation.basis_fact_ids_added,
            ("fact-deterministic-2", "fact-manual-2"),
        )
        self.assertEqual(
            diff.relation.manual_basis_fact_ids_added,
            ("fact-manual-2",),
        )
        self.assertEqual(diff.formal_gate_gaps.added, ())
        self.assertEqual(
            diff.formal_gate_gaps.removed,
            (FormalRiskGateGap.CORRECTION_LINKS_INCOMPLETE,),
        )
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.applied_to_d3)
        self.assertFalse(result.applied_to_d1)

    def test_three_versions_keep_only_adjacent_differences(self):
        first = make_bundle()
        second = make_second_bundle()
        third = make_second_bundle(
            bundle_id="risk-research-evidence-bundle:bundle-3",
            built_at=BASE_TIME + timedelta(hours=2),
            deterministic_fact_ids=("fact-deterministic-2",),
            merged_fact_ids=(
                "fact-deterministic-2",
                "fact-manual-1",
                "fact-manual-2",
            ),
            relation_id="supplemented-relation-3",
        )

        result = audit(first, second, third)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            tuple(
                (item.previous_bundle_id, item.current_bundle_id)
                for item in result.diffs
            ),
            (
                (first.bundle_id, second.bundle_id),
                (second.bundle_id, third.bundle_id),
            ),
        )
        self.assertEqual(
            result.diffs[1].facts.deterministic_removed,
            ("fact-deterministic-1",),
        )
        self.assertEqual(
            result.diffs[1].relation.field_changes[0].field_name,
            "relation_id",
        )
        self.assertIs(result.current_bundle, third)

    def test_mixed_identity_or_content_snapshot_is_rejected(self):
        first = make_bundle()
        for field_name, value in (
            ("document_id", "cninfo:other-document"),
            ("symbol", "300999"),
            ("issuer_identity", "cninfo-org:other"),
            ("content_sha256", "e" * 64),
            ("content_fetched_at", FETCHED_AT + timedelta(seconds=1)),
            ("target_event_id", "risk-event-other"),
            ("target_document_id", "cninfo:other-target"),
        ):
            with self.subTest(field_name=field_name):
                second = make_second_bundle(**{field_name: value})

                result = audit(first, second)

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.current_bundle)
                self.assertEqual(
                    result.reasons,
                    ("risk_evidence_bundle_audit_identity_mismatch",),
                )

    def test_order_duplicate_and_timestamp_conflicts_are_rejected(self):
        first = make_bundle()
        second = make_second_bundle()
        cases = (
            (
                (second, first),
                "risk_evidence_bundle_audit_order_unverified",
            ),
            (
                (
                    first,
                    replace(second, bundle_id=first.bundle_id),
                ),
                "risk_evidence_bundle_audit_bundle_duplicate",
            ),
            (
                (
                    first,
                    replace(second, built_at=first.built_at),
                ),
                "risk_evidence_bundle_audit_order_unverified",
            ),
        )
        for bundles, reason in cases:
            with self.subTest(reason=reason):
                result = audit(*bundles)

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.current_bundle)
                self.assertEqual(result.reasons, (reason,))

    def test_timestamp_only_replacement_is_rejected(self):
        first = make_bundle()
        second = replace(
            first,
            bundle_id="risk-research-evidence-bundle:bundle-2",
            built_at=first.built_at + timedelta(hours=1),
        )

        result = audit(first, second)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.current_bundle)
        self.assertEqual(
            result.reasons,
            ("risk_evidence_bundle_audit_material_change_missing",),
        )

    def test_review_metadata_only_replacement_is_rejected(self):
        first = make_bundle()
        second = replace(
            first,
            bundle_id="risk-research-evidence-bundle:bundle-2",
            built_at=first.built_at + timedelta(hours=1),
            active_artifact_id="artifact-2",
            active_artifact_version="manual-review-v2",
            relation_id="supplemented-relation-2",
            review_id="supplemented-review-2",
            mapping_version="supplemented-map-v2",
        )

        result = audit(first, second)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.current_bundle)
        self.assertEqual(
            result.reasons,
            ("risk_evidence_bundle_audit_material_change_missing",),
        )

    def test_malformed_and_insufficient_inputs_are_stable(self):
        malformed = (
            audit_risk_research_evidence_bundle_versions(None)
        )
        empty = audit()
        single = audit(make_bundle())

        self.assertEqual(
            malformed.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            malformed.reasons,
            ("risk_evidence_bundle_audit_contract_unverified",),
        )
        for result in (empty, single):
            self.assertEqual(
                result.status,
                ResearchFeatureStatus.MISSING,
            )
            self.assertEqual(
                result.reasons,
                ("risk_evidence_bundle_audit_history_insufficient",),
            )
            self.assertIsNone(result.current_bundle)

    def test_forged_d8_contract_and_formal_flags_are_rejected(self):
        first = make_bundle()
        forged_values = (
            replace(
                make_second_bundle(),
                bundle_contract_id="forged-contract",
            ),
            replace(
                make_second_bundle(),
                formal_gate_ready=True,
            ),
            replace(
                make_second_bundle(),
                formal_gate_gaps=(
                    FormalRiskGateGap.CONTENT_NOT_FORMAL,
                    FormalRiskGateGap.CONTENT_NOT_FORMAL,
                ),
            ),
        )
        for forged in forged_values:
            with self.subTest(forged=forged.bundle_id):
                result = audit(first, forged)

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(
                    result.reasons,
                    ("risk_evidence_bundle_audit_bundle_unverified",),
                )
        self.assertEqual(
            first.bundle_contract_id,
            RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
        )

    def test_result_and_nested_differences_are_frozen(self):
        result = audit(make_bundle(), make_second_bundle())

        with self.assertRaises(FrozenInstanceError):
            result.current_bundle_id = "forged"
        with self.assertRaises(FrozenInstanceError):
            result.diffs[0].previous_bundle_id = "forged"
        with self.assertRaises(FrozenInstanceError):
            result.diffs[0].facts.manual_added = ()


if __name__ == "__main__":
    unittest.main()
