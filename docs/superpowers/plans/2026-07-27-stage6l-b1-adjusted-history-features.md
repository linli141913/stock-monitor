# 阶段 6L-B1 同口径复权历史研究特征 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增严格校验的同口径历史原始研究指标，并以可选输入接入阶段 6 龙头证据包，同时保持正式评分和状态机关闭。

**Architecture:** 新模块负责无网络、无数据库的历史合同、质量门禁和纯计算；现有运行时组装器只负责传入可选历史并投影证据。默认没有历史输入时保持现有缺失语义。

**Tech Stack:** Python 3.9、dataclasses、unittest、现有 `radar` 合同与测试工具。

## Global Constraints

- 不访问或修改生产 SQLite。
- 不新增依赖、迁移、调度、环境变量、API 或前端。
- 不修改正式评分、门禁或状态机结果。
- 不执行 `git add`、提交或推送。
- 阶段 5 影子观察继续并行。

---

### Task 1: 历史序列合同与纯计算

**Files:**
- Create: `backend/radar/leader_history_features.py`
- Create: `backend/tests/test_radar_leader_history_features.py`

**Interfaces:**
- Produces: `AdjustedHistorySeries`、`LeaderHistoryFeatureInput`、
  `LeaderHistoryFeatureResult`、`build_leader_history_features()` 和
  `missing_leader_history_features()`。
- Consumes: `ResearchFeatureStatus`，不连接其他运行时模块。

- [x] **Step 1: 写失败测试**

覆盖 21 个对齐交易日的 3/5/10/20 日收益、超额收益、跑赢天数、最大回撤、
抗跌差值和谷底修复；同时覆盖真实 0 与输出不包含完整价格序列。

- [x] **Step 2: 运行测试并确认红灯**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_history_features
```

Expected: 因 `radar.leader_history_features` 尚不存在而失败。

- [x] **Step 3: 实现最小合同与计算**

实现三个固定角色和固定口径；校验带时区时间、最近 21 个预期交易日、日期
唯一升序、正数有限价格和来源状态。收益公式统一为
`last_close / start_close - 1`，最大回撤为窗口内相对历史峰值的最小跌幅。

- [x] **Step 4: 补齐失败和缺失分支测试**

加入少于 21 日、日期断档、跨序列错位、非法价格、错误复权口径、未来来源
时间、来源失败、实际回撤修复和无回撤事件测试。

- [x] **Step 5: 运行专项测试并确认绿灯**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_history_features
```

Expected: 全部通过，0 failures，0 errors。

### Task 2: 运行时证据接入

**Files:**
- Modify: `backend/radar/leader_runtime_inputs.py`
- Modify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: `Mapping[str, LeaderHistoryFeatureInput]`。
- Produces: `researchFeatures.historyContinuity` 证据；不产生正式分数。

- [x] **Step 1: 写默认兼容和可用证据失败测试**

默认输入断言 `historyContinuity.status=missing` 和
`continuity_history_missing`。提供合格输入时断言原始指标可用，但正式
`relative_strength_continuity.score` 仍为空、门禁仍为 `false`。

- [x] **Step 2: 运行两个定向测试并确认红灯**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_runtime_inputs
```

Expected: 新证据字段或新参数不存在导致失败。

- [x] **Step 3: 实现可选接入**

为 `build_leader_runtime_evidence` 添加默认 `None` 的
`history_inputs_by_symbol`；每个候选调用纯计算器或生成缺失结果，并将
压缩证据写入 `researchFeatures.historyContinuity`。

- [x] **Step 4: 保持正式门禁关闭**

历史研究证据可用时，正式维度使用 `source_unverified` 和
`continuity_research_only`，缺失前置项使用 `continuity_formal_rule`；
`continuity_passed`、正式分数、榜单状态均不改变。

- [x] **Step 5: 运行运行时专项测试并确认绿灯**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_runtime_inputs
```

Expected: 全部通过，0 failures，0 errors。

### Task 3: 隔离回归与边界核对

**Files:**
- Verify: `backend/radar/leader_history_features.py`
- Verify: `backend/radar/leader_runtime_inputs.py`
- Verify: `backend/tests/test_radar_leader_history_features.py`
- Verify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: Task 1 和 Task 2 的最终接口。
- Produces: 可重复的测试、语法和 Git 差异证据。

- [x] **Step 1: 运行全部龙头测试**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -p 'test_radar_leader*.py'
```

Expected: 全部通过且不访问生产数据库。

- [x] **Step 2: 运行语法编译**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/stock-monitor-codex-pycache backend/venv/bin/python -m py_compile backend/radar/leader_history_features.py backend/radar/leader_runtime_inputs.py backend/tests/test_radar_leader_history_features.py backend/tests/test_radar_leader_runtime_inputs.py
```

Expected: exit 0。

- [x] **Step 3: 检查差异**

Run:

```bash
git diff --check
```

Expected: exit 0；没有生产数据、密钥、依赖、迁移、服务配置或无关修改。

- [x] **Step 4: 保持 Git 未提交**

Run:

```bash
git status --short --branch
```

Expected: 只保留累计阶段 6 本地改动，不执行暂存、提交或推送。
