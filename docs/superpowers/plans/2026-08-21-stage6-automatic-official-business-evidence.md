# 阶段6官方主营与催化证据全自动化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个无需逐股人工回填、可从真实主营来源包自动生成可重放官方主营与催化关系证据的失败关闭流水线。

**Architecture:** 先把长期官方证据与候选运行批次分离：选择并解码年报和催化 PDF，形成带内容哈希与页码的长期事实，再由确定性规则生成非人工验证工件。后续候选轮通过统一验证适配层重放长期证据，只有身份、行业、时间、文档和规则版本全部一致时才生成现有 `LeaderBusinessCatalystFeatureInput`；现有人工路径保持兼容。

**Tech Stack:** Python 3、标准库 `dataclasses/enum/hashlib/json/re/concurrent.futures`、现有 `requests`、现有 `pypdf==6.14.2`、`unittest`。

**Spec:** `docs/superpowers/specs/2026-08-21-stage6-automatic-official-business-evidence-design.md`

## Global Constraints

- 只操作 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`，不创建项目副本。
- 不读取或写入生产 SQLite；测试完整回归时把默认生产库连接精确重定向到 `/private/tmp` 临时库。
- 不新增或升级依赖，不修改环境变量、迁移、调度器、服务配置或正式门。
- 不调用付费 AI；AI 不参与提取、关系判断、评分或状态迁移。
- 自动工件不能创建或冒充 `human` 审核，现有人工路径保持兼容。
- 原始 PDF 不落盘；开发和真实验收工件仅写调用方显式指定的 `/private/tmp` 路径。
- 年报上限固定为 50 MiB、800 页、单页 100,000 字符、全文 8,000,000 字符；PDF 下载与解析最多 2 并发。
- 全集任一候选未就绪时不交付部分正式来源载荷；四个正式标志始终为 `false`。
- `git add/commit/push/PR` 需要新的明确授权；计划内仅记录本地检查点，不执行 Git 写操作。

---

## File Map

- Create `backend/radar/leader_business_automatic_contracts.py`: 自动证据共用状态、页文本、事实、长期验证工件和批次结果类型。
- Create `backend/radar/leader_business_annual_report_selector.py`: 最新有效中文完整版年报的确定性选择。
- Create `backend/radar/sources/leader_business_document_content.py`: 官方 PDF 身份、响应、容量和内存解码合同。
- Create `backend/radar/leader_business_document_facts.py`: 年报主营章节、行业身份和产品词组提取。
- Create `backend/radar/sources/leader_business_catalyst_official.py`: 六类催化公告元数据发现、去重和下载候选上限。
- Create `backend/radar/leader_business_catalyst_facts.py`: 催化事件与业务对象的页级提取。
- Create `backend/radar/leader_business_deterministic_verification.py`: 主营与催化精确词组关系判断和长期验证工件。
- Create `backend/radar/leader_business_official_verification_adapter.py`: 自动验证重放为现有研究输入的单只及全集适配。
- Create `backend/radar/leader_business_automatic_evidence.py`: 来源包加载、断点身份、并发编排和失败关闭汇总。
- Create `backend/run_leader_business_automatic_evidence.py`: 本地只读命令和脱敏 JSON 工件输出。
- Modify `backend/radar/leader_business_catalyst_features.py`: 允许受类型保护的 `deterministic_official` 验证方法，同时保留 `manual`。
- Modify `backend/radar/leader_business_catalyst_runtime_bridge.py`: 接受人工批次或确定性官方批次，拒绝混合和字符串伪装。
- Modify `backend/radar/leader_business_catalyst_production_collector.py`: 从两类合法验证批次推导真实来源时间。
- Modify `NEXT_CHAT_HANDOFF.md`: 记录实现、真实验收、测试和剩余独立门。

---

### Task 1: 共用合同与最新年报选择

**Files:**
- Create: `backend/radar/leader_business_automatic_contracts.py`
- Create: `backend/radar/leader_business_annual_report_selector.py`
- Test: `backend/tests/test_radar_leader_business_annual_report_selector.py`

**Interfaces:**
- Consumes: `LeaderRuntimeCandidatePlanItem`、`LeaderBusinessMaterialReviewQueueItem`、`OfficialBusinessMaterialDocument`。
- Produces: `AutomaticBusinessEvidenceStatus`、`OfficialBusinessDocumentKind`、`LeaderBusinessAnnualReportSelectionResult` 和 `select_latest_official_annual_report(plan_item, queue_item)`。

- [x] **Step 1: Write the failing selector tests**

```python
def test_latest_full_chinese_report_wins_over_summary_and_english():
    result = select_latest_official_annual_report(plan_item(), queue_item(
        documents=(
            document("2025年年度报告摘要", "1"),
            document("2025年年度报告（英文版）", "2"),
            document("2025年年度报告", "3"),
        )
    ))
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.document.document_id, "cninfo:3")

def test_latest_revision_replaces_original_and_ambiguous_pair_fails_closed():
    revised = select_latest_official_annual_report(
        plan_item(),
        queue_item(documents=(
            document("2025年年度报告", "1", published_day=1),
            document("2025年年度报告（修订版）", "2", published_day=2),
        )),
    )
    self.assertEqual(revised.replaced_document_ids, ("cninfo:1",))
    ambiguous = select_latest_official_annual_report(
        plan_item(),
        queue_item(documents=(
            document("2025年年度报告", "1", published_day=2),
            document("2025年年度报告", "2", published_day=2),
        )),
    )
    self.assertEqual(
        ambiguous.status,
        AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
    )
```

- [x] **Step 2: Run the selector test and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_annual_report_selector`

Expected: import failure because `radar.leader_business_annual_report_selector` does not exist.

- [x] **Step 3: Implement the contracts and selector**

```python
class AutomaticBusinessEvidenceStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"

class OfficialBusinessDocumentKind(str, Enum):
    ANNUAL_REPORT = "annual_report"
    CATALYST = "catalyst"

def select_latest_official_annual_report(plan_item, queue_item):
    if plan_item.symbol != queue_item.symbol:
        return _selection_unverified("annual_report_scope_unverified")
    candidates = tuple(
        parsed for document in queue_item.documents
        if (parsed := _parse_full_annual_report(document)) is not None
    )
    if not candidates:
        return _selection_missing("annual_report_missing")
    latest_year = max(item.report_year for item in candidates)
    return _choose_unique_latest_revision(
        tuple(item for item in candidates if item.report_year == latest_year)
    )
```

The implementation must return stable reasons `annual_report_scope_unverified`, `annual_report_missing`, or `annual_report_selection_ambiguous`; it must never pick by tuple order.

- [x] **Step 4: Run selector tests to green**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_annual_report_selector`

Expected: all selector tests pass.

- [x] **Step 5: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: only the already existing worktree plus Task 1 files; do not run `git add` or `git commit`.

---

### Task 2: 官方 PDF 受控下载与内存解码

**Files:**
- Create: `backend/radar/sources/leader_business_document_content.py`
- Test: `backend/tests/test_radar_leader_business_document_content.py`

**Interfaces:**
- Consumes: `LeaderBusinessAnnualReportSelectionResult.document` and later `OfficialBusinessCatalystDocument` through a common metadata projection.
- Produces: `OfficialBusinessDocumentHttpResponse`、`OfficialBusinessDocumentPage`、`OfficialBusinessDocumentContentResult` and `fetch_official_business_document_content(document, *, kind, fetched_at=None, transport=None)`。

- [x] **Step 1: Write failing security and decoding tests**

```python
def test_valid_official_pdf_returns_hash_pages_and_no_body_in_repr():
    result = fetch_official_business_document_content(
        document(),
        kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
        fetched_at=FETCHED_AT,
        transport=lambda *args, **kwargs: pdf_response(TEXT_PDF),
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.content_sha256, hashlib.sha256(TEXT_PDF).hexdigest())
    self.assertGreaterEqual(result.page_count, 1)
    self.assertNotIn("主营业务", repr(result))

def test_redirect_wrong_domain_oversize_encrypted_and_blank_text_fail_closed():
    for response, reason in invalid_pdf_cases():
        with self.subTest(reason=reason):
            result = fetch_official_business_document_content(
                document(),
                kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
                fetched_at=FETCHED_AT,
                transport=lambda *args, value=response, **kwargs: value,
            )
            self.assertEqual(
                result.status,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            )
            self.assertIn(reason, result.reasons)
```

- [x] **Step 2: Run PDF tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_document_content`

Expected: import failure because the content module does not exist.

- [x] **Step 3: Implement strict metadata, HTTP and PDF limits**

```python
MAXIMUM_PDF_BYTES = 50 * 1024 * 1024
MAXIMUM_PAGE_COUNT = 800
MAXIMUM_PAGE_CHARACTERS = 100_000
MAXIMUM_TOTAL_CHARACTERS = 8_000_000

def fetch_official_business_document_content(document, *, kind, fetched_at=None, transport=None):
    actual_fetched_at = fetched_at or datetime.now(UTC)
    metadata_reason = _validate_document_identity(document, kind, actual_fetched_at)
    if metadata_reason:
        return _content_unverified(document, actual_fetched_at, metadata_reason)
    response = (transport or _default_transport)(
        document.source_url,
        headers=OFFICIAL_PDF_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
        stream=True,
    )
    return _validate_and_decode_response(document, kind, response, actual_fetched_at)
```

Transport exceptions derived from `requests.RequestException` map to `source_failed`; parser/type/value errors map to `source_unverified`. Text-bearing test PDF bytes live only in the test helper and are not production fixtures.

- [x] **Step 4: Run PDF tests and the existing risk PDF suite**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_document_content backend.tests.test_radar_leader_risk_document_content`

Expected: both suites pass; risk PDF behavior remains unchanged.

- [x] **Step 5: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: Task 1–2 files are uncommitted; no Git writes.

---

### Task 3: 年报主营事实与行业身份提取

**Files:**
- Create: `backend/radar/leader_business_document_facts.py`
- Test: `backend/tests/test_radar_leader_business_document_facts.py`

**Interfaces:**
- Consumes: `LeaderRuntimeCandidatePlanItem`、`LeaderBusinessAnnualReportSelectionResult`、`OfficialBusinessDocumentContentResult`。
- Produces: `OfficialBusinessEvidenceFragment`、`OfficialBusinessFactResult` and `extract_official_business_facts(plan_item, selection, content)`。

- [x] **Step 1: Write failing page-evidence tests**

```python
def test_explicit_main_business_section_builds_page_hashed_facts():
    result = extract_official_business_facts(
        plan_item(industry_name="软件和信息技术服务业"),
        selection(),
        content(pages=(page(8, "证券代码000001 所属行业软件和信息技术服务业"),
                       page(20, "主营业务：主要产品包括工业软件、云平台。"))),
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.business_terms, ("工业软件", "云平台"))
    self.assertEqual(result.fragments[0].page_number, 20)
    self.assertRegex(result.fragments[0].fragment_sha256, r"^[0-9a-f]{64}$")

def test_directory_only_industry_conflict_and_hash_drift_are_unverified():
    for value in (directory_only_content(), wrong_industry_content(), drifted_content()):
        with self.subTest(value=value):
            result = extract_official_business_facts(plan_item(), selection(), value)
            self.assertEqual(
                result.status,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            )
```

- [x] **Step 2: Run fact tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_document_facts`

Expected: import failure because the fact module does not exist.

- [x] **Step 3: Implement deterministic anchors and finite term extraction**

```python
BUSINESS_SECTION_ANCHORS = ("主要业务", "主营业务", "核心业务", "营业收入构成")
PRODUCT_PREFIXES = ("主要产品包括", "主营产品包括", "核心产品包括", "主要业务包括")

def extract_official_business_facts(plan_item, selection, content):
    if not _bound_to_selection(plan_item, selection, content):
        return _facts_unverified("business_fact_identity_unverified")
    pages = _verified_non_directory_pages(content.pages)
    fragments = _extract_anchored_fragments(pages, BUSINESS_SECTION_ANCHORS)
    terms = _extract_explicit_product_terms(fragments, PRODUCT_PREFIXES)
    return _build_verified_fact_result(plan_item, selection, content, fragments, terms)
```

Terms must be 2–20 normalized Chinese/alphanumeric characters, unique in source order, and must reject company names, `产品/服务/业务/行业` alone and the frozen industry name itself as relation terms.

- [x] **Step 4: Run fact, selector and content suites**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_annual_report_selector backend.tests.test_radar_leader_business_document_content backend.tests.test_radar_leader_business_document_facts`

Expected: all pass.

- [x] **Step 5: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: Task 1–3 files remain local and uncommitted.

---

### Task 4: 六类官方催化发现与事实提取

**Files:**
- Create: `backend/radar/sources/leader_business_catalyst_official.py`
- Create: `backend/radar/leader_business_catalyst_facts.py`
- Test: `backend/tests/test_radar_leader_business_catalyst_official.py`
- Test: `backend/tests/test_radar_leader_business_catalyst_facts.py`

**Interfaces:**
- Consumes: candidate symbol、issuer identity from the annual-report document、`window_from/window_until` and the Task 2 content loader.
- Produces: `OfficialBusinessCatalystKind`、`OfficialBusinessCatalystDocument`、`OfficialBusinessCatalystDiscoveryResult`、`OfficialBusinessCatalystFactResult`、`fetch_official_business_catalysts(query, transport=None, clock=None)` and `extract_official_business_catalyst_facts(document, content)`。

- [ ] **Step 1: Write failing discovery tests**

```python
def test_allowlisted_events_are_deduped_sorted_and_capped_at_three():
    result = fetch_official_business_catalysts(
        query(), transport=transport_with_contract_bid_capacity_and_noise
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertLessEqual(len(result.documents), 3)
    self.assertEqual(len({item.document_id for item in result.documents}), len(result.documents))
    self.assertNotIn("日常关联交易", tuple(item.title for item in result.documents))

def test_partial_page_future_document_and_wrong_issuer_fail_closed():
    for payload, reason in invalid_discovery_payloads():
        result = parse_official_business_catalyst_payload(query(), payload, fetched_at=FETCHED_AT)
        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED)
        self.assertIn(reason, result.reasons)
```

- [ ] **Step 2: Run discovery tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_catalyst_official`

Expected: import failure because the catalyst source module does not exist.

- [ ] **Step 3: Implement six event kinds and strict Cninfo parsing**

```python
class OfficialBusinessCatalystKind(str, Enum):
    MAJOR_CONTRACT = "major_contract"
    PROJECT_AWARD = "project_award"
    CAPACITY_START = "capacity_start"
    PRODUCT_CERTIFICATION = "product_certification"
    PRIVATE_PLACEMENT_PROJECT = "private_placement_project"
    EARNINGS_FORECAST = "earnings_forecast"
```

Use explicit title patterns per kind, exact symbol/org identity, full page coverage, official PDF URL identity, newest-first stable sorting and a maximum of three unique documents per symbol. Only `requests.RequestException` receives one retry.

- [ ] **Step 4: Write failing catalyst fact tests**

```python
def test_contract_fact_extracts_event_and_explicit_business_object():
    result = extract_official_business_catalyst_facts(
        catalyst_document(kind=OfficialBusinessCatalystKind.MAJOR_CONTRACT),
        content(pages=(page(3, "公司签订工业软件项目合同，合同金额2亿元。"),)),
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.business_terms, ("工业软件",))
    self.assertEqual(result.event_kind, OfficialBusinessCatalystKind.MAJOR_CONTRACT)

def test_title_only_or_no_business_object_remains_unverified():
    result = extract_official_business_catalyst_facts(
        catalyst_document(), content(pages=(page(1, "重大合同公告"),))
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED)
```

- [ ] **Step 5: Implement catalyst fact extraction and run both suites**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_catalyst_official backend.tests.test_radar_leader_business_catalyst_facts`

Expected: both pass. Event phrases and product lists must come from PDF text, never from the title alone.

- [ ] **Step 6: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: Task 4 files remain local and uncommitted.

---

### Task 5: 确定性主营—催化关系验证

**Files:**
- Create: `backend/radar/leader_business_deterministic_verification.py`
- Test: `backend/tests/test_radar_leader_business_deterministic_verification.py`

**Interfaces:**
- Consumes: plan item、annual selection、business facts、one to three catalyst fact results and `validated_at`。
- Produces: `DeterministicOfficialBusinessVerificationArtifact`、`DeterministicOfficialBusinessVerificationResult` and `build_deterministic_official_business_verification(plan_item, annual_facts, catalyst_facts, *, validated_at)`。

- [ ] **Step 1: Write failing exact-relation tests**

```python
def test_exact_non_generic_business_term_builds_direct_versioned_artifact():
    result = build_deterministic_official_business_verification(
        plan_item(), annual_facts(terms=("工业软件", "云平台")),
        (catalyst_facts(terms=("工业软件",), kind="major_contract"),),
        validated_at=VALIDATED_AT,
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.artifact.relation, BusinessCatalystRelation.DIRECT)
    self.assertEqual(result.artifact.matched_terms, ("工业软件",))
    self.assertFalse(result.artifact.formal_usable)

def test_company_name_generic_term_fuzzy_match_and_hash_drift_are_unconfirmed():
    for annual, catalyst in unsafe_relation_cases():
        result = build_deterministic_official_business_verification(
            plan_item(), annual, (catalyst,), validated_at=VALIDATED_AT
        )
        self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED)
        self.assertIsNone(result.artifact)
```

- [ ] **Step 2: Run relation tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_deterministic_verification`

Expected: import failure because the verification module does not exist.

- [ ] **Step 3: Implement deterministic identity and relation replay**

```python
DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION = (
    "radar-leader-business-deterministic-relation-v1"
)

def _matched_terms(annual_facts, catalyst_fact):
    annual_terms = set(annual_facts.business_terms)
    return tuple(
        term for term in catalyst_fact.business_terms
        if term in annual_terms and term not in GENERIC_RELATION_TERMS
    )

def _verification_fingerprint(payload):
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
```

The artifact identity hash must include candidate symbol, industry code/release, annual/catalyst document versions and content hashes, evidence fragment hashes, matched terms, relation, rule version and `validatedAt`.

- [ ] **Step 4: Run Task 3–5 suites together**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_document_facts backend.tests.test_radar_leader_business_catalyst_facts backend.tests.test_radar_leader_business_deterministic_verification`

Expected: all pass.

- [ ] **Step 5: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: Task 5 files remain local and uncommitted.

---

### Task 6: 统一官方验证适配与现有生产链兼容

**Files:**
- Create: `backend/radar/leader_business_official_verification_adapter.py`
- Modify: `backend/radar/leader_business_catalyst_features.py`
- Modify: `backend/radar/leader_business_catalyst_runtime_bridge.py`
- Modify: `backend/radar/leader_business_catalyst_production_collector.py`
- Test: `backend/tests/test_radar_leader_business_official_verification_adapter.py`
- Modify test: `backend/tests/test_radar_leader_business_catalyst_features.py`
- Modify test: `backend/tests/test_radar_leader_business_catalyst_runtime_bridge.py`
- Modify test: `backend/tests/test_radar_leader_business_catalyst_production_collector.py`

**Interfaces:**
- Consumes: existing `LeaderOfficialBusinessMaterialBatchResult` plus either existing manual-review batch or a tuple of deterministic artifacts.
- Produces: `LeaderOfficialBusinessVerificationBatchResult` and `apply_official_business_verifications_batch(material_batch, *, manual_entries=None, deterministic_entries=None)`; runtime source batch gains a typed `verification_batch` field while retaining legacy `review_entries` compatibility.

- [ ] **Step 1: Write failing adapter and anti-forgery tests**

```python
def test_deterministic_artifact_replays_to_existing_ready_feature_input():
    result = apply_official_business_verifications_batch(
        material_batch(), deterministic_entries=deterministic_entries()
    )
    self.assertEqual(result.status, LeaderOfficialBusinessVerificationBatchStatus.READY)
    feature = build_leader_business_catalyst_features(result.items[0].input_value)
    self.assertEqual(feature.relation, BusinessCatalystRelation.DIRECT)

def test_auto_cannot_be_passed_as_human_or_mixed_with_manual():
    forged = apply_official_business_verifications_batch(
        material_batch(), manual_entries=deterministic_entries()
    )
    mixed = apply_official_business_verifications_batch(
        material_batch(), manual_entries=manual_entries(),
        deterministic_entries=deterministic_entries(),
    )
    self.assertEqual(forged.status, LeaderOfficialBusinessVerificationBatchStatus.BLOCKED)
    self.assertEqual(mixed.status, LeaderOfficialBusinessVerificationBatchStatus.BLOCKED)
```

- [ ] **Step 2: Run adapter tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_official_verification_adapter`

Expected: import failure because the adapter does not exist.

- [ ] **Step 3: Implement the typed adapter and feature method**

```python
ALLOWED_BUSINESS_REVIEW_METHODS = frozenset({"manual", "deterministic_official"})

# New adapter creates LeaderBusinessCatalystReview with:
review_method="deterministic_official"
reviewer_key=artifact.rule_version
```

The adapter must reconstruct proof and catalyst artifacts from validated facts, call the existing official material adapter, recompute the deterministic relation, and only then append the typed review. It must not call or populate `ManualReviewerKind`.

- [ ] **Step 4: Modify runtime bridge and collector with legacy compatibility**

Runtime rules:

```python
if source_batch.verification_batch is not None and source_batch.review_entries:
    return SOURCE_UNVERIFIED
if source_batch.verification_batch is not None:
    business_review_batch = replay_official_business_verification_batch(
        material_batch,
        source_batch.verification_batch,
        as_of=context.as_of,
    )
else:
    business_review_batch = apply_official_business_manual_reviews_batch(
        material_batch,
        source_batch.review_entries,
    )
```

Collector source time for deterministic input is the maximum of official document publication times and `validated_at`; manual input retains the current `reviewed_at` rule. Both remain bounded by the candidate `asOf` when rebound.

- [ ] **Step 5: Run all affected compatibility suites**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_official_verification_adapter backend.tests.test_radar_leader_business_catalyst_features backend.tests.test_radar_leader_business_catalyst_manual_review backend.tests.test_radar_leader_business_catalyst_runtime_bridge backend.tests.test_radar_leader_business_catalyst_production_collector`

Expected: deterministic and legacy manual paths pass; string-forged and mixed paths fail closed.

- [ ] **Step 6: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: Task 6 files remain local and uncommitted.

---

### Task 7: 候选全集断点编排、工件合同与一键命令

**Files:**
- Create: `backend/radar/leader_business_automatic_evidence.py`
- Create: `backend/run_leader_business_automatic_evidence.py`
- Test: `backend/tests/test_radar_leader_business_automatic_evidence.py`
- Test: `backend/tests/test_run_leader_business_automatic_evidence.py`

**Interfaces:**
- Consumes: existing `source.json` through `load_leader_business_material_review_source_packet`、explicit `artifact_dir: Path`、clock and injectable source/content callbacks.
- Produces: `LeaderBusinessAutomaticEvidenceBatchResult`、`run_leader_business_automatic_evidence(source_packet, *, artifact_dir, sources, clock)` and CLI arguments `source_path --artifact-dir`。

- [ ] **Step 1: Write failing full-batch and checkpoint tests**

```python
def test_complete_batch_restores_385_order_and_exports_replayable_packet():
    result = run_leader_business_automatic_evidence(
        source_packet(385), artifact_dir=temp_path,
        sources=all_ready_sources(), clock=lambda: VALIDATED_AT,
    )
    self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.candidate_count, 385)
    self.assertEqual(result.ready_count, 385)
    self.assertEqual(tuple(item.index for item in result.items), tuple(range(385)))
    self.assertTrue(result.packet_path.is_file())
    self.assertFalse(result.formal_gate_ready)

def test_one_missing_candidate_keeps_batch_unverified_and_no_delivery_packet():
    result = run_leader_business_automatic_evidence(
        source_packet(385), artifact_dir=temp_path,
        sources=sources_missing_index(200), clock=lambda: VALIDATED_AT,
    )
    self.assertNotEqual(result.status, AutomaticBusinessEvidenceStatus.READY)
    self.assertEqual(result.ready_count, 384)
    self.assertIsNone(result.delivery_packet_path)

def test_checkpoint_requires_exact_plan_document_content_and_rule_identity():
    first = run_with_checkpoint(source_packet(2))
    self.assertEqual(run_with_checkpoint(source_packet(2)).reused_count, 2)
    self.assertEqual(run_with_checkpoint(changed_document_packet()).reused_count, 1)
    self.assertEqual(run_with_checkpoint(changed_rule_version()).reused_count, 0)
```

- [ ] **Step 2: Run orchestrator tests and verify the red failure**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_automatic_evidence`

Expected: import failure because the orchestrator does not exist.

- [ ] **Step 3: Implement bounded parallel orchestration and atomic checkpoints**

```python
def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

with ThreadPoolExecutor(max_workers=MAXIMUM_PDF_WORKERS) as executor:
    indexed_results = tuple(executor.map(process_candidate, indexed_items))
ordered_results = tuple(
    result for _, result in sorted(indexed_results, key=lambda pair: pair[0])
)
```

Checkpoint filenames are SHA-256 identities, not raw symbols. Each checkpoint contains contract ID, plan discovery ID, document versions, content hashes, rule version, validated time and finite evidence fragments; it contains no raw PDF or full document text.

- [ ] **Step 4: Write failing CLI output and error tests**

```python
def test_cli_prints_only_counts_paths_reasons_and_false_gate():
    code, payload = run_cli(valid_source_path, artifact_dir=temp_path)
    self.assertEqual(code, 0)
    self.assertEqual(payload["candidateCount"], 385)
    self.assertFalse(payload["gate"]["formalGateReady"])
    self.assertNotIn("items", payload)
    self.assertNotIn("主营业务原文", json.dumps(payload, ensure_ascii=False))

def test_cli_invalid_packet_and_write_failure_return_three_without_traceback():
    self.assertEqual(run_cli(invalid_path)[0], 3)
    self.assertEqual(run_cli(valid_source_path, artifact_dir=read_only_path)[0], 3)
```

- [ ] **Step 5: Implement CLI and run Task 7 suites**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_automatic_evidence backend.tests.test_run_leader_business_automatic_evidence`

Expected: orchestrator and CLI suites pass; invalid local input/write errors exit 3, real source not-ready exits 2, full ready exits 0.

- [ ] **Step 6: Run the entire automatic evidence related suite**

Run: `PYTHONPATH=backend backend/venv/bin/python -m unittest backend.tests.test_radar_leader_business_annual_report_selector backend.tests.test_radar_leader_business_document_content backend.tests.test_radar_leader_business_document_facts backend.tests.test_radar_leader_business_catalyst_official backend.tests.test_radar_leader_business_catalyst_facts backend.tests.test_radar_leader_business_deterministic_verification backend.tests.test_radar_leader_business_official_verification_adapter backend.tests.test_radar_leader_business_automatic_evidence backend.tests.test_run_leader_business_automatic_evidence`

Expected: all automatic evidence tests pass.

- [ ] **Step 7: Record the local checkpoint without Git writes**

Run: `git status --short`

Expected: the full automatic evidence batch remains local and uncommitted.

---

### Task 8: 容量、完整回归、真实只读验收与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- No production code is added in this task unless a failing regression test first demonstrates a defect.

**Interfaces:**
- Consumes: Task 1–7 implementation and `/private/tmp/stage6-business-material-acceptance-20260821T095920-source.json`.
- Produces: fresh test evidence, `/private/tmp/stage6-business-auto-*` artifact directory and an updated unique handoff checkpoint.

- [ ] **Step 1: Run a 385-candidate synthetic capacity test**

Run the Task 7 suite's 385-item capacity case with timing and peak RSS capture. Expected: exact order, two PDF workers maximum, peak RSS not above 512 MiB, no raw PDF files in the artifact directory.

- [ ] **Step 2: Run the isolated-production-SQLite full backend suite**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python -c 'import os, sqlite3, sys, tempfile, unittest
with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
    isolated_db = os.path.join(directory, "isolated-stock-monitor.db")
    real_connect = sqlite3.connect
    def isolated_connect(database, *args, **kwargs):
        try:
            database_path = os.fspath(database)
        except TypeError:
            database_path = ""
        if database_path.endswith("backend/data/stock_monitor.db"):
            database = isolated_db
        return real_connect(database, *args, **kwargs)
    sqlite3.connect = isolated_connect
    suite = unittest.defaultTestLoader.discover("backend/tests", pattern="test_*.py", top_level_dir="backend")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)'
```

Expected: zero failures and zero errors; production SQLite path is never opened.

- [ ] **Step 3: Run compilation and whitespace verification**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stage6-auto-pycache backend/venv/bin/python -m compileall -q backend/radar backend/run_leader_business_automatic_evidence.py backend/tests`

Run: `git diff --check`

Expected: both exit 0.

- [ ] **Step 4: Run the real public-source command**

Run:

```bash
PYTHONPATH=backend backend/venv/bin/python backend/run_leader_business_automatic_evidence.py \
  /private/tmp/stage6-business-material-acceptance-20260821T095920-source.json \
  --artifact-dir /private/tmp/stage6-business-auto-20260821
```

Expected: the command processes the real 385-candidate source packet automatically, prints only aggregate evidence, writes no raw PDF, creates no human review artifact, and honestly returns `ready`, `missing`, `source_failed`, or `source_unverified` counts. A non-zero not-ready exit is a valid truthful outcome and must not be converted to success.

- [ ] **Step 5: Verify service and Git boundaries**

Run: `lsof -nP -iTCP:4000 -sTCP:LISTEN`

Run: `lsof -nP -iTCP:8001 -sTCP:LISTEN`

Run: `git status --short --branch`

Expected: 8001 remains the pre-existing PID 791 unless external state changed independently; no service is stopped or reloaded; all changes remain uncommitted unless the user separately authorizes Git writes.

- [ ] **Step 6: Update the unique handoff**

Record the exact real command time, candidate/ready/missing/failed/unverified counts, artifact paths, test counts, service state, Git state and remaining industry/D8 gates in `NEXT_CHAT_HANDOFF.md`. Do not copy full PDF text, stock-level results or temporary logs into the handoff.
