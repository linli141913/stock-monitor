# 阶段 6L-C3 可交易性来源合同实施计划

> 本计划只在唯一真实项目内执行本地代码和测试修改。禁止生产数据库、服务
> 重启、依赖变更和 Git 写操作。

**目标：** 实现版本化交易规则、腾讯上下限辅助字段、官方参考输入合同和冲突
降级，并接入既有 C1 研究证据入口。

**架构：** 新增一个无网络、无数据库的来源组装模块。行情源只扩展同响应字段；
运行时继续通过现有 `tradability_inputs_by_symbol` 接收 C1 输入，不新增调度或
正式门禁。

## 任务 1：冻结版本化规则目录

**文件：**

- 新增 `backend/tests/test_radar_leader_tradability_sources.py`
- 新增 `backend/radar/leader_tradability_sources.py`

步骤：

1. 先写 2023/2026 切换、板块比例和无涨跌幅限制边界测试。
2. 运行专项测试，确认因模块或符号缺失而失败。
3. 最小实现规则条目、版本选择和十进制上下限计算。
4. 重跑专项测试。

## 任务 2：扩展腾讯同响应辅助字段

**文件：**

- 修改 `backend/radar/contracts.py`
- 修改 `backend/radar/sources/tencent_quotes.py`
- 修改 `backend/tests/test_radar_sources.py`
- 修改 `backend/tests/test_radar_contracts.py`

步骤：

1. 先写字段 47、48 正常、空值、非法值和非必填合同测试。
2. 运行腾讯源与合同专项测试，确认失败。
3. 增加两个可选字段并复用现有非负有限数解析。
4. 确认同一响应完成解析，调用次数和覆盖率口径不变。

## 任务 3：官方参考输入与冲突降级

**文件：**

- 修改 `backend/tests/test_radar_leader_tradability_sources.py`
- 修改 `backend/radar/leader_tradability_sources.py`

步骤：

1. 先写官方来源缺失、错误等级、身份冲突、规则冲突和腾讯冲突测试。
2. 运行专项测试，确认失败。
3. 实现严格输入合同、来源等级和来源组装结果。
4. 仅在官方主来源完整且一致时生成 C1 输入。
5. 缺失返回 `missing`；冲突返回 `source_unverified`。

## 任务 4：接入 C1 研究证据

**文件：**

- 修改 `backend/tests/test_radar_leader_tradability_sources.py`
- 必要时最小修改 `backend/tests/test_radar_leader_runtime_inputs.py`

步骤：

1. 用组装器输出调用 C1，验证限价和一字板研究状态。
2. 将同一输入交给现有运行时入口。
3. 验证 `scoreReady=false`、`formalUsable=false`，
   `tradability_passed` 仍不进入正式维度。

## 任务 5：验证与交接

**文件：**

- 修改 `NEXT_CHAT_HANDOFF.md`

步骤：

1. 运行 C3 专项测试。
2. 运行龙头相关全量测试。
3. 运行后端完整测试；所有导入 `main` 的测试必须先重定向精确生产数据库路径。
4. 运行相关语法或导入检查。
5. 检查 Git diff、固定端口进程和敏感文件。
6. 更新唯一交接文档，记录完成项、仍关闭的门禁和下一步。

## 完成条件

- [x] 规则目录和解析器测试通过。
- [x] 腾讯辅助字段合同测试通过。
- [x] 官方参考缺失与冲突降级测试通过。
- [x] C1 与运行时研究入口验证通过。
- [x] 后端相关和完整测试通过。
- [x] 4000/8001 原进程未重启。
- [x] 未触碰生产 SQLite、环境变量和 Git 写操作。
