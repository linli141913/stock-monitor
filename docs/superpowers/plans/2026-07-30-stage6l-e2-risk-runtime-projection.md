# 阶段 6L-E2 风险候选投影到运行时研究证据实施计划

## Task 1：先写失败测试

修改 `backend/tests/test_radar_leader_runtime_inputs.py`，覆盖：

- 无 E1 投影时的风险研究缺失；
- 合法 E1 投影的单候选接入；
- 证券和批次身份冲突；
- 合同不可信时的单候选降级；
- 伪造正式标志拒绝；
- 所有路径 `risk_filter_passed=false`。

先运行运行时专项并确认因新参数或新字段不存在而失败。

## Task 2：最小运行时接入

修改 `backend/radar/leader_runtime_inputs.py`：

- 新增按证券提供的可选冻结 E1 投影映射；
- 对当前候选执行证券和批次检查；
- 校验 E1 投影合同和全部正式标志；
- 生成固定研究状态包装；
- 写入 `researchFeatures.riskCandidateProjection`；
- 把 `runtimeInputVersion` 升为 `v2`；
- 所有路径继续保留 `risk_evidence`；
- 保持评分维度和全部正式门禁不变。

## Task 3：验证与交接

运行：

```bash
cd backend
venv/bin/python -m unittest \
  tests.test_radar_leader_risk_candidate_projection \
  tests.test_radar_leader_runtime_inputs

venv/bin/python -m unittest discover \
  -s tests -p 'test_radar_leader*.py'
```

完整后端回归必须在导入前把精确生产 SQLite 连接重定向到
`/private/tmp`。最后运行相关语法检查、敏感依赖搜索和新文件差异卫生
检查，更新唯一 `NEXT_CHAT_HANDOFF.md`。不执行 Git 暂存、提交、推送或
部署。
