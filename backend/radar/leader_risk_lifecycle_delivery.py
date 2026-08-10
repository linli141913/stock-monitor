"""阶段6风险官方发现与人工生命周期的受控单次交付入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
    pagination_blocked = False
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

    def fetch(query: CninfoRiskDiscoveryQuery) -> Optional[
        OfficialRiskDiscoveryBatch
    ]:
        nonlocal request_count, unexpected_source_failure
        request_count += 1
        try:
            return fetch_cninfo_risk_discovery(
                query,
                fetched_at=collected_at,
                transport=transport,
            )
        except Exception:
            unexpected_source_failure = True
            return None

    for shard_index, scope_shard in enumerate(scope_shards):
        for category, search_key in CANONICAL_DISCOVERY_SEARCH_KEYS.items():
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
        if source_terminal_status is not None or pagination_blocked:
            break

    if source_terminal_status is None and not pagination_blocked and (
        len(expected_pages)
        == len(CANONICAL_DISCOVERY_SEARCH_KEYS) * len(scope_shards)
    ):
        highest_page = max(expected_pages.values(), default=0)
        budget_exhausted = False
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
    else:
        budget_exhausted = False

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
