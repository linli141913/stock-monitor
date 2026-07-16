# 板块四维状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让板块跌幅、上涨家数、龙头跌幅和资金排名分别暴露真实的已触发、未触发或暂无判断状态。

**Architecture:** 风险引擎继续负责固定阈值，只在 `sectorRisk` 下增加四个字段级状态；前端按后端状态原样展示，不自行推算。现有总风险、P2/P3、海外判断和提醒链路保持兼容。

**Tech Stack:** Python、unittest、TypeScript、React、CSS Modules。

## Global Constraints

- 不新增依赖，不修改数据库结构，不回填历史数据。
- 保留当前未提交修改和所有运行文件。
- 缺失字段必须显示“暂无判断”，不得根据其他字段推算。
- 行为修改必须先看到失败测试。
- 本任务不执行 Git 暂存、提交、推送或部署。

---

### Task 1: 后端返回四个独立板块状态

**Files:**
- Modify: `backend/risk_engine.py:248-345,556-640`
- Test: `backend/tests/test_risk_engine.py:360-460`

**Interfaces:**
- Produces: `sectorRisk.dimensions.decline|breadth|leader|fundFlow`
- 每个维度返回：`status`、`label`、`reason`。
- `sectorRisk.dataComplete` 只有四个维度均可判断时才为 `true`。

- [ ] **Step 1: 写入失败测试**

在完整板块样本测试中断言四项均为 `triggered`、`sectorRisk.dataComplete` 为真；另用缺少成分行情的样本断言 `decline/fundFlow` 可判断、`breadth/leader` 为 `unavailable`、`dataComplete` 为假。

- [ ] **Step 2: 运行测试确认因 `dimensions` 缺失而失败**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_risk_engine.LinkageRiskTests.test_sector_rules_use_verified_decline_breadth_leader_and_flow_rank \
  backend.tests.test_risk_engine.LinkageRiskTests.test_sector_snapshot_uses_complete_constituents_and_real_flow_ranking -v
```

- [ ] **Step 3: 最小实现字段级状态**

为四项分别构造：

```python
{
    "status": "triggered" | "no_signal" | "unavailable",
    "label": "已触发" | "未触发" | "暂无判断",
    "reason": "使用真实字段生成的具体原因",
}
```

板块跌幅阈值为 `<= -3`，上涨占比阈值为 `< 20%`，龙头阈值为 `<= -8` 或跌停，资金流排名阈值为同方向前5。没有对应真实字段时保持 `unavailable`。

- [ ] **Step 4: 运行风险引擎测试**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine -v
```

预期：全部通过。

---

### Task 2: 首页显示四个字段级状态

**Files:**
- Modify: `stock-monitor/src/types/industry.ts:16-32`
- Modify: `stock-monitor/src/components/industry/IndustryMonitorCard.tsx:238-272`
- Modify: `stock-monitor/src/components/industry/IndustryMonitorCard.module.css:205-218`
- Test: `backend/tests/test_repo_safety.py`

**Interfaces:**
- Consumes: `linkageRisk.sectorRisk.dimensions`
- 保持旧接口兼容：维度缺失时四项均显示“暂无判断”。

- [ ] **Step 1: 写入失败的前端契约测试**

增加静态契约测试，要求组件包含以下四个标签，并读取 `sectorRisk?.dimensions`：

```text
板块跌幅
上涨家数
板块龙头
资金排名
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_repo_safety.RepositorySafetyTests.test_industry_card_exposes_four_sector_rule_states -v
```

- [ ] **Step 3: 更新类型和最小页面展示**

在 `LinkageRuleState` 增加可选 `dataComplete` 和 `dimensions`；首页联动卡使用现有标签样式逐项显示：

```tsx
板块跌幅：{dimensions?.decline?.label || '暂无判断'}
上涨家数：{dimensions?.breadth?.label || '暂无判断'}
板块龙头：{dimensions?.leader?.label || '暂无判断'}
资金排名：{dimensions?.fundFlow?.label || '暂无判断'}
```

每项 `title` 使用对应 `reason`，不在前端重新计算阈值。

- [ ] **Step 4: 验证前端和真实页面**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_repo_safety.RepositorySafetyTests.test_industry_card_exposes_four_sector_rule_states -v
cd stock-monitor && npm run lint && npx tsc --noEmit && npm run build
```

随后重载固定端口服务，用外置盘 Chrome 在 `1920x1080` 检查首页，确认四项状态来自真实接口。
