# 阶段 6L-C1 证券生命周期与可交易性研究合同设计

## 目标

在不增加行情请求、不启用正式评分和状态机的前提下，为阶段 6 建立可追溯的
证券生命周期、当日交易状态和涨跌停规则输入合同，并把同一腾讯全市场行情
响应中已经存在的昨收、开盘、最高和最低价纳入研究证据。

本阶段解决“字段和语义没有合同”的问题，不宣称已经具备正式可交易性来源。

## 已确认事实

- `QuoteSnapshot` 当前只有价格、涨跌幅、成交额、换手率、量比和市值等字段。
- 腾讯同一响应中已经包含昨收、开盘、最高和最低价，不需要第二次网络请求。
- 当前证券主档只有代码、名称、交易所、板块和上市日期等基础字段，没有带生效
  区间的 ST、退市整理、重新上市、停牌或异常交易状态。
- 当前运行时把 `stock_gate_passed` 和 `tradability_passed` 固定为 `False`。
- 当前阶段 6 正式状态开关关闭，生产 SQLite 只有迁移版本 1 至 4。

## 设计原则

1. 不根据股票简称猜测 ST、退市整理或异常状态。
2. 不按固定 10% 或 20% 推断涨跌停价格。
3. 不把价格为 0、成交为 0 或无行情静默解释为停牌。
4. 不把缺失值写成 0；真实 0 原样保留。
5. 所有生命周期和规则证据必须绑定证券、交易日、来源合同、版本及有效期。
6. 研究结果可以说明“研究上可交易”或明确排除原因，但不得打开正式门禁。

## 数据合同

### 行情字段扩展

在 `QuoteSnapshot` 增加四个可选字段：

- `previous_close`
- `open_price`
- `high_price`
- `low_price`

字段从现有腾讯响应的 4、5、33、34 号位置解析。它们暂不加入全市场批次
`REQUIRED_FIELDS`，避免尚未完成来源覆盖率核对前改变现有市场和行业健康门禁。

字段必须是有限的非负数。真实 0 允许保留，缺失或非法值返回 `None`。

### 证券生命周期证据

`LeaderSecurityLifecycleEvidence` 至少包含：

- `symbol`
- `exchange`
- `board`
- `lifecycle_status`
- `listed_trading_day_count`
- `source_contract_id`
- `source_name`
- `source_url`
- `document_id`
- `published_at`
- `effective_from`
- `effective_until`
- `fetched_at`

`lifecycle_status` 只允许：

- `normal`
- `st`
- `star_st`
- `delisting`
- `relisted`
- `abnormal`
- `unknown`

上市前 5 个交易日研究上排除；第 6 至 10 个交易日标记
`preliminary_only=true`；超过 10 个交易日按普通证券处理。交易日数量由调用方
根据正式交易日历提供，本模块不按自然日猜测。

### 当日交易状态证据

`LeaderTradingStatusEvidence` 至少包含：

- `symbol`
- `trading_date`
- `status`
- `source_contract_id`
- `source_name`
- `source_time`
- `fetched_at`

`status` 只允许：

- `trading`
- `suspended`
- `abnormal`
- `unknown`

交易状态必须与本轮 `as_of` 同交易日，且满足盘中来源时效。缺失或过期时不能
推断为正常交易。

### 当日涨跌停规则证据

`LeaderTradingRuleEvidence` 至少包含：

- `symbol`
- `trading_date`
- `rule_version`
- `price_limit_mode`
- `upper_limit_price`
- `lower_limit_price`
- `source_contract_id`
- `source_name`
- `source_url`
- `published_at`
- `effective_from`
- `effective_until`

`price_limit_mode` 只允许：

- `bounded`
- `no_limit`
- `unknown`

`bounded` 必须同时提供已经按当日正式规则解析的涨停价和跌停价；本模块只比较
价格，不自行用板块名称和百分比计算边界。`no_limit` 必须保持上下限价格缺失。

## 纯计算模块

新增 `backend/radar/leader_tradability_features.py`，提供：

- `LeaderTradabilityFeatureInput`
- `LeaderTradabilityFeatureResult`
- `build_leader_tradability_features()`
- `missing_leader_tradability_features()`

输出固定包含：

- 公式版本；
- 研究状态；
- 生命周期状态；
- 当日交易状态；
- 涨跌停状态；
- 一字涨跌停状态；
- `researchEligible`；
- `preliminaryOnly`；
- 压缩来源引用；
- 原因列表；
- `scoreReady=false`；
- `formalUsable=false`。

涨跌停状态只在规则为 `bounded` 且行情四价与边界可用时判断。一字状态要求
开盘、最高、最低和现价均等于同一涨停价或跌停价。比较使用人民币最小价格
单位 0.01 元的容差，不重新计算涨跌停边界。

## 运行时接入

`build_leader_runtime_evidence()` 新增可选
`tradability_inputs_by_symbol`。未提供时输出明确的缺失研究证据；提供但证券、
行业批次时间或交易日身份不一致时输出 `source_unverified`。

结果写入：

```text
evidence.researchFeatures.securityTradability
```

本阶段不修改：

- `stock_gate_passed`
- `liquidity_passed`
- `tradability_passed`
- `risk_filter_passed`
- 六维正式分数
- `business_exposure_source_contract_id`
- 状态机正式开关

## 错误和降级语义

- 来源失败优先返回 `source_failed`。
- 未来时间、身份错配、无效区间和非 HTTPS 官方材料返回
  `source_unverified`。
- 证据过期返回 `stale`。
- 正常来源缺字段返回 `missing`。
- `suspended`、`delisting`、`abnormal` 和上市前 5 个交易日属于真实排除，
  不是数据缺失。
- `no_limit` 是真实规则状态，不得冒充 `unknown`。

## 测试边界

必须覆盖：

- 腾讯同一响应解析四个新增字段，且不增加请求次数；
- 真实 0、缺失、非有限值和负值；
- 正常交易、停牌、异常、ST、退市整理和重新上市；
- 上市第 1 至 5、第 6 至 10及第 11 个交易日；
- 有涨跌幅限制、无涨跌幅限制、涨停、跌停和一字涨跌停；
- 生命周期、交易状态和规则的未来、过期、身份错配及无效有效期；
- 默认缺失和完整研究证据的运行时投影；
- 研究证据可用时正式分数、门禁、状态机和 API 仍保持关闭。

## 明确不做

- 不抓取或选择新的生命周期、停牌或交易规则生产来源。
- 不把简称中的 ST 字样作为正式证据。
- 不新增数据库迁移或仓储。
- 不生成正式流动性15分或打开任何正式门禁。
- 不修改 API、前端、提醒、AI、调度、环境变量和依赖。
- 不读取或写入生产 SQLite。
- 不停止、重启或重载 4000/8001。
- 不执行 Git 暂存、提交或推送。

## 后续顺序

1. 阶段 6L-C2 对生命周期、交易状态和当日规则来源做真实只读 POC。
2. 来源通过后再设计版本仓储和正式硬过滤桥接。
3. 随后处理主营正式10分、连续性20分、风险证据和阶段6独立影子校准。
4. 旧单股历史资金源的备用源、熔断和缓存作为独立修复，不与本合同混改。
