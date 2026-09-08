# 阶段9三源补齐实施计划

> 目标：按 `docs/superpowers/specs/2026-09-01-stage9-three-source-completion-design.md` 补齐北交所规则、中证指数前向证据和公司行为版本化边界。

1. 在 `backend/tests/test_radar_leader_tradability_sources.py` 和 `backend/tests/test_radar_replay_source_adapters.py` 增加北交所失败测试，确认红灯后修改 `leader_tradability_sources.py` 及适配器。
2. 在 `test_radar_replay_source_adapters.py` 增加指数前向观测就绪/未就绪测试，最小修改 `replay_source_adapters.py`，保留正式历史缺口。
3. 先为公司行为强类型事件和三所覆盖校验增加测试，再修改 `replay_source_adapters.py`。
4. 为前向基线增加可注入公司行为来源，默认官方来源未完成时仍如实产生缺口，不写库。
5. 运行相关测试，再运行公开真实前向基线；若官方来源不完整，记录稳定原因而不放宽规则。
6. 运行全套验证，更新唯一 `NEXT_CHAT_HANDOFF.md`，复核 Git 与 4000/8001 服务状态。
