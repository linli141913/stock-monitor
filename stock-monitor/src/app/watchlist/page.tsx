'use client';

const API_BASE = '/api/backend';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useWatchlist } from '@/hooks/useWatchlist';
import type { MarketRisk } from '@/types/stock';
import styles from './page.module.css';


interface StockLiveData {
  stockCode: string;
  stockName: string;
  price: number | null;
  changePercent: number | null;
  volume: number | null;
  amount: number | null;
  risk: MarketRisk | null;
  addedAt: string;
  loading: boolean;
  error: boolean;
}

interface BatchOverviewItem {
  symbol: string;
  price?: number | string | null;
  changePercent?: number | null;
  volume?: number | null;
  amount?: number | null;
  risk?: MarketRisk | null;
}

interface AlertPreference {
  symbol: string;
  enabled: boolean;
  emailEnabled: boolean;
  p2Email: boolean;
}

interface PreferenceStatus {
  loading?: boolean;
  saving?: boolean;
  error?: string;
}

const RISK_LABELS: Record<MarketRisk['riskStatus'], string> = {
  normal: '正常',
  watch: '观察',
  warning: '警惕',
  critical: '紧急',
  unavailable: '暂无判断',
};

export default function WatchlistPage() {
  const router = useRouter();
  const { watchlist, removeFromWatchlist } = useWatchlist();
  const [liveData, setLiveData] = useState<StockLiveData[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [preferences, setPreferences] = useState<Record<string, AlertPreference>>({});
  const [preferenceStatus, setPreferenceStatus] = useState<Record<string, PreferenceStatus>>({});

  // 批量拉取实时行情
  const fetchLiveData = useCallback(async () => {
    if (watchlist.length === 0) return;
    const symbols = watchlist.map(i => i.stockCode).join(',');
    try {
      const res = await fetch(`${API_BASE}/api/stock/batch_overview?symbols=${symbols}&_t=${Date.now()}`, { headers: { 'ngrok-skip-browser-warning': 'true' }, cache: 'no-store' });
      if (!res.ok) throw new Error('fetch failed');
      const json = await res.json() as { data?: BatchOverviewItem[] };
      const quoteMap: Record<string, BatchOverviewItem> = {};
      (json.data || []).forEach((item) => {
        quoteMap[item.symbol] = item;
      });
      setLiveData(watchlist.map(item => {
        const real = quoteMap[item.stockCode];
        if (!real) {
          return { ...item, price: null, changePercent: null, volume: null, amount: null, risk: null, loading: false, error: true };
        }
        const price = real.price == null ? null : Number(real.price);
        return {
          ...item,
          price: price != null && Number.isFinite(price) ? price : null,
          changePercent: real.changePercent ?? null,
          volume: real.volume ?? null,
          amount: real.amount ?? null,
          risk: real.risk ?? null,
          loading: false,
          error: false,
        };
      }));
      setLastRefresh(new Date());
    } catch {
      setLiveData(prev => prev.map(d => ({ ...d, loading: false, error: true })));
    } finally {
      setRefreshing(false);
    }
  }, [watchlist]);

  useEffect(() => {
    if (watchlist.length > 0) {
      const timer = window.setTimeout(() => {
        void fetchLiveData();
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [watchlist, fetchLiveData]);

  useEffect(() => {
    if (watchlist.length === 0) {
      setPreferences({});
      setPreferenceStatus({});
      return;
    }
    const controller = new AbortController();
    const loadPreferences = async () => {
      const entries = await Promise.all(watchlist.map(async (item) => {
        try {
          const response = await fetch(
            `${API_BASE}/api/alerts/preferences?symbol=${encodeURIComponent(item.stockCode)}`,
            { cache: 'no-store', signal: controller.signal },
          );
          if (!response.ok) throw new Error(`读取失败 (${response.status})`);
          const payload = await response.json() as { data: AlertPreference };
          return [item.stockCode, payload.data, ''] as const;
        } catch (error) {
          if (controller.signal.aborted) return null;
          return [
            item.stockCode,
            null,
            error instanceof Error ? error.message : '读取失败',
          ] as const;
        }
      }));
      if (controller.signal.aborted) return;
      const nextPreferences: Record<string, AlertPreference> = {};
      const nextStatus: Record<string, PreferenceStatus> = {};
      entries.forEach((entry) => {
        if (!entry) return;
        const [symbol, preference, error] = entry;
        if (preference) nextPreferences[symbol] = preference;
        nextStatus[symbol] = error ? { error } : {};
      });
      setPreferences(nextPreferences);
      setPreferenceStatus(nextStatus);
    };
    setPreferenceStatus(Object.fromEntries(
      watchlist.map((item) => [item.stockCode, { loading: true }]),
    ));
    void loadPreferences();
    return () => controller.abort();
  }, [watchlist]);

  const handleRowClick = (stockCode: string) => {
    window.location.assign(`/?code=${stockCode}`);
  };

  const handleRefresh = () => {
    setRefreshing(true);
    void fetchLiveData();
  };

  const handleRemove = (e: React.MouseEvent, stockCode: string) => {
    e.stopPropagation();
    removeFromWatchlist(stockCode);
  };

  const updatePreference = async (
    stockCode: string,
    key: 'enabled' | 'emailEnabled' | 'p2Email',
    value: boolean,
  ) => {
    const current = preferences[stockCode];
    if (!current) return;
    const next = { ...current, [key]: value };
    setPreferenceStatus((status) => ({
      ...status,
      [stockCode]: { saving: true },
    }));
    try {
      const response = await fetch(`${API_BASE}/api/alerts/preferences`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
        cache: 'no-store',
      });
      const payload = await response.json() as { data?: AlertPreference; detail?: string };
      if (!response.ok || !payload.data) {
        throw new Error(payload.detail || `保存失败 (${response.status})`);
      }
      setPreferences((items) => ({ ...items, [stockCode]: payload.data as AlertPreference }));
      setPreferenceStatus((status) => ({ ...status, [stockCode]: {} }));
    } catch (error) {
      setPreferenceStatus((status) => ({
        ...status,
        [stockCode]: {
          error: error instanceof Error ? `保存失败：${error.message}` : '保存失败',
        },
      }));
    }
  };

  const formatPrice = (v: number | null | undefined) => v == null ? '-' : v.toFixed(2);
  const formatPct = (v: number | null | undefined) => {
    if (v == null) return '-';
    const num = v;
    const sign = num >= 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  };
  const formatAmount = (v: number | null | undefined, stockCode?: string) => {
    if (v == null) return '-';
    const num = v;
    const isHk = stockCode && (stockCode.toLowerCase().startsWith('hk') || (stockCode.length === 5 && !isNaN(Number(stockCode))));
    const unit = isHk ? '港元' : '元';
    if (num >= 1e8) return `${(num / 1e8).toFixed(2)}亿${unit}`;
    if (num >= 1e4) return `${(num / 1e4).toFixed(2)}万${unit}`;
    return num.toFixed(0) + unit;
  };

  const displayLiveData = watchlist.map((item) => liveData.find((liveItem) => liveItem.stockCode === item.stockCode) || {
    ...item,
    price: null,
    changePercent: null,
    volume: null,
    amount: null,
    risk: null,
    loading: true,
    error: false,
  });

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>📋 我的监测列表</h1>
          <p className={styles.subtitle}>
            {watchlist.length === 0
              ? '暂无监测股票，前往首页搜索后点击「加入监测」'
              : `共 ${watchlist.length} 只股票 · ${lastRefresh ? `最后刷新 ${lastRefresh.toLocaleTimeString('zh-CN')}` : '加载中…'}`}
          </p>
        </div>
        {watchlist.length > 0 && (
          <button
            className={`${styles.refreshBtn} ${refreshing ? styles.refreshing : ''}`}
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? '刷新中…' : '🔄 刷新行情'}
          </button>
        )}
      </div>

      {watchlist.length === 0 ? (
        <div className={styles.emptyState}>
        <div className={styles.emptyIcon}>📊</div>
        <p className={styles.emptyHint}>暂无监测股票</p>
        <button onClick={() => router.push('/')} className={styles.goHomeBtn} style={{ display: 'inline-block', padding: '8px 16px', background: '#3b82f6', color: 'white', borderRadius: '4px', border: 'none', cursor: 'pointer' }}>
          前往首页搜索添加
        </button>
      </div>
      ) : (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>股票名称</th>
                <th>代码</th>
                <th style={{ textAlign: 'right' }}>最新价</th>
                <th style={{ textAlign: 'right' }}>涨跌幅</th>
                <th style={{ textAlign: 'right' }}>成交额</th>
                <th style={{ textAlign: 'center' }}>风险状态</th>
                <th style={{ textAlign: 'center' }}>提醒设置</th>
                <th style={{ textAlign: 'center' }}>加入时间</th>
                <th style={{ textAlign: 'center' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {displayLiveData.map(item => {
                const isRise = item.changePercent != null && item.changePercent >= 0;
                const addedDate = new Date(item.addedAt).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
                return (
                  <tr
                    key={item.stockCode}
                    className={styles.row}
                    onClick={() => handleRowClick(item.stockCode)}
                  >
                    <td>
                      <div className={styles.stockName}>{item.stockName}</div>
                    </td>
                    <td>
                      <span className={styles.stockCode}>{item.stockCode}</span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {item.loading ? (
                        <span className={styles.skeleton} />
                      ) : item.error ? (
                        <span className={styles.errorText}>获取失败</span>
                      ) : (
                        <span className={`${styles.price} ${isRise ? styles.rise : styles.fall}`}>
                          {formatPrice(item.price)}
                        </span>
                      )}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {item.loading ? (
                        <span className={styles.skeleton} />
                      ) : item.error ? (
                        <span className={styles.errorText}>-</span>
                      ) : (
                        <span className={`${styles.changePct} ${isRise ? styles.rise : styles.fall}`}>
                          {item.changePercent != null && (
                            <span className={styles.changeTriangle}>{isRise ? '▲' : '▼'}</span>
                          )}
                          {formatPct(item.changePercent)}
                        </span>
                      )}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {item.loading ? <span className={styles.skeleton} /> : formatAmount(item.amount, item.stockCode)}
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      {item.loading ? (
                        <span className={styles.skeleton} />
                      ) : (
                        <div className={styles.riskCell}>
                          <span
                            className={`${styles.riskBadge} ${
                              item.risk?.riskStatus === 'critical'
                                ? styles.riskCritical
                                : item.risk?.riskStatus === 'warning'
                                  ? styles.riskWarning
                                  : item.risk?.riskStatus === 'watch'
                                    ? styles.riskWatch
                                    : item.risk?.riskStatus === 'normal'
                                      ? styles.riskNormal
                                      : styles.riskUnknown
                            }`}
                          >
                            {item.risk ? RISK_LABELS[item.risk.riskStatus] : '样本积累中'}
                          </span>
                          <span className={styles.riskReason}>
                            {item.risk?.reason || '换手率自身基线正在积累'}
                          </span>
                        </div>
                      )}
                    </td>
                    <td
                      className={styles.preferenceCell}
                      onClick={(event) => event.stopPropagation()}
                    >
                      {preferenceStatus[item.stockCode]?.loading ? (
                        <span className={styles.preferenceStatus}>读取中…</span>
                      ) : preferences[item.stockCode] ? (
                        <details className={styles.preferenceDetails}>
                          <summary>
                            {preferences[item.stockCode].enabled ? '提醒已启用' : '提醒已关闭'}
                          </summary>
                          <div className={styles.preferencePanel}>
                            <label>
                              <input
                                type="checkbox"
                                checked={preferences[item.stockCode].enabled}
                                disabled={preferenceStatus[item.stockCode]?.saving}
                                onChange={(event) => void updatePreference(
                                  item.stockCode,
                                  'enabled',
                                  event.target.checked,
                                )}
                              />
                              <span>启用提醒</span>
                            </label>
                            <label>
                              <input
                                type="checkbox"
                                checked={preferences[item.stockCode].emailEnabled}
                                disabled={
                                  !preferences[item.stockCode].enabled
                                  || preferenceStatus[item.stockCode]?.saving
                                }
                                onChange={(event) => void updatePreference(
                                  item.stockCode,
                                  'emailEnabled',
                                  event.target.checked,
                                )}
                              />
                              <span>发送邮件</span>
                            </label>
                            <label>
                              <input
                                type="checkbox"
                                checked={preferences[item.stockCode].p2Email}
                                disabled={
                                  !preferences[item.stockCode].enabled
                                  || !preferences[item.stockCode].emailEnabled
                                  || preferenceStatus[item.stockCode]?.saving
                                }
                                onChange={(event) => void updatePreference(
                                  item.stockCode,
                                  'p2Email',
                                  event.target.checked,
                                )}
                              />
                              <span>P2即时邮件</span>
                            </label>
                            <p>P1始终站内提醒；邮件能否送达取决于真实 SMTP 配置。</p>
                          </div>
                        </details>
                      ) : (
                        <span className={styles.preferenceError}>
                          {preferenceStatus[item.stockCode]?.error || '设置不可用'}
                        </span>
                      )}
                      {preferenceStatus[item.stockCode]?.saving && (
                        <span className={styles.preferenceStatus}>保存中…</span>
                      )}
                      {preferenceStatus[item.stockCode]?.error && preferences[item.stockCode] && (
                        <span className={styles.preferenceError}>
                          {preferenceStatus[item.stockCode].error}
                        </span>
                      )}
                    </td>
                    <td style={{ textAlign: 'center', color: '#6b7280', fontSize: '12px' }}>
                      {addedDate}
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <button
                        className={styles.removeBtn}
                        onClick={(e) => handleRemove(e, item.stockCode)}
                      >
                        移除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className={styles.disclaimer}>
        本系统采用准实时公开数据源，不构成投资建议，不适用于高频交易
      </p>
    </div>
  );
}
