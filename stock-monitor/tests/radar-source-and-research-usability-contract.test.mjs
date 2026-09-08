import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const industryPage = readFileSync(
  new URL('../src/app/industry/page.tsx', import.meta.url),
  'utf8',
);
const radarPage = readFileSync(
  new URL('../src/app/radar/page.tsx', import.meta.url),
  'utf8',
);
const leaderPanel = readFileSync(
  new URL('../src/components/radar/LeaderObservationPanel.tsx', import.meta.url),
  'utf8',
);
const etfPanel = readFileSync(
  new URL('../src/components/radar/EtfObservationPanel.tsx', import.meta.url),
  'utf8',
);

assert.match(industryPage, /cache:\s*'no-store'/);
assert.match(industryPage, />\s*重新读取\s*</);
assert.match(industryPage, /fetchNews\(activeTab, false\)/);
assert.match(industryPage, /role="alert"/);
assert.match(industryPage, /来源可用；当前分类下暂无权威资讯/);
assert.match(industryPage, /未将空结果当作“暂无资讯”/);

assert.match(radarPage, /replays\/latest\/etfs/);
assert.match(radarPage, /radar-replay-etf-research-v1/);
assert.match(leaderPanel, /真实来源概况/);
assert.match(leaderPanel, /module\.sourceSummary/);
assert.match(etfPanel, /逐产品研究分流/);
assert.match(etfPanel, /researchState/);

console.log('radar source and ETF research usability contract: ok');
