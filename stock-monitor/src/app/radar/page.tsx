'use client';

import {
  Activity,
  Database,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import MarketContextPanel from '@/components/radar/MarketContextPanel';
import RadarAiInsightLoader from '@/components/radar/RadarAiInsightLoader';
import LeaderObservationPanel from '@/components/radar/LeaderObservationPanel';
import ModuleStatePanel from '@/components/radar/ModuleStatePanel';
import RadarDataGatePanel from '@/components/radar/RadarDataGatePanel';
import RadarReplayQualityPanel from '@/components/radar/RadarReplayQualityPanel';
import RadarStage10ReadinessPanel from '@/components/radar/RadarStage10ReadinessPanel';
import RadarStatusStrip from '@/components/radar/RadarStatusStrip';
import EtfObservationPanel from '@/components/radar/EtfObservationPanel';
import SectorObservationPanel from '@/components/radar/SectorObservationPanel';
import type {
  RadarEtfsResponse,
  RadarLeaderModule,
  RadarLeaderReviewQueueResponse,
  RadarLeadersResponse,
  RadarOverviewResponse,
  RadarFormalReadinessResponse,
  RadarFormalShadowProgressResponse,
  RadarReplayEtfResearchResponse,
  RadarReplayQualityResponse,
  RadarSectorsResponse,
  RadarSectorHistoryResponse,
} from '@/types/radar';
import type { RadarAiScopeType } from '@/types/radar-ai';
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

function formatDateTime(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
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
  const [sectorHistory, setSectorHistory] = useState<
    RadarSectorHistoryResponse | null
  >(null);
  const [replayQuality, setReplayQuality] = useState<
    RadarReplayQualityResponse | null
  >(null);
  const [etfResearch, setEtfResearch] = useState<
    RadarReplayEtfResearchResponse | null
  >(null);
  const [formalReadiness, setFormalReadiness] = useState<
    RadarFormalReadinessResponse | null
  >(null);
  const [formalShadowProgress, setFormalShadowProgress] = useState<
    RadarFormalShadowProgressResponse | null
  >(null);
  const [leaderReviewQueue, setLeaderReviewQueue] = useState<
    RadarLeaderReviewQueueResponse | null
  >(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [sectorsRefreshError, setSectorsRefreshError] = useState('');
  const [etfsRefreshError, setEtfsRefreshError] = useState('');
  const [etfResearchRefreshError, setEtfResearchRefreshError] = useState('');
  const [leadersRefreshError, setLeadersRefreshError] = useState('');
  const [historyRefreshError, setHistoryRefreshError] = useState('');
  const [replayRefreshError, setReplayRefreshError] = useState('');
  const [formalReadinessRefreshError, setFormalReadinessRefreshError] = useState('');
  const [formalShadowProgressRefreshError, setFormalShadowProgressRefreshError] = useState('');
  const [leaderReviewQueueError, setLeaderReviewQueueError] = useState('');
  const [leaderReviewQueueLoading, setLeaderReviewQueueLoading] = useState(false);
  const [renderedAt, setRenderedAt] = useState<string | null>(null);
  const overviewInFlight = useRef(false);
  const sectorsInFlight = useRef(false);
  const etfsInFlight = useRef(false);
  const etfResearchInFlight = useRef(false);
  const leadersInFlight = useRef(false);
  const historyInFlight = useRef(false);
  const replayInFlight = useRef(false);
  const formalReadinessInFlight = useRef(false);
  const formalShadowProgressInFlight = useRef(false);
  const historyTabActive = useRef(false);
  const mounted = useRef(true);
  const replayRequestGeneration = useRef(0);
  const formalReadinessRequestGeneration = useRef(0);
  const formalShadowProgressRequestGeneration = useRef(0);
  const replayAbortController = useRef<AbortController | null>(null);
  const formalReadinessAbortController = useRef<AbortController | null>(null);
  const formalShadowProgressAbortController = useRef<AbortController | null>(null);
  const leaderReviewQueueInFlight = useRef(false);
  const leaderReviewQueueOffset = useRef(0);

  const aiTarget = useMemo(() => {
    if (activeTab === 'overview') {
      return { scopeType: 'market' as RadarAiScopeType, scopeId: 'overview', path: 'overview', title: '雷达AI解读 · 市场环境' };
    }
    if (activeTab === 'sectors') {
      return focusedIndustryCode
        ? { scopeType: 'sector' as RadarAiScopeType, scopeId: focusedIndustryCode, path: `sectors/${encodeURIComponent(focusedIndustryCode)}`, title: `雷达AI解读 · ${focusedIndustryName || focusedIndustryCode}` }
        : null;
    }
    if (activeTab === 'etf') {
      const symbol = etfs?.module.candidates[0]?.representativeSymbol
        || '';
      return symbol
        ? { scopeType: 'etf' as RadarAiScopeType, scopeId: symbol, path: `etfs/${symbol}`, title: `雷达AI解读 · ETF ${symbol}` }
        : null;
    }
    if (activeTab === 'leaders') {
      const symbol = leaders?.module.preliminary[0]?.symbol
        || leaders?.module.candidates[0]?.symbol
        || leaders?.module.confirmed[0]?.symbol
        || '';
      return symbol
        ? { scopeType: 'leader' as RadarAiScopeType, scopeId: symbol, path: `leaders/${symbol}`, title: `雷达AI解读 · 龙头 ${symbol}` }
        : null;
    }
    return null;
  }, [
    activeTab,
    etfs?.module.candidates,
    focusedIndustryCode,
    focusedIndustryName,
    leaders?.module.candidates,
    leaders?.module.confirmed,
    leaders?.module.preliminary,
  ]);

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

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      historyTabActive.current = false;
      replayRequestGeneration.current += 1;
      formalReadinessRequestGeneration.current += 1;
      formalShadowProgressRequestGeneration.current += 1;
      replayAbortController.current?.abort();
      formalReadinessAbortController.current?.abort();
      formalShadowProgressAbortController.current?.abort();
      replayAbortController.current = null;
      formalReadinessAbortController.current = null;
      formalShadowProgressAbortController.current = null;
    };
  }, []);

  const loadOverview = useCallback(async (silent = false) => {
    if (overviewInFlight.current) return;
    overviewInFlight.current = true;
    if (!silent) setRefreshing(true);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 15_000);
    try {
      const response = await fetch(
        `/api/backend/api/radar/overview?_t=${Date.now()}`,
        { cache: 'no-store', signal: controller.signal },
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
        error instanceof Error && error.name === 'AbortError'
          ? '主线雷达请求超时，请重试'
          : error instanceof Error ? error.message : '主线雷达数据暂不可用',
      );
    } finally {
      window.clearTimeout(timeoutId);
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

  const loadEtfResearch = useCallback(async () => {
    if (etfResearchInFlight.current) return;
    etfResearchInFlight.current = true;
    try {
      const response = await fetch(
        `/api/backend/api/radar/replays/latest/etfs?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('ETF逐产品研究状态暂不可用');
      const payload = await response.json() as RadarReplayEtfResearchResponse;
      if (payload.schemaVersion !== 'radar-replay-etf-research-v1') {
        throw new Error('ETF研究状态数据契约不匹配');
      }
      setEtfResearch(payload);
      setEtfResearchRefreshError('');
    } catch (error) {
      setEtfResearchRefreshError(
        error instanceof Error ? error.message : 'ETF逐产品研究状态暂不可用',
      );
    } finally {
      etfResearchInFlight.current = false;
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

  const loadSectorHistory = useCallback(async () => {
    if (historyInFlight.current) return;
    historyInFlight.current = true;
    try {
      const response = await fetch(
        `/api/backend/api/radar/sector-history?_t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('行业历史真实快照暂不可用');
      const payload = await response.json() as RadarSectorHistoryResponse;
      if (payload.schemaVersion !== 'radar-sector-history-v1') {
        throw new Error('行业历史数据契约不匹配');
      }
      setSectorHistory(payload);
      setRenderedAt(new Date().toISOString());
      setHistoryRefreshError('');
    } catch (error) {
      setHistoryRefreshError(
        error instanceof Error ? error.message : '行业历史真实快照暂不可用',
      );
    } finally {
      historyInFlight.current = false;
    }
  }, []);

  const loadReplayQuality = useCallback(async (force = false) => {
    if (replayInFlight.current && !force) return;
    replayRequestGeneration.current += 1;
    const requestGeneration = replayRequestGeneration.current;
    replayAbortController.current?.abort();
    const controller = new AbortController();
    replayAbortController.current = controller;
    replayInFlight.current = true;
    const canCommit = () => (
      mounted.current
      && historyTabActive.current
      && requestGeneration === replayRequestGeneration.current
    );
    try {
      const response = await fetch(
        `/api/backend/api/radar/replays/latest?_t=${Date.now()}`,
        { cache: 'no-store', signal: controller.signal },
      );
      if (!response.ok) throw new Error('历史回放质量报告暂不可用');
      const payload = await response.json() as RadarReplayQualityResponse;
      if (payload.schemaVersion !== 'radar-replay-quality-v1') {
        throw new Error('历史回放数据契约不匹配');
      }
      if (!canCommit()) return;
      setReplayQuality(payload);
      setRenderedAt(new Date().toISOString());
      setReplayRefreshError('');
    } catch (error) {
      if (!canCommit()) return;
      if (error instanceof Error && error.name === 'AbortError') return;
      setReplayRefreshError(
        error instanceof Error ? error.message : '历史回放质量报告暂不可用',
      );
    } finally {
      if (requestGeneration === replayRequestGeneration.current) {
        replayInFlight.current = false;
        replayAbortController.current = null;
      }
    }
  }, []);

  const loadFormalReadiness = useCallback(async (force = false) => {
    if (formalReadinessInFlight.current && !force) return;
    formalReadinessRequestGeneration.current += 1;
    const requestGeneration = formalReadinessRequestGeneration.current;
    formalReadinessAbortController.current?.abort();
    const controller = new AbortController();
    formalReadinessAbortController.current = controller;
    formalReadinessInFlight.current = true;
    const canCommit = () => (
      mounted.current
      && historyTabActive.current
      && requestGeneration === formalReadinessRequestGeneration.current
    );
    if (canCommit()) {
      setFormalReadiness(null);
      setFormalReadinessRefreshError('');
    }
    try {
      const response = await fetch(
        `/api/backend/api/radar/formal-readiness?_t=${Date.now()}`,
        { cache: 'no-store', signal: controller.signal },
      );
      if (!response.ok) throw new Error('正式启用准备度报告暂不可用');
      const payload = await response.json() as RadarFormalReadinessResponse;
      if (payload.contractVersion !== 'radar-formal-readiness-v1') {
        throw new Error('正式启用准备度数据契约不匹配');
      }
      if (!canCommit()) return;
      setFormalReadiness(payload);
      setRenderedAt(new Date().toISOString());
    } catch (error) {
      if (!canCommit()) return;
      setFormalReadiness(null);
      if (error instanceof Error && error.name === 'AbortError') {
        setFormalReadinessRefreshError('');
        return;
      }
      setFormalReadinessRefreshError(
        error instanceof Error ? error.message : '正式启用准备度报告暂不可用',
      );
    } finally {
      if (requestGeneration === formalReadinessRequestGeneration.current) {
        formalReadinessInFlight.current = false;
        formalReadinessAbortController.current = null;
      }
    }
  }, []);

  const loadFormalShadowProgress = useCallback(async (force = false) => {
    if (formalShadowProgressInFlight.current && !force) return;
    formalShadowProgressRequestGeneration.current += 1;
    const requestGeneration = formalShadowProgressRequestGeneration.current;
    formalShadowProgressAbortController.current?.abort();
    const controller = new AbortController();
    formalShadowProgressAbortController.current = controller;
    formalShadowProgressInFlight.current = true;
    const canCommit = () => (
      mounted.current
      && historyTabActive.current
      && requestGeneration === formalShadowProgressRequestGeneration.current
    );
    if (canCommit()) {
      setFormalShadowProgress(null);
      setFormalShadowProgressRefreshError('');
    }
    try {
      const response = await fetch(
        `/api/backend/api/radar/formal-shadow-progress?_t=${Date.now()}`,
        { cache: 'no-store', signal: controller.signal },
      );
      if (!response.ok) throw new Error('真实影子进度暂不可用');
      const payload = await response.json() as RadarFormalShadowProgressResponse;
      if (payload.contractVersion !== 'radar-formal-shadow-progress-v1') {
        throw new Error('真实影子进度数据契约不匹配');
      }
      if (!canCommit()) return;
      setFormalShadowProgress(payload);
      setRenderedAt(new Date().toISOString());
    } catch (error) {
      if (!canCommit()) return;
      setFormalShadowProgress(null);
      if (error instanceof Error && error.name === 'AbortError') {
        setFormalShadowProgressRefreshError('');
        return;
      }
      setFormalShadowProgressRefreshError(
        error instanceof Error ? error.message : '真实影子进度暂不可用',
      );
    } finally {
      if (requestGeneration === formalShadowProgressRequestGeneration.current) {
        formalShadowProgressInFlight.current = false;
        formalShadowProgressAbortController.current = null;
      }
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
      if (!response.ok) throw new Error('官方公告清单暂不可用');
      const payload = await response.json() as RadarLeaderReviewQueueResponse;
      if (payload.schemaVersion !== 'radar-leader-review-queue-v1') {
        throw new Error('官方公告清单数据契约不匹配');
      }
      setLeaderReviewQueue(payload);
      leaderReviewQueueOffset.current = payload.offset;
      setLeaderReviewQueueError('');
    } catch (error) {
      setLeaderReviewQueueError(
        error instanceof Error ? error.message : '官方公告清单暂不可用',
      );
    } finally {
      leaderReviewQueueInFlight.current = false;
      setLeaderReviewQueueLoading(false);
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
    const initialTimer = window.setTimeout(() => {
      void loadEtfs();
      void loadEtfResearch();
    }, 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadEtfs();
        void loadEtfResearch();
      }
    }, 180_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [activeTab, loadEtfResearch, loadEtfs]);

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

  useEffect(() => {
    if (activeTab !== 'history') return;
    historyTabActive.current = true;
    const initialTimer = window.setTimeout(() => {
      void loadSectorHistory();
      void loadReplayQuality();
      void loadFormalReadiness();
      void loadFormalShadowProgress();
    }, 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadSectorHistory();
        void loadReplayQuality();
        void loadFormalReadiness();
        void loadFormalShadowProgress();
      }
    }, 300_000);
    return () => {
      historyTabActive.current = false;
      replayRequestGeneration.current += 1;
      formalReadinessRequestGeneration.current += 1;
      formalShadowProgressRequestGeneration.current += 1;
      replayAbortController.current?.abort();
      formalReadinessAbortController.current?.abort();
      formalShadowProgressAbortController.current?.abort();
      replayAbortController.current = null;
      formalReadinessAbortController.current = null;
      formalShadowProgressAbortController.current = null;
      replayInFlight.current = false;
      formalReadinessInFlight.current = false;
      formalShadowProgressInFlight.current = false;
      setReplayQuality(null);
      setReplayRefreshError('');
      setFormalReadiness(null);
      setFormalReadinessRefreshError('');
      setFormalShadowProgress(null);
      setFormalShadowProgressRefreshError('');
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [
    activeTab,
    loadFormalReadiness,
    loadFormalShadowProgress,
    loadReplayQuality,
    loadSectorHistory,
  ]);

  const refreshNow = async () => {
    await loadOverview(false);
    if (activeTab === 'sectors') await loadSectors();
    if (activeTab === 'etf') {
      await Promise.all([loadEtfs(), loadEtfResearch()]);
    }
    if (activeTab === 'leaders') {
      await loadLeaders();
      await loadLeaderReviewQueue(leaderReviewQueueOffset.current);
    }
    if (activeTab === 'history') {
      await Promise.all([
        loadSectorHistory(),
        loadReplayQuality(true),
        loadFormalReadiness(true),
        loadFormalShadowProgress(true),
      ]);
    }
  };

  const showTab = (tab: RadarTab) => {
    if (tab === 'history' && activeTab !== 'history') {
      setReplayQuality(null);
      setReplayRefreshError('');
      setFormalReadiness(null);
      setFormalReadinessRefreshError('');
      setFormalShadowProgress(null);
      setFormalShadowProgressRefreshError('');
    }
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
  const leaderRiskScanReady = leaderModule?.reviewQueue?.status === 'ready';
  const leaderObservationReady = Boolean(
    leaderModule?.observation.displayAllowed
    && leaderModule.observation.candidateCount > 0,
  );
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
          <p>市场、行业、ETF与龙头观察按真实数据范围展示。</p>
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
          <span><b>{leaderModule?.summary.eligibleCount ?? '—'}</b> 龙头观察候选</span>
          <span className={anomalyCount ? styles.anomalyStat : ''}><b>{anomalyCount}</b> 模块异常</span>
        </div>
      </section>

      {refreshError && (
        <div className={styles.refreshError}>
          {refreshError}。当前保留上一轮成功页面内容。
        </div>
      )}

      {activeTab !== 'history' && (
        <RadarAiInsightLoader
          key={aiTarget ? `${aiTarget.scopeType}:${aiTarget.scopeId}` : activeTab}
          target={aiTarget}
        />
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
                { label: '完整证据', value: `${etfModule.summary.eligibleProductCount} 只` },
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
                  detail: '观察达标后自动标记为已验证',
                  status: etfModule.summary.formalStateEnabled ? 'done' : 'pending',
                },
              ]}
              blockers={blockerLabels(etfReasonCodes, ETF_REASON_LABELS)}
              nextStep="继续补齐产品分类与指数证据，同时保留阶段5影子观察。"
            />
            <ModuleStatePanel
              state={overview.modules.leaders.state}
              title="龙头梯队"
              description="总览优先显示真实观察候选；证据完整时再附加确定性三级状态。"
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
                { label: '三级状态', value: '尚未形成' },
              ]}
              progress={[
                {
                  label: '阶段6影子入口',
                  value: leaderRuntimeEnabled ? '已启用' : '未启用',
                  detail: '用于生成真实观察候选',
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
                  label: '真实观察快照',
                  value: leaderObservationReady ? '已生成' : '未生成',
                  detail: '健康行情与已确认行业映射形成后即可展示',
                  percent: leaderObservationReady ? 100 : 0,
                  tone: leaderObservationReady ? 'good' : 'warning',
                },
                {
                  label: '三级规则状态',
                  value: leaderSnapshotReady ? `${leaderFormalCount} 只已评级` : '尚未形成',
                  detail: '这是附加研究层，不阻塞观察候选展示',
                  percent: leaderEligibleCount > 0
                    ? (leaderFormalCount / leaderEligibleCount) * 100
                    : 0,
                  tone: leaderFormalCount > 0 ? 'good' : 'warning',
                },
              ]}
              milestones={[
                {
                  label: '真实全市场观察',
                  detail: '健康行情与已确认行业映射形成当轮候选',
                  status: leaderObservationReady ? 'done' : 'active',
                },
                {
                  label: '官方公告扫描',
                  detail: '展示官方来源、时间和实际覆盖，不冒充确定风险',
                  status: leaderRiskScanReady ? 'done' : 'active',
                },
                {
                  label: '监测结果直接展示',
                  detail: '观察候选与官方公告可直接查看',
                  status: 'done',
                },
                {
                  label: '三级规则研究',
                  detail: '证据完整后附加评分和状态，仍不触发交易',
                  status: leaderSnapshotReady ? 'done' : 'pending',
                },
              ]}
              blockers={[
                ...(!leaderRuntimeEnabled ? ['阶段6影子入口未启用'] : []),
                ...(!leaderObservationReady ? ['真实观察候选快照尚未生成'] : []),
              ]}
              nextStep={leaderObservationReady
                ? '观察候选已可用；继续补充确定性研究证据，不阻塞当前监测。'
                : '等待下一轮健康全市场行情自动形成真实观察候选。'}
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
              research={etfResearch}
              researchLoadError={etfResearchRefreshError}
              formatTime={formatTime}
              renderedAt={renderedAt}
            />
          ) : (
            <EtfObservationPanel
              module={etfModule}
              research={etfResearch}
              researchLoadError={etfResearchRefreshError}
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
              reviewQueueLoading={leaderReviewQueueLoading}
              reviewQueueError={leaderReviewQueueError}
              onReviewPageChange={(offset) => void loadLeaderReviewQueue(offset)}
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
        <>
          <RadarStage10ReadinessPanel
            data={formalReadiness}
            loadError={formalReadinessRefreshError}
            shadowProgress={formalShadowProgress}
            shadowProgressLoadError={formalShadowProgressRefreshError}
          />
          <RadarReplayQualityPanel
            data={replayQuality}
            loadError={replayRefreshError}
          />
          {historyRefreshError && (
            <div className={styles.refreshError}>
              {historyRefreshError}。没有用旧数据冒充本轮成功。
            </div>
          )}
          <ModuleStatePanel
            state={sectorHistory?.state || (historyRefreshError ? 'failed' : 'not_ready')}
            title="行业历史与阈值校准"
            description={sectorHistory?.state === 'available'
              ? `真实公开源历史已持久化。数据时点 ${formatDateTime(sectorHistory.asOf)}，发布到项目运行仓 ${formatDateTime(sectorHistory.publishedAt)}。`
              : '正在读取项目运行仓中的真实历史汇总，不触发全市场重新抓取。'}
            metrics={sectorHistory ? [
              { label: '证券覆盖', value: `${sectorHistory.requestedCount.toLocaleString('zh-CN')} 只` },
              { label: '行业覆盖', value: `${sectorHistory.sectorCount} 个` },
              { label: '市场样本', value: `${sectorHistory.marketSampleCount} 日` },
              {
                label: '单指标样本',
                value: `${(sectorHistory.metricSampleCounts.relativeReturn || 0).toLocaleString('zh-CN')} 训练 / ${(sectorHistory.holdoutMetricSampleCounts.relativeReturn || 0).toLocaleString('zh-CN')} 留出`,
              },
            ] : []}
            progress={sectorHistory?.state === 'available' ? [
              {
                label: '全市场分钟历史',
                value: `${sectorHistory.requestedCount - sectorHistory.failureCount}/${sectorHistory.requestedCount}`,
                detail: '真实在线历史或同内容断点复用',
                percent: sectorHistory.requestedCount
                  ? ((sectorHistory.requestedCount - sectorHistory.failureCount) / sectorHistory.requestedCount) * 100
                  : 0,
                tone: 'good',
              },
              {
                label: '分钟缺口日线核验',
                value: `${sectorHistory.tradingPresenceReturnedCount}/${sectorHistory.tradingPresenceRequestedCount}`,
                detail: '只用腾讯/新浪真实日线证明交易或停牌',
                percent: sectorHistory.tradingPresenceRequestedCount
                  ? (sectorHistory.tradingPresenceReturnedCount / sectorHistory.tradingPresenceRequestedCount) * 100
                  : 100,
                tone: 'good',
              },
              {
                label: '阈值校准观察窗',
                value: `${sectorHistory.observationDateCount}/20 日`,
                detail: `训练 ${sectorHistory.trainObservationDateCount} 日（截止 ${sectorHistory.trainEndDate || '—'}），留出 ${sectorHistory.holdoutObservationDateCount} 日（开始 ${sectorHistory.holdoutStartDate || '—'}）`,
                percent: Math.min(100, (sectorHistory.observationDateCount / 20) * 100),
                tone: sectorHistory.observationDateCount >= 20 ? 'good' : 'warning',
              },
            ] : []}
            milestones={sectorHistory?.state === 'available' ? [
              {
                label: '真实历史主动回填',
                detail: `${sectorHistory.sectorCount} 个行业，${sectorHistory.marketSampleCount} 个市场日期样本`,
                status: sectorHistory.historyCoverageReady ? 'done' : 'active',
              },
              {
                label: '自动校准提案',
                detail: `${sectorHistory.industryCount} 个行业，覆盖 ${sectorHistory.marketRegimes.join(' / ') || '未知'} 市场状态`,
                status: sectorHistory.calibrationStatus === 'proposal_ready' ? 'done' : 'active',
              },
              {
                label: '版本化规则状态',
                detail: sectorHistory.formalApproval
                  ? `已加载阈值集 ${sectorHistory.thresholdSetId || '—'}，更新于 ${formatDateTime(sectorHistory.approvedAt)}`
                  : `当前校准版本 ${sectorHistory.calibrationIdentity?.slice(0, 12) || '—'}，系统继续自动校验`,
                status: sectorHistory.formalApproval ? 'done' : 'pending',
              },
            ] : []}
            blockers={sectorHistory?.state === 'available' && !sectorHistory.formalApproval
              ? [
                '真实训练集与留出集已经严格隔离；八状态策略版本尚未完成校验',
                ...sectorHistory.thresholdReviewReasonCodes,
              ]
              : sectorHistory?.reasonCodes || []}
            nextStep={sectorHistory?.state === 'available'
              ? (sectorHistory.formalApproval
                ? '规则版本已可由行业监测自动加载；其他真实数据继续独立校验'
                : '系统继续用真实样本自动校验八状态进入、保持与退出策略')
              : '运行全自动历史同步后，本页将直接读取持久化结果'}
            badges={[
              '不展示虚假胜率',
              '不生成收益承诺',
              sectorHistory?.gate.formalGateReady ? '正式门已通过' : '正式门保持关闭',
            ]}
          />
        </>
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
