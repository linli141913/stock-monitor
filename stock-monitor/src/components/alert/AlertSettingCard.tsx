'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { BellRing, RefreshCw } from 'lucide-react';
import styles from './AlertSettingCard.module.css';

interface TaskHealth {
  status: 'healthy' | 'running' | 'failed';
  lastSuccessAt?: string;
  lastFailedAt?: string;
  itemCount?: number | null;
}

interface MonitoringHealth {
  status: 'healthy' | 'degraded' | 'unknown';
  watchlistCount: number;
  unreadCount: number;
  email: {
    status: 'configured' | 'not_configured';
    configured: boolean;
    recipientConfigured?: boolean;
    senderConfigured?: boolean;
  };
  tasks: Record<string, TaskHealth>;
  fetchedAt: string;
}

interface EmailSettings {
  recipientEmail: string | null;
  recipientConfigured: boolean;
  senderConfigured: boolean;
  configured: boolean;
}

interface EmailFeedback {
  tone: 'success' | 'warning' | 'error';
  text: string;
}

const formatTime = (value?: string) => {
  if (!value) return '尚未完成扫描';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN', { hour12: false });
};

export default function AlertSettingCard() {
  const [health, setHealth] = useState<MonitoringHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [recipientEmail, setRecipientEmail] = useState('');
  const [savedRecipientEmail, setSavedRecipientEmail] = useState('');
  const [emailLoading, setEmailLoading] = useState(true);
  const [emailSaving, setEmailSaving] = useState(false);
  const [emailTesting, setEmailTesting] = useState(false);
  const [emailFeedback, setEmailFeedback] = useState<EmailFeedback | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/backend/api/monitoring/health', {
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`监测状态请求失败 (${response.status})`);
      setHealth(await response.json() as MonitoringHealth);
      setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '监测状态获取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchEmailSettings = useCallback(async () => {
    setEmailLoading(true);
    try {
      const response = await fetch('/api/backend/api/alerts/email-settings', {
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`邮箱设置请求失败 (${response.status})`);
      const payload = await response.json() as { data: EmailSettings };
      const savedEmail = payload.data.recipientEmail || '';
      setRecipientEmail(savedEmail);
      setSavedRecipientEmail(savedEmail);
    } catch (requestError) {
      setEmailFeedback({
        tone: 'error',
        text: requestError instanceof Error ? requestError.message : '邮箱设置获取失败',
      });
    } finally {
      setEmailLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchHealth();
      void fetchEmailSettings();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchEmailSettings, fetchHealth]);

  const isValidEmail = (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value);

  const saveEmail = async () => {
    const normalizedEmail = recipientEmail.trim();
    if (!isValidEmail(normalizedEmail)) {
      setEmailFeedback({ tone: 'error', text: '请输入有效的收件邮箱' });
      return false;
    }
    setEmailSaving(true);
    try {
      const response = await fetch('/api/backend/api/alerts/email-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipientEmail: normalizedEmail }),
        cache: 'no-store',
      });
      const payload = await response.json() as { data?: EmailSettings; detail?: string };
      if (!response.ok || !payload.data) {
        throw new Error(payload.detail || `保存失败 (${response.status})`);
      }
      setRecipientEmail(payload.data.recipientEmail || normalizedEmail);
      setSavedRecipientEmail(payload.data.recipientEmail || normalizedEmail);
      setEmailFeedback({
        tone: payload.data.senderConfigured ? 'success' : 'warning',
        text: payload.data.senderConfigured
          ? '收件邮箱已保存，可以发送测试邮件'
          : '收件邮箱已保存；后端发件服务尚未配置',
      });
      await fetchHealth();
      return true;
    } catch (requestError) {
      setEmailFeedback({
        tone: 'error',
        text: requestError instanceof Error ? requestError.message : '收件邮箱保存失败',
      });
      return false;
    } finally {
      setEmailSaving(false);
    }
  };

  const sendTestEmail = async () => {
    const normalizedEmail = recipientEmail.trim();
    if (!isValidEmail(normalizedEmail)) {
      setEmailFeedback({ tone: 'error', text: '请先输入有效的收件邮箱' });
      return;
    }
    setEmailTesting(true);
    try {
      if (normalizedEmail !== savedRecipientEmail) {
        const saved = await saveEmail();
        if (!saved) return;
      }
      const response = await fetch('/api/backend/api/alerts/email-settings/test', {
        method: 'POST',
        cache: 'no-store',
      });
      const payload = await response.json() as {
        status?: 'sent' | 'not_configured' | 'failed';
        message?: string;
        detail?: string;
        error?: string | null;
      };
      if (!response.ok) throw new Error(payload.detail || `测试失败 (${response.status})`);
      setEmailFeedback({
        tone: payload.status === 'sent' ? 'success' : payload.status === 'not_configured' ? 'warning' : 'error',
        text: payload.error && payload.status === 'failed'
          ? `${payload.message || '测试邮件发送失败'}：${payload.error}`
          : payload.message || '测试结果未知',
      });
    } catch (requestError) {
      setEmailFeedback({
        tone: 'error',
        text: requestError instanceof Error ? requestError.message : '测试邮件发送失败',
      });
    } finally {
      setEmailTesting(false);
    }
  };

  const statusLabel = health?.status === 'healthy'
    ? '运行正常'
    : health?.status === 'degraded'
      ? '部分异常'
      : '状态待确认';
  const officialTask = health?.tasks.officialAnnouncements;
  const marketTask = health?.tasks.marketRisk;

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleGroup}>
          <BellRing className={styles.icon} size={20} />
          <h2 className={styles.title}>监测运行状态</h2>
        </div>
        <button
          className={styles.refreshBtn}
          onClick={() => void fetchHealth()}
          disabled={loading}
          aria-label="刷新监测状态"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {error ? (
        <div className={styles.error}>{error}</div>
      ) : (
        <div className={styles.statusList}>
          <div className={styles.statusRow}>
            <span>系统状态</span>
            <strong className={health?.status === 'healthy' ? styles.healthy : health?.status === 'degraded' ? styles.warning : styles.unknown}>
              {loading && !health ? '读取中…' : statusLabel}
            </strong>
          </div>
          <div className={styles.statusRow}>
            <span>后台监测</span>
            <strong>{health ? `${health.watchlistCount} 只股票` : '—'}</strong>
          </div>
          <div className={styles.statusRow}>
            <span>官方公告扫描</span>
            <strong title={formatTime(officialTask?.lastSuccessAt)}>
              {officialTask?.status === 'failed' ? '最近失败' : formatTime(officialTask?.lastSuccessAt)}
            </strong>
          </div>
          <div className={styles.statusRow}>
            <span>量价风险扫描</span>
            <strong title={formatTime(marketTask?.lastSuccessAt)}>
              {marketTask?.status === 'failed' ? '最近失败' : formatTime(marketTask?.lastSuccessAt)}
            </strong>
          </div>
          <div className={styles.statusRow}>
            <span>强提醒邮件</span>
            <strong className={health?.email.configured ? styles.healthy : styles.unknown}>
              {health?.email.configured
                ? '已配置'
                : health?.email.recipientConfigured
                  ? '收件箱已保存'
                  : '邮件未配置'}
            </strong>
          </div>
        </div>
      )}

      <div className={styles.emailPanel}>
        <div>
          <h3>全局收件邮箱</h3>
          <p>所有监测股票的强提醒发往这里，保存新地址即可替换。</p>
        </div>
        <input
          className={styles.emailInput}
          type="email"
          value={recipientEmail}
          onChange={(event) => {
            setRecipientEmail(event.target.value);
            setEmailFeedback(null);
          }}
          placeholder="name@example.com"
          aria-label="全局强提醒收件邮箱"
          disabled={emailLoading || emailSaving || emailTesting}
        />
        <div className={styles.emailActions}>
          <button
            type="button"
            className={styles.saveEmailButton}
            onClick={() => void saveEmail()}
            disabled={emailLoading || emailSaving || emailTesting}
          >
            {emailSaving ? '保存中…' : '保存收件邮箱'}
          </button>
          <button
            type="button"
            className={styles.testEmailButton}
            onClick={() => void sendTestEmail()}
            disabled={emailLoading || emailSaving || emailTesting}
          >
            {emailTesting ? '发送中…' : '发送测试邮件'}
          </button>
        </div>
        {emailFeedback && (
          <div
            className={`${styles.emailFeedback} ${styles[emailFeedback.tone]}`}
            aria-live="polite"
          >
            {emailFeedback.text}
          </div>
        )}
      </div>

      <Link href="/alerts" className={styles.alertLink}>
        查看提醒中心{health?.unreadCount ? `（${health.unreadCount} 条未读）` : ''}
      </Link>
    </div>
  );
}
