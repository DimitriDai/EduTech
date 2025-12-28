# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from contextlib import contextmanager


def _pid_is_alive(pid: int) -> bool:
    """
    Windows/Unix 通用的“进程是否存在”检查。
    - os.kill(pid, 0) 不会真的杀进程，只是检查是否存在/有权限
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


@contextmanager
def file_lock(lock_path: str, timeout: float = 60.0, poll: float = 0.05):
    """
    跨进程文件锁（开发/上线都稳）：
    - 使用“独占创建 lock 文件”实现（O_EXCL）
    - lock 文件内容写入 pid
    - 若 lock 文件存在：
        - 读取 pid，如果 pid 不存在 => 认为是陈旧锁，立即删除
        - pid 存在 => 等待直到 timeout
    """
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)

    start = time.time()
    fd = None

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            break

        except FileExistsError:
            # 锁存在：尝试读取锁持有者 pid
            pid = None
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                pid = int(raw) if raw.isdigit() else None
            except Exception:
                pid = None

            # 若 pid 不存在/不可读，或进程已死 => 清理陈旧锁
            if pid is None or not _pid_is_alive(pid):
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
                # 清理完立刻重试抢锁
                continue

            # pid 仍存活 => 等待
            if (time.time() - start) >= timeout:
                raise TimeoutError(f"Could not acquire lock within {timeout}s: {lock_path}")

            time.sleep(poll)

    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            os.remove(lock_path)
        except Exception:
            pass