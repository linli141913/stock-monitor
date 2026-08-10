# 阶段6剩余合并交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 不再拆分新的阶段6小编号，在保持真实数据门禁和正式状态关闭的前提下，把现有历史、主营催化、可交易性和风险研究合同接入同一运行时提供器，并完成阶段6本地开发收口所需的真实来源验收准备。

**Architecture:** 继续复用现有候选计划、F5提供器、F6单次编排和阶段6影子运行，不新增第二次全市场行情请求。新增的组合入口只接受与当前 `candidatePlanId/radarRunId/asOf` 一致的已验证组件输入；缺失、失败、过期和未验收来源逐组件保留真实状态，绝不补0或伪造 `ready`。

**Tech Stack:** Python 3.9、dataclasses、FastAPI现有雷达模块、unittest、SQLite临时测试库。

## Global Constraints

- 唯一真实目录为 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`，保留当前未提交工作树。
- 第一版运行范围只包含上交所、深交所A股，北交所不进入候选、行业成员或覆盖率分母。
- 不新增依赖，不读取或写入生产SQLite，不修改环境变量，不停止或重启4000/8001。
- 不执行Git暂存、提交、推送、部署；版本5生产迁移和阶段6开关启用继续单独授权。
- 阶段5的20个交易日观察与本计划并行，不能被代码测试替代。
- C4C-2B真实可交易性验收只在有效A股连续交易时段执行；闭市期间不伪造结果。

---

### Task 1: 记录真实基线与合并边界

**Files:**
- Create: `docs/superpowers/plans/2026-08-08-stage6-remainder-consolidated-delivery.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: 实际Git状态、固定端口服务、阶段6代码、现有测试和真实POC结果。
- Produces: 单一剩余开发清单，以及“可立即开发/等待交易窗口/生产操作另授权”的明确边界。

- [x] 按固定顺序核对 `AGENTS.md`、`PRD.md`、V5规划书和交接文件。
- [x] 核对工作树、HEAD和4000/8001监听，不覆盖既有未提交改动。
- [x] 确认阶段6状态机、仓储、API、页面、候选计划、F1-F6和运行时骨架已存在，不重复开发。
- [x] 在本轮验收后更新唯一 `NEXT_CHAT_HANDOFF.md`，替换过时的细碎下一步。

### Task 2: 运行时已验证组件组合入口

**Files:**
- Modify: `backend/radar/leader_research_runtime_provider.py`
- Modify: `backend/tests/test_radar_leader_research_runtime_provider.py`
- Modify: `backend/tests/test_radar_runtime.py`

**Interfaces:**
- Consumes: `LeaderResearchRuntimeSourceContext`、各候选的 `LeaderHistoryFeatureInput`、`LeaderBusinessCatalystFeatureInput`、`LeaderTradabilityFeatureInput` 和 `LeaderRiskCandidateProjectionBatchResult`。
- Produces: `build_verified_leader_research_provider_input(...) -> LeaderResearchInputProviderPlanBatchInput`。

- [x] 先写失败测试，证明当前没有稳定入口把部分真实组件按候选计划组合。
- [x] 实现严格候选全集、额外证券拒绝、运行ID/时点/计划ID绑定和候选顺序保持。
- [x] 任一组件缺失时只把该组件标记缺失；畸形组件、跨证券或跨批次输入整体拒绝。
- [x] 缺少风险批次时生成覆盖全部候选的显式缺失风险批次；不得生成可用风险结论。
- [x] 通过现有F5/F6和临时SQLite运行时测试，确认部分研究输入仍只写 analysis-only 快照，正式标志保持 `false`。

### Task 3: 真实来源接入与等待项并行

**Files:**
- Modify: `backend/radar/sources/leader_history_public_poc.py`
- Modify: `backend/run_public_history_poc.py`
- Modify: `backend/radar/sources/leader_tradability_public_live_poc.py`
- Modify/Create: 与主营催化、风险官方证据生产适配直接相关的 `backend/radar/leader_*` / `backend/radar/sources/leader_*` 文件及专项测试。

**Interfaces:**
- Consumes: 已通过合同的免费历史POC、C4C公开可交易性POC、官方披露与版本化人工审核工件、D8/D9风险证据包。
- Produces: Task 2组合入口可直接消费的逐候选研究组件。

- [x] 免费历史结果只有 `resolutionStatus=ready` 且21日、点时成员和全部沪深序列完整时才提供历史输入；当前 `partial` 不接入。
- [x] 2026-08-10连续交易时段执行C4C-2B真实样本；修正静态证券身份时间边界后4只全部返回，并以东方财富逐行公开时间生成`300152 -> star_st`交叉证据。免费路径最终为`partial`：普通股未反推正常交易，停牌源无发布时间，官方全市场盘中状态仍不可用，因此不进入C3/C1。RQData继续仅作可选对照。
- [x] 主营催化只接受交易所或指定披露平台可信官方域名的结构化事实和版本化人工审核映射；公司官网等待与证券身份绑定的版本化域名名册，AI、关键词命中或新闻摘要不能生成正式关联。
- [x] 新增上交所、深交所、巨潮固定合同的官方结构化材料适配器；来源身份由平台派生，入口不接收关系或AI审核字段，只生成待人工审核输入并保留来源合同与材料版本。
- [x] 新增候选全集官方材料批处理，严格绑定候选计划并逐证券保留失败状态；只有READY条目进入只读研究输入映射。
- [x] 新增版本化人工复核单只/候选全集适配器，只接受human复核、官方材料身份、明确证据依据和有效时间；缺审核与逐只失败不丢候选，确定性复核ID可重放，READY映射可进入现有提供器，AI无入口，正式门禁固定关闭。
- [x] 免费可交易性 `field_candidate` 整批必须原始输入重放一致并再次通过C3/C1预检，才生成运行时输入；其他状态不接入。
- [x] 风险D8连续证据包通过D9/E1重算后生成运行时批次；不足两个版本逐证券保持 `missing`，且不污染其他候选。
- [x] 风险D2官方发现批次按候选全集生成压缩人工审核队列；跨关键词同公告合并、来源状态与七类查询缺口保留，发行人冲突逐证券阻断，候选外和北交所文档排除，不生成覆盖证明或D1/D8正式输入。
- [x] 新增四来源统一准入包：历史内部重放严格POC并绑定完整点时行业成员，主营只接版本化人工复核批次，可交易性从原始查询与观察重新构建运行输入，风险只接同计划投影批次；候选错位、契约改写、时间漂移或行情内容改写整批阻断，`source_failed/stale/source_unverified`逐组件保真，并一次贯通现有F5/F6。
- [x] 新增风险生命周期批次：从同计划D2原始发现页内部重放D5-D8人工版本，再交给既有D9/E1/E3和统一准入；固定七类关键词、分页与总记录守恒、开放事件身份结转、更正时序、24小时人工审核时效及失败状态保真，正式门继续关闭。
- [x] 新增风险生命周期受控交付入口：只有显式确认和冻结候选/窗口/强类型人工版本合同通过后才采集D2；七类关键词按全局请求预算轮转分页，无重试，来源失败、分页预算耗尽和跨页漂移均失败关闭。脱敏报告不输出标题、URL、正文或人工摘要，注入Fixture固定不算真实POC，所有正式门继续关闭。
- [x] 完成D2真实候选范围与分页语义复验：发行人身份由巨潮官方代码查询精确解析，多候选查询严格限定冻结候选全集；原始`totalpages`只在符合已验证向下取整语义时归一化，其他漂移及范围外文档继续失败关闭。全市场数学总页数约909的无界扫描明确不采用。
- [x] 2026-08-10用C4C同组4只沪深研究样本完成D2官方来源级预验收：4个发行人身份全部精确解析，七类有界查询全部为可解析`partial`，共4类真实空结果、3类16条待审元数据。巨潮真实空结果的`announcements=null`只在总数/页数为0且`hasMore=false`时归一化为空列表；非零或分页矛盾继续失败关闭。该批次不是真实候选计划，不冒充D8。
- [x] 2026-08-10连续交易时段在`/private/tmp`隔离库复用真实注册、行业和市场影子链，生成`ready`冻结候选计划：扫描7451项、映射5155项、候选385只；行业83个和市场聚合门禁均通过，所有正式门关闭。
- [x] 真实D2在来源请求前发现合同冲突并失败关闭：冻结候选385只超过巨潮候选范围单批上限30只，准确原因为`cninfo_query_candidate_scope_unverified`；发行人解析、七类查询和人工版本均未启动，没有裁剪、抽样、Fixture或手工补位。
- [x] 设计并实现保持同一冻结计划身份、确定性顺序、候选全集覆盖、全局请求预算和跨分片文档守恒的D2分片/合并合同；385只稳定分13片且最低91次请求，没有裁剪、抽样或合成伪批次。
- [ ] 用真实D2官方发现批次和真实人工审核工件形成至少两个连续D8版本；当前代码能力不能替代真实来源连续覆盖，不足两个版本时继续保持研究阻断。

### Task 4: 集中回归与阶段6本地收口

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Verify: `backend/radar/leader_*`、`backend/radar/sources/leader_*`、`backend/radar/runtime.py`
- Verify: `backend/tests/test_radar_leader*.py`、`backend/tests/test_radar_runtime.py`

**Interfaces:**
- Consumes: Task 2与Task 3的最终输入和运行时结果。
- Produces: 可审计的阶段6本地收口结论；生产启用仍保持独立门槛。

- [x] 在精确重定向默认生产SQLite到临时库后运行全部龙头及运行时回归。
- [x] 运行Python语法、`git diff --check`、前端TypeScript和ESLint。
- [ ] 在不改写当前4000共享`.next`的隔离条件具备后运行Next.js build；本轮无前端改动。
- [x] 只读检查4000/8001及 `/radar`、`/docs`，不重启服务。
- [x] 更新交接文件，列出已完成、真实阻塞、交易窗口验收和生产启用授权边界。
- [x] 未完成20个交易日观察、版本5生产迁移、阶段6开关和受控重载前，不宣称正式启用。
- [x] 风险生命周期受控交付专项14项、候选范围与风险链64项及临时目录隔离下全部雷达887项回归通过；语法编译和差异卫生检查通过。采集时点、上海跨日、异常脱敏、失败页计数、安全ID、候选范围、来源分页语义和跨页原始页数漂移均有失败关闭测试。
- [x] C4C-2B静态身份和东方财富ST逐行时间修正后，公开可交易性专项80项、临时SQLite隔离下全部雷达892项通过；相关语法编译和差异检查通过。
- [x] D2真实空结果契约修正后，风险来源/候选/生命周期/交付链65项、临时SQLite隔离下全部雷达893项通过。
- [x] D2真实冻结候选验收后再次运行风险来源/候选/生命周期/交付链65项，全部通过；相关9个Python文件AST语法检查通过。临时SQLite和锁已清理，生产SQLite、服务、环境、Git与部署均未触碰。
- [x] D2分片/合并合同收口后，四层专项77项、风险全链200项和临时SQLite隔离下全部雷达905项通过；原始候选顺序、计划ID、分片矩阵、跨片/跨页守恒、自动最低预算、来源失败保真和无作用域阻断均有回归测试。
