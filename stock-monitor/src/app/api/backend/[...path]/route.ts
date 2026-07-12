import { NextResponse } from 'next/server';

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

async function proxyRequest(request: Request, context: RouteContext) {
  const backendUrl = process.env.BACKEND_URL
    || process.env.NEXT_PUBLIC_API_BASE
    || (process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8001' : undefined);
  if (!backendUrl) {
    return NextResponse.json(
      { detail: '后端服务地址尚未配置' },
      { status: 503 },
    );
  }

  const { path } = await context.params;
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(`/${path.join('/')}`, backendUrl);
  targetUrl.search = incomingUrl.search;

  const headers = new Headers();
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('Content-Type', contentType);
  headers.set('ngrok-skip-browser-warning', 'true');

  const backendToken = process.env.BACKEND_API_TOKEN;
  if (backendToken) headers.set('X-Backend-Token', backendToken);

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD'
        ? undefined
        : await request.arrayBuffer(),
      cache: 'no-store',
    });

    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('content-type') || 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return NextResponse.json(
      { detail: '后端服务暂时不可用' },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
