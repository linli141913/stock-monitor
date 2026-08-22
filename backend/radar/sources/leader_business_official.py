"""阶段6候选主营材料的巨潮官方元数据发现合同。

只发现官方文档，不解析正文、不推断主营关系、不生成正式状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from html import unescape
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from radar.sources.leader_risk_official import (
    CNINFO_QUERY_URL,
    CNINFO_STATIC_BASE_URL,
    CninfoRiskIssuerScope,
)


CNINFO_BUSINESS_MATERIAL_CONTRACT_ID = (
    "radar-leader-business-cninfo-discovery-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 20.0
MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS = 2
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TITLE_TAG_PATTERN = re.compile(r"<[^>]*>")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class OfficialBusinessMaterialDiscoveryStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class CninfoBusinessMaterialQuery:
    candidate_plan_id: str
    scope: CninfoRiskIssuerScope
    window_from: date
    window_until: date
    page_number: int = 1
    page_size: int = 30
    search_key: str = "年度报告"


@dataclass(frozen=True)
class OfficialBusinessMaterialDocument:
    document_id: str
    document_version: str
    symbol: str
    issuer_identity: str
    title: str
    published_at: datetime
    source_url: str
    source_name: str = "巨潮资讯"
    source_contract_id: str = CNINFO_BUSINESS_MATERIAL_CONTRACT_ID


@dataclass(frozen=True)
class OfficialBusinessMaterialDiscoveryBatch:
    status: OfficialBusinessMaterialDiscoveryStatus
    query: CninfoBusinessMaterialQuery
    fetched_at: Optional[datetime]
    documents: Tuple[OfficialBusinessMaterialDocument, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    coverage_complete: bool = False
    reasons: Tuple[str, ...] = ()
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": CNINFO_BUSINESS_MATERIAL_CONTRACT_ID,
            "status": self.status.value,
            "candidatePlanId": self.query.candidate_plan_id,
            "symbol": self.query.scope.symbol,
            "fetchedAt": (
                self.fetched_at.isoformat()
                if isinstance(self.fetched_at, datetime)
                else None
            ),
            "documentCount": len(self.documents),
            "coverageComplete": self.coverage_complete,
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _query_valid(query: Any) -> bool:
    return bool(
        type(query) is CninfoBusinessMaterialQuery
        and isinstance(query.candidate_plan_id, str)
        and bool(SAFE_ID_PATTERN.fullmatch(query.candidate_plan_id))
        and type(query.scope) is CninfoRiskIssuerScope
        and query.scope.symbol.isdigit()
        and len(query.scope.symbol) == 6
        and query.scope.issuer_identity.startswith("cninfo-org:")
        and _aware(query.scope.resolved_at)
        and isinstance(query.window_from, date)
        and not isinstance(query.window_from, datetime)
        and isinstance(query.window_until, date)
        and not isinstance(query.window_until, datetime)
        and query.window_from <= query.window_until
        and isinstance(query.page_number, int)
        and not isinstance(query.page_number, bool)
        and query.page_number >= 1
        and isinstance(query.page_size, int)
        and not isinstance(query.page_size, bool)
        and 1 <= query.page_size <= 30
        and isinstance(query.search_key, str)
        and bool(query.search_key.strip())
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
    if (
        checked.scheme != "https"
        or checked.hostname != "static.cninfo.com.cn"
        or checked.username is not None
        or checked.password is not None
        or checked.query
        or checked.fragment
    ):
        return None
    return result


def _result(
    query: CninfoBusinessMaterialQuery,
    status: OfficialBusinessMaterialDiscoveryStatus,
    *,
    fetched_at: Optional[datetime],
    documents: Sequence[OfficialBusinessMaterialDocument] = (),
    coverage_complete: bool = False,
    reasons: Sequence[str] = (),
) -> OfficialBusinessMaterialDiscoveryBatch:
    return OfficialBusinessMaterialDiscoveryBatch(
        status=status,
        query=query,
        fetched_at=fetched_at,
        documents=tuple(documents),
        coverage_complete=coverage_complete,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def parse_cninfo_business_material_payload(
    query: Any,
    payload: Any,
    *,
    fetched_at: Any,
) -> OfficialBusinessMaterialDiscoveryBatch:
    if not _query_valid(query) or not _aware(fetched_at):
        fallback = query if type(query) is CninfoBusinessMaterialQuery else (
            CninfoBusinessMaterialQuery(
                candidate_plan_id="invalid",
                scope=CninfoRiskIssuerScope(
                    symbol="000000",
                    issuer_identity="cninfo-org:invalid",
                    resolved_at=datetime.now(SHANGHAI_TZ),
                ),
                window_from=date.min,
                window_until=date.min,
            )
        )
        return _result(
            fallback,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
            fetched_at=None,
            reasons=("business_material_query_unverified",),
        )
    assert isinstance(query, CninfoBusinessMaterialQuery)
    if (
        not isinstance(payload, Mapping)
        or not isinstance(payload.get("announcements"), list)
        or not isinstance(payload.get("totalAnnouncement"), int)
        or isinstance(payload.get("totalAnnouncement"), bool)
        or not isinstance(payload.get("totalpages"), int)
        or isinstance(payload.get("totalpages"), bool)
        or not isinstance(payload.get("hasMore"), bool)
        or payload["totalpages"] < 0
        or payload["totalAnnouncement"] < 0
        or payload["hasMore"]
        or payload["totalpages"] > query.page_number
    ):
        return _result(
            query,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at,
            reasons=("business_material_page_coverage_unverified",),
        )
    issuer_org = query.scope.issuer_identity.removeprefix("cninfo-org:")
    documents = []
    seen = set()
    for row in payload["announcements"]:
        if not isinstance(row, Mapping):
            return _result(
                query,
                OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                fetched_at=fetched_at,
                reasons=("business_material_document_unverified",),
            )
        announcement_id = row.get("announcementId")
        title = row.get("announcementTitle")
        timestamp = row.get("announcementTime")
        source_url = _source_url(row.get("adjunctUrl"))
        if (
            row.get("secCode") != query.scope.symbol
            or row.get("orgId") != issuer_org
            or not isinstance(announcement_id, str)
            or not SAFE_ID_PATTERN.fullmatch(announcement_id)
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp <= 0
            or source_url is None
            or str(row.get("adjunctType", "")).casefold() not in {
                "pdf", ".pdf"
            }
        ):
            return _result(
                query,
                OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                fetched_at=fetched_at,
                reasons=("business_material_document_unverified",),
            )
        published_at = datetime.fromtimestamp(
            timestamp / 1000,
            tz=SHANGHAI_TZ,
        )
        if published_at > fetched_at + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        ):
            return _result(
                query,
                OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                fetched_at=fetched_at,
                reasons=("business_material_document_time_unverified",),
            )
        document_id = f"cninfo:{announcement_id}"
        if document_id in seen:
            return _result(
                query,
                OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                fetched_at=fetched_at,
                reasons=("business_material_document_duplicate",),
            )
        seen.add(document_id)
        documents.append(OfficialBusinessMaterialDocument(
            document_id=document_id,
            document_version=f"{document_id}:{timestamp}",
            symbol=query.scope.symbol,
            issuer_identity=query.scope.issuer_identity,
            title=unescape(TITLE_TAG_PATTERN.sub("", title)).strip(),
            published_at=published_at,
            source_url=source_url,
        ))
    if len(documents) != payload["totalAnnouncement"]:
        return _result(
            query,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
            fetched_at=fetched_at,
            reasons=("business_material_record_count_unverified",),
        )
    return _result(
        query,
        (
            OfficialBusinessMaterialDiscoveryStatus.READY
            if documents
            else OfficialBusinessMaterialDiscoveryStatus.MISSING
        ),
        fetched_at=fetched_at,
        documents=documents,
        coverage_complete=True,
    )


def fetch_cninfo_business_materials(
    query: CninfoBusinessMaterialQuery,
    *,
    transport: Optional[Callable[..., Mapping[str, Any]]] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> OfficialBusinessMaterialDiscoveryBatch:
    if not _query_valid(query):
        return parse_cninfo_business_material_payload(
            query,
            {},
            fetched_at=datetime.now(SHANGHAI_TZ),
        )
    requester = transport

    def request_page(page_query: CninfoBusinessMaterialQuery):
        for attempt in range(MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS):
            try:
                if requester is not None:
                    return requester(page_query)
                with requests.Session() as session:
                    session.trust_env = False
                    response = session.post(
                        CNINFO_QUERY_URL,
                        data={
                            "pageNum": page_query.page_number,
                            "pageSize": page_query.page_size,
                            "tabName": "fulltext",
                            "column": "szse",
                            "stock": (
                                f"{page_query.scope.symbol},"
                                f"{page_query.scope.issuer_identity.removeprefix('cninfo-org:')}"
                            ),
                            "searchkey": page_query.search_key,
                            "seDate": (
                                f"{page_query.window_from.isoformat()}~"
                                f"{page_query.window_until.isoformat()}"
                            ),
                            "sortName": "announcementId",
                            "sortType": "desc",
                            "isHLtitle": "true",
                        },
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()
                    return response.json()
            except requests.RequestException:
                if attempt + 1 == MAXIMUM_TRANSIENT_REQUEST_ATTEMPTS:
                    raise
        raise RuntimeError("business_material_request_attempts_exhausted")

    try:
        payload = request_page(query)
        if isinstance(payload, Mapping) and payload.get("hasMore") is True:
            next_payload = request_page(replace(
                query,
                page_number=query.page_number + 1,
            ))
            if (
                not isinstance(next_payload, Mapping)
                or not isinstance(payload.get("announcements"), list)
                or not isinstance(next_payload.get("announcements"), list)
                or payload.get("totalAnnouncement")
                != next_payload.get("totalAnnouncement")
                or payload.get("totalpages")
                != next_payload.get("totalpages")
                or not isinstance(next_payload.get("hasMore"), bool)
            ):
                return _result(
                    query,
                    OfficialBusinessMaterialDiscoveryStatus.SOURCE_UNVERIFIED,
                    fetched_at=(
                        clock or (lambda: datetime.now(SHANGHAI_TZ))
                    )(),
                    reasons=(
                        "business_material_page_coverage_unverified",
                    ),
                )
            payload = {
                **payload,
                "announcements": [
                    *payload["announcements"],
                    *next_payload["announcements"],
                ],
                "hasMore": next_payload["hasMore"],
            }
        return parse_cninfo_business_material_payload(
            query,
            payload,
            fetched_at=(clock or (lambda: datetime.now(SHANGHAI_TZ)))(),
        )
    except Exception:
        return _result(
            query,
            OfficialBusinessMaterialDiscoveryStatus.SOURCE_FAILED,
            fetched_at=None,
            reasons=("business_material_source_failed",),
        )
