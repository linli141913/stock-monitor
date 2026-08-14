# 阶段8提醒与独立雷达AI实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成正式状态门禁后的雷达状态变化提醒、邮件偏好和与旧单股AI完全隔离的四类雷达AI解释链路。

**Architecture:** 冻结证据合同和校验器位于独立 `backend/radar/ai/`，迁移版本7保存独立任务与输出；提醒只消费正式状态变化并复用现有提醒事件、已读和送达记录。当前影子数据通过严格门禁返回证据不足或影子抑制，绝不调用模型。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、标准库HTTP客户端、Next.js 16、React 19、TypeScript、CSS Modules、`unittest`。

## Global Constraints

- 只操作 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`，保留现有本地提交和未推送历史。
- 不新增依赖，不修改 `backend/ai_analysis.py` 的雷达业务，不复用旧AI表、缓存、锁、配置或前端状态。
- 不读取、迁移或写入生产SQLite；数据库测试只用内存或临时文件。
- 不修改生产环境变量，不重载4000/8001，不发送真实提醒或邮件，不推送或部署。
- AI只能解释正式状态和冻结证据，不能改变状态、分数、排名、优先级或证据等级。
- 每项生产行为先写失败测试并确认按预期失败，再做最小实现。

---

### Task 1: 冻结证据、输出合同与严格校验

**Files:**
- Create: `backend/radar/ai/__init__.py`
- Create: `backend/radar/ai/contracts.py`
- Create: `backend/radar/ai/prompts.py`
- Create: `backend/radar/ai/validator.py`
- Test: `backend/tests/test_radar_ai_contracts.py`

**Interfaces:**
- Produces: `FrozenRadarEvidencePackage`、`RadarAiStructuredOutput`、`validate_evidence_package()`、`validate_model_output()`、`prompt_version_for_scope()`。

- [ ] 写失败测试，覆盖四类提示词、指纹稳定、正式状态关闭、覆盖率不足、未知来源编号和越权字段。
- [ ] 运行 `venv/bin/python -m unittest -q tests.test_radar_ai_contracts`，确认因模块缺失或行为缺失失败。
- [ ] 实现最小Pydantic合同、规范化指纹、提示词注册和严格校验。
- [ ] 重跑专项测试并确认通过。

### Task 2: 独立配置、客户端、服务和配额门禁

**Files:**
- Create: `backend/radar/ai/config.py`
- Create: `backend/radar/ai/client.py`
- Create: `backend/radar/ai/service.py`
- Test: `backend/tests/test_radar_ai_service.py`

**Interfaces:**
- Consumes: Task 1合同与校验器。
- Produces: `RadarAiSettings`、`RadarAiService.analyze()`、`RadarAiAnalysisResult`；客户端通过依赖注入，测试不访问外部网络。

- [ ] 写失败测试，覆盖关闭、未配置、证据不足、模型失败、成功、配额耗尽和同复用键零重复调用。
- [ ] 运行专项测试并确认预期失败。
- [ ] 实现默认关闭配置、轻量客户端边界、进程锁、日配额和失败分类。
- [ ] 重跑专项测试并确认通过。

### Task 3: 迁移版本7与AI专用仓储

**Files:**
- Modify: `backend/radar/migrations.py`
- Create: `backend/radar/ai/repository.py`
- Test: `backend/tests/test_radar_ai_migration.py`
- Test: `backend/tests/test_radar_ai_repository.py`

**Interfaces:**
- Produces: `STAGE8_RADAR_MIGRATIONS`、版本7必需对象、`RadarAiRepository` 的创建任务、完成任务、按复用键读取、对象最新结果和历史分页方法。

- [ ] 写失败迁移测试，断言版本1至6名称和SHA-256不变，版本7三表/索引/约束存在且幂等。
- [ ] 写失败仓储测试，覆盖成功复用、失败不冒充成功、不同指纹/提示词/模型隔离和临时库重开。
- [ ] 运行两个专项测试并确认预期失败。
- [ ] 追加版本7迁移和最小仓储实现，不改旧迁移文本。
- [ ] 重跑两个专项测试及既有迁移测试。

### Task 4: 正式状态变化提醒与邮件偏好

**Files:**
- Create: `backend/radar/notifications.py`
- Modify: `backend/notification_service.py`
- Modify: `backend/alert_repository.py`
- Test: `backend/tests/test_radar_notifications.py`
- Test: `backend/tests/test_alert_system.py`

**Interfaces:**
- Produces: `RadarStateChange`、`process_radar_state_change()`；`process_new_alert(alert, allow_email=True)` 保持旧调用兼容。

- [ ] 写失败测试，覆盖四类事件映射、保持状态零提醒、非正式/影子抑制、来源事件去重和邮件关闭仍写站内送达。
- [ ] 运行专项测试并确认预期失败。
- [ ] 实现状态变化映射，并给既有通知入口增加默认兼容的邮件许可参数。
- [ ] 重跑雷达提醒和完整既有提醒测试。

### Task 5: AI API、手动保护和健康状态

**Files:**
- Create: `backend/radar/ai/api.py`
- Modify: `backend/main.py`
- Modify: `backend/radar/api_contracts.py`
- Modify: `backend/radar/runtime.py`
- Test: `backend/tests/test_radar_ai_api.py`
- Test: `backend/tests/test_radar_runtime.py`

**Interfaces:**
- Produces: `/api/radar/ai/*`、`/api/radar/alerts/preferences`；手动POST在功能关闭时返回403，缺迁移/结果时返回稳定未运行状态。

- [ ] 写失败API测试，覆盖五种状态、四类对象、非法代码、历史隔离、手动开关关闭、Token保护和无生产库写入。
- [ ] 运行专项测试并确认预期失败。
- [ ] 实现独立路由、响应模型、仓储装配和默认关闭的自动任务注册边界。
- [ ] 重跑API、运行时和既有雷达API测试。

### Task 6: 雷达专用前端组件与提醒偏好

**Files:**
- Create: `stock-monitor/src/types/radar-ai.ts`
- Create: `stock-monitor/src/components/radar/RadarAiInsight.tsx`
- Modify: `stock-monitor/src/components/radar/Radar.module.css`
- Modify: `stock-monitor/src/app/radar/page.tsx`
- Modify: `stock-monitor/src/components/alert/AlertSettingCard.tsx`
- Test: use existing static/type/build verification because this repository has no configured component test runner.

**Interfaces:**
- Produces: 五种雷达AI状态展示、按当前雷达tab/object请求且使用AbortController防止对象切换残留；雷达邮件偏好读写服务端代理。

- [ ] 先在类型引用和页面中写出预期合同，运行 TypeScript 确认因类型/组件缺失失败。
- [ ] 实现类型、专用组件、页面接入和邮件偏好控件，不导入旧 `AiAttributionTab`。
- [ ] 运行相关ESLint和TypeScript，修复至通过。

### Task 7: 全量验证、交接和本地提交

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Produces: 可复核的阶段8本地工程检查点；明确生产迁移、真实正式证据与运行启用仍未执行。

- [ ] 运行所有阶段8专项、全部雷达测试和完整后端测试。
- [ ] 运行前端ESLint、TypeScript、Next.js build、Python编译和 `git diff --check`。
- [ ] 只读核对Git与4000/8001监听进程，不读取生产SQLite、不重载服务。
- [ ] 更新唯一 `NEXT_CHAT_HANDOFF.md`，记录准确结果与下一步生产门槛。
- [ ] 检查暂存范围不含 `.env`、数据库、日志、构建产物或无关文件；用户已授权本地提交本阶段修复，不推送。
