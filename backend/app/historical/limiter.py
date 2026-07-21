"""Shared token-bucket rate limiter for historical requests.

Configurable requests/second with a burst; shared across all download tasks. The clock
is injectable so tests are deterministic (no real sleeping).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_second: float,
        burst: int = 8,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self.rate = rate_per_second
        self.burst = burst
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)

    async def acquire(self) -> None:
        """Acquire one token, waiting (async) if the bucket is empty."""
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                needed = (1.0 - self._tokens) / self.rate
                await self._sleep(needed)
                self._refill()
            self._tokens -= 1.0

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens
