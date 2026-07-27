"""Tests for the reliability/performance fixes on the display + broadcast path."""

from __future__ import annotations

import json
import math

from app.capture.broadcaster import _finite
from app.reconstruct.bs import CALL, implied_vol
from app.ws.routes import ConnectionManager

# --- JSON safety --------------------------------------------------------------


def test_finite_guards_infinities_and_nan():
    """Non-finite floats must never reach json.dumps: JSON.parse rejects them."""
    assert _finite(float("inf")) == 0.0
    assert _finite(float("-inf")) == 0.0
    assert _finite(float("nan")) == 0.0
    assert _finite(None) == 0.0
    assert _finite(1.23456789, 4) == 1.2346


def test_finite_output_is_json_parseable():
    payload = {"gamma": _finite(float("inf")), "iv": _finite(float("nan"))}
    text = json.dumps(payload)
    assert "Infinity" not in text and "NaN" not in text
    assert json.loads(text) == {"gamma": 0.0, "iv": 0.0}


# --- implied vol bracket ------------------------------------------------------


def test_implied_vol_returns_none_when_price_above_bracket():
    """A price unreachable within sigma<=8 must short-circuit, not bisect 200 times."""
    # An absurdly high premium (above the price at the bracket's upper vol) has no root.
    assert implied_vol(1_000_000.0, 100.0, 100.0, 0.05, 0.06, CALL) is None


def test_implied_vol_still_solves_a_normal_quote():
    from app.reconstruct.bs import bs_price

    true_sigma = 0.25
    price = bs_price(100.0, 100.0, 0.5, 0.06, true_sigma, CALL)
    solved = implied_vol(price, 100.0, 100.0, 0.5, 0.06, CALL)
    assert solved is not None
    assert math.isclose(solved, true_sigma, abs_tol=1e-3)


def test_implied_vol_below_intrinsic_is_none():
    assert implied_vol(0.01, 200.0, 100.0, 0.5, 0.06, CALL) is None


# --- broadcast serialization --------------------------------------------------


class _FakeSocket:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.json_calls = 0

    async def accept(self) -> None:  # pragma: no cover - not used here
        pass

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_json(self, message: dict) -> None:  # pragma: no cover
        self.json_calls += 1


async def test_broadcast_serializes_once_and_sends_text_to_all():
    hub = ConnectionManager()
    a, b = _FakeSocket(), _FakeSocket()
    hub._topics["market-data"].add(a)
    hub._topics["market-data"].add(b)

    sent = await hub.broadcast("market-data", {"type": "X", "payload": {"n": 1}})

    assert sent == 2
    # Both clients received the identical serialized text (encoded once, not per client).
    assert a.texts == b.texts == ['{"type": "X", "payload": {"n": 1}}']
    assert a.json_calls == 0 and b.json_calls == 0


async def test_broadcast_with_no_subscribers_skips_serialization():
    hub = ConnectionManager()
    assert await hub.broadcast("stocks", {"type": "X"}) == 0


async def test_broadcast_prunes_a_broken_client():
    class _Broken(_FakeSocket):
        async def send_text(self, text: str) -> None:
            raise RuntimeError("socket closed")

    hub = ConnectionManager()
    good, bad = _FakeSocket(), _Broken()
    hub._topics["stocks"].add(good)
    hub._topics["stocks"].add(bad)

    sent = await hub.broadcast("stocks", {"type": "X"})

    assert sent == 1
    assert hub.count("stocks") == 1  # the broken socket was pruned
