export interface StockOverview {
  stockName: string;
  stockCode: string;
  marketStatus?: string; // e.g. "交易中" or "已休市"
  latestPrice: number;
  changeAmount: number;
  changePercent: number;
  openPrice: number;
  highPrice: number;
  lowPrice: number;
  previousClose: number;
  volume: string;
  turnoverAmount: string;
  turnoverRate: number;
  peDynamic?: number;
  marketCap?: string;
  updateTime: string;
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
  latestPrice: number;
  changePercent: number;
  industryRelation?: string;
  abnormalTag?: string;
  updateTime?: string;
  fundFlow?: string;
}

export interface AbnormalStock {
  stockName: string;
  stockCode: string;
  oneDayChange: number;
  fiveDayChange?: number;
  twentyDayChange?: number;
  volumeRatio?: number;
  turnoverRate?: number;
  fundFlow?: string;
  reason?: string;
  riskNote?: string;
  updateTime?: string;
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
  sentiment: "利好" | "利空" | "中性";
  url: string;
}
