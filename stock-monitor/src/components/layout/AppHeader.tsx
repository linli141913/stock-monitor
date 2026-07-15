'use client';

import { LayoutList, LineChart, Bell, TrendingUp, ArrowUp } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, useEffect } from 'react';
import styles from './AppHeader.module.css';

export default function AppHeader() {
  const pathname = usePathname();
  const [showScroll, setShowScroll] = useState(false);
  const [unreadAlertCount, setUnreadAlertCount] = useState(0);

  useEffect(() => {
    const checkScrollTop = () => {
      if (!showScroll && window.pageYOffset > 200) {
        setShowScroll(true);
      } else if (showScroll && window.pageYOffset <= 200) {
        setShowScroll(false);
      }
    };
    window.addEventListener('scroll', checkScrollTop);
    return () => window.removeEventListener('scroll', checkScrollTop);
  }, [showScroll]);

  useEffect(() => {
    const loadUnreadCount = async () => {
      try {
        const response = await fetch(
          `/api/backend/api/alerts/unread-count?_t=${Date.now()}`,
          { cache: 'no-store' },
        );
        if (!response.ok) return;
        const payload = await response.json() as { count?: number };
        setUnreadAlertCount(payload.count || 0);
      } catch {
        // 导航不因提醒服务暂时不可用而失效。
      }
    };
    const handleUnreadChanged = () => void loadUnreadCount();
    void loadUnreadCount();
    const timer = window.setInterval(() => void loadUnreadCount(), 60_000);
    window.addEventListener('alerts:unread-changed', handleUnreadChanged);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('alerts:unread-changed', handleUnreadChanged);
    };
  }, []);

  const scrollTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const navItems = [
    { name: '首页', path: '/', icon: <LineChart size={18} /> },
    { name: '监测列表', path: '/watchlist', icon: <LayoutList size={18} /> },
    { name: '提醒中心', path: '/alerts', icon: <Bell size={18} /> },
    { name: '行业洞察', path: '/industry', icon: <TrendingUp size={18} /> },
  ];

  return (
    <header className={styles.header}>
      <div className={styles.container}>
        <div className={styles.logo}>
          <LineChart className={styles.logoIcon} size={28} color="var(--color-primary)" />
          <span className={styles.logoText}>股票监测助手</span>
        </div>
        <nav className={styles.nav}>
          {navItems.map((item) => {
            const isActive = pathname === item.path;
            return (
              <Link 
                key={item.path} 
                href={item.path}
                className={`${styles.navItem} ${isActive ? styles.active : ''}`}
              >
                <span>{item.name}</span>
                {item.path === '/alerts' && unreadAlertCount > 0 && (
                  <span className={styles.unreadBadge}>
                    {unreadAlertCount > 99 ? '99+' : unreadAlertCount}
                  </span>
                )}
              </Link>
            );
          })}
          
          {/* 回到置顶按钮 - 常驻显示，滑动后激活 */}
          <button 
            className={`${styles.scrollTopBtn} ${showScroll ? styles.active : ''}`}
            onClick={scrollTop}
            title="回到置顶"
          >
            <ArrowUp size={14} />
            <span>回到置顶</span>
          </button>
        </nav>
      </div>
    </header>
  );
}
