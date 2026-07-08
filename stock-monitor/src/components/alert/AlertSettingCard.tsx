'use client';

import { useState } from 'react';
import { BellRing, Info } from 'lucide-react';
import { AlertRule } from '@/types/alert';
import styles from './AlertSettingCard.module.css';

interface Props {
  initialData: AlertRule;
}

export default function AlertSettingCard({ initialData }: Props) {
  const [email, setEmail] = useState(initialData.email || '');
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const handleSave = () => {
    if (!email.includes('@')) {
      alert('请输入有效的邮箱地址');
      return;
    }
    
    setSaving(true);
    // 模拟API请求
    setTimeout(() => {
      setSaving(false);
      setSuccessMsg('提醒设置已保存');
      setTimeout(() => setSuccessMsg(''), 3000);
    }, 800);
  };

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <BellRing className={styles.icon} size={20} />
        <h2 className={styles.title}>提醒设置</h2>
      </div>

      <div className={styles.content}>
        <div className={styles.checkboxGroup}>
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.priceChangeAlert} />
            <span className={styles.labelText}>股价涨跌幅提醒</span>
            <span className={styles.threshold}>≥ {initialData.priceChangeThreshold}%</span>
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.volumeAlert} />
            <span className={styles.labelText}>异常放量提醒</span>
            <span className={styles.threshold}>≥ {initialData.volumeRatioThreshold}倍</span>
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.announcementAlert} />
            <span className={styles.labelText}>公告更新提醒</span>
            <Info size={14} className={styles.infoIcon} />
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.industryAlert} />
            <span className={styles.labelText}>行业异动提醒</span>
            <Info size={14} className={styles.infoIcon} />
          </label>
        </div>

        <div className={styles.emailSection}>
          <label className={styles.emailLabel}>接收邮箱</label>
          <input 
            type="email" 
            className={styles.emailInput} 
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <button 
          className={styles.saveBtn} 
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? '保存中...' : '保存提醒'}
        </button>

        {successMsg && (
          <div className={styles.successMsg}>{successMsg}</div>
        )}
      </div>
    </div>
  );
}
