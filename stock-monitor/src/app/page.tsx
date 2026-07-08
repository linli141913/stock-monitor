'use client';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'https://banister-drilling-jawless.ngrok-free.dev';

import { useState, useEffect, useCallback, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
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

// Removed mock data imports

const AUTO_REFRESH_INTERVAL = 5 * 1000; // 5秒高频自动刷新行情

function HomeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const codeParam = searchParams.get('code');
  const stockCode = codeParam || '000725';
  
  const [isMounted, setIsMounted] = useState(false);
  const [period, setPeriod] = useState<'day'|'week'|'month'|'year'>('day');

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const [overviewData, setOverviewData] = useState<any>(null);
  const [klineData, setKlineData] = useState<any[]>([]);
  const [companyData, setCompanyData] = useState<any>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [klineLoading, setKlineLoading] = useState(true);
  const [overviewError, setOverviewError] = useState('');
  const [klineError, setKlineError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [globalNews, setGlobalNews] = useState<any[]>([]);

  const autoRefreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

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
    if (!isSilent) setOverviewLoading(true);
    setOverviewError('');
    try {
      const res = await fetch(`${API_BASE}/api/stock/overview/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `行情请求失败 (${res.status})`);
      }
      const data = await res.json();
      const overviewResult = {
        stockName:    data.name,
        stockCode:    data.code,
        marketStatus: data.marketStatus,
        latestPrice:  data.latestPrice,
        changeAmount: data.changeAmount,
        changePercent:data.changePercent,
        openPrice:    data.details.open,
        highPrice:    data.details.high,
        lowPrice:     data.details.low,
        previousClose:data.details.previousClose,
        volume:       data.details.volume,
        turnoverAmount:data.details.turnoverAmount,
        turnoverRate: parseFloat(data.details.turnoverRate) || 0,
        peDynamic:    parseFloat(data.details.peRatio) || 0,
        marketCap:    data.details.marketCap,
        updateTime:   data.updateTime,
        fundFlow:     data.fundFlow,
      };
      
      setOverviewData(overviewResult);
      setLastRefresh(new Date());

      // 如果后端做了代码纠偏（比如港股多输了0），同步更新前端的统一搜索状态
      if (data.code && data.code.toLowerCase() !== stockCode.toLowerCase()) {
        // 如果只是加了 sh/sz/bj/hk 前缀，就不强制刷新 URL，避免二次渲染带来的卡顿
        const strippedBackend = data.code.toLowerCase().replace(/^(sh|sz|hk|bj)/, '');
        const strippedFront = stockCode.toLowerCase().replace(/^(sh|sz|hk|bj)/, '');
        if (strippedBackend !== strippedFront && data.name !== stockCode) {
          router.push(`/?code=${data.code.toLowerCase()}`);
        }
      }

      // 实时更新 K 线的最后一根蜡烛（产生“动态呼吸感”）
      setKlineData(prev => {
        if (prev.length === 0) return prev;
        const newKline = [...prev];
        const last = { ...newKline[newKline.length - 1] };
        const currentPrice = overviewResult.latestPrice;
        
        // 动态撑开影线并更新收盘价
        last.close = currentPrice;
        if (currentPrice > last.high) last.high = currentPrice;
        if (currentPrice < last.low) last.low = currentPrice;
        if (overviewResult.volume > 0) last.value = overviewResult.volume;
        
        newKline[newKline.length - 1] = last;
        return newKline;
      });

    } catch (err: any) {
      if (!isSilent) setOverviewError(err.message || '行情获取失败');
    } finally {
      if (!isSilent) setOverviewLoading(false);
    }
  }, [stockCode]);

  // ── 获取 K 线（换股或换周期时触发） ─────────────────────
  const fetchKline = useCallback(async () => {
    setKlineLoading(true);
    setKlineError('');
    try {
      const res = await fetch(`${API_BASE}/api/stock/kline/${stockCode}?period=${period}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `K 线请求失败 (${res.status})`);
      }
      const json = await res.json();
      const mapped = json.data.map((item: any) => ({
        ...item,
        date: item.time,       // 对齐前端字段名
        value: item.volume,    // ECharts 成交量用 value
      }));
      setKlineData(mapped);
    } catch (err: any) {
      setKlineError(err.message || 'K 线获取失败');
    } finally {
      setKlineLoading(false);
    }
  }, [stockCode, period]);

  // ── 获取公司信息和公告 ─────────────────────
  const fetchCompanyInfo = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/company/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res.ok) {
        const data = await res.json();
        setCompanyData(data);
      }
    } catch (err) {
      console.error('Failed to fetch company info', err);
    }
  }, [stockCode]);

  // ── 获取自建的新浪财经新闻 (方案A) ─────────────────────
  const fetchGlobalNews = useCallback(async () => {
    try {
      const res = await fetch(`/api/news?num=30`);
      if (res.ok) {
        const json = await res.json();
        setGlobalNews(json.data || []);
      }
    } catch (err) {
      console.error('Failed to fetch global news', err);
    }
  }, []);

  const [relatedStocks, setRelatedStocks] = useState<any[]>([]);
  const [industryMonitorData, setIndustryMonitorData] = useState<any>({});
  const [abnormalPeers, setAbnormalPeers] = useState<any[]>([]);
  // ── 批量获取相关股票真实价格 ─────────────────────
  const fetchRelatedPrices = useCallback(async (sym: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/related/${sym}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res.ok) {
        const json = await res.json();
        if (json.data && json.data.length > 0) {
          setRelatedStocks(json.data);
        } else {
          setRelatedStocks([]);
        }
      }
    } catch (err) {
      console.error('Failed to fetch related prices', err);
    }
  }, []);

  // ── 获取行业资金与异常推荐 ─────────────────────
  const fetchIndustryAndPeers = useCallback(async (sym: string) => {
    try {
      const res1 = await fetch(`${API_BASE}/api/stock/industry/${sym}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res1.ok) {
        setIndustryMonitorData(await res1.json());
      }
      const res2 = await fetch(`${API_BASE}/api/stock/abnormal_peers/${sym}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res2.ok) {
        const json2 = await res2.json();
        let fetchedData = json2.data || [];
        setAbnormalPeers(fetchedData);
      }
    } catch (err) {
      console.error('Failed to fetch industry/peers', err);
    }
  }, []);

  // 换股时，行情 + 公司信息 都刷新
  useEffect(() => {
    // 切换股票时立刻清空旧状态，防止闪烁上次的数据
    setOverviewData(null);
    setCompanyData(null);
    setGlobalNews([]);
    setRelatedStocks([]);
    setIndustryMonitorData({});
    setAbnormalPeers([]);

    fetchOverview();
    fetchCompanyInfo();
    fetchRelatedPrices(stockCode);
    fetchIndustryAndPeers(stockCode);
    fetchGlobalNews();
  }, [stockCode, fetchOverview, fetchCompanyInfo, fetchRelatedPrices, fetchIndustryAndPeers, fetchGlobalNews]);

  // 换周期或换股时，刷新 K 线
  useEffect(() => {
    // 切换股票时立刻清空K线数据
    setKlineData([]);
    fetchKline();
  }, [stockCode, period, fetchKline]);

  // 30 秒自动刷新行情（不影响 K 线）
  useEffect(() => {
    if (autoRefreshTimer.current) clearInterval(autoRefreshTimer.current);
    autoRefreshTimer.current = setInterval(() => {
      fetchOverview(true);
      fetchRelatedPrices(stockCode);
      fetchIndustryAndPeers(stockCode);
    }, AUTO_REFRESH_INTERVAL);
    return () => {
      if (autoRefreshTimer.current) clearInterval(autoRefreshTimer.current);
    };
  }, [fetchOverview, fetchRelatedPrices]);

  // ── 渲染 ──────────────────────────────────────────────────
  if (!isMounted) {
    return <div style={{ padding: '24px', textAlign: 'center' }}>加载中...</div>;
  }

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
              <button onClick={() => fetchOverview(false)} style={{ marginLeft: 12, cursor: 'pointer' }}>重试</button>
            </div>
          ) : overviewData && (
            <StockOverviewCard
              data={overviewData}
              lastRefresh={lastRefresh}
              onRefresh={() => fetchOverview(false)}
            />
          )}

          {klineError ? (
            <div className={styles.errorContainer}>
              ⚠️ {klineError}
              <button onClick={fetchKline} style={{ marginLeft: 12, cursor: 'pointer' }}>重试</button>
            </div>
          ) : klineData.length > 0 && !isDataStale ? (
            <StockChartCard
              data={klineData}
              period={period}
              onPeriodChange={setPeriod}
              loading={klineLoading}
            />
          ) : (
            <div className={styles.loadingContainer}>K 线加载中...</div>
          )}

          <StockInfoTabs
            stockCode={stockCode}
            companyInfo={companyData?.companyInfo || {}}
            financialData={companyData?.financialData || {}}
            announcements={companyData?.announcements || []}
            news={globalNews.length > 0 ? globalNews : (companyData?.news || [])}
          />
        </div>

        {/* 右侧侧栏 */}
        <div className={styles.rightCol}>
          <IndustryMonitorCard data={{
             ...industryMonitorData,
             industryName: companyData?.companyInfo?.industryTags?.length > 0 ? companyData.companyInfo.industryTags[0] : industryMonitorData.industryName
          }} />
          <RelatedStocksCard data={relatedStocks} onStockClick={handleSearch} />
          {(() => {
            const relatedCodes = new Set(relatedStocks.map((r: any) => r.stockCode));
            const filteredPeers = abnormalPeers.filter(p => !relatedCodes.has(p.stockCode)).slice(0, 10);
            return filteredPeers.length > 0 ? <AbnormalStocksCard data={filteredPeers} onStockClick={handleSearch} /> : null;
          })()}
          <AlertSettingCard initialData={{
            stockCode: stockCode,
            stockName: overviewData ? overviewData.stockName : '',
            email: 'admin@example.com',
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
