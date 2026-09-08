# 阶段6官方风险生命周期与正式行业门收口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不读取生产 SQLite、不调用 AI、不启用生产开关的前提下，为官方风险开放事件建立可审计的跨窗口延续、更正和解除合同，并明确证明阶段6正式行业门仍缺少已批准的数值政策。

**Architecture:** 风险链以现有 D8 输入/结果和 D9 投影输入/结果的完整重放作为开放事件基线，后续窗口只接纳现有官方确定性批次中已经下载正文并重放出的事实；任何事件消失、版本变化或解除都必须具有同证券、同事件精确版本、同官方文档、同正文哈希和确定性关系事实。行业门继续复用现有可信行业范围、行业规则就绪和父池横截面证据，同时新增政策审计字段来区分“已有定性需求”“已有行业状态机批准”和“尚不存在的龙头正式行业门数值批准”。

**Tech Stack:** Python 3、冻结 dataclass、现有 `unittest`、现有 D1/D2/D8/D9/官方确定性风险合同。

**Spec:** `PRD.md`、`docs/股票监测助手V5.0升级规划书.md` 第7.4、9.5、9.7、9.8和阶段6章节，以及现有 D2/D8/D9 设计文档。

## Global Constraints

- 唯一工作树为 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`，不创建副本或 worktree。
- 不读取或写入 `backend/data/stock_monitor.db`；完整测试从解释器启动前重定向到 `/private/tmp` 新库并安装生产路径拦截守卫。
- 不调用付费 AI，不修改生产环境变量、服务、雷达开关、依赖或部署。
- 不执行 `git add`、`git commit`、`git push`；本计划中的每个任务以测试通过和 diff 复核结束。
- D2 发现、标题、AI、空模板、人工首版复制或推断均不得生成开放事件或更正/解除结论。
- 任一正文、哈希、事实、事件版本、窗口或审批来源不齐时保持失败关闭。

---

### Task 1: 官方风险跨窗口生命周期合同

**Files:**
- Create: `backend/radar/leader_risk_official_lifecycle.py`
- Create: `backend/tests/test_radar_leader_risk_official_lifecycle.py`
- Modify: `backend/radar/leader_risk_official_deterministic.py`

**Interfaces:**
- Consumes: `RiskResearchEvidenceBundleInput` 与对应 D8 结果、`LeaderRiskCandidateProjectionInput` 与对应 D9/E1 投影结果、`LeaderOfficialDeterministicRiskBatchResult`、`OfficialRiskDocumentFactResult`、D1 事件和解除对象。
- Produces: `build_leader_official_risk_lifecycle(...) -> LeaderOfficialRiskLifecycleResult`，逐证券输出覆盖连续性、开放事件结转、更正链、解除链、当前活跃风险和 `risk_filter_passed`；该结果本身不允许状态迁移。

- [x] **Step 1: 写失败测试证明当前官方确定性批次无法表达合法跨窗口结转**

  测试构造两个严格递增、查询窗口连续的可信官方批次，并提供一个由真实 D8 输入/结果与 D9 投影输入/结果重放得到的开放事件；期望新的生命周期构建器存在，且同一开放事件在第二窗口原样保留时标记结转完整。

- [x] **Step 2: 运行风险生命周期专项并确认因缺少构建器失败**

  Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_leader_risk_official_lifecycle -v`

- [x] **Step 3: 实现最小可信基线和窗口顺序校验**

  D8 必须由 `build_risk_research_evidence_bundle()` 重放相等，D9/E1 必须由 `build_leader_risk_candidate_projection()` 重放相等；窗口批次必须保留生产者身份、严格递增、候选证券和发行人一致、日期窗口无缺口且首窗覆盖发行人上市时点。

- [x] **Step 4: 写失败测试覆盖静默丢事件、伪造 D8、断窗、错发行人与只含 D2 元数据**

  每个用例断言稳定失败原因，且 `risk_filter_passed/formal_usable/state_transition_allowed` 全为假。

- [x] **Step 5: 实现开放事件延续失败关闭**

  前窗仍开放的事件在后窗必须原样存在，或由同一事件的精确更正版本替代，或由精确解除证据关闭；仅 D2 文档身份或正文缺失不能改变事件。

- [x] **Step 6: 写失败测试覆盖无正文、正文哈希错配、无原公告事实、反向/未来更正、错事件版本解除**

  期望分别返回正文未验证、事实绑定未验证、关系目标未验证、时间顺序未验证或事件版本未验证原因。

- [x] **Step 7: 实现官方正文与确定性事实绑定**

  更正和解除文档必须存在于对应官方确定性投影，正文 SHA-256 与 D3 事实结果相等；`REFERENCED_DOCUMENT_ID` 必须精确指向目标事件文档，调查/诉讼继续核对案号，业绩/审计继续核对报告期，且更正的新版本不得倒退或引用未来事实。

- [x] **Step 8: 写并通过成功测试覆盖持续开放、合法更正、合法解除和仍有活跃风险四条路径**

  结转、更正、解除和 D1 身份可以经官方正文与事实链审计；但 D2 关键词查询不是从上市日至今的正式覆盖证明，因此本合同仍保持 `risk_filter_passed=false`、`formal_usable=false`和 `state_transition_allowed=false`。

### Task 2: 风险生命周期审计字段接入现有研究证据链

**Files:**
- Modify: `backend/radar/leader_risk_official_deterministic.py`
- Test: `backend/tests/test_radar_leader_risk_official_lifecycle.py`
- Test: 研究审计、运行时批次、提供器和运行时输入的已有专项测试。

**Interfaces:**
- Consumes: Task 1 生产者绑定的生命周期结果。
- Produces: 现有官方投影中生产者绑定的结转、更正和解除完整度字段；现有研究运行时校验器可读取该投影，同时保持 `riskFilterPassed/formalGateReady/formalUsable/appliedToD1/stateTransitionAllowed=false`。

- [x] **Step 1: 写失败集成测试证明当前官方投影无法表达生命周期完整度**

- [x] **Step 2: 运行集成测试并确认生命周期字段缺失，而正式标志依法必须继续为 false**

- [x] **Step 3: 最小修改校验器和证据序列化**

  只对 Task 1 生产者身份与完整合同校验通过的官方生命周期投影接纳完整度字段；D8/D9 研究投影、普通官方当前窗口投影和生命周期投影的所有正式标志仍要求关闭。

- [x] **Step 4: 运行风险、研究审计、运行时输入和单轮编排相关测试**

### Task 3: 正式行业门政策缺口与审批来源审计

**Files:**
- Modify: `backend/radar/leader_formal_industry_gate.py`
- Modify: `backend/tests/test_radar_leader_formal_industry_gate.py`

**Interfaces:**
- Consumes: 当前可信行业范围、行业规则就绪结果、父池横截面证据，以及可选的政策/批准对象。
- Produces: 固定六项定性需求、数值政策必需字段、审批来源类型和明确缺口；在没有龙头行业门专属版本化政策与可信批准时保持 `leader_formal_industry_gate_policy_unapproved`。

- [x] **Step 1: 写失败测试证明现有行业状态阈值批准不能冒充龙头正式行业门批准**

- [x] **Step 2: 运行行业门专项并确认旧结果没有政策来源与错绑保护**

- [x] **Step 3: 实现最小政策审计合同**

  记录六项明文条件和缺少的统计窗口、最小样本、成交额合格阈值、扩散/持续/回流判定、催化可信来源、完整度阈值、进入/保持/退出、缺失/过期/冲突策略及批准身份；现有 `SectorThresholdApprovalEvidence` 只能证明行业状态规则就绪，不能填充这些缺口。

- [x] **Step 4: 写并通过缺审批、错合同、错规则版本、错父子计划和证据缺失测试**

- [x] **Step 5: 保持正式行业门关闭并输出可审计缺口**

### Task 4: 全量验证与唯一交接更新

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: 本轮真实 diff、测试、编译、依赖和只读服务/Git 状态。
- Produces: 顶部最新阶段6检查点。

- [x] **Step 1: 运行全部相关专项测试**

- [x] **Step 2: 从解释器启动前隔离生产 SQLite 并运行完整后端测试**

- [x] **Step 3: 运行 Python 全量编译、`pip check` 和 `git diff --check`**

- [x] **Step 4: 只读核对 `git status`、HEAD/远端差异、`4000/8001` 和 `launchd validate/status/preflight`**

- [x] **Step 5: 更新 `NEXT_CHAT_HANDOFF.md` 顶部并复核最终 diff**
