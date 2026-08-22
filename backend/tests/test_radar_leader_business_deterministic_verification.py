import unittest
import hashlib
from datetime import datetime, timedelta, timezone

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_catalyst_facts import (
    OfficialBusinessCatalystFactResult,
)
from radar.leader_business_catalyst_features import BusinessCatalystRelation
from radar.leader_business_deterministic_verification import (
    build_deterministic_official_business_verification,
)
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
    OfficialBusinessFactResult,
)
from radar.leader_runtime_candidate_plan import LeaderRuntimeCandidatePlanItem
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystKind,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)
VALIDATED_AT = AS_OF - timedelta(minutes=5)


def plan_item(**changes):
    values = {
        "index": 0,
        "symbol": "000001",
        "as_of": AS_OF,
        "industry_code": "65",
        "industry_name": "软件和信息技术服务业",
        "industry_release_id": "industry-release-2026h1",
        "within_industry_rank": 1,
        "quote_source_contract_id": "quote-v1",
        "sector_source_contract_id": "sector-v1",
    }
    values.update(changes)
    return LeaderRuntimeCandidatePlanItem(**values)


def fragment(page_number, digest=None, text="evidence"):
    return OfficialBusinessEvidenceFragment(
        page_number=page_number,
        fragment_sha256=(
            digest or hashlib.sha256(text.encode("utf-8")).hexdigest()
        ),
        text=text,
    )


def annual_facts(*, terms=("工业软件", "云平台"), **changes):
    values = {
        "status": AutomaticBusinessEvidenceStatus.READY,
        "symbol": "000001",
        "industry_code": "65",
        "industry_release_id": "industry-release-2026h1",
        "document_id": "cninfo:annual-1",
        "document_version": "cninfo:annual-1:v1",
        "content_sha256": "a" * 64,
        "business_terms": terms,
        "fragments": (fragment(20, text="annual evidence"),),
        "source_time": VALIDATED_AT - timedelta(days=100),
        "validated_at": VALIDATED_AT - timedelta(minutes=2),
    }
    values.update(changes)
    return OfficialBusinessFactResult(**values)


def catalyst_facts(*, terms=("工业软件",), negative=False, **changes):
    values = {
        "status": AutomaticBusinessEvidenceStatus.READY,
        "document_id": "cninfo:catalyst-1",
        "document_version": "cninfo:catalyst-1:v1",
        "symbol": "000001",
        "issuer_identity": "cninfo-org:9900000001",
        "event_kind": OfficialBusinessCatalystKind.MAJOR_CONTRACT,
        "content_sha256": "b" * 64,
        "business_terms": terms,
        "fragments": (fragment(3, text="catalyst evidence"),),
        "negative_event": negative,
        "source_time": VALIDATED_AT - timedelta(days=1),
        "validated_at": VALIDATED_AT - timedelta(minutes=1),
    }
    values.update(changes)
    return OfficialBusinessCatalystFactResult(**values)


class LeaderBusinessDeterministicVerificationTests(unittest.TestCase):
    def test_exact_non_generic_term_builds_direct_versioned_artifact(self):
        result = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(),
            (catalyst_facts(),),
            validated_at=VALIDATED_AT,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.artifact.relation, BusinessCatalystRelation.DIRECT)
        self.assertEqual(result.artifact.matched_terms, ("工业软件",))
        self.assertRegex(result.artifact.verification_id, r"^business-auto:[0-9a-f]{64}$")
        self.assertFalse(result.artifact.formal_usable)
        self.assertNotIn("evidence", repr(result))

    def test_negative_official_event_builds_disproved_relation(self):
        result = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(),
            (catalyst_facts(negative=True),),
            validated_at=VALIDATED_AT,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(
            result.artifact.relation,
            BusinessCatalystRelation.DISPROVED,
        )

    def test_generic_fuzzy_and_company_name_matches_are_not_confirmed(self):
        cases = (
            (annual_facts(terms=("平台",)), catalyst_facts(terms=("平台",))),
            (annual_facts(terms=("工业软件",)), catalyst_facts(terms=("工业软件项目",))),
            (annual_facts(terms=("平安银行",)), catalyst_facts(terms=("平安银行",))),
        )

        for annual, catalyst in cases:
            with self.subTest(terms=catalyst.business_terms):
                result = build_deterministic_official_business_verification(
                    plan_item(),
                    annual,
                    (catalyst,),
                    validated_at=VALIDATED_AT,
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.artifact)

    def test_structural_qualifiers_do_not_hide_exact_business_objects(self):
        result = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(terms=("焦化板块", "金针菇")),
            (
                catalyst_facts(
                    terms=("焦化", "公司金针菇"),
                ),
            ),
            validated_at=VALIDATED_AT,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.artifact.matched_terms, ("焦化板块", "金针菇"))

    def test_incomplete_structural_phrase_is_not_an_exact_business_object(self):
        result = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(terms=("公司主要",)),
            (catalyst_facts(terms=("公司主要",)),),
            validated_at=VALIDATED_AT,
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.artifact)

    def test_fragment_hash_drift_and_future_validation_fail_closed(self):
        drifted = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(fragments=(fragment(20, digest="bad"),)),
            (catalyst_facts(),),
            validated_at=VALIDATED_AT,
        )
        future = build_deterministic_official_business_verification(
            plan_item(),
            annual_facts(),
            (catalyst_facts(validated_at=VALIDATED_AT + timedelta(seconds=1)),),
            validated_at=VALIDATED_AT,
        )

        self.assertEqual(
            drifted.reasons,
            ("business_deterministic_evidence_unverified",),
        )
        self.assertEqual(
            future.reasons,
            ("business_deterministic_time_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
