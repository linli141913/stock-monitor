# 阶段6L-C4C免费组合源可交易性POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不依赖付费订阅、与RQData解耦的免费组合源可交易性字段POC，并以稳定原因码审计交易所公开证据、AKShare聚合观测和现有腾讯行情之间的缺失、延迟与冲突。

**Architecture:** 新增一个纯内存模块，只消费调用方注入的证券上下文、公开来源观测和现有`QuoteSnapshot`，不自行联网、不连接数据库。模块先校验身份、来源、交易日和时间，再逐字段按交易所原文、公开聚合、腾讯辅助的层级做一致性审计；所有正式准入开关固定为`false`，RQData适配器不被导入且保持可选。

**Tech Stack:** Python 3、`dataclasses`、`enum`、现有Pydantic `QuoteSnapshot`、现有C1/C3可交易性枚举与规则解析、`unittest`。

## Global Constraints

- 只操作`/Volumes/HermesSSD/AntigravityData/量化监测-股票`。
- 不新增或升级依赖，不读取或写入生产SQLite。
- 不修改运行时、调度、API、前端、环境变量或LaunchAgent。
- 不停止、重启或重载4000/8001。
- 不执行Git暂存、提交、推送、部署或创建PR。
- 腾讯普通行情行、当前价格和非零成交不能推断`trading`。
- 缺失、冲突、过期和来源失败不得使用旧值或0回填。
- RQData未配置、失败或到期不得影响免费路径。

---

## File Structure

- Create: `backend/radar/sources/leader_tradability_public_poc.py`
  - 定义免费组合源查询、证券上下文、公开观测、逐证券结果、批次报告和纯内存组装函数。
- Create: `backend/tests/test_radar_leader_tradability_public_poc.py`
  - 覆盖身份、时间、来源安全、字段一致性、冲突、缺失、正常状态不推断、脱敏和RQData解耦。
- Modify: `NEXT_CHAT_HANDOFF.md`
  - 只在代码和回归完成后记录C4C-1真实完成状态、C4C-2未执行和RQData备用边界。

### Task 1: 冻结查询、来源和报告合同

**Files:**
- Create: `backend/radar/sources/leader_tradability_public_poc.py`
- Test: `backend/tests/test_radar_leader_tradability_public_poc.py`

**Interfaces:**
- Consumes: `QuoteSnapshot`、`SecurityLifecycleStatus`、`TradingSessionStatus`、`PriceLimitMode`、`PriceLimitSpecialSession`。
- Produces: `PublicCompositePocStatus`、`PublicSourceKind`、`PublicSecurityContext`、`PublicTradabilityObservation`、`PublicCompositeTradabilityQuery`、`PublicCompositeTradabilityReport`、`run_public_composite_tradability_poc(...)`。

- [x] **Step 1: Write the failing contract test**

```python
def test_not_executed_report_is_nonformal_and_redacted(self):
    report = run_public_composite_tradability_poc(
        query=self.query(),
        quotes=(),
        official_observations=(),
        aggregator_observations=(),
        executed=False,
    )
    self.assertEqual(report.status, PublicCompositePocStatus.NOT_RUN)
    self.assertFalse(report.formal_gate_ready)
    self.assertNotIn("records", report.to_evidence())
```

- [x] **Step 2: Run test to verify RED**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc -v`

Expected: `ModuleNotFoundError`指向`radar.sources.leader_tradability_public_poc`。

- [x] **Step 3: Implement immutable contracts and NOT_RUN report**

实现冻结dataclass、枚举、`to_evidence()`及固定的四个非正式布尔值。`to_evidence()`只输出合同ID、状态、计数、字段覆盖和原因，不输出逐证券原始观测或上游正文。

- [x] **Step 4: Run focused test to verify GREEN**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc -v`

Expected: PASS。

### Task 2: 校验身份、来源安全和批次完整性

**Files:**
- Modify: `backend/radar/sources/leader_tradability_public_poc.py`
- Modify: `backend/tests/test_radar_leader_tradability_public_poc.py`

**Interfaces:**
- Consumes: Task 1的查询和观测对象。
- Produces: 稳定原因码`public_query_*`、`public_source_*`、`public_batch_*`及`blocked/no_data`结果。

- [x] **Step 1: Add failing validation tests**

```python
def test_sensitive_source_url_blocks_batch(self):
    observation = self.official(source_url="https://example.com/a?token=secret")
    report = self.run(official_observations=(observation,))
    self.assertEqual(report.fixture_resolution_status, PublicCompositePocStatus.BLOCKED)
    self.assertIn("public_source_url_sensitive", report.reasons)

def test_duplicate_and_wrong_date_observations_block_batch(self):
    first = self.official()
    second = replace(first, trading_date=date(2026, 8, 3))
    report = self.run(official_observations=(first, first, second))
    self.assertEqual(report.fixture_resolution_status, PublicCompositePocStatus.BLOCKED)
```

- [x] **Step 2: Run tests to verify RED**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc -v`

Expected: 新增断言失败，缺少稳定校验原因码。

- [x] **Step 3: Implement validation**

校验1至8只唯一A股代码、带时区`as_of`、统一交易日、唯一来源记录、HTTPS且不含用户名/密码/查询参数/片段的URL、非空合同和文档身份、SHA-256摘要、源时间不晚于`as_of+5秒`。真实执行但所有输入为空返回`no_data`，不写成完成。

- [x] **Step 4: Run focused tests to verify GREEN**

Run同Task 2 Step 2，Expected: PASS。

### Task 3: 实现逐字段组合、时效和冲突审计

**Files:**
- Modify: `backend/radar/sources/leader_tradability_public_poc.py`
- Modify: `backend/tests/test_radar_leader_tradability_public_poc.py`

**Interfaces:**
- Consumes: `resolve_trading_rule_catalog(...)`、腾讯`QuoteSnapshot`可选字段、官方与聚合观测。
- Produces: `PublicCompositeTradabilityRecord`及`field_candidate/partial/blocked`报告。

- [x] **Step 1: Add failing field-resolution tests**

```python
def test_quote_without_explicit_status_does_not_infer_trading(self):
    report = self.run(quotes=(self.quote(trading_status=None),))
    self.assertEqual(report.fixture_resolution_status, PublicCompositePocStatus.PARTIAL)
    self.assertIn("public_trading_status_missing", report.reasons)

def test_explicit_suspension_conflict_blocks_record(self):
    report = self.run(
        quotes=(self.quote(trading_status=QuoteTradingStatus.SUSPENDED),),
        official_observations=(
            self.official(trading_status=TradingSessionStatus.TRADING),
        ),
    )
    self.assertEqual(report.fixture_resolution_status, PublicCompositePocStatus.BLOCKED)
    self.assertIn("public_trading_status_conflict", report.reasons)

def test_price_limits_must_match_versioned_rule(self):
    report = self.run(
        quotes=(self.quote(previous_close=10.0, upper=11.0, lower=9.0),),
        official_observations=(self.complete_official(),),
    )
    self.assertEqual(report.fixture_resolution_status, PublicCompositePocStatus.FIELD_CANDIDATE)
```

- [x] **Step 2: Run tests to verify RED**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc -v`

Expected: 新增逐字段断言失败。

- [x] **Step 3: Implement deterministic field resolution**

逐证券执行：官方与聚合值一致性比较；腾讯`S/D/U`只提供异常交叉校验；普通行情不推断`trading`；动态状态最大延迟90秒、未来偏差5秒；生命周期和特殊交易日解析C3规则；腾讯上下限必须成对、为正有限值并与规则目录一致。官方与低优先级来源冲突仍阻断，不静默选高优先级值。

- [x] **Step 4: Run focused tests to verify GREEN**

Run同Task 3 Step 2，Expected: PASS。

### Task 4: 冻结RQData解耦、脱敏和回归边界

**Files:**
- Modify: `backend/tests/test_radar_leader_tradability_public_poc.py`
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: 完整C4C-1模块。
- Produces: 可重复验收证据和准确交接。

- [x] **Step 1: Add failing independence and evidence tests**

```python
def test_public_poc_does_not_import_rqdata_adapter(self):
    source = Path(PUBLIC_MODULE.__file__).read_text(encoding="utf-8")
    self.assertNotIn("leader_tradability_rqdata_poc", source)

def test_evidence_contains_no_raw_records_or_sensitive_values(self):
    evidence = self.run_complete().to_evidence()
    self.assertNotIn("records", evidence)
    self.assertNotIn("token", json.dumps(evidence).lower())
```

- [x] **Step 2: Run focused tests and confirm RED/GREEN as applicable**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc -v`

Expected: PASS after adding only the minimum production correction required by a genuine failure.

- [x] **Step 3: Run related regression**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_public_poc tests.test_radar_leader_tradability_sources tests.test_radar_leader_tradability_provider_delivery tests.test_radar_leader_tradability_rqdata_poc tests.test_radar_leader_tradability_rqdata_live_poc -v`

Expected: PASS。

- [x] **Step 4: Run leader and backend regression without production SQLite**

先运行所有`test_radar_leader_*`；再复用项目既有导入前SQLite重定向方式运行完整`backend/tests`。任何测试不得打开生产数据库。

- [x] **Step 5: Run static and diff checks**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m py_compile radar/sources/leader_tradability_public_poc.py tests/test_radar_leader_tradability_public_poc.py`

Run: `git diff --check`

Expected: PASS；敏感词扫描不命中账号、密码、Token或上游响应正文。

- [x] **Step 6: Update the single handoff accurately**

记录C4C-1已完成、真实公开源调用仍未执行、免费路径仍非正式、RQData保留备用且官方HTTP Token为当天有效。不得把Fixture通过写成真实来源通过。

### 独立审查加固记录

- [x] 五轮只读对抗审查已完成，最终无P1/P2 findings。
- [x] 来源合同已绑定能力、允许字段、来源名称、官方域名和交易所；腾讯逐行来源及批次规范化SHA-256也已绑定。
- [x] 上市交易日计数改由交易所主档上市日期和最近6个官方交易日推导，不接受调用方整数。
- [x] 官方主证据、聚合交叉证据、静态生效区间、上游时间、上海时区、`UNKNOWN`、规则不可计算和逐字段来源审计均已冻结。
- [x] 最终专项43项、C4相关98项、全部龙头436项、隔离生产SQLite的完整后端1020项通过。

## Completion Boundary

C4C-1完成只表示隔离组合与冲突合同可用。下一步C4C-2必须在有效A股交易时段，
以不超过8只样本执行一次真实免费源POC；该调用只读公开来源、不读写生产SQLite，
并仍保持正式评分、门禁和状态迁移关闭。

## C4C-2A 交易日前准备包

- [x] 新增 `leader_tradability_public_live_poc.py`：只读组装交易所证券主档、
  上交所A股官方休市页、腾讯同批行情和AKShare公开聚合备证，不连接数据库，
  不导入RQData，不接运行时。
- [x] 新增 `run_public_tradability_poc.py`：只有显式确认、1至8只合法代码、
  官方交易日和A股连续交易窗口全部通过后，才启动35秒总超时的隔离worker。
- [x] 真实结果只输出字段覆盖、来源状态、稳定原因码和耗时，不输出逐股记录、
  上游正文、请求参数、异常正文或凭证；所有正式开关继续固定为`false`。
- [x] 上交所官方A股休市页只作为沪深共同交易日窗口证据，不冒充深交所证券
  状态主证据；深交所证券身份仍绑定深交所官方列表。
- [x] 静态停复牌合同与盘中动态状态时效分离：明确生效区间覆盖当日的静态
  停牌证据不套90秒，动态状态仍受90秒限制；当日复牌记录不得标成停牌。
- [x] 2026-08-02周日闭市干跑返回退出码2、
  `not_run / public_live_calendar_closed`，没有启动worker或执行真实来源请求。

C4C-2A只代表真实POC入口和证据包装已准备完成。C4C-2B仍须在下一有效A股
连续交易时段执行最多8只样本，并按真实字段覆盖、时效、来源失败和冲突给出
`field_candidate/partial/blocked/no_data`结论；普通行情存在不得推断正常交易。
