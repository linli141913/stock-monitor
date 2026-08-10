"""阶段6L-E3风险候选投影的纯内存批次组装。

本模块逐条重算E1并输出只读投影映射和压缩批次审计。它不调用运行时、
数据库或外部来源，单条失败也不会阻断其他证券。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_candidate_projection import (
    LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
    LeaderRiskCandidateProjection,
    LeaderRiskCandidateProjectionInput,
    LeaderRiskCandidateProjectionResult,
    build_leader_risk_candidate_projection,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
    RiskResearchEvidenceBundle,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
    RiskResearchEvidenceBundleAuditInput,
    audit_risk_research_evidence_bundle_versions,
)


UTC = timezone.utc
LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID = (
    "radar-leader-risk-candidate-projection-batch-v1"
)
STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN = re.compile(
    r"[036][0-9]{5}"
)
SAFE_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_:.+-]{0,119}$")


class LeaderRiskCandidateProjectionBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionBatchEntry:
    symbol: str
    projection_input: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionBatchInput:
    as_of: datetime
    entries: Tuple[
        LeaderRiskCandidateProjectionBatchEntry,
        ...,
    ] = field(repr=False)


@dataclass(frozen=True)
class LeaderRiskEvidenceBundleBatchEntry:
    symbol: str
    issuer_identity: Optional[str]
    bundles: Tuple[RiskResearchEvidenceBundle, ...] = field(
        repr=False,
    )
    source_status: Optional[ResearchFeatureStatus] = None
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionBatchItem:
    index: int
    symbol: str
    input_symbol: Optional[str]
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    projection: Optional[
        LeaderRiskCandidateProjection
    ] = field(default=None, repr=False)
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False

    @property
    def included(self) -> bool:
        return (
            self.status == ResearchFeatureStatus.READY
            and self.projection is not None
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "inputSymbol": self.input_symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "included": self.included,
            "projectionContractId": (
                self.projection.projection_contract_id
                if self.projection is not None
                else None
            ),
        }


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionBatchResult:
    status: LeaderRiskCandidateProjectionBatchStatus
    as_of: Optional[datetime]
    input_count: int
    items: Tuple[
        LeaderRiskCandidateProjectionBatchItem,
        ...,
    ] = field(default_factory=tuple)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    batch_contract_id: str = (
        LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID
    )
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False

    @property
    def ready_count(self) -> int:
        return sum(item.included for item in self.items)

    @property
    def rejected_count(self) -> int:
        return self.input_count - self.ready_count

    @property
    def projections_by_symbol(
        self,
    ) -> Mapping[str, LeaderRiskCandidateProjection]:
        return MappingProxyType({
            item.symbol: item.projection
            for item in self.items
            if item.included and item.projection is not None
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "batchContractId": self.batch_contract_id,
            "status": self.status.value,
            "asOf": (
                self.as_of.isoformat()
                if self.as_of is not None
                else None
            ),
            "reasons": list(self.reasons),
            "inputCount": self.input_count,
            "readyCount": self.ready_count,
            "rejectedCount": self.rejected_count,
            "items": [
                item.to_evidence()
                for item in self.items
            ],
            "gate": {
                "riskFilterPassed": self.risk_filter_passed,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "appliedToD3": self.applied_to_d3,
                "appliedToD1": self.applied_to_d1,
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


def _batch_result(
    *,
    status: LeaderRiskCandidateProjectionBatchStatus,
    as_of: Optional[datetime],
    input_count: int,
    reasons: Sequence[str],
    items: Sequence[
        LeaderRiskCandidateProjectionBatchItem
    ] = (),
) -> LeaderRiskCandidateProjectionBatchResult:
    return LeaderRiskCandidateProjectionBatchResult(
        status=status,
        as_of=as_of,
        input_count=input_count,
        items=tuple(items),
        reasons=_dedupe(reasons),
    )


def _batch_item(
    *,
    index: int,
    symbol: str,
    input_symbol: Optional[str],
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    projection: Optional[LeaderRiskCandidateProjection] = None,
) -> LeaderRiskCandidateProjectionBatchItem:
    return LeaderRiskCandidateProjectionBatchItem(
        index=index,
        symbol=symbol,
        input_symbol=input_symbol,
        status=status,
        reasons=_dedupe(reasons),
        projection=projection,
    )


def _aggregate_status(
    items: Sequence[LeaderRiskCandidateProjectionBatchItem],
) -> LeaderRiskCandidateProjectionBatchStatus:
    ready_count = sum(item.included for item in items)
    if ready_count == len(items):
        return LeaderRiskCandidateProjectionBatchStatus.READY
    if ready_count:
        return LeaderRiskCandidateProjectionBatchStatus.PARTIAL
    statuses = {item.status for item in items}
    if ResearchFeatureStatus.SOURCE_FAILED in statuses:
        return LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED
    if ResearchFeatureStatus.SOURCE_UNVERIFIED in statuses:
        return (
            LeaderRiskCandidateProjectionBatchStatus
            .SOURCE_UNVERIFIED
        )
    if ResearchFeatureStatus.STALE in statuses:
        return LeaderRiskCandidateProjectionBatchStatus.STALE
    return LeaderRiskCandidateProjectionBatchStatus.MISSING


def _projection_is_valid(
    projection: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    if not isinstance(projection, LeaderRiskCandidateProjection):
        return False
    projection_as_of = _aware_utc(projection.as_of)
    return bool(
        projection.symbol == symbol
        and projection_as_of == as_of
        and (
            projection.projection_contract_id
            == LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID
        )
        and (
            projection.audit_contract_id
            == RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
        )
        and (
            projection.bundle_contract_id
            == RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
        )
        and projection.risk_filter_passed is False
        and projection.formal_gate_ready is False
        and projection.formal_usable is False
        and projection.applied_to_d3 is False
        and projection.applied_to_d1 is False
    )


def _projection_result_is_valid(
    result: LeaderRiskCandidateProjectionResult,
) -> bool:
    if not isinstance(result.status, ResearchFeatureStatus):
        return False
    if not isinstance(result.reasons, tuple) or any(
        not isinstance(reason, str) or not reason
        for reason in result.reasons
    ):
        return False
    if any((
        result.risk_filter_passed is not False,
        result.formal_gate_ready is not False,
        result.formal_usable is not False,
        result.applied_to_d3 is not False,
        result.applied_to_d1 is not False,
    )):
        return False
    if result.status == ResearchFeatureStatus.READY:
        return not result.reasons
    return result.projection is None


def build_leader_risk_candidate_projection_batch(
    input_value: Any,
) -> LeaderRiskCandidateProjectionBatchResult:
    """逐条重算E1，返回合法投影的只读映射和批次审计。"""

    if not isinstance(
        input_value,
        LeaderRiskCandidateProjectionBatchInput,
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=None,
            input_count=0,
            reasons=(
                "risk_candidate_projection_"
                "batch_contract_unverified",
            ),
        )

    entries = input_value.entries
    input_count = len(entries) if isinstance(entries, tuple) else 0
    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=None,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_as_of_timezone_missing",
            ),
        )
    if not isinstance(entries, tuple) or any(
        not isinstance(
            entry,
            LeaderRiskCandidateProjectionBatchEntry,
        )
        or not isinstance(entry.symbol, str)
        or not entry.symbol.strip()
        for entry in entries
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_contract_unverified",
            ),
        )
    if not entries:
        return _batch_result(
            status=LeaderRiskCandidateProjectionBatchStatus.MISSING,
            as_of=as_of,
            input_count=0,
            reasons=("risk_candidate_projection_batch_empty",),
        )

    mapping_symbols = tuple(entry.symbol for entry in entries)
    input_symbols = tuple(
        entry.projection_input.symbol
        for entry in entries
        if isinstance(
            entry.projection_input,
            LeaderRiskCandidateProjectionInput,
        )
        and isinstance(entry.projection_input.symbol, str)
        and entry.projection_input.symbol
    )
    if (
        len(set(mapping_symbols)) != len(mapping_symbols)
        or len(set(input_symbols)) != len(input_symbols)
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_duplicate_symbol",
            ),
        )
    if any(
        STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(symbol)
        is None
        for symbol in (*mapping_symbols, *input_symbols)
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_symbol_out_of_scope",
            ),
        )

    items = []
    for index, entry in enumerate(entries):
        projection_input = entry.projection_input
        input_symbol = (
            projection_input.symbol
            if isinstance(
                projection_input,
                LeaderRiskCandidateProjectionInput,
            )
            and isinstance(projection_input.symbol, str)
            else None
        )
        if not isinstance(
            projection_input,
            LeaderRiskCandidateProjectionInput,
        ):
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_item_contract_unverified",
                ),
            ))
            continue
        if entry.symbol != projection_input.symbol:
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_mapping_identity_mismatch",
                ),
            ))
            continue
        if _aware_utc(projection_input.as_of) != as_of:
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_as_of_mismatch",
                ),
            ))
            continue

        try:
            projection_result = (
                build_leader_risk_candidate_projection(
                    projection_input
                )
            )
        except Exception:
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_FAILED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_item_recompute_failed",
                ),
            ))
            continue

        if not isinstance(
            projection_result,
            LeaderRiskCandidateProjectionResult,
        ) or not _projection_result_is_valid(projection_result):
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_result_unverified",
                ),
            ))
            continue
        if projection_result.status != ResearchFeatureStatus.READY:
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=projection_result.status,
                reasons=projection_result.reasons,
            ))
            continue
        if not _projection_is_valid(
            projection_result.projection,
            symbol=entry.symbol,
            as_of=as_of,
        ):
            items.append(_batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=input_symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_"
                    "batch_result_unverified",
                ),
            ))
            continue
        items.append(_batch_item(
            index=index,
            symbol=entry.symbol,
            input_symbol=input_symbol,
            status=ResearchFeatureStatus.READY,
            reasons=(),
            projection=projection_result.projection,
        ))

    status = _aggregate_status(items)
    if status == LeaderRiskCandidateProjectionBatchStatus.READY:
        reasons = ()
    elif status == LeaderRiskCandidateProjectionBatchStatus.PARTIAL:
        reasons = ("risk_candidate_projection_batch_partial",)
    else:
        reasons = (
            "risk_candidate_projection_batch_no_ready_items",
        )
    return _batch_result(
        status=status,
        as_of=as_of,
        input_count=input_count,
        reasons=reasons,
        items=items,
    )


def build_leader_risk_projection_batch_from_bundles(
    *,
    as_of: Any,
    entries: Any,
) -> LeaderRiskCandidateProjectionBatchResult:
    """从连续D8证据包重算D9/E1并输出运行时可消费批次。"""

    normalized_as_of = _aware_utc(as_of)
    input_count = len(entries) if isinstance(entries, tuple) else 0
    if normalized_as_of is None:
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=None,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_as_of_timezone_missing",
            ),
        )
    if not isinstance(entries, tuple) or any(
        not isinstance(entry, LeaderRiskEvidenceBundleBatchEntry)
        or not isinstance(entry.symbol, str)
        or not entry.symbol
        or not isinstance(entry.bundles, tuple)
        or (
            entry.source_status is not None
            and (
                not isinstance(entry.source_status, ResearchFeatureStatus)
                or entry.source_status == ResearchFeatureStatus.READY
                or not entry.reasons
            )
        )
        or not isinstance(entry.reasons, tuple)
        or any(
            not isinstance(reason, str)
            or SAFE_REASON_PATTERN.fullmatch(reason) is None
            for reason in entry.reasons
        )
        or (
            bool(entry.bundles)
            and (entry.source_status is not None or bool(entry.reasons))
        )
        or (
            entry.source_status is None and bool(entry.reasons)
        )
        or (
            bool(entry.bundles)
            and (
                not isinstance(entry.issuer_identity, str)
                or not entry.issuer_identity
            )
        )
        for entry in entries
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=normalized_as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "bundle_batch_contract_unverified",
            ),
        )

    symbols = tuple(entry.symbol for entry in entries)
    if len(symbols) != len(set(symbols)):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=normalized_as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_batch_duplicate_symbol",
            ),
        )
    if any(
        STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(symbol)
        is None
        for symbol in symbols
    ):
        return _batch_result(
            status=(
                LeaderRiskCandidateProjectionBatchStatus
                .SOURCE_UNVERIFIED
            ),
            as_of=normalized_as_of,
            input_count=input_count,
            reasons=(
                "risk_candidate_projection_"
                "batch_symbol_out_of_scope",
            ),
        )

    projection_entries = []
    for entry in entries:
        audit_input = RiskResearchEvidenceBundleAuditInput(
            bundles=entry.bundles,
        )
        audit_result = (
            audit_risk_research_evidence_bundle_versions(
                audit_input
            )
        )
        projection_entries.append(
            LeaderRiskCandidateProjectionBatchEntry(
                symbol=entry.symbol,
                projection_input=LeaderRiskCandidateProjectionInput(
                    symbol=entry.symbol,
                    issuer_identity=entry.issuer_identity,
                    as_of=normalized_as_of,
                    audit_input=audit_input,
                    audit_result=audit_result,
                ),
            )
        )

    result = build_leader_risk_candidate_projection_batch(
        LeaderRiskCandidateProjectionBatchInput(
            as_of=normalized_as_of,
            entries=tuple(projection_entries),
        )
    )
    if not any(entry.source_status is not None for entry in entries):
        return result
    items = tuple(
        (
            _batch_item(
                index=index,
                symbol=entry.symbol,
                input_symbol=entry.symbol,
                status=entry.source_status,
                reasons=entry.reasons,
            )
            if entry.source_status is not None
            else result.items[index]
        )
        for index, entry in enumerate(entries)
    )
    status = _aggregate_status(items)
    reasons = (
        ()
        if status == LeaderRiskCandidateProjectionBatchStatus.READY
        else (
            ("risk_candidate_projection_batch_partial",)
            if status == LeaderRiskCandidateProjectionBatchStatus.PARTIAL
            else ("risk_candidate_projection_batch_no_ready_items",)
        )
    )
    return _batch_result(
        status=status,
        as_of=normalized_as_of,
        input_count=len(entries),
        reasons=reasons,
        items=items,
    )
