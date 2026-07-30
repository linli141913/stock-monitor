# 阶段 6L-A 龙头当期研究特征设计

## 1. 目标

阶段 6L-A 只计算当前真实数据能够支持的当期横截面研究特征：

- 行业强度，最大 25 分；
- 市场领先性，最大 25 分；
- 辅助指标，最大 5 分。

本阶段不生成正式龙头总分，不改变预备、候选或已确认状态，不把缺失字段
补成 0，也不使用新闻、AI 或主题标签证明主营与催化关系。

## 2. 实施边界

- 新增纯计算模块，不访问网络或数据库。
- 复用阶段 6K 已冻结的全市场行情批次、市场聚合、行业聚合、行业映射和
  证券主档。
- 不第二次抓取全市场行情。
- 不新增数据库迁移，不修改前端或公开 API。
- 不启用阶段 6 功能开关，不停止或重启 4000/8001。
- 不读取或写入生产数据库。
- 所有正式状态门槛继续关闭，`formalUsable` 继续为 `false`。

## 3. 方案选择

采用“研究分与正式分隔离”方案。

三个可计算维度写入版本化研究特征合同。现有
`LeaderDimensionEvidence` 不接收这些研究分，现有评分器不会把部分分数
误当作正式总分。阶段 6L-B 补齐历史连续性、流动性与可交易性、主营催化
证据后，再单独审核研究特征能否进入正式评分输入。

不采用以下方案：

- 只保存原始字段而不冻结公式：无法验证横截面计算和缺失语义。
- 用单日行情代理全部六维：会把价格强势误写成龙头状态。

## 4. 合同

新增公式版本：

```text
radar-leader-research-feature-v1
```

单个维度至少包含：

- `fieldName`
- `maximumScore`
- `researchScore`
- `status`
- `components`
- `sourceContractIds`
- `reasons`

候选汇总至少包含：

- `formulaVersion`
- `researchPartialScore`
- `participatingWeight`
- `requiredFormalWeight`
- `scoreReady`
- `dimensions`

本阶段固定：

```text
participatingWeight = 55
requiredFormalWeight = 95
scoreReady = false
```

95 是五个核心维度的总权重，辅助指标 5 分不是正式评分完备性的前置条件。
`researchPartialScore` 只用于公式审计，不进入 `score`、状态机或公开榜单。

## 5. 公共百分位公式

对至少两个有效样本组成的集合 `V` 和目标值 `x`：

```text
lower = count(v < x)
equal = count(v == x)
percentile = (lower + (equal - 1) / 2) / (len(V) - 1)
```

结果限制在 `[0, 1]`。并列值获得相同的平均百分位；所有值相同时结果为
0.5。只接受有限数值，`NaN` 和正负无穷均按缺失处理。内部计算不提前
四舍五入，序列化时保留至多 6 位小数。

样本门槛：

- 行业横截面至少 20 个有效行业；
- 单行业至少 2 只有效成分股；
- 全市场横截面至少 100 只有效沪深 A 股。

样本不足时对应维度保持缺失，不缩小分母后继续计分。

## 6. 行业强度

最大 25 分，由全部有效行业的当期聚合计算：

```text
行业等权收益分 = 10 * positive_percentile(equalReturn)
上涨广度分 = 8 * clamp(upRatio, 0, 1)
剔除首股收益分 = 7 * positive_percentile(exTopReturn)
行业强度研究分 = 三项之和
```

`positive_percentile(value)` 在 `value <= 0` 时为 0；大于 0 时使用公共
百分位公式。这样弱市中相对靠前但仍为负收益的行业不会仅凭排名拿到收益
项分数。

行业必须同时满足：

- `isComplete=true`
- `shadowUsable=true`
- `equalReturn`、`upRatio`、`exTopReturn` 均为真实数值
- 行业聚合时间不晚于候选 `asOf`

行业成交额、历史放量、持续性、回流和正式行业状态不在本阶段计算。

## 7. 市场领先性

最大 25 分：

```text
行业内涨幅排名分 = 10 * percentile(changePercent)
正向行业贡献排名分 = 8 * positive_percentile(contribution)
板块指数超额排名分 = 7 * positive_percentile(excessReturn)
市场领先性研究分 = 三项之和
```

其中：

```text
contribution = marketCapSource * max(changePercent, 0)
excessReturn = changePercent - boardIndexChangePercent
```

`marketCapSource` 单位尚未正式验证，因此只允许在同一来源、同一行业内做
相对排序，不展示绝对市值或绝对贡献金额。

板块指数映射：

- 上交所科创板：科创 50；
- 上交所其他板块：上证指数；
- 深交所创业板：创业板指；
- 深交所其他板块：深证成指。

板块名称通过是否包含“科创”或“创业”判断；交易所与板块无法识别、对应
指数缺失或指数数据不可用时，整个市场领先性维度保持缺失，不降级成其他
指数。

行业内排名使用该行业全部有效成分，不使用最终展示前 5 名作为排名分母。
板块指数超额排名使用全部有效沪深 A 股，每只股票先按自身板块匹配指数。

## 8. 辅助指标

最大 5 分，只使用量比：

```text
volumeRatio <= 1 时，辅助研究分 = 0
volumeRatio > 1 时，辅助研究分 = 5 * percentile(volumeRatio)
```

百分位分母为全部量比非负且来源有效的沪深 A 股。真实量比 0 保留为 0；
缺失量比保持缺失。量比不能改变任何硬门槛，也不能单独产生状态。

龙虎榜、资金流、新闻热度和媒体观点不在本阶段接入。

## 9. 继续缺失的正式维度

- `relative_strength_continuity /20`：缺少同口径历史连续序列。
- `liquidity_tradability /15`：成交额单位未验证，且缺少停牌、涨跌停、
  一字板和价差合同。
- `business_exposure /10`：缺少公司披露或人工审核的版本化主营催化证据。

上述任何一项不得用单日涨幅、换手率、量比、行业分类或 AI 推断替代。

## 10. 运行数据流

```text
阶段6K冻结输入
→ 过滤沪深A股、有效时间和有限数值
→ 每批次一次性构建指数超额和量比分母
→ 构建行业、全市场横截面
→ 计算三个研究维度
→ 写入候选 evidence.researchFeatures
→ 现有输入门禁继续返回缺失
→ 状态机继续 blocked/out
→ 只读 API 继续 not_ready
```

研究公式计算失败时只影响阶段 6 后处理；既有市场和行业任务结果继续可用。
错误原因只记录稳定错误码，不包含证券全集、原始行情值或堆栈。

## 11. 测试

新增纯函数测试和 6K 组装集成测试，至少覆盖：

- 百分位边界、并列值和全相同值；
- 真实 0 与缺失；
- 行业或市场样本不足；
- 负行业收益和负板块超额不获得正向排名分；
- 行业排名使用全部成分而不是前 5 名；
- 上证、深证、创业板和科创板指数映射；
- 指数缺失、未来、过期和来源失败；
- 市值原值只做相对贡献排序；
- 量比小于等于 1 为真实 0 分；
- 北交所证券不进入候选或百分位分母；
- 单日涨幅第一仍保持 `scoreReady=false`、`blocked/out` 和 API
  `not_ready`；
- 不发生网络请求、生产数据库访问或第二次全市场抓取。

阶段 6L-A 完成验证：

- 新增相关测试通过；
- 全部 `test_radar_leader*.py` 通过；
- 后端完整测试通过；
- `py_compile` 与 `git diff --check` 通过。

## 12. 后续阶段

阶段 6L-B 再分别设计和接入：

- 同复权口径的历史相对强度、持续性、抗跌和回流；
- 经过单位验证的成交与流动性，以及可交易性字段；
- 公司披露或人工审核的版本化主营催化证据。

阶段 6L-B 完成前，阶段 6 不具备正式评分或生产启用条件。
