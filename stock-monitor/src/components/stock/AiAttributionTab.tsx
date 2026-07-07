'use client';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';

import { useState, useEffect } from 'react';
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
  changePercent: number;
  score: number;
  evidenceChain: EvidenceChain;
  futureTrendPrediction: string;
  aiJudgment: string;
  credibility: string;
  riskNotice: string;
}

export default function AiAttributionTab({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<AttributionData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState('正在初始化 AI 推理引擎...');
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

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
      const res = await fetch(`${API_BASE}/api/stock/ai_attribution/${stockCode}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('无法获取归因分析数据');
      const json = await res.json();
      setData(json);
      setLastUpdated(new Date());
    } catch (err: any) {
      setError(err.message || '网络异常');
    } finally {
      setLoading(false);
    }
  };

  // 移除了初始挂载时的自动请求，改为完全手动触发

  if (loading) return (
    <div className={styles.loadingContainer}>
      <div className={styles.spinner}></div>
      <div className={styles.loadingText}>{loadingText}</div>
    </div>
  );
  if (error) return <div className={styles.error}>{error}</div>;

  if (!data) return (
    <div className={styles.emptyContainer}>
      <div className={styles.emptyIcon}>
        <BrainCircuit size={64} color="#1890ff" strokeWidth={1.5} />
      </div>
      <div className={styles.emptyText}>点击下方按钮，立即让大模型结合实时盘口生成归因分析。</div>
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

  const isUp = data.changePercent > 0;
  const isDown = data.changePercent < 0;

  const getGaugeOption = () => {
    const score = data.score || 50;
    
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

  const score = data.score || 50;
  let statusColor = '#f59e0b'; // default yellow
  let statusText = '中性观望';
  let statusBg = '#fef3c7';
  
  if (score >= 80) { 
    statusColor = '#ef4444'; // Red
    statusBg = '#fee2e2';
    statusText = '强烈看好';
  } else if (score <= 40) { 
    statusColor = '#10b981'; // Green
    statusBg = '#d1fae5';
    statusText = '高危警戒';
  }

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.stockName}>{data.stockName}</span>
          <span className={`${styles.stockChange} ${isUp ? styles.textRise : isDown ? styles.textFall : ''}`}>
            今日涨跌幅：{isUp ? '+' : ''}{data.changePercent}%
          </span>
          <div className={styles.badge}>每日涨跌归因分析</div>
        </div>
        <div className={styles.headerRight}>
          {lastUpdated && <span className={styles.lastUpdated}>更新于 {lastUpdated.toLocaleTimeString()}</span>}
          <button className={styles.refreshBtn} onClick={fetchAttribution} disabled={loading}>
            AI分析原因
          </button>
        </div>
      </div>

      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        background: 'linear-gradient(145deg, #ffffff 0%, #f9fafb 100%)', 
        border: '1px solid #e5e7eb', 
        borderRadius: '16px', 
        padding: '32px 40px', 
        marginBottom: '32px',
        boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05)'
      }}>
        <div style={{ flex: 1, maxWidth: '400px' }}>
          <ReactECharts option={getGaugeOption()} style={{ height: '180px', width: '100%' }} />
        </div>
        <div style={{ flex: 1, paddingLeft: '40px', borderLeft: '1px solid #e5e7eb' }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '1.2rem', color: '#374151', fontWeight: 600 }}>
            机构综合逻辑健康度
          </h3>
          <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: '16px' }}>
            <span style={{ fontSize: '3.5rem', fontWeight: 800, color: statusColor, lineHeight: 1 }}>
              {score}
            </span>
            <span style={{ fontSize: '1.2rem', color: '#6b7280', marginLeft: '4px', fontWeight: 500 }}>
              分
            </span>
            <span style={{ 
              marginLeft: '20px', 
              padding: '6px 16px', 
              backgroundColor: statusBg, 
              color: statusColor, 
              borderRadius: '999px', 
              fontSize: '1rem', 
              fontWeight: 600,
              border: `1px solid ${statusColor}40`
            }}>
              {statusText}
            </span>
          </div>
          <p style={{ margin: 0, fontSize: '0.95rem', color: '#6b7280', lineHeight: 1.6 }}>
            该评分由 AI 引擎基于<strong style={{ color: '#4b5563' }}>主力资金流向</strong>、<strong style={{ color: '#4b5563' }}>最新基本面</strong>、<strong style={{ color: '#4b5563' }}>行业板块热度</strong>以及<strong style={{ color: '#4b5563' }}>核心资讯</strong>多维综合计算得出。分数越高代表多头共振越强。
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
    </div>
  );
}
