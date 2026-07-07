'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
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

// Mock Data（暂时保留，后续逐步替换）
import { mockCompanyInfo, mockFinancialSummary, mockAnnouncements, mockNews, mockRelatedStocks, mockAbnormalStocks } from '@/mock/stockMock';
import { mockIndustryMonitor } from '@/mock/industryMock';
import { mockAlertRule } from '@/mock/alertMock';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';
const AUTO_REFRESH_INTERVAL = 5 * 1000; // 5秒高频自动刷新行情

export default function Home() {
  const [stockCode, setStockCode] = useState('000021');
  const [period, setPeriod] = useState<'day'|'week'|'month'|'year'>('day');

  const [overviewData, setOverviewData] = useState<any>(null);
  const [klineData, setKlineData] = useState<any[]>([]);
  const [companyData, setCompanyData] = useState<any>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [klineLoading, setKlineLoading] = useState(true);
  const [overviewError, setOverviewError] = useState('');
  const [klineError, setKlineError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const autoRefreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 搜索 ──────────────────────────────────────────────────
  const handleSearch = (keyword: string) => {
    const code = keyword.match(/(?:hk)?\d{5,6}/i);
    if (code) {
      setStockCode(code[0].toLowerCase());
    } else {
      alert('请输入 6 位 A 股股票代码 或 5 位港股代码，如：000021 / hk00700');
    }
  };

  // ── 获取实时行情（独立，可单独刷新） ─────────────────────
  const fetchOverview = useCallback(async (isSilent = false) => {
    if (!isSilent) setOverviewLoading(true);
    setOverviewError('');
    try {
      const res = await fetch(`${API_BASE}/api/stock/overview/${stockCode}`);
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
        setStockCode(data.code.toLowerCase());
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
      const res = await fetch(`${API_BASE}/api/stock/kline/${stockCode}?period=${period}`);
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
      const res = await fetch(`${API_BASE}/api/stock/company/${stockCode}`);
      if (res.ok) {
        const data = await res.json();
        setCompanyData(data);
      }
    } catch (err) {
      console.error('Failed to fetch company info', err);
    }
  }, [stockCode]);

  const [relatedStocks, setRelatedStocks] = useState(mockRelatedStocks);
  const [industryMonitorData, setIndustryMonitorData] = useState(mockIndustryMonitor);
  const [abnormalPeers, setAbnormalPeers] = useState<any[]>(mockAbnormalStocks);
  // ── 批量获取相关股票真实价格 ─────────────────────
  const fetchRelatedPrices = useCallback(async (sym: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/related/${sym}`);
      if (res.ok) {
        const json = await res.json();
        if (json.data && json.data.length > 0) {
          setRelatedStocks(json.data);
        } else {
          setRelatedStocks(mockRelatedStocks);
        }
      }
    } catch (err) {
      console.error('Failed to fetch related prices', err);
    }
  }, []);

  // ── 获取行业资金与异常推荐 ─────────────────────
  const fetchIndustryAndPeers = useCallback(async (sym: string) => {
    try {
      const res1 = await fetch(`${API_BASE}/api/stock/industry/${sym}`);
      if (res1.ok) {
        setIndustryMonitorData(await res1.json());
      }
      const res2 = await fetch(`${API_BASE}/api/stock/abnormal_peers/${sym}`);
      if (res2.ok) {
        const json2 = await res2.json();
        let fetchedData = json2.data || [];
        // 如果真实接口取到的异动股票不足10支，用预设黑马池子补齐，保证视觉丰满
        if (fetchedData.length < 10) {
          fetchedData = [...fetchedData, ...mockAbnormalStocks.filter((m: any) => !fetchedData.some((f:any) => f.stockCode === m.stockCode))].slice(0, 10);
        }
        setAbnormalPeers(fetchedData);
      }
    } catch (err) {
      console.error('Failed to fetch industry/peers', err);
    }
  }, []);

  // 换股时，行情 + K 线 + 公司信息 都刷新
  useEffect(() => {
    fetchOverview();
    fetchKline();
    fetchCompanyInfo();
    fetchRelatedPrices(stockCode);
    fetchIndustryAndPeers(stockCode);
  }, [stockCode]);

  // 换周期时，只刷新 K 线，行情不动
  useEffect(() => {
    fetchKline();
  }, [period]);

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

  // ── 渲染 ─────────────────────────────────────────────────
  return (
    <div className={styles.container}>
      <SearchMonitorBar onSearch={handleSearch} />

      <div className={styles.layout}>
        {/* 左侧主内容区 */}
        <div className={styles.leftCol}>
          {overviewLoading ? (
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
          ) : klineData.length > 0 ? (
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
            companyInfo={companyData?.companyInfo || mockCompanyInfo}
            financialData={companyData?.financialData || mockFinancialSummary}
            announcements={companyData?.announcements || mockAnnouncements}
            news={companyData?.news || mockNews}
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
          <AlertSettingCard initialData={mockAlertRule} />
          <DataSourceCard />
        </div>
      </div>
    </div>
  );
}
