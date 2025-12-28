# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Optional

from core.limiter.base import LimitResult


class RedisSlidingWindowLimiter:
    """
    Redis 滑动窗口限流（上线级，跨进程/多 worker/多机器一致）
    依赖：pip install redis

    使用 ZSET 记录时间戳：
    - key: rate:{ip}
    - member: now_ms
    - score: now_ms
    - 清理 window 外，再计算 zcard
    """
    def __init__(self, redis_url: str, max_requests: int = 60, window_seconds: int = 60):
        self.redis_url = redis_url
        self.max_requests = max_requests
        self.window_seconds = window_seconds

        # 延迟导入，避免你没装 redis 时项目直接崩
        import redis  # type: ignore
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)

    def allow(self, key: str) -> LimitResult:
        now_ms = int(time.time() * 1000)
        win_ms = self.window_seconds * 1000
        cutoff = now_ms - win_ms

        zkey = f"rate:{key}"

        pipe = self._redis.pipeline(transaction=True)
        # 1) 清理窗口外
        pipe.zremrangebyscore(zkey, 0, cutoff)
        # 2) 插入本次请求
        pipe.zadd(zkey, {str(now_ms): now_ms})
        # 3) 读取数量
        pipe.zcard(zkey)
        # 4) 设置过期（防止 key 长期堆积）
        pipe.expire(zkey, self.window_seconds + 5)
        _, _, count, _ = pipe.execute()

        if count > self.max_requests:
            # 再查最早的一个时间戳，算 retry_after
            oldest = self._redis.zrange(zkey, 0, 0, withscores=True)
            if oldest:
                oldest_ms = int(oldest[0][1])
                retry_after = int((oldest_ms + win_ms - now_ms) / 1000) + 1
            else:
                retry_after = 1
            return LimitResult(allowed=False, retry_after=max(retry_after, 1))

        return LimitResult(allowed=True, retry_after=0)
