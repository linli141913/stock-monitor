'use client';

import {
  Activity,
  Database,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import MarketContextPanel from '@/components/radar/MarketContextPanel';
import ModuleStatePanel from '@/components/radar/ModuleStatePanel';
import RadarDataGatePanel from '@/components/radar/RadarDataGatePanel';
import RadarStatusStrip from '@/components/radar/RadarStatusStrip';
import SectorObservationPanel from '@/components/radar/SectorObservationPanel';
import type {
  RadarOverviewResponse,
  RadarSectorsResponse,
} from '@/types/radar';
import styles from './page.module.css';

type RadarTab = 'overview' | 'sectors' | 'etf' | 'leaders' | 'history';

const TABS: Array<{ id: RadarTab; label: string }> = [
  { id: 'overview', label: '总览' },
  { id: 'sectors', label: '行业主线' },
  { id: 'etf', label: '行业ETF' },
  { id: 'leaders', label: '龙头梯队' },
  { id: 'history', label: '历史验证' },
];

const TIME_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function formatTime(
  value: string | null | undefined,
  withSeconds = false,
) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  if (withSeconds) return TIME_FORMATTER.format(date);
  return TIME_FORMATTER.format(date).slice(0, 5);
}

function stateLabel(state: string) {
  return {
    available: '数据可用',
    empty: '真实空榜',
    stale: '数据过期',
    failed: '来源失败',
    not_ready: '等待快照',
    not_enabled: '尚未启用',
  }[state] || state;
}

export default function RadarPage() {
  const [activeTab, setActiveTab] = useState<RadarTab>('overview');
  const [overview, setOverview] = useState<RadarOverviewResponse | null>(null);
  const [sectors, setSectors] = useState<RadarSectorsResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [sectorsRefreshError, setSectorsRefreshError] = useState('');
  const [renderedAt, setRenderedAt] = useState<string | null>(null);
  const overviewInFlight = useRef(false);
  const sectorsInFlight = useRef(false);

  const loadOverview = useCallback(async (silent = false) => {
    if (overviewInFlight.current) return;
    overviewInFlight.current = true;
    if (!silent) setRefreshing(true);
    try {
      const response = await fetch(
        `/api/backend/api/radar/overview?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('主线雷达数据暂不可用');
      const payload = await response.json() as RadarOverviewResponse;
      if (payload.schemaVersion !== 'radar-overview-v1') {
        throw new Error('主线雷达数据契约不匹配');
      }
      setOverview(payload);
      setRenderedAt(new Date().toISOString());
      setRefreshError('');
    } catch (error) {
      setRefreshError(
        error instanceof Error ? error.message : '主线雷达数据暂不可用',
      );
    } finally {
      overviewInFlight.current = false;
      setInitialLoading(false);
      if (!silent) setRefreshing(false);
    }
  }, []);

  const loadSectors = useCallback(async () => {
    if (sectorsInFlight.current) return;
    sectorsInFlight.current = true;
    try {
      const response = await fetch(
        `/api/backend/api/radar/sectors?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('完整行业快照暂不可用');
      const payload = await response.json() as RadarSectorsResponse;
      if (payload.schemaVersion !== 'radar-sectors-v1') {
        throw new Error('行业雷达数据契约不匹配');
      }
      setSectors(payload);
      setRenderedAt(new Date().toISOString());
      setSectorsRefreshError('');
    } catch (error) {
      setSectorsRefreshError(
        error instanceof Error ? error.message : '完整行业快照暂不可用',
      );
    } finally {
      sectorsInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void loadOverview(false), 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadOverview(true);
    }, 60_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [loadOverview]);

  useEffect(() => {
    if (activeTab !== 'sectors') return;
    const initialTimer = window.setTimeout(() => void loadSectors(), 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadSectors();
    }, 180_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [activeTab, loadSectors]);

  const refreshNow = async () => {
    await loadOverview(false);
    if (activeTab === 'sectors') await loadSectors();
  };

  const showTab = (tab: RadarTab) => {
    setActiveTab(tab);
  };

  if (initialLoading && !overview) {
    return (
      <div className={styles.loadingPage}>
        <span><Activity size={24} /></span>
        <strong>正在读取主线雷达快照</strong>
        <p>只读取既有市场与行业聚合，不触发全市场重算。</p>
      </div>
    );
  }

  if (!overview) {
    return (
      <div className={styles.loadingPage}>
        <span className={styles.errorIcon}><Database size={24} /></span>
        <strong>主线雷达暂不可用</strong>
        <p>{refreshError || '后端尚未加载阶段4只读接口。'}</p>
        <button onClick={() => void loadOverview(false)}>重新读取</button>
      </div>
    );
  }

  const market = overview.modules.market;
  const sectorModule = overview.modules.sectors;
  const dataHealthy = overview.mode === 'shadow'
    && market.state === 'available'
    && sectorModule.state === 'available';
  const anomalyCount = [market.state, sectorModule.state].filter(
    (state) => state === 'failed' || state === 'stale' || state === 'not_ready',
  ).length;

  return (
    <div className={styles.pageShell}>
      <header className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>MAINLINE RADAR / VERIFIED SHADOW DATA</span>
          <h1>主线雷达</h1>
          <p>阶段4先展示市场与行业真实聚合；ETF与龙头将在后续阶段接入。</p>
        </div>
        <div className={styles.heroActions}>
          <span className={styles.shadowBadge}>
            {overview.mode === 'shadow' ? '影子运行' : '雷达已关闭'}
          </span>
          <span className={dataHealthy ? styles.healthBadge : styles.partialHealthBadge}>
            <ShieldCheck size={14} />
            {dataHealthy ? '影子数据可用' : '部分模块受限'}
          </span>
          <button onClick={() => void refreshNow()} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? styles.spinning : ''} />
            {refreshing ? '读取中' : '刷新页面数据'}
          </button>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="主线雷达页面">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? styles.activeTab : ''}
            onClick={() => showTab(tab.id)}
          >
            {tab.label}
            {tab.id === 'sectors' && (
              <span>{sectorModule.summary.totalCount || ''}</span>
            )}
          </button>
        ))}
      </nav>

      <RadarStatusStrip overview={overview} formatTime={formatTime} />

      <section className={styles.snapshotBand}>
        <div>
          <strong>本轮快照与状态</strong>
          <span>只显示真实快照、可用数量和异常，不冒充状态变化。</span>
        </div>
        <div className={styles.snapshotStats}>
          <span><b>{market.data ? 1 : 0}</b> 市场快照</span>
          <span><b>{sectorModule.summary.totalCount}</b> 行业快照</span>
          <span><b className={styles.greenNumber}>{sectorModule.summary.usableCount}</b> 影子可用</span>
          <span><b>—</b> ETF未启用</span>
          <span><b>—</b> 龙头未启用</span>
          <span className={anomalyCount ? styles.anomalyStat : ''}><b>{anomalyCount}</b> 模块异常</span>
        </div>
      </section>

      {refreshError && (
        <div className={styles.refreshError}>
          {refreshError}。当前保留上一轮成功页面内容。
        </div>
      )}

      {activeTab === 'overview' && (
        <div className={styles.overviewGrid}>
          <div className={styles.leftRail}>
            <MarketContextPanel module={market} formatTime={formatTime} />
            <SectorObservationPanel
              module={sectorModule}
              onViewAll={() => showTab('sectors')}
            />
            <RadarDataGatePanel
              overview={overview}
              renderedAt={renderedAt}
              formatTime={formatTime}
            />
          </div>
          <div className={styles.mainRail}>
            <ModuleStatePanel
              state={overview.modules.etf.state}
              title="行业ETF监测"
              description="页面位置已预留；阶段5才接入官方产品信息、指数版本和真实观察池。"
              stage={overview.modules.etf.enabledStage}
              badges={['不生成ETF候选', '不展示基础名册数量']}
            />
            <ModuleStatePanel
              state={overview.modules.leaders.state}
              title="龙头梯队"
              description="三层状态位置已预留；阶段6完成确定性状态机后按真实状态展示。"
              stage={overview.modules.leaders.enabledStage}
              badges={['预备龙头 —', '候选龙头 —', '已确认龙头 —']}
            />
          </div>
        </div>
      )}

      {activeTab === 'sectors' && (
        <>
          {sectorsRefreshError && (
            <div className={styles.refreshError}>
              {sectorsRefreshError}。当前保留上一轮成功页面内容。
            </div>
          )}
          {sectors ? (
            <SectorObservationPanel module={sectors.module} full />
          ) : (
            <ModuleStatePanel
              state="not_ready"
              title="行业主线 · 全部观察"
              description="正在读取完整行业快照，不触发新的扫描。"
            />
          )}
        </>
      )}

      {activeTab === 'etf' && (
        <ModuleStatePanel
          state="not_enabled"
          title="行业ETF"
          description="阶段5接入官方ETF名册、指数版本、产品归组和真实观察池。"
          stage={5}
          badges={['趋势轮动未计算', '中期跟踪未计算']}
        />
      )}

      {activeTab === 'leaders' && (
        <ModuleStatePanel
          state="not_enabled"
          title="龙头梯队"
          description="阶段6冻结硬门槛、评分和状态机后，再生成预备、候选与已确认状态。"
          stage={6}
          badges={['预备龙头 —', '候选龙头 —', '已确认龙头 —']}
        />
      )}

      {activeTab === 'history' && (
        <ModuleStatePanel
          state="not_enabled"
          title="历史验证"
          description="阶段9才接入严格时间点回放、黄金样本和影子质量结果。"
          stage={9}
          badges={['不展示虚假胜率', '不生成收益承诺']}
        />
      )}

      <footer className={styles.radarFooter}>
        <span>V2 · 阶段4真实数据骨架</span>
        <p>规则聚合仅用于分析与监测，不构成交易建议。</p>
        <span>{stateLabel(market.state)} · {stateLabel(sectorModule.state)}</span>
      </footer>
    </div>
  );
}
