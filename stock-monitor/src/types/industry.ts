export interface DynamicsItem {
  title: string;
  source: string;
  url?: string;
  impact?: '利好' | '利空' | '中性';
  desc?: string;
  time?: string;
}

export interface IndustryMonitor {
  industryName: string;
  heatScore: number | null;
  sectorChangePercent: number | null;
  fundFlow: string;
  policySummary?: string;
  upstreamStatus?: string;
  downstreamStatus?: string;
  updateTime?: string;
  refreshInterval?: string;
  policies?: DynamicsItem[];
  upstreamDownstream?: DynamicsItem[];
  allNews?: DynamicsItem[];
}
