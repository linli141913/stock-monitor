"""雷达任务的POSIX跨进程建议锁。"""

from __future__ import annotations

import errno
import fcntl
import os
import threading
from typing import Optional, Union


PathLike = Union[str, os.PathLike]


class CrossProcessFileLock:
    """使用显式锁文件路径阻止多个进程同时执行雷达任务。

    释放锁时只解除建议锁并关闭文件描述符，不删除锁文件。删除仍被其他
    进程引用的锁文件会产生不同inode并破坏互斥，因此锁文件应长期保留。
    """

    def __init__(self, path: PathLike):
        self._path = os.fspath(path)
        if not self._path:
            raise ValueError("雷达任务锁路径不能为空")
        self._fd: Optional[int] = None
        self._state_lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    def acquire(self, blocking: bool = True) -> bool:
        """获取独占锁；非阻塞竞争失败时返回False。"""

        with self._state_lock:
            if self._fd is not None:
                return False

            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.fchmod(fd, 0o600)
                operation = fcntl.LOCK_EX
                if not blocking:
                    operation |= fcntl.LOCK_NB
                fcntl.flock(fd, operation)
            except OSError as exc:
                os.close(fd)
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return False
                raise

            self._fd = fd
            return True

    def release(self) -> None:
        """释放当前实例持有的锁；重复调用安全。"""

        with self._state_lock:
            if self._fd is None:
                return
            fd, self._fd = self._fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
