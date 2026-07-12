'use client';

import { BellRing, Info } from 'lucide-react';
import { AlertRule } from '@/types/alert';
import styles from './AlertSettingCard.module.css';

interface Props {
  initialData: AlertRule;
}

export default function AlertSettingCard({ initialData }: Props) {
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <BellRing className={styles.icon} size={20} />
        <h2 className={styles.title}>提醒设置</h2>
      </div>

      <div className={styles.content}>
        <div className={styles.checkboxGroup}>
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.priceChangeAlert} disabled />
            <span className={styles.labelText}>股价涨跌幅提醒</span>
            <span className={styles.threshold}>≥ {initialData.priceChangeThreshold}%</span>
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.volumeAlert} disabled />
            <span className={styles.labelText}>异常放量提醒</span>
            <span className={styles.threshold}>≥ {initialData.volumeRatioThreshold}倍</span>
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.announcementAlert} disabled />
            <span className={styles.labelText}>公告更新提醒</span>
            <Info size={14} className={styles.infoIcon} />
          </label>
          
          <label className={styles.checkboxLabel}>
            <input type="checkbox" defaultChecked={initialData.industryAlert} disabled />
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
            value={initialData.email || ''}
            disabled
            readOnly
          />
        </div>

        <button 
          className={styles.saveBtn} 
          disabled
        >
          提醒功能准备中
        </button>

        <div className={styles.successMsg}>当前不会保存设置或发送邮件</div>
      </div>
    </div>
  );
}
