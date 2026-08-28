"""阶段6L-F6三级龙头运行时候选计划。

本模块只冻结候选身份和同轮来源边界，不计算任何研究特征。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryIdentityStatus,
    IndustryRecordStatus,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
)
from radar.leader_research_features import is_research_quote_eligible


UTC = timezone.utc
SYMBOL_PATTERN = re.compile(r"^\d{6}$")
MAX_CANDIDATES_PER_INDUSTRY = 5
LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID = (
    "radar-leader-runtime-candidate-plan-v1"
)
LEADER_RUNTIME_CANDIDATE_PLAN_ITEM_CONTRACT_ID = (
    "radar-leader-runtime-candidate-plan-item-v1"
)


class LeaderRuntimeCandidatePlanStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRuntimeCandidatePlanInput:
    as_of: Any
    quote_batch: Any = field(repr=False)
    quote_health: Any = field(repr=False)
    market_snapshot: Any = field(repr=False)
    sector_rows: Any = field(repr=False)
    industry_records: Any = field(repr=False)
    security_records: Any = field(repr=False)


@dataclass(frozen=True)
class LeaderRuntimeCandidatePlanItem:
    index: int
    symbol: str
    as_of: datetime
    industry_code: str
    industry_name: str
    industry_release_id: str
    within_industry_rank: int
    quote_source_contract_id: str
    sector_source_contract_id: str
    contract_id: str = LEADER_RUNTIME_CANDIDATE_PLAN_ITEM_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "asOf": self.as_of.isoformat(),
            "industryCode": self.industry_code,
            "industryReleaseId": self.industry_release_id,
            "withinIndustryRank": self.within_industry_rank,
            "quoteSourceContractId": self.quote_source_contract_id,
            "sectorSourceContractId": self.sector_source_contract_id,
            "contractId": self.contract_id,
        }


@dataclass(frozen=True)
class LeaderRuntimeCandidatePlan:
    status: LeaderRuntimeCandidatePlanStatus
    as_of: Optional[datetime]
    radar_run_id: Optional[str]
    quote_batch_id: Optional[str]
    market_source_contract_id: Optional[str] = None
    items: Tuple[LeaderRuntimeCandidatePlanItem, ...] = field(
        default_factory=tuple,
    )
    gate_reasons: Tuple[str, ...] = field(default_factory=tuple)
    scanned_count: int = 0
    mapped_count: int = 0
    candidate_set_id: Optional[str] = None
    parent_candidate_set_id: Optional[str] = None
    derivation_policy_id: Optional[str] = None
    contract_id: str = LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def candidate_count(self) -> int:
        return len(self.items)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "radarRunId": self.radar_run_id,
            "quoteBatchId": self.quote_batch_id,
            "marketSourceContractId": self.market_source_contract_id,
            "candidateSetId": self.candidate_set_id,
            "parentCandidateSetId": self.parent_candidate_set_id,
            "derivationPolicyId": self.derivation_policy_id,
            "candidateCount": self.candidate_count,
            "scannedCount": self.scanned_count,
            "mappedCount": self.mapped_count,
            "gateReasons": list(self.gate_reasons),
            "items": [item.to_evidence() for item in self.items],
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


def _candidate_set_id(
    *,
    radar_run_id: str,
    quote_batch_id: str,
    as_of: datetime,
    items: Sequence[LeaderRuntimeCandidatePlanItem],
    market_source_contract_id: str,
    parent_candidate_set_id: Optional[str] = None,
    derivation_policy_id: Optional[str] = None,
) -> str:
    payload = {
        "contractId": LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID,
        "radarRunId": radar_run_id,
        "quoteBatchId": quote_batch_id,
        "asOf": as_of.isoformat(),
        "marketSourceContractId": market_source_contract_id,
        "items": [
            {
                "index": item.index,
                "symbol": item.symbol,
                "industryCode": item.industry_code,
                "industryReleaseId": item.industry_release_id,
                "withinIndustryRank": item.within_industry_rank,
                "quoteSourceContractId": (
                    item.quote_source_contract_id
                ),
                "sectorSourceContractId": (
                    item.sector_source_contract_id
                ),
            }
            for item in items
        ],
    }
    if parent_candidate_set_id is not None:
        payload["parentCandidateSetId"] = parent_candidate_set_id
        payload["derivationPolicyId"] = derivation_policy_id
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"{LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID}:{digest}"


def _result(
    *,
    status: LeaderRuntimeCandidatePlanStatus,
    as_of: Optional[datetime],
    radar_run_id: Optional[str],
    quote_batch_id: Optional[str],
    reasons: Sequence[str],
    scanned_count: int = 0,
    mapped_count: int = 0,
    items: Sequence[LeaderRuntimeCandidatePlanItem] = (),
    market_source_contract_id: Optional[str] = None,
    parent_candidate_set_id: Optional[str] = None,
    derivation_policy_id: Optional[str] = None,
) -> LeaderRuntimeCandidatePlan:
    frozen_items = tuple(items)
    candidate_set_id = (
        _candidate_set_id(
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            as_of=as_of,
            items=frozen_items,
            market_source_contract_id=market_source_contract_id,
            parent_candidate_set_id=parent_candidate_set_id,
            derivation_policy_id=derivation_policy_id,
        )
        if (
            status == LeaderRuntimeCandidatePlanStatus.READY
            and as_of is not None
            and radar_run_id is not None
            and quote_batch_id is not None
            and market_source_contract_id is not None
        )
        else None
    )
    return LeaderRuntimeCandidatePlan(
        status=status,
        as_of=as_of,
        radar_run_id=radar_run_id,
        quote_batch_id=quote_batch_id,
        market_source_contract_id=market_source_contract_id,
        items=frozen_items,
        gate_reasons=_dedupe(reasons),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
        candidate_set_id=candidate_set_id,
        parent_candidate_set_id=parent_candidate_set_id,
        derivation_policy_id=derivation_policy_id,
    )


def _accepted_industries(records: Sequence[Any]):
    accepted = {}
    conflicts = set()
    for record in records:
        if (
            getattr(record, "record_status", None)
            != IndustryRecordStatus.ACCEPTED
            or getattr(record, "identity_status", None)
            == IndustryIdentityStatus.UNRESOLVED
            or getattr(record, "security_identity", None) is None
        ):
            continue
        current = accepted.get(record.security_identity)
        if (
            current is not None
            and current.division_code != record.division_code
        ):
            conflicts.add(record.security_identity)
            continue
        accepted[record.security_identity] = record
    for symbol in conflicts:
        accepted.pop(symbol, None)
    return accepted, tuple(sorted(conflicts))


def is_leader_runtime_candidate_plan_valid(value: Any) -> bool:
    if (
        not isinstance(value, LeaderRuntimeCandidatePlan)
        or value.status != LeaderRuntimeCandidatePlanStatus.READY
        or value.contract_id != LEADER_RUNTIME_CANDIDATE_PLAN_CONTRACT_ID
        or _aware_utc(value.as_of) is None
        or not isinstance(value.radar_run_id, str)
        or not value.radar_run_id
        or not isinstance(value.quote_batch_id, str)
        or not value.quote_batch_id
        or not isinstance(value.market_source_contract_id, str)
        or not value.market_source_contract_id
        or not isinstance(value.items, tuple)
        or not value.items
        or not isinstance(value.gate_reasons, tuple)
        or (
            (value.parent_candidate_set_id is None)
            != (value.derivation_policy_id is None)
        )
        or (
            value.parent_candidate_set_id is not None
            and (
                not isinstance(value.parent_candidate_set_id, str)
                or not value.parent_candidate_set_id.strip()
                or not isinstance(value.derivation_policy_id, str)
                or not value.derivation_policy_id.strip()
            )
        )
        or any(
            not isinstance(reason, str) or not reason
            for reason in value.gate_reasons
        )
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    as_of = _aware_utc(value.as_of)
    symbols = []
    for index, item in enumerate(value.items):
        if (
            not isinstance(item, LeaderRuntimeCandidatePlanItem)
            or item.index != index
            or item.contract_id
            != LEADER_RUNTIME_CANDIDATE_PLAN_ITEM_CONTRACT_ID
            or not isinstance(item.symbol, str)
            or SYMBOL_PATTERN.fullmatch(item.symbol) is None
            or _aware_utc(item.as_of) != as_of
            or not isinstance(item.industry_code, str)
            or not item.industry_code
            or not isinstance(item.industry_name, str)
            or not item.industry_name
            or not isinstance(item.industry_release_id, str)
            or not item.industry_release_id
            or item.within_industry_rank < 1
            or item.within_industry_rank > MAX_CANDIDATES_PER_INDUSTRY
            or item.quote_source_contract_id
            != f"tencent-full-market-quote-v1:{value.quote_batch_id}"
            or not isinstance(item.sector_source_contract_id, str)
            or not item.sector_source_contract_id
        ):
            return False
        symbols.append(item.symbol)
    if len(set(symbols)) != len(symbols):
        return False
    return value.candidate_set_id == _candidate_set_id(
        radar_run_id=value.radar_run_id,
        quote_batch_id=value.quote_batch_id,
        as_of=as_of,
        items=value.items,
        market_source_contract_id=value.market_source_contract_id,
        parent_candidate_set_id=value.parent_candidate_set_id,
        derivation_policy_id=value.derivation_policy_id,
    )


def derive_leader_runtime_candidate_plan_subset(
    parent_plan: Any,
    *,
    symbols: Any,
    derivation_policy_id: Any,
) -> LeaderRuntimeCandidatePlan:
    """从已验证的初筛计划派生有序子集，不改写来源身份。"""

    if (
        not is_leader_runtime_candidate_plan_valid(parent_plan)
        or not isinstance(symbols, tuple)
        or not symbols
        or not isinstance(derivation_policy_id, str)
        or not derivation_policy_id.strip()
        or len(symbols) != len(set(symbols))
        or any(
            not isinstance(symbol, str)
            or SYMBOL_PATTERN.fullmatch(symbol) is None
            for symbol in symbols
        )
    ):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=None,
            radar_run_id=None,
            quote_batch_id=None,
            reasons=("leader_candidate_subset_contract_unverified",),
        )
    by_symbol = {item.symbol: item for item in parent_plan.items}
    if any(symbol not in by_symbol for symbol in symbols):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=parent_plan.as_of,
            radar_run_id=parent_plan.radar_run_id,
            quote_batch_id=parent_plan.quote_batch_id,
            reasons=("leader_candidate_subset_scope_unverified",),
            scanned_count=parent_plan.scanned_count,
            mapped_count=parent_plan.mapped_count,
            market_source_contract_id=(
                parent_plan.market_source_contract_id
            ),
        )
    items = tuple(
        replace(by_symbol[symbol], index=index)
        for index, symbol in enumerate(symbols)
    )
    return _result(
        status=LeaderRuntimeCandidatePlanStatus.READY,
        as_of=parent_plan.as_of,
        radar_run_id=parent_plan.radar_run_id,
        quote_batch_id=parent_plan.quote_batch_id,
        reasons=parent_plan.gate_reasons,
        scanned_count=parent_plan.scanned_count,
        mapped_count=parent_plan.mapped_count,
        items=items,
        market_source_contract_id=parent_plan.market_source_contract_id,
        parent_candidate_set_id=parent_plan.candidate_set_id,
        derivation_policy_id=derivation_policy_id,
    )


def build_leader_runtime_candidate_plan(
    input_value: Any,
) -> LeaderRuntimeCandidatePlan:
    """按现有运行时口径冻结候选身份，不构建研究特征。"""

    if not isinstance(input_value, LeaderRuntimeCandidatePlanInput):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=None,
            radar_run_id=None,
            quote_batch_id=None,
            reasons=("leader_candidate_plan_contract_unverified",),
        )
    as_of = _aware_utc(input_value.as_of)
    quote_batch = input_value.quote_batch
    quote_health = input_value.quote_health
    if (
        as_of is None
        or not isinstance(quote_batch, SourceBatch)
        or _aware_utc(quote_batch.meta.as_of) != as_of
        or not isinstance(quote_health, SourceHealthResult)
    ):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=as_of,
            radar_run_id=getattr(
                getattr(quote_batch, "meta", None),
                "radar_run_id",
                None,
            ),
            quote_batch_id=getattr(
                getattr(quote_batch, "meta", None),
                "batch_id",
                None,
            ),
            reasons=("leader_candidate_plan_contract_unverified",),
        )
    radar_run_id = quote_batch.meta.radar_run_id
    quote_batch_id = quote_batch.meta.batch_id
    scanned_count = len(quote_batch.items)
    if (
        quote_health.status != SourceStatus.HEALTHY
        or not quote_health.allows_new_state
    ):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("quote_source_not_healthy", *quote_health.reasons),
            scanned_count=scanned_count,
        )
    market_snapshot = input_value.market_snapshot
    if not isinstance(market_snapshot, MappingABC):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("market_snapshot_missing",),
            scanned_count=scanned_count,
        )
    market_as_of = _aware_utc(market_snapshot.get("asOf"))
    if market_as_of is None:
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("market_snapshot_as_of_missing",),
            scanned_count=scanned_count,
        )
    if market_as_of > as_of:
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("market_snapshot_from_future",),
            scanned_count=scanned_count,
        )
    market_run_id = market_snapshot.get("radarRunId")
    if not isinstance(market_run_id, str) or not market_run_id:
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("market_snapshot_contract_unverified",),
            scanned_count=scanned_count,
        )
    market_source_contract_id = (
        f"radar-market-aggregate-v1:{market_run_id}"
    )
    sector_rows = input_value.sector_rows
    if (
        not isinstance(sector_rows, (tuple, list))
        or not sector_rows
        or any(not isinstance(row, MappingABC) for row in sector_rows)
    ):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("sector_snapshot_missing",),
            scanned_count=scanned_count,
        )
    if any(
        _aware_utc(row.get("asOf")) is None
        or _aware_utc(row.get("asOf")) > as_of
        for row in sector_rows
    ):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("sector_snapshot_from_future",),
            scanned_count=scanned_count,
        )
    if not isinstance(input_value.industry_records, (tuple, list)):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("leader_candidate_plan_contract_unverified",),
            scanned_count=scanned_count,
        )
    if not isinstance(input_value.security_records, (tuple, list)):
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.BLOCKED,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=("leader_candidate_plan_contract_unverified",),
            scanned_count=scanned_count,
        )

    industry_by_symbol, conflicts = _accepted_industries(
        input_value.industry_records
    )
    if not industry_by_symbol:
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=(
                "industry_mapping_missing",
                "industry_mapping_conflict" if conflicts else None,
            ),
            scanned_count=scanned_count,
        )
    security_by_symbol = {
        record.symbol: record
        for record in input_value.security_records
        if getattr(record, "exchange", None) in {"sse", "szse"}
    }
    quote_by_symbol = {
        quote.symbol: quote
        for quote in quote_batch.items
        if quote.symbol in security_by_symbol
    }
    sector_by_code = {
        str(row["divisionCode"]): row
        for row in sector_rows
        if row.get("divisionCode") is not None
    }
    grouped = {}
    for symbol, industry in industry_by_symbol.items():
        quote = quote_by_symbol.get(symbol)
        if (
            quote is None
            or not is_research_quote_eligible(quote, as_of)
            or industry.division_code not in sector_by_code
        ):
            continue
        grouped.setdefault(industry.division_code, []).append(quote)

    mapped_count = sum(len(values) for values in grouped.values())
    items = []
    source_contract_id = (
        f"tencent-full-market-quote-v1:{quote_batch_id}"
    )
    for division_code in sorted(grouped):
        industry_quotes = sorted(
            grouped[division_code],
            key=lambda quote: (
                -float(quote.change_percent),
                quote.symbol,
            ),
        )[:MAX_CANDIDATES_PER_INDUSTRY]
        sector = sector_by_code[division_code]
        sector_run_id = sector.get("radarRunId")
        if not isinstance(sector_run_id, str) or not sector_run_id:
            return _result(
                status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
                as_of=as_of,
                radar_run_id=radar_run_id,
                quote_batch_id=quote_batch_id,
                reasons=("sector_snapshot_contract_unverified",),
                scanned_count=scanned_count,
                mapped_count=mapped_count,
            )
        for rank, quote in enumerate(industry_quotes, start=1):
            industry = industry_by_symbol[quote.symbol]
            items.append(LeaderRuntimeCandidatePlanItem(
                index=len(items),
                symbol=quote.symbol,
                as_of=as_of,
                industry_code=industry.division_code,
                industry_name=industry.division_name,
                industry_release_id=str(sector["industryReleaseId"]),
                within_industry_rank=rank,
                quote_source_contract_id=source_contract_id,
                sector_source_contract_id=(
                    "radar-sector-aggregate-v1:"
                    f"{sector_run_id}:{division_code}"
                ),
            ))
    if not items:
        return _result(
            status=LeaderRuntimeCandidatePlanStatus.NOT_READY,
            as_of=as_of,
            radar_run_id=radar_run_id,
            quote_batch_id=quote_batch_id,
            reasons=(
                "leader_candidate_input_empty",
                "industry_mapping_conflict" if conflicts else None,
            ),
            scanned_count=scanned_count,
            mapped_count=mapped_count,
        )
    return _result(
        status=LeaderRuntimeCandidatePlanStatus.READY,
        as_of=as_of,
        radar_run_id=radar_run_id,
        quote_batch_id=quote_batch_id,
        reasons=("industry_mapping_conflict",) if conflicts else (),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
        items=items,
        market_source_contract_id=market_source_contract_id,
    )
