# Alert Episode And News Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阻止同一风险区间因信号组合抖动重复提醒，并让行业资讯明确区分正常为空与数据读取失败。

**Architecture:** 不新增数据库表或字段。风险提醒复用 `signal_snapshots` 中最近两个五分钟快照判断是否仍处于同一风险区间；同方向持续风险只允许优先级升级，连续两个已保存快照解除风险后才允许产生新的提醒事件。资讯接口返回带 `status` 的稳定对象，前端根据 `available`、`available_empty`、`unavailable` 展示真实状态。

**Tech Stack:** Python、FastAPI、SQLite、unittest、Next.js、React、TypeScript。

## Global Constraints

- 只操作 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`。
- 不新增依赖，不修改数据库结构，不删除数据库、备份、截图或运行文件。
- 保留现有未提交和未跟踪文件，不修改无关代码。
- 行为修改先写失败测试，再做最小实现。
- 未经授权不执行 `git add`、`git commit`、`git push` 或部署。
- 前端固定 `http://localhost:4000`，后端固定 `http://127.0.0.1:8001`。

---

### Task 1: 风险区间快照查询

**Files:**
- Modify: `backend/alert_repository.py`
- Test: `backend/tests/test_risk_engine.py`

**Interfaces:**
- Produces: `get_recent_risk_states(symbol, source_time, risk_kind, limit=2)`。
- Produces: `get_latest_risk_episode_alert(symbol, event_type, direction, trade_date)`。

- [ ] **Step 1: 写失败测试**

覆盖最近两个五分钟快照能够分别读取 `market` 和 `linkage` 的 `priority`、`direction`、`riskStatus`，且不读取其他日期。

- [ ] **Step 2: 运行测试并确认因接口不存在失败**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine.SignalSnapshotRepositoryTests -v`

- [ ] **Step 3: 最小实现查询接口**

只读取现有 `signal_snapshots.signals_json` 和现有列；保存市场快照时把 `direction` 放入 JSON，不增加列。

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine.SignalSnapshotRepositoryTests -v`

### Task 2: 冷却、升级与重新进入

**Files:**
- Modify: `backend/risk_engine.py`
- Test: `backend/tests/test_risk_engine.py`

**Interfaces:**
- Produces: `_prepare_episode_alert(event, snapshot, risk_kind)`，返回允许入库的事件或 `None`。

- [ ] **Step 1: 写失败测试**

覆盖四种行为：首次进入生成提醒；同方向同等级但信号组合变化不重复；P3 升级 P2 更新同一事件；连续两个无风险快照后再次进入生成新事件。

- [ ] **Step 2: 运行测试并确认当前实现重复提醒**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine.SignalSnapshotRepositoryTests -v`

- [ ] **Step 3: 最小实现风险区间逻辑**

事件 ID 使用风险类型、交易日、方向和新风险区间开始时间；持续风险复用最近事件 ID，只有优先级升级才交给现有 `save_alert_event` 更新。

- [ ] **Step 4: 运行风险和提醒测试**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_risk_engine backend.tests.test_alert_system -v`

### Task 3: 资讯可用状态契约

**Files:**
- Modify: `backend/news_api.py`
- Modify: `stock-monitor/src/app/industry/page.tsx`
- Test: `backend/tests/test_data_integrity.py`
- Test: `backend/tests/test_repo_safety.py`

**Interfaces:**
- Produces: `RadarNewsFeed`，字段为 `status`、`data`、`error`、`checkedAt`。
- `status` 只允许 `available`、`available_empty`、`unavailable`。

- [ ] **Step 1: 写失败测试**

覆盖正常有数据、正常为空和数据库读取异常；异常只能返回通用错误，不能泄露内部路径或堆栈。前端必须识别三种状态。

- [ ] **Step 2: 运行测试并确认旧列表契约失败**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_data_integrity.NewsIntegrityTests backend.tests.test_repo_safety.RepositorySafetyTests -v`

- [ ] **Step 3: 最小实现后端和前端契约**

正常为空显示“当前分类下暂无权威资讯”；读取失败显示“资讯数据暂不可用”，不得同时伪装为空状态。

- [ ] **Step 4: 运行相关测试、ESLint 和 TypeScript**

Run: `npm run lint`

Run: `npx tsc --noEmit`

### Task 4: 完整验证与本地重载

**Files:**
- Verify only: all changed files

- [ ] **Step 1: 运行完整后端测试**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v`

- [ ] **Step 2: 运行前端生产构建**

Run: `npm run build`

- [ ] **Step 3: 检查 diff 和固定端口服务**

Run: `git diff --check`

- [ ] **Step 4: 重载服务并核对真实接口**

确认 `/api/alerts` 不再因信号组合抖动产生新提醒，确认 `/api/semiconductor-news/latest` 返回带状态对象。

- [ ] **Step 5: 外置 Chrome 进行 1920×1080 验收**

检查首页、监测列表、提醒中心和行业洞察，控制台不得有功能错误。
