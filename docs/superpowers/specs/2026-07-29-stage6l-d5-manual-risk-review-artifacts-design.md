# 阶段 6L-D5 离线人工事实补录与版本化审核工件设计

## 1. 目标

阶段 6L-D5 消费 D4 生成的 `fact_extraction_missing` 或
`relation_review_required` 候选，建立隔离、可验证、可版本化的人工审核工件。

D5 解决两个问题：

- D4 已成功解码官方 PDF，但 D3 没有提取到明确标签事实时，允许人工基于同一
  内存页文本补录压缩事实；
- D3 已有明确事实但缺少关系时，允许人工提交 D3 现有版本化关系审核，并重新
  经过 D3 全部事件、版本和事实门禁。

D5 不把人工结果直接写入 D3 或 D1，不改变正式风险状态。

## 2. 方案取舍

采用方案 A“独立人工审核工件层”：

1. D5 重新核对 D2 文档、D4 内容、D3 结果和 D4 候选；
2. 人工事实补录只生成独立 `manual` 研究事实工件；
3. 人工关系审核调用 D3 重新提取事实并验证关系；
4. 每次审核具有版本、前序版本、审核人、时间和有效期；
5. 输出不含 PDF、页文本、原始值、命中片段或审核摘要。

不采用：

- 直接修改 D3，让人工事实与确定性抽取事实混在一起；
- 立即新增数据库、审核页面、运行时任务或正式风险门禁。

## 3. 模块边界

新增：

`backend/radar/leader_risk_review_artifacts.py`

该模块：

- 只做内存纯计算；
- 不联网、不打开数据库、不读写文件；
- 不调用 AI 或 OCR；
- 不修改 D1、D2、D3 或 D4 的既有状态；
- 不接仓储、迁移、运行时、调度、API、前端或提醒。

## 4. 输入合同

`ManualRiskReviewArtifactInput`：

- `as_of`；
- D2 `OfficialRiskDocumentMetadata`；
- D4 `OfficialRiskDocumentContentResult`；
- D3 `OfficialRiskDocumentFactResult`；
- D4 `RiskDocumentReviewCandidate`；
- D1 已知事件版本；
- 当前 `ManualRiskReviewSubmission`；
- 已接受的前序人工工件。

D5 必须调用 D4 候选构造器重新生成候选，并与输入候选完全相等。文档、证券、
发行人、PDF SHA-256、字节数、页数、候选类型和原因码任何一项不一致都拒绝。

## 5. 人工事实补录

`ManualRiskDocumentFactSubmission`：

- `fact_kind`；
- `source_value`，只在当前调用内使用并从 `repr` 隐藏；
- `page_number`；
- `source_fragment`，只在当前调用内使用并从 `repr` 隐藏。

规则：

- 只允许 D3 已冻结的六类事实；
- 页码必须指向 D4 当前内容页；
- 片段必须真实存在于该页文本，最长 500 字符；
- 原始值去除空白后必须真实存在于片段；
- 案号和审计报告号按 D3 口径输出 SHA-256 身份；
- 报告期、原公告编号、日期和区间按 D3 口径归一化；
- 同类同值同页事实去重，冲突或非法值拒绝；
- 只允许 `fact_extraction_missing` 候选提交补录事实。

输出使用现有 `RiskDocumentFact` 结构，但
`extractor_version=radar-leader-risk-document-manual-review-v1`，明确区别于 D3
确定性抽取。

## 6. 人工关系审核

`relation_review_required` 候选只接受一个现有
`RiskDocumentVersionReview`：

- `review_method` 必须为 `manual`；
- 审核人、审核时间、有效期和映射版本必须与 D5 提交一致；
- 不允许同时补录人工事实；
- D5 使用 D4 当前页、D2 文档、D1 事件版本和该审核重新调用 D3；
- 只有 D3 返回 `ready` 且接受关系时，D5 才保存压缩关系工件；
- D3 的错事件、错版本、错文档、错案号、错报告期、冲突和过期规则全部保留。

D5 输出不包含 `decision_summary`。

## 7. 版本合同

`ManualRiskReviewSubmission`：

- `review_version`；
- 可选 `supersedes_review_version`；
- `review_method=manual`；
- `reviewer_key`；
- `reviewed_at`；
- 可选 `effective_until`；
- 候选、文档、证券、发行人和 PDF 哈希身份；
- 人工事实补录或人工关系审核，二选一。

首版规则：

- 首个版本不得声明前序版本；
- 后续版本必须精确替代同一工件的当前最后版本；
- 版本、审核时间必须单调递增；
- 前序工件必须属于同一候选、文档、证券、发行人和 PDF；
- 重复版本、分叉、跨候选替代和未来审核拒绝；
- 已过有效期的新工件返回 `stale`，不得作为当前审核结果；
- 旧版本一旦被新版本替代，只保留审计身份，不再作为当前版本。

工件 ID 由候选 ID 和候选类型稳定生成；版本变化不改变工件 ID。

## 8. 输出合同

`AcceptedManualRiskReviewArtifact`：

- 稳定工件 ID；
- 审核版本和前序版本；
- 候选 ID 与类型；
- 文档、证券、发行人和 PDF 哈希；
- 审核人、审核时间和有效期；
- 压缩人工事实或 D3 接受关系；
- `review_method=manual`；
- `formal_usable=false`；
- `applied_to_d3=false`；
- `applied_to_d1=false`。

`ManualRiskReviewArtifactResult`：

- `status`；
- 可选工件；
- `formal_usable=false`；
- 稳定原因码。

## 9. 状态语义

- 有效人工事实补录或关系审核：`ready`；
- 当前提交已过有效期：`stale`；
- 候选、身份、哈希、片段、事实、关系、版本或时间不可信：
  `source_unverified`；
- D3 关系重验失败时保留 D3 的 `missing/stale/source_failed/source_unverified`
  语义，不生成工件；
- 不把人工“未发现”解释为没有风险。

## 10. 安全边界

- 人工 `source_value`、`source_fragment` 和关系 `decision_summary` 均不得进入
  输出或 `repr`；
- 案号和审计报告号只输出哈希身份；
- 错误原因不得回显正文、原始编号、审核摘要、内部路径或异常；
- AI、模型、自动审核和缺审核人一律拒绝；
- 人工工件始终是研究证据，不能绕过 D1/D3。

## 11. 明确不做

- 不做 OCR、AI 抽取或自动补录；
- 不把人工补录合并回 D3 事实集合；
- 不生成 D1 风险事件或解除证据；
- 不新增数据库、迁移、仓储、运行时、调度、API、前端或提醒；
- 不新增依赖；
- 不读取或写入生产 SQLite；
- 不修改环境变量或服务；
- 不执行 Git 暂存、提交或推送。

## 12. 验收

- 六类人工事实均可从真实页片段归一化为压缩事实；
- 错页、片段不存在、值不在片段、非法值、重复事实和跨类型提交被拒绝；
- 关系审核重新经过 D3 的事件、版本和事实门禁；
- 候选伪造、空身份、错哈希和内容漂移被拒绝；
- 首版、顺序升级、过期、重复、分叉和跨候选版本链有稳定语义；
- 输出和 `repr` 不包含原始值、片段、页文本或审核摘要；
- 全部结果保持 `formal_usable=false`，且未应用到 D3/D1；
- D3-D5、全部龙头和后端完整测试通过；
- 语法检查、差异检查和只读安全复核通过。

## 13. 实施结果

阶段 6L-D5 按方案 A 完成本地隔离实现：

- 六类人工事实从当前 D4 页文本的真实片段归一化；
- 案号和审计报告号只输出 SHA-256 身份；
- D4 候选和 D3 事实结果在每次审核时重新构造并比对；
- 关系审核重新调用 D3 验证目标事件、版本和事实依据；
- 审核版本只允许同一工件线性替代；
- 重复事实、同类冲突事实、错页、错片段、错原始值、AI 审核、版本分叉、
  跨候选历史、伪造历史载荷和过期非法载荷均被拒绝；
- D1 事件集合、人工原始值、页片段和审核摘要从输入或输出 `repr` 隐藏；
- 所有工件保持 `formal_usable=false`、`applied_to_d3=false` 和
  `applied_to_d1=false`。

本阶段没有运行真实人工审核，也没有保存人工工件；Fixture 只验证合同行为。
