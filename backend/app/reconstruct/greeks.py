"""Reconstruct option Greeks/IV for an index frame from stored raw + risk-free rate."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.ops.calendar import _get_tz
from app.reconstruct import bs

# algo_engine (utils.rs time_to_expiry_years) uses 365.25 days/year and a tiny
# non-zero floor for BS stability once expiry has passed.
YEAR_MS = 365.25 * 24 * 3600 * 1000
MIN_MATURITY_YEARS = 1e-5


def expiry_timestamp_ms(
    expiry_date: str, market_close: str = "15:30", timezone_name: str = "Asia/Kolkata"
) -> int:
    """Epoch ms of an expiry's close (default 15:30 IST)."""
    tz = _get_tz(timezone_name)
    hh, mm = (int(x) for x in market_close.split(":"))
    y, mo, d = (int(x) for x in expiry_date.split("-"))
    dt = datetime(y, mo, d, hh, mm, tzinfo=tz)
    return int(dt.astimezone(UTC).timestamp() * 1000)


def year_fraction(now_ms: int, expiry_ms: int) -> float:
    """Time-to-expiry in years (calendar-day / 365.25 convention), floored to a tiny
    positive value for BS stability once expiry has passed (algo_engine parity)."""
    return max((expiry_ms - now_ms) / YEAR_MS, MIN_MATURITY_YEARS)


def _paise(v) -> float:
    return float(v) / 100.0


def _reconstruct_side(
    block: RawBlock,
    strikes_rupees,
    spot: float,
    t: float,
    r: float,
    option: str,
    fallback_iv: float | None = None,
) -> dict:
    n = block.length()
    iv = [math.nan] * n
    delta = [math.nan] * n
    gamma = [math.nan] * n
    vega = [math.nan] * n
    theta = [math.nan] * n
    rho = [math.nan] * n
    change = [0.0] * n
    ltp_col = block.columns["ltp"]
    close_col = block.columns["ohlc_close"]
    for i in range(n):
        ltp = _paise(ltp_col[i])
        change[i] = ltp - _paise(close_col[i])
        strike = float(strikes_rupees[i])
        if ltp <= 0 or strike <= 0:
            continue
        # Grossly below intrinsic (arbitrage / bad tick) -> no IV, no fallback.
        if bs.is_below_intrinsic(ltp, spot, strike, t, r, option):
            continue
        sigma = bs.implied_vol(ltp, spot, strike, t, r, option)
        # Continuity fallback: VIX-derived IV when the solve fails (algo_engine parity).
        if sigma is None and fallback_iv is not None and fallback_iv > 0:
            sigma = fallback_iv
        if sigma is None:
            continue
        g = bs.greeks(spot, strike, t, r, sigma, option)
        iv[i], delta[i], gamma[i] = g.iv, g.delta, g.gamma
        vega[i], theta[i], rho[i] = g.vega, g.theta, g.rho
    return {
        "iv": iv, "delta": delta, "gamma": gamma,
        "vega": vega, "theta": theta, "rho": rho, "change": change,
    }


def reconstruct_greeks(
    frame: IndexFrame,
    header: IndexHeader,
    *,
    now_ms: int | None = None,
    market_close: str = "15:30",
    timezone_name: str = "Asia/Kolkata",
) -> dict:
    """Per-strike IV + Greeks for calls and puts, computed from the stored frame."""
    ts = now_ms if now_ms is not None else frame.timestamp_unix_ms
    exp_ms = expiry_timestamp_ms(header.expiry_date, market_close, timezone_name)
    t = year_fraction(ts, exp_ms)
    spot = _paise(frame.spot_price)
    r = float(header.risk_free_rate)
    strikes_rupees = [s / 100.0 for s in header.strikes]
    # VIX is stored x100 (paise-style); VIX/100 as decimal vol is the ultimate IV fallback.
    vix_decimal = _paise(frame.vix) / 100.0
    fallback_iv = vix_decimal if vix_decimal > 0 else None
    return {
        "t_years": t,
        "spot": spot,
        "fallback_iv": fallback_iv,
        "calls": _reconstruct_side(frame.calls, strikes_rupees, spot, t, r, bs.CALL, fallback_iv),
        "puts": _reconstruct_side(frame.puts, strikes_rupees, spot, t, r, bs.PUT, fallback_iv),
    }
