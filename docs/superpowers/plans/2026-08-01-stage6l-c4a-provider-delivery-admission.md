# 阶段6L-C4A供应商交付与批次准入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立供应商可交易性数据进入真实POC前的双层血缘、授权、静态/动态时效和整批覆盖合同。

**Architecture:** 新增独立纯内存模块，不修改C3官方来源准入。单条记录负责字段、来源、授权和时间语义，批次审计负责目标证券全集和交付批次一致性；通过只表示候选可进入POC，全部正式标志固定关闭。

**Tech Stack:** Python 3、标准库`dataclasses`/`datetime`/`enum`/`hashlib`、现有`unittest`。

## Global Constraints

- 不联网、不安装依赖、不配置账号或密钥。
- 不读取或写入生产数据库，不接入仓储、调度、API、页面或运行时。
- 不修改C3的`official_primary`唯一准入语义。
- 不执行Git提交、推送、部署或服务重载。
- 所有正式评分、门禁、可用性和状态迁移标志固定为`false`。

---

### Task 1: 供应商单条交付合同

**Files:**
- Create: `backend/radar/leader_tradability_provider_delivery.py`
- Create: `backend/tests/test_radar_leader_tradability_provider_delivery.py`

**Interfaces:**
- Consumes: `SecurityLifecycleStatus`、`TradingSessionStatus`、`PriceLimitMode`和`PriceLimitSpecialSession`。
- Produces: `ProviderLicenseEvidence`、`ProviderSourceProvenance`、`ProviderTradabilityDeliveryRecord`及稳定验证原因码。

- [x] **Step 1: 写失败测试**

覆盖合法记录、授权过期、授权范围不足、来源身份缺失、未知枚举、价格模式不一致、静态时间与动态时间分离。

- [x] **Step 2: 确认测试因模块不存在而失败**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_provider_delivery -v`

Expected: `ImportError`或`ModuleNotFoundError`指向新合同模块。

- [x] **Step 3: 实现最小冻结合同和单条校验**

实现必填文本、HTTPS、授权有效期、授权范围、枚举、`bounded/no_limit`、带时区时间和静态/动态时效校验。对象不得保存或输出凭证正文。

- [x] **Step 4: 运行专项测试**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_provider_delivery -v`

Expected: 新增单条合同测试全部通过。

### Task 2: 整批覆盖与候选准入

**Files:**
- Modify: `backend/radar/leader_tradability_provider_delivery.py`
- Modify: `backend/tests/test_radar_leader_tradability_provider_delivery.py`

**Interfaces:**
- Consumes: Task 1冻结的`ProviderTradabilityDeliveryRecord`。
- Produces: `ProviderAdmissionPolicy`、`ProviderTradabilityBatchInput`、`ProviderTradabilityBatchResult`和`audit_provider_tradability_batch(batch_input, admission_policy=...)`。

- [x] **Step 1: 写批次失败测试**

覆盖完整沪深批次、目标集合空/重复、记录缺失/重复/额外、交易日混批、供应商/合同/批次错配、动态状态过期、交付时间倒置、稳定批次ID和压缩证据。

- [x] **Step 2: 确认批次测试失败**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_provider_delivery -v`

Expected: 批次类型或审计函数尚不存在。

- [x] **Step 3: 实现整批审计**

按目标证券顺序验证每只证券恰好一条，生成缺失/重复/额外集合和稳定SHA-256批次ID；任一失败整批阻断，通过时仅返回`admissible_candidate`且正式标志保持关闭。

- [x] **Step 4: 运行专项和相关回归**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest tests.test_radar_leader_tradability_provider_delivery tests.test_radar_leader_tradability_sources tests.test_radar_leader_tradability_features -v`

Expected: 全部通过。

### Task 3: 完整验证与交接

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`

**Interfaces:**
- Consumes: Task 1-2通过的纯内存合同和测试结果。
- Produces: 唯一真实交接中的C4A状态、未完成真实POC和下一步提醒。

- [x] **Step 1: 运行阶段6龙头测试**

Run: `cd backend && PYTHONPYCACHEPREFIX=/tmp/stock-monitor-pycache venv/bin/python -m unittest discover -s tests -p 'test_radar_leader*.py'`

Expected: 全部通过。

- [x] **Step 2: 使用临时SQLite拦截运行完整后端测试**

生产数据库路径必须在导入`database`前替换为临时数据库，完成后销毁临时目录。

- [x] **Step 3: 静态检查和范围检查**

Run: `git diff --check`

Expected: 无空白错误；diff不包含依赖、环境变量、数据库、服务或生产运行时改动。

- [x] **Step 4: 更新唯一交接**

记录C4A只完成候选准入合同，真实供应商POC、授权核对和C3转换仍未完成；下一步为申请RQData免费试用并运行字段级POC。
