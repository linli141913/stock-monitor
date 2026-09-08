# 阶段10双轨正式就绪控制面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段9真实样本继续按交易日积累的同时，完成阶段10分模块正式就绪控制面、只读页面、离线验收、运维状态修复和使用手册，且不实际开启任何生产模块。

**Architecture:** 新增独立的强类型证据合同、影子交易日台账、内容寻址仓和纯判定服务；API与前端只展示服务端判定，不在浏览器拼门。趋势轮动、ETF观察和龙头观察分别准入，生产请求与自动证据门双重满足后才允许后续正式启用。

**Tech Stack:** Python 3.9、FastAPI、Pydantic 2、unittest、Next.js 16.2.10、React 19.2.4、TypeScript、CSS Modules、zsh/launchd。

**Spec:** `docs/superpowers/specs/2026-09-04-stage10-parallel-formal-readiness-design.md`

## Global Constraints

- 只操作`/Volumes/HermesSSD/AntigravityData/量化监测-股票`并保留全部既有未提交改动。
- 不读取或写入生产SQLite；测试在导入前把生产路径精确重定向到`/private/tmp`。
- 不修改生产环境变量、已安装LaunchAgent、功能开关、服务进程或部署。
- 不新增数据库迁移、第三方依赖或Mock生产返回值。
- 阶段9未成熟和真实观察日不足必须显示`collecting/not_ready`，不能写成已通过。
- ETF维持20日或60日真实时点历史覆盖加连续5个现场交易日；趋势轮动和三级龙头首版各要求20个不同有效A股交易日。
- 不把任何模块通过结果替代其他模块，不让AI改变状态、分数、排名或优先级。
- 本轮不执行`git add/commit/push`；用户后续明确授权时再整体提交。

---

### Task 1: 正式就绪合同与分模块影子台账

**Files:**
- Create: `backend/radar/formal_readiness_contracts.py`
- Create: `backend/radar/formal_shadow_ledger.py`
- Test: `backend/tests/test_radar_formal_readiness_contracts.py`
- Test: `backend/tests/test_radar_formal_shadow_ledger.py`

**Interfaces:**
- Produces: `FormalModuleName = Literal["trendRotation", "etfObservation", "leaderObservation"]`。
- Produces: `FormalReadinessState = Literal["collecting", "not_ready", "ready_to_enable", "failed", "formal_enabled"]`。
- Produces: `RadarFormalReadiness`, `FormalModuleReadiness`, `FormalEvidenceRef`, `FormalGateState`, `FormalShadowObservation`, `FormalShadowLedger` Pydantic强类型。
- Produces: `build_shadow_ledger(observations, *, calendar_provider) -> FormalShadowLedger`。

- [ ] **Step 1: 写合同失败测试**

覆盖非法模块、未来`sourceTime/fetchedAt`、时间倒置、非64位SHA、重复模块、总体开关与逐模块开关冲突、`formal_enabled`但门未齐等分支。

- [ ] **Step 2: 运行合同测试确认红灯**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_contracts`

Expected: `ModuleNotFoundError: radar.formal_readiness_contracts`。

- [ ] **Step 3: 实现最小强类型合同**

合同必须使用`extra="forbid"`和别名输出，明确区分`anyFormalEnabled`与`allModulesFormalEnabled`；任何已正式启用模块都必须同时满足`requested=true`和全部必要门为`ready`。

- [ ] **Step 4: 写台账失败测试**

覆盖同日去重、非交易日不计数、未知日失败、跨模块不互相计数、ETF要求5日、趋势/龙头要求20日、缺失/失败日不计入ready、真实0耗时合法。

- [ ] **Step 5: 运行台账测试确认红灯**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_shadow_ledger`

Expected: `ModuleNotFoundError: radar.formal_shadow_ledger`。

- [ ] **Step 6: 实现台账纯函数并运行两模块测试**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_contracts tests.test_radar_formal_shadow_ledger`

Expected: all tests `OK`。

---

### Task 2: 内容寻址仓与纯正式就绪判定

**Files:**
- Create: `backend/radar/formal_readiness_store.py`
- Create: `backend/radar/formal_readiness_service.py`
- Test: `backend/tests/test_radar_formal_readiness_store.py`
- Test: `backend/tests/test_radar_formal_readiness_service.py`

**Interfaces:**
- Consumes: Task 1全部合同和`FormalShadowLedger`。
- Produces: `save_formal_readiness(report: RadarFormalReadiness, root: Path) -> StoredFormalReadinessRef`。
- Produces: `load_latest_formal_readiness(root: Path) -> FormalReadinessLoadResult`，状态为`available/missing/failed`。
- Produces: `build_formal_readiness(inputs: FormalReadinessInputs) -> RadarFormalReadiness`。

- [ ] **Step 1: 写仓储失败测试**

覆盖原子保存、相对状态路径、SHA重算、`latest.json`身份/时间错配、内容篡改、绝对路径拒绝、缺失与损坏语义；所有目录使用`TemporaryDirectory`。

- [ ] **Step 2: 运行仓储测试确认红灯**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_store`

- [ ] **Step 3: 实现内容寻址仓并跑绿**

状态文件名使用报告规范JSON的SHA-256，先写临时文件再`os.replace`；加载时同时重放外层清单和内层合同。

- [ ] **Step 4: 写判定服务失败测试**

覆盖阶段9缺失/failed/not_ready、分域标签不足、影子天数不足、规则/校准/性能/安全/回退/手册任一缺失、模块独立ready、请求开关关闭、请求开启但证据不足、模块撤销、证据时间晚于`checkedAt`。

- [ ] **Step 5: 实现纯判定服务并跑相关测试**

`ready_to_enable`只表示自动证据满足；`formal_enabled`还要求调用方传入真实有效开关。总体状态不覆盖逐模块原因。

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_contracts tests.test_radar_formal_shadow_ledger tests.test_radar_formal_readiness_store tests.test_radar_formal_readiness_service`

Expected: all tests `OK`。

---

### Task 3: 后端只读API与零正式任务安全边界

**Files:**
- Modify: `backend/radar/api_contracts.py`
- Modify: `backend/radar/api.py`
- Modify: `backend/radar/config.py`
- Modify: `backend/radar/runtime.py`
- Modify: `backend/radar/scheduler.py`
- Create: `backend/run_radar_formal_readiness.py`
- Create: `backend/tests/test_radar_formal_readiness_api.py`
- Create: `backend/tests/test_run_radar_formal_readiness.py`
- Modify: `backend/tests/test_radar_runtime.py`
- Modify: `backend/tests/test_radar_scheduler.py`

**Interfaces:**
- Consumes: Task 2的`load_latest_formal_readiness`和`RadarFormalReadiness`。
- Produces: `GET /api/radar/formal-readiness`，响应`radar-formal-readiness-v1`。
- Produces: `RadarSettings.formal_trend_requested/formal_etf_requested/formal_leader_requested`，默认全部`False`。
- Produces: CLI `run_radar_formal_readiness.py --input-dir ... --output-dir ...`，只操作显式目录。

- [ ] **Step 1: 写API失败测试**

覆盖无报告返回`not_ready`、损坏返回`failed`、正常报告逐字段透传、`Cache-Control: no-store`、响应不含绝对路径和Token、旧雷达API合同不变。

- [ ] **Step 2: 运行API测试确认红灯**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_api`

- [ ] **Step 3: 实现API合同和依赖注入加载器**

API只读文件仓，不访问SQLite；默认根目录缺失时稳定返回`collecting/not_ready`，不能抛500或构造ready。

- [ ] **Step 4: 写配置与运行时红灯测试**

覆盖三个请求开关默认false、非法布尔拒绝、未加载ready证据时零正式job、仅单模块ready且请求开启时只允许该模块、证据撤销后不执行、影子任务继续独立。

- [ ] **Step 5: 实现默认关闭请求字段和正式任务守卫**

本轮只实现守卫与“零任务”安全路径，不新增生产plist，不修改现有环境变量；正式执行函数仍由后续获授权阶段接入。

- [ ] **Step 6: 写并实现离线CLI测试**

CLI缺显式目录或目录重叠时拒绝；只读输入、写显式`/private/tmp`输出，不联网、不访问数据库、不改变开关。

- [ ] **Step 7: 运行后端控制面联合测试**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_api tests.test_run_radar_formal_readiness tests.test_radar_runtime tests.test_radar_scheduler`

Expected: all tests `OK`。

---

### Task 4: 阶段10前端只读面板与阶段9语义修正

**Files:**
- Modify: `stock-monitor/src/types/radar.ts`
- Create: `stock-monitor/src/components/radar/RadarStage10ReadinessPanel.tsx`
- Modify: `stock-monitor/src/components/radar/RadarReplayQualityPanel.tsx`
- Modify: `stock-monitor/src/app/radar/page.tsx`
- Create: `stock-monitor/tests/radar-formal-readiness-contract.ts`

**Interfaces:**
- Consumes: Task 3的`GET /api/radar/formal-readiness`合同。
- Produces: `RadarFormalReadinessResponse` TypeScript类型。
- Produces: `RadarStage10ReadinessPanel({data, loading})`。

- [ ] **Step 1: 增加TypeScript合同Fixture**

Fixture覆盖`collecting`、`ready_to_enable`、单模块`formal_enabled`和`failed`；用`satisfies RadarFormalReadinessResponse`锁定字段与枚举。

- [ ] **Step 2: 运行TypeScript确认组件缺失红灯**

Run: `cd stock-monitor && npx tsc --noEmit`

- [ ] **Step 3: 实现只读面板**

复用`ModuleStatePanel`和现有CSS；显示三个模块的观察天数、各门状态、首个原因和实际开关。不提供按钮，不把`ready_to_enable`写成“已开启”。

- [ ] **Step 4: 接入页面请求与清旧状态**

请求使用`cache: 'no-store'`和时间戳；切换历史标签触发加载，请求失败保留明确失败态但不展示上一轮正式状态。

- [ ] **Step 5: 修正阶段9卡头语义**

只有`stage9QualityGatePassed`映射质量可用；`engineeringState=complete`且验证积累中时明确展示“工程链完成、真实验证观察中”。

- [ ] **Step 6: 运行前端完整静态验证**

Run: `cd stock-monitor && npx tsc --noEmit`

Run: `cd stock-monitor && npm run lint`

Run: `cd stock-monitor && npm run build`

Expected: three commands exit 0。

---

### Task 5: 运维状态交叉验证和离线验收合同

**Files:**
- Modify: `ops/launchd/manage.sh`
- Modify: `backend/tests/test_launchd_assets.py`
- Create: `backend/radar/formal_operational_checks.py`
- Create: `backend/run_radar_formal_operational_checks.py`
- Create: `backend/tests/test_radar_formal_operational_checks.py`
- Modify: `backend/radar/formal_readiness_service.py`
- Modify: `backend/tests/test_radar_formal_readiness_service.py`

**Interfaces:**
- Produces: `resolve_ngrok_runtime_status(launchd_loaded, pid_visible, port_listening) -> status/reasons`对应的可测试判定语义。
- Produces: `radar-formal-operational-checks-v1`，包含performance/security/rollback/manual四类状态与证据摘要。
- Consumes: Task 2的`FormalReadinessInputs`与`build_formal_readiness`；运营检查包作为四个基础门的唯一版本化输入，缺失或损坏继续失败关闭。

- [ ] **Step 1: 写ngrok状态失败测试**

覆盖LaunchAgent有PID但`pgrep`不可见、4040监听但身份未知、三信号一致运行、三信号均无、信号冲突输出degraded；测试不得启停服务。

- [ ] **Step 2: 修改`manage.sh status`并运行资产测试**

只改只读状态判断，不改变install/reload/enable/disable行为。

Run: `cd backend && ./venv/bin/python -m unittest tests.test_launchd_assets`

- [ ] **Step 3: 写离线运营检查合同红灯测试**

覆盖耗时大于调度间隔、任务重入、敏感路径命中、管理接口非默认关闭、AI越权、回退资产缺失、手册哈希缺失和全部检查通过。

- [ ] **Step 4: 实现纯检查器、显式目录CLI并接入正式就绪服务**

性能阈值只从版本化输入读取；未提供阈值返回`collecting`，不得自行生成通过值。安全扫描只输出相对文件和稳定原因，不输出秘密内容。`build_formal_readiness`只接受通过强类型与SHA校验的运营检查包来设置performance/security/rollback/manual四门，不接受调用方散装布尔值。

- [ ] **Step 5: 运行运维相关联合测试**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_operational_checks tests.test_launchd_assets tests.test_external_health_monitor tests.test_backup_database`

Expected: all tests `OK`。

---

### Task 6: 运行手册、用户手册与阶段收口

**Files:**
- Create: `docs/阶段10正式启用运行手册.md`
- Create: `docs/用户使用手册.md`
- Modify: `README.md`
- Modify: `PRD.md`
- Modify: `docs/股票监测助手V5.0升级规划书.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: Task 1—5的实际合同、命令和验证结果。
- Produces: 操作者可执行的只读检查/授权/停止/回退说明，以及用户可理解的状态和数据语义。

- [ ] **Step 1: 编写运行手册**

明确每条命令的只读或变更属性、证据目录、必须授权项、失败停止条件、单模块启用与撤销、服务和数据库回退边界；不得写入未实现命令。

- [ ] **Step 2: 编写用户手册**

解释影子、观察中、可启用、正式开启、过期、失败和缺失；说明不是自动交易、不是收益承诺，用户不审批股票。

- [ ] **Step 3: 更新路线与唯一检查点**

PRD和规划书记录双轨语义、分模块门和真实剩余时间；`NEXT_CHAT_HANDOFF.md`顶部只记录本批结果、验证、服务/Git状态和下一步最多3项。

- [ ] **Step 4: 运行相关后端测试**

Run: `cd backend && ./venv/bin/python -m unittest tests.test_radar_formal_readiness_contracts tests.test_radar_formal_shadow_ledger tests.test_radar_formal_readiness_store tests.test_radar_formal_readiness_service tests.test_radar_formal_readiness_api tests.test_run_radar_formal_readiness tests.test_radar_formal_operational_checks tests.test_launchd_assets`

- [ ] **Step 5: 运行精确隔离生产SQLite的完整后端测试**

在导入`database`前将生产绝对路径的`sqlite3.connect`精确重定向到自动清理的`/private/tmp`临时库，执行`unittest discover -s tests`；输出护栏命中数并要求退出码0。

- [ ] **Step 6: 运行全量编译和差异卫生检查**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/codex-stage10-pycache backend/venv/bin/python -m compileall -q backend`

Run: `cd backend && ./venv/bin/python -m pip check`

Run: `git diff --check`

- [ ] **Step 7: 复核服务与Git但不改变它们**

只读确认4000/8001监听PID、当前HEAD/上游差异、工作树文件数及正式活动状态；不得重载、暂存、提交或推送。
