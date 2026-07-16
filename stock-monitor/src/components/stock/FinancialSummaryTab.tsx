const API_BASE = '/api/backend';
import React, { useCallback, useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import styles from './FinancialSummaryTab.module.css';

interface ReportData {
  reportDate: string;
  reportName: string;
  revenue: number | null;
  revenueYoy: number | null;
  netProfit: number | null;
  netProfitYoy: number | null;
  deductNetProfit: number | null;
  deductNetProfitYoy: number | null;
  grossMargin: number | null;
  netMargin: number | null;
  roe: number | null;
  assetLiabilityRatio: number | null;
  operateCashFlow: number | null;
  eps: number | null;
}

interface FinanceResponse {
  source: string;
  fetchedAt: string;
  stockCode: string;
  stockName: string;
  latest: ReportData | null;
  yearly: ReportData[];
  quarterly: ReportData[];
}

export const FinancialSummaryTab: React.FC<{ stockCode: string }> = ({ stockCode }) => {
  const [data, setData] = useState<FinanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/stock/finance/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) {
        throw new Error('暂无真实财报数据或拉取失败');
      }
      const json = await res.json() as FinanceResponse;
      setData(json);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '获取财报失败');
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchData();
    }, 0);
    return () => clearTimeout(timer);
  }, [fetchData]);

  if (loading) {
    return (
      <div className={styles.loadingContainer}>
        <div className={styles.spinner}></div>
        <span>正在从真实接口抓取结构化财报...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className={styles.errorContainer}>
        <div>数据抓取失败: {error}</div>
        <button onClick={fetchData} className={styles.retryBtn}>重试</button>
      </div>
    );
  }

  const { latest, yearly, quarterly } = data;
  const normalizedCode = stockCode.toLowerCase().replace(/^hk/, '');
  const isHongKong = stockCode.toLowerCase().startsWith('hk') || normalizedCode.length === 5;

  if (!latest) {
    return <div className={styles.infoContainer}>暂无最新财报数据</div>;
  }

  // 格式化金额 (亿)
  const formatMoney = (val?: number | null) => {
    if (val === undefined || val === null) return '-';
    return (val / 100000000).toFixed(2) + ' 亿';
  };

  const formatOperatingCashFlow = (val?: number | null) => {
    if (val === undefined || val === null) return '暂无数据';
    return formatMoney(val);
  };

  // 格式化百分比
  const formatPct = (val?: number | null) => {
    if (val === undefined || val === null) return '-';
    return val.toFixed(2) + '%';
  };

  const renderYoy = (val?: number | null) => {
    if (val === undefined || val === null) return <span>-</span>;
    if (val === 0) return <span>0.00%</span>;
    const isUp = val > 0;
    return (
      <span className={isUp ? styles.textRise : styles.textFall}>
        {isUp ? '↑' : '↓'} {Math.abs(val).toFixed(2)}%
      </span>
    );
  };

  const radarMetrics = [
    latest.roe,
    latest.grossMargin,
    latest.netMargin,
    latest.assetLiabilityRatio,
    latest.revenueYoy,
  ];
  const hasCompleteRadarData = radarMetrics.every(
    (value): value is number => value !== null && value !== undefined,
  );
  const radarValues = hasCompleteRadarData
    ? [
        Math.max(0, Math.min(latest.roe as number, 30)),
        Math.max(0, Math.min(latest.grossMargin as number, 60)),
        Math.max(0, Math.min(latest.netMargin as number, 40)),
        Math.max(0, 100 - (latest.assetLiabilityRatio as number)),
        Math.max(0, Math.min(latest.revenueYoy as number, 50)),
      ]
    : [];

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h3 className={styles.title}>核心财务指标 ({latest.reportName})</h3>
        <div className={styles.meta}>
          <span>数据来源: {data.source}</span>
          <span>更新时间: {data.fetchedAt}</span>
          <button onClick={fetchData} className={styles.refreshBtn}>刷新</button>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.card}>
          <div className={styles.cardTitle}>营业收入</div>
          <div className={styles.cardValue}>{formatMoney(latest.revenue)}</div>
          <div className={styles.cardSub}>同比: {renderYoy(latest.revenueYoy)}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardTitle}>归母净利润</div>
          <div className={styles.cardValue}>{formatMoney(latest.netProfit)}</div>
          <div className={styles.cardSub}>同比: {renderYoy(latest.netProfitYoy)}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardTitle}>{isHongKong ? '扣非净利润（港股口径）' : '扣非净利润'}</div>
          <div className={styles.cardValue}>{isHongKong ? '不适用' : formatMoney(latest.deductNetProfit)}</div>
          <div className={styles.cardSub}>
            {isHongKong ? '港股财报通常不强制披露' : <>同比: {renderYoy(latest.deductNetProfitYoy)}</>}
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardTitle}>净资产收益率</div>
          <div className={styles.cardValue}>{formatPct(latest.roe)}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardTitle}>资产负债率</div>
          <div className={styles.cardValue}>{formatPct(latest.assetLiabilityRatio)}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardTitle}>经营现金流净额</div>
          <div className={styles.cardValue}>{formatOperatingCashFlow(latest.operateCashFlow)}</div>
        </div>
      </div>

      <div className={styles.chartRow}>
        <div className={styles.chartCard}>
          <ReactECharts option={{
            title: { text: '营业收入与净利润趋势', textStyle: { fontSize: 14, fontWeight: 'normal' }, left: 'center' },
            tooltip: { trigger: 'axis' },
            legend: { data: ['营业收入(亿)', '归母净利润(亿)'], bottom: 0 },
            grid: { left: '3%', right: '4%', bottom: '15%', containLabel: true },
            xAxis: { type: 'category', data: [...yearly].reverse().map(item => item.reportName) },
            yAxis: [
              { type: 'value', name: '营收' },
              { type: 'value', name: '利润', splitLine: { show: false } }
            ],
            series: [
              { name: '营业收入(亿)', type: 'bar', data: [...yearly].reverse().map(item => item.revenue == null ? null : Number((item.revenue / 100000000).toFixed(2))), itemStyle: { color: '#3b82f6' } },
              { name: '归母净利润(亿)', type: 'line', yAxisIndex: 1, data: [...yearly].reverse().map(item => item.netProfit == null ? null : Number((item.netProfit / 100000000).toFixed(2))), itemStyle: { color: '#10b981' }, smooth: true }
            ]
          }} style={{ height: '300px' }} />
        </div>
        <div className={styles.chartCard}>
          {hasCompleteRadarData ? <>
            <ReactECharts option={{
            title: { text: '杜邦分析核心能力矩阵', textStyle: { fontSize: 14, fontWeight: 'normal' }, left: 'center' },
            tooltip: {},
            radar: {
              indicator: [
                { name: '净资产收益率', max: 30 },
                { name: '毛利率', max: 60 },
                { name: '净利率', max: 40 },
                { name: '健康度 (反向负债率)', max: 100 },
                { name: '成长性 (营收增速)', max: 50 },
              ],
              center: ['50%', '55%'],
              radius: '60%'
            },
            series: [{
              name: '财务能力',
              type: 'radar',
              data: [
                {
                  value: radarValues,
                  name: '最新一期',
                  areaStyle: { color: 'rgba(59, 130, 246, 0.2)' },
                  lineStyle: { color: '#3b82f6' },
                  itemStyle: { color: '#3b82f6' }
                }
              ]
            }]
            }} style={{ height: '300px' }} />
            <div className={styles.meta}>
              雷达图按固定范围展示，超出范围会截断；实际数值以表格为准，不生成综合评分。
            </div>
          </> : (
            <div className={styles.infoContainer} style={{ minHeight: '300px' }}>
              杜邦分析数据不完整，缺失指标不按0计算；请以已披露财务表格为准。
            </div>
          )}
        </div>
      </div>

      <h4 className={styles.sectionTitle}>最近年度财报趋势 (近3-4年)</h4>
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>报告期</th>
              <th>营业收入</th>
              <th>净利润</th>
              <th>{isHongKong ? '扣非净利润（港股口径）' : '扣非净利润'}</th>
              <th>毛利率</th>
              <th>净利率</th>
              <th>净资产收益率</th>
              <th>资产负债率</th>
              <th>经营现金流</th>
              <th>EPS</th>
            </tr>
          </thead>
          <tbody>
            {yearly.map((row, idx) => (
              <tr key={idx}>
                <td>{row.reportName}</td>
                <td>{formatMoney(row.revenue)}</td>
                <td>{formatMoney(row.netProfit)}</td>
                <td>{isHongKong ? '不适用' : formatMoney(row.deductNetProfit)}</td>
                <td>{formatPct(row.grossMargin)}</td>
                <td>{formatPct(row.netMargin)}</td>
                <td>{formatPct(row.roe)}</td>
                <td>{formatPct(row.assetLiabilityRatio)}</td>
                <td>{formatOperatingCashFlow(row.operateCashFlow)}</td>
                <td>{row.eps !== null && row.eps !== undefined ? row.eps : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 className={styles.sectionTitle}>最近报告期数据 (近8个季度/半年度)</h4>
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>报告期</th>
              <th>营业收入</th>
              <th>净利润</th>
              <th>营收同比</th>
              <th>净利润同比</th>
              <th>毛利率</th>
              <th>净资产收益率</th>
              <th>资产负债率</th>
            </tr>
          </thead>
          <tbody>
            {quarterly.map((row, idx) => (
              <tr key={idx}>
                <td>{row.reportName}</td>
                <td>{formatMoney(row.revenue)}</td>
                <td>{formatMoney(row.netProfit)}</td>
                <td>{renderYoy(row.revenueYoy)}</td>
                <td>{renderYoy(row.netProfitYoy)}</td>
                <td>{formatPct(row.grossMargin)}</td>
                <td>{formatPct(row.roe)}</td>
                <td>{formatPct(row.assetLiabilityRatio)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
