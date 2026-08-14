from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


RadarAiScopeType = Literal["market", "sector", "etf", "leader"]
RadarAiAnalysisStatus = Literal[
    "success",
    "not_run",
    "not_configured",
    "evidence_insufficient",
    "failed",
]
RadarAiHealthStatus = Literal[
    "disabled",
    "not_configured",
    "storage_not_ready",
    "ready",
]


class RadarAiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RadarAiSource(RadarAiModel):
    source_id: str = Field(alias="sourceId", min_length=1)
    source: str = Field(min_length=1)
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    status: str = Field(min_length=1)
    source_url: Optional[str] = Field(default=None, alias="sourceUrl")


class RadarAiEvidence(RadarAiModel):
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    statement: str = Field(min_length=1)
    source_ids: List[str] = Field(alias="sourceIds", min_length=1)
    fact_kind: Literal["verified", "calculated", "reviewed", "unconfirmed"] = Field(
        alias="factKind"
    )


class RadarAiCompleteness(RadarAiModel):
    minimum_coverage: float = Field(alias="minimumCoverage", ge=0, le=1)
    is_complete: bool = Field(alias="isComplete")
    missing_fields: List[str] = Field(default_factory=list, alias="missingFields")


class FrozenRadarEvidencePackage(RadarAiModel):
    radar_run_id: str = Field(alias="radarRunId", min_length=1)
    batch_id: str = Field(alias="batchId", min_length=1)
    as_of: datetime = Field(alias="asOf")
    rule_version: str = Field(alias="ruleVersion", min_length=1)
    coverage: float = Field(ge=0, le=1)
    scope_type: RadarAiScopeType = Field(alias="scopeType")
    scope_id: str = Field(alias="scopeId", min_length=1)
    formal_state: str = Field(alias="formalState", min_length=1)
    formal_score_breakdown: Dict[str, Any] = Field(
        default_factory=dict,
        alias="formalScoreBreakdown",
    )
    state_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        alias="stateHistory",
    )
    evidence: List[RadarAiEvidence] = Field(default_factory=list)
    counter_evidence: List[RadarAiEvidence] = Field(
        default_factory=list,
        alias="counterEvidence",
    )
    first_rejection_reason: Optional[str] = Field(
        default=None,
        alias="firstRejectionReason",
    )
    unknowns: List[str] = Field(default_factory=list)
    source_catalog: List[RadarAiSource] = Field(alias="sourceCatalog", min_length=1)
    data_completeness: RadarAiCompleteness = Field(alias="dataCompleteness")
    formal_state_enabled: bool = Field(alias="formalStateEnabled")


class RadarAiConfirmedFact(RadarAiModel):
    text: str = Field(min_length=1)
    source_ids: List[str] = Field(alias="sourceIds", min_length=1)


class RadarAiStructuredOutput(RadarAiModel):
    analysis_status: Literal["success"] = Field(alias="analysisStatus")
    confirmed_facts: List[RadarAiConfirmedFact] = Field(
        default_factory=list,
        alias="confirmedFacts",
    )
    inferences: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    counter_evidence: List[str] = Field(
        default_factory=list,
        alias="counterEvidence",
    )
    conditional_scenarios: List[str] = Field(
        default_factory=list,
        alias="conditionalScenarios",
    )
    plain_english_summary: str = Field(alias="plainEnglishSummary", min_length=1)
    source_ids: List[str] = Field(default_factory=list, alias="sourceIds")
    invalidating_conditions: List[str] = Field(
        default_factory=list,
        alias="invalidatingConditions",
    )


class RadarAiUsageResponse(RadarAiModel):
    prompt_tokens: int = Field(default=0, alias="promptTokens", ge=0)
    completion_tokens: int = Field(default=0, alias="completionTokens", ge=0)
    total_tokens: int = Field(default=0, alias="totalTokens", ge=0)


class RadarAiResponse(RadarAiModel):
    schema_version: Literal["radar-ai-v1"] = Field(
        default="radar-ai-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    radar_run_id: Optional[str] = Field(default=None, alias="radarRunId")
    as_of: Optional[datetime] = Field(default=None, alias="asOf")
    scope_type: RadarAiScopeType = Field(alias="scopeType")
    scope_id: str = Field(alias="scopeId")
    analysis_status: RadarAiAnalysisStatus = Field(alias="analysisStatus")
    evidence_fingerprint: Optional[str] = Field(
        default=None,
        alias="evidenceFingerprint",
    )
    prompt_version: str = Field(alias="promptVersion")
    model: Optional[str] = None
    analysis_at: Optional[datetime] = Field(default=None, alias="analysisAt")
    duration_ms: int = Field(default=0, alias="durationMs", ge=0)
    usage: RadarAiUsageResponse = Field(default_factory=RadarAiUsageResponse)
    output: Optional[RadarAiStructuredOutput] = None
    error_category: Optional[str] = Field(default=None, alias="errorCategory")
    reused: bool = False


class RadarAiHistoryResponse(RadarAiModel):
    schema_version: Literal["radar-ai-history-v1"] = Field(
        default="radar-ai-history-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    scope_type: RadarAiScopeType = Field(alias="scopeType")
    scope_id: str = Field(alias="scopeId")
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    items: List[RadarAiResponse] = Field(default_factory=list)


class RadarAiHealthResponse(RadarAiModel):
    schema_version: Literal["radar-ai-health-v1"] = Field(
        default="radar-ai-health-v1",
        alias="schemaVersion",
    )
    checked_at: datetime = Field(alias="checkedAt")
    status: RadarAiHealthStatus
    enabled: bool
    manual_enabled: bool = Field(alias="manualEnabled")
    configured: bool
    storage_ready: bool = Field(alias="storageReady")
    daily_call_limit: int = Field(alias="dailyCallLimit", ge=1)
    daily_token_limit: int = Field(alias="dailyTokenLimit", ge=1)


class RadarAiAnalyzeRequest(RadarAiModel):
    scope_type: RadarAiScopeType = Field(alias="scopeType")
    scope_id: str = Field(alias="scopeId", min_length=1, max_length=160)


class RadarNotificationPreferences(RadarAiModel):
    site_enabled: bool = Field(default=True, alias="siteEnabled")
    email_enabled: bool = Field(default=False, alias="emailEnabled")
    p2_email: bool = Field(default=True, alias="p2Email")
    p3_email: bool = Field(default=False, alias="p3Email")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")


class RadarNotificationPreferencesResponse(RadarAiModel):
    data: RadarNotificationPreferences
