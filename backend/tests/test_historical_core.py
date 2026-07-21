"""Tests for historical intervals, windowing, request validation, limiter, client."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.historical.client import (
    Candle,
    HistoricalClient,
    HistoricalError,
    is_retryable,
    parse_candles,
)
from app.historical.intervals import get_interval
from app.historical.limiter import TokenBucket
from app.historical.request import HistoricalRequest
from app.historical.windows import chunk_windows

# --- intervals ---------------------------------------------------------------


def test_interval_policy():
    assert get_interval("minute").max_request_days == 60
    assert get_interval("day").max_ui_days == 2000
    with pytest.raises(KeyError):
        get_interval("2minute")


# --- windows -----------------------------------------------------------------


def test_chunk_windows_splits_and_clamps():
    wins = chunk_windows(date(2026, 1, 1), date(2026, 1, 10), max_request_days=4)
    assert wins == [
        (date(2026, 1, 1), date(2026, 1, 4)),
        (date(2026, 1, 5), date(2026, 1, 8)),
        (date(2026, 1, 9), date(2026, 1, 10)),
    ]
    # chunk_size clamped to max_request_days
    wins2 = chunk_windows(
        date(2026, 1, 1), date(2026, 1, 10), max_request_days=3, chunk_size_days=100
    )
    assert all((w[1] - w[0]).days <= 2 for w in wins2)


def test_chunk_windows_rejects_reversed_range():
    with pytest.raises(ValueError):
        chunk_windows(date(2026, 1, 10), date(2026, 1, 1), 5)


# --- request validation ------------------------------------------------------


def _req(**kw):
    base = dict(
        underlying="NIFTY",
        interval="day",
        from_date=date(2026, 1, 1),
        to_date=date(2026, 2, 1),
        expiry="2026-01-29",
    )
    base.update(kw)
    return HistoricalRequest(**base)


def test_valid_request_passes():
    _req().validate()  # should not raise


def test_request_from_before_to():
    with pytest.raises(ValueError):
        _req(from_date=date(2026, 2, 1), to_date=date(2026, 1, 1)).validate()


def test_request_span_within_max_ui_days():
    with pytest.raises(ValueError):
        _req(interval="minute", from_date=date(2026, 1, 1), to_date=date(2026, 6, 1)).validate()


def test_request_weekly_monthly_exclusive():
    with pytest.raises(ValueError):
        _req(weekly_only=True, monthly_only=True).validate()


def test_request_atm_and_strike_range_exclusive():
    with pytest.raises(ValueError):
        _req(selection_mode="atm_window", strike_range=(24000, 25000)).validate()


def test_request_expiry_format():
    with pytest.raises(ValueError):
        _req(expiry="29-01-2026").validate()


# --- limiter (deterministic clock) -------------------------------------------


async def test_token_bucket_bursts_then_throttles():
    t = {"now": 0.0}
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        t["now"] += d
        slept.append(d)

    bucket = TokenBucket(
        rate_per_second=1.0, burst=2, clock=lambda: t["now"], sleep=fake_sleep
    )
    await bucket.acquire()  # token 2 -> 1
    await bucket.acquire()  # token 1 -> 0
    await bucket.acquire()  # empty -> must wait ~1s
    assert slept == [1.0]


# --- client parsing + retry --------------------------------------------------


def test_parse_candles_dict_and_list():
    raw = {
        "data": {
            "candles": [
                ["2026-07-21T09:15:00+0530", 100.5, 101.0, 100.0, 100.8, 1500, 42000],
                ["2026-07-21T09:16:00+0530", 100.8, 101.5, 100.7, 101.2, 1600],
            ]
        }
    }
    candles = parse_candles(raw)
    assert len(candles) == 2
    assert candles[0].open == 100.5 and candles[0].oi == 42000
    assert candles[1].oi == 0  # missing OI -> 0
    # 09:15 IST == 03:45 UTC
    dt = datetime.fromtimestamp(candles[0].timestamp_unix_ms / 1000, tz=UTC)
    assert (dt.hour, dt.minute) == (3, 45)


def test_is_retryable():
    assert is_retryable(429) and is_retryable(503)
    assert not is_retryable(200) and not is_retryable(404)


async def test_client_retries_then_succeeds():
    attempts = {"n": 0}

    async def fetcher(token, interval, frm, to, oi):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise HistoricalError(429)
        return [["2026-07-21T09:15:00+0530", 1, 2, 0.5, 1.5, 10, 5]]

    slept: list[float] = []
    bucket = TokenBucket(1000.0, burst=100)
    client = HistoricalClient(fetcher, bucket, sleep=lambda d: slept.append(d) or _noop())
    candles = await client.fetch_window(
        123, "minute", datetime(2026, 7, 21), datetime(2026, 7, 22)
    )
    assert attempts["n"] == 2  # retried once
    assert len(candles) == 1 and isinstance(candles[0], Candle)
    assert slept  # backoff slept at least once


async def _noop():
    return None


async def test_client_raises_on_non_retryable():
    async def fetcher(token, interval, frm, to, oi):
        raise HistoricalError(404)

    client = HistoricalClient(fetcher, TokenBucket(1000.0, burst=100))
    with pytest.raises(HistoricalError):
        await client.fetch_window(1, "day", datetime(2026, 1, 1), datetime(2026, 1, 2))
