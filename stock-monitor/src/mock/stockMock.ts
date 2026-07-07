import { StockOverview, CompanyInfo, FinancialSummary, Announcement, News, RelatedStock, AbnormalStock } from '@/types/stock';

export const mockStockOverview: StockOverview = {
  stockName: "深科技",
  stockCode: "000021",
  latestPrice: 18.76,
  changeAmount: 0.58,
  changePercent: 3.19,
  openPrice: 18.21,
  highPrice: 18.98,
  lowPrice: 18.05,
  previousClose: 18.18,
  volume: "1,256.32万手",
  turnoverAmount: "23.55亿元",
  turnoverRate: 3.45,
  peDynamic: 28.56,
  marketCap: "286.61亿元",
  updateTime: "2024-05-23 15:00:00",
  dataSource: "AKShare / 东方财富",
  dataType: "准实时",
  refreshInterval: "15～60秒",
  delayNote: "公开数据源可能存在几十秒到数分钟延迟",
  industry: "电子制造",
  concepts: ["半导体概念", "5G", "物联网", "消费电子", "国产芯片"]
};

export const mockCompanyInfo: CompanyInfo = {
  mainBusiness: "专注于电子制造服务 (EMS)，为全球客户提供电子产品研发设计、生产制造及技术支持服务。",
  coreProducts: ["存储半导体封测", "消费电子产品制造", "汽车电子", "工业控制"],
  industryTags: ["电子制造", "半导体", "国产芯片"],
  companyDescription: "深科技是全球领先的电子制造服务企业，主要提供高质量的EMS服务，涉及存储芯片、通信及消费电子产品等领域。",
  businessRelation: "与半导体封测、存储芯片、电子制造产业链密切相关，是国产存储产业链的重要一环。",
  updateTime: "2024-05-23 15:00:00"
};

export const mockFinancialSummary: FinancialSummary = {
  reportPeriod: "2024Q1",
  revenue: "35.24亿元",
  revenueYoy: "+12.5%",
  netProfit: "1.85亿元",
  netProfitYoy: "+8.2%",
  grossMargin: "14.2%",
  netMargin: "5.3%",
  roe: "2.1%",
  eps: "0.12",
  debtRatio: "58.4%",
  updateTime: "2024-05-23 15:00:00"
};

export const mockAnnouncements: Announcement[] = [
  {
    id: "1",
    title: "关于控股子公司增资扩股的公告",
    publishTime: "2024-05-20",
    source: "公司公告",
    summary: "公司发布控股子公司相关增资事项，旨在扩大存储封测产能。",
    url: "#",
    importance: "高"
  },
  {
    id: "2",
    title: "2023年年度权益分派实施公告",
    publishTime: "2024-05-15",
    source: "公司公告",
    summary: "向全体股东每10股派发现金红利1.5元（含税）。",
    url: "#",
    importance: "中"
  },
  {
    id: "3",
    title: "关于高级管理人员减持股份计划的预披露公告",
    publishTime: "2024-05-10",
    source: "公司公告",
    summary: "某高管拟减持不超过50万股。",
    url: "#",
    importance: "中"
  }
];

export const mockNews: News[] = [
  {
    id: "1",
    title: "半导体产业链景气度回升，封测板块领涨",
    source: "财经新闻网",
    publishTime: "2024-05-23 10:30:00",
    summary: "受AI需求拉动，半导体产业链近期受到资金关注，封测产能利用率提升。",
    sentiment: "利好",
    url: "#"
  },
  {
    id: "2",
    title: "存储芯片价格持续企稳反弹",
    source: "行业内参",
    publishTime: "2024-05-22 14:15:00",
    summary: "各大原厂持续减产效应显现，DRAM和NAND价格呈现温和上涨态势。",
    sentiment: "利好",
    url: "#"
  },
  {
    id: "3",
    title: "消费电子复苏不及预期，手机出货量承压",
    source: "科技日报",
    publishTime: "2024-05-21 09:00:00",
    summary: "一季度全球智能手机出货量增幅放缓，对相关供应链企业可能造成一定压力。",
    sentiment: "利空",
    url: "#"
  }
];

export const mockRelatedStocks: RelatedStock[] = [
  { stockName: "深科技", stockCode: "000021", latestPrice: 18.76, changePercent: 3.19, industryRelation: "电子制造核心标的", abnormalTag: "资金流入" },
  { stockName: "工业富联", stockCode: "601138", latestPrice: 24.36, changePercent: 2.78, industryRelation: "EMS同行", abnormalTag: "资金流入" },
  { stockName: "立讯精密", stockCode: "002475", latestPrice: 33.18, changePercent: 1.99, industryRelation: "消费电子" },
  { stockName: "长电科技", stockCode: "600584", latestPrice: 28.45, changePercent: 1.20, industryRelation: "半导体封测" },
  { stockName: "歌尔股份", stockCode: "002241", latestPrice: 15.67, changePercent: -0.51, industryRelation: "消费电子" },
  { stockName: "环旭电子", stockCode: "601231", latestPrice: 15.20, changePercent: -0.59, industryRelation: "EMS同行" }
];

export const mockAbnormalStocks: AbnormalStock[] = [
  { stockName: "北方华创", stockCode: "002371", oneDayChange: 6.8, twentyDayChange: 32.1, volumeRatio: 2.6, reason: "半导体设备景气度高", riskNote: "短期涨幅较大", updateTime: "15:01:00" },
  { stockName: "中芯国际", stockCode: "688981", oneDayChange: 4.5, twentyDayChange: 15.2, volumeRatio: 1.8, fundFlow: "净流入 5.3亿元", reason: "大基金加码预期", updateTime: "15:01:00" },
  { stockName: "海光信息", stockCode: "688041", oneDayChange: -5.2, twentyDayChange: -12.4, volumeRatio: 1.2, reason: "获利盘涌出", updateTime: "15:01:00" },
  { stockName: "寒武纪", stockCode: "688256", oneDayChange: 10.0, twentyDayChange: 45.0, volumeRatio: 3.5, reason: "AI算力需求暴增", riskNote: "估值偏高", updateTime: "15:01:00" },
  { stockName: "长电科技", stockCode: "600584", oneDayChange: 5.1, twentyDayChange: 8.5, volumeRatio: 1.9, reason: "先进封装订单激增", updateTime: "15:01:00" },
  { stockName: "紫光国微", stockCode: "002049", oneDayChange: -2.3, twentyDayChange: 5.4, volumeRatio: 0.8, reason: "特种IC排产不及预期", updateTime: "15:01:00" },
  { stockName: "兆易创新", stockCode: "603986", oneDayChange: 4.2, twentyDayChange: 11.2, volumeRatio: 1.5, reason: "存储芯片涨价", updateTime: "15:01:00" },
  { stockName: "韦尔股份", stockCode: "603501", oneDayChange: 3.8, twentyDayChange: 9.7, volumeRatio: 1.3, reason: "消费电子复苏", updateTime: "15:01:00" },
  { stockName: "澜起科技", stockCode: "688008", oneDayChange: 7.5, twentyDayChange: 25.4, volumeRatio: 2.2, reason: "DDR5渗透率提升", updateTime: "15:01:00" },
  { stockName: "江丰电子", stockCode: "300666", oneDayChange: 8.9, twentyDayChange: 18.9, volumeRatio: 2.8, reason: "半导体材料国产替代", updateTime: "15:01:00" },
];
