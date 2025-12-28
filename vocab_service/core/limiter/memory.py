# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict

from core.limiter.base import LimitResult


class MemorySlidingWindowLimiter:
    """
    内存滑动窗口限流（开发期可用）：
    - 多 worker/多机器时不是全局一致，但结构上可无缝替换为 RedisLimiter。
    """
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> LimitResult:
        now = time.time()
        q = self._hits.setdefault(key, deque())

        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= self.max_requests:
            retry_after = int(q[0] + self.window_seconds - now) + 1
            return LimitResult(allowed=False, retry_after=max(retry_after, 1))

        q.append(now)
        return LimitResult(allowed=True, retry_after=0)
