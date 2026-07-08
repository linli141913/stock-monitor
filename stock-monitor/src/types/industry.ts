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
}
