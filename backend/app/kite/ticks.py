"""Helpers for reading raw fields out of a KiteTicker ``full``-mode tick dict.

A ``full`` tick looks like::

    {
      "instrument_token": 738561,
      "last_price": 2456.70,
      "volume_traded": 1234567,
      "total_buy_quantity": 1000, "total_sell_quantity": 900,
      "oi": 555000, "oi_day_high": 560000, "oi_day_low": 540000,
      "ohlc": {"open": ..., "high": ..., "low": ..., "close": ...},
      "depth": {"buy":  [{"price":.., "quantity":.., "orders":..}, x5],
                "sell": [{"price":.., "quantity":.., "orders":..}, x5]},
      ...
    }

Prices are rupees floats on the wire; we store integer **paise** (value * 100). Only
the raw fields our schema defines are read -- change / IV / Greeks are never stored
(docs/20-data-and-storage/bin-format.md).
"""

from __future__ import annotations

from typing import Any

# Empty depth level sentinel: price 0, qty 0, orders 0 (docs/failure-modes.md).
EMPTY_LEVEL = (0, 0, 0)


def to_paise(value: Any) -> int:
    """Rupees (float/str/None) -> integer paise. ``None`` -> 0."""
    if value is None:
        return 0
    return int(round(float(value) * 100))


def field_int(tick: dict, key: str) -> int:
    """Non-negative integer field with a 0 default (qty / OI / volume)."""
    value = tick.get(key)
    if value is None:
        return 0
    return int(value)


def ohlc_paise(tick: dict) -> tuple[int, int, int, int]:
    """(open, high, low, close) in paise; missing -> 0."""
    ohlc = tick.get("ohlc") or {}
    return (
        to_paise(ohlc.get("open")),
        to_paise(ohlc.get("high")),
        to_paise(ohlc.get("low")),
        to_paise(ohlc.get("close")),
    )


def depth_side(tick: dict, side: str, n_levels: int) -> list[tuple[int, int, int]]:
    """Return ``n_levels`` (price_paise, quantity, orders) tuples for ``side``.

    ``side`` is "buy" or "sell". Short/absent depth is padded with EMPTY_LEVEL.
    """
    depth = tick.get("depth") or {}
    levels = depth.get(side) or []
    out: list[tuple[int, int, int]] = []
    for i in range(n_levels):
        if i < len(levels):
            lvl = levels[i]
            out.append(
                (
                    to_paise(lvl.get("price")),
                    int(lvl.get("quantity") or 0),
                    int(lvl.get("orders") or 0),
                )
            )
        else:
            out.append(EMPTY_LEVEL)
    return out


def best_bid_ask(tick: dict) -> tuple[int, int, int, int]:
    """(bid_price, bid_qty, ask_price, ask_qty) in paise/qty from L1 of the book."""
    buy = depth_side(tick, "buy", 1)[0]
    sell = depth_side(tick, "sell", 1)[0]
    return (buy[0], buy[1], sell[0], sell[1])
