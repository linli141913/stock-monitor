'use client';

const API_BASE = '/api/backend';

import { Fragment, useState, useEffect, useCallback } from 'react';
import { BrainCircuit } from 'lucide-react';
import styles from './AiAttributionTab.module.css';

interface EvidenceChain {
  technicalAndSentiment?: string;
  fundFactor?: string;
  fundamentalAndNews?: string;
  sectorAndMacro?: string;
}

interface AttributionSource {
  sourceId: string;
  title: string;
  source: string;
  url: string;
  time?: string | null;
  evidenceLevel?: string | null;
}

interface EvidenceCompleteness {
  available?: string[];
  missing?: string[];
  availableCount?: number;
  totalCount?: number;
  label?: string;
}

interface MarketDimension {
  key: string;
  label: string;
  state: string;
  summary: string;
  details?: Record<string, unknown>;
}

interface MarketView {
  overallState: string;
  structureLabel: string;
  dimensions: MarketDimension[];
  improvingConditions: string[];
  continuingConditions: string[];
  worseningConditions: string[];
  confirmedCount: number;
  unavailableCount: number;
}

interface AttributionData {
  stockName: string;
  stockCode: string;
  changePercent: number | null;
  evidenceChain: EvidenceChain;
  scenarioAnalysis?: string;
  futureTrendPrediction?: string;
  plainEnglishSummary?: string;
  aiJudgment: string;
  credibility: string;
  riskNotice: string;
  sourceTime?: string | null;
  sourceDate?: string | null;
  analysisAt?: string | null;
  resultReused?: boolean;
  analysisStatus?: string | null;
  sources?: AttributionSource[];
  sourceIds?: string[];
  confirmedFacts?: string[];
  inferences?: string[];
  unknowns?: string[];
  evidenceCompleteness?: EvidenceCompleteness | null;
  reuseReason?: string | null;
  recheckedAt?: string | null;
  reuseMessage?: string | null;
  checkedDimensions?: number | null;
  marketView?: MarketView | null;
  promptVersion?: string | null;
}

interface HistoryItem {
  date?: string;
  target_date?: string;
  time: string;
  timestamp?: string;
  trigger_type: string;
  plain_english_summary: string;
  full_json: AttributionData;
  period_bounds?: { start: string; end: string } | null;
}

function triggerLabel(triggerType: string) {
  if (triggerType.startsWith('event:')) return '事件触发';
  if (triggerType.startsWith('auto:')) return '定时复盘';
  return '手动生成';
}

function safeSourceUrl(value?: string) {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? value : null;
  } catch {
    return null;
  }
}

function localizeAnalysisText(text?: string) {
  const replacements: Array<[RegExp, string]> = [
    [/\bunavailable\b/gi, '暂无可靠数据'],
    [/\binsufficient\b/gi, '样本不足'],
    [/\bnot_applicable\b/gi, '不适用'],
    [/\bno_signal\b/gi, '未触发'],
    [/\bwarning\b/gi, '警惕'],
    [/\bnegative\b/gi, '负向风险'],
    [/\bpositive\b/gi, '正向信号'],
    [/\bneutral\b/gi, '中性'],
    [/\buncertain\b/gi, '暂无法确认'],
    [/\bavailable\b/gi, '数据可用'],
    [/\btriggered\b/gi, '已触发'],
    [/\bnull\b/gi, '本轮没有对应事件'],
    [/\bMA5\b/g, '5日均线'],
    [/\bMA10\b/g, '10日均线'],
    [/\bMA20\b/g, '20日均线'],
    [/\bROE\b/gi, '净资产收益率'],
  ];
  return replacements.reduce(
    (result, [pattern, value]) => result.replace(pattern, value),
    text || '暂无判断',
  );
}

function renderSafeText(text?: string) {
  const normalized = localizeAnalysisText(text)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/\\n/g, '\n');
  return normalized.split('\n').map((line, index) => (
    <Fragment key={`${index}-${line.slice(0, 12)}`}>
      {index > 0 && <br />}
      {line}
    </Fragment>
  ));
}

function formatMetric(value: unknown, suffix = '') {
  return typeof value === 'number' ? `${value.toFixed(2)}${suffix}` : '暂无数据';
}

function renderMarketDimensionDetails(dimension: MarketDimension) {
  const details = dimension.details || {};
  if (dimension.key === 'priceVolume') {
    const rules = Array.isArray(details.triggeredRules) ? details.triggeredRules as string[] : [];
    return (
      <div className={styles.dimensionEvidence}>
        <span>涨跌幅 <strong>{formatMetric(details.changePercent, '%')}</strong></span>
        <span>量比 <strong>{formatMetric(details.volumeRatio)}</strong></span>
        <span>触发规则 <strong>{rules.length ? rules.join('、') : '无'}</strong></span>
      </div>
    );
  }
  if (dimension.key === 'continuousFund') {
    return <div className={styles.dimensionBasis}>验证口径：最近3个交易日的真实主力资金与收盘价必须全部完整。</div>;
  }
  if (dimension.key === 'movingAverage') {
    const periods = Array.isArray(details.periods) ? details.periods as string[] : [];
    return <div className={styles.dimensionBasis}>可验证破位：前一交易日收盘价在均线上方，当日收盘价跌到均线下方。{periods.length ? ` 当前涉及：${periods.join('、')}` : ''}</div>;
  }
  if (dimension.key === 'breadth') {
    return (
      <div className={styles.dimensionEvidence}>
        <span>上涨 <strong>{String(details.advancers ?? '-')} 只</strong> / 共 <strong>{String(details.total ?? '-')} 只</strong></span>
        <span>占比 <strong>{formatMetric(details.ratioPercent, '%')}</strong></span>
        <span>触发阈值 <strong>低于 {String(details.triggerThresholdPercent ?? 20)}%</strong></span>
      </div>
    );
  }
  if (dimension.key === 'leader') {
    const leaders = Array.isArray(details.leaders) ? details.leaders as Array<Record<string, unknown>> : [];
    return (
      <div className={styles.marketLeaderList}>
        <div className={styles.dimensionBasis}>选择口径：按本轮完整成分行情中的总市值从高到低排序。</div>
        {leaders.map((leader, index) => (
          <div key={`${String(leader.symbol || '')}-${index}`} className={styles.marketLeaderRow}>
            <span>{String(leader.rank || index + 1)}</span>
            <strong>{String(leader.name || '名称暂缺')} <small>{String(leader.symbol || '')}</small></strong>
            <em>{formatMetric(leader.change_percent, '%')}</em>
          </div>
        ))}
      </div>
    );
  }
  if (dimension.key === 'fundFlow') {
    return (
      <div className={styles.dimensionEvidence}>
        <span>方向 <strong>{details.direction === 'inflow' ? '净流入' : details.direction === 'outflow' ? '净流出' : '暂无判断'}</strong></span>
        <span>同方向板块中 <strong>第 {String(details.rank ?? '-')} / {String(details.total ?? '-')} 名</strong></span>
        <span>触发阈值 <strong>前 {String(details.triggerRank ?? 5)}</strong></span>
      </div>
    );
  }
  return null;
}

function getMarketStateClass(state?: string) {
  if (state === '风险升高' || state === '偏弱') return styles.marketStateRisk;
  if (state === '偏强') return styles.marketStatePositive;
  if (state === '暂无判断') return styles.marketStateUnavailable;
  return styles.marketStateNeutral;
}

export default function AiAttributionTab({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<AttributionData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState('正在初始化智能分析引擎...');
  const [error, setError] = useState('');
  const [analysisAt, setAnalysisAt] = useState<string | null>(null);
  const [historyList, setHistoryList] = useState<HistoryItem[]>([]);
  const [allHistoryList, setAllHistoryList] = useState<HistoryItem[]>([]);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [fetchingAllHistory, setFetchingAllHistory] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [selectedHistoryItem, setSelectedHistoryItem] = useState<HistoryItem | null>(null);
  const [bounds, setBounds] = useState<{ start: string; end: string } | null>(null);
  const [calendarStatus, setCalendarStatus] = useState<'available' | 'unknown'>('available');
  
  const [calendarYear, setCalendarYear] = useState<number>(() => new Date().getFullYear());
  const [calendarMonth, setCalendarMonth] = useState<number>(() => new Date().getMonth());
  const [selectedDateStr, setSelectedDateStr] = useState<string>('');

  const getDaysInMonthGrid = (year: number, month: number) => {
    const firstDayIndex = new Date(year, month, 1).getDay();
    const totalDays = new Date(year, month + 1, 0).getDate();
    const prevMonthTotalDays = new Date(year, month, 0).getDate();
    
    const days: { dateStr: string; dayNum: number; isCurrentMonth: boolean }[] = [];
    
    for (let i = firstDayIndex - 1; i >= 0; i--) {
      const prevDay = prevMonthTotalDays - i;
      const prevMonth = month === 0 ? 11 : month - 1;
      const prevYear = month === 0 ? year - 1 : year;
      days.push({
        dateStr: `${prevYear}-${String(prevMonth + 1).padStart(2, '0')}-${String(prevDay).padStart(2, '0')}`,
        dayNum: prevDay,
        isCurrentMonth: false
      });
    }
    
    for (let i = 1; i <= totalDays; i++) {
      days.push({
        dateStr: `${year}-${String(month + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}`,
        dayNum: i,
        isCurrentMonth: true
      });
    }
    
    const totalGridCells = 42;
    const remainingCells = totalGridCells - days.length;
    for (let i = 1; i <= remainingCells; i++) {
      const nextMonth = month === 11 ? 0 : month + 1;
      const nextYear = month === 11 ? year + 1 : year;
      days.push({
        dateStr: `${nextYear}-${String(nextMonth + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}`,
        dayNum: i,
        isCurrentMonth: false
      });
    }
    
    return days;
  };

  const formatBoundsDate = (dateStr?: string) => {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return '';
      
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const date = String(d.getDate()).padStart(2, '0');
      const hours = String(d.getHours()).padStart(2, '0');
      const minutes = String(d.getMinutes()).padStart(2, '0');
      
      const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
      const weekday = weekdays[d.getDay()];
      
      return `${month}-${date} (${weekday}) ${hours}:${minutes}`;
    } catch {
      return '';
    }
  };

  const fetchHistory = useCallback(async (selectLatest = true) => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_history/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res.ok) {
        const json = await res.json() as {
          data?: HistoryItem[];
          bounds?: { start: string; end: string } | null;
          calendarStatus?: 'available' | 'unknown';
        };
        const currentItems = json.data || [];
        setHistoryList(currentItems);
        setBounds(json.bounds || null);
        setCalendarStatus(json.calendarStatus || 'unknown');
        if (currentItems.length > 0) {
          if (selectLatest) {
            const latestCurrentItem = currentItems[currentItems.length - 1];
            setData(latestCurrentItem.full_json);
            setAnalysisAt(latestCurrentItem.full_json.analysisAt || null);
            setSelectedHistoryItem(null);
          }
        } else {
          const allResponse = await fetch(
            `${API_BASE}/api/stock/ai_history_all/${stockCode}`,
            { headers: { 'ngrok-skip-browser-warning': 'true' } },
          );
          if (allResponse.ok) {
            const allPayload = await allResponse.json() as { data?: HistoryItem[] };
            const latest = (allPayload.data || [])[0];
            if (latest) {
              setData(latest.full_json);
              setAnalysisAt(latest.full_json.analysisAt || null);
              setSelectedHistoryItem(latest);
            }
          }
        }
      }
    } catch (err) {
      setBounds(null);
      setCalendarStatus('unknown');
      console.error('获取历史记录失败', err);
    }
  }, [stockCode]);

  const fetchAllHistory = async () => {
    setFetchingAllHistory(true);
    setHistoryError('');
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_history_all/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) {
        throw new Error(`历史记录接口返回 ${res.status}`);
      }

      const json = await res.json() as { data?: HistoryItem[] };
      const historyItems = json.data || [];
      setAllHistoryList(historyItems);
      if (!data && historyItems.length > 0) {
        setData(historyItems[0].full_json);
        setAnalysisAt(historyItems[0].full_json.analysisAt || null);
        setSelectedHistoryItem(historyItems[0]);
      }

      const targetDate = historyItems.find((item) => item.target_date)?.target_date;
      const today = new Date();
      const fallbackDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      const selectedDate = targetDate || fallbackDate;
      const parts = selectedDate.split('-');
      if (parts.length === 3) {
        setCalendarYear(parseInt(parts[0]));
        setCalendarMonth(parseInt(parts[1]) - 1);
        setSelectedDateStr(selectedDate);
      }
    } catch (err) {
      setAllHistoryList([]);
      setHistoryError('历史记录加载失败，请检查服务连接后重试。');
      console.error('获取所有历史失败', err);
    } finally {
      setFetchingAllHistory(false);
    }
  };

  const openHistory = () => {
    setShowHistoryModal(true);
    void fetchAllHistory();
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchHistory();
    }, 0);
    return () => clearTimeout(timer);
  }, [fetchHistory]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (loading) {
      const messages = [
        '正在获取腾讯财经最新可追溯行情快照...',
        '正在筛选来源日期为当天的可追溯资讯...',
        '正在核对公司、行业、ETF或港股对应信息...',
        '证据已拼装，正在进行影响与风险分析...'
      ];
      let i = 0;
      interval = setInterval(() => {
        i++;
        if (i < messages.length) {
          setLoadingText(messages[i]);
        }
      }, 2500);
    }
    return () => clearInterval(interval);
  }, [loading]);

  const fetchAttribution = async () => {
    setLoading(true);
    setError('');
    setLoadingText('正在初始化智能分析引擎...');
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_attribution/${stockCode}?trigger=manual`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('无法获取归因分析数据');
      const json = await res.json() as AttributionData;
      setData(json);
      setAnalysisAt(json.analysisAt || null);
      setSelectedHistoryItem(null);
      void fetchHistory(false); // 只刷新时间轴，保留本次复核结果
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '网络异常');
    } finally {
      setLoading(false);
    }
  };

  const handleTimelineClick = (item: HistoryItem, isHistorical = false) => {
    setData(item.full_json);
    setAnalysisAt(item.full_json.analysisAt || null);
    setSelectedHistoryItem(isHistorical ? item : null);
  };

  const returnToToday = () => {
    const latestCurrentItem = historyList[historyList.length - 1];
    const latestData = latestCurrentItem?.full_json || null;
    setData(latestData);
    setAnalysisAt(latestData?.analysisAt || null);
    setSelectedHistoryItem(null);
  };

  // 移除了初始挂载时的自动请求，改为完全手动触发

  if (loading) return (
    <div className={styles.loadingContainer}>
      <div className={styles.spinner}></div>
      <div className={styles.loadingText}>{loadingText}</div>
    </div>
  );
  if (error) return <div className={styles.error}>{error}</div>;

  if (!data && historyList.length === 0) return (
    <div className={styles.emptyContainer}>
      <div className={styles.emptyIcon}>
        <BrainCircuit size={64} color="#1890ff" strokeWidth={1.5} />
      </div>
      <div className={styles.emptyText}>当前交易周期暂无自动追踪记录。点击下方按钮，可基于最新可追溯行情快照生成分析。</div>
      <div className={styles.emptyActions}>
        <button className={styles.refreshBtn} onClick={fetchAttribution} aria-label="生成事件与风险解释">
          重新核对最新数据并分析
        </button>
        <button className={styles.historyBtn} onClick={openHistory} disabled={fetchingAllHistory}>
          {fetchingAllHistory ? '加载历史...' : '查看历史记录'}
        </button>
      </div>
      {historyError && <div className={styles.historyError}>{historyError}</div>}
    </div>
  );

  const isUp = data?.changePercent != null && data.changePercent > 0;
  const isDown = data?.changePercent != null && data.changePercent < 0;

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.stockName}>{data ? data.stockName : stockCode}</span>
          {data && (
            <span className={`${styles.stockChange} ${isUp ? styles.textRise : isDown ? styles.textFall : ''}`}>
              {data.sourceDate ? `${data.sourceDate}涨跌幅` : '来源日期未知的涨跌幅'}：{data.changePercent == null ? '暂无数据' : `${isUp ? '+' : ''}${data.changePercent}%`}
            </span>
          )}
          <div className={styles.badge}>事件与风险解释</div>
          {selectedHistoryItem && (
            <div
              className={styles.historyPeriod}
              title={selectedHistoryItem.period_bounds
                ? `历史复盘：${formatBoundsDate(selectedHistoryItem.period_bounds.start)} 至 ${formatBoundsDate(selectedHistoryItem.period_bounds.end)}`
                : '历史复盘周期暂不可用'}
            >
              <span className={styles.historyPeriodLabel}>最近一次历史解释</span>
              <span>
                {selectedHistoryItem.period_bounds
                  ? `${formatBoundsDate(selectedHistoryItem.period_bounds.start).replace(/ \([^)]+\)/, '').replace('-', '/')}–${formatBoundsDate(selectedHistoryItem.period_bounds.end).replace(/ \([^)]+\)/, '').replace('-', '/')}`
                  : `${selectedHistoryItem.target_date || selectedHistoryItem.date || '日期暂缺'} ${selectedHistoryItem.time}`}
              </span>
              <button type="button" onClick={returnToToday} aria-label="返回今日视图" title="返回今日视图">
                ×
              </button>
            </div>
          )}
        </div>
        <div className={styles.headerRight}>
          {data?.sourceTime && <span className={styles.lastUpdated}>行情源 {data.sourceTime}</span>}
          {analysisAt && <span className={styles.lastUpdated}>分析于 {analysisAt.replace('T', ' ').slice(0, 19)}</span>}
          {data?.resultReused && (
            <span className={styles.lastUpdated}>
              {data.reuseReason === 'evidence_unchanged'
                ? '已重新核对，证据未变化'
                : '已核对历史解释'}
            </span>
          )}
          <button className={styles.refreshBtn} onClick={fetchAttribution} disabled={loading} aria-label="生成事件与风险解释">
            重新核对最新数据并分析
          </button>
        </div>
      </div>

      {historyList.length > 0 && !selectedHistoryItem && (
        <div className={styles.timelineContainer} style={{ padding: '16px', background: '#f8fafc', borderRadius: '8px', marginBottom: '16px', border: '1px solid #e2e8f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 'bold', color: '#1e293b' }}>当前交易周期追踪时间轴</span>
              {bounds && (
                <span style={{ fontSize: '0.8rem', color: '#475569', background: '#f1f5f9', padding: '2px 8px', borderRadius: '4px', border: '1px solid #e2e8f0', fontWeight: 500 }}>
                  分析跨度: {formatBoundsDate(bounds.start)} ~ {formatBoundsDate(bounds.end)}
                </span>
              )}
            </div>
            <button 
              onClick={openHistory}
              style={{ fontSize: '0.85rem', color: '#2563eb', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
            >
              查看更早历史
            </button>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {historyList.map((item, idx) => (
              <div 
                key={idx} 
                onClick={() => handleTimelineClick(item)}
                style={{
                  padding: '8px 12px',
                  borderRadius: '6px',
                  background: '#ffffff',
                  border: '1px solid #cbd5e1',
                  cursor: 'pointer',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
                  fontSize: '0.9rem',
                  display: 'flex',
                  flexDirection: 'column',
                  minWidth: '140px'
                }}
              >
                <div style={{ color: '#64748b', fontSize: '0.8rem', marginBottom: '4px', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>{item.time} ({triggerLabel(item.trigger_type)})</span>
                </div>
                <div style={{ color: '#0f172a', fontWeight: 500, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {localizeAnalysisText(item.plain_english_summary)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {calendarStatus === 'unknown' && (
        <div style={{ padding: '12px 16px', marginBottom: '16px', borderRadius: '8px', background: '#fff7ed', border: '1px solid #fed7aa', color: '#9a3412', fontSize: '0.9rem' }}>
          交易日历暂不可用，无法按交易周期归组。
        </div>
      )}
      
      {!data && historyList.length > 0 && (
        <div className={styles.emptyContainer} style={{ marginTop: '32px' }}>
          <div className={styles.emptyIcon} style={{ marginBottom: '16px' }}>
            <BrainCircuit size={48} color="#64748b" strokeWidth={1.5} />
          </div>
          <div className={styles.emptyText}>请点击上方时间轴上的节点，查看该时刻的历史分析报告；或点击右上角按钮进行最新分析。</div>
        </div>
      )}

      {data?.resultReused && data.reuseMessage && (
        <div className={styles.recheckNotice}>
          <strong>本次已重新核对</strong>
          <span>{localizeAnalysisText(data.reuseMessage)}</span>
          <small>原分析时间：{data.analysisAt?.replace('T', ' ').slice(0, 19) || '暂缺'} · 本次复核时间：{data.recheckedAt?.replace('T', ' ').slice(0, 19) || '暂缺'}</small>
        </div>
      )}

      {data && data.promptVersion !== 'evidence-v3' && (
        <div className={styles.legacyNotice}>
          <strong>旧版历史解释已停止展示详细结论</strong>
          <span>该记录生成于证据化规则上线前，可能包含未经当前来源校验的推断。原始记录仍保留用于审计，请生成一次新的事件与风险解释。</span>
          <button type="button" className={styles.refreshBtn} onClick={fetchAttribution}>
            生成新版解释
          </button>
        </div>
      )}

      {data && data.promptVersion === 'evidence-v3' && (
        <>
      {data.marketView && (
        <section className={styles.marketViewCard}>
          <div className={styles.marketViewHeader}>
            <div>
              <span className={styles.marketViewEyebrow}>当前市场结构</span>
              <div className={styles.marketViewTitleRow}>
                <strong className={`${styles.marketState} ${getMarketStateClass(data.marketView.overallState)}`}>
                  {data.marketView.overallState}
                </strong>
                <span>{data.marketView.structureLabel}</span>
              </div>
            </div>
            <div className={styles.evidenceCounter}>
              <strong>{data.marketView.confirmedCount}</strong>
              <span>/ 7 项已核对</span>
            </div>
          </div>

          <div className={styles.marketDimensionTitle}>七项监测证据</div>
          <div className={styles.marketDimensionGrid}>
            {data.marketView.dimensions.map((dimension) => (
              <article key={dimension.key} className={styles.marketDimensionCard}>
                <div className={styles.marketDimensionHeader}>
                  <strong>{dimension.label}</strong>
                  <span className={dimension.state === '暂无判断' ? styles.dimensionStateUnavailable : styles.dimensionState}>{dimension.state}</span>
                </div>
                <p>{localizeAnalysisText(dimension.summary)}</p>
                {renderMarketDimensionDetails(dimension)}
              </article>
            ))}
          </div>

          <div className={styles.conditionGrid}>
            <div className={styles.conditionImprove}>
              <strong>风险缓和需要看到</strong>
              {data.marketView.improvingConditions.map((item, index) => <p key={index}>{localizeAnalysisText(item)}</p>)}
            </div>
            <div className={styles.conditionContinue}>
              <strong>当前结构延续</strong>
              {data.marketView.continuingConditions.map((item, index) => <p key={index}>{localizeAnalysisText(item)}</p>)}
            </div>
            <div className={styles.conditionWorsen}>
              <strong>风险进一步确认</strong>
              {data.marketView.worseningConditions.map((item, index) => <p key={index}>{localizeAnalysisText(item)}</p>)}
            </div>
          </div>
        </section>
      )}

      <div className={styles.sectionTitle}>事件与风险解释</div>
      <details className={styles.analysisDetails}>
        <summary>查看完整证据、事件解释和暂无法确认的内容</summary>
      <div className={styles.evidenceList}>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>1. 量价与情绪面</div>
          <div className={styles.evidenceText}>{renderSafeText(data.evidenceChain?.technicalAndSentiment)}</div>
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>2. 资金数据与缺口</div>
          <div className={styles.evidenceText}>{renderSafeText(data.evidenceChain?.fundFactor)}</div>
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>3. 基本面与资讯</div>
          <div className={styles.evidenceText}>{renderSafeText(data.evidenceChain?.fundamentalAndNews)}</div>
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>4. 板块与海外映射</div>
          <div className={styles.evidenceText}>{renderSafeText(data.evidenceChain?.sectorAndMacro)}</div>
        </div>
      </div>

      {(data.confirmedFacts?.length || data.inferences?.length || data.unknowns?.length) ? (
        <div className={styles.factGrid}>
          <div className={styles.factColumn}>
            <strong>已确认事实</strong>
            {(data.confirmedFacts || []).map((item, index) => <p key={index}>{renderSafeText(item)}</p>)}
          </div>
          <div className={styles.factColumn}>
            <strong>基于事实的推断</strong>
            {(data.inferences || []).map((item, index) => <p key={index}>{renderSafeText(item)}</p>)}
          </div>
          <div className={styles.factColumn}>
            <strong>暂无法确认</strong>
            {(data.unknowns || []).map((item, index) => <p key={index}>{renderSafeText(item)}</p>)}
          </div>
        </div>
      ) : null}

      {data.scenarioAnalysis && (
        <div className={styles.scenarioCard}>
          <div className={styles.scenarioTitle}>条件情景分析（非预测）</div>
          <div className={styles.scenarioText}>
            {renderSafeText(data.scenarioAnalysis)}
          </div>
        </div>
      )}
      </details>

      {data.plainEnglishSummary && (
        <div className={styles.plainEnglishCard} style={{ marginTop: '16px', padding: '16px', borderRadius: '8px', border: '2px solid #1890ff', background: '#e6f4ff', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ fontSize: '24px' }}>💡</div>
          <div>
            <div style={{ fontWeight: 'bold', color: '#0958d9', marginBottom: '4px', fontSize: '0.9rem' }}>通俗总结</div>
            <div style={{ color: '#1677ff', fontSize: '1.2rem', fontWeight: 600 }}>
              {localizeAnalysisText(data.plainEnglishSummary?.replace(/^【[^】]+】\s*/, ''))}
            </div>
          </div>
        </div>
      )}

      <div className={styles.judgmentCard} style={{ marginTop: '16px' }}>
        <div className={styles.judgmentHeader}>
          <span className={styles.judgmentTitle}>模型解释（非事实结论）</span>
          <span className={styles.credibilityTag}>证据完整度：{data.credibility}</span>
        </div>
        <div className={styles.judgmentText}>{renderSafeText(data.aiJudgment)}</div>
      </div>

      <div className={styles.riskCard}>
        <div className={styles.riskTitle}>风险提示</div>
        <div className={styles.riskText}>{renderSafeText(data.riskNotice)}</div>
      </div>

      {data.sources && data.sources.length > 0 && (
        <div className={styles.sourceCard}>
          <div className={styles.sourceTitle}>本次实际引用来源</div>
          <div className={styles.sourceList}>
            {data.sources.map((source) => {
              const url = safeSourceUrl(source.url);
              return (
                <div key={source.sourceId} className={styles.sourceItem}>
                  <span>{source.sourceId}</span>
                  <div>
                    <strong>{source.title}</strong>
                    <small>{source.source} · {source.time || '发布时间暂缺'} · 证据 {source.evidenceLevel || '待核验'}</small>
                  </div>
                  {url ? <a href={url} target="_blank" rel="noreferrer">查看原文</a> : <em>链接不可用</em>}
                </div>
              );
            })}
          </div>
        </div>
      )}
      </>
      )}
      
      {showHistoryModal && (
        <div 
          onClick={() => setShowHistoryModal(false)}
          style={{ 
            position: 'fixed', 
            top: 0, 
            left: 0, 
            right: 0, 
            bottom: 0, 
            background: 'rgba(15, 23, 42, 0.4)', 
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
            zIndex: 999, 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            padding: '16px' 
          }}
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            style={{ 
              background: 'rgba(255, 255, 255, 0.65)', 
              backdropFilter: 'blur(20px) saturate(190%)', 
              WebkitBackdropFilter: 'blur(20px) saturate(190%)',
              width: '100%', 
              maxWidth: '850px', 
              maxHeight: '90vh', 
              borderRadius: '20px', 
              display: 'flex', 
              flexDirection: 'column', 
              boxShadow: '0 30px 60px -15px rgba(15, 23, 42, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.6)', 
              border: '1px solid rgba(255, 255, 255, 0.4)',
              overflow: 'hidden' 
            }}
          >
            <div style={{ padding: '16px 20px', borderBottom: '1px solid rgba(255, 255, 255, 0.25)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255, 255, 255, 0.15)' }}>
              <h3 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 600, color: '#0f172a' }}>{stockCode} 历史深度复盘记录</h3>
              <button onClick={() => setShowHistoryModal(false)} style={{ background: 'none', border: 'none', fontSize: '1.5rem', cursor: 'pointer', color: '#475569' }}>&times;</button>
            </div>
            
            {fetchingAllHistory ? (
              <div style={{ textAlign: 'center', color: '#64748b', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>加载中...</div>
            ) : historyError ? (
              <div style={{ textAlign: 'center', color: '#b91c1c', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{historyError}</div>
            ) : allHistoryList.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#64748b', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>暂无历史记录</div>
            ) : (
              <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: '450px', background: 'transparent' }}>
                
                {/* Left Column: Calendar */}
                <div style={{ width: '380px', borderRight: '1px solid rgba(255, 255, 255, 0.3)', padding: '20px', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'transparent' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <button 
                      onClick={() => {
                        if (calendarMonth === 0) {
                          setCalendarMonth(11);
                          setCalendarYear(prev => prev - 1);
                        } else {
                          setCalendarMonth(prev => prev - 1);
                        }
                      }}
                      style={{ padding: '6px 10px', background: 'rgba(255, 255, 255, 0.4)', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold', color: '#334155' }}
                    >
                      ◀
                    </button>
                    <div style={{ fontWeight: 600, color: '#1e293b', fontSize: '1.05rem' }}>
                      {calendarYear}年 {calendarMonth + 1}月
                    </div>
                    <button 
                      onClick={() => {
                        if (calendarMonth === 11) {
                          setCalendarMonth(0);
                          setCalendarYear(prev => prev + 1);
                        } else {
                          setCalendarMonth(prev => prev + 1);
                        }
                      }}
                      style={{ padding: '6px 10px', background: 'rgba(255, 255, 255, 0.4)', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold', color: '#334155' }}
                    >
                      ▶
                    </button>
                  </div>
                  
                  {/* Weekday headers */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px', marginBottom: '8px', textAlign: 'center', fontWeight: 600, color: '#64748b', fontSize: '0.85rem' }}>
                    {['日', '一', '二', '三', '四', '五', '六'].map((day, i) => (
                      <div key={i}>{day}</div>
                    ))}
                  </div>
                  
                  {/* Days grid */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px', textAlign: 'center', flex: 1 }}>
                    {getDaysInMonthGrid(calendarYear, calendarMonth).map((cell, idx) => {
                      const isSelected = cell.dateStr === selectedDateStr;
                      const hasHistory = new Set(allHistoryList.map(item => item.target_date).filter(Boolean)).has(cell.dateStr);
                      return (
                        <div
                          key={idx}
                          onClick={() => {
                            setSelectedDateStr(cell.dateStr);
                            if (!cell.isCurrentMonth) {
                              const parts = cell.dateStr.split('-');
                              setCalendarYear(parseInt(parts[0]));
                              setCalendarMonth(parseInt(parts[1]) - 1);
                            }
                          }}
                          style={{
                            width: '38px',
                            height: '38px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            borderRadius: '50%',
                            cursor: 'pointer',
                            position: 'relative',
                            margin: 'auto',
                            fontSize: '0.9rem',
                            fontWeight: isSelected ? 'bold' : cell.isCurrentMonth ? 500 : 'normal',
                            background: isSelected ? '#2563eb' : 'transparent',
                            color: isSelected ? '#ffffff' : cell.isCurrentMonth ? '#334155' : '#cbd5e1',
                            boxShadow: isSelected ? '0 4px 10px rgba(37, 99, 235, 0.4)' : 'none',
                            transition: 'all 0.15s ease'
                          }}
                        >
                          {cell.dayNum}
                          {hasHistory && (
                            <span 
                              style={{ 
                                position: 'absolute', 
                                bottom: '4px', 
                                width: '4px', 
                                height: '4px', 
                                borderRadius: '50%', 
                                background: isSelected ? '#ffffff' : '#2563eb',
                                transition: 'background-color 0.15s'
                              }} 
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                
                {/* Right Column: Records */}
                <div style={{ flex: 1, padding: '20px', overflowY: 'auto', background: 'rgba(255, 255, 255, 0.25)', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.3)', paddingBottom: '12px', marginBottom: '16px' }}>
                    <div style={{ fontWeight: 600, color: '#0f172a', fontSize: '1.05rem' }}>
                      {(() => {
                        if (!selectedDateStr) return '';
                        try {
                          const d = new Date(selectedDateStr);
                          const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
                          return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 (${weekdays[d.getDay()]}) 交易周期`;
                        } catch {
                          return selectedDateStr;
                        }
                      })()}
                    </div>
                    <div style={{ color: '#475569', fontSize: '0.8rem', marginTop: '4px' }}>
                      {bounds && bounds.end.startsWith(selectedDateStr)
                        ? `当前周期：${formatBoundsDate(bounds.start)} 至 ${formatBoundsDate(bounds.end)}`
                        : '记录按后端返回的对应市场交易周期归组'}
                    </div>
                  </div>
                  
                  {allHistoryList.filter(item => item.target_date === selectedDateStr).length === 0 ? (
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#475569', fontSize: '0.9rem', textAlign: 'center', padding: '20px', border: '2px dashed rgba(255, 255, 255, 0.5)', borderRadius: '12px', background: 'rgba(255, 255, 255, 0.35)' }}>
                      该日期暂无历史深度复盘记录。<br/>请在左侧日历上选择带有蓝点标记的日期。
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      {allHistoryList
                        .filter(item => item.target_date === selectedDateStr)
                        .map((item, idx) => (
                          <div 
                            key={idx}
                            onClick={() => { handleTimelineClick(item, true); setShowHistoryModal(false); }}
                            style={{ 
                              padding: '16px', 
                              border: '1px solid rgba(255, 255, 255, 0.4)', 
                              borderRadius: '10px', 
                              cursor: 'pointer', 
                              background: 'rgba(255, 255, 255, 0.75)',
                              boxShadow: '0 4px 6px -1px rgba(15, 23, 42, 0.05)',
                              transition: 'all 0.2s ease',
                              borderLeft: item.trigger_type === 'manual' ? '4px solid #f59e0b' : '4px solid #3b82f6'
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.6)';
                              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.9)';
                              e.currentTarget.style.transform = 'translateY(-1.5px)';
                              e.currentTarget.style.boxShadow = '0 10px 15px -3px rgba(15, 23, 42, 0.08)';
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.4)';
                              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.75)';
                              e.currentTarget.style.transform = 'none';
                              e.currentTarget.style.boxShadow = '0 4px 6px -1px rgba(15, 23, 42, 0.05)';
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                              <span style={{ fontWeight: 600, color: '#1e293b', fontSize: '0.95rem', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
                                <span>{item.time} ({triggerLabel(item.trigger_type)})</span>
                              </span>
                              <span style={{ fontSize: '0.75rem', padding: '2px 8px', borderRadius: '4px', background: item.trigger_type === 'manual' ? '#fffbeb' : '#eff6ff', color: item.trigger_type === 'manual' ? '#b45309' : '#1d4ed8', fontWeight: 500 }}>
                                {triggerLabel(item.trigger_type)}
                              </span>
                            </div>
                            <div style={{ color: '#475569', fontSize: '0.9rem', lineHeight: 1.5 }}>
                              {localizeAnalysisText(item.plain_english_summary?.replace(/^【[^】]+】\s*/, ''))}
                            </div>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
                
              </div>
            )}
            
          </div>
        </div>
      )}
    </div>
  );
}
