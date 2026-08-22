"""阶段6L-B3主营与催化版本化研究证据。

本模块只校验调用方显式提供的结构化证据，不抓取公告、不连接数据库，也
不把研究关系转换为正式分数、门禁或状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from radar.leader_business_automatic_contracts import (
    DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
)
from radar.leader_research_features import ResearchFeatureStatus


LEADER_BUSINESS_CATALYST_FEATURE_VERSION = (
    "radar-leader-business-catalyst-feature-v1"
)
UTC = timezone.utc
STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN = re.compile(
    r"[036][0-9]{5}"
)


class BusinessCatalystRelation(str, Enum):
    DIRECT = "direct"
    HIGHLY_RELATED = "highly_related"
    UNCONFIRMED = "unconfirmed"
    DISPROVED = "disproved"


class BusinessProofType(str, Enum):
    REVENUE = "revenue"
    PRODUCT = "product"
    ORDER = "order"
    CAPACITY = "capacity"
    CUSTOMER = "customer"
    OTHER_OFFICIAL = "other_official"


class BusinessEvidenceSourceKind(str, Enum):
    COMPANY_DISCLOSURE = "company_disclosure"
    EXCHANGE_DISCLOSURE = "exchange_disclosure"
    DESIGNATED_DISCLOSURE_PLATFORM = (
        "designated_disclosure_platform"
    )


TRUSTED_SOURCE_DOMAINS = {
    BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE: (
        "sse.com.cn",
        "szse.cn",
    ),
    BusinessEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM: (
        "cninfo.com.cn",
    ),
}


@dataclass(frozen=True)
class LeaderCatalystReference:
    catalyst_id: str
    industry_code: str
    industry_release_id: str
    source_kind: BusinessEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    summary: str
    source_contract_id: Optional[str] = None
    document_version: Optional[str] = None


@dataclass(frozen=True)
class LeaderBusinessProof:
    evidence_id: str
    evidence_version: str
    symbol: str
    proof_type: BusinessProofType
    source_kind: BusinessEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    related_catalyst_ids: Tuple[str, ...]
    fact_summary: str
    source_contract_id: Optional[str] = None


@dataclass(frozen=True)
class LeaderBusinessCatalystReview:
    review_id: str
    mapping_version: str
    symbol: str
    industry_code: str
    industry_release_id: str
    catalyst_id: str
    relation: BusinessCatalystRelation
    review_method: str
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    basis_evidence_ids: Tuple[str, ...]
    basis_catalyst_id: str
    decision_summary: str


@dataclass(frozen=True)
class LeaderBusinessCatalystFeatureInput:
    as_of: datetime
    symbol: str
    industry_code: str
    industry_release_id: str
    catalyst: LeaderCatalystReference
    business_proofs: Tuple[LeaderBusinessProof, ...]
    reviews: Tuple[LeaderBusinessCatalystReview, ...]
    source_status: ResearchFeatureStatus


def _iso_optional(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _aware_utc(value: datetime) -> Optional[datetime]:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _required_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _https_url(value: str) -> bool:
    try:
        parts = urlsplit(str(value or "").strip())
        port = parts.port
    except (TypeError, ValueError):
        return False
    return all((
        parts.scheme == "https",
        bool(parts.hostname),
        parts.username is None,
        parts.password is None,
        not parts.query,
        not parts.fragment,
        port is None or 0 < port < 65536,
    ))


def _normalized_domain(value: Any) -> Optional[str]:
    domain = str(value or "").strip().lower().rstrip(".")
    if (
        not domain
        or "/" in domain
        or ":" in domain
        or domain.startswith(".")
        or ".." in domain
    ):
        return None
    return domain


def _source_url_domain_verified(
    source_kind: BusinessEvidenceSourceKind,
    source_url: str,
) -> bool:
    hostname = _normalized_domain(urlsplit(source_url).hostname)
    if hostname is None:
        return False
    if source_kind == BusinessEvidenceSourceKind.COMPANY_DISCLOSURE:
        return False
    allowed_domains = TRUSTED_SOURCE_DOMAINS.get(
        source_kind,
        (),
    )
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in allowed_domains
        if domain is not None
    )


def _has_duplicates(values: Sequence[str]) -> bool:
    return len(values) != len(set(values))


def _compressed_references(
    input_value: LeaderBusinessCatalystFeatureInput,
) -> Tuple[Mapping[str, Any], ...]:
    catalyst = input_value.catalyst
    references = [{
        "kind": "catalyst",
        "catalystId": catalyst.catalyst_id,
        "industryCode": catalyst.industry_code,
        "industryReleaseId": catalyst.industry_release_id,
        "sourceKind": catalyst.source_kind.value,
        "sourceName": catalyst.source_name,
        "sourceContractId": catalyst.source_contract_id,
        "sourceUrl": catalyst.source_url,
        "documentId": catalyst.document_id,
        "documentVersion": catalyst.document_version,
        "publishedAt": catalyst.published_at.isoformat(),
        "effectiveFrom": catalyst.effective_from.isoformat(),
        "effectiveUntil": _iso_optional(catalyst.effective_until),
    }]
    references.extend({
        "kind": "business_proof",
        "evidenceId": proof.evidence_id,
        "evidenceVersion": proof.evidence_version,
        "proofType": proof.proof_type.value,
        "sourceKind": proof.source_kind.value,
        "sourceName": proof.source_name,
        "sourceContractId": proof.source_contract_id,
        "sourceUrl": proof.source_url,
        "documentId": proof.document_id,
        "publishedAt": proof.published_at.isoformat(),
        "effectiveFrom": proof.effective_from.isoformat(),
        "effectiveUntil": _iso_optional(proof.effective_until),
    } for proof in input_value.business_proofs)
    references.extend({
        "kind": "manual_review",
        "reviewId": review.review_id,
        "mappingVersion": review.mapping_version,
        "relation": review.relation.value,
        "reviewedAt": review.reviewed_at.isoformat(),
        "effectiveUntil": _iso_optional(review.effective_until),
        "basisEvidenceIds": list(review.basis_evidence_ids),
        "basisCatalystId": review.basis_catalyst_id,
    } for review in input_value.reviews)
    return tuple(references)


@dataclass(frozen=True)
class LeaderBusinessCatalystFeatureResult:
    status: ResearchFeatureStatus
    relation: BusinessCatalystRelation
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple
    )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": (
                LEADER_BUSINESS_CATALYST_FEATURE_VERSION
            ),
            "status": self.status.value,
            "relation": self.relation.value,
            "scoreReady": False,
            "formalUsable": False,
            "researchScore": None,
            "references": [
                dict(reference)
                for reference in self.references
            ],
            "reasons": list(self.reasons),
        }


def missing_leader_business_catalyst_features(
    reasons: Sequence[str] = (
        "business_exposure_evidence_missing",
    ),
    *,
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING,
) -> LeaderBusinessCatalystFeatureResult:
    return LeaderBusinessCatalystFeatureResult(
        status=status,
        relation=BusinessCatalystRelation.UNCONFIRMED,
        reasons=tuple(dict.fromkeys(
            reason for reason in reasons if reason
        )),
    )


def _invalid_result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
) -> LeaderBusinessCatalystFeatureResult:
    return missing_leader_business_catalyst_features(
        reasons,
        status=status,
    )


def build_leader_business_catalyst_features(
    input_value: LeaderBusinessCatalystFeatureInput,
) -> LeaderBusinessCatalystFeatureResult:
    """构建主营催化压缩研究证据，正式评分和门禁始终关闭。"""

    if input_value.source_status != ResearchFeatureStatus.READY:
        return missing_leader_business_catalyst_features(
            (f"business_source_{input_value.source_status.value}",),
            status=input_value.source_status,
        )

    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_as_of_timezone_missing",),
        )
    if not input_value.business_proofs:
        return _invalid_result(
            ResearchFeatureStatus.MISSING,
            ("business_evidence_missing",),
        )

    catalyst = input_value.catalyst
    proofs = input_value.business_proofs
    reviews = input_value.reviews
    if (
        len(input_value.symbol) != 6
        or not input_value.symbol.isdigit()
        or not _required_text(input_value.industry_code)
        or not _required_text(input_value.industry_release_id)
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_identity_missing",),
        )
    if STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(
        input_value.symbol
    ) is None:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_symbol_out_of_scope",),
        )
    if (
        catalyst.industry_code != input_value.industry_code
        or catalyst.industry_release_id
        != input_value.industry_release_id
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_catalyst_identity_mismatch",),
        )
    if not all((
        _required_text(catalyst.catalyst_id),
        isinstance(
            catalyst.source_kind,
            BusinessEvidenceSourceKind,
        ),
        _required_text(catalyst.source_name),
        _required_text(catalyst.source_url),
        _required_text(catalyst.document_id),
        _required_text(catalyst.summary),
        (
            catalyst.source_contract_id is None
            or _required_text(catalyst.source_contract_id)
        ),
        (
            catalyst.document_version is None
            or _required_text(catalyst.document_version)
        ),
    )):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_source_identity_missing",),
        )

    for proof in proofs:
        if not all((
            _required_text(proof.evidence_id),
            _required_text(proof.evidence_version),
            isinstance(proof.proof_type, BusinessProofType),
            isinstance(
                proof.source_kind,
                BusinessEvidenceSourceKind,
            ),
            _required_text(proof.source_name),
            _required_text(proof.source_url),
            _required_text(proof.document_id),
            _required_text(proof.fact_summary),
            (
                proof.source_contract_id is None
                or _required_text(proof.source_contract_id)
            ),
        )):
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_source_identity_missing",),
            )
        if proof.symbol != input_value.symbol:
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_identity_mismatch",),
            )

    for review in reviews:
        if not all((
            _required_text(review.review_id),
            _required_text(review.mapping_version),
            isinstance(
                review.relation,
                BusinessCatalystRelation,
            ),
            _required_text(review.reviewer_key),
            _required_text(review.basis_catalyst_id),
            _required_text(review.decision_summary),
        )):
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_review_identity_missing",),
            )
        if (
            review.symbol != input_value.symbol
            or review.industry_code != input_value.industry_code
            or review.industry_release_id
            != input_value.industry_release_id
            or review.catalyst_id != catalyst.catalyst_id
        ):
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_review_identity_mismatch",),
            )

    evidence_ids = tuple(proof.evidence_id for proof in proofs)
    evidence_versions = tuple(
        proof.evidence_version for proof in proofs
    )
    document_ids = tuple(proof.document_id for proof in proofs)
    if any((
        _has_duplicates(evidence_ids),
        _has_duplicates(evidence_versions),
        _has_duplicates(document_ids),
    )):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_evidence_identity_duplicate",),
        )
    review_ids = tuple(review.review_id for review in reviews)
    mapping_versions = tuple(
        review.mapping_version for review in reviews
    )
    if (
        _has_duplicates(review_ids)
        or _has_duplicates(mapping_versions)
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_review_identity_duplicate",),
        )
    if (
        not _https_url(catalyst.source_url)
        or any(not _https_url(proof.source_url) for proof in proofs)
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_source_url_unverified",),
        )
    if (
        not _source_url_domain_verified(
            catalyst.source_kind,
            catalyst.source_url,
        )
        or any(
            not _source_url_domain_verified(
                proof.source_kind,
                proof.source_url,
            )
            for proof in proofs
        )
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_source_domain_unverified",),
        )

    catalyst_published_at = _aware_utc(catalyst.published_at)
    catalyst_effective_from = _aware_utc(
        catalyst.effective_from
    )
    catalyst_effective_until = (
        _aware_utc(catalyst.effective_until)
        if catalyst.effective_until is not None
        else None
    )
    proof_times = tuple((
        _aware_utc(proof.published_at),
        _aware_utc(proof.effective_from),
        (
            _aware_utc(proof.effective_until)
            if proof.effective_until is not None
            else None
        ),
    ) for proof in proofs)
    review_times = tuple((
        _aware_utc(review.reviewed_at),
        (
            _aware_utc(review.effective_until)
            if review.effective_until is not None
            else None
        ),
    ) for review in reviews)
    if (
        catalyst_published_at is None
        or catalyst_effective_from is None
        or (
            catalyst.effective_until is not None
            and catalyst_effective_until is None
        )
        or any(
            published is None
            or effective_from is None
            or (
                proof.effective_until is not None
                and effective_until is None
            )
            for proof, (
                published,
                effective_from,
                effective_until,
            ) in zip(proofs, proof_times)
        )
        or any(
            reviewed_at is None
            or (
                review.effective_until is not None
                and effective_until is None
            )
            for review, (
                reviewed_at,
                effective_until,
            ) in zip(reviews, review_times)
        )
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_timestamp_timezone_missing",),
        )

    if (
        (
            catalyst_effective_until is not None
            and catalyst_effective_until
            < catalyst_effective_from
        )
        or any(
            effective_until is not None
            and effective_until < effective_from
            for _, effective_from, effective_until in proof_times
        )
        or any(
            effective_until is not None
            and effective_until < reviewed_at
            for reviewed_at, effective_until in review_times
        )
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_effective_range_unverified",),
        )
    if catalyst_published_at > as_of:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("catalyst_published_at_future",),
        )
    if catalyst_effective_from > as_of:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("catalyst_not_effective",),
        )
    if any(published > as_of for published, _, _ in proof_times):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_published_at_future",),
        )
    if any(
        effective_from > as_of
        for _, effective_from, _ in proof_times
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_evidence_not_effective",),
        )
    if any(
        reviewed_at > as_of
        for reviewed_at, _ in review_times
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_reviewed_at_future",),
        )
    if any(
        review.review_method not in {"manual", "deterministic_official"}
        or (
            review.review_method == "deterministic_official"
            and (
                not review.review_id.startswith("business-auto:")
                or review.mapping_version
                != DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION
                or review.reviewer_key != review.mapping_version
            )
        )
        for review in reviews
    ):
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_review_method_unverified",),
        )

    evidence_id_set = set(evidence_ids)
    for review in reviews:
        if (
            not review.basis_evidence_ids
            or any(
                evidence_id not in evidence_id_set
                for evidence_id in review.basis_evidence_ids
            )
            or review.basis_catalyst_id != catalyst.catalyst_id
        ):
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_review_reference_missing",),
            )
        if _has_duplicates(review.basis_evidence_ids):
            return _invalid_result(
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                ("business_review_reference_duplicate",),
            )

    if (
        catalyst_effective_until is not None
        and catalyst_effective_until < as_of
    ):
        return _invalid_result(
            ResearchFeatureStatus.STALE,
            ("catalyst_evidence_expired",),
        )
    active_proofs = tuple(
        proof
        for proof, (_, _, effective_until) in zip(
            proofs,
            proof_times,
        )
        if effective_until is None or effective_until >= as_of
    )
    active_reviews = tuple(
        review
        for review, (_, effective_until) in zip(
            reviews,
            review_times,
        )
        if effective_until is None or effective_until >= as_of
    )
    if not active_proofs:
        return _invalid_result(
            ResearchFeatureStatus.STALE,
            ("business_evidence_expired",),
        )
    active_evidence_ids = {
        proof.evidence_id for proof in active_proofs
    }
    if any(
        any(
            evidence_id not in active_evidence_ids
            for evidence_id in review.basis_evidence_ids
        )
        for review in active_reviews
    ):
        return _invalid_result(
            ResearchFeatureStatus.STALE,
            ("business_review_basis_expired",),
        )
    if reviews and not active_reviews and not any(
        catalyst.catalyst_id in proof.related_catalyst_ids
        for proof in active_proofs
    ):
        return _invalid_result(
            ResearchFeatureStatus.STALE,
            ("business_review_expired",),
        )
    if len({review.relation for review in active_reviews}) > 1:
        return _invalid_result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("business_review_conflict",),
        )

    references = _compressed_references(input_value)
    if active_reviews:
        relation = active_reviews[0].relation
        return LeaderBusinessCatalystFeatureResult(
            status=(
                ResearchFeatureStatus.SOURCE_UNVERIFIED
                if relation == BusinessCatalystRelation.UNCONFIRMED
                else ResearchFeatureStatus.READY
            ),
            relation=relation,
            reasons=(
                ("business_relation_unconfirmed",)
                if relation == BusinessCatalystRelation.UNCONFIRMED
                else ()
            ),
            references=references,
        )

    directly_related = any(
        input_value.catalyst.catalyst_id
        in proof.related_catalyst_ids
        for proof in active_proofs
    )
    return LeaderBusinessCatalystFeatureResult(
        status=(
            ResearchFeatureStatus.READY
            if directly_related
            else ResearchFeatureStatus.SOURCE_UNVERIFIED
        ),
        relation=(
            BusinessCatalystRelation.DIRECT
            if directly_related
            else BusinessCatalystRelation.UNCONFIRMED
        ),
        reasons=(
            ()
            if directly_related
            else ("business_relation_unconfirmed",)
        ),
        references=references,
    )
