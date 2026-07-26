import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Layers3,
} from 'lucide-react';

import type { RadarEtfModule, RadarModuleState } from '@/types/radar';
import styles from './Radar.module.css';

interface EtfObservationPanelProps {
  module: RadarEtfModule;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
  renderedAt: string | null;
}

const STATE_COPY: Record<RadarModuleState, string> = {
  available: '真实候选可用',
  empty: '本轮真实空榜',
  stale: '候选快照已过期',
  failed: '产品主档或读取失败',
  not_ready: '阶段5尚未形成可用候选快照',
  not_enabled: '阶段5尚未启用',
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

function productTypeLabel(value: string) {
  return {
    etf: 'ETF',
    lof: 'LOF',
    reit: 'REIT',
    other_listed_fund: '其他上市基金',
  }[value] || value;
}

function managementLabel(value: string) {
  return value === 'passive_index' ? '被动指数' : value === 'active' ? '主动' : '未确认';
}

function assetLabel(value: string) {
  return value === 'domestic_equity' ? '沪深股票' : value;
}

export default function EtfObservationPanel({
  module,
  formatTime,
  renderedAt,
}: EtfObservationPanelProps) {
  const stateMessage = STATE_COPY[module.state];
  const hasCandidates = module.candidates.length > 0;
  const latestSource = module.lastAttempt?.sourceTime
    || module.lastSuccess?.sourceTime;

  return (
    <section className={`${styles.panel} ${styles.etfPanel}`}>
      <div className={styles.panelHeader}>
        <div>
          <h2>行业ETF观察</h2>
          <p>官方产品主档、真实候选组和替代标的；不生成交易建议。</p>
        </div>
        <span className={stateTone(module.state)}>
          {renderStateIcon(module.state)}
          {stateMessage}
        </span>
      </div>

      <div className={styles.etfSummary}>
        <div>
          <span>官方产品</span>
          <strong>{module.summary.productCount}</strong>
          <small>当前有效版本</small>
        </div>
        <div>
          <span>合资格产品</span>
          <strong>{module.summary.eligibleProductCount}</strong>
          <small>通过前置门槛</small>
        </div>
        <div>
          <span>候选组</span>
          <strong>{module.summary.candidateGroupCount}</strong>
          <small>{module.summary.formalStateEnabled ? '正式可用' : '正式状态未启用'}</small>
        </div>
        <div>
          <span>输入覆盖</span>
          <strong>{Math.round(module.summary.coverage * 100)}%</strong>
          <small>{module.summary.missingCount ? `${module.summary.missingCount} 项缺失` : '无缺失标记'}</small>
        </div>
        <div>
          <span>规则版本</span>
          <strong className={styles.etfSummaryText}>
            {module.summary.ruleVersionId || '未冻结'}
          </strong>
          <small>仅展示已记录版本</small>
        </div>
      </div>

      {module.reasonCodes.length > 0 && (
        <div className={module.state === 'failed' ? styles.errorBanner : styles.warningBanner}>
          <AlertTriangle size={14} />
          <span>{module.reasonCodes.join(' · ')}</span>
        </div>
      )}

      <div className={styles.etfMeta}>
        <span>源时间 <b>{formatTime(latestSource, true)}</b></span>
        <span>抓取时间 <b>{formatTime(module.lastAttempt?.fetchedAt || module.lastSuccess?.fetchedAt, true)}</b></span>
        <span>页面渲染 <b>{formatTime(renderedAt, true)}</b></span>
      </div>

      {hasCandidates ? (
        <div className={styles.etfSection}>
          <div className={styles.etfSectionHeader}>
            <div>
              <strong>主线候选组</strong>
              <span>按行业与指数归组，替代标的保留为同组备选。</span>
            </div>
            <span>{module.candidates.length} 组</span>
          </div>
          <div className={styles.etfCandidateList}>
            {module.candidates.map((candidate) => (
              <article
                className={styles.etfCandidateRow}
                key={`${candidate.industryCode}-${candidate.indexGroupKey}`}
              >
                <div className={styles.etfCandidateIdentity}>
                  <strong>{candidate.industryCode}</strong>
                  <span>{candidate.indexGroupKey}</span>
                </div>
                <div>
                  <span className={styles.etfFieldLabel}>代表标的</span>
                  <strong>{candidate.representativeSymbol || '—'}</strong>
                </div>
                <div>
                  <span className={styles.etfFieldLabel}>替代标的</span>
                  <strong>{candidate.alternativeSymbols.join('、') || '—'}</strong>
                </div>
                <div>
                  <span className={styles.etfFieldLabel}>状态</span>
                  <strong className={candidate.formalUsable ? styles.etfFormal : styles.etfPending}>
                    {candidate.formalUsable ? '正式可用' : '仅影子记录'}
                  </strong>
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : (
        <div className={styles.etfEmpty}>
          <strong>{module.state === 'empty' ? '本轮没有符合门槛的候选组' : '暂不展示候选组'}</strong>
          <span>
            {module.state === 'empty'
              ? '这是来自真实快照的空榜，不代表数据抓取失败。'
              : '候选规则或输入尚未达到正式展示门槛。'}
          </span>
        </div>
      )}

      <div className={styles.etfSection}>
        <div className={styles.etfSectionHeader}>
          <div>
            <strong>官方产品主档</strong>
            <span>显示当前版本与首次观测时间，未确认的历史时间不会被补写。</span>
          </div>
          <span>{module.products.length} 个</span>
        </div>
        {module.products.length > 0 ? (
          <div className={styles.etfProductTable}>
            <div className={styles.etfProductHead}>
              <span>代码 / 名称</span>
              <span>类别</span>
              <span>跟踪指数</span>
              <span>版本时间</span>
              <span>来源</span>
            </div>
            {module.products.map((product) => (
              <div className={styles.etfProductRow} key={`${product.symbol}-${product.sourceContractId}`}>
                <div>
                  <strong>{product.symbol}</strong>
                  <span>{product.officialName}</span>
                </div>
                <div>
                  <span>{productTypeLabel(product.productType)}</span>
                  <span>{managementLabel(product.managementStyle)} · {assetLabel(product.assetClass)}</span>
                </div>
                <span>{product.targetIndexName || '未确认'}</span>
                <div>
                  <span>{product.versionTimeKind === 'official_effective' ? '官方生效' : '首次观测'}</span>
                  <small>{formatTime(product.effectiveFrom, true)}</small>
                </div>
                <div>
                  <span>{product.source}</span>
                  <small>抓取 {formatTime(product.fetchedAt, true)}</small>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className={styles.inlineEmpty}>暂无已记录的官方产品主档。</div>
        )}
      </div>
    </section>
  );
}
