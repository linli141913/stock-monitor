'use client';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'https://banister-drilling-jawless.ngrok-free.dev';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useWatchlist, WatchlistItem } from '@/hooks/useWatchlist';
import styles from './page.module.css';


interface StockLiveData {
  stockCode: string;
  stockName: string;
  price: number | null;
  changePercent: number | null;
  volume: number | null;
  amount: number | null;
  addedAt: string;
  loading: boolean;
  error: boolean;
}

export default function WatchlistPage() {
  const router = useRouter();
  const { watchlist, removeFromWatchlist } = useWatchlist();
  const [liveData, setLiveData] = useState<StockLiveData[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // 初始化行情数据结构
  useEffect(() => {
    setLiveData(watchlist.map(item => ({
      ...item,
      price: null,
      changePercent: null,
      volume: null,
      amount: null,
      loading: true,
      error: false,
    })));
  }, [watchlist]);

  // 批量拉取实时行情
  const fetchLiveData = useCallback(async () => {
    if (watchlist.length === 0) return;
    setRefreshing(true);
    const symbols = watchlist.map(i => i.stockCode).join(',');
    try {
      const res = await fetch(`${API_BASE}/api/stock/batch_overview?symbols=${symbols}&_t=${Date.now()}`, { headers: { 'ngrok-skip-browser-warning': 'true' }, cache: 'no-store' });
      if (!res.ok) throw new Error('fetch failed');
      const json = await res.json();
      const map: Record<string, any> = {};
      (json.data || []).forEach((item: any) => {
        map[item.symbol] = item;
      });
      setLiveData(watchlist.map(item => {
        const real = map[item.stockCode];
        if (!real) {
          return { ...item, price: null, changePercent: null, volume: null, amount: null, loading: false, error: true };
        }
        return {
          ...item,
          price: real.price ?? null,
          changePercent: real.changePercent ?? null,
          volume: real.volume ?? null,
          amount: real.amount ?? null,
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
      fetchLiveData();
    }
  }, [watchlist, fetchLiveData]);

  const handleRowClick = (stockCode: string) => {
    window.location.href = `/?code=${stockCode}`;
  };

  const handleRemove = (e: React.MouseEvent, stockCode: string) => {
    e.stopPropagation();
    removeFromWatchlist(stockCode);
  };

  const formatPrice = (v: any) => v == null ? '-' : Number(v).toFixed(2);
  const formatPct = (v: any) => {
    if (v == null) return '-';
    const num = Number(v);
    const sign = num >= 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  };
  const formatAmount = (v: any) => {
    if (v == null) return '-';
    const num = Number(v);
    if (num >= 1e8) return `${(num / 1e8).toFixed(2)}亿`;
    if (num >= 1e4) return `${(num / 1e4).toFixed(2)}万`;
    return num.toFixed(0);
  };

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
            onClick={fetchLiveData}
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
                <th style={{ textAlign: 'center' }}>加入时间</th>
                <th style={{ textAlign: 'center' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {liveData.map(item => {
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
                      {item.loading ? <span className={styles.skeleton} /> : formatAmount(item.amount)}
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
