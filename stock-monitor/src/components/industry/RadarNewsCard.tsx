import React from 'react';
import styles from './RadarNewsCard.module.css';
import { ExternalLink, BrainCircuit } from 'lucide-react';

export interface RadarNews {
  id: string;
  title: string;
  source: string;
  publish_time: string;
  original_link: string;
  credibility_level: string; // S, A, B, C
  region: string; // 国内, 国外
  related_chains: string[];
  related_stocks: string[];
  ai_summary: string;
  ai_impact: string;
  ai_verification_status: string;
}

interface Props {
  news: RadarNews;
}

export default function RadarNewsCard({ news }: Props) {
  
  // Helper to map credibility to CSS class
  const getCredClass = (level: string) => {
    switch (level) {
      case 'S': return styles.credS;
      case 'A': return styles.credA;
      case 'B': return styles.credB;
      default: return styles.credC;
    }
  };

  const isVerified = news.ai_verification_status.includes('✅');

  const getSourceClass = (source: string) => {
    if (source.includes('巨潮')) return styles.sourceCninfo;
    if (source.includes('财联社')) return styles.sourceCls;
    if (source.includes('东财') || source.includes('东方财富') || source.includes('研报')) return styles.sourceEastmoney;
    if (source.includes('新浪')) return styles.sourceSina;
    if (source.includes('报') || source.includes('央视') || source.includes('21世纪') || source.includes('报道')) return styles.sourcePress;
    return styles.sourceDefault;
  };

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleWrapper}>
          <h3 className={styles.title}>
            <span className={`${styles.sourceBadge} ${getSourceClass(news.source)}`}>
              {news.source}
            </span>
            <a href={news.original_link} target="_blank" rel="noopener noreferrer" className={styles.titleLink}>
              {news.title}
            </a>
          </h3>
          <div className={styles.meta}>
            <span className={styles.time}>{news.publish_time}</span>
          </div>
        </div>
        {news.original_link && news.original_link !== '#' && (
          <a href={news.original_link} target="_blank" rel="noopener noreferrer" className={styles.linkIcon}>
            原文链接 <ExternalLink size={14} />
          </a>
        )}
      </div>

      <div className={styles.tagsRow}>
        <span className={`${styles.tag} ${getCredClass(news.credibility_level)}`}>
          {news.credibility_level}级
        </span>
        <span className={`${styles.tag} ${styles.regionTag}`}>
          {news.region}
        </span>
        
        {news.related_chains.map((chain, i) => (
          <span key={`chain-${i}`} className={`${styles.tag} ${styles.chainTag}`}>
            {chain}
          </span>
        ))}
        
        {news.related_stocks.map((stock, i) => (
          <span key={`stock-${i}`} className={`${styles.tag} ${styles.stockTag}`}>
            {stock}
          </span>
        ))}
      </div>

      <div className={styles.aiContainer}>
        <div className={styles.aiHeader}>
          <BrainCircuit size={16} className={styles.aiIcon} />
          <span>AI 交叉验证与解析</span>
          <span className={`${styles.verifyStatus} ${isVerified ? styles.statusVerified : styles.statusWarning}`}>
            {news.ai_verification_status}
          </span>
        </div>
        <div className={styles.aiContent}>
          <p className={styles.summary}>
            <strong>一句话摘要：</strong> {news.ai_summary}
          </p>
          <p className={styles.impact}>
            <strong>可能影响：</strong> {news.ai_impact}
          </p>
        </div>
      </div>
    </div>
  );
}
