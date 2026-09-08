"""阶段6官方主营 PDF 的受控下载与内存解码合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
import unicodedata
from urllib.parse import urlsplit

from pypdf import PdfReader
import requests

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.sources.leader_business_official import (
    CNINFO_BUSINESS_MATERIAL_CONTRACT_ID,
    MAXIMUM_FUTURE_SKEW_SECONDS,
    OfficialBusinessMaterialDocument,
)
from radar.sources.leader_business_catalyst_official import (
    CNINFO_BUSINESS_CATALYST_CONTRACT_ID,
    OfficialBusinessCatalystDocument,
)


BUSINESS_DOCUMENT_CONTENT_CONTRACT_ID = (
    "radar-leader-business-document-content-v1"
)
REQUEST_TIMEOUT_SECONDS = 20.0
MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS = 2
MAXIMUM_PDF_BYTES = 50 * 1024 * 1024
MAXIMUM_PAGE_COUNT = 800
MAXIMUM_PAGE_CHARACTERS = 100_000
MAXIMUM_TOTAL_CHARACTERS = 8_000_000
DOWNLOAD_CHUNK_BYTES = 64 * 1024
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
UTC = timezone.utc


@dataclass(frozen=True)
class OfficialBusinessDocumentHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    final_url: str
    redirect_count: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class OfficialBusinessDocumentPage:
    page_number: int
    text: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class OfficialBusinessDocumentContentResult:
    status: AutomaticBusinessEvidenceStatus
    document_id: Optional[str] = None
    document_version: Optional[str] = None
    symbol: Optional[str] = None
    issuer_identity: Optional[str] = None
    document_kind: Optional[OfficialBusinessDocumentKind] = None
    content_sha256: Optional[str] = None
    byte_count: Optional[int] = None
    page_count: Optional[int] = None
    pages: Tuple[OfficialBusinessDocumentPage, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    fetched_at: Optional[datetime] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = BUSINESS_DOCUMENT_CONTENT_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


BusinessDocumentContentTransport = Callable[
    ...,
    OfficialBusinessDocumentHttpResponse,
]


class _BusinessDocumentContentLimitError(ValueError):
    pass


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    document: Any,
    kind: Any,
    status: AutomaticBusinessEvidenceStatus,
    fetched_at: Any,
    reasons: Sequence[str],
    *,
    content_sha256: Optional[str] = None,
    byte_count: Optional[int] = None,
    pages: Sequence[OfficialBusinessDocumentPage] = (),
) -> OfficialBusinessDocumentContentResult:
    valid_document = (
        document
        if type(document) in {
            OfficialBusinessMaterialDocument,
            OfficialBusinessCatalystDocument,
        }
        else None
    )
    valid_kind = (
        kind if type(kind) is OfficialBusinessDocumentKind else None
    )
    page_tuple = tuple(pages)
    return OfficialBusinessDocumentContentResult(
        status=status,
        document_id=(
            valid_document.document_id if valid_document else None
        ),
        document_version=(
            valid_document.document_version if valid_document else None
        ),
        symbol=valid_document.symbol if valid_document else None,
        issuer_identity=(
            valid_document.issuer_identity if valid_document else None
        ),
        document_kind=valid_kind,
        content_sha256=content_sha256,
        byte_count=byte_count,
        page_count=len(page_tuple) if page_tuple else None,
        pages=page_tuple,
        fetched_at=fetched_at if _aware(fetched_at) else None,
        reasons=_dedupe(reasons),
    )


def _metadata_valid(document: Any, kind: Any, fetched_at: Any) -> bool:
    if (
        type(document) not in {
            OfficialBusinessMaterialDocument,
            OfficialBusinessCatalystDocument,
        }
        or type(kind) is not OfficialBusinessDocumentKind
        or (
            kind is OfficialBusinessDocumentKind.ANNUAL_REPORT
            and type(document) is not OfficialBusinessMaterialDocument
        )
        or (
            kind is OfficialBusinessDocumentKind.CATALYST
            and type(document) is not OfficialBusinessCatalystDocument
        )
        or not _aware(fetched_at)
        or document.source_contract_id != (
            CNINFO_BUSINESS_MATERIAL_CONTRACT_ID
            if kind is OfficialBusinessDocumentKind.ANNUAL_REPORT
            else CNINFO_BUSINESS_CATALYST_CONTRACT_ID
        )
        or not SAFE_ID_PATTERN.fullmatch(document.document_id)
        or not SAFE_ID_PATTERN.fullmatch(document.document_version)
        or not SAFE_ID_PATTERN.fullmatch(document.symbol)
        or len(document.symbol) != 6
        or not document.symbol.isdigit()
        or not SAFE_ID_PATTERN.fullmatch(document.issuer_identity)
        or not document.issuer_identity.startswith("cninfo-org:")
        or not _aware(document.published_at)
        or document.published_at > fetched_at + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return False
    document_suffix = document.document_id.removeprefix("cninfo:")
    if not document_suffix.isdigit():
        return False
    try:
        parsed = urlsplit(document.source_url)
    except (TypeError, ValueError):
        return False
    path = PurePosixPath(parsed.path)
    path_parts = path.parts
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "static.cninfo.com.cn"
        and parsed.netloc == "static.cninfo.com.cn"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and path.suffix.casefold() == ".pdf"
        and path.stem == document_suffix
        and len(path_parts) == 4
        and path_parts[0] == "/"
        and path_parts[1] == "finalpage"
        and bool(path_parts[2])
    )


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    lowered = name.casefold()
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == lowered:
            return value if isinstance(value, str) else None
    return None


def _response_reasons(
    document: Any,
    response: Any,
) -> Tuple[str, ...]:
    if (
        type(response) is not OfficialBusinessDocumentHttpResponse
        or not isinstance(response.status_code, int)
        or isinstance(response.status_code, bool)
        or not isinstance(response.headers, Mapping)
        or not isinstance(response.final_url, str)
        or not isinstance(response.redirect_count, int)
        or isinstance(response.redirect_count, bool)
        or response.redirect_count < 0
        or not isinstance(response.content, bytes)
    ):
        return ("business_document_content_response_unverified",)
    if (
        300 <= response.status_code < 400
        or response.redirect_count != 0
        or response.final_url != document.source_url
    ):
        return ("business_document_content_redirect_forbidden",)
    if response.status_code != 200:
        return ("business_document_content_http_status_unverified",)
    content_type = _header(response.headers, "Content-Type")
    if (
        content_type is None
        or content_type.split(";", 1)[0].strip().casefold()
        != "application/pdf"
    ):
        return ("business_document_content_type_unverified",)
    raw_length = _header(response.headers, "Content-Length")
    try:
        declared_length = int(raw_length) if raw_length is not None else -1
    except ValueError:
        declared_length = -1
    actual_length = len(response.content)
    if (
        declared_length > MAXIMUM_PDF_BYTES
        or actual_length > MAXIMUM_PDF_BYTES
    ):
        return ("business_document_content_too_large",)
    if declared_length < 1 or declared_length != actual_length:
        return ("business_document_content_length_unverified",)
    if not response.content.startswith(b"%PDF-"):
        return ("business_document_content_signature_unverified",)
    return ()


def _normalize_page_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\x00", "")
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def _decode_pdf(
    document: Any,
    kind: OfficialBusinessDocumentKind,
    response: OfficialBusinessDocumentHttpResponse,
    fetched_at: datetime,
) -> OfficialBusinessDocumentContentResult:
    content_sha256 = hashlib.sha256(response.content).hexdigest()
    try:
        reader = PdfReader(BytesIO(response.content), strict=False)
        if reader.is_encrypted:
            return _result(
                document,
                kind,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                fetched_at,
                ("business_document_content_encrypted",),
                content_sha256=content_sha256,
                byte_count=len(response.content),
            )
        page_count = len(reader.pages)
        if page_count < 1 or page_count > MAXIMUM_PAGE_COUNT:
            return _result(
                document,
                kind,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                fetched_at,
                ("business_document_content_page_count_unverified",),
                content_sha256=content_sha256,
                byte_count=len(response.content),
            )
        pages = []
        total_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            raw_text = page.extract_text() or ""
            if not isinstance(raw_text, str):
                raise ValueError("business_document_page_text_invalid")
            text = _normalize_page_text(raw_text)
            if len(text) > MAXIMUM_PAGE_CHARACTERS:
                return _result(
                    document,
                    kind,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                    fetched_at,
                    ("business_document_content_text_too_large",),
                    content_sha256=content_sha256,
                    byte_count=len(response.content),
                )
            total_characters += len(text)
            if total_characters > MAXIMUM_TOTAL_CHARACTERS:
                return _result(
                    document,
                    kind,
                    AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                    fetched_at,
                    ("business_document_content_text_too_large",),
                    content_sha256=content_sha256,
                    byte_count=len(response.content),
                )
            pages.append(OfficialBusinessDocumentPage(page_number, text))
    except Exception:
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            fetched_at,
            ("business_document_content_pdf_unverified",),
            content_sha256=content_sha256,
            byte_count=len(response.content),
        )
    if total_characters == 0:
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            fetched_at,
            ("business_document_content_text_missing",),
            content_sha256=content_sha256,
            byte_count=len(response.content),
        )
    return _result(
        document,
        kind,
        AutomaticBusinessEvidenceStatus.READY,
        fetched_at,
        (),
        content_sha256=content_sha256,
        byte_count=len(response.content),
        pages=pages,
    )


def _default_transport(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    allow_redirects: bool,
    stream: bool,
) -> OfficialBusinessDocumentHttpResponse:
    with requests.Session() as session:
        session.trust_env = False
        with session.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
            stream=stream,
        ) as response:
            raw_length = response.headers.get("Content-Length")
            try:
                declared_length = (
                    int(raw_length) if raw_length is not None else -1
                )
            except ValueError:
                declared_length = -1
            if declared_length > MAXIMUM_PDF_BYTES:
                raise _BusinessDocumentContentLimitError
            chunks = []
            byte_count = 0
            for chunk in response.iter_content(
                chunk_size=DOWNLOAD_CHUNK_BYTES
            ):
                if not chunk:
                    continue
                byte_count += len(chunk)
                if byte_count > MAXIMUM_PDF_BYTES:
                    raise _BusinessDocumentContentLimitError
                chunks.append(chunk)
            return OfficialBusinessDocumentHttpResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                final_url=response.url,
                redirect_count=len(response.history),
                content=b"".join(chunks),
            )


def fetch_official_business_document_content(
    document: Any,
    *,
    kind: Any,
    fetched_at: Optional[datetime] = None,
    transport: Optional[BusinessDocumentContentTransport] = None,
) -> OfficialBusinessDocumentContentResult:
    """下载并解码官方 PDF；任何异常都不会产生部分正式证据。"""

    actual_fetched_at = fetched_at or datetime.now(UTC)
    if not _metadata_valid(document, kind, actual_fetched_at):
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            ("business_document_content_metadata_unverified",),
        )
    assert type(document) in {
        OfficialBusinessMaterialDocument,
        OfficialBusinessCatalystDocument,
    }
    assert isinstance(kind, OfficialBusinessDocumentKind)
    requester = transport or _default_transport
    try:
        response = None
        for attempt in range(MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS):
            try:
                response = requester(
                    document.source_url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.cninfo.com.cn/",
                        "Accept": "application/pdf",
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=True,
                )
                break
            except requests.RequestException:
                if attempt + 1 == MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS:
                    raise
    except _BusinessDocumentContentLimitError:
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            ("business_document_content_too_large",),
        )
    except requests.RequestException:
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            actual_fetched_at,
            ("business_document_content_request_failed",),
        )
    except (TypeError, ValueError):
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            ("business_document_content_response_unverified",),
        )
    response_reasons = _response_reasons(document, response)
    if response_reasons:
        return _result(
            document,
            kind,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            response_reasons,
        )
    return _decode_pdf(document, kind, response, actual_fetched_at)
