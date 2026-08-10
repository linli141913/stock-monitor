"""阶段6风险官方发现结果的候选全集人工审核队列。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    MAXIMUM_COVERAGE_AGE_SECONDS,
    RiskCategory,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_risk_official import (
    CNINFO_ISSUER_SCOPE_CONTRACT_ID,
    CNINFO_SOURCE_CONTRACT_ID,
    MAXIMUM_CANDIDATE_SCOPE_COUNT,
    CninfoRiskIssuerScope,
    CninfoRiskDiscoveryQuery,
    OfficialRiskDiscoveryBatch,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
)


LEADER_RISK_OFFICIAL_CANDIDATE_DISCOVERY_CONTRACT_ID = (
    "radar-leader-risk-official-candidate-discovery-v1"
)
UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SECURITY_CODE_PATTERN = re.compile(r"[0-9]{6}")
STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN = re.compile(
    r"[036][0-9]{5}"
)
SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,160}")


class LeaderRiskOfficialCandidateDiscoveryStatus(str, Enum):
    PARTIAL = "partial"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRiskOfficialCandidateDocument:
    document: OfficialRiskDocumentMetadata = field(repr=False)
    candidate_categories: Tuple[RiskCategory, ...]
    matched_query_keys: Tuple[str, ...]
    source_statuses: Tuple[OfficialRiskSourceStatus, ...]
    formal_usable: bool = False

    @property
    def document_id(self) -> str:
        return self.document.document_id

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "documentId": self.document.document_id,
            "publishedAt": self.document.published_at.isoformat(),
            "candidateCategories": [
                category.value for category in self.candidate_categories
            ],
            "matchedQueryKeys": list(self.matched_query_keys),
            "sourceStatuses": [
                status.value for status in self.source_statuses
            ],
            "formalUsable": False,
        }


@dataclass(frozen=True)
class LeaderRiskOfficialCandidateDiscoveryItem:
    index: int
    symbol: str
    issuer_identity: Optional[str]
    status: LeaderRiskOfficialCandidateDiscoveryStatus
    documents: Tuple[LeaderRiskOfficialCandidateDocument, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    coverage_complete: bool = False
    open_event_carry_forward_complete: bool = False
    correction_links_complete: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "status": self.status.value,
            "documentCount": len(self.documents),
            "documents": [
                document.to_evidence() for document in self.documents
            ],
            "reasons": list(self.reasons),
            "coverage": {
                "complete": False,
                "openEventCarryForwardComplete": False,
                "correctionLinksComplete": False,
            },
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


@dataclass(frozen=True)
class LeaderRiskOfficialCandidateDiscoveryResult:
    status: LeaderRiskOfficialCandidateDiscoveryStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    query_count: int
    ignored_document_count: int
    queried_categories: Tuple[RiskCategory, ...] = field(
        default_factory=tuple,
    )
    missing_query_categories: Tuple[RiskCategory, ...] = field(
        default_factory=tuple,
    )
    items: Tuple[LeaderRiskOfficialCandidateDiscoveryItem, ...] = field(
        default_factory=tuple,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = (
        LEADER_RISK_OFFICIAL_CANDIDATE_DISCOVERY_CONTRACT_ID
    )
    coverage_complete: bool = False
    open_event_carry_forward_complete: bool = False
    correction_links_complete: bool = False
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def documents_by_symbol(
        self,
    ) -> Mapping[str, Tuple[LeaderRiskOfficialCandidateDocument, ...]]:
        return MappingProxyType({
            item.symbol: item.documents
            for item in self.items
            if item.documents
            and item.status
            == LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "queryCount": self.query_count,
            "ignoredDocumentCount": self.ignored_document_count,
            "queriedCategories": [
                category.value for category in self.queried_categories
            ],
            "missingQueryCategories": [
                category.value
                for category in self.missing_query_categories
            ],
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "coverageComplete": False,
                "openEventCarryForwardComplete": False,
                "correctionLinksComplete": False,
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _safe_url(value: Any) -> bool:
    if not _required_text(value):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except (TypeError, ValueError):
        return False
    return bool(
        parts.scheme == "https"
        and parts.hostname == "static.cninfo.com.cn"
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
        and port is None
        and parts.path.startswith("/finalpage/")
        and parts.path.lower().endswith(".pdf")
    )


def _query_valid(query: Any) -> bool:
    scope_identity = _query_scope_identity(query)
    return bool(
        type(query) is CninfoRiskDiscoveryQuery
        and _required_text(query.search_key)
        and isinstance(query.candidate_category, RiskCategory)
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
        and scope_identity is not None
    )


def _query_scope_identity(query: Any) -> Optional[Tuple[Any, ...]]:
    scopes = getattr(query, "candidate_scopes", None)
    if not isinstance(scopes, tuple):
        return None
    identities = []
    for scope in scopes:
        if (
            type(scope) is not CninfoRiskIssuerScope
            or scope.source_contract_id
            != CNINFO_ISSUER_SCOPE_CONTRACT_ID
            or not isinstance(scope.symbol, str)
            or STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(
                scope.symbol
            ) is None
            or not isinstance(scope.issuer_identity, str)
            or not scope.issuer_identity.startswith("cninfo-org:")
            or SAFE_ID_PATTERN.fullmatch(scope.issuer_identity) is None
            or (resolved_at := _aware_utc(scope.resolved_at)) is None
        ):
            return None
        identities.append((scope.symbol, scope.issuer_identity, resolved_at))
    if (
        len({identity[0] for identity in identities}) != len(identities)
        or len({identity[1] for identity in identities}) != len(identities)
    ):
        return None
    return tuple(identities)


def _document_valid(
    document: Any,
    *,
    query: CninfoRiskDiscoveryQuery,
    fetched_at: datetime,
    as_of: datetime,
) -> bool:
    published_at = _aware_utc(getattr(document, "published_at", None))
    if (
        type(document) is not OfficialRiskDocumentMetadata
        or document.source_contract_id != CNINFO_SOURCE_CONTRACT_ID
        or not _required_text(document.document_id)
        or not document.document_id.startswith("cninfo:")
        or not _required_text(document.symbol)
        or SECURITY_CODE_PATTERN.fullmatch(document.symbol) is None
        or not _required_text(document.issuer_identity)
        or not document.issuer_identity.startswith("cninfo-org:")
        or not _required_text(document.issuer_name)
        or not _required_text(document.title)
        or published_at is None
        or published_at > fetched_at
        or published_at > as_of
        or not (
            query.window_from
            <= published_at.astimezone(SHANGHAI_TZ).date()
            <= query.window_until
        )
        or document.source_name != "巨潮资讯"
        or not _safe_url(document.source_url)
        or document.candidate_category != query.candidate_category
        or document.formal_usable is not False
    ):
        return False
    document_id = document.document_id.removeprefix("cninfo:")
    url_stem = urlsplit(document.source_url).path.rsplit("/", 1)[-1][:-4]
    if not bool(
        SAFE_ID_PATTERN.fullmatch(document_id)
        and url_stem == document_id
    ):
        return False
    scope_identity = _query_scope_identity(query)
    return bool(
        scope_identity is not None
        and (
            not scope_identity
            or (document.symbol, document.issuer_identity)
            in {
                (symbol, issuer_identity)
                for symbol, issuer_identity, _ in scope_identity
            }
        )
    )


def _batch_valid(
    batch: Any,
    *,
    as_of: datetime,
) -> bool:
    if (
        type(batch) is not OfficialRiskDiscoveryBatch
        or not isinstance(batch.status, OfficialRiskSourceStatus)
        or not _query_valid(batch.query)
        or not isinstance(batch.documents, tuple)
        or not isinstance(batch.reasons, tuple)
        or any(not _required_text(reason) for reason in batch.reasons)
        or batch.coverage_complete is not False
        or batch.open_event_carry_forward_complete is not False
        or batch.correction_links_complete is not False
        or batch.formal_usable is not False
        or (
            batch.status == OfficialRiskSourceStatus.SOURCE_FAILED
            and (
                bool(batch.documents)
                or batch.total_records is not None
                or batch.total_pages is not None
                or batch.reported_total_pages is not None
                or batch.has_more is not None
            )
        )
    ):
        return False
    fetched_at = _aware_utc(batch.fetched_at)
    scope_identity = _query_scope_identity(batch.query)
    if (
        fetched_at is None
        or scope_identity is None
        or any(
            resolved_at > fetched_at
            for _, _, resolved_at in scope_identity
        )
        or fetched_at
        > as_of + timedelta(seconds=MAXIMUM_COVERAGE_AGE_SECONDS)
        or batch.query.window_until
        > as_of.astimezone(SHANGHAI_TZ).date()
    ):
        return False
    if batch.status == OfficialRiskSourceStatus.PARTIAL:
        if (
            not isinstance(batch.total_records, int)
            or isinstance(batch.total_records, bool)
            or batch.total_records < 0
            or not isinstance(batch.total_pages, int)
            or isinstance(batch.total_pages, bool)
            or batch.total_pages < 0
            or not isinstance(batch.reported_total_pages, int)
            or isinstance(batch.reported_total_pages, bool)
            or batch.reported_total_pages < 0
            or not isinstance(batch.has_more, bool)
            or len(batch.documents) > batch.query.page_size
            or len(batch.documents) > batch.total_records
        ):
            return False
    return all(
        _document_valid(
            document,
            query=batch.query,
            fetched_at=fetched_at,
            as_of=as_of,
        )
        for document in batch.documents
    )


def _document_identity(document: OfficialRiskDocumentMetadata) -> Any:
    return replace(document, candidate_category=RiskCategory.REDUCTION)


def _result(
    *,
    status: LeaderRiskOfficialCandidateDiscoveryStatus,
    candidate_plan_id: Optional[str],
    candidate_count: int,
    query_count: int,
    ignored_document_count: int,
    reasons: Sequence[str],
    items: Sequence[LeaderRiskOfficialCandidateDiscoveryItem] = (),
    queried_categories: Sequence[RiskCategory] = (),
    missing_query_categories: Sequence[RiskCategory] = (),
) -> LeaderRiskOfficialCandidateDiscoveryResult:
    return LeaderRiskOfficialCandidateDiscoveryResult(
        status=status,
        candidate_plan_id=candidate_plan_id,
        candidate_count=candidate_count,
        query_count=query_count,
        ignored_document_count=ignored_document_count,
        queried_categories=tuple(queried_categories),
        missing_query_categories=tuple(missing_query_categories),
        items=tuple(items),
        reasons=_dedupe(reasons),
    )


def _item_status_without_documents(
    *,
    any_failed: bool,
    any_unverified: bool,
) -> Tuple[LeaderRiskOfficialCandidateDiscoveryStatus, Tuple[str, ...]]:
    if any_failed:
        return (
            LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED,
            ("risk_official_candidate_discovery_source_failed",),
        )
    if any_unverified:
        return (
            LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_UNVERIFIED,
            ("risk_official_candidate_discovery_source_unverified",),
        )
    return (
        LeaderRiskOfficialCandidateDiscoveryStatus.MISSING,
        ("risk_official_candidate_discovery_documents_missing",),
    )


def build_leader_risk_official_candidate_discovery_batch(
    *,
    candidate_plan: Any,
    batches: Any,
) -> LeaderRiskOfficialCandidateDiscoveryResult:
    """把D2官方发现结果整理为候选全集人工审核队列。"""

    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or not isinstance(batches, tuple)
    ):
        return _result(
            status=LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
            candidate_plan_id=None,
            candidate_count=0,
            query_count=0,
            ignored_document_count=0,
            reasons=(
                "risk_official_candidate_discovery_contract_unverified",
            ),
        )
    plan = candidate_plan
    as_of = _aware_utc(plan.as_of)
    assert as_of is not None
    candidate_symbols = tuple(item.symbol for item in plan.items)
    if any(not _batch_valid(batch, as_of=as_of) for batch in batches):
        return _result(
            status=LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=len(plan.items),
            query_count=len(batches),
            ignored_document_count=0,
            reasons=(
                "risk_official_candidate_discovery_batch_unverified",
            ),
        )
    scope_identities = tuple(
        _query_scope_identity(batch.query) for batch in batches
    )
    scoped_identities = tuple(
        identity for identity in scope_identities if identity
    )
    expected_symbol_shards = tuple(
        candidate_symbols[index:index + MAXIMUM_CANDIDATE_SCOPE_COUNT]
        for index in range(
            0,
            len(candidate_symbols),
            MAXIMUM_CANDIDATE_SCOPE_COUNT,
        )
    )
    scope_layout_valid = not batches or bool(scoped_identities)
    if scoped_identities:
        scope_layout_valid = len(scoped_identities) == len(batches)
        canonical_shards = {}
        category_first_page_shards = {}
        for batch, identity in zip(batches, scope_identities):
            identity_symbols = tuple(
                symbol for symbol, _, _ in identity
            )
            try:
                shard_index = expected_symbol_shards.index(
                    identity_symbols
                )
            except ValueError:
                scope_layout_valid = False
                continue
            if (
                batch.query.candidate_plan_id
                != plan.candidate_set_id
                or batch.query.shard_index != shard_index
                or batch.query.shard_count != len(expected_symbol_shards)
            ):
                scope_layout_valid = False
            canonical = canonical_shards.get(shard_index)
            if canonical is None:
                canonical_shards[shard_index] = identity
            elif canonical != identity:
                scope_layout_valid = False
            if batch.query.page_number == 1:
                category_first_page_shards.setdefault(
                    batch.query.candidate_category,
                    [],
                ).append(shard_index)
        expected_shard_indexes = list(range(len(expected_symbol_shards)))
        if (
            set(canonical_shards) != set(expected_shard_indexes)
            or any(
                indexes != expected_shard_indexes
                for indexes in category_first_page_shards.values()
            )
        ):
            scope_layout_valid = False
    if not scope_layout_valid:
        return _result(
            status=LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=len(plan.items),
            query_count=len(batches),
            ignored_document_count=0,
            reasons=(
                "risk_official_candidate_discovery_scope_mismatch",
            ),
        )
    query_identities = tuple((
        batch.query.search_key.strip(),
        batch.query.candidate_category,
        batch.query.window_from,
        batch.query.window_until,
        batch.query.page_number,
        batch.query.page_size,
        _query_scope_identity(batch.query),
    ) for batch in batches)
    if len(query_identities) != len(set(query_identities)):
        return _result(
            status=LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED,
            candidate_plan_id=plan.candidate_set_id,
            candidate_count=len(plan.items),
            query_count=len(batches),
            ignored_document_count=0,
            reasons=(
                "risk_official_candidate_discovery_query_duplicate",
            ),
        )

    candidate_symbol_set = set(candidate_symbols)
    grouped = {}
    conflicting_symbols = set()
    ignored_document_count = 0
    for batch in batches:
        for source_document in batch.documents:
            if (
                source_document.symbol not in candidate_symbol_set
                or STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(
                    source_document.symbol
                ) is None
            ):
                ignored_document_count += 1
                continue
            key = (source_document.symbol, source_document.document_id)
            current = grouped.get(key)
            if current is None:
                grouped[key] = (
                    source_document,
                    [batch.query.candidate_category],
                    [batch.query.search_key.strip()],
                    [batch.status],
                )
                continue
            document, categories, query_keys, source_statuses = current
            if _document_identity(document) != _document_identity(
                source_document
            ):
                conflicting_symbols.add(source_document.symbol)
                continue
            if batch.query.candidate_category not in categories:
                categories.append(batch.query.candidate_category)
            query_key = batch.query.search_key.strip()
            if query_key not in query_keys:
                query_keys.append(query_key)
            if batch.status not in source_statuses:
                source_statuses.append(batch.status)

    by_symbol = {symbol: [] for symbol in candidate_symbols}
    issuer_identities = {symbol: set() for symbol in candidate_symbols}
    for (
        symbol,
        _,
    ), (
        source_document,
        categories,
        query_keys,
        source_statuses,
    ) in grouped.items():
        by_symbol[symbol].append(LeaderRiskOfficialCandidateDocument(
            document=source_document,
            candidate_categories=tuple(categories),
            matched_query_keys=tuple(query_keys),
            source_statuses=tuple(source_statuses),
        ))
        issuer_identities[symbol].add(source_document.issuer_identity)
    any_failed = any(
        batch.status == OfficialRiskSourceStatus.SOURCE_FAILED
        for batch in batches
    )
    any_unverified = any(
        batch.status == OfficialRiskSourceStatus.SOURCE_UNVERIFIED
        for batch in batches
    )
    queried_categories = tuple(
        category
        for category in ALL_RISK_CATEGORIES
        if any(
            batch.query.candidate_category == category
            for batch in batches
        )
    )
    missing_query_categories = tuple(
        category
        for category in ALL_RISK_CATEGORIES
        if category not in queried_categories
    )
    items = []
    for index, symbol in enumerate(candidate_symbols):
        documents = tuple(sorted(
            by_symbol[symbol],
            key=lambda value: (
                value.document.published_at,
                value.document.document_id,
            ),
        ))
        issuers = issuer_identities[symbol]
        if symbol in conflicting_symbols or len(issuers) > 1:
            items.append(LeaderRiskOfficialCandidateDiscoveryItem(
                index=index,
                symbol=symbol,
                issuer_identity=None,
                status=(
                    LeaderRiskOfficialCandidateDiscoveryStatus
                    .SOURCE_UNVERIFIED
                ),
                reasons=(
                    "risk_official_candidate_discovery_issuer_conflict",
                ),
            ))
        elif documents:
            items.append(LeaderRiskOfficialCandidateDiscoveryItem(
                index=index,
                symbol=symbol,
                issuer_identity=next(iter(issuers)),
                status=LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
                documents=documents,
                reasons=(
                    "risk_official_candidate_discovery_partial",
                    "risk_official_candidate_discovery_coverage_not_proven",
                ),
            ))
        else:
            status, reasons = _item_status_without_documents(
                any_failed=any_failed,
                any_unverified=any_unverified,
            )
            items.append(LeaderRiskOfficialCandidateDiscoveryItem(
                index=index,
                symbol=symbol,
                issuer_identity=None,
                status=status,
                reasons=reasons,
            ))

    usable_items = tuple(
        item for item in items
        if item.status == LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL
    )
    if usable_items:
        status = LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL
        reasons = [
            "risk_official_candidate_discovery_partial",
            "risk_official_candidate_discovery_coverage_not_proven",
        ]
    elif any(
        item.status
        == LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_UNVERIFIED
        for item in items
    ):
        status = LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_UNVERIFIED
        reasons = [
            "risk_official_candidate_discovery_source_unverified",
        ]
    elif any_failed:
        status = LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED
        reasons = ["risk_official_candidate_discovery_source_failed"]
    else:
        status = LeaderRiskOfficialCandidateDiscoveryStatus.MISSING
        reasons = ["risk_official_candidate_discovery_documents_missing"]
    if ignored_document_count:
        reasons.append(
            "risk_official_candidate_discovery_out_of_scope_excluded"
        )
    if missing_query_categories:
        reasons.append(
            "risk_official_candidate_discovery_categories_incomplete"
        )
    if any_failed and status == LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL:
        reasons.append("risk_official_candidate_discovery_source_failed")
    if any_unverified and status == LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL:
        reasons.append(
            "risk_official_candidate_discovery_source_unverified"
        )
    return _result(
        status=status,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(plan.items),
        query_count=len(batches),
        ignored_document_count=ignored_document_count,
        reasons=reasons,
        items=items,
        queried_categories=queried_categories,
        missing_query_categories=missing_query_categories,
    )
