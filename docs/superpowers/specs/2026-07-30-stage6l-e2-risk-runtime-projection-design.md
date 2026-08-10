# 阶段 6L-E2 风险候选投影到运行时研究证据的可选接入设计

## 目标

允许现有龙头运行时接收调用方按证券显式提供的、已经由 E1 冻结的风险候选
投影，并把结果写入候选
`researchFeatures.riskCandidateProjection`。接入只增加研究证据，不改变
评分、正式风险过滤或状态机。

## 输入合同

`build_leader_runtime_evidence()` 新增可选参数：

```text
risk_candidate_projections_by_symbol
```

键为当前候选证券代码，值为
`LeaderRiskCandidateProjection`。调用方不提供时保持现有运行路径，只输出
明确的风险研究证据缺失状态。运行时不重新执行 E1、不自动发现文档、不查询
数据库，也不抓取任何风险来源。

## 校验与降级

每个进入行业前五的候选独立处理：

1. 没有输入：`missing/risk_candidate_projection_missing`；
2. 映射键命中但投影证券与候选不一致：`source_unverified`；
3. 投影 `as_of` 缺少时区或与当前运行批次不一致：
   `source_unverified`；
4. E1投影、D9审计或D8证据包合同版本不匹配：
   `source_unverified`；
5. 投影任一正式标志不是严格的 `false`：`source_unverified`；
6. 身份、时点、合同和正式标志都通过后，完整只读投影才进入候选研究
   证据。

任一候选风险输入失败不得使整轮市场、行业或其他候选失败。

## 输出合同

`researchFeatures.riskCandidateProjection` 固定包含：

- 研究状态和稳定原因；
- `riskFilterPassed=false`；
- `scoreReady=false`；
- `formalGateReady=false`；
- `formalUsable=false`；
- `appliedToD3=false`；
- `appliedToD1=false`；
- 校验通过时的完整只读 `projection`，否则为 `null`。

新增研究字段后，`runtimeInputVersion` 从
`radar-leader-runtime-input-v1` 升为
`radar-leader-runtime-input-v2`；其余既有字段保持兼容。候选
`invalidation.missingPrerequisites` 在所有路径继续保留
`risk_evidence`，因为 E1 投影仍不是正式风险证据。

## 明确不做

- 不修改 E1、D1-D9 或正式风险事件；
- 不增加风险评分维度；
- 不把 E1 的研究关系应用到 D1 或 D3；
- 不修改 `LeaderGateInput.risk_filter_passed=false`；
- 不连接 SQLite、仓储、调度、API、前端、提醒或 AI；
- 不新增迁移、依赖、环境变量或运行开关；
- 不重启服务，不部署。

## 验收

1. 缺失输入稳定输出研究缺失，现有候选数量和其他研究证据不变；
2. 合法 E1 投影只进入对应候选；
3. 错证券、错批次和不可信合同只降级单个候选；
4. 伪造为正式可用的 E1 投影被拒绝；
5. 所有路径的风险门禁和评分就绪标志保持 `false`；
6. E1-E2 专项、全部龙头和隔离生产库的后端完整回归通过。
