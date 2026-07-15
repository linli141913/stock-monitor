# 提醒系统验收报告

- 固定 Fixture：通过
- 真实历史样本：暂无判断
- 样本数：6
- 漏报：0
- 误报：0
- 重复提醒：0
- 已拦截未来信息：1
- 已拒绝非当天来源：1

## 真实历史指标限制

没有独立核验过的真实历史事件集，不能计算真实覆盖率、误报率或 P95 延迟

固定 Fixture 只验证规则、时间截断和去重行为，不能代替真实历史回放。

## 样本明细

| 样本 | 结果 | 预期提醒 | 实际提醒 | 漏报 | 误报 |
| --- | --- | ---: | ---: | ---: | ---: |
| fixture-deeptech-abnormal | 通过 | 1 | 1 | 0 | 0 |
| fixture-boe-earnings | 通过 | 1 | 1 | 0 | 0 |
| fixture-stale-official-rejected | 通过 | 0 | 0 | 0 | 0 |
| fixture-sector-only-risk | 通过 | 1 | 1 | 0 | 0 |
| fixture-missing-market-data | 通过 | 0 | 0 | 0 | 0 |
| fixture-future-evidence-blocked | 通过 | 0 | 0 | 0 | 0 |
