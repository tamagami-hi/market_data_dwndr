"""Split a [from, to] date range into API-sized fetch windows."""

from __future__ import annotations

from datetime import date, timedelta


def chunk_windows(
    from_date: date,
    to_date: date,
    max_request_days: int,
    chunk_size_days: int | None = None,
) -> list[tuple[date, date]]:
    """Inclusive [start, end] windows no larger than the allowed chunk size.

    ``chunk_size_days`` (user override) is clamped to ``max_request_days``.
    """
    if from_date > to_date:
        raise ValueError("from_date must be <= to_date")
    span = max_request_days if chunk_size_days is None else min(chunk_size_days, max_request_days)
    span = max(span, 1)

    windows: list[tuple[date, date]] = []
    start = from_date
    while start <= to_date:
        end = min(start + timedelta(days=span - 1), to_date)
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows
