# 阶段5A行业ETF字段级数据合同与实施计划

> 状态：方案B增强版已由用户批准。本文件只冻结数据合同和实施顺序，不代表阶段5生产能力已经实现或启用。
>
> 阶段5B状态：2026-07-24已完成官方产品主档与分类POC；产品主档模块尚未接入数据库、调度、API或页面。
>
> 阶段5C状态：2026-07-25已完成指数身份、方法和成分版本POC；代表样本仍按证据缺口保持未正式就绪，模块尚未接入数据库、调度、API或页面。
>
> 阶段5D状态：2026-07-25已完成行业暴露、同指数产品组和成分重合度POC；历史真实结果仅作研究证据，模块尚未接入数据库、调度、API或页面。
>
> 阶段5E状态：2026-07-25已完成行情、份额日频事实、排名输入审计和临时SQLite容量POC；正式候选仍被产品/指数证据和未验证排名字段阻断，模块尚未接入数据库、调度、API或页面。

**Goal:** 在现有雷达影子基础上，为阶段5“行业ETF”冻结可追溯、可版本化、可回放的数据合同，使第一版正式观察池只消费证据完整的境内股票型被动ETF；主动ETF和其他基金类别继续只进入名册与健康层。

**Architecture:** 保留现有 `etf_product_registry` 作为来源发现和原始历史层，在其上新增产品归一化、指数关系、指数版本、行业暴露、指标观测和候选结果六类逻辑实体。每类事实独立保存来源时间、生效时间、抓取时间和证据校验值；不建设完整事件系统，也不把不同频率的事实压进一张当前值表。

**Approved Decision:** 使用“方案B增强版”。阶段5和阶段9所需的事实、历史版本、候选快照、来源地址、关键原始字段、证据校验值和规则版本必须保存；不保存每一次内部动作的完整事件流。

**Current Tech Stack:** Python、FastAPI、Pydantic、APScheduler、SQLite、Next.js 16、React 19、TypeScript；不新增依赖。

---

## 1. 阶段5A准确范围

本阶段只完成：

- 冻结正式观察池的准入边界；
- 冻结字段命名、类型、来源职责、时间语义和缺失语义；
- 冻结逻辑存储实体、只读API外壳、刷新和缓存边界；
- 冻结现有影子任务、旧业务表和未来阶段5任务的隔离方式；
- 冻结测试、容量、备份、回退和细分实施顺序。

本阶段明确不做：

- 不修改 `backend/` 或 `stock-monitor/` 生产代码；
- 不创建或迁移SQLite表，不读取或写入生产SQLite；
- 不修改环境变量、依赖、调度器、LaunchAgent或系统启动项；
- 不停止、重启或重载前端、后端和ngrok；
- 不调用正式ETF排名，不把页面ETF模块从 `not_enabled` 改为可用；
- 不实现主动ETF排名、跨境ETF排名、中期跟踪转正、AI解释或提醒；
- 不执行Git提交、推送、部署。

---

## 2. 当前基线和已确认缺口

### 2.1 现有能力

- `backend/radar/sources/etf_registry.py` 当前合并上交所ETF规模入口和深交所上市基金列表，保留交易所原始分类，不推断主动/被动或指数关系。
- `etf_product_registry` 已保存代码、名称、交易所原始类别、部分产品字段、来源时间、抓取时间和版本有效期，可继续作为来源发现和健康层。
- 现有ETF行情任务按300秒运行，只记录来源健康和数量，不保存逐ETF行情明细，也不生成阶段5候选。
- `/api/radar/overview` 当前固定返回ETF `not_enabled`，不存在阶段5只读ETF列表接口。
- 雷达表和旧业务表共用 `backend/data/stock_monitor.db`；隔离必须依靠新表命名、仓储入口、事务、功能开关和任务锁，不能声称已经使用独立数据库。

### 2.2 2026-07-23只读审核快照

以下数字只用于证明当前来源合同不足，不得硬编码为未来预期数量：

- 现有组合适配器返回1,895条记录，其中深交所原始列表包含LOF 285条、REIT 27条，不能把1,895直接称为纯ETF总数。
- 上交所现有规模入口返回882条，而官方产品名录按ETF分类识别907条；产品身份主表和每日规模事实必须分开。
- 深交所当前上市基金列表返回1,013条，其中ETF分类701条；其他上市基金类别必须明确排除或保留为名册项。
- 现有合同没有稳定覆盖主动/被动、官方指数代码、指数公司、方法版本、成分权重版本、行业暴露、同指数产品组、PCF、IOPV、精确价差、跟踪误差和跟踪差异。

结论：

- 当前 `etf_product_registry` 和腾讯行情只能证明产品存在、来源健康和当前报价覆盖；
- 不能直接把现有1,895条名册转成正式行业ETF排名；
- 正式准入必须从完整产品身份源重新归一化，不得以规模接口或聚合行情列表替代产品主档。

---

## 3. 第一版正式观察池边界

### 3.1 可进入正式准入评估

单只产品必须同时满足：

1. 交易所官方产品信息明确认定为ETF；
2. 管理方式由官方材料明确认定为被动指数型；
3. 资产范围明确为境内股票；
4. 产品在 `asOf` 时点已经上市且未终止上市；
5. 官方材料明确给出标的指数代码、名称和指数提供商；
6. 存在 `asOf` 时点有效的指数编制方案和成分权重版本；
7. 指数成分可以使用当时有效的行业分类版本计算暴露；
8. 本轮所需行情和低频事实通过各自来源、完整度和时点门禁；
9. 没有正式终止、清盘、合并、转型或终止上市事实阻止继续观察。

### 3.2 只进入名册和健康层

- 主动ETF；
- 跨境股票ETF；
- 宽基ETF；
- 债券、商品、货币、混合和策略ETF；
- LOF、REIT及其他上市基金；
- 产品类型、管理方式、资产范围或指数关系仍为 `unknown` 的产品；
- 上市状态、指数版本、成分权重或行业映射证据不足的产品。

宽基ETF可以在未来作为市场基准，但第一版不进入行业ETF观察池。主动ETF必须使用独立规则、数据和回放，在单独验收前不得套用被动指数ETF排名。

---

## 4. 标准枚举

### 4.1 产品类型 `productType`

```text
etf
lof
reit
other_listed_fund
unknown
```

### 4.2 管理方式 `managementStyle`

```text
passive_index
active
unknown
```

不得根据“标的指数缺失”推断主动ETF，也不得根据产品名称包含行业词推断被动ETF。

### 4.3 资产范围 `assetClass`

```text
domestic_equity
cross_border_equity
bond
commodity
money_market
mixed
strategy
other
unknown
```

### 4.4 产品生命周期 `productStatus`

```text
pre_listing
listed
temporarily_suspended
terminating
delisted
unknown
```

临时停牌属于时点状态；终止、清盘、合并、转型和终止上市必须保存对应官方事实，不得从某轮名册缺项推断。

### 4.5 字段可用状态 `fieldState`

```text
available
missing
not_applicable
stale
source_failed
source_conflict
source_unverified
```

### 4.6 准入状态 `eligibilityState`

```text
registry_only
pending_evidence
eligible
excluded
inactive
```

### 4.7 模块状态

沿用阶段4：

```text
available
empty
stale
failed
not_ready
not_enabled
```

质量继续使用：

```text
complete
partial
unavailable
```

---

## 5. 时间合同

所有时间使用带时区ISO 8601；交易日字段使用 `YYYY-MM-DD`。

| 字段 | 含义 | 禁止替代 |
|---|---|---|
| `asOf` | 本轮冻结的研究时点 | 不得使用任务结束时间代替 |
| `sourceTime` | 行情或上游事实实际对应时间 | 不得使用抓取时间代替 |
| `sourceReportDate` | 交易所、基金或报告标明的统计日期 | 不得使用访问页面日期代替 |
| `publishedAt` | 官方文件首次公开时间 | 不得使用报告期结束日代替 |
| `effectiveFrom` / `effectiveTo` | 产品、指数、成分、映射或关系的有效期 | 不得只保存当前值 |
| `fetchedAt` | 本系统实际抓取时间 | 不得显示为源更新时间 |
| `computedAt` | 本系统完成归一化或计算的时间 | 不得显示为原始数据时间 |
| `checkedAt` | 只读API生成响应的时间 | 不得改变数据新鲜度 |
| `renderedAt` | 浏览器完成当前页面渲染的时间 | 只在前端生成，不写成源时间 |

时点查询统一满足：

```text
effectiveFrom <= asOf < effectiveTo
```

`effectiveTo` 为空表示当前尚未失效。未来才生效的指数成分、产品关系、行业映射和规则不得进入当前批次。

---

## 6. 字段级来源合同

每个正式字段必须登记：

```text
sourceContractId
fieldKey
primarySource
backupSources
sourceTimeKind
refreshPolicy
maximumLagPolicy
expectedCoverage
missingPolicy
conflictStrategy
formalUse
evidenceRequirement
```

### 6.1 来源职责矩阵

| 字段组 | 主源 | 备用或交叉核验 | 正式使用规则 |
|---|---|---|---|
| 产品身份、交易所原始分类 | 上交所、深交所官方产品名录 | 基金管理人产品页 | 产品名录是身份主表；规模接口不能替代 |
| 主动/被动、资产范围 | 基金合同、招募说明书、交易所官方类别 | 基金管理人产品页 | 必须有明确官方证据；无法确认则 `unknown` |
| 上市、终止、合并、转型 | 交易所公告 | 基金管理人公告 | 缺项不等于退市；冲突时停止正式准入 |
| 标的指数身份 | 基金合同或官方产品页，并与指数公司核对 | 基金公告 | 指数代码、名称、提供商必须一致 |
| 指数方法和生效版本 | 指数公司官方编制方案 | 官方修订公告 | 保存版本、发布日期、生效日、URL和SHA-256 |
| 指数成分和权重 | 指数公司官方成分文件 | 基金PCF仅作交叉核验 | PCF不能静默替代指数成分 |
| 基金规模和份额 | 交易所官方规模数据、基金管理人官方披露 | 基金定期报告 | 保存原始单位和统计日期 |
| 净值 | 基金管理人或交易所官方净值 | 基金定期报告 | 与IOPV分开 |
| IOPV、PCF、申赎状态 | 交易所或基金管理人官方文件 | 无可靠来源时为空 | IOPV只是参考，不等于真实净值 |
| 盘中价格、涨跌幅、成交量、成交额 | 当前已验证的腾讯批量行情合同 | 其他来源需另做POC | 保留源时间、覆盖率和字段完整度 |
| 买卖价差和盘口 | 尚无通过审核的正式源 | 后续POC | 当前固定 `source_unverified`，不参与排名 |
| 历史净值和指数回报 | 基金管理人、指数公司或通过POC的历史源 | 定期报告 | 必须对齐同一交易日和回报口径 |
| 产品风险 | 交易所、基金管理人公告 | 定期报告 | 正式事实与规则风险标记分开 |

来源冲突处理：

1. 不按“最后抓到的值”覆盖；
2. 保存冲突来源、字段、源时间和原值摘要；
3. 当前字段标记 `source_conflict`；
4. 必需字段冲突时产品状态为 `pending_evidence`，不进入正式候选；
5. 只有版本化人工映射或更高优先级官方修订才能解除冲突。

### 6.2 现阶段已知刷新边界

- ETF盘中行情：300秒一个名义周期；
- 行情在抓取时继续使用90秒最大源年龄、5秒未来偏差和现有批次覆盖门禁；
- 产品身份：交易日内每日最多成功一次，生命周期公告可以独立触发版本变化；
- 规模、份额、净值和跟踪数据：按真实发布频率，不用页面轮询频率强刷；
- 指数方法、成分和权重：按官方公布和实际生效日期建立新版本；
- 行业暴露：仅在指数成分版本或行业分类映射版本变化时重算；
- 精确价差、IOPV、PCF、历史净值和跟踪数据的最大延迟，在对应真实来源POC通过后写入来源合同；未冻结前不得正式参与排名。

---

## 7. 逻辑实体合同

以下是逻辑实体，不强制一实体一表；物理合并不能丢失版本、生效时间、来源或缺失语义。

### 7.1 现有原始发现层 `etf_product_registry`

职责：

- 保留交易所原始分类和原始字段；
- 保存来源历史和有效期；
- 提供来源健康、产品发现和差异审计；
- 不直接产生主动/被动、行业归属、指数关系或正式排名。

阶段5不得重写历史记录的语义。

### 7.2 产品归一化版本 `radar_etf_product_profiles`

必需字段：

```text
profileId
symbol
exchange
officialName
productType
managementStyle
assetClass
productStatus
listingDate
manager
sourceContractId
sourceTime / sourceReportDate
fetchedAt
effectiveFrom / effectiveTo
evidenceUrl
evidenceSha256
```

可空字段：

```text
custodian
terminationDate
terminationReason
linkedFundCode
shareClassGroupId
```

同一代码同一时点只能有一个有效归一化版本。

### 7.3 ETF与指数关系版本 `radar_etf_index_relation_versions`

```text
relationId
symbol
indexProvider
indexCode
indexName
relationType = primary_tracking
publishedAt
effectiveFrom / effectiveTo
sourceContractId
evidenceUrl
evidenceSha256
```

被动指数ETF缺少当前有效关系时为 `pending_evidence`。主动ETF不创建伪指数关系。

### 7.4 指数方法版本 `radar_index_methodology_versions`

```text
methodologyVersionId
indexProvider
indexCode
indexName
providerVersion
publishedAt
effectiveFrom / effectiveTo
universeRule
selectionRule
weightingMethod
constituentCap
rebalanceFrequency
evidenceUrl
evidenceSha256
```

官方未提供明确版本号时，可以使用“发布日期 + 文件SHA-256”形成内部版本标识，但页面必须显示为内部证据版本，不得伪装成官方版本号。

### 7.5 指数成分版本

头表 `radar_index_constituent_sets`：

```text
constituentSetId
indexProvider
indexCode
announcedAt
effectiveFrom / effectiveTo
expectedCount
returnedCount
weightTotal
sourceContractId
evidenceUrl
evidenceSha256
```

明细 `radar_index_constituents`：

```text
constituentSetId
stockCode
weight
weightUnit
```

权重缺失不能使用等权补齐。成分数量、权重总和和重复代码必须在整批门禁中记录。

### 7.6 行业暴露版本 `radar_index_industry_exposures`

```text
exposureVersionId
constituentSetId
industryReleaseId
industryCode
industryName
exposureRatio
mappedWeight
unmappedWeight
mappingCoverage
computedAt
calculationVersion
```

计算规则：

```text
行业暴露 = 当前指数成分中，映射到该行业的原始权重之和
映射覆盖率 = 已有正式行业映射的原始权重 / 指数有效权重总和
```

禁止：

- 不把未映射权重重新分配给已映射行业；
- 不把最大行业暴露强制写成唯一行业；
- 不用当前行业分类补写历史指数版本；
- 不用ETF名称关键词代替成分权重计算。

### 7.7 日频事实 `radar_etf_daily_facts`

```text
symbol
tradeDate / sourceReportDate
fundSize
fundSizeUnit
fundShares
fundSharesUnit
nav
navCurrency
shareChange5d
shareChange20d
averageTurnover20d
trackingDifference
trackingError
indexCorrelation
windowTradingDays
sampleCount
formulaVersion
fieldStates
sourceContractIds
fetchedAt
computedAt
```

`fundSize` 优先使用官方规模。若未来允许计算值，必须另标 `valueKind=calculated` 并保存公式和输入，不能与官方规模混为同一事实。

### 7.8 盘中特征快照 `radar_etf_feature_snapshots`

```text
radarRunId
asOf
symbol
sourceTime
fetchedAt
price
changePercent
turnoverVolume
turnoverAmount
bid1
ask1
spreadBps
iopv
premiumDiscountRate
fieldStates
formalUsable
reasonCodes
```

第一版不保存完整盘口、逐笔成交或上游原始响应。只有通过产品和指数准入的ETF进入阶段5特征存储；完整名册继续只做健康和排除计数。

### 7.9 候选结果

汇总 `radar_etf_candidate_snapshots`：

```text
radarRunId
asOf
ruleVersionId
registryCount
etfCount
eligibleProductCount
industryThemeCount
computedCount
staleCount
missingCount
excludedCount
candidateGroupCount
coverage
quality
reasonCounts
createdAt
```

明细 `radar_etf_candidate_entries`：

```text
radarRunId
industryCode
indexGroupKey
rank
representativeSymbol
alternativeSymbols
industryExposures
rankingComponents
entryReasons
riskReasons
exitConditions
formalUsable
```

同一行业最多输出5个不同指数产品组。真实结果不足5组时保持实际数量。

---

## 8. 指标公式边界

### 8.1 同指数产品组

```text
indexGroupKey = normalized(indexProvider) + ":" + normalized(indexCode)
```

指数方法版本是时点属性，不进入长期组标识。一个产品组先选代表ETF，组内其他产品作为替代项，不得重复占用行业前5名。

### 8.2 买卖价差

只有同一源时间存在正数 `bid1` 和 `ask1` 时才计算：

```text
mid = (ask1 + bid1) / 2
spreadBps = (ask1 - bid1) / mid * 10000
```

缺少任一字段、买一大于卖一、时间不同步或来源未验证时为 `null`，不使用日内振幅替代。

### 8.3 折溢价

仅在价格与IOPV的资产、币种和时点口径一致时计算：

```text
premiumDiscountRate = (price - iopv) / iopv
```

IOPV为参考值，不等于真实净值。没有可验证IOPV时不使用单位净值冒充盘中IOPV。

### 8.4 份额变化

```text
shareChangeNd = currentShares / priorShares - 1
```

必须保存两个统计日期和样本缺口。字段名称固定为份额变化，不得改写为资金净流入或资金净流出。

### 8.5 日均成交额

`averageTurnover20d` 只使用20个完整交易日的正式成交额。盘中累计成交额不能与完整交易日平均值直接比较，也不能按已交易分钟简单线性外推。

### 8.6 跟踪差异、跟踪误差和相关性

三者必须分开：

```text
trackingDifference = 同一窗口ETF累计回报 - 标的指数累计回报
trackingError = stddev(ETF日回报 - 指数日回报) * sqrt(250)
indexCorrelation = corr(ETF日回报, 指数日回报)
```

要求：

- ETF回报优先使用包含分红影响的正式净值回报；
- 指数必须明确价格指数或全收益指数口径；
- 保存窗口、样本数、缺失交易日和公式版本；
- 样本不足、口径不一致或源时间错位时为 `null`；
- 正式窗口和最低样本数由后续 `radar-etf-rule-v1` 在真实来源POC后冻结。

### 8.7 排名

阶段5A只冻结输入和缺失行为，不提前冻结未经POC验证的权重。

排名顺序必须满足：

1. 产品和指数硬门槛；
2. 行业暴露及覆盖门槛；
3. 同指数归组；
4. 组内比较规模、流动性、价差、折溢价稳定、跟踪质量和产品风险；
5. 每个指数产品组只保留一个代表进入行业排名；
6. 可选字段缺失不记零，不把对应权重重分配给其他字段；
7. 必需字段缺失时不生成正式候选。

正式权重、阈值和窗口必须作为 `radar-etf-rule-v1` 单独版本化，并在5E真实来源POC及回放样本完成后审核。

---

## 9. 状态与缺失语义

### 9.1 模块状态

- `not_enabled`：阶段5开关关闭；不得连接阶段5仓储或请求阶段5新来源。
- `not_ready`：来源请求可以成功，但产品分类、指数关系、历史窗口或规则版本尚未达到正式计算条件。
- `empty`：本轮所有必需来源和完整度门禁通过，真实候选数量为0。
- `stale`：存在最近成功快照，但交易时段内已经超过对应任务新鲜度；返回 `usingLastSuccess=true`。
- `failed`：最新尝试的必需来源或计算失败；有旧成功时仍单独返回旧成功身份，不把失败伪装成成功。
- `available`：本轮可用；字段或可选维度缺失时质量可以是 `partial`。

### 9.2 空、失败和过期

- 空数组只有在来源成功、预期集合完整且规则计算成功时才是 `empty`；
- 来源返回空但预期不应为空时是来源异常，不是真实空榜；
- 最新尝试失败且存在旧成功时，必须同时展示失败原因和旧数据时间；
- 非交易时段保留最后收盘结果，不按浏览器当前时间持续老化；
- 日频或版本型事实按自身发布频率判断，不套用300秒行情阈值。

### 9.3 标准原因码

至少包括：

```text
non_etf_product
active_etf_registry_only
non_domestic_equity
broad_market_benchmark_only
product_status_inactive
product_type_unknown
management_style_unknown
index_identity_missing
index_identity_conflict
methodology_version_missing
constituent_version_missing
constituent_coverage_insufficient
industry_mapping_insufficient
quote_missing
quote_stale
source_failed
source_conflict
metric_source_unverified
history_insufficient
product_risk_blocked
```

页面可以翻译原因码，但API和仓储保存稳定代码，不保存随文案变化的字符串作为唯一判断依据。

---

## 10. 只读API合同

### 10.1 路由

新增：

```text
GET /api/radar/etfs
```

可选查询：

```text
industryCode
asOf
```

`asOf` 只允许读取已经冻结并存在的历史快照，不能触发补算或写入。第一版不新增管理型重算接口。

### 10.2 响应外壳

```json
{
  "schemaVersion": "radar-etfs-v1",
  "checkedAt": "ISO-8601",
  "mode": "shadow",
  "marketSession": {},
  "module": {
    "state": "available",
    "quality": "complete",
    "usingLastSuccess": false,
    "lastAttempt": {},
    "lastSuccess": {},
    "freshness": {},
    "sources": [],
    "summary": {},
    "items": []
  }
}
```

`summary` 至少返回：

```text
registryCount
etfCount
eligibleProductCount
industryThemeCount
computedCount
candidateGroupCount
staleCount
missingCount
excludedCount
exclusionCounts
```

`items` 每项至少返回：

```text
industry
indexGroup
representativeEtf
alternativeEtfs
industryExposures
productStatus
metrics
rankingComponents
entryReasons
riskReasons
exitConditions
ruleVersionId
asOf
sourceTime
fetchedAt
formalUsable
```

要求：

- 沿用阶段4的 `lastAttempt`、`lastSuccess`、`freshness` 和 `sources` 模型；
- `Cache-Control: no-store, max-age=0`；
- API只使用SQLite只读连接和 `query_only`；
- 页面或API请求不得触发外部来源抓取、计算或数据库写入；
- `/api/radar/overview` 只增加ETF摘要，不塞入完整ETF列表；
- 错误响应不包含数据库路径、堆栈、上游响应或内部SQL。

---

## 11. 刷新、缓存与任务边界

### 11.1 盘中

- 现有ETF行情名义周期保持300秒；
- 阶段5启用后复用同一轮已经通过健康门禁的不可变行情批次计算特征，不再为同一ETF集合重复请求一次腾讯行情；
- 页面ETF模块最多每300秒读取一次只读API，手动刷新也只读数据库；
- 交易时段模块新鲜度初始按“300秒周期 + 30秒容差”设计，最终值进入版本化配置；
- 抓取时的单条行情源年龄仍遵守现有90秒门禁，两种新鲜度不能混为一个指标。

### 11.2 低频

- 产品名册和归一化分类每日最多成功刷新一次；
- 指数方法、成分和权重按官方发布及生效版本检查；
- 规模、份额、净值、PCF和跟踪数据各自独立刷新；
- 低频来源失败不得阻断既有股票、市场和行业任务；
- 浏览器缓存不能反向覆盖后端历史。

### 11.3 功能开关与锁

未来新增：

```text
RADAR_ETF_STAGE5_ENABLED=false
```

合同：

- 默认关闭；
- 关闭时在获取阶段5任务锁、连接数据库和请求阶段5新增来源之前返回；
- 不改变现有 `RADAR_ENABLED`、`RADAR_SHADOW_MODE` 和 `RADAR_ETF_SCAN_INTERVAL_SECONDS` 的含义；
- 阶段5使用独立进程内锁和跨进程锁；
- APScheduler继续使用 `max_instances=1`、`coalesce=True`；
- 新任务必须与股票、现有ETF健康、行业、市场和名册任务错峰。

阶段5A不修改任何实际环境变量或运行资产。

---

## 12. SQLite隔离和容量合同

### 12.1 隔离

- 新表统一使用 `radar_etf_` 或 `radar_index_` 前缀；
- 仓储只能位于 `backend/radar/`；
- 不从 `backend/database.py` 的旧股票业务表读取ETF排名事实；
- 新表可以引用现有 `radar_runs`、`radar_rule_versions` 和雷达来源状态，不向旧业务表增加触发器；
- 新增迁移必须是显式、可校验、只增结构的迁移；
- API连接继续使用SQLite `mode=ro` 和 `PRAGMA query_only=ON`。

### 12.2 存储边界

第一版不保存：

- 全量ETF原始上游响应；
- 逐笔成交；
- 完整盘口深度；
- 全量ETF每5分钟未归一化行情副本；
- 重复的指数成分文件正文。

第一版保存：

- 关键官方文件URL、发布日期、有效期和SHA-256；
- 归一化产品、指数关系、方法、成分和行业暴露版本；
- 正式准入ETF所需的日频事实和盘中特征；
- 每轮候选汇总、代表产品、替代产品和排除原因；
- 支持阶段9时点回放的规则版本和引用身份。

### 12.3 启用前容量门槛

在任何生产迁移或持续写入前，必须使用临时SQLite完成：

```text
正式准入ETF数量 N
N × 48轮/交易日
20个交易日明细增长
60个交易日明细增长
单行平均字节数
索引体积
单轮事务耗时
行业查询P95
备份耗时
关闭重开完整性
```

保留周期和是否生成长期日频汇总必须在容量结果后单独冻结。未冻结保留策略前不得开启生产持续写入。

---

## 13. 测试合同

### 13.1 数据合同和来源

- 上交所ETF、深交所ETF、LOF、REIT、主动ETF、跨境ETF和未知类别；
- 交易所分类映射版本变化；
- 产品名称变化但代码不变；
- 上市、停牌、终止、清盘、合并、转型和退市；
- 主动ETF没有指数关系；
- 被动ETF指数代码缺失或两方冲突；
- 指数方法修订、未来生效和历史失效；
- 成分重复、权重缺失、权重总和异常和成分空集；
- 同一指数多ETF归组；
- 多行业暴露、未映射权重和映射版本变化；
- 真实0、缺失、失败、不适用、过期、未来时间和来源冲突；
- 份额变化不会输出资金流；
- IOPV、净值、折溢价、跟踪误差和跟踪差异不会混用。

### 13.2 计算和仓储

- 行业暴露不重新分配未知权重；
- 同指数产品组只占一个候选名额；
- 候选不足5组时保持真实数量；
- 必需字段缺失不生成正式候选；
- 可选字段缺失不记零、不重新分配权重；
- 版本时点查询无未来数据；
- 同一事实重试幂等，事实变化生成新版本；
- 整批失败事务回滚；
- 历史补写和当前运行分开；
- 临时文件库关闭重开、`quick_check`、`integrity_check` 和外键检查通过。

### 13.3 任务和隔离

- 阶段5开关默认关闭且不触库、不请求来源、不创建锁；
- 现有ETF健康任务关闭阶段5后行为不变；
- 阶段5复用同一行情批次，不重复外部请求；
- 盘前、午休、收盘、周末、节假日和日历未知正确跳过；
- 进程内和跨进程防重入；
- 阶段5失败不影响股票、行业、市场、提醒和旧单股AI；
- 测试只使用内存或临时SQLite，不打开生产SQLite。

### 13.4 API和前端

- `available`、真实 `empty`、`stale`、`failed`、`not_ready`、`not_enabled`；
- `usingLastSuccess` 与最新失败同时存在；
- 源时间、抓取时间、检查时间和渲染时间分别显示；
- 同指数替代产品不会占用主列表名额；
- 页面不显示“资金流入”“推荐买入”“配置优先级”等越界文案；
- 1920×1080正常状态、空榜、过期、失败、未就绪和未启用；
- 浏览器请求拦截验收结束后恢复真实接口；
- ESLint、TypeScript、Next.js生产构建和相关后端测试通过。

现有完整后端测试具有导入 `backend/database.py` 后可能初始化生产库的副作用。在隔离该副作用前，不能把完整测试作为“不触碰生产SQLite”的证明；阶段5测试必须显式注入临时数据库路径。

---

## 14. 回退合同

### 14.1 代码和任务

1. 关闭 `RADAR_ETF_STAGE5_ENABLED`；
2. 阶段5任务在锁、数据库和来源之前跳过；
3. ETF页面模块回到 `not_enabled`；
4. 回退阶段5代码，不改变阶段0至4模块；
5. 不删除已经写入的雷达ETF历史。

### 14.2 数据库

- 生产迁移前必须创建在线一致性备份、SHA-256和独立完整性验证；
- 新迁移必须先在生产库临时复制件演练；
- 添加表后的常规回退优先关闭功能并保留表，不执行破坏性删表；
- 只有确认需要恢复旧结构时，才使用迁移前备份整体恢复，并需单独授权停机和恢复；
- 不得手工删除候选或补写生产状态。

### 14.3 来源

- 单一低频来源失败只降低对应字段或准入状态；
- 必需产品、指数或行情来源失败时允许空榜或保持上次成功，不切换到低可信来源静默补位；
- 备用源启用必须在来源合同中预先登记并保留冲突证据。

---

## 15. 细分实施顺序

### 阶段5B：官方产品主档与分类POC

目标：

- 分开“产品身份主表”和“每日规模事实”；
- 验证沪深官方产品名录的真实ETF范围；
- 冻结交易所原始分类到 `productType`、`managementStyle` 和 `assetClass` 的版本化映射；
- 验证主动ETF、LOF、REIT、跨境、宽基和未知类别的排除语义。

边界：

- 先使用Fixture和真实只读POC；
- 不迁移生产库，不启用任务，不改页面。

完成门槛：

- 官方ETF身份覆盖、重复代码、分类冲突、源时间和最大延迟均有证据；
- 当前1,895条原始发现集合与正式ETF集合的差异可解释。

### 阶段5C：指数身份、方法和成分版本POC

目标：

- 建立ETF到官方指数的关系；
- 验证主要指数公司的指数代码、编制方案和成分权重来源；
- 冻结方法版本、成分版本、生效日和证据哈希合同。

完成门槛：

- 被动ETF不能靠名称猜指数；
- 指数关系冲突、成分空集、未来生效和历史失效有测试；
- 没有可靠官方成分的产品保持 `pending_evidence`。

### 阶段5D：行业暴露和同指数产品组

目标：

- 使用当时有效指数成分与行业映射计算多行业暴露；
- 保存映射覆盖率和未知权重；
- 按指数提供商和代码建立产品组；
- 计算同类指数成分重合度，但不提前生成最终排名。

完成门槛：

- 未映射权重不重新分配；
- 同指数多ETF不会重复占候选名额；
- 历史时点不使用当前成分或当前行业分类补写。

### 阶段5E：行情、日频事实和排名输入POC

目标：

- 复用现有300秒ETF行情批次；
- 验证规模、份额、净值、IOPV、PCF、价差和历史回报来源；
- 冻结可正式使用的指标、窗口、样本数、最大延迟和公式版本；
- 完成临时SQLite容量估算。

完成门槛：

- 未通过POC的指标保持 `source_unverified`；
- 份额变化、折溢价、跟踪差异和跟踪误差不会混用；
- 形成 `radar-etf-rule-v1` 权重和阈值审核稿；
- 生产保留周期和增长预算得到用户确认。

### 阶段5F：迁移与仓储

目标：

- 新增版本化实体、时点查询、幂等写入、事务回滚和完整性约束；
- 先完成内存及临时文件SQLite测试；
- 再单独申请生产备份和加法迁移授权。

完成门槛：

- 关闭重开、完整性、外键、旧数据数量不变和代码回退均通过；
- 未经授权不对生产SQLite执行迁移。

### 阶段5G：影子执行器和调度

目标：

- 新增默认关闭的阶段5开关、独立锁和候选影子执行器；
- 在现有健康ETF行情批次通过后计算阶段5特征；
- 低频产品、指数和日频事实独立刷新。

完成门槛：

- 默认关闭不触库、不请求来源；
- 不重复请求同一轮ETF行情；
- 不影响股票、现有ETF健康、行业和市场任务；
- 来源失败、空榜、过期和未就绪均有自然运行证据。

### 阶段5H：只读API与ETF页面

目标：

- 实现 `GET /api/radar/etfs`；
- 把 `/api/radar/overview` 的ETF模块从固定占位扩展为摘要；
- 接入批准的阶段4页面位置，展示真实候选、替代产品、时间、来源和状态。

完成门槛：

- API全程只读；
- 页面不触发来源或写入；
- 1920×1080正常、真实空榜、过期、失败、未就绪和关闭状态通过；
- 无Mock、无投资建议文案。

### 阶段5I：影子观察与阶段5工程验收

目标：

- 冻结规则和人工抽查标准；
- 按启用规则需要，以真实且通过时间点校验的历史数据满足20日或60日指标窗口；
- 连续5个真实A股交易日完成现场影子运行，并覆盖开盘、盘中、收盘和休市边界；
- 自动验证来源失败、数据过期、必需字段缺失、重复调度、身份漂移和非交易时段六类异常场景；
- 分开记录中期1、3、6个月观察的未满足状态；
- 报告完整度、空榜、误报、来源失败、候选稳定性和容量增长。

完成门槛：

- 未来数据违规为0；
- 同指数重复占位为0；
- 数据异常不会生成伪候选；
- 用户完成结果抽查；
- 历史窗口、5日现场运行和异常场景三项必须分别通过，任何一项都不能替代其他项；
- 5日现场通过只证明运行链稳定，不证明ETF排名有效；排名有效性继续由阶段9历史回放和阶段10正式启用验收判断。

---

## 16. 阶段5A完成标准

- 用户已批准方案B增强版；
- 第一版正式池和名册层边界明确；
- 字段、来源、时间、缺失、冲突和版本语义明确；
- 存储、API、刷新、隔离、测试、容量和回退合同明确；
- 阶段5B至5I的顺序和门槛明确；
- 阶段5页面仍保持 `not_enabled`；
- 未修改生产代码、SQLite、环境变量、依赖、服务或启动项。

---

## 17. 阶段5B完成证据：官方产品主档与分类POC

### 17.1 实现边界

新增：

- `backend/radar/sources/etf_product_master.py`；
- `backend/tests/test_radar_etf_product_master.py`；
- `EtfProductMasterRecord`、`ListedFundProductType`、`EtfManagementStyle` 和 `EtfAssetClass` 合同。

保持不变：

- 现有 `fetch_etf_registry` 及其每日名册/规模健康链路；
- 现有SQLite结构、仓储和迁移；
- 现有300秒ETF行情任务；
- 环境变量、调度、API、页面和服务；
- ETF页面 `not_enabled` 状态。

新适配器当前只作为独立POC入口，不被 `backend/main.py`、运行时或仓储导入。

### 17.2 冻结的分类映射

上交所仅在官方分类接口返回对应代码和名称时归一化：

| 官方代码 | 产品类型 | 资产类别 |
|---|---|---|
| `F111`、`F112`、`F114`、`F115` | ETF | `domestic_equity` |
| `F113` | ETF | `cross_border_equity` |
| `F131` | ETF | `unknown`，原因 `cross_border_asset_unverified` |
| `F121`、`F122`、`F123` | ETF | `bond` |
| `F141` | ETF | `commodity` |
| `F150` | ETF | `money_market` |
| `F2xx` | LOF | `unknown` |
| `F6xx` | REIT | `unknown` |
| `F4xx` | 其他上市基金 | `unknown` |
| 未出现在官方分类表中的代码 | `unknown` | `unknown` |

`F131` 官方只定义为“跨境ETF”，没有证明全部属于股票资产，因此不得统一写成跨境股票ETF。

深交所：

| 官方字段 | 产品类型或资产类别 |
|---|---|
| `基金类别=ETF` | 产品类型 `etf` |
| `基金类别=LOF` | 产品类型 `lof` |
| `基金类别=不动产基金` | 产品类型 `reit` |
| `投资类别=债券基金` | 资产类别 `bond` |
| `投资类别=货币市场基金` | 资产类别 `money_market` |
| `投资类别=混合基金` | 资产类别 `mixed` |
| `投资类别=股票基金` | 资产类别 `unknown`，原因 `equity_region_unverified` |
| `投资类别=其它基金` | 资产类别 `unknown` |

深交所“股票基金”没有区分境内与跨境，不能直接映射为 `domestic_equity`。

主动/被动：

- 沪深产品主档均未提供可直接确认主动/被动的正式字段；
- ETF名称包含“主动”不自动形成正式分类；
- 1,608只ETF的 `managementStyle` 在本阶段全部保持 `unknown`；
- 正式主动/被动关系必须在阶段5C使用基金合同、官方产品详情和指数公司证据确认。

分类映射版本固定为：

```text
radar-etf-product-classification-v1
```

### 17.3 真实官方源POC

只读时间：

```text
asOf      = 2026-07-24T23:32:28.658123+08:00
fetchedAt = 2026-07-24T23:32:28.658241+08:00
```

结果：

| 项目 | 数量或覆盖率 |
|---|---:|
| 官方上市基金总数 | 2,225 |
| 返回数 | 2,225 |
| 行覆盖率 | 100% |
| 来源问题 | 0 |
| ETF | 1,608 |
| 上交所ETF | 907 |
| 深交所ETF | 701 |
| LOF | 404 |
| REIT | 86 |
| 其他上市基金 | 10 |
| 官方分类表未定义的 `F300` | 117 |
| 名称覆盖 | 100% |
| 产品类型确认覆盖 | 94.7416% |
| 上市日期覆盖 | 99.5955% |
| ETF资产类别确认覆盖 | 50% |
| ETF主动/被动确认覆盖 | 0% |

ETF资产类别：

| 资产类别 | 数量 |
|---|---:|
| 境内股票 | 685 |
| 跨境股票 | 31 |
| 债券 | 53 |
| 商品 | 8 |
| 货币 | 27 |
| 未确认 | 804 |

未确认原因：

| 原因 | 数量 |
|---|---:|
| 管理方式未验证 | 1,608 |
| 深交所股票ETF地域未验证 | 632 |
| 上交所跨境ETF资产未验证 | 125 |
| 其他ETF资产类别未验证 | 47 |

`F300` 的117条记录未出现在上交所官方分类代码接口中，样本表现为传统场内基金。当前继续保存原始代码并标记 `unknown`，不根据名称补分类。

### 17.4 时间和最大延迟

- 沪深官方产品名录都是当前列表，没有提供统一 `sourceReportDate`；
- 批次 `sourceTime` 保持 `null`，只保存真实 `fetchedAt`；
- 产品主档计划每个A股交易日最多成功检查一次；
- 连续两个A股交易日没有健康检查时，产品主档来源状态标记 `stale`；
- 来源变旧不自动终止现有产品生命周期，但阻止生成新的正式候选；
- 上市、终止、清盘、合并、转型和退市仍需独立生命周期公告事实，不能从列表缺项推断。

### 17.5 验证

- 新增8项产品主档专项测试；
- 连同现有ETF来源和雷达合同共32项测试通过；
- Python语法编译通过，字节码只写任务专用临时目录；
- 真实POC未连接或写入生产SQLite，没有保存官方响应正文或逐产品结果；
- 未新增依赖，未修改服务、环境变量、调度、API或页面。

### 17.6 下一步

进入阶段5C“指数身份、方法和成分版本POC”：

1. 对被动ETF建立基金官方材料与指数公司的双向身份核验；
2. 冻结指数代码、指数公司、方法版本、公布时间和生效时间；
3. 验证官方成分与权重来源、重复、空集、未来生效和历史失效；
4. 只有完成产品主动/被动和指数证据的ETF，才允许进入阶段5D行业暴露计算。

阶段5C通过前不迁移生产SQLite、不启用阶段5任务、不新增ETF API，也不改变页面 `not_enabled`。

---

## 18. 阶段5C完成证据：指数身份、方法和成分版本POC

### 18.1 实施边界

新增：

- `backend/radar/sources/etf_index_evidence.py`
- `backend/tests/test_radar_etf_index_evidence.py`
- 指数身份、方法版本、成分版本和证据状态合同

以上代码只用于Fixture和显式调用的真实只读POC，没有接入：

- 生产SQLite、迁移或仓储；
- `main.py`、调度器或阶段5运行任务；
- 阶段4雷达只读API；
- `/radar` 页面或浏览器刷新链路。

阶段5C没有保存官方响应正文、PDF、Excel或逐产品结果。

### 18.2 冻结判断

ETF与指数关系：

- 基金官方材料必须明确被动指数属性；
- 基金方指数代码和名称必须与指数公司官方身份完全一致；
- 只有名称、没有基金方指数代码时，不按名称猜测指数关系；
- 主动ETF不创建伪指数关系；
- 身份一致与“正式版本就绪”分开。缺少关系发布日期或生效日时可以记录身份已核对，但 `formalReady=false`。

指数方法：

- 保存指数公司、指数代码、名称、官方版本标签或内部证据版本、发布日期、生效和失效时间、方法摘要、URL与SHA-256；
- 官方文件没有版本号时，允许使用文件元数据日期和SHA-256生成 `internal_evidence`，不得冒充官方版本；
- 缺少准确发布日期或生效日时保持未正式就绪；
- 未来生效和历史失效版本均不能用于当前时点。

成分和权重：

- 成分代码、名称、权重、单位、来源日期、公布/生效/失效时间分别保存；
- 权重缺失不补等权；
- 重复代码、空集、数量不足、权重缺失、权重总和异常、未来生效和历史失效均阻止正式使用；
- 完整权重合计允许 `100% ± 0.5` 个百分点的官方四舍五入误差，超出范围标记 `constituent_weight_total_abnormal`；
- 当日成分文件与月末权重文件是两个版本，不能跨日期静默拼接；
- 月末完整权重只能作为该月末历史快照，不能自动升级为当前权重版本。

### 18.3 真实官方源POC

只读时间：

```text
asOf = 2026-07-25T00:13:43.867711+08:00
```

ETF与指数关系：

| ETF | 基金官方证据 | 指数公司证据 | 结果 |
|---|---|---|---|
| 159915 易方达创业板ETF | 被动指数；创业板指数；代码399006 | 国证399006创业板指数 | 身份完全一致；缺关系发布日期和生效日，`formalReady=false` |
| 510310 易方达沪深300ETF | 被动指数；沪深300指数；未披露基金方指数代码 | 中证000300沪深300指数 | 不按名称猜代码，保持 `pending_evidence` |

指数方法：

| 指数 | 版本证据 | SHA-256 | 结果 |
|---|---|---|---|
| 国证399006 | PDF内部证据版本 `internal-2025-06-10-02f76513f29ee502` | `02f76513f29ee502570053034051aa5644cc0ae0560b702deabea4a16f0666b8` | 方法字段可读，但缺正式发布日期和生效日 |
| 中证000300 | 官方文件标签 `2023-09` | `de6491e03e5d57ecf1aca104b1412543643a59e387a5343c9bd21ecbbdeba5b6` | 方法字段可读，但缺准确发布日期和生效日 |

指数成分和权重：

| 指数与版本 | 成分 | 已披露权重 | 权重合计 | 结果 |
|---|---:|---:|---:|---|
| 国证399006，来源日2026-07-24 | 100 | 10 | 58.79% | 其余90只权重缺失，不补等权 |
| 中证000300，当日成分2026-07-24 | 300 | 0 | — | 可确认当前成分，不能形成权重版本 |
| 中证000300，月末权重2026-06-30 | 300 | 300 | 100.008% | 权重完整且为四舍五入误差；缺公布/生效区间，只作为历史证据 |

真实POC没有发现代表样本代码或名称冲突；冲突、重复、空集、未来生效和历史失效由Fixture覆盖。代表样本没有任何一条关系、方法和当前成分权重证据链同时满足正式门槛，因此不得生成阶段5正式候选。

### 18.4 源时间和最大延迟

- 基金产品页、指数身份页和方法文件分别保存真实 `fetchedAt` 和证据SHA-256；来源没有发布日期时保持 `publishedAt=null`；
- 静态身份和方法证据每个A股交易日最多健康检查一次，连续两个A股交易日没有健康检查时标记 `stale`；
- 当前成分来源日期必须达到最近一个已完成A股交易日；漏一个交易日后阻止新候选；
- 历史月末权重不套用“一个交易日”延迟后继续冒充当前权重；
- 方法或成分的官方公布时间、生效时间和失效时间按版本事实判断，不套用300秒ETF行情阈值；
- 判断交易日延迟必须复用已验证的官方交易日历，不能用自然日或浏览器时间代替。

### 18.5 验证

- 新增11项指数证据专项Fixture测试；
- 连同阶段5B产品主档、现有ETF来源和雷达合同共43项测试通过；
- 覆盖代码/名称一致、名称无代码、身份冲突、主动ETF、关系日期缺失、结构化产品页解析、方法未来生效与历史失效、成分真实0、重复、空集、权重缺失、权重异常和版本时点；
- 真实POC使用易方达、中证指数和国证指数官方来源，所有响应只在内存解析；
- 未新增依赖，未读取或写入生产SQLite，未修改环境变量、服务、调度、API或页面。

### 18.6 下一步

进入阶段5D“行业暴露和同指数产品组”POC：

1. 用Fixture验证原始权重到行业暴露的计算，未知权重不重新分配；
2. 用中证000300的2026-06-30完整历史权重只做历史时点POC，不冒充当前版本；
3. 按指数公司与指数代码建立产品组，同指数多ETF只占一个候选组；
4. 国证399006和其他缺当前完整权重的产品继续保持 `pending_evidence`；
5. 阶段5D仍不迁移生产SQLite、不启用任务、不新增ETF API，也不改变页面 `not_enabled`。

---

## 19. 阶段5D完成证据：行业暴露和同指数产品组POC

### 19.1 实施边界

新增：

- `backend/radar/etf_industry_exposure.py`
- `backend/tests/test_radar_etf_industry_exposure.py`
- 行业暴露结果、指数产品组、产品组汇总和指数成分重合度合同

以上均为纯计算代码，没有接入生产SQLite、仓储、调度、API或页面。真实来源仍由阶段5C显式只读抓取器提供，阶段5D没有新增后台任务或持久化路径。

### 19.2 冻结公式

行业暴露：

```text
totalWeight = 所有成分原始权重之和
industryRawWeight = 映射到该行业的成分原始权重之和
mappedWeight = 所有已映射行业原始权重之和
unmappedWeight = totalWeight - mappedWeight
exposureRatio = industryRawWeight / totalWeight
mappingCoverage = mappedWeight / totalWeight
```

约束：

- 未映射、映射冲突或缺少行业身份的股票整笔计入 `unmappedWeight`；
- 未映射权重不重新分配，行业暴露比例合计等于 `mappingCoverage`，不强制等于1；
- 权重缺失或成分重复时不计算行业暴露；
- 当前没有获批低于100%的行业映射正式阈值，因此映射覆盖不足100%时 `formalReady=false`；
- 行业版本发布日期晚于计算时点时直接拒绝；事后首次核验的历史版本可以研究性计算，但必须保留 `retrospective_unverified`，不得用于正式回放。

同指数产品组：

```text
indexGroupKey = lower(indexProvider) + ":" + upper(indexCode)
```

- 同一个 `indexGroupKey` 无论包含多少只ETF，只提供一个候选槽位；
- 同一产品同时指向两个组时整只产品排除并记录 `index_group_identity_conflict`；
- 阶段5D不使用名称、规模或行情选代表ETF，`representativeSymbol=null`；
- 代表与替代产品的选择留待阶段5E/5F完成排名输入和规则审核后处理。

指数成分重合度：

```text
symbolJaccard = |A ∩ B| / |A ∪ B|
commonMinimumWeight = Σ min(weightA, weightB)，仅计算共同成分
weightedOverlap = commonMinimumWeight / min(totalWeightA, totalWeightB)
```

- 加权重合度只在两边权重完整时计算；
- 正式比较要求两个成分版本来源日期一致；
- 重合度只作为同类指数结构证据，本阶段不进入最终ETF排名。

计算版本固定为：

```text
radar-etf-industry-exposure-v1
```

### 19.3 真实官方源历史POC

```text
historicalAsOf = 2026-06-30T15:30:00+08:00
fetchedAt       = 2026-07-25T00:30:42.262608+08:00
```

输入证据：

- 中证000300月末权重：来源日2026-06-30，成分300/300，权重300/300，合计100.008%；
- 中上协行业版本：`2025H2`，发布日2026-04-03，PDF SHA-256为 `b1d0140572b20de11cd62ca478edb4da6e229928b216116df6345b3c2e461f58`；
- 000300的300只成分全部获得唯一大类映射，映射300/300，原始权重覆盖100%；
- 行业版本是本系统在历史时点之后首次核验，状态为 `retrospective_unverified`；成分权重文件也缺精确公布/生效区间，因此结果不正式就绪。

000300前五大行业暴露：

| 大类代码 | 大类名称 | 原始权重 |
|---|---|---:|
| 39 | 计算机、通信和其他电子设备制造业 | 30.739% |
| 38 | 电气机械和器材制造业 | 9.772% |
| 66 | 货币金融服务 | 9.415% |
| 35 | 专用设备制造业 | 5.476% |
| 67 | 资本市场服务 | 5.126% |

行业暴露结果：

```text
mappedWeight    = 100.008
unmappedWeight  = 0
mappingCoverage = 1
formalReady     = false
reasons         = constituent_version_not_formal
                  industry_mapping_retrospective_unverified
                  industry_mapping_not_formal
```

000300与中证800（000906）同日历史版本重合：

```text
共同成分数             = 300
并集成分数             = 800
symbolJaccard          = 0.375
commonMinimumWeight    = 71.44
weightedOverlap        = 0.7143785686
formalReady            = false
reason                 = overlap_constituent_version_not_formal
```

以上结果只证明计算合同能够消费真实历史证据，不代表2026-06-30已经存在可正式回放的阶段5结果。

### 19.4 验证

- 新增8项阶段5D专项Fixture测试；
- 覆盖多行业原始权重、未知权重不重分配、行业身份冲突、未来行业版本拒绝、事后核验不转正式、同指数多ETF单槽位、同产品跨组冲突、代码与加权重合度、缺失权重和版本日期不一致；
- 连同阶段5B、5C、现有ETF来源和雷达合同共51项测试通过；
- Python语法编译和 `git diff --check` 通过；
- 真实POC所有官方文件只在内存解析，没有读取或写入生产SQLite；
- 未新增依赖，未修改环境变量、服务、调度、API或页面。

### 19.5 下一步

进入阶段5E“行情、日频事实和排名输入POC”：

1. 复用现有300秒ETF行情合同，不复制行情任务；
2. 分开验证规模、份额、净值、IOPV、PCF、价差、历史回报、跟踪差异和跟踪误差；
3. 未通过真实来源POC的字段继续 `source_unverified`，不记0、不参与排名；
4. 在临时SQLite复制环境完成容量估算前不迁移生产SQLite；
5. 阶段5E仍不启用任务、不新增ETF API，也不改变页面 `not_enabled`。

## 20. 阶段5E完成证据：行情、日频事实和排名输入POC

### 20.1 实施边界

新增：

- `backend/radar/sources/etf_daily_facts.py`
- `backend/radar/etf_ranking_inputs.py`
- `backend/tests/test_radar_etf_ranking_inputs.py`
- 份额观测、份额变化、ETF日频事实和排名输入审计合同

以上代码只在内存中消费来源结果，未接入生产SQLite、仓储、调度、API或页面。现有 `QuoteSnapshot` 仍是唯一盘中行情输入，未复制300秒行情任务。

### 20.2 真实来源POC

真实只读探测时间：2026-07-25凌晨；请求代码为 `159915`、`159949`、`510300`、`510310`。

| 字段/来源 | 真实结果 | 正式状态 |
|---|---|---|
| 上交所ETF份额日频 `fund_etf_scale_sse` | 2/2，统计日 `2026-07-24`，份额覆盖率100% | 可作为带报告日的份额事实 |
| 深交所ETF份额日频 `fund_scale_daily_szse` | 2/2，统计日 `2026-07-24`，份额覆盖率100% | 可作为带报告日的份额事实 |
| 当前份额示例 | 159915=`16653454936`，159949=`13624785113`；510300=`24380587700`，510310=`11281244000` | 保留原始份额单位，不改写为资金流 |
| 腾讯盘中行情 | 2/2有价格、涨跌幅、源时间和原始成交额字段 | 价格/涨跌幅可审计；成交额单位仍 `source_unverified` |
| 深交所当前产品列表净值 | 可返回净值，但没有与本批次绑定的报告日 | `source_unverified`，不得作为正式净值事实 |
| 基金规模、IOPV、PCF、精确买卖价差 | 当前没有完成来源时间、口径和覆盖门禁的正式POC | `source_unverified`，不参与排名 |
| 历史净值回报、指数回报、跟踪差异、跟踪误差、相关性 | 尚未形成同一交易日、同一指数口径和完整窗口的官方序列 | `source_unverified`，不参与排名 |

本轮未保存官方响应正文、逐产品结果或上游文件。全量交易所文件只筛选请求代码，未把未请求的其他ETF报成异常。

### 20.3 日频事实和缺失语义

份额变化固定为：

```text
shareChangeN = currentShares / priorShares - 1
```

只有当前、前一期报告日和份额单位一致，且前一期份额大于0时才生成正式数值。前一期为真实0时保持缺失并记录 `prior_shares_zero`，不把除零改成0。份额变化始终称为份额变化，不输出资金净流入或资金净流出。

日频事实计算版本为 `radar-etf-share-change-v1`。已验证的 `fundShares` 可以进入事实；基金规模、净值、日均成交额、跟踪指标等没有通过来源POC的字段保留原值或空值并标记 `source_unverified`，不重新分配其他字段权重。

### 20.4 排名输入审计与规则审核稿

排名输入审计版本为 `radar-etf-ranking-input-v1`。它同时保存可展示的价格/涨跌幅和不可排名的成交额原值；任何非 `verified` 字段都会进入 `excludedFields`，不会被当作0或参与分数。

`radar-etf-rule-v1` 当前只能形成审核稿，尚不冻结数值权重和阈值：

1. 产品类型、主动/被动、指数关系、成分版本和行业覆盖先过硬门槛；
2. 基金规模和20个完整交易日平均成交额是规模/流动性核心输入，缺一不生成正式候选；
3. 跟踪差异、跟踪误差和相关性必须分别保存、分别检查窗口和样本数；
4. 精确价差、折溢价稳定性、IOPV和PCF在来源口径通过前只作风险提示或缺失，不进入排序；
5. 同指数产品组仍只保留一个代表槽位，但本POC没有任何代表ETF达到正式选择条件。

因此当前正式候选数为0，原因是产品/指数证据覆盖不足且排名核心字段未正式可用；不以熟悉ETF、价格涨幅或成交额原始值补位。

### 20.5 临时SQLite容量上限估算

压测完全使用独立临时SQLite文件，没有打开、复制或读取生产SQLite。保守场景采用阶段5B已确认的685只境内股票ETF，按 `685 × 48轮/交易日 × 60个交易日` 写入带索引的盘中特征明细：

| 指标 | 20个交易日 | 60个交易日 |
|---|---:|---:|
| 明细行数 | 657,600 | 1,972,800 |
| SQLite文件体积 | 133,148,672B | 397,508,608B |
| 事务耗时P95 | 约31.6ms | 约31.6ms |
| 行业/产品组查询P95 | 约0.132ms | 约0.132ms |
| 在线备份耗时 | — | 约0.570s |

60日临时备份与主文件同为397,508,608B，`PRAGMA integrity_check` 返回 `ok`；压测结束后临时文件和备份已清理。该结果是按全量685只ETF、每轮保存明细和字段状态的上限估算，不是生产保留策略批准。正式迁移前仍需单独确认保留天数、索引范围和是否生成长期日频汇总。

### 20.6 验证和下一步

- 新增9项阶段5E专项测试；
- 阶段5B至5E相关测试、现有ETF来源和雷达合同共60项通过；
- Python语法编译、`git diff --check`和临时库完整性检查通过；
- 未新增依赖，未读取或写入生产SQLite，未修改环境变量、服务、调度、API或页面；
- 阶段5页面继续保持 `not_enabled`，现有4000/8001服务和影子配置未改变。

下一步进入阶段5F“迁移与仓储”设计审查，但在任何生产迁移前必须先确认来源正式门槛、`radar-etf-rule-v1`数值权重/阈值和60日容量保留策略。

## 21. 阶段5F设计审查：迁移与仓储

### 21.1 审查结论

阶段5F可以设计为一次只增结构的雷达迁移版本，但当前不执行代码迁移、生产SQLite写入或历史回填。原因是阶段5E仍没有正式可用的基金规模、20日成交额、净值/IOPV、价差和跟踪序列，正式候选数仍为0；先建空结构不会产生真实候选，也不能替代来源门槛。

现有基础可复用：

- `backend/radar/migrations.py` 的显式连接、递增版本、迁移校验值、单迁移事务和失败回滚；
- `backend/radar/repository.py` 的UTC时间序列化、显式连接、时点查询、幂等冲突和事务封装；
- `radar_runs`、`radar_rule_versions`、`industry_classification_releases` 的外键与版本证据。

不得复用：

- 旧业务表或旧业务仓储作为ETF候选事实来源；
- `etf_product_registry` 的当前值语义替代产品归一化版本；
- 浏览器缓存、API请求或当前页面状态作为写入触发器；
- 未通过POC的行情、净值、IOPV、价差或跟踪数据。

### 21.2 拟议迁移版本和表边界

拟议新增 `RADAR_ETF_STORAGE_MIGRATION`，版本号沿用现有递增规则，建议为下一未使用版本。迁移只创建以下带 `radar_` 前缀的表和索引，不修改版本1至3的SQL、表、触发器或旧数据：

| 表 | 主键/幂等键 | 职责 |
|---|---|---|
| `radar_etf_product_profiles` | `profile_id`；`symbol + source_contract_id + effective_from` | 产品归一化版本、资产范围、主动/被动和生命周期 |
| `radar_etf_index_relations` | `relation_id`；`profile_id + effective_from` | ETF与标的指数的时点关系 |
| `radar_index_methodology_versions` | `methodology_version_id`；`provider + index_code + effective_from` | 指数方法和证据哈希 |
| `radar_index_constituent_sets` | `constituent_set_id`；`provider + index_code + source_date + evidence_sha256` | 指数成分批次和权重门禁 |
| `radar_index_constituents` | `constituent_set_id + stock_code` | 成分权重明细，不补等权 |
| `radar_index_industry_exposures` | `exposure_version_id + industry_code` | 行业暴露、映射覆盖率和计算版本 |
| `radar_etf_daily_facts` | `daily_fact_id`；`symbol + source_report_date + record_checksum` | 份额、净值、规模、历史窗口和字段状态 |
| `radar_etf_feature_snapshots` | `radar_run_id + symbol` | 通过准入门禁后的盘中特征快照 |
| `radar_etf_candidate_snapshots` | `radar_run_id` | 每轮候选汇总、覆盖率和原因计数 |
| `radar_etf_candidate_entries` | `radar_run_id + industry_code + index_group_key` | 候选、代表、替代和风险证据 |

所有时间统一存UTC ISO-8601文本，交易日/报告日存`YYYY-MM-DD`。JSON只保存结构化字段状态、来源合同、原因和必要的行业暴露摘要；不保存上游响应正文、完整盘口、逐笔成交或全量ETF未归一化行情。

### 21.3 外键、时点和完整性约束

- 成分明细必须外键到成分批次；行业暴露必须外键到成分批次和既有行业发布版本；
- 候选汇总和盘中特征必须外键到`radar_runs`；候选规则必须外键到`radar_rule_versions`；
- 产品、指数关系、方法和成分版本使用`effective_from/effective_to`，查询固定为：

```sql
effective_from <= :as_of
AND (effective_to IS NULL OR effective_to > :as_of)
```

- 同一业务实体的有效区间不能重叠；在SQLite中用唯一当前索引加插入/更新触发器或仓储前置检查共同保证；
- `source_report_date`、`source_time`、`fetched_at`、`computed_at`分开保存，不能用页面时间补齐来源时间；
- 成分权重缺失、日频事实字段未验证、来源冲突和历史版本不完整必须保留状态，不以`0`替代；
- `formal_usable=1`时必须没有阻断原因，且产品/指数/行业/排名输入硬门槛均已满足；
- 加权重合、行业暴露和跟踪指标的公式版本必须随事实保存，公式变化生成新记录，不覆盖旧记录。

### 21.4 独立仓储边界

建议新增 `backend/radar/etf_repository.py`，只接受调用方显式提供且已通过迁移校验的SQLite连接。第一版公开方法保持窄接口：

- `upsert_product_profiles`
- `upsert_index_relations`
- `upsert_methodology_version`
- `insert_constituent_set`（成分头表和明细同一事务）
- `insert_industry_exposure`
- `upsert_daily_fact`
- `insert_feature_snapshot_batch`
- `save_candidate_snapshot`
- `get_product_profile_as_of`
- `get_daily_fact_as_of`
- `get_latest_feature_snapshot`
- `get_candidate_snapshot`

写入规则：

1. 每个批次先校验Pydantic合同、来源时间和证据哈希，再进入事务；
2. 相同幂等键且内容哈希相同返回`unchanged`，不同内容抛出冲突，不静默覆盖；
3. 成分头表、成分明细、行业暴露和候选结果采用整批事务；任一明细失败全部回滚；
4. 阶段5关闭或未就绪时，调用方不得获得该仓储连接，不创建锁、不请求来源；
5. 读接口使用只读连接和`PRAGMA query_only=ON`，不在API或页面请求中触发写入。

### 21.5 临时SQLite演练和验收

生产迁移前必须在全新内存库和临时文件库分别完成：

- 版本1至拟议版本的连续迁移、重复执行幂等和校验值防漂移；
- 成分重复、时间区间重叠、未来版本查询、缺失权重、唯一键冲突和外键失败；
- 同一事实重试不重复写入，事实变化生成新版本；
- 成分头表/明细/行业暴露/候选整批失败时事务完全回滚；
- 20日和60日容量场景、索引体积、单轮写入P95、时点查询P95和临时备份耗时；
- 关闭重开、`PRAGMA quick_check`、`PRAGMA integrity_check`、外键检查和迁移表校验；
- 旧版本1至3表的对象集合、行计数和迁移校验值保持不变。

由于阶段5E容量上限在60日已约397MB，生产保留策略必须先选定：

- 仅保留20个交易日特征明细；
- 60日明细用于影子观察；
- 更长期只保留日频事实、候选汇总和证据摘要。

未确认保留策略前，不应申请持续生产写入。

### 21.6 生产授权、备份和回退

阶段5F设计审查不包含以下动作：

- 修改`backend/radar/migrations.py`并令运行时要求新版本；
- 对`backend/data/stock_monitor.db`执行迁移；
- 修改环境变量、launchd资产、调度任务或功能开关；
- 启用阶段5仓储、API或页面真实结果。

获得单独授权后，执行顺序固定为：

1. 关闭阶段5开关并确认没有阶段5任务、锁或写入者；
2. 在有效交易时段创建在线一致性备份、SHA-256和独立重开校验；
3. 在生产库临时复制件演练迁移和回退；
4. 受控应用只增迁移，核对迁移行、对象、旧表行数、完整性和外键；
5. 保持阶段5任务关闭，观察旧股票/行业/市场影子链路不受影响；
6. 异常时优先关闭阶段5并保留新表；只有备份恢复得到单独授权时才恢复旧库。

本轮结论：阶段5F设计可审、可进入临时SQLite实现，但生产迁移门槛尚未满足，也未获得生产写入授权。

### 21.7 阶段5F临时库实现记录（2026-07-25）

- 新增可选 `ETF_STORAGE_MIGRATION` 版本4，并导出 `STAGE5_RADAR_MIGRATIONS`；默认 `RADAR_MIGRATIONS` 仍只包含版本1至3，因此当前生产运行链路不会自动要求新表；
- 新增 `backend/radar/etf_repository.py`，只接受调用方显式提供且已完成版本4校验的SQLite连接，不查找数据库路径、不自动迁移、不在导入时打开连接；
- 仓储覆盖产品主档、指数关系、方法版本、成分头表/明细、行业暴露、ETF日频事实、盘中特征批次和候选快照/明细；写入采用UTC时间、记录哈希幂等、冲突停止和整批事务回滚；
- 新增 `backend/tests/test_radar_etf_repository.py`，在内存SQLite和临时文件SQLite中验证版本4连续迁移、默认版本3隔离、关闭重开、完整性、外键、时点查询、重复写入、内容冲突及批量失败回滚；
- 本轮验证：阶段5F仓储专项4项通过；迁移、通用仓储、市场迁移、行业仓储、ETF排名输入和阶段5F专项合计66项通过；Python语法编译和`git diff --check`通过；
- 没有读取或写入生产SQLite，没有修改环境变量、依赖、调度、服务、API或页面，也没有启用阶段5任务。版本4仍是临时演练和未来受控启用用的可选结构。

下一步仍需先确认正式来源门槛、`radar-etf-rule-v1`权重/阈值和20日/60日保留策略；在单独获得备份、复制库演练和生产迁移授权前，不得把版本4应用到生产SQLite。

## 22. 阶段5G第一步：同行情批次影子执行器

### 22.1 实现边界

- 新增默认关闭的 `RADAR_ETF_STAGE5_ENABLED` 配置读取，未设置时为 `false`；本轮没有修改实际环境变量或运行资产；
- 新增 `backend/radar/etf_shadow_runner.py`，只消费现有ETF行情任务已经取得并完成健康判定的内存批次，不创建数据库连接、不注册第二个行情任务、不再次请求腾讯行情；
- 现有ETF行情执行器仅在阶段5开关开启时把同一个批次对象交给阶段5处理器；阶段5失败只记录自身失败原因，不改变既有ETF行情任务的健康结果；
- 阶段5使用独立跨进程锁，并拒绝过期 `as_of`、批次身份不一致、重复代码和非腾讯行情批次；
- 当前来源和规则门槛尚未冻结，因此健康行情只保存可审计盘中特征，`formal_usable=false`；候选快照固定为真实空榜 `candidate_group_count=0`、`quality=unavailable`，原因明确为产品证据未就绪和规则未冻结；
- 来源过期或失败时不保存盘中特征，只保存对应失败/过期空榜摘要，不用旧数据、0或Mock补位。

### 22.2 迁移兼容修复

默认运行时仍只要求迁移版本1至3，但必须允许已知版本4共存。迁移校验现改为：

- 版本1至3继续是默认最低要求；
- 版本4存在时继续核对名称和SHA-256，不把它误报为未知未来版本；
- 真正未知的版本或任一已知版本校验漂移仍立即拒绝。

### 22.3 验证与未完成项

- 新增8项阶段5G专项测试，覆盖默认关闭不建锁不写库、健康/过期批次、真实空候选、重复批次、独立锁、同批次单次行情请求、阶段5失败隔离及临时文件版本4运行时接入；
- 阶段5G、配置、运行时、调度、迁移和仓储相关90项测试通过；扩展阶段5及既有雷达相关160项测试通过；
- Python语法编译和 `git diff --check` 通过；没有读取或写入生产SQLite，没有修改环境变量、依赖、服务、API或页面。

阶段5G尚未整体完成。低频产品主档、指数方法/成分和日频事实的独立刷新仍受正式来源门槛、规则权重/阈值和保留策略阻断；生产版本4迁移、阶段5开关启用、服务重载和自然运行证据继续需要单独授权。

## 23. 阶段5G第二步：正式准入与低频任务门禁

### 23.1 冻结合同

新增 `backend/radar/etf_stage5_policy.py`，把阶段5当前可以诚实冻结的内容写成确定性合同：

- 正式产品必须是境内股票型被动ETF，生命周期有效；
- ETF与指数关系、指数方法、当前完整成分权重和行业暴露必须分别正式就绪；
- 行业映射覆盖率正式门槛保持100%，未映射权重不重新分配；
- 正式排名必需字段固定为 `fundSize`、`averageTurnover20d`、`trackingDifference`、`trackingError` 和 `indexCorrelation`；
- `radar-etf-rule-v1` 当前冻结为禁用状态，权重和阈值保持空；只有全部正式字段和校准样本齐全后，才允许生成一个权重合计为1、完整覆盖必需字段且带阈值的新冻结版本；
- 盘中特征明细保留60个交易日；正式指标根据冻结规则选择20日或60日真实历史窗口，工程现场影子最少连续5个交易日；日频事实和候选摘要长期保留，不长期保存上游响应正文；
- 现场验收必须通过来源失败、数据过期、必需字段缺失、重复调度、身份漂移和非交易时段六类异常场景；历史回补必须保留报告日、生效日和当时有效身份，不能用当前名册倒填过去；
- 自动清理仍关闭。生产清理任务、删除范围和恢复验证必须另行授权。

### 23.2 低频任务当前阻断

低频任务必须同时满足：

1. 产品官方主档存在可追溯生效时间；
2. 仓储支持安全关闭旧产品版本并创建新版本；
3. 指数关系、方法和成分具备可扩展到正式范围的全量刷新入口；
4. 日频事实具有明确的ETF代码池；
5. 保留策略已经批准。

当前第1至3项仍不满足：产品列表没有统一生效日和主动/被动字段，仓储没有产品版本切换写接口，指数来源仍是代表样本POC。因此本轮没有注册低频任务、没有请求真实来源，也没有用抓取时间冒充生效时间。

### 23.3 验证

- 策略专项测试覆盖默认禁用规则、权重/阈值完整性、100%行业映射门槛、逐项准入原因、60日保留、20/60日历史窗口、5日现场验收、六类异常场景和低频任务阻断；
- 阶段5及既有雷达相关166项测试通过；
- Python语法编译和 `git diff --check` 通过；
- 未读取或写入生产SQLite，未修改实际环境变量、依赖、服务、API或页面。

下一步应先补产品版本切换仓储接口及临时库测试，再设计低频产品主档任务；指数全量刷新仍须在正式来源范围扩展后接入。

## 24. 阶段5G第三步：产品版本切换仓储

- 版本4产品表补全官方分类代码/名称、投资类型、目标指数、分类映射版本、分类原因和原始来源字段，避免归一化合同在落库时丢失；
- 产品内容哈希不包含抓取时间。官方归一化内容和证据哈希未变化时，重复健康检查保持幂等，不因每天 `fetched_at` 变化制造新版本；
- 新增 `transition_product_profile`：内容变化时在同一事务内关闭当前版本并创建新版本；新版本生效时间必须晚于当前版本，失败时旧版本关闭和新版本写入一起回滚；
- 新增 `get_product_profile_as_of`，按 `effective_from <= as_of < effective_to` 返回当时有效产品和完整版本证据；
- 产品、指数关系和指数方法补充更新区间重叠触发器，直接SQL更新也不能绕过版本区间约束；
- 阶段5仓储专项增至5项，阶段5及既有雷达相关167项测试通过；语法编译和 `git diff --check` 通过；
- 本轮只修改未应用的可选版本4代码并使用内存/临时文件SQLite验证，没有读取或写入生产SQLite。

产品版本切换能力已经就绪，但产品官方源仍缺统一生效时间，不能用抓取时间代替。因此产品主档低频任务继续保持阻断；下一步应先冻结“首次观察”和“官方生效时间缺失”的版本语义，再实现低频产品主档执行器。

## 25. 阶段5G第四步：首次观察语义与产品主档低频执行器

### 25.1 时间语义冻结

- `radar_etf_product_profiles.version_time_kind` 只有 `official_effective` 和 `first_observed` 两种值；
- 有可靠官方版本生效日时，`official_effective_from` 与 `effective_from` 使用官方生效日，`first_observed_at` 另存本系统首次看到该版本的时间；
- 当前官方上市基金列表没有统一版本生效日，因此产品主档只能写 `first_observed`，此时 `effective_from = first_observed_at`、`official_effective_from = null`；
- `fetched_at` 只表示本系统抓取完成时间，`source_time` 仍表示来源时间；来源没有源时间时保持 `null`；
- `get_product_profile_as_of` 返回版本时间基准、首次观察、官方生效时间和 `historicalReplayReady`。首次观察版本只支持从首次观察时点向前观察，不支持假装存在的历史回放。

### 25.2 低频执行器与审计

- 新增 `backend/radar/etf_product_master_runner.py`，只接受显式仓储和产品主档来源函数，不创建数据库连接、不启动任务、不修改环境变量；
- 执行器严格校验批次身份、来源、`sourceTime=null`、抓取时间、代码唯一性、交易所来源和覆盖率；来源失败、空榜、重复或不完整批次只记 `failed/degraded` 审计，不推进任何产品版本；
- 版本4新增 `radar_etf_product_master_runs`，保存每次低频尝试的 `asOf`、来源时间、抓取时间、状态、覆盖率、问题和插入/幂等计数；产品版本和成功/降级/失败审计在同一事务中写入；
- 内容哈希继续排除 `fetchedAt`，同一内容次日只增加执行审计，不生成新产品版本；内容变化才关闭当前版本并写入下一版本；
- 运行时仅在 `RADAR_ENABLED=true`、`RADAR_SHADOW_MODE=true` 且 `RADAR_ETF_STAGE5_ENABLED=true` 时注册产品主档任务。任务间隔86400秒，首轮错峰150秒；每日已经有一次尝试时只读跳过，不再次请求来源；
- 阶段5产品任务使用独立锁和独立健康任务名，不复用股票、ETF行情、行业或市场任务锁。

### 25.3 验证与边界

- 新增产品主档执行器Fixture测试，覆盖默认关闭、完整批次、首次观察不可回放、次日幂等、内容变更、空/失败/不完整批次、身份冲突和原子回滚；
- 新增运行时测试，确认默认配置仍只注册5个原有任务，阶段5开启时才注册第6个每日任务，且当天失败也不会重复尝试；
- 阶段5及既有雷达相关271项测试通过；
- 未读取或写入生产SQLite，没有启用阶段5任务，没有修改真实环境变量、依赖、系统启动项、API或页面，4000/8001服务未停止或重启；
- 版本4仍只在内存和临时文件SQLite验证，生产迁移、阶段5开关启用、自然运行观察和ETF页面接入继续需要单独授权。

本步完成后，产品主档低频任务具备可审的合同、原子写入和默认关闭调度，但尚未在生产运行。下一步应继续处理指数/方法/成分的全量来源适配与独立刷新门槛，不得把当前首次观察版本直接用于历史回放或正式候选。

## 26. 阶段5H：只读ETF API与行业ETF页面（2026-07-26）

### 26.1 实施范围

- 新增 `GET /api/radar/etfs`，并扩展 `GET /api/radar/overview` 的ETF模块；两条接口均使用只读连接和 `no-store`，不提供写入路由；
- 阶段5关闭时返回 `not_enabled`；版本4存储未就绪时返回 `not_ready`；产品主档来源失败、快照过期、真实空榜和候选规则未冻结分别保留独立状态；
- ETF模块契约同时返回产品主档、候选组、代表标的、替代标的、官方/首次观察时间、源时间、抓取时间、规则版本、覆盖率和原因码；
- 行业ETF页新增官方产品主档表、候选组摘要、替代标的和源/抓取/页面渲染时间；不生成Mock、不把正式候选或交易建议写入页面；
- 版本4读取方法只在阶段5开关开启时创建，生产默认环境没有 `RADAR_ETF_STAGE5_ENABLED` 时不会接触版本4表。

### 26.2 验证

- 新增API/读服务状态测试，覆盖默认关闭、版本4存储缺失、真实空榜、过期、来源失败和规则未冻结不冒充空榜；雷达相关测试共276项通过；
- 前端 `npm run lint`、`npx tsc --noEmit` 和 `npm run build` 通过；
- `git diff --check` 通过；
- 现场用户可见浏览器的两个标签仍保持 `/radar` 与 `/docs`。当前4000服务为未重载的既有构建，按本阶段限制未重启服务，因此没有把旧服务页面当作本次源码UI验收证据。

## 27. 阶段5I：生产启用前审查结论（2026-07-26）

| 门槛 | 结论 | 证据/剩余动作 |
|---|---|---|
| 阶段5合同、版本4结构、只读API和页面状态矩阵 | 通过 | 临时/内存SQLite、276项雷达测试、前端构建 |
| 默认关闭、独立锁、任务防重入、失败隔离 | 通过 | 配置和运行时专项测试；阶段5开关已写入下一次启动配置，当前进程未重载 |
| 产品版本时间语义 | 通过（仅首次观察可用） | 未确认官方生效日的产品不得用于历史回放 |
| 正式候选规则、权重和阈值 | 未通过 | `radar-etf-rule-v1`仍禁用，不能生成正式候选 |
| 生产SQLite版本4迁移与在线备份 | 通过 | 在线备份、SHA-256、临时副本迁移演练和生产重开完整性/外键核对通过 |
| 生产环境开关与自然运行 | 部分完成 | `RADAR_ETF_STAGE5_ENABLED=true`已写入LaunchAgent；未重载8001，因此自然运行尚未开始 |
| 连续20个A股交易日影子观察 | 未开始 | 迁移、开关和自然运行通过后才计数 |

### 27.1 受控生产启用执行记录（2026-07-26 07:38-07:40，Asia/Shanghai）

- 生产库 `backend/data/stock_monitor.db` 在线备份已生成：`backend/data/backups/stock_monitor.db.stage5-pre-v4-20260726T073833+0800.bak`，SHA-256 为 `c9b353d51fb015733503d157802fa9cbec15b3e44ae950ed28510bd467922a06`；
- 以该备份为源的临时副本成功应用版本4，重开后版本为1至4，既有雷达/市场记录计数不变，`quick_check`、`integrity_check`和两次外键检查均通过；
- 生产SQLite只应用版本4 `etf_versioned_storage`，迁移前版本为1至3，迁移后为1至4；未回填ETF业务数据；
- LaunchAgent配置备份为 `backend/data/backups/com.linjian.stock-monitor.fastapi.plist.stage5-pre-enable-20260726T074020+0800.bak`；已持久化 `RADAR_ETF_STAGE5_ENABLED=true`；
- 8001已按用户授权完成一次受控重载，当前PID为90418，LaunchAgent运行环境已确认包含 `RADAR_ETF_STAGE5_ENABLED=true`；4000保持原PID；
- 重载后只读接口 `/docs`、`/api/radar/overview`、`/api/radar/etfs` 均返回200；ETF模块按真实条件返回 `not_ready`，没有Mock或正式候选；
- 重载发生在2026-07-26休市日，20个交易日计数尚未开始，将从下一个有效A股交易日（预计2026-07-27）开始；
- 正式候选规则仍禁用，当前只能积累影子审计，不能生成正式候选或交易建议。

本轮阶段5I结论更新为“生产结构、运行配置和后端重载均已完成，持续影子运行将在下一个有效交易日开始”。回退优先关闭阶段5开关并按同一LaunchAgent完成受控重载；若需要恢复数据库结构，使用上述一致性备份恢复到临时库验证后再单独授权生产恢复，不执行向下迁移。
