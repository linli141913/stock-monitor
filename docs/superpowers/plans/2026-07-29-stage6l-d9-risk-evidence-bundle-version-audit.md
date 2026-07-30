# 阶段 6L-D9 风险研究证据包版本链与差异审计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 只消费多个 D8 冻结证据包，验证线性研究版本链、选择当前版本并输出四组结构化差异。

**Architecture:** 新增一个纯计算 D9 模块。模块先整体校验有序 D8 包链，再逐对计算相邻差异；任一位置失败时整体拒绝，不输出部分当前版本或部分差异。

**Tech Stack:** Python 3.9、冻结 dataclass、Enum、`unittest`，不新增依赖。

## Global Constraints

- 只在内存中消费 D8 `RiskResearchEvidenceBundle`。
- 至少两个包，按旧到新排列，时间严格递增。
- 同来源文档、同内容快照、同目标事件身份。
- 不自动排序、合并冲突、猜测缺失版本或提升正式状态。
- 不连接生产 SQLite、调度、API、前端或 AI。
- 不执行 Git 暂存、提交或推送。

---

### Task 1: D9 合同与线性链校验

**Files:**
- Create: `backend/radar/leader_risk_evidence_bundle_audit.py`
- Create: `backend/tests/test_radar_leader_risk_evidence_bundle_audit.py`

**Interfaces:**
- Consumes: `RiskResearchEvidenceBundleAuditInput(bundles: Tuple[RiskResearchEvidenceBundle, ...])`
- Produces: `audit_risk_research_evidence_bundle_versions(input_value: Any) -> RiskResearchEvidenceBundleAuditResult`

- [ ] **Step 1: 写失败测试**

覆盖合法双版本链、当前版本选择、空链、单包、混合身份、乱序、重复、伪造合同和无实质变化。

- [ ] **Step 2: 确认红灯**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_risk_evidence_bundle_audit
```

Expected: `ModuleNotFoundError`，因为 D9 模块尚不存在。

- [ ] **Step 3: 实现最小合同和校验**

新增冻结输入、字段变化、事实差异、关系差异、门禁缺口差异、相邻版本差异和结果对象。验证 D8 合同、固定身份、严格顺序、唯一性、正式标志和实质变化。

- [ ] **Step 4: 确认转绿**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_risk_evidence_bundle_audit
```

Expected: 全部通过。

### Task 2: 相邻版本差异与安全边界

**Files:**
- Modify: `backend/radar/leader_risk_evidence_bundle_audit.py`
- Modify: `backend/tests/test_radar_leader_risk_evidence_bundle_audit.py`

**Interfaces:**
- Produces: `RiskResearchEvidenceBundleVersionDiff`
- Produces: `RiskEvidenceFactDiff`
- Produces: `RiskEvidenceArtifactDiff`
- Produces: `RiskEvidenceRelationDiff`
- Produces: `RiskEvidenceFormalGateGapDiff`

- [ ] **Step 1: 写失败测试**

覆盖三版本相邻差异、事实增删顺序、工件字段变化、关系字段和依据变化、门禁缺口变化、冻结结果及敏感文本不进入 `repr`。

- [ ] **Step 2: 确认红灯**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_risk_evidence_bundle_audit
```

Expected: 差异字段或行为尚未实现导致断言失败。

- [ ] **Step 3: 实现最小差异计算**

使用稳定集合差异保留前后版本原始顺序；字段值统一转换为字符串、枚举值、ISO 时间或 `None`。每个相邻版本只生成一个冻结差异对象。

- [ ] **Step 4: 确认转绿**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_risk_evidence_bundle_audit
```

Expected: 全部通过。

### Task 3: 联合回归与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

- [ ] **Step 1: 运行 D1-D9 联合测试**

Run:

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_invalidation_features \
  tests.test_radar_leader_risk_official_source \
  tests.test_radar_leader_risk_document_facts \
  tests.test_radar_leader_risk_document_content \
  tests.test_radar_leader_risk_review_artifacts \
  tests.test_radar_leader_risk_review_replay \
  tests.test_radar_leader_risk_supplemented_relation \
  tests.test_radar_leader_risk_evidence_bundle \
  tests.test_radar_leader_risk_evidence_bundle_audit
```

- [ ] **Step 2: 运行龙头全量和后端完整回归**

Run:

```bash
cd backend
venv/bin/python -m unittest discover -s tests -p 'test_radar_leader*.py'
venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

- [ ] **Step 3: 运行语法与差异检查**

Run:

```bash
cd backend
env PYTHONPYCACHEPREFIX=/private/tmp/stock-monitor-pycache \
  venv/bin/python -m py_compile \
  radar/leader_risk_evidence_bundle_audit.py \
  tests/test_radar_leader_risk_evidence_bundle_audit.py
cd ..
git diff --check
```

- [ ] **Step 4: 更新唯一交接文档**

记录 D9 合同、测试证据、生产未启用状态和下一步，不执行 Git 暂存、提交或推送。
