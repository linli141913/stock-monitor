# 阶段10连续工程收口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 完成单次真实采集协调、运营证据自动汇总和默认关闭正式执行暗骨架，使项目在非交易日继续工程推进，并在交易日只补真实样本。

**Architecture:** 三项工作保持独立文件边界。协调入口只编排现有阶段9/阶段6/ETF/台账入口并保存不可变检查点；运营汇总器只消费显式内容寻址证据并调用现有四门检查器；正式执行暗骨架只修信任与执行守卫，不创建真实executor或生产接线。

**Tech Stack:** Python 3、Pydantic 2、unittest、现有内容寻址store与APScheduler适配层。

**Spec:** `docs/superpowers/specs/2026-09-05-stage10-continuous-engineering-design.md`

## Global Constraints

- 只修改唯一真实项目并保留全部未提交工作树。
- 先测试并确认目标缺口红灯，再写最小生产实现。
- 所有新工件、锁和测试SQLite只在`/private/tmp`；不读写`backend/data/stock_monitor.db`。
- 不联网运行真实采集，不调用付费AI，不改环境变量、依赖、main.py、launchd、服务或生产开关。
- 不Git暂存、提交、推送或部署。
- 状态合同严格、版本化、拒绝额外字段、严格布尔、带时区时间和绝对路径泄漏。

---

### Task 1: 单次真实采集协调与恢复检查点

**Files:**
- Create: `backend/radar/stage10_live_collection.py`
- Create: `backend/run_radar_stage10_single_live_collection.py`
- Modify: `backend/run_radar_replay_formal_live_acceptance.py`
- Modify: `backend/tests/test_run_radar_replay_formal_live_acceptance.py`
- Create: `backend/tests/test_radar_stage10_live_collection.py`
- Create: `backend/tests/test_run_radar_stage10_single_live_collection.py`

**Interfaces:**
- `Stage10LiveCollectionResult`：`radar-stage10-single-live-collection-v1`强类型结果，固定`formalEnabled=false`。
- `run_stage10_live_collection(...) -> Stage10LiveCollectionResult`：注入阶段9runner、影子登记器、ETF runner、本地时钟和本地日历验证器。
- 阶段9成功JSON新增`formalShadowInputDirs`与`etfFormalAdmissionPath`，只来自本轮强类型结果，不扫描目录。
- 检查点采用`radar-stage10-live-collection-attempt-v1`，原子写入显式`/private/tmp`attempt根；恢复只消费已发布内容引用。

- [ ] 先写缺确认、周末、日历未知、非连续竞价测试；验证阶段9/ETF/锁/写入均零调用。
- [ ] 运行新测试并确认因模块或参数不存在而失败。
- [ ] 写阶段9透传测试：`--formal-shadow-input-root`必须进入同一次阶段6argv，并返回两模块目录和本轮ETF准入包。
- [ ] 运行并确认旧入口缺参数/字段红灯。
- [ ] 实现最小透传与目录两两隔离，不改变未指定新参数时的旧成功JSON。
- [ ] 写协调成功测试：阶段9一次、趋势一次、龙头一次、ETF一次，输出逐模块状态和固定false。
- [ ] 写独立失败测试：趋势失败仍继续龙头/ETF；阶段9失败时ETF零调用；ETF contended不回滚前段。
- [ ] 写检查点恢复测试：发布后登记失败只重放已有输入，不再次调用阶段9或ETF网络捕获。
- [ ] 写路径、符号链接、检查点换件、同attempt幂等和同日异内容测试。
- [ ] 实现强类型协调器、安全检查点和CLI；所有未知工程异常稳定失败且不泄漏路径。
- [ ] 运行Task 1全部测试与既有四个入口回归。

### Task 2: 运营证据自动汇总器

**Files:**
- Create: `backend/radar/formal_operational_evidence_collector.py`
- Create: `backend/run_radar_formal_operational_evidence_collector.py`
- Create: `backend/tests/test_radar_formal_operational_evidence_collector.py`
- Create: `backend/tests/test_run_radar_formal_operational_evidence_collector.py`
- Modify: `backend/radar/formal_operational_checks.py`
- Modify: `backend/tests/test_radar_formal_operational_checks.py`
- Modify: `backend/run_radar_formal_operational_checks.py`

**Interfaces:**
- `radar-formal-operational-evidence-input-v1`显式绑定subject、checkedAt、v2台账SHA、回执包、执行上下文、性能策略SHA、安全策略与静态资产角色。
- `collect_operational_evidence(...) -> OperationalChecksInput`从既有适配器与台账重放派生性能值，再调用`build_operational_checks`生成现有v1报告。
- `PerformancePolicy`新增可选`policyId/policySha256/evidenceScope`；自动汇总路径强制三者，原手工合同保持兼容但不能被自动汇总器当冻结生产策略。

- [ ] 写台账缺失/篡改、回执与台账身份或SHA不一致、目录链接/换件/超限测试并观察红灯。
- [ ] 写真实0、missing/failed/stale、分位数、覆盖率和锁竞争字面期望测试。
- [ ] 写无策略、策略缺字段、SHA错、reentry不可验证、rehearsal范围测试，断言性能保持collecting或failed。
- [ ] 写安全扫描白名单、排除`.env*`/backend/data/venv/node_modules/.git、输出不含匹配内容/绝对路径测试。
- [ ] 写回退和runbook静态资产哈希、缺失、链接、换件测试，断言不调用任何服务命令。
- [ ] 实现强类型清单、显式输入读取、台账/回执交叉验证、性能聚合和静态证据快照。
- [ ] 实现CLI，输入/输出/台账均限`/private/tmp`，项目资产根只读且只能为显式项目根；输出原子化。
- [ ] 运行Task 2全部测试及正式就绪/运营检查回归。

### Task 3: 默认关闭正式执行暗骨架安全修复

**Files:**
- Create: `backend/radar/formal_execution_contracts.py`
- Create: `backend/radar/formal_execution_guard.py`
- Create: `backend/tests/test_radar_formal_execution_contracts.py`
- Create: `backend/tests/test_radar_formal_execution_guard.py`
- Modify: `backend/radar/formal_readiness_service.py`
- Modify: `backend/radar/scheduler.py`
- Modify: `backend/radar/runtime.py`
- Modify: `backend/tests/test_radar_formal_readiness_service.py`
- Modify: `backend/tests/test_radar_scheduler.py`
- Modify: `backend/tests/test_radar_runtime.py`

**Interfaces:**
- `FormalExecutionBinding`：模块、固定jobId、executorId、executorContractVersion、configSha256、generatedAt/expiresAt与内容SHA绑定。
- `FormalExecutionContext`：invocationId、模块、job/executor身份、readiness引用、binding引用和九门快照。
- `FormalExecutionResult`：只允许`completed|blocked|failed`、时间、原因和候选引用，拒绝任何正式状态字段。
- 固定job ID：`radar-formal-trend-rotation`、`radar-formal-etf-observation`、`radar-formal-leader-observation`。

- [ ] 写九门全绿加输入requested/enabled仍不能生成formal_enabled的红灯测试。
- [ ] 写binding缺失/损坏/未来/过期/哈希错、模块/job/executor/config身份错测试。
- [ ] 写任意job ID、影子/AI ID冲突、同ID已有不同wrapper不可报already_registered测试。
- [ ] 写跨进程锁竞争、锁内请求撤销、锁内报告过期、executor异常释放锁测试。
- [ ] 写单模块坏规格不阻断其他模块、空spec零读取零锁零调度测试。
- [ ] 写结果合同拒绝formalEnabled/configuredEnabled/状态写入指令测试。
- [ ] 实现合同和纯守卫；正式就绪服务最多输出ready_to_enable，缺真实运行证明始终formalEnabled=false。
- [ ] 收紧scheduler/runtime：固定身份、模块独立校验、模块独立安全锁、锁内重验；不创建executor/spec或调度器。
- [ ] 运行Task 3全部测试及scheduler/runtime/formal readiness回归，静态确认main/launchd/AI零接线。

### Task 4: 整包审查、验证和状态收口

**Files:**
- Modify: `docs/阶段10正式启用运行手册.md`
- Modify: `PRD.md`
- Modify: `docs/股票监测助手V5.0升级规划书.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

- [ ] 对Task 1、Task 2、Task 3分别做独立只读代码审查；修复全部Critical/Important后复审。
- [ ] 运行阶段10聚焦回归。
- [ ] 在测试发现前把`database.DB_PATH`精确重定向并初始化到新的`/private/tmp`SQLite，运行完整后端测试。
- [ ] 运行受影响Python编译、`pip check`和`git diff --check`；新文件逐项空白检查。
- [ ] 只读核对4000/8001、Git HEAD/远端差异和工作树；不得重载或Git写。
- [ ] 更新运行手册、PRD、V5规划和唯一NEXT检查点，明确真实观察天数未因周末工程增加。
