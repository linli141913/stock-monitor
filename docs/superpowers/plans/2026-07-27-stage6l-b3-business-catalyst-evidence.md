# 阶段 6L-B3 主营与催化版本化研究证据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 校验官方披露与版本化人工审核映射，生成不改变正式分数和状态机的主营催化研究证据。

**Architecture:** 新增独立纯计算模块，负责证据身份、时间、有效期、引用和冲突校验；阶段 6 运行时只消费调用方可选提供的结构化证据，并把压缩结果投影到现有研究证据。首版不抓取材料、不连接数据库、不接正式来源门禁。

**Tech Stack:** Python 3.9、dataclasses、Enum、unittest、现有 `ResearchFeatureStatus` 和阶段 6 运行时合同。

## Global Constraints

- 不读取或写入生产 SQLite；所有可能导入 `main` 的测试必须在导入前把生产数据库精确路径重定向到临时 SQLite。
- 不新增依赖、数据库迁移、仓储、抓取任务、调度、环境变量、API、前端、提醒或 AI。
- 不使用东方财富经营范围、新闻、主题标签或 AI 作为主营催化通过依据。
- 不生成正式 10 分，不设置 `business_exposure_source_contract_id`，不升级 `business_exposure_status`。
- 不改变状态机 `blocked/out` 和只读 API `not_ready` 的真实结果。
- 不停止或重载当前 4000/8001 服务。
- 不执行 Git 暂存、提交或推送；本计划中的每个任务只保留未暂存本地修改。
- 阶段 5 的 20 个交易日观察继续并行。

---

### Task 1: 主营催化证据合同与成功路径

**Files:**
- Create: `backend/radar/leader_business_catalyst_features.py`
- Create: `backend/tests/test_radar_leader_business_catalyst_features.py`

**Interfaces:**
- Consumes: `radar.leader_research_features.ResearchFeatureStatus`。
- Produces: `BusinessCatalystRelation`、`BusinessProofType`、`BusinessEvidenceSourceKind`、`LeaderCatalystReference`、`LeaderBusinessProof`、`LeaderBusinessCatalystReview`、`LeaderBusinessCatalystFeatureInput`、`LeaderBusinessCatalystFeatureResult`、`build_leader_business_catalyst_features()` 和 `missing_leader_business_catalyst_features()`。

- [x] **Step 1: 写官方直证和人工审核映射失败测试**

在 `backend/tests/test_radar_leader_business_catalyst_features.py` 创建固定时点：

```python
AS_OF = datetime(2026, 7, 27, 2, 0, tzinfo=timezone.utc)


def reviewed_input(
    relation=BusinessCatalystRelation.HIGHLY_RELATED,
):
    catalyst = LeaderCatalystReference(
        catalyst_id="catalyst-66-20260727",
        industry_code="66",
        industry_release_id="release-1",
        source_kind=(
            BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE
        ),
        source_name="深圳证券交易所",
        source_url="https://www.szse.cn/disclosure/catalyst-1",
        document_id="catalyst-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=AS_OF + timedelta(days=30),
        summary="行业催化结构化摘要",
    )
    proof = LeaderBusinessProof(
        evidence_id="business-proof-1",
        evidence_version="business-proof-v1",
        symbol="000001",
        proof_type=BusinessProofType.PRODUCT,
        source_kind=(
            BusinessEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
        ),
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-26/business-proof-1.PDF"
        ),
        document_id="business-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=None,
        related_catalyst_ids=(),
        fact_summary="主营产品结构化摘要",
    )
    review = LeaderBusinessCatalystReview(
        review_id="review-1",
        mapping_version="mapping-v1",
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst_id=catalyst.catalyst_id,
        relation=relation,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=AS_OF - timedelta(hours=1),
        effective_until=AS_OF + timedelta(days=30),
        basis_evidence_ids=(proof.evidence_id,),
        basis_catalyst_id=catalyst.catalyst_id,
        decision_summary="人工审核映射摘要",
    )
    return LeaderBusinessCatalystFeatureInput(
        as_of=AS_OF,
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst=catalyst,
        business_proofs=(proof,),
        reviews=(review,),
        source_status=ResearchFeatureStatus.READY,
    )


def official_direct_input():
    value = reviewed_input()
    proof = replace(
        value.business_proofs[0],
        related_catalyst_ids=(value.catalyst.catalyst_id,),
    )
    return replace(
        value,
        business_proofs=(proof,),
        reviews=(),
    )
```

提供两个互相独立的成功测试：

```python
def test_official_disclosure_direct_relation_is_ready_research_only(self):
    result = build_leader_business_catalyst_features(
        official_direct_input()
    )
    evidence = result.to_evidence()

    self.assertEqual(result.status, ResearchFeatureStatus.READY)
    self.assertEqual(result.relation, BusinessCatalystRelation.DIRECT)
    self.assertFalse(evidence["scoreReady"])
    self.assertFalse(evidence["formalUsable"])
    self.assertIsNone(evidence["researchScore"])
    self.assertNotIn("factSummary", str(evidence))


def test_manual_reviewed_mapping_can_be_highly_related(self):
    result = build_leader_business_catalyst_features(
        reviewed_input(
            relation=BusinessCatalystRelation.HIGHLY_RELATED
        )
    )

    self.assertEqual(result.status, ResearchFeatureStatus.READY)
    self.assertEqual(
        result.relation,
        BusinessCatalystRelation.HIGHLY_RELATED,
    )
```

`official_direct_input()` 的主营证明必须在
`related_catalyst_ids=("catalyst-66-20260727",)` 中显式引用同一催化；
`reviewed_input()` 必须由 `review_method="manual"` 的审核记录引用输入内
真实存在的主营证明 ID。

- [x] **Step 2: 运行专项测试并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_business_catalyst_features -v
```

Expected: 因 `radar.leader_business_catalyst_features` 不存在而导入失败。

- [x] **Step 3: 实现最小数据结构**

在 `backend/radar/leader_business_catalyst_features.py` 定义：

```python
LEADER_BUSINESS_CATALYST_FEATURE_VERSION = (
    "radar-leader-business-catalyst-feature-v1"
)


class BusinessCatalystRelation(str, Enum):
    DIRECT = "direct"
    HIGHLY_RELATED = "highly_related"
    UNCONFIRMED = "unconfirmed"
    DISPROVED = "disproved"


class BusinessProofType(str, Enum):
    REVENUE = "revenue"
    PRODUCT = "product"
    ORDER = "order"
    CAPACITY = "capacity"
    CUSTOMER = "customer"
    OTHER_OFFICIAL = "other_official"


class BusinessEvidenceSourceKind(str, Enum):
    COMPANY_DISCLOSURE = "company_disclosure"
    EXCHANGE_DISCLOSURE = "exchange_disclosure"
    DESIGNATED_DISCLOSURE_PLATFORM = (
        "designated_disclosure_platform"
    )


@dataclass(frozen=True)
class LeaderCatalystReference:
    catalyst_id: str
    industry_code: str
    industry_release_id: str
    source_kind: BusinessEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    summary: str


@dataclass(frozen=True)
class LeaderBusinessProof:
    evidence_id: str
    evidence_version: str
    symbol: str
    proof_type: BusinessProofType
    source_kind: BusinessEvidenceSourceKind
    source_name: str
    source_url: str
    document_id: str
    published_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime]
    related_catalyst_ids: Tuple[str, ...]
    fact_summary: str


@dataclass(frozen=True)
class LeaderBusinessCatalystReview:
    review_id: str
    mapping_version: str
    symbol: str
    industry_code: str
    industry_release_id: str
    catalyst_id: str
    relation: BusinessCatalystRelation
    review_method: str
    reviewer_key: str
    reviewed_at: datetime
    effective_until: Optional[datetime]
    basis_evidence_ids: Tuple[str, ...]
    basis_catalyst_id: str
    decision_summary: str


@dataclass(frozen=True)
class LeaderBusinessCatalystFeatureInput:
    as_of: datetime
    symbol: str
    industry_code: str
    industry_release_id: str
    catalyst: LeaderCatalystReference
    business_proofs: Tuple[LeaderBusinessProof, ...]
    reviews: Tuple[LeaderBusinessCatalystReview, ...]
    source_status: ResearchFeatureStatus


@dataclass(frozen=True)
class LeaderBusinessCatalystFeatureResult:
    status: ResearchFeatureStatus
    relation: BusinessCatalystRelation
    reasons: Tuple[str, ...] = ()
    references: Tuple[Mapping[str, Any], ...] = ()
```

`LeaderBusinessCatalystFeatureResult.to_evidence()` 只输出公式版本、状态、
关系、压缩文档身份、版本和有效期，不输出 `summary`、`fact_summary` 或
`decision_summary`；输出结构固定为：

```python
{
    "formulaVersion": LEADER_BUSINESS_CATALYST_FEATURE_VERSION,
    "status": self.status.value,
    "relation": self.relation.value,
    "scoreReady": False,
    "formalUsable": False,
    "researchScore": None,
    "references": [dict(item) for item in self.references],
    "reasons": list(self.reasons),
}
```

- [x] **Step 4: 实现两个成功判定**

`build_leader_business_catalyst_features(input_value)` 使用以下顺序：

```text
source_status不是ready
→ 原样返回来源状态

存在manual审核
→ 审核身份和引用有效
→ relation为direct/highly_related/disproved时返回ready
→ relation为unconfirmed时返回source_unverified

不存在审核
→ 至少一个官方主营证明显式引用当前catalyst_id
→ 返回ready/direct
→ 否则返回source_unverified/unconfirmed
```

缺失结果函数签名固定为：

```python
def missing_leader_business_catalyst_features(
    reasons: Sequence[str] = (
        "business_exposure_evidence_missing",
    ),
    *,
    status: ResearchFeatureStatus = ResearchFeatureStatus.MISSING,
) -> LeaderBusinessCatalystFeatureResult:
```

默认返回 `BusinessCatalystRelation.UNCONFIRMED`、空引用和压缩后的唯一原因。

- [x] **Step 5: 运行专项测试并确认绿灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_business_catalyst_features -v
```

Expected: 官方直证与人工审核成功测试通过，0 failures，0 errors。

### Task 2: 时间、身份、引用和冲突失败语义

**Files:**
- Modify: `backend/radar/leader_business_catalyst_features.py`
- Modify: `backend/tests/test_radar_leader_business_catalyst_features.py`

**Interfaces:**
- Consumes: Task 1 的 `LeaderBusinessCatalystFeatureInput`。
- Produces: 稳定的 `missing/stale/source_failed/source_unverified/ready` 和原因代码。

- [x] **Step 1: 写失败分支测试**

使用 `dataclasses.replace()` 和固定输入构造器覆盖失败行为：

```python
def test_contract_failures_keep_stable_status_and_reason(self):
    value = reviewed_input()
    proof = value.business_proofs[0]
    review = value.reviews[0]
    cases = (
        (
            replace(
                value,
                catalyst=replace(
                    value.catalyst,
                    published_at=AS_OF + timedelta(seconds=1),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "catalyst_published_at_future",
        ),
        (
            replace(
                value,
                business_proofs=(
                    replace(
                        proof,
                        effective_from=AS_OF + timedelta(seconds=1),
                    ),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_evidence_not_effective",
        ),
        (
            replace(
                value,
                business_proofs=(
                    replace(
                        proof,
                        effective_until=AS_OF - timedelta(seconds=1),
                    ),
                ),
            ),
            ResearchFeatureStatus.STALE,
            "business_evidence_expired",
        ),
        (
            replace(value, symbol="000002"),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_identity_mismatch",
        ),
        (
            replace(
                value,
                catalyst=replace(
                    value.catalyst,
                    industry_code="67",
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_catalyst_identity_mismatch",
        ),
        (
            replace(value, business_proofs=(proof, proof)),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_evidence_identity_duplicate",
        ),
        (
            replace(
                value,
                business_proofs=(
                    replace(proof, document_id=""),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_source_identity_missing",
        ),
        (
            replace(
                value,
                reviews=(
                    replace(
                        review,
                        basis_evidence_ids=("missing-evidence",),
                    ),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_review_reference_missing",
        ),
        (
            replace(
                value,
                reviews=(
                    replace(
                        review,
                        reviewed_at=AS_OF + timedelta(seconds=1),
                    ),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_reviewed_at_future",
        ),
        (
            replace(
                value,
                reviews=(
                    review,
                    replace(
                        review,
                        review_id="review-2",
                        mapping_version="mapping-v2",
                        relation=BusinessCatalystRelation.DISPROVED,
                    ),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_review_conflict",
        ),
        (
            replace(
                value,
                business_proofs=(
                    replace(proof, source_url="http://example.test/a"),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_source_url_unverified",
        ),
        (
            replace(
                value,
                reviews=(
                    replace(review, review_method="ai"),
                ),
            ),
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            "business_review_method_unverified",
        ),
    )
    for input_value, expected_status, expected_reason in cases:
        with self.subTest(expected_reason=expected_reason):
            result = build_leader_business_catalyst_features(
                input_value
            )
            self.assertEqual(result.status, expected_status)
            self.assertIn(expected_reason, result.reasons)
```

另写来源优先级和关系状态测试：

```python
def test_source_failure_has_priority(self):
    result = build_leader_business_catalyst_features(replace(
        reviewed_input(),
        source_status=ResearchFeatureStatus.SOURCE_FAILED,
    ))
    self.assertEqual(
        result.status,
        ResearchFeatureStatus.SOURCE_FAILED,
    )
    self.assertEqual(result.references, ())


def test_unconfirmed_and_disproved_remain_distinct(self):
    value = reviewed_input()
    review = value.reviews[0]
    unconfirmed = build_leader_business_catalyst_features(replace(
        value,
        reviews=(
            replace(
                review,
                relation=BusinessCatalystRelation.UNCONFIRMED,
            ),
        ),
    ))
    disproved = build_leader_business_catalyst_features(replace(
        value,
        reviews=(
            replace(
                review,
                relation=BusinessCatalystRelation.DISPROVED,
            ),
        ),
    ))
    self.assertEqual(
        unconfirmed.status,
        ResearchFeatureStatus.SOURCE_UNVERIFIED,
    )
    self.assertEqual(
        disproved.status,
        ResearchFeatureStatus.READY,
    )
    self.assertEqual(
        disproved.relation,
        BusinessCatalystRelation.DISPROVED,
    )
```

关键断言：

```python
self.assertEqual(result.status, ResearchFeatureStatus.STALE)
self.assertIn("business_evidence_expired", result.reasons)

self.assertEqual(disproved.status, ResearchFeatureStatus.READY)
self.assertEqual(
    disproved.relation,
    BusinessCatalystRelation.DISPROVED,
)

self.assertEqual(
    source_failed.status,
    ResearchFeatureStatus.SOURCE_FAILED,
)
self.assertEqual(source_failed.references, ())
```

同一 `as_of` 有两个有效且结论不同的审核版本时必须拒绝，不能自动选择
较新版本。

- [x] **Step 2: 运行失败分支测试并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_business_catalyst_features -v
```

Expected: 新增边界断言失败，原因是验证逻辑尚未完整实现。

- [x] **Step 3: 实现统一验证顺序**

按以下优先级返回，原因使用稳定英文代码：

```text
source_failed
→ missing
→ timezone/source identity/https/duplicate/identity mismatch
→ future/not effective/reference mismatch/review_method_not_manual
→ expired
→ conflicting review
→ disproved
→ direct/highly_related
→ unconfirmed
```

具体约束：

- `symbol` 必须是 6 位数字；
- `industry_code`、`industry_release_id`、`catalyst_id`、证据 ID、版本、
  文档 ID 和来源名必须非空；
- 原文 URL 必须是 `https://`；
- 所有时间必须含时区；
- `published_at/effective_from/reviewed_at <= as_of`；
- `effective_until` 存在时必须不早于 `effective_from/reviewed_at`；
- 当前有效区间使用
  `effective_from <= as_of <= effective_until`；
- 主营证明的 `symbol` 必须等于输入证券；
- 催化、审核和输入的行业代码、行业版本、催化 ID 必须完全一致；
- `review_method` 只能为 `"manual"`；
- `basis_evidence_ids` 必须非空且全部存在；
- 同一输入中的 `evidence_id`、`document_id`、`review_id` 和
  `mapping_version` 不得重复；
- 任意两个当前有效审核结论不同即返回
  `business_review_conflict`。

- [x] **Step 4: 运行专项测试并确认绿灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_business_catalyst_features -v
```

Expected: 成功、未确认、证伪、缺失、过期、未来、冲突和来源失败全部通过。

### Task 3: 阶段 6 运行时可选投影

**Files:**
- Modify: `backend/radar/leader_runtime_inputs.py`
- Modify: `backend/tests/test_radar_leader_runtime_inputs.py`

**Interfaces:**
- Consumes: `Mapping[str, LeaderBusinessCatalystFeatureInput]`。
- Produces: `researchFeatures.businessCatalyst`；不产生正式来源合同、分数或门禁。

- [x] **Step 1: 写运行时失败测试**

在 `LeaderRuntimeInputsTests` 增加：

```python
def test_ready_business_catalyst_is_research_only(self):
    assembly = build_leader_runtime_evidence(
        **runtime_inputs(),
        business_catalyst_inputs_by_symbol={
            "000001": valid_business_catalyst_input()
        },
    )
    item = next(
        value
        for value in assembly.evidence_items
        if value.symbol == "000001"
    )
    research = item.evidence["researchFeatures"]["businessCatalyst"]
    dimension = next(
        value
        for value in item.dimensions
        if value.field_name == "business_exposure"
    )

    self.assertEqual(research["status"], "ready")
    self.assertFalse(research["formalUsable"])
    self.assertIsNone(dimension.score)
    self.assertEqual(dimension.status, LeaderMetricStatus.MISSING)
    self.assertEqual(
        item.gates.business_exposure_status,
        BusinessExposureStatus.MISSING,
    )
    self.assertIsNone(item.business_exposure_source_contract_id)
```

在运行时测试文件增加 `from dataclasses import replace` 和 B3 合同导入，
并定义独立本地构造器，不从另一个测试模块导入测试辅助函数：

```python
def valid_business_catalyst_input():
    catalyst = LeaderCatalystReference(
        catalyst_id="catalyst-66-20260727",
        industry_code="66",
        industry_release_id="release-1",
        source_kind=(
            BusinessEvidenceSourceKind.EXCHANGE_DISCLOSURE
        ),
        source_name="深圳证券交易所",
        source_url="https://www.szse.cn/disclosure/catalyst-1",
        document_id="catalyst-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=AS_OF + timedelta(days=30),
        summary="行业催化结构化摘要",
    )
    proof = LeaderBusinessProof(
        evidence_id="business-proof-1",
        evidence_version="business-proof-v1",
        symbol="000001",
        proof_type=BusinessProofType.PRODUCT,
        source_kind=(
            BusinessEvidenceSourceKind.DESIGNATED_DISCLOSURE_PLATFORM
        ),
        source_name="巨潮资讯",
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-26/business-proof-1.PDF"
        ),
        document_id="business-document-1",
        published_at=AS_OF - timedelta(days=1),
        effective_from=AS_OF - timedelta(days=1),
        effective_until=None,
        related_catalyst_ids=(),
        fact_summary="主营产品结构化摘要",
    )
    review = LeaderBusinessCatalystReview(
        review_id="review-1",
        mapping_version="mapping-v1",
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst_id=catalyst.catalyst_id,
        relation=BusinessCatalystRelation.HIGHLY_RELATED,
        review_method="manual",
        reviewer_key="reviewer-local-1",
        reviewed_at=AS_OF - timedelta(hours=1),
        effective_until=AS_OF + timedelta(days=30),
        basis_evidence_ids=(proof.evidence_id,),
        basis_catalyst_id=catalyst.catalyst_id,
        decision_summary="人工审核映射摘要",
    )
    return LeaderBusinessCatalystFeatureInput(
        as_of=AS_OF,
        symbol="000001",
        industry_code="66",
        industry_release_id="release-1",
        catalyst=catalyst,
        business_proofs=(proof,),
        reviews=(review,),
        source_status=ResearchFeatureStatus.READY,
    )
```

身份错配和证伪测试写成：

```python
def test_business_catalyst_identity_mismatch_is_research_only(self):
    value = valid_business_catalyst_input()
    assembly = build_leader_runtime_evidence(
        **runtime_inputs(),
        business_catalyst_inputs_by_symbol={
            "000001": replace(
                value,
                industry_release_id="release-other",
            )
        },
    )
    item = next(
        candidate
        for candidate in assembly.evidence_items
        if candidate.symbol == "000001"
    )
    research = item.evidence["researchFeatures"]["businessCatalyst"]
    self.assertEqual(research["status"], "source_unverified")
    self.assertIn(
        "business_evidence_identity_mismatch",
        research["reasons"],
    )
    self.assertEqual(
        item.gates.business_exposure_status,
        BusinessExposureStatus.MISSING,
    )


def test_disproved_business_catalyst_does_not_change_formal_gate(self):
    value = valid_business_catalyst_input()
    review = replace(
        value.reviews[0],
        relation=BusinessCatalystRelation.DISPROVED,
    )
    assembly = build_leader_runtime_evidence(
        **runtime_inputs(),
        business_catalyst_inputs_by_symbol={
            "000001": replace(value, reviews=(review,))
        },
    )
    item = next(
        candidate
        for candidate in assembly.evidence_items
        if candidate.symbol == "000001"
    )
    research = item.evidence["researchFeatures"]["businessCatalyst"]
    self.assertEqual(research["status"], "ready")
    self.assertEqual(research["relation"], "disproved")
    self.assertEqual(
        item.gates.business_exposure_status,
        BusinessExposureStatus.MISSING,
    )
    self.assertIsNone(item.business_exposure_source_contract_id)
```

- [x] **Step 2: 运行运行时测试并确认红灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: 因 `business_catalyst_inputs_by_symbol` 参数或
`researchFeatures.businessCatalyst` 不存在而失败。

- [x] **Step 3: 实现可选运行时接入**

在 `build_leader_runtime_evidence()` 增加：

```python
business_catalyst_inputs_by_symbol: Optional[
    Mapping[str, LeaderBusinessCatalystFeatureInput]
] = None,
```

每个候选只执行一次：

```python
business_input = business_catalyst_inputs_by_symbol.get(quote.symbol)
if business_input is None:
    business_result = missing_leader_business_catalyst_features()
elif business_input.as_of != as_of:
    business_result = missing_leader_business_catalyst_features(
        ("business_evidence_as_of_mismatch",),
        status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
    )
elif (
    business_input.symbol != quote.symbol
    or business_input.industry_code != industry.division_code
    or business_input.industry_release_id
    != str(sector["industryReleaseId"])
):
    business_result = missing_leader_business_catalyst_features(
        ("business_evidence_identity_mismatch",),
        status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
    )
else:
    business_result = build_leader_business_catalyst_features(
        business_input
    )
```

写入：

```python
"businessCatalyst": business_result.to_evidence()
```

保留现有正式业务暴露维度、`LeaderGateInput` 和
`business_exposure_source_contract_id` 原值，不新增
`LeaderSourceEvidence`。

- [x] **Step 4: 运行运行时测试并确认绿灯**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: 新增 B3 证据、既有 B1/B2 证据和全部正式阻断测试通过。

### Task 4: 隔离回归、文档同步和边界检查

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Verify: 本计划涉及的代码、测试、规格和计划文件。

**Interfaces:**
- Produces: 可重复的专项、完整回归、语法和 Git 差异证据。

- [x] **Step 1: 运行 B3 与运行时专项测试**

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_business_catalyst_features \
  tests.test_radar_leader_runtime_inputs -v
```

Expected: 0 failures，0 errors。

- [x] **Step 2: 临时 SQLite 隔离运行全部龙头测试**

在导入 `database` 前包装 `sqlite3.connect`：仅当目标绝对路径等于
`backend/data/stock_monitor.db` 时重定向到临时文件，然后设置
`database.DB_PATH` 为同一临时路径，再发现并运行：

```python
unittest.defaultTestLoader.discover(
    "tests",
    pattern="test_radar_leader_*.py",
)
```

Expected: 全部通过；真实生产 SQLite 未被打开。

- [x] **Step 3: 临时 SQLite 隔离运行完整后端测试**

使用与 Step 2 相同的精确路径保护，发现并运行：

```python
unittest.defaultTestLoader.discover(
    "tests",
    pattern="test_*.py",
)
```

Expected: 全部通过；既有受控降级日志允许出现，但最终结果必须为 `OK`。

- [x] **Step 4: 运行语法和差异检查**

```bash
PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache-b3 \
  backend/venv/bin/python -m py_compile \
  backend/radar/leader_business_catalyst_features.py \
  backend/radar/leader_runtime_inputs.py \
  backend/tests/test_radar_leader_business_catalyst_features.py \
  backend/tests/test_radar_leader_runtime_inputs.py

git diff --check
git status --short --branch
```

Expected: 语法和差异检查通过；工作树保留阶段 6 累积未暂存修改，没有生产
数据、密钥、依赖、迁移、服务配置或无关新增操作。

- [x] **Step 5: 更新唯一交接文档**

在 `NEXT_CHAT_HANDOFF.md` 记录：

- B3 研究合同和运行时投影已完成；
- 正式 10 分、业务暴露门禁、专用有效期策略、版本仓储和生产阶段 6 仍未启用；
- 最终专项、全部龙头和完整后端测试数量；
- 未读取或写入生产 SQLite，未重载服务，未执行 Git 写操作；
- 下一开发项是阶段 6L 正式化缺口审计；
- 2026-07-27 收盘后仍需完成阶段 5 第 1 日观察日结。
