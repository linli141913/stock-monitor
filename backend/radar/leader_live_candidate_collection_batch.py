"""阶段6候选全集的可重复、只读、失败关闭采集批次。

本模块把官方证券主档、行业发布、全量行情和市场指数接到现有候选计划
合同上。来源回调由调用方显式注入，模块本身不连接数据库、不写入状态，
也不会把行业影子结果或候选计划误标为正式龙头状态。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationRecord,
    IndustryClassificationSnapshot,
    IndustryClassificationRelease,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    IndexQuoteSnapshot,
    QuoteSnapshot,
    SecurityMasterRecord,
    SectorFeatureBatch,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
    industry_classification_release_id,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_leader_research_runtime_source_context,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    LeaderRuntimeCandidatePlanInput,
    LeaderRuntimeCandidatePlanStatus,
    build_leader_runtime_candidate_plan,
)
from radar.market_cap_unit_evidence import build_market_cap_unit_evidence
from radar.market_features import build_market_features
from radar.sector_features import (
    build_sector_feature_runtime_rows,
    build_sector_features,
)
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.industry_classification import fetch_industry_classification
from radar.sources.market_indices import fetch_market_indices
from radar.sources.security_master import (
    SecurityMasterProviders,
    fetch_security_master,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes_concurrent
from radar.turnover_unit_evidence import build_turnover_unit_evidence


UTC = timezone.utc
LEADER_LIVE_CANDIDATE_COLLECTION_BATCH_CONTRACT_ID = (
    "radar-leader-live-candidate-collection-batch-v1"
)


class LeaderLiveCandidateCollectionStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    SOURCE_FAILED = "source_failed"


@dataclass(frozen=True)
class LeaderLiveCandidateCollectionRequest:
    radar_run_id: str
    security_master_batch_id: str
    discovery_classification_batch_id: str
    collection_classification_batch_id: str
    quote_batch_id: str
    index_batch_id: str
    etf_symbols: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "radar_run_id",
            "security_master_batch_id",
            "discovery_classification_batch_id",
            "collection_classification_batch_id",
            "quote_batch_id",
            "index_batch_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{field_name}必须为非空字符串"
                )
        if (
            not isinstance(self.etf_symbols, tuple)
            or len(self.etf_symbols) != len(set(self.etf_symbols))
            or any(
                not isinstance(symbol, str)
                or len(symbol) != 6
                or not symbol.isdigit()
                for symbol in self.etf_symbols
            )
        ):
            raise ValueError("etf_symbols必须是无重复六位代码元组")


SecurityMasterLoader = Callable[[str, str, datetime], Any]
ClassificationLoader = Callable[..., Any]
ClassificationReleaseLoader = Callable[[str, str], Any]
QuoteLoader = Callable[[Sequence[str], str, str, datetime], Any]
IndexLoader = Callable[[str, str, datetime], Any]


@dataclass(frozen=True)
class LeaderLiveCandidateCollectionSources:
    security_master_loader: SecurityMasterLoader = field(repr=False)
    classification_loader: ClassificationLoader = field(repr=False)
    quote_loader: QuoteLoader = field(repr=False)
    index_loader: IndexLoader = field(repr=False)
    classification_release_loader: Optional[
        ClassificationReleaseLoader
    ] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not all(callable(value) for value in (
            self.security_master_loader,
            self.classification_loader,
            self.quote_loader,
            self.index_loader,
        )):
            raise ValueError(
                "leader_live_candidate_collection_sources_contract_unverified"
            )
        if (
            self.classification_release_loader is not None
            and not callable(self.classification_release_loader)
        ):
            raise ValueError(
                "leader_live_candidate_collection_sources_contract_unverified"
            )


def build_repository_classification_release_loader(
    repository: Any,
) -> ClassificationReleaseLoader:
    """把显式传入的雷达仓储转换为行业版本只读加载器。

    调用方必须自己管理连接生命周期；本适配器不打开路径、不写入数据库。
    """

    loader = getattr(repository, "get_industry_classification_release", None)
    if not callable(loader):
        raise ValueError(
            "leader_classification_release_repository_unverified"
        )

    def load(
        classification_system: str,
        release_period: str,
    ) -> Any:
        return loader(classification_system, release_period)

    return load


@dataclass(frozen=True, repr=False)
class LeaderLiveCandidateRuntimeInputs:
    candidate_plan: LeaderRuntimeCandidatePlan = field(repr=False)
    source_context: LeaderResearchRuntimeSourceContext = field(repr=False)
    as_of: datetime
    security_master_batch: SourceBatch = field(repr=False)
    quote_batch: SourceBatch = field(repr=False)
    index_batch: SourceBatch = field(repr=False)
    classification_snapshot: IndustryClassificationSnapshot = field(
        repr=False
    )
    quote_health: SourceHealthResult = field(repr=False)
    market_snapshot: Mapping[str, Any] = field(repr=False)
    sector_rows: Tuple[Mapping[str, Any], ...] = field(repr=False)
    sector_feature_batch: SectorFeatureBatch = field(repr=False)
    industry_records: Tuple[Any, ...] = field(repr=False)
    industry_release: IndustryClassificationRelease = field(repr=False)
    security_records: Tuple[SecurityMasterRecord, ...] = field(repr=False)
    etf_symbols: Tuple[str, ...] = ()
    contract_id: str = (
        "radar-leader-live-candidate-runtime-inputs-v1"
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "asOf": self.as_of.isoformat(),
            "candidatePlanId": self.candidate_plan.candidate_set_id,
            "candidateCount": self.candidate_plan.candidate_count,
            "quoteBatchId": self.quote_batch.meta.batch_id,
            "quoteHealthStatus": self.quote_health.status.value,
            "sectorCount": len(self.sector_rows),
            "industryRecordCount": len(self.industry_records),
            "industryReleaseId": industry_classification_release_id(
                self.industry_release
            ),
            "industryFirstObservedAt": (
                self.industry_release.first_observed_at.isoformat()
            ),
            "securityRecordCount": len(self.security_records),
        }


@dataclass(frozen=True, repr=False)
class LeaderLiveCandidateCollectionResult:
    status: LeaderLiveCandidateCollectionStatus
    radar_run_id: Optional[str]
    discovery_as_of: Optional[datetime]
    as_of: Optional[datetime]
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = field(
        default=None,
        repr=False,
    )
    source_context: Optional[LeaderResearchRuntimeSourceContext] = field(
        default=None,
        repr=False,
    )
    quote_health: Optional[SourceHealthResult] = field(
        default=None,
        repr=False,
    )
    runtime_inputs: Optional[LeaderLiveCandidateRuntimeInputs] = field(
        default=None,
        repr=False,
    )
    candidate_count: int = 0
    scanned_count: int = 0
    mapped_count: int = 0
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_LIVE_CANDIDATE_COLLECTION_BATCH_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def __repr__(self) -> str:
        return (
            "LeaderLiveCandidateCollectionResult("
            f"status={self.status.value!r}, "
            f"candidate_count={self.candidate_count!r})"
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "discoveryAsOf": (
                self.discovery_as_of.isoformat()
                if self.discovery_as_of is not None
                else None
            ),
            "asOf": self.as_of.isoformat() if self.as_of is not None else None,
            "candidatePlanId": (
                self.candidate_plan.candidate_set_id
                if self.candidate_plan is not None
                else None
            ),
            "candidateCount": self.candidate_count,
            "scannedCount": self.scanned_count,
            "mappedCount": self.mapped_count,
            "quoteHealthStatus": (
                self.quote_health.status.value
                if self.quote_health is not None
                else None
            ),
            "quoteHealthReasons": (
                list(self.quote_health.reasons)
                if self.quote_health is not None
                else []
            ),
            "runtimeInputsReady": self.runtime_inputs is not None,
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": self.state_transition_allowed,
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
    status: LeaderLiveCandidateCollectionStatus,
    request: LeaderLiveCandidateCollectionRequest,
    discovery_as_of: Optional[datetime],
    as_of: Optional[datetime],
    reasons: Sequence[str],
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = None,
    source_context: Optional[LeaderResearchRuntimeSourceContext] = None,
    quote_health: Optional[SourceHealthResult] = None,
    runtime_inputs: Optional[LeaderLiveCandidateRuntimeInputs] = None,
    scanned_count: int = 0,
    mapped_count: int = 0,
) -> LeaderLiveCandidateCollectionResult:
    return LeaderLiveCandidateCollectionResult(
        status=status,
        radar_run_id=request.radar_run_id,
        discovery_as_of=discovery_as_of,
        as_of=as_of,
        candidate_plan=candidate_plan,
        source_context=source_context,
        quote_health=quote_health,
        runtime_inputs=runtime_inputs,
        candidate_count=(
            candidate_plan.candidate_count
            if candidate_plan is not None
            else 0
        ),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
        reasons=_dedupe(reasons),
    )


def _release_document_identity(release: Any) -> Tuple[Any, ...]:
    if not isinstance(release, IndustryClassificationRelease):
        return ()
    return (
        release.classification_system,
        release.scheme_version,
        release.release_period,
        release.source_page_title,
        release.publication_page_url,
        release.document_url,
        release.document_sha256,
        release.published_date,
        release.classification_start_date,
        release.source_record_count,
        release.unique_source_symbol_count,
    )


def _release_identity(snapshot: IndustryClassificationSnapshot) -> Tuple[Any, ...]:
    return _release_document_identity(snapshot.release)


def mapped_industry_unit_scope_symbols(
    snapshot: IndustryClassificationSnapshot,
) -> Tuple[str, ...]:
    symbols = tuple(
        record.security_identity
        for record in snapshot.records
        if (
            type(record) is IndustryClassificationRecord
            and record.record_status == IndustryRecordStatus.ACCEPTED
            and record.identity_status != IndustryIdentityStatus.UNRESOLVED
            and record.security_identity is not None
        )
    )
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("industry_mapped_unit_scope_unverified")
    return symbols


def _valid_security_batch(
    batch: Any,
    *,
    request: LeaderLiveCandidateCollectionRequest,
    as_of: datetime,
) -> bool:
    if (
        not isinstance(batch, SourceBatch)
        or batch.meta.radar_run_id != request.radar_run_id
        or batch.meta.batch_id != request.security_master_batch_id
        or _aware_utc(batch.meta.as_of) != as_of
        or batch.meta.source != "official_exchange_security_master"
        or not batch.items
        or any(not isinstance(item, SecurityMasterRecord) for item in batch.items)
        or batch.meta.expected_count != len(batch.items)
        or batch.meta.returned_count != len(batch.items)
        or bool(batch.meta.issues)
    ):
        return False
    symbols = tuple(item.symbol for item in batch.items)
    return len(symbols) == len(set(symbols)) and any(
        item.exchange in {"sse", "szse"} for item in batch.items
    )


def _valid_classification_snapshot(
    snapshot: Any,
    *,
    request: LeaderLiveCandidateCollectionRequest,
    batch_id: str,
    as_of: datetime,
) -> bool:
    return bool(
        isinstance(snapshot, IndustryClassificationSnapshot)
        and snapshot.meta.radar_run_id == request.radar_run_id
        and snapshot.meta.batch_id == batch_id
        and _aware_utc(snapshot.meta.as_of) == as_of
        and snapshot.status != SourceStatus.FAILED
        and snapshot.release is not None
        and snapshot.completeness.shadow_usable
        and snapshot.meta.returned_count == len(snapshot.records)
    )


def _valid_quote_batch(
    batch: Any,
    *,
    request: LeaderLiveCandidateCollectionRequest,
    as_of: datetime,
    expected_symbols: Tuple[str, ...],
) -> bool:
    if (
        not isinstance(batch, SourceBatch)
        or batch.meta.radar_run_id != request.radar_run_id
        or batch.meta.batch_id != request.quote_batch_id
        or _aware_utc(batch.meta.as_of) != as_of
        or batch.meta.source != "tencent_finance"
        or any(type(item) is not QuoteSnapshot for item in batch.items)
        or batch.meta.expected_count != len(expected_symbols)
        or batch.meta.returned_count != len(batch.items)
    ):
        return False
    symbols = tuple(getattr(item, "symbol", None) for item in batch.items)
    return (
        all(isinstance(symbol, str) for symbol in symbols)
        and len(symbols) == len(set(symbols))
        and set(symbols).issubset(set(expected_symbols))
    )


def _valid_index_batch(
    batch: Any,
    *,
    request: LeaderLiveCandidateCollectionRequest,
    as_of: datetime,
) -> bool:
    return bool(
        isinstance(batch, SourceBatch)
        and batch.meta.radar_run_id == request.radar_run_id
        and batch.meta.batch_id == request.index_batch_id
        and _aware_utc(batch.meta.as_of) == as_of
        and batch.meta.source == "tencent_finance_indices"
        and all(isinstance(item, IndexQuoteSnapshot) for item in batch.items)
        and batch.meta.returned_count == len(batch.items)
        and batch.meta.expected_count == len(batch.items)
    )


def collect_leader_live_candidate_batch(
    request: LeaderLiveCandidateCollectionRequest,
    sources: LeaderLiveCandidateCollectionSources,
    *,
    clock: Callable[[], datetime],
) -> LeaderLiveCandidateCollectionResult:
    """执行一轮同轮只读采集，任何来源或身份不一致都关闭候选输出。"""

    if (
        not isinstance(request, LeaderLiveCandidateCollectionRequest)
        or not isinstance(sources, LeaderLiveCandidateCollectionSources)
        or not callable(clock)
    ):
        raise ValueError(
            "leader_live_candidate_collection_batch_contract_unverified"
        )

    try:
        discovery_as_of = _aware_utc(clock())
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=None,
            as_of=None,
            reasons=("collection_clock_failed",),
        )
    if discovery_as_of is None:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=None,
            as_of=None,
            reasons=("discovery_as_of_missing",),
        )

    try:
        security_batch = sources.security_master_loader(
            request.radar_run_id,
            request.security_master_batch_id,
            discovery_as_of,
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("security_master_source_failed",),
        )
    if not _valid_security_batch(
        security_batch,
        request=request,
        as_of=discovery_as_of,
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("security_master_contract_unverified",),
        )
    security_records = tuple(security_batch.items)
    security_symbols = tuple(record.symbol for record in security_records)
    expected_symbols = tuple(dict.fromkeys((*security_symbols, *request.etf_symbols)))

    try:
        discovery_classification = sources.classification_loader(
            request.radar_run_id,
            request.discovery_classification_batch_id,
            discovery_as_of,
            security_records,
            first_observed_at=None,
            known_document_hashes=None,
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("classification_discovery_source_failed",),
            scanned_count=len(security_records),
        )
    if not _valid_classification_snapshot(
        discovery_classification,
        request=request,
        batch_id=request.discovery_classification_batch_id,
        as_of=discovery_as_of,
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("classification_discovery_contract_unverified",),
            scanned_count=len(security_records),
        )
    release = discovery_classification.release
    assert release is not None
    first_observed_at = _aware_utc(release.first_observed_at)
    if first_observed_at is None:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("classification_first_observed_missing",),
            scanned_count=len(security_records),
        )
    if sources.classification_release_loader is not None:
        try:
            stored_release = sources.classification_release_loader(
                release.classification_system,
                release.release_period,
            )
        except Exception:
            return _result(
                status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
                request=request,
                discovery_as_of=discovery_as_of,
                as_of=None,
                reasons=("classification_observation_evidence_failed",),
                scanned_count=len(security_records),
            )
        if stored_release is not None:
            stored_first_observed_at = _aware_utc(
                getattr(stored_release, "first_observed_at", None)
            )
            stored_fetched_at = _aware_utc(
                getattr(stored_release, "fetched_at", None)
            )
            if (
                _release_document_identity(stored_release)
                != _release_document_identity(release)
                or stored_first_observed_at is None
                or stored_fetched_at is None
                or stored_first_observed_at > discovery_as_of
                or stored_fetched_at > discovery_as_of
            ):
                return _result(
                    status=LeaderLiveCandidateCollectionStatus.NOT_READY,
                    request=request,
                    discovery_as_of=discovery_as_of,
                    as_of=None,
                    reasons=(
                        "classification_observation_evidence_mismatch",
                    ),
                    scanned_count=len(security_records),
                )
            first_observed_at = stored_first_observed_at
    try:
        collection_as_of = _aware_utc(clock())
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("collection_clock_failed",),
            scanned_count=len(security_records),
        )
    if collection_as_of is None:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=None,
            reasons=("collection_as_of_missing",),
            scanned_count=len(security_records),
        )
    if collection_as_of <= discovery_as_of:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("collection_as_of_not_after_discovery",),
            scanned_count=len(security_records),
        )
    if first_observed_at > collection_as_of:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("classification_first_observed_after_collection",),
            scanned_count=len(security_records),
        )

    try:
        collection_classification = sources.classification_loader(
            request.radar_run_id,
            request.collection_classification_batch_id,
            collection_as_of,
            security_records,
            first_observed_at=first_observed_at,
            known_document_hashes={
                release.release_period: release.document_sha256,
            },
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("classification_collection_source_failed",),
            scanned_count=len(security_records),
        )
    if not _valid_classification_snapshot(
        collection_classification,
        request=request,
        batch_id=request.collection_classification_batch_id,
        as_of=collection_as_of,
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("classification_collection_contract_unverified",),
            scanned_count=len(security_records),
        )
    if _release_identity(collection_classification) != _release_identity(
        discovery_classification
    ) or (
        _aware_utc(
            collection_classification.release.first_observed_at
        )
        != first_observed_at
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("classification_release_identity_mismatch",),
            scanned_count=len(security_records),
        )

    try:
        quote_batch = sources.quote_loader(
            expected_symbols,
            request.radar_run_id,
            request.quote_batch_id,
            collection_as_of,
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("quote_source_failed",),
            scanned_count=len(security_records),
        )
    try:
        index_batch = sources.index_loader(
            request.radar_run_id,
            request.index_batch_id,
            collection_as_of,
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.SOURCE_FAILED,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("index_source_failed",),
            scanned_count=len(security_records),
        )
    if not _valid_quote_batch(
        quote_batch,
        request=request,
        as_of=collection_as_of,
        expected_symbols=expected_symbols,
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("quote_batch_contract_unverified",),
            scanned_count=len(security_records),
        )
    if not _valid_index_batch(
        index_batch,
        request=request,
        as_of=collection_as_of,
    ):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("index_batch_contract_unverified",),
            scanned_count=len(security_records),
        )

    candidate_as_of_values = tuple(
        _aware_utc(value)
        for value in (
            collection_as_of,
            collection_classification.meta.fetched_at,
            quote_batch.meta.fetched_at,
            index_batch.meta.fetched_at,
        )
    )
    if any(value is None for value in candidate_as_of_values):
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=collection_as_of,
            reasons=("source_collection_completion_time_missing",),
            scanned_count=len(security_records),
        )
    candidate_as_of = max(candidate_as_of_values)
    collection_classification = collection_classification.model_copy(
        update={
            "meta": collection_classification.meta.model_copy(
                update={"as_of": candidate_as_of}
            )
        },
    )
    quote_batch = quote_batch.model_copy(
        update={
            "meta": quote_batch.meta.model_copy(
                update={"as_of": candidate_as_of}
            )
        },
    )
    index_batch = index_batch.model_copy(
        update={
            "meta": index_batch.meta.model_copy(
                update={"as_of": candidate_as_of}
            )
        },
    )

    quote_health = evaluate_source_health(
        quote_batch.meta,
        SourceHealthPolicy(
            minimum_row_coverage=0.995,
            minimum_required_field_coverage=0.99,
            maximum_age_seconds=90,
            maximum_future_skew_seconds=5,
            required_fields=("price", "source_time"),
        ),
        now=candidate_as_of,
    )
    if quote_health.status != SourceStatus.HEALTHY or not quote_health.allows_new_state:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=candidate_as_of,
            reasons=("quote_source_not_healthy", *quote_health.reasons),
            quote_health=quote_health,
            scanned_count=len(security_records),
        )

    try:
        stock_symbols = security_symbols
        mapped_industry_symbols = mapped_industry_unit_scope_symbols(
            collection_classification
        )
        market_cap_evidence = build_market_cap_unit_evidence(
            tuple(quote_batch.items),
            stock_symbols=mapped_industry_symbols,
        )
        market_turnover_evidence = build_turnover_unit_evidence(
            tuple(quote_batch.items),
            stock_symbols=stock_symbols,
        )
        sector_turnover_evidence = build_turnover_unit_evidence(
            tuple(quote_batch.items),
            stock_symbols=mapped_industry_symbols,
        )
        market_features = build_market_features(
            index_batch,
            quote_batch,
            stock_symbols=stock_symbols,
            etf_symbols=request.etf_symbols,
            turnover_unit_status=market_turnover_evidence.status,
        )
        sector_features = build_sector_features(
            collection_classification,
            quote_batch,
            stock_symbols=stock_symbols,
            etf_symbols=request.etf_symbols,
            market_cap_unit_status=market_cap_evidence.status,
            turnover_unit_status=sector_turnover_evidence.status,
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=candidate_as_of,
            reasons=("feature_aggregation_contract_rejected",),
            quote_health=quote_health,
            scanned_count=len(security_records),
        )

    market_snapshot = market_features.model_dump(by_alias=True)
    industry_release_id = industry_classification_release_id(
        collection_classification.release
    )
    sector_rows = build_sector_feature_runtime_rows(
        sector_features,
        industry_release_id=industry_release_id,
    )
    candidate_plan = build_leader_runtime_candidate_plan(
        LeaderRuntimeCandidatePlanInput(
            as_of=candidate_as_of,
            quote_batch=quote_batch,
            quote_health=quote_health,
            market_snapshot=market_snapshot,
            sector_rows=sector_rows,
            industry_records=tuple(collection_classification.records),
            security_records=security_records,
        )
    )
    if candidate_plan.status != LeaderRuntimeCandidatePlanStatus.READY:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=candidate_as_of,
            reasons=("candidate_plan_not_ready", *candidate_plan.gate_reasons),
            candidate_plan=candidate_plan,
            quote_health=quote_health,
            scanned_count=candidate_plan.scanned_count,
            mapped_count=candidate_plan.mapped_count,
        )

    try:
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=candidate_plan,
            quote_batch=quote_batch,
            quote_health=quote_health,
            security_records=security_records,
            industry_records=tuple(collection_classification.records),
        )
    except Exception:
        return _result(
            status=LeaderLiveCandidateCollectionStatus.NOT_READY,
            request=request,
            discovery_as_of=discovery_as_of,
            as_of=candidate_as_of,
            reasons=("runtime_source_context_contract_rejected",),
            candidate_plan=candidate_plan,
            quote_health=quote_health,
            scanned_count=candidate_plan.scanned_count,
            mapped_count=candidate_plan.mapped_count,
        )

    runtime_inputs = LeaderLiveCandidateRuntimeInputs(
        candidate_plan=candidate_plan,
        source_context=source_context,
        as_of=candidate_as_of,
        security_master_batch=security_batch.model_copy(deep=True),
        quote_batch=quote_batch.model_copy(deep=True),
        index_batch=index_batch.model_copy(deep=True),
        classification_snapshot=(
            collection_classification.model_copy(deep=True)
        ),
        quote_health=quote_health.model_copy(deep=True),
        market_snapshot=MappingProxyType(copy.deepcopy(market_snapshot)),
        sector_rows=tuple(
            MappingProxyType(copy.deepcopy(row)) for row in sector_rows
        ),
        sector_feature_batch=sector_features.model_copy(deep=True),
        industry_records=tuple(
            record.model_copy(deep=True)
            for record in collection_classification.records
        ),
        industry_release=collection_classification.release.model_copy(deep=True),
        security_records=tuple(
            record.model_copy(deep=True) for record in security_records
        ),
        etf_symbols=request.etf_symbols,
    )
    return _result(
        status=LeaderLiveCandidateCollectionStatus.READY,
        request=request,
        discovery_as_of=discovery_as_of,
        as_of=candidate_as_of,
        reasons=(),
        candidate_plan=candidate_plan,
        source_context=source_context,
        quote_health=quote_health,
        runtime_inputs=runtime_inputs,
        scanned_count=candidate_plan.scanned_count,
        mapped_count=candidate_plan.mapped_count,
    )


def build_default_leader_live_candidate_collection_sources(
    *,
    classification_publication_page_url: str,
    security_master_providers: Optional[SecurityMasterProviders] = None,
    verify_official_classification_archive: bool = False,
    classification_release_loader: Optional[
        ClassificationReleaseLoader
    ] = None,
    classification_release_repository: Any = None,
) -> LeaderLiveCandidateCollectionSources:
    """构造默认公开源回调；仅创建回调，不在构造阶段联网。"""

    if type(verify_official_classification_archive) is not bool:
        raise ValueError(
            "verify_official_classification_archive必须是布尔值"
        )

    if (
        classification_release_loader is not None
        and classification_release_repository is not None
    ):
        raise ValueError(
            "leader_classification_release_loader_ambiguous"
        )
    if classification_release_repository is not None:
        classification_release_loader = (
            build_repository_classification_release_loader(
                classification_release_repository
            )
        )

    def security_loader(run_id: str, batch_id: str, as_of: datetime):
        return fetch_security_master(
            run_id,
            batch_id,
            as_of,
            providers=security_master_providers,
        )

    def classification_loader(
        run_id: str,
        batch_id: str,
        as_of: datetime,
        security_records: Sequence[SecurityMasterRecord],
        **kwargs: Any,
    ):
        return fetch_industry_classification(
            run_id,
            batch_id,
            as_of,
            classification_publication_page_url,
            security_records,
            first_observed_at=kwargs.get("first_observed_at"),
            known_document_hashes=kwargs.get("known_document_hashes"),
            verify_official_archive=(
                verify_official_classification_archive
            ),
        )

    def quote_loader(
        symbols: Sequence[str],
        run_id: str,
        batch_id: str,
        as_of: datetime,
    ):
        return fetch_tencent_quotes_concurrent(
            symbols,
            run_id,
            batch_id,
            as_of,
        )

    return LeaderLiveCandidateCollectionSources(
        security_master_loader=security_loader,
        classification_loader=classification_loader,
        quote_loader=quote_loader,
        index_loader=fetch_market_indices,
        classification_release_loader=classification_release_loader,
    )
