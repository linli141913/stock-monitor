import type { RadarOverviewResponse } from '@/types/radar';
import styles from './Radar.module.css';

interface RadarStatusStripProps {
  overview: RadarOverviewResponse;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
}

function stateLabel(state: string) {
  return {
    available: '可用',
    empty: '真实空榜',
    stale: '已过期',
    failed: '来源失败',
    not_ready: '未准备',
    not_enabled: '未启用',
  }[state] || state;
}

function stateTone(state: string) {
  if (state === 'failed') return styles.statusDanger;
  if (state === 'stale') return styles.statusWarning;
  if (state === 'available') return styles.statusGood;
  return styles.statusNeutral;
}

export default function RadarStatusStrip({
  overview,
  formatTime,
}: RadarStatusStripProps) {
  const market = overview.modules.market;
  const sectors = overview.modules.sectors;
  const cells = [
    {
      label: '当前交易状态',
      value: overview.marketSession.label,
      tone: overview.marketSession.code === 'trading'
        ? styles.statusGood
        : styles.statusNeutral,
    },
    {
      label: '市场数据',
      value: `${formatTime(market.lastSuccess?.sourceTime)} · ${stateLabel(market.state)}`,
      tone: stateTone(market.state),
    },
    {
      label: '行业数据',
      value: `${formatTime(sectors.lastSuccess?.sourceTime)} · ${stateLabel(sectors.state)}`,
      tone: stateTone(sectors.state),
    },
    {
      label: 'ETF模块',
      value: '阶段5未启用',
      tone: styles.statusNeutral,
    },
    {
      label: '龙头模块',
      value: '阶段6未启用',
      tone: styles.statusNeutral,
    },
    {
      label: '模块时差',
      value: overview.moduleSkewSeconds === null
        ? '尚不可比'
        : `市场/行业 ${overview.moduleSkewSeconds}秒`,
      tone: overview.moduleSkewSeconds !== null && overview.moduleSkewSeconds <= 180
        ? styles.statusAccent
        : styles.statusWarning,
    },
    {
      label: '覆盖与运行',
      value: market.data
        ? `${market.data.breadth.completeness.returnedCount.toLocaleString('zh-CN')} / ${market.data.breadth.completeness.expectedCount.toLocaleString('zh-CN')} · 影子`
        : '尚无市场快照',
      tone: market.data ? styles.statusWarning : styles.statusNeutral,
    },
  ];

  return (
    <section className={styles.statusStrip}>
      {cells.map((cell) => (
        <div className={styles.statusCell} key={cell.label}>
          <span>{cell.label}</span>
          <strong className={cell.tone}>{cell.value}</strong>
        </div>
      ))}
    </section>
  );
}
