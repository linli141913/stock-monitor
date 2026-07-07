import { Target } from 'lucide-react';
import styles from './PageFooter.module.css';

export default function PageFooter() {
  return (
    <footer className={styles.footer}>
      <div className={styles.container}>
        <div className={styles.target}>
          <Target size={16} color="var(--color-rise)" />
          <span>目标：输入股票 → 查看完整股票信息 → 同步监测行业动态 → 触发邮件提醒</span>
        </div>
        <div className={styles.disclaimer}>
          本系统第一版采用准实时公开数据源，不构成投资建议，不适用于高频交易。
        </div>
      </div>
    </footer>
  );
}
