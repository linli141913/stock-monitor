# 阶段 6L-F1 候选研究证据完整度与首次否决审计设计

## 目标

把阶段 6L-A 至 6L-E3 已经形成的候选研究结果汇总成一份冻结审计：
逐项说明证据是否存在、是否足以继续研究、当前第一个阻断原因是什么。
本阶段只解释“为什么还不能升级”，不产生正式分数、龙头状态或状态迁移。

## 输入合同

审计输入固定包含候选证券、统一带时区 `as_of` 和以下强类型结果：

- 当期横截面 `LeaderResearchFeatureResult`；
- 历史连续性 `LeaderHistoryFeatureResult`；
- 流动性 `LeaderLiquidityFeatureResult`；
- 主营催化 `LeaderBusinessCatalystFeatureResult`；
- 可交易性 `LeaderTradabilityFeatureResult`；
- E3 单证券风险批次条目
  `LeaderRiskCandidateProjectionBatchItem`。

审计器不解析运行时松散 JSON，不重算上游特征，不接收正式门禁结果。
风险条目同时保留非就绪证券的状态、原因和可选 E1 投影；不能只消费 E3
的 ready-only 映射，否则会把真实失败退化成通用缺失。

## 固定审计顺序

首次否决原因按规划书的龙头判断顺序固定为：

1. 行业强度研究证据；
2. 证券生命周期和可交易性；
3. 市场领先性研究证据；
4. 流动性研究证据；
5. 历史连续性与回流研究证据；
6. 主营与催化关系证据；
7. 风险候选投影。

当期横截面的 `auxiliary` 只作辅助观察，不计入必需项，也不能绕过任一
前序阻断。

## 可用与否决语义

每个审计项分别保存：

- 上游 `ResearchFeatureStatus`；
- 证据是否可用；
- 当前研究结论是否形成明确阻断；
- 稳定阻断原因；
- 上游具体原因和来源合同。

`ready` 不自动等于“正面”或“正式通过”。以下情况证据本身可用，但仍形成
明确研究阻断：

- 可交易性结果为 `research_eligible=false`；
- 新股规则只允许进入预备观察；
- 历史结果明确缺少回流事件；
- 主营催化关系为 `unconfirmed` 或 `disproved`；
- 风险投影仍存在正式门禁缺口，或正式风险过滤尚未启用。

横截面分数只用于证明研究证据已计算；F1 不设置任何分数阈值，不判断
分数高低是否达标。真实 0 分仍属于真实已计算结果，不能改写为缺失。

## 输出合同

冻结结果固定包含：

- 合同版本、候选身份和批次时点；
- `ready/partial/blocked` 审计状态；
- 有序逐项审计；
- 必需项总数、证据可用数、满足数；
- 缺失项、明确阻断项、稳定 `ordered_blocker_codes`、
  `first_veto_reason` 和 `first_research_blocker_reason`；
- `formal_score_ready/formal_gate_ready/formal_usable/
  state_transition_allowed=false`。

状态语义：

- `ready`：七项研究证据均能形成可信解释；证据结论可以是正面、负面或
  限制性结果；
- `partial`：至少一项证据为缺失、过期、来源失败或来源未验证；
- `blocked`：输入合同、候选身份、时点或固定子合同不可信，无法安全执行
  跨维度审计。

F1 没有正式行业门槛和正式股票硬门槛输入，不能把运行时固定的 `false`
解释为“门槛失败”。因此所有合同可信的当前结果中，正式
`first_veto_reason` 固定先返回
`leader_formal_industry_gate_unavailable`；后续阻断按正式判断顺序进入
`ordered_blocker_codes`。

`first_research_blocker_reason` 才表示七项候选研究证据中的第一项缺口、
反证或限制。两者不得混用：前者回答“为什么现在不能进入正式状态”，后者
回答“候选自己的研究证据首先卡在哪里”。

稳定原因码至少包括：

- `leader_research_audit_contract_unverified`；
- `leader_research_audit_identity_mismatch`；
- `leader_research_audit_as_of_mismatch`；
- `leader_formal_industry_gate_unavailable`；
- `leader_formal_stock_gate_unavailable`；
- `leader_formal_stock_gate_failed`；
- `leader_cross_section_evidence_unavailable`；
- `leader_liquidity_evidence_unavailable`；
- `leader_history_evidence_unavailable`；
- `leader_business_exposure_disproved`；
- `leader_business_exposure_unconfirmed`；
- `leader_business_evidence_unavailable`；
- `leader_risk_evidence_unavailable`；
- `leader_formal_risk_gate_unavailable`；
- `leader_formal_evaluation_disabled`。

## 明确不做

- 不修改 `LeaderGateInput`、评分、状态机或同股状态约束；
- 不接入运行时、仓储、SQLite、调度、API、前端、提醒或 AI；
- 不新增迁移、依赖、环境变量或运行开关；
- 不把缺失补 0，不推导正式阈值；
- 不重启服务，不执行 Git 暂存、提交、推送或部署。

## 验收

1. 七项必需研究检查按固定顺序输出；
2. 真实 0 分仍计为证据可用；
3. 缺失、过期、来源失败和来源未验证保持原语义；
4. 可交易性排除、主营证伪和风险正式缺口形成明确阻断，但不把审计
   `ready` 冒充候选通过；
5. 首次否决不受输入映射顺序影响；
6. 伪造风险投影身份、时点、合同或正式标志使审计整体 `blocked`；
7. 所有正式评分、门禁、状态迁移标志保持 `false`；
8. 专项、全部龙头和隔离临时库的后端完整回归通过。
