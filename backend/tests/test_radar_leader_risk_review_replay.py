import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    AcceptedRiskDocumentRelation,
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentPage,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
    RiskEventSubtype,
    RiskEvidenceSourceKind,
    RiskOfficialStatus,
)
from radar.leader_risk_review_artifacts import (
    MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    AcceptedManualRiskReviewArtifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)
PUBLISHED_AT = AS_OF - timedelta(days=2)
FETCHED_AT = AS_OF - timedelta(hours=5)
REVIEWED_AT = AS_OF - timedelta(hours=2)
CONTENT_SHA256 = "d" * 64
DOCUMENT_ID = "cninfo:1225443882"
TARGET_DOCUMENT_ID = "cninfo:1224000001"
ISSUER_IDENTITY = "cninfo-org:9900012108"
RAW_CASE_ID = "证监立案字0202026001号"
CASE_ID = (
    "case:"
    + hashlib.sha256(
        RAW_CASE_ID.encode("utf-8")
    ).hexdigest()
)
MANUAL_PAGE_TEXT = (
    "本页版式没有标准标签。\n"
    f"{RAW_CASE_ID}\n"
)
LABELED_PAGE_TEXT = (
    f"案号：{RAW_CASE_ID}\n"
    "原公告编号：1224000001\n"
)


def make_document(**changes):
    document = OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        issuer_name="ST恒信",
        title="关于收到中国证监会立案告知书的公告",
        published_at=PUBLISHED_AT,
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-27/1225443882.PDF"
        ),
        candidate_category=RiskCategory.INVESTIGATION,
        formal_usable=False,
    )
    return replace(document, **changes)


def make_content(page_text=MANUAL_PAGE_TEXT, **changes):
    content = OfficialRiskDocumentContentResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        byte_count=125_092,
        page_count=1,
        pages=(OfficialRiskDocumentPage(1, page_text),),
        fetched_at=FETCHED_AT,
        reasons=(),
    )
    return replace(content, **changes)


def make_event(**changes):
    event = LeaderRiskEventEvidence(
        event_id="risk-event-1",
        event_version="v1",
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        category=RiskCategory.INVESTIGATION,
        event_subtype=RiskEventSubtype.FORMAL_INVESTIGATION,
        case_id=CASE_ID,
        source_kind=(
            RiskEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
        ),
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-01-01/1224000001.PDF"
        ),
        document_id=TARGET_DOCUMENT_ID,
        published_at=PUBLISHED_AT - timedelta(days=30),
        effective_from=PUBLISHED_AT - timedelta(days=30),
        effective_until=None,
        reporting_period=None,
        fact_summary="收到正式立案告知书。",
        official_status=RiskOfficialStatus.ACTIVE,
    )
    return replace(event, **changes)


def make_base_context(
    page_text=MANUAL_PAGE_TEXT,
    events=(),
):
    document = make_document()
    content = make_content(page_text)
    facts = extract_official_risk_document_facts(
        OfficialRiskDocumentFactInput(
            as_of=AS_OF,
            document=document,
            content_sha256=content.content_sha256,
            pages=content.pages,
            extracted_at=content.fetched_at,
            source_status=content.status,
            event_versions=tuple(events),
            reviews=(),
        )
    )
    candidate_result = build_risk_document_review_candidate(
        content,
        facts,
    )
    return document, content, facts, candidate_result.candidate


def fact_id(
    fact_kind,
    normalized_value,
    page_number=1,
):
    identity = (
        f"{DOCUMENT_ID}|{fact_kind.value}|"
        f"{normalized_value}|{page_number}"
    )
    return "riskfact:" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def make_manual_fact(
    normalized_value=CASE_ID,
    *,
    fact_kind=RiskDocumentFactKind.CASE_ID,
    page_number=1,
    **changes,
):
    fact = RiskDocumentFact(
        fact_id=fact_id(
            fact_kind,
            normalized_value,
            page_number,
        ),
        fact_kind=fact_kind,
        normalized_value=normalized_value,
        page_number=page_number,
        fragment_sha256="a" * 64,
        extractor_version=MANUAL_RISK_DOCUMENT_REVIEW_VERSION,
    )
    return replace(fact, **changes)


def artifact_id(candidate):
    material = (
        f"{candidate.candidate_id}|"
        f"{candidate.candidate_kind.value}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def make_fact_artifact(candidate, **changes):
    artifact = AcceptedManualRiskReviewArtifact(
        artifact_id=artifact_id(candidate),
        review_version="manual-review-v1",
        supersedes_review_version=None,
        candidate_id=candidate.candidate_id,
        candidate_kind=(
            RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
        ),
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=REVIEWED_AT,
        effective_until=None,
        facts=(make_manual_fact(),),
        relations=(),
    )
    return replace(artifact, **changes)


def make_relation_artifact(candidate, facts, event, **changes):
    facts_by_kind = {
        fact.fact_kind: fact.fact_id
        for fact in facts.facts
    }
    relation = AcceptedRiskDocumentRelation(
        relation_id="risk-review-1",
        mapping_version="manual-review-v1",
        relation_kind=RiskDocumentRelationKind.RESOLVES,
        source_document_id=DOCUMENT_ID,
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        target_document_id=event.document_id,
        replacement_event_version=None,
        basis_fact_ids=(
            facts_by_kind[
                RiskDocumentFactKind.REFERENCED_DOCUMENT_ID
            ],
            facts_by_kind[RiskDocumentFactKind.CASE_ID],
        ),
        reviewed_at=REVIEWED_AT,
    )
    artifact = AcceptedManualRiskReviewArtifact(
        artifact_id=artifact_id(candidate),
        review_version="manual-review-v1",
        supersedes_review_version=None,
        candidate_id=candidate.candidate_id,
        candidate_kind=(
            RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED
        ),
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity=ISSUER_IDENTITY,
        content_sha256=CONTENT_SHA256,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=REVIEWED_AT,
        effective_until=None,
        facts=(),
        relations=(relation,),
    )
    return replace(artifact, **changes)


def make_input(
    document,
    content,
    facts,
    *,
    events=(),
    artifacts=(),
    as_of=AS_OF,
):
    return RiskDocumentResearchReplayInput(
        as_of=as_of,
        document=document,
        content=content,
        facts=facts,
        event_versions=tuple(events),
        artifacts=tuple(artifacts),
    )


class LeaderRiskReviewReplayTests(unittest.TestCase):
    def test_latest_manual_fact_version_builds_research_view(self):
        document, content, facts, candidate = (
            make_base_context(
                page_text=(
                    MANUAL_PAGE_TEXT
                    + "\n证监立案字0202026999号\n"
                )
            )
        )
        first = make_fact_artifact(candidate)
        second_fact = make_manual_fact(
            normalized_value=(
                "case:"
                + hashlib.sha256(
                    "证监立案字0202026999号".encode("utf-8")
                ).hexdigest()
            )
        )
        second = make_fact_artifact(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
            facts=(second_fact,),
        )

        result = replay_risk_document_research_evidence(
            make_input(
                document,
                content,
                facts,
                artifacts=(first, second),
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.active_review_version, "manual-review-v2")
        self.assertEqual(result.active_artifact_id, first.artifact_id)
        self.assertEqual(result.deterministic_facts, ())
        self.assertEqual(result.manual_facts, (second_fact,))
        self.assertEqual(result.facts, (second_fact,))
        self.assertEqual(result.relations, ())
        self.assertTrue(result.manual_review_applied_to_view)
        self.assertFalse(result.d3_mutated)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.applied_to_d1)

    def test_candidate_without_artifact_is_missing(self):
        document, content, facts, _ = make_base_context()

        result = replay_risk_document_research_evidence(
            make_input(document, content, facts)
        )

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertEqual(result.facts, facts.facts)
        self.assertIn(
            "risk_document_review_artifact_missing",
            result.reasons,
        )

    def test_expired_latest_version_never_falls_back(self):
        document, content, facts, candidate = (
            make_base_context()
        )
        first = make_fact_artifact(candidate)
        second = make_fact_artifact(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
            effective_until=AS_OF - timedelta(minutes=1),
        )

        result = replay_risk_document_research_evidence(
            make_input(
                document,
                content,
                facts,
                artifacts=(first, second),
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.STALE)
        self.assertIsNone(result.active_review_version)
        self.assertEqual(result.manual_facts, ())
        self.assertIn(
            "risk_document_review_artifact_latest_expired",
            result.reasons,
        )

    def test_invalid_version_chains_are_rejected(self):
        document, content, facts, candidate = (
            make_base_context()
        )
        first = make_fact_artifact(candidate)
        valid_second = make_fact_artifact(
            candidate,
            review_version="manual-review-v2",
            supersedes_review_version="manual-review-v1",
            reviewed_at=REVIEWED_AT + timedelta(minutes=10),
        )
        cases = (
            (valid_second,),
            (first, replace(valid_second, review_version="manual-review-v1")),
            (
                first,
                replace(valid_second, reviewed_at=REVIEWED_AT),
            ),
            (
                first,
                replace(
                    valid_second,
                    candidate_id="other-candidate",
                ),
            ),
            (
                first,
                replace(
                    valid_second,
                    reviewed_at=AS_OF + timedelta(seconds=1),
                ),
            ),
        )

        for artifacts in cases:
            with self.subTest(count=len(artifacts)):
                result = replay_risk_document_research_evidence(
                    make_input(
                        document,
                        content,
                        facts,
                        artifacts=artifacts,
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "risk_document_review_artifact_chain_unverified",
                    result.reasons,
                )

    def test_tampered_manual_facts_are_rejected(self):
        document, content, facts, candidate = (
            make_base_context()
        )
        valid_fact = make_manual_fact()
        invalid_facts = (
            replace(valid_fact, fact_id="riskfact:bad"),
            replace(valid_fact, fragment_sha256="bad"),
            replace(valid_fact, page_number=2),
            replace(valid_fact, normalized_value="raw-case-id"),
            replace(valid_fact, extractor_version="automatic"),
        )

        for invalid_fact in invalid_facts:
            with self.subTest(fact=invalid_fact):
                artifact = make_fact_artifact(
                    candidate,
                    facts=(invalid_fact,),
                )
                result = replay_risk_document_research_evidence(
                    make_input(
                        document,
                        content,
                        facts,
                        artifacts=(artifact,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "risk_document_review_artifact_fact_unverified",
                    result.reasons,
                )

    def test_accepted_relation_is_replayed_against_current_events(self):
        event = make_event()
        document, content, facts, candidate = (
            make_base_context(
                page_text=LABELED_PAGE_TEXT,
                events=(event,),
            )
        )
        artifact = make_relation_artifact(
            candidate,
            facts,
            event,
        )
        replay_input = make_input(
            document,
            content,
            facts,
            events=(event,),
            artifacts=(artifact,),
        )

        result = replay_risk_document_research_evidence(
            replay_input
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.facts, facts.facts)
        self.assertEqual(result.manual_facts, ())
        self.assertEqual(result.relations, artifact.relations)
        self.assertNotIn(event.fact_summary, repr(replay_input))

    def test_tampered_relation_identity_and_basis_are_rejected(self):
        event = make_event()
        document, content, facts, candidate = (
            make_base_context(
                page_text=LABELED_PAGE_TEXT,
                events=(event,),
            )
        )
        artifact = make_relation_artifact(
            candidate,
            facts,
            event,
        )
        relation = artifact.relations[0]
        invalid_relations = (
            replace(relation, mapping_version="other-version"),
            replace(relation, target_event_version="v2"),
            replace(relation, target_event_id=[]),
            replace(relation, target_document_id="cninfo:wrong"),
            replace(relation, basis_fact_ids=("riskfact:missing",)),
            replace(relation, basis_fact_ids=([],)),
            replace(
                relation,
                reviewed_at=REVIEWED_AT + timedelta(seconds=1),
            ),
        )

        for invalid_relation in invalid_relations:
            with self.subTest(relation=invalid_relation):
                invalid_artifact = replace(
                    artifact,
                    relations=(invalid_relation,),
                )
                result = replay_risk_document_research_evidence(
                    make_input(
                        document,
                        content,
                        facts,
                        events=(event,),
                        artifacts=(invalid_artifact,),
                    )
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "risk_document_review_artifact_relation_unverified",
                    result.reasons,
                )

        malformed_artifact = replace(artifact, relations=None)
        malformed_result = replay_risk_document_research_evidence(
            make_input(
                document,
                content,
                facts,
                events=(event,),
                artifacts=(malformed_artifact,),
            )
        )
        self.assertEqual(
            malformed_result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "risk_document_review_artifact_relation_unverified",
            malformed_result.reasons,
        )

    def test_fact_result_drift_is_rejected(self):
        document, content, facts, candidate = (
            make_base_context()
        )
        drifted_facts = replace(
            facts,
            content_sha256="f" * 64,
        )

        result = replay_risk_document_research_evidence(
            make_input(
                document,
                content,
                drifted_facts,
                artifacts=(make_fact_artifact(candidate),),
            )
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "risk_document_replay_fact_result_unverified",
            result.reasons,
        )

    def test_malformed_contract_is_stably_unverified(self):
        result = replay_risk_document_research_evidence(None)

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_document_replay_contract_unverified",),
        )
        self.assertIsNone(result.active_artifact_id)


if __name__ == "__main__":
    unittest.main()
