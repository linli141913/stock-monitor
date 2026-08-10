"""阶段6风险官方发现到连续人工审核证据包的纯内存编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskEvidenceBundleBatchEntry,
    build_leader_risk_projection_batch_from_bundles,
)
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactResult,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
)
from radar.leader_risk_evidence_bundle import (
    RiskResearchEvidenceBundle,
    RiskResearchEvidenceBundleInput,
    build_risk_research_evidence_bundle,
)
from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    LeaderRiskEventEvidence,
    MAXIMUM_COVERAGE_AGE_SECONDS,
    RiskCategory,
    RiskOfficialStatus,
)
from radar.leader_risk_review_artifacts import (
    AcceptedManualRiskReviewArtifact,
    ManualRiskReviewArtifactInput,
    ManualRiskReviewSubmission,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_supplemented_relation import (
    SupplementedRiskDocumentRelationInput,
    review_supplemented_risk_document_relation,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_risk_candidate_discovery import (
    LeaderRiskOfficialCandidateDiscoveryResult,
    LeaderRiskOfficialCandidateDiscoveryStatus,
    build_leader_risk_official_candidate_discovery_batch,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RiskDocumentReviewCandidate,
)
from radar.sources.leader_risk_official import (
    MAXIMUM_CANDIDATE_SCOPE_COUNT,
    OfficialRiskDiscoveryBatch,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
)


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STAGE6_SYMBOL_PATTERN = re.compile(r"[036][0-9]{5}")
LEADER_RISK_LIFECYCLE_BATCH_CONTRACT_ID = (
    "radar-leader-risk-lifecycle-batch-v1"
)
CANONICAL_DISCOVERY_SEARCH_KEYS = {
    RiskCategory.REDUCTION: "减持计划",
    RiskCategory.UNLOCK: "解除限售",
    RiskCategory.REGULATORY: "监管措施决定书",
    RiskCategory.INVESTIGATION: "立案告知书",
    RiskCategory.LITIGATION: "重大诉讼",
    RiskCategory.EARNINGS: "业绩预告",
    RiskCategory.AUDIT: "审计报告",
}


class LeaderRiskLifecycleBatchStatus(str, Enum):
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRiskLifecycleReviewVersion:
    as_of: Any
    document: Any = field(repr=False)
    content: Any = field(repr=False)
    facts: Any = field(repr=False)
    candidate: Any = field(repr=False)
    event_versions: Any = field(repr=False)
    submission: Any = field(repr=False)
    relation_review: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderRiskLifecycleBatchEntry:
    symbol: str
    issuer_identity: Optional[str]
    versions: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderRiskLifecycleBatchInput:
    candidate_plan: Any = field(repr=False)
    discovery_batches: Any = field(repr=False)
    entries: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderRiskLifecycleBatchItem:
    index: int
    symbol: str
    issuer_identity: Optional[str]
    status: ResearchFeatureStatus
    version_count: int
    bundle_ids: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    open_events_continuous: bool = False
    correction_links_consistent: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "status": self.status.value,
            "versionCount": self.version_count,
            "bundleIds": list(self.bundle_ids),
            "reasons": list(self.reasons),
            "submittedOpenEventsContinuous": self.open_events_continuous,
            "submittedCorrectionLinksConsistent": (
                self.correction_links_consistent
            ),
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


@dataclass(frozen=True)
class LeaderRiskLifecycleBatchResult:
    status: LeaderRiskLifecycleBatchStatus
    candidate_plan_id: Optional[str]
    radar_run_id: Optional[str]
    quote_batch_id: Optional[str]
    as_of: Optional[datetime]
    candidate_count: int
    items: Tuple[LeaderRiskLifecycleBatchItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    discovery_result: Optional[
        LeaderRiskOfficialCandidateDiscoveryResult
    ] = field(default=None, repr=False)
    projection_batch: Optional[
        LeaderRiskCandidateProjectionBatchResult
    ] = field(default=None, repr=False)
    query_categories_complete: bool = False
    query_pages_complete: bool = False
    query_window_continuous: bool = False
    coverage_complete: bool = False
    contract_id: str = LEADER_RISK_LIFECYCLE_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "radarRunId": self.radar_run_id,
            "quoteBatchId": self.quote_batch_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "queryAudit": {
                "categoriesComplete": self.query_categories_complete,
                "pagesComplete": self.query_pages_complete,
                "windowContinuous": self.query_window_continuous,
                "formalCoverageComplete": False,
            },
            "items": [item.to_evidence() for item in self.items],
            "projectionStatus": (
                self.projection_batch.status.value
                if self.projection_batch is not None
                else None
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


def _result(
    *,
    status: LeaderRiskLifecycleBatchStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    reasons: Sequence[str],
    items: Sequence[LeaderRiskLifecycleBatchItem] = (),
    discovery_result: Optional[
        LeaderRiskOfficialCandidateDiscoveryResult
    ] = None,
    projection_batch: Optional[
        LeaderRiskCandidateProjectionBatchResult
    ] = None,
    query_categories_complete: bool = False,
    query_pages_complete: bool = False,
    query_window_continuous: bool = False,
) -> LeaderRiskLifecycleBatchResult:
    return LeaderRiskLifecycleBatchResult(
        status=status,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        radar_run_id=plan.radar_run_id if plan is not None else None,
        quote_batch_id=plan.quote_batch_id if plan is not None else None,
        as_of=plan.as_of if plan is not None else None,
        candidate_count=(plan.candidate_count if plan is not None else 0),
        items=tuple(items),
        reasons=_dedupe(reasons),
        discovery_result=discovery_result,
        projection_batch=projection_batch,
        query_categories_complete=query_categories_complete,
        query_pages_complete=query_pages_complete,
        query_window_continuous=query_window_continuous,
    )


def _query_audit(
    batches: Tuple[OfficialRiskDiscoveryBatch, ...],
    *,
    as_of: datetime,
    expected_symbols: Tuple[str, ...],
    expected_candidate_plan_id: str,
) -> Tuple[bool, bool, bool]:
    queries = tuple(getattr(batch, "query", None) for batch in batches)
    categories_complete = (
        {getattr(query, "candidate_category", None) for query in queries}
        == set(ALL_RISK_CATEGORIES)
    )
    groups = {}
    windows = set()
    query_contract_valid = True
    expected_symbol_shards = tuple(
        expected_symbols[index:index + MAXIMUM_CANDIDATE_SCOPE_COUNT]
        for index in range(
            0,
            len(expected_symbols),
            MAXIMUM_CANDIDATE_SCOPE_COUNT,
        )
    )
    canonical_scope_shards = {}
    category_first_page_shards = {}
    for batch in batches:
        query = getattr(batch, "query", None)
        if query is None:
            return categories_complete, False, False
        scopes = getattr(query, "candidate_scopes", None)
        if not isinstance(scopes, tuple):
            return categories_complete, False, False
        scope_identity = tuple(
            (
                getattr(scope, "symbol", None),
                getattr(scope, "issuer_identity", None),
                _aware_utc(getattr(scope, "resolved_at", None)),
            )
            for scope in scopes
        )
        if scopes:
            scope_symbols = tuple(value[0] for value in scope_identity)
            try:
                shard_index = expected_symbol_shards.index(scope_symbols)
            except ValueError:
                query_contract_valid = False
                shard_index = None
            if shard_index is not None:
                if (
                    query.candidate_plan_id
                    != expected_candidate_plan_id
                    or query.shard_index != shard_index
                    or query.shard_count != len(expected_symbol_shards)
                ):
                    query_contract_valid = False
                canonical = canonical_scope_shards.get(shard_index)
                if canonical is None:
                    canonical_scope_shards[shard_index] = scope_identity
                elif canonical != scope_identity:
                    query_contract_valid = False
                if query.page_number == 1:
                    category_first_page_shards.setdefault(
                        query.candidate_category,
                        [],
                    ).append(shard_index)
        else:
            query_contract_valid = False
        expected_search_key = CANONICAL_DISCOVERY_SEARCH_KEYS.get(
            query.candidate_category
        )
        if (
            expected_search_key is None
            or query.search_key.strip() != expected_search_key
            or not isinstance(query.page_size, int)
            or query.page_size <= 0
        ):
            query_contract_valid = False
        key = (
            query.search_key,
            query.candidate_category,
            query.window_from,
            query.window_until,
            query.page_size,
            scope_identity,
        )
        groups.setdefault(key, []).append(batch)
        windows.add((query.window_from, query.window_until))
    expected_shard_indexes = list(range(len(expected_symbol_shards)))
    if (
        set(canonical_scope_shards) != set(expected_shard_indexes)
        or any(
            indexes != expected_shard_indexes
            for indexes in category_first_page_shards.values()
        )
    ):
        query_contract_valid = False
    expected_group_count = (
        len(ALL_RISK_CATEGORIES) * len(expected_symbol_shards)
    )
    pages_complete = bool(groups) and query_contract_valid and (
        len(groups) == expected_group_count
    )
    category_document_ids = {}
    for grouped in groups.values():
        total_pages_values = {item.total_pages for item in grouped}
        reported_total_pages_values = {
            item.reported_total_pages for item in grouped
        }
        total_records_values = {item.total_records for item in grouped}
        if (
            len(total_pages_values) != 1
            or len(reported_total_pages_values) != 1
            or len(total_records_values) != 1
            or any(
                item.status != OfficialRiskSourceStatus.PARTIAL
                for item in grouped
            )
        ):
            pages_complete = False
            break
        total_pages = next(iter(total_pages_values))
        total_records = next(iter(total_records_values))
        page_size = grouped[0].query.page_size
        if (
            not isinstance(total_pages, int)
            or total_pages < 0
            or not isinstance(total_records, int)
            or total_records < 0
            or not isinstance(page_size, int)
            or page_size <= 0
            or total_pages
            != (
                0
                if total_records == 0
                else (total_records + page_size - 1) // page_size
            )
        ):
            pages_complete = False
            break
        expected_pages = (
            (1,) if total_pages == 0 else tuple(range(1, total_pages + 1))
        )
        ordered = tuple(sorted(
            grouped,
            key=lambda item: item.query.page_number,
        ))
        page_numbers = tuple(item.query.page_number for item in ordered)
        if page_numbers != expected_pages or any(
            item.has_more != (index < len(ordered) - 1)
            for index, item in enumerate(ordered)
        ):
            pages_complete = False
            break
        document_ids = []
        for page_index, item in enumerate(ordered):
            documents = item.documents
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
                not isinstance(documents, tuple)
                or len(documents) != expected_count
                or any(
                    not isinstance(document, OfficialRiskDocumentMetadata)
                    or not isinstance(document.document_id, str)
                    or not document.document_id
                    for document in documents
                )
            ):
                pages_complete = False
                break
            document_ids.extend(document.document_id for document in documents)
        if (
            not pages_complete
            or len(document_ids) != total_records
            or len(document_ids) != len(set(document_ids))
        ):
            pages_complete = False
            break
        category = grouped[0].query.candidate_category
        category_document_ids.setdefault(category, []).extend(document_ids)
    if pages_complete and any(
        len(document_ids) != len(set(document_ids))
        for document_ids in category_document_ids.values()
    ):
        pages_complete = False
    plan_date = as_of.astimezone(SHANGHAI_TZ).date()
    window_continuous = bool(
        len(windows) == 1
        and next(iter(windows))[0] <= plan_date
        and next(iter(windows))[1] >= plan_date
    )
    return categories_complete, pages_complete, window_continuous


def _open_events(
    version: LeaderRiskLifecycleReviewVersion,
) -> Optional[Tuple[LeaderRiskEventEvidence, ...]]:
    as_of = _aware_utc(version.as_of)
    events = version.event_versions
    if as_of is None or not isinstance(events, tuple) or any(
        not isinstance(event, LeaderRiskEventEvidence)
        for event in events
    ):
        return None
    values = tuple(
        event
        for event in events
        if (
            event.official_status == RiskOfficialStatus.ACTIVE
            and _aware_utc(event.effective_from) is not None
            and _aware_utc(event.effective_from) <= as_of
            and (
                event.effective_until is None
                or (
                    _aware_utc(event.effective_until) is not None
                    and _aware_utc(event.effective_until) >= as_of
                )
            )
        )
    )
    event_ids = tuple(event.event_id for event in values)
    return values if len(event_ids) == len(set(event_ids)) else None


def _same_event_identity(
    left: LeaderRiskEventEvidence,
    right: LeaderRiskEventEvidence,
) -> bool:
    return all(
        getattr(left, field_name) == getattr(right, field_name)
        for field_name in (
            "event_id",
            "symbol",
            "issuer_identity",
            "category",
            "event_subtype",
            "case_id",
            "source_kind",
            "effective_from",
            "reporting_period",
        )
    )


def _events_continuous(
    previous: LeaderRiskLifecycleReviewVersion,
    current: LeaderRiskLifecycleReviewVersion,
) -> bool:
    previous_open = _open_events(previous)
    current_events = current.event_versions
    current_as_of = _aware_utc(current.as_of)
    if (
        previous_open is None
        or current_as_of is None
        or not isinstance(current_events, tuple)
        or any(
            not isinstance(event, LeaderRiskEventEvidence)
            for event in current_events
        )
    ):
        return False
    for previous_event in previous_open:
        matches = tuple(
            event
            for event in current_events
            if event.event_id == previous_event.event_id
        )
        if len(matches) != 1:
            return False
        current_event = matches[0]
        effective_from = _aware_utc(current_event.effective_from)
        effective_until = (
            _aware_utc(current_event.effective_until)
            if current_event.effective_until is not None
            else None
        )
        if (
            not _same_event_identity(previous_event, current_event)
            or current_event.official_status != RiskOfficialStatus.ACTIVE
            or effective_from is None
            or effective_from > current_as_of
            or (
                current_event.effective_until is not None
                and effective_until is None
            )
            or (
                effective_until is not None
                and effective_until < current_as_of
            )
        ):
            return False
        if current_event.event_version == previous_event.event_version:
            if current_event != previous_event:
                return False
    return True


def _has_correction_claim(
    version: LeaderRiskLifecycleReviewVersion,
) -> bool:
    review = version.relation_review
    events = version.event_versions
    return bool(
        isinstance(review, RiskDocumentVersionReview)
        and review.relation_kind == RiskDocumentRelationKind.SUPERSEDES
    ) or bool(
        isinstance(events, tuple)
        and any(
            isinstance(event, LeaderRiskEventEvidence)
            and event.official_status == RiskOfficialStatus.CORRECTED
            for event in events
        )
    )


def _correction_consistent(
    version: LeaderRiskLifecycleReviewVersion,
    lifecycle_versions: Tuple[LeaderRiskLifecycleReviewVersion, ...],
    version_index: int,
) -> bool:
    review = version.relation_review
    events = version.event_versions
    if (
        not isinstance(review, RiskDocumentVersionReview)
        or not isinstance(events, tuple)
    ):
        return False
    target = tuple(
        event
        for event in events
        if (
            isinstance(event, LeaderRiskEventEvidence)
            and event.event_id == review.target_event_id
            and event.event_version == review.target_event_version
        )
    )
    if len(target) != 1:
        return False
    corrected_events = tuple(
        event
        for event in events
        if (
            isinstance(event, LeaderRiskEventEvidence)
            and event.official_status == RiskOfficialStatus.CORRECTED
        )
    )
    if corrected_events and (
        len(corrected_events) != 1 or target[0] != corrected_events[0]
    ):
        return False
    if review.relation_kind != RiskDocumentRelationKind.SUPERSEDES:
        return not corrected_events and review.replacement_event_version is None
    event = target[0]
    if not (
        review.relation_kind == RiskDocumentRelationKind.SUPERSEDES
        and isinstance(review.replacement_event_version, str)
        and review.replacement_event_version
        and review.replacement_event_version != event.event_version
    ):
        return False
    replacements = []
    current_as_of = _aware_utc(version.as_of)
    if current_as_of is None:
        return False
    for candidate_index, lifecycle_version in enumerate(lifecycle_versions):
        if candidate_index <= version_index:
            continue
        lifecycle_events = getattr(lifecycle_version, "event_versions", None)
        if not isinstance(lifecycle_events, tuple):
            return False
        for candidate in lifecycle_events:
            if (
                isinstance(candidate, LeaderRiskEventEvidence)
                and candidate.event_id == event.event_id
                and candidate.event_version
                == review.replacement_event_version
                and _aware_utc(candidate.published_at) is not None
                and _aware_utc(candidate.published_at) <= current_as_of
                and _aware_utc(candidate.effective_from) is not None
                and _aware_utc(candidate.effective_from) <= current_as_of
                and candidate not in replacements
            ):
                replacements.append(candidate)
    return bool(
        len(replacements) == 1
        and _same_event_identity(event, replacements[0])
        and replacements[0].official_status == RiskOfficialStatus.ACTIVE
        and (
            replacements[0].effective_until is None
            or (
                _aware_utc(replacements[0].effective_until) is not None
                and _aware_utc(replacements[0].effective_until)
                >= current_as_of
            )
        )
    )


def _item(
    *,
    index: int,
    entry: LeaderRiskLifecycleBatchEntry,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    bundles: Sequence[RiskResearchEvidenceBundle] = (),
    open_events_continuous: bool = False,
    correction_links_consistent: bool = False,
) -> LeaderRiskLifecycleBatchItem:
    return LeaderRiskLifecycleBatchItem(
        index=index,
        symbol=entry.symbol,
        issuer_identity=entry.issuer_identity,
        status=status,
        version_count=(
            len(entry.versions) if isinstance(entry.versions, tuple) else 0
        ),
        bundle_ids=tuple(bundle.bundle_id for bundle in bundles),
        reasons=_dedupe(reasons),
        open_events_continuous=open_events_continuous,
        correction_links_consistent=correction_links_consistent,
    )


def _replay_entry(
    *,
    index: int,
    entry: LeaderRiskLifecycleBatchEntry,
    plan: LeaderRuntimeCandidatePlan,
    discovered_documents: Sequence[Any],
) -> Tuple[LeaderRiskLifecycleBatchItem, Tuple[RiskResearchEvidenceBundle, ...]]:
    versions = entry.versions
    if not isinstance(versions, tuple):
        return _item(
            index=index,
            entry=entry,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("risk_lifecycle_version_contract_unverified",),
        ), ()
    if not versions:
        return _item(
            index=index,
            entry=entry,
            status=ResearchFeatureStatus.MISSING,
            reasons=("risk_lifecycle_version_history_insufficient",),
        ), ()
    if (
        not isinstance(entry.issuer_identity, str)
        or not entry.issuer_identity
    ):
        return _item(
            index=index,
            entry=entry,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("risk_lifecycle_issuer_identity_missing",),
        ), ()
    normalized_times = tuple(
        _aware_utc(getattr(version, "as_of", None)) for version in versions
    )
    if any(
        not isinstance(version, LeaderRiskLifecycleReviewVersion)
        or normalized is None
        or normalized > plan.as_of
        for version, normalized in zip(versions, normalized_times)
    ) or any(
        current <= previous
        for previous, current in zip(
            normalized_times,
            normalized_times[1:],
        )
        if previous is not None and current is not None
    ):
        return _item(
            index=index,
            entry=entry,
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("risk_lifecycle_version_order_unverified",),
        ), ()
    if len(versions) >= 2:
        latest_version = versions[-1]
        submission_reviewed_at = _aware_utc(
            getattr(latest_version.submission, "reviewed_at", None)
        )
        relation_reviewed_at = _aware_utc(
            getattr(latest_version.relation_review, "reviewed_at", None)
        )
        latest_version_as_of = normalized_times[-1]
        if (
            submission_reviewed_at is None
            or relation_reviewed_at is None
            or latest_version_as_of is None
            or submission_reviewed_at > relation_reviewed_at
            or relation_reviewed_at > latest_version_as_of
        ):
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_review_time_unverified",),
            ), ()
        if (
            plan.as_of - submission_reviewed_at
        ).total_seconds() > MAXIMUM_COVERAGE_AGE_SECONDS:
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.STALE,
                reasons=("risk_lifecycle_latest_review_stale",),
            ), ()

    artifacts: list[AcceptedManualRiskReviewArtifact] = []
    bundles = []
    previous_version = None
    for version_index, version in enumerate(versions):
        matching_documents = tuple(
            item.document
            for item in discovered_documents
            if getattr(item, "document_id", None)
            == getattr(version.document, "document_id", None)
        )
        if (
            not isinstance(version.document, OfficialRiskDocumentMetadata)
            or not isinstance(version.content, OfficialRiskDocumentContentResult)
            or not isinstance(version.facts, OfficialRiskDocumentFactResult)
            or not isinstance(version.candidate, RiskDocumentReviewCandidate)
            or not isinstance(version.submission, ManualRiskReviewSubmission)
            or version.document.symbol != entry.symbol
            or version.document.issuer_identity != entry.issuer_identity
            or len(matching_documents) != 1
            or matching_documents[0] != version.document
        ):
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_version_identity_unverified",),
            ), ()
        correction_consistent = _correction_consistent(
            version,
            versions,
            version_index,
        )
        if _has_correction_claim(version) and not correction_consistent:
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_correction_link_unverified",),
                open_events_continuous=True,
                correction_links_consistent=False,
            ), ()
        if previous_version is not None and not _events_continuous(
            previous_version,
            version,
        ):
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_open_event_not_carried_forward",),
                open_events_continuous=False,
            ), ()
        if not correction_consistent:
            return _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_correction_link_unverified",),
                open_events_continuous=True,
                correction_links_consistent=False,
            ), ()

        artifact_result = build_manual_risk_review_artifact(
            ManualRiskReviewArtifactInput(
                as_of=version.as_of,
                document=version.document,
                content=version.content,
                facts=version.facts,
                candidate=version.candidate,
                event_versions=version.event_versions,
                submission=version.submission,
                previous_artifacts=tuple(artifacts),
            )
        )
        if (
            artifact_result.status != ResearchFeatureStatus.READY
            or artifact_result.artifact is None
        ):
            return _item(
                index=index,
                entry=entry,
                status=artifact_result.status,
                reasons=artifact_result.reasons,
            ), ()
        artifacts.append(artifact_result.artifact)
        replay_input = RiskDocumentResearchReplayInput(
            as_of=version.as_of,
            document=version.document,
            content=version.content,
            facts=version.facts,
            event_versions=version.event_versions,
            artifacts=tuple(artifacts),
        )
        replay_result = replay_risk_document_research_evidence(
            replay_input
        )
        relation_input = SupplementedRiskDocumentRelationInput(
            replay_input=replay_input,
            replay_result=replay_result,
            review=version.relation_review,
        )
        relation_result = review_supplemented_risk_document_relation(
            relation_input
        )
        bundle_result = build_risk_research_evidence_bundle(
            RiskResearchEvidenceBundleInput(
                relation_input=relation_input,
                relation_result=relation_result,
            )
        )
        if (
            bundle_result.status != ResearchFeatureStatus.READY
            or bundle_result.bundle is None
        ):
            return _item(
                index=index,
                entry=entry,
                status=bundle_result.status,
                reasons=bundle_result.reasons,
            ), ()
        bundles.append(bundle_result.bundle)
        previous_version = version

    if len(bundles) < 2:
        return _item(
            index=index,
            entry=entry,
            status=ResearchFeatureStatus.MISSING,
            reasons=("risk_lifecycle_version_history_insufficient",),
            bundles=bundles,
            open_events_continuous=True,
            correction_links_consistent=True,
        ), tuple(bundles)
    return _item(
        index=index,
        entry=entry,
        status=ResearchFeatureStatus.READY,
        reasons=(),
        bundles=bundles,
        open_events_continuous=True,
        correction_links_consistent=True,
    ), tuple(bundles)


def _aggregate_status(
    items: Sequence[LeaderRiskLifecycleBatchItem],
) -> LeaderRiskLifecycleBatchStatus:
    if any(item.status == ResearchFeatureStatus.READY for item in items):
        return LeaderRiskLifecycleBatchStatus.PARTIAL
    statuses = {item.status for item in items}
    if ResearchFeatureStatus.SOURCE_FAILED in statuses:
        return LeaderRiskLifecycleBatchStatus.SOURCE_FAILED
    if ResearchFeatureStatus.SOURCE_UNVERIFIED in statuses:
        return LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED
    if ResearchFeatureStatus.STALE in statuses:
        return LeaderRiskLifecycleBatchStatus.STALE
    return LeaderRiskLifecycleBatchStatus.MISSING


def build_leader_risk_lifecycle_batch(
    input_value: Any,
) -> LeaderRiskLifecycleBatchResult:
    """重放D2与D5-D8，再交给现有D9/E1/E3构建研究批次。"""

    if not isinstance(input_value, LeaderRiskLifecycleBatchInput):
        return _result(
            status=LeaderRiskLifecycleBatchStatus.BLOCKED,
            plan=None,
            reasons=("risk_lifecycle_batch_contract_unverified",),
        )
    plan = input_value.candidate_plan
    if (
        not isinstance(plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(plan)
        or not isinstance(input_value.discovery_batches, tuple)
        or not isinstance(input_value.entries, tuple)
    ):
        return _result(
            status=LeaderRiskLifecycleBatchStatus.BLOCKED,
            plan=plan if isinstance(plan, LeaderRuntimeCandidatePlan) else None,
            reasons=("risk_lifecycle_batch_contract_unverified",),
        )
    expected_symbols = tuple(item.symbol for item in plan.items)
    if (
        len(input_value.entries) != len(expected_symbols)
        or tuple(
            getattr(entry, "symbol", None) for entry in input_value.entries
        ) != expected_symbols
        or any(
            not isinstance(entry, LeaderRiskLifecycleBatchEntry)
            or STAGE6_SYMBOL_PATTERN.fullmatch(entry.symbol) is None
            for entry in input_value.entries
        )
    ):
        return _result(
            status=LeaderRiskLifecycleBatchStatus.BLOCKED,
            plan=plan,
            reasons=("risk_lifecycle_candidate_plan_mismatch",),
        )

    try:
        discovery = build_leader_risk_official_candidate_discovery_batch(
            candidate_plan=plan,
            batches=input_value.discovery_batches,
        )
        categories_complete, pages_complete, window_continuous = (
            _query_audit(
                input_value.discovery_batches,
                as_of=plan.as_of,
                expected_symbols=expected_symbols,
                expected_candidate_plan_id=plan.candidate_set_id,
            )
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _result(
            status=LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
            plan=plan,
            reasons=("risk_lifecycle_discovery_unverified",),
        )
    source_statuses = {
        batch.status for batch in input_value.discovery_batches
        if isinstance(batch, OfficialRiskDiscoveryBatch)
    }
    if OfficialRiskSourceStatus.SOURCE_FAILED in source_statuses:
        return _result(
            status=LeaderRiskLifecycleBatchStatus.SOURCE_FAILED,
            plan=plan,
            reasons=("risk_lifecycle_discovery_source_failed",),
            discovery_result=discovery,
            query_categories_complete=categories_complete,
            query_pages_complete=False,
            query_window_continuous=window_continuous,
        )
    if (
        discovery.status
        == LeaderRiskOfficialCandidateDiscoveryStatus.BLOCKED
    ):
        return _result(
            status=LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
            plan=plan,
            reasons=discovery.reasons,
            discovery_result=discovery,
            query_categories_complete=categories_complete,
            query_pages_complete=False,
            query_window_continuous=window_continuous,
        )
    if OfficialRiskSourceStatus.SOURCE_UNVERIFIED in source_statuses:
        return _result(
            status=LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
            plan=plan,
            reasons=("risk_lifecycle_discovery_source_unverified",),
            discovery_result=discovery,
            query_categories_complete=categories_complete,
            query_pages_complete=False,
            query_window_continuous=window_continuous,
        )
    if discovery.status in {
        LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED,
        LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_UNVERIFIED,
    }:
        return _result(
            status=(
                LeaderRiskLifecycleBatchStatus.SOURCE_FAILED
                if discovery.status
                == LeaderRiskOfficialCandidateDiscoveryStatus.SOURCE_FAILED
                else LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED
            ),
            plan=plan,
            reasons=discovery.reasons,
            discovery_result=discovery,
            query_categories_complete=categories_complete,
            query_pages_complete=pages_complete,
            query_window_continuous=window_continuous,
        )
    query_reasons = (
        *(
            ()
            if categories_complete
            else ("risk_lifecycle_query_categories_incomplete",)
        ),
        *(
            ()
            if pages_complete
            else ("risk_lifecycle_query_pages_incomplete",)
        ),
        *(
            ()
            if window_continuous
            else ("risk_lifecycle_query_window_discontinuous",)
        ),
    )
    if query_reasons:
        return _result(
            status=LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
            plan=plan,
            reasons=query_reasons,
            discovery_result=discovery,
            query_categories_complete=categories_complete,
            query_pages_complete=pages_complete,
            query_window_continuous=window_continuous,
        )

    discovered_by_symbol = discovery.documents_by_symbol
    items = []
    bundle_entries = []
    for index, entry in enumerate(input_value.entries):
        discovered_documents = discovered_by_symbol.get(entry.symbol, ())
        try:
            lifecycle_item, bundles = _replay_entry(
                index=index,
                entry=entry,
                plan=plan,
                discovered_documents=discovered_documents,
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            lifecycle_item, bundles = _item(
                index=index,
                entry=entry,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_lifecycle_version_contract_unverified",),
            ), ()
        items.append(lifecycle_item)
        bundle_entries.append(LeaderRiskEvidenceBundleBatchEntry(
            symbol=entry.symbol,
            issuer_identity=entry.issuer_identity,
            bundles=bundles,
            source_status=(
                None
                if bundles
                else lifecycle_item.status
            ),
            reasons=(
                ()
                if bundles
                else lifecycle_item.reasons
            ),
        ))
    projection_batch = build_leader_risk_projection_batch_from_bundles(
        as_of=plan.as_of,
        entries=tuple(bundle_entries),
    )
    status = _aggregate_status(items)
    reasons = (
        ("risk_lifecycle_batch_partial",)
        if status == LeaderRiskLifecycleBatchStatus.PARTIAL
        else (
            ("risk_lifecycle_batch_stale",)
            if status == LeaderRiskLifecycleBatchStatus.STALE
            else ("risk_lifecycle_batch_no_ready_items",)
        )
    )
    return _result(
        status=status,
        plan=plan,
        reasons=reasons,
        items=items,
        discovery_result=discovery,
        projection_batch=projection_batch,
        query_categories_complete=True,
        query_pages_complete=True,
        query_window_continuous=True,
    )
