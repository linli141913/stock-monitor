# 阶段6官方风险覆盖证明就绪合同设计

## 1. 目标

在不把 D2 关键词发现冒充正式覆盖的前提下，为“发行人上市日至今七类官方风险
覆盖证明”建立可审计、版本化、默认失败关闭的就绪合同。

本轮只实现当前有真实依据的证据结构、身份绑定、校验和失败关闭。由于项目尚无
获批的正式覆盖查询政策，任何输入都不得生成 `coverage_complete=true`，不得打开
风险过滤、正式门或状态迁移。

## 2. 已核实的现状

- 证券主档包含上市日期，但现有官方风险生命周期窗口中的上市日期仍是调用方输入，
  尚未绑定到获批的风险覆盖来源合同。
- D2 能核验候选发行人范围、七个发现关键词、分页记录守恒和查询窗口连续性。
- D8/D9及官方确定性风险链能够核验官方正文、事件版本、开放事件结转、更正和解除。
- D2 设计明确规定关键词命中数、空结果、搜索或栏目列表均不能证明上市以来完整覆盖。
- PRD、V5升级规划书、D1-D9规格与代码中没有正式覆盖查询政策的版本、审批者、
  类别范围和来源能力声明。

## 3. 方案

新增独立纯内存模块 `backend/radar/leader_risk_official_coverage.py`，不修改 D2
发现适配器，也不改变现有生命周期结果。

模块接受：

- 可信的同轮 `LeaderRuntimeCandidatePlan`；
- 由现有生产者生成且可重放验证的 `LeaderOfficialRiskLifecycleResult`；
- 每只候选的发行人上市证据合同；
- 可选的正式覆盖查询政策审批合同。

模块输出逐证券和整批就绪结果，公开以下审计事实：

- 候选、发行人和上市证据身份是否一致；
- 上市日期、来源时间、来源合同和原文身份是否结构有效；
- 现有生命周期是否完成开放事件结转、更正和解除关系；
- 是否存在代码内冻结、可信且完全匹配的正式覆盖政策审批；
- 覆盖、风险过滤、正式门和状态迁移是否允许。

## 4. 政策审批边界

定义版本化 `LeaderOfficialRiskCoveragePolicyApproval`，字段只描述审批证据，不定义
或猜测业务阈值：

- `policy_id`、`policy_version`；
- `approved_by`、`approved_at`；
- `source_contract_ids`；
- 七类风险对应的查询政策版本身份。

可信审批只允许来自代码内冻结注册表的精确对象。当前注册表为空，因为项目没有
明文批准记录。因此：

- `None`、普通字典、伪造 dataclass 或调用方自称已批准均不可信；
- 当前所有结构正常的批次返回 `policy_unapproved`；
- 将来只有在正式规则、来源能力和审批证据明确后，才能通过单独评审修改注册表和
  新增成功路径测试；调用方运行参数不能自行启门。

## 5. 发行人上市证据边界

定义 `LeaderOfficialRiskIssuerListingEvidence`，至少保存：

- `symbol`、`issuer_identity`、`issuer_listed_at`；
- `source_contract_id`、`source_name`、`source_url`；
- `fetched_at`、`record_checksum`。

合同校验证券顺序、唯一性、发行人身份、日期不晚于本轮、带时区来源时间、HTTPS
官方来源和校验和格式。结构通过只表示证据可审计，不表示其来源已获正式覆盖政策
批准；审批未通过时 `issuer_listing_evidence_approved=false`。

## 6. 状态与失败关闭

- 合同、候选计划或生命周期不可重放：`source_unverified`；
- 上市证据缺失：`missing`；
- 上市证据身份、时间、URL或校验和非法：`source_unverified`；
- 结构证据完整但正式政策不存在：`policy_unapproved`；
- D2关键词能力始终保留 `risk_official_keyword_discovery_not_coverage_proof`；
- 当前所有输出固定：
  `coverage_complete=false`、`risk_filter_passed=false`、
  `formal_gate_ready=false`、`formal_usable=false`、
  `state_transition_allowed=false`。

输出由模块内部生产者令牌保护，并提供独立验证函数，拒绝 `dataclasses.replace`
或调用方手工构造对象后篡改正式字段。

## 7. 测试

专项测试至少覆盖：

- 合法同轮生命周期和结构完整上市证据仍因政策未批准而失败关闭；
- 缺少上市证据、顺序错位、重复证券、发行人不一致；
- 上市日期晚于本轮、来源时间无时区/未来、HTTP或非官方来源、非法校验和；
- 伪造审批、字典审批和篡改输出不能通过验证；
- 生命周期错轮、非生产者对象和不完整关系不能进入覆盖合同；
- 证据输出不宣称 D2 关键词具备正式覆盖能力。

## 8. 非目标

- 不自创查询关键词、类别映射、阈值或审批记录；
- 不联网、不读取或写入 SQLite；
- 不下载正文、不调用 AI；
- 不修改生产环境变量、服务、调度、API或前端；
- 不打开阶段7/8/9，也不替代下一合法交易窗口的现场复跑。
