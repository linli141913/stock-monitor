from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar, Dict, Generic, List, Optional, Tuple, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILED = "failed"


class UnitVerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class MarketIndexKey(str, Enum):
    SSE_COMPOSITE = "sse_composite"
    SZSE_COMPONENT = "szse_component"
    CHINEXT = "chinext"
    STAR50 = "star50"


class IndustryHistoryStatus(str, Enum):
    FORWARD_OBSERVED = "forward_observed"
    RETROSPECTIVE_UNVERIFIED = "retrospective_unverified"


class IndustryIdentityStatus(str, Enum):
    EXACT = "exact"
    VERIFIED_ALIAS = "verified_alias"
    UNRESOLVED = "unresolved"


class IndustryRecordStatus(str, Enum):
    ACCEPTED = "accepted"
    UNCONFIRMED = "unconfirmed"
    CONFLICT = "conflict"
    SOURCE_FAILED = "source_failed"


class IndustryMiddleClassStatus(str, Enum):
    SOURCE_NOT_PUBLISHED = "source_not_published"


class SourceIssue(ContractModel):
    code: str
    message: str
    source: Optional[str] = None
    batch_index: Optional[int] = Field(default=None, alias="batchIndex", ge=0)
    symbols: List[str] = Field(default_factory=list)


class RadarBatchMeta(ContractModel):
    radar_run_id: str = Field(alias="radarRunId", min_length=1)
    batch_id: str = Field(alias="batchId", min_length=1)
    source: str = Field(min_length=1)
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    expected_count: Optional[int] = Field(default=None, alias="expectedCount", ge=0)
    returned_count: int = Field(alias="returnedCount", ge=0)
    row_coverage: Optional[float] = Field(
        default=None,
        alias="rowCoverage",
        ge=0,
        le=1,
    )
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    issues: List[SourceIssue] = Field(default_factory=list)

    @field_validator("as_of", "source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间字段必须包含时区")
        return value

    @field_validator("required_field_coverage")
    @classmethod
    def validate_field_coverage(cls, value):
        for field_name, coverage in value.items():
            if not field_name:
                raise ValueError("覆盖率字段名不能为空")
            if not 0 <= coverage <= 1:
                raise ValueError("字段覆盖率必须在0到1之间")
        return value

    @model_validator(mode="after")
    def validate_counts(self):
        if self.expected_count is None:
            if self.row_coverage is not None:
                raise ValueError("expectedCount未知时rowCoverage必须为空")
            return self
        if self.returned_count > self.expected_count:
            raise ValueError("returnedCount不能大于expectedCount")
        expected_coverage = (
            self.returned_count / self.expected_count
            if self.expected_count
            else 0.0
        )
        if self.row_coverage is None:
            raise ValueError("expectedCount已知时必须提供rowCoverage")
        if abs(self.row_coverage - expected_coverage) > 1e-9:
            raise ValueError("rowCoverage必须由returnedCount/expectedCount计算")
        return self


class SecurityMasterRecord(ContractModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    exchange: str
    board: str
    listing_date: Optional[date] = Field(default=None, alias="listingDate")
    total_shares: Optional[float] = Field(default=None, alias="totalShares")
    circulating_shares: Optional[float] = Field(
        default=None,
        alias="circulatingShares",
    )
    source_industry: Optional[str] = Field(default=None, alias="sourceIndustry")
    source_report_date: Optional[date] = Field(
        default=None,
        alias="sourceReportDate",
    )
    source: str
    fetched_at: datetime = Field(alias="fetchedAt")
    source_fields: Dict[str, Any] = Field(default_factory=dict, alias="sourceFields")

    @field_validator("fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetchedAt必须包含时区")
        return value


class VerifiedSecurityAlias(ContractModel):
    source_symbol: str = Field(alias="sourceSymbol", pattern=r"^\d{6}$")
    security_identity: str = Field(
        alias="securityIdentity",
        pattern=r"^\d{6}$",
    )
    published_date: date = Field(alias="publishedDate")
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_to: Optional[datetime] = Field(default=None, alias="effectiveTo")
    evidence_url: str = Field(alias="evidenceUrl", min_length=1)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("别名生效时间必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_effective_interval(self):
        if self.source_symbol == self.security_identity:
            raise ValueError("别名原代码与稳定身份不能相同")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("别名失效时间必须晚于生效时间")
        return self


class IndustryClassificationRelease(ContractModel):
    classification_system: str = Field(
        default="capco_listed_company_industry",
        alias="classificationSystem",
        pattern=r"^capco_listed_company_industry$",
    )
    scheme_version: str = Field(alias="schemeVersion", min_length=1)
    release_period: str = Field(alias="releasePeriod", pattern=r"^\d{4}H[12]$")
    source_page_title: str = Field(alias="sourcePageTitle", min_length=1)
    publication_page_url: str = Field(alias="publicationPageUrl", min_length=1)
    document_url: str = Field(alias="documentUrl", min_length=1)
    document_sha256: str = Field(
        alias="documentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    published_date: date = Field(alias="publishedDate")
    first_observed_at: datetime = Field(alias="firstObservedAt")
    fetched_at: datetime = Field(alias="fetchedAt")
    knowledge_effective_from: datetime = Field(alias="knowledgeEffectiveFrom")
    knowledge_effective_to: Optional[datetime] = Field(
        default=None,
        alias="knowledgeEffectiveTo",
    )
    classification_start_date: date = Field(alias="classificationStartDate")
    history_status: IndustryHistoryStatus = Field(alias="historyStatus")
    source_record_count: int = Field(alias="sourceRecordCount", ge=0)
    unique_source_symbol_count: int = Field(
        alias="uniqueSourceSymbolCount",
        ge=0,
    )
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )

    @field_validator(
        "first_observed_at",
        "fetched_at",
        "knowledge_effective_from",
        "knowledge_effective_to",
    )
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("行业版本时间必须包含时区")
        return value

    @field_validator("required_field_coverage")
    @classmethod
    def validate_field_coverage(cls, value):
        for field_name, coverage in value.items():
            if not field_name:
                raise ValueError("覆盖率字段名不能为空")
            if not 0 <= coverage <= 1:
                raise ValueError("字段覆盖率必须在0到1之间")
        return value

    @model_validator(mode="after")
    def validate_release_times_and_counts(self):
        if self.fetched_at < self.first_observed_at:
            raise ValueError("fetchedAt不能早于firstObservedAt")
        if self.knowledge_effective_from < self.first_observed_at:
            raise ValueError("knowledgeEffectiveFrom不能早于firstObservedAt")
        if (
            self.knowledge_effective_to is not None
            and self.knowledge_effective_to <= self.knowledge_effective_from
        ):
            raise ValueError("knowledgeEffectiveTo必须晚于knowledgeEffectiveFrom")
        if self.unique_source_symbol_count > self.source_record_count:
            raise ValueError("唯一来源代码数不能大于来源记录数")
        return self


class IndustryClassificationRecord(ContractModel):
    classification_system: str = Field(
        default="capco_listed_company_industry",
        alias="classificationSystem",
        pattern=r"^capco_listed_company_industry$",
    )
    release_period: str = Field(alias="releasePeriod", pattern=r"^\d{4}H[12]$")
    source_symbol: str = Field(alias="sourceSymbol", pattern=r"^\d{6}$")
    source_name: str = Field(alias="sourceName", min_length=1)
    security_identity: Optional[str] = Field(
        default=None,
        alias="securityIdentity",
        pattern=r"^\d{6}$",
    )
    identity_status: IndustryIdentityStatus = Field(alias="identityStatus")
    category_code: str = Field(alias="categoryCode", pattern=r"^[A-T]$")
    category_name: str = Field(alias="categoryName", min_length=1)
    division_code: str = Field(alias="divisionCode", pattern=r"^\d{2}$")
    division_name: str = Field(alias="divisionName", min_length=1)
    manufacturing_subclass_code: Optional[str] = Field(
        default=None,
        alias="manufacturingSubclassCode",
        pattern=r"^[A-Z]{2}$",
    )
    manufacturing_subclass_name: Optional[str] = Field(
        default=None,
        alias="manufacturingSubclassName",
        min_length=1,
    )
    middle_class_code: Optional[str] = Field(default=None, alias="middleClassCode")
    middle_class_name: Optional[str] = Field(default=None, alias="middleClassName")
    middle_class_status: IndustryMiddleClassStatus = Field(
        default=IndustryMiddleClassStatus.SOURCE_NOT_PUBLISHED,
        alias="middleClassStatus",
    )
    record_status: IndustryRecordStatus = Field(alias="recordStatus")
    issue_codes: Tuple[str, ...] = Field(default=(), alias="issueCodes")
    source_fields: Dict[str, Any] = Field(default_factory=dict, alias="sourceFields")

    @model_validator(mode="after")
    def validate_levels_and_identity(self):
        subclass_pair = (
            self.manufacturing_subclass_code,
            self.manufacturing_subclass_name,
        )
        if (subclass_pair[0] is None) != (subclass_pair[1] is None):
            raise ValueError("制造业次类代码和名称必须同时存在或同时缺失")
        if self.category_code == "C" and subclass_pair[0] is None:
            raise ValueError("制造业记录必须提供次类代码和名称")
        if self.category_code != "C" and subclass_pair[0] is not None:
            raise ValueError("非制造业记录不得提供制造业次类")
        if self.middle_class_code is not None or self.middle_class_name is not None:
            raise ValueError("中上协公开结果未发布中类，不得填充")
        if self.identity_status == IndustryIdentityStatus.UNRESOLVED:
            if self.security_identity is not None:
                raise ValueError("未解析身份不得填充securityIdentity")
            if self.record_status == IndustryRecordStatus.ACCEPTED:
                raise ValueError("未解析身份不得标记accepted")
        elif self.security_identity is None:
            raise ValueError("已解析身份必须提供securityIdentity")
        if (
            self.record_status == IndustryRecordStatus.ACCEPTED
            and self.identity_status == IndustryIdentityStatus.UNRESOLVED
        ):
            raise ValueError("accepted记录必须具有已解析身份")
        return self


class IndustryClassificationGap(ContractModel):
    security_identity: str = Field(alias="securityIdentity", pattern=r"^\d{6}$")
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    listing_date: Optional[date] = Field(default=None, alias="listingDate")
    record_status: IndustryRecordStatus = Field(
        default=IndustryRecordStatus.UNCONFIRMED,
        alias="recordStatus",
    )
    issue_codes: Tuple[str, ...] = Field(default=(), alias="issueCodes")


class IndustryClassificationCompleteness(ContractModel):
    source_record_count: int = Field(alias="sourceRecordCount", ge=0)
    unique_source_symbol_count: int = Field(
        alias="uniqueSourceSymbolCount",
        ge=0,
    )
    current_master_count: int = Field(alias="currentMasterCount", ge=0)
    mapped_count: int = Field(alias="mappedCount", ge=0)
    unconfirmed_count: int = Field(alias="unconfirmedCount", ge=0)
    excluded_source_count: int = Field(alias="excludedSourceCount", ge=0)
    mapping_coverage: Optional[float] = Field(
        default=None,
        alias="mappingCoverage",
        ge=0,
        le=1,
    )
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    shadow_usable: bool = Field(alias="shadowUsable")
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @field_validator("required_field_coverage")
    @classmethod
    def validate_field_coverage(cls, value):
        for field_name, coverage in value.items():
            if not field_name:
                raise ValueError("覆盖率字段名不能为空")
            if not 0 <= coverage <= 1:
                raise ValueError("字段覆盖率必须在0到1之间")
        return value

    @model_validator(mode="after")
    def validate_counts_and_coverage(self):
        if self.unique_source_symbol_count > self.source_record_count:
            raise ValueError("唯一来源代码数不能大于来源记录数")
        if self.excluded_source_count > self.source_record_count:
            raise ValueError("排除来源记录数不能大于来源记录数")
        if self.mapped_count > self.current_master_count:
            raise ValueError("映射数量不能大于当前主档数量")
        if self.mapped_count + self.unconfirmed_count != self.current_master_count:
            raise ValueError("映射与未确认数量必须覆盖当前主档")
        if self.current_master_count == 0:
            if self.mapping_coverage is not None:
                raise ValueError("当前主档为空时mappingCoverage必须为空")
        else:
            expected = self.mapped_count / self.current_master_count
            if self.mapping_coverage is None:
                raise ValueError("当前主档非空时必须提供mappingCoverage")
            if abs(self.mapping_coverage - expected) > 1e-9:
                raise ValueError("mappingCoverage必须由mappedCount/currentMasterCount计算")
        return self


class IndustryClassificationSnapshot(ContractModel):
    meta: RadarBatchMeta
    status: SourceStatus
    release: Optional[IndustryClassificationRelease] = None
    records: List[IndustryClassificationRecord] = Field(default_factory=list)
    current_master_gaps: List[IndustryClassificationGap] = Field(
        default_factory=list,
        alias="currentMasterGaps",
    )
    completeness: IndustryClassificationCompleteness
    issues: List[SourceIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_snapshot_counts(self):
        if self.release is not None and len(self.records) != self.release.source_record_count:
            raise ValueError("行业记录数必须与发布版本来源记录数一致")
        if len(self.current_master_gaps) != self.completeness.unconfirmed_count:
            raise ValueError("当前主档缺口数必须与unconfirmedCount一致")
        if self.status != SourceStatus.FAILED and self.release is None:
            raise ValueError("非失败快照必须包含发布版本")
        return self


class QuoteSnapshot(ContractModel):
    REQUIRED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "price",
        "change_percent",
        "turnover_amount_source",
        "turnover_rate_percent",
        "volume_ratio",
        "market_cap_source",
    )

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    price: Optional[float] = None
    change_percent: Optional[float] = Field(default=None, alias="changePercent")
    turnover_amount_source: Optional[float] = Field(
        default=None,
        alias="turnoverAmountSource",
    )
    turnover_rate_percent: Optional[float] = Field(
        default=None,
        alias="turnoverRatePercent",
    )
    volume_ratio: Optional[float] = Field(default=None, alias="volumeRatio")
    market_cap_source: Optional[float] = Field(
        default=None,
        alias="marketCapSource",
    )
    source: str = "tencent_finance"

    @field_validator("source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间字段必须包含时区")
        return value

    def missing_fields(self) -> Tuple[str, ...]:
        return tuple(
            field_name
            for field_name in self.REQUIRED_FIELDS
            if getattr(self, field_name) is None
        )


class IndexQuoteSnapshot(ContractModel):
    REQUIRED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "price",
        "change_percent",
        "source_time",
    )

    index_key: MarketIndexKey = Field(alias="indexKey")
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    exchange: str = Field(pattern=r"^(sse|szse)$")
    source_symbol: str = Field(alias="sourceSymbol", pattern=r"^(sh|sz)\d{6}$")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    price: Optional[float] = None
    change_percent: Optional[float] = Field(default=None, alias="changePercent")
    source: str = "tencent_finance"

    @field_validator("source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间字段必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_exchange_identity(self):
        expected_prefix = "sh" if self.exchange == "sse" else "sz"
        if not self.source_symbol.startswith(expected_prefix):
            raise ValueError("指数交易所与来源代码前缀不一致")
        if self.source_symbol[2:] != self.symbol:
            raise ValueError("指数来源代码与指数代码不一致")
        return self

    def missing_fields(self) -> Tuple[str, ...]:
        return tuple(
            field_name
            for field_name in self.REQUIRED_FIELDS
            if getattr(self, field_name) is None
        )


class FeatureCompleteness(ContractModel):
    expected_count: int = Field(alias="expectedCount", ge=0)
    returned_count: int = Field(alias="returnedCount", ge=0)
    valid_count: int = Field(alias="validCount", ge=0)
    row_coverage: float = Field(alias="rowCoverage", ge=0, le=1)
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    is_complete: bool = Field(alias="isComplete")
    reasons: Tuple[str, ...] = ()

    @field_validator("required_field_coverage")
    @classmethod
    def validate_field_coverage(cls, value):
        for field_name, coverage in value.items():
            if not field_name:
                raise ValueError("覆盖率字段名不能为空")
            if not 0 <= coverage <= 1:
                raise ValueError("字段覆盖率必须在0到1之间")
        return value

    @model_validator(mode="after")
    def validate_counts(self):
        if self.returned_count > self.expected_count:
            raise ValueError("returnedCount不能大于expectedCount")
        if self.valid_count > self.returned_count:
            raise ValueError("validCount不能大于returnedCount")
        expected_coverage = (
            self.returned_count / self.expected_count
            if self.expected_count
            else 0.0
        )
        if abs(self.row_coverage - expected_coverage) > 1e-9:
            raise ValueError("rowCoverage必须由returnedCount/expectedCount计算")
        return self


class MarketBreadthSnapshot(ContractModel):
    advancers: int = Field(ge=0)
    decliners: int = Field(ge=0)
    flat: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    completeness: FeatureCompleteness

    @model_validator(mode="after")
    def validate_total(self):
        total = self.advancers + self.decliners + self.flat + self.unavailable
        if total != self.completeness.expected_count:
            raise ValueError("市场广度计数必须覆盖完整预期股票池")
        return self


class MarketTurnoverSnapshot(ContractModel):
    raw_value: Optional[float] = Field(default=None, alias="rawValue")
    contributing_count: int = Field(alias="contributingCount", ge=0)
    unit_status: UnitVerificationStatus = Field(alias="unitStatus")
    formal_usable: bool = Field(alias="formalUsable")
    completeness: FeatureCompleteness
    reasons: Tuple[str, ...] = ()


class MarketFeatureSnapshot(ContractModel):
    radar_run_id: str = Field(alias="radarRunId", min_length=1)
    index_batch_id: str = Field(alias="indexBatchId", min_length=1)
    quote_batch_id: str = Field(alias="quoteBatchId", min_length=1)
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    indices: List[IndexQuoteSnapshot]
    index_completeness: FeatureCompleteness = Field(alias="indexCompleteness")
    breadth: MarketBreadthSnapshot
    turnover: MarketTurnoverSnapshot
    excluded_etf_count: int = Field(alias="excludedEtfCount", ge=0)
    duplicate_symbols: Tuple[str, ...] = Field(
        default=(),
        alias="duplicateSymbols",
    )
    unknown_symbols: Tuple[str, ...] = Field(
        default=(),
        alias="unknownSymbols",
    )

    @field_validator("as_of", "source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间字段必须包含时区")
        return value


class SectorMetricValue(ContractModel):
    raw_value: Optional[float] = Field(default=None, alias="rawValue")
    available: bool
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_availability(self):
        if self.available != (self.raw_value is not None):
            raise ValueError("available必须与rawValue是否存在一致")
        if self.formal_usable and not self.available:
            raise ValueError("不可用指标不能标记为正式可用")
        return self


class SectorConstituentCompleteness(ContractModel):
    expected_count: int = Field(alias="expectedCount", ge=0)
    returned_count: int = Field(alias="returnedCount", ge=0)
    fresh_count: int = Field(alias="freshCount", ge=0)
    valid_return_count: int = Field(alias="validReturnCount", ge=0)
    valid_market_cap_count: int = Field(alias="validMarketCapCount", ge=0)
    valid_turnover_count: int = Field(alias="validTurnoverCount", ge=0)
    row_coverage: float = Field(alias="rowCoverage", ge=0, le=1)
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    is_complete: bool = Field(alias="isComplete")
    reasons: Tuple[str, ...] = ()

    @field_validator("required_field_coverage")
    @classmethod
    def validate_field_coverage(cls, value):
        for field_name, coverage in value.items():
            if not field_name:
                raise ValueError("覆盖率字段名不能为空")
            if not 0 <= coverage <= 1:
                raise ValueError("字段覆盖率必须在0到1之间")
        return value

    @model_validator(mode="after")
    def validate_counts(self):
        if self.returned_count > self.expected_count:
            raise ValueError("returnedCount不能大于expectedCount")
        for value in (
            self.fresh_count,
            self.valid_return_count,
            self.valid_market_cap_count,
            self.valid_turnover_count,
        ):
            if value > self.returned_count:
                raise ValueError("有效成分数不能大于returnedCount")
        expected_coverage = (
            self.returned_count / self.expected_count
            if self.expected_count
            else 0.0
        )
        if abs(self.row_coverage - expected_coverage) > 1e-9:
            raise ValueError("rowCoverage必须由returnedCount/expectedCount计算")
        return self


class SectorReturnSnapshot(ContractModel):
    equal_return: SectorMetricValue = Field(alias="equalReturn")
    cap_weighted_return: SectorMetricValue = Field(alias="capWeightedReturn")
    ex_top_return: SectorMetricValue = Field(alias="exTopReturn")
    top_contributor_symbol: Optional[str] = Field(
        default=None,
        alias="topContributorSymbol",
        pattern=r"^\d{6}$",
    )
    top_contribution_percent_points: Optional[float] = Field(
        default=None,
        alias="topContributionPercentPoints",
    )
    market_cap_basis: str = Field(
        default="total_market_cap_source",
        alias="marketCapBasis",
        pattern=r"^total_market_cap_source$",
    )
    market_cap_unit_status: UnitVerificationStatus = Field(
        alias="marketCapUnitStatus",
    )
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_top_contributor(self):
        top_pair = (
            self.top_contributor_symbol,
            self.top_contribution_percent_points,
        )
        if (top_pair[0] is None) != (top_pair[1] is None):
            raise ValueError("第一贡献股代码和贡献值必须同时存在或同时缺失")
        if self.cap_weighted_return.available and top_pair[0] is None:
            raise ValueError("市值加权收益可用时必须提供第一贡献股")
        if not self.cap_weighted_return.available and top_pair[0] is not None:
            raise ValueError("市值加权收益不可用时不得提供第一贡献股")
        if self.formal_usable and not all((
            self.equal_return.formal_usable,
            self.cap_weighted_return.formal_usable,
            self.ex_top_return.formal_usable,
        )):
            raise ValueError("行业收益正式可用时三个收益指标必须均正式可用")
        return self


class SectorBreadthSnapshot(ContractModel):
    advancers: int = Field(ge=0)
    decliners: int = Field(ge=0)
    flat: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    up_ratio: SectorMetricValue = Field(alias="upRatio")
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_up_ratio(self):
        valid_count = self.advancers + self.decliners + self.flat
        if self.up_ratio.available:
            if valid_count == 0:
                raise ValueError("无有效成分时upRatio不得可用")
            expected = self.advancers / valid_count
            if abs(self.up_ratio.raw_value - expected) > 1e-9:
                raise ValueError("upRatio必须由上涨数/有效成分数计算")
        if self.formal_usable and not self.up_ratio.formal_usable:
            raise ValueError("行业广度正式可用时upRatio必须正式可用")
        return self


class SectorTurnoverSnapshot(ContractModel):
    raw_value: Optional[float] = Field(default=None, alias="rawValue")
    contributing_count: int = Field(alias="contributingCount", ge=0)
    unit_status: UnitVerificationStatus = Field(alias="unitStatus")
    available: bool
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_availability(self):
        if self.available != (self.raw_value is not None):
            raise ValueError("available必须与rawValue是否存在一致")
        if self.formal_usable and not self.available:
            raise ValueError("不可用成交额不能标记为正式可用")
        return self


class SectorFeatureSnapshot(ContractModel):
    classification_system: str = Field(
        default="capco_listed_company_industry",
        alias="classificationSystem",
        pattern=r"^capco_listed_company_industry$",
    )
    release_period: str = Field(alias="releasePeriod", pattern=r"^\d{4}H[12]$")
    category_code: str = Field(alias="categoryCode", pattern=r"^[A-T]$")
    category_name: str = Field(alias="categoryName", min_length=1)
    division_code: str = Field(alias="divisionCode", pattern=r"^\d{2}$")
    division_name: str = Field(alias="divisionName", min_length=1)
    constituent_symbols: Tuple[str, ...] = Field(alias="constituentSymbols")
    completeness: SectorConstituentCompleteness
    returns: SectorReturnSnapshot
    breadth: SectorBreadthSnapshot
    turnover: SectorTurnoverSnapshot
    shadow_usable: bool = Field(alias="shadowUsable")
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @field_validator("constituent_symbols")
    @classmethod
    def validate_constituent_symbols(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("行业成分代码不得重复")
        if any(not symbol.isdigit() or len(symbol) != 6 for symbol in value):
            raise ValueError("行业成分代码必须为6位数字")
        return value

    @model_validator(mode="after")
    def validate_constituent_count(self):
        if len(self.constituent_symbols) != self.completeness.expected_count:
            raise ValueError("行业成分数必须与expectedCount一致")
        breadth_total = (
            self.breadth.advancers
            + self.breadth.decliners
            + self.breadth.flat
            + self.breadth.unavailable
        )
        if breadth_total != self.completeness.expected_count:
            raise ValueError("行业广度计数必须覆盖完整成分集合")
        if self.turnover.contributing_count > self.completeness.expected_count:
            raise ValueError("成交额贡献成分数不能大于行业成分数")
        if self.formal_usable and not self.shadow_usable:
            raise ValueError("正式可用行业特征必须先满足影子可用")
        return self


class SectorFeatureBatch(ContractModel):
    radar_run_id: str = Field(alias="radarRunId", min_length=1)
    classification_batch_id: str = Field(
        alias="classificationBatchId",
        min_length=1,
    )
    quote_batch_id: str = Field(alias="quoteBatchId", min_length=1)
    classification_system: str = Field(
        default="capco_listed_company_industry",
        alias="classificationSystem",
        pattern=r"^capco_listed_company_industry$",
    )
    release_period: str = Field(alias="releasePeriod", pattern=r"^\d{4}H[12]$")
    classification_document_sha256: str = Field(
        alias="classificationDocumentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    classification_mapping_coverage: Optional[float] = Field(
        default=None,
        alias="classificationMappingCoverage",
        ge=0,
        le=1,
    )
    mapped_constituent_count: int = Field(alias="mappedConstituentCount", ge=0)
    unconfirmed_stock_count: int = Field(alias="unconfirmedStockCount", ge=0)
    sectors: List[SectorFeatureSnapshot] = Field(default_factory=list)
    excluded_etf_count: int = Field(alias="excludedEtfCount", ge=0)
    duplicate_quote_symbols: Tuple[str, ...] = Field(
        default=(),
        alias="duplicateQuoteSymbols",
    )
    unknown_quote_symbols: Tuple[str, ...] = Field(
        default=(),
        alias="unknownQuoteSymbols",
    )
    status: SourceStatus
    shadow_usable: bool = Field(alias="shadowUsable")
    formal_usable: bool = Field(alias="formalUsable")
    reasons: Tuple[str, ...] = ()

    @field_validator("as_of", "source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("行业特征时间必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_batch(self):
        division_codes = [sector.division_code for sector in self.sectors]
        if len(division_codes) != len(set(division_codes)):
            raise ValueError("行业特征批次内大类代码不得重复")
        if self.formal_usable and not self.shadow_usable:
            raise ValueError("正式可用行业批次必须先满足影子可用")
        return self


class EtfRegistryRecord(ContractModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    exchange: str
    source_type: Optional[str] = Field(default=None, alias="sourceType")
    investment_type: Optional[str] = Field(default=None, alias="investmentType")
    listing_date: Optional[date] = Field(default=None, alias="listingDate")
    fund_shares: Optional[float] = Field(default=None, alias="fundShares")
    manager: Optional[str] = None
    sponsor: Optional[str] = None
    custodian: Optional[str] = None
    nav: Optional[float] = None
    source_report_date: Optional[date] = Field(
        default=None,
        alias="sourceReportDate",
    )
    source: str
    fetched_at: datetime = Field(alias="fetchedAt")
    source_fields: Dict[str, Any] = Field(default_factory=dict, alias="sourceFields")

    @field_validator("fetched_at")
    @classmethod
    def require_aware_datetime(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetchedAt必须包含时区")
        return value


T = TypeVar("T")


class SourceBatch(ContractModel, Generic[T]):
    meta: RadarBatchMeta
    items: List[T]


class SourceHealthResult(ContractModel):
    status: SourceStatus
    allows_new_state: bool = Field(alias="allowsNewState")
    reasons: Tuple[str, ...] = ()
    age_seconds: Optional[float] = Field(default=None, alias="ageSeconds")
