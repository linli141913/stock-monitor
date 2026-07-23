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
    etf: RadarDeferredModule;
    leaders: RadarDeferredModule;
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
