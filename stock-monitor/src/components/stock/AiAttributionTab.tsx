'use client';

const API_BASE = '/api/backend';

import { useState, useEffect, useCallback } from 'react';
import { BrainCircuit } from 'lucide-react';
import ReactECharts from 'echarts-for-react';
import styles from './AiAttributionTab.module.css';

interface EvidenceChain {
  technicalAndSentiment: string;
  fundFactor: string;
  fundamentalAndNews: string;
  sectorAndMacro: string;
}

interface AttributionData {
  stockName: string;
  stockCode: string;
  changePercent: number | null;
  score: number | null;
  evidenceChain: EvidenceChain;
  futureTrendPrediction: string;
  plainEnglishSummary?: string;
  aiJudgment: string;
  credibility: string;
  riskNotice: string;
}

interface HistoryItem {
  date?: string;
  target_date?: string;
  time: string;
  timestamp?: string;
  trigger_type: string;
  plain_english_summary: string;
  full_json: AttributionData;
}

export default function AiAttributionTab({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<AttributionData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState('正在初始化 AI 推理引擎...');
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [historyList, setHistoryList] = useState<HistoryItem[]>([]);
  const [allHistoryList, setAllHistoryList] = useState<HistoryItem[]>([]);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [fetchingAllHistory, setFetchingAllHistory] = useState(false);
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

  const getScoreInfo = (s: number) => {
    let color = '#f59e0b';
    let text = '中性';
    let bg = '#fffbeb';
    let border = '#fef3c7';
    if (s >= 80) {
      color = '#ef4444';
      text = '看好';
      bg = '#fee2e2';
      border = '#fecaca';
    } else if (s <= 40) {
      color = '#10b981';
      text = '警戒';
      bg = '#d1fae5';
      border = '#a7f3d0';
    }
    return { color, text, bg, border };
  };

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_history/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res.ok) {
        const json = await res.json() as {
          data?: HistoryItem[];
          bounds?: { start: string; end: string } | null;
          calendarStatus?: 'available' | 'unknown';
        };
        setHistoryList(json.data || []);
        setBounds(json.bounds || null);
        setCalendarStatus(json.calendarStatus || 'unknown');
      }
    } catch (err) {
      setBounds(null);
      setCalendarStatus('unknown');
      console.error('获取历史记录失败', err);
    }
  }, [stockCode]);

  const fetchAllHistory = async () => {
    setFetchingAllHistory(true);
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_history_all/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (res.ok) {
        const json = await res.json() as { data?: HistoryItem[] };
        const historyItems = json.data || [];
        setAllHistoryList(historyItems);

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
      }
    } catch (err) {
      console.error('获取所有历史失败', err);
    } finally {
      setFetchingAllHistory(false);
    }
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
        '正在通过 AKShare 获取实时盘口数据...',
        '正在分析近 5 日资金流向与大单情况...',
        '正在拉取最新的半导体宏观资讯与海外映射(SOX/NVDA)...',
        '情报拼装完毕，大模型 (LLM) 深度推理中，请耐心等待 (约需10-15秒)...'
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
    setLoadingText('正在初始化 AI 推理引擎...');
    try {
      const res = await fetch(`${API_BASE}/api/stock/ai_attribution/${stockCode}?trigger=manual`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('无法获取归因分析数据');
      const json = await res.json() as AttributionData;
      setData(json);
      setLastUpdated(new Date());
      fetchHistory(); // 刷新时间轴
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '网络异常');
    } finally {
      setLoading(false);
    }
  };

  const handleTimelineClick = (item: HistoryItem) => {
    setData(item.full_json);
    setLastUpdated(null); // 不显示当前时间，因为是历史记录
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
      <div className={styles.emptyText}>今日暂无自动追踪记录。点击下方按钮，立即让大模型结合实时盘口生成归因分析。</div>
      <button className={styles.refreshBtn} onClick={fetchAttribution} style={{ marginTop: 24, padding: '12px 24px', fontSize: '1.1rem' }}>
        AI分析原因
      </button>
    </div>
  );

  const renderRichText = (text: string) => {
    if (!text) return '';
    let html = text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
    
    // Parse Markdown links [text](url)
    html = html.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" style="color:#2563eb; text-decoration:none; font-weight:500;">$1</a>');
    
    // Parse escaped <br/> tags
    html = html.replace(/&lt;br\/?&gt;/gi, '<br/>');
    html = html.replace(/\\n/g, '<br/>');

    // Colorize positive keywords and numbers
    html = html.replace(/(净流入|流入|上涨|大涨|暴涨|涨跌幅)([\s\d\.\+\-%亿元万千万]+)?/g, `<span class="${styles.textRise}">$1$2</span>`);
    // Colorize negative keywords and numbers
    html = html.replace(/(净流出|流出|下跌|大跌|暴跌|回调)([\s\d\.\+\-%亿元万千万]+)?/g, `<span class="${styles.textFall}">$1$2</span>`);

    return html;
  };

  const isUp = data?.changePercent != null && data.changePercent > 0;
  const isDown = data?.changePercent != null && data.changePercent < 0;

  const getGaugeOption = () => {
    const score = data?.score ?? 0;
    
    return {
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          center: ['50%', '70%'],
          radius: '120%',
          min: 0,
          max: 100,
          splitNumber: 10,
          axisLine: {
            lineStyle: {
              width: 12,
              color: [
                [0.4, '#10b981'], // <=40 green
                [0.8, '#f59e0b'], // 40-80 yellow
                [1, '#ef4444']    // >80 red
              ]
            }
          },
          pointer: {
            icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
            length: '55%',
            width: 8,
            offsetCenter: [0, '-15%'],
            itemStyle: { color: '#4b5563' }
          },
          axisTick: { length: 6, lineStyle: { color: 'auto', width: 1 } },
          splitLine: { length: 12, lineStyle: { color: 'auto', width: 2 } },
          axisLabel: { color: '#6b7280', fontSize: 10, distance: -30, formatter: (val: number) => (val % 20 === 0 ? val : '') },
          title: { show: false },
          detail: { show: false }, // Hide text inside the gauge to prevent overlap
          data: [{ value: score }]
        }
      ]
    };
  };

  const score = data?.score ?? null;
  let statusColor = '#f59e0b'; // default yellow
  let statusText = score == null ? '未生成评分' : '中性观望';
  let statusBg = '#fef3c7';
  
  if (score != null && score >= 80) {
    statusColor = '#ef4444'; // Red
    statusBg = '#fee2e2';
    statusText = '强烈看好';
  } else if (score != null && score <= 40) {
    statusColor = '#10b981'; // Green
    statusBg = '#d1fae5';
    statusText = '高危警戒';
  }

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.stockName}>{data ? data.stockName : stockCode}</span>
          {data && (
            <span className={`${styles.stockChange} ${isUp ? styles.textRise : isDown ? styles.textFall : ''}`}>
              今日涨跌幅：{data.changePercent == null ? '暂无数据' : `${isUp ? '+' : ''}${data.changePercent}%`}
            </span>
          )}
          <div className={styles.badge}>每日涨跌归因分析</div>
        </div>
        <div className={styles.headerRight}>
          {lastUpdated && <span className={styles.lastUpdated}>更新于 {lastUpdated.toLocaleTimeString()}</span>}
          <button className={styles.refreshBtn} onClick={fetchAttribution} disabled={loading}>
            手动分析最新
          </button>
        </div>
      </div>

      {historyList.length > 0 && (
        <div className={styles.timelineContainer} style={{ padding: '16px', background: '#f8fafc', borderRadius: '8px', marginBottom: '16px', border: '1px solid #e2e8f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 'bold', color: '#1e293b' }}>今日盘中追踪时间轴</span>
              {bounds && (
                <span style={{ fontSize: '0.8rem', color: '#475569', background: '#f1f5f9', padding: '2px 8px', borderRadius: '4px', border: '1px solid #e2e8f0', fontWeight: 500 }}>
                  分析跨度: {formatBoundsDate(bounds.start)} ~ {formatBoundsDate(bounds.end)}
                </span>
              )}
            </div>
            <button 
              onClick={() => { setShowHistoryModal(true); fetchAllHistory(); }}
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
                  <span>{item.time} ({item.trigger_type === 'auto' ? '自动' : '手动'})</span>
                  {(() => {
                    const itemScore = item.full_json?.score;
                    if (itemScore == null) {
                      return <span style={{ marginLeft: '6px', fontSize: '0.7rem', color: '#64748b' }}>未评分</span>;
                    }
                    const { color, text, bg, border } = getScoreInfo(itemScore);
                    return (
                      <span style={{ 
                        marginLeft: '6px', 
                        fontSize: '0.7rem', 
                        padding: '0px 4px', 
                        borderRadius: '3px', 
                        backgroundColor: bg, 
                        color: color, 
                        border: `1px solid ${border}`,
                        fontWeight: 'bold',
                        lineHeight: 1.2
                      }}>
                        {itemScore}分 {text}
                      </span>
                    );
                  })()}
                </div>
                <div style={{ color: '#0f172a', fontWeight: 500, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {item.plain_english_summary}
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

      {data && (
        <>
      <div className={styles.gaugeCard}>
        <div className={styles.gaugeChart}>
          {score == null
            ? <div className={styles.emptyText}>本次 AI 分析未生成评分</div>
            : <ReactECharts option={getGaugeOption()} style={{ height: '180px', width: '100%' }} />}
        </div>
        <div className={styles.gaugeInfo}>
          <h3 className={styles.gaugeInfoTitle}>
            机构综合逻辑健康度
          </h3>
          <div className={styles.gaugeScoreArea}>
            <span className={styles.gaugeScoreNum} style={{ color: statusColor }}>
              {score ?? '—'}
            </span>
            <span className={styles.gaugeScoreUnit}>
              {score == null ? '' : '分'}
            </span>
            <span className={styles.gaugeScoreTag} style={{ 
              backgroundColor: statusBg, 
              color: statusColor, 
              border: `1px solid ${statusColor}40`
            }}>
              {statusText}
            </span>
          </div>
          <p className={styles.gaugeInfoDesc}>
            该评分由 AI 引擎基于<strong>主力资金流向</strong>、<strong>最新基本面</strong>、<strong>行业板块热度</strong>以及<strong>核心资讯</strong>多维综合计算得出。分数越高代表多头共振越强。
          </p>
        </div>
      </div>

      <div className={styles.sectionTitle}>机构级归因拆解</div>
      
      <div className={styles.evidenceList}>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>1. 量价与情绪面</div>
          <div className={styles.evidenceText} dangerouslySetInnerHTML={{ __html: renderRichText(data.evidenceChain.technicalAndSentiment) }} />
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>2. 资金面博弈</div>
          <div className={styles.evidenceText} dangerouslySetInnerHTML={{ __html: renderRichText(data.evidenceChain.fundFactor) }} />
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>3. 基本面与资讯</div>
          <div className={styles.evidenceText} dangerouslySetInnerHTML={{ __html: renderRichText(data.evidenceChain.fundamentalAndNews) }} />
        </div>
        <div className={styles.evidenceItem}>
          <div className={styles.evidenceLabel}>4. 板块与宏观共振</div>
          <div className={styles.evidenceText} dangerouslySetInnerHTML={{ __html: renderRichText(data.evidenceChain.sectorAndMacro) }} />
        </div>
      </div>

      <div className={styles.predictionCard} style={{ marginTop: '16px', padding: '16px', borderRadius: '8px', border: '1px solid #ffccc7', background: '#fff2f0' }}>
        <div className={styles.riskTitle} style={{ color: '#cf1322', marginBottom: '8px' }}>🚀 未来走势推演</div>
        <div className={styles.riskText} dangerouslySetInnerHTML={{ __html: renderRichText(data.futureTrendPrediction) }} />
      </div>

      {data.plainEnglishSummary && (
        <div className={styles.plainEnglishCard} style={{ marginTop: '16px', padding: '16px', borderRadius: '8px', border: '2px solid #1890ff', background: '#e6f4ff', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ fontSize: '24px' }}>💡</div>
          <div>
            <div style={{ fontWeight: 'bold', color: '#0958d9', marginBottom: '4px', fontSize: '0.9rem' }}>大白话总结</div>
            <div style={{ color: '#1677ff', fontSize: '1.2rem', fontWeight: 600 }}>
              {data.plainEnglishSummary ? data.plainEnglishSummary.replace(/^【[^】]+】\s*/, '') : ''}
            </div>
          </div>
        </div>
      )}

      <div className={styles.judgmentCard} style={{ marginTop: '16px' }}>
        <div className={styles.judgmentHeader}>
          <span className={styles.judgmentTitle}>AI 综合判断</span>
          <span className={styles.credibilityTag}>可信度：{data.credibility}</span>
        </div>
        <div className={styles.judgmentText} dangerouslySetInnerHTML={{ __html: renderRichText(data.aiJudgment) }} />
      </div>

      <div className={styles.riskCard}>
        <div className={styles.riskTitle}>风险提示</div>
        <div className={styles.riskText} dangerouslySetInnerHTML={{ __html: renderRichText(data.riskNotice) }} />
      </div>
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
                            onClick={() => { handleTimelineClick(item); setShowHistoryModal(false); }}
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
                                <span>{item.time} ({item.trigger_type === 'auto' ? '自动分析' : '手动分析最新'})</span>
                                {(() => {
                                  const itemScore = item.full_json?.score;
                                  if (itemScore == null) return <span style={{ color: '#64748b' }}>未评分</span>;
                                  const { color, text, bg, border } = getScoreInfo(itemScore);
                                  return (
                                    <span style={{ 
                                      marginLeft: '8px', 
                                      fontSize: '0.75rem', 
                                      padding: '1px 6px', 
                                      borderRadius: '4px', 
                                      backgroundColor: bg, 
                                      color: color, 
                                      border: `1px solid ${border}`,
                                      fontWeight: 'bold',
                                      lineHeight: 1.2
                                    }}>
                                      {itemScore}分 {text}
                                    </span>
                                  );
                                })()}
                              </span>
                              <span style={{ fontSize: '0.75rem', padding: '2px 8px', borderRadius: '4px', background: item.trigger_type === 'manual' ? '#fffbeb' : '#eff6ff', color: item.trigger_type === 'manual' ? '#b45309' : '#1d4ed8', fontWeight: 500 }}>
                                {item.trigger_type === 'manual' ? '手动' : '定时自动'}
                              </span>
                            </div>
                            <div style={{ color: '#475569', fontSize: '0.9rem', lineHeight: 1.5 }}>
                              {item.plain_english_summary ? item.plain_english_summary.replace(/^【[^】]+】\s*/, '') : ''}
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
