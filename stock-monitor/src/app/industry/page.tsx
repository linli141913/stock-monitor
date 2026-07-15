'use client';

const API_BASE = '/api/backend';

import { useState, useEffect, useCallback } from 'react';
import { Activity } from 'lucide-react';
import styles from './page.module.css';
import RadarNewsCard, { RadarNews } from '@/components/industry/RadarNewsCard';

const TABS = [
  { id: 'all', label: '全部' },
  { id: 'company-announcements', label: '公司公告' },
  { id: 'industry-policy', label: '行业政策' },
  { id: 'industry-dynamics', label: '产业动态' },
  { id: 'overseas-controls', label: '海外与管制' },
];

type NewsFeedStatus = 'available' | 'available_empty' | 'unavailable';

interface NewsFeedPayload {
  status: NewsFeedStatus;
  data: RadarNews[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  error: string | null;
  checkedAt: string;
}

export default function IndustryInsightPage() {
  const [activeTab, setActiveTab] = useState('all');
  const [newsList, setNewsList] = useState<RadarNews[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dataStatus, setDataStatus] = useState<NewsFeedStatus>('available_empty');
  const [total, setTotal] = useState(0);

  const fetchNews = useCallback(async (tabId: string, isSilent = false) => {
    try {
      const endpoint = `/latest?category=${encodeURIComponent(tabId)}&limit=100&offset=0`;
      const res = await fetch(`${API_BASE}/api/semiconductor-news${endpoint}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('获取资讯失败');
      const payload = await res.json() as NewsFeedPayload;
      if (!Array.isArray(payload.data)) throw new Error('资讯接口格式错误');
      if (payload.status === 'unavailable') {
        setDataStatus('unavailable');
        setError(payload.error || '资讯数据暂不可用');
        return;
      }
      setTotal(payload.total);
      setError('');
      if (payload.status === 'available_empty') {
        setDataStatus('available_empty');
        setNewsList([]);
        return;
      }
      setDataStatus('available');
      setNewsList(payload.data);
    } catch (err: unknown) {
      setDataStatus('unavailable');
      if (!isSilent) setError(err instanceof Error ? err.message : '资讯数据暂不可用');
    } finally {
      if (!isSilent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => {
      void fetchNews(activeTab, false);
    }, 0);
    
    // 30秒静默轮询更新
    const timer = setInterval(() => {
      fetchNews(activeTab, true);
    }, 30000);
    
    return () => {
      clearTimeout(initialTimer);
      clearInterval(timer);
    };
  }, [activeTab, fetchNews]);

  const handleTabChange = (tabId: string) => {
    if (tabId === activeTab) return;
    setLoading(true);
    setError('');
    setActiveTab(tabId);
  };

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h1 className={styles.title}>行业洞察</h1>
        <div className={styles.radarBadge}>
          <Activity size={18} />
          当日公开资讯（最新 {newsList.length} / {total} 条）
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`${styles.tab} ${activeTab === tab.id ? styles.activeTab : ''}`}
            onClick={() => handleTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {loading ? (
        <div className={styles.loading}>雷达扫描中，请稍候...</div>
      ) : dataStatus === 'unavailable' && newsList.length === 0 ? (
        null
      ) : newsList.length === 0 ? (
        <div className={styles.empty}>当前分类下暂无权威资讯</div>
      ) : (
        <div className={styles.feed}>
          {newsList.map(news => (
            <RadarNewsCard key={news.id} news={news} />
          ))}
        </div>
      )}
    </div>
  );
}
