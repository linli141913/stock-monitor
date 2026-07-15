import { useState } from 'react';
import { IndustryMonitor, DynamicsItem } from '@/types/industry';
import { BarChart3, ChevronDown, FileText, Layers, ExternalLink } from 'lucide-react';
import styles from './IndustryMonitorCard.module.css';

interface Props {
  data: IndustryMonitor;
  loading?: boolean;
  refreshing?: boolean;
  statusMessage?: string;
}

const DIRECTION_LABELS = {
  positive: '正面',
  negative: '负面',
  neutral: '中性',
  uncertain: '影响待判断',
} as const;

const PRIORITY_LABELS = {
  P1: 'P1 重大',
  P2: 'P2 重要',
  P3: 'P3 一般',
} as const;

const PRIORITY_RANK = { P1: 1, P2: 2, P3: 3 } as const;

const EVIDENCE_LEVEL_CLASSES = {
  S: styles.evidenceS,
  A: styles.evidenceA,
  B: styles.evidenceB,
  C: styles.evidenceC,
} as const;

const DIRECTION_CLASSES = {
  positive: styles.directionPositive,
  negative: styles.directionNegative,
  neutral: styles.directionNeutral,
  uncertain: styles.directionUncertain,
} as const;

const PRIORITY_CLASSES = {
  P1: styles.priorityP1,
  P2: styles.priorityP2,
  P3: styles.priorityP3,
} as const;

const formatSystemTime = (value?: string | null) => (
  value ? value.replace('T', ' ').slice(0, 19) : ''
);

const getSourceTimeLabel = (item: DynamicsItem) => {
  if (item.timePrecision === 'date') {
    return `公告日期：${item.time || '日期暂缺'}（具体时刻未提供）`;
  }
  if (item.timePrecision === 'datetime') {
    return `发布时间：${item.time || '时间暂缺'}`;
  }
  return item.time || '来源时间暂缺';
};

export default function IndustryMonitorCard({ data, loading, refreshing, statusMessage }: Props) {
  const [policiesOpen, setPoliciesOpen] = useState(false);
  const [dynamicsOpen, setDynamicsOpen] = useState(false);

  const getSourceClass = (source: string) => {
    if (source.includes('巨潮')) return styles.sourceCninfo;
    if (source.includes('财联社')) return styles.sourceCls;
    if (source.includes('东财') || source.includes('东方财富') || source.includes('研报')) return styles.sourceEastmoney;
    if (source.includes('新浪')) return styles.sourceSina;
    if (source.includes('报') || source.includes('央视') || source.includes('21世纪') || source.includes('报道')) return styles.sourcePress;
    return styles.sourceDefault;
  };

  const renderDimensionTags = (item: DynamicsItem, compact = false) => {
    if (!item.evidenceLevel || !item.direction || !item.priority) return null;
    return (
      <span className={`${styles.dimensionTags} ${compact ? styles.dimensionTagsCompact : ''}`}>
        <span
          className={`${styles.dimensionTag} ${styles.evidenceTag} ${EVIDENCE_LEVEL_CLASSES[item.evidenceLevel]}`}
          title={item.verificationStatus}
        >
          来源等级 {item.evidenceLevel}
        </span>
        <span className={`${styles.dimensionTag} ${DIRECTION_CLASSES[item.direction]}`}>
          {DIRECTION_LABELS[item.direction]}
        </span>
        <span className={`${styles.dimensionTag} ${styles.priorityTag} ${PRIORITY_CLASSES[item.priority]}`}>
          {PRIORITY_LABELS[item.priority]}
        </span>
      </span>
    );
  };

  const getHighestPriorityItem = (items?: DynamicsItem[]) => {
    return (items || []).reduce<DynamicsItem | undefined>((current, item) => {
      if (!item.evidenceLevel || !item.priority) return current;
      if (!current?.priority || PRIORITY_RANK[item.priority] < PRIORITY_RANK[current.priority]) {
        return item;
      }
      return current;
    }, undefined);
  };

  const renderSummary = (items: DynamicsItem[] | undefined, fallback: string) => {
    const highest = getHighestPriorityItem(items);
    if (!highest) return <span className={styles.summaryText}>{fallback}</span>;
    return (
      <span className={styles.summaryEvent}>
        {renderDimensionTags(highest, true)}
        <span className={styles.summaryTitle}>{highest.title}</span>
      </span>
    );
  };

  const renderList = (items?: DynamicsItem[]) => {
    if (!items || items.length === 0) {
      return <div className={styles.emptyList}>当日暂无已验证动态</div>;
    }
    return (
      <div className={styles.dynamicsList}>
        {items.map((item, index) => {
          return (
            <div key={item.url || `${item.title}-${item.time || index}`} className={styles.dynamicsItem}>
              {renderDimensionTags(item)}
              <div className={styles.itemHeader}>
                <div className={styles.itemTitleArea}>
                  <span className={`${styles.sourceBadge} ${getSourceClass(item.source)}`}>
                    {item.source}
                  </span>
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noopener noreferrer" className={styles.itemLink}>
                      {item.title}
                      <ExternalLink size={12} className={styles.linkIcon} />
                    </a>
                  ) : (
                    <span className={styles.itemTitle}>{item.title}</span>
                  )}
                </div>
              </div>
              <div className={styles.itemFooter}>
                <span className={styles.itemTimes}>
                  <span className={styles.itemTime}>{getSourceTimeLabel(item)}</span>
                  {item.discoveredAt && (
                    <span className={styles.itemTime}>系统发现：{formatSystemTime(item.discoveredAt)}</span>
                  )}
                </span>
                <span className={styles.itemSource}>资讯来源：{item.source}</span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  if (loading) {
    return (
      <div className={styles.card}>
        <div className={styles.header}>
          <div className={styles.titleArea}>
            <BarChart3 className={styles.icon} size={20} />
            <h2 className={styles.title}>行业监测</h2>
          </div>
          <div className={styles.loadingText}>
            <div className={styles.spinner} />
            <span>行业数据加载中...</span>
          </div>
        </div>
        <div className={styles.loadingContainer}>
          <div className={styles.skeletonRow} style={{ width: '80%' }} />
          <div className={styles.skeletonRow} style={{ width: '100%' }} />
          <div className={styles.skeletonRow} style={{ width: '90%' }} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleArea}>
          <BarChart3 className={styles.icon} size={20} />
          <h2 className={styles.title}>行业监测</h2>
        </div>
        <div className={styles.headerMeta}>
          <div className={styles.industryName}>所属行业：{data?.industryName || '-'}</div>
          <div className={styles.refreshStatus} aria-live="polite">
            {refreshing ? (
              <>
                <div className={styles.spinner} />
                <span>后台更新中</span>
              </>
            ) : statusMessage ? (
              <span className={styles.statusWarning}>{statusMessage}</span>
            ) : data?.industryDataStatus === 'unavailable' ? (
              <span className={styles.statusWarning}>{data.industryDataError || '行业指标上游暂不可用'}</span>
            ) : data?.fetchedAt ? (
              <span>后端抓取：{data.fetchedAt.replace('T', ' ').slice(0, 19)}</span>
            ) : null}
          </div>
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.row}>
          <div className={styles.label} title="由同一行业行的资金净额和涨跌幅计算，不是上游原始评分">计算热度</div>
          <div className={styles.valueArea}>
            <div className={styles.progressTrack}>
              <div 
                className={styles.progressFill} 
                style={{ width: `${data?.heatScore ?? 0}%` }}
              ></div>
            </div>
            <span className={styles.score}>{data?.heatScore == null ? '暂无数据' : `${data.heatScore}/100`}</span>
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>板块涨跌</div>
          <div className={`${styles.value} ${data?.sectorChangePercent != null && data.sectorChangePercent > 0 ? 'text-rise' : data?.sectorChangePercent != null && data.sectorChangePercent < 0 ? 'text-fall' : ''}`}>
            {data?.sectorChangePercent == null ? '暂无数据' : `${data.sectorChangePercent > 0 ? '+' : ''}${data.sectorChangePercent.toFixed(2)}%`}
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>资金流向</div>
          <div className={styles.valueWithNote}>
            <div className={`${styles.value} ${(data?.fundFlow || '').includes('流入') ? 'text-rise' : ((data?.fundFlow || '').includes('流出') ? 'text-fall' : '')}`}>
              {data?.fundFlow || '-'}
            </div>
            {data?.fundFlowTimeScope && (
              <div className={styles.dataNote}>{data.fundFlowTimeScope}</div>
            )}
          </div>
        </div>

        <div className={styles.linkagePanel}>
          <div className={styles.linkageHeader}>
            <span>板块与海外联动风险</span>
            <span className={`${styles.linkageBadge} ${
              data?.linkageRisk?.riskStatus === 'warning'
                ? styles.linkageWarning
                : data?.linkageRisk?.riskStatus === 'watch'
                  ? styles.linkageWatch
                  : data?.linkageRisk?.riskStatus === 'normal'
                    ? styles.linkageNormal
                    : styles.linkageUnavailable
            }`}>
              {data?.linkageRisk?.riskStatus === 'warning'
                ? 'P2 警惕'
                : data?.linkageRisk?.riskStatus === 'watch'
                  ? 'P3 观察'
                  : data?.linkageRisk?.riskStatus === 'normal'
                    ? '未触发'
                    : '暂无判断'}
            </span>
          </div>
          <p className={styles.linkageReason}>
            {data?.linkageRisk?.reason || '真实板块或海外映射数据尚未完成，暂无判断'}
          </p>
          <div className={styles.linkageSources}>
            <span title={data?.linkageRisk?.sectorRisk?.reason}>
              板块：{data?.linkageRisk?.sectorRisk?.label || '暂无判断'}
            </span>
            <span title={data?.linkageRisk?.overseasRisk?.reason}>
              海外：{data?.linkageRisk?.overseasRisk?.label || '暂无判断'}
            </span>
          </div>
          <p className={styles.linkageFootnote}>
            海外标的仅在公司业务精确映射后参与；同属科技股不会自动关联。
          </p>
        </div>

        {/* 相关政策 Accordion */}
        <div className={styles.accordionGroup}>
          <button 
            className={`${styles.accordionHeader} ${policiesOpen ? styles.headerActive : ''}`}
            onClick={() => setPoliciesOpen(!policiesOpen)}
          >
            <div className={styles.headerLabelArea}>
              <FileText size={16} className={styles.headerIcon} />
              <span>相关政策</span>
            </div>
            <div className={styles.headerRightArea}>
              {renderSummary(data?.policies, data?.policySummary || '暂无数据')}
              <ChevronDown 
                size={16} 
                className={`${styles.chevron} ${policiesOpen ? styles.chevronRotate : ''}`} 
              />
            </div>
          </button>
          {policiesOpen && (
            <div className={styles.accordionContent}>
              {renderList(data?.policies)}
            </div>
          )}
        </div>

        {/* 上下游动态 Accordion */}
        <div className={styles.accordionGroup}>
          <button 
            className={`${styles.accordionHeader} ${dynamicsOpen ? styles.headerActive : ''}`}
            onClick={() => setDynamicsOpen(!dynamicsOpen)}
          >
            <div className={styles.headerLabelArea}>
              <Layers size={16} className={styles.headerIcon} />
              <span>上下游动态</span>
            </div>
            <div className={styles.headerRightArea}>
              {renderSummary(
                data?.upstreamDownstream,
                data?.upstreamStatus ? `${data.upstreamStatus} | ${data.downstreamStatus}` : '暂无数据',
              )}
              <ChevronDown 
                size={16} 
                className={`${styles.chevron} ${dynamicsOpen ? styles.chevronRotate : ''}`} 
              />
            </div>
          </button>
          {dynamicsOpen && (
            <div className={styles.accordionContent}>
              {renderList(data?.upstreamDownstream)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
