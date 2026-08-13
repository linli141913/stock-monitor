"""阶段6L-D2风险官方文档发现适配器。

本模块只发现和归一化官方文档元数据，不下载正文、不连接数据库，也不生成
阶段6L-D1正式风险事件、解除证据或覆盖证明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from html import unescape
from pathlib import PurePosixPath
import re
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from radar.leader_risk_invalidation_features import RiskCategory


CNINFO_QUERY_URL = (
    "https://www.cninfo.com.cn/new/hisAnnouncement/query"
)
CNINFO_ISSUER_SEARCH_URL = (
    "https://www.cninfo.com.cn/new/information/topSearch/query"
)
CNINFO_ISSUER_ROSTER_URL = (
    "https://www.cninfo.com.cn/new/data/szse_stock.json"
)
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"
CNINFO_SOURCE_CONTRACT_ID = (
    "radar-leader-risk-cninfo-discovery-v1"
)
CNINFO_ISSUER_SCOPE_CONTRACT_ID = (
    "radar-leader-risk-cninfo-issuer-scope-v1"
)
REQUEST_TIMEOUT_SECONDS = 20.0
MAXIMUM_CANDIDATE_SCOPE_COUNT = 30
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
SAFE_SOURCE_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,160}$"
)
SECURITY_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
STAGE6_SECURITY_CODE_PATTERN = re.compile(r"^[036][0-9]{5}$")
TITLE_TAG_PATTERN = re.compile(r"<[^>]*>")


class OfficialRiskSourceStatus(str, Enum):
    PARTIAL = "partial"
    SOURCE_UNVERIFIED = "source_unverified"
    SOURCE_FAILED = "source_failed"


class CninfoIssuerResolutionStatus(str, Enum):
    READY = "ready"
    SOURCE_UNVERIFIED = "source_unverified"
    SOURCE_FAILED = "source_failed"


@dataclass(frozen=True)
class CninfoRiskIssuerScope:
    symbol: str
    issuer_identity: str
    resolved_at: datetime
    source_contract_id: str = CNINFO_ISSUER_SCOPE_CONTRACT_ID


@dataclass(frozen=True)
class CninfoRiskDiscoveryQuery:
    search_key: str
    candidate_category: RiskCategory
    window_from: date
    window_until: date
    page_number: int = 1
    page_size: int = 30
    candidate_scopes: Tuple[CninfoRiskIssuerScope, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    candidate_plan_id: Optional[str] = None
    shard_index: Optional[int] = None
    shard_count: Optional[int] = None


@dataclass(frozen=True)
class CninfoIssuerResolutionResult:
    status: CninfoIssuerResolutionStatus
    symbol: str
    fetched_at: Optional[datetime]
    scope: Optional[CninfoRiskIssuerScope] = field(
        default=None,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CninfoIssuerRosterResolutionResult:
    status: CninfoIssuerResolutionStatus
    fetched_at: Optional[datetime]
    scopes: Tuple[CninfoRiskIssuerScope, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OfficialRiskDocumentMetadata:
    source_contract_id: str
    document_id: str
    symbol: str
    issuer_identity: str
    issuer_name: str
    title: str
    published_at: datetime
    source_name: str
    source_url: str
    candidate_category: RiskCategory
    raw_column_ids: Tuple[str, ...] = ()
    raw_announcement_types: Tuple[str, ...] = ()
    raw_page_column: Optional[str] = None
    association_reported: bool = False
    formal_usable: bool = False


@dataclass(frozen=True)
class OfficialRiskDiscoveryBatch:
    status: OfficialRiskSourceStatus
    query: CninfoRiskDiscoveryQuery
    fetched_at: Optional[datetime]
    total_records: Optional[int] = None
    total_pages: Optional[int] = None
    reported_total_pages: Optional[int] = None
    has_more: Optional[bool] = None
    documents: Tuple[OfficialRiskDocumentMetadata, ...] = ()
    coverage_complete: bool = False
    open_event_carry_forward_complete: bool = False
    correction_links_complete: bool = False
    formal_usable: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)


CninfoTransport = Callable[..., Mapping[str, Any]]


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_source_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(SAFE_SOURCE_ID_PATTERN.fullmatch(value.strip()))
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _clean_title(value: Any) -> Optional[str]:
    if not _required_text(value):
        return None
    cleaned = unescape(TITLE_TAG_PATTERN.sub("", value)).strip()
    return cleaned or None


def _split_raw_codes(value: Any) -> Optional[Tuple[str, ...]]:
    if value is None or value == "":
        return ()
    if not isinstance(value, str):
        return None
    values = tuple(
        part.strip()
        for part in value.split("||")
        if part.strip()
    )
    if any(not _safe_source_id(part) for part in values):
        return None
    return values


def _official_document_url(value: Any) -> Optional[str]:
    if not _required_text(value):
        return None
    raw_path = value.strip()
    parsed_input = urlsplit(raw_path)
    if parsed_input.scheme or parsed_input.netloc:
        return None
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != "finalpage"
        or path.suffix.lower() not in {".pdf", ".html"}
    ):
        return None
    source_url = f"{CNINFO_STATIC_BASE_URL}{path.as_posix()}"
    parsed_url = urlsplit(source_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "static.cninfo.com.cn"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        return None
    return source_url


def _official_document_type(value: Any, source_url: str) -> bool:
    suffix = PurePosixPath(urlsplit(source_url).path).suffix.casefold()
    if suffix == ".pdf":
        return (
            isinstance(value, str)
            and value.strip().casefold() in {"pdf", ".pdf"}
        )
    return suffix == ".html" and value is None


def _published_at(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        return None
    try:
        return datetime.fromtimestamp(
            value / 1000,
            tz=UTC,
        ).astimezone(SHANGHAI_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _query_reasons(
    query: CninfoRiskDiscoveryQuery,
) -> Tuple[str, ...]:
    if not isinstance(query, CninfoRiskDiscoveryQuery):
        return ("cninfo_query_contract_unverified",)

    reasons = []
    if not _required_text(query.search_key):
        reasons.append("cninfo_query_search_key_missing")
    if not isinstance(query.candidate_category, RiskCategory):
        reasons.append("cninfo_query_category_unverified")
    if (
        not isinstance(query.window_from, date)
        or isinstance(query.window_from, datetime)
        or not isinstance(query.window_until, date)
        or isinstance(query.window_until, datetime)
    ):
        reasons.append("cninfo_query_window_unverified")
    elif query.window_from > query.window_until:
        reasons.append("cninfo_query_window_invalid")
    if (
        not isinstance(query.page_number, int)
        or isinstance(query.page_number, bool)
        or query.page_number < 1
    ):
        reasons.append("cninfo_query_page_number_invalid")
    if (
        not isinstance(query.page_size, int)
        or isinstance(query.page_size, bool)
        or not 1 <= query.page_size <= 30
    ):
        reasons.append("cninfo_query_page_size_invalid")
    scopes = query.candidate_scopes
    if (
        not isinstance(scopes, tuple)
        or len(scopes) > MAXIMUM_CANDIDATE_SCOPE_COUNT
        or any(
            not isinstance(scope, CninfoRiskIssuerScope)
            or scope.source_contract_id
            != CNINFO_ISSUER_SCOPE_CONTRACT_ID
            or not isinstance(scope.symbol, str)
            or STAGE6_SECURITY_CODE_PATTERN.fullmatch(scope.symbol) is None
            or not isinstance(scope.issuer_identity, str)
            or not scope.issuer_identity.startswith("cninfo-org:")
            or not _safe_source_id(
                scope.issuer_identity.removeprefix("cninfo-org:")
            )
            or _aware_utc(scope.resolved_at) is None
            for scope in scopes
        )
        or len({scope.symbol for scope in scopes}) != len(scopes)
        or len({scope.issuer_identity for scope in scopes}) != len(scopes)
    ):
        reasons.append("cninfo_query_candidate_scope_unverified")
    shard_metadata = (
        query.candidate_plan_id,
        query.shard_index,
        query.shard_count,
    )
    if any(value is not None for value in shard_metadata) and (
        not _safe_source_id(query.candidate_plan_id)
        or not isinstance(query.shard_index, int)
        or isinstance(query.shard_index, bool)
        or query.shard_index < 0
        or not isinstance(query.shard_count, int)
        or isinstance(query.shard_count, bool)
        or query.shard_count < 1
        or query.shard_index >= query.shard_count
        or not scopes
    ):
        reasons.append("cninfo_query_candidate_shard_unverified")
    return _dedupe(reasons)


def _issuer_result(
    *,
    status: CninfoIssuerResolutionStatus,
    symbol: Any,
    fetched_at: Optional[datetime],
    reasons: Sequence[str],
    scope: Optional[CninfoRiskIssuerScope] = None,
) -> CninfoIssuerResolutionResult:
    return CninfoIssuerResolutionResult(
        status=status,
        symbol=(symbol if isinstance(symbol, str) else ""),
        fetched_at=fetched_at,
        scope=scope,
        reasons=_dedupe(reasons),
    )


def parse_cninfo_issuer_search_payload(
    symbol: Any,
    payload: Any,
    *,
    fetched_at: datetime,
) -> CninfoIssuerResolutionResult:
    fetched_at_utc = _aware_utc(fetched_at)
    if (
        not isinstance(symbol, str)
        or STAGE6_SECURITY_CODE_PATTERN.fullmatch(symbol) is None
        or fetched_at_utc is None
    ):
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_query_contract_unverified",),
        )
    rows = (
        payload
        if isinstance(payload, list)
        else payload.get("data")
        if isinstance(payload, Mapping)
        else None
    )
    if not isinstance(rows, list):
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_response_contract_unverified",),
        )
    identities = {
        row.get("orgId") or row.get("orgid")
        for row in rows
        if (
            isinstance(row, Mapping)
            and str(row.get("code", "")) == symbol
            and _safe_source_id(row.get("orgId") or row.get("orgid"))
        )
    }
    if len(identities) != 1:
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_identity_unverified",),
        )
    identity = next(iter(identities))
    assert isinstance(identity, str)
    return _issuer_result(
        status=CninfoIssuerResolutionStatus.READY,
        symbol=symbol,
        fetched_at=fetched_at_utc,
        reasons=(),
        scope=CninfoRiskIssuerScope(
            symbol=symbol,
            issuer_identity=f"cninfo-org:{identity.strip()}",
            resolved_at=fetched_at_utc,
        ),
    )


def _result(
    query: CninfoRiskDiscoveryQuery,
    status: OfficialRiskSourceStatus,
    fetched_at: Optional[datetime],
    reasons: Sequence[str],
    *,
    total_records: Optional[int] = None,
    total_pages: Optional[int] = None,
    reported_total_pages: Optional[int] = None,
    has_more: Optional[bool] = None,
    documents: Sequence[OfficialRiskDocumentMetadata] = (),
) -> OfficialRiskDiscoveryBatch:
    return OfficialRiskDiscoveryBatch(
        status=status,
        query=query,
        fetched_at=fetched_at,
        total_records=total_records,
        total_pages=total_pages,
        reported_total_pages=reported_total_pages,
        has_more=has_more,
        documents=tuple(documents),
        reasons=_dedupe(reasons),
    )


def _parse_document(
    row: Mapping[str, Any],
    query: CninfoRiskDiscoveryQuery,
    fetched_at_utc: datetime,
) -> Tuple[
    Optional[OfficialRiskDocumentMetadata],
    Tuple[str, ...],
]:
    reasons = []
    symbol = row.get("secCode")
    issuer_name = row.get("secName")
    org_id = row.get("orgId")
    announcement_id = row.get("announcementId")
    if (
        not isinstance(symbol, str)
        or not SECURITY_CODE_PATTERN.fullmatch(symbol.strip())
        or not _required_text(issuer_name)
        or not _safe_source_id(org_id)
        or not _safe_source_id(announcement_id)
    ):
        reasons.append("cninfo_document_identity_unverified")
    elif query.candidate_scopes and (
        symbol.strip(),
        f"cninfo-org:{org_id.strip()}",
    ) not in {
        (scope.symbol, scope.issuer_identity)
        for scope in query.candidate_scopes
    }:
        reasons.append("cninfo_document_outside_candidate_scope")

    title = _clean_title(row.get("announcementTitle"))
    if title is None:
        reasons.append("cninfo_document_title_unverified")

    published_at = _published_at(row.get("announcementTime"))
    if published_at is None:
        reasons.append("cninfo_document_time_unverified")
    elif published_at.astimezone(UTC) > fetched_at_utc:
        reasons.append("cninfo_document_published_in_future")
    elif not (
        query.window_from
        <= published_at.date()
        <= query.window_until
    ):
        reasons.append(
            "cninfo_document_outside_query_window"
        )

    source_url = _official_document_url(row.get("adjunctUrl"))
    if (
        source_url is None
        or not _official_document_type(row.get("adjunctType"), source_url)
    ):
        reasons.append("cninfo_document_url_unverified")
    elif (
        _safe_source_id(announcement_id)
        and PurePosixPath(urlsplit(source_url).path).stem
        != announcement_id.strip()
    ):
        reasons.append(
            "cninfo_document_url_identity_mismatch"
        )

    raw_column_ids = _split_raw_codes(row.get("columnId"))
    raw_announcement_types = _split_raw_codes(
        row.get("announcementType")
    )
    raw_page_column = row.get("pageColumn")
    if (
        raw_column_ids is None
        or raw_announcement_types is None
        or (
            raw_page_column is not None
            and not _safe_source_id(raw_page_column)
        )
    ):
        reasons.append(
            "cninfo_document_classification_unverified"
        )

    association_value = row.get("associateAnnouncement")
    if association_value is None or association_value == "":
        association_reported = False
    elif isinstance(association_value, (str, int)):
        association_reported = True
    else:
        association_reported = False
        reasons.append(
            "cninfo_document_association_unverified"
        )

    if reasons:
        return None, _dedupe(reasons)

    assert isinstance(symbol, str)
    assert isinstance(issuer_name, str)
    assert isinstance(org_id, str)
    assert isinstance(announcement_id, str)
    assert title is not None
    assert published_at is not None
    assert source_url is not None
    assert raw_column_ids is not None
    assert raw_announcement_types is not None
    return (
        OfficialRiskDocumentMetadata(
            source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
            document_id=f"cninfo:{announcement_id.strip()}",
            symbol=symbol.strip(),
            issuer_identity=f"cninfo-org:{org_id.strip()}",
            issuer_name=issuer_name.strip(),
            title=title,
            published_at=published_at,
            source_name="巨潮资讯",
            source_url=source_url,
            candidate_category=query.candidate_category,
            raw_column_ids=raw_column_ids,
            raw_announcement_types=raw_announcement_types,
            raw_page_column=(
                raw_page_column.strip()
                if isinstance(raw_page_column, str)
                else None
            ),
            association_reported=association_reported,
        ),
        (),
    )


def parse_cninfo_risk_discovery_payload(
    query: CninfoRiskDiscoveryQuery,
    payload: Any,
    *,
    fetched_at: datetime,
) -> OfficialRiskDiscoveryBatch:
    """把巨潮查询响应归一化为研究性文档发现批次。"""

    query_reasons = _query_reasons(query)
    fetched_at_utc = _aware_utc(fetched_at)
    if query_reasons or fetched_at_utc is None:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            (
                *query_reasons,
                *(
                    ("cninfo_fetched_at_unverified",)
                    if fetched_at_utc is None
                    else ()
                ),
            ),
        )
    if not isinstance(payload, Mapping):
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_contract_unverified",),
        )

    total_records = payload.get("totalRecordNum")
    reported_total_pages = payload.get("totalpages")
    has_more = payload.get("hasMore")
    rows = payload.get("announcements")
    if (
        total_records == 0
        and reported_total_pages == 0
        and has_more is False
        and rows is None
    ):
        rows = []
    if (
        not isinstance(total_records, int)
        or isinstance(total_records, bool)
        or total_records < 0
        or not isinstance(reported_total_pages, int)
        or isinstance(reported_total_pages, bool)
        or reported_total_pages < 0
        or not isinstance(rows, list)
    ):
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_contract_unverified",),
        )
    if not isinstance(has_more, bool):
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_pagination_unverified",),
            total_records=total_records,
            total_pages=reported_total_pages,
            reported_total_pages=reported_total_pages,
        )

    expected_pages = (
        (total_records + query.page_size - 1) // query.page_size
        if total_records
        else 0
    )
    vendor_floor_pages = (
        total_records // query.page_size if total_records else 0
    )
    total_pages_recognized = reported_total_pages in {
        expected_pages,
        vendor_floor_pages,
    }
    total_pages = (
        expected_pages
        if total_pages_recognized
        else reported_total_pages
    )
    expected_has_more = (
        query.page_number * query.page_size < total_records
    )
    page_start = (
        query.page_number - 1
    ) * query.page_size
    page_out_of_range = (
        total_records > 0
        and page_start >= total_records
    )
    expected_page_records = (
        min(query.page_size, total_records - page_start)
        if total_records > page_start
        else 0
    )
    page_records_inconsistent = (
        len(rows) != expected_page_records
    )
    pagination_inconsistent = (
        not total_pages_recognized
        or has_more != expected_has_more
    )
    pagination_invalid = (
        len(rows) > query.page_size
        or len(rows) > total_records
        or (
            query.page_number == 1
            and total_records > 0
            and not rows
        )
    )
    if pagination_invalid:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_pagination_unverified",),
            total_records=total_records,
            total_pages=total_pages,
            reported_total_pages=reported_total_pages,
            has_more=has_more,
        )
    if page_out_of_range:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_page_out_of_range",),
            total_records=total_records,
            total_pages=total_pages,
            reported_total_pages=reported_total_pages,
            has_more=has_more,
        )
    if expected_page_records > 0 and not rows:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_page_records_unverified",),
            total_records=total_records,
            total_pages=total_pages,
            reported_total_pages=reported_total_pages,
            has_more=has_more,
        )

    documents = []
    reasons = []
    association_reported = False
    for row in rows:
        if not isinstance(row, Mapping):
            reasons.append("cninfo_document_contract_unverified")
            continue
        document, document_reasons = _parse_document(
            row,
            query,
            fetched_at_utc,
        )
        reasons.extend(document_reasons)
        if document is not None:
            documents.append(document)
            association_reported = (
                association_reported
                or document.association_reported
            )

    document_ids = [
        document.document_id for document in documents
    ]
    if len(document_ids) != len(set(document_ids)):
        reasons.append("cninfo_document_identity_duplicate")
    source_urls = [
        document.source_url for document in documents
    ]
    if len(source_urls) != len(set(source_urls)):
        reasons.append("cninfo_document_url_duplicate")

    base_reasons = [
        "cninfo_keyword_discovery_not_coverage_proof",
        "cninfo_open_event_carry_forward_unverified",
        "cninfo_correction_relation_unverified",
    ]
    if total_records == 0:
        base_reasons.append("cninfo_discovery_empty")
    if association_reported:
        base_reasons.append(
            "cninfo_association_semantics_unverified"
        )
    if (
        total_pages_recognized
        and reported_total_pages != expected_pages
    ):
        base_reasons.append(
            "cninfo_response_total_pages_normalized"
        )
    if pagination_inconsistent:
        base_reasons.append(
            "cninfo_response_pagination_inconsistent"
        )
    if page_records_inconsistent:
        base_reasons.append(
            "cninfo_response_page_records_inconsistent"
        )
    status = (
        OfficialRiskSourceStatus.SOURCE_UNVERIFIED
        if reasons
        else OfficialRiskSourceStatus.PARTIAL
    )
    return _result(
        query,
        status,
        fetched_at_utc,
        (*base_reasons, *reasons),
        total_records=total_records,
        total_pages=total_pages,
        reported_total_pages=reported_total_pages,
        has_more=has_more,
        documents=documents,
    )


def _default_transport(
    url: str,
    *,
    data: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("巨潮响应不是对象")
    return payload


def _default_issuer_transport(
    url: str,
    *,
    data: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> Any:
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


def _default_issuer_roster_transport(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
) -> Any:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            url,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


def fetch_cninfo_issuer_scopes_from_roster(
    symbols: Any,
    *,
    fetched_at: Optional[datetime] = None,
    transport: Optional[Callable[..., Any]] = None,
) -> CninfoIssuerRosterResolutionResult:
    """从巨潮官方全量名册一次解析候选全集发行人身份。"""

    actual_fetched_at = fetched_at or datetime.now(UTC)
    fetched_at_utc = _aware_utc(actual_fetched_at)
    if (
        not isinstance(symbols, tuple)
        or not symbols
        or len(symbols) != len(set(symbols))
        or any(
            not isinstance(symbol, str)
            or STAGE6_SECURITY_CODE_PATTERN.fullmatch(symbol) is None
            for symbol in symbols
        )
        or fetched_at_utc is None
    ):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_query_contract_unverified",),
        )
    request = transport or _default_issuer_roster_transport
    try:
        payload = request(
            CNINFO_ISSUER_ROSTER_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_FAILED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_source_request_failed",),
        )
    except (TypeError, ValueError):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_response_contract_unverified",),
        )
    if not isinstance(payload, Mapping):
        rows = None
    else:
        rows = payload.get("stockList")
    if not isinstance(rows, list):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_response_contract_unverified",),
        )
    requested = set(symbols)
    matches = {symbol: [] for symbol in symbols}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = row.get("code")
        if code not in requested or row.get("category") != "A股":
            continue
        org_id = row.get("orgId")
        if not _safe_source_id(org_id):
            return CninfoIssuerRosterResolutionResult(
                status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
                fetched_at=fetched_at_utc,
                reasons=("cninfo_issuer_roster_identity_unverified",),
            )
        matches[code].append(str(org_id).strip())
    if any(
        len(identities) != 1
        for identities in matches.values()
    ):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_coverage_unverified",),
        )
    identities = tuple(matches[symbol][0] for symbol in symbols)
    if len(identities) != len(set(identities)):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_roster_identity_duplicate",),
        )
    return CninfoIssuerRosterResolutionResult(
        status=CninfoIssuerResolutionStatus.READY,
        fetched_at=fetched_at_utc,
        scopes=tuple(
            CninfoRiskIssuerScope(
                symbol=symbol,
                issuer_identity=f"cninfo-org:{matches[symbol][0]}",
                resolved_at=fetched_at_utc,
            )
            for symbol in symbols
        ),
    )


def fetch_cninfo_issuer_scope(
    symbol: Any,
    *,
    fetched_at: Optional[datetime] = None,
    transport: Optional[Callable[..., Any]] = None,
) -> CninfoIssuerResolutionResult:
    """只读解析证券代码对应的巨潮发行人身份。"""

    actual_fetched_at = fetched_at or datetime.now(UTC)
    fetched_at_utc = _aware_utc(actual_fetched_at)
    if (
        not isinstance(symbol, str)
        or STAGE6_SECURITY_CODE_PATTERN.fullmatch(symbol) is None
        or fetched_at_utc is None
    ):
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_query_contract_unverified",),
        )
    request = transport or _default_issuer_transport
    try:
        payload = request(
            CNINFO_ISSUER_SEARCH_URL,
            data={"keyWord": symbol, "maxNum": "10"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_FAILED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_source_request_failed",),
        )
    except (TypeError, ValueError):
        return _issuer_result(
            status=CninfoIssuerResolutionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            fetched_at=fetched_at_utc,
            reasons=("cninfo_issuer_response_contract_unverified",),
        )
    return parse_cninfo_issuer_search_payload(
        symbol,
        payload,
        fetched_at=actual_fetched_at,
    )


def fetch_cninfo_risk_discovery(
    query: CninfoRiskDiscoveryQuery,
    *,
    fetched_at: Optional[datetime] = None,
    transport: Optional[CninfoTransport] = None,
) -> OfficialRiskDiscoveryBatch:
    """执行一次有界巨潮元数据查询；不会下载公告正文。"""

    actual_fetched_at = fetched_at or datetime.now(UTC)
    fetched_at_utc = _aware_utc(actual_fetched_at)
    query_reasons = _query_reasons(query)
    if query_reasons or fetched_at_utc is None:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            (
                *query_reasons,
                *(
                    ("cninfo_fetched_at_unverified",)
                    if fetched_at_utc is None
                    else ()
                ),
            ),
        )

    if any(
        _aware_utc(scope.resolved_at) > fetched_at_utc
        for scope in query.candidate_scopes
    ):
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_query_candidate_scope_time_unverified",),
        )

    data = {
        "pageNum": str(query.page_number),
        "pageSize": str(query.page_size),
        "tabName": "fulltext",
        "column": "szse",
        "stock": ";".join(
            f"{scope.symbol},"
            f"{scope.issuer_identity.removeprefix('cninfo-org:')}"
            for scope in query.candidate_scopes
        ),
        "searchkey": query.search_key.strip(),
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": (
            f"{query.window_from.isoformat()}"
            f"~{query.window_until.isoformat()}"
        ),
        "sortName": "announcementId",
        "sortType": "desc",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": (
            "https://www.cninfo.com.cn/new/commonUrl"
            "?url=disclosure/list/notice"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    request = transport or _default_transport
    try:
        payload = request(
            CNINFO_QUERY_URL,
            data=data,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_FAILED,
            fetched_at_utc,
            ("cninfo_source_request_failed",),
        )
    except (TypeError, ValueError):
        return _result(
            query,
            OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            fetched_at_utc,
            ("cninfo_response_contract_unverified",),
        )

    return parse_cninfo_risk_discovery_payload(
        query,
        payload,
        fetched_at=actual_fetched_at,
    )
