const LEADER_REASON_LABELS: Record<string, string> = {
  stage_not_enabled: '龙头监测模块尚未启用',
  stage6_storage_not_ready: '龙头监测数据尚未就绪',
  candidate_snapshot_missing: '尚未形成候选快照',
  stage6_read_failed: '龙头监测快照读取失败',
  snapshot_stale: '监测快照已超过有效时间',
  state_entered: '本轮进入该状态',
  state_maintained: '本轮继续保持该状态',
  leader_observation_only: '当前仅为真实观察候选，尚未形成三级规则评级',
  leader_observation_stale: '观察候选行情已超过有效时间',
  leader_observation_snapshot_missing: '尚未形成真实观察候选快照',
  leader_observation_failed: '观察候选读取失败',
  leader_observation_publish_failed: '观察候选发布失败',
  business_evidence_missing: '缺少可核验的主营业务证据',
  business_evidence_source_unverified: '主营业务证据来源尚未验证',
  business_evidence_source_failed: '主营业务证据来源读取失败',
  business_exposure_unverified: '主营业务与当前主线的关联尚未验证',
  risk_evidence_missing: '缺少完整的风险证据',
  tradability_evidence_missing: '缺少当轮可交易性数据',
  industry_evidence_missing: '缺少可核验的行业映射',
};

export function radarLeaderReasonLabel(value: string | null | undefined) {
  if (!value) return '无';
  return LEADER_REASON_LABELS[value] || '当前未满足全部龙头规则';
}
