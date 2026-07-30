# 阶段 6L-D6 审核工件重放与研究事实合并实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选择唯一当前 D5 工件并生成不修改 D3/D1 的合并研究事实视图。

**Architecture:** 重放器先重新计算 D3 与 D4 候选，再验证完整 D5 线性链；最后
版本通过后，按确定性优先规则合并事实或重放关系。

**Tech Stack:** Python 3.9、标准库 dataclass/hashlib/re/datetime、现有 D1-D5
合同和 unittest。

## Global Constraints

- 不新增依赖。
- 不读取或写入生产 SQLite。
- 不联网，不保存 PDF、正文、原始值或片段。
- 不接迁移、仓储、运行时、调度、API、前端、提醒或正式门禁。
- 不停止、重启或重载 4000/8001。
- 不修改环境变量。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

### Task 1: 冻结版本链和事实合并

**Files:**
- Create: `backend/tests/test_radar_leader_risk_review_replay.py`
- Create: `backend/radar/leader_risk_review_replay.py`

- [x] 写合法事实链、空链和最后版本过期失败测试
- [x] 写重复、分叉、未来、跨候选和伪造事实失败测试
- [x] 运行专项测试确认 RED
- [x] 实现候选重建、线性链验证和确定性优先合并
- [x] 运行事实重放专项测试确认 GREEN

### Task 2: 冻结关系重放和安全输出

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_review_replay.py`
- Modify: `backend/radar/leader_risk_review_replay.py`

- [x] 写合法关系及错事件、版本、文档和事实依据失败测试
- [x] 写输入输出 `repr` 与非正式边界测试
- [x] 运行新增测试确认 RED
- [x] 实现关系结构重验和压缩输出
- [x] 运行 D3-D6 联合测试确认 GREEN

### Task 3: 回归、复核与交接

**Files:**
- Modify: `docs/superpowers/specs/2026-07-29-stage6l-d6-risk-review-replay-design.md`
- Modify: `docs/superpowers/plans/2026-07-29-stage6l-d6-risk-review-replay.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

- [x] 运行 D1-D6 联合测试和全部龙头测试
- [x] 运行后端完整测试与 Python 语法检查
- [x] 复核版本回退、事实覆盖、关系依据和敏感输出
- [x] 运行差异卫生检查
- [x] 更新唯一交接并明确下一阶段
