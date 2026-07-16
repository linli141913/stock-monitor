import { useWatchlist } from '@/hooks/useWatchlist';
import { StockOverview } from '@/types/stock';
import styles from './StockOverviewCard.module.css';

interface Props {
  data: StockOverview;
  lastRefresh?: Date | null;
  statusMessage?: string;
  onRefresh?: () => void;
  onWatchlistToggle?: () => void;
}

const TURNOVER_LABELS = {
  normal: '正常',
  active: '活跃',
  warning: '警惕',
  insufficient: '样本不足',
  unavailable: '暂无判断',
} as const;

export default function StockOverviewCard({ data, lastRefresh, statusMessage, onRefresh, onWatchlistToggle }: Props) {
  const isRise = data.changeAmount != null && data.changeAmount > 0;
  const isFall = data.changeAmount != null && data.changeAmount < 0;
  const priceColorClass = isRise ? 'text-rise' : isFall ? 'text-fall' : '';
  const formatNumber = (value: number | null, suffix = '') => value == null ? '暂无数据' : `${value.toFixed(2)}${suffix}`;
  const getTrendClass = (value: number | null, base: number | null) => {
    if (value == null || base == null) return '';
    return value > base ? 'text-rise' : value < base ? 'text-fall' : '';
  };
  const formatTimestamp = (value: string | null) => {
    if (!value) return '暂无数据';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false });
  };
  const monitoringLabel = data.monitoringStatus === 'active'
    ? '后台监测已生效'
    : data.monitoringStatus === 'inactive'
      ? '后台监测未启用'
      : '监测状态未知';
  const monitoringClass = data.monitoringStatus === 'active'
    ? styles.monitoringActive
    : data.monitoringStatus === 'inactive'
      ? styles.monitoringInactive
      : styles.monitoringUnknown;
  const turnoverRisk = data.turnoverRisk || data.risk?.turnoverRisk;
  const turnoverStatus = turnoverRisk?.status || 'unavailable';
  const turnoverLabel = TURNOVER_LABELS[turnoverStatus];
  const turnoverClass = turnoverStatus === 'normal'
    ? styles.turnoverNormal
    : turnoverStatus === 'active'
      ? styles.turnoverActive
      : turnoverStatus === 'warning'
        ? styles.turnoverWarning
        : styles.turnoverUnavailable;
  
  const { isInWatchlist, addToWatchlist, removeFromWatchlist } = useWatchlist();
  const watched = isInWatchlist(data.stockCode);

  const toggleWatchlist = () => {
    const action = watched 
      ? removeFromWatchlist(data.stockCode) 
      : addToWatchlist(data.stockCode, data.stockName);
      
    action.then(() => {
      if (onWatchlistToggle) {
        onWatchlistToggle();
      }
    });
  };

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleArea}>
          <h1 className={styles.name}>{data.stockName}</h1>
          <span className={styles.code}>{data.stockCode}</span>
          <span className={styles.tag}>腾讯财经准实时</span>
          <span
            className={`${styles.monitoringBadge} ${monitoringClass}`}
            title={data.monitoringError || monitoringLabel}
          >
            {monitoringLabel}
          </span>
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
              页面刷新 {lastRefresh.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          {statusMessage && (
            <span
              title="当前显示上次成功数据"
              style={{ fontSize: '12px', color: '#b45309', fontWeight: 600 }}
            >
              {statusMessage}
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
            {formatNumber(data.latestPrice)}
          </div>
          <div className={styles.changeArea}>
            <div className={`${styles.changeAmount} ${priceColorClass}`}>
              {data.changeAmount != null && data.changeAmount > 0 ? '+' : ''}{formatNumber(data.changeAmount)}
            </div>
            <div className={`${styles.changePercent} ${priceColorClass}`}>
              {data.changePercent != null && data.changePercent > 0 ? '+' : ''}{formatNumber(data.changePercent, '%')}
            </div>
          </div>
          <div className={styles.updateTime}>
            状态：{data.marketStatus || '状态未知'}
            &nbsp;|&nbsp; 数据源时间：{data.sourceTime || '暂无数据'}
            &nbsp;|&nbsp; 后端抓取：{formatTimestamp(data.fetchedAt)}
          </div>
        </div>

        <div className={styles.metricsGrid}>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>今开</span>
            <span className={`${styles.metricValue} ${getTrendClass(data.openPrice, data.previousClose)}`}>
              {formatNumber(data.openPrice)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>最高</span>
            <span className={`${styles.metricValue} ${getTrendClass(data.highPrice, data.previousClose)}`}>
              {formatNumber(data.highPrice)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>最低</span>
            <span className={`${styles.metricValue} ${getTrendClass(data.lowPrice, data.previousClose)}`}>
              {formatNumber(data.lowPrice)}
            </span>
          </div>
          <div className={styles.metricItem}>
            <span className={styles.metricLabel}>昨收</span>
            <span className={styles.metricValue}>{formatNumber(data.previousClose)}</span>
          </div>
        </div>
      </div>

      <div className={styles.bottomMetrics}>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>成交量</span>
          <span className={styles.metricValueBold}>{data.volume || '暂无数据'}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>成交额</span>
          <span className={styles.metricValueBold}>{data.turnoverAmount || '暂无数据'}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>换手率(动)</span>
          <span className={styles.turnoverValueRow}>
            <span className={styles.metricValueBold}>{formatNumber(data.turnoverRate, '%')}</span>
            <span
              className={`${styles.turnoverBadge} ${turnoverClass}`}
              title={turnoverRisk?.reason || '当前暂无可验证的换手率历史基线'}
            >
              {turnoverLabel}
            </span>
          </span>
          {turnoverStatus === 'insufficient' && turnoverRisk?.reason && (
            <span className={styles.turnoverReason}>{turnoverRisk.reason}</span>
          )}
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>总市值</span>
          <span className={styles.metricValueBold}>{data.marketCap || '暂无数据'}</span>
        </div>
        <div className={styles.bottomMetricItem}>
          <span className={styles.metricLabel}>个股主力净额</span>
          <span className={`${styles.metricValueBold} ${data.fundFlow?.includes('流入') ? 'text-rise' : data.fundFlow?.includes('流出') ? 'text-fall' : ''}`}>
            {data.fundFlow || '暂无数据'}
          </span>
          {data.fundFlowSource && (
            <span className={styles.turnoverReason}>
              {data.fundFlowSource}
              {data.fundFlowFetchedAt ? ` · ${formatTimestamp(data.fundFlowFetchedAt)}抓取` : ''}
            </span>
          )}
          {data.fundFlowTimeScope && (
            <span className={styles.turnoverReason}>{data.fundFlowTimeScope}</span>
          )}
          {data.fundFlowComparisonNote && (
            <span className={styles.turnoverReason}>{data.fundFlowComparisonNote}</span>
          )}
        </div>
      </div>
    </div>
  );
}
