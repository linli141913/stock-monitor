import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';


const ROUTE_PATH = new URL(
  '../src/app/api/health/route.ts',
  import.meta.url,
);

function loadRoute(fetchImpl) {
  const source = readFileSync(ROUTE_PATH, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const loadedModule = { exports: {} };
  const context = {
    AbortSignal,
    Headers,
    URL,
    exports: loadedModule.exports,
    fetch: fetchImpl,
    module: loadedModule,
    process: {
      env: {
        BACKEND_URL: 'http://backend.example',
        NODE_ENV: 'test',
      },
    },
    require(specifier) {
      if (specifier !== 'next/server') {
        throw new Error(`unexpected require: ${specifier}`);
      }
      return {
        NextResponse: {
          json(payload, init = {}) {
            return {
              status: init.status ?? 200,
              headers: init.headers ?? {},
              async json() {
                return payload;
              },
            };
          },
        },
      };
    },
  };
  vm.runInNewContext(compiled, context, {
    filename: ROUTE_PATH.pathname,
  });
  return loadedModule.exports;
}

test('reachable backend stays available when only background data is degraded', async () => {
  const route = loadRoute(async () => ({
    ok: true,
    async json() {
      return {
        status: 'degraded',
        tasks: {
          officialAnnouncements: { status: 'healthy' },
          radarMarketFeatures: { status: 'degraded' },
        },
      };
    },
  }));

  const response = await route.GET();
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.status, 'degraded');
  assert.equal(payload.components.fastapi, 'healthy');
  assert.equal(payload.components.backgroundTasks, 'degraded');
});

test('unreachable backend remains an external availability failure', async () => {
  const route = loadRoute(async () => {
    throw new Error('network unavailable');
  });

  const response = await route.GET();
  const payload = await response.json();

  assert.equal(response.status, 503);
  assert.equal(payload.status, 'unavailable');
  assert.equal(payload.components.tunnel, 'unavailable');
  assert.equal(payload.components.fastapi, 'unknown');
});
