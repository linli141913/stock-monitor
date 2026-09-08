# 阶段9严格时间点回放与质量页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不读取“当前值”补历史、不写生产SQLite的前提下，建立可验证的雷达时间点回放合同、未来数据检查、样本分区、只读质量API和历史验证页面。

**Architecture:** 回放核心只消费调用方显式提供的不可变历史包，不自行抓取最新数据。校验器先检查时点、身份、版本、来源时间和样本分区，再由评估器产生版本化质量报告；JSON仓只保存已校验报告，公开API仅只读最新报告。没有真实报告时返回稳定 `not_ready`，前端展示真实缺口而不是Fixture指标。

**Tech Stack:** Python 3、FastAPI、Pydantic 2、原子JSON工件、Next.js 16 App Router、React 19、TypeScript 5。

**Spec:** `docs/股票监测助手V5.0升级规划书.md` 第18、19、20、21章；`PRD.md` 第24.4、24.8章。

## Global Constraints

- 只操作唯一真实项目 `/Volumes/HermesSSD/AntigravityData/量化监测-股票`，保留全部未提交工作树。
- 不读取或写入生产SQLite，不新增迁移或依赖，不修改生产环境变量或功能开关。
- 生产接口不返回Mock、测试Fixture、当前证券名册倒填的历史值或推算结果。
- 回放只接受 `sourceTime/fetchedAt/effectiveFrom <= asOf` 的证据；未来数据违规必须失败关闭。
- 规则开发、校准和最终保留样本身份互斥；系统输出不得成为自己的黄金标签。
- 未达到真实覆盖时只报告 `not_ready/partial/unverifiable`，不宣称规则有效或产生收益结论。
- 本轮不执行Git暂存、提交、推送、部署或服务重载。

---

### Task 1: 严格时间点回放输入合同

**Files:**
- Create: `backend/radar/replay_contracts.py`
- Test: `backend/tests/test_radar_replay_contracts.py`

**Interfaces:**
- Produces: `RadarReplayInput`, `RadarReplayEvidence`, `RadarReplaySample`, `RadarReplaySampleRole`。
- Enforces: 全部时间字段不晚于 `asOf`、证券/行业/指数/ETF/公司行为身份明确、同一 `sampleId` 不跨分区。

- [ ] 写失败测试：未来来源时间、未来抓取时间、未来生效时间分别拒绝。
- [ ] 运行测试并确认因模块缺失或行为缺失失败。
- [ ] 实现最小强类型合同与稳定错误码。
- [ ] 写失败测试：开发/校准/保留样本重复、空来源目录、重复证据编号拒绝。
- [ ] 实现分区和来源身份校验并跑绿。

### Task 2: 回放评估与质量报告

**Files:**
- Create: `backend/radar/replay_service.py`
- Test: `backend/tests/test_radar_replay_service.py`

**Interfaces:**
- Consumes: `RadarReplayInput`。
- Produces: `RadarReplayQualityReport`，固定包含纳入、排除、缺失、不可验证、未来违规、状态重复、同股多状态和分模块指标。

- [ ] 写失败测试：完整小样本产生 `ready` 且计数为手工字面量。
- [ ] 写失败测试：缺历史证券池/交易规则/行业/指数ETF/公司行为任一必需域时返回 `not_ready`，不填零指标。
- [ ] 写失败测试：同股同轮多正式状态和未来证据使报告 `failed`。
- [ ] 实现最小评估器并跑绿；不计算未提供的收益或事后表现。

### Task 3: 原子JSON报告仓与只读API

**Files:**
- Create: `backend/radar/replay_store.py`
- Modify: `backend/radar/api_contracts.py`
- Modify: `backend/radar/api.py`
- Test: `backend/tests/test_radar_replay_store.py`
- Test: `backend/tests/test_radar_replay_api.py`

**Interfaces:**
- Produces: `publish_replay_report(report, store_dir)`、`load_latest_replay_report(store_dir)`。
- API: `GET /api/radar/replays/latest`，稳定返回 `radar-replay-quality-v1`。

- [ ] 写失败测试：缺清单、损坏JSON、哈希不符、未来发布时间分别返回稳定状态。
- [ ] 实现内容寻址快照和原子 `latest.json`，读取时重新校验合同与SHA-256。
- [ ] 写失败API测试：无报告返回HTTP 200 + `not_ready`，损坏返回 `failed`，有效报告透传计数和原因。
- [ ] 实现只读路由；GET设置 `Cache-Control: no-store`，不得触发回放或写库。

### Task 4: 历史验证页面

**Files:**
- Create: `stock-monitor/src/components/radar/RadarReplayQualityPanel.tsx`
- Modify: `stock-monitor/src/types/radar.ts`
- Modify: `stock-monitor/src/app/radar/page.tsx`
- Modify: `stock-monitor/src/components/radar/Radar.module.css`
- Test: `stock-monitor/tests/radar-replay-contract.ts`

**Interfaces:**
- Consumes: `GET /api/backend/api/radar/replays/latest`。
- Displays: 回放状态、批次时点、样本分区、纳入/排除/缺失/不可验证、未来违规、模块覆盖和明确下一缺口。

- [ ] 先增加TypeScript合同断言，使缺少新类型/字段时失败。
- [ ] 实现独立面板，不覆盖现有行业历史与阈值校准卡。
- [ ] 对 `not_ready/partial/failed/ready` 分别提供明确文案；没有报告时不显示0%成功率。
- [ ] 对象/标签切换时清空旧报告，GET使用 `cache: 'no-store'`。

### Task 5: 验证与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

- [ ] 运行回放专项测试及受影响API测试。
- [ ] 精确重定向可能导入的生产SQLite路径到 `/private/tmp` 后运行完整后端测试。
- [ ] 运行Python编译、`pip check`、前端TypeScript、ESLint、Next.js build和 `git diff --check`。
- [ ] 在不重载服务前提下只做静态/测试验收；运行页未加载新代码时明确说明。
- [ ] 更新唯一检查点，纠正阶段8已完成事实，记录阶段9真实完成项与仍缺的真实历史证据。
