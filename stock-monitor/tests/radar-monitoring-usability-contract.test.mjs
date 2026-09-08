import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const readSource = (path) => readFileSync(
  resolve(import.meta.dirname, `../${path}`),
  'utf8',
);

const radarPageSource = readSource('src/app/radar/page.tsx');
const leaderPanelSource = readSource('src/components/radar/LeaderObservationPanel.tsx');
const etfPanelSource = readSource('src/components/radar/EtfObservationPanel.tsx');
const stage10PanelSource = readSource('src/components/radar/RadarStage10ReadinessPanel.tsx');
const formalShadowProgressSource = readSource('src/lib/formal-shadow-progress.ts');
const stockPageSource = readSource('src/app/page.tsx');
const watchlistSource = readSource('src/hooks/useWatchlist.ts');
const monitoringUiSource = [
  radarPageSource,
  leaderPanelSource,
  etfPanelSource,
  stage10PanelSource,
].join('\n');

for (const removedUserWorkflow of [
  '/review-version',
  'ManualReviewForm',
  'onReviewPreflight',
  'onReviewSubmit',
  '审核者标识',
  '人工批准',
  '人工审批',
]) {
  assert.ok(
    !monitoringUiSource.includes(removedUserWorkflow),
    `radar monitoring UI must not expose approval workflow: ${removedUserWorkflow}`,
  );
}

for (const requiredReadOnlyFeature of [
  '/leaders/review-queue?',
  '官方公告扫描',
  '加入监测列表',
]) {
  assert.ok(
    monitoringUiSource.includes(requiredReadOnlyFeature),
    `missing read-only monitoring feature: ${requiredReadOnlyFeature}`,
  );
}

assert.ok(
  watchlistSource.includes('if (!response.ok)'),
  'watchlist persistence must reject non-2xx responses',
);
assert.ok(
  watchlistSource.indexOf('await persist(next)') < watchlistSource.indexOf('setWatchlist(next)'),
  'watchlist UI must update only after backend persistence succeeds',
);

assert.ok(
  stockPageSource.includes('void fetchRadarStock(stockCode);'),
  'stock radar state must participate in user-triggered or periodic refresh',
);
assert.ok(
  stockPageSource.includes('radarLeaderReasonLabel(radarStock.leader.firstRejectionReason)'),
  'stock radar rejection reasons must be translated for users',
);

const overviewLoaderSource = radarPageSource.slice(
  radarPageSource.indexOf('const loadOverview ='),
  radarPageSource.indexOf('const loadSectors ='),
);
for (const requestGuard of [
  'const controller = new AbortController();',
  'controller.abort()',
  'signal: controller.signal',
  "error.name === 'AbortError'",
  'window.clearTimeout(timeoutId)',
]) {
  assert.ok(
    overviewLoaderSource.includes(requestGuard),
    `radar overview must stop an indefinitely hanging request: ${requestGuard}`,
  );
}

assert.ok(
  stage10PanelSource.includes('selectFormalShadowObservation(module, shadowProgress)')
    && formalShadowProgressSource.includes("'formal_readiness_report_missing'"),
  'missing readiness reports must be distinguished from a verified zero-day ledger',
);
assert.ok(
  formalShadowProgressSource.includes('`未载入 / ${readiness.requiredTradingDays}`'),
  'missing readiness reports must not render a misleading 0/N observation count',
);
