"""从官方年报页文本提取可重放的主营事实。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any, Optional, Sequence, Tuple
import unicodedata

from radar.leader_business_annual_report_selector import (
    LeaderBusinessAnnualReportSelectionResult,
    LeaderBusinessAnnualReportSelectionStatus,
)
from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_runtime_candidate_plan import LeaderRuntimeCandidatePlanItem
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)


BUSINESS_DOCUMENT_FACT_CONTRACT_ID = (
    "radar-leader-business-document-facts-v1"
)
BUSINESS_SECTION_ANCHORS = ("主要业务", "主营业务", "核心业务", "营业收入构成")
BUSINESS_OVERVIEW_HEADINGS = (
    "报告期内公司从事的业务情况",
    "报告期内公司从事的主要业务",
    "报告期内公司所从事的主要业务",
)
BUSINESS_OVERVIEW_ENDS = (
    "报告期内公司所处行业情况",
    "报告期内公司新增重要非主营业务的说明",
    "新增重要非主营业务情况",
    "核心竞争力分析",
    "主营业务分析",
    "非主营业务分析",
    "收入与成本",
    "资产及负债状况分析",
    "投资状况分析",
    "公司未来发展的展望",
)
BUSINESS_SUBHEADING_PATTERN = re.compile(
    r"(?:^|\n)\s*[（(][一二三四五六七八九十0-9]+[）)]\s*"
    r"([^\n。；;]{2,30}?(?:业务|产品))"
    r"(?=\s*(?:\n|公司|采购|生产|销售|经营))"
)
BUSINESS_NUMBERED_HEADING_PATTERN = re.compile(
    r"(?:^|\n)\s*[一二三四五六七八九十0-9]+[、.．]\s*"
    r"([^\n:：。；;]{2,30})\s*[:：]?"
    r"(?=\s*(?:\n|公司|本公司|采购|生产|销售|经营|通过|报告期))"
)
EXISTING_NAMED_BUSINESS_PATTERN = re.compile(
    r"基于现有"
    r"(?!相关|其他|公司|主要|主营|整体|新|核心|业务)"
    r"([^\n。；;,，]{2,20}?业务)[,，]"
)
EXPLICIT_MAIN_BUSINESS_PATTERNS = (
    re.compile(r"公司主要从事\s*([^，,。\n]{2,160})"),
    re.compile(
        r"公司(?:构建起)?以\s*([^。\n]{2,180}?)\s*为(?:核心)?主业"
    ),
    re.compile(
        r"公司的主营业务(?:包括|为)\s*"
        r"([^。\n]{2,160}?)(?=[，,](?:公司|本公司|主要产品)|。|\n)"
    ),
)
PRODUCT_PATTERN = re.compile(
    r"(?:主要产品|主营产品|核心产品|主要业务)\s*"
    r"(?:包括|涵盖|为|是)\s*[:：]?\s*([^。\n]{2,240})"
)
NUMBERED_NAMED_PRODUCT_DESCRIPTION_PATTERN = re.compile(
    r"(?:^|\n)\s*\d+[、.．]\s*"
    r"([^\n(（:：。；;]{2,30}?)"
    r"(?:[(（][^()\n（）]{2,40}[)）])?为"
)
COMPANY_PRODUCT_OUTPUT_PATTERN = re.compile(
    r"公司产品以([^，,。\n]{2,30}?)为主[，,]"
    r"占公司([^，,。\n]{2,20}?)产量"
)
ATOMIC_METALS_ACTIVITY_PATTERN = re.compile(
    r"(有色金属|贵金属)(?:的)?(?:采选|冶炼|加工)"
)
COMPANY_EXPLICIT_BUSINESS_LIST_PATTERN = re.compile(
    r"报告期内[,，]\s*公司(?:从事的)?主要业务(?:包括|为)\s*"
    r"([^。\n]{2,240})"
)
INDUSTRY_DECLARATION_PATTERN = re.compile(
    r"(?<![一-鿿A-Za-z0-9])所属行业\s*[:：]?\s*([^\n。；;]{2,80})"
)
TERM_SPLIT_PATTERN = re.compile(r"[、,，;；]|以及|及|和")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GENERIC_TERMS = frozenset({
    "产品", "服务", "业务", "行业", "项目", "技术", "平台", "系统",
})
STRUCTURAL_TERM_MARKERS = (
    "主要产品及用途",
    "采购模式",
    "生产模式",
    "销售模式",
    "经营模式",
    "核心竞争力分析",
    "收入与成本",
    "费用",
    "研发投入",
    "现金流",
    "投资状况分析",
    "资产及负债状况分析",
    "业绩驱动因素",
)
STRUCTURAL_TERMS = frozenset({
    "主要", "主营", "公司主要", "公司主营", "公司的主要",
    "主要业务及其变化", "报告期内主要",
    "主要业务的情况", "主要产品和服务的情况",
})


@dataclass(frozen=True)
class OfficialBusinessEvidenceFragment:
    page_number: int
    fragment_sha256: str
    text: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class OfficialBusinessFactResult:
    status: AutomaticBusinessEvidenceStatus
    symbol: Optional[str]
    industry_code: Optional[str]
    industry_release_id: Optional[str]
    document_id: Optional[str] = None
    document_version: Optional[str] = None
    content_sha256: Optional[str] = None
    business_terms: Tuple[str, ...] = field(default_factory=tuple, repr=False)
    fragments: Tuple[OfficialBusinessEvidenceFragment, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    source_time: Optional[datetime] = None
    validated_at: Optional[datetime] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = BUSINESS_DOCUMENT_FACT_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: AutomaticBusinessEvidenceStatus,
    *,
    plan_item: Any,
    selection: Any,
    content: Any,
    terms: Sequence[str] = (),
    fragments: Sequence[OfficialBusinessEvidenceFragment] = (),
    reasons: Sequence[str] = (),
) -> OfficialBusinessFactResult:
    valid_plan = (
        plan_item
        if type(plan_item) is LeaderRuntimeCandidatePlanItem
        else None
    )
    valid_selection = (
        selection
        if type(selection) is LeaderBusinessAnnualReportSelectionResult
        else None
    )
    valid_content = (
        content
        if type(content) is OfficialBusinessDocumentContentResult
        else None
    )
    document = valid_selection.document if valid_selection else None
    return OfficialBusinessFactResult(
        status=status,
        symbol=valid_plan.symbol if valid_plan else None,
        industry_code=valid_plan.industry_code if valid_plan else None,
        industry_release_id=(
            valid_plan.industry_release_id if valid_plan else None
        ),
        document_id=document.document_id if document else None,
        document_version=document.document_version if document else None,
        content_sha256=(
            valid_content.content_sha256 if valid_content else None
        ),
        business_terms=tuple(terms),
        fragments=tuple(fragments),
        source_time=document.published_at if document else None,
        validated_at=(
            valid_content.fetched_at if valid_content else None
        ),
        reasons=_dedupe(reasons),
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _bound(
    plan_item: Any,
    selection: Any,
    content: Any,
) -> bool:
    if (
        type(plan_item) is not LeaderRuntimeCandidatePlanItem
        or type(selection) is not LeaderBusinessAnnualReportSelectionResult
        or selection.status
        is not LeaderBusinessAnnualReportSelectionStatus.READY
        or selection.document is None
        or type(content) is not OfficialBusinessDocumentContentResult
        or content.status is not AutomaticBusinessEvidenceStatus.READY
        or content.document_kind
        is not OfficialBusinessDocumentKind.ANNUAL_REPORT
        or content.document_id != selection.document.document_id
        or content.document_version != selection.document.document_version
        or content.symbol != plan_item.symbol
        or content.symbol != selection.document.symbol
        or content.issuer_identity != selection.document.issuer_identity
        or not isinstance(content.content_sha256, str)
        or SHA256_PATTERN.fullmatch(content.content_sha256) is None
        or not isinstance(content.pages, tuple)
        or not content.pages
        or content.page_count != len(content.pages)
        or not _aware(content.fetched_at)
        or selection.document.published_at > content.fetched_at
    ):
        return False
    page_numbers = []
    for page in content.pages:
        if (
            type(page) is not OfficialBusinessDocumentPage
            or not isinstance(page.page_number, int)
            or isinstance(page.page_number, bool)
            or page.page_number < 1
            or not isinstance(page.text, str)
        ):
            return False
        page_numbers.append(page.page_number)
    return page_numbers == sorted(set(page_numbers))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _normalize_layout(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(
        r"(?<=[\u4e00-\u9fff])[\t \u3000]+(?=[\u4e00-\u9fff])",
        "",
        normalized,
    )


def _directory_page(text: str) -> bool:
    head = text[:120]
    return "目录" in head and bool(
        re.search(r"(?:\.{4,}|…{2,})\s*\d+", text)
    )


def _clean_term(value: str, industry_name: str) -> Optional[str]:
    term = _normalize(value).strip("：:。；;、,，‘’“”\"'()（）")
    term = re.sub(r"^(?:以|包括)", "", term)
    term = re.sub(r"除(?:自用|内部自用)外.*$", "", term)
    if (
        not 2 <= len(term) <= 20
        or term in GENERIC_TERMS
        or term in STRUCTURAL_TERMS
        or term.startswith((
            "相关", "其他", "公司主要", "公司主营", "公司的主要",
            "公司的主营", "报告期内主要", "主要业务及",
            "主要销售", "经销商用以",
        ))
        or term.endswith("模式")
        or any(marker in term for marker in STRUCTURAL_TERM_MARKERS)
        or term == _normalize(industry_name)
        or not re.search(r"[\u4e00-\u9fffA-Za-z]", term)
    ):
        return None
    return term


def extract_official_business_facts(
    plan_item: Any,
    selection: Any,
    content: Any,
) -> OfficialBusinessFactResult:
    """只接受明确章节、冻结行业身份和显式产品列表。"""

    if not _bound(plan_item, selection, content):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            plan_item=plan_item,
            selection=selection,
            content=content,
            reasons=("business_fact_identity_unverified",),
        )
    assert isinstance(plan_item, LeaderRuntimeCandidatePlanItem)
    assert isinstance(content, OfficialBusinessDocumentContentResult)
    combined = _normalize("\n".join(page.text for page in content.pages))
    if re.search(rf"(?<!\d){re.escape(plan_item.symbol)}(?!\d)", combined) is None:
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            plan_item=plan_item,
            selection=selection,
            content=content,
            reasons=("business_fact_symbol_unverified",),
        )
    declared_industries = tuple(
        _normalize(match.group(1))
        for page in content.pages
        for match in INDUSTRY_DECLARATION_PATTERN.finditer(page.text)
    )
    expected_industry = _normalize(plan_item.industry_name)
    if (
        declared_industries
        and all(
            expected_industry not in declaration
            for declaration in declared_industries
        )
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            plan_item=plan_item,
            selection=selection,
            content=content,
            reasons=("business_fact_industry_unverified",),
        )

    fragments = []
    terms = []
    seen_fragments = set()
    inside_business_overview = False
    for page in content.pages:
        page_text = _normalize_layout(page.text)
        if not inside_business_overview and _directory_page(page_text):
            continue
        heading_matches = tuple(
            (position, heading)
            for heading in BUSINESS_OVERVIEW_HEADINGS
            if (position := page_text.find(heading)) >= 0
        )
        if heading_matches:
            inside_business_overview = True
            position, heading = min(heading_matches)
            page_text = page_text[position + len(heading):]
        if not inside_business_overview:
            continue
        end_positions = tuple(
            position
            for heading in BUSINESS_OVERVIEW_ENDS
            if (position := page_text.find(heading)) >= 0
        )
        ends_overview = bool(end_positions)
        if ends_overview:
            page_text = page_text[:min(end_positions)]
        for pattern in (
            BUSINESS_SUBHEADING_PATTERN,
            BUSINESS_NUMBERED_HEADING_PATTERN,
            EXISTING_NAMED_BUSINESS_PATTERN,
        ):
            for match in pattern.finditer(page_text):
                term = _clean_term(
                    re.sub(r"(?:业务|产品)$", "", match.group(1)),
                    plan_item.industry_name,
                )
                if term is None:
                    continue
                fragment = _normalize(match.group(0))
                digest = hashlib.sha256(
                    fragment.encode("utf-8")
                ).hexdigest()
                if digest not in seen_fragments:
                    seen_fragments.add(digest)
                    fragments.append(OfficialBusinessEvidenceFragment(
                        page_number=page.page_number,
                        fragment_sha256=digest,
                        text=fragment,
                    ))
                if term not in terms:
                    terms.append(term)
        for pattern in (() if terms else EXPLICIT_MAIN_BUSINESS_PATTERNS):
            for match in pattern.finditer(page_text):
                matched_terms = []
                for raw_term in TERM_SPLIT_PATTERN.split(match.group(1)):
                    term = _clean_term(
                        re.sub(r"(?:业务|产品)$", "", raw_term),
                        plan_item.industry_name,
                    )
                    if term is not None and term not in matched_terms:
                        matched_terms.append(term)
                if not matched_terms:
                    continue
                fragment = _normalize(match.group(0))
                digest = hashlib.sha256(
                    fragment.encode("utf-8")
                ).hexdigest()
                if digest not in seen_fragments:
                    seen_fragments.add(digest)
                    fragments.append(OfficialBusinessEvidenceFragment(
                        page_number=page.page_number,
                        fragment_sha256=digest,
                        text=fragment,
                    ))
                for term in matched_terms:
                    if term not in terms:
                        terms.append(term)
        for match in PRODUCT_PATTERN.finditer(page_text):
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            for raw_term in TERM_SPLIT_PATTERN.split(match.group(1)):
                term = _clean_term(raw_term, plan_item.industry_name)
                if term is not None and term not in terms:
                    terms.append(term)
        for match in NUMBERED_NAMED_PRODUCT_DESCRIPTION_PATTERN.finditer(
            page_text
        ):
            term = _clean_term(match.group(1), plan_item.industry_name)
            if term is None:
                continue
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            if term not in terms:
                terms.append(term)
        for match in COMPANY_PRODUCT_OUTPUT_PATTERN.finditer(page_text):
            matched_terms = tuple(
                term
                for group in match.groups()
                if (
                    term := _clean_term(group, plan_item.industry_name)
                ) is not None
            )
            if not matched_terms:
                continue
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            for term in matched_terms:
                if term not in terms:
                    terms.append(term)
        for match in ATOMIC_METALS_ACTIVITY_PATTERN.finditer(page_text):
            term = match.group(1)
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            if term not in terms:
                terms.append(term)
        if ends_overview:
            inside_business_overview = False
    for page in content.pages:
        if _directory_page(page.text):
            continue
        page_text = _normalize_layout(page.text)
        for match in COMPANY_EXPLICIT_BUSINESS_LIST_PATTERN.finditer(
            page_text
        ):
            matched_terms = []
            for raw_term in TERM_SPLIT_PATTERN.split(match.group(1)):
                term = _clean_term(
                    re.sub(r"(?:业务|产品)$", "", raw_term),
                    plan_item.industry_name,
                )
                if term is not None and term not in matched_terms:
                    matched_terms.append(term)
            if not matched_terms:
                continue
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            for term in matched_terms:
                if term not in terms:
                    terms.append(term)
    for page in content.pages:
        if (
            _directory_page(page.text)
            or any(
                heading in page.text
                for heading in BUSINESS_OVERVIEW_HEADINGS
            )
            or not any(
            anchor in page.text for anchor in BUSINESS_SECTION_ANCHORS
            )
        ):
            continue
        for match in PRODUCT_PATTERN.finditer(page.text):
            fragment = _normalize(match.group(0))
            digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            if digest not in seen_fragments:
                seen_fragments.add(digest)
                fragments.append(OfficialBusinessEvidenceFragment(
                    page_number=page.page_number,
                    fragment_sha256=digest,
                    text=fragment,
                ))
            for raw_term in TERM_SPLIT_PATTERN.split(match.group(1)):
                term = _clean_term(raw_term, plan_item.industry_name)
                if term is not None and term not in terms:
                    terms.append(term)
    if not fragments or not terms:
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            plan_item=plan_item,
            selection=selection,
            content=content,
            reasons=("business_fact_section_missing",),
        )
    return _result(
        AutomaticBusinessEvidenceStatus.READY,
        plan_item=plan_item,
        selection=selection,
        content=content,
        terms=terms,
        fragments=fragments,
    )
