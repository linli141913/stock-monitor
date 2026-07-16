export interface DynamicsItem {
  title: string;
  source: string;
  url?: string;
  impact?: '利好' | '利空' | '中性';
  evidenceLevel?: 'S' | 'A' | 'B' | 'C';
  verificationStatus?: '来源已核验' | '多源印证' | '单一来源' | '线索级，待核实';
  direction?: 'positive' | 'negative' | 'neutral' | 'uncertain';
  priority?: 'P1' | 'P2' | 'P3';
  desc?: string;
  time?: string;
  timePrecision?: 'date' | 'datetime' | 'unknown';
  discoveredAt?: string | null;
  categoryKey?: 'company-announcements' | 'industry-policy' | 'industry-dynamics' | 'overseas-controls';
}

export interface LinkageDimensionState {
  status: 'triggered' | 'no_signal' | 'unavailable';
  label: '已触发' | '未触发' | '暂无判断';
  reason: string;
  details?: {
    changePercent?: number;
    triggerThreshold?: number;
    advancers?: number;
    total?: number;
    ratioPercent?: number;
    triggerThresholdPercent?: number;
    selectionMethod?: string;
    leaders?: Array<{
      rank?: number;
      symbol?: string;
      name?: string;
      market_cap?: number;
      change_percent?: number;
      is_limit_down?: boolean;
    }>;
    direction?: 'inflow' | 'outflow';
    value?: number | null;
    rank?: number;
    triggerRank?: number;
    source?: string;
  };
}

export interface LinkageRuleState extends LinkageDimensionState {
  dataComplete?: boolean;
  scopeNote?: string | null;
  dimensions?: {
    decline: LinkageDimensionState;
    breadth: LinkageDimensionState;
    leader: LinkageDimensionState;
    fundFlow: LinkageDimensionState;
  };
}

export interface LinkageRisk {
  riskStatus: 'normal' | 'watch' | 'warning' | 'unavailable';
  priority: 'P2' | 'P3' | null;
  direction: 'positive' | 'negative' | 'neutral';
  reason: string;
  dataComplete: boolean;
  sectorRisk: LinkageRuleState;
  overseasRisk: LinkageRuleState;
  signals: Array<{ code: string; label: string }>;
}

export interface IndustryMonitor {
  industryName: string;
  heatScore: number | null;
  heatScoreMethod?: 'calculated' | 'unavailable';
  heatScoreExplanation?: string | null;
  sectorChangePercent: number | null;
  fundFlow: string;
  fundFlowTimeScope?: string;
  fundFlowSource?: string | null;
  industryDataFetchedAt?: string | null;
  industryDataStatus?: 'available' | 'unavailable' | 'not_applicable';
  industryDataError?: string | null;
  linkageRisk?: LinkageRisk;
  policySummary?: string;
  upstreamStatus?: string;
  downstreamStatus?: string;
  fetchedAt?: string;
  updateTime?: string;
  refreshInterval?: string;
  policies?: DynamicsItem[];
  upstreamDownstream?: DynamicsItem[];
  allNews?: DynamicsItem[];
}
