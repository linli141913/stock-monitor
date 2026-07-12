export interface StockOverview {
  stockName: string;
  stockCode: string;
  marketStatus?: string;
  marketStatusCode?: "pre_open" | "trading" | "lunch_break" | "closed" | "holiday" | "unknown";
  latestPrice: number | null;
  changeAmount: number | null;
  changePercent: number | null;
  openPrice: number | null;
  highPrice: number | null;
  lowPrice: number | null;
  previousClose: number | null;
  volume: string | null;
  turnoverAmount: string | null;
  turnoverRate: number | null;
  peDynamic?: number | null;
  marketCap?: string | null;
  sourceTime: string | null;
  fetchedAt: string;
  dataSource?: string;
  dataType?: "实时" | "准实时" | "延迟";
  refreshInterval?: string;
  delayNote?: string;
  industry?: string;
  concepts?: string[];
  fundFlow?: string;
}

export interface KlineItem {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
}

export interface RelatedStock {
  stockName: string;
  stockCode: string;
  latestPrice: number | null;
  changePercent: number | null;
  industryRelation?: string;
  abnormalTag?: string;
  updateTime?: string;
  fundFlow?: string | null;
}

export interface AbnormalStock {
  stockName: string;
  stockCode: string;
  oneDayChange: number;
  fiveDayChange?: number | null;
  twentyDayChange?: number | null;
  volumeRatio?: number | null;
  turnoverRate?: number | null;
  fundFlow?: string | null;
  reason?: string | null;
  riskNote?: string | null;
  updateTime?: string | null;
}

export interface CompanyInfo {
  mainBusiness: string;
  coreProducts: string[];
  industryTags: string[];
  companyDescription: string;
  businessRelation: string;
  updateTime: string;
}

export interface FinancialSummary {
  reportPeriod: string;
  revenue: string;
  revenueYoy: string;
  netProfit: string;
  netProfitYoy: string;
  grossMargin: string;
  netMargin: string;
  roe: string;
  eps: string;
  debtRatio: string;
  updateTime: string;
}

export interface Announcement {
  id: string;
  title: string;
  publishTime: string;
  source: string;
  summary: string;
  url: string;
  importance: "高" | "中" | "低";
}

export interface News {
  id: string;
  title: string;
  source: string;
  publishTime: string;
  summary: string;
  sentiment: "利好" | "利空" | "中性" | "未分析";
  url: string;
}
