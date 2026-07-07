'use client';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';

import { useState, useEffect } from 'react';
import { Activity } from 'lucide-react';
import styles from './page.module.css';
import RadarNewsCard, { RadarNews } from '@/components/industry/RadarNewsCard';

const TABS = [
  { id: 'latest', label: '全市场' },
  { id: 'semi', label: '只看半导体' },
  { id: 'domestic', label: '国内' },
  { id: 'global', label: '国外' },
  { id: 'policies', label: '政策' },
  { id: 'company-events', label: '龙头公司' },
  { id: 'export-control', label: '出口管制' },
];

export default function IndustryInsightPage() {
  const [activeTab, setActiveTab] = useState('latest');
  const [newsList, setNewsList] = useState<RadarNews[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchNews = async (tabId: string, isSilent = false) => {
    if (!isSilent) setLoading(true);
    setError('');
    try {
      let endpoint = `/${tabId}`;
      if (tabId === 'latest') endpoint = '/latest?category=all';
      if (tabId === 'semi') endpoint = '/latest?category=semi';
      const res = await fetch(`${API_BASE}/api/semiconductor-news${endpoint}`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
      if (!res.ok) throw new Error('获取资讯失败');
      const data = await res.json();
      setNewsList(data);
    } catch (err: any) {
      if (!isSilent) setError(err.message || '网络错误');
    } finally {
      if (!isSilent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchNews(activeTab, false);
    
    // 30秒静默轮询更新
    const timer = setInterval(() => {
      fetchNews(activeTab, true);
    }, 30000);
    
    return () => clearInterval(timer);
  }, [activeTab]);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h1 className={styles.title}>行业洞察</h1>
        <div className={styles.radarBadge}>
          <Activity size={18} />
          实时资讯雷达
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`${styles.tab} ${activeTab === tab.id ? styles.activeTab : ''}`}
            onClick={() => setActiveTab(tab.id)}
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
