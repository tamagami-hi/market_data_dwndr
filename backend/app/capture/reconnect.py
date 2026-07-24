"""Reconnect backoff + stall detection for the live WebSocket.

Mirrors algo_engine's ``ReconnectPolicy``: exponential backoff base 5 s, max 300 s,
~20 attempts before a circuit-breaker give-up. Stall detection triggers a reconnect if
no message arrives for ~30 s (docs/30-live-capture/live-data-pipeline.md).
"""

from __future__ import annotations

BASE_DELAY_S = 5.0
MAX_DELAY_S = 300.0
MAX_ATTEMPTS = 20
STALL_TIMEOUT_MS = 30_000


class ReconnectPolicy:
    """Exponential backoff with a circuit breaker."""

    def __init__(
        self,
        base_delay_s: float = BASE_DELAY_S,
        max_delay_s: float = MAX_DELAY_S,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.max_attempts = max_attempts
        self.attempt = 0

    def reset(self) -> None:
        """Call after a successful (re)connection."""
        self.attempt = 0

    def next_delay(self) -> float:
        """Advance one attempt and return the delay before it (seconds)."""
        self.attempt += 1
        delay = self.base_delay_s * (2 ** (self.attempt - 1))
        return min(delay, self.max_delay_s)

    def should_give_up(self) -> bool:
        return self.attempt >= self.max_attempts


class StallDetector:
    """Flags a stall when no message has arrived within the timeout."""

    def __init__(self, timeout_ms: int = STALL_TIMEOUT_MS) -> None:
        self.timeout_ms = timeout_ms
        self.last_message_ms: int | None = None

    def mark_message(self, now_ms: int) -> None:
        self.last_message_ms = now_ms

    def is_stalled(self, now_ms: int) -> bool:
        if self.last_message_ms is None:
            return False
        return (now_ms - self.last_message_ms) >= self.timeout_ms


class FreshnessMonitor:
    """Tracks whether live ticks are both *arriving* and *changing*.

    Two independent signals are kept:

    * **liveness** — time since the last batch arrived (any ticks at all).
    * **freshness** — time since the batch *content* last changed, using a rolling
      digest of the volatile fields (last price, volume, OI) plus the exchange
      timestamps.

    Freshness is the stronger signal. Both a half-open socket (no ticks arrive) and a
    frozen upstream feed (ticks arrive but their values never change) surface as a
    growing *content* age — which is exactly the "connected but frozen on the
    frontend" symptom. A plain liveness check misses the second case entirely.

    ``stale_after_ms`` is sourced from ``CAPTURE_STALE_SECONDS`` (see config) so the
    tolerance is tunable per deployment.
    """

    # Volatile fields whose change proves the feed is genuinely live. Exchange
    # timestamps advance even when a quote is unchanged, so they distinguish "feed
    # flowing" from "our side frozen".
    _DIGEST_FIELDS = (
        "last_price",
        "volume_traded",
        "oi",
        "exchange_timestamp",
        "last_trade_time",
    )

    def __init__(self, stale_after_ms: int = 5_000, *, start_ms: int | None = None) -> None:
        self.stale_after_ms = max(1, int(stale_after_ms))
        self.last_tick_ms: int | None = None
        self.last_change_ms: int | None = None
        self._started_ms: int | None = start_ms
        self._last_digest: int | None = None
        # Observability counters.
        self.batches_seen = 0
        self.frozen_batches = 0

    def start(self, now_ms: int) -> None:
        """Seed the reference clock so pre-first-tick staleness is measured from here."""
        if self._started_ms is None:
            self._started_ms = now_ms

    def observe(self, batch: list[dict], now_ms: int) -> None:
        """Record a delivered batch and whether its content changed."""
        if self._started_ms is None:
            self._started_ms = now_ms
        self.last_tick_ms = now_ms
        self.batches_seen += 1
        digest = self._digest(batch)
        if digest != self._last_digest:
            self._last_digest = digest
            self.last_change_ms = now_ms
        else:
            # Same content as the previous batch (including an empty batch) — no
            # fresh market data even though something may have been delivered.
            self.frozen_batches += 1

    def _digest(self, batch: list[dict]) -> int:
        parts: list[int] = []
        for tick in batch:
            if not isinstance(tick, dict):
                parts.append(hash(repr(tick)))
                continue
            token = tick.get("instrument_token")
            for field in self._DIGEST_FIELDS:
                value = tick.get(field)
                try:
                    parts.append(hash((token, field, value)))
                except TypeError:  # unhashable exotic value — fall back to repr
                    parts.append(hash((token, field, repr(value))))
        return hash(tuple(parts))

    def _content_reference_ms(self) -> int | None:
        if self.last_change_ms is not None:
            return self.last_change_ms
        return self._started_ms

    def content_age_ms(self, now_ms: int) -> int | None:
        """Milliseconds since the feed content last changed (None before startup)."""
        ref = self._content_reference_ms()
        if ref is None:
            return None
        return max(0, now_ms - ref)

    def liveness_age_ms(self, now_ms: int) -> int | None:
        """Milliseconds since the last batch of any kind arrived (None before startup)."""
        ref = self.last_tick_ms if self.last_tick_ms is not None else self._started_ms
        if ref is None:
            return None
        return max(0, now_ms - ref)

    def is_stale(self, now_ms: int) -> bool:
        """True once the content has not changed for ``stale_after_ms``."""
        age = self.content_age_ms(now_ms)
        if age is None:
            return False
        return age >= self.stale_after_ms
