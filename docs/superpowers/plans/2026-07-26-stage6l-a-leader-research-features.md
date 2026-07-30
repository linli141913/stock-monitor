# Stage 6L-A Leader Research Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用现有真实当期聚合计算三个版本化龙头研究维度，同时保证部分研究分不能进入正式总分、状态机或公开榜单。

**Architecture:** 新增一个无网络、无数据库依赖的纯计算模块，负责百分位、行业强度、市场领先性和量比辅助分。阶段 6K 运行时组装器只把结果附加到候选证据，现有正式维度继续保持缺失，因而状态机仍阻断并且 API 继续 `not_ready`。

**Tech Stack:** Python 3、dataclasses、现有 Pydantic 雷达合同、`unittest`

## Global Constraints

- 公式版本固定为 `radar-leader-research-feature-v1`。
- `participatingWeight=55`、`requiredFormalWeight=95`、`scoreReady=false`。
- 不新增依赖、数据库迁移、公开 API 或前端改动。
- 不访问或写入生产数据库，不停止或重启 4000/8001。
- 不执行 `git add`、`git commit` 或 `git push`。
- 北交所不进入候选或任何百分位分母。
- 研究分只进入 `evidence.researchFeatures`，不得进入现有正式 `score`。

---

### Task 1: 纯研究特征合同与公式

**Files:**
- Create: `backend/radar/leader_research_features.py`
- Test: `backend/tests/test_radar_leader_research_features.py`

**Interfaces:**
- Produces: `average_tie_percentile(value, population) -> Optional[float]`
- Produces: `build_leader_research_features(...) -> LeaderResearchFeatureResult`
- Produces: `LeaderResearchFeatureResult.to_evidence() -> Dict[str, Any]`
- Consumes: `QuoteSnapshot`、`SecurityMasterRecord`、行业和市场聚合只读映射

- [x] **Step 1: 写百分位和合同失败测试**

```python
def test_average_tie_percentile_preserves_ties_and_zero():
    self.assertEqual(average_tie_percentile(0.0, (0.0, 1.0, 2.0)), 0.0)
    self.assertEqual(average_tie_percentile(1.0, (0.0, 1.0, 1.0, 2.0)), 0.5)
    self.assertEqual(average_tie_percentile(1.0, (1.0, 1.0, 1.0)), 0.5)
    self.assertIsNone(average_tie_percentile(1.0, (1.0,)))

def test_result_contract_never_marks_partial_research_score_ready():
    result = build_leader_research_features(...)
    payload = result.to_evidence()
    self.assertEqual(payload["participatingWeight"], 55)
    self.assertEqual(payload["requiredFormalWeight"], 95)
    self.assertFalse(payload["scoreReady"])
```

- [x] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_research_features
```

Expected: `ModuleNotFoundError: radar.leader_research_features`

- [x] **Step 3: 实现最小合同和公共百分位**

```python
LEADER_RESEARCH_FEATURE_VERSION = "radar-leader-research-feature-v1"
PARTICIPATING_WEIGHT = 55.0
REQUIRED_FORMAL_WEIGHT = 95.0

def average_tie_percentile(
    value: float,
    population: Sequence[float],
) -> Optional[float]:
    values = tuple(float(item) for item in population)
    if len(values) < 2:
        return None
    lower = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    if equal == 0:
        return None
    return min(1.0, max(
        0.0,
        (lower + (equal - 1) / 2) / (len(values) - 1),
    ))
```

`LeaderResearchFeatureResult.to_evidence()` 必须输出版本、部分研究分、权重、
`scoreReady=false`、维度明细和稳定原因码。

- [x] **Step 4: 增加公式失败测试**

```python
def test_industry_strength_uses_positive_returns_and_breadth():
    result = build_leader_research_features(...)
    dimension = result.dimension("industry_strength")
    self.assertEqual(dimension.maximum_score, 25.0)
    self.assertGreater(dimension.research_score, 0.0)

def test_negative_return_components_do_not_receive_percentile_score():
    result = build_leader_research_features(...)
    components = result.dimension("industry_strength").components
    self.assertEqual(components["equal_return"].score, 0.0)
    self.assertEqual(components["ex_top_return"].score, 0.0)

def test_volume_ratio_at_or_below_one_is_real_zero():
    result = build_leader_research_features(..., volume_ratio=1.0)
    self.assertEqual(result.dimension("auxiliary").research_score, 0.0)
```

- [x] **Step 5: 实现三个研究公式**

```python
industry_strength = (
    10 * positive_percentile(equal_return, sector_equal_returns)
    + 8 * clamp(up_ratio, 0, 1)
    + 7 * positive_percentile(ex_top_return, sector_ex_top_returns)
)
market_leadership = (
    10 * percentile(change_percent, industry_changes)
    + 8 * positive_percentile(contribution, industry_contributions)
    + 7 * positive_percentile(excess_return, market_excess_returns)
)
auxiliary = (
    0.0
    if volume_ratio <= 1
    else 5 * percentile(volume_ratio, market_volume_ratios)
)
```

板块指数通过 `exchange` 和 `board` 映射；`board` 包含“科创”时使用
`star50`，包含“创业”时使用 `chinext`，其余分别使用
`sse_composite` 和 `szse_component`。行业少于 20 个、行业成分少于 2
只或市场少于 100 只时，对应维度返回缺失。

- [x] **Step 6: 运行纯公式测试**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_research_features
```

Expected: all tests `OK`

### Task 2: 接入阶段 6K 运行时证据

**Files:**
- Modify: `backend/radar/leader_runtime_inputs.py`
- Modify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: `build_leader_research_features(...)`
- Produces: `LeaderInputEvidence.evidence["researchFeatures"]`
- Preserves: 现有 `dimensions` 正式评分输入仍全部不可用

- [x] **Step 1: 写运行时接入失败测试**

```python
def test_runtime_attaches_research_features_without_enabling_score():
    assembly = build_leader_runtime_evidence(...)
    feature_payload = assembly.evidence_items[0].evidence["researchFeatures"]
    self.assertFalse(feature_payload["scoreReady"])
    self.assertEqual(feature_payload["participatingWeight"], 55)
    self.assertTrue(all(
        dimension.score is None
        for dimension in assembly.evidence_items[0].dimensions
    ))

def test_runtime_ranking_population_is_not_limited_to_top_five():
    assembly = build_leader_runtime_evidence(...six_industry_quotes...)
    payload = assembly.evidence_items[0].evidence["researchFeatures"]
    self.assertEqual(
        payload["dimensions"]["market_leadership"]
        ["components"]["industry_return_rank"]["populationSize"],
        6,
    )
```

- [x] **Step 2: 运行接入测试并确认缺少 `researchFeatures`**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_runtime_inputs
```

Expected: FAIL with missing `researchFeatures`

- [x] **Step 3: 最小接入纯计算模块**

```python
research_features = build_leader_research_features(
    as_of=as_of,
    candidate_quote=quote,
    candidate_security=security,
    industry_quotes=grouped[division_code],
    market_quotes=tuple(quote_by_symbol.values()),
    security_by_symbol=security_by_symbol,
    sector=sector,
    sector_rows=sector_rows,
    market_snapshot=market_snapshot,
)

evidence={
    "runtimeInputVersion": "radar-leader-runtime-input-v1",
    "researchFeatures": research_features.to_evidence(),
    ...
}
```

把 `market_leadership_formula_not_frozen` 改为
`formal_leader_score_incomplete`，但不改变任何门槛布尔值或正式维度分数。

- [x] **Step 4: 补来源异常和排除范围测试**

```python
def test_runtime_keeps_not_ready_when_quote_source_is_stale():
    assembly = build_leader_runtime_evidence(...stale_quote_health...)
    self.assertEqual(assembly.status, "not_ready")

def test_bse_quote_is_excluded_from_research_population():
    assembly = build_leader_runtime_evidence(...with_bse_quote...)
    payload = assembly.evidence_items[0].evidence["researchFeatures"]
    self.assertNotIn("bse", str(payload))
```

- [x] **Step 5: 运行阶段 6L-A 和全部龙头专项测试**

Run:

```bash
cd backend
venv/bin/python -m unittest tests.test_radar_leader_research_features
venv/bin/python -m unittest tests.test_radar_leader_runtime_inputs
venv/bin/python -m unittest discover -s tests -p 'test_radar_leader*.py'
```

Expected: all tests `OK`

### Task 3: 完整回归与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Records: 真实 Git、服务和验证结果
- Preserves: 阶段 5 观察与阶段 6L-B 下一步

- [x] **Step 1: 运行语法、完整后端和差异检查**

Run:

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/codex-stage6l-a-pycache venv/bin/python -m py_compile radar/leader_research_features.py radar/leader_runtime_inputs.py
venv/bin/python -m unittest discover -s tests
cd ..
git diff --check
```

Expected: compile succeeds, full suite `OK`, diff check has no output.

- [x] **Step 2: 核对服务仍由原进程监听**

Run:

```bash
lsof -nP -iTCP:4000 -sTCP:LISTEN -iTCP:8001
```

Expected: 4000 PID 23280、8001 PID 90418 继续监听；不执行重启。

- [x] **Step 3: 更新唯一交接文件**

记录：

```text
阶段6L-A本地完成；
正式评分和状态继续not_ready；
未访问生产数据库、未迁移、未重启、未提交；
下一步阶段6L-B：历史连续性、流动性/可交易性和版本化主营催化证据。
```

- [x] **Step 4: 最终核对工作树**

Run:

```bash
git status --short --branch
git diff --stat
```

Expected: 仅保留阶段 6 累计本地改动，无暂存、提交或推送。
