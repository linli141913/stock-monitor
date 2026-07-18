import { NextResponse } from 'next/server';


type ComponentStatus = 'healthy' | 'degraded' | 'unavailable' | 'unknown';

interface PublicHealthPayload {
  status: ComponentStatus;
  components: {
    vercel: ComponentStatus;
    tunnel: ComponentStatus;
    fastapi: ComponentStatus;
    backgroundTasks: ComponentStatus;
  };
  checkedAt: string;
}

const HEALTH_TIMEOUT_MS = 10_000;

function buildPayload(
  status: ComponentStatus,
  components: PublicHealthPayload['components'],
): PublicHealthPayload {
  return {
    status,
    components,
    checkedAt: new Date().toISOString(),
  };
}

function healthResponse(payload: PublicHealthPayload, statusCode: number) {
  return NextResponse.json(payload, {
    status: statusCode,
    headers: {
      'Cache-Control': 'no-store, max-age=0',
    },
  });
}

function getBackgroundTaskStatus(payload: unknown): ComponentStatus {
  if (!payload || typeof payload !== 'object') return 'unknown';
  const tasks = (payload as { tasks?: unknown }).tasks;
  if (!tasks || typeof tasks !== 'object') return 'unknown';

  const statuses = Object.values(tasks).map((task) => {
    if (!task || typeof task !== 'object') return 'unknown';
    return String((task as { status?: unknown }).status || 'unknown');
  });

  if (statuses.includes('failed')) return 'degraded';
  if (statuses.some((status) => status === 'healthy' || status === 'running')) {
    return 'healthy';
  }
  return 'unknown';
}

export async function GET() {
  const backendUrl = process.env.BACKEND_URL
    || process.env.NEXT_PUBLIC_API_BASE
    || (process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8001' : undefined);

  if (!backendUrl) {
    return healthResponse(buildPayload('unavailable', {
      vercel: 'healthy',
      tunnel: 'unknown',
      fastapi: 'unknown',
      backgroundTasks: 'unknown',
    }), 503);
  }

  const headers = new Headers({
    Accept: 'application/json',
    'ngrok-skip-browser-warning': 'true',
  });
  const backendToken = process.env.BACKEND_API_TOKEN;
  if (backendToken) headers.set('X-Backend-Token', backendToken);

  let response: Response;
  try {
    response = await fetch(new URL('/api/monitoring/health', backendUrl), {
      method: 'GET',
      headers,
      cache: 'no-store',
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
  } catch {
    return healthResponse(buildPayload('unavailable', {
      vercel: 'healthy',
      tunnel: 'unavailable',
      fastapi: 'unknown',
      backgroundTasks: 'unknown',
    }), 503);
  }

  if (!response.ok) {
    return healthResponse(buildPayload('unavailable', {
      vercel: 'healthy',
      tunnel: 'healthy',
      fastapi: 'unavailable',
      backgroundTasks: 'unknown',
    }), 503);
  }

  let backendHealth: unknown;
  try {
    backendHealth = await response.json();
  } catch {
    return healthResponse(buildPayload('degraded', {
      vercel: 'healthy',
      tunnel: 'healthy',
      fastapi: 'degraded',
      backgroundTasks: 'unknown',
    }), 503);
  }

  const backendStatus = backendHealth && typeof backendHealth === 'object'
    ? String((backendHealth as { status?: unknown }).status || 'unknown')
    : 'unknown';
  const fastapi: ComponentStatus = backendStatus === 'healthy'
    ? 'healthy'
    : backendStatus === 'degraded'
      ? 'degraded'
      : 'unknown';
  const backgroundTasks = getBackgroundTaskStatus(backendHealth);
  const healthy = fastapi === 'healthy' && backgroundTasks === 'healthy';

  return healthResponse(buildPayload(healthy ? 'healthy' : 'degraded', {
    vercel: 'healthy',
    tunnel: 'healthy',
    fastapi,
    backgroundTasks,
  }), healthy ? 200 : 503);
}

export const dynamic = 'force-dynamic';
