import { AbnormalStock } from '@/types/stock';
import { Activity, Plus, Check } from 'lucide-react';
import { useWatchlist } from '@/hooks/useWatchlist';
import styles from './AbnormalStocksCard.module.css';

interface Props {
  data: AbnormalStock[];
  onStockClick?: (code: string) => void;
}

export default function AbnormalStocksCard({ data, onStockClick }: Props) {
  const { isInWatchlist, addToWatchlist, removeFromWatchlist } = useWatchlist();

  const handleToggleWatchlist = (e: React.MouseEvent, stockCode: string, stockName: string) => {
    e.stopPropagation();
    if (isInWatchlist(stockCode)) {
      removeFromWatchlist(stockCode);
    } else {
      addToWatchlist(stockCode, stockName);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <Activity className={styles.icon} size={20} />
        <h2 className={styles.title}>半导体异动</h2>
      </div>

      <div className={styles.content}>
        <div className={styles.list}>
          {data.map(stock => {
            const watched = isInWatchlist(stock.stockCode);
            return (
              <div 
                key={stock.stockCode} 
                className={styles.item}
                onClick={() => onStockClick && onStockClick(stock.stockCode)}
                style={{ cursor: onStockClick ? 'pointer' : 'default' }}
              >
                <div className={styles.itemHeader}>
                  <div className={styles.stockInfo}>
                    <span className={styles.name}>{stock.stockName}</span>
                    <span className={styles.code}>{stock.stockCode}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div className={`${styles.change} ${stock.oneDayChange > 0 ? 'text-rise' : stock.oneDayChange < 0 ? 'text-fall' : ''}`}>
                      {stock.oneDayChange > 0 ? '+' : ''}{stock.oneDayChange.toFixed(2)}%
                    </div>
                    <button
                      onClick={(e) => handleToggleWatchlist(e, stock.stockCode, stock.stockName)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        color: watched ? '#10b981' : '#9ca3af',
                        padding: '4px'
                      }}
                      title={watched ? '取消监测' : '加入监测'}
                    >
                      {watched ? <Check size={18} /> : <Plus size={18} />}
                    </button>
                  </div>
                </div>
              <div className={styles.reason}>
                <span className={styles.reasonLabel}>异动原因：</span>
                {stock.reason || '暂无可验证归因'}
              </div>
              <div className={styles.meta}>
                <span>量比: <strong className="text-rise">{stock.volumeRatio ?? '暂无数据'}</strong></span>
                {stock.twentyDayChange != null && (
                  <span>20日: <strong className={stock.twentyDayChange > 0 ? 'text-rise' : 'text-fall'}>{stock.twentyDayChange > 0 ? '+' : ''}{stock.twentyDayChange}%</strong></span>
                )}
                {stock.fundFlow && (
                  <span className={stock.fundFlow.includes('流入') ? 'text-rise' : (stock.fundFlow.includes('流出') ? 'text-fall' : '')}>
                    {stock.fundFlow}
                  </span>
                )}
              </div>
              {stock.riskNote && (
                <div className={styles.riskNote}>
                  ⚠️ {stock.riskNote}
                </div>
              )}
            </div>
          );
        })}
        </div>
        <div className={styles.footerNote}>
          * 异动观察仅供参考，不构成买卖建议
        </div>
      </div>
    </div>
  );
}
