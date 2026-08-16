from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union

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


class RadarEtfAttempt(RadarApiModel):
    product_master_run_id: str = Field(alias="productMasterRunId")
    as_of: datetime = Field(alias="asOf")
    source: str
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    status: Literal["succeeded", "degraded", "failed"]
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
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    inserted_count: int = Field(alias="insertedCount", ge=0)
    unchanged_count: int = Field(alias="unchangedCount", ge=0)


class RadarEtfSnapshot(RadarApiModel):
    radar_run_id: str = Field(alias="radarRunId")
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")


class RadarEtfSummary(RadarApiModel):
    product_count: int = Field(alias="productCount", ge=0)
    eligible_product_count: int = Field(alias="eligibleProductCount", ge=0)
    candidate_group_count: int = Field(alias="candidateGroupCount", ge=0)
    computed_count: int = Field(alias="computedCount", ge=0)
    stale_count: int = Field(alias="staleCount", ge=0)
    missing_count: int = Field(alias="missingCount", ge=0)
    coverage: float = Field(ge=0, le=1)
    formal_usable_count: int = Field(alias="formalUsableCount", ge=0)
    rule_version_id: Optional[str] = Field(default=None, alias="ruleVersionId")
    formal_state_enabled: Literal[False] = Field(
        default=False,
        alias="formalStateEnabled",
    )
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarEtfProductItem(RadarApiModel):
    symbol: str
    official_name: str = Field(alias="officialName")
    exchange: Literal["sse", "szse"]
    product_type: str = Field(alias="productType")
    management_style: str = Field(alias="managementStyle")
    asset_class: str = Field(alias="assetClass")
    target_index_name: Optional[str] = Field(default=None, alias="targetIndexName")
    classification_mapping_version: str = Field(
        alias="classificationMappingVersion",
    )
    classification_reasons: List[str] = Field(
        default_factory=list,
        alias="classificationReasons",
    )
    source_contract_id: str = Field(alias="sourceContractId")
    source: str
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    version_time_kind: Literal["official_effective", "first_observed"] = Field(
        alias="versionTimeKind",
    )
    official_effective_from: Optional[datetime] = Field(
        default=None,
        alias="officialEffectiveFrom",
    )
    first_observed_at: datetime = Field(alias="firstObservedAt")
    effective_from: datetime = Field(alias="effectiveFrom")
    historical_replay_ready: bool = Field(alias="historicalReplayReady")
    evidence_url: Optional[str] = Field(default=None, alias="evidenceUrl")


class RadarEtfCandidateItem(RadarApiModel):
    industry_code: str = Field(alias="industryCode")
    index_group_key: str = Field(alias="indexGroupKey")
    rank: int = Field(ge=1)
    representative_symbol: Optional[str] = Field(
        default=None,
        alias="representativeSymbol",
    )
    alternative_symbols: List[str] = Field(
        default_factory=list,
        alias="alternativeSymbols",
    )
    industry_exposures: List[Dict[str, Any]] = Field(
        default_factory=list,
        alias="industryExposures",
    )
    ranking_components: Dict[str, Any] = Field(
        default_factory=dict,
        alias="rankingComponents",
    )
    entry_reasons: List[str] = Field(default_factory=list, alias="entryReasons")
    risk_reasons: List[str] = Field(default_factory=list, alias="riskReasons")
    exit_conditions: List[str] = Field(default_factory=list, alias="exitConditions")
    formal_usable: bool = Field(alias="formalUsable")


class RadarEtfModule(RadarApiModel):
    state: RadarModuleState
    quality: RadarModuleQuality
    using_last_success: bool = Field(alias="usingLastSuccess")
    last_attempt: Optional[RadarEtfAttempt] = Field(
        default=None,
        alias="lastAttempt",
    )
    last_success: Optional[RadarEtfSnapshot] = Field(
        default=None,
        alias="lastSuccess",
    )
    freshness: RadarFreshness
    sources: List[RadarSourceStatus] = Field(default_factory=list)
    summary: RadarEtfSummary
    products: List[RadarEtfProductItem] = Field(default_factory=list)
    candidates: List[RadarEtfCandidateItem] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarLeaderSnapshot(RadarApiModel):
    radar_run_id: str = Field(alias="radarRunId")
    as_of: datetime = Field(alias="asOf")
    created_at: datetime = Field(alias="createdAt")
    rule_version: str = Field(alias="ruleVersion")


class RadarLeaderSummary(RadarApiModel):
    eligible_count: int = Field(alias="eligibleCount", ge=0)
    preliminary_count: int = Field(alias="preliminaryCount", ge=0)
    candidate_count: int = Field(alias="candidateCount", ge=0)
    confirmed_count: int = Field(alias="confirmedCount", ge=0)
    removed_count: int = Field(alias="removedCount", ge=0)
    overflow_counts: Dict[str, int] = Field(
        default_factory=dict,
        alias="overflowCounts",
    )
    coverage: float = Field(ge=0, le=1)
    formal_usable_count: int = Field(alias="formalUsableCount", ge=0)
    rule_version: Optional[str] = Field(default=None, alias="ruleVersion")
    formal_state_enabled: Literal[False] = Field(
        default=False,
        alias="formalStateEnabled",
    )
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarLeaderItem(RadarApiModel):
    symbol: str
    name: str
    industry_code: Optional[str] = Field(default=None, alias="industryCode")
    industry_name: Optional[str] = Field(default=None, alias="industryName")
    state: Literal["preliminary", "candidate", "confirmed"]
    score: float = Field(ge=0, le=100)
    business_exposure_status: str = Field(alias="businessExposureStatus")
    data_status: str = Field(alias="dataStatus")
    first_rejection_reason: Optional[str] = Field(
        default=None,
        alias="firstRejectionReason",
    )
    reasons: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    invalidation: Dict[str, Any] = Field(default_factory=dict)
    state_age_periods: int = Field(alias="stateAgePeriods", ge=0)
    formal_usable: Literal[False] = Field(alias="formalUsable")


class RadarLeaderReviewQueue(RadarApiModel):
    status: Literal["not_ready", "ready", "failed"] = "not_ready"
    review_batch_id: Optional[str] = Field(
        default=None,
        alias="reviewBatchId",
    )
    candidate_plan_id: Optional[str] = Field(
        default=None,
        alias="candidatePlanId",
    )
    as_of: Optional[datetime] = Field(default=None, alias="asOf")
    window_from: Optional[date] = Field(default=None, alias="windowFrom")
    window_until: Optional[date] = Field(default=None, alias="windowUntil")
    candidate_count: int = Field(default=0, alias="candidateCount", ge=0)
    document_count: int = Field(default=0, alias="documentCount", ge=0)
    document_link_count: int = Field(
        default=0,
        alias="documentLinkCount",
        ge=0,
    )
    content_snapshot_count: int = Field(
        default=0,
        alias="contentSnapshotCount",
        ge=0,
    )
    reviewed_document_count: int = Field(
        default=0,
        alias="reviewedDocumentCount",
        ge=0,
    )
    review_version_count: int = Field(
        default=0,
        alias="reviewVersionCount",
        ge=0,
    )
    query_categories_complete: bool = Field(
        default=False,
        alias="queryCategoriesComplete",
    )
    query_pages_complete: bool = Field(
        default=False,
        alias="queryPagesComplete",
    )
    query_window_continuous: bool = Field(
        default=False,
        alias="queryWindowContinuous",
    )
    reason_codes: List[str] = Field(
        default_factory=lambda: ["d2_review_batch_missing"],
        alias="reasonCodes",
    )
    formal_usable: Literal[False] = Field(
        default=False,
        alias="formalUsable",
    )


class RadarLeaderReviewDocument(RadarApiModel):
    document_id: str = Field(alias="documentId")
    symbol: str
    issuer_name: str = Field(alias="issuerName")
    title: str
    published_at: datetime = Field(alias="publishedAt")
    source_name: str = Field(alias="sourceName")
    source_url: str = Field(alias="sourceUrl")
    candidate_category: str = Field(alias="candidateCategory")
    has_content_snapshot: bool = Field(alias="hasContentSnapshot")
    content_snapshot_count: int = Field(
        default=0,
        alias="contentSnapshotCount",
        ge=0,
    )
    content_status: Literal["not_fetched", "available"] = Field(
        default="not_fetched",
        alias="contentStatus",
    )
    content_fetched_at: Optional[datetime] = Field(
        default=None,
        alias="contentFetchedAt",
    )
    review_version_count: int = Field(alias="reviewVersionCount", ge=0)
    formal_usable: Literal[False] = Field(
        default=False,
        alias="formalUsable",
    )


class RadarLeaderModule(RadarApiModel):
    state: RadarModuleState
    quality: RadarModuleQuality
    using_last_success: bool = Field(alias="usingLastSuccess")
    last_attempt: Optional[RadarLastAttempt] = Field(
        default=None,
        alias="lastAttempt",
    )
    last_success: Optional[RadarLeaderSnapshot] = Field(
        default=None,
        alias="lastSuccess",
    )
    freshness: RadarFreshness
    sources: List[RadarSourceStatus] = Field(default_factory=list)
    summary: RadarLeaderSummary
    review_queue: RadarLeaderReviewQueue = Field(
        default_factory=RadarLeaderReviewQueue,
        alias="reviewQueue",
    )
    preliminary: List[RadarLeaderItem] = Field(default_factory=list)
    candidates: List[RadarLeaderItem] = Field(default_factory=list)
    confirmed: List[RadarLeaderItem] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarModuleCollection(RadarApiModel):
    market: RadarMarketModule
    sectors: RadarSectorModule
    etf: RadarEtfModule
    leaders: Union[RadarDeferredModule, RadarLeaderModule]
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


class RadarEtfsResponse(RadarApiModel):
    schema_version: Literal["radar-etfs-v1"] = Field(
        default="radar-etfs-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    module: RadarEtfModule


class RadarLeadersResponse(RadarApiModel):
    schema_version: Literal["radar-leaders-v1"] = Field(
        default="radar-leaders-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    module: RadarLeaderModule


class RadarStockResponse(RadarApiModel):
    schema_version: Literal["radar-stock-v1"] = Field(
        default="radar-stock-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    symbol: str
    status: Literal[
        "matched",
        "not_listed",
        "no_snapshot",
        "stale",
        "failed",
        "not_enabled",
    ]
    snapshot: Optional[RadarLeaderSnapshot] = None
    freshness: RadarFreshness
    leader: Optional[RadarLeaderItem] = None
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")


class RadarLeaderReviewQueueResponse(RadarApiModel):
    schema_version: Literal["radar-leader-review-queue-v1"] = Field(
        default="radar-leader-review-queue-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    summary: RadarLeaderReviewQueue
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    items: List[RadarLeaderReviewDocument] = Field(default_factory=list)


class RadarLeaderReviewDocumentResponse(RadarApiModel):
    schema_version: Literal["radar-leader-review-document-v1"] = Field(
        default="radar-leader-review-document-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    summary: RadarLeaderReviewQueue
    item: RadarLeaderReviewDocument


class RadarLeaderReviewPage(RadarApiModel):
    page_number: int = Field(alias="pageNumber", ge=1)
    text: str


class RadarLeaderReviewCandidate(RadarApiModel):
    candidate_id: str = Field(alias="candidateId")
    candidate_kind: Literal[
        "fact_extraction_missing",
        "relation_review_required",
    ] = Field(alias="candidateKind")
    auto_fact_count: int = Field(alias="autoFactCount", ge=0)
    required_fact_kinds: List[str] = Field(
        default_factory=list,
        alias="requiredFactKinds",
    )
    reason_codes: List[str] = Field(
        default_factory=list,
        alias="reasonCodes",
    )


class RadarLeaderReviewReplayDiagnostic(RadarApiModel):
    status: Literal[
        "ready",
        "missing",
        "source_unverified",
        "stale",
        "source_failed",
    ]
    review_version_count: int = Field(alias="reviewVersionCount", ge=0)
    bundle_count: int = Field(alias="bundleCount", ge=0)
    active_review_version: Optional[str] = Field(
        default=None,
        alias="activeReviewVersion",
    )
    material_change_required: bool = Field(alias="materialChangeRequired")
    reason_codes: List[str] = Field(
        default_factory=list,
        alias="reasonCodes",
    )
    formal_usable: Literal[False] = Field(
        default=False,
        alias="formalUsable",
    )


class RadarLeaderReviewFormResponse(RadarApiModel):
    schema_version: Literal["radar-leader-review-form-v1"] = Field(
        default="radar-leader-review-form-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    mode: Literal["shadow", "disabled"]
    market_session: RadarMarketSession = Field(alias="marketSession")
    summary: RadarLeaderReviewQueue
    item: RadarLeaderReviewDocument
    content_sha256: str = Field(alias="contentSha256")
    content_fetched_at: datetime = Field(alias="contentFetchedAt")
    page_count: int = Field(alias="pageCount", ge=1)
    pages: List[RadarLeaderReviewPage]
    candidate: RadarLeaderReviewCandidate
    replay_diagnostic: RadarLeaderReviewReplayDiagnostic = Field(
        alias="replayDiagnostic"
    )
    next_review_version: str = Field(alias="nextReviewVersion")
    supersedes_review_version: Optional[str] = Field(
        default=None,
        alias="supersedesReviewVersion",
    )
    write_enabled: bool = Field(alias="writeEnabled")
    write_reason_code: str = Field(alias="writeReasonCode")
    formal_usable: Literal[False] = Field(
        default=False,
        alias="formalUsable",
    )


class RadarLeaderManualFactRequest(RadarApiModel):
    fact_kind: Literal[
        "case_id",
        "reporting_period",
        "audit_report_id",
        "referenced_document_id",
        "effective_date",
        "effective_interval",
    ] = Field(alias="factKind")
    source_value: str = Field(alias="sourceValue", min_length=1, max_length=200)
    page_number: int = Field(alias="pageNumber", ge=1, le=200)
    source_fragment: str = Field(
        alias="sourceFragment",
        min_length=1,
        max_length=500,
    )
    mapped_document_id: Optional[str] = Field(
        default=None,
        alias="mappedDocumentId",
        max_length=160,
    )


class RadarLeaderTargetEventRequest(RadarApiModel):
    event_version: str = Field(alias="eventVersion", min_length=1, max_length=80)
    event_subtype: str = Field(alias="eventSubtype", min_length=1)
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=500)
    document_id: str = Field(alias="documentId", min_length=1, max_length=160)
    published_at: datetime = Field(alias="publishedAt")
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_until: Optional[datetime] = Field(
        default=None,
        alias="effectiveUntil",
    )
    fact_summary: str = Field(alias="factSummary", min_length=2, max_length=300)
    official_status: Literal["active", "completed", "withdrawn"] = Field(
        alias="officialStatus",
    )


class RadarLeaderReviewVersionRequest(RadarApiModel):
    review_batch_id: str = Field(alias="reviewBatchId", min_length=1)
    document_id: str = Field(alias="documentId", min_length=1)
    candidate_category: str = Field(alias="candidateCategory", min_length=1)
    content_sha256: str = Field(alias="contentSha256", min_length=64, max_length=64)
    candidate_id: str = Field(alias="candidateId", min_length=1, max_length=160)
    reviewer_key: str = Field(alias="reviewerKey", min_length=1, max_length=160)
    effective_until: Optional[datetime] = Field(
        default=None,
        alias="effectiveUntil",
    )
    fact_supplements: List[RadarLeaderManualFactRequest] = Field(
        alias="factSupplements",
        min_length=1,
        max_length=12,
    )
    target_event: RadarLeaderTargetEventRequest = Field(alias="targetEvent")
    relation_kind: Literal["resolves", "supersedes"] = Field(alias="relationKind")
    replacement_event_version: Optional[str] = Field(
        default=None,
        alias="replacementEventVersion",
        max_length=80,
    )
    decision_summary: str = Field(
        alias="decisionSummary",
        min_length=4,
        max_length=300,
    )
    confirm_official_evidence: Literal[True] = Field(
        alias="confirmOfficialEvidence",
    )


class RadarLeaderReviewVersionPreflightResponse(RadarApiModel):
    schema_version: Literal[
        "radar-leader-review-version-preflight-v1"
    ] = Field(
        default="radar-leader-review-version-preflight-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    status: Literal[
        "ready",
        "missing",
        "source_unverified",
        "stale",
        "source_failed",
    ]
    review_batch_id: str = Field(alias="reviewBatchId")
    document_id: str = Field(alias="documentId")
    candidate_category: str = Field(alias="candidateCategory")
    proposed_review_version: str = Field(alias="proposedReviewVersion")
    supersedes_review_version: Optional[str] = Field(
        default=None,
        alias="supersedesReviewVersion",
    )
    existing_review_version_count: int = Field(
        alias="existingReviewVersionCount",
        ge=0,
    )
    proposed_review_version_count: int = Field(
        alias="proposedReviewVersionCount",
        ge=1,
    )
    material_change_present: bool = Field(alias="materialChangePresent")
    change_kinds: List[str] = Field(default_factory=list, alias="changeKinds")
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")
    write_enabled: bool = Field(alias="writeEnabled")
    submission_allowed: bool = Field(alias="submissionAllowed")
    formal_usable: Literal[False] = Field(default=False, alias="formalUsable")
    state_transition_allowed: Literal[False] = Field(
        default=False,
        alias="stateTransitionAllowed",
    )


class RadarLeaderReviewVersionResponse(RadarApiModel):
    schema_version: Literal["radar-leader-review-version-v1"] = Field(
        default="radar-leader-review-version-v1",
        alias="schemaVersion",
    )
    accepted_at: datetime = Field(alias="acceptedAt")
    created: bool
    review_batch_id: str = Field(alias="reviewBatchId")
    document_id: str = Field(alias="documentId")
    review_version: str = Field(alias="reviewVersion")
    supersedes_review_version: Optional[str] = Field(
        default=None,
        alias="supersedesReviewVersion",
    )
    review_version_count: int = Field(alias="reviewVersionCount", ge=1)
    formal_usable: Literal[False] = Field(
        default=False,
        alias="formalUsable",
    )
