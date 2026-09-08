import assert from 'node:assert/strict';
import test from 'node:test';

import { selectFormalShadowObservation } from '../src/lib/formal-shadow-progress.ts';


const readinessModule = {
  module: 'leaderObservation',
  state: 'not_ready',
  requested: false,
  configuredEnabled: false,
  formalEnabled: false,
  observedTradingDays: 0,
  requiredTradingDays: 20,
  lastObservedTradingDate: null,
  gates: [],
  reasonCodes: ['formal_readiness_report_missing'],
};

test('verified shadow progress wins over the missing readiness placeholder', () => {
  const selected = selectFormalShadowObservation(readinessModule, {
    contractVersion: 'radar-formal-shadow-progress-v1',
    checkedAt: '2026-09-08T08:00:00Z',
    state: 'available',
    modules: [{
      module: 'leaderObservation',
      observedTradingDays: 1,
      requiredTradingDays: 20,
      latestReadyStreak: 1,
      latestReadyTradingDate: '2026-09-08',
    }],
    ledgerSha256: 'a'.repeat(64),
    reasonCodes: [],
  });

  assert.deepEqual(selected, {
    source: 'shadow_ledger',
    evidenceLoaded: true,
    observedTradingDays: 1,
    requiredTradingDays: 20,
    latestReadyStreak: 1,
    latestReadyTradingDate: '2026-09-08',
    value: '1/20',
    percent: 5,
  });
});

test('missing shadow ledger does not turn a readiness placeholder into real zero', () => {
  const selected = selectFormalShadowObservation(readinessModule, {
    contractVersion: 'radar-formal-shadow-progress-v1',
    checkedAt: '2026-09-08T08:00:00Z',
    state: 'missing',
    modules: [],
    ledgerSha256: null,
    reasonCodes: ['formal_shadow_progress_store_unconfigured'],
  });

  assert.equal(selected.evidenceLoaded, false);
  assert.equal(selected.value, '未载入 / 20');
  assert.equal(selected.observedTradingDays, null);
  assert.equal(selected.source, 'unavailable');
});

test('a verified formal readiness report remains a safe fallback', () => {
  const selected = selectFormalShadowObservation(
    {
      ...readinessModule,
      observedTradingDays: 4,
      lastObservedTradingDate: '2026-09-07',
      reasonCodes: ['shadow_observation_collecting'],
    },
    null,
  );

  assert.equal(selected.source, 'formal_readiness');
  assert.equal(selected.value, '4/20');
  assert.equal(selected.percent, 20);
});
