"""候选全集官方主营材料发现与人工复核清单编排。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Callable, Mapping, Optional, Tuple

from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueStatus,
    build_leader_business_material_review_queue,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_business_official import (
    CninfoBusinessMaterialQuery,
    fetch_cninfo_business_materials,
)
from radar.sources.leader_risk_official import CninfoRiskIssuerScope


MAXIMUM_BUSINESS_MATERIAL_FETCH_WORKERS = 8


def _blocked(
    candidate_plan: Any,
    reason: str,
) -> LeaderBusinessMaterialReviewQueue:
    return LeaderBusinessMaterialReviewQueue(
        status=LeaderBusinessMaterialReviewQueueStatus.BLOCKED,
        candidate_plan_id=(
            candidate_plan.candidate_set_id
            if isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
            else ""
        ),
        candidate_count=(
            candidate_plan.candidate_count
            if isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
            else 0
        ),
        reasons=(reason,),
    )


def collect_leader_business_material_review_queue(
    candidate_plan: Any,
    *,
    issuer_scopes: Any,
    window_from: Any,
    transport: Optional[Callable[..., Mapping[str, Any]]] = None,
    clock: Optional[Callable[[], datetime]] = None,
    max_workers: int = MAXIMUM_BUSINESS_MATERIAL_FETCH_WORKERS,
) -> LeaderBusinessMaterialReviewQueue:
    """每候选只查询一次，完整保留来源失败和缺失语义。"""

    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or not isinstance(issuer_scopes, tuple)
        or any(type(scope) is not CninfoRiskIssuerScope for scope in issuer_scopes)
        or not isinstance(window_from, date)
        or isinstance(window_from, datetime)
        or window_from > candidate_plan.as_of.date()
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or not 1 <= max_workers <= MAXIMUM_BUSINESS_MATERIAL_FETCH_WORKERS
    ):
        return _blocked(
            candidate_plan,
            "business_material_batch_collection_contract_unverified",
        )
    expected = tuple(item.symbol for item in candidate_plan.items)
    actual = tuple(scope.symbol for scope in issuer_scopes)
    if (
        len(actual) != len(set(actual))
        or set(actual) != set(expected)
    ):
        return _blocked(
            candidate_plan,
            "business_material_batch_collection_scope_mismatch",
        )
    scopes_by_symbol = {scope.symbol: scope for scope in issuer_scopes}
    queries = tuple(
        CninfoBusinessMaterialQuery(
            candidate_plan_id=candidate_plan.candidate_set_id,
            scope=scopes_by_symbol[symbol],
            window_from=window_from,
            window_until=candidate_plan.as_of.date(),
        )
        for symbol in expected
    )
    results = {}

    def collect(query: CninfoBusinessMaterialQuery):
        return fetch_cninfo_business_materials(
            query,
            transport=transport,
            clock=clock,
        )

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(queries))
    ) as pool:
        futures = {pool.submit(collect, query): query for query in queries}
        for future in as_completed(futures):
            query = futures[future]
            results[query.scope.symbol] = future.result()
    return build_leader_business_material_review_queue(
        candidate_plan,
        tuple(results[symbol] for symbol in expected),
    )
