import { useState } from 'react';
import { IndustryMonitor, DynamicsItem } from '@/types/industry';
import { BarChart3, ChevronDown, FileText, Layers, ExternalLink } from 'lucide-react';
import styles from './IndustryMonitorCard.module.css';

interface Props {
  data: IndustryMonitor;
  loading?: boolean;
}

export default function IndustryMonitorCard({ data, loading }: Props) {
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

  const renderList = (items?: DynamicsItem[]) => {
    if (!items || items.length === 0) {
      return <div className={styles.emptyList}>暂无最新动态数据</div>;
    }
    return (
      <div className={styles.dynamicsList}>
        {items.map((item, index) => {
          return (
            <div key={index} className={styles.dynamicsItem}>
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
                <span className={styles.itemTime}>{item.time}</span>
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
            <span>AI 分析中...</span>
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
        <div className={styles.industryName}>所属行业：{data?.industryName || '-'}</div>
      </div>

      <div className={styles.content}>
        <div className={styles.row}>
          <div className={styles.label}>行业热度</div>
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
          <div className={`${styles.value} ${(data?.fundFlow || '').includes('流入') ? 'text-rise' : ((data?.fundFlow || '').includes('流出') ? 'text-fall' : '')}`}>
            {data?.fundFlow || '-'}
          </div>
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
              <span className={styles.summaryText}>{data?.policySummary || '暂无数据'}</span>
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
              <span className={styles.summaryText}>
                {data?.upstreamStatus ? `${data.upstreamStatus} | ${data.downstreamStatus}` : '暂无数据'}
              </span>
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
