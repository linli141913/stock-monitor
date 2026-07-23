# 股票监测助手 V5 当前续做检查点

> 保存时间：2026-07-23 16:55 CST
> 作用：跨 Codex 对话继续当前项目。它是检查点，不代替真实代码、Git、服务、数据库结构和测试证据。

## 新对话直接执行

全程中文，在唯一真实项目中继续：

`/Volumes/HermesSSD/AntigravityData/量化监测-股票`

开始前必须按顺序读取并现场复核：

1. `AGENTS.md`
2. `PRD.md`
3. `docs/股票监测助手V5.0升级规划书.md`
4. `NEXT_CHAT_HANDOFF.md`
5. 当前代码、`git status`、固定端口服务、雷达运行配置和相关测试

不得只依赖本检查点，也不得覆盖、回滚、清理或删除当前任何已修改、未跟踪文件和运行数据。

## 当前阶段结论

- V5 总计划为阶段0至阶段10，共11个大阶段。
- 阶段0至阶段3已经完成工程实施；2026-07-23“阶段3生产门槛修复与联合复验”已经通过。
- 当前准确位置：**允许进入阶段4“主线雷达页面骨架”，但阶段4生产代码尚未开始。**
- 当前代码中不存在前端 `/radar` 路由，也不存在雷达只读API；不能把已批准图稿写成已上线页面。
- 阶段3当前只证明市场和行业聚合影子链路可以持续观察，不等于正式市场四状态、行业评分、排名或信号已经启用。
- 成交额/总市值单位验证和至少20个交易日影子观察仍未完成；相关 `formalUsable` 必须继续保持 `false`。
- 当前不进入ETF正式引擎、三级龙头状态机、提醒、雷达AI或交易建议。

## 阶段4已经确认的产品方向

- 独立一级导航“主线雷达”，桌面端1920×1080优先。
- 页面最终重点是：行业ETF、龙头、准龙头、候选龙头，以及它们的状态变化与证据。
- 阶段4第一版只能接当前真实可用的市场、行业聚合数据；ETF和龙头区域先保留产品位置，并明确显示后续阶段尚未启用，禁止用Mock或旧数据填充。
- 必须分别展示正常、真实空榜、数据过期、来源失败、未启用，不能把它们都显示成0或“暂无数据”。
- 数据时间必须区分源时间、抓取时间和页面渲染时间；模块失败不能拖垮其他正常模块。
- 页面只做分析和监测，不给出买卖指令、目标价或确定性走势预测。

已批准的Figma第二版：

`https://www.figma.com/design/O1z8bTcqh3Na6U0gEjUmQY?node-id=2-2`

本地审稿资产：

- 第二版总览：`/Users/linjian/.codex/visualizations/2026/07/21/019f835e-978a-74a1-9163-b629fe04ec62/stage4-radar-mockups/figma-v2-final.png`
- 阶段4真实可用状态：`/Users/linjian/.codex/visualizations/2026/07/21/019f835e-978a-74a1-9163-b629fe04ec62/stage4-radar-mockups/stage4-real-state.png`
- 空榜/过期/失败状态：`/Users/linjian/.codex/visualizations/2026/07/21/019f835e-978a-74a1-9163-b629fe04ec62/stage4-radar-mockups/resilience-state.png`
- 可交互审稿文件：`/Users/linjian/.codex/visualizations/2026/07/21/019f835e-978a-74a1-9163-b629fe04ec62/stage4-radar-mockups/state-variants.html`

Figma Starter 调用额度在保存时已达到上限，两张配套状态稿尚未写回Figma；原第二版图稿完整保留。这不阻断后续按已批准图稿实施。

## Git与工作树快照

阶段保存结果：

- 分支：`main`
- 阶段提交前的父提交：`ec38a85880a2c0ffcf4dd8e593bae8fdc8f45101`
- 阶段0至阶段3的代码、测试、运行资产、规划和本检查点已由用户授权保存为本地Git提交；准确提交号必须用 `git log -1` 现场读取。
- 本次没有执行 `git push`、创建PR或部署，因此远端 `origin/main` 不包含该本地阶段提交。

本次阶段提交包含的原跟踪文件修改：

- `AGENTS.md`
- `PRD.md`
- `README.md`
- `NEXT_CHAT_HANDOFF.md`
- `backend/backup_database.py`
- `backend/main.py`
- `backend/monitoring_health.py`
- `backend/tests/test_backup_database.py`

本次阶段提交包含的原未跟踪内容：

- `backend/radar/`：V5雷达合同、迁移、仓储、来源、市场/行业特征、影子执行器、调度与运行时。
- `backend/tests/test_radar_*.py`、`backend/tests/test_launchd_assets.py`：雷达和运行资产专项测试。
- `backend/validate_radar_trading_day.py`
- `ops/launchd/`：FastAPI、ngrok、雷达关闭、行业影子启用、市场影子启用及管理脚本。
- `docs/股票监测助手V5.0升级规划书.md`
- `docs/superpowers/plans/2026-07-18-launchd-stage-1b1.md`
- `docs/superpowers/plans/2026-07-18-radar-shadow-scheduler-2b-6b1.md`

提交完成后应保持工作树干净。任何新对话仍必须重新运行 `git status --short` 和 `git log -1`；如果出现新修改，不得按本列表执行清理。

## 运行进程快照

保存时间点的只读核对：

- 前端 `http://localhost:4000/`：HTTP 200；`screen` 会话 `stock-monitor-frontend`；监听PID 6767。
- 后端 `http://127.0.0.1:8001/docs`：HTTP 200；LaunchAgent `com.linjian.stock-monitor.fastapi`；监听PID 49983。
- ngrok LaunchAgent `com.linjian.stock-monitor.ngrok`：运行中；PID 17883。
- 雷达全局开关：开启。
- 雷达影子模式：开启。
- 行业影子：开启，180秒。
- 市场影子：开启，180秒。

PID和HTTP状态会随系统运行变化，进入新对话后只能只读复核，未经明确授权不得停止或重启前端、后端、ngrok或影子任务。

## 最近验证证据

升级规划书当前记录：

- 阶段3生产门槛修复专项39项通过。
- 后端完整470项通过。
- Python语法编译和显式导入检查通过。
- 行业与市场各连续两轮联合自然调度通过。
- 前端4000、后端8001及ngrok保持运行。
- 生产库当时 `quick_check=ok`、`integrity_check=ok`、外键违规0。

以上是阶段3完成时的证据，本次“上下文保存”没有重新运行完整测试，也没有读取或写入生产数据库。继续实施前应按修改风险运行最小充分验证。

## 固定边界

- 现有单股AI与未来雷达AI严格隔离；不得共用提示词、业务缓存、API、存储、调度或UI状态。
- 真实数据只允许原始数据、可解释计算数据、明确AI判断、缺失/失败四类；禁止生产Mock。
- 真实0与 `NULL` 必须区分；过期数据不得冒充当前数据。
- 不保存全市场逐证券原始行情快照。
- 未经单独授权，不修改生产数据库、环境变量、依赖、系统启动项，不执行Git操作、部署或生产写入。
- 登录、会话、CSRF、用户权限和用户限流继续按用户已接受风险暂缓。
- 固定本地端口只能使用前端4000、后端8001。

## 下一步

下一步只应先提出并确认阶段4的最小实施包：按已批准第二版图稿建立只读雷达数据契约、只读API与 `/radar` 页面骨架，展示真实市场/行业聚合、时间、健康和空/失败/过期状态；ETF与龙头保持明确未启用。

在用户明确授权阶段4生产实现前，不自动修改相关生产代码。
