"""Tests for the three-signal feed-health classification."""

from __future__ import annotations

from app.capture.feed_health import (
    HEALTH_ARTIFACT_STALE,
    HEALTH_HEALTHY,
    HEALTH_INACTIVE,
    HEALTH_QUIET,
    HEALTH_RECOVERY_ABANDONED,
    HEALTH_RECOVERY_PENDING,
    HEALTH_TRANSPORT_STALE,
    classify,
    is_fault,
    stale_artifacts,
)

STALE_MS = 5_000


def _classify(**overrides) -> str:
    base = {
        "capture_expected": True,
        "transport_age_ms": 100,
        "content_age_ms": 100,
        "stale_after_ms": STALE_MS,
        "artifact_ages_ms": {"NIFTY": 100, "STOCKS": 100},
    }
    return classify(**{**base, **overrides})


def test_packets_arriving_and_artifacts_updating_is_healthy():
    assert _classify() == HEALTH_HEALTHY


def test_no_packets_at_all_is_transport_stale():
    """A dead uplink — the one condition restart-first recovery is designed for."""
    assert _classify(transport_age_ms=30_000, content_age_ms=30_000) == HEALTH_TRANSPORT_STALE


def test_packets_arriving_but_one_artifact_frozen_is_artifact_stale():
    """The socket is fine; index derivatives stopped. Must NOT read as a dead feed."""
    health = _classify(
        content_age_ms=30_000,
        artifact_ages_ms={"NIFTY": 60_000, "STOCKS": 100},
    )
    assert health == HEALTH_ARTIFACT_STALE


def test_packets_arriving_and_routed_but_values_unchanged_is_quiet():
    """A quiet market is not a fault, and the old single-signal design could not say so."""
    assert _classify(content_age_ms=30_000) == HEALTH_QUIET
    assert is_fault(HEALTH_QUIET) is False


def test_transport_staleness_outranks_artifact_staleness():
    health = _classify(
        transport_age_ms=30_000,
        content_age_ms=30_000,
        artifact_ages_ms={"NIFTY": 60_000, "STOCKS": 60_000},
    )
    assert health == HEALTH_TRANSPORT_STALE


def test_nothing_expected_is_inactive_not_unhealthy():
    """§23: market phase and feed health are independent. Silence off-session is fine."""
    health = _classify(
        capture_expected=False, transport_age_ms=900_000, content_age_ms=900_000
    )
    assert health == HEALTH_INACTIVE
    assert is_fault(HEALTH_INACTIVE) is False


def test_recovery_pending_outranks_the_underlying_signal():
    health = _classify(
        transport_age_ms=90_000, content_age_ms=90_000, recovery_pending=True
    )
    assert health == HEALTH_RECOVERY_PENDING
    assert is_fault(health) is True


def test_recovery_abandoned_outranks_everything_including_inactive():
    """Once the budget is spent the operator must keep seeing it, even after the close."""
    assert _classify(recovery_abandoned=True) == HEALTH_RECOVERY_ABANDONED
    assert (
        _classify(capture_expected=False, recovery_abandoned=True)
        == HEALTH_RECOVERY_ABANDONED
    )


def test_a_startup_with_no_signals_yet_is_healthy_not_alarming():
    assert _classify(transport_age_ms=None, content_age_ms=None, artifact_ages_ms={}) == (
        HEALTH_HEALTHY
    )


# --- artifact tolerance -------------------------------------------------------


def test_artifacts_are_more_tolerant_than_the_content_signal():
    """A dataset with no trades for a few seconds is ordinary, not a fault."""
    ages = {"NIFTY": STALE_MS + 1}
    assert stale_artifacts(ages, STALE_MS) == ()  # within 3x tolerance
    assert stale_artifacts({"NIFTY": STALE_MS * 3}, STALE_MS) == ("NIFTY",)


def test_an_artifact_that_never_updated_counts_as_stale():
    """Otherwise a silently unsubscribed instrument would read as perfectly healthy."""
    assert stale_artifacts({"NIFTY": None, "STOCKS": 10}, STALE_MS) == ("NIFTY",)


def test_stale_artifact_names_are_sorted_for_stable_reporting():
    ages = {"STOCKS": None, "NIFTY": None, "BANKNIFTY": None}
    assert stale_artifacts(ages, STALE_MS) == ("BANKNIFTY", "NIFTY", "STOCKS")
