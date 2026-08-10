# 阶段6L-C4B RQData字段级真实POC设计

## 目标

使用RQData官方HTTP API对阶段6可交易性字段做一次有界、可脱敏、可复验的真实POC。C4B只回答“供应商是否真实返回所需字段、时间和证券覆盖”，不把供应商数据升级为交易所官方直连，不接生产运行时、数据库、评分、门禁或状态机。

## 当前现场

- 本地后端虚拟环境未安装`rqdatac`，仓库依赖也未声明RQData SDK。
- 当前进程环境没有RQData配置；仓库没有RQData生产适配器。
- 官方文档确认RQData支持Python API和HTTP API，A股数据包含停牌、ST判断、日/分钟/tick及实时行情；HTTP API与Python API方法通用。
- 官方HTTP文档声明返回UTF-8 CSV文本；`get_price`的两处官方示例分别出现`date`和`datetime`日期列，适配器只允许二者之一并统一校验交易日。
- 官方HTTP示例可返回`prev_close`、`limit_up`、`limit_down`、`datetime`和`trading_phase_code`；合约对象可提供证券、交易所、板块、上市/退市和当前状态。
- 试用申请需要用户本人完成手机号验证；凭证、Token和许可证不得由项目代填、保存或写入Git。

官方依据：

- `https://www.ricequant.com/welcome/rqdata`
- `https://rqopen.ricequant.com/doc/rqdata/python/manual.html`
- `https://www.ricequant.com/doc/rqdata/http/data-process`
- `https://rqopen.ricequant.com/doc/rqdata/http/interface-method`

## 方案选择

采用官方HTTP API和Python标准库实现隔离POC，不安装RQData SDK，不新增项目依赖。传输层作为调用参数注入，Fixture测试不联网且永远只能得到`real_poc_status=not_run`；只有未注入测试opener的固定端点HTTP传输才可能记录真实POC完成。真实POC只在用户完成试用并在进程外提供临时Token后运行一次。

不采用以下方案：

- 暂不安装RQData SDK：当前没有许可证安装指引，安装依赖和修改运行环境需要单独授权。
- 暂不接Tushare、Wind、Choice或iFinD：C4B只验证一个供应商，避免口径同时扩散。
- 不直接转换为C3 `official_primary`：供应商响应没有证明交易所级上游来源身份。

## 实施分段

### C4B-1：隔离字段适配器

新增`backend/radar/sources/leader_tradability_rqdata_poc.py`，只提供：

1. 固定方法白名单和请求合同；
2. 标准库HTTPS传输边界；
3. 官方HTTP CSV结果的确定性解析；
4. 字段覆盖、证券身份、交易日、时间和缺失语义审计；
5. 脱敏POC报告和响应内容SHA-256，不保留完整响应正文。

适配器不读取`.env`，不自行申请账号，不保存Token，不把用户名、密码、Token、请求头、响应正文或URL查询参数放进`repr`、异常、证据或日志。

### C4B-2：一次真实小样本POC

用户完成试用后，从进程外临时注入认证信息，最多执行一轮下列只读请求：

- `instruments`：证券身份、交易所、板块、上市/退市和当前合约状态；
- `get_price(adjust_type=none)`：同一交易日的`prev_close`、`limit_up`、`limit_down`及日线存在性；
- `is_suspended(count=1)`：当前交易日最近一次全天停牌判断；
- `is_st_stock(count=1)`：当前交易日最近一次ST与`*ST`判断；
- `current_snapshot`：交易时段内的`datetime`、`trading_phase_code`和动态涨跌停边界。

样本只覆盖少量沪深A股，并至少包含普通交易、ST、停牌和无涨跌幅限制/特殊交易日证据中的可获得类别。样本身份必须来自本轮RQData结果，不依赖证券简称猜测。

C4B-2使用正式单次入口`backend/run_rqdata_tradability_poc.py`。入口要求显式`--confirm-live-poc`，依次核对当前带时区时间、上交所官方交易日历、A股连续交易窗口和C4B查询合同；任一前置条件不满足时在读取Token之前退出。Token只能由终端隐藏输入进入当前进程内存，不允许使用命令行参数、项目配置、`.env`、输出文件或日志传递。

命令只把`RqdataTradabilityPocReport.to_evidence()`输出到标准输出，不包含逐股记录、响应正文、Token、账号、请求头或异常正文。退出码固定为：`0`字段候选、`1`阻断、`2`未运行或部分结果。2026-08-01周六零费用干跑已得到`not_run / rqdata_live_calendar_closed`，未读取Token、未调用RQData；这只证明前置门禁有效，不属于真实POC结果。

### C4B-3：准入矩阵

POC结果固定分为：

- `field_candidate`：字段、证券、交易日和时间满足POC合同，但仍不是正式来源；
- `partial`：部分字段、样本或时间缺失；
- `blocked`：认证、合同、身份、混批、格式或来源失败。

无论哪种结果，`formal_score_ready`、`formal_gate_ready`、`formal_usable`和`state_transition_allowed`全部为`false`。只有字段矩阵、独立授权证据、来源血缘和合法交易窗口动态时效均通过后，后续阶段才可讨论C4A候选批次转换。

## 字段与证据边界

| 目标字段 | POC接口 | C4B可证明 | C4B不能证明 |
| --- | --- | --- | --- |
| 证券/交易所/板块 | `instruments` | 当前合约身份与静态属性 | 历史时点属性一定有效 |
| 生命周期 | `instruments` | 当前上市、退市或暂停上市描述 | 全历史版本链完整 |
| ST状态 | `is_st_stock` | 当前交易日最近一次供应商判断 | 交易所原始文件身份与生效公告 |
| 全天停牌 | `is_suspended` | 当前交易日最近一次供应商判断 | 盘中临停和全部异常阶段 |
| 涨跌停价 | `get_price`/`current_snapshot` | 供应商返回的未复权边界 | 交易规则版本来源已核对 |
| 动态交易状态 | `current_snapshot` | 快照时间和交易阶段码 | 非交易窗口下90秒动态时效 |
| 授权 | 用户试用/合同证据 | 当前账号可调用 | 本地服务长期处理与生产使用必然获准 |
| 上游来源 | 供应商文档与响应摘要 | 数据来自RQData交付 | 交易所级上游原文和版本血缘完整 |

## 错误与费用控制

- 连接、认证、权限、配额、限流、超时、格式和字段缺失分别返回稳定原因码。
- 批量接口每批最多调用一次；`is_suspended`和`is_st_stock`按官方单证券`count=1`签名对每个样本各调用一次。每个“方法+证券”组合最多一次，不自动重试、不递归扩样、不调用付费AI。
- 任一响应出现空数据、缺字段、身份不一致或格式问题后立即停止后续请求；全空响应固定为`real_poc_status=no_data`，不得写成完成。
- 动态窗口、交易日和快照年龄以`fetched_at`为准，`query.as_of`与真实抓取时刻最多允许5秒偏差。
- 超时固定且有界；真实POC成功或失败后立即停止。
- 不读取或写入生产SQLite，不调用现有业务API，不影响4000/8001服务。

## 测试与验收

TDD覆盖：

- 请求方法白名单和固定HTTPS端点；
- Token/凭证不进入对象、异常和报告；
- 正常、真实0、空CSV、缺字段、重复证券、混合交易日、非法代码、未来/过期时间；
- 普通、ST、停牌、特殊交易日和无涨跌幅限制语义；
- 字段齐全仍固定非正式；
- 真实POC未运行时明确`not_run`，不能写成成功。

完成标准：专项测试、全部三级龙头测试和后端完整临时SQLite回归通过；`git diff --check`通过；唯一交接明确区分C4B-1本地能力、C4B-2真实调用和C4B-3正式准入。

## 明确不做

- 不安装SDK、不新增依赖、不修改环境变量或LaunchAgent。
- 不持久化账号、密码、Token、许可证或供应商响应正文。
- 不实现用户名/密码自动换Token，不要求用户把Token粘贴到聊天、命令行参数或环境变量。
- 不修改C3、C4A、运行时、仓储、调度、API、页面或生产配置。
- 不读取或写入生产数据库，不重启服务，不执行Git暂存、提交、推送或部署。
