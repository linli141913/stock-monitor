# 阶段 6L-D5 离线人工事实补录与版本化审核工件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 D4 人工候选建立不可直接进入正式门禁的压缩、版本化审核工件。

**Architecture:** D5 先重建并核对 D4 候选；事实候选验证页内原文后生成
`manual` 事实；关系候选重新调用 D3 验证事件版本和关系；版本层只接受同工件的
线性替代链。

**Tech Stack:** Python 3.9、标准库 dataclass/enum/hashlib/re/datetime、现有
D1-D4 合同和 unittest。

## Global Constraints

- 不新增依赖。
- 不读取或写入生产 SQLite。
- 不联网，不保存 PDF、正文、原始值或片段。
- 不接迁移、仓储、运行时、调度、API、前端、提醒或正式门禁。
- 不停止、重启或重载 4000/8001。
- 不修改环境变量。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

### Task 1: 冻结事实补录合同

**Files:**
- Create: `backend/tests/test_radar_leader_risk_review_artifacts.py`
- Create: `backend/radar/leader_risk_review_artifacts.py`

- [x] 写六类事实归一化与安全输出失败测试
- [x] 写页码、片段、原始值、重复和候选类型失败测试
- [x] 运行专项测试确认 RED
- [x] 实现候选真实性复核和人工事实工件
- [x] 运行事实专项测试确认 GREEN

### Task 2: 冻结关系审核与版本合同

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_review_artifacts.py`
- Modify: `backend/radar/leader_risk_review_artifacts.py`

- [x] 写 D3 关系重验成功和失败测试
- [x] 写首次版本、顺序替代、重复、分叉、跨候选和过期测试
- [x] 运行新增测试确认 RED
- [x] 实现关系重验、压缩输出和线性版本链
- [x] 运行 D3-D5 专项测试确认 GREEN

### Task 3: 回归、复核与交接

**Files:**
- Modify: `docs/superpowers/specs/2026-07-29-stage6l-d5-manual-risk-review-artifacts-design.md`
- Modify: `docs/superpowers/plans/2026-07-29-stage6l-d5-manual-risk-review-artifacts.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

- [x] 运行 D1-D5 联合测试和全部龙头测试
- [x] 运行后端完整测试与 Python 语法检查
- [x] 复核原文、审核摘要、人工身份和正式门禁边界
- [x] 运行差异卫生检查
- [x] 更新唯一交接并明确下一阶段
