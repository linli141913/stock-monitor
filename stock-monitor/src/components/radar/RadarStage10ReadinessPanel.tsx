import ModuleStatePanel from './ModuleStatePanel';
import { selectFormalShadowObservation } from '@/lib/formal-shadow-progress';
import type {
  RadarFormalGateReadiness,
  RadarFormalReadinessModule,
  RadarFormalReadinessResponse,
  RadarFormalReadinessState,
  RadarFormalShadowProgressResponse,
  RadarModuleState,
} from '@/types/radar';

interface RadarStage10ReadinessPanelProps {
  data: RadarFormalReadinessResponse | null;
  loadError?: string;
  shadowProgress?: RadarFormalShadowProgressResponse | null;
  shadowProgressLoadError?: string;
}

const MODULE_LABELS: Record<RadarFormalReadinessModule['module'], string> = {
  trendRotation: '趋势轮动',
  etfObservation: 'ETF监测',
  leaderObservation: '龙头观察',
};

const GATE_LABELS: Record<string, string> = {
  stage9_quality: '阶段9质量',
  rule_version: '规则版本冻结',
  calibration: '校准证据',
  shadow_ledger: '影子观察台账',
  data_quality: '数据质量',
  performance: '性能检查',
  security: '安全检查',
  rollback: '回退准备',
  runbook: '运行手册',
};

const REASON_LABELS: Record<string, string> = {
  formal_readiness_report_missing: '正式就绪证据报告尚未生成',
  formal_readiness_store_unverified: '正式就绪证据仓无法验证',
  formal_readiness_report_unverified: '正式就绪报告无法验证',
  formal_readiness_checked_at_future: '正式就绪报告时间来自未来',
  formal_clock_unverified: '服务端当前时钟无法验证',
  formal_freshness_policy_missing: '正式就绪证据缺少版本化有效期策略',
  formal_readiness_report_expired: '正式就绪报告已过期',
  formal_evidence_expired: '正式就绪的普通证据已过期',
  formal_evidence_source_time_future: '正式就绪证据的来源时间来自未来',
  formal_operational_checks_expired: '性能、安全、回退与手册运营证据已过期',
  formal_shadow_progress_store_unconfigured: '真实影子台账读取路径尚未配置',
  formal_shadow_progress_store_unverified: '真实影子台账读取路径无法验证',
  formal_shadow_ledger_missing: '真实影子台账尚未生成',
  formal_shadow_ledger_hash_mismatch: '真实影子台账内容哈希不匹配',
  formal_shadow_ledger_manifest_unverified: '真实影子台账清单无法验证',
  formal_shadow_ledger_report_unverified: '真实影子台账内容无法验证',
  formal_shadow_ledger_future_observation: '真实影子台账包含未来观察时间',
  shadow_observation_collecting: '影子观察交易日仍在积累',
  stage9_quality_collecting: '阶段9真实验证仍在积累',
  stage9_quality_not_ready: '阶段9对应域质量门尚未就绪',
  stage9_quality_failed: '阶段9对应域质量检查失败',
  rule_version_collecting: '规则版本尚未冻结',
  calibration_collecting: '校准证据仍在积累',
  shadow_ledger_collecting: '影子观察台账仍在积累',
  data_quality_collecting: '数据质量证据仍在积累',
  performance_collecting: '性能证据仍在积累',
  security_collecting: '安全检查证据仍在积累',
  rollback_collecting: '回退准备证据仍在积累',
  runbook_collecting: '运行手册证据仍在积累',
};

function labelFor(code: string) {
  return REASON_LABELS[code] || code;
}

function visualState(state: RadarFormalReadinessState): RadarModuleState {
  if (state === 'formal_enabled') return 'available';
  if (state === 'failed') return 'failed';
  return 'not_ready';
}

function formalStateCopy(module: RadarFormalReadinessModule) {
  if (module.formalEnabled) return '已正式开启';
  if (module.state === 'ready_to_enable') return '自动证据门已就绪，尚未开启';
  if (module.state === 'collecting') return '真实影子观察中';
  if (module.state === 'failed') return '证据校验失败，保持关闭';
  return '自动证据门尚未就绪';
}

function gateMilestoneState(state: RadarFormalGateReadiness) {
  if (state === 'ready') return 'done' as const;
  if (state === 'failed') return 'active' as const;
  return 'pending' as const;
}

function allBlockers(module: RadarFormalReadinessModule) {
  return Array.from(new Set([
    ...module.reasonCodes,
    ...module.gates.flatMap((gate) => gate.reasonCodes),
  ].map(labelFor)));
}

function FormalModulePanel({
  module,
  shadowProgress,
}: {
  module: RadarFormalReadinessModule;
  shadowProgress: RadarFormalShadowProgressResponse | null;
}) {
  const blockers = allBlockers(module);
  const readyGateCount = module.gates.filter((gate) => gate.state === 'ready').length;
  const firstBlocker = blockers[0];
  const observation = selectFormalShadowObservation(module, shadowProgress);

  return (
    <ModuleStatePanel
      state={visualState(module.state)}
      title={`阶段10 · ${MODULE_LABELS[module.module]}`}
      description={`${formalStateCopy(module)}。正式请求与实际开关均为只读事实，不在页面修改。`}
      metrics={[
        {
          label: '影子观察',
          value: `${observation.value} 个有效交易日`,
        },
        { label: '最近有效日', value: observation.latestReadyTradingDate || '—' },
        {
          label: '当前连续日',
          value: observation.latestReadyStreak === null
            ? '—'
            : `${observation.latestReadyStreak} 个交易日`,
        },
        { label: '正式请求', value: module.requested ? '已请求' : '未请求' },
        { label: '配置开关', value: module.configuredEnabled ? '已配置开启' : '保持关闭' },
        { label: '实际状态', value: module.formalEnabled ? '已正式开启' : '尚未正式开启' },
      ]}
      progress={[
        {
          label: '影子观察日',
          value: observation.value,
          detail: observation.source === 'shadow_ledger'
            ? '来自已校验内容哈希的真实影子台账，不由页面补算'
            : observation.evidenceLoaded
              ? '来自已校验的正式就绪报告，不由页面补算'
            : '就绪报告未载入，当前不能把后端默认0当成已核验的0天',
          percent: observation.percent,
          tone: observation.evidenceLoaded
            && observation.observedTradingDays !== null
            && observation.observedTradingDays >= observation.requiredTradingDays
            ? 'good'
            : 'warning',
        },
        {
          label: '九项自动证据门',
          value: `${readyGateCount}/${module.gates.length}`,
          detail: '每项门的状态与阻塞原因均来自只读接口',
          percent: module.gates.length ? (readyGateCount / module.gates.length) * 100 : 0,
          tone: readyGateCount === module.gates.length ? 'good' : 'accent',
        },
      ]}
      milestones={module.gates.map((gate) => ({
        label: GATE_LABELS[gate.gate] || gate.gate,
        detail: gate.reasonCodes.length
          ? gate.reasonCodes.map(labelFor).join('；')
          : gate.state === 'ready'
            ? '证据已验证'
            : '等待后端形成可验证证据',
        status: gateMilestoneState(gate.state),
      }))}
      blockers={blockers}
      nextStep={firstBlocker
        ? `首个稳定阻塞：${firstBlocker}`
        : module.formalEnabled
          ? '已保持正式状态，仍按各自证据周期继续监测。'
          : '自动证据门已就绪，仍需后续独立授权生产配置与服务变更。'}
      badges={[
        `后端状态：${module.state}`,
        module.formalEnabled ? '已正式开启' : '未正式开启',
      ]}
    />
  );
}

export default function RadarStage10ReadinessPanel({
  data,
  loadError = '',
  shadowProgress = null,
  shadowProgressLoadError = '',
}: RadarStage10ReadinessPanelProps) {
  if (!data) {
    return (
      <ModuleStatePanel
        state={loadError ? 'failed' : 'not_ready'}
        title="阶段10 · 正式启用准备度"
        description={loadError
          ? '正式就绪报告读取失败；页面已清空上一份结果，不使用旧状态冒充本轮成功。'
          : '正在等待只读正式就绪报告；不会把空证据显示为通过。'}
        blockers={loadError ? [loadError] : []}
      />
    );
  }

  const overallCopy = data.anyFormalEnabled
    ? '至少一个模块已正式开启；其余模块仍按各自门槛独立展示。'
    : data.state === 'ready_to_enable'
      ? '存在自动证据门已就绪的模块，但尚未正式开启。'
      : data.state === 'failed'
        ? '正式就绪证据校验失败，全部模块保持关闭。'
        : '各模块仍在独立积累真实影子观察和自动证据。';
  const freshnessPolicy = data.freshnessPolicy;
  const shadowProgressReasons = shadowProgress?.reasonCodes.map(labelFor) || [];

  return (
    <>
      <ModuleStatePanel
        state={visualState(data.state)}
        title="阶段10 · 正式启用准备度"
        description={`${overallCopy} 本区域只展示后端的真实只读结果，不触发交易或AI操作。`}
        metrics={[
          { label: '报告检查时间', value: data.checkedAt },
          { label: '模块数', value: `${data.modules.length} 个` },
          { label: '正式开启模块', value: `${data.modules.filter((module) => module.formalEnabled).length} 个` },
          { label: '阶段9质量状态', value: data.stage9QualityState },
          { label: '证据引用', value: `${data.evidence.length} 项` },
          {
            label: '真实影子台账',
            value: shadowProgress?.state === 'available'
              ? '已验证载入'
              : shadowProgress?.state === 'failed'
                ? '验证失败'
                : '尚未载入',
          },
          {
            label: '台账内容哈希',
            value: shadowProgress?.ledgerSha256
              ? shadowProgress.ledgerSha256.slice(0, 12)
              : '—',
          },
          {
            label: '有效期策略',
            value: freshnessPolicy?.policyVersion || '未提供',
          },
          {
            label: '报告最大年龄',
            value: freshnessPolicy
              ? `${freshnessPolicy.reportMaxAgeSeconds} 秒`
              : '—',
          },
          {
            label: '普通证据最大年龄',
            value: freshnessPolicy
              ? `${freshnessPolicy.evidenceMaxAgeSeconds} 秒`
              : '—',
          },
          {
            label: '运营证据最大年龄',
            value: freshnessPolicy
              ? `${freshnessPolicy.operationalChecksMaxAgeSeconds} 秒`
              : '—',
          },
        ]}
        blockers={[
          ...data.reasonCodes.map(labelFor),
          ...shadowProgressReasons,
          ...(shadowProgressLoadError ? [shadowProgressLoadError] : []),
        ]}
        badges={[
          `正式就绪合同：${data.contractVersion}`,
          data.allModulesFormalEnabled ? '全部模块已正式开启' : '并非全部模块已正式开启',
        ]}
      />
      {data.modules.map((module) => (
        <FormalModulePanel
          key={module.module}
          module={module}
          shadowProgress={shadowProgress}
        />
      ))}
    </>
  );
}
