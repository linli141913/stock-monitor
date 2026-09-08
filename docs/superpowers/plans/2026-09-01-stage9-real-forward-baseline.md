# 阶段9真实向前回放基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将六类真实历史域接入严格时点回放，生成第一份不倒填过去的向前样本和质量报告。

**Architecture:** 纯适配层将既有强类型来源转换为 `RadarReplayEvidence`，不网络、不数据库。向前采集器调用已有官方来源，在全部请求完成后冻结 `asOf`，并只向显式 `/private/tmp` 目录发布快照、回放输入和质量报告。

**Tech Stack:** Python 3、Pydantic 2、现有官方来源适配器、原子 JSON、unittest。

**Spec:** `docs/superpowers/specs/2026-09-01-stage9-real-forward-baseline-design.md`

## Global Constraints

- 不读写生产 SQLite，不使用当前值回填过去。
- 不新增依赖、迁移、环境变量、调度、功能开关或 AI 调用。
- 真实工件只写入调用方显式提供的 `/private/tmp` 目录。
- 未验证或缺失域必须保持 `unverifiable/missing`，不用 Fixture、Mock 或其他域代替。
- 本轮不 Git 暂存、提交、推送、部署或重载服务。

---

### Task 1: 六域纯适配合同

**Files:**
- Create: `backend/radar/replay_source_adapters.py`
- Test: `backend/tests/test_radar_replay_source_adapters.py`

**Interfaces:**
- Produces: `adapt_security_universe`、`adapt_trading_rules`、`adapt_industry`、`adapt_index`、`adapt_etf`、`adapt_corporate_actions`。
- Consumes: 现有 `SourceBatch`、`IndustryClassificationSnapshot`、`OfficialIndexPocResult` 和显式公司行为快照。

- [x] 先写失败测试：完整官方证券主档产生 `ready`，来源失败和覆盖不足分别产生 `failed/unverifiable`。
- [x] 观看模块缺失红灯，再实现证券主档适配。
- [x] 按同样红绿循环覆盖交易规则、行业、指数、ETF 和公司行为。
- [x] 断言每份证据都保留源合同、时间、数量、原因码和内容 SHA-256。

### Task 2: 分区完整度评估

**Files:**
- Modify: `backend/radar/replay_service.py`
- Modify: `backend/radar/api_contracts.py`
- Modify: `stock-monitor/src/types/radar.ts`
- Modify: `stock-monitor/src/components/radar/RadarReplayQualityPanel.tsx`
- Test: `backend/tests/test_radar_replay_service.py`
- Test: `backend/tests/test_radar_replay_api.py`
- Test: `stock-monitor/tests/radar-replay-contract.ts`

**Interfaces:**
- Adds: `missingPartitions` 和 `replay_partition_missing`。

- [x] 先写失败测试：只有 development 样本时报告必须列出 calibration/holdout 缺失。
- [x] 实现最小评估和后端 API 透传，跑绿相关测试。
- [x] 先让前端类型合同因缺字段失败，再接入“缺少校准/留出集”真实文案。

### Task 3: 向前基线采集与安全 CLI

**Files:**
- Create: `backend/radar/replay_forward_baseline.py`
- Create: `backend/run_radar_replay_forward_baseline.py`
- Test: `backend/tests/test_radar_replay_forward_baseline.py`
- Test: `backend/tests/test_run_radar_replay_forward_baseline.py`

**Interfaces:**
- Produces: `collect_forward_replay_baseline(...)`、`run_radar_replay_forward_baseline.py --confirm-live-baseline --output-dir /private/tmp/...`。

- [x] 先写失败测试：无确认参数、非 `/private/tmp` 输出、未来抓取时间均拒绝。
- [x] 实现依赖注入的采集编排，全部来源完成后冻结单个 development 样本。
- [x] 原子落盘六域快照、`RadarReplayInput` 和 `RadarReplayQualityReport`，重新加载校验身份与哈希。
- [x] CLI 只调用已有官方公开来源，公司行为缺失时明确输出 `missing`。

### Task 4: 真实首轮与完整验收

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

- [x] 使用公开网络权限在新 `/private/tmp` 目录运行安全 CLI，记录六域 `ready/missing/unverifiable/failed`和样本分区。
- [x] 重新加载所有工件，验证没有未来数据、重复身份、同股多状态和虚假指标。
- [x] 运行相关测试、精确隔离生产 SQLite 的完整后端、前端 TypeScript/ESLint/build、Python 编译、`pip check` 和 `git diff --check`。
- [x] 更新唯一 `NEXT_CHAT_HANDOFF.md`，不重载服务、不提交或推送。
