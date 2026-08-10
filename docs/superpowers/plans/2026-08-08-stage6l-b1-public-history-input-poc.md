# 阶段6L-B1免费历史输入POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用免费公开来源组装候选股票、点时行业等权基准和板块指数的21日研究输入，并保持全部正式门禁关闭。

**Architecture:** 独立来源模块只负责合同、解析和纯内存计算；单次CLI负责有界网络采集与脱敏报告。现有运行时、仓储和API不接入本POC。

**Tech Stack:** Python 3.9、dataclasses、requests、现有AKShare/PDF解析、unittest。

## Global Constraints

- 不读取或写入生产SQLite。
- 不新增依赖、迁移、缓存、环境变量或调度。
- 不修改正式评分、门禁、状态机、API或页面。
- 不重启服务，不执行Git暂存、提交、推送或部署。

---

### Task 1: 纯内存历史来源合同

**Files:**
- Create: `backend/radar/sources/leader_history_public_poc.py`
- Test: `backend/tests/test_radar_leader_history_public_poc.py`

**Interfaces:**
- Produces: `PublicHistorySeries`、`PointInTimeIndustryMembership`、
  `PublicHistoryPocQuery`、`PublicHistoryPocResult`、
  `parse_tencent_history_payload()`、`run_public_history_input_poc()`。
- Consumes: 既有 `AdjustedHistorySeries` 和 `LeaderHistoryFeatureInput`。

- [x] 写完整序列和等权基准失败测试，确认模块缺失红灯。
- [x] 实现严格日期、来源、价格、成员和时间校验。
- [x] 实现逐日成员等权收益复合，并只在全部门禁通过时输出历史输入。
- [x] 补齐真实0、成员缺日、错行业、晚观察、未来时间和Fixture状态测试。
- [x] 运行专项测试并确认绿灯。

### Task 2: 单次真实来源入口

**Files:**
- Create: `backend/run_public_history_poc.py`
- Test: `backend/tests/test_radar_leader_history_public_live_cli.py`

**Interfaces:**
- Consumes: `run_public_history_input_poc()` 和现有官方证券/行业来源适配器。
- Produces: 单行脱敏JSON证据；只允许 `--symbol` 和
  `--confirm-live-poc`。

- [x] 写未确认、非法代码、异常脱敏和Fixture伪装失败测试，确认红灯。
- [x] 实现最近21个已完成官方交易日计算。
- [x] 实现官方行业版本和腾讯日K的有界采集，最多80个行业成员、8路并发、每请求8秒。
- [x] 将行业成员锁定为上交所、深交所证券，并审计范围外排除数量。
- [x] 输出覆盖率、原因、来源状态和固定关闭的四个正式标志，不输出原始响应。
- [x] 运行CLI专项测试并确认绿灯。

### Task 3: 回归与一次真实样本

**Files:**
- Verify: `backend/radar/sources/leader_history_public_poc.py`
- Verify: `backend/run_public_history_poc.py`
- Verify: 两个专项测试文件

**Interfaces:**
- Consumes: Task 1与Task 2最终接口。
- Produces: 可重复测试和真实POC证据。

- [x] 运行两个专项测试和全部 `test_radar_leader*.py` 回归。
- [x] 运行Python语法检查和 `git diff --check`。
- [x] 用一个沪深A股样本执行一次确认后的真实只读POC。
- [x] 更新 `NEXT_CHAT_HANDOFF.md`，明确真实结果与未启用边界。
