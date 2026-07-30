"""阶段6L-D4官方风险PDF受控解码与人工审核候选。

本模块只下载并在内存中解码D2确认的巨潮官方PDF。它不落盘、不连接数据库，
也不把D3结果转换成D1正式风险事件或解除证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from pypdf import PdfReader
import requests

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    AcceptedRiskDocumentRelation,
    MAXIMUM_PAGE_CHARACTERS,
    MAXIMUM_PAGE_COUNT,
    MAXIMUM_TOTAL_CHARACTERS,
    OfficialRiskDocumentFactResult,
    OfficialRiskDocumentPage,
    RiskDocumentFact,
    RiskDocumentFactKind,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


RISK_DOCUMENT_CONTENT_CONTRACT_ID = (
    "radar-leader-risk-document-content-v1"
)
RISK_DOCUMENT_REVIEW_CANDIDATE_CONTRACT_ID = (
    "radar-leader-risk-document-review-candidate-v1"
)
REQUEST_TIMEOUT_SECONDS = 20.0
MAXIMUM_PDF_BYTES = 10 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
UTC = timezone.utc
SAFE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REASON_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,160}$")


class RiskDocumentReviewCandidateKind(str, Enum):
    FACT_EXTRACTION_MISSING = "fact_extraction_missing"
    RELATION_REVIEW_REQUIRED = "relation_review_required"


@dataclass(frozen=True)
class OfficialRiskDocumentHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    final_url: str
    redirect_count: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class OfficialRiskDocumentContentResult:
    status: ResearchFeatureStatus
    document_id: Optional[str] = None
    symbol: Optional[str] = None
    issuer_identity: Optional[str] = None
    content_sha256: Optional[str] = None
    byte_count: Optional[int] = None
    page_count: Optional[int] = None
    pages: Tuple[OfficialRiskDocumentPage, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    fetched_at: Optional[datetime] = None
    content_contract_id: str = RISK_DOCUMENT_CONTENT_CONTRACT_ID
    formal_usable: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RiskDocumentReviewCandidate:
    candidate_id: str
    candidate_kind: RiskDocumentReviewCandidateKind
    document_id: str
    symbol: str
    issuer_identity: str
    content_sha256: str
    byte_count: int
    page_count: int
    fact_status: ResearchFeatureStatus
    fact_ids: Tuple[str, ...]
    fact_kinds: Tuple[RiskDocumentFactKind, ...]
    source_reason_codes: Tuple[str, ...]
    manual_review_required: bool = True
    candidate_contract_id: str = (
        RISK_DOCUMENT_REVIEW_CANDIDATE_CONTRACT_ID
    )
    formal_usable: bool = False


@dataclass(frozen=True)
class RiskDocumentReviewCandidateResult:
    status: ResearchFeatureStatus
    candidate: Optional[RiskDocumentReviewCandidate] = None
    formal_usable: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


RiskDocumentContentTransport = Callable[
    ...,
    OfficialRiskDocumentHttpResponse,
]


class _RiskDocumentContentLimitError(ValueError):
    pass


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SAFE_IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _metadata_reasons(
    document: Any,
    fetched_at: Any,
) -> Tuple[str, ...]:
    if not isinstance(document, OfficialRiskDocumentMetadata):
        return ("risk_document_content_metadata_unverified",)
    fetched_at_utc = _aware_utc(fetched_at)
    published_at_utc = _aware_utc(document.published_at)
    try:
        parsed_url = urlsplit(document.source_url)
    except (TypeError, ValueError):
        return ("risk_document_content_metadata_unverified",)
    path = PurePosixPath(parsed_url.path)
    path_parts = path.parts
    document_suffix = document.document_id.removeprefix("cninfo:")
    valid = (
        document.source_contract_id == CNINFO_SOURCE_CONTRACT_ID
        and _safe_identifier(document.document_id)
        and document.document_id.startswith("cninfo:")
        and document_suffix.isdigit()
        and _safe_identifier(document.symbol)
        and len(document.symbol) == 6
        and document.symbol.isdigit()
        and _safe_identifier(document.issuer_identity)
        and document.issuer_identity.startswith("cninfo-org:")
        and isinstance(document.issuer_name, str)
        and bool(document.issuer_name.strip())
        and isinstance(document.title, str)
        and bool(document.title.strip())
        and isinstance(document.source_name, str)
        and bool(document.source_name.strip())
        and document.formal_usable is False
        and fetched_at_utc is not None
        and published_at_utc is not None
        and fetched_at_utc >= published_at_utc
        and parsed_url.scheme == "https"
        and parsed_url.hostname == "static.cninfo.com.cn"
        and parsed_url.netloc == "static.cninfo.com.cn"
        and parsed_url.username is None
        and parsed_url.password is None
        and not parsed_url.query
        and not parsed_url.fragment
        and path.suffix.lower() == ".pdf"
        and path.stem == document_suffix
        and len(path_parts) == 4
        and path_parts[0] == "/"
        and path_parts[1] == "finalpage"
        and bool(path_parts[2])
    )
    return () if valid else (
        "risk_document_content_metadata_unverified",
    )


def _content_result(
    document: Any,
    status: ResearchFeatureStatus,
    fetched_at: Any,
    reasons: Sequence[str],
    *,
    content_sha256: Optional[str] = None,
    byte_count: Optional[int] = None,
    pages: Sequence[OfficialRiskDocumentPage] = (),
) -> OfficialRiskDocumentContentResult:
    valid_document = (
        document
        if isinstance(document, OfficialRiskDocumentMetadata)
        else None
    )
    page_tuple = tuple(pages)
    return OfficialRiskDocumentContentResult(
        status=status,
        document_id=(
            valid_document.document_id
            if valid_document is not None
            else None
        ),
        symbol=(
            valid_document.symbol
            if valid_document is not None
            else None
        ),
        issuer_identity=(
            valid_document.issuer_identity
            if valid_document is not None
            else None
        ),
        content_sha256=content_sha256,
        byte_count=byte_count,
        page_count=len(page_tuple) if page_tuple else None,
        pages=page_tuple,
        fetched_at=_aware_utc(fetched_at),
        reasons=_dedupe(reasons),
    )


def _header(
    headers: Mapping[str, str],
    name: str,
) -> Optional[str]:
    lowered = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == lowered:
            return value if isinstance(value, str) else None
    return None


def _response_reasons(
    document: OfficialRiskDocumentMetadata,
    response: Any,
) -> Tuple[str, ...]:
    if not isinstance(response, OfficialRiskDocumentHttpResponse):
        return ("risk_document_content_response_unverified",)
    if (
        not isinstance(response.status_code, int)
        or isinstance(response.status_code, bool)
        or not isinstance(response.redirect_count, int)
        or isinstance(response.redirect_count, bool)
        or response.redirect_count < 0
        or not isinstance(response.final_url, str)
        or not isinstance(response.headers, Mapping)
        or not isinstance(response.content, bytes)
    ):
        return ("risk_document_content_response_unverified",)
    if (
        300 <= response.status_code < 400
        or response.redirect_count != 0
        or response.final_url != document.source_url
    ):
        return ("risk_document_content_redirect_forbidden",)
    if response.status_code != 200:
        return ("risk_document_content_http_status_unverified",)

    content_type = _header(response.headers, "Content-Type")
    if (
        content_type is None
        or content_type.split(";", 1)[0].strip().lower()
        != "application/pdf"
    ):
        return ("risk_document_content_type_unverified",)

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
        return ("risk_document_content_too_large",)
    if declared_length < 1 or declared_length != actual_length:
        return ("risk_document_content_length_unverified",)
    if not response.content.startswith(b"%PDF-"):
        return ("risk_document_content_signature_unverified",)
    return ()


def _decode_pdf(
    document: OfficialRiskDocumentMetadata,
    response: OfficialRiskDocumentHttpResponse,
    fetched_at: datetime,
) -> OfficialRiskDocumentContentResult:
    content = response.content
    content_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            return _content_result(
                document,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                fetched_at,
                ("risk_document_content_encrypted",),
                content_sha256=content_sha256,
                byte_count=len(content),
            )
        page_count = len(reader.pages)
        if page_count < 1:
            return _content_result(
                document,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                fetched_at,
                ("risk_document_content_page_count_unverified",),
                content_sha256=content_sha256,
                byte_count=len(content),
            )
        if page_count > MAXIMUM_PAGE_COUNT:
            return _content_result(
                document,
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                fetched_at,
                ("risk_document_content_page_limit_exceeded",),
                content_sha256=content_sha256,
                byte_count=len(content),
            )

        pages = []
        total_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if not isinstance(text, str):
                return _content_result(
                    document,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                    fetched_at,
                    ("risk_document_content_text_unverified",),
                    content_sha256=content_sha256,
                    byte_count=len(content),
                )
            if len(text) > MAXIMUM_PAGE_CHARACTERS:
                return _content_result(
                    document,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                    fetched_at,
                    ("risk_document_content_text_too_large",),
                    content_sha256=content_sha256,
                    byte_count=len(content),
                )
            total_characters += len(text)
            if total_characters > MAXIMUM_TOTAL_CHARACTERS:
                return _content_result(
                    document,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                    fetched_at,
                    ("risk_document_content_text_too_large",),
                    content_sha256=content_sha256,
                    byte_count=len(content),
                )
            pages.append(OfficialRiskDocumentPage(page_number, text))
    except Exception:
        # PDF 是不可信外部输入；解析器异常统一压缩为稳定来源状态。
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            fetched_at,
            ("risk_document_content_pdf_unverified",),
            content_sha256=content_sha256,
            byte_count=len(content),
        )

    return _content_result(
        document,
        ResearchFeatureStatus.READY,
        fetched_at,
        (),
        content_sha256=content_sha256,
        byte_count=len(content),
        pages=pages,
    )


def _default_transport(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    allow_redirects: bool,
    stream: bool,
) -> OfficialRiskDocumentHttpResponse:
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
                    int(raw_length)
                    if raw_length is not None
                    else -1
                )
            except ValueError:
                declared_length = -1
            if declared_length > MAXIMUM_PDF_BYTES:
                raise _RiskDocumentContentLimitError

            chunks = []
            byte_count = 0
            for chunk in response.iter_content(
                chunk_size=DOWNLOAD_CHUNK_BYTES
            ):
                if not chunk:
                    continue
                byte_count += len(chunk)
                if byte_count > MAXIMUM_PDF_BYTES:
                    raise _RiskDocumentContentLimitError
                chunks.append(chunk)
            return OfficialRiskDocumentHttpResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                final_url=response.url,
                redirect_count=len(response.history),
                content=b"".join(chunks),
            )


def fetch_official_risk_document_content(
    document: Any,
    *,
    fetched_at: Optional[datetime] = None,
    transport: Optional[RiskDocumentContentTransport] = None,
) -> OfficialRiskDocumentContentResult:
    """受控下载并在内存中解码一份D2巨潮官方PDF。"""

    actual_fetched_at = fetched_at or datetime.now(UTC)
    metadata_reasons = _metadata_reasons(
        document,
        actual_fetched_at,
    )
    if metadata_reasons:
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            metadata_reasons,
        )

    request = transport or _default_transport
    try:
        response = request(
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
    except _RiskDocumentContentLimitError:
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            ("risk_document_content_too_large",),
        )
    except requests.RequestException:
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_FAILED,
            actual_fetched_at,
            ("risk_document_content_request_failed",),
        )
    except (TypeError, ValueError):
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            ("risk_document_content_response_unverified",),
        )

    response_reasons = _response_reasons(document, response)
    if response_reasons:
        return _content_result(
            document,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            actual_fetched_at,
            response_reasons,
        )
    return _decode_pdf(document, response, actual_fetched_at)


def _candidate_result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    candidate: Optional[RiskDocumentReviewCandidate] = None,
) -> RiskDocumentReviewCandidateResult:
    return RiskDocumentReviewCandidateResult(
        status=status,
        candidate=candidate,
        reasons=_dedupe(reasons),
    )


def _candidate_identity_matches(
    content: OfficialRiskDocumentContentResult,
    facts: OfficialRiskDocumentFactResult,
) -> bool:
    return (
        content.document_id == facts.document_id
        and content.symbol == facts.symbol
        and content.issuer_identity == facts.issuer_identity
    )


def build_risk_document_review_candidate(
    content: Any,
    facts: Any,
) -> RiskDocumentReviewCandidateResult:
    """从D4内容结果与D3压缩结果生成不含正文的人工待审候选。"""

    if (
        not isinstance(content, OfficialRiskDocumentContentResult)
        or not isinstance(facts, OfficialRiskDocumentFactResult)
    ):
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_contract_unverified",),
        )
    if content.status != ResearchFeatureStatus.READY:
        return _candidate_result(
            content.status,
            ("risk_document_review_candidate_content_not_ready",),
        )
    if not _candidate_identity_matches(content, facts):
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_identity_mismatch",),
        )
    if (
        not isinstance(content.content_sha256, str)
        or SHA256_PATTERN.fullmatch(content.content_sha256) is None
        or content.content_sha256 != facts.content_sha256
    ):
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_content_mismatch",),
        )
    if facts.status == ResearchFeatureStatus.SOURCE_FAILED:
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_FAILED,
            ("risk_document_review_candidate_source_failed",),
        )
    if facts.status == ResearchFeatureStatus.SOURCE_UNVERIFIED:
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_source_unverified",),
        )
    if facts.status == ResearchFeatureStatus.STALE:
        return _candidate_result(
            ResearchFeatureStatus.STALE,
            ("risk_document_review_candidate_source_stale",),
        )

    if (
        not _safe_identifier(content.document_id)
        or not content.document_id.startswith("cninfo:")
        or not isinstance(content.symbol, str)
        or len(content.symbol) != 6
        or not content.symbol.isdigit()
        or not _safe_identifier(content.issuer_identity)
        or not content.issuer_identity.startswith("cninfo-org:")
        or _aware_utc(content.fetched_at) is None
        or not isinstance(content.byte_count, int)
        or isinstance(content.byte_count, bool)
        or not 1 <= content.byte_count <= MAXIMUM_PDF_BYTES
        or not isinstance(content.page_count, int)
        or isinstance(content.page_count, bool)
        or not 1 <= content.page_count <= MAXIMUM_PAGE_COUNT
        or content.content_contract_id
        != RISK_DOCUMENT_CONTENT_CONTRACT_ID
        or content.formal_usable is not False
        or not isinstance(content.pages, (tuple, list))
        or len(content.pages) != content.page_count
        or any(
            not isinstance(page, OfficialRiskDocumentPage)
            or page.page_number != page_index
            or not isinstance(page.text, str)
            for page_index, page in enumerate(
                content.pages,
                start=1,
            )
        )
        or bool(content.reasons)
        or facts.formal_usable is not False
        or facts.correction_links_complete is not False
        or not isinstance(facts.facts, (tuple, list))
        or not isinstance(facts.relations, (tuple, list))
        or any(
            not isinstance(fact, RiskDocumentFact)
            for fact in facts.facts
        )
        or any(
            not isinstance(
                relation,
                AcceptedRiskDocumentRelation,
            )
            for relation in facts.relations
        )
        or not isinstance(facts.reasons, (tuple, list))
        or any(
            not isinstance(reason, str)
            or REASON_CODE_PATTERN.fullmatch(reason) is None
            for reason in facts.reasons
        )
    ):
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_contract_unverified",),
        )

    if facts.relations:
        if facts.status != ResearchFeatureStatus.READY:
            return _candidate_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                (
                    "risk_document_review_candidate_"
                    "contract_unverified",
                ),
            )
        return _candidate_result(
            ResearchFeatureStatus.MISSING,
            ("risk_document_review_candidate_not_required",),
        )

    if facts.status == ResearchFeatureStatus.MISSING:
        if facts.facts:
            return _candidate_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("risk_document_review_candidate_contract_unverified",),
            )
        candidate_kind = (
            RiskDocumentReviewCandidateKind.FACT_EXTRACTION_MISSING
        )
    elif (
        facts.status == ResearchFeatureStatus.READY
        and facts.facts
        and "risk_document_relation_missing" in facts.reasons
    ):
        candidate_kind = (
            RiskDocumentReviewCandidateKind.RELATION_REVIEW_REQUIRED
        )
    else:
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_contract_unverified",),
        )

    fact_pairs = tuple(
        (fact.fact_id, fact.fact_kind)
        for fact in facts.facts
        if (
            _safe_identifier(fact.fact_id)
            and isinstance(fact.fact_kind, RiskDocumentFactKind)
        )
    )
    if len(fact_pairs) != len(facts.facts) or len(
        {fact_id for fact_id, _ in fact_pairs}
    ) != len(fact_pairs):
        return _candidate_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_review_candidate_contract_unverified",),
        )

    candidate_material = "|".join((
        content.document_id or "",
        content.content_sha256,
        candidate_kind.value,
        *(fact_id for fact_id, _ in fact_pairs),
    ))
    candidate = RiskDocumentReviewCandidate(
        candidate_id=hashlib.sha256(
            candidate_material.encode("utf-8")
        ).hexdigest(),
        candidate_kind=candidate_kind,
        document_id=content.document_id or "",
        symbol=content.symbol or "",
        issuer_identity=content.issuer_identity or "",
        content_sha256=content.content_sha256,
        byte_count=content.byte_count,
        page_count=content.page_count,
        fact_status=facts.status,
        fact_ids=tuple(fact_id for fact_id, _ in fact_pairs),
        fact_kinds=tuple(fact_kind for _, fact_kind in fact_pairs),
        source_reason_codes=tuple(facts.reasons),
    )
    return _candidate_result(
        ResearchFeatureStatus.READY,
        (),
        candidate,
    )
