import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Layers3,
  LockKeyhole,
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
  metrics?: Array<{
    label: string;
    value: string;
  }>;
  progress?: Array<{
    label: string;
    value: string;
    detail: string;
    percent: number;
    tone?: 'accent' | 'good' | 'warning';
  }>;
  milestones?: Array<{
    label: string;
    detail: string;
    status: 'done' | 'active' | 'pending';
  }>;
  blockers?: string[];
  nextStep?: string;
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
  metrics = [],
  progress = [],
  milestones = [],
  blockers = [],
  nextStep,
  compact = false,
}: ModuleStatePanelProps) {
  const copy = STATE_COPY[state];
  const hasProgressDetail = progress.length > 0
    || milestones.length > 0
    || blockers.length > 0
    || Boolean(nextStep);
  const Icon = state === 'failed'
    ? AlertTriangle
    : state === 'stale'
      ? Clock3
      : state === 'available'
        ? ShieldCheck
        : Layers3;

  return (
    <section className={`${styles.panel} ${compact ? styles.compactStatePanel : ''} ${hasProgressDetail ? styles.progressStatePanel : ''}`}>
      <div className={styles.panelHeader}>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        {stage && <span className={styles.stageBadge}>阶段{stage}</span>}
      </div>
      <div className={`${styles.stateBody} ${styles[`state_${state}`]}`}>
        <div className={styles.stateIntro}>
          <span className={styles.stateIcon}><Icon size={23} /></span>
          <div>
            <strong>{stage ? `阶段${stage} · ${copy.label}` : copy.label}</strong>
            <p>{copy.detail}</p>
          </div>
        </div>
        {metrics.length > 0 && (
          <dl className={styles.stateMetrics}>
            {metrics.map((metric) => (
              <div key={metric.label}>
                <dt>{metric.label}</dt>
                <dd>{metric.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {(progress.length > 0 || milestones.length > 0) && (
          <div className={styles.readinessLayout}>
            {progress.length > 0 && (
              <div className={styles.readinessProgress}>
                <div className={styles.readinessHeading}>
                  <strong>数据准备度</strong>
                  <span>仅统计接口已返回的真实字段</span>
                </div>
                <div className={styles.progressList}>
                  {progress.map((item) => {
                    const percent = Math.max(0, Math.min(100, item.percent));
                    return (
                      <div className={styles.progressItem} key={item.label}>
                        <div>
                          <span>{item.label}</span>
                          <b>{item.value}</b>
                        </div>
                        <div
                          className={styles.progressTrack}
                          role="progressbar"
                          aria-label={item.label}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={Math.round(percent)}
                        >
                          <span
                            className={styles[`progress_${item.tone || 'accent'}`]}
                            style={{ width: `${percent}%` }}
                          />
                        </div>
                        <small>{item.detail}</small>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {milestones.length > 0 && (
              <div className={styles.milestoneSection}>
                <div className={styles.readinessHeading}>
                  <strong>解锁路径</strong>
                  <span>按真实门禁依次推进</span>
                </div>
                <ol className={styles.milestoneList}>
                  {milestones.map((milestone) => {
                    const MilestoneIcon = milestone.status === 'done'
                      ? CheckCircle2
                      : milestone.status === 'active'
                        ? CircleDashed
                        : LockKeyhole;
                    return (
                      <li
                        className={styles[`milestone_${milestone.status}`]}
                        key={milestone.label}
                      >
                        <MilestoneIcon size={15} />
                        <div>
                          <strong>{milestone.label}</strong>
                          <span>{milestone.detail}</span>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}
          </div>
        )}
        {blockers.length > 0 && (
          <div className={styles.stateBlockers}>
            <strong>当前阻塞</strong>
            <div>
              {blockers.map((blocker) => <span key={blocker}>{blocker}</span>)}
            </div>
          </div>
        )}
        {nextStep && (
          <div className={styles.stateNextStep}>
            <span>下一步</span>
            <strong>{nextStep}</strong>
            <ArrowRight size={15} />
          </div>
        )}
        {badges.length > 0 && (
          <div className={styles.stateBadges}>
            {badges.map((badge) => <span key={badge}>{badge}</span>)}
          </div>
        )}
      </div>
    </section>
  );
}
