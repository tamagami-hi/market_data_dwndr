"""Tests for tick-field extraction helpers."""

from __future__ import annotations

from app.kite.ticks import (
    EMPTY_LEVEL,
    best_bid_ask,
    depth_side,
    field_int,
    ohlc_paise,
    to_paise,
)


def _full_tick() -> dict:
    return {
        "instrument_token": 738561,
        "last_price": 2456.70,
        "volume_traded": 1_234_567,
        "total_buy_quantity": 1000,
        "total_sell_quantity": 900,
        "oi": 555_000,
        "oi_day_high": 560_000,
        "oi_day_low": 540_000,
        "ohlc": {"open": 2450.0, "high": 2470.5, "low": 2445.25, "close": 2448.0},
        "depth": {
            "buy": [
                {"price": 2456.65, "quantity": 50, "orders": 3},
                {"price": 2456.60, "quantity": 75, "orders": 5},
            ],
            "sell": [
                {"price": 2456.75, "quantity": 40, "orders": 2},
            ],
        },
    }


def test_to_paise_rounds_and_handles_none():
    assert to_paise(2456.70) == 245670
    assert to_paise(0) == 0
    assert to_paise(None) == 0
    assert to_paise("12.34") == 1234


def test_field_int_default_zero():
    assert field_int({"oi": 5}, "oi") == 5
    assert field_int({}, "oi") == 0
    assert field_int({"oi": None}, "oi") == 0


def test_ohlc_paise():
    assert ohlc_paise(_full_tick()) == (245000, 247050, 244525, 244800)
    assert ohlc_paise({}) == (0, 0, 0, 0)


def test_depth_side_pads_to_requested_levels():
    levels = depth_side(_full_tick(), "buy", 5)
    assert len(levels) == 5
    assert levels[0] == (245665, 50, 3)
    assert levels[1] == (245660, 75, 5)
    assert levels[2] == EMPTY_LEVEL  # padded
    # sell side has only one real level
    sell = depth_side(_full_tick(), "sell", 5)
    assert sell[0] == (245675, 40, 2)
    assert sell[1] == EMPTY_LEVEL


def test_best_bid_ask():
    bid_p, bid_q, ask_p, ask_q = best_bid_ask(_full_tick())
    assert (bid_p, bid_q, ask_p, ask_q) == (245665, 50, 245675, 40)
