"""Black-Scholes pricing, Greeks, and implied volatility.

Conventions mirror algo_engine's ``oc_maker/bs_models.rs``: **theta per calendar day**
and **vega / rho per 1% move** (the values traders read). No dividend yield (index /
single-stock options priced off spot). All inputs in rupees / decimals / years.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CALL = "CE"
PUT = "PE"

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def d1_d2(spot: float, strike: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        raise ValueError("spot, strike, t, sigma must be > 0")
    vol_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def bs_price(spot: float, strike: float, t: float, r: float, sigma: float, option: str) -> float:
    d1, d2 = d1_d2(spot, strike, t, r, sigma)
    disc = math.exp(-r * t)
    if option == CALL:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    iv: float
    delta: float
    gamma: float
    vega: float  # per 1% vol move
    theta: float  # per calendar day
    rho: float  # per 1% rate move


def greeks(spot: float, strike: float, t: float, r: float, sigma: float, option: str) -> Greeks:
    d1, d2 = d1_d2(spot, strike, t, r, sigma)
    disc = math.exp(-r * t)
    pdf = _norm_pdf(d1)
    sqrt_t = math.sqrt(t)

    gamma = pdf / (spot * sigma * sqrt_t)
    vega = spot * pdf * sqrt_t / 100.0  # per 1%
    if option == CALL:
        delta = _norm_cdf(d1)
        theta_year = -(spot * pdf * sigma) / (2 * sqrt_t) - r * strike * disc * _norm_cdf(d2)
        rho = strike * t * disc * _norm_cdf(d2) / 100.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_year = -(spot * pdf * sigma) / (2 * sqrt_t) + r * strike * disc * _norm_cdf(-d2)
        rho = -strike * t * disc * _norm_cdf(-d2) / 100.0
    return Greeks(sigma, delta, gamma, vega, theta_year / 365.0, rho)


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    t: float,
    r: float,
    option: str,
    *,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Solve BS implied vol from an option price (Newton + bisection fallback).

    Returns ``None`` when the price is below intrinsic value or no root is found.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    disc = math.exp(-r * t)
    intrinsic = max(spot - strike * disc, 0.0) if option == CALL else max(strike * disc - spot, 0.0)
    if price < intrinsic - tol:
        return None

    sigma = 0.2  # seed
    for _ in range(max_iter):
        try:
            diff = bs_price(spot, strike, t, r, sigma, option) - price
        except ValueError:
            break
        if abs(diff) < tol:
            return sigma
        vega_raw = spot * _norm_pdf(d1_d2(spot, strike, t, r, sigma)[0]) * math.sqrt(t)
        if vega_raw < 1e-12:
            break
        sigma -= diff / vega_raw
        if sigma <= 0 or sigma > 10:
            break

    # Bisection fallback over a wide vol range.
    lo, hi = 1e-4, 8.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        diff = bs_price(spot, strike, t, r, mid, option) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return None
