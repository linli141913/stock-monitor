# 股票监测助手 Web 端 PRD

## 1. 项目名称

**股票监测助手**

---

## 2. 产品定位

这是一个 Web 端股票监测工具，核心用于：

- 查看单只股票的完整信息
- 监测该股票所属行业动态
- 监测半导体及相关产业链异动
- 设置邮件提醒
- 帮助用户判断一只股票是否值得继续观察

本产品不是自动交易系统，不做自动下单，不直接提供买入卖出建议。

本产品第一版定位为：

> **准实时股票监测 + 行业信息聚合 + 邮件提醒工具**

产品核心闭环：

> 输入股票 → 查看完整股票信息 → 同步监测行业动态 → 发现相关异动 → 邮件提醒

---

## 3. 核心目标

用户输入股票名称或代码，例如：

```text
深科技 / 000021
```

系统需要展示：

1. 股票完整行情信息
2. K线图与成交量
3. 公司概览、财务摘要、公告、相关新闻
4. 所属行业监测
5. 相关股票
6. 半导体相关异动股
7. 邮件提醒设置
8. 数据来源说明
9. 数据最后更新时间与延迟说明

---

## 4. 主要监测方向

产品主线关注：

> **A 股半导体及相关产业链**

包括但不限于：

- 半导体设备
- 半导体材料
- 芯片设计
- 晶圆制造
- 封装测试
- 存储芯片
- AI 芯片
- 光模块 / 算力硬件
- 电子制造 EMS
- 消费电子产业链
- 国产替代方向

用户输入某只股票后，系统不仅展示这只股票本身，还要分析：

- 它是否属于半导体相关方向
- 它属于哪个产业链环节
- 它和哪些概念、板块、相关股票有关
- 所属行业近期是否有政策、资金、新闻、公告刺激
- 半导体相关股票里是否有明显异动股

---

## 5. 产品范围

### 5.1 第一版必须做

- Web 端页面
- 股票搜索
- 股票详情展示
- K线图展示
- 公司概览
- 财务摘要
- 公告列表
- 相关新闻
- 行业监测
- 相关股票
- 半导体异动股
- 邮件提醒
- 监测列表
- 数据来源说明
- 数据更新时间展示
- 数据延迟说明

### 5.2 第一版不做

- App
- 自动交易
- 券商下单接口
- 微信提醒
- 短信提醒
- 高频交易
- 秒级抢单
- 复杂量化回测
- 自动买卖建议
- 跟单功能
- 社区功能

---

## 6. 数据实时性要求

这是本产品非常重要的要求。

第一版不得宣传为“绝对实时行情系统”。

第一版采用：

> **准实时数据方案**

也就是说：

- 行情数据会尽量接近实时
- 但不承诺毫秒级实时
- 不适合高频交易
- 不适合抢涨停、秒级下单
- 适合做股票监测、异动提醒、公告提醒、行业观察

---

## 7. 数据刷新频率建议

第一版建议刷新频率如下：

| 监测内容 | 建议刷新频率 | 说明 |
|---|---:|---|
| 股票行情 / 最新价 / 涨跌幅 | 15～60 秒 | 用于基础监测和提醒 |
| 成交量 / 放量监测 | 30～60 秒 | 用于异常放量判断 |
| 板块 / 行业异动 | 1～5 分钟 | 用于行业趋势观察 |
| 半导体异动股 | 1～5 分钟 | 用于相关股票异动筛选 |
| 公司公告 | 5～10 分钟 | 用于公告更新提醒 |
| 新闻 / 政策资讯 | 5～15 分钟 | 用于信息聚合和摘要 |
| 财务数据 | 每日或按财报更新 | 不需要高频刷新 |

---

## 8. 页面必须展示的数据实时性信息

页面上必须明确显示：

1. 数据来源
2. 最后更新时间
3. 当前刷新频率
4. 数据类型：实时 / 准实时 / 延迟
5. 数据延迟说明

建议在股票详情卡片中显示：

```text
数据来源：AKShare / 东方财富 / 新浪 / 其他公开数据源
最后更新：2024-05-23 15:00:00
刷新频率：约 15～60 秒
数据类型：准实时行情
```

在页面底部或信息来源模块中显示：

```text
本系统第一版采用准实时公开数据源，不承诺毫秒级实时行情。如需高频交易或低延迟行情，需要接入券商、交易所授权数据或付费 Level-2 行情接口。
```

---

## 9. 数据延迟处理原则

开发时必须遵守：

1. 不得把公开免费数据包装成绝对实时数据
2. 所有行情数据必须显示最后更新时间
3. 邮件提醒中必须显示触发时间
4. 数据源异常时必须提示“数据暂不可用”
5. 数据超过设定时间未更新时，需要标记为“数据可能延迟”
6. 不同数据源返回结果不一致时，优先显示主数据源，并保留数据源说明
7. 后续可增加付费 Level-2 或券商行情接口作为增强版本

---

## 10. 页面结构

产品建议包含 4 个页面：

1. 首页 / 股票监测页
2. 监测列表页
3. 提醒设置页
4. 行业洞察页

第一版重点完成首页 / 股票监测页。

---

# 11. 首页 / 股票监测页

## 11.1 页面目标

首页是核心页面。用户在这里完成：

- 输入股票
- 查看股票详情
- 查看行业动态
- 查看相关股票
- 查看半导体异动
- 设置邮件提醒

---

## 11.2 页面整体布局

采用：

> 顶部导航 + 搜索区 + 左右双栏内容区

左侧为股票主体信息，占页面约 65%。  
右侧为行业监测、相关股票、半导体异动、提醒设置、信息来源，占页面约 35%。

---

## 11.3 顶部导航

左侧显示产品名称：

```text
股票监测助手
```

右侧导航：

```text
首页
监测列表
提醒设置
行业洞察
```

当前页面需要高亮。

---

## 11.4 搜索区

搜索区位于顶部导航下方。

包含：

### 搜索输入框

placeholder：

```text
输入股票名称 / 代码，例如：深科技 / 000021
```

### 按钮

```text
开始监测
```

### 功能标签

```text
Web端监测 + 邮件提醒
```

---

# 12. 左侧主内容区

## 12.1 股票信息总览卡片

展示股票核心行情。

### 基础字段

| 字段 | 示例 |
|---|---|
| 股票名称 | 深科技 |
| 股票代码 | 000021 |
| 最新价 | 18.76 |
| 涨跌额 | +0.58 |
| 涨跌幅 | +3.19% |
| 更新时间 | 2024-05-23 15:00:00 |
| 数据类型 | 准实时 |
| 刷新频率 | 15～60 秒 |

### 行情指标字段

| 字段 | 示例 |
|---|---|
| 今开 | 18.21 |
| 最高 | 18.98 |
| 最低 | 18.05 |
| 昨收 | 18.18 |
| 成交量 | 1256.32万手 |
| 成交额 | 23.55亿元 |
| 换手率 | 3.45% |
| 市盈率（动） | 28.56 |
| 总市值 | 286.61亿元 |

### UI 要求

- 最新价要最醒目
- 涨跌使用红绿颜色区分
- 指标网格排列
- 显示数据更新时间
- 显示数据来源或数据类型
- 整体简洁，不要像复杂交易终端一样拥挤

---

## 12.2 K线图模块

展示股票走势。

### 时间周期切换

```text
分时
日K
周K
月K
```

默认选中：

```text
日K
```

### 图表内容

- K线图
- 成交量柱状图
- MA5
- MA10
- MA20

### K线字段

| 字段 | 说明 |
|---|---|
| date | 日期 |
| open | 开盘价 |
| close | 收盘价 |
| high | 最高价 |
| low | 最低价 |
| volume | 成交量 |
| ma5 | 5日均线 |
| ma10 | 10日均线 |
| ma20 | 20日均线 |

---

## 12.3 股票详情 Tab 模块

K线图下方放置详情标签。

标签包括：

```text
公司概览
财务摘要
公告
相关新闻
```

默认显示：

```text
公司概览
```

---

### 12.3.1 公司概览

展示字段：

| 字段 | 说明 |
|---|---|
| 主营业务 | 公司主要业务 |
| 核心产品 | 公司主要产品 |
| 所属行业 | 公司所属行业 |
| 所属概念 | 半导体、国产芯片、5G等 |
| 近期公告摘要 | 最近重要公告简述 |
| 与半导体关系 | 说明该公司是否属于半导体相关产业链 |

示例：

```text
主营业务：专注于电子制造服务 EMS，为全球客户提供电子产品研发设计、生产制造及技术支持服务。
核心产品：存储半导体封测、消费电子产品制造、汽车电子、工业控制等。
所属概念：半导体概念、5G、物联网、消费电子、国产芯片。
```

---

### 12.3.2 财务摘要

展示字段：

| 字段 | 说明 |
|---|---|
| 营收 | 最近一期营收 |
| 营收同比 | 营收同比变化 |
| 净利润 | 最近一期净利润 |
| 净利润同比 | 净利润同比变化 |
| 毛利率 | 毛利率 |
| 净利率 | 净利率 |
| ROE | 净资产收益率 |
| EPS | 每股收益 |
| 资产负债率 | 财务风险参考 |
| 财报周期 | 例如 2024Q1 |

---

### 12.3.3 公告

展示最近公告列表。

字段：

| 字段 | 说明 |
|---|---|
| 公告标题 | 公告名称 |
| 发布时间 | 日期 |
| 来源 | 交易所 / 公司公告 |
| 摘要 | 简短摘要 |
| 重要程度 | 高 / 中 / 低 |
| 原文链接 | 可点击跳转 |

---

### 12.3.4 相关新闻

展示和该股票相关的新闻。

字段：

| 字段 | 说明 |
|---|---|
| 新闻标题 | 标题 |
| 来源 | 新闻来源 |
| 发布时间 | 时间 |
| 摘要 | 简短摘要 |
| 情绪判断 | 利好 / 利空 / 中性 |
| 原文链接 | 可点击跳转 |

注意：新闻情绪只能作为辅助参考，不可直接作为买卖依据。

---

# 13. 右侧侧栏内容区

## 13.1 行业监测模块

标题：

```text
行业监测
```

展示该股票所属行业的状态。

### 字段

| 字段 | 示例 |
|---|---|
| 所属行业 | 电子制造 / 半导体相关 |
| 行业热度 | 78/100 |
| 板块涨跌 | +2.35% |
| 资金流向 | 净流入 12.48亿元 |
| 相关政策 | 国家大基金相关政策 |
| 上游动态 | 晶圆代工价格稳定 |
| 下游动态 | 下游需求逐步回暖 |
| 更新时间 | 2024-05-23 15:01:00 |
| 刷新频率 | 1～5 分钟 |

### UI 要求

- 行业热度用进度条展示
- 板块涨跌可用小趋势线展示
- 资金流向突出显示
- 政策和上下游动态只展示简短摘要
- 必须显示更新时间

---

## 13.2 相关股票模块

标题：

```text
相关股票
```

展示与当前股票相关的股票。

相关逻辑包括：

- 同行业
- 同概念
- 同产业链
- 同板块
- 半导体相关方向

### 字段

| 字段 | 说明 |
|---|---|
| 股票名称 | 相关股票名称 |
| 股票代码 | 股票代码 |
| 最新价 | 当前价格 |
| 涨跌幅 | 今日涨跌幅 |
| 相关原因 | 同行业 / 同概念 / 同产业链 |
| 异动标签 | 放量 / 涨停 / 资金流入 / 高换手 |
| 更新时间 | 数据更新时间 |

### 交互

点击相关股票后，切换到该股票详情页。

---

## 13.3 半导体异动模块

标题建议：

```text
半导体异动
```

或：

```text
相关异动观察
```

该模块用于筛选近期涨幅较大、成交量放大、资金流入明显的半导体相关股票。

注意：这里不是买入推荐，只是异动监测。

### 筛选规则建议

满足任一条件即可进入异动列表：

- 今日涨幅 ≥ 5%
- 5日涨幅 ≥ 15%
- 20日涨幅 ≥ 30%
- 成交量 ≥ 近5日均量的 2 倍
- 主力资金明显净流入
- 出现重要公告
- 出现政策刺激
- 出现行业新闻刺激

### 字段

| 字段 | 说明 |
|---|---|
| 股票名称 | 股票名称 |
| 股票代码 | 股票代码 |
| 今日涨跌幅 | 今日表现 |
| 5日涨跌幅 | 短期趋势 |
| 20日涨跌幅 | 阶段趋势 |
| 量比 | 成交量异动 |
| 换手率 | 交易活跃度 |
| 资金流 | 主力资金流向 |
| 异动原因 | 为什么进入列表 |
| 风险提示 | 是否短期涨幅过大 |
| 更新时间 | 数据更新时间 |

### 文案要求

可以使用：

```text
值得关注
异动观察
相关异动
```

不要使用：

```text
推荐买入
强烈推荐
必涨
买入信号
```

---

## 13.4 提醒设置模块

标题：

```text
提醒设置
```

第一版只做邮件提醒。

### 提醒选项

支持勾选：

| 提醒项 | 默认阈值 |
|---|---|
| 股价涨跌幅提醒 | ≥ 5% |
| 异常放量提醒 | ≥ 2倍 |
| 公告更新提醒 | 有新公告 |
| 行业异动提醒 | 行业热度 / 板块涨跌明显 |
| 半导体异动提醒 | 相关股票出现明显异动 |

### 邮箱输入

字段：

```text
接收邮箱
```

placeholder：

```text
you@example.com
```

### 按钮

```text
保存提醒
```

### 交互要求

- 邮箱格式必须校验
- 至少选择一个提醒项
- 保存成功后显示提示
- 保存失败要显示错误原因
- 用户可修改或关闭提醒
- 邮件内容必须显示触发时间和数据更新时间

---

## 13.5 信息来源模块

标题：

```text
信息来源
```

展示当前页面数据来源分类：

```text
行情数据
公司公告
行业新闻
财报数据
政策资讯
```

同时展示：

```text
数据类型：准实时
最后更新：xxxx-xx-xx xx:xx:xx
延迟说明：公开数据源可能存在几十秒到数分钟延迟
```

目的：

让用户知道数据来自真实来源，不是 AI 瞎编。

---

# 14. 监测列表页

## 14.1 页面目标

统一管理用户已添加监测的股票。

## 14.2 字段

| 字段 | 说明 |
|---|---|
| 股票名称 | 名称 |
| 股票代码 | 代码 |
| 所属行业 | 行业 |
| 当前价格 | 最新价 |
| 今日涨跌幅 | 涨跌幅 |
| 已启用提醒项 | 邮件提醒类型 |
| 接收邮箱 | 邮箱 |
| 最后更新时间 | 更新时间 |
| 数据状态 | 正常 / 延迟 / 异常 |
| 状态 | 启用 / 暂停 |
| 操作 | 查看 / 编辑 / 删除 |

## 14.3 功能

- 查看监测列表
- 搜索监测股票
- 删除监测股票
- 编辑提醒条件
- 进入股票详情
- 暂停 / 启用提醒
- 显示每只股票最后更新时间
- 数据长时间未更新时标记异常

---

# 15. 提醒设置页

## 15.1 页面目标

统一管理所有邮件提醒规则。

## 15.2 功能

- 查看全部提醒规则
- 修改提醒阈值
- 修改接收邮箱
- 开启 / 关闭提醒
- 删除提醒规则
- 测试发送邮件
- 查看最近一次提醒发送时间
- 查看最近一次触发原因

---

# 16. 行业洞察页

## 16.1 页面目标

集中查看半导体及相关行业的整体状态。

## 16.2 内容

- 半导体行业热度
- 半导体板块涨跌
- 资金流入排行
- 今日异动股
- 5日强势股
- 20日强势股
- 重要政策摘要
- 重要行业新闻
- 上游 / 下游动态
- 数据更新时间
- 数据延迟说明

## 16.3 行业分类

建议支持：

```text
半导体设备
半导体材料
芯片设计
封装测试
存储芯片
AI芯片
光模块 / 算力硬件
电子制造 EMS
消费电子
国产替代
```

---

# 17. 开源项目复用方案

本项目不应完全从零开发。  
优先复用和参考现有成熟开源项目。

---

## 17.1 AKShare

项目地址：

```text
https://github.com/akfamily/akshare
```

用途：

```text
作为第一版优先使用的股票行情和基础数据源。
```

可用于：

- A 股历史行情
- K线数据
- 涨跌幅
- 成交量
- 成交额
- 换手率
- 部分指数、板块、资金流数据
- 部分宏观 / 行业数据

使用方式：

```text
后端通过 Python 服务封装 AKShare，不要让前端直接调用。
```

建议封装为：

```text
stock_market_service
```

对应 API：

```text
/api/stocks/{stock_code}/overview
/api/stocks/{stock_code}/kline
/api/industry/semiconductor/abnormal-stocks
```

结论：

```text
第一版可以直接使用 AKShare 作为核心数据源。
```

注意：

AKShare 属于公开数据源封装工具，实时性取决于其底层数据来源。第一版不得承诺毫秒级实时。

---

## 17.2 china-stock-mcp

项目地址：

```text
https://github.com/xinkuang/china-stock-mcp
```

用途：

```text
作为中国股票数据服务 / AI Agent 查询股票数据的参考实现。
```

它适合用于：

- 查询 A 股实时行情
- 查询历史行情
- 查询新闻数据
- 查询财务报表
- 查询资金流
- 查询技术指标
- 给 AI Agent / MCP 工具调用

它的数据源包含：

```text
东方财富
新浪财经
雪球
```

使用建议：

第一版如果不接 MCP，可以先参考它的数据接口设计和数据源结构。  
后续如果需要接 Hermes、Claude、Cursor、Antigravity 这类 Agent，可以把它作为股票数据 MCP 服务使用。

结论：

```text
非常适合保留，建议作为后续 AI Agent 股票查询能力的核心参考。
```

---

## 17.3 stock_monitor

项目地址：

```text
https://github.com/wangkayn/stock_monitor
```

用途：

```text
参考它的新闻监控、定时任务、AI总结、提醒推送逻辑。
```

它已有能力：

- 多源新闻聚合
- AI 分析新闻
- 定时检查
- 每日摘要
- 突发新闻提醒
- 缓存机制
- 通知推送逻辑

本项目不需要照搬它的 Telegram 方向。  
需要改成：

```text
Web端监测 + 邮件提醒
```

可参考模块：

```text
news_fetcher
ai_analyzer
scheduler
cache
alert_trigger
```

需要改造：

```text
Telegram 推送 → 邮件提醒
美股新闻源 → 中国 A 股 / 半导体 / 财经新闻源
股票 ticker 体系 → A 股股票代码体系
```

结论：

```text
适合参考监控和提醒架构，不建议完全原样照搬。
```

---

## 17.4 TuShare

项目地址：

```text
https://github.com/waditu/tushare
```

用途：

```text
作为 AKShare 的补充数据源。
```

可用于：

- 股票基础资料
- 财务数据
- 历史行情
- 指数数据
- 部分专业数据

注意：

TuShare Pro 部分接口可能需要 token 或权限。  
第一版不要强依赖 TuShare，避免开发被 API 权限卡住。

结论：

```text
作为补充数据源保留，不作为第一优先。
```

---

## 17.5 InStock

项目地址：

```text
https://github.com/ethqunzhong/InStock
```

用途：

```text
参考完整股票系统的功能结构。
```

可参考：

- 综合选股
- 技术指标
- 筹码分布
- K线形态识别
- 资金流
- 行业 / 概念 / 消息面字段
- Web 页面信息组织方式

不建议第一版直接使用原因：

- 系统较重
- 功能复杂
- 包含自动交易能力
- 不符合本项目第一版“简洁 Web 监测 + 邮件提醒”的定位

结论：

```text
只参考，不作为主系统直接集成。
```

---

## 17.6 第一版实际采用方案

第一版建议采用：

```text
AKShare + 自建后端 API + Web 前端 + 邮件提醒
```

架构：

```text
前端 Web 页面
↓
自建后端 API
↓
AKShare 获取行情 / K线 / 基础数据
↓
自建行业监测逻辑
↓
邮件提醒服务
```

同时预留：

```text
china-stock-mcp 接入能力
TuShare 补充数据源能力
stock_monitor 新闻监控逻辑参考
InStock 指标和选股逻辑参考
付费 Level-2 / 券商行情接口扩展能力
```

---

## 17.7 开源项目使用原则

开发时必须遵守：

1. 优先复用成熟开源项目，不要从零手搓行情数据能力
2. 开源项目只作为数据源、服务能力或架构参考
3. 不要把重型股票系统整体搬进来
4. 第一版保持简洁，避免功能过载
5. 如果复制 MIT License 项目代码，需要保留原版权和 License 声明
6. 所有数据源都要封装成可替换服务，后续方便更换或增加数据源
7. 不得把免费公开数据源宣传为绝对实时行情源

---

# 18. 数据来源要求

产品必须基于真实有效的数据源。

## 18.1 数据原则

- 数据必须真实可验证
- 优先使用稳定数据源
- 优先支持中国 A 股
- 不要依赖单一来源
- 不要让 AI 编造数据
- 关键数据必须可追溯来源
- 页面必须显示最后更新时间
- 页面必须显示数据延迟说明

## 18.2 建议数据来源

### 行情数据

- AKShare
- TuShare
- 东方财富
- 新浪财经
- 其他合法稳定行情接口

### 财务数据

- AKShare
- TuShare
- 上市公司财报公开数据

### 公告数据

- 交易所公告
- 上市公司公告

### 新闻数据

- 主流财经新闻源
- 行业新闻源
- 合法 RSS / API

### 政策资讯

- 政府公开政策信息
- 财经资讯源
- 行业研究信息源

### 低延迟增强数据源

后续如需更低延迟，可接入：

- 券商行情接口
- 交易所授权行情
- 付费 Level-2 行情
- 专业金融数据服务

---

# 19. 后端服务设计

建议将后端服务拆成模块：

```text
股票行情服务
股票详情服务
K线数据服务
公司信息服务
财务数据服务
公告服务
新闻服务
行业监测服务
相关股票服务
半导体异动服务
邮件提醒服务
监测列表服务
数据源状态服务
```

不要在前端直接处理复杂数据源逻辑。

---

# 20. API 设计建议

## 20.1 股票搜索

```text
GET /api/stocks/search?keyword=深科技
```

返回字段：

```json
{
  "list": [
    {
      "stock_name": "深科技",
      "stock_code": "000021",
      "market": "SZ",
      "industry": "电子制造",
      "concepts": ["半导体", "国产芯片", "5G"]
    }
  ]
}
```

---

## 20.2 股票详情

```text
GET /api/stocks/{stock_code}/overview
```

返回字段：

```json
{
  "stock_name": "深科技",
  "stock_code": "000021",
  "latest_price": 18.76,
  "change_amount": 0.58,
  "change_percent": 3.19,
  "open_price": 18.21,
  "high_price": 18.98,
  "low_price": 18.05,
  "previous_close": 18.18,
  "volume": "1256.32万手",
  "turnover_amount": "23.55亿元",
  "turnover_rate": 3.45,
  "pe_dynamic": 28.56,
  "market_cap": "286.61亿元",
  "update_time": "2024-05-23 15:00:00",
  "data_source": "AKShare",
  "data_type": "准实时",
  "refresh_interval": "15-60秒",
  "delay_note": "公开数据源可能存在几十秒到数分钟延迟"
}
```

---

## 20.3 K线数据

```text
GET /api/stocks/{stock_code}/kline?period=day
```

period 支持：

```text
minute
day
week
month
```

返回字段：

```json
{
  "period": "day",
  "data_source": "AKShare",
  "update_time": "2024-05-23 15:00:00",
  "list": [
    {
      "date": "2024-05-23",
      "open": 18.21,
      "close": 18.76,
      "high": 18.98,
      "low": 18.05,
      "volume": 12563200,
      "ma5": 18.28,
      "ma10": 18.06,
      "ma20": 17.34
    }
  ]
}
```

---

## 20.4 公司信息

```text
GET /api/stocks/{stock_code}/company
```

返回字段：

```json
{
  "main_business": "电子制造服务 EMS",
  "core_products": ["存储半导体封测", "消费电子产品制造", "汽车电子", "工业控制"],
  "industry_tags": ["电子制造", "半导体", "国产芯片"],
  "company_description": "公司主要为全球客户提供电子产品研发设计、生产制造及技术支持服务。",
  "business_relation": "与半导体封测、存储芯片、电子制造产业链相关。",
  "update_time": "2024-05-23 15:00:00"
}
```

---

## 20.5 财务摘要

```text
GET /api/stocks/{stock_code}/financials
```

返回字段：

```json
{
  "report_period": "2024Q1",
  "revenue": "xx亿元",
  "revenue_yoy": "xx%",
  "net_profit": "xx亿元",
  "net_profit_yoy": "xx%",
  "gross_margin": "xx%",
  "net_margin": "xx%",
  "roe": "xx%",
  "eps": "xx",
  "debt_ratio": "xx%",
  "update_time": "2024-05-23 15:00:00"
}
```

---

## 20.6 公告列表

```text
GET /api/stocks/{stock_code}/announcements
```

返回字段：

```json
{
  "update_time": "2024-05-23 15:00:00",
  "list": [
    {
      "title": "关于控股子公司增资扩股的公告",
      "publish_time": "2024-05-20",
      "source": "交易所公告",
      "summary": "公司发布控股子公司相关增资事项。",
      "url": "https://example.com",
      "importance": "中"
    }
  ]
}
```

---

## 20.7 新闻列表

```text
GET /api/stocks/{stock_code}/news
```

返回字段：

```json
{
  "update_time": "2024-05-23 15:00:00",
  "list": [
    {
      "title": "半导体产业链景气度回升",
      "source": "财经新闻源",
      "publish_time": "2024-05-23 10:30:00",
      "summary": "相关产业链近期受到资金关注。",
      "sentiment": "利好",
      "url": "https://example.com"
    }
  ]
}
```

---

## 20.8 行业监测

```text
GET /api/stocks/{stock_code}/industry-monitor
```

返回字段：

```json
{
  "industry_name": "电子制造 / 半导体相关",
  "industry_heat_score": 78,
  "sector_change_percent": 2.35,
  "fund_flow": "净流入 12.48亿元",
  "policy_summary": "国家大基金相关政策持续受到关注。",
  "upstream_status": "上游晶圆代工价格稳定。",
  "downstream_status": "下游需求逐步回暖。",
  "update_time": "2024-05-23 15:01:00",
  "refresh_interval": "1-5分钟"
}
```

---

## 20.9 相关股票

```text
GET /api/stocks/{stock_code}/related-stocks
```

返回字段：

```json
{
  "update_time": "2024-05-23 15:01:00",
  "list": [
    {
      "stock_name": "长电科技",
      "stock_code": "600584",
      "latest_price": 28.45,
      "change_percent": 1.20,
      "industry_relation": "封测相关",
      "abnormal_tag": "放量"
    }
  ]
}
```

---

## 20.10 半导体异动股

```text
GET /api/industry/semiconductor/abnormal-stocks
```

返回字段：

```json
{
  "update_time": "2024-05-23 15:01:00",
  "refresh_interval": "1-5分钟",
  "list": [
    {
      "stock_name": "示例股票",
      "stock_code": "000000",
      "one_day_change": 6.8,
      "five_day_change": 18.5,
      "twenty_day_change": 32.1,
      "volume_ratio": 2.6,
      "turnover_rate": 9.8,
      "fund_flow": "净流入 2.3亿元",
      "reason": "半导体概念活跃，成交量明显放大",
      "risk_note": "短期涨幅较大，注意回撤风险"
    }
  ]
}
```

---

## 20.11 保存邮件提醒

```text
POST /api/alerts
```

请求字段：

```json
{
  "stock_code": "000021",
  "stock_name": "深科技",
  "email": "you@example.com",
  "price_change_alert": true,
  "price_change_threshold": 5,
  "volume_alert": true,
  "volume_ratio_threshold": 2,
  "announcement_alert": true,
  "industry_alert": true,
  "abnormal_stock_alert": true,
  "enabled": true
}
```

返回字段：

```json
{
  "success": true,
  "message": "提醒设置已保存"
}
```

---

## 20.12 监测列表

```text
GET /api/watchlist
```

返回字段：

```json
{
  "list": [
    {
      "stock_name": "深科技",
      "stock_code": "000021",
      "industry": "电子制造",
      "latest_price": 18.76,
      "change_percent": 3.19,
      "alert_count": 4,
      "email": "you@example.com",
      "enabled": true,
      "data_status": "正常",
      "updated_at": "2024-05-23 15:00:00"
    }
  ]
}
```

---

# 21. 邮件提醒逻辑

## 21.1 触发条件

邮件提醒第一版支持：

1. 股价涨跌幅超过阈值
2. 成交量异常放大
3. 出现新公告
4. 所属行业出现明显异动
5. 半导体相关股票出现明显异动

---

## 21.2 邮件标题示例

```text
【股票监测提醒】深科技涨幅超过 5%
```

```text
【股票监测提醒】深科技出现公告更新
```

```text
【股票监测提醒】半导体相关股票出现异动
```

---

## 21.3 邮件正文示例

```text
你关注的股票出现监测提醒：

股票：深科技 000021
当前价格：18.76
当前涨跌幅：+5.23%
触发原因：股价涨幅超过设定阈值，成交量明显放大
所属行业：电子制造 / 半导体相关
数据来源：AKShare / 东方财富 / 新浪等公开数据源
数据更新时间：2024-05-23 15:00:00
触发时间：2024-05-23 15:01:00

相关动态：半导体板块今日资金流入较明显。

请注意：该提醒仅表示出现异动，不构成买卖建议。
```

---

# 22. UI 设计要求

## 22.1 整体风格

按照已确认的 UI 示意图方向：

- 简洁
- 现代
- Web 控制台风格
- 金融工具感
- 白色卡片
- 浅灰背景
- 蓝色主色
- 红绿表示涨跌
- 圆角卡片
- 轻阴影
- 信息清晰分区

可进一步扩展为：

- 深色科技风
- 霓虹金融风
- 轻奢白色渐变风

但第一版必须保证信息清晰，不要为了炫酷牺牲可读性。

---

## 22.2 页面布局

```text
顶部导航
搜索区
左侧股票详情
右侧行业监测 / 相关股票 / 半导体异动 / 提醒设置 / 信息来源
底部提示语
```

底部提示语：

```text
目标：输入股票 → 查看完整股票信息 → 同步监测行业动态 → 触发邮件提醒
```

同时在底部或信息来源模块显示：

```text
本系统采用准实时公开数据源，不构成投资建议，不适用于高频交易。
```

---

## 22.3 重点 UI 组件

需要实现：

- 搜索框
- 开始监测按钮
- 股票总览卡片
- 指标网格
- K线图
- 成交量图
- Tab 标签
- 行业监测卡片
- 相关股票表格
- 半导体异动卡片
- 提醒设置卡片
- 邮箱输入框
- 信息来源卡片
- 数据更新时间标签
- 数据延迟说明
- 加载状态
- 空状态
- 错误状态

---

# 23. 前端组件结构建议

```text
StockMonitorPage
├── AppHeader
│   ├── LogoTitle
│   └── NavMenu
│
├── SearchMonitorBar
│   ├── StockSearchInput
│   ├── StartMonitorButton
│   └── MonitorModeBadge
│
├── MainLayout
│   ├── LeftContent
│   │   ├── StockOverviewCard
│   │   ├── StockChartCard
│   │   └── StockInfoTabs
│   │
│   └── RightSidebar
│       ├── IndustryMonitorCard
│       ├── RelatedStocksCard
│       ├── AbnormalStocksCard
│       ├── AlertSettingCard
│       └── DataSourceCard
│
└── PageFooterHint
```

---

# 24. 推荐目录结构

```text
src/
├── app/
│   ├── page.tsx
│   ├── watchlist/
│   │   └── page.tsx
│   ├── alerts/
│   │   └── page.tsx
│   └── industry/
│       └── page.tsx
│
├── components/
│   ├── layout/
│   │   ├── AppHeader.tsx
│   │   └── MainLayout.tsx
│   │
│   ├── stock/
│   │   ├── SearchMonitorBar.tsx
│   │   ├── StockOverviewCard.tsx
│   │   ├── StockMetricsGrid.tsx
│   │   ├── StockChartCard.tsx
│   │   ├── StockInfoTabs.tsx
│   │   └── RelatedStocksCard.tsx
│   │
│   ├── industry/
│   │   ├── IndustryMonitorCard.tsx
│   │   └── AbnormalStocksCard.tsx
│   │
│   ├── alert/
│   │   └── AlertSettingCard.tsx
│   │
│   └── common/
│       ├── DataSourceCard.tsx
│       ├── EmptyState.tsx
│       ├── LoadingState.tsx
│       └── ErrorState.tsx
│
├── services/
│   ├── stockService.ts
│   ├── industryService.ts
│   ├── alertService.ts
│   ├── emailService.ts
│   └── dataSourceStatusService.ts
│
├── types/
│   ├── stock.ts
│   ├── industry.ts
│   └── alert.ts
│
└── mock/
    ├── stockMock.ts
    ├── klineMock.ts
    ├── industryMock.ts
    └── alertMock.ts
```

---

# 25. TypeScript 类型建议

```ts
export interface StockOverview {
  stockName: string;
  stockCode: string;
  latestPrice: number;
  changeAmount: number;
  changePercent: number;
  openPrice: number;
  highPrice: number;
  lowPrice: number;
  previousClose: number;
  volume: string;
  turnoverAmount: string;
  turnoverRate: number;
  peDynamic?: number;
  marketCap?: string;
  updateTime: string;
  dataSource?: string;
  dataType?: "实时" | "准实时" | "延迟";
  refreshInterval?: string;
  delayNote?: string;
  industry?: string;
  concepts?: string[];
}

export interface KlineItem {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
}

export interface IndustryMonitor {
  industryName: string;
  heatScore: number;
  sectorChangePercent: number;
  fundFlow: string;
  policySummary?: string;
  upstreamStatus?: string;
  downstreamStatus?: string;
  updateTime?: string;
  refreshInterval?: string;
}

export interface RelatedStock {
  stockName: string;
  stockCode: string;
  latestPrice: number;
  changePercent: number;
  industryRelation?: string;
  abnormalTag?: string;
  updateTime?: string;
}

export interface AbnormalStock {
  stockName: string;
  stockCode: string;
  oneDayChange: number;
  fiveDayChange?: number;
  twentyDayChange?: number;
  volumeRatio?: number;
  turnoverRate?: number;
  fundFlow?: string;
  reason?: string;
  riskNote?: string;
  updateTime?: string;
}

export interface AlertRule {
  stockCode: string;
  stockName: string;
  email: string;
  priceChangeAlert: boolean;
  priceChangeThreshold?: number;
  volumeAlert: boolean;
  volumeRatioThreshold?: number;
  announcementAlert: boolean;
  industryAlert: boolean;
  abnormalStockAlert: boolean;
  enabled: boolean;
}

export interface DataSourceStatus {
  sourceName: string;
  dataType: "实时" | "准实时" | "延迟";
  lastUpdateTime: string;
  refreshInterval: string;
  delayNote?: string;
  status: "正常" | "延迟" | "异常";
}
```

---

# 26. 开发阶段建议

## 阶段 1：完成首页静态 UI

目标：

先还原已确认的 UI 示意图。

任务：

- 顶部导航
- 搜索区
- 股票详情卡片
- K线图区域
- 公司概览 Tab
- 行业监测卡片
- 相关股票卡片
- 半导体异动卡片
- 提醒设置卡片
- 信息来源卡片
- 数据更新时间展示
- 数据延迟说明展示

---

## 阶段 2：接入 Mock 数据

目标：

不用真实接口也能完整展示页面。

任务：

- Mock 股票行情
- Mock K线数据
- Mock 公司信息
- Mock 行业监测
- Mock 相关股票
- Mock 异动股
- Mock 邮件提醒
- Mock 数据更新时间和数据延迟说明

---

## 阶段 3：接入真实数据

目标：

开始使用真实数据源。

优先级：

```text
1. AKShare
2. 自建后端 API 封装
3. TuShare 补充
4. china-stock-mcp 后续接入
5. stock_monitor 逻辑参考
```

任务：

- 股票搜索接口
- 股票详情接口
- K线接口
- 公司信息接口
- 财务接口
- 公告接口
- 新闻接口
- 行业监测接口
- 相关股票接口
- 半导体异动股接口
- 数据源状态接口
- 数据更新时间记录

---

## 阶段 4：实现邮件提醒

目标：

用户可以保存邮箱并收到提醒。

任务：

- 保存提醒规则
- 定时检查数据
- 判断触发条件
- 发送邮件
- 保存发送日志
- 避免重复提醒
- 邮件中加入数据更新时间、触发时间、数据来源

---

## 阶段 5：完善监测列表和行业洞察

目标：

让产品可以长期使用。

任务：

- 监测列表管理
- 提醒规则管理
- 半导体行业洞察
- 异动股排行
- 资金流排行
- 政策新闻摘要
- 数据异常提示
- 后续低延迟数据源扩展

---

# 27. 第一版成功标准

第一版做到以下效果即可：

1. 用户能输入股票名称或代码
2. 能看到完整股票行情
3. 能看到 K线图
4. 能看到公司概览、财务、公告、新闻
5. 能看到所属行业动态
6. 能看到相关股票
7. 能看到半导体相关异动
8. 能设置邮件提醒
9. 能在监测列表管理股票
10. 页面简洁清楚，不复杂
11. 页面显示数据来源
12. 页面显示最后更新时间
13. 页面明确说明数据为准实时，不适合高频交易

---

# 28. 最终原则

开发时必须遵守：

1. 优先 Web 端
2. 第一版只做邮件提醒
3. 不做自动交易
4. 不做买卖推荐
5. 不做高频交易
6. 数据必须真实可追溯
7. 页面要简洁
8. 先做核心闭环，不要一开始做太复杂
9. 半导体是主线，但允许监测半导体相关产业链
10. 异动股只做观察提醒，不做买入建议
11. 所有数据源要可替换、可维护
12. 优先使用 AKShare，不要从零手搓行情数据能力
13. china-stock-mcp 作为后续 AI Agent / MCP 能力参考
14. stock_monitor 只参考监控和提醒逻辑
15. InStock 只参考指标和系统结构，不直接作为第一版主系统
16. 不得把公开免费数据包装成绝对实时行情
17. 页面和邮件必须显示数据更新时间
18. 如果未来需要更低延迟，必须接入券商、交易所授权或付费 Level-2 行情接口

---
