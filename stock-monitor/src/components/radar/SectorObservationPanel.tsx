import type { RadarSectorModule } from '@/types/radar';
import ModuleStatePanel from './ModuleStatePanel';
import styles from './Radar.module.css';

interface SectorObservationPanelProps {
  module: RadarSectorModule;
  full?: boolean;
  onViewAll?: () => void;
}

function signedPercent(value: number | null) {
  if (value === null) return '暂无';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export default function SectorObservationPanel({
  module,
  full = false,
  onViewAll,
}: SectorObservationPanelProps) {
  if (module.state === 'empty') {
    return (
      <ModuleStatePanel
        state="empty"
        title="行业聚合观察"
        description="本轮读取已经完成，真实结果为 0 条。"
      />
    );
  }
  if (module.state === 'not_ready' && module.items.length === 0) {
    return (
      <ModuleStatePanel
        state="not_ready"
        title="行业聚合观察"
        description="等待第一轮完整行业快照。"
      />
    );
  }
  if (module.state === 'failed' && module.items.length === 0) {
    return (
      <ModuleStatePanel
        state="failed"
        title="行业聚合观察"
        description="行业模块失败不会影响市场背景和后续模块的独立状态。"
      />
    );
  }

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>{full ? '行业主线 · 全部观察' : '行业聚合观察'}</h2>
          <p>按当日等权涨跌幅观察排序，不是正式主线评分或投资排名。</p>
        </div>
        {onViewAll && (
          <button className={styles.textButton} onClick={onViewAll}>查看全部行业</button>
        )}
      </div>
      {(module.state === 'stale' || module.state === 'failed') && (
        <div className={module.state === 'failed' ? styles.errorBanner : styles.warningBanner}>
          当前显示最近成功行业快照，状态为
          {module.state === 'failed' ? '来源失败' : '已过期'}。
        </div>
      )}
      <div className={full ? styles.sectorGrid : styles.sectorList}>
        {module.items.map((item) => (
          <article className={styles.sectorCard} key={item.divisionCode}>
            <div className={styles.sectorTitleRow}>
              <div>
                <strong>{item.divisionName}</strong>
                <span>代码 {item.divisionCode} · {item.expectedCount} 只成分</span>
              </div>
              <b className={
                item.equalReturn === null
                  ? styles.muted
                  : item.equalReturn >= 0
                    ? styles.rise
                    : styles.fall
              }>
                {signedPercent(item.equalReturn)}
              </b>
            </div>
            <div className={styles.sectorMetrics}>
              <span>上涨广度 {item.upRatio === null ? '暂无' : `${(item.upRatio * 100).toFixed(1)}%`}</span>
              <span>{item.advancers}涨 / {item.decliners}跌 / {item.flat}平</span>
            </div>
            <div className={styles.sectorFooter}>
              <span className={item.shadowUsable ? styles.usableBadge : styles.partialBadge}>
                {item.shadowUsable ? '影子可用' : '部分字段不可用'}
              </span>
              <span>{item.freshCount} 条行情新鲜</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
