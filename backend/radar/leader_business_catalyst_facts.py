"""从官方催化公告正文提取事件与明确业务对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any, Optional, Sequence, Tuple
import unicodedata

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystDocument,
    OfficialBusinessCatalystKind,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)


BUSINESS_CATALYST_FACT_CONTRACT_ID = (
    "radar-leader-business-catalyst-facts-v1"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GENERIC_TERMS = frozenset({
    "产品", "服务", "业务", "行业", "项目", "技术", "平台", "系统",
})
NEGATIVE_MARKERS = ("终止", "取消", "未中标", "不再履行")
TERM_SPLIT_PATTERN = re.compile(r"、|以及|及|和")
EARNINGS_DIRECTION_PATTERN = r"(?:增长|提升|增加|上升|下降|减少|承压|回落|扭亏)"
OBJECT_PATTERNS = {
    OfficialBusinessCatalystKind.MAJOR_CONTRACT: (
        re.compile(
            r"(?:签订|签署|续签|终止|取消)(?:了)?《"
            r"([^》]{2,40}?)(?:合同|协议)》"
        ),
        re.compile(
            r"(?:签订|签署|续签|终止|取消)(?:了)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目合同|项目|合同|协议)"
        ),
    ),
    OfficialBusinessCatalystKind.PROJECT_AWARD: (
        re.compile(
            r"第[0-9一二三四五六七八九十、，至和及-]{1,16}标段"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
        re.compile(
            r"(?:中标|未中标)([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
    ),
    OfficialBusinessCatalystKind.CAPACITY_START: (
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:生产线|项目)"
            r"(?:已|正式)?(?:建成|投产)"
        ),
    ),
    OfficialBusinessCatalystKind.PRODUCT_CERTIFICATION: (
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:产品)?"
            r"(?:获得|通过).{0,12}(?:认证|注册证|许可)"
        ),
    ),
    OfficialBusinessCatalystKind.PRIVATE_PLACEMENT_PROJECT: (
        re.compile(
            r"募集资金(?:拟)?用于([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
    ),
    OfficialBusinessCatalystKind.EARNINGS_FORECAST: (
        re.compile(
            r"(?:公司)?主要产品"
            r"([\u4e00-\u9fffA-Za-z0-9、及和]{2,48}?)(?:产品)?(?:的)?(?:市场)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量).{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营|公司主要产品|主营|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:业务|产品)(?:的)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量|"
            r"收入|营业收入|毛利率|毛利|利润).{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营业务|公司主要产品|主营业务|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:销售价格|销售均价|销售单价|"
            r"销量|产量|产销量).{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?本期"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}(?:及|和)"
            r"[\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:营业收入|毛利率|毛利)"
            r".{0,12}" + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:主要)?(?:受益于|得益于)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:业务|产品)?"
            r"(?:实现|保持|持续)?" + EARNINGS_DIRECTION_PATTERN
        ),
    ),
}


@dataclass(frozen=True, repr=False)
class OfficialBusinessCatalystFactResult:
    status: AutomaticBusinessEvidenceStatus
    document_id: Optional[str]
    document_version: Optional[str]
    symbol: Optional[str]
    issuer_identity: Optional[str]
    event_kind: Optional[OfficialBusinessCatalystKind]
    content_sha256: Optional[str] = None
    business_terms: Tuple[str, ...] = field(default_factory=tuple, repr=False)
    fragments: Tuple[OfficialBusinessEvidenceFragment, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    negative_event: bool = False
    source_time: Optional[datetime] = None
    validated_at: Optional[datetime] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = BUSINESS_CATALYST_FACT_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


def _result(
    status: AutomaticBusinessEvidenceStatus,
    *,
    document: Any,
    content: Any,
    business_terms: Sequence[str] = (),
    fragments: Sequence[OfficialBusinessEvidenceFragment] = (),
    negative_event: bool = False,
    reasons: Sequence[str] = (),
) -> OfficialBusinessCatalystFactResult:
    valid_document = (
        document if type(document) is OfficialBusinessCatalystDocument else None
    )
    valid_content = (
        content if type(content) is OfficialBusinessDocumentContentResult else None
    )
    return OfficialBusinessCatalystFactResult(
        status=status,
        document_id=valid_document.document_id if valid_document else None,
        document_version=(valid_document.document_version if valid_document else None),
        symbol=valid_document.symbol if valid_document else None,
        issuer_identity=(valid_document.issuer_identity if valid_document else None),
        event_kind=valid_document.event_kind if valid_document else None,
        content_sha256=(valid_content.content_sha256 if valid_content else None),
        business_terms=tuple(business_terms),
        fragments=tuple(fragments),
        negative_event=negative_event,
        source_time=valid_document.published_at if valid_document else None,
        validated_at=valid_content.fetched_at if valid_content else None,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _bound(document: Any, content: Any) -> bool:
    if (
        type(document) is not OfficialBusinessCatalystDocument
        or type(content) is not OfficialBusinessDocumentContentResult
        or content.status is not AutomaticBusinessEvidenceStatus.READY
        or content.document_kind is not OfficialBusinessDocumentKind.CATALYST
        or content.document_id != document.document_id
        or content.document_version != document.document_version
        or content.symbol != document.symbol
        or content.issuer_identity != document.issuer_identity
        or not isinstance(content.content_sha256, str)
        or SHA256_PATTERN.fullmatch(content.content_sha256) is None
        or not isinstance(content.pages, tuple)
        or not content.pages
        or content.page_count != len(content.pages)
        or not _aware(document.published_at)
        or not _aware(content.fetched_at)
        or content.fetched_at < document.published_at
    ):
        return False
    return all(
        type(page) is OfficialBusinessDocumentPage
        and isinstance(page.page_number, int)
        and not isinstance(page.page_number, bool)
        and page.page_number >= 1
        and isinstance(page.text, str)
        for page in content.pages
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _clean_term(value: str) -> Optional[str]:
    term = _normalize(value).strip("：:。；;、,，‘’“”\"'()（）")
    term = re.sub(
        r"^(?:20\d{2}年(?:度|上半年|下半年|一季度|前三季度)?|"
        r"报告期内|本期|主要受益于|受益于|得益于|"
        r"主要是|主要系|主要原因是|公司主要产品|本公司|公司)+",
        "",
        term,
    )
    term = re.sub(r"^(?:全年|整体上公司)", "", term)
    term = re.sub(r"(?:业务|产品|板块)$", "", term)
    if (
        not 2 <= len(term) <= 20
        or term in GENERIC_TERMS
        or term in {
            "主营", "主营业务", "主要", "公司", "整体", "营业", "该产品", "重大", "上述",
            "补充", "项目主要内容", "日常经营重大", "产品力", "品牌力的",
        }
        or term.startswith((
            "相关", "其他", "导致", "致使", "因", "用以", "通知书后",
            "的第", "项目对公司",
        ))
        or any(marker in term for marker in (
            "净利润", "归属于", "股东", "业绩", "预计", "万元",
            "扣除非经常", "同比", "本年度", "影响该", "当事人之间",
            "权利义务", "招标人洽谈", "签订上述",
        ))
        or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", term)
    ):
        return None
    return term


def _matched_terms(
    event_kind: OfficialBusinessCatalystKind,
    raw_value: str,
) -> Tuple[str, ...]:
    raw_terms = (
        TERM_SPLIT_PATTERN.split(raw_value)
        if event_kind is OfficialBusinessCatalystKind.EARNINGS_FORECAST
        else (raw_value,)
    )
    return tuple(dict.fromkeys(
        term
        for raw_term in raw_terms
        if (term := _clean_term(raw_term)) is not None
    ))


def extract_official_business_catalyst_facts(
    document: Any,
    content: Any,
) -> OfficialBusinessCatalystFactResult:
    if not _bound(document, content):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            document=document,
            content=content,
            reasons=("business_catalyst_fact_identity_unverified",),
        )
    assert isinstance(document, OfficialBusinessCatalystDocument)
    assert isinstance(content, OfficialBusinessDocumentContentResult)
    patterns = OBJECT_PATTERNS[document.event_kind]
    terms = []
    fragments = []
    seen_fragments = set()
    normalized_document = ""
    for page in content.pages:
        normalized = _normalize(page.text)
        normalized_document += normalized
        for pattern in patterns:
            for match in pattern.finditer(normalized):
                matched_terms = _matched_terms(
                    document.event_kind,
                    match.group(1),
                )
                if not matched_terms:
                    continue
                fragment = match.group(0)
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
    if not terms or not fragments:
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            document=document,
            content=content,
            reasons=("business_catalyst_fact_object_missing",),
        )
    return _result(
        AutomaticBusinessEvidenceStatus.READY,
        document=document,
        content=content,
        business_terms=terms,
        fragments=fragments,
        negative_event=any(marker in normalized_document for marker in NEGATIVE_MARKERS),
    )
