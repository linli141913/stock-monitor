import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const pageSource = readFileSync(
  resolve(import.meta.dirname, '../src/app/radar/page.tsx'),
  'utf8',
);

for (const requiredSnippet of [
  'const historyTabActive = useRef(false);',
  'const mounted = useRef(true);',
  'const replayRequestGeneration = useRef(0);',
  'const formalReadinessRequestGeneration = useRef(0);',
  'const formalShadowProgressRequestGeneration = useRef(0);',
  'const replayAbortController = useRef<AbortController | null>(null);',
  'const formalReadinessAbortController = useRef<AbortController | null>(null);',
  'const formalShadowProgressAbortController = useRef<AbortController | null>(null);',
  'const loadReplayQuality = useCallback(async (force = false) => {',
  'const loadFormalReadiness = useCallback(async (force = false) => {',
  'const loadFormalShadowProgress = useCallback(async (force = false) => {',
  'signal: controller.signal',
  '&& historyTabActive.current',
  '&& requestGeneration === replayRequestGeneration.current',
  '&& requestGeneration === formalReadinessRequestGeneration.current',
  '&& requestGeneration === formalShadowProgressRequestGeneration.current',
  'replayAbortController.current?.abort();',
  'formalReadinessAbortController.current?.abort();',
  'formalShadowProgressAbortController.current?.abort();',
  "error instanceof Error && error.name === 'AbortError'",
  'loadReplayQuality(true),',
  'loadFormalReadiness(true),',
  'loadFormalShadowProgress(true),',
]) {
  assert.ok(pageSource.includes(requiredSnippet), `missing request guard: ${requiredSnippet}`);
}
