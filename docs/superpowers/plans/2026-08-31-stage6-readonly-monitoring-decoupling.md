# 阶段6只读监测与人工审批解耦实施计划

> **执行约束：** 本计划在当前唯一真实工作树内执行；保留全部未提交改动，不创建新工作树，不提交 Git，不读写生产 SQLite，不重载服务。

**目标：** 让真实行情和已确认行业映射形成的观察候选可以直接只读展示，人工审核不再阻塞监测页面；正式评分、状态迁移和自动交易能力仍保持关闭。

**架构：** 在现有运行时候选计划与正式五源研究链之间增加一个独立、带哈希校验的 JSON 观察仓。运行时在候选计划形成后立即发布观察快照，随后继续既有正式研究门；只读 API 优先展示正式影子梯队，缺少正式梯队时安全回退到观察候选。D2 数据继续作为官方公告扫描来源，但前端不再提供 D8 人工审批工作台。

**技术栈：** Python、FastAPI、Pydantic 2、React 19、Next.js 16、TypeScript、CSS Modules。

## 全局约束

- 观察候选不得生成或推算龙头分数，不得冒充预备/候选/已确认正式状态。
- `formalStateEnabled`、`formalUsable`、`stateTransitionAllowed` 始终为 `false`。
- 人工确认仅保留为未来可选内部能力，`humanApprovalRequired` 必须为 `false`。
- 只展示真实来源、真实时间、覆盖范围、缺失和失败；不使用 Fixture、Mock 或旧候选补生产空白。
- 保持 `/api/radar/leaders` 和 `/api/radar/stocks/{symbol}` 现有字段兼容，只增加字段和观察回退语义。

### 任务1：观察快照仓

**文件：**

- 新建 `backend/radar/leader_observation_store.py`
- 新建 `backend/tests/test_radar_leader_observation_store.py`

- [x] 先写失败测试：真实候选发布/读取、空仓、哈希篡改、未来时间、非法代码和人工审批恒为 false。
- [x] 运行测试确认因模块缺失失败。
- [x] 实现原子写入、清单哈希和严格读取合同。
- [x] 运行测试确认通过。

### 任务2：运行时提前发布真实观察候选

**文件：**

- 修改 `backend/radar/runtime.py`
- 修改 `backend/tests/test_radar_runtime.py`

- [x] 先写失败测试：候选计划 ready 后即使正式研究输入 blocked，观察发布器仍收到同轮计划、真实行情和证券主档。
- [x] 运行测试确认失败原因是发布器尚未接入。
- [x] 以可注入发布器接入运行时；正式门逻辑和四个关闭状态不变。
- [x] 运行相关运行时测试。

### 任务3：扩展稳定只读 API 合同

**文件：**

- 修改 `backend/radar/api_contracts.py`
- 修改 `backend/radar/read_service.py`
- 修改 `backend/radar/api.py`
- 修改 `backend/tests/test_radar_leader_api.py`

- [x] 先写失败测试：无正式快照但有观察快照时返回 `available/partial`；空仓、过期、失败、未启用、个股命中和未命中语义明确。
- [x] 运行测试确认新合同缺失。
- [x] 增加观察模块与条目强类型，注入只读加载器并实现回退；保持正式梯队优先。
- [x] 运行后端相关测试。

### 任务4：主线雷达页面改为监测语义

**文件：**

- 修改 `stock-monitor/src/types/radar.ts`
- 修改 `stock-monitor/src/app/radar/page.tsx`
- 修改 `stock-monitor/src/components/radar/LeaderObservationPanel.tsx`
- 按需修改 `stock-monitor/src/components/radar/Radar.module.css`

- [x] 增加观察合同类型并由类型检查先暴露未实现引用。
- [x] 展示“观察候选（非评级）”、真实行情时间、行业内排名、来源范围和不完整原因。
- [x] 将 D2 区域改为“官方公告扫描”，只保留来源链接与覆盖说明；删除页面上的 D8 表单、提交、预检和“等待人工批准”文案。
- [x] 保留手动加入监测列表按钮，不自动加入。
- [x] 运行 TypeScript、ESLint 和 Next.js build。

### 任务5：整体验证与交接

- [x] 运行受影响后端测试和精确隔离生产库的完整后端测试。
- [x] 运行 Python 编译、`pip check`、`git diff --check`。
- [x] 复核 Git、4000/8001 服务状态，不重载服务。
- [x] 更新唯一 `NEXT_CHAT_HANDOFF.md`，把人工审批从产品阻塞项改为可选内部能力。
