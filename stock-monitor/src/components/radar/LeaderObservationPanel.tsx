import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Layers3,
  ShieldAlert,
} from 'lucide-react';

import type {
  RadarLeaderItem,
  RadarLeaderModule,
  RadarModuleState,
} from '@/types/radar';
import styles from './Radar.module.css';

interface LeaderObservationPanelProps {
  module: RadarLeaderModule;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
  renderedAt: string | null;
}

const STATE_COPY: Record<RadarModuleState, string> = {
  available: '影子梯队可用',
  empty: '本轮真实空榜',
  stale: '梯队快照已过期',
  failed: '梯队读取失败',
  not_ready: '等待影子快照',
  not_enabled: '阶段6尚未启用',
};

const LANE_META = [
  {
    key: 'preliminary',
    title: '预备龙头',
    description: '通过硬门槛，等待连续性确认',
  },
  {
    key: 'candidates',
    title: '候选龙头',
    description: '评分与状态条件已进一步满足',
  },
  {
    key: 'confirmed',
    title: '已确认龙头',
    description: '仅表示规则状态，不是交易建议',
  },
] as const;

const REASON_LABELS: Record<string, string> = {
  stage_not_enabled: '阶段6功能开关未启用',
  stage6_storage_not_ready: '阶段6存储尚未迁移',
  candidate_snapshot_missing: '尚未形成候选快照',
  stage6_read_failed: '阶段6只读快照读取失败',
  snapshot_stale: '快照超过允许时效',
  state_entered: '本轮进入该状态',
  state_maintained: '本轮维持该状态',
};

function stateTone(state: RadarModuleState) {
  if (state === 'available') return styles.goodBadge;
  if (state === 'failed') return styles.alertBadge;
  if (state === 'stale') return styles.warningBadge;
  return styles.neutralBadge;
}

function renderStateIcon(state: RadarModuleState) {
  if (state === 'available') return <CheckCircle2 size={13} />;
  if (state === 'failed') return <AlertTriangle size={13} />;
  if (state === 'stale') return <Clock3 size={13} />;
  return <Layers3 size={13} />;
}

function reasonLabel(value: string) {
  return REASON_LABELS[value] || value;
}

function exposureLabel(value: string) {
  return {
    verified: '主营关联已验证',
    pending: '主营关联待验证',
    unavailable: '主营关联缺失',
  }[value] || value;
}

function dataStatusLabel(value: string) {
  return {
    healthy: '输入完整',
    partial: '输入部分缺失',
    stale: '输入过期',
    unavailable: '输入不可用',
  }[value] || value;
}

function LeaderRow({ item }: { item: RadarLeaderItem }) {
  const evidenceCount = Object.keys(item.evidence).length;
  const invalidationCount = Object.keys(item.invalidation).length;

  return (
    <article className={styles.leaderRow}>
      <div className={styles.leaderIdentity}>
        <div>
          <strong>{item.symbol}</strong>
          <span>{item.name}</span>
        </div>
        <b>{item.score.toFixed(1)}</b>
      </div>
      <div className={styles.leaderIndustry}>
        <span>{item.industryName || '行业未确认'}</span>
        <small>{item.industryCode || '—'}</small>
      </div>
      <div className={styles.leaderTags}>
        <span>{exposureLabel(item.businessExposureStatus)}</span>
        <span>{dataStatusLabel(item.dataStatus)}</span>
      </div>
      <div className={styles.leaderAudit}>
        <span>状态持续 {item.stateAgePeriods} 轮</span>
        <span>证据 {evidenceCount} · 失效条件 {invalidationCount}</span>
      </div>
      {item.firstRejectionReason && (
        <div className={styles.leaderRejection}>
          <ShieldAlert size={12} />
          {reasonLabel(item.firstRejectionReason)}
        </div>
      )}
      {item.reasons.length > 0 && (
        <p className={styles.leaderReasons}>
          {item.reasons.map(reasonLabel).join(' · ')}
        </p>
      )}
    </article>
  );
}

export default function LeaderObservationPanel({
  module,
  formatTime,
  renderedAt,
}: LeaderObservationPanelProps) {
  const hasEntries = module.preliminary.length
    + module.candidates.length
    + module.confirmed.length > 0;
  const reasons = Array.from(new Set([
    ...module.reasonCodes,
    ...module.summary.reasonCodes,
  ]));

  return (
    <section className={`${styles.panel} ${styles.leaderPanel}`}>
      <div className={styles.panelHeader}>
        <div>
          <h2>三级龙头梯队</h2>
          <p>确定性硬门槛、评分与状态机结果；全部保持影子记录。</p>
        </div>
        <span className={stateTone(module.state)}>
          {renderStateIcon(module.state)}
          {STATE_COPY[module.state]}
        </span>
      </div>

      <div className={styles.leaderSummary}>
        <div>
          <span>合资格</span>
          <strong>{module.summary.eligibleCount}</strong>
        </div>
        <div>
          <span>预备</span>
          <strong>{module.summary.preliminaryCount}</strong>
        </div>
        <div>
          <span>候选</span>
          <strong>{module.summary.candidateCount}</strong>
        </div>
        <div>
          <span>确认</span>
          <strong>{module.summary.confirmedCount}</strong>
        </div>
        <div>
          <span>输入覆盖</span>
          <strong>{Math.round(module.summary.coverage * 100)}%</strong>
        </div>
        <div>
          <span>正式可用</span>
          <strong>{module.summary.formalUsableCount}</strong>
        </div>
      </div>

      {reasons.length > 0 && (
        <div className={module.state === 'failed' ? styles.errorBanner : styles.warningBanner}>
          <AlertTriangle size={14} />
          <span>{reasons.map(reasonLabel).join(' · ')}</span>
        </div>
      )}

      <div className={styles.leaderMeta}>
        <span>快照时间 <b>{formatTime(module.lastSuccess?.asOf, true)}</b></span>
        <span>落库时间 <b>{formatTime(module.lastSuccess?.createdAt, true)}</b></span>
        <span>页面渲染 <b>{formatTime(renderedAt, true)}</b></span>
        <span>规则 <b>{module.summary.ruleVersion || '未冻结'}</b></span>
        <span className={styles.shadowOnly}>仅影子记录</span>
      </div>

      {hasEntries ? (
        <div className={styles.leaderLanes}>
          {LANE_META.map((lane) => {
            const entries = module[lane.key];
            const overflow = module.summary.overflowCounts[
              lane.key === 'candidates' ? 'candidate' : lane.key
            ] || 0;

            return (
              <section className={styles.leaderLane} key={lane.key}>
                <header>
                  <div>
                    <strong>{lane.title}</strong>
                    <span>{lane.description}</span>
                  </div>
                  <b>{entries.length}{overflow ? ` +${overflow}` : ''}</b>
                </header>
                <div className={styles.leaderList}>
                  {entries.length > 0 ? (
                    entries.map((item) => (
                      <LeaderRow item={item} key={`${item.state}-${item.symbol}`} />
                    ))
                  ) : (
                    <div className={styles.leaderLaneEmpty}>本轮无该级别标的</div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <div className={styles.leaderEmpty}>
          <strong>
            {module.state === 'empty'
              ? '本轮没有符合规则的龙头状态'
              : module.state === 'failed'
                ? '当前无法读取龙头梯队'
                : '尚未形成可展示的三级龙头快照'}
          </strong>
          <span>
            {module.state === 'empty'
              ? '这是确定性规则计算得到的真实空榜，不代表数据抓取失败。'
              : module.state === 'failed'
                ? '失败与真实空榜严格区分，页面不会沿用未知来源的数据。'
                : '需要阶段6存储、输入门槛和影子状态机全部就绪。'}
          </span>
        </div>
      )}
    </section>
  );
}
