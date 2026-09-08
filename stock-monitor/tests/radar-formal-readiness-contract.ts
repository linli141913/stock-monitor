import type {
  RadarFormalReadinessGate,
  RadarFormalReadinessModule,
  RadarFormalReadinessResponse,
} from '@/types/radar';

const ALL_GATES = [
  'stage9_quality',
  'rule_version',
  'calibration',
  'shadow_ledger',
  'data_quality',
  'performance',
  'security',
  'rollback',
  'runbook',
] as const;

function gates(state: RadarFormalReadinessGate['state']): RadarFormalReadinessGate[] {
  return ALL_GATES.map((gate) => ({
    gate,
    state,
    required: true,
    reasonCodes: state === 'ready' ? [] : [`${gate}_${state}`],
  }));
}

function moduleFixture(
  module: RadarFormalReadinessModule['module'],
  state: RadarFormalReadinessModule['state'],
  overrides: Partial<RadarFormalReadinessModule> = {},
) {
  const requiredTradingDays = module === 'etfObservation' ? 5 : 20;
  return {
    module,
    state,
    requested: false,
    configuredEnabled: false,
    formalEnabled: false,
    observedTradingDays: state === 'collecting' ? 1 : requiredTradingDays,
    requiredTradingDays,
    lastObservedTradingDate: '2026-09-04',
    gates: gates(state === 'failed' ? 'failed' : state === 'collecting' ? 'collecting' : state === 'not_ready' ? 'not_ready' : 'ready'),
    reasonCodes: state === 'ready_to_enable' || state === 'formal_enabled'
      ? []
      : [`${module}_${state}`],
    ...overrides,
  } satisfies RadarFormalReadinessModule;
}

const collecting = {
  contractVersion: 'radar-formal-readiness-v1',
  freshnessPolicy: null,
  checkedAt: '2026-09-04T08:00:00Z',
  state: 'collecting',
  anyFormalEnabled: false,
  allModulesFormalEnabled: false,
  stage9ReplayRunId: null,
  stage9QualitySha256: null,
  stage9QualityState: 'collecting',
  modules: [
    moduleFixture('trendRotation', 'collecting'),
    moduleFixture('etfObservation', 'collecting'),
    moduleFixture('leaderObservation', 'collecting'),
  ],
  evidence: [],
  reasonCodes: ['shadow_observation_collecting'],
} satisfies RadarFormalReadinessResponse;

const readyToEnable = {
  ...collecting,
  freshnessPolicy: {
    policyVersion: 'radar-formal-freshness-policy-v1',
    reportMaxAgeSeconds: 300,
    evidenceMaxAgeSeconds: 600,
    operationalChecksMaxAgeSeconds: 120,
  },
  state: 'ready_to_enable',
  stage9ReplayRunId: 'stage9-replay-20260904',
  stage9QualitySha256: 'a'.repeat(64),
  stage9QualityState: 'ready',
  modules: [
    moduleFixture('trendRotation', 'ready_to_enable'),
    moduleFixture('etfObservation', 'ready_to_enable'),
    moduleFixture('leaderObservation', 'ready_to_enable'),
  ],
  evidence: [{
    evidenceType: 'stage9_quality',
    contractVersion: 'radar-replay-quality-v2',
    contentSha256: 'a'.repeat(64),
    subjectId: 'stage9-replay-20260904',
    generatedAt: '2026-09-04T08:00:00Z',
    sourceTime: '2026-09-04T07:58:00Z',
    fetchedAt: '2026-09-04T07:59:00Z',
  }, {
    evidenceType: 'formal_operational_checks',
    contractVersion: 'radar-formal-operational-checks-v1',
    contentSha256: 'b'.repeat(64),
    subjectId: 'stage9-replay-20260904',
    generatedAt: '2026-09-04T08:00:00Z',
    sourceTime: '2026-09-04T08:00:00Z',
    fetchedAt: '2026-09-04T08:00:00Z',
  }],
  reasonCodes: [],
} satisfies RadarFormalReadinessResponse;

const oneModuleFormalEnabled = {
  ...readyToEnable,
  state: 'formal_enabled',
  anyFormalEnabled: true,
  allModulesFormalEnabled: false,
  modules: [
    moduleFixture('trendRotation', 'formal_enabled', {
      requested: true,
      configuredEnabled: true,
      formalEnabled: true,
    }),
    moduleFixture('etfObservation', 'ready_to_enable'),
    moduleFixture('leaderObservation', 'ready_to_enable'),
  ],
} satisfies RadarFormalReadinessResponse;

const failed = {
  ...collecting,
  state: 'failed',
  stage9QualityState: 'failed',
  modules: [
    moduleFixture('trendRotation', 'failed'),
    moduleFixture('etfObservation', 'failed'),
    moduleFixture('leaderObservation', 'failed'),
  ],
  reasonCodes: ['formal_readiness_store_unverified'],
} satisfies RadarFormalReadinessResponse;

void [collecting, readyToEnable, oneModuleFormalEnabled, failed];
