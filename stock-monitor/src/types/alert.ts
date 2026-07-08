export interface AlertRule {
  stockCode: string;
  stockName: string;
  email: string;
  priceChangeAlert: boolean;
  priceChangeThreshold?: number;
  volumeAlert: boolean;
  volumeRatioThreshold?: number;
  announcementAlert: boolean;
  industryAlert: boolean;
  abnormalStockAlert: boolean;
  enabled: boolean;
}
