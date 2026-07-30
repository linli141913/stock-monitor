# 阶段 6L-B2 流动性与可交易性研究证据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交叉验证腾讯成交额万元口径，生成不含正式分数的流动性与可交易性研究证据。

**Architecture:** 行情适配器只负责同响应单位核对并保留向后兼容的原始值；独立纯计算模块负责研究证据；阶段6运行时只投影同批次候选证据，不新增抓取或存储。

**Tech Stack:** Python 3.9、Pydantic、dataclasses、unittest、现有 `radar` 合同。

## Global Constraints

- 不读取或写入生产SQLite。
- 不新增依赖、迁移、调度、环境变量、API或前端。
- 不实现正式15分，不打开流动性或可交易性门禁。
- 不重载服务，不执行Git暂存、提交或推送。
- 阶段5交易日观察继续并行。

---

### Task 1: 成交额人民币元交叉校验

**Files:**
- Modify: `backend/radar/contracts.py`
- Modify: `backend/radar/sources/tencent_quotes.py`
- Modify: `backend/tests/test_radar_sources.py`

**Interfaces:**
- Produces: `QuoteSnapshot.turnover_amount_cny` 和
  `QuoteSnapshot.turnover_amount_unit_status`。
- Preserves: `turnover_amount_source` 原始值和现有必需字段覆盖率。

- [x] **Step 1: 写万元换算、真实0和失败分支测试**

构造字段37原始值和字段35精确金额，断言误差不超过10000元时保存精确人民币
元；真实0保留；缺失、负值和超差结果保持未验证。

- [x] **Step 2: 运行来源定向测试并确认红灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_sources.TencentQuoteSourceTests
```

Expected: 新行情字段不存在导致失败。

- [x] **Step 3: 实现合同和同响应交叉校验**

复用 `UnitVerificationStatus`；只有两个字段均为非负有限数且
`abs(raw * 10000 - exact) <= 10000` 时保存精确人民币元和 `verified`。

- [x] **Step 4: 运行来源测试并确认绿灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_sources.TencentQuoteSourceTests
```

Expected: 全部通过，现有批次、真实0和失败语义不回归。

### Task 2: 流动性研究证据纯计算

**Files:**
- Create: `backend/radar/leader_liquidity_features.py`
- Create: `backend/tests/test_radar_leader_liquidity_features.py`

**Interfaces:**
- Produces: `LeaderLiquidityFeatureResult`、
  `build_leader_liquidity_features()`。
- Consumes: 当前 `QuoteSnapshot`、来源合同标识和
  `ResearchFeatureStatus`。

- [x] **Step 1: 写成功、真实0和缺失分支测试**

断言已验证人民币元成交额和非负换手率进入压缩证据；历史基线、交易状态、
涨跌停、一字板和价差保持缺失或未验证；`scoreReady=false`。

- [x] **Step 2: 运行专项测试并确认红灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_liquidity_features
```

Expected: 模块不存在导致失败。

- [x] **Step 3: 实现时间、来源和数值门禁**

复用90秒最大年龄和5秒未来偏差；来源失败、过期、单位未验证和非法数值保留
稳定状态及原因，真实0不得改成缺失。

- [x] **Step 4: 运行专项测试并确认绿灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_liquidity_features
```

Expected: 全部通过，0 failures，0 errors。

### Task 3: 阶段6运行时研究证据接入

**Files:**
- Modify: `backend/radar/leader_runtime_inputs.py`
- Modify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: 候选已有的 `QuoteSnapshot` 和行情来源合同。
- Produces: `researchFeatures.liquidityTradability`。

- [x] **Step 1: 写运行时失败测试**

断言研究证据包含已验证成交额和换手率，但正式
`liquidity_tradability.score` 为空，`liquidity_passed` 与
`tradability_passed` 均为假。

- [x] **Step 2: 运行定向测试并确认红灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_runtime_inputs
```

Expected: 新研究证据字段不存在导致失败。

- [x] **Step 3: 实现同批次投影**

每个候选只调用纯计算模块一次，把压缩结果附加到现有
`researchFeatures`，不发网络请求、不增加来源或数据库调用。

- [x] **Step 4: 运行运行时测试并确认绿灯**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_runtime_inputs
```

Expected: 全部通过，现有B1历史证据和正式阻断不回归。

### Task 4: 隔离回归与边界检查

**Files:**
- Verify: 本计划涉及的代码、测试、规格和计划文件。

**Interfaces:**
- Produces: 可重复的完整测试、语法和Git差异证据。

- [x] **Step 1: 临时SQLite隔离运行全部龙头测试**

在导入测试前把生产数据库绝对路径重定向到临时SQLite，再发现并运行
`test_radar_leader*.py`。

- [x] **Step 2: 临时SQLite隔离运行完整后端测试**

使用同一重定向保护发现并运行 `backend/tests/test_*.py`。

- [x] **Step 3: 运行语法和差异检查**

```bash
PYTHONPYCACHEPREFIX=/tmp/stock-monitor-codex-pycache backend/venv/bin/python -m py_compile backend/radar/contracts.py backend/radar/sources/tencent_quotes.py backend/radar/leader_liquidity_features.py backend/radar/leader_runtime_inputs.py backend/tests/test_radar_sources.py backend/tests/test_radar_leader_liquidity_features.py backend/tests/test_radar_leader_runtime_inputs.py
git diff --check
git status --short --branch
```

Expected: 检查通过；无生产数据、密钥、依赖、迁移、服务配置或无关新增操作，
Git保持未暂存、未提交、未推送。
