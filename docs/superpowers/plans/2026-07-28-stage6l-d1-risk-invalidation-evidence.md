# 阶段 6L-D1 风险与失效官方证据合同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现覆盖七类官方风险、官方解除关系和空结果覆盖证明的独立纯计算研究合同。

**Architecture:** 新增单一 `leader_risk_invalidation_features.py`，使用冻结
dataclass 和 Enum 表达覆盖证明、事件、解除证据、输入及结果。冻结来源合同、
适配器合同、官方域名、类别子类型和24小时覆盖新鲜度；构建函数先校验来源和
上市以来连续覆盖，再校验事件及分类专属字段，最后解析精确事件版本的官方解除
关系。所有输出保持研究态，不接入运行时和正式门禁。

**Tech Stack:** Python 3、标准库 dataclasses/enum/datetime/urllib、现有
`ResearchFeatureStatus`、unittest。

## Global Constraints

- 只操作唯一真实项目目录 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`。
- 不发网络请求，不读取或写入生产 SQLite，不读取环境变量。
- 不修改迁移、仓储、调度、API、前端、提醒、评分、状态机或正式门禁。
- `scoreReady=false`、`formalUsable=false`、`researchScore=null`。
- 不新增依赖，不停止或重载 4000/8001。
- 不执行 Git 暂存、提交或推送。
- 测试遵循严格 RED-GREEN：每组行为先观察预期失败，再写最小实现。

---

### Task 1: 冻结公共合同与空结果覆盖语义

**Files:**
- Create: `backend/tests/test_radar_leader_risk_invalidation_features.py`
- Create: `backend/radar/leader_risk_invalidation_features.py`

**Interfaces:**
- Consumes: `radar.leader_research_features.ResearchFeatureStatus`
- Produces: `RiskCategory`、`RiskEventSubtype`、`RiskEvidenceSourceKind`、
  `RiskOfficialStatus`、`RiskResolutionKind`、
  `LeaderRiskEvidenceCoverage`、`LeaderRiskEventEvidence`、
  `LeaderRiskResolutionEvidence`、`LeaderRiskInvalidationFeatureInput`、
  `LeaderRiskInvalidationFeatureResult`、
  `build_leader_risk_invalidation_features()`

- [x] **Step 1: 写空结果和覆盖失败测试**

```python
def test_complete_coverage_can_report_no_active_risk():
    result = build_leader_risk_invalidation_features(
        make_input(events=(), resolutions=())
    )
    self.assertEqual(result.status, ResearchFeatureStatus.READY)
    self.assertTrue(result.no_active_risk_observed)
    self.assertEqual(result.active_risk_categories, ())
    self.assertFalse(result.to_evidence()["scoreReady"])

def test_empty_events_without_all_categories_never_claims_no_risk():
    coverage = replace(
        make_coverage(),
        covered_categories=(RiskCategory.REDUCTION,),
    )
    result = build_leader_risk_invalidation_features(
        make_input(coverage=coverage, events=())
    )
    self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
    self.assertFalse(result.no_active_risk_observed)
    self.assertIn("risk_coverage_categories_incomplete", result.reasons)
```

- [x] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/codex-stock-pycache \
  venv/bin/python -m unittest \
  tests.test_radar_leader_risk_invalidation_features -v
```

Expected: FAIL，原因是
`radar.leader_risk_invalidation_features` 尚不存在。

- [x] **Step 3: 实现最小公共合同和覆盖校验**

```python
class RiskCategory(str, Enum):
    REDUCTION = "reduction"
    UNLOCK = "unlock"
    REGULATORY = "regulatory"
    INVESTIGATION = "investigation"
    LITIGATION = "litigation"
    EARNINGS = "earnings"
    AUDIT = "audit"

ALL_RISK_CATEGORIES = tuple(RiskCategory)

@dataclass(frozen=True)
class LeaderRiskInvalidationFeatureResult:
    status: ResearchFeatureStatus
    coverage_state: str
    active_risk_categories: Tuple[RiskCategory, ...] = ()
    no_active_risk_observed: bool = False
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = ()

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "formulaVersion": LEADER_RISK_INVALIDATION_FEATURE_VERSION,
            "status": self.status.value,
            "coverageState": self.coverage_state,
            "activeRiskCategories": [
                item.value for item in self.active_risk_categories
            ],
            "noActiveRiskObserved": self.no_active_risk_observed,
            "scoreReady": False,
            "formalUsable": False,
            "researchScore": None,
            "references": [dict(item) for item in self.references],
            "reasons": list(self.reasons),
        }
```

覆盖校验必须验证七类集合精确完整、冻结来源/适配器合同、官方域名与类别绑定、
身份一致、HTTPS 来源、带时区时间、上市以来连续覆盖、开放事件结转、
`window_from <= issuer_listed_at <= window_until <= checked_at <= as_of`、
窗口结束及检查时间距 `as_of` 不超过24小时、有效期从检查时间起不超过24小时，
以及 `coverage_complete=True`。

- [x] **Step 4: 运行专项测试并确认 GREEN**

Run: Task 1 Step 2 相同命令。

Expected: PASS。

### Task 2: 实现七类事件和分类专属校验

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_invalidation_features.py`
- Modify: `backend/radar/leader_risk_invalidation_features.py`

**Interfaces:**
- Consumes: Task 1 的公共合同和覆盖校验
- Produces: 七类有效事件、过期事件和分类错误的稳定研究结果

- [x] **Step 1: 写七类事件表驱动测试**

```python
def test_all_seven_official_risk_categories_are_preserved():
    events = (
        make_event(RiskCategory.REDUCTION, effective_until=AS_OF + DAY),
        make_event(RiskCategory.UNLOCK, effective_until=AS_OF + DAY),
        make_event(RiskCategory.REGULATORY),
        make_event(RiskCategory.INVESTIGATION),
        make_event(RiskCategory.LITIGATION),
        make_event(RiskCategory.EARNINGS, reporting_period="2026Q2"),
        make_event(RiskCategory.AUDIT, reporting_period="2025FY"),
    )
    result = build_leader_risk_invalidation_features(
        make_input(events=events)
    )
    self.assertEqual(result.status, ResearchFeatureStatus.READY)
    self.assertEqual(
        set(result.active_risk_categories),
        set(RiskCategory),
    )
    self.assertFalse(result.no_active_risk_observed)
```

分别新增断言：

- 减持、解禁缺 `effective_until` 返回 `source_unverified`；
- 业绩、审计缺 `reporting_period` 返回 `source_unverified`；
- HTTP 原文、未来发布时间、证券或发行人错配被拒绝；
- 已过期事件不进入有效类别，但压缩引用仍存在；
- 重复事件版本、重复文档和同时有效的冲突版本被拒绝。

- [x] **Step 2: 运行新增测试并确认 RED**

Run: Task 1 Step 2 相同命令。

Expected: FAIL，原因是事件尚未被校验和分类。

- [x] **Step 3: 实现分类校验和事件压缩**

实现 `_validate_event()`，公共校验包括：

```python
if event.symbol != input_value.symbol:
    reasons.append("risk_event_symbol_mismatch")
if event.issuer_identity != input_value.issuer_identity:
    reasons.append("risk_event_issuer_mismatch")
if not _https_url(event.source_url):
    reasons.append("risk_event_source_url_unverified")
if event.published_at > input_value.as_of:
    reasons.append("risk_event_published_in_future")
```

分类校验包括：

```python
if event.category in {
    RiskCategory.REDUCTION,
    RiskCategory.UNLOCK,
} and event.effective_until is None:
    reasons.append(
        f"risk_{event.category.value}_effective_until_missing"
    )
if event.category in {
    RiskCategory.EARNINGS,
    RiskCategory.AUDIT,
} and not _required_text(event.reporting_period):
    reasons.append(
        f"risk_{event.category.value}_reporting_period_missing"
    )
```

只把 `official_status=active`、已生效、未过期且未被解除的事件列入
`active_risk_categories`。其他事件保留压缩身份，不保存 `fact_summary`。

- [x] **Step 4: 运行专项测试并确认 GREEN**

Run: Task 1 Step 2 相同命令。

Expected: PASS。

### Task 3: 实现官方解除、来源失败和冲突优先级

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_invalidation_features.py`
- Modify: `backend/radar/leader_risk_invalidation_features.py`

**Interfaces:**
- Consumes: Task 2 的有效事件集合
- Produces: `completed`、`withdrawn`、`officially_cleared` 三类可关闭
  关系；`corrected` 在替代版本合同完成前稳定降级

- [x] **Step 1: 写解除与失败测试**

```python
def test_matching_official_resolution_closes_open_ended_event():
    event = make_event(RiskCategory.INVESTIGATION)
    resolution = make_resolution(
        target_event_id=event.event_id,
        target_event_version=event.event_version,
        effective_from=AS_OF - HOUR,
    )
    result = build_leader_risk_invalidation_features(
        make_input(events=(event,), resolutions=(resolution,))
    )
    self.assertEqual(result.status, ResearchFeatureStatus.READY)
    self.assertEqual(result.active_risk_categories, ())
    self.assertTrue(result.no_active_risk_observed)
    self.assertTrue(any(
        item["kind"] == "risk_resolution"
        for item in result.to_evidence()["references"]
    ))
```

分别新增断言：

- 调查、监管、诉讼没有解除时持续有效；
- 解除引用不存在、主体错配、发布时间早于事件时被拒绝；
- 同一事件存在多个有效且结论冲突的解除版本时被拒绝；
- `source_failed` 优先于覆盖和事件错误；
- 覆盖过期返回 `stale`；
- 输出不包含 `fact_summary`、`resolution_summary` 或自定义严重程度。

- [x] **Step 2: 运行新增测试并确认 RED**

Run: Task 1 Step 2 相同命令。

Expected: FAIL，原因是解除关系和错误优先级尚未实现。

- [x] **Step 3: 实现解除匹配和结果优先级**

处理顺序固定为：

```text
来源状态
→ 覆盖证明存在性和时效
→ 输入身份及重复/冲突
→ 风险事件公共与分类校验
→ 官方解除校验
→ 有效事件计算
→ 压缩研究输出
```

只允许满足以下条件的解除关闭事件：

```python
resolution.target_event_id == event.event_id
resolution.target_event_version == event.event_version
resolution.symbol == event.symbol
resolution.issuer_identity == event.issuer_identity
resolution.published_at >= event.published_at
resolution.effective_from <= input_value.as_of
```

`no_active_risk_observed` 只在完整有效覆盖且最终有效事件为空时为真。

### Task 3.5: 独立审查后的合同加固

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_invalidation_features.py`
- Modify: `backend/radar/leader_risk_invalidation_features.py`
- Modify: `docs/superpowers/specs/2026-07-28-stage6l-d1-risk-invalidation-evidence-design.md`

**Interfaces:**
- Consumes: Task 1-3 的研究合同
- Produces: 不会因短窗口、自报来源、更正记录或模糊版本引用错误声明无风险的
  收紧合同

- [x] **Step 1: 为独立审查发现补失败测试**

新增可复现输入并确认以下行为先失败：

```text
短于上市历史的覆盖不能声明无风险
窗口或检查时间超过24小时返回stale
自报来源合同和非官方域名返回source_unverified
corrected事件或解除记录不能关闭风险
解除必须引用精确event_id+event_version
事件和解除不得复用同一document_id
诉讼/调查缺case_id或类别子类型错配被拒绝
URL凭证被拒绝，查询参数和原始文档ID不进入输出
events/resolutions/categories缺失返回稳定状态
非字符串摘要和不可哈希类别返回source_unverified
非布尔覆盖完成标志和其他不可哈希嵌套字段稳定降级
```

- [x] **Step 2: 运行专项测试并确认 RED**

Run: Task 1 Step 2 相同命令。

Expected: FAIL，原因分别对应缺失字段、错误关闭、来源未注册、覆盖过短或异常。

- [x] **Step 3: 实现收紧合同**

实现冻结来源与适配器合同、官方域名注册表、类别子类型表、上市时间和开放事件
结转字段、24小时新鲜度、版本级解除引用、跨集合文档唯一、URL安全投影和缺失
集合前置校验。

关闭集合固定为：

```python
{
    RiskResolutionKind.COMPLETED,
    RiskResolutionKind.WITHDRAWN,
    RiskResolutionKind.OFFICIALLY_CLEARED,
}
```

`RiskOfficialStatus.CORRECTED` 和 `RiskResolutionKind.CORRECTED` 当前必须
返回 `source_unverified`，直到 D2 冻结替代版本链。

- [x] **Step 4: 运行专项测试并确认 GREEN**

Run: Task 1 Step 2 相同命令。

Expected: 22项全部 PASS。

- [x] **Step 5: 完成独立复审**

复验完成标志类型、不可哈希嵌套字段、版本解除、更正语义和安全输出。

Expected: 无 P1/P2 阻断。

### Task 4: 回归、静态验证与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Verify only: all files changed in Tasks 1-3

**Interfaces:**
- Consumes: 完整 D1 纯计算合同
- Produces: 可复现的测试证据和唯一阶段检查点

- [x] **Step 1: 运行 D1 专项与全部龙头测试**

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/codex-stock-pycache \
  venv/bin/python -m unittest \
  tests.test_radar_leader_risk_invalidation_features -v

PYTHONPYCACHEPREFIX=/tmp/codex-stock-pycache \
  venv/bin/python -m unittest discover \
  -s tests -p 'test_radar_leader*.py' -v
```

Expected: 全部 PASS。

- [x] **Step 2: 运行必要后端回归**

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/codex-stock-pycache \
  venv/bin/python -m unittest discover -s tests -v
```

Expected: 全部 PASS；任何导入 `main` 的测试仍先把生产数据库路径重定向到
临时 SQLite。

- [x] **Step 3: 运行语法与差异检查**

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/codex-stock-pycache \
  venv/bin/python -m py_compile \
  radar/leader_risk_invalidation_features.py \
  tests/test_radar_leader_risk_invalidation_features.py

cd ..
git diff --check
```

Expected: 无输出且退出码为 0。

- [x] **Step 4: 更新唯一交接文件**

在 `NEXT_CHAT_HANDOFF.md` 记录：

- 阶段 6L-D1 已完成的合同、分类和测试；
- D1 未接真实来源、运行时、仓储、API、前端和正式门禁；
- 阶段 5 观察继续并行；
- 下一步建议进入 D2 官方来源 POC，不把旧提醒或媒体内容冒充正式证据；
- 当前仍未获得 Git 暂存、提交、推送及生产操作授权。

- [x] **Step 5: 最终复核**

```bash
git status --short
git diff --stat
git diff --check
```

Expected: 只有当前累计授权范围内的工作树变化，没有 `.env`、数据库、日志、
密钥或构建产物。
