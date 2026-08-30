"""Recovery ladder and auth alarms: the gaps that let a whole session go missing.

Covers the fixes for:

* **pre-first-tick writes** — five zero-filled frames per occurrence, one of which wrote
  the header that then defined the file's strike axis;
* **arming on evidence** — a feed that died at the open could not be acted on for the full
  300 s arm delay, turning a ~47 s fault into a ~295 s hole on six of nine sessions;
* **the in-process reconnect rung** — recovery had only "wait for the SDK" and "SIGTERM
  the process", so 22 mid-day stalls each cost a full restart;
* **AUTH_BLOCKED** — a broker token Kite refused for 98 minutes produced only a per-poll
  WARNING while ``/health`` stayed 200.
"""

from __future__ import annotations

import pytest

from app.api.health import NOT_CAPTURING_DEAD_AFTER_S, build_health
from app.capture.engine import CaptureEngine, CaptureStalledError
from app.ops.calendar import PHASE_CLOSED, PHASE_OPEN
from app.session_service import AUTH_BLOCKED, AUTH_NO_SESSION, AUTH_OK

TICK = [{"instrument_token": 42, "last_price": 101.5}]


def _engine(**kwargs) -> CaptureEngine:
    kwargs.setdefault("stale_after_ms", 5_000)
    return CaptureEngine({}, None, {}, None, **kwargs)


# --- pre-first-tick suppression ----------------------------------------------


def test_no_frame_is_written_before_the_first_tick():
    engine = _engine()
    engine.freshness.start(0)

    for ts in (1_000, 2_000, 3_000, 4_000, 5_000):
        engine.capture_snapshot(ts)

    assert engine.captures == 0, "an all-zero table is not market data"
    assert engine.awaiting_first_tick_seconds == 5
    assert engine.stale_seconds == 0, "never-started is not the same failure as gone-stale"
    assert engine.first_capture_ms is None


def test_writing_starts_at_the_first_tick_and_the_wait_is_not_counted_as_stale():
    engine = _engine()
    engine.freshness.start(0)
    engine.capture_snapshot(1_000)
    engine.capture_snapshot(2_000)

    engine.freshness.observe(TICK, 2_500)
    engine.capture_snapshot(3_000)

    assert engine.awaiting_first_tick_seconds == 2
    assert engine.captures == 1
    assert engine.first_capture_ms == 3_000
    assert engine.stale_seconds == 0


def test_ticks_applied_without_a_freshness_observe_still_open_the_gate():
    """The gate must not depend on which entry point a caller happens to use."""
    engine = _engine()
    engine.freshness.start(0)
    engine.apply_ticks(TICK, 500)

    engine.capture_snapshot(1_000)

    assert engine.has_received_data() is True
    assert engine.captures == 1
    assert engine.awaiting_first_tick_seconds == 0


def test_the_pre_first_tick_gate_ignores_the_stale_write_opt_out():
    """``CAPTURE_SUPPRESS_STALE_WRITES=false`` restores frozen frames, not fabricated ones."""
    engine = _engine(suppress_stale_writes=False)
    engine.freshness.start(0)

    engine.capture_snapshot(1_000)

    assert engine.captures == 0
    assert engine.awaiting_first_tick_seconds == 1


# --- arming on evidence ------------------------------------------------------


def test_recovery_arms_once_the_feed_has_proven_itself_even_if_the_delay_has_not_elapsed():
    engine = _engine(recovery_armed=lambda _now: False)  # time-based gate still closed
    engine.freshness.start(0)

    assert engine.recovery_armed(1_000) is False, "no data yet: nothing to act on"

    engine.freshness.observe(TICK, 1_000)
    assert engine.recovery_armed(2_000) is True, "the feed worked; a stall is now a fault"


def test_evidence_arming_respects_the_session_gate():
    """Outside the session an absent feed is normal, whatever happened earlier."""
    engine = _engine(recovery_armed=lambda _now: False, capture_expected=lambda _now: False)
    engine.freshness.observe(TICK, 1_000)
    assert engine.recovery_armed(2_000) is False


def test_a_feed_that_worked_then_died_escalates_at_the_deadline():
    escalations: list[int] = []
    engine = _engine(
        stale_exit_ms=40_000,
        recovery_armed=lambda _now: False,  # only evidence-arming applies
        escalation_recorder=lambda: escalations.append(1) or 1,
    )
    engine.freshness.start(0)
    engine.freshness.observe(TICK, 1_000)

    engine.observe_feed_health(10_000)  # stale spell begins
    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(60_000)
    assert escalations == [1]


# --- the in-process reconnect rung ------------------------------------------


def test_the_socket_is_replaced_in_place_before_a_restart_is_escalated():
    attempts: list[str] = []
    engine = _engine(
        stale_exit_ms=40_000,
        reconnect_after_ms=10_000,
        recovery_armed=lambda _now: True,
    )
    engine.set_reconnect_fn(lambda: attempts.append("reconnect"))
    engine.freshness.start(0)
    engine.freshness.observe(TICK, 0)

    engine.observe_feed_health(6_000)  # the stale spell starts here
    assert attempts == [], "below the reconnect threshold"

    engine.observe_feed_health(14_000)  # spell 8 s, still below
    assert attempts == []

    engine.observe_feed_health(17_000)  # spell 11 s >= reconnect_after_ms
    assert attempts == ["reconnect"]
    assert engine.reconnect_attempts == 1


def test_reconnects_are_rate_limited_so_they_cannot_storm():
    attempts: list[int] = []
    engine = _engine(
        stale_exit_ms=0,  # escalation disabled: isolate the reconnect rung
        reconnect_after_ms=1_000,
        reconnect_limit_per_hour=3,
        recovery_armed=lambda _now: True,
    )
    engine.set_reconnect_fn(lambda: attempts.append(1))
    engine.freshness.start(0)
    engine.freshness.observe(TICK, 0)

    for second in range(1, 40):
        engine.observe_feed_health(second * 2_000)

    assert len(attempts) == 3, "the hourly cap must hold"


def test_a_reconnect_does_not_reset_the_escalation_deadline():
    """A reconnect that does not restore the feed must still reach the restart."""
    engine = _engine(
        stale_exit_ms=40_000,
        reconnect_after_ms=10_000,
        recovery_armed=lambda _now: True,
    )
    engine.set_reconnect_fn(lambda: None)
    engine.freshness.start(0)
    engine.freshness.observe(TICK, 0)

    engine.observe_feed_health(6_000)   # spell starts
    engine.observe_feed_health(17_000)  # spell 11 s -> in-process reconnect
    assert engine.reconnect_attempts == 1
    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(50_000)  # spell 44 s -> restart anyway


def test_a_recovered_feed_is_never_reconnected_or_restarted():
    attempts: list[int] = []
    engine = _engine(
        stale_exit_ms=40_000,
        reconnect_after_ms=10_000,
        recovery_armed=lambda _now: True,
    )
    engine.set_reconnect_fn(lambda: attempts.append(1))
    engine.freshness.start(0)
    for second in range(0, 60, 2):
        engine.freshness.observe(
            [{"instrument_token": 42, "last_price": 100 + second}], second * 1_000
        )
        engine.observe_feed_health(second * 1_000)
    assert attempts == []


# --- AUTH_BLOCKED and the "owed but not capturing" alarm ---------------------


class _Service:
    def __init__(self, **status) -> None:
        self._status = {
            "auth_state": AUTH_OK,
            "auth_blocked_seconds": 0,
            "auth_last_error": None,
            "market_phase": PHASE_OPEN,
            **status,
        }

    def status(self) -> dict:
        return dict(self._status)


class _Controller:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.has_failed = False

    def monitor_snapshot(self) -> dict:
        return {}


def _state(service, controller):
    from types import SimpleNamespace

    return SimpleNamespace(session_service=service, capture_controller=controller)


def _check(payload, component):
    return next(c for c in payload["checks"] if c["component"] == component)


def test_an_open_session_with_capture_running_is_healthy():
    payload, status = build_health(_state(_Service(), _Controller(True)))
    assert status == 200
    assert _check(payload, "broker_session")["status"] == "ok"


def test_a_rejected_broker_token_before_the_open_is_degraded_not_dead():
    service = _Service(
        auth_state=AUTH_BLOCKED, auth_blocked_seconds=600, market_phase=PHASE_CLOSED
    )
    payload, status = build_health(_state(service, _Controller(False)))
    assert status == 200, "degraded must not drive a restart loop"
    assert payload["status"] == "degraded"
    check = _check(payload, "broker_session")
    assert check["status"] == "stale"
    assert "AUTH_BLOCKED" in check["message"]


def test_an_open_session_with_a_rejected_token_is_dead_and_503():
    """The 2026-08-28 condition: 54 minutes of owed seconds with nothing capturing."""
    service = _Service(auth_state=AUTH_BLOCKED, auth_blocked_seconds=3_239)
    payload, status = build_health(_state(service, _Controller(False)))
    assert status == 503
    assert payload["status"] == "dead"
    check = _check(payload, "broker_session")
    assert check["status"] == "dead"
    assert "unrecoverable data loss" in check["message"]


def test_a_brief_start_delay_is_degraded_then_escalates_to_dead():
    service = _Service(auth_state=AUTH_NO_SESSION, auth_blocked_seconds=5)
    payload, status = build_health(_state(service, _Controller(False)))
    assert status == 200
    assert _check(payload, "broker_session")["status"] == "stale"

    service = _Service(
        auth_state=AUTH_NO_SESSION, auth_blocked_seconds=NOT_CAPTURING_DEAD_AFTER_S + 1
    )
    payload, status = build_health(_state(service, _Controller(False)))
    assert status == 503
    assert _check(payload, "broker_session")["status"] == "dead"


def test_waiting_for_the_broker_outside_the_session_is_not_an_alarm():
    service = _Service(auth_state=AUTH_NO_SESSION, auth_blocked_seconds=120,
                       market_phase=PHASE_CLOSED)
    payload, status = build_health(_state(service, _Controller(False)))
    assert status == 200
    assert _check(payload, "broker_session")["status"] == "ok"


def test_an_unconfigured_backend_reports_idle_rather_than_failing():
    from types import SimpleNamespace

    payload, status = build_health(SimpleNamespace())
    assert status == 200
    assert _check(payload, "broker_session")["status"] == "idle"
