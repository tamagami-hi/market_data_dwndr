"""Reconstruct option Greeks/IV for an index frame from stored raw + bond yield."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.ops.calendar import _get_tz
from app.reconstruct import bs

YEAR_MS = 365.0 * 24 * 3600 * 1000


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
    """Time-to-expiry in years (calendar-day convention), floored to a tiny positive."""
    return max((expiry_ms - now_ms) / YEAR_MS, 1e-9)


def _paise(v) -> float:
    return float(v) / 100.0


def _reconstruct_side(
    block: RawBlock, strikes_rupees, spot: float, t: float, r: float, option: str
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
        sigma = bs.implied_vol(ltp, spot, strike, t, r, option)
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
    return {
        "t_years": t,
        "spot": spot,
        "calls": _reconstruct_side(frame.calls, strikes_rupees, spot, t, r, bs.CALL),
        "puts": _reconstruct_side(frame.puts, strikes_rupees, spot, t, r, bs.PUT),
    }
