"""阶段6风险官方发现与人工生命周期的受控单次交付入口。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import re
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_risk_invalidation_features import (
    MAXIMUM_COVERAGE_AGE_SECONDS,
)
from radar.leader_risk_lifecycle_batch import (
    CANONICAL_DISCOVERY_SEARCH_KEYS,
    LeaderRiskLifecycleBatchEntry,
    LeaderRiskLifecycleBatchInput,
    LeaderRiskLifecycleBatchResult,
    LeaderRiskLifecycleBatchStatus,
    build_leader_risk_lifecycle_batch,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_risk_official import (
    CNINFO_ISSUER_SCOPE_CONTRACT_ID,
    MAXIMUM_CANDIDATE_SCOPE_COUNT,
    CninfoRiskIssuerScope,
    CninfoRiskDiscoveryQuery,
    CninfoTransport,
    OfficialRiskDiscoveryBatch,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
    fetch_cninfo_risk_discovery,
)


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SAFE_REPORT_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,160}")
LEADER_RISK_LIFECYCLE_DELIVERY_CONTRACT_ID = (
    "radar-leader-risk-lifecycle-delivery-v1"
)
MINIMUM_PAGE_REQUESTS = len(CANONICAL_DISCOVERY_SEARCH_KEYS)
MAXIMUM_PAGE_REQUESTS = 70
PAGE_SIZE = 30
PAGINATION_BLOCKING_REASONS = frozenset({
    "cninfo_response_pagination_inconsistent",
    "cninfo_response_pagination_unverified",
})


class LeaderRiskLifecycleDeliveryStatus(str, Enum):
    NOT_RUN = "not_run"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


class LeaderRiskLifecycleRealPocStatus(str, Enum):
    NOT_RUN = "not_run"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class LeaderRiskLifecycleDeliveryInput:
    candidate_plan: Any = field(repr=False)
    entries: Any = field(repr=False)
    candidate_scopes: Any = field(repr=False)
    window_from: Any
    window_until: Any
    collected_at: Any
    confirm_live_poc: Any = False
    max_page_requests: Any = None


@dataclass(frozen=True)
class LeaderRiskLifecycleDeliveryResult:
    status: LeaderRiskLifecycleDeliveryStatus
    real_poc_status: LeaderRiskLifecycleRealPocStatus
    candidate_plan_id: Optional[str]
    radar_run_id: Optional[str]
    quote_batch_id: Optional[str]
    as_of: Optional[datetime]
    request_count: int = 0
    fetched_page_count: int = 0
    category_count: int = 0
    candidate_scope_count: int = 0
    shard_count: int = 0
    reasons: Tuple[str, ...] = ()
    lifecycle_result: Optional[
        LeaderRiskLifecycleBatchResult
    ] = field(default=None, repr=False)
    contract_id: str = LEADER_RISK_LIFECYCLE_DELIVERY_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        lifecycle = self.lifecycle_result
        projection = (
            lifecycle.projection_batch
            if lifecycle is not None
            else None
        )
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "realPocStatus": self.real_poc_status.value,
            "candidatePlanId": _safe_report_id(self.candidate_plan_id),
            "radarRunId": _safe_report_id(self.radar_run_id),
            "quoteBatchId": _safe_report_id(self.quote_batch_id),
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "requestCount": self.request_count,
            "fetchedPageCount": self.fetched_page_count,
            "categoryCount": self.category_count,
            "candidateScopeCount": self.candidate_scope_count,
            "shardCount": self.shard_count,
            "reasons": list(self.reasons),
            "lifecycleStatus": (
                lifecycle.status.value if lifecycle is not None else None
            ),
            "projectionStatus": (
                projection.status.value if projection is not None else None
            ),
            "readyProjectionCount": (
                projection.ready_count if projection is not None else 0
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _safe_report_id(value: Any) -> Optional[str]:
    if (
        isinstance(value, str)
        and SAFE_REPORT_ID_PATTERN.fullmatch(value) is not None
    ):
        return value
    return None


def _pagination_group_key(
    batch: OfficialRiskDiscoveryBatch,
) -> Tuple[Optional[int], object]:
    return (
        batch.query.shard_index,
        batch.query.candidate_category,
    )


def _validated_group_snapshot(
    grouped: Sequence[OfficialRiskDiscoveryBatch],
    *,
    require_unique: bool,
) -> Optional[Tuple[OfficialRiskDocumentMetadata, ...]]:
    if not grouped:
        return None
    keys = {_pagination_group_key(item) for item in grouped}
    total_pages_values = {item.total_pages for item in grouped}
    reported_total_pages_values = {
        item.reported_total_pages for item in grouped
    }
    total_records_values = {item.total_records for item in grouped}
    if (
        len(keys) != 1
        or len(total_pages_values) != 1
        or len(reported_total_pages_values) != 1
        or len(total_records_values) != 1
        or any(
            item.status != OfficialRiskSourceStatus.PARTIAL
            for item in grouped
        )
    ):
        return None
    total_pages = next(iter(total_pages_values))
    total_records = next(iter(total_records_values))
    page_size = grouped[0].query.page_size
    if (
        not isinstance(total_pages, int)
        or isinstance(total_pages, bool)
        or total_pages < 0
        or not isinstance(total_records, int)
        or isinstance(total_records, bool)
        or total_records < 0
        or not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or page_size <= 0
        or total_pages
        != (
            0
            if total_records == 0
            else (total_records + page_size - 1) // page_size
        )
    ):
        return None
    expected_pages = (
        (1,) if total_pages == 0 else tuple(range(1, total_pages + 1))
    )
    ordered = tuple(sorted(
        grouped,
        key=lambda item: item.query.page_number,
    ))
    if tuple(item.query.page_number for item in ordered) != expected_pages:
        return None
    if any(
        item.has_more != (index < len(ordered) - 1)
        for index, item in enumerate(ordered)
    ):
        return None
    documents = []
    for page_index, item in enumerate(ordered):
        expected_count = (
            0
            if total_pages == 0
            else (
                page_size
                if page_index < total_pages - 1
                else total_records - page_size * (total_pages - 1)
            )
        )
        if (
            not isinstance(item.documents, tuple)
            or len(item.documents) != expected_count
            or any(
                not isinstance(document, OfficialRiskDocumentMetadata)
                or not isinstance(document.document_id, str)
                or not document.document_id
                for document in item.documents
            )
        ):
            return None
        documents.extend(item.documents)
    if len(documents) != total_records:
        return None
    document_ids = tuple(item.document_id for item in documents)
    if require_unique and len(document_ids) != len(set(document_ids)):
        return None
    return tuple(sorted(documents, key=lambda item: item.document_id))


def _exact_duplicate_pagination_groups(
    batches: Sequence[OfficialRiskDiscoveryBatch],
) -> Tuple[Tuple[Tuple[Optional[int], object], CninfoRiskDiscoveryQuery], ...]:
    groups = {}
    for batch in batches:
        groups.setdefault(_pagination_group_key(batch), []).append(batch)
    matches = []
    for key, grouped in groups.items():
        snapshot = _validated_group_snapshot(
            grouped,
            require_unique=False,
        )
        if snapshot is None or len(grouped) < 2:
            continue
        documents_by_id = {}
        for document in snapshot:
            documents_by_id.setdefault(document.document_id, []).append(
                document
            )
        duplicate_groups = tuple(
            documents
            for documents in documents_by_id.values()
            if len(documents) > 1
        )
        if not duplicate_groups or any(
            any(document != documents[0] for document in documents[1:])
            for documents in duplicate_groups
        ):
            continue
        representative = min(
            grouped,
            key=lambda item: item.query.page_number,
        ).query
        matches.append((key, representative))
    return tuple(matches)


def _validated_partition_snapshot(
    batches: Sequence[OfficialRiskDiscoveryBatch],
    *,
    representative: CninfoRiskDiscoveryQuery,
) -> Optional[
    Tuple[
        Tuple[Tuple[date, date], ...],
        Tuple[OfficialRiskDocumentMetadata, ...],
    ]
]:
    if not batches:
        return None
    ordered = tuple(sorted(
        batches,
        key=lambda item: (
            item.query.window_from,
            item.query.window_until,
        ),
    ))
    windows = tuple(
        (item.query.window_from, item.query.window_until)
        for item in ordered
    )
    if (
        windows[0][0] != representative.window_from
        or windows[-1][1] != representative.window_until
        or any(
            current[0] != previous[1] + timedelta(days=1)
            for previous, current in zip(windows, windows[1:])
        )
        or any(
            item.query.page_number != 1
            or item.query.search_key != representative.search_key
            or item.query.candidate_category
            != representative.candidate_category
            or item.query.page_size != representative.page_size
            or item.query.candidate_scopes
            != representative.candidate_scopes
            or item.query.candidate_plan_id
            != representative.candidate_plan_id
            or item.query.shard_index != representative.shard_index
            or item.query.shard_count != representative.shard_count
            for item in ordered
        )
    ):
        return None
    documents = []
    for batch in ordered:
        snapshot = _validated_group_snapshot(
            (batch,),
            require_unique=True,
        )
        if snapshot is None:
            return None
        documents.extend(snapshot)
    document_ids = tuple(item.document_id for item in documents)
    if len(document_ids) != len(set(document_ids)):
        return None
    return (
        windows,
        tuple(sorted(documents, key=lambda item: item.document_id)),
    )


def _result(
    *,
    status: LeaderRiskLifecycleDeliveryStatus,
    real_poc_status: LeaderRiskLifecycleRealPocStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    as_of: Optional[datetime],
    reasons: Sequence[str],
    batches: Sequence[OfficialRiskDiscoveryBatch] = (),
    request_count: int = 0,
    candidate_scope_count: int = 0,
    shard_count: int = 0,
    lifecycle_result: Optional[LeaderRiskLifecycleBatchResult] = None,
) -> LeaderRiskLifecycleDeliveryResult:
    fetched_batches = tuple(
        batch
        for batch in batches
        if (
            isinstance(batch, OfficialRiskDiscoveryBatch)
            and batch.status == OfficialRiskSourceStatus.PARTIAL
        )
    )
    categories = {
        batch.query.candidate_category
        for batch in fetched_batches
    }
    return LeaderRiskLifecycleDeliveryResult(
        status=status,
        real_poc_status=real_poc_status,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        radar_run_id=plan.radar_run_id if plan is not None else None,
        quote_batch_id=plan.quote_batch_id if plan is not None else None,
        as_of=as_of,
        request_count=request_count,
        fetched_page_count=len(fetched_batches),
        category_count=len(categories),
        candidate_scope_count=candidate_scope_count,
        shard_count=shard_count,
        reasons=_dedupe(reasons),
        lifecycle_result=lifecycle_result,
    )


def _contract_reasons(
    input_value: LeaderRiskLifecycleDeliveryInput,
) -> Tuple[str, ...]:
    plan = input_value.candidate_plan
    if (
        not isinstance(plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(plan)
    ):
        return ("risk_lifecycle_delivery_candidate_plan_unverified",)
    entries = input_value.entries
    expected_symbols = tuple(item.symbol for item in plan.items)
    if (
        not isinstance(entries, tuple)
        or len(entries) != len(expected_symbols)
        or any(
            not isinstance(entry, LeaderRiskLifecycleBatchEntry)
            for entry in entries
        )
        or tuple(entry.symbol for entry in entries) != expected_symbols
    ):
        return ("risk_lifecycle_delivery_entries_unverified",)
    candidate_scopes = input_value.candidate_scopes
    if (
        not isinstance(candidate_scopes, tuple)
        or len(candidate_scopes) != len(expected_symbols)
        or any(
            type(scope) is not CninfoRiskIssuerScope
            or scope.source_contract_id
            != CNINFO_ISSUER_SCOPE_CONTRACT_ID
            or scope.symbol != expected_symbols[index]
            or not isinstance(scope.issuer_identity, str)
            or not scope.issuer_identity.startswith("cninfo-org:")
            or SAFE_REPORT_ID_PATTERN.fullmatch(scope.issuer_identity)
            is None
            or _aware_utc(scope.resolved_at) is None
            for index, scope in enumerate(candidate_scopes)
        )
        or len({scope.issuer_identity for scope in candidate_scopes})
        != len(candidate_scopes)
        or any(
            entry.issuer_identity is not None
            and entry.issuer_identity
            != candidate_scopes[index].issuer_identity
            for index, entry in enumerate(entries)
        )
    ):
        return ("risk_lifecycle_delivery_candidate_scope_unverified",)
    if (
        not isinstance(input_value.window_from, date)
        or isinstance(input_value.window_from, datetime)
        or not isinstance(input_value.window_until, date)
        or isinstance(input_value.window_until, datetime)
        or input_value.window_from > input_value.window_until
    ):
        return ("risk_lifecycle_delivery_window_unverified",)
    collected_at = _aware_utc(input_value.collected_at)
    plan_as_of = _aware_utc(plan.as_of)
    if (
        collected_at is None
        or plan_as_of is None
        or collected_at < plan_as_of
        or (
            collected_at - plan_as_of
        ).total_seconds() > MAXIMUM_COVERAGE_AGE_SECONDS
    ):
        return ("risk_lifecycle_delivery_time_unverified",)
    if any(
        (resolved_at := _aware_utc(scope.resolved_at)) is None
        or resolved_at < plan_as_of
        or resolved_at > collected_at
        for scope in candidate_scopes
    ):
        return ("risk_lifecycle_delivery_candidate_scope_unverified",)
    assert plan_as_of is not None
    plan_trade_date = plan_as_of.astimezone(SHANGHAI_TZ).date()
    if input_value.window_until != plan_trade_date:
        return ("risk_lifecycle_delivery_window_unverified",)
    shard_count = (
        len(candidate_scopes) + MAXIMUM_CANDIDATE_SCOPE_COUNT - 1
    ) // MAXIMUM_CANDIDATE_SCOPE_COUNT
    max_page_requests = input_value.max_page_requests
    if max_page_requests is None:
        return ()
    if (
        not isinstance(max_page_requests, int)
        or isinstance(max_page_requests, bool)
        or max_page_requests < 1
        or max_page_requests > MAXIMUM_PAGE_REQUESTS * shard_count
    ):
        return ("risk_lifecycle_delivery_page_budget_unverified",)
    if max_page_requests < MINIMUM_PAGE_REQUESTS * shard_count:
        return ("risk_lifecycle_delivery_page_budget_insufficient",)
    return ()


def _delivery_status(
    lifecycle: LeaderRiskLifecycleBatchResult,
) -> LeaderRiskLifecycleDeliveryStatus:
    mapping = {
        LeaderRiskLifecycleBatchStatus.PARTIAL: (
            LeaderRiskLifecycleDeliveryStatus.PARTIAL
        ),
        LeaderRiskLifecycleBatchStatus.MISSING: (
            LeaderRiskLifecycleDeliveryStatus.MISSING
        ),
        LeaderRiskLifecycleBatchStatus.STALE: (
            LeaderRiskLifecycleDeliveryStatus.STALE
        ),
        LeaderRiskLifecycleBatchStatus.SOURCE_FAILED: (
            LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED
        ),
        LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED: (
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED
        ),
        LeaderRiskLifecycleBatchStatus.BLOCKED: (
            LeaderRiskLifecycleDeliveryStatus.BLOCKED
        ),
    }
    return mapping[lifecycle.status]


def _real_poc_status(
    *,
    transport: Optional[CninfoTransport],
    status: LeaderRiskLifecycleDeliveryStatus,
) -> LeaderRiskLifecycleRealPocStatus:
    if transport is not None:
        return LeaderRiskLifecycleRealPocStatus.NOT_RUN
    if status == LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED:
        return LeaderRiskLifecycleRealPocStatus.FAILED
    if status in {
        LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        LeaderRiskLifecycleDeliveryStatus.MISSING,
        LeaderRiskLifecycleDeliveryStatus.STALE,
        LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
    }:
        return LeaderRiskLifecycleRealPocStatus.PARTIAL
    return LeaderRiskLifecycleRealPocStatus.NOT_RUN


def deliver_leader_risk_lifecycle(
    input_value: Any,
    *,
    transport: Optional[CninfoTransport] = None,
) -> LeaderRiskLifecycleDeliveryResult:
    """有界采集七类D2分页，并把强类型人工版本交给生命周期重放。"""

    if not isinstance(input_value, LeaderRiskLifecycleDeliveryInput):
        return _result(
            status=LeaderRiskLifecycleDeliveryStatus.BLOCKED,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.NOT_RUN,
            plan=None,
            as_of=None,
            reasons=("risk_lifecycle_delivery_contract_unverified",),
        )
    plan = input_value.candidate_plan
    candidate_scopes = input_value.candidate_scopes
    candidate_scope_count = (
        len(candidate_scopes)
        if isinstance(candidate_scopes, tuple)
        else 0
    )
    shard_count = (
        (
            candidate_scope_count
            + MAXIMUM_CANDIDATE_SCOPE_COUNT
            - 1
        )
        // MAXIMUM_CANDIDATE_SCOPE_COUNT
        if candidate_scope_count
        else 0
    )
    if input_value.confirm_live_poc is not True:
        return _result(
            status=LeaderRiskLifecycleDeliveryStatus.NOT_RUN,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.NOT_RUN,
            plan=(
                plan if isinstance(plan, LeaderRuntimeCandidatePlan) else None
            ),
            as_of=_aware_utc(input_value.collected_at),
            reasons=("risk_lifecycle_delivery_confirmation_missing",),
            candidate_scope_count=candidate_scope_count,
            shard_count=shard_count,
        )
    contract_reasons = _contract_reasons(input_value)
    if contract_reasons or (transport is not None and not callable(transport)):
        return _result(
            status=LeaderRiskLifecycleDeliveryStatus.BLOCKED,
            real_poc_status=LeaderRiskLifecycleRealPocStatus.NOT_RUN,
            plan=(
                plan if isinstance(plan, LeaderRuntimeCandidatePlan) else None
            ),
            as_of=_aware_utc(input_value.collected_at),
            reasons=(
                *contract_reasons,
                *(
                    ("risk_lifecycle_delivery_transport_unverified",)
                    if transport is not None and not callable(transport)
                    else ()
                ),
            ),
            candidate_scope_count=candidate_scope_count,
            shard_count=shard_count,
        )

    assert isinstance(plan, LeaderRuntimeCandidatePlan)
    collected_at = _aware_utc(input_value.collected_at)
    assert collected_at is not None
    batches = []
    expected_pages = {}
    request_count = 0
    source_terminal_status = None
    unexpected_source_failure = False
    source_retry_attempted = False
    source_retry_succeeded = False
    pagination_blocked = False
    date_partition_attempted = False
    date_partition_succeeded = False
    date_partition_unverified = False
    scope_shards = tuple(
        input_value.candidate_scopes[
            index:index + MAXIMUM_CANDIDATE_SCOPE_COUNT
        ]
        for index in range(
            0,
            len(input_value.candidate_scopes),
            MAXIMUM_CANDIDATE_SCOPE_COUNT,
        )
    )
    page_budget = (
        MINIMUM_PAGE_REQUESTS * len(scope_shards)
        if input_value.max_page_requests is None
        else input_value.max_page_requests
    )
    assert isinstance(page_budget, int)
    budget_exhausted = False

    def fetch(query: CninfoRiskDiscoveryQuery) -> Optional[
        OfficialRiskDiscoveryBatch
    ]:
        nonlocal request_count, unexpected_source_failure
        nonlocal source_retry_attempted, source_retry_succeeded
        request_count += 1
        try:
            batch = fetch_cninfo_risk_discovery(
                query,
                fetched_at=collected_at,
                transport=transport,
            )
        except Exception:
            unexpected_source_failure = True
            return None
        if (
            batch.status != OfficialRiskSourceStatus.SOURCE_FAILED
            or "cninfo_source_request_failed" not in batch.reasons
            or request_count >= page_budget
        ):
            return batch
        source_retry_attempted = True
        request_count += 1
        try:
            retry_batch = fetch_cninfo_risk_discovery(
                query,
                fetched_at=collected_at,
                transport=transport,
            )
        except Exception:
            unexpected_source_failure = True
            return None
        if retry_batch.status != OfficialRiskSourceStatus.SOURCE_FAILED:
            source_retry_succeeded = True
        return retry_batch

    for shard_index, scope_shard in enumerate(scope_shards):
        for category, search_key in CANONICAL_DISCOVERY_SEARCH_KEYS.items():
            if request_count >= page_budget:
                budget_exhausted = True
                break
            query = CninfoRiskDiscoveryQuery(
                search_key=search_key,
                candidate_category=category,
                window_from=input_value.window_from,
                window_until=input_value.window_until,
                page_number=1,
                page_size=PAGE_SIZE,
                candidate_scopes=scope_shard,
                candidate_plan_id=plan.candidate_set_id,
                shard_index=shard_index,
                shard_count=len(scope_shards),
            )
            batch = fetch(query)
            if batch is None:
                source_terminal_status = OfficialRiskSourceStatus.SOURCE_FAILED
                break
            batches.append(batch)
            if batch.status in {
                OfficialRiskSourceStatus.SOURCE_FAILED,
                OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            }:
                source_terminal_status = batch.status
                break
            if any(
                reason in PAGINATION_BLOCKING_REASONS
                for reason in batch.reasons
            ):
                pagination_blocked = True
                break
            expected_pages[(shard_index, category)] = batch.total_pages or 0
        if (
            source_terminal_status is not None
            or pagination_blocked
            or budget_exhausted
        ):
            break

    if source_terminal_status is None and not pagination_blocked and (
        len(expected_pages)
        == len(CANONICAL_DISCOVERY_SEARCH_KEYS) * len(scope_shards)
    ):
        highest_page = max(expected_pages.values(), default=0)
        for page_number in range(2, highest_page + 1):
            for shard_index, scope_shard in enumerate(scope_shards):
                for category, search_key in (
                    CANONICAL_DISCOVERY_SEARCH_KEYS.items()
                ):
                    if expected_pages[(shard_index, category)] < page_number:
                        continue
                    if request_count >= page_budget:
                        budget_exhausted = True
                        break
                    query = CninfoRiskDiscoveryQuery(
                        search_key=search_key,
                        candidate_category=category,
                        window_from=input_value.window_from,
                        window_until=input_value.window_until,
                        page_number=page_number,
                        page_size=PAGE_SIZE,
                        candidate_scopes=scope_shard,
                        candidate_plan_id=plan.candidate_set_id,
                        shard_index=shard_index,
                        shard_count=len(scope_shards),
                    )
                    batch = fetch(query)
                    if batch is None:
                        source_terminal_status = (
                            OfficialRiskSourceStatus.SOURCE_FAILED
                        )
                        break
                    batches.append(batch)
                    if batch.status in {
                        OfficialRiskSourceStatus.SOURCE_FAILED,
                        OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
                    }:
                        source_terminal_status = batch.status
                        break
                    if any(
                        reason in PAGINATION_BLOCKING_REASONS
                        for reason in batch.reasons
                    ):
                        pagination_blocked = True
                        break
                if (
                    source_terminal_status is not None
                    or pagination_blocked
                    or budget_exhausted
                ):
                    break
            if source_terminal_status is not None or pagination_blocked:
                break
            if budget_exhausted:
                break

    def fetch_date_partition_sweep(
        representative: CninfoRiskDiscoveryQuery,
    ) -> Optional[Tuple[OfficialRiskDiscoveryBatch, ...]]:
        nonlocal budget_exhausted, pagination_blocked
        nonlocal source_terminal_status
        pending = [(
            representative.window_from,
            representative.window_until,
        )]
        sweep = []
        while pending:
            window_from, window_until = pending.pop()
            if request_count >= page_budget:
                budget_exhausted = True
                return None
            batch = fetch(replace(
                representative,
                window_from=window_from,
                window_until=window_until,
                page_number=1,
            ))
            if batch is None:
                source_terminal_status = (
                    OfficialRiskSourceStatus.SOURCE_FAILED
                )
                return None
            if batch.status in {
                OfficialRiskSourceStatus.SOURCE_FAILED,
                OfficialRiskSourceStatus.SOURCE_UNVERIFIED,
            }:
                source_terminal_status = batch.status
                return None
            if any(
                reason in PAGINATION_BLOCKING_REASONS
                for reason in batch.reasons
            ):
                pagination_blocked = True
                return None
            total_records = batch.total_records
            total_pages = batch.total_pages
            requires_partition = bool(
                isinstance(total_records, int)
                and not isinstance(total_records, bool)
                and total_records > PAGE_SIZE
            ) or bool(
                isinstance(total_pages, int)
                and not isinstance(total_pages, bool)
                and total_pages > 1
            )
            if requires_partition:
                if window_from >= window_until:
                    return None
                midpoint = window_from + timedelta(
                    days=(window_until - window_from).days // 2
                )
                pending.append((
                    midpoint + timedelta(days=1),
                    window_until,
                ))
                pending.append((window_from, midpoint))
                continue
            sweep.append(batch)
        return tuple(sorted(
            sweep,
            key=lambda item: (
                item.query.window_from,
                item.query.window_until,
            ),
        ))

    if (
        source_terminal_status is None
        and not pagination_blocked
        and not budget_exhausted
    ):
        duplicate_groups = _exact_duplicate_pagination_groups(batches)
        if duplicate_groups:
            date_partition_attempted = True
            date_partition_succeeded = True
        for group_key, representative in duplicate_groups:
            first_sweep = fetch_date_partition_sweep(representative)
            if first_sweep is None:
                date_partition_succeeded = False
                date_partition_unverified = True
                break
            second_sweep = fetch_date_partition_sweep(representative)
            first_snapshot = (
                _validated_partition_snapshot(
                    first_sweep,
                    representative=representative,
                )
                if first_sweep is not None
                else None
            )
            second_snapshot = (
                _validated_partition_snapshot(
                    second_sweep,
                    representative=representative,
                )
                if second_sweep is not None
                else None
            )
            if (
                first_snapshot is None
                or second_snapshot is None
                or first_snapshot != second_snapshot
            ):
                date_partition_succeeded = False
                date_partition_unverified = True
                break
            matching_indexes = tuple(
                index
                for index, batch in enumerate(batches)
                if _pagination_group_key(batch) == group_key
            )
            if not matching_indexes:
                date_partition_succeeded = False
                date_partition_unverified = True
                break
            first_index = matching_indexes[0]
            batches = [
                batch
                for index, batch in enumerate(batches)
                if index not in matching_indexes
            ]
            batches[first_index:first_index] = list(second_sweep)

    lifecycle = build_leader_risk_lifecycle_batch(
        LeaderRiskLifecycleBatchInput(
            candidate_plan=plan,
            discovery_batches=tuple(batches),
            entries=input_value.entries,
        )
    )
    status = _delivery_status(lifecycle)
    reasons = list(lifecycle.reasons)
    if unexpected_source_failure:
        reasons.append("risk_lifecycle_delivery_source_request_failed")
    if source_retry_attempted:
        reasons.append("risk_lifecycle_delivery_source_retry_attempted")
    if source_retry_succeeded:
        reasons.append("risk_lifecycle_delivery_source_retry_succeeded")
    if date_partition_attempted:
        reasons.append(
            "risk_lifecycle_delivery_date_partition_check_attempted"
        )
    if date_partition_succeeded:
        reasons.append(
            "risk_lifecycle_delivery_date_partition_check_succeeded"
        )
    if date_partition_unverified:
        reasons.append(
            "risk_lifecycle_delivery_date_partition_unverified"
        )
    if source_terminal_status == OfficialRiskSourceStatus.SOURCE_FAILED:
        status = LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED
    elif (
        source_terminal_status == OfficialRiskSourceStatus.SOURCE_UNVERIFIED
        or pagination_blocked
    ):
        status = LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED
    if budget_exhausted:
        status = LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED
        reasons.append("risk_lifecycle_delivery_page_budget_exhausted")
    if pagination_blocked:
        reasons.append("risk_lifecycle_delivery_pagination_unverified")
    if (
        date_partition_unverified
        and source_terminal_status
        != OfficialRiskSourceStatus.SOURCE_FAILED
    ):
        status = LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED

    return _result(
        status=status,
        real_poc_status=_real_poc_status(
            transport=transport,
            status=status,
        ),
        plan=plan,
        as_of=collected_at,
        reasons=reasons,
        batches=batches,
        request_count=request_count,
        candidate_scope_count=len(input_value.candidate_scopes),
        shard_count=len(scope_shards),
        lifecycle_result=lifecycle,
    )
