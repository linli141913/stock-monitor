import hashlib
import io
import unittest
from datetime import datetime, timedelta, timezone

import requests
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.sources.leader_business_document_content import (
    MAXIMUM_PDF_BYTES,
    OfficialBusinessDocumentHttpResponse,
    fetch_official_business_document_content,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


UTC = timezone.utc
FETCHED_AT = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
DOCUMENT_ID = "cninfo:1234567890"
SOURCE_URL = (
    "https://static.cninfo.com.cn/finalpage/"
    "2026-04-30/1234567890.PDF"
)


def make_document(**changes):
    values = {
        "document_id": DOCUMENT_ID,
        "document_version": "cninfo:1234567890:1777500000000",
        "symbol": "000001",
        "issuer_identity": "cninfo-org:9900000001",
        "title": "2025年年度报告",
        "published_at": FETCHED_AT - timedelta(days=100),
        "source_url": SOURCE_URL,
    }
    values.update(changes)
    return OfficialBusinessMaterialDocument(**values)


def make_pdf(*, text="Annual report business", encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): font_reference,
            }),
        })
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(
            f"BT /F1 12 Tf 50 750 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
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
    return OfficialBusinessDocumentHttpResponse(**values)


class LeaderBusinessDocumentContentTests(unittest.TestCase):
    def test_valid_official_pdf_returns_hash_and_page_text(self):
        body = make_pdf(text="Annual report business segment")

        result = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=lambda *args, **kwargs: make_response(content=body),
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(result.content_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(result.byte_count, len(body))
        self.assertEqual(result.page_count, 1)
        self.assertIn("Annual report business segment", result.pages[0].text)
        self.assertNotIn("Annual report business segment", repr(result))
        self.assertFalse(result.formal_usable)

    def test_invalid_metadata_is_rejected_before_transport(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return make_response()

        invalid_documents = (
            make_document(source_url=SOURCE_URL.replace(
                "static.cninfo.com.cn", "evil.example"
            )),
            make_document(source_url=SOURCE_URL.replace(
                "1234567890.PDF", "1234567899.PDF"
            )),
            make_document(published_at=FETCHED_AT + timedelta(seconds=6)),
        )

        for invalid in invalid_documents:
            with self.subTest(document=invalid):
                result = fetch_official_business_document_content(
                    invalid,
                    kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
                    fetched_at=FETCHED_AT,
                    transport=transport,
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(
                    result.reasons,
                    ("business_document_content_metadata_unverified",),
                )
        self.assertEqual(calls, [])

    def test_response_identity_and_pdf_contract_are_strict(self):
        body = make_pdf()
        cases = (
            (
                make_response(status_code=302),
                "business_document_content_redirect_forbidden",
            ),
            (
                make_response(final_url=SOURCE_URL + "?download=1", redirect_count=1),
                "business_document_content_redirect_forbidden",
            ),
            (
                make_response(status_code=404),
                "business_document_content_http_status_unverified",
            ),
            (
                make_response(
                    content=b"not-a-pdf",
                    headers={
                        "Content-Type": "text/plain",
                        "Content-Length": "9",
                    },
                ),
                "business_document_content_type_unverified",
            ),
            (
                make_response(
                    content=body,
                    headers={
                        "Content-Type": "application/pdf",
                        "Content-Length": str(len(body) + 1),
                    },
                ),
                "business_document_content_length_unverified",
            ),
            (
                make_response(
                    content=body,
                    headers={
                        "Content-Type": "application/pdf",
                        "Content-Length": str(MAXIMUM_PDF_BYTES + 1),
                    },
                ),
                "business_document_content_too_large",
            ),
        )

        for response, reason in cases:
            with self.subTest(reason=reason):
                result = fetch_official_business_document_content(
                    make_document(),
                    kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
                    fetched_at=FETCHED_AT,
                    transport=lambda *args, value=response, **kwargs: value,
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)

    def test_encrypted_and_textless_pdfs_are_not_accepted(self):
        cases = (
            (
                make_pdf(encrypted=True),
                "business_document_content_encrypted",
            ),
            (
                make_pdf(text=""),
                "business_document_content_text_missing",
            ),
        )

        for body, reason in cases:
            with self.subTest(reason=reason):
                result = fetch_official_business_document_content(
                    make_document(),
                    kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
                    fetched_at=FETCHED_AT,
                    transport=lambda *args, value=body, **kwargs: make_response(
                        content=value
                    ),
                )
                self.assertEqual(
                    result.status,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIn(reason, result.reasons)

    def test_network_failure_is_distinct_from_malformed_transport_result(self):
        calls = []

        def failed_transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise requests.ConnectionError("offline")

        failed = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=failed_transport,
        )
        malformed = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=lambda *args, **kwargs: object(),
        )

        self.assertEqual(failed.status, AutomaticBusinessEvidenceStatus.SOURCE_FAILED)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            malformed.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )

    def test_transient_network_failure_retries_once_then_accepts_strict_pdf(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise requests.ConnectionError("transient")
            return make_response()

        result = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
        self.assertEqual(len(calls), 2)

    def test_invalid_http_response_is_not_retried(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return make_response(status_code=404)

        result = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_document_content_http_status_unverified",),
        )
        self.assertEqual(len(calls), 1)

    def test_transient_failure_then_invalid_response_stays_unverified(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise requests.ConnectionError("transient")
            return make_response(status_code=404)

        result = fetch_official_business_document_content(
            make_document(),
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=FETCHED_AT,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("business_document_content_http_status_unverified",),
        )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
