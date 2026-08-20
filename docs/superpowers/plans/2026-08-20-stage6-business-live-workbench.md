# 阶段6主营材料真实验收台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把合法同轮候选、巨潮发行人身份、候选全集官方材料发现和人工复核清单串成一键只读验收，并生成可直接查看的本地验收台。

**Architecture:** 复用已通过的可交易性真实验收取得冻结候选计划，一次解析巨潮发行人全量名册，再调用候选全集主营材料编排。输出只含公开材料和门禁摘要；HTML采用与主线雷达一致的深色工业化信息密度，不接生产数据库。

**Tech Stack:** Python、unittest、标准库HTML/CSS；不新增依赖。

**Spec:** docs/superpowers/specs/2026-08-20-stage6-official-business-materials-design.md

## Global Constraints

- 不读取或写入生产SQLite，不复用旧候选计划。
- 不调用AI，不生成主营关联结论。
- 不修改环境变量、迁移、依赖、服务或正式门。
- 不Git提交、不push、不部署。

### Task 1: 一键只读验收编排

- [x] 先写失败测试，覆盖完整候选、可交易性未就绪、发行人身份失败、单股材料失败和脱敏证据。
- [x] 实现 leader_business_material_live_acceptance.py。
- [x] 运行专项转绿。

### Task 2: 本地验收台报告

- [x] 先写失败测试，覆盖状态计数、官方链接转义、未就绪说明和固定非投资建议。
- [x] 实现 leader_business_material_acceptance_report.py。
- [x] 运行专项转绿。

### Task 3: 命令行与验证

- [x] 新增 run_leader_business_material_live_acceptance.py，仅打印JSON并把HTML写到/private/tmp显式路径。
- [x] 运行主营及阶段6联合测试、完整后端隔离回归、Python编译和git diff --check。
- [x] 更新唯一NEXT_CHAT_HANDOFF.md。
