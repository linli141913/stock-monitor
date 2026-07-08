'use client';

import { useState, useEffect, useCallback } from 'react';

export interface WatchlistItem {
  stockCode: string;
  stockName: string;
  addedAt: string; // ISO datetime
}

const STORAGE_KEY = 'stock_watchlist';
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'https://banister-drilling-jawless.ngrok-free.dev';

export function useWatchlist() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);

  // 初始化时从 localStorage 读取，然后从后端同步
  useEffect(() => {
    let localList: WatchlistItem[] = [];
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        localList = JSON.parse(raw);
        setWatchlist(localList);
      }
    } catch {}

    // 从后端拉取最新数据同步
    fetch(`${API_BASE}/api/watchlist`, { headers: { 'ngrok-skip-browser-warning': 'true' } })
      .then(res => res.json())
      .then(data => {
        if (data && data.data && Array.isArray(data.data)) {
          let backendList = data.data as WatchlistItem[];
          
          // 处理迁移情况：后端如果没有数据，且本地有数据，将本地同步到后端
          if (backendList.length === 0 && localList.length > 0) {
            fetch(`${API_BASE}/api/watchlist`, {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
              },
              body: JSON.stringify({ items: localList })
            }).catch(() => {});
            return;
          }

          // 处理迁移情况：后端数据有 symbol 但没有 name，从本地找 name 补齐
          let needsUpdate = false;
          backendList = backendList.map(item => {
            if (!item.stockName) {
              const localMatch = localList.find(l => l.stockCode === item.stockCode);
              if (localMatch) {
                needsUpdate = true;
                return { ...item, stockName: localMatch.stockName };
              }
            }
            return item;
          });

          if (needsUpdate) {
            fetch(`${API_BASE}/api/watchlist`, {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
              },
              body: JSON.stringify({ items: backendList })
            }).catch(() => {});
          }
          
          // 只保留有名字的数据
          const validData = backendList.filter(i => i.stockCode && i.stockName);
          if (validData.length > 0 || backendList.length === 0) {
            setWatchlist(validData);
            localStorage.setItem(STORAGE_KEY, JSON.stringify(validData));
          }
        }
      })
      .catch(err => console.error("读取后台监测列表失败", err));
  }, []);

  // 写入 localStorage 并同步到后台
  const persist = (list: WatchlistItem[]) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
      // 同步到后台 API
      fetch(`${API_BASE}/api/watchlist`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': 'true'
        },
        body: JSON.stringify({ items: list })
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
