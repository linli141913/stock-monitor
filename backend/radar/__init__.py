"""V5 主线雷达独立子系统。

当前提供独立数据合同、来源适配器、健康判定、迁移、仓储和默认关闭的
分频调度接入合同；未开启环境变量时不注册任务或连接生产数据库。
"""

__all__ = [
    "config",
    "contracts",
    "migrations",
    "repository",
    "run_lock",
    "runtime",
    "scheduler",
    "scoped_runner",
    "shadow_runner",
    "source_health",
    "sources",
]
