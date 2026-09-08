import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ExternalLink,
  Layers3,
  ShieldAlert,
  UserPlus,
} from 'lucide-react';
import { useState } from 'react';

import { useWatchlist } from '@/hooks/useWatchlist';
import { nextVisibleCount, takeVisibleItems } from '@/lib/progressive-list';
import { radarLeaderReasonLabel } from '@/lib/radar-reasons';
import type {
  RadarLeaderItem,
  RadarLeaderModule,
  RadarLeaderObservationItem,
  RadarLeaderReviewQueueResponse,
  RadarModuleState,
} from '@/types/radar';
import styles from './Radar.module.css';

interface LeaderObservationPanelProps {
  module: RadarLeaderModule;
  reviewQueuePage: RadarLeaderReviewQueueResponse | null;
  reviewQueueLoading: boolean;
  reviewQueueError: string;
  onReviewPageChange: (offset: number) => void;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
  renderedAt: string | null;
}

const STATE_COPY: Record<RadarModuleState, string> = {
  available: '龙头梯队可用',
  empty: '本轮真实空榜',
  stale: '梯队快照已过期',
  failed: '梯队读取失败',
  not_ready: '等待监测快照',
  not_enabled: '龙头监测尚未启用',
};

const LANE_META = [
  { key: 'preliminary', title: '预备龙头', description: '通过硬门槛，等待连续性确认' },
  { key: 'candidates', title: '候选龙头', description: '评分与状态条件已进一步满足' },
  { key: 'confirmed', title: '已确认龙头', description: '仅表示规则状态，不是交易建议' },
] as const;

const RISK_CATEGORY_LABELS: Record<string, string> = {
  reduction: '减持',
  unlock: '解禁',
  regulatory: '监管',
  investigation: '调查',
  litigation: '诉讼',
  earnings: '业绩',
  audit: '审计',
};

const SOURCE_DOMAIN_LABELS = {
  quote: '行情',
  sector: '行业映射',
  business: '主营与催化',
  announcement: '官方公告',
} as const;

const SOURCE_STATUS_LABELS = {
  available: '可用',
  partial: '部分可用',
  missing: '待补齐',
  stale: '已过期',
  failed: '读取失败',
  unverified: '无法验证',
} as const;

const LEADER_OBSERVATION_PAGE_SIZE = 50;

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

function WatchlistButton({ monitored, onAdd }: {
  monitored: boolean;
  onAdd: () => Promise<boolean>;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <button
      className={styles.leaderWatchButton}
      disabled={monitored || adding}
      onClick={async () => {
        setAdding(true);
        try {
          await onAdd();
        } finally {
          setAdding(false);
        }
      }}
    >
      <UserPlus size={13} />
      {monitored ? '已在监测列表' : adding ? '正在保存…' : '加入监测列表'}
    </button>
  );
}

function LeaderRow({ item, monitored, onAdd }: {
  item: RadarLeaderItem;
  monitored: boolean;
  onAdd: () => Promise<boolean>;
}) {
  const evidenceCount = Object.keys(item.evidence).length;
  const invalidationCount = Object.keys(item.invalidation).length;
  return (
    <article className={styles.leaderRow}>
      <div className={styles.leaderIdentity}>
        <div><strong>{item.symbol}</strong><span>{item.name}</span></div>
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
      <WatchlistButton monitored={monitored} onAdd={onAdd} />
      {item.firstRejectionReason && (
        <div className={styles.leaderRejection}>
          <ShieldAlert size={12} />
          {radarLeaderReasonLabel(item.firstRejectionReason)}
        </div>
      )}
      {item.reasons.length > 0 && (
        <p className={styles.leaderReasons}>
          {item.reasons.map(radarLeaderReasonLabel).join(' · ')}
        </p>
      )}
    </article>
  );
}

function ObservationRow({ item, monitored, onAdd, formatTime }: {
  item: RadarLeaderObservationItem;
  monitored: boolean;
  onAdd: () => Promise<boolean>;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
}) {
  return (
    <article className={styles.leaderRow}>
      <div className={styles.leaderIdentity}>
        <div><strong>{item.symbol}</strong><span>{item.name}</span></div>
        <b>{item.changePercent >= 0 ? '+' : ''}{item.changePercent.toFixed(2)}%</b>
      </div>
      <div className={styles.leaderIndustry}>
        <span>{item.industryName}</span>
        <small>{item.industryCode} · 行业内第 {item.withinIndustryRank} 名</small>
      </div>
      <div className={styles.leaderTags}>
        <span>当轮价格 {item.price.toFixed(2)}</span>
        <span>真实行情</span>
      </div>
      <div className={styles.leaderAudit}>
        <span>行情时间 {formatTime(item.sourceTime, true)}</span>
        <span>观察候选，不含龙头评分</span>
      </div>
      <WatchlistButton monitored={monitored} onAdd={onAdd} />
    </article>
  );
}

export default function LeaderObservationPanel({
  module,
  reviewQueuePage,
  reviewQueueLoading,
  reviewQueueError,
  onReviewPageChange,
  formatTime,
  renderedAt,
}: LeaderObservationPanelProps) {
  const { addToWatchlist, isInWatchlist, mutationError } = useWatchlist();
  const [visibleObservationCount, setVisibleObservationCount] = useState(
    LEADER_OBSERVATION_PAGE_SIZE,
  );
  const hasEntries = module.preliminary.length + module.candidates.length + module.confirmed.length > 0;
  const reasons = Array.from(new Set([...module.reasonCodes, ...module.summary.reasonCodes]));
  const reviewQueue = module.reviewQueue;
  const observation = module.observation;
  const hasObservation = observation.displayAllowed && observation.items.length > 0;
  const sourceSummary = module.sourceSummary || [];
  const visibleObservationItems = takeVisibleItems(
    observation.items,
    visibleObservationCount,
  );

  return (
    <section className={`${styles.panel} ${styles.leaderPanel}`}>
      <div className={styles.panelHeader}>
        <div>
          <h2>三级龙头梯队</h2>
          <p>真实观察候选优先展示；证据完整时再显示三级评分和状态。</p>
        </div>
        <span className={stateTone(module.state)}>
          {renderStateIcon(module.state)}
          {STATE_COPY[module.state]}
        </span>
      </div>

      <div className={styles.leaderSummary}>
        <div><span>观察候选</span><strong>{module.summary.eligibleCount}</strong></div>
        <div><span>预备</span><strong>{module.summary.preliminaryCount}</strong></div>
        <div><span>候选</span><strong>{module.summary.candidateCount}</strong></div>
        <div><span>确认</span><strong>{module.summary.confirmedCount}</strong></div>
        <div><span>输入覆盖</span><strong>{Math.round(module.summary.coverage * 100)}%</strong></div>
        <div><span>公告发现</span><strong>{reviewQueue?.documentCount ?? '—'}</strong></div>
      </div>

      {mutationError && (
        <div className={styles.errorBanner}>
          <AlertTriangle size={14} />
          <span>{mutationError}</span>
        </div>
      )}

      {sourceSummary.length > 0 && (
        <section className={styles.leaderReviewQueue}>
          <header>
            <div>
              <span>真实来源概况</span>
              <strong>只汇总当前观察或公开梯队中能核对的来源</strong>
            </div>
            <b className={sourceSummary.every((item) => item.status === 'available')
              ? styles.queueReady
              : styles.reviewPending}
            >
              {sourceSummary.filter((item) => item.status === 'available').length} / {sourceSummary.length} 可用
            </b>
          </header>
          <div className={styles.leaderReviewMetrics}>
            {sourceSummary.map((item) => (
              <div key={item.domain}>
                <span>{SOURCE_DOMAIN_LABELS[item.domain]}</span>
                <strong>{SOURCE_STATUS_LABELS[item.status]}</strong>
                <small>
                  {item.expectedCount === null
                    ? `${item.coveredCount} 项已核对`
                    : `${item.coveredCount} / ${item.expectedCount}`}
                </small>
              </div>
            ))}
          </div>
          <footer>
            <span>缺失、过期或跨计划无法绑定的来源不会被标成可用。</span>
            <b>{formatTime(observation.asOf || module.lastSuccess?.asOf, true)}</b>
          </footer>
        </section>
      )}

      {hasObservation && (
        <section className={styles.leaderReviewQueue}>
          <header>
            <div>
              <span>实时观察候选（非评级）</span>
              <strong>行情与行业映射满足当轮观察口径即展示</strong>
            </div>
            <b className={observation.status === 'stale' ? styles.reviewPending : styles.queueReady}>
              {observation.status === 'stale' ? '数据已过期' : '实时可用'}
            </b>
          </header>
          <div className={styles.leaderReviewMetrics}>
            <div><span>扫描证券</span><strong>{observation.scannedCount}</strong></div>
            <div><span>有效映射</span><strong>{observation.mappedCount}</strong></div>
            <div><span>观察候选</span><strong>{observation.candidateCount}</strong></div>
            <div><span>行情时间</span><strong>{formatTime(observation.asOf, true)}</strong></div>
          </div>
          <div className={styles.leaderList}>
            {visibleObservationItems.map((item) => (
              <ObservationRow
                item={item}
                key={item.symbol}
                monitored={isInWatchlist(item.symbol)}
                onAdd={() => addToWatchlist(item.symbol, item.name)}
                formatTime={formatTime}
              />
            ))}
          </div>
          <div className={styles.progressiveListFooter} aria-live="polite">
            <span>已显示 {visibleObservationItems.length} / {observation.items.length} 只观察候选</span>
            {visibleObservationItems.length < observation.items.length && (
              <button
                type="button"
                onClick={() => setVisibleObservationCount((current) => nextVisibleCount(
                  current,
                  LEADER_OBSERVATION_PAGE_SIZE,
                  observation.items.length,
                ))}
              >
                继续显示 {Math.min(
                  LEADER_OBSERVATION_PAGE_SIZE,
                  observation.items.length - visibleObservationItems.length,
                )} 只
              </button>
            )}
          </div>
          <footer>
            <span>{observation.coverageScope}。主营、风险等证据不足会如实标注。</span>
            <b>{formatTime(observation.asOf, true)}</b>
          </footer>
        </section>
      )}

      {reviewQueue && reviewQueue.status !== 'not_ready' && (
        <section className={styles.leaderReviewQueue}>
          <header>
            <div>
              <span>官方公告扫描</span>
              <strong>{reviewQueue.status === 'ready' ? '真实来源可查看' : '扫描读取异常'}</strong>
            </div>
            <b className={reviewQueue.status === 'ready' ? styles.queueReady : styles.queueFailed}>
              {reviewQueue.status === 'ready' ? '只读可用' : '读取失败'}
            </b>
          </header>
          <div className={styles.leaderReviewMetrics}>
            <div><span>候选范围</span><strong>{reviewQueue.candidateCount}</strong></div>
            <div><span>官方公告</span><strong>{reviewQueue.documentCount}</strong></div>
            <div><span>正文快照</span><strong>{reviewQueue.contentSnapshotCount}</strong></div>
            <div><span>扫描时间</span><strong>{formatTime(reviewQueue.asOf, true)}</strong></div>
          </div>
          <footer>
            <span>{reviewQueue.coverageStatement}，页面不会把“可能相关”冒充确定风险。</span>
            <b>{formatTime(reviewQueue.asOf, true)}</b>
          </footer>
        </section>
      )}

      {reviewQueue?.status === 'ready' && (
        <section className={styles.reviewWorkbench}>
          <header>
            <div>
              <span>官方公告发现结果</span>
              <strong>只读来源清单 · 关键词命中不等于风险事实成立</strong>
            </div>
            <span>
              {reviewQueuePage
                ? `${reviewQueuePage.offset + 1}–${Math.min(
                  reviewQueuePage.offset + reviewQueuePage.items.length,
                  reviewQueuePage.total,
                )} / ${reviewQueuePage.total}`
                : '正在读取'}
            </span>
          </header>
          {reviewQueueError && (
            <div className={styles.reviewQueueError}>
              <AlertTriangle size={13} />
              {reviewQueueError}
            </div>
          )}
          <div className={styles.reviewDocumentList}>
            {reviewQueuePage?.items.map((item) => (
              <article className={styles.reviewDocumentRow} key={`${item.documentId}-${item.candidateCategory}`}>
                <div className={styles.reviewDocumentIdentity}>
                  <strong>{item.symbol}</strong><span>{item.issuerName}</span>
                </div>
                <div className={styles.reviewDocumentMain}>
                  <strong>{item.title}</strong>
                  <span>
                    {RISK_CATEGORY_LABELS[item.candidateCategory] || item.candidateCategory}
                    {' · '}{formatTime(item.publishedAt, true)}
                  </span>
                </div>
                <div className={styles.reviewDocumentState}>
                  <span className={styles.reviewPending}>可能相关</span>
                  <small>{item.sourceName}</small>
                </div>
                <div className={styles.reviewDocumentActions}>
                  <a
                    href={item.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="打开官方原文"
                    aria-label={`打开${item.symbol}官方公告`}
                  >
                    <ExternalLink size={14} />
                  </a>
                </div>
              </article>
            ))}
            {!reviewQueueLoading && reviewQueuePage?.items.length === 0 && (
              <div className={styles.reviewQueueEmpty}>当前分页没有官方公告发现项</div>
            )}
            {reviewQueueLoading && !reviewQueuePage && (
              <div className={styles.reviewQueueEmpty}>正在读取官方公告清单</div>
            )}
          </div>
          <footer className={styles.reviewPagination}>
            <button
              type="button"
              title="上一页"
              aria-label="官方公告上一页"
              disabled={reviewQueueLoading || !reviewQueuePage || reviewQueuePage.offset === 0}
              onClick={() => onReviewPageChange(Math.max(0, (reviewQueuePage?.offset || 0) - 8))}
            >
              <ChevronLeft size={15} />
            </button>
            <span>每页 8 条</span>
            <button
              type="button"
              title="下一页"
              aria-label="官方公告下一页"
              disabled={reviewQueueLoading || !reviewQueuePage
                || reviewQueuePage.offset + reviewQueuePage.items.length >= reviewQueuePage.total}
              onClick={() => onReviewPageChange((reviewQueuePage?.offset || 0) + 8)}
            >
              <ChevronRight size={15} />
            </button>
          </footer>
        </section>
      )}

      {reasons.length > 0 && (
        <div className={module.state === 'failed' ? styles.errorBanner : styles.warningBanner}>
          <AlertTriangle size={14} />
          <span>{reasons.map(radarLeaderReasonLabel).join(' · ')}</span>
        </div>
      )}

      <div className={styles.leaderMeta}>
        <span>快照时间 <b>{formatTime(module.lastSuccess?.asOf, true)}</b></span>
        <span>落库时间 <b>{formatTime(module.lastSuccess?.createdAt, true)}</b></span>
        <span>页面渲染 <b>{formatTime(renderedAt, true)}</b></span>
        <span>规则 <b>{module.summary.ruleVersion || '尚未形成'}</b></span>
        <span className={styles.shadowOnly}>只读监测</span>
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
                  <div><strong>{lane.title}</strong><span>{lane.description}</span></div>
                  <b>{entries.length}{overflow ? ` +${overflow}` : ''}</b>
                </header>
                <div className={styles.leaderList}>
                  {entries.length > 0 ? entries.map((item) => (
                    <LeaderRow
                      item={item}
                      key={`${item.state}-${item.symbol}`}
                      monitored={isInWatchlist(item.symbol)}
                      onAdd={() => addToWatchlist(item.symbol, item.name)}
                    />
                  )) : (
                    <div className={styles.leaderLaneEmpty}>本轮无该级别标的</div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      ) : hasObservation ? (
        <div className={styles.leaderEmpty}>
          <strong>三级规则状态尚未形成，真实观察候选继续可用</strong>
          <span>观察候选不含伪造评分；证据完整后才会进入预备、候选或已确认梯队。</span>
        </div>
      ) : (
        <div className={styles.leaderEmpty}>
          <strong>
            {module.state === 'empty'
              ? '本轮没有符合规则的龙头状态'
              : module.state === 'failed'
                ? '当前无法读取龙头梯队'
                : '尚未形成可展示的观察或三级龙头快照'}
          </strong>
          <span>
            {module.state === 'empty'
              ? '这是确定性规则计算得到的真实空榜，不代表数据抓取失败。'
              : module.state === 'failed'
                ? '失败与真实空榜严格区分，页面不会沿用未知来源的数据。'
                : '等待下一轮真实行情与行业映射形成观察候选。'}
          </span>
        </div>
      )}
    </section>
  );
}
