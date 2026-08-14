export type RadarAiScopeType = 'market' | 'sector' | 'etf' | 'leader';

export type RadarAiAnalysisStatus =
  | 'success'
  | 'not_run'
  | 'not_configured'
  | 'evidence_insufficient'
  | 'failed';

export interface RadarAiConfirmedFact {
  text: string;
  sourceIds: string[];
}

export interface RadarAiStructuredOutput {
  analysisStatus: 'success';
  confirmedFacts: RadarAiConfirmedFact[];
  inferences: string[];
  unknowns: string[];
  counterEvidence: string[];
  conditionalScenarios: string[];
  plainEnglishSummary: string;
  sourceIds: string[];
  invalidatingConditions: string[];
}

export interface RadarAiResponse {
  schemaVersion: 'radar-ai-v1';
  checkedAt: string;
  radarRunId: string | null;
  asOf: string | null;
  scopeType: RadarAiScopeType;
  scopeId: string;
  analysisStatus: RadarAiAnalysisStatus;
  evidenceFingerprint: string | null;
  promptVersion: string;
  model: string | null;
  analysisAt: string | null;
  durationMs: number;
  usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
  output: RadarAiStructuredOutput | null;
  errorCategory: string | null;
  reused: boolean;
}

export interface RadarAiHealthResponse {
  schemaVersion: 'radar-ai-health-v1';
  checkedAt: string;
  status: 'disabled' | 'not_configured' | 'storage_not_ready' | 'ready';
  enabled: boolean;
  manualEnabled: boolean;
  configured: boolean;
  storageReady: boolean;
  dailyCallLimit: number;
  dailyTokenLimit: number;
}

export interface RadarNotificationPreferences {
  siteEnabled: boolean;
  emailEnabled: boolean;
  p2Email: boolean;
  p3Email: boolean;
  updatedAt: string | null;
}
