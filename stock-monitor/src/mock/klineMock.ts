import { KlineItem } from '@/types/stock';

// 简单生成60天的模拟K线数据
export const generateMockKlines = (): KlineItem[] => {
  const data: KlineItem[] = [];
  let basePrice = 14.5;
  const now = new Date('2024-05-23');
  
  for (let i = 59; i >= 0; i--) {
    const date = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
    // 跳过周末
    if (date.getDay() === 0 || date.getDay() === 6) continue;
    
    const open = basePrice + (Math.random() - 0.5) * 0.5;
    const close = open + (Math.random() - 0.45) * 0.8; // 微微向上的趋势
    const high = Math.max(open, close) + Math.random() * 0.3;
    const low = Math.min(open, close) - Math.random() * 0.3;
    const volume = Math.floor(Math.random() * 10000000 + 5000000);
    
    data.push({
      date: date.toISOString().split('T')[0],
      open: Number(open.toFixed(2)),
      close: Number(close.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      volume,
    });
    
    basePrice = close;
  }
  
  // 计算均线
  return data.map((item, index, arr) => {
    let ma5, ma10, ma20;
    
    if (index >= 4) {
      const sum5 = arr.slice(index - 4, index + 1).reduce((sum, curr) => sum + curr.close, 0);
      ma5 = Number((sum5 / 5).toFixed(2));
    }
    if (index >= 9) {
      const sum10 = arr.slice(index - 9, index + 1).reduce((sum, curr) => sum + curr.close, 0);
      ma10 = Number((sum10 / 10).toFixed(2));
    }
    if (index >= 19) {
      const sum20 = arr.slice(index - 19, index + 1).reduce((sum, curr) => sum + curr.close, 0);
      ma20 = Number((sum20 / 20).toFixed(2));
    }
    
    return { ...item, ma5, ma10, ma20 };
  });
};

export const mockKlines = generateMockKlines();
