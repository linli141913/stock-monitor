# 阶段 6L-F6 两阶段候选计划与单次 Assembly 编排实施计划

**目标：** 一次完成候选计划、Plan提供器入口、单次Assembly构建和F4串联，
结束“先空构建取候选、再带输入二次构建”的测试架构。

**限制：** 只做纯内存和临时环境验证；不接生产运行时、数据库、调度、
API、页面、正式门禁、服务或Git操作。

## Task 1：并行审查与失败测试

- 主线程先写F6端到端失败测试；
- 只读子代理审查API最小边界、循环依赖和必须测试项；
- 覆盖候选计划冻结、零特征计算、Plan提供器、单次Assembly、提前阻断、
  来源错配、可信partial和正式标志关闭。

## Task 2：批量实现

- 新增`leader_runtime_candidate_plan.py`；
- 扩展F5提供器，保留Assembly入口并新增Plan入口和`candidatePlanId`；
- 新增最外层`leader_research_single_pass_orchestration.py`；
- 复用现有运行时Assembly和F4，不修改F1-F4、E3或五类公式模块。

## Task 3：完整验证与交接

运行F6专项、F1-F6联合、全部龙头、Python语法和完整后端回归。完整后端
回归必须在导入`database`前拦截默认生产路径并重定向到临时SQLite。最后
检查差异边界、固定服务、阶段开关和唯一交接文件。
