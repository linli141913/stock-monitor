"""阶段6L-D1风险与失效官方研究证据。

本模块只校验调用方显式提供的结构化官方证据，不抓取数据、不连接数据库，
也不把研究结果转换为正式分数、门禁或状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from radar.leader_research_features import ResearchFeatureStatus


LEADER_RISK_INVALIDATION_FEATURE_VERSION = (
    "radar-leader-risk-invalidation-feature-v1"
)
UTC = timezone.utc
RISK_SOURCE_CONTRACT_ID = (
    "radar-leader-risk-official-coverage-v1"
)
RISK_SOURCE_ADAPTER_CONTRACT_ID = (
    "radar-leader-risk-official-coverage-adapter-v1"
)
MAXIMUM_COVERAGE_AGE_SECONDS = 24 * 60 * 60
MAXIMUM_COVERAGE_VALIDITY_SECONDS = 24 * 60 * 60
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class RiskCategory(str, Enum):
    REDUCTION = "reduction"
    UNLOCK = "unlock"
    REGULATORY = "regulatory"
    INVESTIGATION = "investigation"
    LITIGATION = "litigation"
    EARNINGS = "earnings"
    AUDIT = "audit"


class RiskEventSubtype(str, Enum):
    REDUCTION_PLAN = "reduction_plan"
    SHARE_UNLOCK = "share_unlock"
    REGULATORY_INQUIRY = "regulatory_inquiry"
    REGULATORY_MEASURE = "regulatory_measure"
    DISCIPLINARY_ACTION = "disciplinary_action"
    ADMINISTRATIVE_PENALTY = "administrative_penalty"
    FORMAL_INVESTIGATION = "formal_investigation"
    MAJOR_VIOLATION_INVESTIGATION = (
        "major_violation_investigation"
    )
    MATERIAL_LITIGATION = "material_litigation"
    ARBITRATION = "arbitration"
    EARNINGS_LOSS = "earnings_loss"
    EARNINGS_DECLINE = "earnings_decline"
    MATERIAL_IMPAIRMENT = "material_impairment"
    AUDIT_QUALIFIED = "audit_qualified"
    AUDIT_ADVERSE = "audit_adverse"
    AUDIT_DISCLAIMER = "audit_disclaimer"
    AUDIT_GOING_CONCERN = "audit_going_concern"


class RiskEvidenceSourceKind(str, Enum):
    COMPANY_DISCLOSURE = "company_disclosure"
    EXCHANGE_DISCLOSURE = "exchange_disclosure"
    REGULATOR_DISCLOSURE = "regulator_disclosure"
    DESIGNATED_DISCLOSURE_PLATFORM = (
        "designated_disclosure_platform"
    )
    OFFICIAL_JUDICIAL_DOCUMENT = "official_judicial_document"


class RiskOfficialStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"
    CORRECTED = "corrected"


class RiskResolutionKind(str, Enum):
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"
    CORRECTED = "corrected"
    OFFICIALLY_CLEARED = "officially_cleared"


ALL_RISK_CATEGORIES = tuple(RiskCategory)
CLOSING_RESOLUTION_KINDS = frozenset({
    RiskResolutionKind.COMPLETED,
    RiskResolutionKind.WITHDRAWN,
    RiskResolutionKind.OFFICIALLY_CLEARED,
})
EVENT_SUBTYPES_BY_CATEGORY = {
    RiskCategory.REDUCTION: frozenset({
        RiskEventSubtype.REDUCTION_PLAN,
    }),
    RiskCategory.UNLOCK: frozenset({
        RiskEventSubtype.SHARE_UNLOCK,
    }),
    RiskCategory.REGULATORY: frozenset({
        RiskEventSubtype.REGULATORY_INQUIRY,
        RiskEventSubtype.REGULATORY_MEASURE,
        RiskEventSubtype.DISCIPLINARY_ACTION,
        RiskEventSubtype.ADMINISTRATIVE_PENALTY,
    }),
    RiskCategory.INVESTIGATION: frozenset({
        RiskEventSubtype.FORMAL_INVESTIGATION,
        RiskEventSubtype.MAJOR_VIOLATION_INVESTIGATION,
    }),
    RiskCategory.LITIGATION: frozenset({
        RiskEventSubtype.MATERIAL_LITIGATION,
        RiskEventSubtype.ARBITRATION,
    }),
    RiskCategory.EARNINGS: frozenset({
        RiskEventSubtype.EARNINGS_LOSS,
        RiskEventSubtype.EARNINGS_DECLINE,
        RiskEventSubtype.MATERIAL_IMPAIRMENT,
    }),
    RiskCategory.AUDIT: frozenset({
        RiskEventSubtype.AUDIT_QUALIFIED,
        RiskEventSubtype.AUDIT_ADVERSE,
        RiskEventSubtype.AUDIT_DISCLAIMER,
        RiskEventSubtype.AUDIT_GOING_CONCERN,
    }),
}
TRUSTED_COVERAGE_DOMAINS = (
    "cninfo.com.cn",
    "csrc.gov.cn",
    "sse.com.cn",
    "szse.cn",
)
COVERAGE_CATEGORIES_BY_DOMAIN = {
    "cninfo.com.cn": frozenset(ALL_RISK_CATEGORIES),
    "csrc.gov.cn": frozenset({
        RiskCategory.REGULATORY,
        RiskCategory.INVESTIGATION,
    }),
    "sse.com.cn": frozenset(ALL_RISK_CATEGORIES),
    "szse.cn": frozenset(ALL_RISK_CATEGORIES),
}
TRUSTED_DOMAINS_BY_SOURCE_KIND = {
    RiskEvidenceSourceKind.COMPANY_DISCLOSURE: (
        "cninfo.com.cn",
        "sse.com.cn",
        "szse.cn",
    ),
    RiskEvidenceSourceKind.EXCHANGE_DISCLOSURE: (
        "sse.com.cn",
        "szse.cn",
    ),
    RiskEvidenceSourceKind.REGULATOR_DISCLOSURE: (
        "csrc.gov.cn",
    ),
    RiskEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM: (
        "cninfo.com.cn",
    ),
    RiskEvidenceSourceKind.OFFICIAL_JUDICIAL_DOCUMENT: (
        "court.gov.cn",
        "chinacourt.org",
    ),
}


@dataclass(frozen=True)
class LeaderRiskEvidenceCoverage:
    coverage_id: str
    source_contract_id: str
    adapter_contract_id: str
    symbol: str
    issuer_identity: str
    issuer_listed_at: datetime
    covered_categories: Tuple[RiskCategory, ...]
    window_from: datetime
    window_until: datetime
    checked_at: datetime
    effective_until: datetime
    source_names: Tuple[str, ...]
    source_urls: Tuple[str, ...]
    coverage_complete: bool
    open_event_carry_forward_complete: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaderRiskEventEvidence:
    event_id: str
    event_version: str
    symbol: str
    issuer_identity: str
    category: RiskCategory
    event_subtype: RiskEventSubtype
    case_id: Optional[str]
    source_kind: RiskEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    reporting_period: Optional[str]
    fact_summary: str
    official_status: RiskOfficialStatus


@dataclass(frozen=True)
class LeaderRiskResolutionEvidence:
    resolution_id: str
    resolution_version: str
    target_event_id: str
    target_event_version: str
    symbol: str
    issuer_identity: str
    resolution_kind: RiskResolutionKind
    source_kind: RiskEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    resolution_summary: str


@dataclass(frozen=True)
class LeaderRiskInvalidationFeatureInput:
    as_of: datetime
    symbol: str
    issuer_identity: str
    coverage: LeaderRiskEvidenceCoverage
    events: Tuple[LeaderRiskEventEvidence, ...]
    resolutions: Tuple[LeaderRiskResolutionEvidence, ...]
    source_status: ResearchFeatureStatus


@dataclass(frozen=True)
class LeaderRiskInvalidationFeatureResult:
    status: ResearchFeatureStatus
    coverage_state: str
    active_risk_categories: Tuple[RiskCategory, ...] = ()
    no_active_risk_observed: bool = False
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple
    )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": (
                LEADER_RISK_INVALIDATION_FEATURE_VERSION
            ),
            "status": self.status.value,
            "coverageState": self.coverage_state,
            "activeRiskCategories": [
                category.value
                for category in self.active_risk_categories
            ],
            "noActiveRiskObserved": self.no_active_risk_observed,
            "scoreReady": False,
            "formalUsable": False,
            "researchScore": None,
            "references": [
                dict(reference)
                for reference in self.references
            ],
            "reasons": list(self.reasons),
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(SAFE_IDENTIFIER_PATTERN.fullmatch(value.strip()))
    )


def _url_parts(value: str):
    try:
        parts = urlsplit(str(value or "").strip())
        _ = parts.port
    except (TypeError, ValueError):
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts


def _https_url(value: str) -> bool:
    return _url_parts(value) is not None


def _url_has_credentials(value: str) -> bool:
    parts = _url_parts(value)
    return bool(
        parts is not None
        and (parts.username is not None or parts.password is not None)
    )


def _host_matches_domain(host: str, domain: str) -> bool:
    normalized_host = host.rstrip(".").lower()
    normalized_domain = domain.rstrip(".").lower()
    return (
        normalized_host == normalized_domain
        or normalized_host.endswith(f".{normalized_domain}")
    )


def _url_uses_trusted_domain(
    value: str,
    trusted_domains: Sequence[str],
) -> bool:
    parts = _url_parts(value)
    if parts is None or _url_has_credentials(value):
        return False
    return any(
        _host_matches_domain(parts.hostname or "", domain)
        for domain in trusted_domains
    )


def _safe_public_url(value: str) -> str:
    parts = _url_parts(value)
    if parts is None or _url_has_credentials(value):
        return ""
    return urlunsplit((
        "https",
        parts.hostname or "",
        parts.path or "/",
        "",
        "",
    ))


def _document_ref(value: str) -> str:
    return hashlib.sha256(
        str(value).encode("utf-8")
    ).hexdigest()[:16]


def _aware_utc(value: datetime) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _result(
    status: ResearchFeatureStatus,
    coverage_state: str,
    reasons: Sequence[str],
) -> LeaderRiskInvalidationFeatureResult:
    return LeaderRiskInvalidationFeatureResult(
        status=status,
        coverage_state=coverage_state,
        reasons=_dedupe(reasons),
    )


def _coverage_reference(
    coverage: LeaderRiskEvidenceCoverage,
) -> Mapping[str, Any]:
    return {
        "kind": "risk_coverage",
        "coverageId": coverage.coverage_id,
        "sourceContractId": coverage.source_contract_id,
        "adapterContractId": coverage.adapter_contract_id,
        "symbol": coverage.symbol,
        "coveredCategories": [
            category.value
            for category in coverage.covered_categories
        ],
        "windowFrom": coverage.window_from.isoformat(),
        "windowUntil": coverage.window_until.isoformat(),
        "checkedAt": coverage.checked_at.isoformat(),
        "effectiveUntil": coverage.effective_until.isoformat(),
        "sources": [
            {
                "sourceHost": (
                    _url_parts(source_url).hostname
                    if _url_parts(source_url) is not None
                    else None
                ),
                "sourceUrl": _safe_public_url(source_url),
            }
            for _, source_url in zip(
                coverage.source_names,
                coverage.source_urls,
            )
        ],
    }


def _event_reference(
    event: LeaderRiskEventEvidence,
    *,
    active: bool,
    closed_by: Sequence[str],
) -> Mapping[str, Any]:
    return {
        "kind": "risk_event",
        "eventId": event.event_id,
        "eventVersion": event.event_version,
        "symbol": event.symbol,
        "category": event.category.value,
        "eventSubtype": event.event_subtype.value,
        "caseRef": (
            _document_ref(event.case_id)
            if event.case_id is not None
            else None
        ),
        "sourceKind": event.source_kind.value,
        "sourceUrl": _safe_public_url(event.source_url),
        "documentRef": _document_ref(event.document_id),
        "factFingerprint": hashlib.sha256(
            event.fact_summary.encode("utf-8")
        ).hexdigest(),
        "publishedAt": event.published_at.isoformat(),
        "effectiveFrom": event.effective_from.isoformat(),
        "effectiveUntil": (
            event.effective_until.isoformat()
            if event.effective_until is not None
            else None
        ),
        "reportingPeriod": event.reporting_period,
        "officialStatus": event.official_status.value,
        "active": active,
        "closedByResolutionIds": list(closed_by),
    }


def _resolution_reference(
    resolution: LeaderRiskResolutionEvidence,
) -> Mapping[str, Any]:
    return {
        "kind": "risk_resolution",
        "resolutionId": resolution.resolution_id,
        "resolutionVersion": resolution.resolution_version,
        "targetEventId": resolution.target_event_id,
        "targetEventVersion": resolution.target_event_version,
        "symbol": resolution.symbol,
        "resolutionKind": resolution.resolution_kind.value,
        "sourceKind": resolution.source_kind.value,
        "sourceUrl": _safe_public_url(resolution.source_url),
        "documentRef": _document_ref(resolution.document_id),
        "resolutionFingerprint": hashlib.sha256(
            resolution.resolution_summary.encode("utf-8")
        ).hexdigest(),
        "publishedAt": resolution.published_at.isoformat(),
        "effectiveFrom": resolution.effective_from.isoformat(),
    }


def _validate_coverage(
    input_value: LeaderRiskInvalidationFeatureInput,
    as_of: datetime,
) -> Optional[LeaderRiskInvalidationFeatureResult]:
    coverage = input_value.coverage
    if coverage is None:
        return _result(
            ResearchFeatureStatus.MISSING,
            "missing",
            ("risk_coverage_missing",),
        )
    if not isinstance(coverage, LeaderRiskEvidenceCoverage):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_coverage_contract_unverified",),
        )

    reasons = []
    stale_reasons = []
    for value in (
        coverage.coverage_id,
        coverage.source_contract_id,
        coverage.adapter_contract_id,
        coverage.symbol,
        coverage.issuer_identity,
    ):
        if not _safe_identifier(value):
            reasons.append("risk_coverage_identity_missing")
            break
    if coverage.source_contract_id != RISK_SOURCE_CONTRACT_ID:
        reasons.append(
            "risk_coverage_source_contract_unverified"
        )
    if (
        coverage.adapter_contract_id
        != RISK_SOURCE_ADAPTER_CONTRACT_ID
    ):
        reasons.append(
            "risk_coverage_adapter_contract_unverified"
        )
    if coverage.symbol != input_value.symbol:
        reasons.append("risk_coverage_symbol_mismatch")
    if coverage.issuer_identity != input_value.issuer_identity:
        reasons.append("risk_coverage_issuer_mismatch")

    categories = coverage.covered_categories
    if categories is None:
        return _result(
            ResearchFeatureStatus.MISSING,
            "missing",
            ("risk_coverage_categories_missing",),
        )
    if not isinstance(categories, (tuple, list)):
        reasons.append("risk_coverage_categories_unverified")
    elif any(
        not isinstance(category, RiskCategory)
        for category in categories
    ):
        reasons.append("risk_coverage_category_unverified")
    elif len(categories) != len(set(categories)):
        reasons.append("risk_coverage_category_duplicate")
    elif set(categories) != set(ALL_RISK_CATEGORIES):
        return _result(
            ResearchFeatureStatus.MISSING,
            "missing",
            ("risk_coverage_categories_incomplete",),
        )

    completion_flags_valid = True
    if not isinstance(coverage.coverage_complete, bool):
        reasons.append("risk_coverage_complete_flag_unverified")
        completion_flags_valid = False
    if not isinstance(
        coverage.open_event_carry_forward_complete,
        bool,
    ):
        reasons.append(
            "risk_coverage_carry_forward_flag_unverified"
        )
        completion_flags_valid = False
    if completion_flags_valid:
        if not coverage.coverage_complete:
            return _result(
                ResearchFeatureStatus.MISSING,
                "missing",
                ("risk_coverage_incomplete",),
            )
        if not coverage.open_event_carry_forward_complete:
            return _result(
                ResearchFeatureStatus.MISSING,
                "missing",
                ("risk_coverage_carry_forward_incomplete",),
            )
    if coverage.reasons:
        reasons.append("risk_coverage_complete_with_reasons")

    source_names = coverage.source_names
    source_urls = coverage.source_urls
    if (
        not isinstance(source_names, (tuple, list))
        or not isinstance(source_urls, (tuple, list))
        or not source_names
        or len(source_names) != len(source_urls)
        or any(
            not _required_text(source_name)
            for source_name in source_names
        )
    ):
        reasons.append("risk_coverage_source_identity_missing")
    else:
        source_urls_are_text = all(
            _required_text(source_url)
            for source_url in source_urls
        )
        if not source_urls_are_text:
            reasons.append("risk_coverage_source_url_unverified")
        elif any(
            not _https_url(source_url)
            for source_url in source_urls
        ):
            reasons.append("risk_coverage_source_url_unverified")
        if source_urls_are_text:
            if any(
                _url_has_credentials(source_url)
                for source_url in source_urls
            ):
                reasons.append(
                    "risk_coverage_source_url_credentials_forbidden"
                )
            if any(
                not _url_uses_trusted_domain(
                    source_url,
                    TRUSTED_COVERAGE_DOMAINS,
                )
                for source_url in source_urls
            ):
                reasons.append(
                    "risk_coverage_source_domain_untrusted"
                )
        if len(source_names) != len(set(source_names)):
            reasons.append("risk_coverage_source_duplicate")
        if (
            source_urls_are_text
            and len(source_urls) != len(set(source_urls))
        ):
            reasons.append("risk_coverage_source_duplicate")

        source_categories = set()
        for source_url in (
            source_urls if source_urls_are_text else ()
        ):
            parts = _url_parts(source_url)
            if parts is None:
                continue
            for domain, domain_categories in (
                COVERAGE_CATEGORIES_BY_DOMAIN.items()
            ):
                if _host_matches_domain(
                    parts.hostname or "",
                    domain,
                ):
                    source_categories.update(domain_categories)
        if set(ALL_RISK_CATEGORIES) - source_categories:
            reasons.append(
                "risk_coverage_source_category_incomplete"
            )

    issuer_listed_at = _aware_utc(coverage.issuer_listed_at)
    window_from = _aware_utc(coverage.window_from)
    window_until = _aware_utc(coverage.window_until)
    checked_at = _aware_utc(coverage.checked_at)
    effective_until = _aware_utc(coverage.effective_until)
    if None in (
        issuer_listed_at,
        window_from,
        window_until,
        checked_at,
        effective_until,
    ):
        reasons.append("risk_coverage_timestamp_timezone_missing")
    else:
        assert issuer_listed_at is not None
        assert window_from is not None
        assert window_until is not None
        assert checked_at is not None
        assert effective_until is not None
        if not window_from <= window_until <= checked_at <= as_of:
            reasons.append("risk_coverage_time_order_invalid")
        if (
            issuer_listed_at > as_of
            or window_from > issuer_listed_at
            or issuer_listed_at > window_until
        ):
            reasons.append("risk_coverage_history_incomplete")
        if (
            (as_of - window_until).total_seconds()
            > MAXIMUM_COVERAGE_AGE_SECONDS
            or (as_of - checked_at).total_seconds()
            > MAXIMUM_COVERAGE_AGE_SECONDS
        ):
            stale_reasons.append("risk_coverage_window_stale")
        validity_seconds = (
            effective_until - checked_at
        ).total_seconds()
        if (
            validity_seconds < 0
            or validity_seconds
            > MAXIMUM_COVERAGE_VALIDITY_SECONDS
        ):
            reasons.append(
                "risk_coverage_effective_window_unbounded"
            )
        if effective_until < as_of:
            stale_reasons.append("risk_coverage_expired")

    if reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            reasons,
        )
    if stale_reasons:
        return _result(
            ResearchFeatureStatus.STALE,
            "stale",
            stale_reasons,
        )
    return None


def _validate_event(
    event: LeaderRiskEventEvidence,
    input_value: LeaderRiskInvalidationFeatureInput,
    as_of: datetime,
) -> Tuple[str, ...]:
    if not isinstance(event, LeaderRiskEventEvidence):
        return ("risk_event_contract_unverified",)

    reasons = []
    if any(
        not _safe_identifier(value)
        for value in (
            event.event_id,
            event.event_version,
            event.symbol,
            event.issuer_identity,
            event.document_id,
        )
    ) or not _required_text(
        event.source_name
    ) or not _required_text(event.fact_summary):
        reasons.append("risk_event_identity_missing")
    if event.symbol != input_value.symbol:
        reasons.append("risk_event_symbol_mismatch")
    if event.issuer_identity != input_value.issuer_identity:
        reasons.append("risk_event_issuer_mismatch")
    if not isinstance(event.category, RiskCategory):
        reasons.append("risk_event_category_unverified")
    if not isinstance(event.event_subtype, RiskEventSubtype):
        reasons.append("risk_event_subtype_unverified")
    elif (
        isinstance(event.category, RiskCategory)
        and event.event_subtype
        not in EVENT_SUBTYPES_BY_CATEGORY[event.category]
    ):
        reasons.append("risk_event_subtype_category_mismatch")
    if isinstance(event.category, RiskCategory):
        if (
            event.category in {
                RiskCategory.INVESTIGATION,
                RiskCategory.LITIGATION,
            }
            and not _safe_identifier(event.case_id)
        ):
            reasons.append(
                f"risk_{event.category.value}_case_id_missing"
            )
        elif (
            event.category
            not in {
                RiskCategory.INVESTIGATION,
                RiskCategory.LITIGATION,
            }
            and event.case_id is not None
        ):
            reasons.append("risk_event_case_id_unexpected")
    elif event.case_id is not None:
        reasons.append("risk_event_case_id_unverified")
    if not isinstance(event.source_kind, RiskEvidenceSourceKind):
        reasons.append("risk_event_source_kind_unverified")
    if not isinstance(event.official_status, RiskOfficialStatus):
        reasons.append("risk_event_official_status_unverified")
    elif event.official_status == RiskOfficialStatus.CORRECTED:
        reasons.append(
            "risk_event_correction_replacement_missing"
        )
    if not _https_url(event.source_url):
        reasons.append("risk_event_source_url_unverified")
    if _url_has_credentials(event.source_url):
        reasons.append(
            "risk_event_source_url_credentials_forbidden"
        )
    if (
        isinstance(event.source_kind, RiskEvidenceSourceKind)
        and not _url_uses_trusted_domain(
            event.source_url,
            TRUSTED_DOMAINS_BY_SOURCE_KIND[
                event.source_kind
            ],
        )
    ):
        reasons.append("risk_event_source_domain_untrusted")

    published_at = _aware_utc(event.published_at)
    effective_from = _aware_utc(event.effective_from)
    effective_until = (
        _aware_utc(event.effective_until)
        if event.effective_until is not None
        else None
    )
    if (
        published_at is None
        or effective_from is None
        or (
            event.effective_until is not None
            and effective_until is None
        )
    ):
        reasons.append("risk_event_timestamp_timezone_missing")
        return _dedupe(reasons)

    if published_at > as_of:
        reasons.append("risk_event_published_in_future")
    if effective_from > as_of:
        reasons.append("risk_event_effective_in_future")
    if (
        effective_until is not None
        and effective_until < effective_from
    ):
        reasons.append("risk_event_effective_interval_invalid")

    if isinstance(event.category, RiskCategory):
        if (
            event.category in {
                RiskCategory.REDUCTION,
                RiskCategory.UNLOCK,
            }
            and event.effective_until is None
        ):
            reasons.append(
                f"risk_{event.category.value}_effective_until_missing"
            )
        if event.category in {
            RiskCategory.EARNINGS,
            RiskCategory.AUDIT,
        }:
            if not _required_text(event.reporting_period):
                reasons.append(
                    f"risk_{event.category.value}_reporting_period_missing"
                )
        elif event.reporting_period is not None:
            reasons.append("risk_event_reporting_period_unexpected")

    return _dedupe(reasons)


def _event_collection_reasons(
    events: Sequence[LeaderRiskEventEvidence],
) -> Tuple[str, ...]:
    if any(
        not isinstance(event, LeaderRiskEventEvidence)
        for event in events
    ):
        return ("risk_event_contract_unverified",)
    if any(
        not _safe_identifier(value)
        for event in events
        for value in (
            event.event_id,
            event.event_version,
            event.document_id,
        )
    ):
        return ()

    reasons = []
    event_versions = [
        (event.event_id, event.event_version)
        for event in events
    ]
    if len(event_versions) != len(set(event_versions)):
        reasons.append("risk_event_identity_duplicate")

    document_ids = [event.document_id for event in events]
    if len(document_ids) != len(set(document_ids)):
        reasons.append("risk_document_identity_duplicate")

    versions_by_event: Dict[
        str, list[LeaderRiskEventEvidence]
    ] = {}
    for event in events:
        versions_by_event.setdefault(event.event_id, []).append(event)
    for versions in versions_by_event.values():
        if len(versions) > 1:
            reasons.append("risk_event_version_conflict")
            break
    return _dedupe(reasons)


def _validate_resolution(
    resolution: LeaderRiskResolutionEvidence,
    event_by_key: Mapping[
        Tuple[str, str],
        LeaderRiskEventEvidence,
    ],
    input_value: LeaderRiskInvalidationFeatureInput,
    as_of: datetime,
) -> Tuple[str, ...]:
    if not isinstance(
        resolution,
        LeaderRiskResolutionEvidence,
    ):
        return ("risk_resolution_contract_unverified",)

    reasons = []
    if any(
        not _safe_identifier(value)
        for value in (
            resolution.resolution_id,
            resolution.resolution_version,
            resolution.target_event_id,
            resolution.target_event_version,
            resolution.symbol,
            resolution.issuer_identity,
            resolution.document_id,
        )
    ) or not _required_text(
        resolution.source_name
    ) or not _required_text(resolution.resolution_summary):
        reasons.append("risk_resolution_identity_missing")
    if resolution.symbol != input_value.symbol:
        reasons.append("risk_resolution_symbol_mismatch")
    if resolution.issuer_identity != input_value.issuer_identity:
        reasons.append("risk_resolution_issuer_mismatch")
    if not isinstance(
        resolution.resolution_kind,
        RiskResolutionKind,
    ):
        reasons.append("risk_resolution_kind_unverified")
    elif resolution.resolution_kind == RiskResolutionKind.CORRECTED:
        reasons.append(
            "risk_resolution_correction_replacement_missing"
        )
    if not isinstance(
        resolution.source_kind,
        RiskEvidenceSourceKind,
    ):
        reasons.append("risk_resolution_source_kind_unverified")
    if not _https_url(resolution.source_url):
        reasons.append("risk_resolution_source_url_unverified")
    if _url_has_credentials(resolution.source_url):
        reasons.append(
            "risk_resolution_source_url_credentials_forbidden"
        )
    if (
        isinstance(
            resolution.source_kind,
            RiskEvidenceSourceKind,
        )
        and not _url_uses_trusted_domain(
            resolution.source_url,
            TRUSTED_DOMAINS_BY_SOURCE_KIND[
                resolution.source_kind
            ],
        )
    ):
        reasons.append(
            "risk_resolution_source_domain_untrusted"
        )

    target_identity_valid = (
        _safe_identifier(resolution.target_event_id)
        and _safe_identifier(resolution.target_event_version)
    )
    event = None
    if target_identity_valid:
        target_key = (
            resolution.target_event_id,
            resolution.target_event_version,
        )
        event = event_by_key.get(target_key)
        if event is None:
            if any(
                event_id == resolution.target_event_id
                for event_id, _ in event_by_key
            ):
                reasons.append(
                    "risk_resolution_target_version_missing"
                )
            else:
                reasons.append("risk_resolution_target_missing")

    published_at = _aware_utc(resolution.published_at)
    effective_from = _aware_utc(resolution.effective_from)
    if published_at is None or effective_from is None:
        reasons.append("risk_resolution_timestamp_timezone_missing")
        return _dedupe(reasons)
    if published_at > as_of:
        reasons.append("risk_resolution_published_in_future")
    if effective_from > as_of:
        reasons.append("risk_resolution_effective_in_future")
    if event is not None:
        event_published_at = _aware_utc(event.published_at)
        event_effective_from = _aware_utc(event.effective_from)
        if (
            event_published_at is not None
            and published_at < event_published_at
        ):
            reasons.append("risk_resolution_precedes_event")
        if (
            event_effective_from is not None
            and effective_from < event_effective_from
        ):
            reasons.append("risk_resolution_precedes_event")
    return _dedupe(reasons)


def _resolution_collection_reasons(
    resolutions: Sequence[LeaderRiskResolutionEvidence],
) -> Tuple[str, ...]:
    if any(
        not isinstance(
            resolution,
            LeaderRiskResolutionEvidence,
        )
        for resolution in resolutions
    ):
        return ("risk_resolution_contract_unverified",)
    if any(
        not _safe_identifier(value)
        for resolution in resolutions
        for value in (
            resolution.resolution_id,
            resolution.resolution_version,
            resolution.target_event_id,
            resolution.target_event_version,
            resolution.document_id,
        )
    ) or any(
        not isinstance(
            resolution.resolution_kind,
            RiskResolutionKind,
        )
        for resolution in resolutions
    ):
        return ()

    reasons = []
    identities = [
        (resolution.resolution_id, resolution.resolution_version)
        for resolution in resolutions
    ]
    if len(identities) != len(set(identities)):
        reasons.append("risk_resolution_identity_duplicate")

    document_ids = [
        resolution.document_id for resolution in resolutions
    ]
    if len(document_ids) != len(set(document_ids)):
        reasons.append("risk_resolution_document_duplicate")

    kinds_by_event: Dict[
        Tuple[str, str],
        set[RiskResolutionKind],
    ] = {}
    for resolution in resolutions:
        kinds_by_event.setdefault(
            (
                resolution.target_event_id,
                resolution.target_event_version,
            ),
            set(),
        ).add(resolution.resolution_kind)
    if any(len(kinds) > 1 for kinds in kinds_by_event.values()):
        reasons.append("risk_resolution_version_conflict")
    return _dedupe(reasons)


def build_leader_risk_invalidation_features(
    input_value: LeaderRiskInvalidationFeatureInput,
) -> LeaderRiskInvalidationFeatureResult:
    """构建风险与失效压缩研究证据，正式评分和门禁始终关闭。"""

    if not isinstance(
        input_value,
        LeaderRiskInvalidationFeatureInput,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_input_contract_unverified",),
        )
    if not isinstance(
        input_value.source_status,
        ResearchFeatureStatus,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_source_status_unverified",),
        )
    if input_value.source_status != ResearchFeatureStatus.READY:
        return _result(
            input_value.source_status,
            input_value.source_status.value,
            (f"risk_source_{input_value.source_status.value}",),
        )

    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_as_of_timezone_missing",),
        )
    if (
        not _safe_identifier(input_value.symbol)
        or not _safe_identifier(input_value.issuer_identity)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_input_identity_missing",),
        )
    if re.fullmatch(r"[036][0-9]{5}", input_value.symbol) is None:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_symbol_out_of_scope",),
        )

    coverage_failure = _validate_coverage(input_value, as_of)
    if coverage_failure is not None:
        return coverage_failure

    if input_value.events is None:
        return _result(
            ResearchFeatureStatus.MISSING,
            "missing",
            ("risk_events_missing",),
        )
    if not isinstance(input_value.events, (tuple, list)):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_events_contract_unverified",),
        )
    if input_value.resolutions is None:
        return _result(
            ResearchFeatureStatus.MISSING,
            "missing",
            ("risk_resolutions_missing",),
        )
    if not isinstance(input_value.resolutions, (tuple, list)):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "source_unverified",
            ("risk_resolutions_contract_unverified",),
        )

    events = tuple(input_value.events)
    resolutions = tuple(input_value.resolutions)
    event_reasons = list(_event_collection_reasons(events))
    for event in events:
        event_reasons.extend(
            _validate_event(event, input_value, as_of)
        )
    if event_reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "complete",
            event_reasons,
        )

    event_by_key = {
        (event.event_id, event.event_version): event
        for event in events
    }
    resolution_reasons = list(
        _resolution_collection_reasons(resolutions)
    )
    event_document_ids = {
        event.document_id for event in events
    }
    resolution_document_ids = {
        resolution.document_id
        for resolution in resolutions
        if isinstance(
            resolution,
            LeaderRiskResolutionEvidence,
        )
        and _safe_identifier(resolution.document_id)
    }
    if event_document_ids & resolution_document_ids:
        resolution_reasons.append(
            "risk_document_identity_cross_duplicate"
        )
    for resolution in resolutions:
        resolution_reasons.extend(
            _validate_resolution(
                resolution,
                event_by_key,
                input_value,
                as_of,
            )
        )
    if resolution_reasons:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "complete",
            resolution_reasons,
        )

    resolutions_by_event: Dict[
        Tuple[str, str],
        list[LeaderRiskResolutionEvidence],
    ] = {}
    for resolution in resolutions:
        resolutions_by_event.setdefault(
            (
                resolution.target_event_id,
                resolution.target_event_version,
            ),
            [],
        ).append(resolution)

    active_categories = set()
    event_references = []
    for event in events:
        event_effective_until = (
            _aware_utc(event.effective_until)
            if event.effective_until is not None
            else None
        )
        matching_resolutions = tuple(
            resolution
            for resolution in resolutions_by_event.get(
                (event.event_id, event.event_version),
                (),
            )
            if resolution.resolution_kind
            in CLOSING_RESOLUTION_KINDS
        )
        active = (
            event.official_status == RiskOfficialStatus.ACTIVE
            and (
                event_effective_until is None
                or as_of <= event_effective_until
            )
            and not matching_resolutions
        )
        if active:
            active_categories.add(event.category)
        event_references.append(
            _event_reference(
                event,
                active=active,
                closed_by=tuple(
                    resolution.resolution_id
                    for resolution in matching_resolutions
                ),
            )
        )

    ordered_active_categories = tuple(
        category
        for category in ALL_RISK_CATEGORIES
        if category in active_categories
    )
    references = (
        _coverage_reference(input_value.coverage),
        *event_references,
        *(
            _resolution_reference(resolution)
            for resolution in resolutions
        ),
    )
    return LeaderRiskInvalidationFeatureResult(
        status=ResearchFeatureStatus.READY,
        coverage_state="complete",
        active_risk_categories=ordered_active_categories,
        no_active_risk_observed=not ordered_active_categories,
        references=tuple(references),
    )
