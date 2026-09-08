export type RadarModuleState =
  | 'available'
  | 'empty'
  | 'stale'
  | 'failed'
  | 'not_ready'
  | 'not_enabled';

export type RadarModuleQuality = 'complete' | 'partial' | 'unavailable';

export interface RadarFreshness {
  ageSeconds: number | null;
  staleAfterSeconds: number;
  isStale: boolean;
  reasonCodes: string[];
}

export interface RadarLastAttempt {
  radarRunId: string;
  asOf: string;
  status: string;
  shadowMode: boolean;
  ruleVersionId: string | null;
  startedAt: string;
  completedAt: string | null;
  errorCode: string | null;
}

export interface RadarLastSuccess {
  radarRunId: string;
  asOf: string;
  sourceTime: string | null;
  fetchedAt: string;
}

export interface RadarSourceStatus {
  batchId: string;
  source: string;
  asOf: string;
  sourceTime: string | null;
  fetchedAt: string;
  status: string;
  expectedCount: number | null;
  returnedCount: number;
  rowCoverage: number | null;
  requiredFieldCoverage: Record<string, number>;
  reasonCodes: string[];
}

export interface RadarCompleteness {
  expectedCount: number;
  returnedCount: number;
  validCount: number;
  rowCoverage: number;
  requiredFieldCoverage: Record<string, number>;
  isComplete: boolean;
  reasons: string[];
}

export interface RadarMarketData {
  radarRunId: string;
  asOf: string;
  sourceTime: string | null;
  fetchedAt: string;
  formalStateEnabled: boolean;
  indexCompleteness: RadarCompleteness;
  breadth: {
    advancers: number;
    decliners: number;
    flat: number;
    unavailable: number;
    completeness: RadarCompleteness;
  };
  turnover: {
    contributingCount: number;
    unitStatus: 'verified' | 'unverified';
    displayAllowed: boolean;
    completeness: RadarCompleteness;
    reasons: string[];
  };
  excludedEtfCount: number;
  duplicateSymbolCount: number;
  unknownSymbolCount: number;
  indices: Array<{
    indexKey: string;
    symbol: string;
    name: string;
    exchange: string;
    sourceSymbol: string;
    sourceTime: string | null;
    fetchedAt: string;
    price: number | null;
    changePercent: number | null;
    source: string;
    missingFields: string[];
  }>;
}

export interface RadarSectorItem {
  divisionCode: string;
  divisionName: string;
  categoryCode: string;
  categoryName: string;
  asOf: string;
  sourceTime: string | null;
  fetchedAt: string;
  classificationMappingCoverage: number;
  mappedConstituentCount: number;
  unconfirmedStockCount: number;
  expectedCount: number;
  returnedCount: number;
  freshCount: number;
  rowCoverage: number;
  isComplete: boolean;
  equalReturn: number | null;
  advancers: number;
  decliners: number;
  flat: number;
  unavailable: number;
  upRatio: number | null;
  shadowUsable: boolean;
  reasons: string[];
}

export interface RadarMarketModule {
  state: RadarModuleState;
  quality: RadarModuleQuality;
  usingLastSuccess: boolean;
  lastAttempt: RadarLastAttempt | null;
  lastSuccess: RadarLastSuccess | null;
  freshness: RadarFreshness;
  sources: RadarSourceStatus[];
  data: RadarMarketData | null;
}

export interface RadarSectorModule {
  state: RadarModuleState;
  quality: RadarModuleQuality;
  usingLastSuccess: boolean;
  lastAttempt: RadarLastAttempt | null;
  lastSuccess: RadarLastSuccess | null;
  freshness: RadarFreshness;
  sources: RadarSourceStatus[];
  summary: {
    totalCount: number;
    usableCount: number;
    unavailableCount: number;
  };
  items: RadarSectorItem[];
}

export interface RadarDeferredModule {
  state: 'not_enabled';
  quality: 'unavailable';
  enabledStage: number;
  reasonCode: 'stage_not_enabled';
  data: null;
}

export interface RadarEtfAttempt {
  productMasterRunId: string;
  asOf: string;
  source: string;
  sourceTime: string | null;
  fetchedAt: string;
  status: 'succeeded' | 'degraded' | 'failed';
  expectedCount: number | null;
  returnedCount: number;
  rowCoverage: number | null;
  requiredFieldCoverage: Record<string, number>;
  issues: Array<Record<string, unknown>>;
  insertedCount: number;
  unchangedCount: number;
}

export interface RadarEtfProductItem {
  symbol: string;
  officialName: string;
  exchange: 'sse' | 'szse';
  productType: string;
  managementStyle: string;
  assetClass: string;
  targetIndexName: string | null;
  classificationMappingVersion: string;
  classificationReasons: string[];
  sourceContractId: string;
  source: string;
  sourceTime: string | null;
  fetchedAt: string;
  versionTimeKind: 'official_effective' | 'first_observed';
  officialEffectiveFrom: string | null;
  firstObservedAt: string;
  effectiveFrom: string;
  historicalReplayReady: boolean;
  evidenceUrl: string | null;
}

export interface RadarEtfCandidateItem {
  industryCode: string;
  indexGroupKey: string;
  rank: number;
  representativeSymbol: string | null;
  alternativeSymbols: string[];
  industryExposures: Array<Record<string, unknown>>;
  rankingComponents: Record<string, unknown>;
  entryReasons: string[];
  riskReasons: string[];
  exitConditions: string[];
  formalUsable: boolean;
}

export interface RadarEtfModule {
  state: RadarModuleState;
  quality: RadarModuleQuality;
  usingLastSuccess: boolean;
  lastAttempt: RadarEtfAttempt | null;
  lastSuccess: RadarLastSuccess | null;
  freshness: RadarFreshness;
  sources: RadarSourceStatus[];
  summary: {
    productCount: number;
    eligibleProductCount: number;
    candidateGroupCount: number;
    computedCount: number;
    staleCount: number;
    missingCount: number;
    coverage: number;
    formalUsableCount: number;
    ruleVersionId: string | null;
    formalStateEnabled: false;
    reasonCodes: string[];
  };
  products: RadarEtfProductItem[];
  candidates: RadarEtfCandidateItem[];
  reasonCodes: string[];
}

export interface RadarLeaderSnapshot {
  radarRunId: string;
  asOf: string;
  createdAt: string;
  ruleVersion: string;
}

export interface RadarLeaderSummary {
  eligibleCount: number;
  preliminaryCount: number;
  candidateCount: number;
  confirmedCount: number;
  removedCount: number;
  overflowCounts: Record<string, number>;
  coverage: number;
  formalUsableCount: number;
  ruleVersion: string | null;
  formalStateEnabled: false;
  reasonCodes: string[];
}

export interface RadarLeaderItem {
  symbol: string;
  name: string;
  industryCode: string | null;
  industryName: string | null;
  state: 'preliminary' | 'candidate' | 'confirmed';
  score: number;
  businessExposureStatus: string;
  dataStatus: string;
  firstRejectionReason: string | null;
  reasons: string[];
  evidence: Record<string, unknown>;
  invalidation: Record<string, unknown>;
  stateAgePeriods: number;
  formalUsable: false;
}

export interface RadarLeaderObservationItem {
  symbol: string;
  name: string;
  industryCode: string;
  industryName: string;
  withinIndustryRank: number;
  price: number;
  changePercent: number;
  sourceTime: string;
  quoteSourceContractId: string;
  sectorSourceContractId: string;
}

export interface RadarLeaderObservation {
  status: RadarModuleState;
  quality: RadarModuleQuality;
  displayAllowed: boolean;
  radarRunId: string | null;
  candidatePlanId: string | null;
  asOf: string | null;
  publishedAt: string | null;
  scannedCount: number;
  mappedCount: number;
  candidateCount: number;
  coverageScope: string;
  humanApprovalRequired: false;
  formalUsable: false;
  stateTransitionAllowed: false;
  freshness: RadarFreshness;
  items: RadarLeaderObservationItem[];
  reasonCodes: string[];
}

export interface RadarLeaderSourceSummaryItem {
  domain: 'quote' | 'sector' | 'business' | 'announcement';
  status: 'available' | 'partial' | 'missing' | 'stale' | 'failed' | 'unverified';
  scope: 'current_observation_candidates' | 'current_public_tiers';
  sourceContractIds: string[];
  asOf: string | null;
  sourceTime: string | null;
  fetchedAt: string | null;
  coveredCount: number;
  expectedCount: number | null;
  reasonCodes: string[];
}

export interface RadarLeaderReviewQueue {
  status: 'not_ready' | 'ready' | 'failed';
  purpose: 'official_announcement_scan';
  humanApprovalRequired: false;
  semanticCoverageStatus: 'partial' | 'unavailable';
  coverageStatement: string;
  reviewBatchId: string | null;
  candidatePlanId: string | null;
  asOf: string | null;
  windowFrom: string | null;
  windowUntil: string | null;
  candidateCount: number;
  documentCount: number;
  documentLinkCount: number;
  contentSnapshotCount: number;
  reviewedDocumentCount: number;
  reviewVersionCount: number;
  queryCategoriesComplete: boolean;
  queryPagesComplete: boolean;
  queryWindowContinuous: boolean;
  reasonCodes: string[];
  formalUsable: false;
}

export interface RadarLeaderReviewDocument {
  documentId: string;
  symbol: string;
  issuerName: string;
  title: string;
  publishedAt: string;
  sourceName: string;
  sourceUrl: string;
  candidateCategory: string;
  hasContentSnapshot: boolean;
  contentSnapshotCount: number;
  contentStatus: 'not_fetched' | 'available';
  contentFetchedAt: string | null;
  reviewVersionCount: number;
  formalUsable: false;
}

export interface RadarLeaderReviewQueueResponse {
  schemaVersion: 'radar-leader-review-queue-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  summary: RadarLeaderReviewQueue;
  total: number;
  limit: number;
  offset: number;
  items: RadarLeaderReviewDocument[];
}

export interface RadarLeaderReviewDocumentResponse {
  schemaVersion: 'radar-leader-review-document-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  summary: RadarLeaderReviewQueue;
  item: RadarLeaderReviewDocument;
}

export interface RadarLeaderReviewPage {
  pageNumber: number;
  text: string;
}

export interface RadarLeaderReviewFormResponse {
  schemaVersion: 'radar-leader-review-form-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  summary: RadarLeaderReviewQueue;
  item: RadarLeaderReviewDocument;
  contentSha256: string;
  contentFetchedAt: string;
  pageCount: number;
  pages: RadarLeaderReviewPage[];
  candidate: {
    candidateId: string;
    candidateKind: 'fact_extraction_missing' | 'relation_review_required';
    autoFactCount: number;
    requiredFactKinds: string[];
    reasonCodes: string[];
  };
  replayDiagnostic: {
    status: 'ready' | 'missing' | 'source_unverified' | 'stale' | 'source_failed';
    reviewVersionCount: number;
    bundleCount: number;
    activeReviewVersion: string | null;
    materialChangeRequired: boolean;
    reasonCodes: string[];
    formalUsable: false;
  };
  nextReviewVersion: string;
  supersedesReviewVersion: string | null;
  writeEnabled: boolean;
  writeReasonCode: string;
  formalUsable: false;
}

export interface RadarLeaderReviewFactDraft {
  factKind: string;
  sourceValue: string;
  pageNumber: number;
  sourceFragment: string;
}

export interface RadarLeaderReviewSubmissionDraft {
  reviewerKey: string;
  effectiveUntil: string | null;
  factSupplements: RadarLeaderReviewFactDraft[];
  targetEvent: {
    eventVersion: string;
    eventSubtype: string;
    sourceUrl: string;
    documentId: string;
    publishedAt: string;
    effectiveFrom: string;
    effectiveUntil: string;
    factSummary: string;
    officialStatus: 'active' | 'completed' | 'withdrawn';
  };
  relationKind: 'resolves' | 'supersedes';
  replacementEventVersion: string | null;
  decisionSummary: string;
  confirmOfficialEvidence: boolean;
}

export interface RadarLeaderReviewVersionPreflightResponse {
  schemaVersion: 'radar-leader-review-version-preflight-v1';
  checkedAt: string;
  status: 'ready' | 'missing' | 'source_unverified' | 'stale' | 'source_failed';
  reviewBatchId: string;
  documentId: string;
  candidateCategory: string;
  proposedReviewVersion: string;
  supersedesReviewVersion: string | null;
  existingReviewVersionCount: number;
  proposedReviewVersionCount: number;
  materialChangePresent: boolean;
  changeKinds: string[];
  reasonCodes: string[];
  writeEnabled: boolean;
  submissionAllowed: boolean;
  formalUsable: false;
  stateTransitionAllowed: false;
}

export interface RadarLeaderModule {
  state: RadarModuleState;
  quality: RadarModuleQuality;
  usingLastSuccess: boolean;
  lastAttempt: RadarLastAttempt | null;
  lastSuccess: RadarLeaderSnapshot | null;
  freshness: RadarFreshness;
  sources: RadarSourceStatus[];
  sourceSummary: RadarLeaderSourceSummaryItem[];
  summary: RadarLeaderSummary;
  observation: RadarLeaderObservation;
  reviewQueue?: RadarLeaderReviewQueue;
  preliminary: RadarLeaderItem[];
  candidates: RadarLeaderItem[];
  confirmed: RadarLeaderItem[];
  reasonCodes: string[];
}

export interface RadarMarketSession {
  code: string;
  label: string;
  calendarKind: string;
  calendarSourceUrl: string | null;
  calendarCheckedAt: string;
}

export interface RadarOverviewResponse {
  schemaVersion: 'radar-overview-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  moduleSkewSeconds: number | null;
  modules: {
    market: RadarMarketModule;
    sectors: RadarSectorModule;
    etf: RadarEtfModule;
    leaders: RadarDeferredModule | RadarLeaderModule;
    history: RadarDeferredModule;
  };
}

export interface RadarSectorsResponse {
  schemaVersion: 'radar-sectors-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  module: RadarSectorModule;
}

export interface RadarEtfsResponse {
  schemaVersion: 'radar-etfs-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  module: RadarEtfModule;
}

export interface RadarLeadersResponse {
  schemaVersion: 'radar-leaders-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  marketSession: RadarMarketSession;
  module: RadarLeaderModule;
}

export interface RadarSectorHistoryResponse {
  schemaVersion: 'radar-sector-history-v1';
  checkedAt: string;
  state: 'available' | 'not_ready' | 'failed';
  quality: 'complete' | 'unavailable';
  asOf: string | null;
  publishedAt: string | null;
  requestedCount: number;
  fetchedCount: number;
  reusedCount: number;
  failureCount: number;
  sectorCount: number;
  marketSampleCount: number;
  historyCoverageReady: boolean;
  tradingPresenceRequestedCount: number;
  tradingPresenceReturnedCount: number;
  calibrationStatus: string | null;
  observationDateCount: number;
  industryCount: number;
  marketRegimes: string[];
  trainEndDate: string | null;
  holdoutStartDate: string | null;
  metricQuantiles: Record<string, Record<string, number>>;
  metricSampleCounts: Record<string, number>;
  trainObservationDateCount: number;
  holdoutObservationDateCount: number;
  holdoutMetricSampleCounts: Record<string, number>;
  thresholdReviewState: 'review_ready' | 'approved' | 'not_ready' | 'failed';
  calibrationIdentity: string | null;
  thresholdSetId: string | null;
  approvalId: string | null;
  approvedBy: string | null;
  approvedAt: string | null;
  thresholdReviewReasonCodes: string[];
  formalApproval: boolean;
  gate: {
    formalScoreReady?: boolean;
    formalGateReady?: boolean;
    formalUsable?: boolean;
    stateTransitionAllowed?: boolean;
  };
  reasonCodes: string[];
}

export interface RadarReplayQualityResponse {
  schemaVersion: 'radar-replay-quality-v1';
  checkedAt: string;
  state: 'ready' | 'not_ready' | 'failed';
  quality: 'complete' | 'partial' | 'unavailable';
  engineeringState: 'complete' | 'not_ready' | 'failed';
  validationState: 'validated' | 'collecting' | 'not_started' | 'failed';
  shadowCollectionAllowed: boolean;
  stage9QualityGatePassed: boolean;
  replayRunId: string | null;
  createdAt: string | null;
  evidenceSha256: string | null;
  sampleCounts: {
    development: number;
    calibration: number;
    holdout: number;
  };
  includedCount: number;
  excludedCount: number;
  scopedExclusionCount: number;
  scopedExclusionCounts: Record<string, number>;
  missingCount: number;
  unverifiableCount: number;
  failedCount: number;
  futureViolationCount: number;
  duplicateStateViolationCount: number;
  multiStateViolationCount: number;
  labelCounts: {
    market: number;
    sector: number;
    etf: number;
    leader: number;
  };
  outputCounts: {
    market: number;
    sector: number;
    etf: number;
    leader: number;
  };
  etfReadinessCounts?: {
    formalAdmissionCount: number;
    monitoringReadyCount: number;
    monitoringMissingCount: number;
    rankingPolicyReadyCount: number;
    rankingPolicyMissingCount: number;
  };
  comparableLabelCount: number;
  incomparableLabelCount: number;
  disputedLabelCount: number;
  unverifiableLabelCount: number;
  unlabeledOutputTargetCount: number;
  unlabeledOutputTargetCounts: {
    market: number;
    sector: number;
    etf: number;
    leader: number;
  };
  partitionChronologyValid: boolean;
  missingDomains: string[];
  missingPartitions: Array<'development' | 'calibration' | 'holdout'>;
  missingLabelDomains: Array<'market' | 'sector' | 'etf' | 'leader'>;
  missingOutputDomains: Array<'market' | 'sector' | 'etf' | 'leader'>;
  unverifiableOutputDomains: Array<'market' | 'sector' | 'etf' | 'leader'>;
  failedOutputDomains: Array<'market' | 'sector' | 'etf' | 'leader'>;
  readyOutputDomains: Array<'market' | 'sector' | 'etf' | 'leader'>;
  missingLabelPartitions: Array<'development' | 'calibration' | 'holdout'>;
  reasonCodes: string[];
  metrics: Record<
    'market' | 'sector' | 'etf' | 'leader',
    Record<string, number> | null
  >;
}

export type RadarEtfProductResearchState =
  | 'product_ready_for_index_research'
  | 'active_product_separate_track'
  | 'out_of_scope_asset'
  | 'product_evidence_incomplete';

export interface RadarReplayEtfResearchItem {
  symbol: string;
  researchState: RadarEtfProductResearchState;
  targetIndexName: string | null;
  monitoringStatus: 'ready' | 'missing' | null;
  rankingStatus: 'ready' | 'missing' | null;
  monitoringReasons: string[];
  rankingReasons: string[];
}

export interface RadarReplayEtfResearchSnapshot {
  schemaVersion: 'radar-replay-etf-research-v1';
  replayOutputBundleId: string;
  sampleId: string;
  radarRunId: string;
  asOf: string;
  createdAt: string;
  source: string;
  sourceTime: string | null;
  fetchedAt: string;
  outputSnapshotSha256: string;
  sourceSnapshotSha256: string;
  classificationMappingVersion: string;
  productCount: number;
  indexResearchReadyCount: number;
  activeSeparateTrackCount: number;
  outOfScopeAssetCount: number;
  evidenceIncompleteCount: number;
  formalAdmissionAvailable: boolean;
  formalAdmissionCount: number;
  monitoringReadyCount: number;
  monitoringMissingCount: number;
  rankingPolicyReadyCount: number;
  rankingPolicyMissingCount: number;
  researchOnly: true;
  rankingReady: false;
  formalUsable: false;
  stateTransitionAllowed: false;
  items: RadarReplayEtfResearchItem[];
  reasonCodes: string[];
}

export interface RadarReplayEtfResearchResponse {
  schemaVersion: 'radar-replay-etf-research-v1';
  checkedAt: string;
  state: 'available' | 'not_ready' | 'failed';
  quality: 'complete' | 'partial' | 'unavailable';
  evidenceSha256: string | null;
  snapshot: RadarReplayEtfResearchSnapshot | null;
  reasonCodes: string[];
}

export type RadarFormalReadinessState =
  | 'collecting'
  | 'not_ready'
  | 'ready_to_enable'
  | 'failed'
  | 'formal_enabled';

export type RadarFormalGateReadiness =
  | 'collecting'
  | 'not_ready'
  | 'ready'
  | 'failed';

export interface RadarFormalReadinessGate {
  gate: string;
  state: RadarFormalGateReadiness;
  required: boolean;
  reasonCodes: string[];
}

export interface RadarFormalReadinessEvidence {
  evidenceType: string;
  contractVersion: string;
  contentSha256: string;
  subjectId: string;
  generatedAt: string;
  sourceTime: string;
  fetchedAt: string;
}

export interface RadarFormalFreshnessPolicy {
  policyVersion: 'radar-formal-freshness-policy-v1';
  reportMaxAgeSeconds: number;
  evidenceMaxAgeSeconds: number;
  operationalChecksMaxAgeSeconds: number;
}

export interface RadarFormalReadinessModule {
  module: 'trendRotation' | 'etfObservation' | 'leaderObservation';
  state: RadarFormalReadinessState;
  requested: boolean;
  configuredEnabled: boolean;
  formalEnabled: boolean;
  observedTradingDays: number;
  requiredTradingDays: number;
  lastObservedTradingDate: string | null;
  gates: RadarFormalReadinessGate[];
  reasonCodes: string[];
}

export interface RadarFormalReadinessResponse {
  contractVersion: 'radar-formal-readiness-v1';
  checkedAt: string;
  freshnessPolicy: RadarFormalFreshnessPolicy | null;
  state: RadarFormalReadinessState;
  anyFormalEnabled: boolean;
  allModulesFormalEnabled: boolean;
  stage9ReplayRunId: string | null;
  stage9QualitySha256: string | null;
  stage9QualityState: RadarFormalGateReadiness;
  modules: RadarFormalReadinessModule[];
  evidence: RadarFormalReadinessEvidence[];
  reasonCodes: string[];
}

export type RadarFormalShadowProgressState = 'available' | 'missing' | 'failed';

export interface RadarFormalShadowProgressModule {
  module: RadarFormalReadinessModule['module'];
  observedTradingDays: number;
  requiredTradingDays: number;
  latestReadyStreak: number;
  latestReadyTradingDate: string | null;
}

export interface RadarFormalShadowProgressResponse {
  contractVersion: 'radar-formal-shadow-progress-v1';
  checkedAt: string;
  state: RadarFormalShadowProgressState;
  modules: RadarFormalShadowProgressModule[];
  ledgerSha256: string | null;
  reasonCodes: string[];
}

export interface RadarStockResponse {
  schemaVersion: 'radar-stock-v1';
  checkedAt: string;
  mode: 'shadow' | 'disabled';
  symbol: string;
  status: 'matched' | 'observed' | 'not_listed' | 'no_snapshot' | 'stale' | 'failed' | 'not_enabled';
  snapshot: RadarLeaderSnapshot | null;
  freshness: RadarFreshness;
  leader: RadarLeaderItem | null;
  observationItem: RadarLeaderObservationItem | null;
  reasonCodes: string[];
}
