# 阶段 6L-D4 官方 PDF 受控解码与审核候选合同设计

## 1. 目标

阶段 6L-D4 补齐 D2 官方文档元数据与 D3 正文事实抽取之间的隔离来源适配器。
它只负责受控下载巨潮官方 PDF、在内存中解码逐页文本，并在 D3 没有明确事实
或缺少人工关系时生成压缩审核候选。

D4 不保存 PDF 或正文，不连接生产数据库，也不把候选转换成 D1 正式风险事件、
解除证据或覆盖证明。

## 2. 已批准方案

采用“严格官方身份门禁 + 有界流式下载 + 内存 PDF 解码 + 压缩人工审核候选”：

1. 只消费 D2 `OfficialRiskDocumentMetadata`；
2. 只允许 `https://static.cninfo.com.cn/.../<document_id>.PDF`；
3. HTTP 会话禁用环境代理继承，超时 20 秒，`allow_redirects=False`；
4. 声明长度和实际读取字节都不得超过 10 MiB；
5. 只接受 200 响应、PDF 内容类型、原始官方 URL 和 `%PDF-` 文件头；
6. 使用项目已有 `pypdf` 在内存中解码，不创建临时文件；
7. 最多 200 页，单页最多 100,000 字符，总计最多 2,000,000 字符；
8. 解码结果只在隐藏 `repr` 的页集合中短暂存在；
9. D3 返回无明确事实或有事实但缺关系时，生成不含正文的人工审核候选；
10. 全部输出继续保持 `formal_usable=false`。

## 3. 模块边界

新增：

`backend/radar/sources/leader_risk_document_content.py`

该模块：

- 可以执行一次受控官方 PDF 下载；
- 只在内存中持有响应字节和页文本；
- 不打开数据库，不读写文件，不读环境变量；
- 不调用 AI，不执行 OCR；
- 不接仓储、迁移、运行时、调度、API、前端或提醒；
- 不生成或修改 D1 风险事件。

## 4. 下载与解码合同

### 4.1 HTTP 响应

`OfficialRiskDocumentHttpResponse`：

- `status_code`；
- `headers`；
- `final_url`；
- `redirect_count`；
- `content`，且 `repr` 隐藏。

默认传输必须使用 `requests.Session(trust_env=False)`、流式读取、
`allow_redirects=False` 和 20 秒超时。注入传输只用于测试，但返回后仍由主合同
重新校验 URL、状态、响应头、长度和文件头，不能绕过安全门禁。

### 4.2 内容结果

`OfficialRiskDocumentContentResult`：

- `status`；
- 文档、证券和发行人身份；
- `content_sha256`；
- `byte_count`；
- `page_count`；
- `pages`，且 `repr` 隐藏；
- `fetched_at`；
- `formal_usable=false`；
- `reasons`。

成功解码使用 `ready`。网络失败使用 `source_failed`。元数据、URL、重定向、
状态码、类型、长度、文件头、加密状态、PDF 结构、页数或文本规模不可信时使用
`source_unverified`。允许 PDF 页面没有可提取文字，交给 D3 返回 `missing`，
再生成审核候选；D4 不擅自 OCR。

## 5. 人工审核候选合同

`RiskDocumentReviewCandidateKind`：

- `fact_extraction_missing`：D3 没有明确事实；
- `relation_review_required`：D3 有明确事实，但没有可接受的人工关系。

`RiskDocumentReviewCandidate` 仅包含：

- 稳定候选 ID 和候选类型；
- 文档、证券、发行人身份；
- PDF SHA-256、字节数和页数；
- D3 状态和稳定原因码；
- 事实 ID 与事实类型；
- `manual_review_required=true`；
- `formal_usable=false`。

候选不得包含 PDF 字节、逐页正文、命中片段、标题推断、审核摘要或 D1 决策。
内容结果与 D3 结果的文档、证券、发行人和 SHA-256 必须完全一致。

当 D3 已存在有效关系时不生成候选；D3 为失败、不可信或过期状态时也不生成
人工候选，而是保留原失败语义，避免把来源故障伪装成人工待办。

## 6. 安全边界

- 只允许巨潮官方静态域名，拒绝用户名、密码、查询参数和片段；
- URL 路径的 PDF 文件名必须与 D2 `document_id` 完全一致；
- 禁止重定向，最终 URL 必须与元数据 URL 完全相同；
- `Content-Length` 非法、与实际长度不一致或超过 10 MiB 时拒绝；
- 实际流式读取超过 10 MiB 时立即中止；
- 拒绝加密、损坏、零页或超过 200 页的 PDF；
- 所有错误只返回稳定原因码，不回显正文、响应体、内部路径或异常堆栈；
- PDF 字节、页文本字段必须从 `repr` 隐藏；
- 不创建临时文件，不持久化任何正文。

## 7. 明确不做

- 不做 OCR、表格识别或 AI 正文分析；
- 不自动识别风险类别、严重程度或关闭状态；
- 不自动建立修订、替代或解除关系；
- 不修改 D1 `CORRECTED` 阻断语义；
- 不声明历史覆盖完整；
- 不接数据库、迁移、仓储、运行时、调度、API、前端或提醒；
- 不新增或升级依赖；
- 不读取或写入生产 SQLite；
- 不修改环境变量，不停止、重启或重载服务；
- 不执行 Git 暂存、提交或推送。

## 8. 验收

- 合法官方 PDF 可在内存中解码并输出哈希、字节数、页数和隐藏页文本；
- 非官方 URL、错文档 ID、重定向、非 200、非 PDF、伪文件头被拒绝；
- 声明长度和实际字节超过 10 MiB 均被拒绝；
- 加密、损坏、零页、超过 200 页及字符超限被拒绝；
- 默认传输明确禁用代理继承、重定向和落盘；
- D3 无事实时生成 `fact_extraction_missing`；
- D3 有事实但无关系时生成 `relation_review_required`；
- 身份或哈希不一致、来源故障和已有有效关系不生成候选；
- 候选和结果 `repr` 不泄露正文或 PDF 字节；
- 真实巨潮 PDF 只在内存中完成一次探针，不保存正文；
- D1-D4、全部龙头和后端完整测试通过；
- 语法检查、`git diff --check` 和独立只读复核通过。

## 9. 真实官方样本 POC

2026-07-29 只在当前 Python 进程内下载并解码巨潮官方文档
`cninfo:1225443882`：

- 官方 URL：
  `https://static.cninfo.com.cn/finalpage/2026-07-27/1225443882.PDF`；
- HTTP 未发生重定向；
- PDF 大小 125,092 字节；
- SHA-256：
  `00d21192fe1280c8940027cfda9e4272b1d66a9d52af5a2774e4747789ff563d`；
- 页数 1；
- D4 内容状态 `ready`；
- D3 事实状态 `missing`，原因 `risk_document_facts_missing`；
- D4 审核候选状态 `ready`，类型 `fact_extraction_missing`；
- 内容结果和候选均为 `formal_usable=false`。

探针没有保存 PDF、正文或逐页文本，没有读取或写入 SQLite，也没有接入运行时、
API、前端或正式风险门禁。
