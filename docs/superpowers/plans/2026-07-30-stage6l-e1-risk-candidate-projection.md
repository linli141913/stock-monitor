# 阶段 6L-E1 风险研究证据到候选输入的只读投影实施计划

## 目标

新增一个纯计算投影层，把经过 D9 重算验证的 D8 当前研究包和差异转换为
候选研究证据，同时固定关闭正式风险门禁。

## Task 1：先写失败测试

**新增：**

- `backend/tests/test_radar_leader_risk_candidate_projection.py`

覆盖：

- 合法两版本 D9 链的当前包、证据 ID、差异和门禁缺口投影；
- 候选证券、发行人和 `as_of` 校验；
- 伪造 D9 结果与畸形输入拒绝；
- D9 历史不足和来源不可验证传播；
- 正式标志固定关闭、对象不可变和敏感文本不进入 `repr`。

先运行专项测试并确认因模块缺失而失败。

## Task 2：实现最小投影模块

**新增：**

- `backend/radar/leader_risk_candidate_projection.py`

实现：

- 冻结输入、投影和结果合同；
- 内部重跑 D9 并比较调用方结果；
- 候选身份和未来数据检查；
- JSON 兼容的证据、差异与门禁缺口投影；
- 固定关闭的风险过滤和正式应用标志。

不修改运行时、评分、仓储、数据库、API 或页面。

## Task 3：验证与交接

运行：

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_candidate_projection

venv/bin/python -m unittest \
  tests.test_radar_leader_risk_invalidation_features \
  tests.test_radar_leader_risk_official_source \
  tests.test_radar_leader_risk_document_facts \
  tests.test_radar_leader_risk_document_content \
  tests.test_radar_leader_risk_review_artifacts \
  tests.test_radar_leader_risk_review_replay \
  tests.test_radar_leader_risk_supplemented_relation \
  tests.test_radar_leader_risk_evidence_bundle \
  tests.test_radar_leader_risk_evidence_bundle_audit \
  tests.test_radar_leader_risk_candidate_projection

venv/bin/python -m unittest discover \
  -s tests -p 'test_radar_leader*.py'
```

完整后端回归必须在导入前把默认生产 SQLite 连接重定向到临时文件。最后
运行相关语法检查、敏感依赖搜索和 `git diff --check`，再更新唯一
`NEXT_CHAT_HANDOFF.md`。不执行 Git 暂存、提交、推送或部署。
