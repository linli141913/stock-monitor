# 北交所 920 板块行情完整度实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将北交所 `920xxx` 股票正确路由到腾讯财经 `bj` 行情前缀，补齐当前三个监测板块的成分股行情。

**Architecture:** 保持现有板块成分来源和腾讯批量行情接口不变，只在统一资产上下文中修正市场前缀。所有调用方继续通过 `asset_context.quote_prefix()` 获取前缀，不新增特殊分支或依赖。

**Tech Stack:** Python、unittest、FastAPI、腾讯财经公开行情。

## Global Constraints

- 唯一项目根目录：`/Volumes/HermesSSD/AntigravityData/量化监测-股票`。
- 不新增依赖，不修改数据库结构，不回填历史数据。
- 保留所有现有未提交修改、数据库、备份、截图和运行文件。
- 行为修改先写失败测试，再做最小实现。
- 本任务不执行 `git add`、`git commit`、`git push` 或部署。
- 前端端口固定为 `4000`，后端端口固定为 `8001`。

---

### Task 1: 修正 920 代码行情前缀并验证板块覆盖

**Files:**
- Modify: `backend/asset_context.py:44-54`
- Test: `backend/tests/test_multi_asset_monitoring.py:21-52`

**Interfaces:**
- Consumes: `asset_context.quote_prefix(symbol: str, stock_name: str = "") -> str`
- Produces: `quote_prefix("920001") == "bj"`，其他沪深、旧北交所、港股和 ETF 规则保持不变。

- [ ] **Step 1: 写入失败测试**

在 `AssetContextTests.test_builds_distinct_contexts_for_a_share_hk_stock_and_domestic_etf` 中增加：

```python
        bse_new_code = asset_context.build_asset_context("920001", "纬达光电")
        self.assertEqual(bse_new_code["asset_type"], "a_stock")
        self.assertEqual(bse_new_code["quote_prefix"], "bj")
```

- [ ] **Step 2: 确认测试按预期失败**

运行：

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_multi_asset_monitoring.AssetContextTests.test_builds_distinct_contexts_for_a_share_hk_stock_and_domestic_etf -v
```

预期：失败，实际前缀为 `sh`，期望为 `bj`。

- [ ] **Step 3: 写入最小实现**

将 `asset_context.quote_prefix` 的 A 股分支调整为：

```python
    if code.startswith(("4", "8", "920")):
        return "bj"
    if code.startswith(("5", "6", "9")):
        return "sh"
```

必须先判断 `920`，避免被通用的 `9` 前缀提前识别为上交所。

- [ ] **Step 4: 运行相关测试**

运行：

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_multi_asset_monitoring.AssetContextTests -v
```

预期：全部通过。

- [ ] **Step 5: 核对三个真实板块成分覆盖**

分别获取 `000725/光学光电子`、`000519/专用设备`、`000021/消费电子` 的真实成分和腾讯行情，输出 `expected`、`returned` 和 `missing`。

验收条件：

```text
000725: returned == expected, missing == []
000519: returned == expected, missing == []
000021: returned == expected, missing == []
```

如果仍有缺失，只报告真实缺失代码和来源状态，不放宽完整度规则。

- [ ] **Step 6: 完整验证与服务重载**

运行：

```bash
git diff --check
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v
```

预期：完整后端测试通过。随后重载后端 `127.0.0.1:8001`，确认健康接口和三个板块真实接口返回正常；前端 `localhost:4000` 保持运行。
