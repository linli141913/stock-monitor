# 阶段 6L-D2 风险官方来源 POC 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不冒充完整覆盖的巨潮官方风险文档发现适配器，并用真实只读探针冻结七类风险、修订和解除来源能力。

**Architecture:** 来源模块只负责官方文档元数据发现和严格归一化，D1 继续负责风险事件合同。查询关键词只作为发现上下文，输出永远保持研究性 `partial`，不得生成 D1 正式事件、解除或覆盖证明。

**Tech Stack:** Python 3.9、标准库 dataclass/zoneinfo/html/urllib、现有 requests、unittest。

## Global Constraints

- 不新增依赖。
- 不读取或写入生产 SQLite。
- 不接运行时、仓储、调度、API、前端、提醒或正式门禁。
- 不停止、重启或重载 4000/8001。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

---

### Task 1: 冻结来源合同和失败测试

**Files:**
- Create: `backend/tests/test_radar_leader_risk_official_source.py`
- Create: `backend/radar/sources/leader_risk_official.py`

**Interfaces:**
- Produces: `CninfoRiskDiscoveryQuery`、`OfficialRiskDocumentMetadata`、
  `OfficialRiskDiscoveryBatch`、`parse_cninfo_risk_discovery_payload` 和
  `fetch_cninfo_risk_discovery`

- [x] **Step 1: 写有效元数据和研究边界测试**

Fixture 使用巨潮真实字段结构，断言：

```text
status=partial
document_id=cninfo:{announcementId}
issuer_identity=cninfo-org:{orgId}
source_url使用HTTPS
coverage_complete=false
open_event_carry_forward_complete=false
correction_links_complete=false
formal_usable=false
```

- [x] **Step 2: 写失败和缺失测试**

覆盖真实空结果、非法响应、缺字段、非数字时间、未来时间、非官方路径、
`associateAnnouncement` 语义未验证和传输失败。

- [x] **Step 3: 运行测试确认 RED**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_official_source -v
```

Expected: FAIL，原因是来源模块尚不存在。

### Task 2: 实现巨潮只读发现适配器

**Files:**
- Create: `backend/radar/sources/leader_risk_official.py`
- Test: `backend/tests/test_radar_leader_risk_official_source.py`

**Interfaces:**
- Consumes: Task 1 冻结的类和函数名
- Produces: 可复用的纯解析器和可注入传输的 HTTPS 查询

- [x] **Step 1: 实现查询和结果 dataclass**

查询必须限制：

```text
1 <= page_number
1 <= page_size <= 30
window_from <= window_until
search_key为非空文本
candidate_category为RiskCategory
```

- [x] **Step 2: 实现严格元数据解析**

只接受六位证券代码、非空 `orgId/announcementId`、毫秒时间戳、
`finalpage/...` 相对 PDF 路径和官方 HTTPS 域名。标题移除高亮标签并
`html.unescape`。

- [x] **Step 3: 实现只读传输**

使用 `requests.Session(trust_env=False)`、20 秒超时、显式 Referer、
User-Agent、日期窗口和分页。捕获 `requests.RequestException` 并返回
`source_failed`。

- [x] **Step 4: 运行专项测试确认 GREEN**

Run: Task 1 Step 3 相同命令。

Expected: 全部 PASS。

### Task 3: 真实官方来源 POC

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-stage6l-d2-official-risk-source-poc-design.md`

**Interfaces:**
- Consumes: 巨潮、证监会、沪深交易所官方只读来源
- Produces: 七类字段能力、修订链和覆盖证明结论

- [x] **Step 1: 查询七类代表关键词**

仅输出总数、分页、字段名和一个代表文档身份；不保存响应正文。

- [x] **Step 2: 查询修订和解除代表关键词**

检查 `associateAnnouncement`、原公告编号、案号、报告期和正式结论能否在
元数据中直接取得。

- [x] **Step 3: 核对证监会和审计原文**

确认文号、发布日期、调查终结和审计影响消除语义，同时记录证券身份和覆盖
证明缺口。

- [x] **Step 4: 固定准入结论**

巨潮、交易所和证监会只进入 `official_document_discovery` 或
`official_exact_document`；D2 不生成 `coverage_proof`。

### Task 4: 回归和交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Verify: D1 与 D2 文件

**Interfaces:**
- Consumes: 完整 D2 POC
- Produces: 可复现验证证据和下一阶段边界

- [x] **Step 1: 运行 D2、D1 和全部龙头测试**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_official_source \
  tests.test_radar_leader_risk_invalidation_features -v

venv/bin/python -m unittest discover \
  -s tests -p 'test_radar_leader*.py'
```

- [x] **Step 2: 运行后端完整回归和语法检查**

```bash
cd backend
venv/bin/python -m unittest discover -s tests
env PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache \
  venv/bin/python -m py_compile \
  radar/sources/leader_risk_official.py \
  tests/test_radar_leader_risk_official_source.py
```

- [x] **Step 3: 独立只读复核**

重点检查是否错误声明完整覆盖、是否按关键词自动分类、是否自动关闭更正或处罚
事件，以及是否泄露正文或敏感标识。

- [x] **Step 4: 更新唯一交接文件**

记录真实来源能力、阻断项、测试数量、生产边界和下一步。

- [x] **Step 5: 最终差异检查**

```bash
git status --short
git diff --check
```
