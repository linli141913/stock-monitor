"""阶段6主营关系的六类巨潮官方催化元数据发现。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from html import unescape
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import requests

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.sources.leader_risk_official import (
    CNINFO_QUERY_URL,
    CNINFO_STATIC_BASE_URL,
)


CNINFO_BUSINESS_CATALYST_CONTRACT_ID = (
    "radar-leader-business-catalyst-cninfo-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
MAXIMUM_SELECTED_DOCUMENTS = 3
MAXIMUM_PAGES_PER_KIND = 20
MAXIMUM_TRANSIENT_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 20.0
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
TITLE_TAG_PATTERN = re.compile(r"<[^>]*>")


class OfficialBusinessCatalystKind(str, Enum):
    MAJOR_CONTRACT = "major_contract"
    PROJECT_AWARD = "project_award"
    CAPACITY_START = "capacity_start"
    PRODUCT_CERTIFICATION = "product_certification"
    PRIVATE_PLACEMENT_PROJECT = "private_placement_project"
    EARNINGS_FORECAST = "earnings_forecast"


KIND_SEARCH_KEYS = {
    OfficialBusinessCatalystKind.MAJOR_CONTRACT: "重大合同",
    OfficialBusinessCatalystKind.PROJECT_AWARD: "中标",
    OfficialBusinessCatalystKind.CAPACITY_START: "投产",
    OfficialBusinessCatalystKind.PRODUCT_CERTIFICATION: "认证",
    OfficialBusinessCatalystKind.PRIVATE_PLACEMENT_PROJECT: "定增项目",
    OfficialBusinessCatalystKind.EARNINGS_FORECAST: "业绩预告",
}
KIND_TITLE_PATTERNS = {
    OfficialBusinessCatalystKind.MAJOR_CONTRACT: re.compile(
        r"(?:签订|签署|续签).*(?:合同|协议)|(?:重大).*合同|合同.*公告"
    ),
    OfficialBusinessCatalystKind.PROJECT_AWARD: re.compile(r"(?:项目)?中标|收到.*中标"),
    OfficialBusinessCatalystKind.CAPACITY_START: re.compile(r"(?:生产线|项目).*(?:投产|建成)|投产.*公告"),
    OfficialBusinessCatalystKind.PRODUCT_CERTIFICATION: re.compile(r"产品.*(?:认证|注册证|许可)"),
    OfficialBusinessCatalystKind.PRIVATE_PLACEMENT_PROJECT: re.compile(r"(?:定增|向特定对象发行).*(?:项目|预案)"),
    OfficialBusinessCatalystKind.EARNINGS_FORECAST: re.compile(r"业绩预告"),
}
TITLE_EXCLUSION_PATTERN = re.compile(
    r"管理(?:制度|办法)|披露标准|核查意见|"
    r"(?:监管工作函|问询函|关注函).*(?:回复|意见)|"
    r"(?:会计师|律师|保荐机构).*业绩预告.*(?:回复|意见)"
)


@dataclass(frozen=True)
class OfficialBusinessCatalystQuery:
    candidate_plan_id: str
    symbol: str
    issuer_identity: str
    window_from: date
    window_until: date
    page_number: int = 1
    page_size: int = 30


@dataclass(frozen=True)
class OfficialBusinessCatalystDocument:
    document_id: str
    document_version: str
    symbol: str
    issuer_identity: str
    title: str
    published_at: datetime
    source_url: str
    event_kind: OfficialBusinessCatalystKind
    source_name: str = "巨潮资讯"
    source_contract_id: str = CNINFO_BUSINESS_CATALYST_CONTRACT_ID


@dataclass(frozen=True, repr=False)
class OfficialBusinessCatalystDiscoveryResult:
    status: AutomaticBusinessEvidenceStatus
    query: Optional[OfficialBusinessCatalystQuery] = None
    fetched_at: Optional[datetime] = None
    documents: Tuple[OfficialBusinessCatalystDocument, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    contract_id: str = CNINFO_BUSINESS_CATALYST_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


@dataclass(frozen=True)
class _ParsedCatalystPage:
    documents: Tuple[OfficialBusinessCatalystDocument, ...]
    raw_count: int
    total_announcement: int
    total_pages: int
    has_more: bool


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _query_valid(query: Any) -> bool:
    return bool(
        type(query) is OfficialBusinessCatalystQuery
        and SAFE_ID_PATTERN.fullmatch(query.candidate_plan_id)
        and len(query.symbol) == 6
        and query.symbol.isdigit()
        and SAFE_ID_PATTERN.fullmatch(query.issuer_identity)
        and query.issuer_identity.startswith("cninfo-org:")
        and isinstance(query.window_from, date)
        and not isinstance(query.window_from, datetime)
        and isinstance(query.window_until, date)
        and not isinstance(query.window_until, datetime)
        and query.window_from <= query.window_until
        and isinstance(query.page_number, int)
        and not isinstance(query.page_number, bool)
        and query.page_number == 1
        and isinstance(query.page_size, int)
        and not isinstance(query.page_size, bool)
        and 1 <= query.page_size <= 30
    )


def _result(
    status: AutomaticBusinessEvidenceStatus,
    *,
    query: Optional[OfficialBusinessCatalystQuery],
    fetched_at: Optional[datetime],
    documents: Sequence[OfficialBusinessCatalystDocument] = (),
    reasons: Sequence[str] = (),
) -> OfficialBusinessCatalystDiscoveryResult:
    return OfficialBusinessCatalystDiscoveryResult(
        status=status,
        query=query,
        fetched_at=fetched_at if _aware(fetched_at) else None,
        documents=tuple(documents),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _source_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed = urlsplit(raw)
    path = PurePosixPath(raw)
    if (
        parsed.scheme
        or parsed.netloc
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != "finalpage"
        or path.suffix.casefold() != ".pdf"
    ):
        return None
    result = f"{CNINFO_STATIC_BASE_URL}{path.as_posix()}"
    checked = urlsplit(result)
    return result if (
        checked.scheme == "https"
        and checked.hostname == "static.cninfo.com.cn"
        and checked.netloc == "static.cninfo.com.cn"
        and checked.username is None
        and checked.password is None
        and not checked.query
        and not checked.fragment
    ) else None


def _parse_payload(
    query: OfficialBusinessCatalystQuery,
    payload: Any,
    kind: OfficialBusinessCatalystKind,
    fetched_at: datetime,
) -> Tuple[Optional[_ParsedCatalystPage], Optional[str]]:
    announcements = (
        payload.get("announcements")
        if isinstance(payload, Mapping)
        else None
    )
    if (
        not isinstance(payload, Mapping)
        or not (
            isinstance(announcements, list)
            or announcements is None
        )
        or not isinstance(payload.get("totalAnnouncement"), int)
        or isinstance(payload.get("totalAnnouncement"), bool)
        or not isinstance(payload.get("totalpages"), int)
        or isinstance(payload.get("totalpages"), bool)
        or not isinstance(payload.get("hasMore"), bool)
        or payload["totalAnnouncement"] < 0
        or payload["totalpages"] < 0
    ):
        return None, "business_catalyst_page_coverage_unverified"
    if announcements is None:
        if (
            payload["totalAnnouncement"] != 0
            or payload["totalpages"] != 0
            or payload["hasMore"]
        ):
            return None, "business_catalyst_page_coverage_unverified"
        announcements = []
    if (
        (
            payload["totalAnnouncement"] == 0
            and (
                announcements
                or payload["totalpages"] != 0
                or payload["hasMore"]
            )
        )
        or (
            payload["totalAnnouncement"] > 0
            and (
                not announcements
                or payload["totalpages"] > MAXIMUM_PAGES_PER_KIND
                or len(announcements) > query.page_size
            )
        )
    ):
        return None, "business_catalyst_page_coverage_unverified"
    issuer_org = query.issuer_identity.removeprefix("cninfo-org:")
    documents = []
    seen = set()
    for row in announcements:
        if not isinstance(row, Mapping):
            return None, "business_catalyst_document_unverified"
        identifier = row.get("announcementId")
        title = row.get("announcementTitle")
        timestamp = row.get("announcementTime")
        source_url = _source_url(row.get("adjunctUrl"))
        if (
            row.get("secCode") != query.symbol
            or row.get("orgId") != issuer_org
            or not isinstance(identifier, str)
            or SAFE_ID_PATTERN.fullmatch(identifier) is None
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp <= 0
            or source_url is None
            or str(row.get("adjunctType", "")).casefold() not in {"pdf", ".pdf"}
        ):
            return None, "business_catalyst_document_unverified"
        published_at = datetime.fromtimestamp(timestamp / 1000, tz=fetched_at.tzinfo)
        if published_at > fetched_at + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS):
            return None, "business_catalyst_document_time_unverified"
        normalized_title = unescape(TITLE_TAG_PATTERN.sub("", title)).strip()
        if (
            TITLE_EXCLUSION_PATTERN.search(normalized_title) is not None
            or KIND_TITLE_PATTERNS[kind].search(normalized_title) is None
        ):
            continue
        document_id = f"cninfo:{identifier}"
        if document_id in seen:
            return None, "business_catalyst_document_duplicate"
        seen.add(document_id)
        documents.append(OfficialBusinessCatalystDocument(
            document_id=document_id,
            document_version=f"{document_id}:{timestamp}",
            symbol=query.symbol,
            issuer_identity=query.issuer_identity,
            title=normalized_title,
            published_at=published_at,
            source_url=source_url,
            event_kind=kind,
        ))
    return _ParsedCatalystPage(
        documents=tuple(documents),
        raw_count=len(announcements),
        total_announcement=payload["totalAnnouncement"],
        total_pages=payload["totalpages"],
        has_more=payload["hasMore"],
    ), None


def _default_transport(
    query: OfficialBusinessCatalystQuery,
    kind: OfficialBusinessCatalystKind,
) -> Mapping[str, Any]:
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            CNINFO_QUERY_URL,
            data={
                "pageNum": query.page_number,
                "pageSize": query.page_size,
                "tabName": "fulltext",
                "column": "szse",
                "stock": f"{query.symbol},{query.issuer_identity.removeprefix('cninfo-org:')}",
                "searchkey": KIND_SEARCH_KEYS[kind],
                "seDate": f"{query.window_from.isoformat()}~{query.window_until.isoformat()}",
                "sortName": "announcementId",
                "sortType": "desc",
                "isHLtitle": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()


def fetch_official_business_catalysts(
    query: Any,
    *,
    fetched_at: Optional[datetime] = None,
    kinds: Tuple[OfficialBusinessCatalystKind, ...] = tuple(OfficialBusinessCatalystKind),
    transport: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> OfficialBusinessCatalystDiscoveryResult:
    actual_fetched_at = fetched_at or datetime.now().astimezone()
    if (
        not _query_valid(query)
        or not _aware(actual_fetched_at)
        or not isinstance(kinds, tuple)
        or not kinds
        or len(kinds) != len(set(kinds))
        or any(type(kind) is not OfficialBusinessCatalystKind for kind in kinds)
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            query=query if type(query) is OfficialBusinessCatalystQuery else None,
            fetched_at=actual_fetched_at,
            reasons=("business_catalyst_query_unverified",),
        )
    requester = transport or _default_transport
    collected = []
    for kind in kinds:
        try:
            pages = []
            expected_total = None
            expected_pages = None
            page_number = 1
            while True:
                page_query = replace(query, page_number=page_number)
                payload = None
                for attempt in range(MAXIMUM_TRANSIENT_ATTEMPTS):
                    try:
                        payload = requester(page_query, kind)
                        break
                    except requests.RequestException:
                        if attempt + 1 == MAXIMUM_TRANSIENT_ATTEMPTS:
                            raise
                parsed, reason = _parse_payload(
                    page_query,
                    payload,
                    kind,
                    actual_fetched_at,
                )
                if reason is not None or parsed is None:
                    break
                if expected_total is None:
                    expected_total = parsed.total_announcement
                    expected_pages = parsed.total_pages
                elif (
                    parsed.total_announcement != expected_total
                    or parsed.total_pages != expected_pages
                ):
                    reason = "business_catalyst_page_coverage_unverified"
                    break
                pages.append(parsed)
                if sum(page.raw_count for page in pages) > expected_total:
                    reason = "business_catalyst_page_coverage_unverified"
                    break
                if not parsed.has_more:
                    break
                if page_number >= MAXIMUM_PAGES_PER_KIND:
                    reason = "business_catalyst_page_coverage_unverified"
                    break
                page_number += 1
            if (
                reason is None
                and (
                    expected_total is None
                    or expected_pages is None
                    or sum(page.raw_count for page in pages) != expected_total
                )
            ):
                reason = "business_catalyst_page_coverage_unverified"
        except requests.RequestException:
            return _result(
                AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
                query=query,
                fetched_at=actual_fetched_at,
                reasons=("business_catalyst_request_failed",),
            )
        except Exception:
            return _result(
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                query=query,
                fetched_at=actual_fetched_at,
                reasons=("business_catalyst_response_unverified",),
            )
        if reason is not None or parsed is None:
            return _result(
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                query=query,
                fetched_at=actual_fetched_at,
                reasons=(reason or "business_catalyst_response_unverified",),
            )
        kind_documents = tuple(
            document
            for page in pages
            for document in page.documents
        )
        if len({item.document_id for item in kind_documents}) != len(
            kind_documents
        ):
            return _result(
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                query=query,
                fetched_at=actual_fetched_at,
                reasons=("business_catalyst_document_duplicate",),
            )
        collected.extend(kind_documents)
    by_id = {}
    for document in collected:
        by_id.setdefault(document.document_id, document)
    documents = tuple(sorted(
        by_id.values(),
        key=lambda item: (item.published_at, item.document_id),
        reverse=True,
    )[:MAXIMUM_SELECTED_DOCUMENTS])
    return _result(
        AutomaticBusinessEvidenceStatus.READY if documents else AutomaticBusinessEvidenceStatus.MISSING,
        query=query,
        fetched_at=actual_fetched_at,
        documents=documents,
        reasons=() if documents else ("business_catalyst_missing",),
    )
