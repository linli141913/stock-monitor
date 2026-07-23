# 阶段1B-1 launchd持续运行基础 Implementation Plan

> **For agentic workers:** 本计划由主 Agent 在当前任务内逐项执行；项目规则禁止默认使用子 Agent。本阶段未经授权不得安装LaunchAgent或执行Git操作。

**Goal:** 在项目内提供可验证的FastAPI与ngrok LaunchAgent资产、安装前检查、状态、卸载和恢复screen脚本，但不安装、不加载、不停止或切换当前服务。

**Architecture:** 两个独立LaunchAgent分别调用项目内后端和ngrok包装脚本，使用固定工作目录、端口、域名、日志路径、`RunAtLoad`、`KeepAlive`和30秒重启节流。统一管理脚本在任何安装动作前检查现有端口、ngrok进程、screen会话和launchd标签；发现当前健康服务时退出，不自动终止它们。

**Tech Stack:** macOS launchd plist、zsh、`launchctl`、`screen`、`lsof`、`curl`、现有Python 3.9 `unittest`和标准库`plistlib`。

## Global Constraints

- 固定项目根目录：`/Volumes/HermesSSD/AntigravityData/量化监测-股票`。
- 后端固定监听 `127.0.0.1:8001`；不创建前端LaunchAgent，前端生产仍由Vercel承载。
- ngrok继续使用项目根目录 `ngrok` 3.39.9、现有固定域名和 `/Users/linjian/Library/Application Support/ngrok/ngrok.yml`；不读取或复制配置内容。
- 不修改`.env`、数据库、端口、现有APScheduler、雷达开关或系统启动项。
- 本阶段不得执行 `launchctl bootstrap/bootout`、复制到 `~/Library/LaunchAgents`、停止screen、重启服务或创建系统日志目录。
- 安装脚本发现8001已监听、ngrok已运行、同名screen会话存在或launchd标签已加载时必须拒绝安装。
- 卸载脚本只卸载对应标签并把已安装plist移动到可恢复归档，不删除数据库、日志、配置或项目文件。
- 不新增依赖；没有shellcheck时使用`zsh -n`、`plutil -lint`和Python合同测试。
- 不执行Git add、commit、push、部署或生产写入。

---

### Task 1: 先写launchd资产合同测试

**Files:**
- Create: `backend/tests/test_launchd_assets.py`

**Interfaces:**
- Consumes: 计划中的两个plist和三个zsh脚本。
- Produces: 对标签、路径、端口、重启策略、日志、敏感字段、脚本语法和管理命令的确定性测试。

- [x] **Step 1: 写失败测试**

测试必须检查：

```python
self.assertEqual(backend["Label"], "com.linjian.stock-monitor.fastapi")
self.assertEqual(ngrok["Label"], "com.linjian.stock-monitor.ngrok")
self.assertTrue(backend["RunAtLoad"])
self.assertTrue(backend["KeepAlive"])
self.assertEqual(backend["ThrottleInterval"], 30)
self.assertNotIn("authtoken", serialized_assets.lower())
self.assertNotIn("BACKEND_API_TOKEN", serialized_assets)
```

并对每个脚本执行 `/bin/zsh -n <script>`，对管理脚本的 `validate` 命令断言退出码为0。

- [x] **Step 2: 验证失败**

Run: `venv/bin/python -m unittest tests.test_launchd_assets -v`

Expected: 因 `ops/launchd` 资产尚不存在而失败。

### Task 2: 后端与ngrok包装脚本

**Files:**
- Create: `ops/launchd/run-backend.sh`
- Create: `ops/launchd/run-ngrok.sh`

**Interfaces:**
- `run-backend.sh [--check]`：验证解释器、入口和8001端口；端口已有监听时退出75，正常模式使用`exec`运行`backend/main.py`。
- `run-ngrok.sh [--check]`：验证ngrok二进制和配置文件；已有ngrok进程时退出75；正常模式等待8001健康后使用现有固定域名启动隧道。

- [x] **Step 1: 实现后端包装器**

```zsh
if /usr/sbin/lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
  print -u2 -- "8001端口已有监听，拒绝启动第二个FastAPI实例"
  exit 75
fi
[[ "${1:-}" == "--check" ]] && exit 0
cd "$BACKEND_DIR"
exec "$PYTHON_BIN" main.py
```

- [x] **Step 2: 实现ngrok包装器**

```zsh
if /usr/bin/pgrep -x ngrok >/dev/null 2>&1; then
  print -u2 -- "ngrok已运行，拒绝启动第二个隧道"
  exit 75
fi
[[ "${1:-}" == "--check" ]] && exit 0
exec "$NGROK_BIN" http "127.0.0.1:8001" \
  "--url=$NGROK_DOMAIN" "--config=$NGROK_CONFIG"
```

- [x] **Step 3: 语法验证**

Run: `/bin/zsh -n ops/launchd/run-backend.sh ops/launchd/run-ngrok.sh`

Expected: 退出码0。

### Task 3: 两个独立LaunchAgent plist

**Files:**
- Create: `ops/launchd/com.linjian.stock-monitor.fastapi.plist`
- Create: `ops/launchd/com.linjian.stock-monitor.ngrok.plist`

**Interfaces:**
- Produces: 标签 `com.linjian.stock-monitor.fastapi` 和 `com.linjian.stock-monitor.ngrok`；避开系统中未加载但已存在的历史 `com.linjian.stock-monitor.backend.plist`。
- ProgramArguments只调用 `/bin/zsh` 和对应包装脚本；不含密钥或Token。

- [x] **Step 1: 写plist**

两个plist必须包含：`RunAtLoad=true`、`KeepAlive=true`、`ThrottleInterval=30`、固定`WorkingDirectory`、独立stdout/stderr路径和最小PATH；后端额外设置`PYTHONUNBUFFERED=1`。

- [x] **Step 2: plist验证**

Run: `/usr/bin/plutil -lint ops/launchd/com.linjian.stock-monitor.fastapi.plist ops/launchd/com.linjian.stock-monitor.ngrok.plist`

Expected: 两个文件均为 `OK`。

### Task 4: 安装、状态、卸载和screen回退管理脚本

**Files:**
- Create: `ops/launchd/manage.sh`

**Interfaces:**
- `validate`：只验证本地资产和运行依赖。
- `preflight`：只读检查安装冲突，当前screen服务存在时退出75。
- `install`：未来授权后复制plist、按后端再ngrok顺序bootstrap；失败时自动bootout本阶段标签并归档plist。
- `status`：只读显示launchd、端口、ngrok和screen状态。
- `uninstall`：bootout两个标签并把plist移动到时间戳归档。
- `rollback-screen`：卸载launchd后，在确认无冲突时恢复现有两个screen启动方式。

- [x] **Step 1: 实现安全检查**

`install`必须在任何`mkdir`、`install`或`launchctl`调用前执行`validate`和`preflight`；不得包含`kill`、`pkill`或`screen -X quit`。

- [x] **Step 2: 实现安装失败回退**

后端bootstrap后最多等待30秒检查`http://127.0.0.1:8001/docs`；ngrokbootstrap失败时按ngrok、后端顺序bootout，并把已复制plist移动到归档目录。

- [x] **Step 3: 实现可恢复卸载**

卸载不得`rm` plist；统一移动到 `/Users/linjian/Library/LaunchAgents/stock-monitor-archive/<timestamp>/`。

- [x] **Step 4: 实现screen回退**

回退使用原会话名 `stock-monitor-backend` 和 `stock-monitor-ngrok`，后端恢复健康后再启动ngrok；发现现有同名会话、8001监听或ngrok进程时拒绝重复启动。

### Task 5: 测试、只读现场验证和文档衔接

**Files:**
- Modify: `README.md`
- Modify: `docs/股票监测助手V5.0升级规划书.md`

- [x] **Step 1: 专项和完整测试**

Run: `venv/bin/python -m unittest tests.test_launchd_assets -v`

Run: `venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

Expected: 全部通过。

- [x] **Step 2: 现场安全验证**

Run: `ops/launchd/manage.sh validate`

Expected: 退出码0。

Run: `ops/launchd/manage.sh preflight`

Expected: 因当前健康screen服务占用8001/ngrok而退出75，且screen会话、launchd标签、端口和服务不变。

- [x] **Step 3: 服务与Git边界**

确认4000和8001仍为HTTP 200；两个launchd标签仍未加载；`git diff --check`无错误；既有未提交修改完整保留。

- [x] **Step 4: 更新长期上下文**

README只记录命令和“当前未安装”；V5规划书记录阶段1B-1完成、验证、回退与阶段1B-2需要单独授权的真实安装/切换范围。
