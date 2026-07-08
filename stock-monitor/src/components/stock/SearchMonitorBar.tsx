'use client';

import { useState } from 'react';
import { Search, Mail } from 'lucide-react';
import styles from './SearchMonitorBar.module.css';

interface Props {
  onSearch: (keyword: string) => void;
}

export default function SearchMonitorBar({ onSearch }: Props) {
  const [keyword, setKeyword] = useState('');

  const handleSearch = () => {
    if (keyword.trim()) {
      onSearch(keyword.trim());
    }
  };

  return (
    <div className={styles.bar}>
      <div className={styles.searchContainer}>
        <Search className={styles.searchIcon} size={18} />
        <input 
          type="text" 
          className={styles.searchInput}
          placeholder="输入股票名称 / 代码，例如：深科技 / 000021"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
      </div>
      <button className={styles.monitorBtn} onClick={handleSearch}>
        开始监测
      </button>
      <div className={styles.badge}>
        <Mail size={14} />
        <span>Web端监测 + 邮件提醒</span>
      </div>
    </div>
  );
}
