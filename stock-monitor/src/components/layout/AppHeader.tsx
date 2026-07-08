'use client';

import { LayoutList, LineChart, Bell, TrendingUp } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import styles from './AppHeader.module.css';

export default function AppHeader() {
  const pathname = usePathname();

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
        </nav>
      </div>
    </header>
  );
}
