import type { RadarOverviewResponse } from '@/types/radar';
import styles from './Radar.module.css';

interface RadarDataGatePanelProps {
  overview: RadarOverviewResponse;
  renderedAt: string | null;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
}

function moduleStateLabel(state: string) {
  return {
    available: '可用',
    empty: '空榜',
    stale: '过期',
    failed: '失败',
    not_ready: '未准备',
    not_enabled: '未启用',
  }[state] || state;
}

export default function RadarDataGatePanel({
  overview,
  renderedAt,
  formatTime,
}: RadarDataGatePanelProps) {
  const market = overview.modules.market;
  const sectors = overview.modules.sectors;
  const sources = [...market.sources, ...sectors.sources];
  const abnormalSources = sources.filter((source) => source.status !== 'healthy');
  const abnormalModules = [market.state, sectors.state].filter(
    (state) => state === 'failed' || state === 'stale',
  );
  const abnormalCount = abnormalSources.length + abnormalModules.length;

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>数据与门禁</h2>
          <p>源时间、抓取时间和页面渲染时间分别展示。</p>
        </div>
        <span className={abnormalCount ? styles.alertBadge : styles.goodBadge}>
          {abnormalCount ? `${abnormalCount} 项异常` : '本轮无来源异常'}
        </span>
      </div>
      <div className={styles.gateGrid}>
        <div>
          <span>市场 · {moduleStateLabel(market.state)}</span>
          <strong>源 {formatTime(market.lastSuccess?.sourceTime, true)}</strong>
          <small>抓取 {formatTime(market.lastSuccess?.fetchedAt, true)}</small>
        </div>
        <div>
          <span>行业 · {moduleStateLabel(sectors.state)}</span>
          <strong>{sectors.summary.usableCount} / {sectors.summary.totalCount} 影子可用</strong>
          <small>抓取 {formatTime(sectors.lastSuccess?.fetchedAt, true)}</small>
        </div>
        <div>
          <span>页面渲染</span>
          <strong>{formatTime(renderedAt, true)}</strong>
          <small>只表示浏览器完成本次更新</small>
        </div>
      </div>
    </section>
  );
}
