# 阶段2B-6B1雷达影子调度基础 Implementation Plan

> **For agentic workers:** 本计划由主 Agent 在当前任务内逐项执行；项目规则禁止默认使用子 Agent。本阶段未经授权不得提交 Git。

**Goal:** 在不接入现有生产 APScheduler、不修改环境变量和生产数据库的前提下，为雷达一次性影子采集增加默认关闭的调度接入层、跨进程防重入和单实例约束。

**Architecture:** 使用显式路径的 POSIX 建议文件锁保证不同进程不能同时执行同一轮影子任务；调度任务包装器在获得锁后才冻结 `asOf`、生成运行编号并调用已有一次性执行器。注册函数只配置传入的调度器，不创建、不启动调度器，也不被 `backend/main.py` 导入。

**Tech Stack:** Python 标准库 `fcntl`、`os`、`threading`、`logging`、`dataclasses`；现有 APScheduler 调用合同；现有 `unittest`、SQLite 临时库和雷达一次性编排器。

## Global Constraints

- 不修改 `backend/main.py`、现有 APScheduler、环境变量、生产数据库、API、页面、提醒或 AI。
- 不新增依赖；不触发真实公开来源请求。
- 默认 `RADAR_ENABLED=false`、`RADAR_SHADOW_MODE=false` 时不得创建锁文件、执行来源或注册任务。
- 文件锁只使用调用方显式传入的路径；释放后保留锁文件，避免删除锁文件造成 inode 竞态。
- 同一调度器使用固定任务 ID、`max_instances=1`、`coalesce=True`、`replace_existing=False`。
- 跨进程竞争失败时本轮明确跳过，不排队、不写数据库；执行器异常必须释放锁并继续向调度器抛出。
- 使用真实项目根目录，不覆盖或清理现有未提交修改；不执行 Git 提交、推送或部署。

---

### Task 1: POSIX 跨进程锁

**Files:**
- Create: `backend/radar/run_lock.py`
- Test: `backend/tests/test_radar_scheduler.py`

**Interfaces:**
- Produces: `CrossProcessFileLock(path: PathLike)`、`acquire(blocking: bool = True) -> bool`、`release() -> None`。
- Consumes: 调用方显式锁文件路径；不读取生产路径或环境变量。

- [x] **Step 1: 写失败测试**

```python
def test_file_lock_blocks_another_process_and_can_be_reacquired(self):
    # 子进程持有同一路径锁时父进程非阻塞获取必须失败；释放后必须成功。
```

- [x] **Step 2: 验证失败**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: `ModuleNotFoundError: No module named 'radar.run_lock'`。

- [x] **Step 3: 最小实现**

```python
class CrossProcessFileLock:
    def __init__(self, path):
        self._path = os.fspath(path)
        self._fd = None
        self._state_lock = threading.Lock()

    def acquire(self, blocking=True):
        with self._state_lock:
            if self._fd is not None:
                return False
            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                operation = fcntl.LOCK_EX
                if not blocking:
                    operation |= fcntl.LOCK_NB
                fcntl.flock(fd, operation)
            except BlockingIOError:
                os.close(fd)
                return False
            self._fd = fd
            return True

    def release(self):
        with self._state_lock:
            if self._fd is None:
                return
            fd, self._fd = self._fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
```

- [x] **Step 4: 验证通过**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: 跨进程互斥、释放后重获和同实例重入测试通过。

### Task 2: 默认关闭的影子调度任务包装器

**Files:**
- Create: `backend/radar/scheduler.py`
- Modify: `backend/tests/test_radar_scheduler.py`

**Interfaces:**
- Consumes: `RadarSettings`、`execute_once(radar_run_id: str, as_of: datetime) -> ShadowRunResult`、显式锁路径和时钟。
- Produces: `ScheduledShadowJob`、`ScheduledRunOutcome`、`ScheduledRunState`。

- [x] **Step 1: 写失败测试**

```python
def test_disabled_job_does_not_create_lock_or_call_executor(self):
    executor = Mock()
    outcome = ScheduledShadowJob(
        settings=RadarSettings(),
        execute_once=executor,
        lock_path=self.lock_path,
        clock=lambda: AS_OF,
    )()
    self.assertEqual(outcome.state, ScheduledRunState.DISABLED)
    executor.assert_not_called()
    self.assertFalse(self.lock_path.exists())

def test_locked_job_skips_without_calling_executor(self):
    holder = CrossProcessFileLock(self.lock_path)
    self.assertTrue(holder.acquire(blocking=False))
    executor = Mock()
    try:
        outcome = self.enabled_job(executor)()
    finally:
        holder.release()
    self.assertEqual(outcome.state, ScheduledRunState.LOCKED)
    executor.assert_not_called()

def test_executor_failure_releases_lock_and_is_raised(self):
    executor = Mock(side_effect=RuntimeError("boom"))
    with self.assertRaisesRegex(RuntimeError, "boom"):
        self.enabled_job(executor)()
    probe = CrossProcessFileLock(self.lock_path)
    self.assertTrue(probe.acquire(blocking=False))
    probe.release()
```

- [x] **Step 2: 验证失败**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: 新接口尚未定义导致失败。

- [x] **Step 3: 最小实现**

```python
class ScheduledShadowJob:
    def __call__(self):
        if not self.settings.enabled or not self.settings.shadow_mode:
            return ScheduledRunOutcome(state=ScheduledRunState.DISABLED)
        lock = CrossProcessFileLock(self.lock_path)
        if not lock.acquire(blocking=False):
            return ScheduledRunOutcome(state=ScheduledRunState.LOCKED)
        started = time.monotonic()
        try:
            as_of = _aware_utc(self.clock())
            radar_run_id = _build_run_id(as_of)
            result = self.execute_once(radar_run_id, as_of)
        finally:
            lock.release()
        return ScheduledRunOutcome(
            state=ScheduledRunState.COMPLETED,
            radar_run_id=radar_run_id,
            result_status=result.status,
            duration_seconds=time.monotonic() - started,
        )
```

- [x] **Step 4: 使用临时SQLite完成确定性端到端测试**

测试执行器显式打开临时SQLite、应用已有迁移、注入固定证券/ETF/行情批次并调用 `OneShotShadowRunner`；完成后重新打开临时库，只核对1条运行、3条来源状态和数据库完整性。

- [x] **Step 5: 验证通过**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: 默认关闭、锁竞争、异常释放、运行编号、UTC `asOf` 和临时库端到端测试全部通过。

### Task 3: 显式 APScheduler 注册合同

**Files:**
- Modify: `backend/radar/scheduler.py`
- Modify: `backend/tests/test_radar_scheduler.py`
- Modify: `backend/radar/__init__.py`

**Interfaces:**
- Produces: `register_shadow_job(scheduler, job, settings) -> ScheduleRegistration`。
- Contract: 固定任务 ID `radar-shadow-scan`；只调用传入调度器的 `get_job` 和 `add_job`，绝不调用 `start()`。

- [x] **Step 1: 写失败测试**

```python
def test_default_settings_do_not_register_job(self):
    scheduler = Mock()
    registration = register_shadow_job(scheduler, Mock(), RadarSettings())
    self.assertEqual(registration.state, ScheduleRegistrationState.DISABLED)
    scheduler.get_job.assert_not_called()
    scheduler.add_job.assert_not_called()
    scheduler.start.assert_not_called()

def test_enabled_registration_uses_single_instance_options(self):
    scheduler = Mock()
    scheduler.get_job.return_value = None
    job = Mock()
    registration = register_shadow_job(scheduler, job, self.enabled_settings)
    self.assertEqual(registration.state, ScheduleRegistrationState.REGISTERED)
    scheduler.add_job.assert_called_once_with(
        job,
        "interval",
        id="radar-shadow-scan",
        seconds=180,
        max_instances=1,
        coalesce=True,
        replace_existing=False,
        misfire_grace_time=180,
    )
    scheduler.start.assert_not_called()

def test_existing_job_is_not_registered_twice(self):
    scheduler = Mock()
    scheduler.get_job.return_value = object()
    registration = register_shadow_job(
        scheduler,
        Mock(),
        self.enabled_settings,
    )
    self.assertEqual(
        registration.state,
        ScheduleRegistrationState.ALREADY_REGISTERED,
    )
    scheduler.add_job.assert_not_called()
```

- [x] **Step 2: 验证失败**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: 注册接口尚未定义导致失败。

- [x] **Step 3: 最小实现**

```python
scheduler.add_job(
    job,
    "interval",
    id="radar-shadow-scan",
    seconds=settings.stock_scan_interval_seconds,
    max_instances=1,
    coalesce=True,
    replace_existing=False,
    misfire_grace_time=settings.stock_scan_interval_seconds,
)
```

- [x] **Step 4: 验证通过**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler -v`

Expected: 关闭不注册、开启配置正确、重复任务不注册、调度器未启动。

### Task 4: 回归、真实状态复核与规划书衔接

**Files:**
- Modify: `docs/股票监测助手V5.0升级规划书.md`

- [x] **Step 1: 运行雷达专项与完整后端测试**

Run: `venv/bin/python -m unittest tests.test_radar_scheduler tests.test_radar_shadow_runner -v`

Run: `venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

Expected: 全部通过；没有真实来源网络调用。

- [x] **Step 2: 只读核对生产状态**

确认阶段2B-6A生产表计数未因本阶段变化，`PRAGMA quick_check=ok`、外键问题为0；4000和8001保持HTTP 200。

- [x] **Step 3: 检查修改范围**

Run: `git diff --check`

Run: `git status --short --branch`

Expected: 只有本阶段新增雷达锁、调度、测试、计划和V5规划书记录；既有未提交修改完整保留。

- [x] **Step 4: 更新V5规划书**

记录阶段2B-6B1完成内容、测试数量、未接入生产调度、回退方式，并把下一步收束为需单独授权的阶段2B-6B2正式启用评估。
