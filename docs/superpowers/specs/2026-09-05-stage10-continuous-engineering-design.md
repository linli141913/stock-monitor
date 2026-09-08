# 阶段10连续工程收口设计

## 1. 目标

在等待真实交易日样本期间，继续完成三项不依赖当日行情的工程工作：

1. 以一次显式命令协调阶段9、阶段6趋势/龙头和ETF现场观察；
2. 从真实影子台账、运行回执、冻结策略和静态资产自动生成运营证据；
3. 修复正式执行缝的循环信任、任务身份和跨进程防重入问题，但保持零真实executor、零生产接线。

本设计不改变阶段9与阶段10的独立门槛，不把测试、周末演练或一次运行冒充真实观察日，也不读取或写入生产SQLite。

## 2. 全局边界

- 只操作当前项目；保留全部未提交工作树。
- 新增入口、输入、输出、台账、检查点和锁全部限制在经安全验证的`/private/tmp`。
- 不修改`main.py`、生产环境变量、launchd资产、生产开关、生产SQLite、前端页面或AI调用链。
- 不重启或重载4000/8001，不联网执行真实采集，不Git暂存、提交、推送或部署。
- 生产代码先有失败测试；所有状态均强类型、版本化、`extra=forbid`、严格布尔和带时区时间。
- 阶段9cohort、趋势台账、龙头台账和ETF台账是四类独立事实：逐段提交、逐段保留，不设计全局回滚。

## 3. 单次采集协调入口

### 3.1 数据流

新增`run_radar_stage10_single_live_collection.py`。入口只在显式确认、官方日历可验证且处于A股连续竞价时进入现场链路：

1. 运行现有阶段9正式入口；
2. 阶段9入口复用同一阶段6调用并透传`--formal-shadow-input-root`；
3. 阶段6输出返回经验证的趋势/龙头输入目录；
4. 阶段9成功结果返回本轮ETF准入包引用；
5. 协调器分别登记趋势、龙头；某一模块失败不阻塞其他模块；
6. ETF入口只请求一次腾讯行情并完成发布、登记；
7. 每一步把稳定状态写入专用不可变检查点，供崩溃后仅重放尚未完成的离线步骤。

### 3.2 非交易时段

周末可由本地日期直接判定`dry_not_trading`。工作日若没有覆盖当日的本地官方日历原文，只返回`dry_calendar_unverified`。午休、收盘后或盘前返回`dry_not_continuous_session`。以上分支都不得创建锁、目录、cohort、输入包或台账，也不得联网。

### 3.3 状态合同

`radar-stage10-single-live-collection-v1`包含：

- `attemptId/startedAt/finishedAt/tradeDate`；
- `preflight`状态；
- `stage9`的状态、角色、样本、运行ID和清单内容哈希；
- 三模块的`not|available|unchanged|contended|failed|skipped|not_attempted`状态、稳定原因、台账SHA和相对路径；
- 真实已发生副作用清单；
- 固定`formalEnabled=false`。

检查点不得保存绝对路径、Token、行情正文或生产状态。阶段9失败时ETF不请求；阶段9已经登记后阶段10失败不得回滚cohort。恢复模式只能读取检查点中的内容寻址引用重放离线登记；不得重新请求同日ETF并覆盖不同内容。

## 4. 运营证据自动汇总

### 4.1 输入

新增`radar-formal-operational-evidence-input-v1`：

- `subjectId/checkedAt`；
- 显式影子台账仓与内容SHA；
- 显式回执输入包清单，不扫描目录猜测“最新”；
- 外部冻结性能策略，含`policyId/contractVersion/policySha256`及完整阈值；
- 静态安全策略和必需资产角色；
- 回退、运行手册静态资产清单与真实SHA。

所有文件必须是`/private/tmp`下显式快照或经独立参数指定的项目只读资产根；读取器不得读取`.env*`、`backend/data`、venv、node_modules、`.git`或任意用户目录。

### 4.2 汇总规则

- 每份回执必须通过既有适配器重建观察，并与v2台账中的模块、runId、observedAt、evidenceSha、duration、coverage、状态逐项一致。
- 性能样本、P50、P95、最大耗时、失败数、覆盖率和锁竞争只能从真实回执/协调检查点计算。
- 无执行上下文完整性证明时，`reentryCount`不得默认为0；性能门保持`collecting`。
- 无性能策略、策略字段不完整或SHA不符时分别保持`collecting`或`failed`；系统不提供默认阈值。
- 周末和Fixture只标`rehearsal`，不得使真实生产性能门变为`ready`。
- 安全扫描只读取白名单快照，只把相对路径和稳定规则码留在内存；最终报告不输出匹配文本、文件内容、Token、环境值或绝对路径。
- 回退门只验证静态资产和哈希，不执行`reload`、`disable`、`rollback`或任何服务命令；报告必须保留`static_only`范围。
- 输出复用现有`radar-formal-operational-checks-v1`和内容SHA，供正式就绪入口作为四门唯一输入。

## 5. 默认关闭的正式执行暗骨架

### 5.1 修复循环信任

`ready_to_enable`只由九项自动证据门产生。离线输入中的`requested/enabled`不得再把报告提升为`formal_enabled`。在尚无可信运行时证明前，API和离线报告的`formalEnabled`始终为`false`。

未来正式执行还必须具备独立`FormalExecutionBinding`，绑定模块、固定任务ID、执行器ID、执行器合同版本、配置SHA、生成时间、到期时间和自身内容SHA。当前批次只实现合同、加载/守卫和负向测试，不创建任何真实binding，不注册任何任务。

### 5.2 固定身份

三个保留任务ID为：

- `radar-formal-trend-rotation`；
- `radar-formal-etf-observation`；
- `radar-formal-leader-observation`。

`FormalExecutor`必须暴露固定`module/executorId/contractVersion`并只接受冻结`FormalExecutionContext`。结果合同只允许执行状态、时间、稳定原因和候选结果引用；拒绝`formalEnabled/configuredEnabled`及任何状态写入指令。暗骨架不提供正式输出publisher。

### 5.3 执行守卫

每次注册与执行按顺序验证：当前请求、独立binding、正式就绪内容仓引用与SHA、报告和逐证据有效期、目标模块九门、固定任务/执行器身份。随后取得模块独立跨进程锁，并在锁内完整重验一次；竞争时零executor调用。执行结束后当前暗骨架只返回强类型结果，不发布正式输出。

已有同ID任务只有在wrapper身份、模块、binding SHA和执行器身份完全一致时才是`already_registered`；否则返回冲突。单模块坏规格只关闭该模块，不阻塞其他模块。`specs=()`必须零读取、零锁、零调度调用。

## 6. 测试与验收

- 单次协调：缺确认、周末、日历未知、非连续竞价、路径重叠、阶段9失败、阶段6未发布、模块独立失败、ETF锁竞争、发布后登记失败恢复、同attempt幂等和同日异内容。
- 运营汇总：台账/回执/策略/资产哈希与时间绑定、真实0、缺失/失败/过期、分位数、阈值缺失、周末演练不放行、安全脱敏、静态回退不执行。
- 正式暗骨架：循环信任关闭、binding缺失/损坏/过期、固定任务身份、跨进程锁、锁内撤销、冲突任务、模块独立、异常释放锁、结果禁止正式状态字段、main/launchd零接线。
- 完成后运行三组聚焦测试、阶段10相关回归、精确重定向`database.DB_PATH`至`/private/tmp`的完整后端测试、Python编译、`pip check`和`git diff --check`。

## 7. 推进裁决

- 单次协调采用逐段提交与检查点恢复，不采用跨阶段总事务；错误地做总回滚会删除已真实发生的独立事实。
- 运营汇总允许在非交易日生成`collecting/rehearsal`结果，但不允许以测试或周末数据通过生产性能门。
- 当前批次修复正式暗骨架的安全合同，但不实现具体业务executor、不接`main.py`、不改launchd或环境；真实证据成熟后仍需单独生产授权。
