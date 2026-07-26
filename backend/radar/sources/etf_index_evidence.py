import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from pypdf import PdfReader

from radar.contracts import (
    EtfIndexIdentityEvidence,
    EtfManagementStyle,
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

EFUNDS_PRODUCT_URL = "https://www.efunds.com.cn/fund/{symbol}.shtml"
CSINDEX_BASIC_URL = (
    "https://www.csindex.com.cn/csindex-home/"
    "indexInfo/index-basic-info/{index_code}"
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
class OfficialIndexPocResult:
    provider: str
    index_code: str
    index_name: str
    identity_evidence_url: str
    identity_evidence_sha256: str
    methodology: IndexMethodologyEvidence
    constituent_sets: Tuple[IndexConstituentSetEvidence, ...]
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


def _normalize_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", "", normalized).strip()
    return normalized or None


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


def _section(text: str, start_number: int, end_number: int) -> Optional[str]:
    pattern = re.compile(
        rf"{start_number}\s*[、.．]\s*(.*?)"
        rf"(?={end_number}\s*[、.．])",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value[:4000] or None


def _constituent_cap(*values: Optional[str]) -> Optional[int]:
    for value in values:
        if not value:
            continue
        match = re.search(r"(\d{1,4})\s*只", value)
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
    ):
        status = IndexEvidenceStatus.CONFLICT
        reasons.append("index_identity_conflict")
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
) -> IndexMethodologyEvidence:
    reasons = []
    if provider_version is None:
        reasons.append("methodology_version_missing")
    if published_at is None:
        reasons.append("methodology_published_at_missing")
    if effective_from is None:
        reasons.append("methodology_effective_from_missing")
    elif effective_from > as_of:
        reasons.append("methodology_future_effective")
    if effective_to is not None and effective_to <= as_of:
        reasons.append("methodology_historical_expired")
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
    if announced_at is None:
        reasons.append("constituent_announced_at_missing")
    if effective_from is None:
        reasons.append("constituent_effective_from_missing")
    elif effective_from > as_of:
        reasons.append("constituent_future_effective")
    if effective_to is not None and effective_to <= as_of:
        reasons.append("constituent_historical_expired")

    formal_ready = not reasons
    return IndexConstituentSetEvidence(
        indexProvider=provider,
        indexCode=index_code,
        indexName=index_name,
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


def _read_excel(content: bytes) -> pd.DataFrame:
    return pd.read_excel(BytesIO(content), dtype=str)


def _response_bytes(session, url: str, **kwargs) -> bytes:
    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        **kwargs,
    )
    response.raise_for_status()
    return response.content


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

    methodology_url = CSINDEX_METHODOLOGY_URL.format(index_code=index_code)
    methodology_content = _response_bytes(active_session, methodology_url)
    methodology_text, _ = _pdf_text(methodology_content)
    version_match = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月",
        methodology_text[:3000],
    )
    provider_version = (
        f"{version_match.group(1)}-{int(version_match.group(2)):02d}"
        if version_match is not None
        else None
    )
    constituent_cap = _constituent_cap(
        str(basic.get("indexCnDesc") or ""),
        methodology_text[:6000],
    )
    fetched_at = clock()
    methodology = build_methodology_evidence(
        provider="csindex",
        index_code=index_code,
        index_name=index_name,
        provider_version=provider_version,
        version_kind=EvidenceVersionKind.OFFICIAL,
        published_at=None,
        effective_from=None,
        effective_to=None,
        universe_rule=_section(methodology_text, 2, 3),
        selection_rule=_section(methodology_text, 3, 4),
        weighting_method=_section(methodology_text, 4, 5),
        constituent_cap=constituent_cap,
        rebalance_frequency=str(basic.get("adjFreqCn") or "").strip() or None,
        as_of=as_of,
        evidence_url=methodology_url,
        evidence_sha256=_sha256(methodology_content),
        first_observed_at=fetched_at,
        fetched_at=fetched_at,
    )

    constituent_sets = []
    for url_template, has_weight in (
        (CSINDEX_CONSTITUENTS_URL, False),
        (CSINDEX_CLOSE_WEIGHT_URL, True),
    ):
        url = url_template.format(index_code=index_code)
        content = _response_bytes(active_session, url)
        frame = _read_excel(content)
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
            source_date=source_date,
            expected_count=len(frame),
            as_of=as_of,
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
