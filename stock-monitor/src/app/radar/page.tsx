'use client';

import {
  Activity,
  Database,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import MarketContextPanel from '@/components/radar/MarketContextPanel';
import LeaderObservationPanel from '@/components/radar/LeaderObservationPanel';
import ModuleStatePanel from '@/components/radar/ModuleStatePanel';
import RadarDataGatePanel from '@/components/radar/RadarDataGatePanel';
import RadarStatusStrip from '@/components/radar/RadarStatusStrip';
import EtfObservationPanel from '@/components/radar/EtfObservationPanel';
import SectorObservationPanel from '@/components/radar/SectorObservationPanel';
import type {
  RadarEtfsResponse,
  RadarLeaderModule,
  RadarLeadersResponse,
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
  const [etfs, setEtfs] = useState<RadarEtfsResponse | null>(null);
  const [leaders, setLeaders] = useState<RadarLeadersResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [sectorsRefreshError, setSectorsRefreshError] = useState('');
  const [etfsRefreshError, setEtfsRefreshError] = useState('');
  const [leadersRefreshError, setLeadersRefreshError] = useState('');
  const [renderedAt, setRenderedAt] = useState<string | null>(null);
  const overviewInFlight = useRef(false);
  const sectorsInFlight = useRef(false);
  const etfsInFlight = useRef(false);
  const leadersInFlight = useRef(false);

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

  const loadEtfs = useCallback(async () => {
    if (etfsInFlight.current) return;
    etfsInFlight.current = true;
    try {
      const response = await fetch(
        `/api/backend/api/radar/etfs?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('行业ETF快照暂不可用');
      const payload = await response.json() as RadarEtfsResponse;
      if (payload.schemaVersion !== 'radar-etfs-v1') {
        throw new Error('行业ETF数据契约不匹配');
      }
      setEtfs(payload);
      setRenderedAt(new Date().toISOString());
      setEtfsRefreshError('');
    } catch (error) {
      setEtfsRefreshError(
        error instanceof Error ? error.message : '行业ETF快照暂不可用',
      );
    } finally {
      etfsInFlight.current = false;
    }
  }, []);

  const loadLeaders = useCallback(async () => {
    if (leadersInFlight.current) return;
    leadersInFlight.current = true;
    try {
      const response = await fetch(
        `/api/backend/api/radar/leaders?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('三级龙头快照暂不可用');
      const payload = await response.json() as RadarLeadersResponse;
      if (payload.schemaVersion !== 'radar-leaders-v1') {
        throw new Error('三级龙头数据契约不匹配');
      }
      setLeaders(payload);
      setRenderedAt(new Date().toISOString());
      setLeadersRefreshError('');
    } catch (error) {
      setLeadersRefreshError(
        error instanceof Error ? error.message : '三级龙头快照暂不可用',
      );
    } finally {
      leadersInFlight.current = false;
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

  useEffect(() => {
    if (activeTab !== 'etf') return;
    const initialTimer = window.setTimeout(() => void loadEtfs(), 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadEtfs();
    }, 180_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [activeTab, loadEtfs]);

  useEffect(() => {
    if (activeTab !== 'leaders') return;
    const initialTimer = window.setTimeout(() => void loadLeaders(), 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadLeaders();
    }, 180_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [activeTab, loadLeaders]);

  const refreshNow = async () => {
    await loadOverview(false);
    if (activeTab === 'sectors') await loadSectors();
    if (activeTab === 'etf') await loadEtfs();
    if (activeTab === 'leaders') await loadLeaders();
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
  const etfModule = overview.modules.etf;
  const overviewLeaderModule = 'summary' in overview.modules.leaders
    ? overview.modules.leaders
    : null;
  const leaderModule: RadarLeaderModule | null = leaders?.module
    || overviewLeaderModule;
  const dataHealthy = overview.mode === 'shadow'
    && market.state === 'available'
    && sectorModule.state === 'available';
  const anomalyCount = [
    market.state,
    sectorModule.state,
    etfModule.state,
    overview.modules.leaders.state,
  ].filter(
    (state) => state === 'failed' || state === 'stale' || state === 'not_ready',
  ).length;

  return (
    <div className={styles.pageShell}>
      <header className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>MAINLINE RADAR / VERIFIED SHADOW DATA</span>
          <h1>主线雷达</h1>
          <p>市场、行业、ETF与三级龙头按各自门槛展示真实影子快照。</p>
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
          <span><b>{etfModule.summary.productCount}</b> ETF产品</span>
          <span><b>{leaderModule?.summary.eligibleCount ?? '—'}</b> 龙头合资格</span>
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
              state={etfModule.state}
              title="行业ETF监测"
              description="总览只显示真实产品主档与候选摘要，详细时间和替代标的进入行业ETF页查看。"
              stage={etfModule.state === 'not_enabled' ? 5 : undefined}
              badges={[
                `官方产品 ${etfModule.summary.productCount}`,
                `候选组 ${etfModule.summary.candidateGroupCount}`,
                etfModule.summary.formalStateEnabled ? '正式状态可用' : '正式状态未启用',
              ]}
            />
            <ModuleStatePanel
              state={overview.modules.leaders.state}
              title="龙头梯队"
              description="总览只显示确定性状态机摘要，完整证据与失效条件进入龙头梯队页查看。"
              stage={'enabledStage' in overview.modules.leaders
                ? overview.modules.leaders.enabledStage
                : undefined}
              badges={leaderModule ? [
                `预备龙头 ${leaderModule.summary.preliminaryCount}`,
                `候选龙头 ${leaderModule.summary.candidateCount}`,
                `已确认龙头 ${leaderModule.summary.confirmedCount}`,
              ] : ['预备龙头 —', '候选龙头 —', '已确认龙头 —']}
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
        <>
          {etfsRefreshError && (
            <div className={styles.refreshError}>
              {etfsRefreshError}。当前保留上一轮成功页面内容。
            </div>
          )}
          {etfs ? (
            <EtfObservationPanel
              module={etfs.module}
              formatTime={formatTime}
              renderedAt={renderedAt}
            />
          ) : (
            <EtfObservationPanel
              module={etfModule}
              formatTime={formatTime}
              renderedAt={renderedAt}
            />
          )}
        </>
      )}

      {activeTab === 'leaders' && (
        <>
          {leadersRefreshError && (
            <div className={styles.refreshError}>
              {leadersRefreshError}。当前保留上一轮成功页面内容。
            </div>
          )}
          {leaderModule ? (
            <LeaderObservationPanel
              module={leaderModule}
              formatTime={formatTime}
              renderedAt={renderedAt}
            />
          ) : (
            <ModuleStatePanel
              state={overview.modules.leaders.state}
              title="龙头梯队"
              description="正在读取阶段6只读快照，不触发状态机重算。"
              stage={'enabledStage' in overview.modules.leaders
                ? overview.modules.leaders.enabledStage
                : undefined}
              badges={['预备龙头 —', '候选龙头 —', '已确认龙头 —']}
            />
          )}
        </>
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
        <span>V5 · 阶段6影子状态</span>
        <p>规则聚合仅用于分析与监测，不构成交易建议。</p>
        <span>
          {stateLabel(market.state)} · {stateLabel(sectorModule.state)} ·{' '}
          {stateLabel(etfModule.state)} · {stateLabel(overview.modules.leaders.state)}
        </span>
      </footer>
    </div>
  );
}
