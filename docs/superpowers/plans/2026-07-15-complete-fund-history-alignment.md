# 资金历史与当日 K 线对齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 在资金流数据比当日 K 线晚一个交易日时，继续使用日期完整、真实可追溯的历史资金样本，同时保留当日 K 线用于可验证的均线破位判断。

**Architecture:** 不新增数据库表、字段或依赖。`merge_verified_market_history` 继续保留完整 K 线行，并将资金字段按真实日期合并；`_verified_market_signals` 对资金规则只读取 `fund_flow` 与 `fund_close` 均存在的行，对均线规则继续读取最新两条有效 K 线行。这样不会把缺失的当日资金流伪装成 0，也不会因为单一数据源滞后而丢弃之前完整的历史样本。

**Tech Stack:** Python、现有 `risk_engine.py`、unittest、腾讯财经 K 线和东方财富资金流公开接口。

## Global Constraints

- 不修改数据库结构，不写入或覆盖历史数据库。
- 不新增依赖，不改变“连续3个交易日”与价格资金背离阈值。
- 资金字段缺失时只跳过该行的资金规则，不补 0、不推算；均线仍只使用真实 K 线字段。
- 港股、ETF 和无法确认资产继续保持现有“暂无判断”语义。
- 不执行 Git add、commit、push、部署或邮箱配置修改。

---

### Task 1: 资金历史与 K 线独立取样

**Files:**
- Modify: `backend/risk_engine.py:_verified_market_signals`
- Test: `backend/tests/test_risk_engine.py:RiskEngineTests`

**Interfaces:**
- Consumes: `verified_history: list[dict]`，其中每行可能有 `trade_date`、`close`、`fund_close`、`fund_flow`、`ma5`、`ma10`、`ma20`。
- Produces: 现有 `fundFlowRisk`、`movingAverageRisk` 和 `signals` 字段，接口名称和阈值不变。

- [x] **Step 1: 写入失败测试**

添加一条测试数据：最近一条 K 线为当天但没有资金流，之前三条交易日有完整且连续的 `fund_close` 与 `fund_flow`；断言资金规则仍按之前三条完整记录计算，均线仍按最新两条 K 线计算。

```python
def test_fund_rules_use_latest_complete_history_when_today_flow_is_delayed(self):
    verified_history = [
        {"trade_date": "2026-07-10", "close": 10.0, "fund_close": 10.0, "fund_flow": -1.0},
        {"trade_date": "2026-07-13", "close": 10.2, "fund_close": 10.2, "fund_flow": -2.0},
        {"trade_date": "2026-07-14", "close": 10.6, "fund_close": 10.6, "fund_flow": -3.0,
         "ma5": 10.0, "ma10": 9.9, "ma20": 9.8},
        {"trade_date": "2026-07-15", "close": 9.7, "fund_close": None, "fund_flow": None,
         "ma5": 9.5, "ma10": 9.4, "ma20": 9.3},
    ]

    result = risk_engine.evaluate_market_risk(
        snapshot(close=9.7),
        history(1.0),
        verified_history=verified_history,
    )

    self.assertIn("consecutive_fund_outflow", {item["code"] for item in result["signals"]})
    self.assertEqual(result["fundFlowRisk"]["status"], "triggered")
    self.assertEqual(result["movingAverageRisk"]["status"], "no_signal")
```

- [x] **Step 2: 运行测试确认当前实现失败**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_risk_engine.RiskEngineTests.test_fund_rules_use_latest_complete_history_when_today_flow_is_delayed -v
```

预期：FAIL，原因是当前实现直接取 `rows[-3:]`，把当日缺失资金流混入资金规则，结果为“暂无判断”。

- [x] **Step 3: 最小实现**

在 `backend/risk_engine.py` 中将资金规则输入改为：

```python
fund_rows = [
    row for row in rows
    if _number(row.get("fund_flow")) is not None
    and _number(row.get("fund_close", row.get("close"))) is not None
]
if len(fund_rows) >= 3:
    recent = fund_rows[-3:]
    flows = [_number(row["fund_flow"]) for row in recent]
    closes = [_number(row.get("fund_close", row.get("close"))) for row in recent]
```

均线部分继续使用 `rows[-2:]`，不使用资金过滤后的列表。

- [x] **Step 4: 运行回归测试**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_risk_engine.RiskEngineTests \
  backend.tests.test_risk_engine.LinkageRiskTests.test_market_history_keeps_verified_kline_when_fund_source_is_missing -v
```

预期：新测试、资金缺失测试和均线测试全部通过。

### Task 2: 真实源覆盖验证

**Files:**
- No production file changes unless Task 1 exposes a source parsing defect.
- Test: `backend/tests/test_risk_engine.py` only if a reproducible parsing defect is found.

- [x] **Step 1: 只读检查三只监测股票**

使用现有函数读取腾讯 K 线和东方财富资金流，输出每只股票的数量、首尾日期、非空资金条数和交集日期数；不写数据库。

- [x] **Step 2: 核对真实结果**

预期当前三只股票均有约300条 K 线、约120条有资金流历史；当日资金缺失只能导致资金规则使用前一完整交易日，不能导致伪造当日资金值。

- [x] **Step 3: 运行全量验证**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v
cd stock-monitor && npm run lint && npx tsc --noEmit && npm run build
```

预期：后端全量通过，前端静态检查和构建通过。

### Task 3: 固定端口真实接口验证

- [x] **Step 1:** 重载后端 `127.0.0.1:8001`，前端 `localhost:4000`保持运行。
- [x] **Step 2:** 请求三个 `/api/stock/risk/{symbol}` 接口，确认资金状态不再因为当日资金源滞后而错误显示暂无判断，均线状态仍有独立完整性说明。
- [x] **Step 3:** 使用外置盘 Chrome 在 1920×1080 检查首页量价卡片、风险原因和提醒中心；不修改阈值，不清理现有运行文件。
