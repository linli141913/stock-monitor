"""阶段6L-D3风险正文事实与版本化人工关联合同。

本模块只消费已经解码的官方正文页文本，输出压缩事实和研究性关系；不下载
PDF、不连接数据库，也不生成D1正式事件或解除证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import PurePosixPath
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_invalidation_features import (
    LeaderRiskEventEvidence,
    RiskCategory,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


RISK_DOCUMENT_FACT_EXTRACTOR_VERSION = (
    "radar-leader-risk-document-fact-v1"
)
RISK_DOCUMENT_MAPPING_CONTRACT_ID = (
    "radar-leader-risk-document-mapping-v1"
)
MAXIMUM_PAGE_COUNT = 200
MAXIMUM_PAGE_CHARACTERS = 100_000
MAXIMUM_TOTAL_CHARACTERS = 2_000_000
SAFE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECURITY_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
AUDIT_REPORT_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9\u4e00-\u9fff"
    r"〔〕（）()\[\]［］第号字审._-]{2,80}$"
)
UTC = timezone.utc


class RiskDocumentFactKind(str, Enum):
    CASE_ID = "case_id"
    REPORTING_PERIOD = "reporting_period"
    AUDIT_REPORT_ID = "audit_report_id"
    REFERENCED_DOCUMENT_ID = "referenced_document_id"
    EFFECTIVE_DATE = "effective_date"
    EFFECTIVE_INTERVAL = "effective_interval"


class RiskDocumentRelationKind(str, Enum):
    SUPERSEDES = "supersedes"
    RESOLVES = "resolves"


@dataclass(frozen=True)
class OfficialRiskDocumentPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class RiskDocumentFact:
    fact_id: str
    fact_kind: RiskDocumentFactKind
    normalized_value: str
    page_number: int
    fragment_sha256: str
    extractor_version: str = RISK_DOCUMENT_FACT_EXTRACTOR_VERSION


@dataclass(frozen=True)
class RiskDocumentVersionReview:
    review_id: str
    mapping_version: str
    relation_kind: RiskDocumentRelationKind
    review_method: str
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    source_document_id: str
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    decision_summary: str


@dataclass(frozen=True)
class AcceptedRiskDocumentRelation:
    relation_id: str
    mapping_version: str
    relation_kind: RiskDocumentRelationKind
    source_document_id: str
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    reviewed_at: datetime
    mapping_contract_id: str = RISK_DOCUMENT_MAPPING_CONTRACT_ID


@dataclass(frozen=True)
class OfficialRiskDocumentFactInput:
    as_of: datetime
    document: OfficialRiskDocumentMetadata
    content_sha256: str
    pages: Tuple[OfficialRiskDocumentPage, ...]
    extracted_at: datetime
    source_status: ResearchFeatureStatus
    event_versions: Tuple[LeaderRiskEventEvidence, ...]
    reviews: Tuple[RiskDocumentVersionReview, ...]


@dataclass(frozen=True)
class OfficialRiskDocumentFactResult:
    status: ResearchFeatureStatus
    document_id: Optional[str] = None
    symbol: Optional[str] = None
    issuer_identity: Optional[str] = None
    content_sha256: Optional[str] = None
    facts: Tuple[RiskDocumentFact, ...] = ()
    relations: Tuple[AcceptedRiskDocumentRelation, ...] = ()
    correction_links_complete: bool = False
    formal_usable: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


CASE_PATTERN = re.compile(
    r"(?:案号|案件编号|立案编号|立案告知书编号)"
    r"\s*[：:]\s*([^\n；;。]{2,160})"
)
REPORTING_PERIOD_PATTERN = re.compile(
    r"(?:报告期|所属报告期)\s*[：:]\s*"
    r"((?:19|20)\d{2}(?:年度|年半年度|年第一季度|年第三季度))"
)
AUDIT_REPORT_PATTERN = re.compile(
    r"(?:审计报告编号|审计报告文号|报告编号)"
    r"\s*[：:]\s*([^\n；;。]{2,160})"
)
REFERENCED_DOCUMENT_PATTERN = re.compile(
    r"(?:原公告编号|被更正公告编号|前次公告编号)"
    r"\s*[：:]\s*(\d{7,12})"
)
DATE_TEXT = (
    r"((?:19|20)\d{2})年"
    r"(0?[1-9]|1[0-2])月"
    r"(0?[1-9]|[12]\d|3[01])日"
)
EFFECTIVE_DATE_PATTERN = re.compile(
    r"(?:生效日期|解除限售日期|实施完成日期)"
    r"\s*[：:]\s*" + DATE_TEXT
)
EFFECTIVE_INTERVAL_PATTERN = re.compile(
    r"(?:减持期间|实施区间)\s*[：:]\s*"
    + DATE_TEXT
    + r"\s*(?:至|到|[-—－])\s*"
    + DATE_TEXT
)


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


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(SAFE_IDENTIFIER_PATTERN.fullmatch(value.strip()))
    )


def _result(
    input_value: Any,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    facts: Sequence[RiskDocumentFact] = (),
    relations: Sequence[AcceptedRiskDocumentRelation] = (),
) -> OfficialRiskDocumentFactResult:
    document = (
        input_value.document
        if isinstance(input_value, OfficialRiskDocumentFactInput)
        and isinstance(
            input_value.document,
            OfficialRiskDocumentMetadata,
        )
        else None
    )
    content_sha256 = (
        input_value.content_sha256
        if isinstance(input_value, OfficialRiskDocumentFactInput)
        and isinstance(input_value.content_sha256, str)
        and SHA256_PATTERN.fullmatch(input_value.content_sha256)
        else None
    )
    return OfficialRiskDocumentFactResult(
        status=status,
        document_id=document.document_id if document else None,
        symbol=document.symbol if document else None,
        issuer_identity=(
            document.issuer_identity if document else None
        ),
        content_sha256=content_sha256,
        facts=tuple(facts),
        relations=tuple(relations),
        reasons=_dedupe(reasons),
    )


def _metadata_reasons(
    document: Any,
) -> Tuple[str, ...]:
    if not isinstance(document, OfficialRiskDocumentMetadata):
        return ("risk_document_metadata_unverified",)
    if (
        not isinstance(document.source_url, str)
        or not isinstance(document.document_id, str)
        or not isinstance(document.symbol, str)
        or not isinstance(document.issuer_identity, str)
    ):
        return ("risk_document_metadata_unverified",)
    try:
        parsed_url = urlsplit(document.source_url)
    except (TypeError, ValueError):
        return ("risk_document_metadata_unverified",)
    path = PurePosixPath(parsed_url.path)
    source_document_id = (
        document.document_id.removeprefix("cninfo:")
        if isinstance(document.document_id, str)
        else ""
    )
    valid = (
        document.source_contract_id == CNINFO_SOURCE_CONTRACT_ID
        and _safe_identifier(document.document_id)
        and document.document_id.startswith("cninfo:")
        and SECURITY_CODE_PATTERN.fullmatch(document.symbol or "")
        is not None
        and _safe_identifier(document.issuer_identity)
        and document.issuer_identity.startswith("cninfo-org:")
        and _required_text(document.issuer_name)
        and _required_text(document.title)
        and _required_text(document.source_name)
        and isinstance(document.candidate_category, RiskCategory)
    )
    valid = bool(valid) and (
        _aware_utc(document.published_at) is not None
        and parsed_url.scheme == "https"
        and parsed_url.hostname == "static.cninfo.com.cn"
        and parsed_url.username is None
        and parsed_url.password is None
        and not parsed_url.query
        and not parsed_url.fragment
        and path.suffix.lower() == ".pdf"
        and path.stem == source_document_id
        and document.formal_usable is False
    )
    return () if valid else ("risk_document_metadata_unverified",)


def _content_reasons(
    input_value: OfficialRiskDocumentFactInput,
) -> Tuple[str, ...]:
    reasons = []
    if (
        not isinstance(input_value.content_sha256, str)
        or SHA256_PATTERN.fullmatch(
            input_value.content_sha256
        ) is None
    ):
        reasons.append("risk_document_content_hash_unverified")

    pages = input_value.pages
    if (
        not isinstance(pages, (tuple, list))
        or not pages
        or any(
            not isinstance(page, OfficialRiskDocumentPage)
            for page in pages
        )
    ):
        reasons.append("risk_document_pages_unverified")
        return _dedupe(reasons)
    if len(pages) > MAXIMUM_PAGE_COUNT:
        reasons.append("risk_document_content_too_large")
        return _dedupe(reasons)

    page_numbers = []
    total_characters = 0
    for page in pages:
        if (
            not isinstance(page.page_number, int)
            or isinstance(page.page_number, bool)
            or not isinstance(page.text, str)
        ):
            reasons.append("risk_document_pages_unverified")
            continue
        page_numbers.append(page.page_number)
        page_characters = len(page.text)
        total_characters += page_characters
        if page_characters > MAXIMUM_PAGE_CHARACTERS:
            reasons.append("risk_document_content_too_large")
    if page_numbers != list(range(1, len(pages) + 1)):
        reasons.append("risk_document_pages_unverified")
    if total_characters > MAXIMUM_TOTAL_CHARACTERS:
        reasons.append("risk_document_content_too_large")

    as_of = _aware_utc(input_value.as_of)
    extracted_at = _aware_utc(input_value.extracted_at)
    published_at = _aware_utc(input_value.document.published_at)
    if (
        as_of is None
        or extracted_at is None
        or published_at is None
    ):
        reasons.append("risk_document_timestamp_unverified")
    else:
        if extracted_at < published_at:
            reasons.append(
                "risk_document_extracted_before_published"
            )
        if extracted_at > as_of:
            reasons.append("risk_document_extracted_in_future")
    return _dedupe(reasons)


def _normalized_identifier(value: str) -> str:
    return re.sub(r"\s+", "", value).strip("，,；;。")


def _normalized_reporting_period(value: str) -> Optional[str]:
    match = re.fullmatch(
        r"((?:19|20)\d{2})(年度|年半年度|年第一季度|年第三季度)",
        value,
    )
    if match is None:
        return None
    year, suffix = match.groups()
    suffixes = {
        "年度": "",
        "年半年度": "-H1",
        "年第一季度": "-Q1",
        "年第三季度": "-Q3",
    }
    return f"{year}{suffixes[suffix]}"


def _normalized_date(
    year: str,
    month: str,
    day: str,
) -> Optional[str]:
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            tzinfo=UTC,
        ).date().isoformat()
    except ValueError:
        return None


def _fact(
    document_id: str,
    fact_kind: RiskDocumentFactKind,
    normalized_value: str,
    page_number: int,
    fragment: str,
) -> RiskDocumentFact:
    identity = (
        f"{document_id}|{fact_kind.value}|"
        f"{normalized_value}|{page_number}"
    )
    return RiskDocumentFact(
        fact_id=(
            "riskfact:"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        ),
        fact_kind=fact_kind,
        normalized_value=normalized_value,
        page_number=page_number,
        fragment_sha256=hashlib.sha256(
            fragment.encode("utf-8")
        ).hexdigest(),
    )


def _extract_page_facts(
    document_id: str,
    page: OfficialRiskDocumentPage,
) -> Tuple[RiskDocumentFact, ...]:
    text = page.text.replace("\r\n", "\n").replace("\r", "\n")
    facts = []

    for match in CASE_PATTERN.finditer(text):
        raw_value = _normalized_identifier(match.group(1))
        if 2 <= len(raw_value) <= 160:
            value = (
                "case:"
                + hashlib.sha256(
                    raw_value.encode("utf-8")
                ).hexdigest()
            )
            facts.append(_fact(
                document_id,
                RiskDocumentFactKind.CASE_ID,
                value,
                page.page_number,
                match.group(0).strip(),
            ))

    for match in REPORTING_PERIOD_PATTERN.finditer(text):
        value = _normalized_reporting_period(match.group(1))
        if value is not None:
            facts.append(_fact(
                document_id,
                RiskDocumentFactKind.REPORTING_PERIOD,
                value,
                page.page_number,
                match.group(0).strip(),
            ))

    for match in AUDIT_REPORT_PATTERN.finditer(text):
        raw_value = _normalized_identifier(match.group(1))
        if (
            AUDIT_REPORT_IDENTIFIER_PATTERN.fullmatch(
                raw_value
            )
            is not None
            and any(character.isdigit() for character in raw_value)
        ):
            value = (
                "audit-report:"
                + hashlib.sha256(
                    raw_value.encode("utf-8")
                ).hexdigest()
            )
            facts.append(_fact(
                document_id,
                RiskDocumentFactKind.AUDIT_REPORT_ID,
                value,
                page.page_number,
                match.group(0).strip(),
            ))

    for match in REFERENCED_DOCUMENT_PATTERN.finditer(text):
        facts.append(_fact(
            document_id,
            RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
            f"cninfo:{match.group(1)}",
            page.page_number,
            match.group(0).strip(),
        ))

    for match in EFFECTIVE_DATE_PATTERN.finditer(text):
        value = _normalized_date(
            match.group(1),
            match.group(2),
            match.group(3),
        )
        if value is not None:
            facts.append(_fact(
                document_id,
                RiskDocumentFactKind.EFFECTIVE_DATE,
                value,
                page.page_number,
                match.group(0).strip(),
            ))

    for match in EFFECTIVE_INTERVAL_PATTERN.finditer(text):
        start = _normalized_date(
            match.group(1),
            match.group(2),
            match.group(3),
        )
        end = _normalized_date(
            match.group(4),
            match.group(5),
            match.group(6),
        )
        if start is not None and end is not None and start <= end:
            facts.append(_fact(
                document_id,
                RiskDocumentFactKind.EFFECTIVE_INTERVAL,
                f"{start}/{end}",
                page.page_number,
                match.group(0).strip(),
            ))
    return tuple(facts)


def _extract_facts(
    document_id: str,
    pages: Sequence[OfficialRiskDocumentPage],
) -> Tuple[RiskDocumentFact, ...]:
    facts = [
        fact
        for page in pages
        for fact in _extract_page_facts(document_id, page)
    ]
    seen = set()
    unique = []
    for fact in facts:
        key = (fact.fact_kind, fact.normalized_value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(fact)
    return tuple(unique)


def _event_reasons(
    events: Any,
    document: OfficialRiskDocumentMetadata,
) -> Tuple[str, ...]:
    if (
        not isinstance(events, (tuple, list))
        or any(
            not isinstance(event, LeaderRiskEventEvidence)
            for event in events
        )
    ):
        return ("risk_document_event_contract_unverified",)
    keys = []
    for event in events:
        if (
            not _safe_identifier(event.event_id)
            or not _safe_identifier(event.event_version)
            or not _safe_identifier(event.document_id)
            or event.symbol != document.symbol
            or event.issuer_identity != document.issuer_identity
            or not isinstance(event.category, RiskCategory)
            or _aware_utc(event.published_at) is None
            or _aware_utc(event.effective_from) is None
        ):
            return ("risk_document_event_contract_unverified",)
        keys.append((event.event_id, event.event_version))
    if len(keys) != len(set(keys)):
        return ("risk_document_event_identity_duplicate",)
    return ()


def _review_collection_reasons(
    reviews: Any,
) -> Tuple[str, ...]:
    if (
        not isinstance(reviews, (tuple, list))
        or any(
            not isinstance(review, RiskDocumentVersionReview)
            for review in reviews
        )
    ):
        return ("risk_document_review_contract_unverified",)
    if any(
        not _safe_identifier(value)
        for review in reviews
        for value in (
            review.review_id,
            review.mapping_version,
            review.source_document_id,
            review.target_event_id,
            review.target_event_version,
        )
    ):
        return ("risk_document_review_identity_unverified",)
    review_ids = [review.review_id for review in reviews]
    mapping_versions = [
        review.mapping_version for review in reviews
    ]
    if (
        len(review_ids) != len(set(review_ids))
        or len(mapping_versions) != len(set(mapping_versions))
    ):
        return ("risk_document_review_identity_duplicate",)
    return ()


def _matching_fact(
    facts_by_id: Mapping[str, RiskDocumentFact],
    basis_fact_ids: Sequence[str],
    fact_kind: RiskDocumentFactKind,
    value: str,
) -> bool:
    return any(
        fact_id in facts_by_id
        and facts_by_id[fact_id].fact_kind == fact_kind
        and facts_by_id[fact_id].normalized_value == value
        for fact_id in basis_fact_ids
    )


def _validate_review(
    review: RiskDocumentVersionReview,
    input_value: OfficialRiskDocumentFactInput,
    event_by_key: Mapping[
        Tuple[str, str],
        LeaderRiskEventEvidence,
    ],
    facts_by_id: Mapping[str, RiskDocumentFact],
    as_of: datetime,
) -> Tuple[
    Optional[AcceptedRiskDocumentRelation],
    Tuple[str, ...],
    bool,
]:
    identity_values = (
        review.review_id,
        review.mapping_version,
        review.reviewer_key,
        review.source_document_id,
        review.target_event_id,
        review.target_event_version,
        review.target_document_id,
    )
    if (
        any(not _safe_identifier(value) for value in identity_values)
        or not isinstance(
            review.relation_kind,
            RiskDocumentRelationKind,
        )
        or not _required_text(review.decision_summary)
    ):
        return (
            None,
            ("risk_document_review_identity_unverified",),
            False,
        )
    if review.review_method != "manual":
        return (
            None,
            ("risk_document_review_method_unverified",),
            False,
        )
    if (
        review.source_document_id
        != input_value.document.document_id
    ):
        return (
            None,
            ("risk_document_review_identity_mismatch",),
            False,
        )

    event = event_by_key.get((
        review.target_event_id,
        review.target_event_version,
    ))
    if event is None:
        return (
            None,
            ("risk_document_review_target_missing",),
            False,
        )
    if (
        review.target_document_id != event.document_id
        or event.symbol != input_value.document.symbol
        or event.issuer_identity
        != input_value.document.issuer_identity
    ):
        return (
            None,
            ("risk_document_review_identity_mismatch",),
            False,
        )
    if event.document_id == input_value.document.document_id:
        return (
            None,
            (
                "risk_document_review_"
                "self_relation_forbidden",
            ),
            False,
        )
    event_published_at = _aware_utc(event.published_at)
    event_effective_from = _aware_utc(event.effective_from)
    current_published_at = _aware_utc(
        input_value.document.published_at
    )
    if (
        event_published_at is None
        or event_effective_from is None
        or current_published_at is None
        or event_published_at >= current_published_at
        or event_effective_from > current_published_at
    ):
        return (
            None,
            ("risk_document_review_target_not_prior",),
            False,
        )

    reviewed_at = _aware_utc(review.reviewed_at)
    effective_until = (
        _aware_utc(review.effective_until)
        if review.effective_until is not None
        else None
    )
    published_at = _aware_utc(
        input_value.document.published_at
    )
    if (
        reviewed_at is None
        or published_at is None
        or (
            review.effective_until is not None
            and effective_until is None
        )
    ):
        return (
            None,
            ("risk_document_review_timestamp_unverified",),
            False,
        )
    if reviewed_at > as_of:
        return (
            None,
            ("risk_document_reviewed_at_future",),
            False,
        )
    if reviewed_at < published_at:
        return (
            None,
            ("risk_document_review_precedes_document",),
            False,
        )
    if (
        effective_until is not None
        and effective_until < reviewed_at
    ):
        return (
            None,
            ("risk_document_review_effective_range_unverified",),
            False,
        )

    basis_fact_ids = review.basis_fact_ids
    if (
        not isinstance(basis_fact_ids, (tuple, list))
        or not basis_fact_ids
        or any(
            not _safe_identifier(fact_id)
            for fact_id in basis_fact_ids
        )
        or len(basis_fact_ids) != len(set(basis_fact_ids))
        or any(
            fact_id not in facts_by_id
            for fact_id in basis_fact_ids
        )
    ):
        return (
            None,
            ("risk_document_review_reference_missing",),
            False,
        )
    if not _matching_fact(
        facts_by_id,
        basis_fact_ids,
        RiskDocumentFactKind.REFERENCED_DOCUMENT_ID,
        event.document_id,
    ):
        return (
            None,
            ("risk_document_review_reference_missing",),
            False,
        )
    if event.category in {
        RiskCategory.INVESTIGATION,
        RiskCategory.LITIGATION,
    } and (
        not isinstance(event.case_id, str)
        or not _matching_fact(
            facts_by_id,
            basis_fact_ids,
            RiskDocumentFactKind.CASE_ID,
            event.case_id,
        )
    ):
        return (
            None,
            ("risk_document_review_case_mismatch",),
            False,
        )
    if event.category in {
        RiskCategory.EARNINGS,
        RiskCategory.AUDIT,
    } and (
        not isinstance(event.reporting_period, str)
        or not _matching_fact(
            facts_by_id,
            basis_fact_ids,
            RiskDocumentFactKind.REPORTING_PERIOD,
            event.reporting_period,
        )
    ):
        return (
            None,
            ("risk_document_review_reporting_period_mismatch",),
            False,
        )

    if (
        review.relation_kind
        == RiskDocumentRelationKind.SUPERSEDES
    ):
        if (
            not _safe_identifier(
                review.replacement_event_version
            )
            or review.replacement_event_version
            == event.event_version
        ):
            return (
                None,
                (
                    "risk_document_review_"
                    "replacement_version_invalid",
                ),
                False,
            )
    elif review.replacement_event_version is not None:
        return (
            None,
            (
                "risk_document_review_"
                "replacement_version_unexpected",
            ),
            False,
        )

    if effective_until is not None and effective_until < as_of:
        return None, ("risk_document_review_expired",), True

    return (
        AcceptedRiskDocumentRelation(
            relation_id=review.review_id,
            mapping_version=review.mapping_version,
            relation_kind=review.relation_kind,
            source_document_id=review.source_document_id,
            target_event_id=review.target_event_id,
            target_event_version=review.target_event_version,
            target_document_id=review.target_document_id,
            replacement_event_version=(
                review.replacement_event_version
            ),
            basis_fact_ids=tuple(review.basis_fact_ids),
            reviewed_at=reviewed_at,
        ),
        (),
        False,
    )


def extract_official_risk_document_facts(
    input_value: Any,
) -> OfficialRiskDocumentFactResult:
    """提取压缩官方正文事实并验证研究性人工关系。"""

    if not isinstance(
        input_value,
        OfficialRiskDocumentFactInput,
    ):
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_contract_unverified",),
        )
    if not isinstance(
        input_value.source_status,
        ResearchFeatureStatus,
    ):
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_source_status_unverified",),
        )
    if input_value.source_status == ResearchFeatureStatus.SOURCE_FAILED:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_FAILED,
            ("risk_document_source_failed",),
        )
    if input_value.source_status == ResearchFeatureStatus.MISSING:
        return _result(
            input_value,
            ResearchFeatureStatus.MISSING,
            ("risk_document_source_missing",),
        )
    if input_value.source_status == ResearchFeatureStatus.STALE:
        return _result(
            input_value,
            ResearchFeatureStatus.STALE,
            ("risk_document_source_stale",),
        )
    if (
        input_value.source_status
        == ResearchFeatureStatus.SOURCE_UNVERIFIED
    ):
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_document_source_unverified",),
        )

    metadata_reasons = _metadata_reasons(input_value.document)
    if metadata_reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            metadata_reasons,
        )
    content_reasons = _content_reasons(input_value)
    if content_reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            content_reasons,
        )
    if not any(page.text.strip() for page in input_value.pages):
        return _result(
            input_value,
            ResearchFeatureStatus.MISSING,
            ("risk_document_text_missing",),
        )
    event_reasons = _event_reasons(
        input_value.event_versions,
        input_value.document,
    )
    if event_reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            event_reasons,
        )
    review_collection_reasons = _review_collection_reasons(
        input_value.reviews
    )
    if review_collection_reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            review_collection_reasons,
        )

    facts = _extract_facts(
        input_value.document.document_id,
        input_value.pages,
    )
    if not facts:
        return _result(
            input_value,
            ResearchFeatureStatus.MISSING,
            ("risk_document_facts_missing",),
        )
    if not input_value.reviews:
        return _result(
            input_value,
            ResearchFeatureStatus.READY,
            ("risk_document_relation_missing",),
            facts=facts,
        )

    as_of = _aware_utc(input_value.as_of)
    assert as_of is not None
    event_by_key = {
        (event.event_id, event.event_version): event
        for event in input_value.event_versions
    }
    facts_by_id = {
        fact.fact_id: fact for fact in facts
    }
    relations = []
    reasons = []
    expired_count = 0
    for review in input_value.reviews:
        relation, review_reasons, expired = _validate_review(
            review,
            input_value,
            event_by_key,
            facts_by_id,
            as_of,
        )
        expired_count += int(expired)
        if not expired:
            reasons.extend(review_reasons)
        if relation is not None:
            relations.append(relation)

    if reasons:
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons,
            facts=facts,
        )
    if expired_count == len(input_value.reviews):
        return _result(
            input_value,
            ResearchFeatureStatus.STALE,
            ("risk_document_review_expired",),
            facts=facts,
        )
    active_relations_by_target: Dict[
        Tuple[str, str, str],
        Tuple[RiskDocumentRelationKind, Optional[str]],
    ] = {}
    for relation in relations:
        key = (
            relation.source_document_id,
            relation.target_event_id,
            relation.target_event_version,
        )
        value = (
            relation.relation_kind,
            relation.replacement_event_version,
        )
        existing = active_relations_by_target.get(key)
        if existing is None:
            active_relations_by_target[key] = value
            continue
        reason = (
            "risk_document_review_active_duplicate"
            if existing == value
            else "risk_document_review_conflict"
        )
        return _result(
            input_value,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            (reason,),
            facts=facts,
        )
    return _result(
        input_value,
        ResearchFeatureStatus.READY,
        (
            ("risk_document_review_expired_ignored",)
            if expired_count
            else ()
        ),
        facts=facts,
        relations=relations,
    )
