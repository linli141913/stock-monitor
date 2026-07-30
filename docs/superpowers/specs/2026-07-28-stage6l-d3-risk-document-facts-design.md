# 阶段 6L-D3 风险正文事实与版本关联合同设计

## 1. 目标

阶段 6L-D3 在 D2 官方文档发现结果之上，建立隔离的正文事实抽取和版本化
人工关联合同。D3 只处理已经取得的官方正文页文本，不联网下载 PDF、不保存
正文，也不直接生成 D1 正式风险事件或解除证据。

D3 必须解决：

- 从正文中确定性提取案号、报告期、审计报告号、原公告编号和明确日期；
- 让修订、替代或解除关系精确指向 `event_id + event_version`；
- 要求人工审核映射有版本、审核人、审核时间和事实依据；
- 对模糊、冲突、错证券、错文档、错报告期和 AI 审核明确降级；
- 输出压缩事实和片段哈希，不输出完整正文或审核摘要。

## 2. 已批准方案

采用“确定性事实抽取 + 版本化人工关联合同”。

1. D2 `OfficialRiskDocumentMetadata` 提供文档、证券、发行人、发布时间和官方
   URL 身份；
2. 调用方提供有界的逐页正文文本和 PDF SHA-256，不把下载或 OCR 混入 D3；
3. D3 只识别带明确标签的案号、报告期、审计报告号、原公告编号、生效日期和
   实施区间，不从标题或一般关键词猜风险类别；
4. 每个事实保存稳定事实 ID、归一化值、页码和片段 SHA-256，不返回原文；
5. 修订或解除关系必须由 `manual` 审核映射提出，并精确引用 D1 已存在的事件
   版本和 D3 事实；
6. 接受的关系仍是研究关系，不生成
   `LeaderRiskResolutionEvidence`，不改变 D1 `CORRECTED` 的阻断语义。

## 3. 模块边界

新增：

`backend/radar/leader_risk_document_facts.py`

该模块：

- 只做内存纯计算；
- 不发起网络请求；
- 不打开数据库；
- 不读环境变量；
- 不下载、保存或回传 PDF 正文；
- 不调用 AI；
- 不接运行时、仓储、调度、API、前端或提醒。

正文下载、PDF 解码和 OCR 属于后续来源适配阶段。D3 只接受已经解码的逐页
文本，因此 Fixture 可以完整覆盖合同，而不会把下载不稳定性混入事实规则。

## 4. 数据合同

### 4.1 正文页

`OfficialRiskDocumentPage`：

- `page_number`：从 1 开始；
- `text`：当前页文本。

限制：

- 最多 200 页；
- 单页最多 100,000 字符；
- 总文本最多 2,000,000 字符；
- 页码必须连续、唯一且从 1 开始；
- 空页可以存在，但所有页均为空时返回 `missing`。

### 4.2 输入

`OfficialRiskDocumentFactInput`：

- `as_of`；
- D2 `OfficialRiskDocumentMetadata`；
- `content_sha256`；
- `pages`；
- `extracted_at`；
- `source_status`；
- 当前证券已知 D1 `LeaderRiskEventEvidence`；
- `RiskDocumentVersionReview` 人工映射。

文档元数据必须继续满足 D2 合同。正文抓取时间不能早于公告发布时间或晚于
本轮 `as_of`，证券和发行人必须与目标事件完全一致。

### 4.3 确定性事实

`RiskDocumentFactKind`：

- `case_id`；
- `reporting_period`；
- `audit_report_id`；
- `referenced_document_id`；
- `effective_date`；
- `effective_interval`。

`RiskDocumentFact`：

- `fact_id`；
- `fact_kind`；
- `normalized_value`；
- `page_number`；
- `fragment_sha256`；
- `extractor_version`。

事实 ID 由文档 ID、事实类型、归一化值和页码生成 SHA-256。片段哈希使用实际
命中的有界文本，不保存命中正文。

### 4.4 版本化人工映射

`RiskDocumentVersionReview`：

- `review_id`；
- `mapping_version`；
- `relation_kind`：`supersedes` 或 `resolves`；
- `review_method`：只接受 `manual`；
- `reviewer_key`；
- `reviewed_at`；
- 可选 `effective_until`；
- `source_document_id`；
- `target_event_id`；
- `target_event_version`；
- `target_document_id`；
- `replacement_event_version`；
- `basis_fact_ids`；
- `decision_summary`。

规则：

- `target_event_id + target_event_version` 必须在输入事件中精确存在；
- 证券、发行人和 `target_document_id` 必须与目标事件一致；
- 每个关系必须引用当前文档抽取出的 `referenced_document_id`，且值等于
  `target_document_id`；
- 调查、诉讼还必须引用与目标事件一致的 `case_id`；
- 业绩、审计还必须引用与目标事件一致的 `reporting_period`；
- `supersedes` 必须提供不同于目标版本的 `replacement_event_version`；
- `resolves` 不得提供替代版本；
- 审核不得晚于 `as_of`，不得早于当前公告发布时间；
- AI、模型、自动审核或缺审核人一律拒绝；
- 同一映射版本、审核 ID 或目标事件关系冲突时降级。

### 4.5 输出

`OfficialRiskDocumentFactResult`：

- `status`；
- 文档、证券和发行人身份；
- `content_sha256`；
- `facts`；
- `relations`；
- `correction_links_complete=false`；
- `formal_usable=false`；
- `reasons`。

输出关系只包含映射身份、关系类型、目标事件版本、可选替代版本、审核时间和
事实 ID，不包含完整正文或 `decision_summary`。

## 5. 确定性抽取边界

首版只识别带明确标签的正文结构：

- `案号/案件编号/立案编号/立案告知书编号`；
- `报告期/所属报告期`；
- `审计报告编号/审计报告文号/报告编号`；
- `原公告编号/被更正公告编号/前次公告编号`；
- `生效日期/解除限售日期/实施完成日期`；
- `减持期间/实施区间`。

案号正文先去除空白，再归一化为 `case:SHA-256`；审计报告号只接受包含数字的
有界编号字符集，并归一化为 `audit-report:SHA-256`。两者都不暴露原始编号，
并与 D1 的安全身份合同一致。日期归一化为 `YYYY-MM-DD`，实施区间归一化为
`YYYY-MM-DD/YYYY-MM-DD`。报告期归一化为 `YYYY`、`YYYY-H1`、
`YYYY-Q1` 或 `YYYY-Q3`。

不识别：

- 无明确标签的一般描述；
- 标题、搜索关键词或 `associateAnnouncement`；
- 金额、数量、严重程度和风险方向；
- AI 或自然语言推断出的原事件；
- 仅凭相同证券、相近日期或相似标题建立的关系。

## 6. 状态与失败语义

- 上游 `source_failed`：返回 `source_failed`；
- 上游缺失：返回 `missing`；
- 内容哈希、页码、大小、时间或身份非法：`source_unverified`；
- 无确定性事实：`missing`；
- 有确定性事实且无审核映射：`ready`，关系为空并记录
  `risk_document_relation_missing`；
- 审核映射非法、冲突或依据不足：`source_unverified`；
- 有效人工映射：`ready`，返回研究关系；
- 审核映射已过期且没有当前映射：`stale`。

无论状态如何：

- `formal_usable=false`；
- `correction_links_complete=false`；
- 不生成 D1 正式事件、解除证据或覆盖证明。

## 7. 安全和隐私

- SHA-256 必须是 64 位小写十六进制；
- 标识符和审核人键限制为安全字符及 160 字符；
- 页文本只在当前函数调用内使用；
- 结果不得包含页文本、匹配片段、审核摘要、URL 查询参数或异常堆栈；
- 错误原因使用稳定代码，不回显正文；
- 不把真实空事实解释为无风险。

## 8. 明确不做

- 不联网下载 PDF；
- 不做 OCR；
- 不保存正文或上游响应；
- 不自动判断风险类别、子类型、严重程度或事件状态；
- 不把修订自动解释为风险关闭；
- 不修改 D1 的 `CORRECTED` 规则；
- 不声明历史完整覆盖；
- 不接数据库、迁移、仓储、运行时、调度、API、前端或提醒；
- 不新增依赖；
- 不读取或写入生产 SQLite；
- 不修改环境变量或服务；
- 不执行 Git 暂存、提交或推送。

## 9. 验收

- 正常 Fixture 提取六类明确事实并只输出压缩字段；
- 模糊文字不产生事实；
- 非连续页码、超限正文、错误哈希、未来抓取时间和错文档身份被拒绝；
- 人工映射必须精确引用当前事实和目标事件版本；
- 错证券、错发行人、错原公告、错案号、错报告期和冲突版本被拒绝；
- AI 审核、未来审核、过期审核和重复映射有稳定语义；
- `supersedes` 与 `resolves` 的替代版本规则严格分开；
- 输出不包含完整正文或审核摘要；
- D1 和 D2 行为保持不变；
- 全部龙头测试、后端完整测试、语法检查和 `git diff --check` 通过；
- 独立只读复核无 P1/P2 阻断。

## 10. 真实官方样本 POC

2026-07-28 仅在内存中下载和解析三份巨潮官方 PDF，没有保存 PDF 或正文：

| 文档 ID | PDF SHA-256 | 大小 | 页数 | D3 结果 |
| --- | --- | ---: | ---: | --- |
| `cninfo:1225443882` | `00d21192fe1280c8940027cfda9e4272b1d66a9d52af5a2774e4747789ff563d` | 125,092B | 1 | `missing` |
| `cninfo:1214867413` | `be309d26241891e51048627382a58746b973cde7165017de2310000331c1cfed` | 160,725B | 1 | `missing` |
| `cninfo:1224836470` | `5221d95e2cee6395518f4e550be9919bf99510edd8f84604d9c4fa4ac3b0e032` | 7,694,073B | 16 | `missing` |

第一份为 D2 发现的“立案告知书”代表样本；后两份分别由“前次公告编号”和
“审计报告编号”全文检索发现。三份 PDF 的解码文本均没有满足 D3 冻结格式的
明确标签，因此没有自动产出事实，`formal_usable=false`。该结果证明首版不会
从标题、全文检索命中或一般描述猜案号、原公告或审计身份；正向提取路径由
Fixture 验证，不能用 Fixture 反向宣称真实来源覆盖。

最终 D3 专项 19 项、D1-D3 联合 56 项、全部龙头 210 项、后端完整 771 项
测试通过。独立复核发现的过期映射并存、自关联/非前序事件、畸形 URL 和编号
泄露 4 个 P2 均已修复，第二轮复核无 P1/P2 阻断。
