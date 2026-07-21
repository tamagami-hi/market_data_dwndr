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
