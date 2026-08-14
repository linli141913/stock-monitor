import { Bot, CircleAlert, CircleCheck, Clock3 } from 'lucide-react';

import type { RadarAiResponse } from '@/types/radar-ai';
import styles from './Radar.module.css';

interface RadarAiInsightProps {
  data: RadarAiResponse | null;
  loading: boolean;
  error: string;
  title: string;
  manualAvailable?: boolean;
  manualRunning?: boolean;
  onManual?: () => void;
}

const STATUS_COPY = {
  not_run: {
    label: '尚未运行',
    text: '当前没有符合正式状态与冻结证据门槛的雷达AI解读。',
  },
  not_configured: {
    label: '模型未配置',
    text: '雷达规则结果仍可用；独立雷达AI模型尚未配置。',
  },
  evidence_insufficient: {
    label: '证据不足',
    text: '冻结证据、正式状态或覆盖率未满足要求，本次未调用模型。',
  },
  failed: {
    label: '解读失败',
    text: '模型调用或结果校验失败；确定性雷达状态没有改变。',
  },
  success: {
    label: '解读完成',
    text: '以下内容仅解释冻结规则结果，不改变正式状态。',
  },
} as const;

export default function RadarAiInsight({
  data,
  loading,
  error,
  title,
  manualAvailable = false,
  manualRunning = false,
  onManual,
}: RadarAiInsightProps) {
  if (loading) {
    return (
      <section className={styles.aiPanel} aria-live="polite">
        <div className={styles.aiHeader}>
          <Bot size={17} />
          <div><h2>{title}</h2><p>正在读取独立雷达AI状态，不触发新调用。</p></div>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className={styles.aiPanel} aria-live="polite">
        <div className={styles.aiHeader}>
          <CircleAlert size={17} />
          <div><h2>{title}</h2><p>{error}</p></div>
        </div>
      </section>
    );
  }

  const status = data?.analysisStatus || 'not_run';
  const copy = STATUS_COPY[status];
  const output = status === 'success' ? data?.output : null;

  return (
    <section className={styles.aiPanel} aria-live="polite">
      <div className={styles.aiHeader}>
        {status === 'success' ? <CircleCheck size={17} /> : <Clock3 size={17} />}
        <div>
          <h2>{title}</h2>
          <p>{copy.text}</p>
        </div>
        <span className={status === 'success' ? styles.goodBadge : styles.neutralBadge}>
          {copy.label}
        </span>
        {manualAvailable && onManual && (
          <button
            type="button"
            className={styles.aiAction}
            disabled={manualRunning}
            onClick={onManual}
          >
            {manualRunning ? '核对中…' : '手动核对'}
          </button>
        )}
      </div>

      {output && (
        <div className={styles.aiBody}>
          <p className={styles.aiSummary}>{output.plainEnglishSummary}</p>
          <div className={styles.aiColumns}>
            <div>
              <strong>已确认事实</strong>
              {output.confirmedFacts.length ? (
                <ul>{output.confirmedFacts.map((fact) => (
                  <li key={`${fact.text}-${fact.sourceIds.join('-')}`}>{fact.text}</li>
                ))}</ul>
              ) : <span>暂无已确认事实</span>}
            </div>
            <div>
              <strong>推断与未知项</strong>
              {output.inferences.length || output.unknowns.length ? (
                <ul>{[...output.inferences, ...output.unknowns].map((item) => (
                  <li key={item}>{item}</li>
                ))}</ul>
              ) : <span>暂无额外推断或未知项</span>}
            </div>
            <div>
              <strong>反证与失效条件</strong>
              {output.counterEvidence.length || output.invalidatingConditions.length ? (
                <ul>{[...output.counterEvidence, ...output.invalidatingConditions].map((item) => (
                  <li key={item}>{item}</li>
                ))}</ul>
              ) : <span>暂无反证或失效条件</span>}
            </div>
          </div>
          <div className={styles.aiMeta}>
            <span>提示词 {data?.promptVersion}</span>
            <span>模型 {data?.model || '未记录'}</span>
            <span>证据指纹 {data?.evidenceFingerprint?.slice(0, 12) || '—'}</span>
            <span>来源编号 {output.sourceIds.join('、') || '—'}</span>
          </div>
        </div>
      )}
    </section>
  );
}
