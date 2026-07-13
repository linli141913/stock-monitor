'use client';

const API_BASE = '/api/backend';

import { useState, useEffect, useCallback, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import styles from './page.module.css';

// Components
import SearchMonitorBar from '@/components/stock/SearchMonitorBar';
import StockOverviewCard from '@/components/stock/StockOverviewCard';
import StockChartCard from '@/components/stock/StockChartCard';
import StockInfoTabs from '@/components/stock/StockInfoTabs';
import IndustryMonitorCard from '@/components/industry/IndustryMonitorCard';
import RelatedStocksCard from '@/components/stock/RelatedStocksCard';
import AbnormalStocksCard from '@/components/industry/AbnormalStocksCard';
import AlertSettingCard from '@/components/alert/AlertSettingCard';
import DataSourceCard from '@/components/common/DataSourceCard';
import type {
  AbnormalStock,
  Announcement,
  CompanyInfo,
  KlineItem,
  News,
  RelatedStock,
  StockOverview,
} from '@/types/stock';
import type { IndustryMonitor } from '@/types/industry';

// Removed mock data imports

const OVERVIEW_REFRESH_INTERVAL = 10 * 1000;
const SLOW_DATA_REFRESH_INTERVAL = 2 * 60 * 1000;
const SLOW_DATA_REQUEST_TIMEOUT = 12 * 1000;

const EMPTY_COMPANY_INFO: CompanyInfo = {
  mainBusiness: '',
  coreProducts: [],
  industryTags: [],
  companyDescription: '',
  businessRelation: '',
  updateTime: '',
};

const EMPTY_INDUSTRY_MONITOR: IndustryMonitor = {
  industryName: '',
  heatScore: null,
  sectorChangePercent: null,
  fundFlow: '',
};

const hasUsableIndustryMetrics = (data: IndustryMonitor) => (
  data.heatScore != null
  || data.sectorChangePercent != null
  || Boolean(data.fundFlow && !data.fundFlow.startsWith('暂无'))
);

interface OverviewApiResponse {
  name: string;
  code: string;
  marketStatus?: string;
  marketStatusCode?: StockOverview['marketStatusCode'];
  latestPrice: number | null;
  changeAmount: number | null;
  changePercent: number | null;
  sourceTime: string | null;
  fetchedAt: string;
  fundFlow?: string;
  details: {
    open: number | null;
    high: number | null;
    low: number | null;
    previousClose: number | null;
    volume: string | null;
    turnoverAmount: string | null;
    turnoverRate?: number | null;
    peRatio?: number | null;
    marketCap?: string | null;
  };
}

interface KlineApiItem extends Omit<KlineItem, 'date'> {
  time: string;
}

interface CompanyDataResponse {
  companyInfo?: CompanyInfo;
  announcements?: Announcement[];
  news?: News[];
}

interface ErrorResponse {
  detail?: string;
}

function HomeContent() {
  const searchParams = useSearchParams();
  const codeParam = searchParams.get('code');
  const stockCode = codeParam || '000725';
  
  const [period, setPeriod] = useState<'day'|'week'|'month'|'year'>('day');

  const [overviewData, setOverviewData] = useState<StockOverview | null>(null);
  const [klineData, setKlineData] = useState<KlineItem[]>([]);
  const [companyData, setCompanyData] = useState<CompanyDataResponse | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [klineLoading, setKlineLoading] = useState(true);
  const [overviewError, setOverviewError] = useState('');
  const [klineError, setKlineError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [globalNews, setGlobalNews] = useState<News[]>([]);
  const [industryLoading, setIndustryLoading] = useState(true);
  const [industryRefreshing, setIndustryRefreshing] = useState(false);
  const [industryStatusMessage, setIndustryStatusMessage] = useState('');

  const overviewRefreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const slowDataRefreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const overviewRequestInFlight = useRef(false);
  const relatedRequestInFlight = useRef(false);
  const industryRequestInFlight = useRef(false);
  const abnormalPeersRequestInFlight = useRef(false);
  const industryHasUsableData = useRef(false);

  // ── 搜索 ──────────────────────────────────────────────────
  const handleSearch = (keyword: string) => {
    const trimmed = keyword.trim();
    if (trimmed) {
      window.location.href = `/?code=${trimmed}`;
    } else {
      alert('请输入股票名称或代码，如：平安银行 / 000001');
    }
  };

  // ── 获取实时行情（独立，可单独刷新） ─────────────────────
  const fetchOverview = useCallback(async (isSilent = false) => {
    if (overviewRequestInFlight.current) return;
    overviewRequestInFlight.current = true;
    if (!isSilent) setOverviewLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/stock/overview/${stockCode}?_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as ErrorResponse;
        throw new Error(err.detail || `行情请求失败 (${res.status})`);
      }
      const data = await res.json() as OverviewApiResponse;
      const overviewResult: StockOverview = {
        stockName:    data.name,
        stockCode:    data.code,
        marketStatus: data.marketStatus,
        marketStatusCode: data.marketStatusCode,
        latestPrice:  data.latestPrice,
        changeAmount: data.changeAmount,
        changePercent:data.changePercent,
        openPrice:    data.details.open,
        highPrice:    data.details.high,
        lowPrice:     data.details.low,
        previousClose:data.details.previousClose,
        volume:       data.details.volume,
        turnoverAmount:data.details.turnoverAmount,
        turnoverRate: data.details.turnoverRate ?? null,
        peDynamic:    data.details.peRatio ?? null,
        marketCap:    data.details.marketCap,
        sourceTime:   data.sourceTime,
        fetchedAt:    data.fetchedAt,
        fundFlow:     data.fundFlow,
      };
      
      setOverviewError('');
      setOverviewData(overviewResult);
      setLastRefresh(new Date());

      // 如果后端做了代码纠偏（比如港股多输了0），同步更新前端的统一搜索状态
      if (data.code && data.code.toLowerCase() !== stockCode.toLowerCase()) {
        // 如果只是加了 sh/sz/bj/hk 前缀，就不强制刷新 URL，避免二次渲染带来的卡顿
        const strippedBackend = data.code.toLowerCase().replace(/^(sh|sz|hk|bj)/, '');
        const strippedFront = stockCode.toLowerCase().replace(/^(sh|sz|hk|bj)/, '');
        if (strippedBackend !== strippedFront && data.name !== stockCode) {
          window.location.href = `/?code=${data.code.toLowerCase()}`;
        }
      }

      // 实时更新 K 线的最后一根蜡烛（产生“动态呼吸感”）
      setKlineData(prev => {
        if (prev.length === 0) return prev;
        const newKline = [...prev];
        const last = { ...newKline[newKline.length - 1] };
        const currentPrice = overviewResult.latestPrice;
        if (currentPrice == null) return prev;
        
        // 动态撑开影线并更新收盘价
        last.close = currentPrice;
        if (currentPrice > last.high) last.high = currentPrice;
        if (currentPrice < last.low) last.low = currentPrice;
        
        newKline[newKline.length - 1] = last;
        return newKline;
      });

    } catch (err: unknown) {
      if (!isSilent) setOverviewError(err instanceof Error ? err.message : '行情获取失败');
    } finally {
      overviewRequestInFlight.current = false;
      if (!isSilent) setOverviewLoading(false);
    }
  }, [stockCode]);

  // ── 获取 K 线（换股或换周期时触发） ─────────────────────
  const fetchKline = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/kline/${stockCode}?period=${period}&_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as ErrorResponse;
        throw new Error(err.detail || `K 线请求失败 (${res.status})`);
      }
      const json = await res.json() as { data: KlineApiItem[] };
      const mapped: KlineItem[] = json.data.map((item) => ({
        ...item,
        date: item.time,       // 对齐前端字段名
      }));
      setKlineError('');
      setKlineData(mapped);
    } catch (err: unknown) {
      setKlineError(err instanceof Error ? err.message : 'K 线获取失败');
    } finally {
      setKlineLoading(false);
    }
  }, [stockCode, period]);

  // ── 获取公司信息和公告 ─────────────────────
  const fetchCompanyInfo = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/company/${stockCode}?_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store'
      });
      if (res.ok) {
        const data = await res.json() as CompanyDataResponse;
        setCompanyData(data);
      }
    } catch (err) {
      console.error('Failed to fetch company info', err);
    }
  }, [stockCode]);

  // ── 获取自建的新浪财经新闻 (方案A) ─────────────────────
  const fetchGlobalNews = useCallback(async () => {
    try {
      const res = await fetch(`/api/news?num=30&_t=${Date.now()}`, {
        cache: 'no-store'
      });
      if (res.ok) {
        const json = await res.json() as { data?: News[] };
        setGlobalNews(json.data || []);
      }
    } catch (err) {
      console.error('Failed to fetch global news', err);
    }
  }, []);

  const [relatedStocks, setRelatedStocks] = useState<RelatedStock[]>([]);
  const [industryMonitorData, setIndustryMonitorData] = useState<IndustryMonitor>(EMPTY_INDUSTRY_MONITOR);
  const [abnormalPeers, setAbnormalPeers] = useState<AbnormalStock[]>([]);
  // ── 批量获取相关股票真实价格 ─────────────────────
  const fetchRelatedPrices = useCallback(async (sym: string) => {
    if (relatedRequestInFlight.current) return;
    relatedRequestInFlight.current = true;
    try {
      const res = await fetch(`${API_BASE}/api/stock/related/${sym}?_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store'
      });
      if (res.ok) {
        const json = await res.json() as { data?: RelatedStock[] };
        if (json.data && json.data.length > 0) {
          setRelatedStocks(json.data);
        } else {
          setRelatedStocks([]);
        }
      }
    } catch (err) {
      console.error('Failed to fetch related prices', err);
    } finally {
      relatedRequestInFlight.current = false;
    }
  }, []);

  // ── 获取行业资金与异常推荐 ─────────────────────
  const fetchIndustry = useCallback(async (sym: string, isSilent = false) => {
    if (industryRequestInFlight.current) return;
    industryRequestInFlight.current = true;
    if (isSilent) {
      setIndustryRefreshing(true);
    } else {
      setIndustryLoading(true);
    }
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), SLOW_DATA_REQUEST_TIMEOUT);
    try {
      const response = await fetch(`${API_BASE}/api/stock/industry/${sym}?_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`行业数据请求失败 (${response.status})`);
      }
      const nextData = await response.json() as IndustryMonitor;
      const nextHasUsableData = hasUsableIndustryMetrics(nextData);
      if (!nextHasUsableData && industryHasUsableData.current) {
        setIndustryStatusMessage('本次指标暂不可用，继续显示上次成功数据');
      } else {
        setIndustryMonitorData(nextData);
        industryHasUsableData.current = nextHasUsableData;
        setIndustryStatusMessage(nextHasUsableData ? '' : '当前行业指标暂无可用数据');
      }
    } catch (err) {
      const timedOut = err instanceof DOMException && err.name === 'AbortError';
      setIndustryStatusMessage(
        industryHasUsableData.current
          ? `${timedOut ? '行业更新超时' : '行业更新失败'}，继续显示上次成功数据`
          : `${timedOut ? '行业数据请求超时' : '行业数据加载失败'}，请稍后重试`,
      );
      console.error('Failed to fetch industry data', err);
    } finally {
      window.clearTimeout(timeoutId);
      industryRequestInFlight.current = false;
      if (isSilent) {
        setIndustryRefreshing(false);
      } else {
        setIndustryLoading(false);
      }
    }
  }, []);

  const fetchAbnormalPeers = useCallback(async (sym: string) => {
    if (abnormalPeersRequestInFlight.current) return;
    abnormalPeersRequestInFlight.current = true;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), SLOW_DATA_REQUEST_TIMEOUT);
    try {
      const response = await fetch(`${API_BASE}/api/stock/abnormal_peers/${sym}?_t=${Date.now()}`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`异常同行请求失败 (${response.status})`);
      }
      const json = await response.json() as { data?: AbnormalStock[] };
      setAbnormalPeers(json.data || []);
    } catch (err) {
      console.error('Failed to fetch abnormal peers', err);
    } finally {
      window.clearTimeout(timeoutId);
      abnormalPeersRequestInFlight.current = false;
    }
  }, []);

  const handleOverviewRefresh = () => {
    setOverviewError('');
    void fetchOverview(false);
  };

  const handleKlineRefresh = () => {
    setKlineLoading(true);
    setKlineError('');
    void fetchKline();
  };

  const handlePeriodChange = (nextPeriod: 'day' | 'week' | 'month' | 'year') => {
    setKlineData([]);
    setKlineLoading(true);
    setKlineError('');
    setPeriod(nextPeriod);
  };

  const handleIndustryRefresh = () => {
    void fetchIndustry(stockCode, true);
    void fetchAbnormalPeers(stockCode);
  };

  // 换股时，行情 + 公司信息 都刷新
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchOverview();
      void fetchCompanyInfo();
      void fetchRelatedPrices(stockCode);
      void fetchIndustry(stockCode);
      void fetchAbnormalPeers(stockCode);
      void fetchGlobalNews();
    }, 0);
    return () => clearTimeout(timer);
  }, [stockCode, fetchOverview, fetchCompanyInfo, fetchRelatedPrices, fetchIndustry, fetchAbnormalPeers, fetchGlobalNews]);

  // 换周期或换股时，刷新 K 线
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchKline();
    }, 0);
    return () => clearTimeout(timer);
  }, [fetchKline]);

  // 行情独立刷新，不触发 K 线或慢数据请求。
  useEffect(() => {
    if (overviewRefreshTimer.current) clearInterval(overviewRefreshTimer.current);
    overviewRefreshTimer.current = setInterval(() => {
      void fetchOverview(true);
    }, OVERVIEW_REFRESH_INTERVAL);
    return () => {
      if (overviewRefreshTimer.current) clearInterval(overviewRefreshTimer.current);
    };
  }, [fetchOverview]);

  // 相关股票和行业数据使用独立的低频刷新。
  useEffect(() => {
    if (slowDataRefreshTimer.current) clearInterval(slowDataRefreshTimer.current);
    slowDataRefreshTimer.current = setInterval(() => {
      void fetchRelatedPrices(stockCode);
      void fetchIndustry(stockCode, true);
      void fetchAbnormalPeers(stockCode);
    }, SLOW_DATA_REFRESH_INTERVAL);
    return () => {
      if (slowDataRefreshTimer.current) clearInterval(slowDataRefreshTimer.current);
    };
  }, [fetchIndustry, fetchAbnormalPeers, fetchRelatedPrices, stockCode]);

  // ── 渲染 ──────────────────────────────────────────────────
  // 关键修复：当组件被 Next.js 路由缓存复用时，判断旧数据是否和当前网址的 stockCode 匹配
  // 如果搜索的是中文名称，而返回的数据中 stockName 匹配，或者代码匹配，则不算 stale。
  const isDataStale = overviewData && 
    !(overviewData.stockCode.toLowerCase().includes(stockCode.toLowerCase().replace(/^(sh|sz|hk)/i, ''))) &&
    !(overviewData.stockName === stockCode);
  const showOverviewLoading = (overviewLoading || isDataStale) && !overviewError;

  return (
    <div className={styles.container}>
      <SearchMonitorBar onSearch={handleSearch} />

      <div className={styles.layout}>
        {/* 左侧主内容区 */}
        <div className={styles.leftCol}>
          {showOverviewLoading ? (
            <div className={styles.loadingContainer}>行情加载中...</div>
          ) : overviewError ? (
            <div className={styles.errorContainer}>
              ⚠️ {overviewError}
              <button onClick={handleOverviewRefresh} style={{ marginLeft: 12, cursor: 'pointer' }}>重试</button>
            </div>
          ) : overviewData && (
            <StockOverviewCard
              data={overviewData}
              lastRefresh={lastRefresh}
              onRefresh={handleOverviewRefresh}
              onWatchlistToggle={handleIndustryRefresh}
            />
          )}

          {klineError ? (
            <div className={styles.errorContainer}>
              ⚠️ {klineError}
              <button onClick={handleKlineRefresh} style={{ marginLeft: 12, cursor: 'pointer' }}>重试</button>
            </div>
          ) : klineData.length > 0 && !isDataStale ? (
            <StockChartCard
              data={klineData}
              period={period}
              onPeriodChange={handlePeriodChange}
              loading={klineLoading}
            />
          ) : (
            <div className={styles.loadingContainer}>K 线加载中...</div>
          )}

          <StockInfoTabs
            stockCode={stockCode}
            companyInfo={companyData?.companyInfo || EMPTY_COMPANY_INFO}
            announcements={companyData?.announcements || []}
            news={globalNews.length > 0 ? globalNews : (companyData?.news || [])}
          />
        </div>

        {/* 右侧侧栏 */}
        <div className={styles.rightCol}>
          <IndustryMonitorCard 
            data={{
               ...industryMonitorData,
               industryName: companyData?.companyInfo?.industryTags?.[0] || industryMonitorData.industryName
            }} 
            loading={industryLoading}
            refreshing={industryRefreshing}
            statusMessage={industryStatusMessage}
          />
          <RelatedStocksCard data={relatedStocks} onStockClick={handleSearch} />
          {(() => {
            const relatedCodes = new Set(relatedStocks.map((relatedStock) => relatedStock.stockCode));
            const filteredPeers = abnormalPeers.filter(p => !relatedCodes.has(p.stockCode)).slice(0, 10);
            return filteredPeers.length > 0 ? <AbnormalStocksCard data={filteredPeers} onStockClick={handleSearch} /> : null;
          })()}
          <AlertSettingCard initialData={{
            stockCode: stockCode,
            stockName: overviewData ? overviewData.stockName : '',
            email: '',
            priceChangeAlert: true,
            priceChangeThreshold: 5,
            volumeAlert: true,
            volumeRatioThreshold: 2.0,
            announcementAlert: true,
            industryAlert: true,
            abnormalStockAlert: true,
            enabled: true
          }} />
          <DataSourceCard />
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div style={{ padding: '24px', textAlign: 'center' }}>加载中...</div>}>
      <HomeContent />
    </Suspense>
  );
}
