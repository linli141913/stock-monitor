# 阶段10真实影子台账采集入口实施计划

## 目标

在不读写生产SQLite、不联网、不接入`main.py`、不重载服务和不开启正式状态的前提下，新增独立离线入口，把显式`/private/tmp`真实影子运行回执和权威工件验证后，幂等登记为内容寻址的阶段10三模块台账，并与正式就绪判定中的影子门禁严格绑定。

## 长期边界与裁决

- 旧`radar-formal-shadow-ledger-v1`只做兼容读取，不能单独证明ETF“连续5日”；新采集仓发布`radar-formal-shadow-ledger-v2`。
- 趋势轮动、龙头观察各需20个不同有效A股交易日；ETF需最新一段连续5个有效交易日全部`ready`。日期缺口、`missing/failed/stale`会中断ETF连续计数，非交易日不计入也不人工伪造观察。
- 不把ETF产品准入或历史净值证据写成ETF当日现场行情观察。ETF要计日，必须显式提供由真实现场运行器产生的版本化回执和工件。
- 阶段6五源工件可用于趋势/龙头权威身份与质量交叉验证，但不伪造其中没有的锁、耗时或覆盖字段；这些只能来自运行器回执。
- 同一`(module, 上海交易日)`内容完全相同时返回`unchanged`；内容不同时拒绝`shadow_observation_identity_conflict`，不覆盖首份快照。
- 最新就绪日、累计天数和ETF连续天数全部由已验证台账派生，不接受CLI或正式就绪输入自报。
- 新CLI只读显式输入、只写显式`/private/tmp`新台账仓；拒绝`backend/data`、路径重叠、逃逸、符号链接、非普通文件、根/目录/文件TOCTOU替换。

## Task 1：台账v2与就绪语义

1. 先在`backend/tests/test_radar_formal_shadow_ledger.py`和`backend/tests/test_radar_formal_readiness_service.py`补红灯：ETF非连续5日不就绪、失败/缺失中断、最新日只能派生、v1 ETF不可通过新门、上海跨日与未知交易日失败关闭。
2. 最小新增v2台账合同，包含派生的`latestReadyTradingDateByModule`、`latestReadyStreakByModule`和不可变的观察全集。
3. 正式就绪服务只从v2台账派生日期/天数；三模块互不代替。

## Task 2：真实回执与三模块适配

1. 先新增合同/适配测试并确认红灯：严格布尔、合同版本、`runId/asOf/sourceTime/fetchedAt/observedAt`时序、覆盖范围、真实0、缺失、失败、过期、锁竞争、证据SHA和模块身份。
2. 新增`radar-formal-shadow-run-receipt-v1`，带模块固定的`sourceContractId`、`sourceArtifactSha256`、`coverageScope`与运行指标。`coverage=0`不能进入`ready`。
3. 趋势适配器强绑阶段6市场研究状态+行业快照；龙头适配器强绑同轮`stateDecisionReview`与资格计划；ETF适配器只接受真实当日现场ETF运行工件，产品准入只做身份佐证。
4. 身份、时间、SHA或质量不一致时不生成`ready`观察。

## Task 3：内容寻址仓与离线CLI

1. 先测试幂等、冲突、三模块独立、不可变内容SHA、原子latest、并发锁和全路径/TOCTOU边界，确认红灯。
2. 复用`formal_readiness_store.py`的目录FD、`O_NOFOLLOW`、inode复验、`O_EXCL`临时文件、`fsync+replace`模式，实现`formal_shadow_ledger_store.py`。
3. 新增`run_radar_formal_shadow_observation.py`：显式`--module --receipt --source-artifact --ledger-dir --calendar-artifact`，输出`available|unchanged|contended|failed`与稳定原因，不输出敏感内容。
4. CLI不导入`database`，不读环境，不发网络请求，不触服务。

## Task 4：联合验收与文档

1. 运行新增/受影响定向测试、阶段10聚焦测试、精确隔离生产SQLite的完整后端测试、Python编译、`pip check`和`git diff --check`。
2. 若未修改前端则不借用旧证据宣称本轮重建；若改前端则运行TypeScript、ESLint和Next build。
3. 独立只读审查通过后更新唯一`NEXT_CHAT_HANDOFF.md`，记录实施结果与仍需生产接线/真实交易日的边界。

## 禁止项

- 不用Fixture、Mock、ETF准入、阶段9回放质量或其他模块结果填满真实观察日。
- 不改生产环境变量、`launchd`、正式开关、`main.py`调度、生产SQLite、服务进程或依赖。
- 不Git暂存、提交、推送、部署，不清理旧工件或未提交工作树。
