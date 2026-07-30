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

export interface RadarLeaderModule {
  state: RadarModuleState;
  quality: RadarModuleQuality;
  usingLastSuccess: boolean;
  lastAttempt: RadarLastAttempt | null;
  lastSuccess: RadarLeaderSnapshot | null;
  freshness: RadarFreshness;
  sources: RadarSourceStatus[];
  summary: RadarLeaderSummary;
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
