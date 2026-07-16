# 板块联动快照持久化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将每次板块与海外联动判断摘要保存到现有 `signal_snapshots.signals_json`，并在进程重启后恢复当天最新状态。

**Architecture:** 不新增表和字段。量价快照与联动快照通过同一个 JSON 对象合并保存，任意写入顺序都必须保留另一类状态；读取时只返回指定交易日、含 `linkageRisk` 的最新记录。

**Tech Stack:** Python、SQLite、unittest、现有 `alert_repository`。

## Global Constraints

- 不修改数据库结构，不删除或覆盖现有数据库。
- 只保存真实输入摘要和规则结果，不补齐缺失字段。
- 历史日期不得作为今天的联动状态返回。
- 本任务不执行 Git 暂存、提交、推送或部署。

---

### Task 1: 合并保存并读取联动快照

**Files:**
- Modify: `backend/alert_repository.py:439-590`
- Modify: `backend/risk_engine.py:790-815`
- Modify: `backend/main.py:941-950`
- Test: `backend/tests/test_risk_engine.py:590-660`

**Interfaces:**
- `save_linkage_snapshot(snapshot: dict, result: dict) -> None`
- `get_latest_linkage_state(symbol: str, trade_date: str) -> Optional[dict]`
- `process_linkage_snapshot(snapshot, create_alert=False, persist_snapshot=True)`

- [ ] **Step 1: 写入失败测试**

先保存一条包含资金和均线状态的量价快照，再保存同一时点联动快照，断言：

```text
get_latest_signal_state 仍保留 fundFlowRisk 和 movingAverageRisk
get_latest_linkage_state 返回 sectorRisk 四维状态
查询其他日期返回 None
```

- [ ] **Step 2: 运行测试确认新接口不存在**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest \
  backend.tests.test_risk_engine.SignalSnapshotRepositoryTests.test_linkage_snapshot_merges_without_overwriting_market_risk -v
```

- [ ] **Step 3: 实现 JSON 合并保存**

`save_linkage_snapshot` 读取同一股票、交易日和5分钟桶的现有 `signals_json`，保留原有 `signals/fundFlowRisk/movingAverageRisk`，增加：

```python
{
    "linkageRisk": result,
    "linkageSnapshot": {
        "sourceTime": snapshot["source_time"],
        "fetchedAt": snapshot.get("fetched_at"),
        "sector": snapshot.get("sector") or {"status": "unavailable"},
        "overseas": snapshot.get("overseas") or [],
    },
}
```

若同桶量价记录尚不存在，插入仅含真实联动摘要的最小记录；后续 `save_signal_snapshot` 必须合并而不是覆盖上述键。

- [ ] **Step 4: 接入生产链路和当天回退读取**

`process_linkage_snapshot` 默认持久化；测试或纯计算可传 `persist_snapshot=False`。`main.get_cached_linkage_risk` 内存缓存缺失时，只读取上海时区当天的最新联动状态，禁止回退到昨天。

- [ ] **Step 5: 运行相关和完整测试**

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine -v
git diff --check
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v
```

预期：全部通过。随后重载后端，确认健康接口、行业接口和首页四维状态正常。
