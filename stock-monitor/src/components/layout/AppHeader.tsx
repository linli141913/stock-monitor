'use client';

import { LayoutList, LineChart, Bell, TrendingUp, ArrowUp } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, useEffect } from 'react';
import styles from './AppHeader.module.css';

export default function AppHeader() {
  const pathname = usePathname();
  const [showScroll, setShowScroll] = useState(false);

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

  const scrollTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const navItems = [
    { name: '首页', path: '/', icon: <LineChart size={18} /> },
    { name: '监测列表', path: '/watchlist', icon: <LayoutList size={18} /> },
    { name: '提醒设置', path: '/alerts', icon: <Bell size={18} /> },
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
              </Link>
            );
          })}
          
          {/* 回到置顶按钮 */}
          <button 
            className={`${styles.scrollTopBtn} ${showScroll ? styles.visible : ''}`}
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
