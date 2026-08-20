# 阶段6候选全集官方主营材料 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立候选全集官方主营材料发现、人工复核清单和现有主营催化合同接入链。

**Architecture:** 官方发现层只返回巨潮文档元数据；复核清单按候选计划恢复顺序并保留缺失/失败；人工提取层是唯一能生成现有官方材料工件的入口。所有正式门始终关闭。

**Tech Stack:** Python 3、dataclass、requests、unittest；不新增依赖。

**Spec:** docs/superpowers/specs/2026-08-20-stage6-official-business-materials-design.md

## Global Constraints

- 不读取或写入生产 SQLite。
- 不调用付费 AI，不从新闻、名称或主题标签推断主营关系。
- 不修改环境变量、依赖、迁移、服务进程或正式门。
- 不执行 Git add、commit、push、PR 或部署。

---

### Task 1: 巨潮官方主营材料发现合同

**Files:** 创建 backend/radar/sources/leader_business_official.py；测试 backend/tests/test_radar_leader_business_official.py。

**Interfaces:** 消费冻结的巨潮发行人身份和注入式 transport；产出查询、文档、发现批次和抓取函数。

- [ ] 写失败测试：合法官方响应形成完整分页批次，伪造域名、未来时间、身份漂移和分页缺口失败关闭。
- [ ] 运行专项并确认因模块缺失失败。
- [ ] 实现最小查询、解析、来源及时点校验。
- [ ] 运行专项通过。

### Task 2: 候选全集人工复核清单

**Files:** 创建 backend/radar/leader_business_material_review_queue.py；测试 backend/tests/test_radar_leader_business_material_review_queue.py。

**Interfaces:** 消费候选计划与 Task 1 发现批次；产出逐候选 pending_review/missing/source_failed/source_unverified 清单。

- [ ] 写失败测试：恢复计划顺序、缺股/重复/跨计划失败关闭、空结果保持 missing、证据脱敏。
- [ ] 运行专项并确认因模块缺失失败。
- [ ] 实现全集聚合与只读证据合同。
- [ ] 运行专项通过。

### Task 3: 人工提取接入现有官方材料适配器

**Files:** 创建 backend/radar/leader_business_material_human_extraction.py；测试 backend/tests/test_radar_leader_business_material_human_extraction.py。

**Interfaces:** 消费 Task 2 清单及人工提取条目；产出完整候选顺序的现有官方材料批次条目。

- [ ] 写失败测试：缺人工条目输出 MISSING，引用不存在文档、非人工审核、身份漂移失败关闭，完整人工提取进入现有 adapter 但正式门仍关闭。
- [ ] 运行专项并确认因模块缺失失败。
- [ ] 实现严格引用校验和现有工件转换。
- [ ] 运行专项通过。

### Task 4: 联合回归与检查点

**Files:** 修改 NEXT_CHAT_HANDOFF.md。

- [ ] 运行三项新增专项及既有主营/阶段6联合测试。
- [ ] 运行隔离生产 SQLite 的完整后端测试。
- [ ] 运行 Python 编译和 git diff --check。
- [ ] 更新唯一检查点，明确未取得真实材料和人工判断前正式门仍关闭。
