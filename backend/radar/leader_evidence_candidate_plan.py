"""阶段6候选证据核验范围。

初筛计划覆盖每个行业的研究性头部股票；本模块只从已冻结的
初筛计划、同轮轻量研究特征和显式行业状态中派生少量证据
核验对象。它不抓取公告，不生成正式分数、梯队或状态迁移。
"""

from __future__ import annotations

import math
import hashlib
import json
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_history_features import LeaderHistoryFeatureResult
from radar.leader_liquidity_features import (
    LeaderLiquidityFeatureResult,
    LiquidityResearchMetric,
)
from radar.leader_research_features import (
    LeaderResearchDimension,
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    derive_leader_runtime_candidate_plan_subset,
    is_leader_runtime_candidate_plan_valid,
)
from radar.leader_runtime_inputs import (
    LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID,
    LeaderResearchComponentBatchItem,
    LeaderRuntimeAssembly,
    build_leader_research_component_set_id,
)
from radar.leader_state_machine import (
    STATE_RANK,
    LeaderState,
    LeaderStateRecord,
)
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureResult,
)
from radar.sector_rule_readiness import (
    SECTOR_RULE_READINESS_CONTRACT_ID,
    SectorRuleReadinessItem,
    SectorRuleReadinessResult,
    SectorRuleReadinessStatus,
)
from radar.sector_rule_runtime_bridge import (
    SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID,
    SectorRuleRuntimeBridgeResult,
    SectorRuleRuntimeBridgeStatus,
)


UTC = timezone.utc
LEADER_EVIDENCE_CANDIDATE_PLAN_CONTRACT_ID = (
    "radar-leader-evidence-candidate-plan-v1"
)
LEADER_EVIDENCE_CANDIDATE_INDUSTRY_SCOPE_CONTRACT_ID = (
    "radar-leader-evidence-candidate-industry-scope-v1"
)
LEADER_EVIDENCE_CANDIDATE_SELECTION_POLICY_VERSION = (
    "radar-leader-evidence-candidate-selection-v1"
)
LEADER_EVIDENCE_INDUSTRY_STATE_SNAPSHOT_SOURCE_CONTRACT_ID = (
    "radar-leader-evidence-industry-state-snapshot-v1"
)
LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_CONTRACT_ID = (
    "radar-leader-evidence-previous-state-snapshot-v1"
)
LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_SOURCE_CONTRACT_ID = (
    "radar-leader-state-repository-snapshot-v1"
)
LEADER_EVIDENCE_CANDIDATE_ACCEPTANCE_CONTRACT_ID = (
    "radar-leader-evidence-candidate-acceptance-v1"
)
MAXIMUM_FORMAL_STOCKS_PER_STATE = 5
FORMAL_STOCK_STATE_COUNT = 3
MAXIMUM_DEFAULT_EVIDENCE_CANDIDATES = (
    MAXIMUM_FORMAL_STOCKS_PER_STATE * FORMAL_STOCK_STATE_COUNT
)
_INDUSTRY_STATE_PRODUCER_TOKEN = object()
_PREVIOUS_STATE_REPOSITORY_TOKEN = object()


class LeaderEvidenceCandidatePlanStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    BLOCKED = "blocked"


class LeaderEvidenceCandidateAcceptanceStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    BLOCKED = "blocked"


class LeaderActiveIndustryState(str, Enum):
    INACTIVE = "inactive"
    OBSERVE = "observe"
    STARTUP = "startup"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class LeaderEvidenceCandidateIndustryScopeItem:
    industry_code: str
    state: LeaderActiveIndustryState


@dataclass(frozen=True, repr=False)
class LeaderEvidenceCandidateIndustryScope:
    candidate_plan_id: str
    radar_run_id: str
    as_of: datetime
    items: Tuple[LeaderEvidenceCandidateIndustryScopeItem, ...]
    sector_rule_bridge: Any = field(repr=False)
    state_source_contract_id: str = (
        LEADER_EVIDENCE_INDUSTRY_STATE_SNAPSHOT_SOURCE_CONTRACT_ID
    )
    complete: bool = True
    state_snapshot_id: Optional[str] = None
    _producer_token: Any = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    contract_id: str = (
        LEADER_EVIDENCE_CANDIDATE_INDUSTRY_SCOPE_CONTRACT_ID
    )

    def __post_init__(self) -> None:
        if self.state_snapshot_id is None:
            object.__setattr__(
                self,
                "state_snapshot_id",
                _industry_state_snapshot_id(
                    candidate_plan_id=self.candidate_plan_id,
                    radar_run_id=self.radar_run_id,
                    as_of=self.as_of,
                    items=self.items,
                    sector_rule_bridge=self.sector_rule_bridge,
                    state_source_contract_id=(
                        self.state_source_contract_id
                    ),
                ),
            )


@dataclass(frozen=True, repr=False)
class LeaderEvidencePreviousStateSnapshot:
    candidate_plan_id: str
    as_of: datetime
    source_contract_id: str
    expected_record_count: int
    records: Tuple[LeaderStateRecord, ...] = field(repr=False)
    complete: bool
    snapshot_id: Optional[str] = None
    _repository_token: Any = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    contract_id: str = (
        LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_CONTRACT_ID
    )

    def __post_init__(self) -> None:
        if self.snapshot_id is None:
            object.__setattr__(
                self,
                "snapshot_id",
                _previous_state_snapshot_id(
                    candidate_plan_id=self.candidate_plan_id,
                    as_of=self.as_of,
                    source_contract_id=self.source_contract_id,
                    expected_record_count=self.expected_record_count,
                    records=self.records,
                    complete=self.complete,
                ),
            )


@dataclass(frozen=True)
class LeaderEvidenceCandidateSelectionPolicy:
    maximum_new_candidates: int = MAXIMUM_DEFAULT_EVIDENCE_CANDIDATES
    maximum_incumbent_candidates: int = (
        MAXIMUM_DEFAULT_EVIDENCE_CANDIDATES
    )
    version: str = LEADER_EVIDENCE_CANDIDATE_SELECTION_POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_new_candidates, int)
            or isinstance(self.maximum_new_candidates, bool)
            or not 1 <= self.maximum_new_candidates <= (
                MAXIMUM_DEFAULT_EVIDENCE_CANDIDATES
            )
            or not isinstance(self.maximum_incumbent_candidates, int)
            or isinstance(self.maximum_incumbent_candidates, bool)
            or not 1 <= self.maximum_incumbent_candidates <= (
                MAXIMUM_DEFAULT_EVIDENCE_CANDIDATES
            )
            or not isinstance(self.version, str)
            or not self.version.strip()
        ):
            raise ValueError(
                "leader_evidence_candidate_selection_policy_unverified"
            )

    @property
    def policy_id(self) -> str:
        return ":".join((
            self.version,
            f"new-{self.maximum_new_candidates}",
            f"incumbent-{self.maximum_incumbent_candidates}",
        ))


@dataclass(frozen=True)
class LeaderEvidenceCandidatePlanInput:
    preliminary_plan: Any = field(repr=False)
    runtime_assembly: Any = field(repr=False)
    industry_scope: Any = field(repr=False)
    previous_state_snapshot: Any = field(repr=False)
    policy: Any = field(
        default_factory=LeaderEvidenceCandidateSelectionPolicy
    )


@dataclass(frozen=True)
class LeaderEvidenceCandidatePlanItem:
    index: int
    symbol: str
    industry_code: str
    parent_index: int
    selection_kind: str
    research_partial_score: Optional[float]
    previous_state: Optional[LeaderState] = None

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "industryCode": self.industry_code,
            "parentIndex": self.parent_index,
            "selectionKind": self.selection_kind,
            "researchPartialScore": (
                round(self.research_partial_score, 6)
                if self.research_partial_score is not None
                else None
            ),
            "previousState": (
                self.previous_state.value
                if self.previous_state is not None
                else None
            ),
        }


@dataclass(frozen=True)
class LeaderEvidenceCandidatePlan:
    status: LeaderEvidenceCandidatePlanStatus
    preliminary_candidate_plan_id: Optional[str]
    preliminary_candidate_count: int
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = field(
        default=None,
        repr=False,
    )
    items: Tuple[LeaderEvidenceCandidatePlanItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    selection_policy_id: Optional[str] = None
    contract_id: str = LEADER_EVIDENCE_CANDIDATE_PLAN_CONTRACT_ID
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
            "preliminaryCandidatePlanId": (
                self.preliminary_candidate_plan_id
            ),
            "preliminaryCandidateCount": (
                self.preliminary_candidate_count
            ),
            "candidatePlanId": (
                self.candidate_plan.candidate_set_id
                if self.candidate_plan is not None
                else None
            ),
            "candidateCount": self.candidate_count,
            "selectionPolicyId": self.selection_policy_id,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True, repr=False)
class LeaderEvidenceCandidateAcceptanceResult:
    status: LeaderEvidenceCandidateAcceptanceStatus
    preliminary_candidate_plan_id: Optional[str]
    preliminary_candidate_count: int
    evidence_plan: Optional[LeaderEvidenceCandidatePlan] = field(
        default=None,
        repr=False,
    )
    tradability: Any = field(default=None, repr=False)
    industry_scope: Any = field(default=None, repr=False)
    industry_scope_snapshot_id: Optional[str] = None
    previous_state_snapshot_id: Optional[str] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_EVIDENCE_CANDIDATE_ACCEPTANCE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def candidate_count(self) -> int:
        return (
            self.evidence_plan.candidate_count
            if self.evidence_plan is not None
            else 0
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "preliminaryCandidatePlanId": (
                self.preliminary_candidate_plan_id
            ),
            "preliminaryCandidateCount": self.preliminary_candidate_count,
            "candidatePlanId": (
                self.evidence_plan.candidate_plan.candidate_set_id
                if self.evidence_plan is not None
                and self.evidence_plan.candidate_plan is not None
                else None
            ),
            "candidateCount": self.candidate_count,
            "industryScopeSnapshotId": self.industry_scope_snapshot_id,
            "previousStateSnapshotId": self.previous_state_snapshot_id,
            "reasons": list(self.reasons),
            "evidencePlan": (
                self.evidence_plan.to_evidence()
                if self.evidence_plan is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def is_leader_evidence_candidate_plan_valid(value: Any) -> bool:
    if (
        type(value) is not LeaderEvidenceCandidatePlan
        or value.status != LeaderEvidenceCandidatePlanStatus.READY
        or value.contract_id != LEADER_EVIDENCE_CANDIDATE_PLAN_CONTRACT_ID
        or not isinstance(value.preliminary_candidate_plan_id, str)
        or not value.preliminary_candidate_plan_id
        or not isinstance(value.preliminary_candidate_count, int)
        or isinstance(value.preliminary_candidate_count, bool)
        or value.preliminary_candidate_count < 1
        or not is_leader_runtime_candidate_plan_valid(value.candidate_plan)
        or value.candidate_plan.parent_candidate_set_id
        != value.preliminary_candidate_plan_id
        or value.candidate_plan.derivation_policy_id
        != value.selection_policy_id
        or value.preliminary_candidate_count < value.candidate_count
        or not isinstance(value.items, tuple)
        or not value.items
        or len(value.items) != value.candidate_plan.candidate_count
        or not isinstance(value.reasons, tuple)
        or value.reasons
        or not isinstance(value.selection_policy_id, str)
        or not value.selection_policy_id
        or any((
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    candidate_symbols = tuple(
        item.symbol for item in value.candidate_plan.items
    )
    for index, item in enumerate(value.items):
        if (
            type(item) is not LeaderEvidenceCandidatePlanItem
            or item.index != index
            or item.symbol != candidate_symbols[index]
            or item.industry_code
            != value.candidate_plan.items[index].industry_code
            or not isinstance(item.parent_index, int)
            or isinstance(item.parent_index, bool)
            or not 0 <= item.parent_index
            < value.preliminary_candidate_count
            or item.selection_kind not in {
                "incumbent",
                "new_candidate",
            }
            or (
                item.research_partial_score is not None
                and (
                    isinstance(item.research_partial_score, bool)
                    or not isinstance(
                        item.research_partial_score,
                        (int, float),
                    )
                    or not math.isfinite(
                        float(item.research_partial_score)
                    )
                )
            )
            or (
                item.selection_kind == "incumbent"
                and item.previous_state in {None, LeaderState.OUT}
            )
            or (
                item.selection_kind == "new_candidate"
                and item.previous_state is not None
            )
        ):
            return False
    return len(candidate_symbols) == len(set(candidate_symbols))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _narrow_tradability_calendars(
    calendars: Any,
    securities: Any,
) -> Tuple[Any, ...]:
    """子计划只保留其实际交易所日历；缺失仍交给既有合同拒绝。"""

    if not isinstance(calendars, tuple) or not isinstance(securities, tuple):
        return ()
    exchanges = {
        str(getattr(item, "exchange", "") or "").strip().lower()
        for item in securities
    }
    return tuple(
        item for item in calendars
        if str(getattr(item, "exchange", "") or "").strip().lower()
        in exchanges
    )


def _digest_id(prefix: str, payload: Mapping[str, object]) -> str:
    digest = hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _industry_state_snapshot_id(
    *,
    candidate_plan_id: Any,
    radar_run_id: Any,
    as_of: Any,
    items: Any,
    sector_rule_bridge: Any,
    state_source_contract_id: Any,
) -> str:
    normalized_as_of = _aware_utc(as_of)
    normalized_items = (
        [
            {
                "industryCode": getattr(item, "industry_code", None),
                "state": (
                    getattr(getattr(item, "state", None), "value", None)
                ),
            }
            for item in items
        ]
        if isinstance(items, tuple)
        else []
    )
    readiness = getattr(sector_rule_bridge, "readiness_result", None)
    return _digest_id(
        LEADER_EVIDENCE_CANDIDATE_INDUSTRY_SCOPE_CONTRACT_ID,
        {
            "candidatePlanId": candidate_plan_id,
            "radarRunId": radar_run_id,
            "asOf": (
                normalized_as_of.isoformat()
                if normalized_as_of is not None
                else None
            ),
            "sectorBridgeContractId": getattr(
                sector_rule_bridge,
                "contract_id",
                None,
            ),
            "sectorRuleVersion": getattr(
                readiness,
                "rule_version",
                None,
            ),
            "stateSourceContractId": state_source_contract_id,
            "items": normalized_items,
        },
    )


def _previous_state_snapshot_id(
    *,
    candidate_plan_id: Any,
    as_of: Any,
    source_contract_id: Any,
    expected_record_count: Any,
    records: Any,
    complete: Any,
) -> str:
    normalized_as_of = _aware_utc(as_of)
    normalized_records = (
        [
            {
                "symbol": getattr(record, "symbol", None),
                "state": getattr(
                    getattr(record, "state", None),
                    "value",
                    None,
                ),
                "ruleVersion": getattr(record, "rule_version", None),
                "stateAgePeriods": getattr(
                    record,
                    "state_age_periods",
                    None,
                ),
                "stateSince": (
                    _aware_utc(
                        getattr(record, "state_since", None)
                    ).isoformat()
                    if _aware_utc(
                        getattr(record, "state_since", None)
                    ) is not None
                    else None
                ),
                "lastEvaluatedAt": (
                    _aware_utc(
                        getattr(record, "last_evaluated_at", None)
                    ).isoformat()
                    if _aware_utc(
                        getattr(record, "last_evaluated_at", None)
                    ) is not None
                    else None
                ),
                "cooldownUntil": (
                    _aware_utc(
                        getattr(record, "cooldown_until", None)
                    ).isoformat()
                    if _aware_utc(
                        getattr(record, "cooldown_until", None)
                    ) is not None
                    else None
                ),
            }
            for record in records
        ]
        if isinstance(records, tuple)
        else []
    )
    return _digest_id(
        LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_CONTRACT_ID,
        {
            "candidatePlanId": candidate_plan_id,
            "asOf": (
                normalized_as_of.isoformat()
                if normalized_as_of is not None
                else None
            ),
            "sourceContractId": source_contract_id,
            "expectedRecordCount": expected_record_count,
            "complete": complete,
            "records": normalized_records,
        },
    )


def build_leader_evidence_candidate_industry_scope(
    *,
    plan: Any,
    sector_rule_bridge: Any,
    state_production: Any,
) -> LeaderEvidenceCandidateIndustryScope:
    """只把真实八阶段生产结果映射为阶段6的新候选资格。"""

    from radar.sector_state_producer import (
        _STATE_PRODUCTION_TOKEN,
        SectorLifecycleState,
        SectorStateProductionResult,
        SectorStateProductionStatus,
    )

    if (
        not is_leader_runtime_candidate_plan_valid(plan)
        or type(sector_rule_bridge) is not SectorRuleRuntimeBridgeResult
        or sector_rule_bridge.status != SectorRuleRuntimeBridgeStatus.READY
        or sector_rule_bridge.reasons
        or type(state_production) is not SectorStateProductionResult
        or state_production.status != SectorStateProductionStatus.READY
        or state_production._producer_token is not _STATE_PRODUCTION_TOKEN
        or state_production.sector_rule_bridge != sector_rule_bridge
        or state_production.candidate_plan_id != plan.candidate_set_id
        or state_production.radar_run_id != plan.radar_run_id
        or _aware_utc(state_production.as_of) != plan.as_of
        or state_production.reasons
        or state_production.next_snapshot is None
        or not isinstance(state_production.items, tuple)
    ):
        raise ValueError(
            "leader_evidence_industry_state_production_unverified"
        )
    parent_codes = tuple(sorted({
        item.industry_code for item in plan.items
    }))
    if tuple(item.industry_code for item in state_production.items) != (
        parent_codes
    ):
        raise ValueError(
            "leader_evidence_industry_state_production_unverified"
        )
    active_mapping = {
        SectorLifecycleState.OBSERVE: LeaderActiveIndustryState.OBSERVE,
        SectorLifecycleState.STARTUP: LeaderActiveIndustryState.STARTUP,
        SectorLifecycleState.CONFIRMED: LeaderActiveIndustryState.CONFIRMED,
    }
    scope = LeaderEvidenceCandidateIndustryScope(
        candidate_plan_id=plan.candidate_set_id,
        radar_run_id=plan.radar_run_id,
        as_of=plan.as_of,
        items=tuple(
            LeaderEvidenceCandidateIndustryScopeItem(
                industry_code=item.industry_code,
                state=active_mapping.get(
                    item.state,
                    LeaderActiveIndustryState.INACTIVE,
                ),
            )
            for item in state_production.items
        ),
        sector_rule_bridge=sector_rule_bridge,
    )
    object.__setattr__(
        scope,
        "_producer_token",
        _INDUSTRY_STATE_PRODUCER_TOKEN,
    )
    return scope


def load_leader_evidence_previous_state_snapshot(
    repository: Any,
    *,
    plan: Any,
) -> LeaderEvidencePreviousStateSnapshot:
    """只从现有龙头仓储的完整冻结前查询生成旧状态快照。"""

    from radar.leader_repository import LeaderRepository

    if (
        type(repository) is not LeaderRepository
        or not is_leader_runtime_candidate_plan_valid(plan)
    ):
        raise ValueError(
            "leader_evidence_previous_state_repository_unverified"
        )
    records_by_symbol = repository.get_latest_state_records_before(
        plan.as_of
    )
    if not isinstance(records_by_symbol, MappingABC):
        raise ValueError(
            "leader_evidence_previous_state_repository_unverified"
        )
    records = tuple(
        records_by_symbol[symbol]
        for symbol in sorted(records_by_symbol)
    )
    snapshot = LeaderEvidencePreviousStateSnapshot(
        candidate_plan_id=plan.candidate_set_id,
        as_of=plan.as_of,
        source_contract_id=(
            LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_SOURCE_CONTRACT_ID
        ),
        expected_record_count=len(records),
        records=records,
        complete=True,
    )
    object.__setattr__(
        snapshot,
        "_repository_token",
        _PREVIOUS_STATE_REPOSITORY_TOKEN,
    )
    return snapshot


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    *,
    status: LeaderEvidenceCandidatePlanStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    reasons: Sequence[str],
    policy: Optional[LeaderEvidenceCandidateSelectionPolicy] = None,
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = None,
    items: Sequence[LeaderEvidenceCandidatePlanItem] = (),
) -> LeaderEvidenceCandidatePlan:
    return LeaderEvidenceCandidatePlan(
        status=status,
        preliminary_candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        preliminary_candidate_count=(
            plan.candidate_count if plan is not None else 0
        ),
        candidate_plan=candidate_plan,
        items=tuple(items),
        reasons=_dedupe(reasons),
        selection_policy_id=(policy.policy_id if policy else None),
    )


def _industry_scope_codes(value: Any) -> Optional[Tuple[str, ...]]:
    bridge = getattr(value, "sector_rule_bridge", None)
    readiness = getattr(bridge, "readiness_result", None)
    if (
        type(value) is not LeaderEvidenceCandidateIndustryScope
        or value.contract_id
        != LEADER_EVIDENCE_CANDIDATE_INDUSTRY_SCOPE_CONTRACT_ID
        or value.state_source_contract_id
        != LEADER_EVIDENCE_INDUSTRY_STATE_SNAPSHOT_SOURCE_CONTRACT_ID
        or value._producer_token is not _INDUSTRY_STATE_PRODUCER_TOKEN
        or value.complete is not True
        or not isinstance(value.items, tuple)
        or type(bridge) is not SectorRuleRuntimeBridgeResult
        or bridge.contract_id != SECTOR_RULE_RUNTIME_BRIDGE_CONTRACT_ID
        or bridge.status != SectorRuleRuntimeBridgeStatus.READY
        or bridge.radar_run_id != value.radar_run_id
        or bridge.candidate_plan_id != value.candidate_plan_id
        or not isinstance(bridge.candidate_count, int)
        or isinstance(bridge.candidate_count, bool)
        or bridge.candidate_count < 1
        or bridge.reasons
        or type(readiness) is not SectorRuleReadinessResult
        or readiness.contract_id != SECTOR_RULE_READINESS_CONTRACT_ID
        or readiness.status != SectorRuleReadinessStatus.READY
        or readiness.radar_run_id != value.radar_run_id
        or _aware_utc(readiness.as_of) != _aware_utc(value.as_of)
        or not isinstance(readiness.rule_version, str)
        or not readiness.rule_version.strip()
        or not isinstance(readiness.items, tuple)
        or not readiness.items
        or any(
            type(item) is not SectorRuleReadinessItem
            or item.status != SectorRuleReadinessStatus.READY
            or item.reasons
            for item in readiness.items
        )
        or readiness.reasons
        or bridge.sector_rule_admission_value != readiness
        or value.state_snapshot_id != _industry_state_snapshot_id(
            candidate_plan_id=value.candidate_plan_id,
            radar_run_id=value.radar_run_id,
            as_of=value.as_of,
            items=value.items,
            sector_rule_bridge=bridge,
            state_source_contract_id=value.state_source_contract_id,
        )
    ):
        return None
    codes = []
    for item in value.items:
        if (
            type(item) is not LeaderEvidenceCandidateIndustryScopeItem
            or not isinstance(item.industry_code, str)
            or not item.industry_code
            or not isinstance(item.state, LeaderActiveIndustryState)
        ):
            return None
        codes.append(item.industry_code)
    if len(codes) != len(set(codes)):
        return None
    return tuple(codes)


def _industry_scope_valid(
    value: Any,
    *,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    codes = _industry_scope_codes(value)
    parent_industry_codes = {
        item.industry_code for item in plan.items
    }
    bridge = getattr(value, "sector_rule_bridge", None)
    return bool(
        codes is not None
        and value.candidate_plan_id == plan.candidate_set_id
        and value.radar_run_id == plan.radar_run_id
        and _aware_utc(value.as_of) == plan.as_of
        and bridge.candidate_count == plan.candidate_count
        and set(codes) == parent_industry_codes
    )


def is_leader_evidence_candidate_industry_scope_valid(
    value: Any,
    *,
    plan: Any,
) -> bool:
    """公开验证同轮候选计划绑定的可信行业状态范围。"""

    try:
        return bool(
            is_leader_runtime_candidate_plan_valid(plan)
            and _industry_scope_valid(value, plan=plan)
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def is_leader_evidence_candidate_industry_scope_valid_for_descendant(
    value: Any,
    *,
    plan: Any,
) -> bool:
    """验证可信行业范围与其直接派生候选计划的血缘。"""

    try:
        codes = _industry_scope_codes(value)
        plan_codes = {item.industry_code for item in plan.items}
        return bool(
            is_leader_runtime_candidate_plan_valid(plan)
            and codes is not None
            and plan.parent_candidate_set_id == value.candidate_plan_id
            and plan.radar_run_id == value.radar_run_id
            and plan.as_of == _aware_utc(value.as_of)
            and plan_codes <= set(codes)
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _assembly_valid(
    value: Any,
    *,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    if (
        type(value) is not LeaderRuntimeAssembly
        or value.status != "ready"
        or value.radar_run_id != plan.radar_run_id
        or value.quote_batch_id != plan.quote_batch_id
        or _aware_utc(value.as_of) != plan.as_of
        or not isinstance(value.evidence_items, tuple)
        or not isinstance(value.research_component_items, tuple)
        or len(value.evidence_items) != plan.candidate_count
        or len(value.research_component_items) != plan.candidate_count
    ):
        return False
    for index, (plan_item, evidence, component) in enumerate(zip(
        plan.items,
        value.evidence_items,
        value.research_component_items,
    )):
        if (
            type(component) is not LeaderResearchComponentBatchItem
            or component.index != index
            or component.symbol != plan_item.symbol
            or component.industry_code != plan_item.industry_code
            or _aware_utc(component.as_of) != plan.as_of
            or component.radar_run_id != plan.radar_run_id
            or component.quote_batch_id != plan.quote_batch_id
            or component.industry_release_id
            != plan_item.industry_release_id
            or component.quote_source_contract_id
            != plan_item.quote_source_contract_id
            or component.contract_id
            != LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID
            or component.component_set_id
            != build_leader_research_component_set_id(
                symbol=component.symbol,
                as_of=component.as_of,
                radar_run_id=component.radar_run_id,
                quote_batch_id=component.quote_batch_id,
                quote_source_contract_id=(
                    component.quote_source_contract_id
                ),
                industry_code=component.industry_code,
                industry_release_id=component.industry_release_id,
            )
            or type(component.cross_sectional_features)
            is not LeaderResearchFeatureResult
            or not isinstance(
                component.cross_sectional_features.dimensions,
                tuple,
            )
            or any(
                type(dimension) is not LeaderResearchDimension
                or not isinstance(dimension.status, ResearchFeatureStatus)
                for dimension
                in component.cross_sectional_features.dimensions
            )
            or type(component.history_features)
            is not LeaderHistoryFeatureResult
            or not isinstance(
                component.history_features.status,
                ResearchFeatureStatus,
            )
            or type(component.liquidity_features)
            is not LeaderLiquidityFeatureResult
            or not isinstance(
                component.liquidity_features.status,
                ResearchFeatureStatus,
            )
            or not isinstance(
                component.liquidity_features.metrics,
                MappingABC,
            )
            or any(
                type(metric) is not LiquidityResearchMetric
                or not isinstance(metric.status, ResearchFeatureStatus)
                for metric
                in component.liquidity_features.metrics.values()
            )
            or type(component.tradability_features)
            is not LeaderTradabilityFeatureResult
            or not isinstance(
                component.tradability_features.status,
                ResearchFeatureStatus,
            )
            or getattr(evidence, "symbol", None) != plan_item.symbol
            or getattr(evidence, "industry_code", None)
            != plan_item.industry_code
            or _aware_utc(getattr(evidence, "as_of", None))
            != plan.as_of
        ):
            return False
    return True


def _previous_state_snapshot_valid(
    value: Any,
    *,
    plan: LeaderRuntimeCandidatePlan,
) -> bool:
    if (
        type(value) is not LeaderEvidencePreviousStateSnapshot
        or value.contract_id
        != LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_CONTRACT_ID
        or value.source_contract_id
        != LEADER_EVIDENCE_PREVIOUS_STATE_SNAPSHOT_SOURCE_CONTRACT_ID
        or value._repository_token is not _PREVIOUS_STATE_REPOSITORY_TOKEN
        or value.candidate_plan_id != plan.candidate_set_id
        or _aware_utc(value.as_of) != plan.as_of
        or value.complete is not True
        or not isinstance(value.expected_record_count, int)
        or isinstance(value.expected_record_count, bool)
        or value.expected_record_count < 0
        or not isinstance(value.records, tuple)
        or value.expected_record_count != len(value.records)
        or value.snapshot_id != _previous_state_snapshot_id(
            candidate_plan_id=value.candidate_plan_id,
            as_of=value.as_of,
            source_contract_id=value.source_contract_id,
            expected_record_count=value.expected_record_count,
            records=value.records,
            complete=value.complete,
        )
    ):
        return False
    symbols = []
    for record in value.records:
        if (
            type(record) is not LeaderStateRecord
            or not isinstance(record.symbol, str)
            or not isinstance(record.state, LeaderState)
            or not isinstance(record.rule_version, str)
            or not record.rule_version.strip()
            or _aware_utc(record.last_evaluated_at) is None
            or _aware_utc(record.last_evaluated_at) > plan.as_of
        ):
            return False
        symbols.append(record.symbol)
    return len(symbols) == len(set(symbols))


def _research_score(
    component: LeaderResearchComponentBatchItem,
) -> Optional[float]:
    dimensions = {
        item.field_name: item
        for item in component.cross_sectional_features.dimensions
    }
    if (
        set(("industry_strength", "market_leadership"))
        - set(dimensions)
        or dimensions["industry_strength"].status
        != ResearchFeatureStatus.READY
        or dimensions["market_leadership"].status
        != ResearchFeatureStatus.READY
        or component.history_features.status
        != ResearchFeatureStatus.READY
        or component.tradability_features.status
        != ResearchFeatureStatus.READY
        or component.tradability_features.research_eligible is not True
    ):
        return None
    liquidity_metrics = component.liquidity_features.metrics
    if not isinstance(liquidity_metrics, MappingABC):
        return None
    for key in ("turnoverAmountCny", "turnoverRatePercent"):
        metric = liquidity_metrics.get(key)
        if (
            metric is None
            or getattr(metric, "status", None)
            != ResearchFeatureStatus.READY
        ):
            return None
    score = component.cross_sectional_features.research_partial_score
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        return None
    return float(score)


def build_leader_evidence_candidate_plan(
    input_value: Any,
) -> LeaderEvidenceCandidatePlan:
    """将行业初筛池收缩为少量证据核验对象。"""

    if not isinstance(input_value, LeaderEvidenceCandidatePlanInput):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=None,
            reasons=("leader_evidence_candidate_plan_contract_unverified",),
        )
    plan = input_value.preliminary_plan
    policy = input_value.policy
    try:
        plan_valid = is_leader_runtime_candidate_plan_valid(plan)
    except (AttributeError, KeyError, TypeError, ValueError):
        plan_valid = False
    if not plan_valid or not isinstance(
        policy,
        LeaderEvidenceCandidateSelectionPolicy,
    ):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan if isinstance(plan, LeaderRuntimeCandidatePlan) else None,
            reasons=("leader_evidence_candidate_plan_contract_unverified",),
        )
    if not _assembly_valid(input_value.runtime_assembly, plan=plan):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_runtime_assembly_unverified",),
        )
    if not _industry_scope_valid(input_value.industry_scope, plan=plan):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_industry_scope_unverified",),
        )
    if not _previous_state_snapshot_valid(
        input_value.previous_state_snapshot,
        plan=plan,
    ):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_previous_states_unverified",),
        )

    previous_states = {
        record.symbol: record
        for record in input_value.previous_state_snapshot.records
    }
    incumbent_records = tuple(
        record for record in previous_states.values()
        if record.state != LeaderState.OUT
    )
    plan_symbols = {item.symbol for item in plan.items}
    if any(record.symbol not in plan_symbols for record in incumbent_records):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_incumbent_outside_preliminary_scope",),
        )
    if len(incumbent_records) > policy.maximum_incumbent_candidates:
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_incumbent_scope_limit_exceeded",),
        )

    parent_index = {
        item.symbol: item.index for item in plan.items
    }
    components = {
        item.symbol: item
        for item in input_value.runtime_assembly.research_component_items
    }
    try:
        scores = {
            symbol: _research_score(component)
            for symbol, component in components.items()
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_runtime_assembly_unverified",),
        )
    incumbents = sorted(
        incumbent_records,
        key=lambda record: (
            -STATE_RANK[record.state],
            parent_index[record.symbol],
        ),
    )
    incumbent_symbols = {record.symbol for record in incumbents}
    active_codes = {
        item.industry_code
        for item in input_value.industry_scope.items
        if item.state != LeaderActiveIndustryState.INACTIVE
    }
    new_candidates = tuple(sorted(
        (
            item for item in plan.items
            if item.symbol not in incumbent_symbols
            and item.industry_code in active_codes
            and scores[item.symbol] is not None
        ),
        key=lambda item: (
            -scores[item.symbol],
            item.within_industry_rank,
            item.symbol,
        ),
    ))[:policy.maximum_new_candidates]

    selected = tuple(
        (
            record.symbol,
            "incumbent",
            record.state,
        )
        for record in incumbents
    ) + tuple(
        (item.symbol, "new_candidate", None)
        for item in new_candidates
    )
    if not selected:
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.EMPTY,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_scope_empty",),
        )

    selected_symbols = tuple(symbol for symbol, _, _ in selected)
    candidate_plan = derive_leader_runtime_candidate_plan_subset(
        plan,
        symbols=selected_symbols,
        derivation_policy_id=policy.policy_id,
    )
    if not is_leader_runtime_candidate_plan_valid(candidate_plan):
        return _result(
            status=LeaderEvidenceCandidatePlanStatus.BLOCKED,
            plan=plan,
            policy=policy,
            reasons=("leader_evidence_candidate_subset_unverified",),
        )
    plan_item_by_symbol = {item.symbol: item for item in plan.items}
    items = tuple(
        LeaderEvidenceCandidatePlanItem(
            index=index,
            symbol=symbol,
            industry_code=plan_item_by_symbol[symbol].industry_code,
            parent_index=parent_index[symbol],
            selection_kind=selection_kind,
            research_partial_score=scores[symbol],
            previous_state=previous_state,
        )
        for index, (symbol, selection_kind, previous_state)
        in enumerate(selected)
    )
    return _result(
        status=LeaderEvidenceCandidatePlanStatus.READY,
        plan=plan,
        policy=policy,
        candidate_plan=candidate_plan,
        items=items,
        reasons=(),
    )


def build_leader_evidence_candidate_runtime_inputs(
    runtime: Any,
    evidence_plan: Any,
):
    """将已冻结的原始运行时输入收缩到证据核验计划。"""

    from radar.leader_live_candidate_collection_batch import (
        LeaderLiveCandidateRuntimeInputs,
    )
    from radar.leader_research_runtime_provider import (
        build_leader_research_runtime_source_context,
        is_leader_research_runtime_source_context_valid,
    )

    if (
        type(runtime) is not LeaderLiveCandidateRuntimeInputs
        or not is_leader_evidence_candidate_plan_valid(evidence_plan)
        or not is_leader_runtime_candidate_plan_valid(
            runtime.candidate_plan
        )
        or runtime.candidate_plan.candidate_set_id
        != evidence_plan.preliminary_candidate_plan_id
        or runtime.candidate_plan.candidate_count
        != evidence_plan.preliminary_candidate_count
        or _aware_utc(runtime.as_of) != runtime.candidate_plan.as_of
        or not is_leader_research_runtime_source_context_valid(
            runtime.source_context
        )
        or runtime.source_context.candidate_plan
        != runtime.candidate_plan
        or any(
            item.parent_index >= runtime.candidate_plan.candidate_count
            or runtime.candidate_plan.items[item.parent_index].symbol
            != item.symbol
            or runtime.candidate_plan.items[item.parent_index].industry_code
            != item.industry_code
            for item in evidence_plan.items
        )
    ):
        raise ValueError(
            "leader_evidence_candidate_runtime_inputs_unverified"
        )
    candidate_plan = evidence_plan.candidate_plan
    try:
        source_context = build_leader_research_runtime_source_context(
            candidate_plan=candidate_plan,
            quote_batch=runtime.quote_batch,
            quote_health=runtime.quote_health,
            security_records=runtime.security_records,
            industry_records=runtime.industry_records,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "leader_evidence_candidate_runtime_inputs_unverified"
        ) from exc
    narrowed = replace(
        runtime,
        candidate_plan=candidate_plan,
        source_context=source_context,
    )
    if (
        not is_leader_research_runtime_source_context_valid(
            narrowed.source_context
        )
        or narrowed.source_context.candidate_plan != candidate_plan
    ):
        raise ValueError(
            "leader_evidence_candidate_runtime_inputs_unverified"
        )
    return narrowed


def derive_leader_evidence_candidate_tradability_acceptance(
    tradability: Any,
    evidence_plan: Any,
    *,
    sector_threshold_approval_binder: Any,
):
    """把已完成的初筛验收安全派生为证据候选子计划验收。"""

    from radar.leader_formal_research_production_provider import (
        LeaderFormalResearchProductionSourceStatus,
    )
    from radar.leader_history_production_collector import (
        collect_leader_history_production_source,
    )
    from radar.leader_live_candidate_collection_batch import (
        LeaderLiveCandidateRuntimeInputs,
    )
    from radar.leader_phase6_live_prefreeze import (
        LeaderPhase6PrefrozenInputs,
    )
    from radar.leader_research_source_admission import (
        LeaderResearchTradabilityAdmissionBundle,
    )
    from radar.leader_tradability_live_acceptance import (
        LeaderTradabilityLiveAcceptanceResult,
        LeaderTradabilityLiveAcceptanceStatus,
    )
    from radar.leader_tradability_production_collector import (
        LeaderTradabilityProductionFrozenBatch,
        collect_leader_tradability_production_source,
    )
    from radar.sector_rule_production_collector import (
        SectorRuleProductionFrozenBatch,
        collect_sector_rule_production_source,
    )
    from radar.sector_rule_runtime_bridge import (
        SectorRuleRuntimeSourceBatch,
    )
    from radar.sources.leader_tradability_public_poc import (
        PublicCompositeTradabilityQuery,
        PublicCompositeTradabilityReport,
        quote_batch_content_sha256,
        run_public_composite_tradability_poc,
    )

    parent_plan = getattr(tradability, "candidate_plan", None)
    parent_runtime = getattr(tradability, "runtime_inputs", None)
    prefrozen = getattr(tradability, "phase6_prefrozen_inputs", None)
    frozen_tradability = getattr(tradability, "frozen_batch", None)
    report = getattr(tradability, "report", None)
    parent_tradability_bundle = getattr(
        frozen_tradability,
        "source_bundle",
        None,
    )
    if (
        type(tradability) is not LeaderTradabilityLiveAcceptanceResult
        or tradability.status
        is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED
        or not is_leader_runtime_candidate_plan_valid(parent_plan)
        or type(parent_runtime) is not LeaderLiveCandidateRuntimeInputs
        or parent_runtime.candidate_plan != parent_plan
        or parent_runtime.source_context != tradability.source_context
        or not is_leader_evidence_candidate_plan_valid(evidence_plan)
        or evidence_plan.preliminary_candidate_plan_id
        != parent_plan.candidate_set_id
        or evidence_plan.preliminary_candidate_count
        != parent_plan.candidate_count
        or type(prefrozen) is not LeaderPhase6PrefrozenInputs
        or type(frozen_tradability)
        is not LeaderTradabilityProductionFrozenBatch
        or type(report) is not PublicCompositeTradabilityReport
        or type(parent_tradability_bundle)
        is not LeaderResearchTradabilityAdmissionBundle
        or parent_tradability_bundle.candidate_plan_id
        != parent_plan.candidate_set_id
        or parent_tradability_bundle.radar_run_id
        != parent_plan.radar_run_id
        or parent_tradability_bundle.quote_batch_id
        != parent_plan.quote_batch_id
        or type(parent_tradability_bundle.query)
        is not PublicCompositeTradabilityQuery
        or type(parent_tradability_bundle.report)
        is not PublicCompositeTradabilityReport
        or not callable(sector_threshold_approval_binder)
    ):
        raise ValueError(
            "leader_evidence_candidate_parent_acceptance_unverified"
        )
    narrowed_runtime = build_leader_evidence_candidate_runtime_inputs(
        parent_runtime,
        evidence_plan,
    )
    if type(narrowed_runtime) is not LeaderLiveCandidateRuntimeInputs:
        raise ValueError(
            "leader_evidence_candidate_runtime_subset_unverified"
        )
    child_plan = narrowed_runtime.candidate_plan
    expected_symbols = tuple(item.symbol for item in child_plan.items)
    sector_frozen = prefrozen.sector_rule
    sector_source_batch = getattr(sector_frozen, "source_batch", None)
    if (
        type(sector_frozen) is not SectorRuleProductionFrozenBatch
        or type(sector_source_batch) is not SectorRuleRuntimeSourceBatch
        or sector_source_batch.candidate_plan_id
        != parent_plan.candidate_set_id
        or sector_source_batch.radar_run_id != parent_plan.radar_run_id
        or sector_source_batch.quote_batch_id != parent_plan.quote_batch_id
        or _aware_utc(sector_source_batch.as_of) != parent_plan.as_of
    ):
        raise ValueError(
            "leader_evidence_candidate_sector_subset_unverified"
        )
    narrowed_prefrozen = replace(
        prefrozen,
        sector_rule=replace(
            sector_frozen,
            source_batch=replace(
                sector_source_batch,
                candidate_plan_id=child_plan.candidate_set_id,
            ),
        ),
    )
    parent_report = parent_tradability_bundle.report
    report_records_by_symbol = {
        item.symbol: item for item in parent_report.records
    }
    acceptance_records_by_symbol = {
        item.symbol: item for item in report.records
    }
    parent_symbols = tuple(item.symbol for item in parent_plan.items)
    if (
        len(report_records_by_symbol) != len(parent_report.records)
        or tuple(report_records_by_symbol) != parent_symbols
        or len(acceptance_records_by_symbol) != len(report.records)
        or tuple(acceptance_records_by_symbol) != parent_symbols
        or any(
            value != 1.0
            for value in parent_report.field_coverage.values()
        )
        or any(value != 1.0 for value in report.field_coverage.values())
    ):
        raise ValueError(
            "leader_evidence_candidate_parent_report_unverified"
        )
    child_quotes = tuple(
        narrowed_runtime.source_context.quotes_by_symbol[symbol]
        for symbol in expected_symbols
    )
    securities_by_symbol = {
        item.symbol: item
        for item in parent_tradability_bundle.query.securities
    }
    if (
        len(securities_by_symbol)
        != len(parent_tradability_bundle.query.securities)
        or tuple(securities_by_symbol) != parent_symbols
        or any(
            symbol not in securities_by_symbol
            for symbol in expected_symbols
        )
        or parent_tradability_bundle.query.quote_batch_evidence is None
    ):
        raise ValueError(
            "leader_evidence_candidate_security_subset_unverified"
        )
    child_symbols = set(expected_symbols)
    child_securities = tuple(
        securities_by_symbol[symbol] for symbol in expected_symbols
    )
    narrowed_query = replace(
        parent_tradability_bundle.query,
        securities=child_securities,
        trading_calendars=_narrow_tradability_calendars(
            parent_tradability_bundle.query.trading_calendars,
            child_securities,
        ),
        quote_batch_evidence=replace(
            parent_tradability_bundle.query.quote_batch_evidence,
            content_sha256=quote_batch_content_sha256(
                batch_id=child_plan.quote_batch_id,
                quotes=child_quotes,
            ),
        ),
    )
    child_official_observations = tuple(
        item
        for item in parent_tradability_bundle.official_observations
        if item.symbol in child_symbols
    )
    child_aggregator_observations = tuple(
        item
        for item in parent_tradability_bundle.aggregator_observations
        if item.symbol in child_symbols
    )
    narrowed_report = run_public_composite_tradability_poc(
        query=narrowed_query,
        quotes=child_quotes,
        official_observations=child_official_observations,
        aggregator_observations=child_aggregator_observations,
        executed=True,
    )
    if (
        narrowed_report.expected_count != len(expected_symbols)
        or narrowed_report.returned_count != len(expected_symbols)
        or tuple(
            item.symbol for item in narrowed_report.records
        ) != expected_symbols
        or any(
            value != 1.0
            for value in narrowed_report.field_coverage.values()
        )
    ):
        raise ValueError(
            "leader_evidence_candidate_report_subset_unverified"
        )
    narrowed_bundle = replace(
        parent_tradability_bundle,
        candidate_plan_id=child_plan.candidate_set_id,
        query=narrowed_query,
        quotes=child_quotes,
        official_observations=child_official_observations,
        aggregator_observations=child_aggregator_observations,
        report=narrowed_report,
    )
    narrowed_frozen_tradability = replace(
        frozen_tradability,
        source_bundle=narrowed_bundle,
    )
    sources = (
        collect_leader_history_production_source(
            narrowed_runtime.source_context,
            narrowed_prefrozen.history,
        ),
        collect_leader_tradability_production_source(
            narrowed_runtime.source_context,
            narrowed_frozen_tradability,
        ),
        collect_sector_rule_production_source(
            narrowed_runtime.source_context,
            narrowed_prefrozen.sector_rule,
            threshold_approval_binder=sector_threshold_approval_binder,
        ),
    )
    for source in sources:
        if (
            source.status
            != LeaderFormalResearchProductionSourceStatus.COMPLETED
            or source.symbols != expected_symbols
        ):
            raise ValueError(
                "leader_evidence_candidate_"
                f"{source.component_name}_source_unverified"
            )
    return LeaderTradabilityLiveAcceptanceResult(
        status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        radar_run_id=child_plan.radar_run_id,
        as_of=child_plan.as_of,
        candidate_plan_id=child_plan.candidate_set_id,
        candidate_count=child_plan.candidate_count,
        reasons=(),
        source_statuses=tradability.source_statuses,
        candidate_plan=child_plan,
        source_context=narrowed_runtime.source_context,
        runtime_inputs=narrowed_runtime,
        report=narrowed_report,
        frozen_batch=narrowed_frozen_tradability,
        collected_source=sources[1],
        phase6_prefrozen_inputs=narrowed_prefrozen,
    )


def _candidate_acceptance_result(
    *,
    status: LeaderEvidenceCandidateAcceptanceStatus,
    plan: Any,
    evidence_plan: Optional[LeaderEvidenceCandidatePlan] = None,
    tradability: Any = None,
    industry_scope: Any = None,
    industry_scope_snapshot_id: Optional[str] = None,
    previous_state_snapshot_id: Optional[str] = None,
    reasons: Sequence[str] = (),
) -> LeaderEvidenceCandidateAcceptanceResult:
    return LeaderEvidenceCandidateAcceptanceResult(
        status=status,
        preliminary_candidate_plan_id=(
            plan.candidate_set_id
            if is_leader_runtime_candidate_plan_valid(plan)
            else None
        ),
        preliminary_candidate_count=(
            plan.candidate_count
            if is_leader_runtime_candidate_plan_valid(plan)
            else 0
        ),
        evidence_plan=evidence_plan,
        tradability=tradability,
        industry_scope=industry_scope,
        industry_scope_snapshot_id=industry_scope_snapshot_id,
        previous_state_snapshot_id=previous_state_snapshot_id,
        reasons=_dedupe(reasons),
    )


def build_leader_phase6_evidence_candidate_acceptance(
    tradability: Any,
    *,
    industry_scope: Any,
    previous_state_snapshot: Any,
    policy: Any,
    sector_threshold_approval_binder: Any,
) -> LeaderEvidenceCandidateAcceptanceResult:
    """在抓取主营和风险前把初筛全集收敛为证据核验池。"""

    from radar.leader_live_candidate_collection_batch import (
        LeaderLiveCandidateRuntimeInputs,
    )
    from radar.leader_phase6_live_prefreeze import (
        LeaderPhase6PrefrozenInputs,
    )
    from radar.leader_tradability_live_acceptance import (
        LeaderTradabilityLiveAcceptanceResult,
        LeaderTradabilityLiveAcceptanceStatus,
    )
    from radar.leader_tradability_production_collector import (
        LeaderTradabilityProductionFrozenBatch,
    )

    plan = getattr(tradability, "candidate_plan", None)
    runtime = getattr(tradability, "runtime_inputs", None)
    prefrozen = getattr(tradability, "phase6_prefrozen_inputs", None)
    frozen_tradability = getattr(tradability, "frozen_batch", None)
    if (
        type(tradability) is not LeaderTradabilityLiveAcceptanceResult
        or tradability.status
        is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED
        or not is_leader_runtime_candidate_plan_valid(plan)
    ):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=("leader_evidence_candidate_acceptance_unverified",),
        )
    if (
        type(runtime) is not LeaderLiveCandidateRuntimeInputs
        or runtime.candidate_plan != plan
    ):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=("leader_evidence_candidate_runtime_unverified",),
        )
    if type(prefrozen) is not LeaderPhase6PrefrozenInputs:
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=("leader_evidence_candidate_prefrozen_unverified",),
        )
    if type(frozen_tradability) is not (
        LeaderTradabilityProductionFrozenBatch
    ):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=(
                "leader_evidence_candidate_tradability_frozen_unverified",
            ),
        )
    if (
        not isinstance(policy, LeaderEvidenceCandidateSelectionPolicy)
        or not callable(sector_threshold_approval_binder)
    ):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=("leader_evidence_candidate_policy_unverified",),
        )
    try:
        assembly = build_leader_evidence_scope_research_assembly(
            runtime,
            history_batch=prefrozen.history,
            tradability_batch=frozen_tradability,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        safe_reason = str(exc)
        if safe_reason not in {
            "leader_evidence_scope_runtime_unverified",
            "leader_evidence_scope_sources_unverified",
            "leader_evidence_scope_source_admission_unverified",
            "leader_evidence_scope_research_assembly_unverified",
        }:
            safe_reason = "leader_evidence_candidate_acceptance_unverified"
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=(safe_reason,),
        )
    try:
        evidence_plan = build_leader_evidence_candidate_plan(
            LeaderEvidenceCandidatePlanInput(
                preliminary_plan=plan,
                runtime_assembly=assembly,
                industry_scope=industry_scope,
                previous_state_snapshot=previous_state_snapshot,
                policy=policy,
            )
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            reasons=("leader_evidence_candidate_acceptance_unverified",),
        )
    if evidence_plan.status is LeaderEvidenceCandidatePlanStatus.EMPTY:
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            plan=plan,
            evidence_plan=evidence_plan,
            industry_scope_snapshot_id=industry_scope.state_snapshot_id,
            previous_state_snapshot_id=previous_state_snapshot.snapshot_id,
            reasons=evidence_plan.reasons,
        )
    if evidence_plan.status is not LeaderEvidenceCandidatePlanStatus.READY:
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            evidence_plan=evidence_plan,
            industry_scope_snapshot_id=industry_scope.state_snapshot_id,
            previous_state_snapshot_id=previous_state_snapshot.snapshot_id,
            reasons=evidence_plan.reasons,
        )
    try:
        narrowed = derive_leader_evidence_candidate_tradability_acceptance(
            tradability,
            evidence_plan,
            sector_threshold_approval_binder=(
                sector_threshold_approval_binder
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _candidate_acceptance_result(
            status=LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
            plan=plan,
            evidence_plan=evidence_plan,
            industry_scope_snapshot_id=industry_scope.state_snapshot_id,
            previous_state_snapshot_id=previous_state_snapshot.snapshot_id,
            reasons=("leader_evidence_candidate_acceptance_unverified",),
        )
    return _candidate_acceptance_result(
        status=LeaderEvidenceCandidateAcceptanceStatus.READY,
        plan=plan,
        evidence_plan=evidence_plan,
        tradability=narrowed,
        industry_scope=industry_scope,
        industry_scope_snapshot_id=industry_scope.state_snapshot_id,
        previous_state_snapshot_id=previous_state_snapshot.snapshot_id,
    )


def build_leader_evidence_scope_research_assembly(
    runtime: Any,
    *,
    history_batch: Any,
    tradability_batch: Any,
) -> LeaderRuntimeAssembly:
    """重放已收集的历史和可交易性，生成筛选用轻量Assembly。"""

    from radar.leader_history_production_collector import (
        LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        collect_leader_history_production_source,
    )
    from radar.leader_live_candidate_collection_batch import (
        LeaderLiveCandidateRuntimeInputs,
    )
    from radar.leader_research_runtime_provider import (
        is_leader_research_runtime_source_context_valid,
    )
    from radar.leader_research_source_admission import (
        LeaderResearchSourceAdmissionInput,
        LeaderResearchSourceAdmissionStatus,
        LeaderResearchSourceComponentStatus,
        build_leader_research_source_admission,
    )
    from radar.leader_runtime_inputs import build_leader_runtime_evidence
    from radar.leader_tradability_production_collector import (
        LEADER_TRADABILITY_PRODUCTION_SOURCE_CONTRACT_ID,
        collect_leader_tradability_production_source,
    )
    from radar.leader_formal_research_production_provider import (
        LeaderFormalResearchProductionCollectedSource,
        LeaderFormalResearchProductionSourceStatus,
    )

    if (
        type(runtime) is not LeaderLiveCandidateRuntimeInputs
        or not is_leader_runtime_candidate_plan_valid(
            runtime.candidate_plan
        )
        or not is_leader_research_runtime_source_context_valid(
            runtime.source_context
        )
        or runtime.source_context.candidate_plan
        != runtime.candidate_plan
        or _aware_utc(runtime.as_of) != runtime.candidate_plan.as_of
    ):
        raise ValueError("leader_evidence_scope_runtime_unverified")
    history_source = collect_leader_history_production_source(
        runtime.source_context,
        history_batch,
    )
    tradability_source = collect_leader_tradability_production_source(
        runtime.source_context,
        tradability_batch,
    )
    symbols = tuple(
        item.symbol for item in runtime.candidate_plan.items
    )
    for expected_name, expected_contract_id, source in (
        (
            "history",
            LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
            history_source,
        ),
        (
            "tradability",
            LEADER_TRADABILITY_PRODUCTION_SOURCE_CONTRACT_ID,
            tradability_source,
        ),
    ):
        if (
            type(source)
            is not LeaderFormalResearchProductionCollectedSource
            or source.component_name != expected_name
            or source.source_contract_id != expected_contract_id
            or source.status
            != LeaderFormalResearchProductionSourceStatus.COMPLETED
            or source.symbols != symbols
            or _aware_utc(source.source_time) is None
            or _aware_utc(source.fetched_at) is None
        ):
            raise ValueError(
                "leader_evidence_scope_sources_unverified"
            )
    admitted = build_leader_research_source_admission(
        LeaderResearchSourceAdmissionInput(
            context=runtime.source_context,
            history_entries=history_source.payload,
            business_review_batch=None,
            tradability_bundle=tradability_source.payload,
            risk_projection_bundle=None,
        )
    )
    component_statuses = {
        item.name: item.status for item in admitted.components
    }
    if (
        admitted.status
        not in {LeaderResearchSourceAdmissionStatus.PARTIAL}
        or admitted.provider_result is None
        or component_statuses.get("history")
        != LeaderResearchSourceComponentStatus.READY
        or component_statuses.get("tradability")
        != LeaderResearchSourceComponentStatus.READY
    ):
        raise ValueError(
            "leader_evidence_scope_source_admission_unverified"
        )
    provider = admitted.provider_result
    assembly = build_leader_runtime_evidence(
        as_of=runtime.as_of,
        quote_batch=runtime.quote_batch,
        quote_health=runtime.quote_health,
        market_snapshot=runtime.market_snapshot,
        sector_rows=runtime.sector_rows,
        industry_records=runtime.industry_records,
        security_records=runtime.security_records,
        history_inputs_by_symbol=provider.history_inputs_by_symbol,
        business_catalyst_inputs_by_symbol={},
        tradability_inputs_by_symbol=provider.tradability_inputs_by_symbol,
        risk_candidate_projections_by_symbol={},
    )
    if not _assembly_valid(assembly, plan=runtime.candidate_plan):
        raise ValueError(
            "leader_evidence_scope_research_assembly_unverified"
        )
    return assembly
