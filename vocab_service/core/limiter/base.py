# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Tuple


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    retry_after: int = 0  # seconds


class RateLimiter(Protocol):
    def allow(self, key: str) -> LimitResult:
        ...
