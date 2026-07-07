import { IndustryMonitor } from '@/types/industry';
import { BarChart3 } from 'lucide-react';
import styles from './IndustryMonitorCard.module.css';

interface Props {
  data: IndustryMonitor;
}

export default function IndustryMonitorCard({ data }: Props) {
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleArea}>
          <BarChart3 className={styles.icon} size={20} />
          <h2 className={styles.title}>行业监测</h2>
        </div>
        <div className={styles.industryName}>所属行业：{data.industryName}</div>
      </div>

      <div className={styles.content}>
        <div className={styles.row}>
          <div className={styles.label}>行业热度</div>
          <div className={styles.valueArea}>
            <div className={styles.progressTrack}>
              <div 
                className={styles.progressFill} 
                style={{ width: `${data.heatScore}%` }}
              ></div>
            </div>
            <span className={styles.score}>{data.heatScore}/100</span>
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>板块涨跌</div>
          <div className={`${styles.value} ${data.sectorChangePercent > 0 ? 'text-rise' : data.sectorChangePercent < 0 ? 'text-fall' : ''}`}>
            {data.sectorChangePercent > 0 ? '+' : ''}{data.sectorChangePercent.toFixed(2)}%
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>资金流向</div>
          <div className={`${styles.value} ${data.fundFlow.includes('流入') ? 'text-rise' : (data.fundFlow.includes('流出') ? 'text-fall' : '')}`}>
            {data.fundFlow}
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>相关政策</div>
          <div className={styles.textValue}>{data.policySummary}</div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>上游/下游动态</div>
          <div className={styles.textValue}>
            {data.upstreamStatus}；{data.downstreamStatus}
          </div>
        </div>
      </div>
    </div>
  );
}
