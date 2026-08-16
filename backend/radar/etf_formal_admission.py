"""阶段5 ETF正式准入证据提供方。

本模块只审计同一产品、指数和时点的冻结证据，不访问数据库或外部来源，
也不计算ETF分数、排名、代表产品或正式候选状态。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.contracts import (
    EtfAssetClass,
    EtfIndexIdentityEvidence,
    EtfManagementStyle,
    EtfMetricState,
    EtfProductMasterRecord,
    EtfRankingInputAudit,
    EvidenceVersionKind,
    IndexConstituentSetEvidence,
    IndexEvidenceStatus,
    IndexIndustryExposureResult,
    IndexMethodologyEvidence,
    ListedFundProductType,
)
from radar.etf_stage5_policy import (
    DEFAULT_ETF_RULE_POLICY,
    REQUIRED_RANKING_FIELDS,
    EtfRulePolicy,
)


ETF_FORMAL_ADMISSION_CONTRACT_ID = (
    "radar-etf-formal-admission-evidence-v1"
)
ETF_LIFECYCLE_EVIDENCE_CONTRACT_ID = (
    "radar-etf-lifecycle-evidence-v1"
)
ETF_INDUSTRY_SCOPE_EVIDENCE_CONTRACT_ID = (
    "radar-etf-industry-scope-evidence-v1"
)
ETF_INDUSTRY_EXPOSURE_CALCULATION_VERSION = (
    "radar-etf-industry-exposure-v1"
)
MINIMUM_CONSTITUENT_WEIGHT_TOTAL = 99.5
MAXIMUM_CONSTITUENT_WEIGHT_TOTAL = 100.5


class EtfFormalAdmissionStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


class EtfLifecycleStatus(str, Enum):
    ACTIVE = "active"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class EtfIndustryScopeKind(str, Enum):
    INDUSTRY = "industry"
    THEME = "theme"
    BROAD_BASED = "broad_based"
    UNKNOWN = "unknown"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}不能为空")


def _require_symbol(value: str) -> None:
    if not isinstance(value, str) or len(value) != 6 or not value.isdigit():
        raise ValueError("ETF代码必须为6位数字")


def _require_sha256(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("证据SHA-256必须为64位小写十六进制")


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


@dataclass(frozen=True)
class EtfLifecycleEvidence:
    symbol: str
    as_of: datetime
    status: EtfLifecycleStatus
    listing_date: Optional[date]
    termination_effective_at: Optional[datetime]
    source_url: str
    source_sha256: str
    fetched_at: datetime
    contract_id: str = ETF_LIFECYCLE_EVIDENCE_CONTRACT_ID

    def __post_init__(self) -> None:
        _require_symbol(self.symbol)
        _require_aware(self.as_of, "ETF生命周期asOf")
        if self.termination_effective_at is not None:
            _require_aware(
                self.termination_effective_at,
                "ETF终止生效时间",
            )
        _require_nonempty(self.source_url, "ETF生命周期证据URL")
        _require_sha256(self.source_sha256)
        _require_aware(self.fetched_at, "ETF生命周期抓取时间")
        if (
            self.status == EtfLifecycleStatus.TERMINATED
            and self.termination_effective_at is None
        ):
            raise ValueError("已终止ETF必须提供终止生效时间")


@dataclass(frozen=True)
class EtfIndustryScopeEvidence:
    symbol: str
    as_of: datetime
    index_provider: str
    index_code: str
    scope_kind: EtfIndustryScopeKind
    classification_version: str
    source_url: str
    source_sha256: str
    fetched_at: datetime
    contract_id: str = ETF_INDUSTRY_SCOPE_EVIDENCE_CONTRACT_ID

    def __post_init__(self) -> None:
        _require_symbol(self.symbol)
        _require_aware(self.as_of, "ETF行业范围asOf")
        _require_nonempty(self.index_provider, "ETF行业范围指数公司")
        _require_nonempty(self.index_code, "ETF行业范围指数代码")
        _require_nonempty(
            self.classification_version,
            "ETF行业范围分类版本",
        )
        _require_nonempty(self.source_url, "ETF行业范围证据URL")
        _require_sha256(self.source_sha256)
        _require_aware(self.fetched_at, "ETF行业范围抓取时间")


@dataclass(frozen=True)
class EtfFormalAdmissionItem:
    key: str
    status: EtfFormalAdmissionStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class EtfFormalAdmissionEvidence:
    symbol: str
    as_of: datetime
    rule_version: str
    status: EtfFormalAdmissionStatus
    items: Tuple[EtfFormalAdmissionItem, ...]
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = ETF_FORMAL_ADMISSION_CONTRACT_ID

    def item(self, key: str) -> EtfFormalAdmissionItem:
        for value in self.items:
            if value.key == key:
                return value
        raise KeyError(key)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "symbol": self.symbol,
            "asOf": self.as_of.isoformat(),
            "ruleVersion": self.rule_version,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
        }


def _item(
    key: str,
    reasons: Sequence[str] = (),
) -> EtfFormalAdmissionItem:
    normalized = _dedupe(reasons)
    return EtfFormalAdmissionItem(
        key=key,
        status=(
            EtfFormalAdmissionStatus.MISSING
            if normalized
            else EtfFormalAdmissionStatus.READY
        ),
        reasons=normalized,
    )


def _effective_at(
    *,
    as_of: datetime,
    published_at: Optional[datetime],
    effective_from: Optional[datetime],
    effective_to: Optional[datetime],
    first_observed_at: datetime,
    fetched_at: datetime,
) -> bool:
    return all((
        published_at is not None,
        effective_from is not None,
        published_at is not None and published_at <= as_of,
        effective_from is not None and effective_from <= as_of,
        effective_to is None or effective_to > as_of,
        first_observed_at <= as_of,
        fetched_at <= as_of,
    ))


def _relation_identity(
    relation: EtfIndexIdentityEvidence,
) -> Tuple[str, str, str]:
    return (
        relation.index_provider.strip().lower(),
        relation.provider_index_code.strip().upper(),
        relation.provider_index_name.strip(),
    )


def _index_identity(
    provider: str,
    code: str,
    name: str,
) -> Tuple[str, str, str]:
    return (
        provider.strip().lower(),
        code.strip().upper(),
        name.strip(),
    )


def provide_etf_formal_admission_evidence(
    *,
    product: EtfProductMasterRecord,
    as_of: datetime,
    lifecycle_evidence: Optional[EtfLifecycleEvidence] = None,
    industry_scope_evidence: Optional[EtfIndustryScopeEvidence] = None,
    index_relation_evidence: Optional[EtfIndexIdentityEvidence] = None,
    methodology_evidence: Optional[IndexMethodologyEvidence] = None,
    constituent_evidence: Optional[IndexConstituentSetEvidence] = None,
    industry_exposure_evidence: Optional[
        IndexIndustryExposureResult
    ] = None,
    ranking_input_evidence: Optional[EtfRankingInputAudit] = None,
    policy: EtfRulePolicy = DEFAULT_ETF_RULE_POLICY,
) -> EtfFormalAdmissionEvidence:
    """逐项核对ETF准入证据；任一缺口都封闭返回missing。"""

    _require_aware(as_of, "ETF准入asOf")

    product_reasons = []
    if product.product_type != ListedFundProductType.ETF:
        product_reasons.append("etf_product_type_not_etf")
    if product.management_style != EtfManagementStyle.PASSIVE_INDEX:
        product_reasons.append(
            "etf_management_style_not_verified_passive"
        )
    if product.asset_class != EtfAssetClass.DOMESTIC_EQUITY:
        product_reasons.append(
            "etf_asset_class_not_domestic_equity"
        )
    if product.classification_reasons:
        product_reasons.append(
            "etf_product_classification_unverified"
        )
    if product.listing_date is None:
        product_reasons.append("etf_product_listing_date_missing")
    if product.fetched_at > as_of:
        product_reasons.append("etf_product_evidence_after_as_of")
    product_item = _item("product_identity", product_reasons)

    lifecycle_reasons = []
    if lifecycle_evidence is None:
        lifecycle_reasons.append(
            "etf_product_lifecycle_evidence_missing"
        )
    elif lifecycle_evidence.contract_id != ETF_LIFECYCLE_EVIDENCE_CONTRACT_ID:
        lifecycle_reasons.append(
            "etf_product_lifecycle_contract_unverified"
        )
    elif any((
        lifecycle_evidence.symbol != product.symbol,
        lifecycle_evidence.as_of != as_of,
        (
            product.listing_date is not None
            and lifecycle_evidence.listing_date
            != product.listing_date
        ),
    )):
        lifecycle_reasons.append("etf_evidence_identity_mismatch")
    else:
        terminated_at = lifecycle_evidence.termination_effective_at
        if any((
            lifecycle_evidence.status != EtfLifecycleStatus.ACTIVE,
            lifecycle_evidence.listing_date is None,
            (
                lifecycle_evidence.listing_date is not None
                and lifecycle_evidence.listing_date > as_of.date()
            ),
            terminated_at is not None and terminated_at <= as_of,
        )):
            lifecycle_reasons.append(
                "etf_product_lifecycle_not_active"
            )
        if lifecycle_evidence.fetched_at > as_of:
            lifecycle_reasons.append(
                "etf_product_lifecycle_evidence_after_as_of"
            )
    lifecycle_item = _item(
        "product_lifecycle",
        lifecycle_reasons,
    )

    relation_identity = (
        _relation_identity(index_relation_evidence)
        if index_relation_evidence is not None
        else None
    )

    scope_reasons = []
    if industry_scope_evidence is None:
        scope_reasons.append("etf_industry_scope_evidence_missing")
    elif (
        industry_scope_evidence.contract_id
        != ETF_INDUSTRY_SCOPE_EVIDENCE_CONTRACT_ID
    ):
        scope_reasons.append("etf_industry_scope_contract_unverified")
    else:
        scope_identity = (
            industry_scope_evidence.index_provider.strip().lower(),
            industry_scope_evidence.index_code.strip().upper(),
        )
        expected_identity = (
            relation_identity[:2]
            if relation_identity is not None
            else None
        )
        if any((
            industry_scope_evidence.symbol != product.symbol,
            industry_scope_evidence.as_of != as_of,
            (
                expected_identity is not None
                and scope_identity != expected_identity
            ),
        )):
            scope_reasons.append("etf_evidence_identity_mismatch")
        if industry_scope_evidence.scope_kind not in {
            EtfIndustryScopeKind.INDUSTRY,
            EtfIndustryScopeKind.THEME,
        }:
            scope_reasons.append(
                "etf_industry_scope_not_industry_or_theme"
            )
        if industry_scope_evidence.fetched_at > as_of:
            scope_reasons.append(
                "etf_industry_scope_evidence_after_as_of"
            )
    scope_item = _item("industry_scope", scope_reasons)

    relation_reasons = []
    if index_relation_evidence is None:
        relation_reasons.append("etf_index_relation_evidence_missing")
    elif any((
        index_relation_evidence.symbol != product.symbol,
        index_relation_evidence.as_of != as_of,
    )):
        relation_reasons.append("etf_evidence_identity_mismatch")
    else:
        if (
            product.target_index_name is not None
            and product.target_index_name.strip()
            != index_relation_evidence.provider_index_name.strip()
        ):
            relation_reasons.append(
                "etf_product_index_identity_mismatch"
            )
        fund_identity_ready = all((
            index_relation_evidence.fund_index_code,
            index_relation_evidence.fund_index_name,
            (
                index_relation_evidence.fund_index_code
                == index_relation_evidence.provider_index_code
            ),
            (
                index_relation_evidence.fund_index_name
                == index_relation_evidence.provider_index_name
            ),
        ))
        if any((
            not index_relation_evidence.formal_ready,
            index_relation_evidence.status
            != IndexEvidenceStatus.VERIFIED,
            not index_relation_evidence.identity_matched,
            index_relation_evidence.management_style
            != EtfManagementStyle.PASSIVE_INDEX,
            not fund_identity_ready,
            not _effective_at(
                as_of=as_of,
                published_at=index_relation_evidence.published_at,
                effective_from=index_relation_evidence.effective_from,
                effective_to=index_relation_evidence.effective_to,
                first_observed_at=(
                    index_relation_evidence.first_observed_at
                ),
                fetched_at=index_relation_evidence.fetched_at,
            ),
        )):
            relation_reasons.append("etf_index_relation_not_verified")
    relation_item = _item("index_relation", relation_reasons)

    methodology_reasons = []
    if methodology_evidence is None:
        methodology_reasons.append(
            "etf_index_methodology_evidence_missing"
        )
    elif (
        relation_identity is None
        or _index_identity(
            methodology_evidence.index_provider,
            methodology_evidence.index_code,
            methodology_evidence.index_name,
        ) != relation_identity
        or methodology_evidence.as_of != as_of
    ):
        methodology_reasons.append("etf_evidence_identity_mismatch")
    elif any((
        not methodology_evidence.formal_ready,
        methodology_evidence.status != IndexEvidenceStatus.VERIFIED,
        methodology_evidence.version_kind != EvidenceVersionKind.OFFICIAL,
        not methodology_evidence.provider_version,
        not methodology_evidence.universe_rule,
        not methodology_evidence.selection_rule,
        not methodology_evidence.weighting_method,
        not methodology_evidence.rebalance_frequency,
        not _effective_at(
            as_of=as_of,
            published_at=methodology_evidence.published_at,
            effective_from=methodology_evidence.effective_from,
            effective_to=methodology_evidence.effective_to,
            first_observed_at=methodology_evidence.first_observed_at,
            fetched_at=methodology_evidence.fetched_at,
        ),
    )):
        methodology_reasons.append(
            "etf_index_methodology_not_verified"
        )
    methodology_item = _item(
        "index_methodology",
        methodology_reasons,
    )

    constituent_reasons = []
    if constituent_evidence is None:
        constituent_reasons.append(
            "etf_index_constituent_evidence_missing"
        )
    elif (
        relation_identity is None
        or _index_identity(
            constituent_evidence.index_provider,
            constituent_evidence.index_code,
            constituent_evidence.index_name,
        ) != relation_identity
        or constituent_evidence.as_of != as_of
    ):
        constituent_reasons.append("etf_evidence_identity_mismatch")
    else:
        stock_codes = tuple(
            item.stock_code for item in constituent_evidence.items
        )
        weights = tuple(
            item.weight for item in constituent_evidence.items
        )
        weights_are_finite = all(
            weight is not None and _finite(weight)
            for weight in weights
        )
        reported_total_matches = (
            constituent_evidence.weight_total is not None
            and weights_are_finite
            and abs(
                sum(float(weight) for weight in weights if weight is not None)
                - constituent_evidence.weight_total
            ) <= 1e-6
        )
        if any((
            not constituent_evidence.formal_ready,
            constituent_evidence.status != IndexEvidenceStatus.VERIFIED,
            constituent_evidence.returned_count <= 0,
            constituent_evidence.expected_count
            != constituent_evidence.returned_count,
            constituent_evidence.weight_count
            != constituent_evidence.returned_count,
            constituent_evidence.weight_total is None,
            (
                constituent_evidence.weight_total is not None
                and not (
                    MINIMUM_CONSTITUENT_WEIGHT_TOTAL
                    <= constituent_evidence.weight_total
                    <= MAXIMUM_CONSTITUENT_WEIGHT_TOTAL
                )
            ),
            len(stock_codes) != len(set(stock_codes)),
            not weights_are_finite,
            not reported_total_matches,
            (
                constituent_evidence.source_date is None
                or constituent_evidence.source_date > as_of.date()
            ),
            not _effective_at(
                as_of=as_of,
                published_at=constituent_evidence.announced_at,
                effective_from=constituent_evidence.effective_from,
                effective_to=constituent_evidence.effective_to,
                first_observed_at=constituent_evidence.first_observed_at,
                fetched_at=constituent_evidence.fetched_at,
            ),
        )):
            constituent_reasons.append(
                "etf_index_constituent_set_not_verified"
            )
    constituent_item = _item(
        "index_constituents",
        constituent_reasons,
    )

    exposure_reasons = []
    if industry_exposure_evidence is None:
        exposure_reasons.append(
            "etf_industry_exposure_evidence_missing"
        )
    elif (
        relation_identity is None
        or _index_identity(
            industry_exposure_evidence.index_provider,
            industry_exposure_evidence.index_code,
            industry_exposure_evidence.index_name,
        ) != relation_identity
        or industry_exposure_evidence.as_of != as_of
        or (
            constituent_evidence is not None
            and industry_exposure_evidence.constituent_source_date
            != constituent_evidence.source_date
        )
    ):
        exposure_reasons.append("etf_evidence_identity_mismatch")
    elif any((
        not industry_exposure_evidence.formal_ready,
        industry_exposure_evidence.mapping_coverage != 1.0,
        industry_exposure_evidence.unmapped_weight != 0.0,
        bool(industry_exposure_evidence.unmapped_symbols),
        not industry_exposure_evidence.exposures,
        industry_exposure_evidence.calculation_version
        != ETF_INDUSTRY_EXPOSURE_CALCULATION_VERSION,
        industry_exposure_evidence.computed_at > as_of,
    )):
        exposure_reasons.append(
            "etf_industry_exposure_not_verified"
        )
    exposure_item = _item(
        "industry_exposure",
        exposure_reasons,
    )

    ranking_reasons = []
    if ranking_input_evidence is None:
        ranking_reasons.append("etf_ranking_input_evidence_missing")
    elif any((
        ranking_input_evidence.symbol != product.symbol,
        ranking_input_evidence.as_of != as_of,
    )):
        ranking_reasons.append("etf_evidence_identity_mismatch")
    else:
        ranking_fields_ready = all(
            field_name in ranking_input_evidence.rankable_fields
            and ranking_input_evidence.field_states.get(field_name)
            == EtfMetricState.VERIFIED
            and _finite(
                ranking_input_evidence.metric_values.get(field_name)
            )
            for field_name in REQUIRED_RANKING_FIELDS
        )
        if any((
            not ranking_input_evidence.formal_ready,
            bool(ranking_input_evidence.reasons),
            not ranking_fields_ready,
            ranking_input_evidence.fetched_at > as_of,
        )):
            ranking_reasons.append("etf_ranking_input_not_verified")
    ranking_item = _item("ranking_inputs", ranking_reasons)

    policy_reasons = []
    if not policy.ranking_enabled:
        policy_reasons.append("etf_rule_not_frozen")
        policy_reasons.extend(policy.disabled_reasons)
    policy_item = _item("rule_policy", policy_reasons)

    items = (
        product_item,
        lifecycle_item,
        scope_item,
        relation_item,
        methodology_item,
        constituent_item,
        exposure_item,
        ranking_item,
        policy_item,
    )
    reasons = _dedupe(tuple(
        reason
        for item in items
        for reason in item.reasons
    ))
    return EtfFormalAdmissionEvidence(
        symbol=product.symbol,
        as_of=as_of,
        rule_version=policy.version,
        status=(
            EtfFormalAdmissionStatus.MISSING
            if reasons
            else EtfFormalAdmissionStatus.READY
        ),
        items=items,
        reasons=reasons,
    )
