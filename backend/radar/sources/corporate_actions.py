"""阶段9公司行为官方前向来源。

上交所市场日历只作为公司行为候选计数；深交所统计月报可确定分红、
送股、配股和实施日，但不把月报发布时间写成公司公告时间；北交所
权益分派只在巨潮官方公告元数据和PDF正文字段一致时转成结构化事件。
任一交易所时间窗口或事件类型不全时，快照仍失败关闭。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
from io import BytesIO
import json
import re
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
import pypdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from radar.replay_source_adapters import (
    CorporateActionEvidenceItem,
    CorporateActionForwardSnapshot,
    CorporateActionUnresolvedDocument,
)
from radar.sources.cninfo_pdf_cache import CninfoPdfCache


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SSE_CORPORATE_ACTION_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_CALENDAR_REFERER = (
    "https://www.sse.com.cn/disclosure/dealinstruc/calendar/"
)
SZSE_MONTH_INDEX_URL = "https://www.szse.cn/market/periodical/month/index.html"
CNINFO_ANNOUNCEMENT_QUERY_URL = (
    "https://www.cninfo.com.cn/new/hisAnnouncement/query"
)
CNINFO_STOCK_DIRECTORY_URL = (
    "https://www.cninfo.com.cn/new/data/szse_stock.json"
)
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"
REQUEST_TIMEOUT_SECONDS = 20.0

REQUIRED_CORPORATE_ACTION_TYPES = (
    "cash_dividend",
    "bonus_share",
    "capitalization_issue",
    "rights_issue",
    "stock_split",
    "reverse_split",
    "placement",
    "repurchase_cancellation",
    "merger",
    "demerger",
    "code_change",
)
CNINFO_CORPORATE_ACTION_QUERY_POLICY_ID = (
    "cninfo-corporate-action-discovery-v2"
)
CNINFO_EQUITY_DISTRIBUTION_ACTION_TYPES = (
    "cash_dividend",
    "bonus_share",
    "capitalization_issue",
)
CNINFO_DETERMINISTIC_ACTION_TYPES = (
    *CNINFO_EQUITY_DISTRIBUTION_ACTION_TYPES,
    "rights_issue",
    "placement",
    "repurchase_cancellation",
    "merger",
    "code_change",
    "reverse_split",
)
CNINFO_PAGE_COLUMN_EXCHANGES = {
    "SHZB": "sse",
    "SHKCB": "sse",
    "SZZB": "szse",
    "SZCY": "szse",
    "BJS": "bse",
}


def cninfo_corporate_action_query_policy() -> Mapping[str, Any]:
    """返回从巨潮官方公告页核验出的版本化发现策略。

    分类和精确搜索词只能证明发现范围，不能替代PDF正文中的实施事实。
    拆股和分立尚无可验证的A股实施样本，继续显式列为缺口。
    """

    missing = tuple(
        action_type
        for action_type in REQUIRED_CORPORATE_ACTION_TYPES
        if action_type not in CNINFO_DETERMINISTIC_ACTION_TYPES
    )
    return {
        "policyId": CNINFO_CORPORATE_ACTION_QUERY_POLICY_ID,
        "requiredActionTypes": REQUIRED_CORPORATE_ACTION_TYPES,
        "deterministicActionTypes": CNINFO_DETERMINISTIC_ACTION_TYPES,
        "missingActionTypes": missing,
        "queries": {
            "equity_distribution": {
                "category": "category_qyfpxzcs_szsh",
                "searchKey": "",
                "actionTypes": CNINFO_EQUITY_DISTRIBUTION_ACTION_TYPES,
            },
            "rights_issue": {
                "category": "category_pg_szsh",
                "searchKey": "",
                "actionTypes": ("rights_issue",),
            },
            "placement": {
                "category": "category_zf_szsh",
                "searchKey": "",
                "actionTypes": ("placement",),
            },
            "equity_change": {
                "category": "category_gqbd_szsh",
                "searchKey": "",
                "actionTypes": (
                    "repurchase_cancellation",
                    "merger",
                    "demerger",
                    "code_change",
                ),
            },
            "code_change": {
                "category": "",
                "searchKey": "证券代码变更实施公告",
                "actionTypes": ("code_change",),
            },
            "reverse_split": {
                "category": "",
                "searchKey": "缩股实施公告",
                "actionTypes": ("reverse_split",),
            },
            "stock_split": {
                "category": "",
                "searchKey": "股份拆细实施公告",
                "actionTypes": ("stock_split",),
            },
            "demerger": {
                "category": "",
                "searchKey": "分立实施公告",
                "actionTypes": ("demerger",),
            },
        },
    }


def cninfo_exchange_from_page_column(value: Any) -> str:
    if not isinstance(value, str) or value not in CNINFO_PAGE_COLUMN_EXCHANGES:
        raise ValueError("cninfo_corporate_action_page_column_unverified")
    return CNINFO_PAGE_COLUMN_EXCHANGES[value]


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


@dataclass(frozen=True)
class CorporateActionExchangeCollection:
    exchange: str
    source_id: str
    source: str
    source_time: Optional[datetime]
    fetched_at: datetime
    coverage_from: date
    coverage_through: date
    expected_count: int
    items: Tuple[CorporateActionEvidenceItem, ...]
    reasons: Tuple[str, ...] = ()
    unresolved_documents: Tuple[CorporateActionUnresolvedDocument, ...] = ()


_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL_PATTERN = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SZSE_MONTH_PATTERN = re.compile(r"[\(（](\d{4})\.(\d{2})[\)）]")
_CHINESE_DATE_PATTERN = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)


def _decode_official_html(content: bytes) -> str:
    if not isinstance(content, bytes) or not content:
        raise ValueError("corporate_action_official_content_missing")
    for encoding in ("utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("corporate_action_official_encoding_unverified")


def _cell_text(value: str) -> str:
    return unescape(_TAG_PATTERN.sub("", value)).strip()


def _number(value: str) -> Optional[float]:
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError("szse_corporate_action_number_invalid") from exc


def _slash_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except ValueError as exc:
        raise ValueError("szse_corporate_action_date_invalid") from exc


def parse_szse_monthly_corporate_actions(
    content: bytes,
    *,
    source_url: str,
    source_time: datetime,
    fetched_at: datetime,
) -> CorporateActionExchangeCollection:
    """解析深交所官方《分红派息配股》月报。

    表中的真实 0 与空值分开处理；月报时间不写入单只公司的
    ``announcedAt``。
    """

    if (
        not source_url.startswith("https://docs.static.szse.cn/")
        or source_time.tzinfo is None
        or source_time.utcoffset() is None
        or fetched_at.tzinfo is None
        or fetched_at.utcoffset() is None
    ):
        raise ValueError("szse_corporate_action_source_unverified")
    html = _decode_official_html(content)
    month_match = _SZSE_MONTH_PATTERN.search(html)
    if month_match is None:
        raise ValueError("szse_corporate_action_month_unverified")
    year, month = (int(value) for value in month_match.groups())
    coverage_through = date(year, month, calendar.monthrange(year, month)[1])
    digest = hashlib.sha256(content).hexdigest()
    document_name = urlsplit(source_url).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"W\d+\.html", document_name):
        raise ValueError("szse_corporate_action_document_id_unverified")

    rows = []
    for raw_row in _ROW_PATTERN.findall(html):
        cells = [_cell_text(cell) for cell in _CELL_PATTERN.findall(raw_row)]
        if len(cells) == 14 and re.fullmatch(r"\d{6}", cells[0]):
            rows.append(cells)
    items = []
    for cells in rows:
        symbol = cells[0]
        effective_on = _slash_date(cells[10])
        action_types = []
        if (_number(cells[2]) or 0) > 0 or (_number(cells[3]) or 0) > 0:
            action_types.append("bonus_share")
        if (_number(cells[4]) or 0) > 0 or (_number(cells[5]) or 0) > 0:
            action_types.append("cash_dividend")
        if any((_number(cells[index]) or 0) > 0 for index in (6, 7, 8)):
            action_types.append("rights_issue")
        if not action_types:
            raise ValueError("szse_corporate_action_row_without_action")
        for action_type in action_types:
            items.append(CorporateActionEvidenceItem(
                symbol=symbol,
                exchange="szse",
                actionType=action_type,
                announcedAt=None,
                effectiveOn=effective_on,
                sourceName="深圳证券交易所统计月报",
                sourceUrl=source_url,
                sourceSha256=digest,
                documentId=document_name.removesuffix(".html"),
            ))
    return CorporateActionExchangeCollection(
        exchange="szse",
        source_id=f"szse-monthly:{year:04d}-{month:02d}:sha256={digest}",
        source="深圳证券交易所分红派息配股统计月报",
        source_time=source_time,
        fetched_at=fetched_at,
        coverage_from=date(year, month, 1),
        coverage_through=coverage_through,
        expected_count=len(items),
        items=tuple(items),
        reasons=(
            ("szse_company_announcement_time_missing",)
            if items
            else ()
        ),
    )


def _chinese_date_after(label: str, normalized_text: str) -> date:
    position = normalized_text.find(label)
    if position < 0:
        raise ValueError("bse_corporate_action_effective_date_missing")
    match = _CHINESE_DATE_PATTERN.search(
        normalized_text,
        position + len(label),
    )
    if match is None:
        raise ValueError("bse_corporate_action_effective_date_missing")
    year, month, day = (int(value) for value in match.groups())
    return date(year, month, day)


def _equity_distribution_effective_date(
    exchange: str,
    normalized_text: str,
    symbol: Optional[str] = None,
) -> date:
    if exchange == "sse":
        four_column_row = re.search(
            r"股权登记日除权（息）日新增无限售条件流通股份上市日"
            r"(?:现金红利发放日)?"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(?:\d{4}/\d{1,2}/\d{1,2})?",
            normalized_text,
        )
        if four_column_row is not None:
            return datetime.strptime(
                four_column_row.group(2),
                "%Y/%m/%d",
            ).date()
        b_share_row = re.search(
            r"(?:Ｂ股|B股)(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})",
            normalized_text,
        )
        if b_share_row is not None:
            return datetime.strptime(
                b_share_row.group(3),
                "%Y/%m/%d",
            ).date()
        row = re.search(
            r"(?:普通股|Ａ股|A股|Ｂ股|B股)(\d{4}/\d{1,2}/\d{1,2})"
            r"(?:－|—|-)(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})",
            normalized_text,
        )
        if row is not None:
            return datetime.strptime(row.group(2), "%Y/%m/%d").date()
        three_column_row = re.search(
            r"股权登记日除权（息）日现金红利发放日"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})",
            normalized_text,
        )
        if three_column_row is not None:
            return datetime.strptime(
                three_column_row.group(2),
                "%Y/%m/%d",
            ).date()
        categorized_three_column_row = re.search(
            r"股权登记日除权（息）日现金红利发放日"
            r"(?:Ａ股|A股|Ｂ股|B股)?"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})",
            normalized_text,
        )
        if categorized_three_column_row is not None:
            return datetime.strptime(
                categorized_three_column_row.group(2),
                "%Y/%m/%d",
            ).date()
        depository_receipt_row = re.search(
            r"存托凭证登记日除权（息）日现金红利发放日"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})"
            r"(\d{4}/\d{1,2}/\d{1,2})",
            normalized_text,
        )
        if depository_receipt_row is not None:
            return datetime.strptime(
                depository_receipt_row.group(2),
                "%Y/%m/%d",
            ).date()
        chinese_three_column_row = re.search(
            r"(?:存托凭证登记日|股权登记日)除权（息）日现金红利发放日"
            + _CHINESE_DATE_PATTERN.pattern
            + _CHINESE_DATE_PATTERN.pattern
            + _CHINESE_DATE_PATTERN.pattern,
            normalized_text,
        )
        if chinese_three_column_row is not None:
            values = chinese_three_column_row.groups()
            return date(*(int(value) for value in values[3:6]))
    if exchange == "szse" and isinstance(symbol, str) and symbol.startswith("2"):
        b_share_label = re.search(
            r"B股.{0,120}?除(?:权除)?息日(?:为|：|:)",
            normalized_text,
        )
        if b_share_label is not None:
            value = _CHINESE_DATE_PATTERN.search(
                normalized_text,
                b_share_label.end(),
            )
            if value is not None:
                year, month, day = (int(item) for item in value.groups())
                return date(year, month, day)
    for pattern in (
        r"A股除息日(?:以及红利发放日)?(?:为|：|:)",
        r"(?<!A股)(?<!B股)(?<!Ｂ股)除息日(?:以及红利发放日)?(?:为|：|:)",
    ):
        ex_date_label = re.search(pattern, normalized_text)
        if ex_date_label is not None:
            value = _CHINESE_DATE_PATTERN.search(
                normalized_text,
                ex_date_label.end(),
            )
            if value is not None:
                year, month, day = (int(item) for item in value.groups())
                return date(year, month, day)
    label = re.search(
        r"除权除息(?:\d{1,3})?(?:及红利发放)?日"
        r"(?:（红利发放日）)?(?:为|：|:)",
        normalized_text,
    )
    if label is not None:
        value = _CHINESE_DATE_PATTERN.search(normalized_text, label.end())
        if value is not None:
            year, month, day = (int(item) for item in value.groups())
            return date(year, month, day)
    parenthesized_label = re.search(
        r"除权日（除息日）(?:为|：|:)",
        normalized_text,
    )
    if parenthesized_label is not None:
        value = _CHINESE_DATE_PATTERN.search(
            normalized_text,
            parenthesized_label.end(),
        )
        if value is not None:
            year, month, day = (int(item) for item in value.groups())
            return date(year, month, day)
    table_start = normalized_text.find("股权登记日除权除息日")
    if table_start >= 0:
        row_start = normalized_text.find("现金红利发放日", table_start)
        if 0 <= row_start - table_start <= 160:
            values = list(_CHINESE_DATE_PATTERN.finditer(
                normalized_text,
                row_start + len("现金红利发放日"),
                row_start + len("现金红利发放日") + 100,
            ))
            if len(values) >= 4:
                year, month, day = (
                    int(item) for item in values[1].groups()
                )
                return date(year, month, day)
    raise ValueError("cninfo_equity_distribution_effective_date_missing")


def _positive_amount(pattern: str, value: str) -> bool:
    match = re.search(pattern, value)
    return match is not None and float(match.group(1)) > 0


def _equity_distribution_matching_operational_date(
    normalized_text: str,
) -> Optional[date]:
    """仅在送转入账日和现金到账日完全相同时接纳正文补强日期。"""

    transfer = re.search(
        r"本次所送（转）股于" + _CHINESE_DATE_PATTERN.pattern
        + r"直接记入股东证券账户",
        normalized_text,
    )
    cash = re.search(
        r"现金红利将于" + _CHINESE_DATE_PATTERN.pattern
        + r"(?:通过|直接)",
        normalized_text,
    )
    if transfer is None or cash is None:
        return None
    try:
        transfer_date = date(*(int(value) for value in transfer.groups()))
        cash_date = date(*(int(value) for value in cash.groups()))
    except ValueError:
        return None
    return transfer_date if transfer_date == cash_date else None


def _is_equity_distribution_implementation_title(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    clean_title = re.sub(r"\s+", "", _cell_text(value))
    return (
        re.search(
            r"(?:权益分派|利润分配(?:及资本公积金转增股本)?|分红派息|"
            r"分红(?:A股|B股)?|"
            r"(?:A股|Ａ股|B股|Ｂ股)派息).*"
            r"实施(?:的)?公告$",
            clean_title,
        ) is not None
        and "提示性" not in clean_title
    )


def _szse_b_share_symbol_from_document(
    text: str,
    primary_symbol: str,
) -> Optional[str]:
    """从深市官方公告证券代码栏确定同发行人的B股代码。"""

    if (
        not isinstance(text, str)
        or re.fullmatch(r"0\d{5}", primary_symbol) is None
    ):
        return None
    expected = "2" + primary_symbol[1:]
    direct = re.search(
        r"(?:证券代码|股票代码)\s*[：:]\s*"
        + re.escape(primary_symbol)
        + r"\s*(?:[、；;/（(]\s*)?"
        + re.escape(expected)
        + r"(?:\s*[）)])?",
        text,
    )
    if direct is not None:
        return expected
    continuation = re.search(
        r"(?:证券代码|股票代码)\s*[：:]\s*"
        + re.escape(primary_symbol)
        + r"[^\n]*\n\s*"
        + re.escape(expected)
        + r"(?:\s|$)",
        text,
    )
    return expected if continuation is not None else None


def parse_cninfo_equity_distribution_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> Tuple[CorporateActionEvidenceItem, ...]:
    """只解析标题和正文均能确认的沪深北权益分派实施公告。"""

    if not _is_equity_distribution_implementation_title(title):
        raise ValueError("cninfo_equity_distribution_title_unverified")
    if (
        re.fullmatch(r"\d{6}", symbol) is None
        or exchange not in {"sse", "szse", "bse"}
        or announced_at.tzinfo is None
        or announced_at.utcoffset() is None
        or not source_url.startswith("https://static.cninfo.com.cn/")
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or not document_id.startswith("cninfo:")
    ):
        raise ValueError("cninfo_equity_distribution_source_unverified")
    normalized = re.sub(r"\s+", "", text)
    symbol_verified = any(
        f"{label}{separator}{symbol}" in normalized
        for label in ("证券代码", "股票代码", "A股代码", "存托凭证代码")
        for separator in ("：", ":")
    ) or re.search(
        r"(?:证券代码|股票代码)(?:（A/H）|\(A/H\))?[：:]"
        + re.escape(symbol)
        + r"(?:/|、)",
        normalized,
    ) is not None or re.search(
        r"(?:证券代码|股票代码)[：:]\d{6}(?:、|/|（|\()"
        + re.escape(symbol)
        + r"(?:）|\))?",
        normalized,
    ) is not None or (
        symbol.startswith("2")
        and _szse_b_share_symbol_from_document(
            text,
            "0" + symbol[1:],
        ) == symbol
    )
    if not symbol_verified:
        raise ValueError("cninfo_equity_distribution_symbol_unverified")
    try:
        effective_on = _equity_distribution_effective_date(
            exchange,
            normalized,
            symbol,
        )
    except ValueError as exc:
        if str(exc).startswith("cninfo_equity_distribution_"):
            raise
        effective_on = _equity_distribution_matching_operational_date(
            normalized
        )
        if effective_on is None:
            raise ValueError(
                "cninfo_equity_distribution_effective_date_invalid"
            ) from exc
    action_types = []
    if (
        _positive_amount(
            r"每10股转增([0-9]+(?:\.[0-9]+)?)股",
            normalized,
        )
        or _positive_amount(
            r"每股转增(?:股份)?([0-9]+(?:\.[0-9]+)?)股",
            normalized,
        )
    ):
        action_types.append("capitalization_issue")
    if (
        _positive_amount(
            r"每10股送(?:红股)?([0-9]+(?:\.[0-9]+)?)股",
            normalized,
        )
        or _positive_amount(
            r"每股送(?:红股)?([0-9]+(?:\.[0-9]+)?)股",
            normalized,
        )
    ):
        action_types.append("bonus_share")
    if (
        _positive_amount(
            r"每10股(?:派|派息|派现金|派发现金(?:红利|股利|分红)?)"
            r"(?:人民币)?([0-9]+(?:\.[0-9]+)?)元(?:人民币)?",
            normalized,
        )
        or _positive_amount(
            r"每10股派发([0-9]+(?:\.[0-9]+)?)元人民币现金",
            normalized,
        )
        or _positive_amount(
            r"每10股送(?:红股)?[0-9]+(?:\.[0-9]+)?股[,，]?"
            r"派([0-9]+(?:\.[0-9]+)?)元人民币现金",
            normalized,
        )
        or _positive_amount(
            r"(?:A股|Ａ股)?每股(?:派发)?现金(?:红利|股息)"
            r"(?:人民币)?([0-9]+(?:\.[0-9]+)?)元",
            normalized,
        )
        or _positive_amount(
            r"每份存托凭证现金红利：?([0-9]+(?:\.[0-9]+)?)元",
            normalized,
        )
    ):
        action_types.append("cash_dividend")
    if not action_types:
        raise ValueError("cninfo_equity_distribution_action_unverified")
    source_names = {
        "sse": "巨潮资讯（上交所官方公告）",
        "szse": "巨潮资讯（深交所官方公告）",
        "bse": "巨潮资讯（北交所官方公告）",
    }
    return tuple(
        CorporateActionEvidenceItem(
            symbol=symbol,
            exchange=exchange,
            actionType=action_type,
            announcedAt=announced_at,
            effectiveOn=effective_on,
            sourceName=source_names[exchange],
            sourceUrl=source_url,
            sourceSha256=source_sha256,
            documentId=document_id,
        )
        for action_type in action_types
    )


def _clean_cninfo_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", _cell_text(value))


def _validated_cninfo_action_context(
    *,
    action_name: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> str:
    if (
        re.fullmatch(r"\d{6}", symbol) is None
        or exchange not in {"sse", "szse", "bse"}
        or announced_at.tzinfo is None
        or announced_at.utcoffset() is None
        or urlsplit(source_url).hostname != "static.cninfo.com.cn"
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or not document_id.startswith("cninfo:")
    ):
        raise ValueError(f"cninfo_{action_name}_source_unverified")
    normalized = re.sub(r"\s+", "", text)
    if not any(
        marker in normalized
        for marker in (
            f"证券代码：{symbol}",
            f"证券代码:{symbol}",
            f"股票代码：{symbol}",
            f"股票代码:{symbol}",
        )
    ):
        raise ValueError(f"cninfo_{action_name}_symbol_unverified")
    return normalized


def _date_from_match(match: Optional[re.Match], reason: str) -> date:
    if match is None:
        raise ValueError(reason)
    year, month, day = (int(value) for value in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(reason) from exc


def _cninfo_action_item(
    *,
    symbol: str,
    exchange: str,
    action_type: str,
    announced_at: datetime,
    effective_on: date,
    source_url: str,
    source_sha256: str,
    document_id: str,
    new_symbol: Optional[str] = None,
) -> CorporateActionEvidenceItem:
    source_names = {
        "sse": "巨潮资讯（上交所官方公告）",
        "szse": "巨潮资讯（深交所官方公告）",
        "bse": "巨潮资讯（北交所官方公告）",
    }
    return CorporateActionEvidenceItem(
        symbol=symbol,
        exchange=exchange,
        actionType=action_type,
        newSymbol=new_symbol,
        announcedAt=announced_at,
        effectiveOn=effective_on,
        sourceName=source_names[exchange],
        sourceUrl=source_url,
        sourceSha256=source_sha256,
        documentId=document_id,
    )


def _is_rights_issue_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "配股" in title
        and "上市公告书" in title
        and not any(marker in title for marker in ("提示性", "预案", "方案"))
    )


def parse_cninfo_rights_issue_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_rights_issue_implementation_title(title):
        raise ValueError("cninfo_rights_issue_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="rights_issue",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    if "本次配股" not in normalized:
        raise ValueError("cninfo_rights_issue_action_unverified")
    effective_on = _date_from_match(
        re.search(
            r"新增股份的上市时间为" + _CHINESE_DATE_PATTERN.pattern,
            normalized,
        ),
        "cninfo_rights_issue_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="rights_issue",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_rights_issue_result_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        title.endswith("配股发行结果公告")
        and not any(marker in title for marker in ("提示性", "预案", "方案"))
    )


def parse_cninfo_rights_issue_result_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    """从配股发行结果公告提取明文除权基准日。"""

    if not _is_rights_issue_result_title(title):
        raise ValueError("cninfo_rights_issue_result_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="rights_issue_result",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    if "本次配股" not in normalized or "配股发行成功" not in normalized:
        raise ValueError("cninfo_rights_issue_result_action_unverified")
    effective_on = _date_from_match(
        re.search(
            r"本公告披露当日[（(]"
            + _CHINESE_DATE_PATTERN.pattern
            + r"[^）)]{0,20}[）)]即为发行成功的除权基准日",
            normalized,
        ),
        "cninfo_rights_issue_result_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="rights_issue",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_placement_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "向特定对象发行股票" in title
        and "上市公告书" in title
        and not any(marker in title for marker in ("提示性", "摘要"))
    )


def parse_cninfo_placement_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_placement_implementation_title(title):
        raise ValueError("cninfo_placement_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="placement",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    if (
        "本次向特定对象发行" not in normalized
        and not (
            "向特定对象发行股票" in _clean_cninfo_title(title)
            and "本次发行的股票为" in normalized
        )
    ):
        raise ValueError("cninfo_placement_action_unverified")
    effective_match = None
    for pattern in (
        r"新增股份的上市时间为",
        r"股票上市时间：?",
        r"向特定对象发行新增股票将于",
    ):
        effective_match = re.search(
            pattern + _CHINESE_DATE_PATTERN.pattern,
            normalized,
        )
        if effective_match is not None:
            break
    if effective_match is None:
        effective_match = re.search(
            r"(?:本次发行|本次募集配套资金的)?"
            r"新增(?:的)?(?:[0-9,]+股)?股份已于"
            + _CHINESE_DATE_PATTERN.pattern
            + r".{0,120}?办理(?:完毕|完成)(?:了)?"
            r"(?:股份)?登记(?:托管)?(?:及限售)?手续",
            normalized,
        )
    if effective_match is None:
        effective_match = re.search(
            r"本次向特定对象发行的(?:[0-9,]+股)?股份已于"
            + _CHINESE_DATE_PATTERN.pattern
            + r".{0,120}?办理(?:完毕|完成)(?:了)?"
            r"(?:股份)?登记(?:托管)?(?:及限售)?手续",
            normalized,
        )
    if effective_match is None:
        effective_match = re.search(
            r"截至"
            + _CHINESE_DATE_PATTERN.pattern
            + r"[（(](?:本次)?新增股份登记日[）)]",
            normalized,
        )
    effective_on = _date_from_match(
        effective_match,
        "cninfo_placement_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="placement",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_repurchase_cancellation_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "回购股份" in title
        and "注销完成" in title
        and "股份变动" in title
        and title.endswith("公告")
    )


def parse_cninfo_repurchase_cancellation_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_repurchase_cancellation_implementation_title(title):
        raise ValueError("cninfo_repurchase_cancellation_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="repurchase_cancellation",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    completion_match = None
    for pattern in (
        r"回购股份注销日：?" + _CHINESE_DATE_PATTERN.pattern,
        r"本次回购股份注销日期为" + _CHINESE_DATE_PATTERN.pattern,
        r"回购(?:A股)?股份(?:的)?注销事宜(?:将于|已于)"
        + _CHINESE_DATE_PATTERN.pattern
        + r"(?:办理完成|起生效)",
        r"截至" + _CHINESE_DATE_PATTERN.pattern
        + r"[，,]?公司已.{0,100}?办理完毕.{0,100}?回购股份的注销手续",
        r"公司已于" + _CHINESE_DATE_PATTERN.pattern
        + r".{0,100}?办理完毕.{0,100}?回购股份的注销手续",
        r"公司(?:已)?于" + _CHINESE_DATE_PATTERN.pattern
        + r".{0,100}?办理完毕.{0,100}?回购股份(?:的)?注销手续",
        r"(?:回购股份注销完成情况)?" + _CHINESE_DATE_PATTERN.pattern
        + r"[，,]?公司已.{0,100}?办理完毕.{0,100}?回购股份(?:的)?注销手续",
        r"截至" + _CHINESE_DATE_PATTERN.pattern
        + r"[，,]?公司已.{0,100}?办理完毕.{0,100}?"
        + r"(?:本次|上述)?回购股份(?:的)?注销手续",
        r"公司已于" + _CHINESE_DATE_PATTERN.pattern
        + r".{0,120}?办理完成.{0,120}?回购股份"
        + r".{0,40}?注销事宜",
    ):
        completion_match = re.search(pattern, normalized)
        if completion_match is not None:
            break
    if (
        completion_match is None
        or "注销完成后" not in normalized
    ):
        raise ValueError("cninfo_repurchase_cancellation_action_unverified")
    effective_on = _date_from_match(
        completion_match,
        "cninfo_repurchase_cancellation_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="repurchase_cancellation",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_merger_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "换股吸收合并" in title
        and "实施结果" in title
        and "新增股份上市公告" in title
    )


def parse_cninfo_merger_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_merger_implementation_title(title):
        raise ValueError("cninfo_merger_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="merger",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    if "本次股票上市类型为吸收合并股份" not in normalized:
        raise ValueError("cninfo_merger_action_unverified")
    effective_on = _date_from_match(
        re.search(
            r"股票上市流通日期为" + _CHINESE_DATE_PATTERN.pattern,
            normalized,
        ),
        "cninfo_merger_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="merger",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_code_change_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "证券代码" in title
        and "变更" in title
        and "实施公告" in title
    )


def parse_cninfo_code_change_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_code_change_implementation_title(title):
        raise ValueError("cninfo_code_change_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="code_change",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    target = re.search(r"变更后的证券代码：?(\d{6})", normalized)
    if target is None or target.group(1) == symbol:
        raise ValueError("cninfo_code_change_target_unverified")
    effective_on = _date_from_match(
        re.search(
            r"变更后的证券简称及证券代码启用日期为"
            + _CHINESE_DATE_PATTERN.pattern,
            normalized,
        ),
        "cninfo_code_change_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="code_change",
        new_symbol=target.group(1),
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def _is_reverse_split_implementation_title(value: Any) -> bool:
    title = _clean_cninfo_title(value)
    return (
        "缩股方案" in title
        and "实施" in title
        and title.endswith("公告")
        and not any(
            marker in title
            for marker in ("拟实施", "进展", "风险提示", "批复")
        )
    )


def parse_cninfo_reverse_split_text(
    *,
    title: str,
    text: str,
    symbol: str,
    exchange: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> CorporateActionEvidenceItem:
    if not _is_reverse_split_implementation_title(title):
        raise ValueError("cninfo_reverse_split_title_unverified")
    normalized = _validated_cninfo_action_context(
        action_name="reverse_split",
        text=text,
        symbol=symbol,
        exchange=exchange,
        announced_at=announced_at,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )
    if re.search(
        r"总股本将由(?:重整前的)?[0-9,]+股缩减至[0-9,]+股",
        normalized,
    ) is None:
        raise ValueError("cninfo_reverse_split_action_unverified")
    effective_on = _date_from_match(
        re.search(r"缩股实施日：?" + _CHINESE_DATE_PATTERN.pattern, normalized),
        "cninfo_reverse_split_effective_date_missing",
    )
    return _cninfo_action_item(
        symbol=symbol,
        exchange=exchange,
        action_type="reverse_split",
        announced_at=announced_at,
        effective_on=effective_on,
        source_url=source_url,
        source_sha256=source_sha256,
        document_id=document_id,
    )


def parse_bse_equity_distribution_text(
    *,
    title: str,
    text: str,
    symbol: str,
    announced_at: datetime,
    source_url: str,
    source_sha256: str,
    document_id: str,
) -> Tuple[CorporateActionEvidenceItem, ...]:
    """保留旧入口和稳定错误语义，内部复用三所解析器。"""

    try:
        return parse_cninfo_equity_distribution_text(
            title=title,
            text=text,
            symbol=symbol,
            exchange="bse",
            announced_at=announced_at,
            source_url=source_url,
            source_sha256=source_sha256,
            document_id=document_id,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason.startswith("cninfo_equity_distribution_"):
            reason = reason.replace(
                "cninfo_equity_distribution_",
                "bse_equity_distribution_",
                1,
            )
        raise ValueError(reason) from exc


def _response_content(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes) and content:
        return content
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text.encode("utf-8")
    raise ValueError("corporate_action_response_content_missing")


def _last_modified(response: Any) -> datetime:
    headers = getattr(response, "headers", None)
    value = headers.get("Last-Modified") if isinstance(headers, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("szse_corporate_action_source_time_missing")
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("szse_corporate_action_source_time_unverified")
    return parsed


def fetch_szse_monthly_corporate_actions(
    *,
    as_of: datetime,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> CorporateActionExchangeCollection:
    """抓取不晚于 ``as_of`` 的最新深交所公司行为月报。"""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf_timezone_required")
    market_as_of = as_of.astimezone(SHANGHAI_TZ)
    active_session = session or _session()
    index_response = active_session.get(
        SZSE_MONTH_INDEX_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    index_response.raise_for_status()
    index_html = _decode_official_html(_response_content(index_response))
    releases = []
    for path, year_text, month_text in re.findall(
        r"value\s*:\s*['\"]([^'\"]+)['\"]\s*,\s*"
        r"text\s*:\s*['\"](\d{4})-(\d{2})['\"]",
        index_html,
        flags=re.I | re.S,
    ):
        year, month = int(year_text), int(month_text)
        if (year, month) <= (market_as_of.year, market_as_of.month):
            releases.append((year, month, path))
    if not releases:
        raise ValueError("szse_corporate_action_release_missing")
    year, month, detail_path = max(releases)
    detail_url = urljoin(SZSE_MONTH_INDEX_URL, detail_path)
    detail_response = active_session.get(
        detail_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    detail_response.raise_for_status()
    detail_html = _decode_official_html(_response_content(detail_response))
    table_match = re.search(
        r"<a\b[^>]*href=['\"]([^'\"]*W\d+\.html)['\"][^>]*>"
        r"\s*分红派息配股\s*</a>",
        detail_html,
        flags=re.I | re.S,
    )
    if table_match is None:
        raise ValueError("szse_corporate_action_table_link_missing")
    table_url = urljoin(detail_url, unescape(table_match.group(1)))
    if table_url.startswith("http://"):
        table_url = "https://" + table_url.removeprefix("http://")
    table_response = active_session.get(
        table_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": detail_url,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    table_response.raise_for_status()
    fetched_at = clock()
    collection = parse_szse_monthly_corporate_actions(
        _response_content(table_response),
        source_url=table_url,
        source_time=_last_modified(table_response),
        fetched_at=fetched_at,
    )
    if collection.coverage_through != date(
        year,
        month,
        calendar.monthrange(year, month)[1],
    ):
        raise ValueError("szse_corporate_action_release_mismatch")
    return collection


def _cninfo_payload(
    session: Any,
    *,
    query_from: date,
    query_until: date,
    page_number: int,
    category: str,
    search_key: str = "",
    stock_filter: str = "",
) -> Mapping[str, Any]:
    policy_queries = {
        (query["category"], query.get("searchKey", ""))
        for query in cninfo_corporate_action_query_policy()["queries"].values()
    }
    if (
        not isinstance(query_from, date)
        or not isinstance(query_until, date)
        or query_from > query_until
        or (
            (category, search_key) not in policy_queries
            and not (
                category == ""
                and search_key == ""
                and re.fullmatch(r"\d{6},[A-Za-z0-9]+", stock_filter)
            )
        )
        or (
            stock_filter != ""
            and re.fullmatch(r"\d{6},[A-Za-z0-9]+", stock_filter) is None
        )
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
    ):
        raise ValueError("cninfo_corporate_action_query_unverified")
    request_kwargs = {
        "data": {
            "pageNum": str(page_number),
            "pageSize": "30",
            "tabName": "fulltext",
            "column": "szse",
            "stock": stock_filter,
            "searchkey": search_key,
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": (
                f"{query_from.isoformat()}~{query_until.isoformat()}"
            ),
            "sortName": "announcementId",
            "sortType": "desc",
            "isHLtitle": "true",
        },
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": (
                "https://www.cninfo.com.cn/new/commonUrl"
                "?url=disclosure/list/notice"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "timeout": REQUEST_TIMEOUT_SECONDS,
    }
    response = None
    for attempt in range(2):
        response = session.post(
            CNINFO_ANNOUNCEMENT_QUERY_URL,
            **request_kwargs,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if attempt == 0 and status_code in {502, 503, 504}:
                continue
            raise
        break
    assert response is not None
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("cninfo_corporate_action_payload_invalid")
    return payload


def validate_cninfo_corporate_action_pages(
    pages: Tuple[Mapping[str, Any], ...],
) -> Tuple[Mapping[str, Any], ...]:
    """验证巨潮公告分页并返回全量行。

    官方接口的 ``totalpages`` 现网存在向下取整语义，因此同时
    接受向下/向上两种页数，但必须依 ``hasMore`` 抓到末页且最终
    行数、唯一公告号与官方总数完全一致。
    """

    if not isinstance(pages, tuple) or not pages:
        raise ValueError("cninfo_corporate_action_pages_missing")
    reported_total = None
    reported_pages = None
    rows = []
    for index, payload in enumerate(pages):
        if not isinstance(payload, Mapping):
            raise ValueError("cninfo_corporate_action_payload_invalid")
        total_records = payload.get("totalRecordNum")
        total_announcements = payload.get("totalAnnouncement")
        total_pages = payload.get("totalpages")
        has_more = payload.get("hasMore")
        announcements = payload.get("announcements")
        if (
            total_records == 0
            and total_announcements == 0
            and total_pages == 0
            and has_more is False
            and announcements is None
        ):
            announcements = []
        if (
            not isinstance(total_records, int)
            or isinstance(total_records, bool)
            or total_records < 0
            or not isinstance(total_announcements, int)
            or isinstance(total_announcements, bool)
            or total_announcements != total_records
            or not isinstance(total_pages, int)
            or isinstance(total_pages, bool)
            or total_pages < 0
            or not isinstance(has_more, bool)
            or not isinstance(announcements, list)
            or len(announcements) > 30
        ):
            raise ValueError("cninfo_corporate_action_payload_invalid")
        if reported_total is None:
            reported_total = total_records
            reported_pages = total_pages
        elif (
            total_records != reported_total
            or total_pages != reported_pages
        ):
            raise ValueError("cninfo_corporate_action_pagination_inconsistent")
        if has_more != (index < len(pages) - 1):
            raise ValueError("cninfo_corporate_action_pagination_inconsistent")
        rows.extend(announcements)

    assert reported_total is not None
    assert reported_pages is not None
    floor_pages = reported_total // 30 if reported_total else 0
    ceil_pages = (reported_total + 29) // 30 if reported_total else 0
    if reported_pages not in {floor_pages, ceil_pages}:
        raise ValueError("cninfo_corporate_action_pagination_inconsistent")
    if len(rows) != reported_total:
        raise ValueError("cninfo_corporate_action_count_inconsistent")

    identities = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("cninfo_corporate_action_metadata_unverified")
        announcement_id = row.get("announcementId")
        if (
            not isinstance(announcement_id, (str, int))
            or not re.fullmatch(r"\d+", str(announcement_id))
        ):
            raise ValueError("cninfo_corporate_action_metadata_unverified")
        identity = str(announcement_id)
        if identity in identities:
            raise ValueError("cninfo_corporate_action_document_duplicate")
        identities.add(identity)
        cninfo_exchange_from_page_column(row.get("pageColumn"))
    return tuple(rows)


def _fetch_cninfo_corporate_action_pages(
    session: Any,
    *,
    query_from: date,
    query_until: date,
    category: str,
    search_key: str = "",
    stock_filter: str = "",
) -> Tuple[Mapping[str, Any], ...]:
    vendor_page_size = 30
    vendor_page_cap = 100
    first_payload = _cninfo_payload(
        session,
        query_from=query_from,
        query_until=query_until,
        page_number=1,
        category=category,
        search_key=search_key,
        stock_filter=stock_filter,
    )
    reported_total = first_payload.get("totalRecordNum")
    if (
        isinstance(reported_total, int)
        and not isinstance(reported_total, bool)
        and reported_total > vendor_page_size * vendor_page_cap
    ):
        if query_from == query_until:
            raise ValueError(
                "cninfo_corporate_action_daily_result_exceeds_page_cap"
            )
        midpoint = date.fromordinal(
            (query_from.toordinal() + query_until.toordinal()) // 2
        )
        left = _fetch_cninfo_corporate_action_pages(
            session,
            query_from=query_from,
            query_until=midpoint,
            category=category,
            search_key=search_key,
            stock_filter=stock_filter,
        )
        right = _fetch_cninfo_corporate_action_pages(
            session,
            query_from=midpoint + timedelta(days=1),
            query_until=query_until,
            category=category,
            search_key=search_key,
            stock_filter=stock_filter,
        )
        combined = (*left, *right)
        identities = [
            str(row.get("announcementId") or "") for row in combined
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("cninfo_corporate_action_document_duplicate")
        return combined

    pages = [first_payload]
    page_number = 1
    while True:
        payload = pages[-1]
        has_more = payload.get("hasMore")
        if has_more is False:
            break
        if has_more is not True:
            raise ValueError("cninfo_corporate_action_pagination_inconsistent")
        page_number += 1
        if page_number > vendor_page_cap:
            raise ValueError("cninfo_corporate_action_pagination_exceeded")
        pages.append(_cninfo_payload(
            session,
            query_from=query_from,
            query_until=query_until,
            page_number=page_number,
            category=category,
            search_key=search_key,
            stock_filter=stock_filter,
        ))
    return validate_cninfo_corporate_action_pages(tuple(pages))


def _cninfo_szse_stock_org_ids(
    session: Any,
    symbols: set[str],
) -> Mapping[str, str]:
    if not symbols or any(re.fullmatch(r"\d{6}", item) is None for item in symbols):
        raise ValueError("cninfo_stock_directory_symbols_unverified")
    response = session.get(
        CNINFO_STOCK_DIRECTORY_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.cninfo.com.cn/",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("stockList") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("cninfo_stock_directory_unverified")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("cninfo_stock_directory_unverified")
        symbol = str(row.get("code") or "").strip()
        if symbol not in symbols:
            continue
        org_id = str(row.get("orgId") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9]+", org_id) is None:
            raise ValueError("cninfo_stock_directory_unverified")
        existing = result.setdefault(symbol, org_id)
        if existing != org_id:
            raise ValueError("cninfo_stock_directory_unverified")
    return result


def _download_cninfo_pdf_text(
    session: Any,
    source_url: str,
) -> Tuple[str, str]:
    response = session.get(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.cninfo.com.cn/",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    content = _response_content(response)
    if not content.startswith(b"%PDF"):
        raise ValueError("cninfo_corporate_action_pdf_invalid")
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(BytesIO(content), strict=False).pages
    )
    return text, hashlib.sha256(content).hexdigest()


def build_cninfo_pdf_document_loader(
    cache_dir,
) -> Callable[[Any, str], Tuple[str, str]]:
    """构造只复用已验证官方PDF、不缓存公告查询结果的加载器。"""

    cache = CninfoPdfCache(cache_dir)

    def load_document(session: Any, source_url: str) -> Tuple[str, str]:
        return cache.load_text(
            session,
            source_url,
            extractor=lambda content: "\n".join(
                page.extract_text() or ""
                for page in PdfReader(BytesIO(content), strict=False).pages
            ),
            extractor_id=f"pypdf-{pypdf.__version__}",
        )

    return load_document


def _cninfo_unresolved_document(
    *,
    query_name: str,
    exchange: str,
    row: Mapping[str, Any],
    reason: str,
    source_sha256: Optional[str] = None,
) -> CorporateActionUnresolvedDocument:
    """在不放宽事件合同的前提下保留官方候选的拒绝原因。"""

    symbol_value = str(row.get("secCode") or "").strip()
    symbol = symbol_value if re.fullmatch(r"\d{6}", symbol_value) else None
    identifier_value = str(row.get("announcementId") or "").strip()
    announcement_id = (
        identifier_value
        if re.fullmatch(r"\d+", identifier_value)
        else None
    )
    title_value = row.get("announcementTitle")
    title = str(title_value).strip() if isinstance(title_value, str) else None
    try:
        announced_at = _announcement_time(row.get("announcementTime"))
    except (TypeError, ValueError):
        announced_at = None
    relative_url = str(row.get("adjunctUrl") or "").strip()
    candidate_url = urljoin(CNINFO_STATIC_BASE_URL, relative_url)
    source_url = (
        candidate_url
        if urlsplit(candidate_url).hostname == "static.cninfo.com.cn"
        else None
    )
    return CorporateActionUnresolvedDocument(
        exchange=exchange,
        queryName=query_name,
        symbol=symbol,
        announcementId=announcement_id,
        title=title,
        announcedAt=announced_at,
        sourceUrl=source_url,
        sourceSha256=source_sha256,
        reason=reason or "cninfo_corporate_action_document_unverified",
    )


def fetch_cninfo_equity_distribution_actions(
    *,
    as_of: datetime,
    query_from: Optional[date] = None,
    session=None,
    clock: Callable[[], datetime] = _now,
    document_loader: Optional[
        Callable[[Any, str], Tuple[str, str]]
    ] = None,
) -> Tuple[CorporateActionExchangeCollection, ...]:
    """按显式时间窗口采集巨潮沪深北公司行为实施公告。

    保留旧函数名以兼容现有调用方。提供方会完整分页验证官方分类或
    精确搜索结果，只将标题和PDF正文同时确定的实施事件升格。
    拆股和分立仍无可验证解析器；仅当官方精确检索实际命中对应实施
    公告时失败关闭。完整分页后的真实空结果属于已验证空窗口。
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf_timezone_required")
    query_until = as_of.astimezone(SHANGHAI_TZ).date()
    window_from = query_from or query_until
    if not isinstance(window_from, date) or window_from > query_until:
        raise ValueError("cninfo_corporate_action_window_unverified")
    active_session = session or _session()
    policy = cninfo_corporate_action_query_policy()
    rows_by_query = {
        query_name: _fetch_cninfo_corporate_action_pages(
            active_session,
            query_from=window_from,
            query_until=query_until,
            category=query["category"],
            search_key=query.get("searchKey", ""),
        )
        for query_name, query in policy["queries"].items()
    }
    fetched_at = clock()
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetchedAt_timezone_required")
    load_document = document_loader or _download_cninfo_pdf_text
    items_by_exchange = {exchange: [] for exchange in ("sse", "szse", "bse")}
    unresolved_by_exchange = {exchange: 0 for exchange in items_by_exchange}
    unresolved_documents_by_exchange = {
        exchange: [] for exchange in items_by_exchange
    }
    times_by_exchange = {exchange: [] for exchange in items_by_exchange}
    reasons_by_exchange = {exchange: [] for exchange in items_by_exchange}

    parser_rules = {
        "equity_distribution": (
            (_is_equity_distribution_implementation_title,
             parse_cninfo_equity_distribution_text),
        ),
        "rights_issue": (
            (_is_rights_issue_implementation_title,
             parse_cninfo_rights_issue_text),
            (_is_rights_issue_result_title,
             parse_cninfo_rights_issue_result_text),
        ),
        "placement": (
            (_is_placement_implementation_title,
             parse_cninfo_placement_text),
        ),
        "equity_change": (
            (_is_repurchase_cancellation_implementation_title,
             parse_cninfo_repurchase_cancellation_text),
            (_is_merger_implementation_title, parse_cninfo_merger_text),
        ),
        "code_change": (
            (_is_code_change_implementation_title,
             parse_cninfo_code_change_text),
        ),
        "reverse_split": (
            (_is_reverse_split_implementation_title,
             parse_cninfo_reverse_split_text),
        ),
        "stock_split": (),
        "demerger": (),
    }
    seen_items = {exchange: set() for exchange in items_by_exchange}
    for query_name, rows in rows_by_query.items():
        for row in rows:
            exchange = cninfo_exchange_from_page_column(row.get("pageColumn"))
            title = row.get("announcementTitle")
            matching_parsers = tuple(
                parser
                for predicate, parser in parser_rules[query_name]
                if predicate(title)
            )
            if not matching_parsers:
                query = policy["queries"][query_name]
                search_key = re.sub(r"\s+", "", query.get("searchKey", ""))
                clean_title = re.sub(r"\s+", "", str(title or ""))
                if (
                    query_name in {"stock_split", "demerger"}
                    and search_key
                    and search_key in clean_title
                ):
                    unresolved_by_exchange[exchange] += 1
                    reasons_by_exchange[exchange].append(
                        f"{exchange}_corporate_action_types_incomplete"
                    )
                    unresolved_documents_by_exchange[exchange].append(
                        _cninfo_unresolved_document(
                            query_name=query_name,
                            exchange=exchange,
                            row=row,
                            reason=f"cninfo_{query_name}_parser_missing",
                        )
                    )
                continue
            document_digest = None
            try:
                symbol = str(row.get("secCode") or "").strip()
                announcement_id = str(row.get("announcementId") or "").strip()
                announced_at = _announcement_time(row.get("announcementTime"))
                source_url = urljoin(
                    CNINFO_STATIC_BASE_URL,
                    str(row.get("adjunctUrl") or "").strip(),
                )
                if (
                    re.fullmatch(r"\d{6}", symbol) is None
                    or re.fullmatch(r"\d+", announcement_id) is None
                    or urlsplit(source_url).hostname != "static.cninfo.com.cn"
                    or announced_at > fetched_at
                    or not (window_from <= announced_at.date() <= query_until)
                ):
                    raise ValueError(
                        "cninfo_corporate_action_metadata_unverified"
                    )
                document_text, document_digest = load_document(
                    active_session,
                    source_url,
                )
                parsed_items = []
                document_symbols = [symbol]
                if query_name == "equity_distribution" and exchange == "szse":
                    b_share_symbol = _szse_b_share_symbol_from_document(
                        document_text,
                        symbol,
                    )
                    if b_share_symbol is not None:
                        document_symbols.append(b_share_symbol)
                for parser in matching_parsers:
                    for document_symbol in document_symbols:
                        parsed = parser(
                            title=str(title),
                            text=document_text,
                            symbol=document_symbol,
                            exchange=exchange,
                            announced_at=announced_at,
                            source_url=source_url,
                            source_sha256=document_digest,
                            document_id=f"cninfo:{announcement_id}",
                        )
                        if isinstance(parsed, CorporateActionEvidenceItem):
                            parsed_items.append(parsed)
                        else:
                            parsed_items.extend(parsed)
                if not parsed_items:
                    raise ValueError(
                        "cninfo_corporate_action_document_unverified"
                    )
            except (
                PdfReadError,
                requests.RequestException,
                TypeError,
                ValueError,
            ) as exc:
                unresolved_by_exchange[exchange] += 1
                reasons_by_exchange[exchange].append(
                    f"{exchange}_corporate_action_document_unverified"
                )
                unresolved_documents_by_exchange[exchange].append(
                    _cninfo_unresolved_document(
                        query_name=query_name,
                        exchange=exchange,
                        row=row,
                        reason=str(exc),
                        source_sha256=document_digest,
                    )
                )
                continue
            for item in parsed_items:
                identity = (
                    item.document_id,
                    item.symbol,
                    item.action_type,
                    item.effective_on,
                )
                if identity in seen_items[exchange]:
                    continue
                seen_items[exchange].add(identity)
                items_by_exchange[exchange].append(item)
            times_by_exchange[exchange].append(announced_at)

    canonical = json.dumps(
        rows_by_query,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return tuple(
        CorporateActionExchangeCollection(
            exchange=exchange,
            source_id=(
                f"cninfo-corporate-actions-v2:{exchange}:"
                f"{window_from.isoformat()}:{query_until.isoformat()}:"
                f"sha256={digest}"
            ),
            source=f"巨潮资讯{exchange.upper()}公司行为实施公告",
            source_time=(
                max(times_by_exchange[exchange])
                if times_by_exchange[exchange]
                else None
            ),
            fetched_at=fetched_at,
            coverage_from=window_from,
            coverage_through=query_until,
            expected_count=(
                len(items_by_exchange[exchange])
                + unresolved_by_exchange[exchange]
            ),
            items=tuple(items_by_exchange[exchange]),
            reasons=tuple(dict.fromkeys(reasons_by_exchange[exchange])),
            unresolved_documents=tuple(
                unresolved_documents_by_exchange[exchange]
            ),
        )
        for exchange in ("sse", "szse", "bse")
    )


def fetch_cninfo_szse_monthly_announcement_backfill(
    *,
    monthly: CorporateActionExchangeCollection,
    as_of: datetime,
    session=None,
    clock: Callable[[], datetime] = _now,
    document_loader: Optional[
        Callable[[Any, str], Tuple[str, str]]
    ] = None,
) -> CorporateActionExchangeCollection:
    """用巨潮实施公告回连深市月表的原公告时间。

    该来源只补强月表中证券、行为类型和实施日完全一致的事件；不会把
    其他公告扩入当前覆盖窗口。全市分类查询未命中时，只对仍缺口的证券
    使用巨潮官方证券目录中的 ``orgId`` 精确回连。
    """

    if (
        monthly.exchange != "szse"
        or monthly.coverage_from.day != 1
        or monthly.coverage_through
        != date(
            monthly.coverage_from.year,
            monthly.coverage_from.month,
            calendar.monthrange(
                monthly.coverage_from.year,
                monthly.coverage_from.month,
            )[1],
        )
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
    ):
        raise ValueError("szse_monthly_announcement_backfill_input_unverified")
    missing_identities = {
        (
            item.symbol,
            item.action_type,
            item.effective_on,
            item.new_symbol,
        )
        for item in monthly.items
        if item.announced_at is None
    }
    if not missing_identities:
        raise ValueError("szse_monthly_announcement_backfill_not_required")

    query_until = monthly.coverage_from - timedelta(days=1)
    query_from = date(query_until.year, query_until.month, 1)
    if query_until >= as_of.astimezone(SHANGHAI_TZ).date():
        raise ValueError("szse_monthly_announcement_backfill_window_unverified")

    active_session = session or _session()
    rows = _fetch_cninfo_corporate_action_pages(
        active_session,
        query_from=query_from,
        query_until=query_until,
        category="category_qyfpxzcs_szsh",
    )
    fetched_at = clock()
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetchedAt_timezone_required")
    load_document = document_loader or _download_cninfo_pdf_text
    matched = {}
    monthly_symbols = {identity[0] for identity in missing_identities}

    def process_rows(
        candidate_rows: Tuple[Mapping[str, Any], ...],
        *,
        allowed_from: date,
        allowed_until: date,
    ) -> None:
        for row in candidate_rows:
            title = row.get("announcementTitle")
            parser = None
            if _is_equity_distribution_implementation_title(title):
                parser = parse_cninfo_equity_distribution_text
            elif _is_rights_issue_result_title(title):
                parser = parse_cninfo_rights_issue_result_text
            if (
                cninfo_exchange_from_page_column(row.get("pageColumn"))
                != "szse"
                or str(row.get("secCode") or "").strip()
                not in monthly_symbols
                or parser is None
            ):
                continue
            try:
                symbol = str(row.get("secCode") or "").strip()
                announcement_id = str(
                    row.get("announcementId") or ""
                ).strip()
                announced_at = _announcement_time(row.get("announcementTime"))
                source_url = urljoin(
                    CNINFO_STATIC_BASE_URL,
                    str(row.get("adjunctUrl") or "").strip(),
                )
                if (
                    re.fullmatch(r"\d{6}", symbol) is None
                    or re.fullmatch(r"\d+", announcement_id) is None
                    or urlsplit(source_url).hostname
                    != "static.cninfo.com.cn"
                    or announced_at > fetched_at
                    or not (
                        allowed_from
                        <= announced_at.date()
                        <= allowed_until
                    )
                ):
                    raise ValueError(
                        "szse_monthly_announcement_backfill_metadata_unverified"
                    )
                document_text, document_digest = load_document(
                    active_session,
                    source_url,
                )
                parsed = parser(
                    title=str(title or ""),
                    text=document_text,
                    symbol=symbol,
                    exchange="szse",
                    announced_at=announced_at,
                    source_url=source_url,
                    source_sha256=document_digest,
                    document_id=f"cninfo:{announcement_id}",
                )
                parsed_items = (
                    (parsed,)
                    if isinstance(parsed, CorporateActionEvidenceItem)
                    else parsed
                )
            except (
                PdfReadError,
                requests.RequestException,
                TypeError,
                ValueError,
            ):
                continue
            for item in parsed_items:
                identity = (
                    item.symbol,
                    item.action_type,
                    item.effective_on,
                    item.new_symbol,
                )
                matched_identity = identity
                broad_bonus_identity = (
                    item.symbol,
                    "bonus_share",
                    item.effective_on,
                    item.new_symbol,
                )
                if (
                    matched_identity not in missing_identities
                    and item.action_type == "capitalization_issue"
                    and broad_bonus_identity in missing_identities
                ):
                    matched_identity = broad_bonus_identity
                if matched_identity not in missing_identities:
                    continue
                existing = matched.get(matched_identity)
                if (
                    existing is None
                    or item.announced_at > existing.announced_at
                ):
                    matched[matched_identity] = item

    process_rows(
        rows,
        allowed_from=query_from,
        allowed_until=query_until,
    )
    unmatched_identities = missing_identities - set(matched)
    symbol_rows = []
    stock_org_ids = {}
    if unmatched_identities:
        unmatched_symbols = {
            identity[0] for identity in unmatched_identities
        }
        stock_org_ids = dict(_cninfo_szse_stock_org_ids(
            active_session,
            unmatched_symbols,
        ))
        symbol_query_from = query_from
        symbol_query_until = monthly.coverage_through
        for symbol in sorted(unmatched_symbols):
            org_id = stock_org_ids.get(symbol)
            if org_id is None:
                continue
            queried_rows = _fetch_cninfo_corporate_action_pages(
                active_session,
                query_from=symbol_query_from,
                query_until=symbol_query_until,
                category="",
                search_key="",
                stock_filter=f"{symbol},{org_id}",
            )
            symbol_rows.extend(queried_rows)
            process_rows(
                queried_rows,
                allowed_from=symbol_query_from,
                allowed_until=symbol_query_until,
            )
    canonical = json.dumps(
        {
            "categoryRows": rows,
            "stockOrgIds": stock_org_ids,
            "symbolRows": symbol_rows,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    items = tuple(matched.values())
    return CorporateActionExchangeCollection(
        exchange="szse",
        source_id=(
            "cninfo-szse-monthly-announcement-backfill-v2:"
            f"{query_from.isoformat()}:{query_until.isoformat()}:"
            f"sha256={digest}"
        ),
        source="巨潮资讯深市月表原公告时间回连",
        source_time=(
            max(item.announced_at for item in items)
            if items
            else None
        ),
        fetched_at=fetched_at,
        coverage_from=monthly.coverage_from,
        coverage_through=monthly.coverage_through,
        expected_count=len(items),
        items=items,
    )


def _announcement_time(value: Any) -> datetime:
    if not isinstance(value, int) or value <= 0:
        raise ValueError("bse_corporate_action_announcement_time_invalid")
    return datetime.fromtimestamp(value / 1000, tz=SHANGHAI_TZ)


def fetch_bse_equity_distribution_actions(
    *,
    as_of: datetime,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> CorporateActionExchangeCollection:
    """采集巨潮官方当日 BJS 权益分派实施公告。

    这个提供方只证明权益分派子集，不声称已覆盖合并、分拆、
    回购注销等全部公司行为。
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf_timezone_required")
    active_session = session or _session()
    query_date = as_of.astimezone(SHANGHAI_TZ).date()
    rows = []
    page_number = 1
    reported_total = None
    while True:
        payload = _cninfo_payload(
            active_session,
            query_from=query_date,
            query_until=query_date,
            page_number=page_number,
            category="category_qyfpxzcs_szsh",
        )
        announcements = payload.get("announcements")
        total = payload.get("totalAnnouncement")
        has_more = payload.get("hasMore")
        if (
            not isinstance(announcements, list)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(has_more, bool)
        ):
            raise ValueError("bse_corporate_action_payload_invalid")
        if reported_total is None:
            reported_total = total
        elif reported_total != total:
            raise ValueError("bse_corporate_action_pagination_inconsistent")
        rows.extend(announcements)
        if not has_more:
            break
        page_number += 1
        if page_number > 50:
            raise ValueError("bse_corporate_action_pagination_exceeded")
    if reported_total != len(rows):
        raise ValueError("bse_corporate_action_count_inconsistent")

    fetched_at = clock()
    items = []
    unresolved = 0
    reasons = ["bse_corporate_action_types_incomplete"]
    source_times = []
    bse_rows = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("pageColumn") != "BJS":
            continue
        title = row.get("announcementTitle")
        if not isinstance(title, str):
            unresolved += 1
            reasons.append("bse_corporate_action_metadata_unverified")
            continue
        clean_title = re.sub(r"\s+", "", _cell_text(title))
        if (
            "权益分派实施公告" not in clean_title
            or "提示性" in clean_title
        ):
            continue
        bse_rows.append(row)
        try:
            symbol = str(row.get("secCode") or "").strip()
            announcement_id = str(row.get("announcementId") or "").strip()
            relative_url = str(row.get("adjunctUrl") or "").strip()
            announced_at = _announcement_time(row.get("announcementTime"))
            source_url = urljoin(CNINFO_STATIC_BASE_URL, relative_url)
            if (
                re.fullmatch(r"\d{6}", symbol) is None
                or re.fullmatch(r"\d+", announcement_id) is None
                or urlsplit(source_url).hostname != "static.cninfo.com.cn"
                or announced_at > fetched_at
            ):
                raise ValueError("bse_corporate_action_metadata_unverified")
            pdf_response = active_session.get(
                source_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.cninfo.com.cn/",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            pdf_response.raise_for_status()
            pdf_content = _response_content(pdf_response)
            if not pdf_content.startswith(b"%PDF"):
                raise ValueError("bse_corporate_action_pdf_invalid")
            pdf_text = "\n".join(
                page.extract_text() or ""
                for page in PdfReader(BytesIO(pdf_content), strict=False).pages
            )
            digest = hashlib.sha256(pdf_content).hexdigest()
            parsed_items = parse_bse_equity_distribution_text(
                title=title,
                text=pdf_text,
                symbol=symbol,
                announced_at=announced_at,
                source_url=source_url,
                source_sha256=digest,
                document_id=f"cninfo:{announcement_id}",
            )
        except (
            PdfReadError,
            requests.RequestException,
            ValueError,
            TypeError,
        ):
            unresolved += 1
            reasons.append("bse_corporate_action_document_unverified")
            continue
        items.extend(parsed_items)
        source_times.append(announced_at)

    canonical = json.dumps(
        bse_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return CorporateActionExchangeCollection(
        exchange="bse",
        source_id=(
            f"cninfo-bjs-equity-distribution:{query_date.isoformat()}:"
            f"sha256={digest}"
        ),
        source="巨潮资讯北交所权益分派实施公告",
        source_time=max(source_times) if source_times else None,
        fetched_at=fetched_at,
        coverage_from=query_date,
        coverage_through=query_date,
        expected_count=len(items) + unresolved,
        items=tuple(items),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _merge_exchange_collections(
    exchange: str,
    values: Tuple[CorporateActionExchangeCollection, ...],
) -> CorporateActionExchangeCollection:
    if not values or any(value.exchange != exchange for value in values):
        raise ValueError("corporate_action_exchange_mismatch")
    ordered = sorted(values, key=lambda value: value.coverage_from)
    reasons = [reason for value in ordered for reason in value.reasons]
    previous_through = ordered[0].coverage_through
    for value in ordered[1:]:
        if value.coverage_from > date.fromordinal(previous_through.toordinal() + 1):
            reasons.append(f"{exchange}_corporate_action_window_gap")
        previous_through = max(previous_through, value.coverage_through)

    precise_share_distribution_keys = {
        (
            item.symbol,
            item.exchange,
            item.effective_on,
            item.new_symbol,
        )
        for value in ordered
        for item in value.items
        if (
            item.action_type in {"bonus_share", "capitalization_issue"}
            and item.announced_at is not None
        )
    }
    items_by_identity = {}
    duplicate_count = 0
    for value in ordered:
        for item in value.items:
            broad_share_key = (
                item.symbol,
                item.exchange,
                item.effective_on,
                item.new_symbol,
            )
            if (
                item.exchange == "szse"
                and item.action_type == "bonus_share"
                and item.announced_at is None
                and item.source_name == "深圳证券交易所统计月报"
                and broad_share_key in precise_share_distribution_keys
            ):
                duplicate_count += 1
                continue
            identity = (
                item.symbol,
                item.exchange,
                item.action_type,
                item.effective_on,
                item.new_symbol,
            )
            existing = items_by_identity.get(identity)
            if existing is not None:
                duplicate_count += 1
                existing_time = existing.announced_at
                candidate_time = item.announced_at
                if (
                    candidate_time is not None
                    and (
                        existing_time is None
                        or candidate_time >= existing_time
                    )
                ):
                    items_by_identity[identity] = item
                continue
            items_by_identity[identity] = item
    items = list(items_by_identity.values())
    if items and all(item.announced_at is not None for item in items):
        reasons = [
            reason
            for reason in reasons
            if reason != f"{exchange}_company_announcement_time_missing"
        ]
    expected_count = sum(value.expected_count for value in ordered) - duplicate_count
    unresolved_documents = []
    unresolved_identities = set()
    for value in ordered:
        for unresolved in value.unresolved_documents:
            identity = (
                unresolved.query_name,
                unresolved.announcement_id,
                unresolved.source_url,
                unresolved.reason,
            )
            if identity in unresolved_identities:
                continue
            unresolved_identities.add(identity)
            unresolved_documents.append(unresolved)
    component_ids = "|".join(value.source_id for value in ordered)
    digest = hashlib.sha256(component_ids.encode("utf-8")).hexdigest()
    source_times = [
        value.source_time for value in ordered if value.source_time is not None
    ]
    return CorporateActionExchangeCollection(
        exchange=exchange,
        source_id=f"merged-{exchange}-corporate-actions:sha256={digest}",
        source=" + ".join(dict.fromkeys(value.source for value in ordered)),
        source_time=max(source_times) if source_times else None,
        fetched_at=max(value.fetched_at for value in ordered),
        coverage_from=min(value.coverage_from for value in ordered),
        coverage_through=max(value.coverage_through for value in ordered),
        expected_count=expected_count,
        items=tuple(items),
        reasons=tuple(dict.fromkeys(reasons)),
        unresolved_documents=tuple(unresolved_documents),
    )


def fetch_corporate_action_forward_snapshot(
    *,
    as_of: datetime,
    session=None,
    clock: Callable[[], datetime] = _now,
    szse_collector: Optional[Callable[..., CorporateActionExchangeCollection]] = None,
    bse_collector: Optional[Callable[..., CorporateActionExchangeCollection]] = None,
    cninfo_collector: Optional[
        Callable[..., Tuple[CorporateActionExchangeCollection, ...]]
    ] = None,
    szse_announcement_backfill_collector: Optional[
        Callable[..., CorporateActionExchangeCollection]
    ] = None,
    cninfo_document_loader: Optional[
        Callable[[Any, str], Tuple[str, str]]
    ] = None,
) -> CorporateActionForwardSnapshot:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf_timezone_required")
    query_date = as_of.astimezone(SHANGHAI_TZ).strftime("%Y%m%d")
    params = {
        "isPagination": "true",
        "order": "tradeBeginDate|desc,stockCode|desc",
        "tradeBeginDate": query_date,
        "tradeEndDate": query_date,
        "sqlId": "PL_SCRL_SCRLB",
        "bizType": 5,
        "pageHelp.pageNo": 1,
        "pageHelp.pageSize": 500,
    }
    active_session = session or _session()
    response = active_session.get(
        SSE_CORPORATE_ACTION_URL,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": SSE_CALENDAR_REFERER,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result")
    page_help = payload.get("pageHelp")
    if not isinstance(rows, list) or not isinstance(page_help, dict):
        raise RuntimeError("sse_corporate_action_payload_invalid")
    total = page_help.get("total")
    if not isinstance(total, int) or total < 0 or total < len(rows):
        raise RuntimeError("sse_corporate_action_payload_invalid")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    sse_source_id = (
        f"sse-market-calendar:{query_date}:rawCount={total}:"
        f"sha256={digest}"
    )
    component_collections = []
    missing_exchanges = []
    coverage_reasons = {}
    use_all_market_cninfo = cninfo_collector is not None or bse_collector is None
    collectors = [("szse", szse_collector or fetch_szse_monthly_corporate_actions)]
    if not use_all_market_cninfo:
        collectors.append(("bse", bse_collector))
    for exchange, collector in collectors:
        assert collector is not None
        try:
            collection = collector(
                as_of=as_of,
                session=active_session,
                clock=clock,
            )
            if collection.exchange != exchange:
                raise ValueError("corporate_action_exchange_mismatch")
        except (
            AttributeError,
            requests.RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            missing_exchanges.append(exchange)
            coverage_reasons[exchange] = [
                f"{exchange}_corporate_action_source_failed"
            ]
            continue
        component_collections.append(collection)

    szse_monthly = next(
        (
            value
            for value in component_collections
            if value.exchange == "szse"
        ),
        None,
    )
    backfill_collector = szse_announcement_backfill_collector
    if backfill_collector is None and cninfo_collector is None:
        backfill_collector = fetch_cninfo_szse_monthly_announcement_backfill
    if (
        szse_monthly is not None
        and backfill_collector is not None
        and any(item.announced_at is None for item in szse_monthly.items)
    ):
        try:
            backfill_kwargs = {
                "monthly": szse_monthly,
                "as_of": as_of,
                "session": active_session,
                "clock": clock,
            }
            if cninfo_document_loader is not None:
                backfill_kwargs["document_loader"] = cninfo_document_loader
            backfill = backfill_collector(**backfill_kwargs)
            if (
                backfill.exchange != "szse"
                or backfill.coverage_from != szse_monthly.coverage_from
                or backfill.coverage_through != szse_monthly.coverage_through
            ):
                raise ValueError(
                    "szse_monthly_announcement_backfill_scope_unverified"
                )
        except (
            AttributeError,
            requests.RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            component_collections.append(CorporateActionExchangeCollection(
                exchange="szse",
                source_id="szse-monthly-announcement-backfill-failed",
                source="巨潮资讯深市月表原公告时间回连",
                source_time=None,
                fetched_at=clock(),
                coverage_from=szse_monthly.coverage_from,
                coverage_through=szse_monthly.coverage_through,
                expected_count=0,
                items=(),
                reasons=("szse_company_announcement_backfill_failed",),
            ))
        else:
            component_collections.append(backfill)

    if use_all_market_cninfo:
        query_until = as_of.astimezone(SHANGHAI_TZ).date()
        cninfo_query_from = query_until
        if szse_monthly is not None:
            cninfo_query_from = min(
                query_until,
                szse_monthly.coverage_from,
            )
        try:
            cninfo_kwargs = {
                "as_of": as_of,
                "query_from": cninfo_query_from,
                "session": active_session,
                "clock": clock,
            }
            if cninfo_document_loader is not None:
                cninfo_kwargs["document_loader"] = cninfo_document_loader
            cninfo_collections = (
                cninfo_collector or fetch_cninfo_equity_distribution_actions
            )(
                **cninfo_kwargs,
            )
            if (
                not isinstance(cninfo_collections, tuple)
                or {value.exchange for value in cninfo_collections}
                != {"sse", "szse", "bse"}
            ):
                raise ValueError("corporate_action_exchange_mismatch")
        except (
            AttributeError,
            requests.RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            for exchange in ("szse", "bse"):
                if not any(
                    value.exchange == exchange for value in component_collections
                ):
                    missing_exchanges.append(exchange)
                    coverage_reasons[exchange] = [
                        f"{exchange}_corporate_action_source_failed"
                    ]
        else:
            component_collections.extend(cninfo_collections)

    collections = []
    for exchange in ("sse", "szse", "bse"):
        components = tuple(
            value for value in component_collections if value.exchange == exchange
        )
        if not components:
            continue
        collection = _merge_exchange_collections(exchange, components)
        collections.append(collection)
        if collection.reasons:
            coverage_reasons[exchange] = list(collection.reasons)

    fetched_at = clock()
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetchedAt_timezone_required")

    covered_exchanges = list(dict.fromkeys([
        "sse",
        *(item.exchange for item in collections),
    ]))
    items = [item for collection in collections for item in collection.items]
    unresolved_documents = [
        unresolved
        for collection in collections
        for unresolved in collection.unresolved_documents
    ]
    returned_count_by_exchange = {
        "sse": 0,
        **{
            collection.exchange: len(collection.items)
            for collection in collections
        },
    }
    coverage_through = {
        "sse": as_of.astimezone(SHANGHAI_TZ).date(),
        **{
            collection.exchange: collection.coverage_through
            for collection in collections
        },
    }
    coverage_from = {
        "sse": as_of.astimezone(SHANGHAI_TZ).date(),
        **{
            collection.exchange: collection.coverage_from
            for collection in collections
        },
    }
    component_ids = [sse_source_id, *(item.source_id for item in collections)]
    component_digest = hashlib.sha256(
        "|".join(component_ids).encode("utf-8")
    ).hexdigest()
    source_times = [
        item.source_time
        for item in collections
        if item.source_time is not None
    ]
    missing_exchanges = list(dict.fromkeys(missing_exchanges))

    # 市场日历行只有标题，无公告日、实施日和原文链接时不升格为事件。
    return CorporateActionForwardSnapshot(
        sourceId=(
            f"official-corporate-actions:{query_date}:rawCount={total}:"
            f"sha256={component_digest}"
        ),
        source="沪深北交易所公开官方公司行为来源",
        sourceTime=(max(source_times) if source_times else None),
        fetchedAt=fetched_at,
        coveredExchanges=covered_exchanges,
        missingExchanges=missing_exchanges,
        expectedCount=(
            sum(item.expected_count for item in collections)
        ),
        returnedCountByExchange=returned_count_by_exchange,
        coverageFromByExchange=coverage_from,
        coverageThroughByExchange=coverage_through,
        coverageReasonsByExchange=coverage_reasons,
        items=items,
        unresolvedDocuments=unresolved_documents,
    )
