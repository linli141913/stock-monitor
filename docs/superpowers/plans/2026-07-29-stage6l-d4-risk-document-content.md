# 阶段 6L-D4 官方 PDF 受控解码与审核候选实施计划

**Goal:** 在 D2 与 D3 之间建立不落盘、不可直接进入正式门禁的官方 PDF 来源适配器。

**Architecture:** 来源层严格验证 D2 官方身份并有界下载；解码层只在内存生成
隐藏正文页；候选层只消费 D4 内容结果与 D3 压缩结果，生成不含正文的人工待审
身份。

**Tech Stack:** Python 3.9、requests、pypdf、标准库 dataclass/enum/hashlib/io、
现有 unittest。

## Global Constraints

- 不新增依赖。
- 不读取或写入生产 SQLite。
- 不保存 PDF、正文或上游响应。
- 不接迁移、仓储、运行时、调度、API、前端、提醒或正式门禁。
- 不停止、重启或重载 4000/8001。
- 不修改环境变量。
- 不执行 Git 暂存、提交或推送。
- 阶段 5 的 20 个交易日观察继续并行。

### Task 1: 冻结下载解码合同

**Files:**
- Create: `backend/tests/test_radar_leader_risk_document_content.py`
- Create: `backend/radar/sources/leader_risk_document_content.py`

- [x] 写合法内存 PDF、传输参数和安全输出失败测试
- [x] 写官方域名、文档身份、重定向、状态、类型和文件头失败测试
- [x] 写声明长度、实际字节、加密、损坏、页数和字符上限失败测试
- [x] 运行专项测试确认 RED
- [x] 实现最小下载、校验和内存解码
- [x] 运行专项测试确认 GREEN

### Task 2: 冻结压缩审核候选合同

**Files:**
- Modify: `backend/tests/test_radar_leader_risk_document_content.py`
- Modify: `backend/radar/sources/leader_risk_document_content.py`

- [x] 写无事实与缺关系候选失败测试
- [x] 写身份、哈希、故障状态和已有关系边界失败测试
- [x] 运行新增测试确认 RED
- [x] 实现稳定候选身份与压缩字段
- [x] 运行 D3-D4 专项测试确认 GREEN

### Task 3: 真实样本、回归与交接

**Files:**
- Modify: `docs/superpowers/specs/2026-07-29-stage6l-d4-risk-document-content-design.md`
- Modify: `docs/superpowers/plans/2026-07-29-stage6l-d4-risk-document-content.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

- [x] 以内存方式运行一份真实巨潮官方 PDF 探针
- [x] 运行 D1-D4 联合测试和全部龙头测试
- [x] 运行后端完整测试、语法检查和 `git diff --check`
- [x] 只读复核安全、身份、上限和正式门禁边界
- [x] 更新唯一交接文件并记录下一阶段
