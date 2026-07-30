# 阶段 6L-C1 证券生命周期与可交易性研究合同 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从现有腾讯全市场行情响应补齐基础价格字段，并用版本化生命周期、当日交易状态和涨跌停规则生成不改变正式门禁的可交易性研究证据。

**Architecture:** 行情适配器只增加同响应字段，不增加网络请求；新纯计算模块负责身份、时间、有效期、排除状态和涨跌停语义；阶段 6 运行时只消费调用方可选提供的结构化证据并投影到 `researchFeatures.securityTradability`。

**Tech Stack:** Python 3.9、Pydantic、dataclasses、Enum、Decimal、zoneinfo、unittest、现有阶段 6 运行时合同。

## Global Constraints

- 不读取或写入生产 SQLite；所有可能导入 `main` 的测试必须在导入前把生产数据库精确路径重定向到临时 SQLite。
- 不新增网络请求、依赖、数据库迁移、仓储、API、前端、调度、提醒、AI、环境变量或系统配置。
- 不根据股票简称猜测 ST、退市整理、停牌或异常状态。
- 不按板块名称和固定百分比自行计算涨跌停边界。
- 不修改正式六维分数、硬门禁、状态机开关或只读 API 的真实结果。
- 不停止、重启或重载 4000/8001。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

---

### Task 1: 腾讯同响应基础价格字段

**Files:**
- Modify: `backend/radar/contracts.py`
- Modify: `backend/radar/sources/tencent_quotes.py`
- Modify: `backend/tests/test_radar_sources.py`

**Interfaces:**
- Produces: `QuoteSnapshot.previous_close`、`open_price`、`high_price` 和 `low_price`。
- Preserves: 现有 `QuoteSnapshot.REQUIRED_FIELDS`、来源健康覆盖率和请求批次行为。

- [x] **Step 1: 写同响应字段和单次请求失败测试**

扩展 `tencent_line()`，让 4、5、33、34 号字段可配置：

```python
def tencent_line(
    code,
    previous_close="9.90",
    open_price="9.95",
    high_price="10.20",
    low_price="9.80",
    **existing,
):
    fields[4] = previous_close
    fields[5] = open_price
    fields[33] = high_price
    fields[34] = low_price
```

新增测试：

```python
def test_same_response_preserves_ohlc_without_extra_request(self):
    session = FakeSession(lambda _url, _call: tencent_line("000001"))
    batch = fetch_tencent_quotes(
        ["000001"],
        radar_run_id="run-1",
        batch_id="quote-1",
        as_of=AS_OF,
        session=session,
        clock=lambda: FETCHED_AT,
    )

    quote = batch.items[0]
    self.assertEqual(len(session.calls), 1)
    self.assertEqual(quote.previous_close, 9.90)
    self.assertEqual(quote.open_price, 9.95)
    self.assertEqual(quote.high_price, 10.20)
    self.assertEqual(quote.low_price, 9.80)
    self.assertNotIn("previous_close", quote.REQUIRED_FIELDS)
```

再用子测试确认 `"0"` 保留为 `0.0`，空字符串、`nan`、`inf` 和负值返回
`None`。

- [x] **Step 2: 运行来源专项并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_sources.TencentQuoteSourceTests -v
```

Expected: 新字段属性或解析断言失败；既有测试不应先失败。

- [x] **Step 3: 最小扩展行情合同和解析**

在 `QuoteSnapshot` 增加可选非负有限字段：

```python
previous_close: Optional[float] = Field(
    default=None,
    alias="previousClose",
    ge=0,
    allow_inf_nan=False,
)
open_price: Optional[float] = Field(
    default=None,
    alias="openPrice",
    ge=0,
    allow_inf_nan=False,
)
high_price: Optional[float] = Field(
    default=None,
    alias="highPrice",
    ge=0,
    allow_inf_nan=False,
)
low_price: Optional[float] = Field(
    default=None,
    alias="lowPrice",
    ge=0,
    allow_inf_nan=False,
)
```

在腾讯适配器新增：

```python
def _optional_non_negative_finite(value):
    parsed = _optional_float(value)
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed
```

从字段 4、5、33、34 构造 `QuoteSnapshot`。不要修改 `REQUIRED_FIELDS`。

- [x] **Step 4: 运行来源专项并确认绿灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_sources.TencentQuoteSourceTests -v
```

Expected: 全部通过；请求次数和现有覆盖率语义不变。

### Task 2: 生命周期与当日规则纯合同

**Files:**
- Create: `backend/radar/leader_tradability_features.py`
- Create: `backend/tests/test_radar_leader_tradability_features.py`

**Interfaces:**
- Consumes: `QuoteSnapshot`、`ResearchFeatureStatus`。
- Produces: `SecurityLifecycleStatus`、`TradingSessionStatus`、
  `PriceLimitMode`、`PriceLimitState`、`OnePriceLimitState`、
  `LeaderSecurityLifecycleEvidence`、`LeaderTradingStatusEvidence`、
  `LeaderTradingRuleEvidence`、`LeaderTradabilityFeatureInput`、
  `LeaderTradabilityFeatureResult`、`build_leader_tradability_features()`
  和 `missing_leader_tradability_features()`。

- [x] **Step 1: 写正常交易和明确排除失败测试**

固定上海时间：

```python
AS_OF = datetime(2026, 7, 27, 10, 0, tzinfo=SHANGHAI_TZ)
```

正常输入必须显式包含：

```python
LeaderTradabilityFeatureInput(
    as_of=AS_OF,
    quote=quote(
        price=10.0,
        previousClose=9.5,
        openPrice=9.8,
        highPrice=10.2,
        lowPrice=9.7,
    ),
    quote_source_contract_id="tencent-full-market-quote-v1:batch-1",
    quote_source_status=ResearchFeatureStatus.READY,
    lifecycle=LeaderSecurityLifecycleEvidence(
        symbol="000001",
        exchange="szse",
        board="主板",
        lifecycle_status=SecurityLifecycleStatus.NORMAL,
        listed_trading_day_count=100,
        source_contract_id="exchange-security-lifecycle-v1:000001",
        source_name="深圳证券交易所",
        source_url="https://www.szse.cn/market/product/stock/list/",
        document_id="security-lifecycle-000001",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=None,
        fetched_at=AS_OF - timedelta(minutes=5),
    ),
    trading_status=LeaderTradingStatusEvidence(
        symbol="000001",
        trading_date=AS_OF.date(),
        status=TradingSessionStatus.TRADING,
        source_contract_id="exchange-trading-status-v1:000001:20260727",
        source_name="深圳证券交易所",
        source_time=AS_OF - timedelta(seconds=10),
        fetched_at=AS_OF - timedelta(seconds=5),
    ),
    trading_rule=LeaderTradingRuleEvidence(
        symbol="000001",
        trading_date=AS_OF.date(),
        rule_version="cn-equity-price-limit-rule-v1",
        price_limit_mode=PriceLimitMode.BOUNDED,
        upper_limit_price=10.45,
        lower_limit_price=8.55,
        source_contract_id="exchange-price-limit-v1:000001:20260727",
        source_name="深圳证券交易所",
        source_url="https://www.szse.cn/lawrules/rule/stock/",
        published_at=AS_OF - timedelta(days=30),
        effective_from=AS_OF - timedelta(days=20),
        effective_until=None,
    ),
)
```

断言：

```python
self.assertEqual(result.status, ResearchFeatureStatus.READY)
self.assertTrue(result.research_eligible)
self.assertFalse(result.preliminary_only)
self.assertEqual(result.price_limit_state, PriceLimitState.NORMAL)
self.assertFalse(result.to_evidence()["scoreReady"])
self.assertFalse(result.to_evidence()["formalUsable"])
```

分别测试 `SUSPENDED`、`ST`、`STAR_ST`、`DELISTING` 和 `ABNORMAL`
得到真实 `researchEligible=false`，而不是 `missing`。

- [x] **Step 2: 运行纯合同专项并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_tradability_features -v
```

Expected: 因新模块不存在而导入失败。

- [x] **Step 3: 实现最小枚举、数据类和成功路径**

公式版本固定为：

```python
LEADER_TRADABILITY_FEATURE_VERSION = (
    "radar-leader-tradability-feature-v1"
)
```

输出不得包含上游完整响应或原始证券主档，只保留来源合同、文档身份、规则版本
和有效期。所有结果固定：

```python
"scoreReady": False
"formalUsable": False
"researchScore": None
```

正常、停牌和明确排除测试先通过。

- [x] **Step 4: 写新股、涨跌停和一字板失败测试**

覆盖：

```python
for count, eligible, preliminary_only in (
    (5, False, False),
    (6, True, True),
    (10, True, True),
    (11, True, False),
):
    with self.subTest(listed_trading_day_count=count):
        result = build_leader_tradability_features(
            replace(
                ready_input(),
                lifecycle=replace(
                    ready_input().lifecycle,
                    listed_trading_day_count=count,
                ),
            )
        )
        self.assertEqual(result.research_eligible, eligible)
        self.assertEqual(result.preliminary_only, preliminary_only)
```

有界规则分别测试：

- 现价等于涨停价；
- 现价等于跌停价；
- 开盘、最高、最低和现价全部等于涨停价；
- 开盘、最高、最低和现价全部等于跌停价。

无涨跌幅限制规则必须输出 `PriceLimitState.NO_LIMIT` 和
`OnePriceLimitState.NOT_APPLICABLE`。

- [x] **Step 5: 实现新股和价格状态判定**

价格比较使用：

```python
PRICE_TOLERANCE = Decimal("0.005")
```

仅比较调用方提供的当日上下限价格，不从板块或百分比重新计算。连续一字板研究
上排除；普通触及涨停或跌停保留为受限状态，不自动排除。

- [x] **Step 6: 写来源与身份失败分支测试**

至少覆盖：

- 证券代码不一致；
- 交易日不一致；
- 未来发布时间和来源时间；
- 无效有效期；
- 已过期生命周期或规则；
- 非 HTTPS 生命周期或规则材料；
- `bounded` 缺任一边界；
- `no_limit` 携带边界；
- 交易状态过期；
- 行情时间过期；
- 来源失败优先于缺字段；
- 正常交易但价格为 0 的来源冲突。

- [x] **Step 7: 实现统一验证顺序并跑绿**

验证顺序固定为：

```text
显式来源失败
→ 时区与身份
→ 发布时间和有效区间
→ 当日交易状态时效
→ 规则结构
→ 生命周期真实排除
→ 行情完整性
→ 涨跌停和一字状态
```

运行：

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_tradability_features -v
```

Expected: 全部通过。

### Task 3: 阶段 6 运行时研究投影

**Files:**
- Modify: `backend/radar/leader_runtime_inputs.py`
- Modify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: `tradability_inputs_by_symbol: Optional[Mapping[str, LeaderTradabilityFeatureInput]]`。
- Produces: `researchFeatures.securityTradability`。
- Preserves: 正式维度分数、全部门禁和状态机输出。

- [x] **Step 1: 写默认缺失与完整证据失败测试**

新增运行时测试：

```python
def test_runtime_keeps_security_tradability_missing_without_input(self):
    evidence = build_full_research().evidence_items[0]
    value = evidence.evidence["researchFeatures"]["securityTradability"]
    self.assertEqual(value["status"], "missing")
    self.assertFalse(evidence.gates.stock_gate_passed)
    self.assertFalse(evidence.gates.tradability_passed)
```

完整输入测试断言：

```python
value = evidence.evidence["researchFeatures"]["securityTradability"]
self.assertEqual(value["status"], "ready")
self.assertTrue(value["researchEligible"])
self.assertFalse(value["formalUsable"])
self.assertFalse(evidence.gates.stock_gate_passed)
self.assertFalse(evidence.gates.tradability_passed)
self.assertIsNone(dimension_by_name["liquidity_tradability"].score)
```

再测试 `as_of` 和证券身份错配保持 `source_unverified`。

- [x] **Step 2: 运行运行时专项并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: `build_leader_runtime_evidence()` 不接受新参数或研究字段不存在。

- [x] **Step 3: 实现可选映射与研究投影**

新增可选参数并默认空映射。只有 `as_of` 和证券身份一致时调用纯计算模块；
否则使用 `missing_leader_tradability_features()` 返回明确原因。

在 `researchFeatures` 中增加：

```python
"securityTradability": tradability_result.to_evidence()
```

不得修改 `_dimensions()` 的正式输出和 `LeaderGateInput` 的任何值。

- [x] **Step 4: 运行运行时专项并确认绿灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: 新增和既有 B1、B2、B3 运行时测试全部通过。

### Task 4: 隔离回归和交接同步

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Verify: C1 代码、测试、规格和计划文件。

**Interfaces:**
- Produces: 可重复的专项、龙头链路、完整回归和边界证据。

- [x] **Step 1: 运行 C1 专项测试**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_sources.TencentQuoteSourceTests \
  tests.test_radar_leader_tradability_features \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: 0 failures，0 errors。

- [x] **Step 2: 临时 SQLite 隔离运行全部龙头测试**

在导入 `database` 前包装 `sqlite3.connect`：仅当绝对路径等于
`backend/data/stock_monitor.db` 时重定向到临时文件，再发现运行：

```python
unittest.defaultTestLoader.discover(
    "tests",
    pattern="test_radar_leader_*.py",
)
```

Expected: 全部通过，生产 SQLite 未被打开。

- [x] **Step 3: 临时 SQLite 隔离运行完整后端测试**

使用同一精确路径保护，发现运行：

```python
unittest.defaultTestLoader.discover(
    "tests",
    pattern="test_*.py",
)
```

Expected: 最终结果 `OK`；既有受控降级日志不等于失败。

- [x] **Step 4: 运行语法和差异检查**

```bash
PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache-c1 \
  backend/venv/bin/python -m py_compile \
  backend/radar/leader_tradability_features.py \
  backend/radar/leader_runtime_inputs.py \
  backend/radar/contracts.py \
  backend/radar/sources/tencent_quotes.py \
  backend/tests/test_radar_leader_tradability_features.py \
  backend/tests/test_radar_leader_runtime_inputs.py \
  backend/tests/test_radar_sources.py

git diff --check
git status --short --branch
```

Expected: 语法和差异检查通过；阶段 6 累积修改保持未暂存。

- [x] **Step 5: 更新唯一交接文档**

记录：

- C1 合同、同响应字段和运行时研究投影；
- 正式门禁、数据库、来源 POC 和生产阶段 6 仍未启用；
- 专项、全部龙头和完整后端测试数量；
- 4000/8001 保持原进程；
- 未读取或写入生产 SQLite，未执行 Git 写操作；
- 下一步为阶段 6L-C2 真实生命周期、停牌和规则来源只读 POC；
- 旧单股历史资金源可靠性修复仍为独立待办；
- 阶段 5 观察继续，2026-07-27 不计合格日。
