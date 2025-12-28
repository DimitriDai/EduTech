# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from core.limiter.base import RateLimiter
from core.limiter.memory import MemorySlidingWindowLimiter


def build_rate_limiter() -> RateLimiter:
    """
    环境变量：
    - RATE_LIMIT_BACKEND=memory|redis（默认 memory）
    - RATE_LIMIT_MAX=30
    - RATE_LIMIT_WINDOW=60
    - REDIS_URL=redis://localhost:6379/0   (当 backend=redis 时必填)
    """
    backend = (os.getenv("RATE_LIMIT_BACKEND", "memory") or "memory").strip().lower()
    max_requests = int(os.getenv("RATE_LIMIT_MAX", "30"))
    window_seconds = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

    if backend == "redis":
        redis_url = (os.getenv("REDIS_URL") or "").strip()
        if not redis_url:
            # 没配 redis_url 就退回 memory（不让服务崩）
            return MemorySlidingWindowLimiter(max_requests=max_requests, window_seconds=window_seconds)

        try:
            from core.limiter.redis_limiter import RedisSlidingWindowLimiter
            return RedisSlidingWindowLimiter(redis_url=redis_url, max_requests=max_requests, window_seconds=window_seconds)
        except Exception:
            # 没装 redis 或连接失败：退回 memory
            return MemorySlidingWindowLimiter(max_requests=max_requests, window_seconds=window_seconds)

    return MemorySlidingWindowLimiter(max_requests=max_requests, window_seconds=window_seconds)
