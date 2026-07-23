from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import re
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from pypdf import PdfReader

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationGap,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceIssue,
    SourceStatus,
    VerifiedSecurityAlias,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SOURCE_NAME = "capco_industry_classification"
SCHEME_VERSION = "capco-guideline-2023-shadow"
ALLOWED_CAPCO_HOSTS = frozenset({
    "capco.org.cn",
    "www.capco.org.cn",
    "sp.capco.org.cn",
})
ALLOWED_ALIAS_EVIDENCE_HOSTS = frozenset({
    "bse.cn",
    "www.bse.cn",
})


@dataclass(frozen=True)
class FetchedResource:
    final_url: str
    content: bytes


@dataclass(frozen=True)
class IndustryClassificationProviders:
    fetch_page: Callable[[str, float], FetchedResource]
    fetch_document: Callable[[str, float], FetchedResource]
    extract_layout_pages: Callable[[bytes], Sequence[str]]


@dataclass(frozen=True)
class _PageMetadata:
    title: str
    release_period: str
    published_date: date
    classification_start_date: date
    document_url: str


@dataclass(frozen=True)
class _RawIndustryRecord:
    source_symbol: str
    source_name: str
    category_code: str
    category_name: str
    manufacturing_subclass_code: Optional[str]
    manufacturing_subclass_name: Optional[str]
    division_code: str
    division_name: str


class _IndustrySourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class _ResultPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: List[str] = []
        self.links: List[Tuple[str, str]] = []
        self._active_href: Optional[str] = None
        self._active_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        self._active_href = attributes.get("href")
        self._active_text = []

    def handle_data(self, data):
        self.text_parts.append(data)
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._active_href is None:
            return
        self.links.append((self._active_href, "".join(self._active_text)))
        self._active_href = None
        self._active_text = []


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _default_layout_extractor(content: bytes) -> Sequence[str]:
    reader = PdfReader(BytesIO(content))
    return [
        page.extract_text(extraction_mode="layout") or ""
        for page in reader.pages
    ]


def _default_providers() -> IndustryClassificationProviders:
    session = requests.Session()
    session.trust_env = False

    def fetch(url: str, timeout_seconds: float) -> FetchedResource:
        response = session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return FetchedResource(
            final_url=str(response.url),
            content=response.content,
        )

    return IndustryClassificationProviders(
        fetch_page=fetch,
        fetch_document=fetch,
        extract_layout_pages=_default_layout_extractor,
    )


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")


def _validate_official_url(
    url: str,
    allowed_hosts=ALLOWED_CAPCO_HOSTS,
) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise _IndustrySourceError(
            "unapproved_source_domain",
            "来源地址端口无效",
        ) from exc
    host = (parsed.hostname or "").lower()
    allowed_ports = {None, 443, 82} if host == "sp.capco.org.cn" else {None, 443}
    if (
        parsed.scheme != "https"
        or host not in allowed_hosts
        or port not in allowed_ports
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _IndustrySourceError(
            "unapproved_source_domain",
            "来源地址不属于允许的官方域名",
        )


def _parse_result_page(content: bytes, base_url: str) -> _PageMetadata:
    try:
        html = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _IndustrySourceError(
            "invalid_publication_page",
            "官方结果页面不是有效UTF-8文本",
        ) from exc

    parser = _ResultPageParser()
    parser.feed(html)
    page_text = " ".join("".join(parser.text_parts).split())
    title_matches = {
        (match.group(0), match.group(1), match.group(2))
        for match in re.finditer(
            r"(\d{4})年(上|下)半年上市公司行业分类结果",
            page_text,
        )
    }
    if len(title_matches) != 1:
        raise _IndustrySourceError(
            "invalid_release_title",
            "官方结果页面缺少唯一发布期标题",
        )
    title, year_text, half_text = next(iter(title_matches))
    release_period = f"{year_text}H{'1' if half_text == '上' else '2'}"

    date_match = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", page_text)
    if date_match is None:
        raise _IndustrySourceError(
            "missing_published_date",
            "官方结果页面缺少发布日期",
        )
    try:
        published_date = date.fromisoformat(date_match.group(1))
    except ValueError as exc:
        raise _IndustrySourceError(
            "invalid_published_date",
            "官方结果页面发布日期无效",
        ) from exc

    document_links = []
    for href, anchor_text in parser.links:
        normalized_anchor = "".join(anchor_text.split())
        if "按股票代码排序" not in normalized_anchor:
            continue
        resolved = urljoin(base_url, href)
        if resolved.lower().split("?", 1)[0].endswith(".pdf"):
            document_links.append(resolved)
    unique_links = tuple(dict.fromkeys(document_links))
    if len(unique_links) != 1:
        raise _IndustrySourceError(
            "missing_code_sorted_document",
            "官方结果页面缺少唯一按股票代码排序PDF",
        )
    _validate_official_url(unique_links[0])

    result_year = int(year_text)
    classification_start_date = date(
        result_year,
        6 if half_text == "上" else 12,
        10 if half_text == "上" else 20,
    )
    return _PageMetadata(
        title=title,
        release_period=release_period,
        published_date=published_date,
        classification_start_date=classification_start_date,
        document_url=unique_links[0],
    )


def _parse_record_block(lines: Sequence[str]) -> _RawIndustryRecord:
    first_line = lines[0]
    prefix_match = re.match(
        r"^(\d{6})(.*?)\s+([A-T])\s+(.+)$",
        first_line,
    )
    if prefix_match is None:
        raise _IndustrySourceError(
            "missing_required_field",
            "行业记录缺少股票代码、简称或门类字段",
        )

    source_symbol, source_name, category_code, remainder = prefix_match.groups()
    remainder_start = prefix_match.start(4)
    division_matches = list(re.finditer(r"(?<!\d)(\d{2})(?!\d)", remainder))
    if not division_matches:
        raise _IndustrySourceError(
            "missing_required_field",
            f"行业记录{source_symbol}缺少大类代码或名称",
        )
    division_match = division_matches[-1]
    division_code = division_match.group(1)
    division_position = remainder_start + division_match.start()
    before_division = remainder[:division_match.start()]
    division_name = remainder[division_match.end():].strip()

    subclass_code = None
    subclass_name = None
    if category_code == "C":
        subclass_matches = list(re.finditer(
            r"(?<![A-Z])([A-Z]{2})(?![A-Z])",
            before_division,
        ))
        if not subclass_matches:
            raise _IndustrySourceError(
                "missing_required_field",
                f"制造业记录{source_symbol}缺少次类代码或名称",
            )
        subclass_match = subclass_matches[-1]
        subclass_code = subclass_match.group(1)
        subclass_position = remainder_start + subclass_match.start()
        category_name = before_division[:subclass_match.start()].strip()
        subclass_name = before_division[subclass_match.end():].strip()
    else:
        if re.search(r"(?<![A-Z])([A-Z]{2})(?![A-Z])", before_division):
            raise _IndustrySourceError(
                "unexpected_manufacturing_subclass",
                f"非制造业记录{source_symbol}出现制造业次类",
            )
        subclass_position = division_position
        category_name = before_division.strip()

    for continuation in lines[1:]:
        category_name += continuation[
            prefix_match.end(3):subclass_position
        ].strip()
        if category_code == "C":
            subclass_name = (subclass_name or "") + continuation[
                subclass_position:division_position
            ].strip()
        division_name += continuation[division_position:].strip()

    required_values = (
        source_symbol,
        source_name.strip(),
        category_code,
        category_name,
        division_code,
        division_name,
    )
    if any(not value for value in required_values):
        raise _IndustrySourceError(
            "missing_required_field",
            f"行业记录{source_symbol}存在必填字段缺失",
        )
    if category_code == "C" and (not subclass_code or not subclass_name):
        raise _IndustrySourceError(
            "missing_required_field",
            f"制造业记录{source_symbol}存在次类字段缺失",
        )
    return _RawIndustryRecord(
        source_symbol=source_symbol,
        source_name=source_name.strip(),
        category_code=category_code,
        category_name=category_name,
        manufacturing_subclass_code=subclass_code,
        manufacturing_subclass_name=subclass_name,
        division_code=division_code,
        division_name=division_name,
    )


def _parse_layout_pages(
    pages: Sequence[str],
    expected_title: str,
) -> List[_RawIndustryRecord]:
    if not pages or not any(page.strip() for page in pages):
        raise _IndustrySourceError(
            "empty_document",
            "行业分类PDF没有可解析页面",
        )
    normalized_title = "".join(expected_title.split())
    first_page_text = "".join(pages[0].split())
    if normalized_title not in first_page_text:
        raise _IndustrySourceError(
            "document_title_mismatch",
            "行业分类PDF标题与发布页面不一致",
        )

    raw_records: List[_RawIndustryRecord] = []
    for page in pages:
        blocks: List[List[str]] = []
        current: Optional[List[str]] = None
        for line in page.splitlines():
            if re.match(r"^\d{6}", line):
                if current:
                    blocks.append(current)
                current = [line]
            elif current is not None and line.strip():
                current.append(line)
        if current:
            blocks.append(current)
        raw_records.extend(_parse_record_block(block) for block in blocks)

    if not raw_records:
        raise _IndustrySourceError(
            "source_returned_no_rows",
            "行业分类PDF没有返回记录",
        )

    symbols = [record.source_symbol for record in raw_records]
    if len(set(symbols)) != len(symbols):
        raise _IndustrySourceError(
            "duplicate_source_symbol",
            "同一发布期存在重复股票代码",
        )

    for code_field, name_field in (
        ("category_code", "category_name"),
        ("manufacturing_subclass_code", "manufacturing_subclass_name"),
        ("division_code", "division_name"),
    ):
        names_by_code: Dict[str, str] = {}
        for record in raw_records:
            code = getattr(record, code_field)
            name = getattr(record, name_field)
            if code is None:
                continue
            previous = names_by_code.setdefault(code, name)
            if previous != name:
                raise _IndustrySourceError(
                    "classification_name_conflict",
                    "同一行业代码对应多个行业名称",
                )
    return raw_records


def _required_field_coverage(
    records: Sequence[_RawIndustryRecord],
) -> Dict[str, float]:
    if not records:
        return {}
    return {
        "source_symbol": 1.0,
        "source_name": 1.0,
        "category_code": 1.0,
        "category_name": 1.0,
        "division_code": 1.0,
        "division_name": 1.0,
        "manufacturing_subclass": 1.0,
    }


def _active_aliases(
    aliases: Sequence[VerifiedSecurityAlias],
    current_by_symbol: Mapping[str, SecurityMasterRecord],
    as_of: datetime,
) -> Dict[str, VerifiedSecurityAlias]:
    active: Dict[str, VerifiedSecurityAlias] = {}
    for alias in aliases:
        if alias.source_symbol in active:
            raise _IndustrySourceError(
                "duplicate_verified_alias",
                "同一来源代码存在多个有效别名映射",
            )
        _validate_official_url(
            alias.evidence_url,
            allowed_hosts=ALLOWED_ALIAS_EVIDENCE_HOSTS,
        )
        if alias.published_date > as_of.astimezone(SHANGHAI_TZ).date():
            continue
        if alias.effective_from > as_of:
            continue
        if alias.effective_to is not None and alias.effective_to <= as_of:
            continue
        if alias.security_identity not in current_by_symbol:
            raise _IndustrySourceError(
                "verified_alias_target_missing",
                "别名映射目标不在当前证券主档",
            )
        active[alias.source_symbol] = alias
    return active


def _failed_snapshot(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    fetched_at: datetime,
    current_security_master: Sequence[SecurityMasterRecord],
    issue: SourceIssue,
) -> IndustryClassificationSnapshot:
    current_by_symbol = {
        record.symbol: record
        for record in current_security_master
    }
    gaps = [
        IndustryClassificationGap(
            securityIdentity=record.symbol,
            symbol=record.symbol,
            name=record.name,
            listingDate=record.listing_date,
            issueCodes=("source_failed",),
        )
        for record in current_by_symbol.values()
    ]
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source=SOURCE_NAME,
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        expectedCount=None,
        returnedCount=0,
        rowCoverage=None,
        requiredFieldCoverage={},
        issues=[issue],
    )
    current_count = len(current_by_symbol)
    return IndustryClassificationSnapshot(
        meta=meta,
        status=SourceStatus.FAILED,
        release=None,
        records=[],
        currentMasterGaps=gaps,
        completeness=IndustryClassificationCompleteness(
            sourceRecordCount=0,
            uniqueSourceSymbolCount=0,
            currentMasterCount=current_count,
            mappedCount=0,
            unconfirmedCount=current_count,
            excludedSourceCount=0,
            mappingCoverage=0.0 if current_count else None,
            requiredFieldCoverage={},
            shadowUsable=False,
            formalUsable=False,
            reasons=(issue.code,),
        ),
        issues=[issue],
    )


def fetch_industry_classification(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    publication_page_url: str,
    current_security_master: Sequence[SecurityMasterRecord],
    providers: Optional[IndustryClassificationProviders] = None,
    verified_aliases: Sequence[VerifiedSecurityAlias] = (),
    known_document_hashes: Optional[Mapping[str, str]] = None,
    first_observed_at: Optional[datetime] = None,
    timeout_seconds: float = 15.0,
    clock: Callable[[], datetime] = _now,
) -> IndustryClassificationSnapshot:
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds必须在0到30秒之间")
    _require_aware(as_of, "asOf")
    fetched_at = clock()
    _require_aware(fetched_at, "fetchedAt")
    observed_at = first_observed_at or fetched_at
    _require_aware(observed_at, "firstObservedAt")

    try:
        _validate_official_url(publication_page_url)
        current_by_symbol: Dict[str, SecurityMasterRecord] = {}
        for record in current_security_master:
            if record.symbol in current_by_symbol:
                raise _IndustrySourceError(
                    "duplicate_current_master_symbol",
                    "当前证券主档存在重复代码",
                )
            current_by_symbol[record.symbol] = record

        active_providers = providers or _default_providers()
        page_resource = active_providers.fetch_page(
            publication_page_url,
            timeout_seconds,
        )
        _validate_official_url(page_resource.final_url)
        page_metadata = _parse_result_page(
            page_resource.content,
            page_resource.final_url,
        )
        if page_metadata.published_date > as_of.astimezone(SHANGHAI_TZ).date():
            raise _IndustrySourceError(
                "future_publication",
                "行业版本发布日期晚于本轮asOf",
            )

        document_resource = active_providers.fetch_document(
            page_metadata.document_url,
            timeout_seconds,
        )
        _validate_official_url(document_resource.final_url)
        if not document_resource.content.startswith(b"%PDF"):
            raise _IndustrySourceError(
                "invalid_document_type",
                "行业分类文档不是PDF",
            )
        document_sha256 = hashlib.sha256(document_resource.content).hexdigest()
        known_hash = (known_document_hashes or {}).get(page_metadata.release_period)
        if known_hash is not None and known_hash != document_sha256:
            raise _IndustrySourceError(
                "document_hash_changed",
                "同一行业发布期的PDF校验值发生变化",
            )

        layout_pages = active_providers.extract_layout_pages(
            document_resource.content
        )
        raw_records = _parse_layout_pages(layout_pages, page_metadata.title)
        field_coverage = _required_field_coverage(raw_records)
        aliases_by_source = _active_aliases(
            verified_aliases,
            current_by_symbol,
            as_of,
        )

        records: List[IndustryClassificationRecord] = []
        mapped_identities = set()
        excluded_source_count = 0
        for raw in raw_records:
            security_identity = None
            identity_status = IndustryIdentityStatus.UNRESOLVED
            record_status = IndustryRecordStatus.UNCONFIRMED
            issue_codes: Tuple[str, ...]
            if raw.source_symbol in current_by_symbol:
                security_identity = raw.source_symbol
                identity_status = IndustryIdentityStatus.EXACT
                record_status = IndustryRecordStatus.ACCEPTED
                issue_codes = ()
            elif raw.source_symbol in aliases_by_source:
                security_identity = aliases_by_source[
                    raw.source_symbol
                ].security_identity
                identity_status = IndustryIdentityStatus.VERIFIED_ALIAS
                record_status = IndustryRecordStatus.ACCEPTED
                issue_codes = ()
            elif raw.source_symbol.startswith(("200", "900")):
                issue_codes = ("b_share_excluded",)
            elif raw.source_symbol.startswith(("4", "8")):
                issue_codes = ("unverified_security_alias",)
            else:
                issue_codes = ("non_current_security",)

            if security_identity is not None:
                if security_identity in mapped_identities:
                    raise _IndustrySourceError(
                        "duplicate_security_identity",
                        "同一发布期多个来源代码映射到同一证券身份",
                    )
                mapped_identities.add(security_identity)
            else:
                excluded_source_count += 1

            records.append(IndustryClassificationRecord(
                releasePeriod=page_metadata.release_period,
                sourceSymbol=raw.source_symbol,
                sourceName=raw.source_name,
                securityIdentity=security_identity,
                identityStatus=identity_status,
                categoryCode=raw.category_code,
                categoryName=raw.category_name,
                divisionCode=raw.division_code,
                divisionName=raw.division_name,
                manufacturingSubclassCode=raw.manufacturing_subclass_code,
                manufacturingSubclassName=raw.manufacturing_subclass_name,
                middleClassCode=None,
                middleClassName=None,
                recordStatus=record_status,
                issueCodes=issue_codes,
                sourceFields={
                    "sourceSymbol": raw.source_symbol,
                    "sourceName": raw.source_name,
                    "categoryCode": raw.category_code,
                    "categoryName": raw.category_name,
                    "manufacturingSubclassCode": (
                        raw.manufacturing_subclass_code
                    ),
                    "manufacturingSubclassName": (
                        raw.manufacturing_subclass_name
                    ),
                    "divisionCode": raw.division_code,
                    "divisionName": raw.division_name,
                },
            ))

        current_master_gaps = []
        for record in current_by_symbol.values():
            if record.symbol in mapped_identities:
                continue
            if record.listing_date is None:
                issue_codes = ("listing_date_missing",)
            elif record.listing_date >= page_metadata.classification_start_date:
                issue_codes = ("listed_on_or_after_classification_start",)
            else:
                issue_codes = ("current_master_mapping_gap",)
            current_master_gaps.append(IndustryClassificationGap(
                securityIdentity=record.symbol,
                symbol=record.symbol,
                name=record.name,
                listingDate=record.listing_date,
                issueCodes=issue_codes,
            ))

        history_status = (
            IndustryHistoryStatus.FORWARD_OBSERVED
            if observed_at.astimezone(SHANGHAI_TZ).date()
            <= page_metadata.published_date + timedelta(days=1)
            else IndustryHistoryStatus.RETROSPECTIVE_UNVERIFIED
        )
        release = IndustryClassificationRelease(
            schemeVersion=SCHEME_VERSION,
            releasePeriod=page_metadata.release_period,
            sourcePageTitle=page_metadata.title,
            publicationPageUrl=page_resource.final_url,
            documentUrl=document_resource.final_url,
            documentSha256=document_sha256,
            publishedDate=page_metadata.published_date,
            firstObservedAt=observed_at,
            fetchedAt=fetched_at,
            knowledgeEffectiveFrom=observed_at,
            knowledgeEffectiveTo=None,
            classificationStartDate=page_metadata.classification_start_date,
            historyStatus=history_status,
            sourceRecordCount=len(raw_records),
            uniqueSourceSymbolCount=len({r.source_symbol for r in raw_records}),
            requiredFieldCoverage=field_coverage,
        )

        issues = []
        if current_master_gaps:
            issues.append(SourceIssue(
                code="current_master_mapping_incomplete",
                source=SOURCE_NAME,
                message=f"当前证券主档有{len(current_master_gaps)}只行业未确认",
                symbols=[gap.symbol for gap in current_master_gaps],
            ))
        if excluded_source_count:
            issues.append(SourceIssue(
                code="source_records_excluded",
                source=SOURCE_NAME,
                message=f"来源文件有{excluded_source_count}条记录未映射当前A股",
            ))

        current_count = len(current_by_symbol)
        mapped_count = len(mapped_identities)
        mapping_coverage = (
            mapped_count / current_count
            if current_count
            else None
        )
        reasons = []
        if current_master_gaps:
            reasons.append("current_master_mapping_incomplete")
        if excluded_source_count:
            reasons.append("source_records_excluded")
        reasons.append("formal_use_not_approved")
        completeness = IndustryClassificationCompleteness(
            sourceRecordCount=len(raw_records),
            uniqueSourceSymbolCount=len({r.source_symbol for r in raw_records}),
            currentMasterCount=current_count,
            mappedCount=mapped_count,
            unconfirmedCount=len(current_master_gaps),
            excludedSourceCount=excluded_source_count,
            mappingCoverage=mapping_coverage,
            requiredFieldCoverage=field_coverage,
            shadowUsable=True,
            formalUsable=False,
            reasons=tuple(reasons),
        )
        meta = RadarBatchMeta(
            radarRunId=radar_run_id,
            batchId=batch_id,
            source=SOURCE_NAME,
            asOf=as_of,
            sourceTime=None,
            fetchedAt=fetched_at,
            expectedCount=len(raw_records),
            returnedCount=len(raw_records),
            rowCoverage=1.0,
            requiredFieldCoverage=field_coverage,
            issues=issues,
        )
        status = (
            SourceStatus.DEGRADED
            if issues
            else SourceStatus.HEALTHY
        )
        return IndustryClassificationSnapshot(
            meta=meta,
            status=status,
            release=release,
            records=records,
            currentMasterGaps=current_master_gaps,
            completeness=completeness,
            issues=issues,
        )
    except _IndustrySourceError as exc:
        issue = SourceIssue(
            code=exc.code,
            source=SOURCE_NAME,
            message=exc.safe_message,
        )
    except Exception as exc:
        issue = SourceIssue(
            code="source_request_failed",
            source=SOURCE_NAME,
            message=f"中上协行业来源请求或解析失败：{type(exc).__name__}",
        )
    return _failed_snapshot(
        radar_run_id=radar_run_id,
        batch_id=batch_id,
        as_of=as_of,
        fetched_at=fetched_at,
        current_security_master=current_security_master,
        issue=issue,
    )
