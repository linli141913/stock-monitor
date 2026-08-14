# 阶段8提醒与独立雷达AI设计

## 目标

在不改变市场、行业、ETF和龙头确定性状态的前提下，完成雷达状态变化提醒、去重、邮件偏好和独立“雷达AI解读”工程链路。当前正式状态门槛尚未开放，因此本阶段代码必须完整支持 `not_run`、`not_configured`、`evidence_insufficient`、`failed` 和 `success`，但真实运行只能在正式状态与冻结证据同时满足后调用模型。

## 已确认实施路径

采用“完整本地工程、正式调用严格门禁”：

1. 新增独立 `backend/radar/ai/`，不修改 `backend/ai_analysis.py` 的业务逻辑。
2. 新增迁移版本7代码与临时SQLite测试；本轮不应用生产迁移。
3. 新增市场、行业、ETF、龙头四类冻结输入和结构化输出合同。
4. 雷达提醒复用现有 `alert_events`、已读状态和 `alert_deliveries`，但使用雷达专用来源事件身份和独立邮件偏好。
5. 新增雷达AI只读接口、受保护手动分析接口和前端专用展示；不复用旧单股AI历史或状态。
6. 自动解释只消费正式状态变化事件；当前影子数据不得触发提醒、邮件或模型调用。

未采用的路径：直接解释当前影子快照会把未通过20个交易日、单位、ETF准入和龙头正式门禁的数据包装成正式结论；等待所有正式门槛完成后再写任何阶段8代码则会阻塞可独立验证的合同、迁移、API和UI工程。

## 冻结证据合同

统一输入 `FrozenRadarEvidencePackage` 至少包含：

- `radarRunId`、`batchId`、`asOf`、`ruleVersion`、`coverage`；
- `scopeType`（`market|sector|etf|leader`）和 `scopeId`；
- 只读 `formalState` 与可选 `formalScoreBreakdown`；
- `stateHistory`、`evidence`、`counterEvidence`、`firstRejectionReason`、`unknowns`；
- `sourceCatalog`、`dataCompleteness` 与 `formalStateEnabled=true`。

服务端按规范化JSON计算 `evidenceFingerprint`。覆盖率不足、正式状态未启用、来源目录为空、证据引用未知来源或时间身份缺失时，状态固定为 `evidence_insufficient`，模型客户端零调用。

## AI输出与越权防护

四类提示词分别版本化为 `radar-market-v1`、`radar-sector-v1`、`radar-etf-v1`、`radar-leader-v1`。结构化输出只允许：已确认事实、推断、未知项、反证、条件情景、通俗总结、来源编号和失效条件。

后端拒绝以下结果：

- 非JSON或字段类型错误；
- 引用冻结证据包之外的来源编号；
- 返回 `formalState`、`score`、`rank`、`priority`、`industryState`、`etfState` 或 `leaderState` 等越权字段；
- 把目标价、收益承诺或确定买卖结论写入结构化字段。

模型调用失败只完成独立AI任务的失败记录，不回滚规则结果、不调用旧单股AI兜底。

## 存储与复用

迁移版本7新增：

- `radar_ai_analysis_runs`：复用身份、雷达批次、对象、证据指纹、提示词和模型版本、状态、耗时、用量、错误分类；
- `radar_ai_outputs`：结构化输出与实际来源编号；
- `radar_notification_preferences`：雷达站内提醒与邮件偏好。

有效复用键为 `radarRunId + scopeType + scopeId + evidenceFingerprint + promptVersion + model`。同键成功结果直接复用；运行中或成功任务不重复计费。失败可以由新的显式任务重试，但不会冒充成功。

## 提醒与邮件

雷达状态变化先转换为强类型 `RadarStateChange`，仅当 `formalUsable=true` 且不是保持不变时才生成提醒。来源固定为 `mainline_radar`，`sourceEventId` 由对象、前后状态、正式批次和规则版本构成，因此现有唯一去重合同可复用。

优先级沿用规划：预备/P3、候选或已确认/P2、普通降级移出/P3、ETF进入/P3、行业确认/P3。影子模式只返回 `shadow_suppressed`，不写提醒、不发邮件、不触发AI。雷达邮件偏好独立于单股监测偏好；关闭邮件时仍保留站内提醒和已读记录。

## API与前端

新增接口：

- `GET /api/radar/ai/overview`
- `GET /api/radar/ai/sectors/{sector_id}`
- `GET /api/radar/ai/etfs/{etf_code}`
- `GET /api/radar/ai/leaders/{symbol}`
- `GET /api/radar/ai/history`
- `POST /api/radar/ai/analyze`
- `GET/PUT /api/radar/alerts/preferences`

所有AI响应均带 `radarRunId`、`asOf`、`analysisStatus`、证据指纹、提示词版本和模型版本。手动接口由 `RADAR_AI_MANUAL_ENABLED` 控制并走现有服务端代理Token；自动调用由 `RADAR_AI_ENABLED` 独立控制。前端只新增雷达专用组件，清楚展示五种状态，不导入 `AiAttributionTab`。

## 配置、配额与健康

新增配置只读取服务端环境：`RADAR_AI_ENABLED`、`RADAR_AI_MANUAL_ENABLED`、`RADAR_LLM_API_KEY`、`RADAR_LLM_BASE_URL`、`RADAR_LLM_MODEL`、日调用上限和日Token上限。本轮不修改生产环境变量。配置值不进入浏览器、日志或数据库。

雷达AI使用独立轻量OpenAI兼容HTTP客户端、单次超时、无无限重试、独立进程锁、独立配额和健康状态。模型或配额异常不影响雷达只读页面、规则任务、旧单股AI和现有提醒。

## 验收边界

- 全部新行为先红后绿；覆盖四类合同、来源越权、状态越权、缺配置、证据不足、模型失败、成功复用、配额和防重入。
- 覆盖提醒映射、影子抑制、状态未变化、去重、邮件关闭仍保留站内提醒。
- 迁移只在内存或临时SQLite执行，验证旧表和旧迁移校验不变。
- 前端覆盖五种AI状态和对象切换不残留旧结果。
- 完整后端、ESLint、TypeScript、Next.js build、Python编译和 `git diff --check` 全部通过。
- 本轮不迁移或读取生产SQLite，不修改生产环境变量，不重载服务，不发送真实提醒或邮件，不推送或部署。
