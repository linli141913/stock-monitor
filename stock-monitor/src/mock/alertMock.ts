import { AlertRule } from '@/types/alert';

export const mockAlertRule: AlertRule = {
  stockCode: "000021",
  stockName: "深科技",
  email: "you@example.com",
  priceChangeAlert: true,
  priceChangeThreshold: 5,
  volumeAlert: true,
  volumeRatioThreshold: 2,
  announcementAlert: true,
  industryAlert: true,
  abnormalStockAlert: false,
  enabled: true
};
