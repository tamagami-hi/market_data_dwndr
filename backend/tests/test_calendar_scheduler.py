"""Tests for the trading calendar and the market-hours scheduler."""

from __future__ import annotations

from datetime import datetime

from app.ops.calendar import (
    IST_FALLBACK,
    PHASE_CLOSED,
    PHASE_HOLIDAY,
    PHASE_OPEN,
    PHASE_PRE_OPEN,
    TradingCalendar,
)
from app.ops.scheduler import (
    EVENT_RUN_EOD,
    EVENT_START_CAPTURE,
    EVENT_STOP_CAPTURE,
    CaptureScheduler,
    PhaseMachine,
)


def _ms(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST_FALLBACK).timestamp() * 1000)


# --- calendar ----------------------------------------------------------------


def test_trading_date_uses_ist():
    cal = TradingCalendar()
    # 2026-07-21 00:30 IST is still 2026-07-20 in UTC, but the IST trading date wins.
    assert cal.trading_date(_ms(2026, 7, 21, 0, 30)) == "2026-07-21"


def test_phases_across_the_day():
    cal = TradingCalendar()  # 2026-07-21 is a Tuesday
    assert cal.phase(_ms(2026, 7, 21, 8, 0)) == PHASE_PRE_OPEN
    assert cal.phase(_ms(2026, 7, 21, 9, 15)) == PHASE_OPEN  # open boundary
    assert cal.phase(_ms(2026, 7, 21, 12, 0)) == PHASE_OPEN
    assert cal.phase(_ms(2026, 7, 21, 15, 30)) == PHASE_OPEN  # close boundary inclusive
    assert cal.phase(_ms(2026, 7, 21, 15, 31)) == PHASE_CLOSED


def test_weekend_and_holiday_are_non_trading():
    cal = TradingCalendar(holidays={"2026-07-22"})
    assert cal.phase(_ms(2026, 7, 25, 12, 0)) == PHASE_HOLIDAY  # Saturday
    assert cal.phase(_ms(2026, 7, 26, 12, 0)) == PHASE_HOLIDAY  # Sunday
    assert cal.phase(_ms(2026, 7, 22, 12, 0)) == PHASE_HOLIDAY  # configured holiday
    assert cal.is_open(_ms(2026, 7, 22, 12, 0)) is False


# --- phase machine -----------------------------------------------------------


def test_phase_machine_transitions():
    m = PhaseMachine()
    assert m.update(PHASE_PRE_OPEN) == []
    assert m.update(PHASE_OPEN) == [EVENT_START_CAPTURE]
    assert m.update(PHASE_OPEN) == []  # no repeat
    assert m.update(PHASE_CLOSED) == [EVENT_STOP_CAPTURE, EVENT_RUN_EOD]
    assert m.update(PHASE_HOLIDAY) == []  # leaving CLOSED (not OPEN) -> nothing


# --- scheduler ---------------------------------------------------------------


def test_scheduler_dispatches_callbacks():
    cal = TradingCalendar()
    events: list[str] = []
    sched = CaptureScheduler(
        cal,
        on_start_capture=lambda: events.append("start"),
        on_stop_capture=lambda: events.append("stop"),
        on_run_eod=lambda: events.append("eod"),
    )
    sched.tick(_ms(2026, 7, 21, 8, 0))  # PRE_OPEN
    sched.tick(_ms(2026, 7, 21, 10, 0))  # OPEN -> start
    sched.tick(_ms(2026, 7, 21, 11, 0))  # still OPEN -> nothing
    sched.tick(_ms(2026, 7, 21, 16, 0))  # CLOSED -> stop + eod
    assert events == ["start", "stop", "eod"]
