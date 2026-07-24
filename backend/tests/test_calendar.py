"""Tests for the trading calendar (market-hours phase detection)."""

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


def _ms(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST_FALLBACK).timestamp() * 1000)


def test_trading_date_uses_ist():
    cal = TradingCalendar()
    # 2026-07-21 00:30 IST is still 2026-07-20 in UTC, but the IST trading date wins.
    assert cal.trading_date(_ms(2026, 7, 21, 0, 30)) == "2026-07-21"


def test_phases_across_the_day():
    cal = TradingCalendar()  # 2026-07-21 is a Tuesday
    assert cal.phase(_ms(2026, 7, 21, 8, 0)) == PHASE_PRE_OPEN
    assert cal.phase(_ms(2026, 7, 21, 9, 15)) == PHASE_OPEN  # open boundary
    assert cal.phase(_ms(2026, 7, 21, 12, 0)) == PHASE_OPEN
    assert cal.phase(_ms(2026, 7, 21, 15, 30)) == PHASE_CLOSED  # stop exactly at close
    assert cal.phase(_ms(2026, 7, 21, 15, 31)) == PHASE_CLOSED


def test_weekend_and_holiday_are_non_trading():
    cal = TradingCalendar(holidays={"2026-07-22"})
    assert cal.phase(_ms(2026, 7, 25, 12, 0)) == PHASE_HOLIDAY  # Saturday
    assert cal.phase(_ms(2026, 7, 26, 12, 0)) == PHASE_HOLIDAY  # Sunday
    assert cal.phase(_ms(2026, 7, 22, 12, 0)) == PHASE_HOLIDAY  # configured holiday
