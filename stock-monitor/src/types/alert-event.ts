export type AlertDirection = 'positive' | 'negative' | 'neutral' | 'uncertain';
export type AlertPriority = 'P1' | 'P2' | 'P3';
export type EvidenceLevel = 'S' | 'A' | 'B' | 'C';
export type DeliveryStatus = 'pending' | 'sent' | 'failed' | 'not_configured';

export interface AlertDelivery {
  id: number;
  alertId: string;
  channel: 'site' | 'email';
  status: DeliveryStatus;
  attemptCount: number;
  error: string | null;
  nextRetryAt: string | null;
  sentAt: string | null;
  updatedAt: string;
}

export interface AlertEvent {
  id: string;
  symbol: string;
  stockName: string;
  eventType: string;
  direction: AlertDirection;
  priority: AlertPriority;
  evidenceLevel: EvidenceLevel;
  title: string;
  summary: string;
  source: string;
  sourceUrl: string | null;
  sourceEventId: string;
  publishedAt: string | null;
  triggeredAt: string;
  isRead: boolean;
  deliveries: AlertDelivery[];
}
