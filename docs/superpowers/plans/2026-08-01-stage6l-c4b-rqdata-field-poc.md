# 阶段6L-C4B RQData字段级POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不保存凭证、不接生产链路的RQData HTTP字段级POC适配器和脱敏准入报告。

**Architecture:** 新增单一隔离来源模块，传输函数由调用方注入，模块只构造固定方法请求、解析有界CSV响应、审计同一证券集合和交易日并输出冻结脱敏报告。C4B-2再增加不落盘的单次命令入口；真实网络调用与Fixture解析共用同一合同，但账号申请、临时Token获取和有效交易窗口真实调用仍单独执行。

**Tech Stack:** Python 3标准库`csv`、`dataclasses`、`datetime`、`enum`、`hashlib`、`json`、`urllib`，现有`unittest`。

## Global Constraints

- 不安装RQData SDK，不新增依赖，不修改环境变量或LaunchAgent。
- 不读取`.env`，不持久化用户名、密码、Token、许可证或供应商响应正文。
- 不修改C3、C4A、运行时、仓储、调度、API、页面或生产配置。
- 不读取或写入生产SQLite，不停止、重启或重载4000/8001。
- 不执行Git暂存、提交、推送、部署或真实RQData网络调用。
- 所有正式评分、门禁、可用性和状态迁移标志固定为`false`。

---

### Task 1: 冻结POC请求与脱敏结果合同

**Files:**
- Create: `backend/radar/sources/leader_tradability_rqdata_poc.py`
- Create: `backend/tests/test_radar_leader_tradability_rqdata_poc.py`

**Interfaces:**
- Consumes: 带时区`as_of`、交易日、唯一沪深证券集合和调用方注入的`RqdataPocTransport`。
- Produces: `RqdataTradabilityPocQuery`、`RqdataTradabilityPocReport`、`RqdataPocStatus`及`run_rqdata_tradability_poc(query, fetched_at, transport)`。

- [x] **Step 1: 写合同失败测试**

测试导入尚不存在的新合同，并断言：对象冻结、`repr`隐藏记录与凭证、`to_evidence()`没有响应正文、所有正式标志为假、`not_run`与成功状态分开。

- [x] **Step 2: 运行专项测试确认RED**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_rqdata_poc -v`

Expected: `ModuleNotFoundError`指向`radar.sources.leader_tradability_rqdata_poc`。

- [x] **Step 3: 实现最小冻结合同**

定义固定合同ID、HTTP端点、五个方法白名单、状态枚举、查询/调用摘要/字段结果/报告dataclass。报告证据只输出合同、状态、证券数量、方法状态、字段覆盖、响应摘要和稳定原因码。

- [x] **Step 4: 运行专项测试确认GREEN**

Run同Step 2，Expected: 合同测试通过。

### Task 2: 固定请求、CSV解析与字段矩阵

**Files:**
- Modify: `backend/radar/sources/leader_tradability_rqdata_poc.py`
- Modify: `backend/tests/test_radar_leader_tradability_rqdata_poc.py`

**Interfaces:**
- Consumes: 传输函数返回的UTF-8 CSV文本，不接受调用方自定义URL或方法。
- Produces: 五类方法的有序请求、响应SHA-256、逐字段覆盖和`field_candidate/partial/blocked`结果；两个单证券判断接口按证券有界调用。

- [x] **Step 1: 写请求与解析失败测试**

覆盖批量方法各调用一次、`is_suspended`和`is_st_stock`按证券各调用一次、固定HTTPS端点、`adjust_type=none`、完整普通/ST/停牌样本、真实0、空CSV、缺字段、重复/额外证券、混合交易日、非法代码、未来快照和非交易窗口动态证据。

- [x] **Step 2: 运行专项测试确认RED**

Run同Task 1 Step 2，Expected: 缺少请求编排或解析行为导致断言失败。

- [x] **Step 3: 实现最小请求与审计**

按固定顺序调用`instruments`、`get_price`、`is_suspended`、`is_st_stock`；仅当查询允许动态快照时调用`current_snapshot`。使用`csv.DictReader`解析，拒绝BOM之外的非UTF-8、重复列、未知证券、错误交易日、缺失必需列和非有限价格；响应正文只在函数局部存在，报告仅保存`sha256:`摘要。

- [x] **Step 4: 运行专项测试确认GREEN**

Run同Task 1 Step 2，Expected: 全部专项测试通过。

### Task 3: 标准库HTTP传输与安全失败语义

**Files:**
- Modify: `backend/radar/sources/leader_tradability_rqdata_poc.py`
- Modify: `backend/tests/test_radar_leader_tradability_rqdata_poc.py`

**Interfaces:**
- Consumes: 调用方显式传入的Token、固定方法和固定请求体。
- Produces: `build_rqdata_http_transport(token, opener=None)`返回不可打印Token的传输闭包；连接、认证、权限、配额、限流、超时和格式错误转换为稳定脱敏原因。

- [x] **Step 1: 写安全传输失败测试**

覆盖空Token、固定`https://rqdata.ricequant.com/api`、请求头Token不进入异常/`repr`、20秒超时、HTTP 401/403/429/5xx、网络超时和非法响应；所有失败不重试。

- [x] **Step 2: 运行专项测试确认RED**

Run同Task 1 Step 2，Expected: 传输构造器或稳定错误分类尚不存在。

- [x] **Step 3: 实现标准库传输**

使用`urllib.request.Request`和注入`opener`发出JSON POST；URL、方法和超时不可由业务输入覆盖。捕获`HTTPError`、`URLError`、`TimeoutError`和解码错误，抛出不包含上游正文、Token或请求体的内部稳定异常供报告层归类。

- [x] **Step 4: 运行专项测试确认GREEN**

Run同Task 1 Step 2，Expected: 全部专项测试通过；批量方法每批最多一次，单证券方法每个“方法+证券”组合最多一次。

### Task 4: 回归、差异与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: C4B-1实现和测试证据。
- Produces: 唯一交接中的本地完成状态、真实POC未运行状态和下一步账号边界。

- [x] **Step 1: 运行语法与专项测试**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m py_compile radar/sources/leader_tradability_rqdata_poc.py tests/test_radar_leader_tradability_rqdata_poc.py`

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_rqdata_poc tests.test_radar_leader_tradability_provider_delivery -v`

- [x] **Step 2: 运行全部三级龙头测试**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest discover -s tests -p 'test_radar_leader*.py'`

- [x] **Step 3: 使用临时SQLite拦截运行完整后端测试**

在导入`database`前拦截默认生产SQLite路径并重定向到临时目录，测试完成后销毁临时目录。

- [x] **Step 4: 检查差异并更新交接**

Run: `git diff --check`

交接必须明确：C4B-1适配器完成，`real_poc_status=not_run`，RQData未安装/未配置/未调用，下一步需要用户本人申请试用并在合法交易窗口授权一次真实调用。

### Task 5: C4B-2单次真实POC安全入口

**Files:**
- Create: `backend/run_rqdata_tradability_poc.py`
- Create: `backend/tests/test_radar_leader_tradability_rqdata_live_poc.py`

- [x] **Step 1: 先写失败测试**

覆盖缺少确认、休市、日历未知、午间休市、非法证券、隐藏Token、脱敏异常、字段候选和部分结果退出码。

- [x] **Step 2: 实现前置门禁和隐藏Token输入**

只有显式确认、官方交易日、连续交易窗口和查询合同全部通过后才调用`getpass`读取Token；不读取环境变量、不保存文件。

- [x] **Step 3: 周末零费用干跑**

2026-08-01执行确认参数后返回`not_run / rqdata_live_calendar_closed`和退出码2，未提示Token、未调用RQData。

- [x] **Step 4: 回归验证**

C4A/C4B联合41项、全部龙头393项、导入前临时SQLite拦截下完整后端977项通过。

### Task 6: C4B-2有效交易窗口真实小样本

- [ ] **Step 1: 用户本人完成RQData试用并在运行前两小时内取得临时Token**

- [ ] **Step 2: 在有效A股连续交易窗口执行且只执行一轮真实POC**

- [ ] **Step 3: 脱敏核对响应形状、字段覆盖、动态时效、授权和来源血缘**

- [ ] **Step 4: 更新真实结果；未通过前保持非READY且不接生产运行时**
