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

export default function IndustryInsightPage() {
  const [activeTab, setActiveTab] = useState('all');
  const [newsList, setNewsList] = useState<RadarNews[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchNews = useCallback(async (tabId: string, isSilent = false) => {
    try {
      const endpoint = `/latest?category=${encodeURIComponent(tabId)}`;
      const res = await fetch(`${API_BASE}/api/semiconductor-news${endpoint}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('获取资讯失败');
      const data = await res.json();
      setError('');
      setNewsList(data);
    } catch (err: unknown) {
      if (!isSilent) setError(err instanceof Error ? err.message : '网络错误');
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
          当日公开资讯（抽样）
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
