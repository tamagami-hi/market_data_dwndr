"""Tests for the market-session abstraction (when persistence is valid)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.ops.calendar import TradingCalendar
from app.ops.sessions import (
    PHASE_BOOTSTRAP,
    PHASE_CLOSED,
    PHASE_INACTIVE,
    PHASE_OPEN,
    PHASE_PRE_OPEN,
    MarketSession,
    SessionRegistry,
    build_session_registry,
)

IST = ZoneInfo("Asia/Kolkata")


def at(hour: int, minute: int, day: int = 10, month: int = 8) -> int:
    """Epoch ms for 2026-08-<day> at an IST wall-clock time (2026-08-10 is a Monday)."""
    return int(datetime(2026, month, day, hour, minute, tzinfo=IST).timestamp() * 1000)


def _calendar(open_at="09:15", close_at="15:30", holidays=None) -> TradingCalendar:
    return TradingCalendar(
        holidays=set(holidays or []),
        timezone_name="Asia/Kolkata",
        market_open=open_at,
        market_close=close_at,
    )


def _session(**overrides) -> MarketSession:
    from app.ops.sessions import parse_hhmm

    defaults = {
        "name": "equity_deriv",
        "open_at": parse_hhmm("09:15"),
        "close_at": parse_hhmm("15:30"),
        "pre_open_start": parse_hhmm("09:00"),
        "pre_open_end": parse_hhmm("09:15"),
        "capture_pre_open": False,
        "enabled": True,
        "stale_arm_delay_s": 300.0,
    }
    return MarketSession(**{**defaults, **overrides})


def _registry(session: MarketSession | None = None, **kwargs) -> SessionRegistry:
    session = session or _session()
    return SessionRegistry(
        _calendar(),
        {"equity_deriv": session, "equity_cash": session},
        kwargs.pop("artifacts", {"NIFTY": "equity_deriv", "STOCKS": "equity_deriv"}),
        "equity_deriv",
    )


# --- phases -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 50, PHASE_BOOTSTRAP),
        (9, 5, PHASE_PRE_OPEN),
        (9, 15, PHASE_OPEN),
        (15, 29, PHASE_OPEN),
        (15, 30, PHASE_CLOSED),
        (23, 0, PHASE_CLOSED),
    ],
)
def test_phase_walks_the_trading_day(hour, minute, expected):
    assert _registry().phase("NIFTY", at(hour, minute)) == expected


def test_a_non_trading_day_is_inactive_for_every_artifact():
    registry = _registry()
    saturday = at(11, 0, day=8)  # 2026-08-08
    assert registry.phase("NIFTY", saturday) == PHASE_INACTIVE
    assert registry.is_capture_expected("NIFTY", saturday) is False
    assert registry.is_stale_armed("NIFTY", saturday) is False


def test_a_holiday_is_inactive():
    registry = SessionRegistry(
        _calendar(holidays=["2026-08-10"]),
        {"equity_deriv": _session()},
        {"NIFTY": "equity_deriv"},
        "equity_deriv",
    )
    assert registry.phase("NIFTY", at(11, 0)) == PHASE_INACTIVE
    assert registry.scheduled_seconds_elapsed("NIFTY", at(15, 30)) == 0


# --- capture expectation vs stale arming --------------------------------------


def test_pre_open_silence_is_neither_captured_nor_a_fault():
    """§14/§24: pre-open is a policy. With it off, its silence must not be data loss."""
    registry = _registry()
    pre_open = at(9, 5)
    assert registry.phase("NIFTY", pre_open) == PHASE_PRE_OPEN
    assert registry.is_capture_expected("NIFTY", pre_open) is False
    assert registry.is_stale_armed("NIFTY", pre_open) is False


def test_captured_pre_open_expects_frames_but_still_does_not_arm_recovery():
    """The auction produces legitimately long silences, so it must never restart us."""
    registry = _registry(_session(capture_pre_open=True))
    pre_open = at(9, 5)
    assert registry.is_capture_expected("NIFTY", pre_open) is True
    assert registry.is_stale_armed("NIFTY", pre_open) is False


def test_recovery_arms_only_after_the_grace_period():
    """The 2026-08-04/05/06 trigger: capture began before the exchange traded."""
    registry = _registry()
    assert registry.is_capture_expected("NIFTY", at(9, 16)) is True
    assert registry.is_stale_armed("NIFTY", at(9, 16)) is False  # inside 300s grace
    assert registry.is_stale_armed("NIFTY", at(9, 20)) is True
    assert registry.is_stale_armed("NIFTY", at(15, 35)) is False  # after close


def test_a_disabled_session_schedules_nothing():
    """§17.9: intentionally disabled must be distinguishable from failed."""
    registry = _registry(_session(enabled=False))
    assert registry.is_capture_expected("NIFTY", at(12, 0)) is False
    assert registry.is_stale_armed("NIFTY", at(12, 0)) is False
    assert registry.scheduled_seconds("NIFTY") == 0
    assert registry.scheduled_seconds_elapsed("NIFTY", at(15, 30)) == 0


# --- scheduled grid, independent of process uptime ----------------------------


def test_scheduled_seconds_come_from_configuration_only():
    assert _session().scheduled_seconds() == 6 * 3600 + 15 * 60  # 09:15-15:30
    assert _session(capture_pre_open=True).scheduled_seconds() == 6 * 3600 + 30 * 60


def test_scheduled_seconds_elapsed_tracks_the_session_not_the_process():
    registry = _registry()
    assert registry.scheduled_seconds_elapsed("NIFTY", at(9, 0)) == 0
    assert registry.scheduled_seconds_elapsed("NIFTY", at(9, 16)) == 60
    assert registry.scheduled_seconds_elapsed("NIFTY", at(12, 0)) == 2 * 3600 + 45 * 60
    # Past the close it saturates at the full session, never beyond.
    assert registry.scheduled_seconds_elapsed("NIFTY", at(23, 0)) == 6 * 3600 + 15 * 60


def test_downtime_between_two_moments_is_measured_in_scheduled_seconds():
    """§17.5: the whole point — owed seconds computable with nothing running."""
    registry = _registry()
    # Application down 09:15 -> 09:27 on a trading day: 12 market minutes owed.
    assert registry.scheduled_seconds_between("NIFTY", at(9, 15), at(9, 27)) == 720
    # Entirely before the session: nothing was owed, so nothing is loss.
    assert registry.scheduled_seconds_between("NIFTY", at(8, 0), at(9, 10)) == 0
    # Entirely after the close.
    assert registry.scheduled_seconds_between("NIFTY", at(16, 0), at(23, 0)) == 0
    # Straddling the open counts only the in-session part (§17.6, late start).
    assert registry.scheduled_seconds_between("NIFTY", at(9, 0), at(9, 20)) == 300
    # Straddling the close counts only up to it (§17.7, never-recovered outage).
    assert registry.scheduled_seconds_between("NIFTY", at(15, 20), at(18, 0)) == 600


def test_downtime_spanning_days_skips_non_trading_days():
    registry = _registry()
    # Friday 15:20 -> Monday 09:20: 10 min of Friday + 5 min of Monday, no weekend.
    friday = at(15, 20, day=7)
    monday = at(9, 20, day=10)
    assert registry.scheduled_seconds_between("NIFTY", friday, monday) == 600 + 300


# --- artifact -> session mapping ----------------------------------------------


def test_artifacts_reference_sessions_instead_of_carrying_times():
    from app.ops.sessions import parse_hhmm

    deriv = _session()
    cash = _session(name="equity_cash", close_at=parse_hhmm("15:00"))
    registry = SessionRegistry(
        _calendar(),
        {"equity_deriv": deriv, "equity_cash": cash},
        {"NIFTY": "equity_deriv", "STOCKS": "equity_cash"},
        "equity_deriv",
    )

    # §17.12: one artifact can close while another is still accumulating expected time.
    late = at(15, 10)
    assert registry.is_capture_expected("NIFTY", late) is True
    assert registry.is_capture_expected("STOCKS", late) is False
    assert registry.any_capture_expected(late) is True
    assert registry.scheduled_seconds("NIFTY") != registry.scheduled_seconds("STOCKS")

    # An unmapped artifact falls back to the default session rather than failing.
    assert registry.session_for("INDICES_FnO").name == "equity_deriv"


def test_any_stale_armed_is_false_once_every_artifact_has_closed():
    registry = _registry()
    assert registry.any_stale_armed(at(12, 0)) is True
    assert registry.any_stale_armed(at(15, 45)) is False


# --- construction from settings -----------------------------------------------


def _settings(**overrides):
    base = SimpleNamespace(
        market_holidays=[],
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        capture_recovery_arm_delay_seconds=300.0,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_sessions_inherit_the_legacy_window_when_unconfigured():
    """An existing deployment must keep its exact schedule until the new block is set."""
    registry = build_session_registry(_settings(), {"NIFTY": "equity_deriv"})
    session = registry.session_for("NIFTY")
    assert (session.open_at.hour, session.open_at.minute) == (9, 0)
    assert (session.close_at.hour, session.close_at.minute) == (15, 30)
    assert session.scheduled_seconds() == 6 * 3600 + 30 * 60  # == expected_frames today
    assert session.has_pre_open is False


def test_session_settings_override_the_legacy_window():
    registry = build_session_registry(
        _settings(
            equity_deriv_open="09:15",
            equity_deriv_close="15:30",
            equity_deriv_preopen_start="09:00",
            equity_deriv_preopen_end="09:15",
            equity_deriv_capture_preopen=True,
        ),
        {"NIFTY": "equity_deriv"},
    )
    session = registry.session_for("NIFTY")
    assert (session.open_at.hour, session.open_at.minute) == (9, 15)
    assert session.capture_pre_open is True
    assert session.scheduled_seconds() == 6 * 3600 + 30 * 60  # 09:00-15:30 incl. pre-open


def test_a_session_rejects_an_impossible_schedule():
    from app.ops.sessions import parse_hhmm

    with pytest.raises(ValueError, match="must be after open"):
        _session(open_at=parse_hhmm("15:30"), close_at=parse_hhmm("09:15"))
    with pytest.raises(ValueError, match="pre-open must end at or before open"):
        _session(pre_open_end=parse_hhmm("10:00"))
    with pytest.raises(ValueError, match="requires a pre-open window"):
        _session(pre_open_start=None, pre_open_end=None, capture_pre_open=True)
