# 阶段 6L-F6 两阶段候选计划与单次 Assembly 编排设计

## 目标

把F5之后仍然倒置的调用方向改为：先冻结候选计划，再按完整候选集合准备
历史、主营催化、可交易性和E3风险输入，最后只构建一次Assembly并直接
进入F4。F6不接生产运行时、数据库或调度。

## 候选计划

新增只读`LeaderRuntimeCandidatePlan`：

- 绑定`radar_run_id`、`quote_batch_id`、等价UTC时点和版本化合同；
- 固定候选顺序、证券、行业、行业发布ID、行业内排名和行情来源合同；
- 使用稳定SHA-256生成`candidate_set_id`；
- 状态区分`ready/not_ready/blocked`，来源失败和合同错误不混淆；
- 所有正式评分、门禁、可用性和状态迁移标志保持`false`。

候选计划复用当前运行时的A股范围、行业映射、行情研究资格及每行业涨跌幅
前5名口径，但不调用横截面、历史、流动性、主营催化或可交易性构造器。

## 基于计划的提供器

F5保留原Assembly入口，同时新增Plan入口。提供器结果显式携带
`candidatePlanId`，并继续执行：

- 条目全集、重复和额外证券校验；
- run、时点、行业、发布ID及行情来源身份校验；
- E3完整批次和非ready条目保留；
- 结果按计划顺序输出只读强类型映射。

## 单次 Assembly 编排

新增最外层纯内存编排器：

1. 校验计划、原始行情批次和Plan提供器输入；
2. 提供器阻断时不调用任何研究特征构造器；
3. 使用完整只读映射调用现有`build_leader_runtime_evidence()`恰好一次；
4. 深层比对Assembly与计划的run、批次、时点、候选顺序、行业、发布ID和
   行情来源合同；
5. 将同一个Assembly及完整E3批次交给F4，取得F2/F3结果。

当前真实Fixture仍因流动性正式证据、历史恢复证据和正式风险门禁缺口返回
`partial`。F6保持该语义，不为了让测试显示ready而伪造输入。

## 依赖方向

```text
candidate_plan -> provider_batch
runtime_inputs -> provider_batch
provider_batch + runtime_inputs + F4 -> single_pass_orchestration
```

`runtime_inputs`和F4均不导入新编排器，避免循环依赖。

## 明确不做

- 不修改`backend/radar/runtime.py`、调度、仓储、迁移、API或页面；
- 不连接真实历史、主营催化、可交易性或风险来源；
- 不改变F1-F4、E3及五类研究公式；
- 不启用正式行业门槛、股票硬门槛、风险过滤、评分或状态迁移；
- 不停止或重启服务，不执行Git、部署或生产数据库操作。

## 验收

1. 计划候选与现有运行时候选顺序一致；
2. 计划生成期间五类构造器调用次数为零；
3. 提供器按`candidatePlanId`绑定完整候选集合；
4. 单次编排中Assembly只构建一次，每类特征每候选只计算一次；
5. 输入阻断发生在特征构建前，来源或Assembly错配闭合失败；
6. F1-F6、全部龙头和隔离完整后端回归通过。
