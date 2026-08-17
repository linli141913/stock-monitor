"""把只读D8版本链桥接到当前龙头正式研究输入合同。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_input_provider_batch import (
    LeaderResearchInputProviderPlanBatchInput,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_explicit_missing_leader_research_provider_input,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchRiskAdmissionBundle,
    LeaderResearchSourceAdmissionInput,
    LeaderResearchSourceAdmissionResult,
    build_leader_research_source_admission,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskEvidenceBundleBatchEntry,
    build_leader_risk_projection_batch_from_bundles,
)
from radar.leader_risk_evidence_bundle import (
    RiskResearchEvidenceBundleInput,
    build_risk_research_evidence_bundle,
)
from radar.leader_risk_review_artifacts import (
    ManualRiskReviewArtifactInput,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_review_repository import (
    LeaderRiskReviewVersionChain,
)
from radar.leader_risk_supplemented_relation import (
    SupplementedRiskDocumentRelationInput,
    review_supplemented_risk_document_relation,
)
from radar.repository import RepositoryStateError


LEADER_FORMAL_RESEARCH_RUNTIME_BRIDGE_CONTRACT_ID = (
    "radar-leader-formal-research-runtime-bridge-v1"
)
BRIDGE_MISSING = "leader_formal_research_runtime_bridge_missing"
REVIEW_VERSIONS_MISSING = (
    "leader_formal_research_bridge_review_versions_missing"
)
REVIEW_CHAIN_AMBIGUOUS = (
    "leader_formal_research_bridge_review_chain_ambiguous"
)
FUTURE_EVIDENCE = "leader_formal_research_bridge_future_evidence"
REPOSITORY_UNAVAILABLE = (
    "leader_formal_research_bridge_repository_unavailable"
)
CHAIN_UNVERIFIED = "leader_formal_research_bridge_chain_unverified"
SOURCE_ADMISSION_UNVERIFIED = (
    "leader_formal_research_bridge_source_admission_unverified"
)
UTC = timezone.utc


class LeaderFormalResearchRuntimeBridgeStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


@dataclass(frozen=True)
class LeaderFormalResearchRuntimeBridgeItem:
    index: int
    symbol: str
    status: LeaderFormalResearchRuntimeBridgeStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    review_chain_count: int = 0
    review_version_count: int = 0

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "reviewChainCount": self.review_chain_count,
            "reviewVersionCount": self.review_version_count,
        }


@dataclass(frozen=True)
class LeaderFormalResearchRuntimeBridgeResult:
    status: LeaderFormalResearchRuntimeBridgeStatus
    radar_run_id: str
    candidate_plan_id: str
    candidate_count: int
    items: Tuple[LeaderFormalResearchRuntimeBridgeItem, ...]
    reasons: Tuple[str, ...]
    risk_projection_batch: LeaderRiskCandidateProjectionBatchResult = field(
        repr=False
    )
    source_admission: LeaderResearchSourceAdmissionResult = field(repr=False)
    provider_input: LeaderResearchInputProviderPlanBatchInput = field(
        repr=False
    )
    review_chains: Tuple[LeaderRiskReviewVersionChain, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    contract_id: str = LEADER_FORMAL_RESEARCH_RUNTIME_BRIDGE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status == LeaderFormalResearchRuntimeBridgeStatus.READY
            for item in self.items
        )

    @property
    def missing_count(self) -> int:
        return self.candidate_count - self.ready_count

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "missingCount": self.missing_count,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "riskProjectionStatus": self.risk_projection_batch.status.value,
            "sourceAdmissionStatus": self.source_admission.status.value,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _replay_chain(
    chain: LeaderRiskReviewVersionChain,
    *,
    symbol: str,
    as_of,
) -> Tuple[Tuple[Any, ...], Tuple[str, ...]]:
    versions = chain.versions
    if (
        chain.symbol != symbol
        or not isinstance(versions, tuple)
        or not versions
        or any(
            version.document.symbol != symbol
            or version.document.document_id != chain.document_id
            for version in versions
        )
    ):
        return (), (CHAIN_UNVERIFIED,)
    normalized_as_of = _aware_utc(as_of)
    version_times = tuple(
        _aware_utc(version.as_of) for version in versions
    )
    if normalized_as_of is None or any(
        value is None for value in version_times
    ):
        return (), (CHAIN_UNVERIFIED,)
    if any(
        value > normalized_as_of
        for value in version_times
        if value is not None
    ):
        return (), (FUTURE_EVIDENCE,)
    if any(
        current <= previous
        for previous, current in zip(
            version_times,
            version_times[1:],
        )
        if previous is not None and current is not None
    ):
        return (), (CHAIN_UNVERIFIED,)

    artifacts = []
    bundles = []
    for version in versions:
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
            return (), _dedupe((
                CHAIN_UNVERIFIED,
                *artifact_result.reasons,
            ))
        artifacts.append(artifact_result.artifact)
        replay_input = RiskDocumentResearchReplayInput(
            as_of=version.as_of,
            document=version.document,
            content=version.content,
            facts=version.facts,
            event_versions=version.event_versions,
            artifacts=tuple(artifacts),
        )
        replay_result = replay_risk_document_research_evidence(replay_input)
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
            return (), _dedupe((
                CHAIN_UNVERIFIED,
                *bundle_result.reasons,
            ))
        bundles.append(bundle_result.bundle)
    return tuple(bundles), ()


def _missing_entry(
    symbol: str,
    reasons: Tuple[str, ...],
) -> LeaderRiskEvidenceBundleBatchEntry:
    return LeaderRiskEvidenceBundleBatchEntry(
        symbol=symbol,
        issuer_identity=None,
        bundles=(),
        source_status=ResearchFeatureStatus.MISSING,
        reasons=reasons,
    )


def build_leader_formal_research_runtime_bridge(
    context: LeaderResearchRuntimeSourceContext,
    *,
    repository: Any,
    history_entries: Any = None,
    business_review_batch: Any = None,
    tradability_bundle: Any = None,
) -> LeaderFormalResearchRuntimeBridgeResult:
    """只读重放D8链，并通过统一来源准入生成当前计划输入。"""

    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(CHAIN_UNVERIFIED)
    plan = context.candidate_plan
    symbols = tuple(item.symbol for item in plan.items)
    repository_reason: Optional[str] = None
    try:
        chains = repository.list_review_version_chains(symbols, plan.as_of)
        if (
            not isinstance(chains, tuple)
            or any(
                not isinstance(chain, LeaderRiskReviewVersionChain)
                or chain.symbol not in symbols
                for chain in chains
            )
        ):
            repository_reason = CHAIN_UNVERIFIED
            chains = ()
    except (
        AttributeError,
        TypeError,
        ValueError,
        sqlite3.DatabaseError,
        RepositoryStateError,
    ):
        repository_reason = REPOSITORY_UNAVAILABLE
        chains = ()

    chains_by_symbol = {symbol: [] for symbol in symbols}
    for chain in chains:
        chains_by_symbol[chain.symbol].append(chain)

    entries = []
    chain_counts = {}
    version_counts = {}
    for symbol in symbols:
        symbol_chains = tuple(chains_by_symbol[symbol])
        chain_counts[symbol] = len(symbol_chains)
        version_counts[symbol] = sum(
            len(chain.versions) for chain in symbol_chains
        )
        if repository_reason is not None:
            entries.append(_missing_entry(symbol, (repository_reason,)))
            continue
        if not symbol_chains:
            entries.append(_missing_entry(
                symbol,
                (REVIEW_VERSIONS_MISSING,),
            ))
            continue
        if len(symbol_chains) != 1:
            entries.append(_missing_entry(
                symbol,
                (REVIEW_CHAIN_AMBIGUOUS,),
            ))
            continue
        chain = symbol_chains[0]
        bundles, reasons = _replay_chain(
            chain,
            symbol=symbol,
            as_of=plan.as_of,
        )
        if reasons:
            entries.append(_missing_entry(symbol, reasons))
            continue
        entries.append(LeaderRiskEvidenceBundleBatchEntry(
            symbol=symbol,
            issuer_identity=chain.versions[-1].document.issuer_identity,
            bundles=bundles,
        ))

    risk_batch = build_leader_risk_projection_batch_from_bundles(
        as_of=plan.as_of,
        entries=tuple(entries),
    )
    source_admission = build_leader_research_source_admission(
        LeaderResearchSourceAdmissionInput(
            context=context,
            history_entries=history_entries,
            business_review_batch=business_review_batch,
            tradability_bundle=tradability_bundle,
            risk_projection_bundle=LeaderResearchRiskAdmissionBundle(
                candidate_plan_id=plan.candidate_set_id,
                radar_run_id=plan.radar_run_id,
                quote_batch_id=plan.quote_batch_id,
                batch=risk_batch,
            ),
        )
    )
    provider_input = source_admission.provider_input
    admission_reason = None
    if provider_input is None:
        provider_input = build_explicit_missing_leader_research_provider_input(
            context
        )
        admission_reason = SOURCE_ADMISSION_UNVERIFIED

    items = tuple(
        LeaderFormalResearchRuntimeBridgeItem(
            index=index,
            symbol=plan_item.symbol,
            status=(
                LeaderFormalResearchRuntimeBridgeStatus.READY
                if projection_item.included
                else LeaderFormalResearchRuntimeBridgeStatus.MISSING
            ),
            reasons=(
                () if projection_item.included else projection_item.reasons
            ),
            review_chain_count=chain_counts[plan_item.symbol],
            review_version_count=version_counts[plan_item.symbol],
        )
        for index, (plan_item, projection_item) in enumerate(zip(
            plan.items,
            risk_batch.items,
        ))
    )
    missing = any(
        item.status == LeaderFormalResearchRuntimeBridgeStatus.MISSING
        for item in items
    )
    reasons = _dedupe((
        repository_reason,
        admission_reason,
        BRIDGE_MISSING if missing else None,
    ))
    return LeaderFormalResearchRuntimeBridgeResult(
        status=(
            LeaderFormalResearchRuntimeBridgeStatus.MISSING
            if missing
            else LeaderFormalResearchRuntimeBridgeStatus.READY
        ),
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(plan.items),
        items=items,
        reasons=reasons,
        risk_projection_batch=risk_batch,
        source_admission=source_admission,
        provider_input=provider_input,
        review_chains=chains,
    )
