# 量化监测-股票

准实时股票监测、行业资讯聚合与 AI 归因分析工具。项目只分析局势、影响、风险和证据，不提供自动交易、买卖指令或收益承诺。

## 项目结构

- `stock-monitor/`：Next.js 16 前端。
- `backend/`：FastAPI 后端、公开数据抓取、AI 分析、定时任务和 SQLite 运行数据。
- `AGENTS.md`：项目开发与协作规则。

## 环境要求

- Node.js 20.19 或更高版本。
- Python 3.9 或更高版本。
- npm。

## 环境变量

复制模板后填写本地真实值：

```bash
cp backend/.env.example backend/.env
cp stock-monitor/.env.example stock-monitor/.env.local
```

真实 `.env`、数据库和日志均属于本地运行数据，不得提交到 Git。

## 安装依赖

后端：

```bash
cd backend
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

前端：

```bash
cd stock-monitor
npm ci
```

## 本地启动

后端固定监听 `http://127.0.0.1:8001`：

```bash
cd backend
./venv/bin/python main.py
```

前端固定监听 `http://localhost:4000`：

```bash
cd stock-monitor
npm run dev
```

前端通过 `/api/backend/...` 服务端代理访问后端，浏览器端不保存后端私密 Token。

## macOS launchd资产（阶段1B-2已启用）

项目已在 `ops/launchd/` 提供FastAPI和ngrok的独立LaunchAgent模板、启动包装器和统一管理脚本。运行包装器会在安装时复制到 `~/Library/Application Support/stock-monitor/launchd/`，避免launchd直接执行外置盘脚本；卸载或失败时会与plist一起归档，不直接删除。

2026-07-18首次切换曾因macOS拒绝launchd访问外置盘而完整回退。用户随后授权 `/bin/zsh` 的“完全磁盘访问”权限，只读探针确认它可以读取项目并执行外置盘虚拟环境Python后，阶段1B-2重新切换成功。FastAPI和ngrok现由 `com.linjian.stock-monitor.fastapi`、`com.linjian.stock-monitor.ngrok` 两个LaunchAgent独立托管；原后端和ngrok `screen` 会话已退出，前端 `screen` 会话保持不变。

只读或静态检查：

```bash
ops/launchd/manage.sh validate
ops/launchd/manage.sh status
ops/launchd/manage.sh preflight
```

当前两个LaunchAgent已加载，因此 `status` 应显示FastAPI、ngrok、8001端口和运行脚本均正常。`preflight` 会因正式服务已存在而拒绝重复安装；`install`、`uninstall` 和 `rollback-screen` 会修改本机服务状态，只能在单独授权后执行。

阶段2B-6B2B为FastAPI增加了限定范围的受控重载和雷达影子开关：

```bash
ops/launchd/manage.sh reload-backend
ops/launchd/manage.sh enable-radar
ops/launchd/manage.sh disable-radar
```

以上三个命令都会修改本机服务状态，只能在单独授权后执行。它们只重载FastAPI，不停止ngrok和前端；执行前会校验现有服务，归档当前已安装的plist与运行包装器，失败时自动恢复原资产。`enable-radar` 使用显式非敏感配置开启影子调度，`disable-radar` 使用项目内固定回退plist关闭雷达，不删除已经产生的雷达历史。雷达跨进程锁目录固定为 `~/Library/Application Support/stock-monitor/runtime/`，目录权限为0700。

运行这些LaunchAgent的本机前置条件是保留 `/bin/zsh` 的“完全磁盘访问”权限。若以后关闭该权限或更换项目磁盘，应先做只读访问探针，不得直接反复重启服务。

## 主线雷达生产影子调度（ETF名册R1正式复验已通过）

2026-07-18已在备份和完整性校验通过后启用主线雷达影子调度。当前显式配置为 `RADAR_ENABLED=true`、`RADAR_SHADOW_MODE=true`、股票行情180秒、ETF行情300秒；证券与ETF名册只在A股交易日允许的时段检查，且每日成功后不重复请求。三个任务共用稳定跨进程锁，休市、午间、非交易时段或交易日历未知时会在创建锁、连接生产数据库和请求公开来源之前跳过。

本次日期为周六，已真实验收三个任务均注册并返回 `skipped / market_holiday`、FastAPI保持单实例、ngrok不中断、正式外部健康端点四项健康，以及先关闭再重新开启雷达的完整回退路径。启用前后雷达表行数一致，证明周末门禁没有产生影子写入。股票180秒、ETF300秒、名册每日一次、来源时间、覆盖率和连续写入仍必须在下一个A股交易时段续验；周末结果不能替代交易时段验收。

2026-07-21 09:14，R1修复上线后的首个合法名册窗口已自然产生 `succeeded`：证券主档和ETF组合名册均为 `healthy`且允许写入新状态，行覆盖率和必填字段覆盖率均为1.0，来源问题为0。ETF返回1,893条，其中上交所880条全部使用 `sourceReportDate=2026-07-20`，深交所1,013条。阶段2B-6B2R1正式复验通过。

2026-07-21，阶段3-1评分合同审核、阶段3-2A市场环境输入源骨架、阶段3-2B主行业分类来源POC、阶段3-2C中上协主行业影子适配器和阶段3-3A行业当期原始特征骨架均已完成。阶段3-3A只消费同一 `radarRunId` 和 `asOf` 的行业内存快照、A股股票池与腾讯行情，按冻结公式计算等权收益、总市值加权收益、剔除第一贡献股收益、上涨广度和成交额原始值；ETF、行业未确认股票、未知行情、重复、缺失、过期和未来时间均不会进入行业分母。真实只读POC中5,529只行情全部返回，5,440只已确认行业映射形成83个大类，其中81个大类全部当期原始特征可用，另2个大类因各只有1只成分而按门禁保持不可用；成交额和总市值单位继续标记为 `unverified`，批次 `formalUsable=false`。当前仍没有新增行业表、生产数据库写入、调度接入、雷达API或页面，也不会生成历史放量、行业分数、排名或八阶段状态。下一步建议单独实施阶段3-3B行业版本与当期特征存储迁移代码，只在内存或临时SQLite验证新迁移，不直接修改生产库。

交易日续验使用 `backend/validate_radar_trading_day.py` 只读检查生产SQLite，不修改表结构或业务数据。三个任务的首次相位已错开为股票立即、ETF延后30秒、名册延后90秒，避免180秒、300秒和1800秒固定周期共同争抢跨进程锁；后续周期本身没有改变。

交易日前先冻结一次基线，交易日收盘后再作最终判定：

```bash
cd backend
./venv/bin/python validate_radar_trading_day.py baseline \
  --output data/radar-validation/<基线文件>.json
./venv/bin/python validate_radar_trading_day.py validate \
  --date YYYY-MM-DD \
  --baseline data/radar-validation/<基线文件>.json \
  --output data/radar-validation/<验收报告>.json
```

命令退出码为0表示全部通过，1表示存在确定失败，2表示证据尚不足。盘中报告即使现有样本正常也必须保持 `pending`；只有收盘后覆盖上午、下午两个交易时段，并通过名册每日一次、股票180秒、ETF300秒、来源时间、覆盖率、缺失语义、单实例、锁权限和数据库增量检查，才允许返回 `passed`。基线和报告位于已忽略的 `backend/data/`，不提交Git。

2026-07-20首个完整交易日最终验收曾因上交所ETF统计日期口径返回 `failed`：股票行情80轮、ETF行情48轮、行情来源合同、上午和下午连续性、单实例、锁权限、数据库完整性与增量均已通过；该失败报告继续作为历史证据保留。统计日期口径随后已修复，并由上方2026-07-21合法名册窗口完成R1正式复验，因此该历史失败不再阻断阶段3。

## 外部健康监测（阶段1B-3已完成在线闭环）

阶段1B-3在前端新增脱敏健康端点 `/api/health`。它由Vercel服务端读取受Token保护的后端健康接口，只对外返回Vercel、ngrok隧道、FastAPI和后台任务四项状态及检查时间，不返回监测列表数量、未读数量、邮箱配置、任务错误或密钥。

`.github/workflows/external-health-monitor.yml` 计划由GitHub托管运行器每5分钟从本机之外检查一次；同一轮连续两次失败、间隔60秒后才判定故障。故障只维护一个GitHub事项，持续失败时更新同一事项，恢复后自动评论并关闭。该工作流只申请仓库内容读取和事项写入权限，不需要后端Token、模型密钥或新增第三方凭证。

本地探针可以这样验证：

```bash
python3 ops/health-monitor/probe.py \
  --url https://stock-monitor-murex-one.vercel.app/api/health \
  --attempts 2 \
  --delay 60
```

2026-07-18已完成Git提交、推送、Vercel生产发布和在线故障闭环：远端 `main` 为 `ec38a85`，生产部署 `dpl_FFH9nVaEWR7XBUu44TkUR5YcyEtc` 状态为 `READY`。正式 `/api/health` 返回200，四项组件均为 `healthy`；手动正常运行 `#1` 成功，无效地址运行 `#2` 在连续两次失败后创建事项 `#1`，随后计划运行 `#3` 使用正式地址成功，`github-actions[bot]` 自动评论恢复并关闭事项。当前工作流存在一条不影响运行的 `actions/checkout@v4` Node.js运行时弃用警告，后续可单独升级。回退时禁用工作流并回退前端路由即可，不涉及数据库、后端进程或环境变量。

## 验证命令

后端：

```bash
cd backend
./venv/bin/python -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/tmp/codex-pycache ./venv/bin/python -m py_compile market_calendar.py database.py main.py
./venv/bin/python -m pip check
```

前端：

```bash
cd stock-monitor
npx tsc --noEmit
npm run lint
npm run build
```

## 部署前备份本地运行数据

后端运行期间也可以执行一致性备份；脚本会保留旧文件，并为新备份生成 SHA-256 校验文件：

```bash
cd backend
./venv/bin/python backup_database.py
```

默认备份到 `backend/data/backups/`。数据库、备份和校验文件都属于本地运行数据，不提交到 Git。

## 数据真实性

- 行情、新闻、公告和财务信息必须来自可追溯公开来源。
- 计算数据需要明确口径。
- AI 内容必须标记为模型或规则判断。
- 数据缺失或抓取失败时返回 `null`、空数组或明确失败状态，禁止用 Mock 或随机数填充生产页面。
