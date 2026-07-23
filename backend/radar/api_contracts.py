from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


RadarModuleState = Literal[
    "available",
    "empty",
    "stale",
    "failed",
    "not_ready",
    "not_enabled",
]
RadarModuleQuality = Literal["complete", "partial", "unavailable"]


class RadarApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RadarMarketSession(RadarApiModel):
    code: str
    label: str
    calendar_kind: str = Field(alias="calendarKind")
    calendar_source_url: Optional[str] = Field(
        default=None,
        alias="calendarSourceUrl",
    )
    calendar_checked_at: str = Field(alias="calendarCheckedAt")


class RadarLastAttempt(RadarApiModel):
    radar_run_id: str = Field(alias="radarRunId")
    as_of: datetime = Field(alias="asOf")
    status: str
    shadow_mode: bool = Field(alias="shadowMode")
    rule_version_id: Optional[str] = Field(default=None, alias="ruleVersionId")
    started_at: datetime = Field(alias="startedAt")
    completed_at: Optional[datetime] = Field(default=None, alias="completedAt")
    error_code: Optional[str] = Field(default=None, alias="errorCode")


class RadarLastSuccess(RadarApiModel):
    radar_run_id: str = Field(alias="radarRunId")
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")


class RadarFreshness(RadarApiModel):
    age_seconds: Optional[int] = Field(default=None, alias="ageSeconds")
    stale_after_seconds: int = Field(alias="staleAfterSeconds")
    is_stale: bool = Field(alias="isStale")
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarSourceStatus(RadarApiModel):
    batch_id: str = Field(alias="batchId")
    source: str
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    status: str
    expected_count: Optional[int] = Field(default=None, alias="expectedCount")
    returned_count: int = Field(alias="returnedCount")
    row_coverage: Optional[float] = Field(default=None, alias="rowCoverage")
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarCompleteness(RadarApiModel):
    expected_count: int = Field(alias="expectedCount", ge=0)
    returned_count: int = Field(alias="returnedCount", ge=0)
    valid_count: int = Field(alias="validCount", ge=0)
    row_coverage: float = Field(alias="rowCoverage", ge=0)
    required_field_coverage: Dict[str, float] = Field(
        default_factory=dict,
        alias="requiredFieldCoverage",
    )
    is_complete: bool = Field(alias="isComplete")
    reasons: List[str] = Field(default_factory=list)


class RadarMarketBreadth(RadarApiModel):
    advancers: int = Field(ge=0)
    decliners: int = Field(ge=0)
    flat: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    completeness: RadarCompleteness


class RadarMarketTurnover(RadarApiModel):
    contributing_count: int = Field(alias="contributingCount", ge=0)
    unit_status: Literal["verified", "unverified"] = Field(alias="unitStatus")
    display_allowed: bool = Field(alias="displayAllowed")
    completeness: RadarCompleteness
    reasons: List[str] = Field(default_factory=list)


class RadarIndexSnapshot(RadarApiModel):
    index_key: str = Field(alias="indexKey")
    symbol: str
    name: str
    exchange: str
    source_symbol: str = Field(alias="sourceSymbol")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    price: Optional[float] = None
    change_percent: Optional[float] = Field(
        default=None,
        alias="changePercent",
    )
    source: str
    missing_fields: List[str] = Field(
        default_factory=list,
        alias="missingFields",
    )


class RadarMarketData(RadarApiModel):
    radar_run_id: str = Field(alias="radarRunId")
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    formal_state_enabled: Literal[False] = Field(alias="formalStateEnabled")
    index_completeness: RadarCompleteness = Field(alias="indexCompleteness")
    breadth: RadarMarketBreadth
    turnover: RadarMarketTurnover
    excluded_etf_count: int = Field(alias="excludedEtfCount", ge=0)
    duplicate_symbol_count: int = Field(alias="duplicateSymbolCount", ge=0)
    unknown_symbol_count: int = Field(alias="unknownSymbolCount", ge=0)
    indices: List[RadarIndexSnapshot] = Field(default_factory=list)


class RadarMarketModule(RadarApiModel):
    state: RadarModuleState
    quality: RadarModuleQuality
    using_last_success: bool = Field(alias="usingLastSuccess")
    last_attempt: Optional[RadarLastAttempt] = Field(
        default=None,
        alias="lastAttempt",
    )
    last_success: Optional[RadarLastSuccess] = Field(
        default=None,
        alias="lastSuccess",
    )
    freshness: RadarFreshness
    sources: List[RadarSourceStatus] = Field(default_factory=list)
    data: Optional[RadarMarketData] = None


class RadarSectorSummary(RadarApiModel):
    total_count: int = Field(alias="totalCount", ge=0)
    usable_count: int = Field(alias="usableCount", ge=0)
    unavailable_count: int = Field(alias="unavailableCount", ge=0)


class RadarSectorItem(RadarApiModel):
    division_code: str = Field(alias="divisionCode")
    division_name: str = Field(alias="divisionName")
    category_code: str = Field(alias="categoryCode")
    category_name: str = Field(alias="categoryName")
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    classification_mapping_coverage: float = Field(
        alias="classificationMappingCoverage",
        ge=0,
    )
    mapped_constituent_count: int = Field(
        alias="mappedConstituentCount",
        ge=0,
    )
    unconfirmed_stock_count: int = Field(alias="unconfirmedStockCount", ge=0)
    expected_count: int = Field(alias="expectedCount", ge=0)
    returned_count: int = Field(alias="returnedCount", ge=0)
    fresh_count: int = Field(alias="freshCount", ge=0)
    row_coverage: float = Field(alias="rowCoverage", ge=0)
    is_complete: bool = Field(alias="isComplete")
    equal_return: Optional[float] = Field(default=None, alias="equalReturn")
    advancers: int = Field(ge=0)
    decliners: int = Field(ge=0)
    flat: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    up_ratio: Optional[float] = Field(default=None, alias="upRatio")
    shadow_usable: bool = Field(alias="shadowUsable")
    reasons: List[str] = Field(default_factory=list)


class RadarSectorModule(RadarApiModel):
    state: RadarModuleState
    quality: RadarModuleQuality
    using_last_success: bool = Field(alias="usingLastSuccess")
    last_attempt: Optional[RadarLastAttempt] = Field(
        default=None,
        alias="lastAttempt",
    )
    last_success: Optional[RadarLastSuccess] = Field(
        default=None,
        alias="lastSuccess",
    )
    freshness: RadarFreshness
    sources: List[RadarSourceStatus] = Field(default_factory=list)
    summary: RadarSectorSummary
    items: List[RadarSectorItem]


class RadarDeferredModule(RadarApiModel):
    state: Literal["not_enabled"] = "not_enabled"
    quality: Literal["unavailable"] = "unavailable"
    enabled_stage: int = Field(alias="enabledStage", ge=1)
    reason_code: Literal["stage_not_enabled"] = Field(
        default="stage_not_enabled",
        alias="reasonCode",
    )
    data: None = None


class RadarModuleCollection(RadarApiModel):
    market: RadarMarketModule
    sectors: RadarSectorModule
    etf: RadarDeferredModule
    leaders: RadarDeferredModule
    history: RadarDeferredModule


class RadarOverviewResponse(RadarApiModel):
    schema_version: Literal["radar-overview-v1"] = Field(
        default="radar-overview-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    module_skew_seconds: Optional[int] = Field(
        default=None,
        alias="moduleSkewSeconds",
    )
    modules: RadarModuleCollection


class RadarSectorsResponse(RadarApiModel):
    schema_version: Literal["radar-sectors-v1"] = Field(
        default="radar-sectors-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    module: RadarSectorModule
