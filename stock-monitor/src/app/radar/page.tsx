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
  RadarLeaderReviewDocument,
  RadarLeaderReviewDocumentResponse,
  RadarLeaderReviewFormResponse,
  RadarLeaderReviewSubmissionDraft,
  RadarLeaderReviewQueueResponse,
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

const ETF_REASON_LABELS: Record<string, string> = {
  etf_product_evidence_not_ready: '产品正式证据未就绪',
  etf_rule_not_frozen: '正式规则尚未冻结',
  formal_source_inputs_incomplete: '正式来源输入不完整',
  ranking_calibration_sample_missing: '排名校准样本不足',
  management_style_unverified: '主动 / 被动属性未确认',
  equity_region_unverified: '境内股票范围未确认',
  asset_class_unverified: '资产类别未确认',
  cross_border_asset_unverified: '跨境资产范围未确认',
  product_type_unknown: '产品类型未确认',
};

function percent(value: number | null | undefined) {
  return Math.round(Math.max(0, Math.min(1, value || 0)) * 100);
}

function blockerLabels(
  reasonCodes: string[],
  labels: Record<string, string>,
  limit = 4,
) {
  const unique = Array.from(new Set(reasonCodes.map(
    (reason) => labels[reason] || reason,
  )));
  const visible = unique.slice(0, limit);
  if (unique.length > limit) visible.push(`另有 ${unique.length - limit} 项待补`);
  return visible;
}

export default function RadarPage() {
  const [activeTab, setActiveTab] = useState<RadarTab>('overview');
  const [focusedIndustryCode, setFocusedIndustryCode] = useState('');
  const [focusedIndustryName, setFocusedIndustryName] = useState('');
  const [overview, setOverview] = useState<RadarOverviewResponse | null>(null);
  const [sectors, setSectors] = useState<RadarSectorsResponse | null>(null);
  const [etfs, setEtfs] = useState<RadarEtfsResponse | null>(null);
  const [leaders, setLeaders] = useState<RadarLeadersResponse | null>(null);
  const [leaderReviewQueue, setLeaderReviewQueue] = useState<
    RadarLeaderReviewQueueResponse | null
  >(null);
  const [selectedReviewDocument, setSelectedReviewDocument] = useState<
    RadarLeaderReviewDocument | null
  >(null);
  const [leaderReviewForm, setLeaderReviewForm] = useState<
    RadarLeaderReviewFormResponse | null
  >(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [sectorsRefreshError, setSectorsRefreshError] = useState('');
  const [etfsRefreshError, setEtfsRefreshError] = useState('');
  const [leadersRefreshError, setLeadersRefreshError] = useState('');
  const [leaderReviewQueueError, setLeaderReviewQueueError] = useState('');
  const [leaderReviewQueueLoading, setLeaderReviewQueueLoading] = useState(false);
  const [leaderReviewSubmitting, setLeaderReviewSubmitting] = useState(false);
  const [leaderReviewSubmitMessage, setLeaderReviewSubmitMessage] = useState('');
  const [renderedAt, setRenderedAt] = useState<string | null>(null);
  const overviewInFlight = useRef(false);
  const sectorsInFlight = useRef(false);
  const etfsInFlight = useRef(false);
  const leadersInFlight = useRef(false);
  const leaderReviewQueueInFlight = useRef(false);
  const leaderReviewQueueOffset = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      if (params.get('tab') !== 'sectors') return;
      setFocusedIndustryCode(params.get('industryCode') || '');
      setFocusedIndustryName(params.get('industryName') || '');
      setActiveTab('sectors');
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

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

  const loadLeaderReviewQueue = useCallback(async (offset = 0) => {
    if (leaderReviewQueueInFlight.current) return;
    leaderReviewQueueInFlight.current = true;
    setLeaderReviewQueueLoading(true);
    try {
      const response = await fetch(
        `/api/backend/api/radar/leaders/review-queue?limit=8&offset=${offset}&_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('D2审核队列暂不可用');
      const payload = await response.json() as RadarLeaderReviewQueueResponse;
      if (payload.schemaVersion !== 'radar-leader-review-queue-v1') {
        throw new Error('D2审核队列数据契约不匹配');
      }
      setLeaderReviewQueue(payload);
      leaderReviewQueueOffset.current = payload.offset;
      setSelectedReviewDocument(null);
      setLeaderReviewForm(null);
      setLeaderReviewSubmitMessage('');
      setLeaderReviewQueueError('');
    } catch (error) {
      setLeaderReviewQueueError(
        error instanceof Error ? error.message : 'D2审核队列暂不可用',
      );
    } finally {
      leaderReviewQueueInFlight.current = false;
      setLeaderReviewQueueLoading(false);
    }
  }, []);

  const loadLeaderReviewDocument = useCallback(async (
    item: RadarLeaderReviewDocument,
  ) => {
    const batchId = leaderReviewQueue?.summary.reviewBatchId;
    if (!batchId) return;
    setLeaderReviewQueueLoading(true);
    try {
      const params = new URLSearchParams({
        reviewBatchId: batchId,
        documentId: item.documentId,
        candidateCategory: item.candidateCategory,
        _t: String(Date.now()),
      });
      const response = await fetch(
        `/api/backend/api/radar/leaders/review-queue/document?${params}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('公告审核状态暂不可用');
      const payload = await response.json() as RadarLeaderReviewDocumentResponse;
      if (payload.schemaVersion !== 'radar-leader-review-document-v1') {
        throw new Error('公告审核状态契约不匹配');
      }
      setSelectedReviewDocument(payload.item);
      const formResponse = await fetch(
        `/api/backend/api/radar/leaders/review-queue/review-form?${params.toString()}`,
        { cache: 'no-store' },
      );
      if (!formResponse.ok) throw new Error('公告正文审核预览暂不可用');
      const formPayload = await formResponse.json() as RadarLeaderReviewFormResponse;
      if (formPayload.schemaVersion !== 'radar-leader-review-form-v1') {
        throw new Error('公告正文审核预览契约不匹配');
      }
      setLeaderReviewForm(formPayload);
      setLeaderReviewSubmitMessage('');
      setLeaderReviewQueueError('');
    } catch (error) {
      setLeaderReviewQueueError(
        error instanceof Error ? error.message : '公告审核状态暂不可用',
      );
    } finally {
      setLeaderReviewQueueLoading(false);
    }
  }, [leaderReviewQueue?.summary.reviewBatchId]);

  const submitLeaderReview = useCallback(async (
    draft: RadarLeaderReviewSubmissionDraft,
  ) => {
    if (!leaderReviewForm) return;
    setLeaderReviewSubmitting(true);
    setLeaderReviewSubmitMessage('');
    try {
      const response = await fetch(
        '/api/backend/api/radar/leaders/review-queue/review-version',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({
            reviewBatchId: leaderReviewForm.summary.reviewBatchId,
            documentId: leaderReviewForm.item.documentId,
            candidateCategory: leaderReviewForm.item.candidateCategory,
            contentSha256: leaderReviewForm.contentSha256,
            candidateId: leaderReviewForm.candidate.candidateId,
            ...draft,
            effectiveUntil: draft.effectiveUntil
              ? new Date(draft.effectiveUntil).toISOString()
              : null,
            targetEvent: {
              ...draft.targetEvent,
              publishedAt: new Date(draft.targetEvent.publishedAt).toISOString(),
              effectiveFrom: new Date(draft.targetEvent.effectiveFrom).toISOString(),
              effectiveUntil: draft.targetEvent.effectiveUntil
                ? new Date(draft.targetEvent.effectiveUntil).toISOString()
                : null,
            },
            factSupplements: draft.factSupplements.map((fact) => ({
              ...fact,
              mappedDocumentId: fact.factKind === 'referenced_document_id'
                ? draft.targetEvent.documentId
                : null,
            })),
          }),
        },
      );
      const payload = await response.json() as {
        detail?: string;
        reviewVersion?: string;
      };
      if (!response.ok) throw new Error(payload.detail || '人工审核版本提交失败');
      setLeaderReviewSubmitMessage(`已保存 ${payload.reviewVersion || '人工审核版本'}`);
      await loadLeaderReviewQueue(leaderReviewQueueOffset.current);
    } catch (error) {
      setLeaderReviewSubmitMessage(
        error instanceof Error ? error.message : '人工审核版本提交失败',
      );
    } finally {
      setLeaderReviewSubmitting(false);
    }
  }, [leaderReviewForm, loadLeaderReviewQueue]);

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
    const initialTimer = window.setTimeout(() => {
      void loadLeaders();
      void loadLeaderReviewQueue(0);
    }, 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadLeaders();
        void loadLeaderReviewQueue(leaderReviewQueueOffset.current);
      }
    }, 180_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [activeTab, loadLeaderReviewQueue, loadLeaders]);

  const refreshNow = async () => {
    await loadOverview(false);
    if (activeTab === 'sectors') await loadSectors();
    if (activeTab === 'etf') await loadEtfs();
    if (activeTab === 'leaders') {
      await loadLeaders();
      await loadLeaderReviewQueue(leaderReviewQueueOffset.current);
    }
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
  const etfSourceCoverage = etfModule.lastAttempt?.requiredFieldCoverage
    || etfModule.sources[0]?.requiredFieldCoverage
    || {};
  const etfRowCoverage = etfModule.lastAttempt?.rowCoverage
    ?? etfModule.sources[0]?.rowCoverage
    ?? 0;
  const etfReasonCodes = Array.from(new Set([
    ...etfModule.reasonCodes,
    ...etfModule.summary.reasonCodes,
  ]));
  const leaderCoverage = percent(leaderModule?.summary.coverage);
  const leaderFormalCount = leaderModule?.summary.formalUsableCount || 0;
  const leaderEligibleCount = leaderModule?.summary.eligibleCount || 0;
  const leaderSnapshotReady = Boolean(leaderModule?.lastSuccess);
  const leaderRuntimeEnabled = !('enabledStage' in overview.modules.leaders);
  const leaderReviewQueueReady = leaderModule?.reviewQueue?.status === 'ready';
  const leaderReviewVersionCount = leaderModule?.reviewQueue?.reviewVersionCount || 0;
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
              metrics={[
                { label: '产品主档', value: `${etfModule.summary.productCount} 只` },
                { label: '正式准入', value: `${etfModule.summary.eligibleProductCount} 只` },
                { label: '待补证据', value: `${etfModule.summary.missingCount} 只` },
                {
                  label: '计算覆盖',
                  value: `${Math.round(etfModule.summary.coverage * 100)}%`,
                },
              ]}
              progress={[
                {
                  label: '官方产品主档',
                  value: `${percent(etfRowCoverage)}%`,
                  detail: '交易所产品行覆盖',
                  percent: percent(etfRowCoverage),
                  tone: 'good',
                },
                {
                  label: '资产类别确认',
                  value: `${percent(etfSourceCoverage.etf_asset_class_confirmed)}%`,
                  detail: '仅采用可追溯分类证据',
                  percent: percent(etfSourceCoverage.etf_asset_class_confirmed),
                  tone: 'accent',
                },
                {
                  label: '管理方式确认',
                  value: `${percent(etfSourceCoverage.etf_management_style_confirmed)}%`,
                  detail: '主动 / 被动仍需官方证据',
                  percent: percent(etfSourceCoverage.etf_management_style_confirmed),
                  tone: 'warning',
                },
                {
                  label: '候选计算覆盖',
                  value: `${percent(etfModule.summary.coverage)}%`,
                  detail: '未满足门槛不生成候选',
                  percent: percent(etfModule.summary.coverage),
                  tone: 'warning',
                },
              ]}
              milestones={[
                {
                  label: '官方产品主档',
                  detail: `${etfModule.summary.productCount} 只产品已进入当前版本`,
                  status: etfModule.summary.productCount > 0 ? 'done' : 'active',
                },
                {
                  label: '产品分类与指数证据',
                  detail: '补齐管理方式、资产范围和指数身份',
                  status: etfModule.summary.eligibleProductCount > 0 ? 'done' : 'active',
                },
                {
                  label: '规则冻结与排名校准',
                  detail: '有真实样本后才能冻结正式规则',
                  status: etfModule.summary.ruleVersionId ? 'done' : 'pending',
                },
                {
                  label: '20个交易日影子验证',
                  detail: '观察达标后再申请正式启用',
                  status: etfModule.summary.formalStateEnabled ? 'done' : 'pending',
                },
              ]}
              blockers={blockerLabels(etfReasonCodes, ETF_REASON_LABELS)}
              nextStep="继续补齐产品分类与指数证据，同时保留阶段5影子观察。"
            />
            <ModuleStatePanel
              state={overview.modules.leaders.state}
              title="龙头梯队"
              description="总览只显示确定性状态机摘要，完整证据与失效条件进入龙头梯队页查看。"
              stage={'enabledStage' in overview.modules.leaders
                ? overview.modules.leaders.enabledStage
                : undefined}
              metrics={leaderModule ? [
                { label: '合资格', value: `${leaderModule.summary.eligibleCount} 只` },
                { label: '预备龙头', value: `${leaderModule.summary.preliminaryCount} 只` },
                { label: '候选龙头', value: `${leaderModule.summary.candidateCount} 只` },
                { label: '已确认', value: `${leaderModule.summary.confirmedCount} 只` },
              ] : [
                { label: '生产开关', value: '尚未启用' },
                { label: '影子快照', value: '未生成' },
                { label: '正式状态', value: '保持关闭' },
              ]}
              progress={[
                {
                  label: '阶段6影子入口',
                  value: leaderRuntimeEnabled ? '已启用' : '未启用',
                  detail: '研究链路开关，不等于正式状态可用',
                  percent: leaderRuntimeEnabled ? 100 : 0,
                  tone: leaderRuntimeEnabled ? 'good' : 'warning',
                },
                {
                  label: '影子输入覆盖',
                  value: `${leaderCoverage}%`,
                  detail: '仅计算已验证来源输入',
                  percent: leaderCoverage,
                  tone: leaderCoverage === 100 ? 'good' : 'accent',
                },
                {
                  label: '可用影子快照',
                  value: leaderSnapshotReady ? '已生成' : '未生成',
                  detail: '无快照时不展示梯队',
                  percent: leaderSnapshotReady ? 100 : 0,
                  tone: leaderSnapshotReady ? 'good' : 'warning',
                },
                {
                  label: '正式可用状态',
                  value: `${leaderFormalCount} 只`,
                  detail: '正式门禁通过的龙头数量',
                  percent: leaderEligibleCount > 0
                    ? (leaderFormalCount / leaderEligibleCount) * 100
                    : 0,
                  tone: leaderFormalCount > 0 ? 'good' : 'warning',
                },
              ]}
              milestones={[
                {
                  label: 'D2 官方来源验收',
                  detail: '385只冻结候选的七类风险公告',
                  status: leaderReviewQueueReady ? 'done' : 'active',
                },
                {
                  label: 'D8 连续人工审核版本',
                  detail: '至少两个真实连续版本，不复制补位',
                  status: leaderReviewVersionCount >= 2 ? 'done' : 'active',
                },
                {
                  label: '风险审核存储迁移',
                  detail: '生产迁移6与只增审核仓储',
                  status: leaderReviewQueueReady ? 'done' : 'pending',
                },
                {
                  label: '阶段6影子启用',
                  detail: '受控重载后才生成真实梯队快照',
                  status: leaderRuntimeEnabled ? 'done' : 'pending',
                },
              ]}
              blockers={[
                ...(!leaderRuntimeEnabled ? ['阶段6影子入口未启用'] : []),
                ...(leaderReviewVersionCount < 2 ? ['D8真实连续人工版本不足'] : []),
                ...(!leaderSnapshotReady ? ['龙头候选影子快照尚未生成'] : []),
              ]}
              nextStep={leaderReviewVersionCount === 0
                ? '基于已保存的3份真实正文形成第一个D8人工审核版本，后续再积累第二个连续版本。'
                : leaderReviewVersionCount === 1
                  ? '第一个真实D8人工版本已形成；等待包含实质证据变化的第二个连续版本，禁止复制补位。'
                  : 'D8连续人工版本门槛已具备，等待下一轮影子快照复核。'}
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
            <SectorObservationPanel
              module={sectors.module}
              full
              focusedIndustryCode={focusedIndustryCode}
              focusedIndustryName={focusedIndustryName}
            />
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
              reviewQueuePage={leaderReviewQueue}
              selectedReviewDocument={selectedReviewDocument}
              reviewForm={leaderReviewForm}
              reviewQueueLoading={leaderReviewQueueLoading}
              reviewQueueError={leaderReviewQueueError}
              reviewSubmitting={leaderReviewSubmitting}
              reviewSubmitMessage={leaderReviewSubmitMessage}
              onReviewPageChange={(offset) => void loadLeaderReviewQueue(offset)}
              onReviewDocumentSelect={(item) => void loadLeaderReviewDocument(item)}
              onReviewSubmit={(draft) => void submitLeaderReview(draft)}
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
