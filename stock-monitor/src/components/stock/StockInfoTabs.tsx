'use client';

import { useState } from 'react';
import { CompanyInfo, Announcement, News } from '@/types/stock';
import styles from './StockInfoTabs.module.css';
import { FinancialSummaryTab } from './FinancialSummaryTab';
import AiAttributionTab from './AiAttributionTab';

interface Props {
  stockCode: string;
  companyInfo: CompanyInfo;
  announcements: Announcement[];
  news: News[];
}

type TabType = 'AI归因' | '公司概览' | '财务摘要' | '公告' | '相关新闻';

export default function StockInfoTabs({ stockCode, companyInfo, announcements, news }: Props) {
  const [activeTab, setActiveTab] = useState<TabType>('AI归因');
  // We now always show the Financial Data tab
  const hasNews = news && news.length > 0;

  const tabs: TabType[] = ['AI归因', '公司概览', '财务摘要', '公告'];
  if (hasNews) tabs.push('相关新闻');

  // ensure activeTab is valid if the currently selected tab disappeared
  if (!tabs.includes(activeTab)) {
    setActiveTab('公司概览');
  }

  return (
    <div className={styles.card}>
      <div className={styles.tabList}>
        {tabs.map(tab => (
          <button
            key={tab}
            className={`${styles.tabBtn} ${activeTab === tab ? styles.active : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {activeTab === 'AI归因' && (
          <div className={styles.tabPanel}>
            <AiAttributionTab stockCode={stockCode} />
          </div>
        )}

        {activeTab === '公司概览' && (
          <div className={styles.tabPanel}>
            <div className={styles.infoRow}>
              <span className={styles.label}>主营业务：</span>
              <span className={styles.value}>{companyInfo.mainBusiness || '-'}</span>
            </div>
            {companyInfo.coreProducts && companyInfo.coreProducts.length > 0 && (
              <div className={styles.infoRow}>
                <span className={styles.label}>核心产品：</span>
                <span className={styles.value}>{companyInfo.coreProducts.join('、')}</span>
              </div>
            )}
            <div className={styles.infoRow}>
              <span className={styles.label}>所属行业：</span>
              <span className={styles.value}>{companyInfo.industryTags && companyInfo.industryTags.length > 0 ? companyInfo.industryTags.join(' / ') : '-'}</span>
            </div>
            {companyInfo.industryTags && companyInfo.industryTags.length > 0 && (
              <div className={styles.infoRow}>
                <span className={styles.label}>概念题材：</span>
                <div className={styles.tags}>
                  {companyInfo.industryTags.map(tag => (
                    <span key={tag} className={styles.tag}>{tag}</span>
                  ))}
                </div>
              </div>
            )}
            <div className={styles.infoRow}>
              <span className={styles.label}>公司简介：</span>
              <span className={styles.value} style={{ lineHeight: '1.6', color: '#555' }}>
                {companyInfo.companyDescription || '-'}
              </span>
            </div>
          </div>
        )}

        {activeTab === '财务摘要' && (
          <div className={styles.tabPanel}>
            <FinancialSummaryTab stockCode={stockCode} />
          </div>
        )}

        {activeTab === '公告' && (
          <div className={styles.tabPanel}>
            <ul className={styles.list}>
              {announcements.map(item => (
                <li key={item.id} className={styles.listItem}>
                  <div className={styles.itemHeader}>
                    <a href={item.url} className={styles.itemTitle}>{item.title}</a>
                    <span className={styles.itemDate}>{item.publishTime}</span>
                  </div>
                  <div className={styles.itemSummary}>{item.summary}</div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {activeTab === '相关新闻' && (
          <div className={styles.tabPanel}>
            <ul className={styles.list}>
              {news.map(item => (
                <li key={item.id} className={styles.listItem}>
                  <div className={styles.itemHeader}>
                    <a href={item.url} className={styles.itemTitle}>{item.title}</a>
                    <span className={styles.itemDate}>{item.publishTime}</span>
                  </div>
                  <div className={styles.itemSummary}>{item.summary}</div>
                  <div className={styles.itemMeta}>
                    <span className={styles.itemSource}>{item.source}</span>
                    <span className={`${styles.itemSentiment} ${item.sentiment === '利好' ? 'text-rise' : item.sentiment === '利空' ? 'text-fall' : ''}`}>
                      {item.sentiment}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
