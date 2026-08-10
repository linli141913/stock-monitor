"""阶段6主营催化官方结构化材料的纯计算输入适配器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from radar.leader_business_catalyst_features import (
    BusinessEvidenceSourceKind,
    BusinessProofType,
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessProof,
    LeaderCatalystReference,
    build_leader_business_catalyst_features,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


LEADER_BUSINESS_OFFICIAL_MATERIAL_ADAPTER_CONTRACT_ID = (
    "radar-leader-business-official-material-adapter-v1"
)
LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID = (
    "radar-leader-business-official-material-batch-v1"
)
STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN = re.compile(
    r"[036][0-9]{5}"
)


class OfficialDisclosurePlatform(str, Enum):
    SSE = "sse"
    SZSE = "szse"
    CNINFO = "cninfo"


class LeaderOfficialBusinessMaterialBatchStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"


OFFICIAL_BUSINESS_SOURCE_CONTRACTS: Mapping[
    OfficialDisclosurePlatform,
    str,
] = MappingProxyType({
    OfficialDisclosurePlatform.SSE: (
        "radar-leader-business-official-sse-v1"
    ),
    OfficialDisclosurePlatform.SZSE: (
        "radar-leader-business-official-szse-v1"
    ),
    OfficialDisclosurePlatform.CNINFO: (
        "radar-leader-business-official-cninfo-v1"
    ),
})

_PLATFORM_SOURCE = MappingProxyType({
    OfficialDisclosurePlatform.SSE: (
        BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE,
        "上海证券交易所",
        "sse.com.cn",
    ),
    OfficialDisclosurePlatform.SZSE: (
        BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE,
        "深圳证券交易所",
        "szse.cn",
    ),
    OfficialDisclosurePlatform.CNINFO: (
        BusinessEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM,
        "巨潮资讯",
        "cninfo.com.cn",
    ),
})


@dataclass(frozen=True)
class LeaderOfficialCatalystArtifact:
    platform: OfficialDisclosurePlatform
    source_contract_id: str
    catalyst_id: str
    industry_code: str
    industry_release_id: str
    document_id: str
    document_version: str
    source_url: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    summary: str


@dataclass(frozen=True)
class LeaderOfficialBusinessProofArtifact:
    platform: OfficialDisclosurePlatform
    source_contract_id: str
    evidence_id: str
    evidence_version: str
    symbol: str
    proof_type: BusinessProofType
    document_id: str
    document_version: str
    source_url: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    fact_summary: str


@dataclass(frozen=True)
class LeaderOfficialMaterialAdapterResult:
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = field(
        default=None,
        repr=False,
    )
    contract_id: str = (
        LEADER_BUSINESS_OFFICIAL_MATERIAL_ADAPTER_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "inputReady": self.input_value is not None,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True)
class LeaderOfficialBusinessMaterialBatchEntry:
    symbol: str
    catalyst_artifact: Any = field(repr=False)
    proof_artifacts: Any = field(repr=False)
    source_status: Any


@dataclass(frozen=True)
class LeaderOfficialBusinessMaterialBatchItem:
    index: int
    symbol: str
    status: ResearchFeatureStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = field(
        default=None,
        repr=False,
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "inputReady": self.input_value is not None,
        }


@dataclass(frozen=True)
class LeaderOfficialBusinessMaterialBatchResult:
    status: LeaderOfficialBusinessMaterialBatchStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    items: Tuple[LeaderOfficialBusinessMaterialBatchItem, ...] = field(
        default_factory=tuple,
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = (
        LEADER_BUSINESS_OFFICIAL_MATERIAL_BATCH_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def inputs_by_symbol(
        self,
    ) -> Mapping[str, LeaderBusinessCatalystFeatureInput]:
        return MappingProxyType({
            item.symbol: item.input_value
            for item in self.items
            if (
                item.status == ResearchFeatureStatus.READY
                and item.input_value is not None
            )
        })

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    input_value: Optional[LeaderBusinessCatalystFeatureInput] = None,
) -> LeaderOfficialMaterialAdapterResult:
    return LeaderOfficialMaterialAdapterResult(
        status=status,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        input_value=input_value,
    )


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _artifact_times_valid(value: Any) -> bool:
    return bool(
        _aware(value.published_at)
        and _aware(value.effective_from)
        and (
            value.effective_until is None
            or _aware(value.effective_until)
        )
    )


def _source_identity(
    platform: Any,
    source_contract_id: Any,
    source_url: Any,
) -> Optional[Tuple[BusinessEvidenceSourceKind, str]]:
    if (
        not isinstance(platform, OfficialDisclosurePlatform)
        or source_contract_id
        != OFFICIAL_BUSINESS_SOURCE_CONTRACTS.get(platform)
        or not _required_text(source_url)
    ):
        return None
    source_kind, source_name, allowed_domain = _PLATFORM_SOURCE[platform]
    try:
        parts = urlsplit(source_url.strip())
        port = parts.port
    except (TypeError, ValueError):
        return None
    hostname = str(parts.hostname or "").lower().rstrip(".")
    if any((
        parts.scheme != "https",
        not hostname,
        parts.username is not None,
        parts.password is not None,
        bool(parts.query),
        bool(parts.fragment),
        port is not None,
        not (
            hostname == allowed_domain
            or hostname.endswith(f".{allowed_domain}")
        ),
    )):
        return None
    return source_kind, source_name


def _artifact_contract_valid(value: Any) -> bool:
    if isinstance(value, LeaderOfficialCatalystArtifact):
        return all((
            _required_text(value.catalyst_id),
            _required_text(value.industry_code),
            _required_text(value.industry_release_id),
            _required_text(value.document_id),
            _required_text(value.document_version),
            _required_text(value.summary),
        ))
    if isinstance(value, LeaderOfficialBusinessProofArtifact):
        return all((
            _required_text(value.evidence_id),
            _required_text(value.evidence_version),
            _required_text(value.symbol),
            isinstance(value.proof_type, BusinessProofType),
            _required_text(value.document_id),
            _required_text(value.document_version),
            value.document_version == value.evidence_version,
            _required_text(value.fact_summary),
        ))
    return False


def build_leader_business_catalyst_input_from_official_artifacts(
    *,
    as_of: Any,
    symbol: Any,
    industry_code: Any,
    industry_release_id: Any,
    catalyst_artifact: Any,
    proof_artifacts: Any,
    source_status: Any,
) -> LeaderOfficialMaterialAdapterResult:
    """把固定官方来源工件转换为无关系推断的既有研究输入。"""

    if not isinstance(source_status, ResearchFeatureStatus):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_source_status_unverified",),
        )
    if source_status == ResearchFeatureStatus.SOURCE_FAILED:
        return _result(
            ResearchFeatureStatus.SOURCE_FAILED,
            ("official_business_material_source_failed",),
        )
    if source_status == ResearchFeatureStatus.MISSING:
        return _result(
            ResearchFeatureStatus.MISSING,
            ("official_business_material_missing",),
        )
    if source_status != ResearchFeatureStatus.READY:
        return _result(
            source_status,
            ("official_business_material_source_not_ready",),
        )
    if not isinstance(proof_artifacts, tuple):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_contract_unverified",),
        )
    if not proof_artifacts:
        return _result(
            ResearchFeatureStatus.MISSING,
            ("official_business_material_proof_missing",),
        )
    if (
        not _artifact_contract_valid(catalyst_artifact)
        or any(
            not _artifact_contract_valid(artifact)
            for artifact in proof_artifacts
        )
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_contract_unverified",),
        )
    if not _aware(as_of) or any(
        not _artifact_times_valid(artifact)
        for artifact in (catalyst_artifact, *proof_artifacts)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_time_unverified",),
        )
    if (
        not _required_text(symbol)
        or not _required_text(industry_code)
        or not _required_text(industry_release_id)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_identity_mismatch",),
        )
    if STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(symbol) is None:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_symbol_out_of_scope",),
        )
    if (
        catalyst_artifact.industry_code != industry_code
        or catalyst_artifact.industry_release_id != industry_release_id
        or any(artifact.symbol != symbol for artifact in proof_artifacts)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_identity_mismatch",),
        )
    all_artifacts = (catalyst_artifact, *proof_artifacts)
    source_identities = tuple(
        _source_identity(
            artifact.platform,
            artifact.source_contract_id,
            artifact.source_url,
        )
        for artifact in all_artifacts
    )
    if any(identity is None for identity in source_identities):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_source_unverified",),
        )
    document_ids = tuple(
        artifact.document_id for artifact in all_artifacts
    )
    if len(document_ids) != len(set(document_ids)):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("official_business_material_document_duplicate",),
        )

    catalyst_source_kind, catalyst_source_name = source_identities[0]
    catalyst = LeaderCatalystReference(
        catalyst_id=catalyst_artifact.catalyst_id,
        industry_code=catalyst_artifact.industry_code,
        industry_release_id=catalyst_artifact.industry_release_id,
        source_kind=catalyst_source_kind,
        source_name=catalyst_source_name,
        source_url=catalyst_artifact.source_url,
        document_id=catalyst_artifact.document_id,
        published_at=catalyst_artifact.published_at,
        effective_from=catalyst_artifact.effective_from,
        effective_until=catalyst_artifact.effective_until,
        summary=catalyst_artifact.summary,
        source_contract_id=catalyst_artifact.source_contract_id,
        document_version=catalyst_artifact.document_version,
    )
    proofs = tuple(
        LeaderBusinessProof(
            evidence_id=artifact.evidence_id,
            evidence_version=artifact.evidence_version,
            symbol=artifact.symbol,
            proof_type=artifact.proof_type,
            source_kind=source_identity[0],
            source_name=source_identity[1],
            source_url=artifact.source_url,
            document_id=artifact.document_id,
            published_at=artifact.published_at,
            effective_from=artifact.effective_from,
            effective_until=artifact.effective_until,
            related_catalyst_ids=(),
            fact_summary=artifact.fact_summary,
            source_contract_id=artifact.source_contract_id,
        )
        for artifact, source_identity in zip(
            proof_artifacts,
            source_identities[1:],
        )
    )
    input_value = LeaderBusinessCatalystFeatureInput(
        as_of=as_of,
        symbol=symbol,
        industry_code=industry_code,
        industry_release_id=industry_release_id,
        catalyst=catalyst,
        business_proofs=proofs,
        reviews=(),
        source_status=ResearchFeatureStatus.READY,
    )
    feature = build_leader_business_catalyst_features(input_value)
    if not (
        feature.status == ResearchFeatureStatus.SOURCE_UNVERIFIED
        and feature.reasons == ("business_relation_unconfirmed",)
    ):
        return _result(feature.status, feature.reasons)
    return _result(
        ResearchFeatureStatus.READY,
        ("official_business_material_input_ready",),
        input_value=input_value,
    )


def _batch_result(
    *,
    status: LeaderOfficialBusinessMaterialBatchStatus,
    candidate_plan_id: Optional[str],
    candidate_count: int,
    reasons: Sequence[str],
    items: Sequence[LeaderOfficialBusinessMaterialBatchItem] = (),
) -> LeaderOfficialBusinessMaterialBatchResult:
    return LeaderOfficialBusinessMaterialBatchResult(
        status=status,
        candidate_plan_id=candidate_plan_id,
        candidate_count=candidate_count,
        items=tuple(items),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def build_leader_business_catalyst_inputs_from_official_artifacts_batch(
    *,
    candidate_plan: Any,
    entries: Any,
) -> LeaderOfficialBusinessMaterialBatchResult:
    """按候选计划批量装配官方主营材料，逐只保留失败状态。"""

    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or not isinstance(entries, tuple)
        or any(
            not isinstance(
                entry,
                LeaderOfficialBusinessMaterialBatchEntry,
            )
            or not _required_text(entry.symbol)
            for entry in entries
        )
    ):
        return _batch_result(
            status=LeaderOfficialBusinessMaterialBatchStatus.BLOCKED,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=(
                "official_business_material_batch_contract_unverified",
            ),
        )
    candidate_symbols = tuple(
        item.symbol for item in candidate_plan.items
    )
    entry_symbols = tuple(entry.symbol for entry in entries)
    if (
        len(set(entry_symbols)) != len(entry_symbols)
        or set(entry_symbols) != set(candidate_symbols)
    ):
        return _batch_result(
            status=LeaderOfficialBusinessMaterialBatchStatus.BLOCKED,
            candidate_plan_id=candidate_plan.candidate_set_id,
            candidate_count=len(candidate_symbols),
            reasons=(
                "official_business_material_batch_candidate_mismatch",
            ),
        )
    if any(
        STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(symbol)
        is None
        for symbol in candidate_symbols
    ):
        return _batch_result(
            status=LeaderOfficialBusinessMaterialBatchStatus.BLOCKED,
            candidate_plan_id=candidate_plan.candidate_set_id,
            candidate_count=len(candidate_symbols),
            reasons=(
                "official_business_material_batch_symbol_out_of_scope",
            ),
        )

    entries_by_symbol = {entry.symbol: entry for entry in entries}
    items = []
    for index, plan_item in enumerate(candidate_plan.items):
        entry = entries_by_symbol[plan_item.symbol]
        adapted = (
            build_leader_business_catalyst_input_from_official_artifacts(
                as_of=candidate_plan.as_of,
                symbol=plan_item.symbol,
                industry_code=plan_item.industry_code,
                industry_release_id=plan_item.industry_release_id,
                catalyst_artifact=entry.catalyst_artifact,
                proof_artifacts=entry.proof_artifacts,
                source_status=entry.source_status,
            )
        )
        items.append(LeaderOfficialBusinessMaterialBatchItem(
            index=index,
            symbol=plan_item.symbol,
            status=adapted.status,
            reasons=adapted.reasons,
            input_value=adapted.input_value,
        ))

    if all(item.status == ResearchFeatureStatus.READY for item in items):
        status = LeaderOfficialBusinessMaterialBatchStatus.READY
        reasons = ()
    elif all(item.status == ResearchFeatureStatus.MISSING for item in items):
        status = LeaderOfficialBusinessMaterialBatchStatus.MISSING
        reasons = ("official_business_material_batch_missing",)
    else:
        status = LeaderOfficialBusinessMaterialBatchStatus.PARTIAL
        reasons = ("official_business_material_batch_partial",)
    return _batch_result(
        status=status,
        candidate_plan_id=candidate_plan.candidate_set_id,
        candidate_count=len(candidate_symbols),
        items=items,
        reasons=reasons,
    )
