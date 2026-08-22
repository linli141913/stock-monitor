"""使用可重放官方页级证据确定主营与催化的精确关系。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Optional, Sequence, Tuple
import unicodedata

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_catalyst_facts import (
    OfficialBusinessCatalystFactResult,
)
from radar.leader_business_catalyst_features import BusinessCatalystRelation
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
    OfficialBusinessFactResult,
)
from radar.leader_runtime_candidate_plan import LeaderRuntimeCandidatePlanItem


DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION = (
    "radar-leader-business-deterministic-relation-v13"
)
DETERMINISTIC_BUSINESS_VERIFICATION_CONTRACT_ID = (
    "radar-leader-business-deterministic-verification-v1"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GENERIC_RELATION_TERMS = frozenset({
    "产品", "服务", "业务", "行业", "项目", "技术", "平台", "系统",
})
ISSUER_LIKE_SUFFIXES = ("股份", "集团", "公司", "银行", "证券")
UNSAFE_RELATION_TERMS = frozenset({
    "主营", "主要", "公司主营", "公司主要", "主营业务",
})


@dataclass(frozen=True, repr=False)
class DeterministicOfficialBusinessVerificationArtifact:
    verification_id: str
    rule_version: str
    symbol: str
    industry_code: str
    industry_name: str
    industry_release_id: str
    relation: BusinessCatalystRelation
    matched_terms: Tuple[str, ...] = field(repr=False)
    annual_document_id: str
    annual_document_version: str
    annual_content_sha256: str
    catalyst_document_id: str
    catalyst_document_version: str
    catalyst_content_sha256: str
    issuer_identity: str
    annual_fragment_sha256s: Tuple[str, ...]
    catalyst_fragment_sha256s: Tuple[str, ...]
    validated_at: datetime
    annual_facts: OfficialBusinessFactResult = field(repr=False)
    catalyst_facts: OfficialBusinessCatalystFactResult = field(repr=False)
    contract_id: str = DETERMINISTIC_BUSINESS_VERIFICATION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


@dataclass(frozen=True, repr=False)
class DeterministicOfficialBusinessVerificationResult:
    status: AutomaticBusinessEvidenceStatus
    artifact: Optional[DeterministicOfficialBusinessVerificationArtifact] = field(
        default=None,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    contract_id: str = DETERMINISTIC_BUSINESS_VERIFICATION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


def _result(
    status: AutomaticBusinessEvidenceStatus,
    reasons: Sequence[str],
    artifact: Optional[DeterministicOfficialBusinessVerificationArtifact] = None,
) -> DeterministicOfficialBusinessVerificationResult:
    return DeterministicOfficialBusinessVerificationResult(
        status=status,
        artifact=artifact,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _canonical_term(value: str) -> str:
    normalized = _normalize(value)
    normalized = re.sub(
        r"^(?:报告期内|本期|主要受益于|受益于|主要系|公司)+",
        "",
        normalized,
    )
    return re.sub(r"(?:业务|产品|板块|领域)$", "", normalized)


def _fragments_valid(value: Any) -> bool:
    return bool(
        isinstance(value, tuple)
        and value
        and all(
            type(fragment) is OfficialBusinessEvidenceFragment
            and isinstance(fragment.page_number, int)
            and not isinstance(fragment.page_number, bool)
            and fragment.page_number >= 1
            and isinstance(fragment.text, str)
            and bool(fragment.text)
            and isinstance(fragment.fragment_sha256, str)
            and SHA256_PATTERN.fullmatch(fragment.fragment_sha256) is not None
            and fragment.fragment_sha256
            == hashlib.sha256(fragment.text.encode("utf-8")).hexdigest()
            for fragment in value
        )
    )


def _terms_valid(value: Any, *, maximum_length: int = 20) -> bool:
    return bool(
        isinstance(value, tuple)
        and value
        and len(value) == len(set(value))
        and all(
            isinstance(term, str)
            and 2 <= len(_normalize(term)) <= maximum_length
            for term in value
        )
    )


def _annual_valid(plan_item: LeaderRuntimeCandidatePlanItem, value: Any) -> bool:
    return bool(
        type(value) is OfficialBusinessFactResult
        and value.status is AutomaticBusinessEvidenceStatus.READY
        and value.symbol == plan_item.symbol
        and value.industry_code == plan_item.industry_code
        and value.industry_release_id == plan_item.industry_release_id
        and isinstance(value.document_id, str)
        and isinstance(value.document_version, str)
        and isinstance(value.content_sha256, str)
        and SHA256_PATTERN.fullmatch(value.content_sha256) is not None
        and _terms_valid(value.business_terms)
        and _fragments_valid(value.fragments)
        and _aware(value.source_time)
        and _aware(value.validated_at)
        and value.source_time <= plan_item.as_of
        and value.source_time <= value.validated_at
    )


def _catalyst_valid(plan_item: LeaderRuntimeCandidatePlanItem, value: Any) -> bool:
    return bool(
        type(value) is OfficialBusinessCatalystFactResult
        and value.status is AutomaticBusinessEvidenceStatus.READY
        and value.symbol == plan_item.symbol
        and isinstance(value.issuer_identity, str)
        and value.issuer_identity.startswith("cninfo-org:")
        and isinstance(value.document_id, str)
        and isinstance(value.document_version, str)
        and isinstance(value.content_sha256, str)
        and SHA256_PATTERN.fullmatch(value.content_sha256) is not None
        and _terms_valid(value.business_terms, maximum_length=60)
        and _fragments_valid(value.fragments)
        and _aware(value.source_time)
        and _aware(value.validated_at)
        and value.source_time <= plan_item.as_of
        and value.source_time <= value.validated_at
    )


def _safe_term(term: str, industry_name: str) -> bool:
    normalized = _normalize(term)
    canonical = _canonical_term(term)
    return bool(
        2 <= len(canonical) <= 60
        and normalized not in GENERIC_RELATION_TERMS
        and normalized not in UNSAFE_RELATION_TERMS
        and canonical not in GENERIC_RELATION_TERMS
        and canonical not in UNSAFE_RELATION_TERMS
        and canonical != _canonical_term(industry_name)
        and not normalized.endswith(ISSUER_LIKE_SUFFIXES)
        and not canonical.endswith(ISSUER_LIKE_SUFFIXES)
    )


def _fingerprint(payload: dict) -> str:
    digest = hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"business-auto:{digest}"


def build_deterministic_official_business_verification(
    plan_item: Any,
    annual_facts: Any,
    catalyst_facts: Any,
    *,
    validated_at: Any,
) -> DeterministicOfficialBusinessVerificationResult:
    if (
        type(plan_item) is not LeaderRuntimeCandidatePlanItem
        or not _annual_valid(plan_item, annual_facts)
        or not isinstance(catalyst_facts, tuple)
        or not catalyst_facts
        or any(not _catalyst_valid(plan_item, item) for item in catalyst_facts)
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_evidence_unverified",),
        )
    assert isinstance(annual_facts, OfficialBusinessFactResult)
    if (
        not _aware(validated_at)
        or annual_facts.validated_at > validated_at
        or any(item.validated_at > validated_at for item in catalyst_facts)
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_time_unverified",),
        )
    annual_terms = {
        _canonical_term(term): term
        for term in annual_facts.business_terms
        if _safe_term(term, plan_item.industry_name)
    }
    matches = []
    for catalyst in catalyst_facts:
        matched = tuple(
            annual_terms[normalized]
            for term in catalyst.business_terms
            if (normalized := _canonical_term(term)) in annual_terms
            and _safe_term(term, plan_item.industry_name)
        )
        matched = tuple(dict.fromkeys(matched))
        if matched:
            matches.append((catalyst, matched))
    if not matches:
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_relation_unconfirmed",),
        )
    matches.sort(
        key=lambda item: (item[0].source_time, item[0].document_id),
        reverse=True,
    )
    catalyst, matched_terms = matches[0]
    relation = (
        BusinessCatalystRelation.DISPROVED
        if catalyst.negative_event
        else BusinessCatalystRelation.DIRECT
    )
    payload = {
        "ruleVersion": DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
        "symbol": plan_item.symbol,
        "industryCode": plan_item.industry_code,
        "industryName": plan_item.industry_name,
        "industryReleaseId": plan_item.industry_release_id,
        "relation": relation.value,
        "matchedTerms": list(matched_terms),
        "annualDocumentVersion": annual_facts.document_version,
        "annualContentSha256": annual_facts.content_sha256,
        "annualFragments": [item.fragment_sha256 for item in annual_facts.fragments],
        "catalystDocumentVersion": catalyst.document_version,
        "catalystContentSha256": catalyst.content_sha256,
        "catalystFragments": [item.fragment_sha256 for item in catalyst.fragments],
        "validatedAt": validated_at.isoformat(),
    }
    artifact = DeterministicOfficialBusinessVerificationArtifact(
        verification_id=_fingerprint(payload),
        rule_version=DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
        symbol=plan_item.symbol,
        industry_code=plan_item.industry_code,
        industry_name=plan_item.industry_name,
        industry_release_id=plan_item.industry_release_id,
        relation=relation,
        matched_terms=matched_terms,
        annual_document_id=annual_facts.document_id,
        annual_document_version=annual_facts.document_version,
        annual_content_sha256=annual_facts.content_sha256,
        catalyst_document_id=catalyst.document_id,
        catalyst_document_version=catalyst.document_version,
        catalyst_content_sha256=catalyst.content_sha256,
        issuer_identity=catalyst.issuer_identity,
        annual_fragment_sha256s=tuple(item.fragment_sha256 for item in annual_facts.fragments),
        catalyst_fragment_sha256s=tuple(item.fragment_sha256 for item in catalyst.fragments),
        validated_at=validated_at,
        annual_facts=annual_facts,
        catalyst_facts=catalyst,
    )
    return _result(AutomaticBusinessEvidenceStatus.READY, (), artifact)


def replay_deterministic_official_business_verification(
    artifact: Any,
    *,
    as_of: Any,
) -> DeterministicOfficialBusinessVerificationResult:
    """从工件内证据重新计算指纹和关系，不信任调用方结论。"""

    if (
        type(artifact) is not DeterministicOfficialBusinessVerificationArtifact
        or not _aware(as_of)
        or artifact.rule_version != DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION
        or artifact.contract_id != DETERMINISTIC_BUSINESS_VERIFICATION_CONTRACT_ID
        or artifact.validated_at > as_of
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_verification_unverified",),
        )
    plan_item = LeaderRuntimeCandidatePlanItem(
        index=0,
        symbol=artifact.symbol,
        as_of=as_of,
        industry_code=artifact.industry_code,
        industry_name=artifact.industry_name,
        industry_release_id=artifact.industry_release_id,
        within_industry_rank=1,
        quote_source_contract_id="deterministic-replay",
        sector_source_contract_id="deterministic-replay",
    )
    rebuilt = build_deterministic_official_business_verification(
        plan_item,
        artifact.annual_facts,
        (artifact.catalyst_facts,),
        validated_at=artifact.validated_at,
    )
    if (
        rebuilt.status is not AutomaticBusinessEvidenceStatus.READY
        or rebuilt.artifact != artifact
    ):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            ("business_deterministic_verification_unverified",),
        )
    return rebuilt
