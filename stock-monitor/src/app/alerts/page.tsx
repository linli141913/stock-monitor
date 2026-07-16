'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { AlertDelivery, AlertDirection, AlertEvent } from '@/types/alert-event';
import styles from './page.module.css';

const DIRECTION_LABELS: Record<AlertDirection, string> = {
  positive: '重大正面',
  negative: '风险提醒',
  neutral: '重要中性',
  uncertain: '待核验',
};

type AlertViewMode = 'today' | 'unread' | 'history';

function formatTime(value: string | null) {
  if (!value) return '时间暂缺';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN', { hour12: false });
}

function isDateOnlyTimestamp(value: string) {
  return /T00:00:00(?:Z|[+-]\d{2}:\d{2})?$/.test(value);
}

function formatAlertSourceTime(alert: AlertEvent) {
  const discovered = `系统发现：${formatTime(alert.triggeredAt)}`;
  if (!alert.publishedAt) return discovered;
  if (isDateOnlyTimestamp(alert.publishedAt)) {
    return `公告日期：${alert.publishedAt.slice(0, 10)}（具体时刻未提供） · ${discovered}`;
  }
  return `发布时间：${formatTime(alert.publishedAt)} · ${discovered}`;
}

function getEmailDelivery(deliveries: AlertDelivery[]) {
  return deliveries.find((item) => item.channel === 'email');
}

function deliveryText(delivery: AlertDelivery | undefined) {
  if (!delivery) return '仅站内提醒';
  if (delivery.status === 'sent') return '邮件已送达';
  if (delivery.status === 'not_configured') return '邮件未配置';
  if (delivery.status === 'failed') return '邮件发送失败';
  return '邮件等待发送';
}

function canOpenStockExplanation(symbol: string) {
  return /^(?:(?:sh|sz|bj)\d{6}|hk\d{5}|\d{6})$/i.test(symbol);
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [viewMode, setViewMode] = useState<AlertViewMode>('today');
  const [todayUnreadCount, setTodayUnreadCount] = useState(0);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const loadAlerts = useCallback(async () => {
    try {
      const scope = viewMode === 'history' ? 'history' : 'today';
      const timestamp = Date.now();
      const [response, unreadResponse] = await Promise.all([
        fetch(
          `/api/backend/api/alerts?scope=${scope}&_t=${timestamp}`,
          { cache: 'no-store' },
        ),
        fetch(
          `/api/backend/api/alerts/unread-count?_t=${timestamp}`,
          { cache: 'no-store' },
        ),
      ]);
      if (!response.ok) throw new Error(`提醒请求失败 (${response.status})`);
      if (!unreadResponse.ok) throw new Error(`未读数请求失败 (${unreadResponse.status})`);
      const payload = await response.json() as { data?: AlertEvent[] };
      const unreadPayload = await unreadResponse.json() as { count?: number };
      setAlerts(payload.data || []);
      setTodayUnreadCount(unreadPayload.count || 0);
      setError('');
      setLastRefresh(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : '提醒加载失败');
    } finally {
      setLoading(false);
    }
  }, [viewMode]);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void loadAlerts(), 0);
    const timer = window.setInterval(() => void loadAlerts(), 60_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [loadAlerts]);

  const markRead = async (alertId: string) => {
    const response = await fetch(`/api/backend/api/alerts/${alertId}/read`, {
      method: 'PATCH',
      cache: 'no-store',
    });
    if (!response.ok) {
      setError(`标记已读失败 (${response.status})`);
      return;
    }
    setAlerts((current) => current.map((item) => (
      item.id === alertId ? { ...item, isRead: true } : item
    )));
    const unreadResponse = await fetch(
      '/api/backend/api/alerts/unread-count',
      { cache: 'no-store' },
    );
    if (unreadResponse.ok) {
      const unreadPayload = await unreadResponse.json() as { count?: number };
      setTodayUnreadCount(unreadPayload.count || 0);
    }
    window.dispatchEvent(new Event('alerts:unread-changed'));
  };

  const visibleAlerts = useMemo(
    () => viewMode === 'unread' ? alerts.filter((item) => !item.isRead) : alerts,
    [alerts, viewMode],
  );
  const emptyTitle = viewMode === 'history' ? '暂无历史提醒' : '暂无提醒';
  const emptyDescription = viewMode === 'history'
    ? '系统触发过的提醒会保留在这里，方便后续回看。'
    : '监测系统会在发现官方重大事件或风险信号后记录在这里。';

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>自选股信号台</p>
          <h1>强提醒中心</h1>
          <p className={styles.subtitle}>
            只展示有来源、有时间、有触发依据的事件；正面和负面同等提醒。
          </p>
        </div>
        <div className={styles.summary} aria-live="polite">
          <span><strong>{todayUnreadCount}</strong> 条今日未读</span>
          <span>{lastRefresh ? `更新于 ${lastRefresh.toLocaleTimeString('zh-CN')}` : '等待首次更新'}</span>
        </div>
      </section>

      <section className={styles.toolbar}>
        <div className={styles.tabs}>
          <button
            type="button"
            className={viewMode === 'today' ? styles.activeTab : ''}
            onClick={() => setViewMode('today')}
          >
            今日提醒
          </button>
          <button
            type="button"
            className={viewMode === 'unread' ? styles.activeTab : ''}
            onClick={() => setViewMode('unread')}
          >
            只看未读
          </button>
          <button
            type="button"
            className={viewMode === 'history' ? styles.activeTab : ''}
            onClick={() => setViewMode('history')}
          >
            历史记录
          </button>
        </div>
        <button type="button" className={styles.refreshButton} onClick={() => void loadAlerts()}>
          刷新提醒
        </button>
      </section>

      {error && <div className={styles.error}>{error}</div>}

      {loading ? (
        <div className={styles.empty}>提醒加载中…</div>
      ) : visibleAlerts.length === 0 ? (
        <div className={styles.empty}>
          <strong>{emptyTitle}</strong>
          <span>{emptyDescription}</span>
        </div>
      ) : (
        <section className={styles.alertList}>
          {visibleAlerts.map((alert) => {
            const emailDelivery = getEmailDelivery(alert.deliveries || []);
            return (
              <article
                key={alert.id}
                className={`${styles.alertCard} ${styles[alert.direction]} ${alert.isRead ? styles.read : ''}`}
              >
                <div className={styles.alertRail} aria-hidden="true" />
                <div className={styles.alertContent}>
                  <div className={styles.alertMeta}>
                    <span className={styles.priority}>{alert.priority}</span>
                    <span className={styles.direction}>{DIRECTION_LABELS[alert.direction]}</span>
                    <span>{alert.stockName} · {alert.symbol}</span>
                    <span>证据 {alert.evidenceLevel}</span>
                  </div>
                  <h2>{alert.title}</h2>
                  <p>{alert.summary}</p>
                  <div className={styles.sourceRow}>
                    <span>{alert.source} · {formatAlertSourceTime(alert)}</span>
                    {alert.eventType === 'market_risk' ? (
                      <span>行情规则提醒，无资讯原文</span>
                    ) : alert.eventType === 'linkage_risk' ? (
                      <span>板块与海外联动规则提醒，无资讯原文</span>
                    ) : alert.eventType === 'system_health' ? (
                      <span>系统状态提醒，无资讯原文</span>
                    ) : alert.sourceUrl ? (
                      <a href={alert.sourceUrl} target="_blank" rel="noreferrer">查看原文</a>
                    ) : (
                      <span>原文链接暂缺</span>
                    )}
                  </div>
                </div>
                <div className={styles.alertActions}>
                  <span
                    className={`${styles.delivery} ${emailDelivery?.status === 'failed' ? styles.deliveryFailed : ''}`}
                    title={emailDelivery?.error || deliveryText(emailDelivery)}
                  >
                    {deliveryText(emailDelivery)}
                  </span>
                  {alert.priority !== 'P3' && canOpenStockExplanation(alert.symbol) && (
                    <Link href={`/?code=${encodeURIComponent(alert.symbol)}`}>
                      查看事件与风险解释
                    </Link>
                  )}
                  {!alert.isRead && (
                    <button type="button" onClick={() => void markRead(alert.id)}>
                      标记已读
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
