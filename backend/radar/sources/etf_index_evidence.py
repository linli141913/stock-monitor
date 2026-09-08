import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from pypdf import PdfReader

from radar.contracts import (
    EtfIndexIdentityEvidence,
    EtfManagementStyle,
    EvidenceTemporalBasis,
    EvidenceVersionKind,
    IndexConstituentEvidenceItem,
    IndexConstituentSetEvidence,
    IndexEvidenceStatus,
    IndexMethodologyEvidence,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
REQUEST_TIMEOUT_SECONDS = 20.0
INDEX_STATIC_EVIDENCE_MAX_MISSED_TRADING_DAYS = 2
INDEX_CURRENT_CONSTITUENT_MAX_MISSED_TRADING_DAYS = 1

SSE_FUND_ANNOUNCEMENT_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_FUND_ANNOUNCEMENT_BASE_URL = "https://www.sse.com.cn"
SSE_FUND_RELATION_SOURCE_CONTRACT_ID = (
    "sse-official-etf-index-relation-prospectus-v1"
)

EFUNDS_PRODUCT_URL = "https://www.efunds.com.cn/fund/{symbol}.shtml"
CSINDEX_BASIC_URL = (
    "https://www.csindex.com.cn/csindex-home/"
    "indexInfo/index-basic-info/{index_code}"
)
CSINDEX_SEARCH_URL = (
    "https://www.csindex.com.cn/csindex-home/"
    "index-list/query-index-item"
)
CSINDEX_MATERIAL_CATALOG_URL = (
    "https://www.csindex.com.cn/csindex-home/"
    "indexInfo/index-details-data"
)
CSINDEX_IDENTITY_RESOLUTION_CONTRACT_ID = (
    "csindex-official-full-name-resolution-v1"
)
CSINDEX_FACTSHEET_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "indices/detail/files/zh_CN/{index_code}factsheet.pdf"
)
CSINDEX_METHODOLOGY_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "indices/detail/files/zh_CN/{index_code}_Index_Methodology_cn.pdf"
)
CSINDEX_CONSTITUENTS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/{index_code}cons.xls"
)
CSINDEX_CLOSE_WEIGHT_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/closeweight/{index_code}closeweight.xls"
)
CNINDEX_IDENTITY_URL = (
    "https://www.cnindex.com.cn/index/selectIndexByCode"
)
CNINDEX_INTRO_URL = "https://www.cnindex.com.cn/index-intro"
CNINDEX_METHODOLOGY_PATH_URL = (
    "https://www.cnindex.com.cn/info/getColorPageBZFA"
)
CNINDEX_CONSTITUENTS_URL = (
    "https://www.cnindex.com.cn/sample-detail/detail"
)
CNINDEX_METHODOLOGY_URL = (
    "https://www.cnindex.com.cn/docs/gz_{index_code}.pdf"
)


@dataclass(frozen=True)
class ParsedFundIndexEvidence:
    management_style: EtfManagementStyle
    index_code: Optional[str]
    index_name: Optional[str]


@dataclass(frozen=True)
class OfficialFundIndexEvidence:
    symbol: str
    parsed: ParsedFundIndexEvidence
    evidence_url: str
    evidence_sha256: str
    fetched_at: datetime


@dataclass(frozen=True)
class ParsedSseFundRelationDocument:
    index_name: Optional[str]
    provider_name: Optional[str]
    content_cutoff_date: Optional[date]


@dataclass(frozen=True)
class OfficialSseFundRelationDocument:
    symbol: str
    index_name: Optional[str]
    provider_name: Optional[str]
    published_at: Optional[datetime]
    evidence_valid_from: Optional[datetime]
    evidence_url: str
    evidence_sha256: str
    fetched_at: datetime
    status: IndexEvidenceStatus
    formal_ready: bool
    reasons: Tuple[str, ...]
    source_contract_id: str = SSE_FUND_RELATION_SOURCE_CONTRACT_ID


@dataclass(frozen=True)
class OfficialIndexIdentityResolution:
    requested_index_name: str
    provider: str
    index_code: Optional[str]
    index_name: Optional[str]
    index_published_at: Optional[datetime]
    identity_resolved: bool
    status: IndexEvidenceStatus
    reasons: Tuple[str, ...]
    source_contract_id: str
    search_evidence_url: str
    search_content_sha256: str
    identity_evidence_url: Optional[str]
    identity_content_sha256: Optional[str]
    fetched_at: datetime
    index_classification: Optional[str] = None


@dataclass(frozen=True)
class OfficialIndexPocResult:
    provider: str
    index_code: str
    index_name: str
    identity_evidence_url: str
    identity_evidence_sha256: str
    methodology: IndexMethodologyEvidence
    constituent_sets: Tuple[IndexConstituentSetEvidence, ...]
    fetched_at: datetime


@dataclass(frozen=True)
class OfficialCsindexMaterialCatalog:
    index_code: str
    methodology_url: str
    methodology_published_at: Optional[datetime]
    constituents_url: str
    close_weight_url: str
    evidence_url: str
    evidence_sha256: str
    fetched_at: datetime


class _EfundsProductParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.management_descriptions: List[str] = []
        self.cells: List[Tuple[Dict[str, str], str]] = []
        self._td_attrs: Optional[Dict[str, str]] = None
        self._td_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            description = attributes.get("data-level1desc")
            if description:
                self.management_descriptions.append(description.strip())
        if tag == "td":
            self._td_attrs = attributes
            self._td_text = []

    def handle_data(self, data):
        if self._td_attrs is not None:
            self._td_text.append(data)

    def handle_endtag(self, tag):
        if tag != "td" or self._td_attrs is None:
            return
        text = " ".join(part.strip() for part in self._td_text if part.strip())
        self.cells.append((self._td_attrs, text.strip()))
        self._td_attrs = None
        self._td_text = []


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_sha256(value: Any) -> str:
    return _sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _require_csindex_material(
    *,
    index_code: str,
    records: Any,
    file_type: str,
    filename_pattern: str,
    path_prefix: str,
) -> str:
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("csindex_material_catalog_unverified")
    record = records[0]
    if not isinstance(record, dict):
        raise ValueError("csindex_material_catalog_unverified")
    returned_type = str(record.get("fileType") or "").strip().lower()
    returned_name = str(record.get("fileName") or "").strip()
    returned_url = str(record.get("filePath") or "").strip()
    parsed = urlparse(returned_url)
    basename = parsed.path.rsplit("/", 1)[-1]
    if any((
        returned_type != file_type,
        parsed.scheme != "https",
        parsed.hostname != "oss-ch.csindex.com.cn",
        not parsed.path.startswith(path_prefix),
        re.fullmatch(filename_pattern, basename) is None,
        index_code not in returned_name,
    )):
        raise ValueError("csindex_material_catalog_unverified")
    if parsed.query and not parsed.query.isdigit():
        raise ValueError("csindex_material_catalog_unverified")
    return returned_url


def build_csindex_material_catalog(
    *,
    index_code: str,
    payload: Any,
    source_sha256: str,
    fetched_at: datetime,
) -> OfficialCsindexMaterialCatalog:
    """Resolve current official files without assuming legacy filenames."""
    if (
        not re.fullmatch(r"\d{6}", index_code)
        or not isinstance(payload, dict)
        or str(payload.get("code")) != "200"
        or payload.get("success") is not True
        or not isinstance(payload.get("data"), dict)
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        or fetched_at.tzinfo is None
        or fetched_at.utcoffset() is None
    ):
        raise ValueError("csindex_material_catalog_unverified")
    data = payload["data"]
    escaped_code = re.escape(index_code)
    methodology_url = _require_csindex_material(
        index_code=index_code,
        records=data.get("编制方案"),
        file_type="pdf",
        filename_pattern=(
            rf"(?:\d{{14}}-)?{escaped_code}_Index_Methodology_cn\.pdf"
        ),
        path_prefix=(
            "/static/html/csindex/public/uploads/indices/detail/files/zh_CN/"
        ),
    )
    constituents_url = _require_csindex_material(
        index_code=index_code,
        records=data.get("样本列表"),
        file_type="xls",
        filename_pattern=rf"{escaped_code}cons\.xls",
        path_prefix=(
            "/static/html/csindex/public/uploads/file/autofile/cons/"
        ),
    )
    close_weight_url = _require_csindex_material(
        index_code=index_code,
        records=data.get("样本权重"),
        file_type="xls",
        filename_pattern=rf"{escaped_code}closeweight\.xls",
        path_prefix=(
            "/static/html/csindex/public/uploads/file/autofile/closeweight/"
        ),
    )
    timestamp_match = re.fullmatch(
        rf"(\d{{14}})-{escaped_code}_Index_Methodology_cn\.pdf",
        urlparse(methodology_url).path.rsplit("/", 1)[-1],
    )
    methodology_published_at = None
    if timestamp_match is not None:
        try:
            methodology_published_at = datetime.strptime(
                timestamp_match.group(1),
                "%Y%m%d%H%M%S",
            ).replace(tzinfo=SHANGHAI_TZ)
        except ValueError as exc:
            raise ValueError(
                "csindex_material_catalog_unverified"
            ) from exc
    return OfficialCsindexMaterialCatalog(
        index_code=index_code,
        methodology_url=methodology_url,
        methodology_published_at=methodology_published_at,
        constituents_url=constituents_url,
        close_weight_url=close_weight_url,
        evidence_url=CSINDEX_MATERIAL_CATALOG_URL,
        evidence_sha256=source_sha256,
        fetched_at=fetched_at,
    )


def _normalize_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", "", normalized).strip()
    return normalized or None


def _published_datetime(value: Any) -> Optional[datetime]:
    try:
        parsed = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
    return datetime(
        parsed.year,
        parsed.month,
        parsed.day,
        tzinfo=SHANGHAI_TZ,
    )


def _identity_candidate_rows(
    rows: List[Any],
    requested_index_name: str,
) -> List[Any]:
    """Prefer an official short-name exact hit over fuzzy search neighbours."""
    requested = _normalize_name(requested_index_name)
    hinted = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        short_name = _normalize_name(row.get("indexName"))
        if short_name and requested in {short_name, f"{short_name}指数"}:
            hinted.append(row)
    return hinted or rows


def _optional_float(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _optional_date(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _pdf_text(content: bytes) -> Tuple[str, Dict[str, Any]]:
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text, dict(reader.metadata or {})


def _official_document_text(content: bytes) -> str:
    """Extract an official PDF, with UTF-8 text accepted at the test boundary."""
    if not content.startswith(b"%PDF-"):
        return content.decode("utf-8")
    try:
        text, _ = _pdf_text(content)
        return text
    except Exception:
        return content.decode("utf-8")


def parse_sse_fund_relation_document(
    content: bytes,
) -> ParsedSseFundRelationDocument:
    text = _official_document_text(content)
    compact = unicodedata.normalize("NFKC", text)

    cutoff_match = re.search(
        r"(?:(?:本更新招募说明书|本招募说明书)"
        r"所载(?:其余)?内容|其他所载内容)\s*截止日为\s*"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        compact,
    )
    cutoff = None
    if cutoff_match is not None:
        try:
            cutoff = date(*(int(value) for value in cutoff_match.groups()))
        except ValueError:
            cutoff = None

    index_names = {
        _normalize_name(match.group(1))
        for match in re.finditer(
            r"本基金的标的指数为\s*([^。；;\n]{2,80}?指数)\s*[。；;]",
            compact,
        )
    }
    index_names.discard(None)
    index_name = next(iter(index_names)) if len(index_names) == 1 else None

    provider_names = {
        provider
        for provider in ("中证指数有限公司", "深圳证券信息有限公司")
        if provider in compact
    }
    provider_name = (
        next(iter(provider_names)) if len(provider_names) == 1 else None
    )
    return ParsedSseFundRelationDocument(
        index_name=index_name,
        provider_name=provider_name,
        content_cutoff_date=cutoff,
    )


def _sse_announcement_url(value: Any) -> Optional[str]:
    path = str(value or "").strip()
    if not path:
        return None
    if path.startswith("https://www.sse.com.cn/"):
        return path
    if path.startswith("/"):
        return f"{SSE_FUND_ANNOUNCEMENT_BASE_URL}{path}"
    return None


def build_official_sse_fund_relation_document(
    *,
    symbol: str,
    announcement_rows: List[Any],
    document_content: bytes,
    fetched_at: datetime,
) -> OfficialSseFundRelationDocument:
    """Build the latest official relation document without backdating it.

    ``evidence_valid_from`` is deliberately the latest prospectus content
    cutoff date.  It is a conservative date on which the current relation is
    evidenced, not the fund's historical relation inception date.
    """
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("symbol必须是6位基金代码")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at_timezone_required")

    reasons = []
    valid_rows = []
    mismatched_symbols = False
    for row in announcement_rows:
        if not isinstance(row, dict):
            reasons.append("fund_relation_announcement_source_incomplete")
            continue
        row_symbol = str(row.get("SECURITY_CODE") or "").strip()
        title = str(row.get("TITLE") or "").strip()
        if row_symbol != symbol:
            if row_symbol:
                mismatched_symbols = True
            continue
        if "招募说明书" not in title:
            continue
        published_at = _published_datetime(row.get("SSEDATE"))
        evidence_url = _sse_announcement_url(row.get("URL"))
        if published_at is None or evidence_url is None:
            reasons.append("fund_relation_announcement_source_incomplete")
            continue
        valid_rows.append((published_at, evidence_url, row))

    selected = None
    if not valid_rows:
        if mismatched_symbols:
            reasons.append("fund_relation_symbol_mismatch")
        else:
            reasons.append("fund_relation_announcement_missing")
    else:
        latest_date = max(item[0] for item in valid_rows)
        latest = {
            (item[0], item[1]): item
            for item in valid_rows
            if item[0] == latest_date
        }
        if len(latest) != 1:
            reasons.append("fund_relation_announcement_ambiguous")
        else:
            selected = next(iter(latest.values()))

    parsed = ParsedSseFundRelationDocument(None, None, None)
    if selected is not None:
        try:
            parsed = parse_sse_fund_relation_document(document_content)
        except Exception:
            reasons.append("fund_relation_document_parse_failed")
    if parsed.index_name is None:
        reasons.append("fund_relation_index_name_missing")
    if parsed.provider_name is None:
        reasons.append("fund_relation_provider_missing")
    if parsed.content_cutoff_date is None:
        reasons.append("fund_relation_evidence_date_missing")

    published_at = selected[0] if selected is not None else None
    evidence_url = (
        selected[1]
        if selected is not None
        else SSE_FUND_ANNOUNCEMENT_QUERY_URL
    )
    evidence_valid_from = (
        datetime(
            parsed.content_cutoff_date.year,
            parsed.content_cutoff_date.month,
            parsed.content_cutoff_date.day,
            tzinfo=SHANGHAI_TZ,
        )
        if parsed.content_cutoff_date is not None
        else None
    )
    if (
        (published_at is not None and published_at > fetched_at)
        or (
            evidence_valid_from is not None
            and evidence_valid_from > fetched_at
        )
    ):
        reasons.append("fund_relation_future_evidence")

    unique_reasons = tuple(dict.fromkeys(reasons))
    conflict_reasons = {
        "fund_relation_symbol_mismatch",
        "fund_relation_announcement_ambiguous",
        "fund_relation_future_evidence",
    }
    failure_reasons = {
        "fund_relation_announcement_source_incomplete",
        "fund_relation_document_parse_failed",
    }
    if set(unique_reasons) & conflict_reasons:
        status = IndexEvidenceStatus.CONFLICT
    elif set(unique_reasons) & failure_reasons:
        status = IndexEvidenceStatus.SOURCE_FAILED
    elif unique_reasons:
        status = IndexEvidenceStatus.PENDING_EVIDENCE
    else:
        status = IndexEvidenceStatus.VERIFIED
    return OfficialSseFundRelationDocument(
        symbol=symbol,
        index_name=parsed.index_name,
        provider_name=parsed.provider_name,
        published_at=published_at,
        evidence_valid_from=evidence_valid_from,
        evidence_url=evidence_url,
        evidence_sha256=_sha256(document_content),
        fetched_at=fetched_at,
        status=status,
        formal_ready=(status == IndexEvidenceStatus.VERIFIED),
        reasons=unique_reasons,
    )


def _sse_get(session, url: str, **kwargs):
    for attempt in range(2):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                raise
    raise RuntimeError("official_fund_relation_request_unreachable")


def fetch_official_sse_fund_relation_document(
    symbol: str,
    *,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialSseFundRelationDocument:
    """Fetch the latest SSE prospectus for one fund, read-only and bounded."""
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("symbol必须是6位基金代码")
    fetched_at = clock()
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at_timezone_required")
    active_session = session or _session()
    try:
        response = _sse_get(
            active_session,
            SSE_FUND_ANNOUNCEMENT_QUERY_URL,
            params={
                "isPagination": "true",
                "sqlId": "COMMON_PL_JJXX_JJGG_NEW_L",
                "pageHelp.pageSize": "100",
                "pageHelp.pageNo": "1",
                "pageHelp.beginPage": "1",
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": "1",
                "type": "inParams",
                "TITLE": "招募说明书",
                "SECURITY_CODE": symbol,
                "BULLETIN_TYPE": "",
                "START_DATE": "2000-01-01",
                "END_DATE": fetched_at.date().isoformat(),
                "DATE_DESC": "",
            },
            headers={
                "Referer": (
                    "https://www.sse.com.cn/disclosure/fund/announcement/"
                ),
                "User-Agent": "Mozilla/5.0",
            },
        )
        payload = response.json()
        rows = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("official_fund_announcement_payload_invalid")

        exact = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("SECURITY_CODE") or "").strip() != symbol:
                continue
            if "招募说明书" not in str(row.get("TITLE") or ""):
                continue
            published_at = _published_datetime(row.get("SSEDATE"))
            evidence_url = _sse_announcement_url(row.get("URL"))
            if published_at is not None and evidence_url is not None:
                exact.append((published_at, evidence_url))
        document_content = b""
        if exact:
            latest_date = max(item[0] for item in exact)
            latest_urls = {
                item[1] for item in exact if item[0] == latest_date
            }
            if len(latest_urls) == 1:
                document_response = _sse_get(
                    active_session,
                    next(iter(latest_urls)),
                    headers={
                        "Referer": (
                            "https://www.sse.com.cn/disclosure/fund/"
                            "announcement/"
                        ),
                        "User-Agent": "Mozilla/5.0",
                    },
                )
                document_content = document_response.content
        return build_official_sse_fund_relation_document(
            symbol=symbol,
            announcement_rows=rows,
            document_content=document_content,
            fetched_at=fetched_at,
        )
    except Exception:
        return OfficialSseFundRelationDocument(
            symbol=symbol,
            index_name=None,
            provider_name=None,
            published_at=None,
            evidence_valid_from=None,
            evidence_url=SSE_FUND_ANNOUNCEMENT_QUERY_URL,
            evidence_sha256=_sha256(b""),
            fetched_at=fetched_at,
            status=IndexEvidenceStatus.SOURCE_FAILED,
            formal_ready=False,
            reasons=("fund_relation_source_failed",),
        )


def _factsheet_confirms_identity(
    content: bytes,
    *,
    index_code: str,
    requested_index_name: str,
) -> bool:
    try:
        text, _ = _pdf_text(content)
    except Exception:
        return False
    normalized_text = _normalize_name(text)
    normalized_name = _normalize_name(requested_index_name)
    return bool(
        normalized_text
        and normalized_name
        and normalized_name in normalized_text
        and index_code in text
    )


def _section(text: str, start_number: int, end_number: int) -> Optional[str]:
    chinese_numbers = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
    }
    start_markers = [str(start_number)]
    end_markers = [str(end_number)]
    if start_number in chinese_numbers:
        start_markers.append(chinese_numbers[start_number])
    if end_number in chinese_numbers:
        end_markers.append(chinese_numbers[end_number])
    pattern = re.compile(
        rf"(?:{'|'.join(start_markers)})\s*[、.．]\s*(.*?)"
        rf"(?=(?:{'|'.join(end_markers)})\s*[、.．])",
        re.DOTALL,
    )
    values = []
    for match in pattern.finditer(text):
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        if value:
            values.append(value[:4000])
    if not values:
        return None
    substantive = [
        value for value in values
        if re.search(r"[.．…]{4,}", value) is None
    ]
    candidates = substantive or values
    return max(candidates, key=len)


def _methodology_selection_parts(
    text: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Split the official selection chapter into its semantic fields."""
    selection_chapter = _section(text, 3, 4)
    if selection_chapter is None:
        return None, None
    subsection_marker = r"(?:\d+|[一二三四五六七八九十]+)\s*[、.．]"
    universe_match = re.search(
        rf"{subsection_marker}\s*样本空间\s*(.*?)"
        rf"(?={subsection_marker}\s*(?:选样方法|样本选取方法))",
        selection_chapter,
        flags=re.DOTALL,
    )
    selection_match = re.search(
        rf"{subsection_marker}\s*(?:选样方法|样本选取方法)\s*(.*)$",
        selection_chapter,
        flags=re.DOTALL,
    )

    def cleaned(match) -> Optional[str]:
        if match is None:
            return None
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        return value[:4000] or None

    universe = cleaned(universe_match)
    selection = cleaned(selection_match)
    if universe is not None and selection is not None:
        return universe, selection

    # Some official methodologies (for example CSI 300) make sample space
    # and selection method adjacent top-level chapters instead of nesting
    # them below a single "sample selection" chapter.
    def named_section(
        start_number: int,
        start_title: str,
        end_number: int,
        end_title: str,
    ) -> Optional[str]:
        pattern = re.compile(
            rf"^[ \t]*{start_number}\s*[、.．]\s*"
            rf"(?:{start_title})[ \t]*\r?$"
            rf"(.*?)"
            rf"(?=^[ \t]*{end_number}\s*[、.．]\s*"
            rf"(?:{end_title})[ \t]*\r?$)",
            flags=re.DOTALL | re.MULTILINE,
        )
        matches = [
            re.sub(r"\s+", " ", match.group(1)).strip()
            for match in pattern.finditer(text)
        ]
        values = [value[:4000] for value in matches if value]
        return max(values, key=len) if values else None

    direct_universe = named_section(
        2,
        r"样本空间",
        3,
        r"选样方法|样本选取方法",
    )
    direct_selection = named_section(
        3,
        r"选样方法|样本选取方法",
        4,
        r"指数计算",
    )
    if direct_universe is None or direct_selection is None:
        return universe, selection
    return direct_universe, direct_selection


def _constituent_cap(*values: Optional[str]) -> Optional[int]:
    for value in values:
        if not value:
            continue
        for pattern in (
            r"(\d{1,4})\s*只",
            r"(?:排名\s*)?前\s*(\d{1,4})\s*(?:只|的)?\s*证券",
        ):
            match = re.search(pattern, value)
            if match is not None:
                return int(match.group(1))
    return None


def parse_efunds_product_page(content: bytes) -> ParsedFundIndexEvidence:
    parser = _EfundsProductParser()
    parser.feed(content.decode("utf-8", errors="replace"))

    management_style = EtfManagementStyle.UNKNOWN
    for description in parser.management_descriptions:
        if "被动" in description:
            management_style = EtfManagementStyle.PASSIVE_INDEX
            break
        if "主动" in description:
            management_style = EtfManagementStyle.ACTIVE
            break

    index_code = None
    index_name = None
    for attributes, text in parser.cells:
        if attributes.get("id") == "UNDERLYINGSECURITYID":
            candidate = text.strip()
            if candidate:
                index_code = candidate
                break
    for position, (_, text) in enumerate(parser.cells):
        label = _normalize_name(text)
        if label not in {"标的指数名称:", "标的指数名称："}:
            continue
        for _, candidate in parser.cells[position + 1:]:
            candidate = candidate.strip()
            if candidate:
                index_name = candidate
                break
        if index_name is not None:
            break

    return ParsedFundIndexEvidence(
        management_style=management_style,
        index_code=index_code,
        index_name=index_name,
    )


def verify_etf_index_identity(
    *,
    symbol: str,
    management_style: EtfManagementStyle,
    fund_index_code: Optional[str],
    fund_index_name: Optional[str],
    provider: str,
    provider_index_code: str,
    provider_index_name: str,
    published_at: Optional[datetime],
    effective_from: Optional[datetime],
    effective_to: Optional[datetime],
    as_of: datetime,
    fund_evidence_url: str,
    fund_evidence_sha256: str,
    provider_evidence_url: str,
    provider_evidence_sha256: str,
    first_observed_at: datetime,
    fetched_at: datetime,
    official_master_index_name: Optional[str] = None,
    fund_provider_name: Optional[str] = None,
) -> EtfIndexIdentityEvidence:
    reasons = []
    identity_matched = False

    if management_style == EtfManagementStyle.ACTIVE:
        status = IndexEvidenceStatus.PENDING_EVIDENCE
        reasons.append("active_etf_registry_only")
    elif management_style != EtfManagementStyle.PASSIVE_INDEX:
        status = IndexEvidenceStatus.PENDING_EVIDENCE
        reasons.append("management_style_unknown")
    elif not fund_index_code or not fund_index_name:
        status = IndexEvidenceStatus.PENDING_EVIDENCE
        reasons.append("index_identity_missing")
    elif (
        fund_index_code.strip() != provider_index_code.strip()
        or _normalize_name(fund_index_name)
        != _normalize_name(provider_index_name)
        or (
            official_master_index_name is not None
            and _normalize_name(fund_index_name)
            != _normalize_name(official_master_index_name)
        )
    ):
        status = IndexEvidenceStatus.CONFLICT
        reasons.append("index_identity_conflict")
    elif (
        fund_provider_name is not None
        and _normalize_name(fund_provider_name)
        != _normalize_name({
            "csindex": "中证指数有限公司",
            "cnindex": "深圳证券信息有限公司",
        }.get(provider))
    ):
        status = IndexEvidenceStatus.CONFLICT
        reasons.append("index_provider_conflict")
    else:
        status = IndexEvidenceStatus.VERIFIED
        identity_matched = True

    if identity_matched:
        if published_at is None:
            reasons.append("relation_published_at_missing")
        if effective_from is None:
            reasons.append("relation_effective_from_missing")
        elif effective_from > as_of:
            reasons.append("relation_future_effective")
        if effective_to is not None and effective_to <= as_of:
            reasons.append("relation_historical_expired")

    formal_ready = status == IndexEvidenceStatus.VERIFIED and not reasons
    return EtfIndexIdentityEvidence(
        symbol=symbol,
        managementStyle=management_style,
        fundIndexCode=fund_index_code,
        fundIndexName=fund_index_name,
        indexProvider=provider,
        providerIndexCode=provider_index_code,
        providerIndexName=provider_index_name,
        identityMatched=identity_matched,
        publishedAt=published_at,
        effectiveFrom=effective_from,
        effectiveTo=effective_to,
        asOf=as_of,
        fundEvidenceUrl=fund_evidence_url,
        fundEvidenceSha256=fund_evidence_sha256,
        providerEvidenceUrl=provider_evidence_url,
        providerEvidenceSha256=provider_evidence_sha256,
        firstObservedAt=first_observed_at,
        fetchedAt=fetched_at,
        status=status,
        formalReady=formal_ready,
        reasons=tuple(reasons),
    )


def verify_official_sse_fund_index_relation(
    *,
    document: OfficialSseFundRelationDocument,
    official_master_target_index_name: str,
    provider_resolution: OfficialIndexIdentityResolution,
    as_of: datetime,
) -> EtfIndexIdentityEvidence:
    """Join three official identities: exchange master, fund PDF, provider."""
    if not provider_resolution.identity_resolved:
        raise ValueError("provider_index_identity_not_resolved")
    if (
        provider_resolution.index_code is None
        or provider_resolution.index_name is None
        or provider_resolution.identity_evidence_url is None
        or provider_resolution.identity_content_sha256 is None
    ):
        raise ValueError("provider_index_identity_incomplete")
    return verify_etf_index_identity(
        symbol=document.symbol,
        management_style=EtfManagementStyle.PASSIVE_INDEX,
        # The code is admitted only after the official fund name, exchange
        # master name and provider full name form one exact identity chain.
        fund_index_code=provider_resolution.index_code,
        fund_index_name=document.index_name,
        provider=provider_resolution.provider,
        provider_index_code=provider_resolution.index_code,
        provider_index_name=provider_resolution.index_name,
        published_at=document.published_at,
        effective_from=document.evidence_valid_from,
        effective_to=None,
        as_of=as_of,
        fund_evidence_url=document.evidence_url,
        fund_evidence_sha256=document.evidence_sha256,
        provider_evidence_url=provider_resolution.identity_evidence_url,
        provider_evidence_sha256=(
            provider_resolution.identity_content_sha256
        ),
        first_observed_at=document.fetched_at,
        fetched_at=max(document.fetched_at, provider_resolution.fetched_at),
        official_master_index_name=official_master_target_index_name,
        fund_provider_name=document.provider_name,
    )


def build_methodology_evidence(
    *,
    provider: str,
    index_code: str,
    index_name: str,
    provider_version: Optional[str],
    published_at: Optional[datetime],
    effective_from: Optional[datetime],
    effective_to: Optional[datetime],
    universe_rule: Optional[str],
    selection_rule: Optional[str],
    weighting_method: Optional[str],
    constituent_cap: Optional[int],
    rebalance_frequency: Optional[str],
    as_of: datetime,
    evidence_url: str,
    evidence_sha256: str,
    first_observed_at: datetime,
    fetched_at: datetime,
    version_kind: EvidenceVersionKind = EvidenceVersionKind.OFFICIAL,
    temporal_basis: EvidenceTemporalBasis = (
        EvidenceTemporalBasis.EXACT_EFFECTIVE_INTERVAL
    ),
) -> IndexMethodologyEvidence:
    reasons = []
    if provider_version is None:
        reasons.append("methodology_version_missing")
    if published_at is None:
        reasons.append("methodology_published_at_missing")
    if temporal_basis == EvidenceTemporalBasis.EXACT_EFFECTIVE_INTERVAL:
        if effective_from is None:
            reasons.append("methodology_effective_from_missing")
        elif effective_from > as_of:
            reasons.append("methodology_future_effective")
        if effective_to is not None and effective_to <= as_of:
            reasons.append("methodology_historical_expired")
    elif any((
        version_kind != EvidenceVersionKind.OFFICIAL,
        first_observed_at > as_of,
        fetched_at > as_of,
    )):
        reasons.append("methodology_current_observation_unverified")
    if not all((
        universe_rule,
        selection_rule,
        weighting_method,
        constituent_cap,
        rebalance_frequency,
    )):
        reasons.append("methodology_fields_incomplete")

    formal_ready = not reasons
    return IndexMethodologyEvidence(
        indexProvider=provider,
        indexCode=index_code,
        indexName=index_name,
        providerVersion=provider_version,
        versionKind=version_kind,
        temporalBasis=temporal_basis,
        publishedAt=published_at,
        effectiveFrom=effective_from,
        effectiveTo=effective_to,
        universeRule=universe_rule,
        selectionRule=selection_rule,
        weightingMethod=weighting_method,
        constituentCap=constituent_cap,
        rebalanceFrequency=rebalance_frequency,
        asOf=as_of,
        evidenceUrl=evidence_url,
        evidenceSha256=evidence_sha256,
        firstObservedAt=first_observed_at,
        fetchedAt=fetched_at,
        status=(
            IndexEvidenceStatus.VERIFIED
            if formal_ready
            else IndexEvidenceStatus.PENDING_EVIDENCE
        ),
        formalReady=formal_ready,
        reasons=tuple(reasons),
    )


def build_constituent_set_evidence(
    *,
    provider: str,
    index_code: str,
    index_name: str,
    announced_at: Optional[datetime],
    effective_from: Optional[datetime],
    effective_to: Optional[datetime],
    expected_count: Optional[int],
    as_of: datetime,
    evidence_url: str,
    evidence_sha256: str,
    first_observed_at: datetime,
    fetched_at: datetime,
    items,
    source_date: Optional[date] = None,
    temporal_basis: EvidenceTemporalBasis = (
        EvidenceTemporalBasis.EXACT_EFFECTIVE_INTERVAL
    ),
) -> IndexConstituentSetEvidence:
    normalized_items = [
        (
            item
            if isinstance(item, IndexConstituentEvidenceItem)
            else IndexConstituentEvidenceItem.model_validate(item)
        )
        for item in items
    ]
    reasons = []
    returned_count = len(normalized_items)
    codes = [item.stock_code for item in normalized_items]
    weight_values = [
        item.weight
        for item in normalized_items
        if item.weight is not None
    ]
    weight_count = len(weight_values)
    weight_total = round(sum(weight_values), 8) if weight_values else None

    if not normalized_items:
        reasons.append("constituent_empty")
    if len(codes) != len(set(codes)):
        reasons.append("constituent_duplicate")
    if expected_count is None or returned_count != expected_count:
        reasons.append("constituent_coverage_insufficient")
    if normalized_items and weight_count != returned_count:
        reasons.append("constituent_weight_missing")
    elif weight_total is not None and abs(weight_total - 100.0) > 0.5:
        reasons.append("constituent_weight_total_abnormal")
    if temporal_basis == EvidenceTemporalBasis.EXACT_EFFECTIVE_INTERVAL:
        if announced_at is None:
            reasons.append("constituent_announced_at_missing")
        if effective_from is None:
            reasons.append("constituent_effective_from_missing")
        elif effective_from > as_of:
            reasons.append("constituent_future_effective")
        if effective_to is not None and effective_to <= as_of:
            reasons.append("constituent_historical_expired")
    elif any((
        source_date is None,
        source_date is not None and source_date > as_of.date(),
        first_observed_at > as_of,
        fetched_at > as_of,
    )):
        reasons.append("constituent_current_observation_unverified")

    formal_ready = not reasons
    return IndexConstituentSetEvidence(
        indexProvider=provider,
        indexCode=index_code,
        indexName=index_name,
        temporalBasis=temporal_basis,
        announcedAt=announced_at,
        effectiveFrom=effective_from,
        effectiveTo=effective_to,
        sourceDate=source_date,
        expectedCount=expected_count,
        returnedCount=returned_count,
        weightCount=weight_count,
        weightTotal=weight_total,
        asOf=as_of,
        evidenceUrl=evidence_url,
        evidenceSha256=evidence_sha256,
        firstObservedAt=first_observed_at,
        fetchedAt=fetched_at,
        items=normalized_items,
        status=(
            IndexEvidenceStatus.VERIFIED
            if formal_ready
            else IndexEvidenceStatus.PENDING_EVIDENCE
        ),
        formalReady=formal_ready,
        reasons=tuple(reasons),
    )


def fetch_efunds_product_index_evidence(
    symbol: str,
    *,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialFundIndexEvidence:
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("symbol必须是6位基金代码")
    url = EFUNDS_PRODUCT_URL.format(symbol=symbol)
    active_session = session or _session()
    response = active_session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    fetched_at = clock()
    content = response.content
    return OfficialFundIndexEvidence(
        symbol=symbol,
        parsed=parse_efunds_product_page(content),
        evidence_url=url,
        evidence_sha256=_sha256(content),
        fetched_at=fetched_at,
    )


def build_official_csindex_identity_resolution(
    *,
    requested_index_name: str,
    search_payload: Any,
    basic_payloads: Dict[str, Any],
    fetched_at: datetime,
) -> OfficialIndexIdentityResolution:
    """Resolve one index only through exact official full-name equality.

    The browser search is deliberately treated as candidate discovery.  A
    candidate becomes verified only after the separate official basic-info
    response returns the same code and an exact normalized full Chinese name.
    The index publication date is descriptive evidence; it is never reused as
    the ETF-to-index relation publication or effective date.
    """
    normalized_requested = _normalize_name(requested_index_name)
    if normalized_requested is None:
        raise ValueError("index_name_required")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at_timezone_required")

    reasons = []
    search_rows = []
    if not isinstance(search_payload, dict):
        reasons.append("provider_index_source_failed")
    elif str(search_payload.get("code")) != "200":
        reasons.append("provider_index_source_failed")
    elif not isinstance(search_payload.get("data"), list):
        reasons.append("provider_index_source_failed")
    else:
        search_rows = search_payload["data"]

    search_rows = _identity_candidate_rows(
        search_rows,
        requested_index_name,
    )

    candidates = {}
    source_incomplete = False
    for row in search_rows:
        if not isinstance(row, dict):
            source_incomplete = True
            continue
        code = str(row.get("indexCode") or "").strip()
        if not code:
            source_incomplete = True
            continue
        if code in candidates:
            continue
        basic_payload = basic_payloads.get(code)
        if not isinstance(basic_payload, dict):
            source_incomplete = True
            continue
        if str(basic_payload.get("code")) != "200":
            source_incomplete = True
            continue
        basic = basic_payload.get("data")
        if not isinstance(basic, dict):
            source_incomplete = True
            continue
        returned_code = str(basic.get("indexCode") or "").strip()
        full_name = _normalize_name(basic.get("indexFullNameCn"))
        if returned_code != code or full_name is None:
            source_incomplete = True
            continue
        candidates[code] = (row, basic_payload, full_name)

    exact = [
        (code, values)
        for code, values in candidates.items()
        if values[2] == normalized_requested
    ]
    if reasons:
        status = IndexEvidenceStatus.SOURCE_FAILED
    elif source_incomplete:
        reasons.append("provider_index_source_incomplete")
        status = IndexEvidenceStatus.SOURCE_FAILED
    elif not exact:
        reasons.append("provider_index_identity_not_found")
        status = IndexEvidenceStatus.PENDING_EVIDENCE
    elif len(exact) > 1:
        reasons.append("provider_index_identity_ambiguous")
        status = IndexEvidenceStatus.CONFLICT
    else:
        status = IndexEvidenceStatus.VERIFIED

    resolved = status == IndexEvidenceStatus.VERIFIED
    selected_code = exact[0][0] if resolved else None
    selected = exact[0][1] if resolved else None
    return OfficialIndexIdentityResolution(
        requested_index_name=requested_index_name.strip(),
        provider="csindex",
        index_code=selected_code,
        index_name=selected[2] if selected is not None else None,
        index_published_at=(
            _published_datetime(selected[0].get("publishDate"))
            if selected is not None
            else None
        ),
        identity_resolved=resolved,
        status=status,
        reasons=tuple(dict.fromkeys(reasons)),
        source_contract_id=CSINDEX_IDENTITY_RESOLUTION_CONTRACT_ID,
        search_evidence_url=CSINDEX_SEARCH_URL,
        search_content_sha256=_json_sha256(search_payload),
        identity_evidence_url=(
            str(selected[1].get("_evidenceUrl"))
            if selected is not None and selected[1].get("_evidenceUrl")
            else CSINDEX_BASIC_URL.format(index_code=selected_code)
            if selected_code is not None
            else None
        ),
        identity_content_sha256=(
            str(selected[1].get("_evidenceContentSha256"))
            if (
                selected is not None
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(selected[1].get("_evidenceContentSha256") or ""),
                )
            )
            else _json_sha256(selected[1]) if selected is not None else None
        ),
        fetched_at=fetched_at,
        index_classification=(
            str(selected[0].get("indexClassify") or "").strip() or None
            if selected is not None
            else None
        ),
    )


def _post_response_bytes(session, url: str, **kwargs) -> bytes:
    for attempt in range(2):
        try:
            response = session.post(
                url,
                headers={
                    "Referer": "https://www.csindex.com.cn/",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
            return response.content
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                raise
    raise RuntimeError("official_index_request_unreachable")


def fetch_official_csindex_identity_resolution(
    requested_index_name: str,
    *,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialIndexIdentityResolution:
    normalized_requested = _normalize_name(requested_index_name)
    if normalized_requested is None:
        raise ValueError("index_name_required")
    fetched_at = clock()
    active_session = session or _session()
    try:
        search_content = _post_response_bytes(
            active_session,
            CSINDEX_SEARCH_URL,
            json={
                "sorter": {"sortField": "null", "sortOrder": None},
                "pager": {"pageNum": 1, "pageSize": 20},
                "searchInput": requested_index_name.strip(),
                "indexFilter": {},
            },
        )
        search_payload = json.loads(search_content.decode("utf-8"))
        basic_payloads = {}
        rows = (
            search_payload.get("data", [])
            if isinstance(search_payload, dict)
            else []
        )
        if isinstance(rows, list):
            rows = _identity_candidate_rows(rows, requested_index_name)
        for row in rows[:20] if isinstance(rows, list) else []:
            code = (
                str(row.get("indexCode") or "").strip()
                if isinstance(row, dict)
                else ""
            )
            if not code or code in basic_payloads:
                continue
            try:
                content = _response_bytes(
                    active_session,
                    CSINDEX_BASIC_URL.format(index_code=code),
                )
                basic_payloads[code] = json.loads(content.decode("utf-8"))
            except Exception:
                factsheet_url = CSINDEX_FACTSHEET_URL.format(
                    index_code=code,
                )
                try:
                    factsheet_content = _response_bytes(
                        active_session,
                        factsheet_url,
                    )
                    if _factsheet_confirms_identity(
                        factsheet_content,
                        index_code=code,
                        requested_index_name=requested_index_name,
                    ):
                        basic_payloads[code] = {
                            "code": "200",
                            "data": {
                                "indexCode": code,
                                "indexFullNameCn": requested_index_name.strip(),
                            },
                            "_evidenceUrl": factsheet_url,
                            "_evidenceContentSha256": _sha256(
                                factsheet_content
                            ),
                        }
                    else:
                        basic_payloads[code] = None
                except Exception:
                    basic_payloads[code] = None
    except Exception:
        search_payload = {"code": "source_failed", "data": []}
        basic_payloads = {}
    finally:
        if session is None:
            active_session.close()

    return build_official_csindex_identity_resolution(
        requested_index_name=requested_index_name,
        search_payload=search_payload,
        basic_payloads=basic_payloads,
        fetched_at=fetched_at,
    )


def _read_excel(content: bytes) -> pd.DataFrame:
    return pd.read_excel(BytesIO(content), dtype=str)


def _response_bytes(session, url: str, **kwargs) -> bytes:
    for attempt in range(2):
        try:
            response = session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
            return response.content
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                raise
    raise RuntimeError("official_index_request_unreachable")


def fetch_csindex_index_poc(
    index_code: str,
    as_of: datetime,
    *,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialIndexPocResult:
    active_session = session or _session()
    basic_url = CSINDEX_BASIC_URL.format(index_code=index_code)
    basic_content = _response_bytes(active_session, basic_url)
    basic_payload = json.loads(basic_content.decode("utf-8"))
    if str(basic_payload.get("code")) != "200" or not basic_payload.get("data"):
        raise RuntimeError("中证指数基础信息响应无有效数据")
    basic = basic_payload["data"]
    returned_code = str(basic.get("indexCode") or "").strip()
    if returned_code != index_code:
        raise RuntimeError("中证指数基础信息代码不一致")
    index_name = str(basic.get("indexFullNameCn") or "").strip()
    if not index_name:
        raise RuntimeError("中证指数基础信息缺少名称")

    material_content = _response_bytes(
        active_session,
        CSINDEX_MATERIAL_CATALOG_URL,
        params={"fileLang": 2, "indexCode": index_code},
    )
    material_catalog = build_csindex_material_catalog(
        index_code=index_code,
        payload=json.loads(material_content.decode("utf-8")),
        source_sha256=_sha256(material_content),
        fetched_at=clock(),
    )
    methodology_url = material_catalog.methodology_url
    methodology_content = _response_bytes(active_session, methodology_url)
    methodology_text, _ = _pdf_text(methodology_content)
    date_match = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月",
        methodology_text[:3000],
    )
    label_match = re.search(
        r"版本号\s*[VＶ]\s*([0-9]+(?:\.[0-9]+)*)",
        methodology_text[:3000],
        flags=re.IGNORECASE,
    )
    provider_version = (
        (
            f"V{label_match.group(1)}@"
            f"{date_match.group(1)}-{int(date_match.group(2)):02d}"
        )
        if date_match is not None and label_match is not None
        else (
            f"{date_match.group(1)}-{int(date_match.group(2)):02d}"
            if date_match is not None
            else None
        )
    )
    constituent_cap = _constituent_cap(
        str(basic.get("indexCnDesc") or ""),
        methodology_text[:6000],
    )
    universe_rule, selection_rule = _methodology_selection_parts(
        methodology_text,
    )
    constituent_materials = []
    for url, has_weight in (
        (material_catalog.constituents_url, False),
        (material_catalog.close_weight_url, True),
    ):
        content = _response_bytes(active_session, url)
        constituent_materials.append((
            url,
            has_weight,
            content,
            _read_excel(content),
        ))
    fetched_at = clock()
    # This adapter performs several network requests before the current
    # official methodology/weights can be observed.  Bind those current-
    # observation contracts no earlier than the final fetch time; callers
    # may later bind them to an even later forward replay sample.
    observation_as_of = max(as_of, fetched_at)
    methodology = build_methodology_evidence(
        provider="csindex",
        index_code=index_code,
        index_name=index_name,
        provider_version=provider_version,
        version_kind=EvidenceVersionKind.OFFICIAL,
        temporal_basis=(
            EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION
        ),
        published_at=material_catalog.methodology_published_at,
        effective_from=None,
        effective_to=None,
        universe_rule=universe_rule,
        selection_rule=selection_rule,
        weighting_method=_section(methodology_text, 4, 5),
        constituent_cap=constituent_cap,
        rebalance_frequency=str(basic.get("adjFreqCn") or "").strip() or None,
        as_of=observation_as_of,
        evidence_url=methodology_url,
        evidence_sha256=_sha256(methodology_content),
        first_observed_at=fetched_at,
        fetched_at=fetched_at,
    )

    constituent_sets = []
    for url, has_weight, content, frame in constituent_materials:
        code_column = "成份券代码Constituent Code"
        name_column = "成份券名称Constituent Name"
        date_column = "日期Date"
        weight_column = "权重(%)weight"
        source_dates = {
            value
            for value in (
                _optional_date(item)
                for item in frame.get(date_column, pd.Series(dtype=str))
            )
            if value is not None
        }
        source_date = next(iter(source_dates)) if len(source_dates) == 1 else None
        items = [
            {
                "stockCode": str(row.get(code_column) or "").strip().zfill(6),
                "stockName": str(row.get(name_column) or "").strip(),
                "weight": (
                    _optional_float(row.get(weight_column))
                    if has_weight
                    else None
                ),
            }
            for _, row in frame.iterrows()
        ]
        constituent_sets.append(build_constituent_set_evidence(
            provider="csindex",
            index_code=index_code,
            index_name=index_name,
            announced_at=None,
            effective_from=None,
            effective_to=None,
            temporal_basis=(
                EvidenceTemporalBasis.CURRENT_OFFICIAL_OBSERVATION
            ),
            source_date=source_date,
            expected_count=len(frame),
            as_of=observation_as_of,
            evidence_url=url,
            evidence_sha256=_sha256(content),
            first_observed_at=fetched_at,
            fetched_at=fetched_at,
            items=items,
        ))

    return OfficialIndexPocResult(
        provider="csindex",
        index_code=index_code,
        index_name=index_name,
        identity_evidence_url=basic_url,
        identity_evidence_sha256=_sha256(basic_content),
        methodology=methodology,
        constituent_sets=tuple(constituent_sets),
        fetched_at=fetched_at,
    )


def fetch_cnindex_index_poc(
    index_code: str,
    as_of: datetime,
    *,
    session=None,
    clock: Callable[[], datetime] = _now,
) -> OfficialIndexPocResult:
    active_session = session or _session()
    identity_content = _response_bytes(
        active_session,
        CNINDEX_IDENTITY_URL,
        params={"codeValue": index_code},
    )
    identity_payload = json.loads(identity_content.decode("utf-8"))
    identity = identity_payload.get("data")
    if identity_payload.get("code") != 200 or not identity:
        raise RuntimeError("国证指数基础信息响应无有效数据")
    returned_code = str(identity.get("indexcode") or "").strip()
    if returned_code != index_code:
        raise RuntimeError("国证指数基础信息代码不一致")
    index_name = str(identity.get("indexfullcname") or "").strip()
    if not index_name:
        raise RuntimeError("国证指数基础信息缺少名称")

    intro_content = _response_bytes(
        active_session,
        CNINDEX_INTRO_URL,
        params={"indexcode": index_code},
    )
    intro_payload = json.loads(intro_content.decode("utf-8"))
    intro = intro_payload.get("data")
    if intro_payload.get("code") != 200 or not intro:
        raise RuntimeError("国证指数编制简介响应无有效数据")

    path_content = _response_bytes(
        active_session,
        CNINDEX_METHODOLOGY_PATH_URL,
        params={"indexCode": index_code},
    )
    path_payload = json.loads(path_content.decode("utf-8"))
    if path_payload.get("code") != 200 or not path_payload.get("data"):
        raise RuntimeError("国证指数编制方案路径响应无有效数据")
    methodology_filename = str(path_payload["data"]).split("&", 1)[0]
    if methodology_filename != f"{index_code}.pdf":
        raise RuntimeError("国证指数编制方案文件身份不一致")
    methodology_url = CNINDEX_METHODOLOGY_URL.format(index_code=index_code)
    methodology_content = _response_bytes(active_session, methodology_url)
    methodology_text, metadata = _pdf_text(methodology_content)
    methodology_hash = _sha256(methodology_content)
    metadata_date = re.search(
        r"D:(\d{4})(\d{2})(\d{2})",
        str(metadata.get("/CreationDate") or ""),
    )
    internal_date = (
        "-".join(metadata_date.groups())
        if metadata_date is not None
        else "undated"
    )
    provider_version = (
        f"internal-{internal_date}-{methodology_hash[:16]}"
    )
    fetched_at = clock()
    methodology = build_methodology_evidence(
        provider="cnindex",
        index_code=index_code,
        index_name=index_name,
        provider_version=provider_version,
        version_kind=EvidenceVersionKind.INTERNAL_EVIDENCE,
        published_at=None,
        effective_from=None,
        effective_to=None,
        universe_rule=str(intro.get("xyfw") or "").strip() or None,
        selection_rule=str(intro.get("xygz") or "").strip() or None,
        weighting_method=str(intro.get("jsfs") or "").strip() or None,
        constituent_cap=_constituent_cap(
            str(intro.get("jsjj") or ""),
            methodology_text[:5000],
        ),
        rebalance_frequency=(
            "semiannual"
            if "每半年调整一次" in methodology_text
            else None
        ),
        as_of=as_of,
        evidence_url=methodology_url,
        evidence_sha256=methodology_hash,
        first_observed_at=fetched_at,
        fetched_at=fetched_at,
    )

    constituents_content = _response_bytes(
        active_session,
        CNINDEX_CONSTITUENTS_URL,
        params={
            "indexcode": index_code,
            "dateStr": as_of.strftime("%Y-%m"),
            "pageNum": 1,
            "rows": 500,
            "isFirstCall": 1,
        },
    )
    constituents_payload = json.loads(constituents_content.decode("utf-8"))
    data = constituents_payload.get("data") or {}
    rows = data.get("rows") or []
    expected_count = data.get("total")
    if expected_count is None:
        expected_count = constituents_payload.get("total")
    source_dates = {
        value
        for value in (
            _optional_date(row.get("dateStr"))
            for row in rows
        )
        if value is not None
    }
    source_date = next(iter(source_dates)) if len(source_dates) == 1 else None
    items = [
        {
            "stockCode": str(row.get("seccode") or "").strip().zfill(6),
            "stockName": str(row.get("secname") or "").strip(),
            "weight": _optional_float(row.get("weight")),
        }
        for row in rows
    ]
    constituents = build_constituent_set_evidence(
        provider="cnindex",
        index_code=index_code,
        index_name=index_name,
        announced_at=None,
        effective_from=None,
        effective_to=None,
        source_date=source_date,
        expected_count=int(expected_count) if expected_count is not None else None,
        as_of=as_of,
        evidence_url=CNINDEX_CONSTITUENTS_URL,
        evidence_sha256=_sha256(constituents_content),
        first_observed_at=fetched_at,
        fetched_at=fetched_at,
        items=items,
    )

    return OfficialIndexPocResult(
        provider="cnindex",
        index_code=index_code,
        index_name=index_name,
        identity_evidence_url=CNINDEX_IDENTITY_URL,
        identity_evidence_sha256=_sha256(identity_content),
        methodology=methodology,
        constituent_sets=(constituents,),
        fetched_at=fetched_at,
    )
