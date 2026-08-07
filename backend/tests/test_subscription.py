"""Tests for subscription planning and broker capacity headroom."""

from __future__ import annotations

from app.capture.subscription import SubscriptionPlan, plan_subscriptions


def _tokens(start: int, count: int) -> list[int]:
    return list(range(start, start + count))


def test_groups_are_deduplicated_into_one_subscription_set():
    """India VIX feeds every index table but must only be subscribed once."""
    plan = plan_subscriptions(
        {
            "NIFTY": [1, 2, 264969],
            "BANKNIFTY": [3, 4, 264969],
            "STOCKS": [5, 6],
        }
    )

    assert plan.token_count == 7  # not 8: the shared VIX token collapses
    assert plan.tokens == (1, 2, 3, 4, 5, 6, 264969)


def test_the_breakdown_records_each_domain_before_deduplication():
    """Attribution matters more than a tidy total when a capacity wall appears."""
    plan = plan_subscriptions({"NIFTY": [1, 264969], "BANKNIFTY": [2, 264969]})

    assert plan.breakdown == {"NIFTY": 2, "BANKNIFTY": 2}
    assert sum(plan.breakdown.values()) > plan.token_count  # honest, not contradictory


def test_headroom_is_reported_against_the_safe_threshold():
    plan = plan_subscriptions({"STOCKS": _tokens(1, 1_629)}, limit=3_000, safety_margin_pct=10)

    assert plan.safe_limit == 2_700
    assert plan.remaining == 2_700 - 1_629
    assert plan.utilisation_pct == 54.3  # of the hard limit, not of the threshold
    assert plan.over_safe_threshold is False


def test_a_universe_within_budget_stays_on_one_connection():
    """§10: do not introduce multiple connections unnecessarily."""
    plan = plan_subscriptions({"STOCKS": _tokens(1, 2_000)})

    assert plan.shard_count == 1
    assert plan.shards() == (plan.tokens,)
    assert plan.exceeds_broker_capacity is False


def test_crossing_the_safe_threshold_plans_shards_without_reaching_the_hard_limit():
    """The margin exists so the decision arrives before a subscribe is rejected."""
    plan = plan_subscriptions(
        {"ALL": _tokens(1, 2_800)}, limit=3_000, safety_margin_pct=10
    )

    assert plan.over_safe_threshold is True  # 2800 > 2700
    assert plan.token_count < plan.limit  # ...but still under the broker's hard limit
    assert plan.shard_count == 2
    assert plan.exceeds_broker_capacity is False


def test_shards_are_balanced_and_lossless():
    plan = plan_subscriptions({"ALL": _tokens(1, 5_000)}, limit=3_000, safety_margin_pct=10)
    shards = plan.shards()

    assert plan.shard_count == 2
    assert sum(len(shard) for shard in shards) == plan.token_count
    assert set().union(*shards) == set(plan.tokens)  # nothing dropped or duplicated
    assert max(len(shard) for shard in shards) <= plan.safe_limit


def test_a_universe_beyond_every_connection_is_flagged_not_silently_truncated():
    """A rejected subscribe surfaces only as a dead feed, so this must be loud."""
    plan = plan_subscriptions(
        {"ALL": _tokens(1, 20_000)}, limit=3_000, safety_margin_pct=10, max_connections=3
    )

    assert plan.shard_count == 8
    assert plan.exceeds_broker_capacity is True


def test_zero_margin_plans_against_the_broker_limit_itself():
    plan = plan_subscriptions({"ALL": _tokens(1, 3_000)}, limit=3_000, safety_margin_pct=0)

    assert plan.safe_limit == 3_000
    assert plan.over_safe_threshold is False
    assert plan.shard_count == 1


def test_telemetry_exposes_the_capacity_picture():
    plan = plan_subscriptions({"NIFTY": _tokens(1, 203), "STOCKS": _tokens(500, 816)})

    telemetry = plan.as_telemetry()

    assert telemetry["subscribed_tokens"] == 1_019
    assert telemetry["subscription_limit"] == 3_000
    assert telemetry["subscription_safe_limit"] == 2_700
    assert telemetry["subscription_remaining"] == 1_681
    assert telemetry["subscription_shards"] == 1
    assert telemetry["subscription_over_threshold"] is False
    assert telemetry["subscription_exceeds_capacity"] is False
    assert telemetry["subscription_breakdown"] == {"NIFTY": 203, "STOCKS": 816}


def test_an_empty_universe_is_not_a_division_by_zero():
    plan = SubscriptionPlan(tokens=())

    assert plan.token_count == 0
    assert plan.utilisation_pct == 0.0
    assert plan.shard_count == 1
    assert plan.exceeds_broker_capacity is False


def test_room_for_an_index_fno_domain_is_answerable_from_the_plan():
    """The question §10 exists to answer: can another domain fit on this connection?

    Today's universe is ~1,629 tokens. A consolidated index-F&O dataset adding futures for
    four indices across three expiries is a rounding error against the remaining headroom;
    this is how that gets verified rather than assumed.
    """
    plan = plan_subscriptions(
        {
            "NIFTY": _tokens(1, 203),
            "BANKNIFTY": _tokens(1_000, 203),
            "FINNIFTY": _tokens(2_000, 203),
            "SENSEX": _tokens(3_000, 203),
            "STOCKS": _tokens(10_000, 816),
        }
    )

    assert plan.token_count == 1_628
    assert plan.remaining > 1_000  # ample room for another domain
    # 4 indices x 3 futures expiries = 12 more tokens.
    with_index_fno = plan_subscriptions(
        {"existing": plan.tokens, "INDICES_FnO": _tokens(50_000, 12)}
    )
    assert with_index_fno.token_count == 1_640
    assert with_index_fno.over_safe_threshold is False



# --- capacity guard for the deployed six-index universe -----------------------


def test_the_six_index_universe_with_index_fno_fits_one_connection():
    """Verified composition from the 2026-08-07 instrument dumps: 2,067 tokens.

    Six index chains at ATM±50 on their nearest expiry (202 option tokens each — every
    index had more than 101 listed strikes, so the window is always full), six index spots,
    India VIX shared across all of them, three futures per index, and 208 F&O stocks whose
    spot + up to three futures give 830 tokens.

    This is a guard, not a decoration: widening the strike window, adding an expiry, or
    growing the stock universe all push against the broker's per-connection ceiling, and
    the failure mode is a silently rejected subscribe. If this test starts failing, the
    subscription genuinely needs sharding rather than a bigger number here.
    """
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"]
    groups: dict[str, list[int]] = {}
    for position, name in enumerate(indices, start=1):
        # 202 options (101 strikes x CE/PE) + the index spot + 3 futures expiries.
        groups[name] = _tokens(position * 100_000, 206)
    groups["VIX"] = [264_969]  # one shared token, routed into every index
    groups["STOCKS"] = _tokens(2_000_000, 830)  # 208 stocks x (spot + <=3 futures)

    plan = plan_subscriptions(groups)

    assert plan.token_count == 6 * 206 + 1 + 830
    assert plan.token_count == 2_067
    assert plan.safe_limit == 2_700
    assert plan.remaining == 633
    assert plan.utilisation_pct < 70.0
    assert plan.shard_count == 1
    assert plan.over_safe_threshold is False
    assert plan.exceeds_broker_capacity is False
