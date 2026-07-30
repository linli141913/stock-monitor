# 阶段 6L-D3 风险正文事实与版本关联合同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不保存正文、不自动关闭风险的确定性风险文档事实抽取和版本化人工关联合同。

**Architecture:** 新模块消费 D2 官方文档元数据、逐页正文文本和 D1 已知事件版本。确定性提取器只生成压缩事实，人工映射验证器只生成研究性 `supersedes/resolves` 关系，永远不直接生成 D1 正式事件或解除证据。

**Tech Stack:** Python 3.9、标准库 dataclass/enum/re/hashlib/datetime、现有 unittest。

## Global Constraints

- 不新增依赖。
- 不联网下载 PDF，不做 OCR，不保存正文。
- 不读取或写入生产 SQLite。
- 不接迁移、仓储、运行时、调度、API、前端、提醒或正式门禁。
- 不停止、重启或重载 4000/8001。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

---

### Task 1: 冻结正文事实合同

**Files:**
- Create: `backend/tests/test_radar_leader_risk_document_facts.py`
- Create: `backend/radar/leader_risk_document_facts.py`

**Interfaces:**
- Produces: `OfficialRiskDocumentPage`、`RiskDocumentFactKind`、
  `RiskDocumentFact`、`OfficialRiskDocumentFactInput`、
  `OfficialRiskDocumentFactResult` 和
  `extract_official_risk_document_facts`

- [x] **Step 1: 写有效事实抽取失败测试**

使用 D2 真实元数据结构和有明确标签的官方样本文本，断言案号、报告期、审计
报告号、原公告编号、生效日期和实施区间被归一化，输出不包含正文。

- [x] **Step 2: 写内容和身份边界失败测试**

覆盖错误 SHA-256、空页、非连续页码、页数/字符超限、未来抓取、抓取早于公告、
错 D2 合同、重复事实和模糊关键词。

- [x] **Step 3: 运行测试确认 RED**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_document_facts -v
```

Expected: FAIL，原因是 `radar.leader_risk_document_facts` 尚不存在。

### Task 2: 实现确定性正文事实抽取

**Files:**
- Create: `backend/radar/leader_risk_document_facts.py`
- Test: `backend/tests/test_radar_leader_risk_document_facts.py`

**Interfaces:**
- Consumes: D2 `OfficialRiskDocumentMetadata`
- Produces: 有界、去重且不含正文的 `RiskDocumentFact`

- [x] **Step 1: 实现输入和输出 dataclass**

固定正文页数、单页字符数、总字符数、哈希、时点和 D2 身份门禁。

- [x] **Step 2: 实现明确标签抽取**

只识别规格中的六类标签，完成日期和报告期归一化；事实 ID 和片段哈希使用
SHA-256。

- [x] **Step 3: 实现安全输出**

输出只包含归一化事实、页码和哈希；正文、片段和审核摘要不得进入结果。

- [x] **Step 4: 运行事实专项测试确认 GREEN**

Run: Task 1 Step 3 相同命令。

Expected: 全部 PASS。

### Task 3: 冻结版本化人工关联合同

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_document_facts.py`
- Modify: `backend/radar/leader_risk_document_facts.py`

**Interfaces:**
- Produces: `RiskDocumentRelationKind`、`RiskDocumentVersionReview` 和
  `AcceptedRiskDocumentRelation`

- [x] **Step 1: 写有效 supersedes/resolves 失败测试**

`supersedes` 必须精确引用目标事件版本、原公告事实和不同的新版本；
`resolves` 必须精确引用目标事件版本且不得提供新版本。

- [x] **Step 2: 写审核失败测试**

覆盖 AI 审核、未来/过期审核、错证券、错发行人、错原公告、错案号、错报告期、
缺事实引用、重复映射和冲突关系。

- [x] **Step 3: 运行新增测试确认 RED**

Expected: FAIL，原因是人工关系类型和验证尚未实现。

- [x] **Step 4: 实现人工映射验证**

只接受 `manual`，校验目标事件、文档、类别专属事实、映射版本和时间；输出不含
`decision_summary`。

- [x] **Step 5: 运行 D3 专项测试确认 GREEN**

Expected: 全部 PASS。

### Task 4: 真实样本核验、回归和交接

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-stage6l-d3-risk-document-facts-design.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: D1、D2、D3 合同
- Produces: 可复现验证证据和下一阶段边界

- [x] **Step 1: 运行官方样本文本只读核验**

只在内存中读取代表性巨潮 PDF，输出文档 ID、PDF 哈希、页数和抽取事实种类；
不保存 PDF 或正文，不把结果接入 D1。

- [x] **Step 2: 运行 D1-D3 和全部龙头测试**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_invalidation_features \
  tests.test_radar_leader_risk_official_source \
  tests.test_radar_leader_risk_document_facts -v

venv/bin/python -m unittest discover \
  -s tests -p 'test_radar_leader*.py'
```

- [x] **Step 3: 运行后端完整回归和语法检查**

```bash
cd backend
venv/bin/python -m unittest discover -s tests
env PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache \
  venv/bin/python -m py_compile \
  radar/leader_risk_document_facts.py \
  tests/test_radar_leader_risk_document_facts.py
```

- [x] **Step 4: 独立只读复核**

重点检查标题/关键词是否会生成正式关系、人工映射能否绕过精确事实、正文或审核
摘要是否泄露，以及 D1 `CORRECTED` 是否被意外改变。

- [x] **Step 5: 更新唯一交接和最终差异检查**

记录真实样本能力、阻断项、测试数量、生产边界和下一步，运行：

```bash
git status --short
git diff --check
```
