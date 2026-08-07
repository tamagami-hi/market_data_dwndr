"""Subscription planning: how many tokens we need, and whether one socket can carry them.

The token universe was previously a side effect — a deduplicated set union computed inline
in ``bootstrap_capture`` and handed straight to a single ``TickerBridge``. Nothing knew how
close that number sat to the broker's per-connection ceiling, so adding a data domain was
a guess: a rejected ``subscribe()`` surfaces only as one log line and a silently dead feed
(``TickerBridge._on_connect`` catches the failure and sets ``connected = False``).

This module makes the count and the headroom explicit, computed from the instruments
actually resolved at bootstrap rather than from a remembered estimate. The decision it
drives is deliberately conservative: **stay on one connection unless the numbers say
otherwise.** A second socket doubles the reconnect surface and splits the ingest ordering,
which is a real cost to pay only when capacity demands it.

Sharding is *planned* here but not wired into the bridge: with the present universe the
plan is always a single shard, and building multi-socket ingestion that nothing exercises
would be speculative. When a plan first reports more than one shard, that is the signal to
wire it — and ``over_safe_threshold`` / ``exceeds_broker_capacity`` are surfaced in
telemetry so the signal arrives before a subscribe is rejected rather than after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Kite Connect allows 3000 instruments per websocket and 3 connections per API key. Both
# are configurable because they are the broker's numbers, not ours.
DEFAULT_SUBSCRIPTION_LIMIT = 3_000
DEFAULT_SAFETY_MARGIN_PCT = 10.0
DEFAULT_MAX_CONNECTIONS = 3


@dataclass(frozen=True)
class SubscriptionPlan:
    """The resolved token universe plus the capacity decision that follows from it."""

    tokens: tuple[int, ...]
    limit: int = DEFAULT_SUBSCRIPTION_LIMIT
    safety_margin_pct: float = DEFAULT_SAFETY_MARGIN_PCT
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    # Where the tokens came from, e.g. {"NIFTY": 203, "STOCKS": 816, "shared": 1}.
    # Kept so a capacity problem can be attributed to a domain instead of guessed at.
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    @property
    def safe_limit(self) -> int:
        """The ceiling we actually plan against, held below the broker's hard limit."""
        margin = max(0.0, min(100.0, self.safety_margin_pct))
        return max(1, int(self.limit * (1.0 - margin / 100.0)))

    @property
    def remaining(self) -> int:
        """Tokens that could still be added before crossing the safe threshold."""
        return self.safe_limit - self.token_count

    @property
    def utilisation_pct(self) -> float:
        """Share of the broker's hard limit in use (not of the safe threshold)."""
        if self.limit <= 0:
            return 0.0
        return round(self.token_count / self.limit * 100, 2)

    @property
    def over_safe_threshold(self) -> bool:
        return self.token_count > self.safe_limit

    @property
    def shard_count(self) -> int:
        if not self.over_safe_threshold:
            return 1
        return -(-self.token_count // self.safe_limit)  # ceil

    @property
    def exceeds_broker_capacity(self) -> bool:
        """True when even the maximum number of connections could not carry the universe."""
        return self.shard_count > self.max_connections

    def shards(self) -> tuple[tuple[int, ...], ...]:
        """Balanced token groups, one per connection. A single shard when within budget."""
        count = self.shard_count
        if count <= 1:
            return (self.tokens,)
        per_shard = -(-self.token_count // count)
        return tuple(
            self.tokens[start : start + per_shard]
            for start in range(0, self.token_count, per_shard)
        )

    def as_telemetry(self) -> dict:
        return {
            "subscribed_tokens": self.token_count,
            "subscription_limit": self.limit,
            "subscription_safe_limit": self.safe_limit,
            "subscription_remaining": self.remaining,
            "subscription_utilisation_pct": self.utilisation_pct,
            "subscription_shards": self.shard_count,
            "subscription_over_threshold": self.over_safe_threshold,
            "subscription_exceeds_capacity": self.exceeds_broker_capacity,
            "subscription_breakdown": dict(self.breakdown),
        }


def plan_subscriptions(
    token_groups: dict[str, list[int] | tuple[int, ...] | set[int]],
    *,
    limit: int = DEFAULT_SUBSCRIPTION_LIMIT,
    safety_margin_pct: float = DEFAULT_SAFETY_MARGIN_PCT,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
) -> SubscriptionPlan:
    """Build a plan from per-domain token groups.

    Groups are deduplicated into one subscription set — a token shared by several domains
    (India VIX feeds every index table) is subscribed once. The breakdown records each
    domain's own count *before* deduplication, so the numbers explain the universe rather
    than just totalling it; ``sum(breakdown.values())`` may therefore exceed
    ``token_count``, which is the honest picture.
    """
    union: set[int] = set()
    breakdown: dict[str, int] = {}
    for name, tokens in token_groups.items():
        group = {int(token) for token in tokens}
        breakdown[name] = len(group)
        union |= group

    plan = SubscriptionPlan(
        tokens=tuple(sorted(union)),
        limit=max(1, int(limit)),
        safety_margin_pct=float(safety_margin_pct),
        max_connections=max(1, int(max_connections)),
        breakdown=breakdown,
    )

    if plan.exceeds_broker_capacity:
        logger.critical(
            "subscription universe of %d tokens needs %d connections but only %d are "
            "available; the broker will reject part of this subscription (breakdown: %s)",
            plan.token_count,
            plan.shard_count,
            plan.max_connections,
            plan.breakdown,
        )
    elif plan.over_safe_threshold:
        logger.error(
            "subscription universe of %d tokens exceeds the safe threshold of %d "
            "(limit %d); %d connections are required — sharding is NOT yet wired into the "
            "ticker bridge, so subscribe may be rejected (breakdown: %s)",
            plan.token_count,
            plan.safe_limit,
            plan.limit,
            plan.shard_count,
            plan.breakdown,
        )
    else:
        logger.info(
            "subscription universe: %d tokens, %.1f%% of the broker limit (%d), %d "
            "remaining below the safe threshold of %d — one connection (breakdown: %s)",
            plan.token_count,
            plan.utilisation_pct,
            plan.limit,
            plan.remaining,
            plan.safe_limit,
            plan.breakdown,
        )
    return plan


__all__ = [
    "DEFAULT_MAX_CONNECTIONS",
    "DEFAULT_SAFETY_MARGIN_PCT",
    "DEFAULT_SUBSCRIPTION_LIMIT",
    "SubscriptionPlan",
    "plan_subscriptions",
]
