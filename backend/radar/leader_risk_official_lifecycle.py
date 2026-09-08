"""阶段6官方风险开放事件的跨窗口生命周期审计。

本模块只接纳能够完整重放的既有 D8/D9 基线，以及由官方确定性风险
生产者生成的连续窗口。D2 发现和关键词零命中不构成正式覆盖证明；当前
合同因此只审计已知开放事件是否被连续结转，不打开风险过滤或状态迁移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactResult,
    RiskDocumentFact,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
)
from radar.leader_risk_invalidation_features import (
    CLOSING_RESOLUTION_KINDS,
    LeaderRiskEventEvidence,
    LeaderRiskResolutionEvidence,
    RiskCategory,
    RiskOfficialStatus,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchInput,
    LeaderRiskLifecycleBatchResult,
    build_leader_risk_lifecycle_batch,
)
from radar.leader_risk_official_deterministic import (
    LEADER_OFFICIAL_RISK_LIFECYCLE_CONTRACT_ID,
    LeaderOfficialDeterministicRiskBatchResult,
    _bind_leader_official_deterministic_risk_lifecycle,
    is_leader_official_deterministic_risk_batch_valid,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


UTC = timezone.utc
_OFFICIAL_RISK_LIFECYCLE_PRODUCER_TOKEN = object()


class LeaderOfficialRiskLifecycleStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True, repr=False)
class LeaderOfficialRiskLifecycleWindowEntry:
    symbol: str
    issuer_listed_at: datetime
    events: Any = field(repr=False)
    resolutions: Any = field(repr=False)
    document_fact_results: Any = field(repr=False)
    relations: Any = field(repr=False)


@dataclass(frozen=True, repr=False)
class LeaderOfficialRiskLifecycleWindow:
    candidate_plan: Any = field(repr=False)
    official_batch: Any = field(repr=False)
    entries: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderOfficialRiskLifecycleRelation:
    relation_kind: RiskDocumentRelationKind
    source_document_id: str
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    effective_from: datetime


@dataclass(frozen=True)
class LeaderOfficialRiskLifecycleItem:
    index: int
    symbol: str
    issuer_identity: Optional[str]
    status: ResearchFeatureStatus
    window_count: int
    reasons: Tuple[str, ...] = ()
    active_risk_categories: Tuple[RiskCategory, ...] = ()
    coverage_complete: bool = False
    open_event_carry_forward_complete: bool = False
    correction_links_complete: bool = False
    resolution_links_complete: bool = False
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "status": self.status.value,
            "windowCount": self.window_count,
            "reasons": list(self.reasons),
            "activeRiskCategories": [
                value.value for value in self.active_risk_categories
            ],
            "coverageComplete": False,
            "openEventCarryForwardComplete": (
                self.open_event_carry_forward_complete
            ),
            "correctionLinksComplete": self.correction_links_complete,
            "resolutionLinksComplete": self.resolution_links_complete,
            "gate": {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True)
class LeaderOfficialRiskLifecycleResult:
    status: LeaderOfficialRiskLifecycleStatus
    radar_run_id: Optional[str]
    candidate_plan_id: Optional[str]
    as_of: Optional[datetime]
    items: Tuple[LeaderOfficialRiskLifecycleItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_OFFICIAL_RISK_LIFECYCLE_CONTRACT_ID
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False
    _producer_token: Any = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _failed(reason: str) -> LeaderOfficialRiskLifecycleResult:
    result = LeaderOfficialRiskLifecycleResult(
        status=LeaderOfficialRiskLifecycleStatus.SOURCE_UNVERIFIED,
        radar_run_id=None,
        candidate_plan_id=None,
        as_of=None,
        reasons=(reason,),
    )
    object.__setattr__(
        result,
        "_producer_token",
        _OFFICIAL_RISK_LIFECYCLE_PRODUCER_TOKEN,
    )
    return result


def _baseline_events(
    baseline_input: LeaderRiskLifecycleBatchInput,
    baseline_result: LeaderRiskLifecycleBatchResult,
) -> Optional[Mapping[str, Tuple[LeaderRiskEventEvidence, ...]]]:
    replayed = build_leader_risk_lifecycle_batch(baseline_input)
    if replayed != baseline_result:
        return None
    values = {}
    for entry, item in zip(baseline_input.entries, replayed.items):
        versions = getattr(entry, "versions", None)
        if (
            item.status != ResearchFeatureStatus.READY
            or not isinstance(versions, tuple)
            or not versions
        ):
            values[entry.symbol] = ()
            continue
        events = getattr(versions[-1], "event_versions", None)
        if (
            not isinstance(events, tuple)
            or any(
                type(event) is not LeaderRiskEventEvidence
                for event in events
            )
        ):
            return None
        values[entry.symbol] = events
    return values


def _same_open_event(
    previous: LeaderRiskEventEvidence,
    current: LeaderRiskEventEvidence,
) -> bool:
    return bool(
        current == previous
        and current.official_status == RiskOfficialStatus.ACTIVE
    )


def _same_event_lineage(
    previous: LeaderRiskEventEvidence,
    current: LeaderRiskEventEvidence,
) -> bool:
    return all(
        getattr(previous, name) == getattr(current, name)
        for name in (
            "event_id",
            "symbol",
            "issuer_identity",
            "category",
            "event_subtype",
            "case_id",
            "source_kind",
            "effective_from",
            "effective_until",
            "reporting_period",
        )
    )


def _facts_by_document(
    entry: LeaderOfficialRiskLifecycleWindowEntry,
    projection: Any,
) -> Optional[Mapping[str, OfficialRiskDocumentFactResult]]:
    results = entry.document_fact_results
    if not isinstance(results, tuple) or any(
        type(result) is not OfficialRiskDocumentFactResult
        for result in results
    ):
        return None
    by_document = {}
    for result in results:
        if (
            result.status != ResearchFeatureStatus.READY
            or result.reasons
            not in {(), ("risk_document_relation_missing",)}
            or not isinstance(result.document_id, str)
            or result.document_id in by_document
            or not isinstance(result.content_sha256, str)
            or not isinstance(result.facts, tuple)
            or any(type(fact) is not RiskDocumentFact for fact in result.facts)
        ):
            return None
        try:
            document_index = projection.document_ids.index(
                result.document_id
            )
        except ValueError:
            return None
        if (
            document_index >= len(projection.content_sha256s)
            or projection.content_sha256s[document_index]
            != result.content_sha256
        ):
            return None
        by_document[result.document_id] = result
    projected_fact_ids = tuple(projection.deterministic_fact_ids)
    supplied_fact_ids = tuple(
        fact.fact_id
        for document_id in projection.document_ids
        if document_id in by_document
        for fact in by_document[document_id].facts
    )
    if results and supplied_fact_ids != projected_fact_ids:
        return None
    return by_document


def _relation_basis_valid(
    relation: LeaderOfficialRiskLifecycleRelation,
    *,
    target: LeaderRiskEventEvidence,
    facts: OfficialRiskDocumentFactResult,
) -> bool:
    facts_by_id = {fact.fact_id: fact for fact in facts.facts}
    if (
        not isinstance(relation.basis_fact_ids, tuple)
        or not relation.basis_fact_ids
        or len(relation.basis_fact_ids)
        != len(set(relation.basis_fact_ids))
        or any(fact_id not in facts_by_id for fact_id in relation.basis_fact_ids)
    ):
        return False
    selected = tuple(
        facts_by_id[fact_id] for fact_id in relation.basis_fact_ids
    )
    if not any(
        fact.fact_kind == RiskDocumentFactKind.REFERENCED_DOCUMENT_ID
        and fact.normalized_value == target.document_id
        for fact in selected
    ):
        return False
    if target.category in {RiskCategory.INVESTIGATION, RiskCategory.LITIGATION}:
        return isinstance(target.case_id, str) and any(
            fact.fact_kind == RiskDocumentFactKind.CASE_ID
            and fact.normalized_value == target.case_id
            for fact in selected
        )
    if target.category in {RiskCategory.EARNINGS, RiskCategory.AUDIT}:
        return isinstance(target.reporting_period, str) and any(
            fact.fact_kind == RiskDocumentFactKind.REPORTING_PERIOD
            and fact.normalized_value == target.reporting_period
            for fact in selected
        )
    return True


def _validate_relations(
    *,
    previous_events: Tuple[LeaderRiskEventEvidence, ...],
    entry: LeaderOfficialRiskLifecycleWindowEntry,
    projection: Any,
    as_of: datetime,
) -> Optional[str]:
    relations = entry.relations
    if not isinstance(relations, tuple) or any(
        type(relation) is not LeaderOfficialRiskLifecycleRelation
        for relation in relations
    ):
        return "risk_official_relation_contract_unverified"
    facts_by_document = _facts_by_document(entry, projection)
    if facts_by_document is None:
        return "risk_official_relation_document_facts_unverified"
    previous_by_key = {
        (event.event_id, event.event_version): event
        for event in previous_events
    }
    current_by_key = {
        (event.event_id, event.event_version): event
        for event in entry.events
    }
    resolutions_by_target = {
        (resolution.target_event_id, resolution.target_event_version): resolution
        for resolution in entry.resolutions
    }
    if len(resolutions_by_target) != len(entry.resolutions):
        return "risk_official_resolution_identity_duplicate"
    for relation in relations:
        target = previous_by_key.get((
            relation.target_event_id,
            relation.target_event_version,
        ))
        if target is None:
            if any(
                event.event_id == relation.target_event_id
                for event in previous_events
            ):
                return "risk_official_relation_target_version_unverified"
            return "risk_official_relation_target_unverified"
        if relation.target_document_id != target.document_id:
            return "risk_official_relation_target_document_unverified"
        facts = facts_by_document.get(relation.source_document_id)
        if facts is None:
            return "risk_official_relation_document_facts_unverified"
        try:
            source_index = projection.document_ids.index(
                relation.source_document_id
            )
        except ValueError:
            return "risk_official_relation_source_document_unverified"
        effective_from = _aware(relation.effective_from)
        published_at = _aware(projection.document_published_at[source_index])
        if (
            effective_from is None
            or published_at is None
            or effective_from < published_at
            or effective_from > as_of
        ):
            return "risk_official_relation_time_order_unverified"
        if not _relation_basis_valid(
            relation,
            target=target,
            facts=facts,
        ):
            return "risk_official_relation_basis_unverified"
        if relation.relation_kind == RiskDocumentRelationKind.SUPERSEDES:
            replacement = current_by_key.get((
                target.event_id,
                relation.replacement_event_version,
            ))
            if (
                not isinstance(relation.replacement_event_version, str)
                or relation.replacement_event_version
                == target.event_version
                or replacement is None
                or replacement.document_id != relation.source_document_id
                or replacement.source_url
                != projection.document_urls[source_index]
                or replacement.official_status != RiskOfficialStatus.ACTIVE
                or not _same_event_lineage(target, replacement)
                or _aware(replacement.published_at) != published_at
                or published_at < _aware(target.published_at)
            ):
                return "risk_official_correction_replacement_unverified"
        elif relation.relation_kind == RiskDocumentRelationKind.RESOLVES:
            if relation.replacement_event_version is not None:
                return "risk_official_resolution_replacement_unexpected"
            resolution = resolutions_by_target.get((
                target.event_id,
                target.event_version,
            ))
            if (
                resolution is None
                or resolution.symbol != target.symbol
                or resolution.issuer_identity != target.issuer_identity
                or resolution.document_id != relation.source_document_id
                or resolution.source_url
                != projection.document_urls[source_index]
                or resolution.resolution_kind
                not in CLOSING_RESOLUTION_KINDS
                or _aware(resolution.published_at) != published_at
                or _aware(resolution.effective_from) != effective_from
                or published_at < _aware(target.published_at)
            ):
                return (
                    "risk_official_resolution_identity_unverified"
                    if resolution is not None
                    and (
                        resolution.symbol != target.symbol
                        or resolution.issuer_identity
                        != target.issuer_identity
                        or resolution.document_id
                        != relation.source_document_id
                        or resolution.source_url
                        != projection.document_urls[source_index]
                    )
                    else "risk_official_resolution_evidence_unverified"
                )
        else:
            return "risk_official_relation_kind_unverified"
    if any(
        not any(
            relation.relation_kind == RiskDocumentRelationKind.RESOLVES
            and relation.target_event_id == resolution.target_event_id
            and relation.target_event_version
            == resolution.target_event_version
            and relation.source_document_id == resolution.document_id
            for relation in relations
        )
        for resolution in entry.resolutions
    ):
        return "risk_official_resolution_relation_missing"
    return None


def _item_status(
    *,
    index: int,
    symbol: str,
    issuer_identity: Optional[str],
    window_count: int,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    active_categories: Sequence[RiskCategory] = (),
    carry_complete: bool = False,
    correction_complete: bool = False,
    resolution_complete: bool = False,
) -> LeaderOfficialRiskLifecycleItem:
    return LeaderOfficialRiskLifecycleItem(
        index=index,
        symbol=symbol,
        issuer_identity=issuer_identity,
        status=status,
        window_count=window_count,
        reasons=_dedupe(reasons),
        active_risk_categories=tuple(active_categories),
        open_event_carry_forward_complete=carry_complete,
        correction_links_complete=correction_complete,
        resolution_links_complete=resolution_complete,
    )


def build_leader_official_risk_lifecycle(
    *,
    baseline_input: Any,
    baseline_result: Any,
    windows: Any,
) -> LeaderOfficialRiskLifecycleResult:
    """审计已知 D8 开放事件的连续官方窗口；D2 本身不提升覆盖。"""

    if (
        type(baseline_input) is not LeaderRiskLifecycleBatchInput
        or type(baseline_result) is not LeaderRiskLifecycleBatchResult
    ):
        return _failed("risk_official_d8_baseline_unverified")
    baseline_events = _baseline_events(baseline_input, baseline_result)
    if baseline_events is None:
        return _failed("risk_official_d8_baseline_unverified")
    if (
        not isinstance(windows, tuple)
        or len(windows) < 2
        or any(type(value) is not LeaderOfficialRiskLifecycleWindow for value in windows)
    ):
        return _failed("risk_official_lifecycle_window_contract_unverified")

    normalized_as_of = []
    previous_until = None
    expected_symbols = None
    for window in windows:
        plan = window.candidate_plan
        batch = window.official_batch
        if (
            type(plan) is not LeaderRuntimeCandidatePlan
            or not is_leader_runtime_candidate_plan_valid(plan)
            or type(batch) is not LeaderOfficialDeterministicRiskBatchResult
            or not is_leader_official_deterministic_risk_batch_valid(
                batch,
                candidate_plan=plan,
            )
            or not isinstance(window.entries, tuple)
        ):
            return _failed("risk_official_lifecycle_window_unverified")
        symbols = tuple(item.symbol for item in plan.items)
        if (
            len(window.entries) != len(symbols)
            or tuple(getattr(item, "symbol", None) for item in window.entries)
            != symbols
            or any(
                type(item) is not LeaderOfficialRiskLifecycleWindowEntry
                for item in window.entries
            )
        ):
            return _failed("risk_official_lifecycle_candidate_mismatch")
        if expected_symbols is None:
            expected_symbols = symbols
        elif symbols != expected_symbols:
            return _failed("risk_official_lifecycle_candidate_mismatch")
        current_as_of = _aware(plan.as_of)
        if (
            current_as_of is None
            or (
                normalized_as_of
                and current_as_of <= normalized_as_of[-1]
            )
        ):
            return _failed("risk_official_lifecycle_window_order_unverified")
        normalized_as_of.append(current_as_of)
        if (
            previous_until is not None
            and batch.window_from > previous_until + timedelta(days=1)
        ):
            return _failed("risk_official_lifecycle_window_discontinuous")
        previous_until = batch.window_until

    assert expected_symbols is not None
    latest_plan = windows[-1].candidate_plan
    items = []
    for index, symbol in enumerate(expected_symbols):
        entries = tuple(window.entries[index] for window in windows)
        listed_times = tuple(_aware(entry.issuer_listed_at) for entry in entries)
        if (
            any(value is None for value in listed_times)
            or len(set(listed_times)) != 1
            or any(
                not isinstance(entry.events, tuple)
                or not isinstance(entry.resolutions, tuple)
                or not isinstance(entry.document_fact_results, tuple)
                or not isinstance(entry.relations, tuple)
                or any(
                    type(event) is not LeaderRiskEventEvidence
                    for event in entry.events
                )
                or any(
                    type(resolution) is not LeaderRiskResolutionEvidence
                    for resolution in entry.resolutions
                )
                for entry in entries
            )
        ):
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=None,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_official_lifecycle_entry_unverified",),
            ))
            continue
        issuer_identities = tuple(
            window.official_batch.projection_batch.items[
                index
            ].projection.issuer_identity
            for window in windows
        )
        issuer_identity = issuer_identities[-1]
        if len(set(issuer_identities)) != 1:
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_official_lifecycle_issuer_identity_unverified",
                ),
            ))
            continue
        if listed_times[0] > normalized_as_of[-1]:
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_official_lifecycle_listing_identity_unverified",
                ),
            ))
            continue
        first_window_start = datetime.combine(
            windows[0].official_batch.window_from,
            datetime.min.time(),
            tzinfo=UTC,
        )
        if listed_times[0] < first_window_start:
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_official_lifecycle_listing_history_incomplete",
                ),
            ))
            continue
        known_events = baseline_events.get(symbol, ())
        if not known_events:
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.MISSING,
                reasons=("risk_official_d8_baseline_missing",),
            ))
            continue
        if any(
            event.symbol != symbol
            or event.issuer_identity != issuer_identity
            for event in known_events
        ):
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_official_lifecycle_issuer_identity_unverified",
                ),
            ))
            continue
        previous_events = known_events
        closed_event_versions = set()
        failed_reason = None
        for window, entry in zip(windows, entries):
            projection = window.official_batch.projection_batch.items[
                index
            ].projection
            relation_reason = _validate_relations(
                previous_events=previous_events,
                entry=entry,
                projection=projection,
                as_of=window.candidate_plan.as_of,
            )
            if relation_reason is not None:
                failed_reason = relation_reason
                break
            current_by_id = {
                event.event_id: event for event in entry.events
            }
            if len(current_by_id) != len(entry.events):
                failed_reason = "risk_official_event_identity_duplicate"
                break
            for event in previous_events:
                event_key = (event.event_id, event.event_version)
                if (
                    event.official_status != RiskOfficialStatus.ACTIVE
                    or event_key in closed_event_versions
                ):
                    continue
                current = current_by_id.get(event.event_id)
                correction = next((
                    relation
                    for relation in entry.relations
                    if relation.relation_kind
                    == RiskDocumentRelationKind.SUPERSEDES
                    and relation.target_event_id == event.event_id
                    and relation.target_event_version
                    == event.event_version
                    and relation.replacement_event_version
                    == getattr(current, "event_version", None)
                ), None)
                resolution = next((
                    relation
                    for relation in entry.relations
                    if relation.relation_kind
                    == RiskDocumentRelationKind.RESOLVES
                    and relation.target_event_id == event.event_id
                    and relation.target_event_version
                    == event.event_version
                ), None)
                if (
                    current is None
                    or not (
                        _same_open_event(event, current)
                        or correction is not None
                    )
                    or (
                        resolution is not None
                        and not _same_open_event(event, current)
                    )
                ):
                    failed_reason = (
                        "risk_official_open_event_not_carried_forward"
                    )
                    break
            if failed_reason is not None:
                break
            closed_event_versions.update(
                (
                    resolution.target_event_id,
                    resolution.target_event_version,
                )
                for resolution in entry.resolutions
                if resolution.resolution_kind
                in CLOSING_RESOLUTION_KINDS
            )
            previous_events = entry.events
        if failed_reason is not None:
            items.append(_item_status(
                index=index,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_count=len(windows),
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(failed_reason,),
            ))
            continue
        active = tuple(dict.fromkeys(
            event.category
            for event in entries[-1].events
            if event.official_status == RiskOfficialStatus.ACTIVE
            and (event.event_id, event.event_version)
            not in closed_event_versions
        ))
        items.append(_item_status(
            index=index,
            symbol=symbol,
            issuer_identity=issuer_identity,
            window_count=len(windows),
            status=ResearchFeatureStatus.READY,
            reasons=("risk_official_formal_coverage_not_proven",),
            active_categories=active,
            carry_complete=True,
            correction_complete=True,
            resolution_complete=True,
        ))

    ready_count = sum(
        item.status == ResearchFeatureStatus.READY for item in items
    )
    if ready_count == len(items):
        status = LeaderOfficialRiskLifecycleStatus.READY
        reasons = ("risk_official_formal_coverage_not_proven",)
    elif ready_count:
        status = LeaderOfficialRiskLifecycleStatus.PARTIAL
        reasons = ("risk_official_lifecycle_partial",)
    elif any(
        item.status == ResearchFeatureStatus.SOURCE_UNVERIFIED
        for item in items
    ):
        status = LeaderOfficialRiskLifecycleStatus.SOURCE_UNVERIFIED
        reasons = _dedupe(
            reason for item in items for reason in item.reasons
        )
    else:
        status = LeaderOfficialRiskLifecycleStatus.MISSING
        reasons = ("risk_official_lifecycle_no_ready_items",)
    result = LeaderOfficialRiskLifecycleResult(
        status=status,
        radar_run_id=latest_plan.radar_run_id,
        candidate_plan_id=latest_plan.candidate_set_id,
        as_of=latest_plan.as_of,
        items=tuple(items),
        reasons=reasons,
    )
    object.__setattr__(
        result,
        "_producer_token",
        _OFFICIAL_RISK_LIFECYCLE_PRODUCER_TOKEN,
    )
    return result


def is_leader_official_risk_lifecycle_valid(
    value: Any,
    *,
    candidate_plan: Any,
) -> bool:
    if (
        type(value) is not LeaderOfficialRiskLifecycleResult
        or value._producer_token
        is not _OFFICIAL_RISK_LIFECYCLE_PRODUCER_TOKEN
        or value.contract_id != LEADER_OFFICIAL_RISK_LIFECYCLE_CONTRACT_ID
        or value.status
        not in {
            LeaderOfficialRiskLifecycleStatus.READY,
            LeaderOfficialRiskLifecycleStatus.PARTIAL,
        }
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or value.radar_run_id != candidate_plan.radar_run_id
        or value.candidate_plan_id != candidate_plan.candidate_set_id
        or _aware(value.as_of) != candidate_plan.as_of
        or len(value.items) != candidate_plan.candidate_count
        or any((
            value.risk_filter_passed is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    for index, (item, plan_item) in enumerate(zip(
        value.items,
        candidate_plan.items,
    )):
        if (
            type(item) is not LeaderOfficialRiskLifecycleItem
            or item.index != index
            or item.symbol != plan_item.symbol
            or not isinstance(item.status, ResearchFeatureStatus)
            or not isinstance(item.window_count, int)
            or item.window_count < 2
            or not isinstance(item.reasons, tuple)
            or any(not isinstance(reason, str) or not reason for reason in item.reasons)
            or any((
                item.coverage_complete is not False,
                item.risk_filter_passed is not False,
                item.formal_gate_ready is not False,
                item.formal_usable is not False,
                item.state_transition_allowed is not False,
            ))
        ):
            return False
        if item.status == ResearchFeatureStatus.READY:
            if (
                item.reasons
                != ("risk_official_formal_coverage_not_proven",)
                or not all((
                    item.open_event_carry_forward_complete,
                    item.correction_links_complete,
                    item.resolution_links_complete,
                ))
            ):
                return False
        elif any((
            item.open_event_carry_forward_complete,
            item.correction_links_complete,
            item.resolution_links_complete,
        )):
            return False
    ready_count = sum(
        item.status == ResearchFeatureStatus.READY for item in value.items
    )
    expected_status = (
        LeaderOfficialRiskLifecycleStatus.READY
        if ready_count == len(value.items)
        else LeaderOfficialRiskLifecycleStatus.PARTIAL
    )
    expected_reasons = (
        ("risk_official_formal_coverage_not_proven",)
        if expected_status == LeaderOfficialRiskLifecycleStatus.READY
        else ("risk_official_lifecycle_partial",)
    )
    return bool(
        ready_count
        and value.status == expected_status
        and value.reasons == expected_reasons
    )


def bind_leader_official_risk_lifecycle_evidence(
    official_batch: Any,
    *,
    lifecycle: Any,
    candidate_plan: Any,
) -> LeaderOfficialDeterministicRiskBatchResult:
    """把可信生命周期完成项写回当前官方投影的审计字段。"""

    if (
        not is_leader_official_risk_lifecycle_valid(
            lifecycle,
            candidate_plan=candidate_plan,
        )
        or not is_leader_official_deterministic_risk_batch_valid(
            official_batch,
            candidate_plan=candidate_plan,
        )
    ):
        raise ValueError("risk_official_lifecycle_binding_unverified")
    completed_symbols = tuple(
        item.symbol
        for item in lifecycle.items
        if item.status == ResearchFeatureStatus.READY
    )
    return _bind_leader_official_deterministic_risk_lifecycle(
        official_batch,
        candidate_plan=candidate_plan,
        completed_symbols=completed_symbols,
    )
