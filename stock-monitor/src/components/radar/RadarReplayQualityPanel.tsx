import ModuleStatePanel from './ModuleStatePanel';
import type { RadarReplayQualityResponse } from '@/types/radar';

interface RadarReplayQualityPanelProps {
  data: RadarReplayQualityResponse | null;
  loadError?: string;
}

const REASON_LABELS: Record<string, string> = {
  radar_replay_report_missing: '尚未发布严格时点回放报告',
  radar_replay_manifest_unverified: '回放清单无法验证',
  radar_replay_report_unverified: '回放报告无法验证',
  radar_replay_report_hash_mismatch: '回放报告内容校验失败',
  radar_replay_report_identity_mismatch: '回放报告身份不一致',
  radar_replay_report_from_future: '回放报告时间来自未来',
  replay_required_domain_missing: '必需历史证据域缺失',
  replay_partition_missing: '开发、校准或独立留出样本分区不足',
  replay_partition_chronology_violation: '开发、校准、留出样本日期顺序错误',
  replay_evidence_unverifiable: '部分历史证据无法验证',
  replay_source_failed: '部分历史来源失败',
  replay_duplicate_state_violation: '同股同时点存在重复状态',
  replay_multi_state_violation: '同股同时点存在多个状态',
  replay_golden_labels_missing: '独立客观结果尚未覆盖全部雷达模块',
  replay_objective_outcomes_missing: '独立客观结果尚未覆盖全部雷达模块',
  replay_rule_outputs_missing: '部分确定性规则输出尚未冻结',
  replay_rule_outputs_unverifiable: '部分确定性规则已运行但输出无法验证',
  replay_rule_outputs_failed: '部分确定性规则生产器运行失败',
  replay_label_partition_missing: '部分样本分区缺少独立客观结果',
  replay_label_output_incomparable: '部分客观结果找不到对应确定性输出',
  replay_label_disputed: '部分独立结果存在来源冲突',
  replay_label_unverifiable: '部分独立结果无法验证',
  replay_output_target_outcomes_incomplete: '部分冻结输出尚无对应客观结果',
};

const INFORMATIONAL_REASONS = new Set([
  'replay_scoped_exclusions_present',
]);

const PARTITION_LABELS: Record<string, string> = {
  development: '开发集',
  calibration: '校准集',
  holdout: '独立留出集',
};

const DOMAIN_LABELS: Record<string, string> = {
  security_universe: '证券范围',
  trading_rule: '历史交易规则',
  industry: '历史行业归属',
  index: '历史指数',
  etf: '历史ETF产品及状态',
  corporate_action: '历史公司行为',
};

const LABEL_DOMAIN_LABELS: Record<string, string> = {
  market: '市场环境',
  sector: '主线行业',
  etf: 'ETF观察',
  leader: '三级龙头',
};

function formatDateTime(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export default function RadarReplayQualityPanel({
  data,
  loadError = '',
}: RadarReplayQualityPanelProps) {
  const hasReport = Boolean(data?.replayRunId);
  const totalSamples = data
    ? data.sampleCounts.development
      + data.sampleCounts.calibration
      + data.sampleCounts.holdout
    : 0;
  const etfReadinessCounts = data?.etfReadinessCounts ?? {
    formalAdmissionCount: 0,
    monitoringReadyCount: 0,
    monitoringMissingCount: 0,
    rankingPolicyReadyCount: 0,
    rankingPolicyMissingCount: 0,
  };
  const hasMetrics = data
    ? Object.values(data.metrics).some((metric) => metric !== null)
    : false;
  const blockers = Array.from(new Set([
    ...(loadError ? [loadError] : []),
    ...(data?.reasonCodes || [])
      .filter((reason) => !INFORMATIONAL_REASONS.has(reason))
      .map((reason) => REASON_LABELS[reason] || reason),
    ...(data?.missingDomains || []).map(
      (domain) => `缺失证据域：${DOMAIN_LABELS[domain] || domain}`,
    ),
    ...(data?.missingPartitions || []).map(
      (partition) => `缺失样本分区：${PARTITION_LABELS[partition] || partition}`,
    ),
    ...(data?.missingLabelDomains || []).map(
      (domain) => `缺失独立客观结果：${LABEL_DOMAIN_LABELS[domain] || domain}`,
    ),
    ...(data?.missingOutputDomains || []).map(
      (domain) => `缺失规则输出：${LABEL_DOMAIN_LABELS[domain] || domain}`,
    ),
    ...(data?.unverifiableOutputDomains || []).map(
      (domain) => `规则输出不可验证：${LABEL_DOMAIN_LABELS[domain] || domain}`,
    ),
    ...(data?.failedOutputDomains || []).map(
      (domain) => `规则输出失败：${LABEL_DOMAIN_LABELS[domain] || domain}`,
    ),
    ...(data?.missingLabelPartitions || []).map(
      (partition) => `该分区缺少独立客观结果：${PARTITION_LABELS[partition] || partition}`,
    ),
    ...(data?.unlabeledOutputTargetCount
      ? [`仍有 ${data.unlabeledOutputTargetCount} 个冻结输出目标未取得客观结果`]
      : []),
  ]));

  return (
    <ModuleStatePanel
      state={data?.stage9QualityGatePassed === true
        ? 'available'
        : data?.engineeringState === 'failed'
          || data?.validationState === 'failed'
          || Boolean(loadError)
          ? 'failed'
          : 'not_ready'}
      title="严格时点历史回放"
      description={hasReport
        ? data?.stage9QualityGatePassed === true
          ? `阶段9质量门已通过，已读取 ${formatDateTime(data?.createdAt || null)} 生成的内容校验报告。只允许使用当时已知证据。`
          : data?.engineeringState === 'complete'
            ? `工程链完成、真实验证观察中。已读取 ${formatDateTime(data?.createdAt || null)} 生成的内容校验报告；在阶段9质量门通过前，页面不会把工程完成显示为质量通过。`
          : `已读取内容校验的回放质量报告，生成于 ${formatDateTime(data?.createdAt || null)}。只允许使用当时已知证据。`
        : '尚无可验证的回放报告。页面不会把空数据显示成0胜率或0收益。'}
      metrics={hasReport && data ? [
        { label: '回放样本', value: `${totalSamples.toLocaleString('zh-CN')} 个` },
        { label: '可用样本', value: `${data.includedCount.toLocaleString('zh-CN')} 个` },
        { label: '排除样本', value: `${data.excludedCount.toLocaleString('zh-CN')} 个` },
        { label: '范围内排除', value: `${data.scopedExclusionCount.toLocaleString('zh-CN')} 项` },
        { label: '独立留出集', value: `${data.sampleCounts.holdout.toLocaleString('zh-CN')} 个` },
        { label: '可比较结果', value: `${data.comparableLabelCount.toLocaleString('zh-CN')} 个` },
        { label: 'ETF监测就绪', value: `${etfReadinessCounts.monitoringReadyCount.toLocaleString('zh-CN')} 条` },
      ] : []}
      milestones={hasReport && data ? [
        {
          label: '阶段9工程链路',
          detail: data.engineeringState === 'complete'
            ? '采集、冻结、回放、校验和质量报告链路可用'
            : '工程链路尚未形成可验证报告',
          status: data.engineeringState === 'complete' ? 'done' : 'pending',
        },
        {
          label: '历史时点证据检查',
          detail: data.futureViolationCount === 0
            ? '未发现使用未来数据'
            : `发现 ${data.futureViolationCount} 项未来数据违规`,
          status: data.futureViolationCount === 0 ? 'done' : 'active',
        },
        {
          label: '显式范围排除',
          detail: data.scopedExclusionCount > 0
            ? `已如实隔离 ${data.scopedExclusionCount} 项不可判断对象，不阻断其余真实样本`
            : '本批没有范围内排除对象',
          status: 'done',
        },
        {
          label: '开发 / 校准 / 留出集隔离',
          detail: data.partitionChronologyValid
            ? `日期顺序有效：开发 ${data.sampleCounts.development}，校准 ${data.sampleCounts.calibration}，留出 ${data.sampleCounts.holdout}`
            : `分区不完整或日期顺序无效：开发 ${data.sampleCounts.development}，校准 ${data.sampleCounts.calibration}，留出 ${data.sampleCounts.holdout}`,
          status: data.partitionChronologyValid ? 'done' : 'pending',
        },
        {
          label: '确定性规则输出',
          detail: (
            data.missingOutputDomains.length === 0
            && data.unverifiableOutputDomains.length === 0
            && data.failedOutputDomains.length === 0
          )
            ? `市场 ${data.outputCounts.market}，行业 ${data.outputCounts.sector}，ETF ${data.outputCounts.etf}，龙头 ${data.outputCounts.leader}`
            : [
              data.missingOutputDomains.length
                ? `缺失 ${data.missingOutputDomains.map((domain) => LABEL_DOMAIN_LABELS[domain] || domain).join('、')}`
                : '',
              data.unverifiableOutputDomains.length
                ? `不可验证 ${data.unverifiableOutputDomains.map((domain) => LABEL_DOMAIN_LABELS[domain] || domain).join('、')}`
                : '',
              data.failedOutputDomains.length
                ? `失败 ${data.failedOutputDomains.map((domain) => LABEL_DOMAIN_LABELS[domain] || domain).join('、')}`
                : '',
            ].filter(Boolean).join('；'),
          status: (
            data.missingOutputDomains.length === 0
            && data.unverifiableOutputDomains.length === 0
            && data.failedOutputDomains.length === 0
          ) ? 'done' : data.failedOutputDomains.length > 0 ? 'active' : 'pending',
        },
        {
          label: 'ETF真实监测 / 加权排名',
          detail: etfReadinessCounts.formalAdmissionCount > 0
            ? `同轮准入证据 ${etfReadinessCounts.formalAdmissionCount} 条；监测就绪 ${etfReadinessCounts.monitoringReadyCount}、输入缺失 ${etfReadinessCounts.monitoringMissingCount}；排名政策就绪 ${etfReadinessCounts.rankingPolicyReadyCount}、仍在校准 ${etfReadinessCounts.rankingPolicyMissingCount}`
            : '当前回放样本尚未绑定同轮ETF正式监测准入证据；产品研究输出不冒充完整监测或排名',
          status: etfReadinessCounts.monitoringReadyCount > 0
            ? 'done'
            : 'pending',
        },
        {
          label: '独立客观结果',
          detail: data.missingLabelDomains.length === 0
            && data.unlabeledOutputTargetCount === 0
            ? `市场 ${data.labelCounts.market}，行业 ${data.labelCounts.sector}，ETF ${data.labelCounts.etf}，龙头 ${data.labelCounts.leader}`
            : [
              data.missingLabelDomains.length
                ? `缺少模块：${data.missingLabelDomains.map((domain) => LABEL_DOMAIN_LABELS[domain] || domain).join('、')}`
                : '',
              data.unlabeledOutputTargetCount
                ? `未覆盖冻结目标 ${data.unlabeledOutputTargetCount} 个`
                : '',
            ].filter(Boolean).join('；'),
          status: data.missingLabelDomains.length === 0
            && data.unlabeledOutputTargetCount === 0
            ? 'done'
            : 'pending',
        },
        {
          label: '结果指标发布',
          detail: hasMetrics
            ? '已发布真实回放指标'
            : '尚未发布可验证的胜率、收益或命中指标',
          status: hasMetrics ? 'done' : 'pending',
        },
      ] : []}
      blockers={blockers}
      nextStep={data?.stage9QualityGatePassed && hasMetrics
        ? '保留独立留出集，按新数据时点继续增量验证'
        : data?.shadowCollectionAllowed
          ? '阶段9工程已完成，可进入阶段10影子采集；继续积累真实来源与独立结果，不把研究待验证误写成工程未完成'
          : '先生成可校验的真实回放报告；缺失、不可验证和失败必须分开记录'}
      badges={[
        data?.engineeringState === 'complete' ? '工程链路已完成' : '工程链路待就绪',
        data?.stage9QualityGatePassed ? '质量门已通过' : '客观结果积累中',
        '禁止未来函数',
        'ETF监测与排名分离',
        '缺失不写成0',
        '不使用模拟历史',
      ]}
    />
  );
}
