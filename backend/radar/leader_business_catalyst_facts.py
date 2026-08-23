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
EXPLICIT_METRIC_DIRECTION_PATTERN = (
    r"(?:增长|提升|增加|上升|下降|减少|承压|回落|扭亏|"
    r"上涨|下滑|下调)"
)
PROSPECTIVE_MARKERS = ("拟", "计划", "意向", "预计")
NEGATIVE_CONFIRMATION_MARKERS = ("未", "不", "无法", "不能")
EARNINGS_PROSPECTIVE_MARKERS = (
    *PROSPECTIVE_MARKERS,
    "有望",
    "可能",
    "预期",
    "或将",
    "将",
)
EARNINGS_UNCONFIRMED_MARKERS = (
    *EARNINGS_PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
    "是否",
    "尚待",
    "有待",
    "待确定",
)
UNEXECUTED_CONTRACT_MARKERS = (
    *PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
)
PRELIMINARY_AWARD_MARKERS = ("预中标", "候选")
UNCONFIRMED_AWARD_MARKERS = (
    *PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
    "是否",
    "尚待",
    "有待",
    "待确定",
    "初步确定",
    "暂时确定",
    "临时确定",
    "预先确定",
)
EARNINGS_REASON_HEADING_MARKERS = ("业绩变动原因说明",)
EARNINGS_REASON_INTRO_PATTERN = re.compile(
    r"(?:主要原因(?:是|为)?|主要是|主要系)[:：]?"
)
FORECASTED_FINANCIAL_RESULT_PATTERN = re.compile(
    r"(?:"
    r"预计[^。;；!?！？]{0,100}(?:净利润|利润总额|业绩)"
    r"|(?:净利润|利润总额|业绩)[^。;；!?！？]{0,40}预计"
    r")[^。;；!?！？]{0,40}$"
)
SENTENCE_END_MARKERS = ("。", "!", "！", "?", "？")
QUOTED_EPC_CONTRACT_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"(?:(?!(?:拟|计划|意向|预计))[^，,。.;；：:]){0,120}?"
    r"就[“\"]([^”\"]{2,60}?项目)[”\"]"
    r"签署EPC承包合同"
)
CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:被(?:确认|确定)为|(?:确认|确定)公司为|"
    r"确定[^“”\"。!?！？]{2,240}?组成的联合体为)"
    r"[“\"]([^”\"]{2,60}?项目[^”\"]{0,30})[”\"]"
    r"的中标(?:单位|供应商)"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN = re.compile(
    r"收到[^。!?！？]{0,60}?《中标通知书》"
    r"[^。!?！？]{0,40}?[，,]确认"
    r"(?:公司|本公司|[一-鿿A-Za-z0-9]{2,32}?)中标"
    r"《([^》]{2,60}?项目[^》]{0,30})》"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"[^。!?！？]{0,100}?组成的联合体被确定为"
    r"([一-鿿A-Za-z0-9()（）-]{6,90}?(?:项目|工程总承包))"
    r"(?:[(（]以下简称[“\"]?项目[”\"]?[)）])?中标人"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN = re.compile(
    r"收到[^。!?！？]{0,180}?发来的中标通知书[：:]"
    r"[^。!?！？]{0,100}?(?:组成的)?联合体被确定为"
    r"([一-鿿A-Za-z0-9()（）-]{6,90}?(?:项目|工程总承包))"
    r"(?:[(（]以下简称[“\"]?项目[”\"]?[)）])?中标人"
    r"(?=$|[，,。.;；：:!?！？])"
)
PLAIN_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:中标|未中标)([一-鿿A-Za-z0-9]{2,20}?)(?:项目)"
)
REASON_PREFIX_BUSINESS_METRIC_PATTERN = re.compile(
    r"(?:业绩变动原因主要是|主要原因(?:是|为)|主要是|主要系)本期"
    r"(?!公司|主营|主要|整体|相关|新|核心|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)(?:业务|产品)(?:的)?"
    r"(?:营业收入|收入|毛利率|毛利|利润)"
    r"[^。!?！？]{0,12}"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
)
NAMED_PRODUCT_AVERAGE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])报告期内[，,]"
    r"(?!公司|行业|市场|产品|原材料|能源|运输|采购|成本)"
    r"([一-鿿A-Za-z0-9]{2,20}?)(?:销售)?均价(?:同比)?"
    r"[^。!?！？]{0,6}"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
    + r"[，,]公司\1(?:及[一-鿿A-Za-z0-9]{2,12})?产品盈利"
    + r"[^。!?！？]{0,6}"
    + EARNINGS_DIRECTION_PATTERN
)
NAMED_PRODUCT_DELIVERY_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:]|业绩变动原因说明)报告期内[，,](?:受益于|得益于)"
    r"(?!公司|行业|市场|主营|主要|整体|相关|新|核心|产品)"
    r"([一-鿿A-Za-z0-9]{2,20}?)产品交付量的同比(?:大幅)?增加"
    r"[，,]公司营业收入同比增长"
)
NAMED_PRODUCT_OUTPUT_SHARE_PATTERN = re.compile(
    r"(?:^|[。!?！？；;])"
    r"[^。!?！？；;]{0,180}?(?:该业务|该项业务)占公司营收比重已达"
    r"(?:约)?\d+(?:\.\d+)?%[^。!?！？；;]{0,30}?[，,]产(?!量|日)"
    r"(?!(?:公司|主营|主要|整体|相关|新|核心|行业|市场|产品|业务|项目|赛道|"
    r"资源化|利用|固危废))"
    r"((?!(?:[一-鿿A-Za-z0-9]{0,18}(?:业务|行业|项目|赛道|资源化|利用|固危废|"
    r"公司|产品)))[一-鿿A-Za-z0-9]{2,20}?)"
    r"(?:超过|达到)(?:约)?\d+(?:\.\d+)?(?:吨|万吨|公斤|千克|kg)"
    r"(?=$|[。!?！？；;]|[(（]|[，,]公司整体营业收入)"
)
NAMED_FOREIGN_BUSINESS_TURNAROUND_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:()（）])"
    r"(?!公司|主营|主要|整体|相关|新|核心|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务多措并举[，,]"
    r"营业收入实现增长[，,]利润大幅减亏"
)
NAMED_ANNUAL_BUSINESS_GROWTH_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司"
    r"(?!主营|主要|整体|相关|新|核心|业务|产品|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务在20\d{2}年度"
    r"实现了显著业绩增长"
)
NAMED_BUSINESS_SEGMENT_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:[一二三四五六七八九十]+是)?公司"
    r"(?!主营|主要|整体|相关|新|核心|业务|产品|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务板块巩固拓展[，,]"
    r"经营收入稳定增长"
)
NAMED_CORE_PRODUCT_ANAPHORA_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])[^\u3002!?！？]{0,30}?核心产品"
    r"(?!该产品|主要产品|公司产品|产品)"
    r"([一-鿿A-Za-z0-9]{2,24}?)(?:[(（][^()（）。!?！？]{2,30}[)）])?"
    r"(?:20\d{2}年[^。!?！？，,]{2,40}[，,])?"
    r"受[^。!?！？]{2,30}?影响[，,]"
    r"该产品销售单价[^。!?！？]{0,8}?下调、销量"
    r"[^。!?！？]{0,8}?下滑[，,]对公司营业收入及"
    r"经营利润形成[^。!?！？]{0,8}?冲击"
)
OBJECT_PATTERNS = {
    OfficialBusinessCatalystKind.MAJOR_CONTRACT: (
        QUOTED_EPC_CONTRACT_PATTERN,
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
        QUOTED_EPC_CONTRACT_PATTERN,
        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
        re.compile(
            r"第[0-9一二三四五六七八九十、，至和及-]{1,16}标段"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
        PLAIN_PROJECT_AWARD_PATTERN,
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
        REASON_PREFIX_BUSINESS_METRIC_PATTERN,
        NAMED_PRODUCT_AVERAGE_PATTERN,
        NAMED_PRODUCT_DELIVERY_PATTERN,
        NAMED_PRODUCT_OUTPUT_SHARE_PATTERN,
        NAMED_FOREIGN_BUSINESS_TURNAROUND_PATTERN,
        NAMED_ANNUAL_BUSINESS_GROWTH_PATTERN,
        NAMED_BUSINESS_SEGMENT_REVENUE_PATTERN,
        NAMED_CORE_PRODUCT_ANAPHORA_PATTERN,
        re.compile(
            r"(?:导致|致使)公司(?:报告期内)?"
            r"(?!报告期内|项目|公司|整体)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?养殖)(?:业务)?"
            r"(?:利润|毛利)[^。!?！？]{0,8}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:推动|带动)(?!公司|行业|主营|整体)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?)板块"
            r"(?:营业收入|收入)(?:实现)?(?:同比)?"
            r"[^。!?！？]{0,4}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])因"
            r"(?!市场|原材料|采购|能源|运输|成本)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?)(?:市场)?"
            r"价格(?:上涨|下降|回落|承压)导致当期营业收入"
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])"
            r"(?!公司业务|主营业务|主要业务)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{1,18}?"
            r"(?:加工|生产|制造|服务|销售|运营))(?:业务)"
            r"(?:有序|逐步|全面)?(?:恢复|复工复产|恢复生产)"
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])[一二三四五六七八九十]+是"
            r"20\d{2}年(?:度|上半年|下半年|一季度|前三季度)?"
            r"(?!度|上半年|下半年|一季度|前三季度|公司|市场|"
            r"原材料|能源|运输|采购|成本)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)"
            r"(?:销售价格|销售均价|销售单价)"
            r"[^。!?！？]{0,12}上涨"
        ),
        re.compile(
            r"(?:公司)?主要产品"
            r"([\u4e00-\u9fffA-Za-z0-9、及和]{2,48}?)(?:产品)?(?:的)?(?:市场)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量)"
            r"[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营|公司主要产品|主营|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:业务|产品)(?:的)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量|"
            r"收入|营业收入|毛利率|毛利|利润)"
            r"[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营业务|公司主要产品|主营业务|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:销售价格|销售均价|销售单价|"
            r"销量|产量|产销量)[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?本期"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}(?:及|和)"
            r"[\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:营业收入|毛利率|毛利)"
            r"[^。!?！？]{0,12}" + EARNINGS_DIRECTION_PATTERN
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


def _clean_term(value: str, *, maximum_length: int = 20) -> Optional[str]:
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
        not 2 <= len(term) <= maximum_length
        or term in GENERIC_TERMS
        or term in {
            "主营", "主营业务", "主要", "公司", "整体", "营业", "该产品", "重大", "上述",
            "补充", "项目主要内容", "日常经营重大", "产品力", "品牌力的",
            "承包", "工程承包", "总承包", "总包", "工程总包", "EPC承包",
        }
        or re.fullmatch(
            r"(?:(?:EPC|施工|机电|工程|建设|总承包|承包|总包)+)项目",
            term,
            flags=re.IGNORECASE,
        ) is not None
        or re.fullmatch(
            r"(?:智慧城市|重点工程|重大项目|一般工程|综合治理)建设项目"
            r"(?:EPC工程总承包)?",
            term,
            flags=re.IGNORECASE,
        ) is not None
        or term.startswith((
            "相关", "其他", "导致", "致使", "因", "用以", "通知书后",
            "的第", "项目对公司",
        ))
        or any(marker in term for marker in (
            "净利润", "归属于", "股东", "业绩", "预计", "万元",
            "扣除非经常", "同比", "本年度", "影响该", "当事人之间",
            "权利义务", "招标人洽谈", "签订上述",
            "交付量",
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
        if (
            term := _clean_term(
                raw_term,
                maximum_length=(
                    60
                    if event_kind in {
                        OfficialBusinessCatalystKind.MAJOR_CONTRACT,
                        OfficialBusinessCatalystKind.PROJECT_AWARD,
                    }
                    else 20
                ),
            )
        ) is not None
    ))


def _has_prospective_sentence_context(value: str, match_start: int) -> bool:
    sentence_start = max(
        (value.rfind(marker, 0, match_start) for marker in SENTENCE_END_MARKERS),
        default=-1,
    ) + 1
    return any(
        marker in value[sentence_start:match_start]
        for marker in PROSPECTIVE_MARKERS
    )


def _has_unconfirmed_award_context(value: str, match: re.Match) -> bool:
    context = _sentence_context(value, match)
    return any(
        marker in context
        for marker in (
            *UNCONFIRMED_AWARD_MARKERS,
            *PRELIMINARY_AWARD_MARKERS,
        )
    )


def _has_unexecuted_contract_context(value: str, match: re.Match) -> bool:
    sentence_start = max(
        (
            value.rfind(marker, 0, match.start())
            for marker in SENTENCE_END_MARKERS
        ),
        default=-1,
    ) + 1
    return any(
        marker in value[sentence_start:match.start(1)]
        for marker in UNEXECUTED_CONTRACT_MARKERS
    )


def _has_preliminary_award_context(value: str, match: re.Match) -> bool:
    context = _sentence_context(value, match)
    return any(marker in context for marker in PRELIMINARY_AWARD_MARKERS)


def _has_unconfirmed_earnings_context(value: str, match: re.Match) -> bool:
    # PDF 文本常丢失句号：页首的“预计净利润”不能否掉后续
    # “业绩变动原因说明”中已发生的经营事实。同理，明确的
    # 财务结果预测可由“主要是/主要原因”引出已发生的对象事实。
    sentence_start = max(
        (value.rfind(marker, 0, match.start()) for marker in SENTENCE_END_MARKERS),
        default=-1,
    ) + 1
    logical_start = sentence_start
    for heading in EARNINGS_REASON_HEADING_MARKERS:
        position = value.rfind(heading, sentence_start, match.end())
        if position < 0 or position > match.start():
            continue
        immediate_prefix = value[max(sentence_start, position - 8):position]
        if not any(
            marker in immediate_prefix
            for marker in UNCONFIRMED_AWARD_MARKERS
        ):
            logical_start = max(logical_start, position)
    prefix_and_match = value[logical_start:match.end()]
    for intro in EARNINGS_REASON_INTRO_PATTERN.finditer(prefix_and_match):
        forecast_prefix = prefix_and_match[:intro.start()]
        if FORECASTED_FINANCIAL_RESULT_PATTERN.search(forecast_prefix):
            logical_start += intro.start()
            prefix_and_match = value[logical_start:match.end()]
            break
    context = prefix_and_match
    return any(marker in context for marker in EARNINGS_UNCONFIRMED_MARKERS)


def _sentence_context(value: str, match: re.Match) -> str:
    sentence_start = max(
        (
            value.rfind(marker, 0, match.start())
            for marker in SENTENCE_END_MARKERS
        ),
        default=-1,
    ) + 1
    sentence_ends = tuple(
        position
        for marker in SENTENCE_END_MARKERS
        if (position := value.find(marker, match.end())) >= 0
    )
    sentence_end = min(sentence_ends) + 1 if sentence_ends else len(value)
    return value[sentence_start:sentence_end]


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
    normalized_pages = tuple(_normalize(page.text) for page in content.pages)
    normalized_document = "".join(normalized_pages)
    for page, normalized in zip(content.pages, normalized_pages):
        for pattern in patterns:
            for match in pattern.finditer(normalized):
                if (
                    document.event_kind in {
                        OfficialBusinessCatalystKind.MAJOR_CONTRACT,
                        OfficialBusinessCatalystKind.PROJECT_AWARD,
                    }
                    and _has_prospective_sentence_context(
                        normalized,
                        match.start(),
                    )
                ):
                    continue
                if (
                    pattern in {
                        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and _has_unconfirmed_award_context(
                        normalized,
                        match,
                    )
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.MAJOR_CONTRACT
                    and _has_unexecuted_contract_context(normalized, match)
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.PROJECT_AWARD
                    and pattern not in {
                        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and _has_preliminary_award_context(normalized, match)
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.PROJECT_AWARD
                    and pattern is PLAIN_PROJECT_AWARD_PATTERN
                    and "中标通知书" in _sentence_context(normalized, match)
                ):
                    continue
                if (
                    pattern in {
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and any(marker in normalized_document for marker in ("终止", "取消"))
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    and _has_unconfirmed_earnings_context(normalized, match)
                ):
                    continue
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
