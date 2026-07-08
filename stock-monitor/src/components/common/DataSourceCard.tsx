'use client';

import React, { useState, useEffect } from 'react';
import { Database, FileText, Newspaper, PieChart, Landmark } from 'lucide-react';
import styles from './DataSourceCard.module.css';

export default function DataSourceCard() {
  const [mounted, setMounted] = useState(false);
  const [timeStr, setTimeStr] = useState('');

  useEffect(() => {
    setMounted(true);
    setTimeStr(new Date().toLocaleString('zh-CN'));
  }, []);

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <Database className={styles.icon} size={20} />
        <h2 className={styles.title}>信息来源</h2>
      </div>

      <div className={styles.content}>
        <div className={styles.grid}>
          <div className={styles.sourceItem}>
            <LineChartIcon size={16} className={styles.sourceIcon} />
            <span>行情数据</span>
          </div>
          <div className={styles.sourceItem}>
            <FileText size={16} className={styles.sourceIcon} />
            <span>公司公告</span>
          </div>
          <div className={styles.sourceItem}>
            <Newspaper size={16} className={styles.sourceIcon} />
            <span>行业新闻</span>
          </div>
          <div className={styles.sourceItem}>
            <PieChart size={16} className={styles.sourceIcon} />
            <span>财报数据</span>
          </div>
          <div className={styles.sourceItem}>
            <Landmark size={16} className={styles.sourceIcon} />
            <span>政策资讯</span>
          </div>
        </div>
        <div style={{ marginTop: '16px', padding: '12px', background: '#f8fafc', borderRadius: '6px', fontSize: '13px', color: '#64748b' }}>
          <div style={{ marginBottom: '4px' }}><strong>数据类型：</strong>准实时</div>
          <div style={{ marginBottom: '4px' }}><strong>最后更新：</strong>{mounted ? timeStr : '加载中...'}</div>
          <div><strong>延迟说明：</strong>公开数据源可能存在几十秒到数分钟延迟</div>
        </div>
      </div>
    </div>
  );
}

// Custom simple icon for LineChart since it might be imported elsewhere
function LineChartIcon({ size, className }: { size: number, className?: string }) {
  return (
    <svg 
      xmlns="http://www.w3.org/2000/svg" 
      width={size} 
      height={size} 
      viewBox="0 0 24 24" 
      fill="none" 
      stroke="currentColor" 
      strokeWidth="2" 
      strokeLinecap="round" 
      strokeLinejoin="round" 
      className={className}
    >
      <path d="M3 3v18h18" />
      <path d="m19 9-5 5-4-4-3 3" />
    </svg>
  );
}
