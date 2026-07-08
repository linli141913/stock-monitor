'use client';

import { useState, useEffect, useCallback } from 'react';

export interface WatchlistItem {
  stockCode: string;
  stockName: string;
  addedAt: string; // ISO datetime
}

const STORAGE_KEY = 'stock_watchlist';

export function useWatchlist() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);

  // 初始化时从 localStorage 读取
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        setWatchlist(JSON.parse(raw));
      }
    } catch {
      setWatchlist([]);
    }
  }, []);

  // 写入 localStorage 并同步到后台
  const persist = (list: WatchlistItem[]) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
      // 同步到后台 API
      const symbols = list.map(item => item.stockCode);
      fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols })
      }).catch(err => console.error("同步后台失败", err));
    } catch {}
  };

  const addToWatchlist = useCallback((stockCode: string, stockName: string) => {
    setWatchlist(prev => {
      if (prev.find(i => i.stockCode === stockCode)) return prev;
      if (prev.length >= 5) {
        alert("监控列表最多只能添加 5 只股票哦！");
        return prev;
      }
      const next = [...prev, { stockCode, stockName, addedAt: new Date().toISOString() }];
      persist(next);
      return next;
    });
  }, []);

  const removeFromWatchlist = useCallback((stockCode: string) => {
    setWatchlist(prev => {
      const next = prev.filter(i => i.stockCode !== stockCode);
      persist(next);
      return next;
    });
  }, []);

  const isInWatchlist = useCallback((stockCode: string) => {
    return watchlist.some(i => i.stockCode === stockCode);
  }, [watchlist]);

  return { watchlist, addToWatchlist, removeFromWatchlist, isInWatchlist };
}
