# 阶段 6L-B3 主营与催化版本化研究证据设计

## 1. 目标

阶段 6L-B3 建立独立、可追溯、可证伪的主营与催化关联研究合同。首版同时
接受公司或交易所官方披露，以及基于官方材料形成的版本化人工审核映射。

本阶段只验证调用方已经提供的结构化证据，不自动解析公告，不生成正式
10 分，不升级业务暴露门禁，不改变阶段 6 状态机和只读 API 的
`not_ready` 状态。阶段 5 的 20 个交易日观察继续并行。

## 2. 现有边界

现有公司资料主要来自东方财富公司概况和经营范围聚合，能够用于页面展示，
但不能独立证明某项主营与当前行业催化直接或高度相关。

现有 `LeaderInputGatePolicy` 对市场、行业和行情来源统一使用 90 秒最大
年龄。年报、公告、公司 IR 和人工审核映射应按披露版本及有效区间判断，
不能套用分钟级行情时效，也不能把本轮抓取时间冒充原始披露时间。

因此 B3 不把新证据加入现有正式 `sources` 和
`business_exposure_source_contract_id`，避免在专用有效期策略和版本仓储
完成前错误开启正式门禁。

## 3. 证据来源

### 3.1 可接受的原始材料

- 上市公司年报、半年报、正式公告和公司 IR 材料；
- 上海、深圳或北京证券交易所正式披露；
- 巨潮资讯等交易所指定信息披露平台的公司原文；
- 具有明确版本、审核时间和官方引用材料的人工审核映射。

### 3.2 不可作为通过依据

- 普通新闻、媒体标题、研报摘要或社交媒体；
- 主题标签、概念板块命中和搜索关键词；
- AI 生成的主营、催化或关联判断；
- 没有原文链接、文档身份或披露时间的二手描述；
- 仅凭股票涨幅、成交、量比或资金流反推业务关联。

以上内容最多作为待验证线索，不进入本阶段的 `ready` 结果。

## 4. 输入合同

新增纯计算模块 `leader_business_catalyst_features.py`，定义以下结构化输入。

### 4.1 催化引用

`LeaderCatalystReference` 至少包含：

- `catalyst_id`：稳定催化身份；
- `industry_code` 和 `industry_release_id`：当时有效的行业身份与版本；
- `source_name`、`source_url` 和 `document_id`；
- `published_at`、`effective_from` 和可选 `effective_until`；
- 简短结构化摘要，不保存完整公告正文。

### 4.2 主营证明

`LeaderBusinessProof` 至少包含：

- `evidence_id` 和 `evidence_version`；
- `symbol`；
- `proof_type`：主营收入、产品、订单、产能、客户或其他正式披露；
- `source_name`、`source_url` 和 `document_id`；
- `published_at`、`effective_from` 和可选 `effective_until`；
- 简短结构化事实摘要，不保存完整年报或公告正文。

### 4.3 人工审核映射

`LeaderBusinessCatalystReview` 至少包含：

- `review_id` 和 `mapping_version`；
- `symbol`、`industry_code`、`industry_release_id` 和 `catalyst_id`；
- `relation`：`direct`、`highly_related`、`unconfirmed` 或
  `disproved`；
- `reviewed_at`、可选 `effective_until` 和稳定 `reviewer_key`；
- `basis_evidence_ids`：引用本输入内的主营证明；
- `basis_catalyst_id`：引用本输入内的催化身份；
- `decision_summary`：压缩审核结论。

`reviewer_key` 只保存稳定审核身份，不要求保存真实姓名或其他个人信息。
AI 不能成为审核者，也不能自动生成 `direct` 或 `highly_related` 结果。

### 4.4 候选输入

`LeaderBusinessCatalystFeatureInput` 组合：

- 本轮 `as_of`；
- 候选证券和行业版本身份；
- 一个催化引用；
- 一个或多个主营证明；
- 零个或多个人工审核映射，用于显式识别有效版本冲突；
- 调用方提供的来源状态。

模块不发网络请求、不读取数据库、不读取环境变量。

## 5. 验证与状态规则

所有时间必须包含时区。披露时间、审核时间和生效时间不得晚于本轮
`as_of`，抓取时间不能替代披露时间。

证券、行业、行业版本和催化身份必须完全一致。证据 ID、文档 ID 和版本
不得重复或为空。人工审核引用的主营证据和催化必须实际存在于同一输入。

有效区间按以下规则处理：

```text
effective_from <= as_of
and (effective_until is null or as_of <= effective_until)
```

结果状态：

- 来源失败：`source_failed`；
- 输入缺失：`missing`；
- 已过有效期：`stale`；
- 身份错配、未来时间、引用缺失或材料不合格：
  `source_unverified`；
- 官方材料明确给出业务与催化的直接关系，或版本化人工审核基于官方材料
  给出 `direct/highly_related`：研究状态 `ready`；
- 审核结论为 `unconfirmed`：`source_unverified`；
- 审核结论为 `disproved`：证据合同状态 `ready`，关系状态
  `disproved`，并保留证伪原因。

证伪优先于任何正向关联。相互冲突的有效审核版本不得自行选择最新一条，
应返回 `source_unverified`，等待调用方提供唯一有效版本。

## 6. 输出合同

`LeaderBusinessCatalystFeatureResult` 输出：

- `formulaVersion=radar-leader-business-catalyst-feature-v1`；
- 整体研究状态和稳定原因；
- 关系状态；
- 催化、行业、主营证明和审核映射的压缩身份；
- 证据版本与有效期；
- `scoreReady=false`；
- `formalUsable=false`；
- `researchScore=null`。

结果不得保存完整公告、年报、IR 文本、AI 推理或个人信息。

## 7. 运行时接入

阶段 6 运行时新增可选
`business_catalyst_inputs_by_symbol`。缺省时继续返回
`business_exposure_evidence_missing`。

输入存在时必须再次核对：

- 输入 `as_of` 等于运行批次；
- 候选证券身份一致；
- 行业代码和行业版本一致；
- 催化引用属于同一行业。

压缩结果写入：

```text
evidence.researchFeatures.businessCatalyst
```

本阶段无论研究关系为正向、未确认或已证伪，正式合同继续保持：

- `business_exposure.score=null`；
- `business_exposure_status=missing`；
- `business_exposure_source_contract_id=null`；
- 状态机继续 `blocked/out`；
- 只读 API 继续 `not_ready`。

证伪结果会进入研究证据和缺失原因，但在专用正式门禁、版本仓储和回退规则
验收前不直接改变生产状态。

## 8. 明确不做

- 不复用东方财富经营范围直接证明主营催化关系；
- 不自动抓取、下载或解析年报、公告和 IR 文档；
- 不让新闻、主题标签或 AI 自动通过审核；
- 不新增数据库表、迁移、仓储、调度或人工审核页面；
- 不新增或修改 API、前端、提醒和交易建议；
- 不生成正式 10 分，不打开业务暴露门禁；
- 不修改环境变量，不启用阶段 6 生产开关；
- 不停止或重载当前 4000/8001 服务；
- 不暂存、提交或推送 Git。

## 9. 测试

纯函数测试至少覆盖：

- 官方材料直接证明；
- 官方主营材料加版本化人工审核映射；
- `direct`、`highly_related`、`unconfirmed` 和 `disproved`；
- 缺失原文链接、文档身份、版本或审核引用；
- 未来披露、未来审核、未生效和已过期；
- 证券、行业、行业版本和催化身份错配；
- 重复证据、冲突审核版本和来源失败；
- 输出不包含完整原文，正式分数与可用标记保持关闭。

运行时测试至少覆盖：

- 缺省输入保持现有缺失语义；
- 有效研究证据正确挂载；
- 身份错配稳定降级；
- 正向和证伪研究结果都不修改正式分数、业务暴露门禁或状态机。

全部龙头测试和后端完整测试必须在导入测试前把生产 SQLite 精确路径重定向
到临时库。最后运行相关语法检查和 `git diff --check`。

## 10. 回退

回退只需撤回独立研究模块、运行时可选输入与投影、对应测试和本文档。
现有状态机、评分、数据库版本、API、阶段 6L-A/B1/B2、旧公司资料页面和
阶段 5 影子观察不受影响。
