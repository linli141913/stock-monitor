"""把现有真实来源快照转换成阶段9严格时点回放证据。

适配器只处理调用方已经取得的对象，不访问网络、SQLite 或生产运行配置。
当前观测只能进入观测时点之后的前向样本，不能倒填成历史事实。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Collection, Dict, List, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from radar.contracts import (
    EtfProductMasterRecord,
    IndustryClassificationSnapshot,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
)
from radar.leader_tradability_features import SecurityLifecycleStatus
from radar.leader_tradability_sources import (
    PriceLimitSpecialSession,
    resolve_trading_rule_catalog,
)
from radar.replay_contracts import RadarReplayEvidence
from radar.sources.etf_index_evidence import OfficialIndexPocResult


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_timezone_required")
    return value


def _not_future(value: Optional[datetime], sample_as_of: datetime, name: str):
    if value is not None and value > sample_as_of:
        raise ValueError(f"future_{name}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _with_hash(payload: Dict[str, Any]) -> Dict[str, Any]:
    canonical = json.dumps(
        _jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **payload,
        "snapshotSha256": hashlib.sha256(canonical).hexdigest(),
    }


def _validate_boundary(*, sample_as_of: datetime, values: Mapping[str, Any]):
    _aware(sample_as_of, "sampleAsOf")
    for name, value in values.items():
        if value is not None:
            _aware(value, name)
            _not_future(value, sample_as_of, name)


def _batch_status(batch: SourceBatch[Any], *, incomplete_reason: str):
    meta = batch.meta
    reasons: List[str] = []
    if meta.expected_count is None:
        reasons.append(f"{incomplete_reason}_expected_count_unknown")
        return "failed", reasons
    if (
        meta.returned_count != len(batch.items)
        or meta.row_coverage != 1.0
        or meta.returned_count != meta.expected_count
    ):
        reasons.append(incomplete_reason)
        return "unverifiable", reasons
    return "ready", reasons


def adapt_security_universe(
    batch: SourceBatch[SecurityMasterRecord],
    *,
    sample_as_of: datetime,
) -> RadarReplayEvidence:
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "sourceTime": batch.meta.source_time,
            "fetchedAt": batch.meta.fetched_at,
        },
    )
    for record in batch.items:
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={"recordFetchedAt": record.fetched_at},
        )
    status, reasons = _batch_status(
        batch,
        incomplete_reason="security_universe_incomplete",
    )
    payload = _with_hash({
        "observationKind": "forward_observed",
        "recordCount": len(batch.items),
        "expectedCount": batch.meta.expected_count,
        "rowCoverage": batch.meta.row_coverage,
        "requiredFieldCoverage": dict(batch.meta.required_field_coverage),
        "records": _jsonable(batch.items),
        "issues": _jsonable(batch.meta.issues),
        "reasons": reasons,
    })
    return RadarReplayEvidence(
        evidenceId=f"security-universe:{batch.meta.batch_id}",
        domain="security_universe",
        sourceId=f"{batch.meta.source}:{batch.meta.batch_id}",
        source=batch.meta.source,
        sourceTime=batch.meta.source_time,
        fetchedAt=batch.meta.fetched_at,
        effectiveFrom=None,
        status=status,
        payload=payload,
    )


_TRADING_BOARDS = (
    ("bse", "北交所", "bse:main"),
    ("sse", "主板", "sse:main"),
    ("sse", "科创板", "sse:star"),
    ("szse", "创业板", "szse:chinext"),
    ("szse", "主板", "szse:main"),
)


def adapt_trading_rules(
    *,
    sample_as_of: datetime,
    fetched_at: datetime,
) -> RadarReplayEvidence:
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={"fetchedAt": fetched_at},
    )
    rules = []
    for exchange, board, identity in _TRADING_BOARDS:
        rule = resolve_trading_rule_catalog(
            exchange=exchange,
            board=board,
            lifecycle_status=SecurityLifecycleStatus.NORMAL,
            trading_date=sample_as_of.date(),
            listed_trading_day_count=6,
            special_session=PriceLimitSpecialSession.NONE,
            previous_close=None,
        )
        rules.append({"boardIdentity": identity, **_jsonable(rule)})

    covered = sorted(item[2] for item in _TRADING_BOARDS)
    missing = []
    payload = _with_hash({
        "observationKind": "versioned_official_catalog",
        "coveredBoards": covered,
        "missingBoards": missing,
        "rules": rules,
        "reasons": [],
    })
    return RadarReplayEvidence(
        evidenceId=(
            "trading-rule-catalog:"
            f"{sample_as_of.date().isoformat()}"
        ),
        domain="trading_rule",
        sourceId="sse-szse-bse-official-trading-rule-catalog-v2",
        source="上海、深圳、北京证券交易所正式交易规则",
        sourceTime=None,
        fetchedAt=fetched_at,
        effectiveFrom=None,
        status="ready",
        payload=payload,
    )


def adapt_industry(
    snapshot: IndustryClassificationSnapshot,
    *,
    sample_as_of: datetime,
) -> RadarReplayEvidence:
    release = snapshot.release
    fetched_at = snapshot.meta.fetched_at
    effective_from = (
        release.knowledge_effective_from if release is not None else None
    )
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "sourceTime": snapshot.meta.source_time,
            "fetchedAt": fetched_at,
            "effectiveFrom": effective_from,
        },
    )
    if release is not None:
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={
                "releaseFirstObservedAt": release.first_observed_at,
                "releaseFetchedAt": release.fetched_at,
            },
        )
    for index, record in enumerate(snapshot.records):
        record_knowledge_effective_from = getattr(
            record,
            "knowledge_effective_from",
            None,
        )
        if record_knowledge_effective_from is not None:
            _validate_boundary(
                sample_as_of=sample_as_of,
                values={
                    f"recordKnowledgeEffectiveFrom[{index}]": (
                        record_knowledge_effective_from
                    ),
                },
            )
    # 阶段9是只读监测与数据质量回放，不把阶段6遗留的人工正式启用门当成
    # 数据真实性缺口。该原因不进入本域质量结论，其他覆盖/来源原因保留。
    reasons = [
        reason
        for reason in snapshot.completeness.reasons
        if reason not in {
            "formal_use_not_approved",
            "current_master_mapping_incomplete",
        }
    ]
    current_master_gaps = list(snapshot.current_master_gaps)
    excluded_symbols = sorted({
        str(getattr(item, "symbol", "") or "").strip()
        for item in current_master_gaps
        if str(getattr(item, "symbol", "") or "").strip()
    })
    safe_new_listing_gaps = bool(
        len(excluded_symbols) == len(current_master_gaps)
        and all(
            set(getattr(item, "issue_codes", ()) or ())
            == {"listed_on_or_after_classification_start"}
            for item in current_master_gaps
        )
    )
    mapping_coverage = snapshot.completeness.mapping_coverage
    shadow_usable = getattr(
        snapshot.completeness,
        "shadow_usable",
        mapping_coverage == 1.0 and not current_master_gaps,
    )
    scoped_replay_ready = bool(
        release is not None
        and shadow_usable
        and isinstance(mapping_coverage, (int, float))
        and not isinstance(mapping_coverage, bool)
        and mapping_coverage > 0
        and (not current_master_gaps or safe_new_listing_gaps)
    )
    if snapshot.status == SourceStatus.FAILED:
        status = "failed"
        reasons.append("industry_source_failed")
        scoped_replay_ready = False
    elif not scoped_replay_ready:
        status = "unverifiable"
        reasons.append("industry_mapping_incomplete")
    else:
        status = "ready"
        if current_master_gaps:
            reasons.append("industry_new_listing_explicitly_excluded")
    payload = _with_hash({
        "observationKind": "forward_observed",
        "recordCount": len(snapshot.records),
        "mappingCoverage": snapshot.completeness.mapping_coverage,
        "mappedCount": getattr(snapshot.completeness, "mapped_count", None),
        "supplementalRecordCount": getattr(
            snapshot.completeness,
            "supplemental_record_count",
            0,
        ),
        "unconfirmedCount": getattr(
            snapshot.completeness,
            "unconfirmed_count",
            len(current_master_gaps),
        ),
        "scopedReplayReady": scoped_replay_ready,
        "excludedCurrentMasterCount": len(current_master_gaps),
        "excludedSymbols": excluded_symbols,
        "release": _jsonable(release),
        "records": _jsonable(snapshot.records),
        "currentMasterGaps": _jsonable(current_master_gaps),
        "issues": _jsonable(snapshot.issues),
        "reasons": sorted(set(reasons)),
    })
    return RadarReplayEvidence(
        evidenceId=f"industry:{snapshot.meta.batch_id}",
        domain="industry",
        sourceId=f"{snapshot.meta.source}:{snapshot.meta.batch_id}",
        source=snapshot.meta.source,
        sourceTime=snapshot.meta.source_time,
        fetchedAt=fetched_at,
        effectiveFrom=effective_from,
        status=status,
        payload=payload,
    )


def adapt_index(
    result: OfficialIndexPocResult,
    *,
    sample_as_of: datetime,
) -> RadarReplayEvidence:
    methodology = result.methodology
    constituent_sets = tuple(result.constituent_sets)
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "fetchedAt": result.fetched_at,
            "effectiveFrom": methodology.effective_from,
        },
    )
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "methodologyFetchedAt": methodology.fetched_at,
            "methodologyFirstObservedAt": getattr(
                methodology,
                "first_observed_at",
                None,
            ),
        },
    )
    for item in constituent_sets:
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={
                "constituentAnnouncedAt": item.announced_at,
                "constituentEffectiveFrom": item.effective_from,
                "constituentFetchedAt": item.fetched_at,
                "constituentFirstObservedAt": getattr(
                    item,
                    "first_observed_at",
                    None,
                ),
            },
        )
    formal_ready = bool(
        methodology.formal_ready
        and constituent_sets
        and all(item.formal_ready for item in constituent_sets)
    )
    def complete_https_evidence(value) -> bool:
        url = str(getattr(value, "evidence_url", "") or "").strip()
        digest = str(
            getattr(value, "evidence_sha256", "") or ""
        ).strip()
        return (
            url.startswith("https://")
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )

    methodology_forward_ready = bool(
        complete_https_evidence(methodology)
        and str(getattr(methodology, "provider_version", "") or "").strip()
        and str(getattr(methodology, "universe_rule", "") or "").strip()
        and str(getattr(methodology, "selection_rule", "") or "").strip()
        and str(getattr(methodology, "weighting_method", "") or "").strip()
    )
    weighted_forward_sets = []
    for item in constituent_sets:
        expected_count = getattr(item, "expected_count", None)
        returned_count = getattr(item, "returned_count", None)
        weight_count = getattr(item, "weight_count", None)
        source_date = getattr(item, "source_date", None)
        weight_total = getattr(item, "weight_total", None)
        items = getattr(item, "items", ())
        if (
            complete_https_evidence(item)
            and isinstance(expected_count, int)
            and expected_count > 0
            and returned_count == expected_count
            and len(items) == expected_count
            and weight_count == expected_count
            and isinstance(weight_total, (int, float))
            and abs(float(weight_total) - 100.0) <= 0.5
            and isinstance(source_date, date)
            and source_date <= sample_as_of.date()
        ):
            weighted_forward_sets.append(item)
    forward_ready = bool(
        result.provider == "csindex"
        and str(result.identity_evidence_url).startswith("https://")
        and len(str(result.identity_evidence_sha256)) == 64
        and methodology_forward_ready
        and weighted_forward_sets
    )
    reasons = []
    if not forward_ready:
        reasons.append("index_formal_evidence_incomplete")
        if not methodology_forward_ready:
            reasons.append("index_forward_methodology_incomplete")
        if not weighted_forward_sets:
            reasons.append("index_forward_weight_snapshot_missing")
    retrospective_reasons = sorted({
        str(reason)
        for reason in (
            tuple(getattr(methodology, "reasons", ()) or ())
            + tuple(
                reason
                for item in constituent_sets
                for reason in (getattr(item, "reasons", ()) or ())
            )
        )
    })
    if not formal_ready and not retrospective_reasons:
        retrospective_reasons.append("index_formal_evidence_incomplete")
    payload = _with_hash({
        "observationKind": "forward_observed",
        "forwardReady": forward_ready,
        "retrospectiveReady": formal_ready,
        "provider": result.provider,
        "indexCode": result.index_code,
        "indexName": result.index_name,
        "identityEvidenceUrl": result.identity_evidence_url,
        "identityEvidenceSha256": result.identity_evidence_sha256,
        "methodology": _jsonable(methodology),
        "constituentSets": _jsonable(constituent_sets),
        "reasons": reasons,
        "retrospectiveReasons": retrospective_reasons,
    })
    return RadarReplayEvidence(
        evidenceId=f"index:{result.provider}:{result.index_code}",
        domain="index",
        sourceId=f"{result.provider}:{result.index_code}",
        source=result.provider,
        sourceTime=None,
        fetchedAt=result.fetched_at,
        effectiveFrom=methodology.effective_from,
        status=("ready" if forward_ready else "unverifiable"),
        payload=payload,
    )


def adapt_etf(
    batch: SourceBatch[EtfProductMasterRecord],
    *,
    sample_as_of: datetime,
) -> RadarReplayEvidence:
    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "sourceTime": batch.meta.source_time,
            "fetchedAt": batch.meta.fetched_at,
        },
    )
    for record in batch.items:
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={"recordFetchedAt": record.fetched_at},
        )
    status, reasons = _batch_status(
        batch,
        incomplete_reason="etf_product_master_incomplete",
    )
    payload = _with_hash({
        "observationKind": "forward_observed",
        "recordCount": len(batch.items),
        "expectedCount": batch.meta.expected_count,
        "rowCoverage": batch.meta.row_coverage,
        "records": _jsonable(batch.items),
        "issues": _jsonable(batch.meta.issues),
        "reasons": reasons,
    })
    return RadarReplayEvidence(
        evidenceId=f"etf-product-master:{batch.meta.batch_id}",
        domain="etf",
        sourceId=f"{batch.meta.source}:{batch.meta.batch_id}",
        source=batch.meta.source,
        sourceTime=batch.meta.source_time,
        fetchedAt=batch.meta.fetched_at,
        effectiveFrom=None,
        status=status,
        payload=payload,
    )


class CorporateActionEvidenceItem(BaseModel):
    """只接受可追溯官方原文的结构化公司行为。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    symbol: str = Field(pattern=r"^\d{6}$")
    exchange: Literal["sse", "szse", "bse"]
    action_type: Literal[
        "cash_dividend",
        "bonus_share",
        "capitalization_issue",
        "rights_issue",
        "stock_split",
        "reverse_split",
        "placement",
        "repurchase_cancellation",
        "merger",
        "demerger",
        "code_change",
    ] = Field(alias="actionType")
    new_symbol: Optional[str] = Field(
        default=None,
        alias="newSymbol",
        pattern=r"^\d{6}$",
    )
    announced_at: Optional[datetime] = Field(default=None, alias="announcedAt")
    effective_on: Optional[date] = Field(default=None, alias="effectiveOn")
    source_name: str = Field(alias="sourceName", min_length=1)
    source_url: str = Field(alias="sourceUrl", min_length=1)
    source_sha256: str = Field(
        alias="sourceSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    document_id: str = Field(alias="documentId", min_length=1)

    @field_validator("announced_at")
    @classmethod
    def require_announced_at_aware(cls, value):
        if value is not None:
            _aware(value, "corporateActionAnnouncedAt")
        return value

    @field_validator("source_url")
    @classmethod
    def require_official_https_url(cls, value):
        if not value.startswith("https://"):
            raise ValueError("corporate_action_source_https_required")
        return value

    @model_validator(mode="after")
    def require_code_change_target(self):
        if self.action_type == "code_change":
            if self.new_symbol is None or self.new_symbol == self.symbol:
                raise ValueError("corporate_action_code_change_target_invalid")
        elif self.new_symbol is not None:
            raise ValueError("corporate_action_new_symbol_unexpected")
        return self


class CorporateActionUnresolvedDocument(BaseModel):
    """官方公告候选未升格时保留的逐文档诊断。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    exchange: Literal["sse", "szse", "bse"]
    query_name: str = Field(alias="queryName", min_length=1)
    symbol: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    announcement_id: Optional[str] = Field(
        default=None,
        alias="announcementId",
        pattern=r"^\d+$",
    )
    title: Optional[str] = None
    announced_at: Optional[datetime] = Field(default=None, alias="announcedAt")
    source_url: Optional[str] = Field(default=None, alias="sourceUrl")
    source_sha256: Optional[str] = Field(
        default=None,
        alias="sourceSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str = Field(min_length=1)

    @field_validator("announced_at")
    @classmethod
    def require_announced_at_aware(cls, value):
        if value is not None:
            _aware(value, "corporateActionUnresolvedAnnouncedAt")
        return value

    @field_validator("source_url")
    @classmethod
    def require_official_source_url(cls, value):
        if value is not None and not value.startswith(
            "https://static.cninfo.com.cn/"
        ):
            raise ValueError("corporate_action_unresolved_source_unverified")
        return value


class CorporateActionForwardSnapshot(BaseModel):
    """公司行为官方来源的显式全市场前向快照合同。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_id: str = Field(alias="sourceId", min_length=1)
    source: str = Field(min_length=1)
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    effective_from: Optional[datetime] = Field(
        default=None,
        alias="effectiveFrom",
    )
    expected_count: Optional[int] = Field(default=None, alias="expectedCount", ge=0)
    covered_exchanges: List[Literal["sse", "szse", "bse"]] = Field(
        default_factory=list,
        alias="coveredExchanges",
    )
    missing_exchanges: List[Literal["sse", "szse", "bse"]] = Field(
        default_factory=lambda: ["sse", "szse", "bse"],
        alias="missingExchanges",
    )
    returned_count_by_exchange: Dict[
        Literal["sse", "szse", "bse"],
        int,
    ] = Field(default_factory=dict, alias="returnedCountByExchange")
    coverage_from_by_exchange: Dict[
        Literal["sse", "szse", "bse"],
        date,
    ] = Field(default_factory=dict, alias="coverageFromByExchange")
    coverage_through_by_exchange: Dict[
        Literal["sse", "szse", "bse"],
        date,
    ] = Field(default_factory=dict, alias="coverageThroughByExchange")
    coverage_reasons_by_exchange: Dict[
        Literal["sse", "szse", "bse"],
        List[str],
    ] = Field(default_factory=dict, alias="coverageReasonsByExchange")
    items: List[CorporateActionEvidenceItem] = Field(default_factory=list)
    unresolved_documents: List[CorporateActionUnresolvedDocument] = Field(
        default_factory=list,
        alias="unresolvedDocuments",
    )

    @field_validator("source_time", "fetched_at", "effective_from")
    @classmethod
    def require_aware(cls, value):
        if value is not None:
            _aware(value, "corporateActionTime")
        return value

    @field_validator("covered_exchanges", "missing_exchanges")
    @classmethod
    def require_unique_exchanges(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("corporate_action_exchange_duplicate")
        return value


def adapt_corporate_actions(
    snapshot: Optional[CorporateActionForwardSnapshot],
    *,
    sample_as_of: datetime,
    observed_at: Optional[datetime] = None,
    allowed_symbols: Optional[Collection[str]] = None,
) -> RadarReplayEvidence:
    _aware(sample_as_of, "sampleAsOf")
    if allowed_symbols is not None and (
        isinstance(allowed_symbols, (str, bytes))
        or any(
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isdigit()
            for symbol in allowed_symbols
        )
    ):
        raise ValueError("corporate_action_allowed_symbols_unverified")
    allowed_symbol_set = (
        set(allowed_symbols) if allowed_symbols is not None else None
    )
    if snapshot is None:
        fetched_at = observed_at or sample_as_of
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={"fetchedAt": fetched_at},
        )
        return RadarReplayEvidence(
            evidenceId=f"corporate-action:missing:{sample_as_of.isoformat()}",
            domain="corporate_action",
            sourceId="corporate-action-versioned-source-missing",
            source="公司行为版本化官方来源未接入",
            sourceTime=None,
            fetchedAt=fetched_at,
            effectiveFrom=None,
            status="missing",
            payload=_with_hash({
                "observationKind": "missing",
                "recordCount": None,
                "reasons": ["corporate_action_versioned_source_missing"],
            }),
        )

    _validate_boundary(
        sample_as_of=sample_as_of,
        values={
            "sourceTime": snapshot.source_time,
            "fetchedAt": snapshot.fetched_at,
            "effectiveFrom": snapshot.effective_from,
        },
    )
    for item in snapshot.items:
        _validate_boundary(
            sample_as_of=sample_as_of,
            values={"corporateActionAnnouncedAt": item.announced_at},
        )
    scoped_items = [
        item for item in snapshot.items
        if allowed_symbol_set is None or item.symbol in allowed_symbol_set
    ]
    excluded_items = [
        item for item in snapshot.items
        if allowed_symbol_set is not None
        and item.symbol not in allowed_symbol_set
    ]
    required_exchanges = {"sse", "szse", "bse"}
    covered_exchanges = set(snapshot.covered_exchanges)
    declared_missing = set(snapshot.missing_exchanges)
    reasons = []
    if (
        covered_exchanges != required_exchanges
        or declared_missing
        or covered_exchanges & declared_missing
    ):
        reasons.append("corporate_action_exchange_coverage_incomplete")
    count_values = snapshot.returned_count_by_exchange
    count_complete = (
        set(count_values) == covered_exchanges
        and all(value >= 0 for value in count_values.values())
        and sum(count_values.values()) == len(snapshot.items)
        and snapshot.expected_count is not None
        and snapshot.expected_count
        == len(snapshot.items) + len(snapshot.unresolved_documents)
    )
    if not count_complete:
        reasons.append("corporate_action_snapshot_incomplete")
    coverage_through = snapshot.coverage_through_by_exchange
    coverage_from = snapshot.coverage_from_by_exchange
    sample_market_date = sample_as_of.astimezone(SHANGHAI_TZ).date()
    if set(coverage_from) != covered_exchanges:
        reasons.append("corporate_action_coverage_window_start_missing")
    if (
        set(coverage_through) != covered_exchanges
        or any(
            value < sample_market_date
            for value in coverage_through.values()
        )
        or any(
            exchange in coverage_from
            and coverage_from[exchange] > sample_market_date
            for exchange in covered_exchanges
        )
        or any(
            exchange in coverage_from
            and exchange in coverage_through
            and coverage_from[exchange] > coverage_through[exchange]
            for exchange in covered_exchanges
        )
    ):
        reasons.append("corporate_action_coverage_window_incomplete")
    unresolved_documents = [
        item for item in snapshot.unresolved_documents
        if (
            allowed_symbol_set is None
            or item.symbol is None
            or item.symbol in allowed_symbol_set
        )
    ]
    excluded_unresolved_documents = [
        item for item in snapshot.unresolved_documents
        if (
            allowed_symbol_set is not None
            and item.symbol is not None
            and item.symbol not in allowed_symbol_set
        )
    ]
    safe_unresolved_reasons = (
        "_effective_date_missing",
        "_action_unverified",
    )
    safe_unresolved_documents = bool(
        unresolved_documents
        and all(
            item.symbol is not None
            and item.announcement_id is not None
            and item.title is not None
            and item.announced_at is not None
            and item.source_url is not None
            and item.source_sha256 is not None
            and item.reason.endswith(safe_unresolved_reasons)
            for item in unresolved_documents
        )
    )
    scoped_coverage_reasons = {
        exchange: [
            reason for reason in values
            if not (
                reason == f"{exchange}_company_announcement_time_missing"
                and not any(
                    item.exchange == exchange
                    and item.announced_at is None
                    for item in scoped_items
                )
            )
        ]
        for exchange, values
        in snapshot.coverage_reasons_by_exchange.items()
    }
    scoped_coverage_reasons = {
        exchange: values
        for exchange, values in scoped_coverage_reasons.items()
        if values
    }
    coverage_reason_values = {
        reason
        for values in scoped_coverage_reasons.values()
        for reason in values
    }
    allowed_scoped_coverage_reasons = {
        "sse_corporate_action_document_unverified",
        "szse_corporate_action_document_unverified",
        "bse_corporate_action_document_unverified",
    }
    coverage_scope_safe = bool(
        not coverage_reason_values
        or (
            safe_unresolved_documents
            and coverage_reason_values <= allowed_scoped_coverage_reasons
        )
    )
    if not coverage_scope_safe:
        reasons.append("corporate_action_exchange_scope_incomplete")
    if unresolved_documents and not safe_unresolved_documents:
        reasons.append("corporate_action_documents_unresolved")
    if any(item.announced_at is None for item in scoped_items):
        reasons.append("corporate_action_announced_at_missing")
    if any(item.effective_on is None for item in scoped_items):
        reasons.append("corporate_action_effective_date_missing")
    if any(
        item.exchange not in covered_exchanges
        for item in scoped_items
    ):
        reasons.append("corporate_action_item_exchange_uncovered")
    blocking_reasons = tuple(reasons)
    scoped_replay_ready = not blocking_reasons
    if safe_unresolved_documents and scoped_replay_ready:
        reasons.append("corporate_action_documents_explicitly_excluded")
    excluded_out_of_scope_count = (
        len(excluded_items) + len(excluded_unresolved_documents)
    )
    if excluded_out_of_scope_count and scoped_replay_ready:
        reasons.append("corporate_action_objects_out_of_scope")
    excluded_symbols = sorted({
        item.symbol
        for item in (
            *unresolved_documents,
            *excluded_unresolved_documents,
            *excluded_items,
        )
        if item.symbol is not None
    })
    return RadarReplayEvidence(
        evidenceId=f"corporate-action:{snapshot.source_id}",
        domain="corporate_action",
        sourceId=snapshot.source_id,
        source=snapshot.source,
        sourceTime=snapshot.source_time,
        fetchedAt=snapshot.fetched_at,
        effectiveFrom=snapshot.effective_from,
        status=("ready" if scoped_replay_ready else "unverifiable"),
        payload=_with_hash({
            "observationKind": "forward_observed",
            "scopedReplayReady": scoped_replay_ready,
            "recordCount": len(scoped_items),
            "expectedCount": snapshot.expected_count,
            "excludedDocumentCount": len(unresolved_documents),
            "excludedOutOfScopeCount": excluded_out_of_scope_count,
            "excludedSymbols": excluded_symbols,
            "coveredExchanges": sorted(covered_exchanges),
            "missingExchanges": sorted(
                required_exchanges - covered_exchanges | declared_missing
            ),
            "returnedCountByExchange": dict(
                sorted(snapshot.returned_count_by_exchange.items())
            ),
            "coverageFromByExchange": {
                exchange: value.isoformat()
                for exchange, value in sorted(
                    snapshot.coverage_from_by_exchange.items()
                )
            },
            "coverageThroughByExchange": {
                exchange: value.isoformat()
                for exchange, value in sorted(
                    snapshot.coverage_through_by_exchange.items()
                )
            },
            "coverageReasonsByExchange": {
                exchange: list(values)
                for exchange, values in sorted(
                    scoped_coverage_reasons.items()
                )
            },
            "items": _jsonable(scoped_items),
            "unresolvedDocuments": _jsonable(unresolved_documents),
            "reasons": sorted(set(reasons)),
        }),
    )
