'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

export interface WatchlistItem {
  stockCode: string;
  stockName: string;
  addedAt: string; // ISO datetime
}

interface WatchlistResponse {
  data?: WatchlistItem[];
}

const STORAGE_KEY = 'stock_watchlist';
const API_BASE = '/api/backend';

export function useWatchlist() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [mutationError, setMutationError] = useState('');
  const mutationInFlight = useRef(false);

  // 初始化时从 localStorage 读取，然后从后端同步
  useEffect(() => {
    let localList: WatchlistItem[] = [];
    let hasLocalSnapshot = false;
    let localSyncTimer: ReturnType<typeof setTimeout> | null = null;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        hasLocalSnapshot = true;
        localList = JSON.parse(raw);
        localSyncTimer = setTimeout(() => setWatchlist(localList), 0);
      }
    } catch {}

    // 从后端拉取最新数据同步
    fetch(`${API_BASE}/api/watchlist?_t=${Date.now()}`, { headers: { 'ngrok-skip-browser-warning': 'true' }, cache: 'no-store' })
      .then(res => res.json())
      .then((data: WatchlistResponse) => {
        if (data && data.data && Array.isArray(data.data)) {
          let backendList = data.data as WatchlistItem[];

          if (hasLocalSnapshot) {
            fetch(`${API_BASE}/api/monitoring/health/watchlist-sync`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
              },
              body: JSON.stringify({ items: localList })
            }).catch(() => {});
          }
          
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

    return () => {
      if (localSyncTimer) clearTimeout(localSyncTimer);
    };
  }, []);

  // 写入 localStorage 并同步到后台
  const persist = useCallback(async (list: WatchlistItem[]): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE}/api/watchlist`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': 'true'
        },
        body: JSON.stringify({ items: list })
      });
      if (!response.ok) {
        throw new Error(`后端返回 ${response.status}`);
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
      return true;
    } catch (error) {
      console.error("同步后台失败", error);
      setMutationError('监测列表保存失败，本次未更改');
      return false;
    }
  }, []);

  const addToWatchlist = useCallback(async (stockCode: string, stockName: string): Promise<boolean> => {
    if (watchlist.find(i => i.stockCode === stockCode)) return true;
    if (watchlist.length >= 10) {
      setMutationError('监测列表最多只能添加 10 只股票');
      return false;
    }
    if (mutationInFlight.current) return false;
    const next = [...watchlist, { stockCode, stockName, addedAt: new Date().toISOString() }];
    mutationInFlight.current = true;
    setMutationError('');
    try {
      const persisted = await persist(next);
      if (!persisted) return false;
      setWatchlist(next);
      return true;
    } finally {
      mutationInFlight.current = false;
    }
  }, [persist, watchlist]);

  const removeFromWatchlist = useCallback(async (stockCode: string): Promise<boolean> => {
    if (mutationInFlight.current) return false;
    const next = watchlist.filter(i => i.stockCode !== stockCode);
    mutationInFlight.current = true;
    setMutationError('');
    try {
      const persisted = await persist(next);
      if (!persisted) return false;
      setWatchlist(next);
      return true;
    } finally {
      mutationInFlight.current = false;
    }
  }, [persist, watchlist]);

  const isInWatchlist = useCallback((stockCode: string) => {
    return watchlist.some(i => i.stockCode === stockCode);
  }, [watchlist]);

  return {
    watchlist,
    addToWatchlist,
    removeFromWatchlist,
    isInWatchlist,
    mutationError,
  };
}
