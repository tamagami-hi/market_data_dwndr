"""Historical candle client: windowed fetch with rate limiting + retries.

Wraps the Kite historical endpoint. The actual HTTP call is injected (async callable)
so parsing, rate limiting, and retry/backoff are unit-testable without the network.
Response ``data.candles`` rows are ``[timestamp, open, high, low, close, volume, oi?]``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.historical.limiter import TokenBucket

MAX_RETRIES = 5
BACKOFF_BASE_S = 0.5

_TZ_FIX = re.compile(r"([+-]\d{2})(\d{2})$")


class HistoricalError(Exception):
    """HTTP-level error carrying the status code for retry decisions."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status


def is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _parse_ts(value) -> int:
    """Kite timestamp -> epoch ms. Accepts ISO string or a numeric epoch (s/ms)."""
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v >= 10**12 else v * 1000  # seconds -> ms heuristic
    s = _TZ_FIX.sub(r"\1:\2", str(value))
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@dataclass(frozen=True)
class Candle:
    timestamp_unix_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int


def parse_candles(raw) -> list[Candle]:
    """Parse either a ``{"data": {"candles": [...]}}`` dict or a bare candle list."""
    if isinstance(raw, dict):
        rows = raw.get("data", {}).get("candles", [])
    else:
        rows = raw
    out: list[Candle] = []
    for row in rows:
        oi = int(row[6]) if len(row) > 6 and row[6] is not None else 0
        out.append(
            Candle(
                timestamp_unix_ms=_parse_ts(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5]),
                oi=oi,
            )
        )
    return out


# Injected HTTP call: (token, interval, from_str, to_str, oi) -> parsed JSON / candles.
Fetcher = Callable[[int, str, str, str, bool], Awaitable[object]]


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class HistoricalClient:
    def __init__(
        self,
        fetcher: Fetcher,
        limiter: TokenBucket,
        *,
        max_retries: int = MAX_RETRIES,
        backoff_base_s: float = BACKOFF_BASE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._fetch = fetcher
        self.limiter = limiter
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep

    async def fetch_window(
        self,
        token: int,
        interval: str,
        start: datetime,
        end: datetime,
        oi: bool = True,
    ) -> list[Candle]:
        """Fetch one window, rate-limited, retrying on 429/5xx with backoff."""
        attempt = 0
        while True:
            await self.limiter.acquire()
            try:
                raw = await self._fetch(token, interval, _fmt(start), _fmt(end), oi)
                return parse_candles(raw)
            except HistoricalError as exc:
                if is_retryable(exc.status) and attempt < self.max_retries:
                    await self._sleep(self.backoff_base_s * (2**attempt))
                    attempt += 1
                    continue
                raise
