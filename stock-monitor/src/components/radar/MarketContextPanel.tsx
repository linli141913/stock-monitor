import { AlertTriangle } from 'lucide-react';

import type { RadarMarketModule } from '@/types/radar';
import styles from './Radar.module.css';

interface MarketContextPanelProps {
  module: RadarMarketModule;
  formatTime: (value: string | null | undefined, withSeconds?: boolean) => string;
}

function signedPercent(value: number | null) {
  if (value === null) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export default function MarketContextPanel({
  module,
  formatTime,
}: MarketContextPanelProps) {
  const data = module.data;
  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>市场背景</h2>
          <p>只展示真实广度和指数快照，不宣告正式市场状态。</p>
        </div>
        <span className={styles.neutralBadge}>正式状态未启用</span>
      </div>

      {(module.state === 'stale' || module.state === 'failed') && (
        <div className={module.state === 'failed' ? styles.errorBanner : styles.warningBanner}>
          <AlertTriangle size={16} />
          <span>
            {module.state === 'failed' ? '最近一次读取或来源失败' : '当前快照已超过页面新鲜度门槛'}
            {module.lastSuccess && `，最近成功 ${formatTime(module.lastSuccess.sourceTime, true)}`}
          </span>
        </div>
      )}

      {!data ? (
        <div className={styles.inlineEmpty}>尚无可展示的市场聚合快照</div>
      ) : (
        <>
          <div className={styles.marketBreadth}>
            <div><strong className={styles.rise}>{data.breadth.advancers.toLocaleString('zh-CN')}</strong><span>上涨</span></div>
            <div><strong className={styles.fall}>{data.breadth.decliners.toLocaleString('zh-CN')}</strong><span>下跌</span></div>
            <div><strong>{data.breadth.flat.toLocaleString('zh-CN')}</strong><span>平盘</span></div>
            <div><strong>{data.breadth.unavailable.toLocaleString('zh-CN')}</strong><span>不可用</span></div>
          </div>
          <div className={styles.indexLine}>
            {data.indices.map((index) => (
              <span key={index.indexKey}>
                {index.name}
                <b className={
                  index.changePercent === null
                    ? ''
                    : index.changePercent >= 0
                      ? styles.rise
                      : styles.fall
                }>
                  {signedPercent(index.changePercent)}
                </b>
              </span>
            ))}
          </div>
          {!data.turnover.displayAllowed && (
            <div className={styles.unitNotice}>成交额原始值已保存，但单位尚未验证，本阶段不展示金额。</div>
          )}
        </>
      )}
    </section>
  );
}
