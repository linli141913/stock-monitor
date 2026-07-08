export interface DynamicsItem {
  title: string;
  source: string;
  url?: string;
  impact: '利好' | '利空' | '中性';
  desc: string;
}

export interface IndustryMonitor {
  industryName: string;
  heatScore: number;
  sectorChangePercent: number;
  fundFlow: string;
  policySummary?: string;
  upstreamStatus?: string;
  downstreamStatus?: string;
  updateTime?: string;
  refreshInterval?: string;
  policies?: DynamicsItem[];
  upstreamDownstream?: DynamicsItem[];
}
