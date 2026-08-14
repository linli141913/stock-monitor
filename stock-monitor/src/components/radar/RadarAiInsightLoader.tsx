'use client';

import { useEffect, useState } from 'react';

import type {
  RadarAiHealthResponse,
  RadarAiResponse,
  RadarAiScopeType,
} from '@/types/radar-ai';
import RadarAiInsight from './RadarAiInsight';

export interface RadarAiTarget {
  scopeType: RadarAiScopeType;
  scopeId: string;
  path: string;
  title: string;
}

interface RadarAiInsightLoaderProps {
  target: RadarAiTarget | null;
}

export default function RadarAiInsightLoader({ target }: RadarAiInsightLoaderProps) {
  const [data, setData] = useState<RadarAiResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(target));
  const [error, setError] = useState(
    target ? '' : '当前没有达到正式门槛的分析对象，未调用模型。',
  );
  const [manualAvailable, setManualAvailable] = useState(false);
  const [manualRunning, setManualRunning] = useState(false);

  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    const expectedScope = `${target.scopeType}:${target.scopeId}`;
    void (async () => {
      try {
        const healthRequest = fetch(
          `/api/backend/api/radar/ai/health?_t=${Date.now()}`,
          { cache: 'no-store', signal: controller.signal },
        ).then(async (response) => (
          response.ok ? await response.json() as RadarAiHealthResponse : null
        )).catch(() => null);
        const response = await fetch(
          `/api/backend/api/radar/ai/${target.path}?_t=${Date.now()}`,
          { cache: 'no-store', signal: controller.signal },
        );
        if (!response.ok) throw new Error(`雷达AI状态请求失败 (${response.status})`);
        const payload = await response.json() as RadarAiResponse;
        if (payload.schemaVersion !== 'radar-ai-v1') {
          throw new Error('雷达AI数据契约不匹配');
        }
        if (`${payload.scopeType}:${payload.scopeId}` !== expectedScope) {
          throw new Error('雷达AI对象身份不匹配');
        }
        setData(payload);
        const health = await healthRequest;
        setManualAvailable(Boolean(
          health?.schemaVersion === 'radar-ai-health-v1'
          && health.status === 'ready'
          && health.manualEnabled,
        ));
      } catch (requestError) {
        if (controller.signal.aborted) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : '雷达AI状态暂不可用',
        );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [target]);

  const runManual = async () => {
    if (!target || manualRunning) return;
    setManualRunning(true);
    try {
      const response = await fetch('/api/backend/api/radar/ai/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scopeType: target.scopeType,
          scopeId: target.scopeId,
        }),
        cache: 'no-store',
      });
      const payload = await response.json() as RadarAiResponse & { detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || `雷达AI手动核对失败 (${response.status})`);
      }
      if (
        payload.schemaVersion !== 'radar-ai-v1'
        || payload.scopeType !== target.scopeType
        || payload.scopeId !== target.scopeId
      ) {
        throw new Error('雷达AI手动核对结果与当前对象不匹配');
      }
      setData(payload);
      setError('');
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : '雷达AI手动核对暂不可用',
      );
    } finally {
      setManualRunning(false);
    }
  };

  return (
    <RadarAiInsight
      data={data}
      loading={loading}
      error={error}
      title={target?.title || '雷达AI解读'}
      manualAvailable={manualAvailable}
      manualRunning={manualRunning}
      onManual={() => void runManual()}
    />
  );
}
