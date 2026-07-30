import hashlib
import io
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests
from pypdf import PdfWriter

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    AcceptedRiskDocumentRelation,
    MAXIMUM_PAGE_CHARACTERS,
    OfficialRiskDocumentFactResult,
    OfficialRiskDocumentPage,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
)
from radar.leader_risk_invalidation_features import RiskCategory
from radar.sources.leader_risk_document_content import (
    MAXIMUM_PDF_BYTES,
    OfficialRiskDocumentContentResult,
    OfficialRiskDocumentHttpResponse,
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
    fetch_official_risk_document_content,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
FETCHED_AT = datetime(2026, 7, 29, 1, 30, tzinfo=UTC)
DOCUMENT_ID = "cninfo:1225443882"
SOURCE_URL = (
    "https://static.cninfo.com.cn/finalpage/"
    "2026-07-27/1225443882.PDF"
)


def make_document(**changes):
    document = OfficialRiskDocumentMetadata(
        source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
        issuer_name="ST恒信",
        title="关于收到中国证监会立案告知书的公告",
        published_at=FETCHED_AT - timedelta(days=1),
        source_name="巨潮资讯",
        source_url=SOURCE_URL,
        candidate_category=RiskCategory.INVESTIGATION,
        raw_column_ids=("09020202",),
        raw_announcement_types=("01010503",),
        raw_page_column="SZCY",
        association_reported=False,
        formal_usable=False,
    )
    return replace(document, **changes)


def make_pdf(*, page_count=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def make_response(content=None, **changes):
    body = make_pdf() if content is None else content
    values = {
        "status_code": 200,
        "headers": {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(body)),
        },
        "final_url": SOURCE_URL,
        "redirect_count": 0,
        "content": body,
    }
    values.update(changes)
    return OfficialRiskDocumentHttpResponse(**values)


def make_content_result(**changes):
    body = make_pdf()
    result = OfficialRiskDocumentContentResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
        content_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        page_count=1,
        pages=(OfficialRiskDocumentPage(1, ""),),
        fetched_at=FETCHED_AT,
        reasons=(),
    )
    return replace(result, **changes)


def make_fact_result(**changes):
    fact = RiskDocumentFact(
        fact_id="fact-1",
        fact_kind=RiskDocumentFactKind.CASE_ID,
        normalized_value="case:" + "a" * 64,
        page_number=1,
        fragment_sha256="b" * 64,
    )
    result = OfficialRiskDocumentFactResult(
        status=ResearchFeatureStatus.READY,
        document_id=DOCUMENT_ID,
        symbol="300081",
        issuer_identity="cninfo-org:9900012108",
        content_sha256=make_content_result().content_sha256,
        facts=(fact,),
        relations=(),
        reasons=("risk_document_relation_missing",),
    )
    return replace(result, **changes)


class LeaderRiskDocumentContentTests(unittest.TestCase):
    def test_valid_official_pdf_is_decoded_in_memory(self):
        calls = []
        body = make_pdf(page_count=2)

        def transport(url, **kwargs):
            calls.append((url, kwargs))
            return make_response(content=body)

        result = fetch_official_risk_document_content(
            make_document(),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(result.document_id, DOCUMENT_ID)
        self.assertEqual(result.content_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(result.byte_count, len(body))
        self.assertEqual(result.page_count, 2)
        self.assertEqual(
            tuple(page.page_number for page in result.pages),
            (1, 2),
        )
        self.assertFalse(result.formal_usable)
        self.assertEqual(result.reasons, ())
        self.assertEqual(calls[0][0], SOURCE_URL)
        self.assertEqual(calls[0][1]["timeout"], 20.0)
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertTrue(calls[0][1]["stream"])
        rendered = repr(result)
        self.assertNotIn("OfficialRiskDocumentPage", rendered)
        self.assertNotIn(body[:20].decode("latin-1"), rendered)

    def test_invalid_metadata_is_rejected_before_transport(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return make_response()

        invalid_documents = (
            make_document(
                source_url=(
                    "https://evil.example/finalpage/"
                    "2026-07-27/1225443882.PDF"
                )
            ),
            make_document(
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/"
                    "2026-07-27/1225000000.PDF"
                )
            ),
            make_document(
                source_url=(
                    "https://static.cninfo.com.cn/download/"
                    "1225443882.PDF"
                )
            ),
            make_document(formal_usable=True),
        )
        for document in invalid_documents:
            with self.subTest(url=document.source_url):
                result = fetch_official_risk_document_content(
                    document,
                    fetched_at=FETCHED_AT,
                    transport=transport,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "risk_document_content_metadata_unverified",
                    result.reasons,
                )
        self.assertEqual(calls, [])

    def test_response_identity_and_pdf_contract_are_strict(self):
        cases = (
            (
                make_response(status_code=302),
                "risk_document_content_redirect_forbidden",
            ),
            (
                make_response(
                    final_url=SOURCE_URL + "?download=1",
                    redirect_count=1,
                ),
                "risk_document_content_redirect_forbidden",
            ),
            (
                make_response(status_code=404),
                "risk_document_content_http_status_unverified",
            ),
            (
                make_response(
                    headers={
                        "Content-Type": "text/html",
                        "Content-Length": "10",
                    },
                    content=b"<html></html>",
                ),
                "risk_document_content_type_unverified",
            ),
            (
                make_response(
                    content=b"not-a-pdf",
                    headers={
                        "Content-Type": "application/pdf",
                        "Content-Length": "9",
                    },
                ),
                "risk_document_content_signature_unverified",
            ),
        )

        for response, reason in cases:
            with self.subTest(reason=reason):
                result = fetch_official_risk_document_content(
                    make_document(),
                    fetched_at=FETCHED_AT,
                    transport=lambda *args, value=response, **kwargs: value,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)
                self.assertFalse(result.formal_usable)

    def test_declared_and_actual_size_limits_are_enforced(self):
        body = make_pdf()
        cases = (
            make_response(
                content=body,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": str(MAXIMUM_PDF_BYTES + 1),
                },
            ),
            make_response(
                content=body,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": str(len(body) + 1),
                },
            ),
            make_response(
                content=b"%PDF-" + b"x" * MAXIMUM_PDF_BYTES,
            ),
        )

        for response in cases:
            with self.subTest(length=len(response.content)):
                result = fetch_official_risk_document_content(
                    make_document(),
                    fetched_at=FETCHED_AT,
                    transport=lambda *args, value=response, **kwargs: value,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(
                    "risk_document_content_too_large"
                    if (
                        len(response.content) > MAXIMUM_PDF_BYTES
                        or int(response.headers["Content-Length"])
                        > MAXIMUM_PDF_BYTES
                    )
                    else "risk_document_content_length_unverified",
                    result.reasons,
                )

    def test_encrypted_corrupt_and_too_many_pages_are_rejected(self):
        cases = (
            (
                make_pdf(encrypted=True),
                "risk_document_content_encrypted",
            ),
            (
                b"%PDF-corrupt",
                "risk_document_content_pdf_unverified",
            ),
            (
                make_pdf(page_count=201),
                "risk_document_content_page_limit_exceeded",
            ),
        )

        for body, reason in cases:
            with self.subTest(reason=reason):
                result = fetch_official_risk_document_content(
                    make_document(),
                    fetched_at=FETCHED_AT,
                    transport=lambda *args, value=body, **kwargs: make_response(
                        content=value
                    ),
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)

    def test_request_failure_preserves_source_failed(self):
        def transport(*args, **kwargs):
            raise requests.Timeout("private upstream detail")

        result = fetch_official_risk_document_content(
            make_document(),
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_document_content_request_failed",),
        )
        self.assertNotIn("private upstream detail", repr(result))

    def test_pdf_parser_failures_and_text_limits_are_contained(self):
        class FakeReader:
            is_encrypted = False

            def __init__(self, text=None, error=None):
                class Page:
                    def extract_text(self):
                        if error is not None:
                            raise error
                        return text

                self.pages = (Page(),)

        cases = (
            (
                FakeReader(
                    error=RuntimeError("private parser detail")
                ),
                "risk_document_content_pdf_unverified",
            ),
            (
                FakeReader(
                    text="x" * (MAXIMUM_PAGE_CHARACTERS + 1)
                ),
                "risk_document_content_text_too_large",
            ),
        )

        for reader, reason in cases:
            with self.subTest(reason=reason):
                with patch(
                    "radar.sources.leader_risk_document_content.PdfReader",
                    return_value=reader,
                ):
                    result = fetch_official_risk_document_content(
                        make_document(),
                        fetched_at=FETCHED_AT,
                        transport=lambda *args, **kwargs: make_response(),
                    )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)
                self.assertNotIn("private parser detail", repr(result))

    def test_default_transport_disables_proxy_and_redirects(self):
        body = make_pdf()

        class FakeResponse:
            status_code = 200
            url = SOURCE_URL
            history = ()
            headers = {
                "Content-Type": "application/pdf",
                "Content-Length": str(len(body)),
            }

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_content(self, chunk_size):
                self.chunk_size = chunk_size
                return iter((body,))

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.response = FakeResponse()
                self.get_kwargs = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                self.url = url
                self.get_kwargs = kwargs
                return self.response

        session = FakeSession()
        with patch(
            "radar.sources.leader_risk_document_content.requests.Session",
            return_value=session,
        ):
            result = fetch_official_risk_document_content(
                make_document(),
                fetched_at=FETCHED_AT,
            )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertFalse(session.trust_env)
        self.assertFalse(session.get_kwargs["allow_redirects"])
        self.assertTrue(session.get_kwargs["stream"])
        self.assertEqual(session.get_kwargs["timeout"], 20.0)

    def test_missing_facts_create_compressed_manual_candidate(self):
        content = make_content_result()
        facts = make_fact_result(
            status=ResearchFeatureStatus.MISSING,
            facts=(),
            reasons=("risk_document_facts_missing",),
        )

        result = build_risk_document_review_candidate(content, facts)

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            result.candidate.candidate_kind,
            RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING,
        )
        self.assertTrue(result.candidate.manual_review_required)
        self.assertFalse(result.candidate.formal_usable)
        self.assertEqual(result.candidate.fact_ids, ())
        self.assertEqual(
            result.candidate.source_reason_codes,
            ("risk_document_facts_missing",),
        )
        self.assertFalse(hasattr(result.candidate, "pages"))
        self.assertFalse(hasattr(result.candidate, "body"))

    def test_missing_relation_creates_fact_only_manual_candidate(self):
        result = build_risk_document_review_candidate(
            make_content_result(),
            make_fact_result(),
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        candidate = result.candidate
        self.assertEqual(
            candidate.candidate_kind,
            RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED,
        )
        self.assertIsInstance(
            candidate.candidate_kind,
            RiskDocumentReviewCandidateKind,
        )
        self.assertEqual(candidate.fact_ids, ("fact-1",))
        self.assertEqual(
            candidate.fact_kinds,
            (RiskDocumentFactKind.CASE_ID,),
        )
        self.assertEqual(len(candidate.candidate_id), 64)

    def test_candidate_rejects_identity_hash_and_failed_status(self):
        content = make_content_result()
        cases = (
            (
                make_fact_result(symbol="000001"),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "risk_document_review_candidate_identity_mismatch",
            ),
            (
                make_fact_result(content_sha256="f" * 64),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "risk_document_review_candidate_content_mismatch",
            ),
            (
                make_fact_result(
                    status=ResearchFeatureStatus.SOURCE_FAILED,
                    facts=(),
                    reasons=("risk_document_source_failed",),
                ),
                ResearchFeatureStatus.SOURCE_FAILED,
                "risk_document_review_candidate_source_failed",
            ),
        )

        for facts, status, reason in cases:
            with self.subTest(reason=reason):
                result = build_risk_document_review_candidate(
                    content,
                    facts,
                )
                self.assertEqual(result.status, status)
                self.assertIsNone(result.candidate)
                self.assertIn(reason, result.reasons)

    def test_forged_ready_content_and_relations_are_rejected(self):
        cases = (
            (
                make_content_result(pages=()),
                make_fact_result(),
            ),
            (
                make_content_result(
                    document_id=None,
                    symbol=None,
                    issuer_identity=None,
                ),
                make_fact_result(
                    document_id=None,
                    symbol=None,
                    issuer_identity=None,
                ),
            ),
            (
                make_content_result(fetched_at=None),
                make_fact_result(),
            ),
            (
                make_content_result(),
                make_fact_result(relations=("not-a-relation",)),
            ),
        )

        for content, facts in cases:
            with self.subTest(
                pages=len(content.pages),
                relations=len(facts.relations),
            ):
                result = build_risk_document_review_candidate(
                    content,
                    facts,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.candidate)
                self.assertIn(
                    "risk_document_review_candidate_contract_unverified",
                    result.reasons,
                )

    def test_accepted_relation_does_not_create_manual_candidate(self):
        relation = AcceptedRiskDocumentRelation(
            relation_id="review-1",
            mapping_version="map-v1",
            relation_kind=RiskDocumentRelationKind.RESOLVES,
            source_document_id=DOCUMENT_ID,
            target_event_id="event-1",
            target_event_version="v1",
            target_document_id="cninfo:1224000001",
            replacement_event_version=None,
            basis_fact_ids=("fact-1",),
            reviewed_at=FETCHED_AT,
        )
        facts = make_fact_result(
            relations=(relation,),
            reasons=(),
        )

        result = build_risk_document_review_candidate(
            make_content_result(),
            facts,
        )

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(result.candidate)
        self.assertEqual(
            result.reasons,
            ("risk_document_review_candidate_not_required",),
        )


if __name__ == "__main__":
    unittest.main()
