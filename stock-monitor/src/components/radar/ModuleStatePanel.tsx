import {
  AlertTriangle,
  Clock3,
  Layers3,
  ShieldCheck,
} from 'lucide-react';

import type { RadarModuleState } from '@/types/radar';
import styles from './Radar.module.css';

interface ModuleStatePanelProps {
  state: RadarModuleState;
  title: string;
  description: string;
  stage?: number;
  badges?: string[];
  compact?: boolean;
}

const STATE_COPY: Record<RadarModuleState, {
  label: string;
  detail: string;
}> = {
  available: {
    label: '数据可用',
    detail: '当前模块已取得真实快照。',
  },
  empty: {
    label: '本轮真实空榜',
    detail: '计算已经完成，没有符合门槛的对象。',
  },
  stale: {
    label: '快照已过期',
    detail: '保留最近一次成功结果，并明确标记数据时间。',
  },
  failed: {
    label: '来源或读取失败',
    detail: '当前结果不冒充成功数据，其他模块仍独立展示。',
  },
  not_ready: {
    label: '尚无完成快照',
    detail: '模块还没有产生可以展示的完整结果。',
  },
  not_enabled: {
    label: '当前阶段尚未启用',
    detail: '只保留产品位置，不生成候选或示例数据。',
  },
};

export default function ModuleStatePanel({
  state,
  title,
  description,
  stage,
  badges = [],
  compact = false,
}: ModuleStatePanelProps) {
  const copy = STATE_COPY[state];
  const Icon = state === 'failed'
    ? AlertTriangle
    : state === 'stale'
      ? Clock3
      : state === 'available'
        ? ShieldCheck
        : Layers3;

  return (
    <section className={`${styles.panel} ${compact ? styles.compactStatePanel : ''}`}>
      <div className={styles.panelHeader}>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        {stage && <span className={styles.stageBadge}>阶段{stage}</span>}
      </div>
      <div className={`${styles.stateBody} ${styles[`state_${state}`]}`}>
        <span className={styles.stateIcon}><Icon size={23} /></span>
        <strong>{stage ? `阶段${stage} · ${copy.label}` : copy.label}</strong>
        <p>{copy.detail}</p>
        {badges.length > 0 && (
          <div className={styles.stateBadges}>
            {badges.map((badge) => <span key={badge}>{badge}</span>)}
          </div>
        )}
      </div>
    </section>
  );
}
