import { useWatchlist } from '@/hooks/useWatchlist';
import { StockOverview } from '@/types/stock';
import styles from './StockOverviewCard.module.css';

interface Props {
  data: StockOverview;
  lastRefresh?: Date | null;
  onRefresh?: () => void;
}

export default function StockOverviewCard({ data, lastRefresh, onRefresh }: Props) {
  const isRise = data.changeAmount > 0;
  const isFall = data.changeAmount < 0;
  const priceColorClass = isRise ? 'text-rise' : isFall ? 'text-fall' : '';
  
  const { isInWatchlist, addToWatchlist, removeFromWatchlist } = useWatchlist();
  const watched = isInWatchlist(data.stockCode);

  const toggleWatchlist = () => {
    if (watched) {
      removeFromWatchlist(data.stockCode);
    } else {
      addToWatchlist(data.stockCode, data.stockName);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleArea}>
          <h1 className={styles.name}>{data.stockName}</h1>
          <span className={styles.code}>{data.stockCode}</span>
          <span className={styles.tag}>腾讯财经实时</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={toggleWatchlist}
            style={{ 
              fontSize: '13px', 
              padding: '4px 12px', 
              cursor: 'pointer', 
              borderRadius: '4px', 
              border: `1px solid ${watched ? '#e5e7eb' : '#3b82f6'}`, 
              background: watched ? '#f9fafb' : '#3b82f6',
              color: watched ? '#6b7280' : 'white',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
          >
            {watched ? '✓ 已监测' : '+ 加入监测'}
          </button>
          {lastRefresh && (
            <span style={{ fontSize: '12px', color: '#999', marginLeft: '4px' }}>
              刷新于 {lastRefresh.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          {onRefresh && (
            <button
              onClick={onRefresh}
              style={{ fontSize: '12px', padding: '2px 8px', cursor: 'pointer', borderRadius: '4px', border: '1px solid #ddd', background: '#f5f5f5' }}
            >
              刷新
            </button>
          )}
        </div>
      </div>

      <div className={styles.mainInfo}>
        <div className={styles.priceArea}>
          <div className={`${styles.latestPrice} ${priceColorClass}`}>
            {data.latestPrice.toFixed(2)}
          </div>
          <div className={styles.changeArea}>
            <div className={`${styles.changeAmount} ${priceColorClass}`}>
              {data.changeAmount > 0 ? '+' : ''}{data.changeAmount.toFixed(2)}
            </div>
            <div className={`${styles.changePercent} ${priceColorClass}`}>
              {data.changePercent > 0 ? '+' : ''}{data.changePercent.toFixed(2)}%
            </div>
          </div>
          <div className={styles.updateTime}>
            状态：{data.marketStatus || '未知'} &nbsp;|&nbsp; 数据时间：{data.updateTime}
          </div>
        </div>

        <div className={styles.metricsGrid}>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>今开</span>
            <span className={`${styles.metricValue} ${data.openPrice > data.previousClose ? 'text-rise' : data.openPrice < data.previousClose ? 'text-fall' : ''}`}>
              {data.openPrice.toFixed(2)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>最高</span>
            <span className={`${styles.metricValue} ${data.highPrice > data.previousClose ? 'text-rise' : data.highPrice < data.previousClose ? 'text-fall' : ''}`}>
              {data.highPrice.toFixed(2)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>最低</span>
            <span className={`${styles.metricValue} ${data.lowPrice > data.previousClose ? 'text-rise' : data.lowPrice < data.previousClose ? 'text-fall' : ''}`}>
              {data.lowPrice.toFixed(2)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>昨收</span>
            <span className={styles.metricValue}>{data.previousClose.toFixed(2)}</span>
          </div>
        </div>
      </div>

      <div className={styles.bottomMetrics}>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>成交量</span>
          <span className={styles.metricValueBold}>{data.volume}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>成交额</span>
          <span className={styles.metricValueBold}>{data.turnoverAmount}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>换手率(动)</span>
          <span className={styles.metricValueBold}>{data.turnoverRate.toFixed(2)}%</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>总市值</span>
          <span className={styles.metricValueBold}>{data.marketCap}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>主力流向</span>
          <span className={`${styles.metricValueBold} ${data.fundFlow?.includes('流入') ? 'text-rise' : data.fundFlow?.includes('流出') ? 'text-fall' : ''}`}>
            {data.fundFlow || '-'}
          </span>
        </div>
      </div>
    </div>
  );
}
