import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ExternalLink,
  Layers3,
  Search,
  ShieldAlert,
  Send,
  UserPlus,
} from 'lucide-react';
import { useState } from 'react';

import type {
  RadarLeaderItem,
  RadarLeaderModule,
  RadarLeaderReviewDocument,
  RadarLeaderReviewFormResponse,
  RadarLeaderReviewSubmissionDraft,
  RadarLeaderReviewQueueResponse,
  RadarModuleState,
} from '@/types/radar';
import styles from './Radar.module.css';
import { useWatchlist } from '@/hooks/useWatchlist';

interface LeaderObservationPanelProps {
  module: RadarLeaderModule;
  reviewQueuePage: RadarLeaderReviewQueueResponse | null;
  selectedReviewDocument: RadarLeaderReviewDocument | null;
  reviewForm: RadarLeaderReviewFormResponse | null;
  reviewQueueLoading: boolean;
  reviewQueueError: string;
  reviewSubmitting: boolean;
  reviewSubmitMessage: string;
  onReviewPageChange: (offset: number) => void;
  onReviewDocumentSelect: (item: RadarLeaderReviewDocument) => void;
  onReviewSubmit: (draft: RadarLeaderReviewSubmissionDraft) => void;
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

const REVIEW_REPLAY_STATUS_LABELS: Record<
  RadarLeaderReviewFormResponse['replayDiagnostic']['status'],
  string
> = {
  ready: '连续证据链可重放',
  missing: '审核版本链尚不完整',
  source_unverified: '版本链校验失败',
  stale: '最新审核版本已过期',
  source_failed: '版本链重放失败',
};

const REVIEW_REPLAY_REASON_LABELS: Record<string, string> = {
  risk_lifecycle_version_history_insufficient: '尚无人工审核版本',
  risk_evidence_bundle_audit_history_insufficient:
    '仅有一个有效版本，还需一份包含实质证据变化的后续版本',
  risk_evidence_bundle_audit_material_change_missing:
    '后续版本只有元数据变化，没有实质证据变化',
  risk_lifecycle_version_contract_unverified: '审核版本数据结构无法验证',
};

function reviewReplayReason(reviewForm: RadarLeaderReviewFormResponse) {
  const reasons = reviewForm.replayDiagnostic.reasonCodes
    .map((reason) => REVIEW_REPLAY_REASON_LABELS[reason] || '版本链尚未通过完整性校验');
  return Array.from(new Set(reasons)).join('·');
}

const RISK_CATEGORY_LABELS: Record<string, string> = {
  reduction: '减持',
  unlock: '解禁',
  regulatory: '监管',
  investigation: '调查',
  litigation: '诉讼',
  earnings: '业绩',
  audit: '审计',
};

const FACT_LABELS: Record<string, string> = {
  case_id: '案号',
  reporting_period: '报告期',
  audit_report_id: '审计报告编号',
  referenced_document_id: '正文原公告编号',
  effective_date: '生效日期',
  effective_interval: '实施区间',
};

const EVENT_SUBTYPES: Record<string, Array<{ value: string; label: string }>> = {
  reduction: [{ value: 'reduction_plan', label: '减持计划' }],
  unlock: [{ value: 'share_unlock', label: '解除限售' }],
  regulatory: [
    { value: 'regulatory_inquiry', label: '监管问询' },
    { value: 'regulatory_measure', label: '监管措施' },
    { value: 'disciplinary_action', label: '纪律处分' },
    { value: 'administrative_penalty', label: '行政处罚' },
  ],
  investigation: [
    { value: 'formal_investigation', label: '正式调查' },
    { value: 'major_violation_investigation', label: '重大违法调查' },
  ],
  litigation: [
    { value: 'material_litigation', label: '重大诉讼' },
    { value: 'arbitration', label: '仲裁' },
  ],
  earnings: [
    { value: 'earnings_loss', label: '业绩亏损' },
    { value: 'earnings_decline', label: '业绩下降' },
    { value: 'material_impairment', label: '重大减值' },
  ],
  audit: [
    { value: 'audit_qualified', label: '保留意见' },
    { value: 'audit_adverse', label: '否定意见' },
    { value: 'audit_disclaimer', label: '无法表示意见' },
    { value: 'audit_going_concern', label: '持续经营重大疑虑' },
  ],
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

function LeaderRow({
  item,
  monitored,
  onAdd,
}: {
  item: RadarLeaderItem;
  monitored: boolean;
  onAdd: () => void;
}) {
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
      <button
        className={styles.leaderWatchButton}
        disabled={monitored}
        onClick={onAdd}
      >
        <UserPlus size={13} />
        {monitored ? '已在监测列表' : '加入监测列表'}
      </button>
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

function createReviewDraft(
  reviewForm: RadarLeaderReviewFormResponse,
): RadarLeaderReviewSubmissionDraft {
  const eventOptions = EVENT_SUBTYPES[reviewForm.item.candidateCategory] || [];
  return {
    reviewerKey: '',
    effectiveUntil: null,
    factSupplements: reviewForm.candidate.requiredFactKinds.map((factKind) => ({
      factKind,
      sourceValue: '',
      pageNumber: 1,
      sourceFragment: '',
    })),
    targetEvent: {
      eventVersion: 'v1',
      eventSubtype: eventOptions[0]?.value || '',
      sourceUrl: '',
      documentId: '',
      publishedAt: '',
      effectiveFrom: '',
      effectiveUntil: '',
      factSummary: '',
      officialStatus: 'active',
    },
    relationKind: 'resolves',
    replacementEventVersion: null,
    decisionSummary: '',
    confirmOfficialEvidence: false,
  };
}

function ManualReviewForm({
  reviewForm,
  reviewSubmitting,
  reviewSubmitMessage,
  onReviewSubmit,
}: {
  reviewForm: RadarLeaderReviewFormResponse;
  reviewSubmitting: boolean;
  reviewSubmitMessage: string;
  onReviewSubmit: (draft: RadarLeaderReviewSubmissionDraft) => void;
}) {
  const [reviewDraft, setReviewDraft] = useState(
    () => createReviewDraft(reviewForm),
  );
  const updateFact = (
    index: number,
    field: 'sourceValue' | 'pageNumber' | 'sourceFragment',
    value: string | number,
  ) => {
    setReviewDraft((current) => ({
      ...current,
      factSupplements: current.factSupplements.map((fact, factIndex) => (
        factIndex === index ? { ...fact, [field]: value } : fact
      )),
    }));
  };
  const updateTargetEvent = (
    patch: Partial<RadarLeaderReviewSubmissionDraft['targetEvent']>,
  ) => {
    setReviewDraft((current) => ({
      ...current,
      targetEvent: { ...current.targetEvent, ...patch },
    }));
  };
  const updateDraft = (
    patch: Partial<RadarLeaderReviewSubmissionDraft>,
  ) => setReviewDraft((current) => ({ ...current, ...patch }));

  return (
    <form
      className={styles.manualReviewForm}
      onSubmit={(event) => {
        event.preventDefault();
        onReviewSubmit(reviewDraft);
      }}
    >
      <div className={styles.manualReviewSection}>
        <header>
          <strong>人工事实</strong>
          <span>值与原文片段必须出现在所选页内</span>
        </header>
        <div className={styles.manualFactGrid}>
          {reviewDraft.factSupplements.map((fact, index) => (
            <div className={styles.manualFactRow} key={fact.factKind}>
              <label>
                <span>{FACT_LABELS[fact.factKind] || fact.factKind}</span>
                <input
                  value={fact.sourceValue}
                  onChange={(event) => updateFact(index, 'sourceValue', event.target.value)}
                  required
                />
              </label>
              <label>
                <span>页码</span>
                <input
                  type="number"
                  min={1}
                  max={reviewForm.pageCount}
                  value={fact.pageNumber}
                  onChange={(event) => updateFact(index, 'pageNumber', Number(event.target.value))}
                  required
                />
              </label>
              <label>
                <span>原文片段</span>
                <input
                  value={fact.sourceFragment}
                  onChange={(event) => updateFact(index, 'sourceFragment', event.target.value)}
                  required
                />
              </label>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.manualReviewSection}>
        <header>
          <strong>原风险事件</strong>
          <span>关联的旧公告必须早于当前公告</span>
        </header>
        <div className={styles.manualEventGrid}>
          <label>
            <span>审核者标识</span>
            <input
              value={reviewDraft.reviewerKey}
              onChange={(event) => updateDraft({ reviewerKey: event.target.value })}
              placeholder="reviewer-local-1"
              required
            />
          </label>
          <label>
            <span>事件类型</span>
            <select
              value={reviewDraft.targetEvent.eventSubtype}
              onChange={(event) => updateTargetEvent({ eventSubtype: event.target.value })}
              required
            >
              {(EVENT_SUBTYPES[reviewForm.item.candidateCategory] || []).map((option) => (
                <option value={option.value} key={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>巨潮原文ID</span>
            <input
              value={reviewDraft.targetEvent.documentId}
              onChange={(event) => updateTargetEvent({ documentId: event.target.value })}
              placeholder="cninfo:1224000001"
              required
            />
          </label>
          <label>
            <span>事件版本</span>
            <input
              value={reviewDraft.targetEvent.eventVersion}
              onChange={(event) => updateTargetEvent({ eventVersion: event.target.value })}
              required
            />
          </label>
          <label className={styles.wideField}>
            <span>官方原公告地址</span>
            <input
              type="url"
              value={reviewDraft.targetEvent.sourceUrl}
              onChange={(event) => updateTargetEvent({ sourceUrl: event.target.value })}
              required
            />
          </label>
          <label>
            <span>原公告时间</span>
            <input
              type="datetime-local"
              value={reviewDraft.targetEvent.publishedAt}
              onChange={(event) => updateTargetEvent({ publishedAt: event.target.value })}
              required
            />
          </label>
          <label>
            <span>事件生效时间</span>
            <input
              type="datetime-local"
              value={reviewDraft.targetEvent.effectiveFrom}
              onChange={(event) => updateTargetEvent({ effectiveFrom: event.target.value })}
              required
            />
          </label>
          <label>
            <span>事件结束时间</span>
            <input
              type="datetime-local"
              value={reviewDraft.targetEvent.effectiveUntil}
              onChange={(event) => updateTargetEvent({ effectiveUntil: event.target.value })}
              required={['reduction', 'unlock'].includes(reviewForm.item.candidateCategory)}
            />
          </label>
          <label>
            <span>官方状态</span>
            <select
              value={reviewDraft.targetEvent.officialStatus}
              onChange={(event) => updateTargetEvent({
                officialStatus: event.target.value as 'active' | 'completed' | 'withdrawn',
              })}
            >
              <option value="active">仍有效</option>
              <option value="completed">已完成</option>
              <option value="withdrawn">已撤回</option>
            </select>
          </label>
          <label>
            <span>关系</span>
            <select
              value={reviewDraft.relationKind}
              onChange={(event) => {
                const relationKind = event.target.value as 'resolves' | 'supersedes';
                updateDraft({
                  relationKind,
                  replacementEventVersion: relationKind === 'supersedes'
                    ? reviewForm.nextReviewVersion
                    : null,
                });
              }}
            >
              <option value="resolves">解除 / 完成原事件</option>
              <option value="supersedes">修正 / 替代原事件</option>
            </select>
          </label>
          <label className={styles.wideField}>
            <span>事件事实摘要</span>
            <input
              value={reviewDraft.targetEvent.factSummary}
              onChange={(event) => updateTargetEvent({ factSummary: event.target.value })}
              required
            />
          </label>
          <label className={styles.wideField}>
            <span>审核结论</span>
            <textarea
              value={reviewDraft.decisionSummary}
              onChange={(event) => updateDraft({ decisionSummary: event.target.value })}
              required
            />
          </label>
        </div>
      </div>

      <div className={styles.manualReviewConfirm}>
        <label>
          <input
            type="checkbox"
            checked={reviewDraft.confirmOfficialEvidence}
            onChange={(event) => updateDraft({ confirmOfficialEvidence: event.target.checked })}
          />
          <span>我已逐页核对官方正文、原公告和事件关系</span>
        </label>
        <button
          type="submit"
          disabled={!reviewForm.writeEnabled
            || !reviewDraft.confirmOfficialEvidence
            || reviewSubmitting}
        >
          <Send size={14} />
          {reviewSubmitting ? '校验中' : `提交 ${reviewForm.nextReviewVersion}`}
        </button>
      </div>
      {reviewSubmitMessage && (
        <div className={styles.reviewSubmitMessage}>{reviewSubmitMessage}</div>
      )}
    </form>
  );
}

export default function LeaderObservationPanel({
  module,
  reviewQueuePage,
  selectedReviewDocument,
  reviewForm,
  reviewQueueLoading,
  reviewQueueError,
  reviewSubmitting,
  reviewSubmitMessage,
  onReviewPageChange,
  onReviewDocumentSelect,
  onReviewSubmit,
  formatTime,
  renderedAt,
}: LeaderObservationPanelProps) {
  const { addToWatchlist, isInWatchlist } = useWatchlist();
  const hasEntries = module.preliminary.length
    + module.candidates.length
    + module.confirmed.length > 0;
  const reasons = Array.from(new Set([
    ...module.reasonCodes,
    ...module.summary.reasonCodes,
  ]));
  const reviewQueue = module.reviewQueue;

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

      {reviewQueue && reviewQueue.status !== 'not_ready' && (
        <section className={styles.leaderReviewQueue}>
          <header>
            <div>
              <span>D2 官方风险审核队列</span>
              <strong>
                {reviewQueue.status === 'ready' ? '完整批次已冻结' : '队列读取异常'}
              </strong>
            </div>
            <b className={reviewQueue.status === 'ready' ? styles.queueReady : styles.queueFailed}>
              {reviewQueue.status === 'ready' ? 'D2 完成' : '读取失败'}
            </b>
          </header>
          <div className={styles.leaderReviewMetrics}>
            <div>
              <span>候选范围</span>
              <strong>{reviewQueue.candidateCount}</strong>
            </div>
            <div>
              <span>官方公告</span>
              <strong>{reviewQueue.documentCount}</strong>
            </div>
            <div>
              <span>已审公告</span>
              <strong>{reviewQueue.reviewedDocumentCount}</strong>
            </div>
            <div>
              <span>D8 人工版本</span>
              <strong>{reviewQueue.reviewVersionCount}</strong>
            </div>
          </div>
          <footer>
            <span>
              D2 元数据已通过分类、分页和连续时间窗复验；D8 仍需选中公告形成至少两个合法连续人工版本。
            </span>
            <b>{formatTime(reviewQueue.asOf, true)}</b>
          </footer>
        </section>
      )}

      {reviewQueue?.status === 'ready' && (
        <section className={styles.reviewWorkbench}>
          <header>
            <div>
              <span>风险公告审核工作台</span>
              <strong>只读审核状态 · 页面不触发抓取</strong>
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
                  <strong>{item.symbol}</strong>
                  <span>{item.issuerName}</span>
                </div>
                <div className={styles.reviewDocumentMain}>
                  <strong>{item.title}</strong>
                  <span>
                    {RISK_CATEGORY_LABELS[item.candidateCategory] || item.candidateCategory}
                    {' · '}{formatTime(item.publishedAt, true)}
                  </span>
                </div>
                <div className={styles.reviewDocumentState}>
                  <span className={item.contentStatus === 'available'
                    ? styles.queueReady
                    : styles.reviewPending}>
                    {item.contentStatus === 'available' ? '正文已抓取' : '正文未抓取'}
                  </span>
                  <small>D8版本 {item.reviewVersionCount}</small>
                </div>
                <div className={styles.reviewDocumentActions}>
                  <button
                    type="button"
                    title="查看本地审核状态"
                    aria-label={`查看${item.symbol}公告审核状态`}
                    onClick={() => onReviewDocumentSelect(item)}
                    disabled={reviewQueueLoading}
                  >
                    <Search size={14} />
                  </button>
                  <a
                    href={item.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="打开巨潮官方原文"
                    aria-label={`打开${item.symbol}官方公告`}
                  >
                    <ExternalLink size={14} />
                  </a>
                </div>
              </article>
            ))}
            {!reviewQueueLoading && reviewQueuePage?.items.length === 0 && (
              <div className={styles.reviewQueueEmpty}>当前分页没有公告工作项</div>
            )}
            {reviewQueueLoading && !reviewQueuePage && (
              <div className={styles.reviewQueueEmpty}>正在读取审核队列</div>
            )}
          </div>

          {selectedReviewDocument && (
            <div className={styles.reviewDocumentDetail}>
              <div>
                <span>当前详情</span>
                <strong>{selectedReviewDocument.symbol} · {selectedReviewDocument.issuerName}</strong>
              </div>
              <div>
                <span>正文状态</span>
                <strong>{selectedReviewDocument.contentStatus === 'available'
                  ? `${selectedReviewDocument.contentSnapshotCount} 个快照`
                  : '尚未抓取'}</strong>
              </div>
              <div>
                <span>D8人工版本</span>
                <strong>{selectedReviewDocument.reviewVersionCount}</strong>
              </div>
              <div>
                <span>正式可用</span>
                <strong>否</strong>
              </div>
            </div>
          )}

          {reviewForm && (
            <div className={styles.reviewFormPreview}>
              <div className={styles.reviewFormHeading}>
                <div>
                  <span>D8 人工审核预览</span>
                  <strong>只读正文与字段校验，不自动形成正式状态</strong>
                </div>
                <span className={reviewForm.writeEnabled
                  ? styles.queueReady
                  : styles.reviewPending}>
                  {reviewForm.writeEnabled ? '可提交审核版本' : '当前仅预览'}
                </span>
              </div>
              <div className={styles.reviewFormSummary}>
                <div>
                  <span>候选类型</span>
                  <strong>{reviewForm.candidate.candidateKind === 'fact_extraction_missing'
                    ? '自动事实缺失，需人工补录'
                    : '需要人工关联已有事件'}</strong>
                </div>
                <div>
                  <span>需要字段</span>
                  <strong>{reviewForm.candidate.requiredFactKinds.join(' · ') || '暂无'}</strong>
                </div>
                <div>
                  <span>下一版本</span>
                  <strong>{reviewForm.nextReviewVersion}</strong>
                </div>
                <div>
                  <span>正文哈希</span>
                  <strong title={reviewForm.contentSha256}>{reviewForm.contentSha256.slice(0, 16)}…</strong>
                </div>
              </div>
              <div className={styles.reviewFormNotice}>
                <ShieldAlert size={14} />
                <span>
                  D8-D9只读重放：
                  {REVIEW_REPLAY_STATUS_LABELS[reviewForm.replayDiagnostic.status]}。
                  已重建 {reviewForm.replayDiagnostic.bundleCount}/
                  {reviewForm.replayDiagnostic.reviewVersionCount} 个证据包。
                  {reviewReplayReason(reviewForm) && ` ${reviewReplayReason(reviewForm)}。`}
                  当前结果仍仅供研究，不形成正式状态或交易结论。
                </span>
              </div>
              <div className={styles.reviewEvidencePages}>
                {reviewForm.pages.map((page) => (
                  <details key={page.pageNumber}>
                    <summary>第 {page.pageNumber} 页 · 官方正文</summary>
                    <pre>{page.text}</pre>
                  </details>
                ))}
              </div>
              <ManualReviewForm
                reviewForm={reviewForm}
                reviewSubmitting={reviewSubmitting}
                reviewSubmitMessage={reviewSubmitMessage}
                onReviewSubmit={onReviewSubmit}
              />
            </div>
          )}

          <footer className={styles.reviewPagination}>
            <button
              type="button"
              title="上一页"
              aria-label="审核队列上一页"
              disabled={reviewQueueLoading || !reviewQueuePage || reviewQueuePage.offset === 0}
              onClick={() => onReviewPageChange(Math.max(0, (reviewQueuePage?.offset || 0) - 8))}
            >
              <ChevronLeft size={15} />
            </button>
            <span>每页 8 条</span>
            <button
              type="button"
              title="下一页"
              aria-label="审核队列下一页"
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
                      <LeaderRow
                        item={item}
                        key={`${item.state}-${item.symbol}`}
                        monitored={isInWatchlist(item.symbol)}
                        onAdd={() => void addToWatchlist(item.symbol, item.name)}
                      />
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
