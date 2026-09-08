import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Layers3,
} from 'lucide-react';
import { useState } from 'react';

import { nextVisibleCount, takeVisibleItems } from '@/lib/progressive-list';
import type {
  RadarEtfModule,
  RadarEtfProductResearchState,
  RadarModuleState,
  RadarReplayEtfResearchResponse,
} from '@/types/radar';
import styles from './Radar.module.css';

interface EtfObservationPanelProps {
  module: RadarEtfModule;
  research: RadarReplayEtfResearchResponse | null;
  researchLoadError: string;
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

const RESEARCH_STATE_LABELS: Record<RadarEtfProductResearchState, string> = {
  product_ready_for_index_research: '可继续指数研究',
  active_product_separate_track: '主动ETF单独观察',
  out_of_scope_asset: '非境内股票研究范围',
  product_evidence_incomplete: '产品证据待补齐',
};

const ETF_LIST_PAGE_SIZE = 100;

export default function EtfObservationPanel({
  module,
  research,
  researchLoadError,
  formatTime,
  renderedAt,
}: EtfObservationPanelProps) {
  const [visibleResearchCount, setVisibleResearchCount] = useState(ETF_LIST_PAGE_SIZE);
  const [visibleProductCount, setVisibleProductCount] = useState(ETF_LIST_PAGE_SIZE);
  const stateMessage = STATE_COPY[module.state];
  const hasCandidates = module.candidates.length > 0;
  const latestSource = module.lastAttempt?.sourceTime
    || module.lastSuccess?.sourceTime;
  const researchSnapshot = research?.snapshot || null;
  const visibleResearchItems = takeVisibleItems(
    researchSnapshot?.items || [],
    visibleResearchCount,
  );
  const visibleProducts = takeVisibleItems(module.products, visibleProductCount);

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
                    {candidate.formalUsable ? '研究数据完整' : '证据补充中'}
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
              : '候选规则或真实输入尚不完整。'}
          </span>
        </div>
      )}

      <div className={styles.etfSection}>
        <div className={styles.etfSectionHeader}>
          <div>
            <strong>逐产品研究分流</strong>
            <span>来自已验证的阶段9冻结输出；只表示研究范围与证据完整度，不是排名或交易建议。</span>
          </div>
          <span>{researchSnapshot ? `${researchSnapshot.productCount} 只` : '尚未发布'}</span>
        </div>
        {researchLoadError ? (
          <div className={styles.warningBanner} role="status">
            <AlertTriangle size={14} />
            <span>{researchLoadError}；未用当前产品主档推测历史研究状态。</span>
          </div>
        ) : research?.state === 'failed' ? (
          <div className={styles.errorBanner} role="alert">
            <AlertTriangle size={14} />
            <span>研究状态发布物校验失败，已停止展示。</span>
          </div>
        ) : researchSnapshot ? (
          <>
            <div className={styles.etfResearchSummary}>
              <span>指数研究 <b>{researchSnapshot.indexResearchReadyCount}</b></span>
              <span>主动分轨 <b>{researchSnapshot.activeSeparateTrackCount}</b></span>
              <span>范围外 <b>{researchSnapshot.outOfScopeAssetCount}</b></span>
              <span>证据待补 <b>{researchSnapshot.evidenceIncompleteCount}</b></span>
              <span>监测证据就绪 <b>{researchSnapshot.monitoringReadyCount}</b></span>
            </div>
            <div className={styles.etfResearchList}>
              {visibleResearchItems.map((item) => (
                <article key={item.symbol} className={styles.etfResearchRow}>
                  <div><strong>{item.symbol}</strong><span>{item.targetIndexName || '未标明跟踪指数'}</span></div>
                  <strong>{RESEARCH_STATE_LABELS[item.researchState]}</strong>
                  <span>
                    监测：{item.monitoringStatus === 'ready' ? '证据就绪' : item.monitoringStatus === 'missing' ? '证据待补' : '未提供正式准入包'}
                  </span>
                  <span>
                    排名：{item.rankingStatus === 'ready' ? '政策就绪' : item.rankingStatus === 'missing' ? '政策待补' : '未评估'}
                  </span>
                </article>
              ))}
            </div>
            <div className={styles.progressiveListFooter} aria-live="polite">
              <span>已显示 {visibleResearchItems.length} / {researchSnapshot.items.length} 只</span>
              {visibleResearchItems.length < researchSnapshot.items.length && (
                <button
                  type="button"
                  onClick={() => setVisibleResearchCount((current) => nextVisibleCount(
                    current,
                    ETF_LIST_PAGE_SIZE,
                    researchSnapshot.items.length,
                  ))}
                >
                  继续显示 {Math.min(
                    ETF_LIST_PAGE_SIZE,
                    researchSnapshot.items.length - visibleResearchItems.length,
                  )} 只
                </button>
              )}
            </div>
            <div className={styles.etfMeta}>
              <span>冻结时点 <b>{formatTime(researchSnapshot.asOf, true)}</b></span>
              <span>来源 <b>{researchSnapshot.source}</b></span>
            </div>
          </>
        ) : (
          <div className={styles.inlineEmpty}>
            尚无已发布的逐产品研究快照；页面不从实时产品主档猜测阶段9结果。
          </div>
        )}
      </div>

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
            {visibleProducts.map((product) => (
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
            <div className={styles.progressiveListFooter} aria-live="polite">
              <span>已显示 {visibleProducts.length} / {module.products.length} 个产品</span>
              {visibleProducts.length < module.products.length && (
                <button
                  type="button"
                  onClick={() => setVisibleProductCount((current) => nextVisibleCount(
                    current,
                    ETF_LIST_PAGE_SIZE,
                    module.products.length,
                  ))}
                >
                  继续显示 {Math.min(
                    ETF_LIST_PAGE_SIZE,
                    module.products.length - visibleProducts.length,
                  )} 个
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className={styles.inlineEmpty}>暂无已记录的官方产品主档。</div>
        )}
      </div>
    </section>
  );
}
