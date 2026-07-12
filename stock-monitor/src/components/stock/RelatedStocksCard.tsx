import { RelatedStock } from '@/types/stock';
import { Share2, Plus, Check } from 'lucide-react';
import { useWatchlist } from '@/hooks/useWatchlist';
import styles from './RelatedStocksCard.module.css';

interface Props {
  data: RelatedStock[];
  onStockClick?: (code: string) => void;
}

export default function RelatedStocksCard({ data, onStockClick }: Props) {
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
        <Share2 className={styles.icon} size={20} />
        <h2 className={styles.title}>相关股票</h2>
      </div>

      <div className={styles.content}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>名称</th>
              <th className={styles.rightAlign}>最新价</th>
              <th className={styles.rightAlign}>涨跌幅</th>
              <th className={styles.rightAlign}>主力流向</th>
              <th style={{ width: 40 }}></th>
            </tr>
          </thead>
          <tbody>
            {data.map(stock => {
              const watched = isInWatchlist(stock.stockCode);
              return (
                <tr 
                  key={stock.stockCode} 
                  className={styles.row}
                  onClick={() => onStockClick && onStockClick(stock.stockCode)}
                  style={{ cursor: onStockClick ? 'pointer' : 'default' }}
                >
                  <td>
                    <div className={styles.stockName}>{stock.stockName}</div>
                    <div className={styles.stockCode}>{stock.stockCode}</div>
                  </td>
                  <td className={`${styles.rightAlign} ${styles.price}`}>
                    {stock.latestPrice == null ? '暂无数据' : stock.latestPrice.toFixed(2)}
                  </td>
                  <td className={`${styles.rightAlign} ${styles.percent} ${stock.changePercent != null && stock.changePercent > 0 ? 'text-rise' : stock.changePercent != null && stock.changePercent < 0 ? 'text-fall' : ''}`}>
                    {stock.changePercent == null ? '暂无数据' : `${stock.changePercent > 0 ? '+' : ''}${stock.changePercent.toFixed(2)}%`}
                  </td>
                  <td className={`${styles.rightAlign} ${stock.fundFlow?.includes('流入') ? 'text-rise' : stock.fundFlow?.includes('流出') ? 'text-fall' : ''}`}>
                    {stock.fundFlow || '-'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
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
                      {watched ? <Check size={16} /> : <Plus size={16} />}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
