"""Tests for Black-Scholes pricing / Greeks / IV and frame reconstruction."""

from __future__ import annotations

import math

import numpy as np

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.reconstruct import bs
from app.reconstruct.greeks import (
    expiry_timestamp_ms,
    reconstruct_greeks,
    year_fraction,
)


def test_bs_price_reference_values():
    # Classic textbook case: S=100, K=100, T=1, r=5%, sigma=20%.
    call = bs.bs_price(100, 100, 1.0, 0.05, 0.20, bs.CALL)
    put = bs.bs_price(100, 100, 1.0, 0.05, 0.20, bs.PUT)
    assert abs(call - 10.4506) < 1e-3
    assert abs(put - 5.5735) < 1e-3
    # put-call parity: C - P = S - K e^{-rT}
    assert abs((call - put) - (100 - 100 * math.exp(-0.05))) < 1e-6


def test_bs_greeks_reference_values():
    g = bs.greeks(100, 100, 1.0, 0.05, 0.20, bs.CALL)
    assert abs(g.delta - 0.6368) < 1e-3
    assert abs(g.gamma - 0.018762) < 1e-4
    assert abs(g.vega - 0.37524) < 1e-4  # per 1%
    # theta per day is small and negative for this call
    assert -0.02 < g.theta < 0.0


def test_implied_vol_recovers_sigma():
    price = bs.bs_price(24500, 24500, 0.25, 0.0691, 0.15, bs.CALL)
    iv = bs.implied_vol(price, 24500, 24500, 0.25, 0.0691, bs.CALL)
    assert iv is not None
    assert abs(iv - 0.15) < 1e-4


def test_implied_vol_below_intrinsic_returns_none():
    # price below intrinsic -> no solution
    assert bs.implied_vol(0.01, 100, 50, 1.0, 0.05, bs.CALL) is None


def test_year_fraction_and_expiry_ts():
    exp = expiry_timestamp_ms("2026-07-24")
    # ~3 days before expiry
    now = exp - 3 * 24 * 3600 * 1000
    t = year_fraction(now, exp)
    assert abs(t - 3 / 365) < 1e-6


def test_reconstruct_greeks_from_frame_roundtrips_iv():
    strikes = np.array([2_450_000, 2_455_000], dtype="<i8")  # 24500, 24550 rupees
    header = IndexHeader("2026-07-21", "NIFTY", "2026-08-28", 0.0691, strikes)

    ts = 1_753_070_400_000
    exp_ms = expiry_timestamp_ms("2026-08-28")
    t = year_fraction(ts, exp_ms)
    spot = 24500.0
    sigma = 0.18

    calls = RawBlock.zeros(2)
    puts = RawBlock.zeros(2)
    # price each call at sigma=0.18 and store as paise
    for i, k in enumerate([24500.0, 24550.0]):
        price = bs.bs_price(spot, k, t, 0.0691, sigma, bs.CALL)
        calls.columns["ltp"][i] = round(price * 100)
        calls.columns["ohlc_close"][i] = round((price - 1.0) * 100)  # arbitrary close

    frame = IndexFrame(ts, 0, int(spot * 100), 1234, calls, puts)
    out = reconstruct_greeks(frame, header)

    assert abs(out["t_years"] - t) < 1e-9
    assert abs(out["calls"]["iv"][0] - sigma) < 1e-3
    assert abs(out["calls"]["iv"][1] - sigma) < 1e-3
    assert 0.0 < out["calls"]["delta"][0] < 1.0
    # change = ltp - ohlc_close = +1.0 rupee (we set close 1 below ltp)
    assert abs(out["calls"]["change"][0] - 1.0) < 1e-6
    # puts have no price -> IV stays NaN
    assert math.isnan(out["puts"]["iv"][0])
