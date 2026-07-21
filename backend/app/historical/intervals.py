"""Kite historical interval policy table (docs/40-historical/historical-data.md)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    wire_name: str
    max_request_days: int  # max span per single API request
    max_ui_days: int  # max span a single job may request
    step_minutes: int
    has_oi: bool = True


INTERVALS: dict[str, Interval] = {
    "minute": Interval("minute", 60, 60, 1),
    "3minute": Interval("3minute", 100, 100, 3),
    "5minute": Interval("5minute", 100, 100, 5),
    "10minute": Interval("10minute", 100, 100, 10),
    "15minute": Interval("15minute", 100, 100, 15),
    "30minute": Interval("30minute", 365, 365, 30),
    "60minute": Interval("60minute", 365, 365, 60),
    "day": Interval("day", 2000, 2000, 1440),
}


def get_interval(wire_name: str) -> Interval:
    if wire_name not in INTERVALS:
        raise KeyError(f"unknown interval '{wire_name}'; known: {sorted(INTERVALS)}")
    return INTERVALS[wire_name]
