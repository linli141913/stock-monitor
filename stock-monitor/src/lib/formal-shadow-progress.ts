import type {
  RadarFormalReadinessModule,
  RadarFormalShadowProgressResponse,
} from '@/types/radar';


const READINESS_PLACEHOLDER_REASONS = new Set([
  'formal_readiness_report_missing',
  'formal_readiness_store_unverified',
  'formal_readiness_report_unverified',
  'formal_readiness_checked_at_future',
  'formal_readiness_report_expired',
  'formal_clock_unverified',
]);

export interface SelectedFormalShadowObservation {
  source: 'shadow_ledger' | 'formal_readiness' | 'unavailable';
  evidenceLoaded: boolean;
  observedTradingDays: number | null;
  requiredTradingDays: number;
  latestReadyStreak: number | null;
  latestReadyTradingDate: string | null;
  value: string;
  percent: number;
}

function percentage(observed: number, required: number) {
  if (required <= 0) return 0;
  return Math.min(100, Math.round((observed / required) * 100));
}

function validProgressNumber(value: number) {
  return Number.isInteger(value) && value >= 0;
}

export function selectFormalShadowObservation(
  readiness: RadarFormalReadinessModule,
  progress: RadarFormalShadowProgressResponse | null,
): SelectedFormalShadowObservation {
  if (progress?.state === 'available') {
    const item = progress.modules.find((candidate) => (
      candidate.module === readiness.module
    ));
    if (
      item
      && validProgressNumber(item.observedTradingDays)
      && validProgressNumber(item.latestReadyStreak)
      && item.latestReadyStreak <= item.observedTradingDays
      && item.requiredTradingDays === readiness.requiredTradingDays
    ) {
      return {
        source: 'shadow_ledger',
        evidenceLoaded: true,
        observedTradingDays: item.observedTradingDays,
        requiredTradingDays: item.requiredTradingDays,
        latestReadyStreak: item.latestReadyStreak,
        latestReadyTradingDate: item.latestReadyTradingDate,
        value: `${item.observedTradingDays}/${item.requiredTradingDays}`,
        percent: percentage(item.observedTradingDays, item.requiredTradingDays),
      };
    }
  }

  const readinessIsPlaceholder = readiness.reasonCodes.some((reason) => (
    READINESS_PLACEHOLDER_REASONS.has(reason)
  ));
  if (!readinessIsPlaceholder) {
    return {
      source: 'formal_readiness',
      evidenceLoaded: true,
      observedTradingDays: readiness.observedTradingDays,
      requiredTradingDays: readiness.requiredTradingDays,
      latestReadyStreak: null,
      latestReadyTradingDate: readiness.lastObservedTradingDate,
      value: `${readiness.observedTradingDays}/${readiness.requiredTradingDays}`,
      percent: percentage(
        readiness.observedTradingDays,
        readiness.requiredTradingDays,
      ),
    };
  }

  return {
    source: 'unavailable',
    evidenceLoaded: false,
    observedTradingDays: null,
    requiredTradingDays: readiness.requiredTradingDays,
    latestReadyStreak: null,
    latestReadyTradingDate: null,
    value: `未载入 / ${readiness.requiredTradingDays}`,
    percent: 0,
  };
}
