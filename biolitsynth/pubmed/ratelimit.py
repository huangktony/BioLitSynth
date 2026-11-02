"""Shared async rate limiter (plan §5.3): 10 req/s with an NCBI API key, 3 req/s without."""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


class TokenBucket:
    """Capacity-one bucket: acquisitions are spaced at least 1/rate seconds apart, across all tasks.

    No bursting on purpose. NCBI counts requests per second, so a burst of 3 at t=0 followed by silence
    would still risk a 429. Clock and sleep are injectable so tests never wait.
    """

    def __init__(self, rate_per_s: float, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if rate_per_s <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate_per_s
        self._clock, self._sleep = clock, sleep
        self._next_free = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait for a slot. Returns the seconds waited."""
        async with self._lock:
            now = self._clock()
            start = max(now, self._next_free)
            self._next_free = start + self._interval
        wait = start - now
        if wait > 0:
            await self._sleep(wait)
        return max(wait, 0.0)
