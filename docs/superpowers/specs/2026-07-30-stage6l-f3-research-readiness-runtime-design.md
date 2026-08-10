# 阶段 6L-F3 研究就绪审计运行时只读接入设计

## 目标

让阶段6运行时只消费调用方显式提供的F2可信审计映射，并把每只候选的
压缩研究就绪摘要写入 `researchFeatures`。本阶段不重算F1/F2，不改变
候选名单、正式评分、门禁、状态机或状态迁移。

## 输入合同

`build_leader_runtime_evidence()`新增可选参数：

```python
research_readiness_audits_by_symbol: Optional[
    Mapping[str, LeaderResearchReadinessAuditResult]
] = None
```

未传参数保持向后兼容。运行时按已经形成的候选证券读取映射，不从映射
反推、增加、删除或重排候选。多余证券静默忽略，映射容器错误不得使整轮
崩溃。

## 校验顺序

每只候选按以下顺序校验：

1. 映射中是否存在该证券；
2. 对象是否为冻结F1结果；
3. 审计证券是否与候选一致；
4. `as_of`是否带时区且与运行时批次表示同一真实时刻；
5. F1合同ID是否正确；
6. 四个正式标志是否严格为`false`；
7. 状态是否为可消费的`ready/partial`；
8. 通过F2公共只读守卫复核七项顺序、嵌套状态、计数、缺失项、
   阻断项和稳定原因码。

F3复用F2深层守卫，不复制私有校验逻辑，不因对象来自普通字典或F2映射
就默认可信。F1 `blocked`表示合同、身份或时点不可信，不能进入运行时
审计摘要。

## 输出合同

运行时证据版本从 `radar-leader-runtime-input-v2` 升为
`radar-leader-runtime-input-v3`，新增：

```text
researchFeatures.researchReadinessAudit
```

外层固定包含：

- `status`：`ready/partial/missing/source_unverified`；
- 稳定脱敏 `reasons`；
- `scoreReady=false`、`researchScore=null`；
- `formalScoreReady/formalGateReady/formalUsable/
  stateTransitionAllowed=false`；
- 可选压缩 `audit`。

压缩审计只包含：

- F1合同ID和审计状态；
- 候选证券和审计时点；
- 必需项、证据可用项、满足项计数；
- 缺失项和明确阻断项；
- 正式首次否决和候选研究首次阻断。

不输出七项完整 `items`、来源合同明细、上游研究对象、风险投影或任意异常
正文。

## 状态与原因

- 未传映射或候选不存在：`missing`，
  `leader_research_readiness_audit_missing`；
- 合法F1 `ready/partial`：保留原审计状态；
- 映射容器错误：`source_unverified`，
  `leader_research_readiness_audit_mapping_unverified`；
- 对象或合同错误：`source_unverified`，
  `leader_research_readiness_audit_contract_unverified`；
- 身份错误：`leader_research_readiness_audit_identity_mismatch`；
- 时点错误：`leader_research_readiness_audit_as_of_mismatch`；
- 正式标志错误：`leader_research_readiness_audit_formal_flag_invalid`；
- 手工注入F1 `blocked`：
  `leader_research_readiness_audit_not_consumable`；
- 深层不变量错误：
  `leader_research_readiness_audit_result_unverified`。

所有错误只影响对应字段或候选；错误映射容器使所有候选的该字段降级，但
运行时Assembly、候选数量、排序和其他研究证据保持不变。

## 明确不做

- 不在候选循环中调用F1或F2构建器；
- 不自动发现、抓取或重算任何研究证据；
- 不修改 `LeaderGateInput`、正式六维分数或 `missingPrerequisites`；
- 不接入仓储、SQLite、迁移、调度、API、前端、提醒或AI；
- 不新增依赖、环境变量或阶段6运行开关；
- 不停止或重启服务，不执行Git暂存、提交、推送或部署。

## 验收

1. 默认缺失、ready、partial和不可信状态稳定输出；
2. F2只读映射可直接消费，运行时不重算F1/F2；
3. 身份、时点、合同、嵌套语义和正式标志伪造被逐股隔离；
4. UTC与等价偏移时区按同一真实时刻接受；
5. 错误映射容器不使整轮崩溃；
6. 压缩证据不包含完整项目、来源或异常正文；
7. 正式评分、门禁、缺失前置、候选数量和排序保持不变；
8. 专项、联合、全部龙头及隔离完整后端回归通过。
