"""In-memory sliding-window limiter per tool. Phase 3 swaps this for a Redis token bucket so
several workers share one budget; the interface stays the same."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, *, window_s: float = 60.0) -> None:
        self._window = window_s
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, per_window: int, *, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        q = self._events[key]
        while q and t - q[0] >= self._window:
            q.popleft()
        if len(q) >= per_window:
            return False
        q.append(t)
        return True
