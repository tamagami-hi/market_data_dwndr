"""The connect guard: a ticker that never comes up must fail loudly, not silently.

Guards RC-1. ``kiteconnect.KiteTicker.connect(threaded=True)`` only starts the global
Twisted reactor ``if not reactor.running``, and ``close()`` never stops it — so the second
ticker in a process used to schedule ``connectWS`` cross-thread onto a live reactor and was
never serviced. No exception, no callback, no ticks. Production correlation was exact
across 38 capture starts: 1st-in-process connected 31/31, 2nd connected 0/7, costing a
~295 s hole plus five zero-filled frames per occurrence.

The fake below reproduces that exact shape — ``connect()`` returns normally and never
invokes ``on_connect`` — which is why a well-behaved fake could never have caught it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.capture.bootstrap import _await_live_feed
from app.kite.errors import KiteAuthenticationError
from app.kite.ticker import MODE_FULL, TickerBridge, TickerConnectFailed


class SilentTicker:
    """The production failure shape: connect() succeeds and nothing ever happens."""

    def __init__(self, api_key: str, access_token: str) -> None:
        self.subscribed: list[int] = []
        self.closed = False
        self.on_ticks = self.on_connect = self.on_close = None
        self.on_error = self.on_reconnect = None

    def connect(self, threaded: bool = False) -> None:
        return None  # no exception, no on_connect — exactly the dead-feed case

    def subscribe(self, tokens: list[int]) -> None:
        self.subscribed = list(tokens)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        return None

    def close(self, *args, **kwargs) -> None:
        self.closed = True


class LiveTicker(SilentTicker):
    """A ticker that behaves: fires on_connect the way the SDK does."""

    def connect(self, threaded: bool = False) -> None:
        self.on_connect(self)


def _bridge(ticker_cls, tokens=(1, 2, 3)) -> TickerBridge:
    return TickerBridge(
        "key", "tok", list(tokens), ticker_factory=lambda k, t: ticker_cls(k, t)
    )


async def test_a_confirmed_connection_sets_the_event_and_subscribes_everything():
    bridge = _bridge(LiveTicker)
    bridge.start(asyncio.get_running_loop())

    await asyncio.wait_for(bridge.await_connected(timeout=1.0), timeout=2.0)

    assert bridge.connected is True
    assert bridge.connect_confirmed.is_set()
    assert bridge.connects_confirmed == 1
    assert bridge.connect_attempts == 1
    assert bridge._ticker.subscribed == [1, 2, 3]


async def test_a_silent_ticker_raises_instead_of_reporting_health():
    """The regression test for RC-1: silence must become a named failure."""
    bridge = _bridge(SilentTicker)
    bridge.start(asyncio.get_running_loop())

    assert bridge.connected is False, "no confirmation means not connected"
    with pytest.raises(TickerConnectFailed):
        await bridge.await_connected(timeout=0.05)


async def test_run_capture_refuses_to_start_the_grid_without_a_live_feed():
    """``_await_live_feed`` is the gate that keeps a dead socket out of the 1 Hz loop."""
    bridge = _bridge(SilentTicker)
    bridge.start(asyncio.get_running_loop())

    with pytest.raises(TickerConnectFailed) as excinfo:
        await _await_live_feed(bridge, 0.05)
    assert "never connected" in str(excinfo.value)


async def test_a_live_feed_passes_the_gate_immediately():
    bridge = _bridge(LiveTicker)
    bridge.start(asyncio.get_running_loop())
    await asyncio.wait_for(_await_live_feed(bridge, 1.0), timeout=2.0)


async def test_an_auth_failure_is_reported_as_auth_not_as_a_timeout():
    """A rejected token must not be misdiagnosed as a slow socket."""
    bridge = _bridge(SilentTicker)
    bridge.bind_loop(asyncio.get_running_loop())
    bridge.auth_failed.set()

    with pytest.raises(KiteAuthenticationError):
        await _await_live_feed(bridge, 5.0)


async def test_every_reconnect_re_arms_the_confirmation():
    """A replacement socket must prove itself again rather than inherit confirmation."""
    bridge = _bridge(LiveTicker)
    bridge.start(asyncio.get_running_loop())
    await asyncio.wait_for(bridge.await_connected(timeout=1.0), timeout=2.0)
    assert bridge.connect_confirmed.is_set()

    bridge._factory = lambda k, t: SilentTicker(k, t)
    with pytest.raises(TickerConnectFailed):
        await bridge.reconnect_confirmed(timeout=0.05)

    assert bridge.connect_confirmed.is_set() is False
    assert bridge.connect_attempts == 2


async def test_full_token_universe_is_replayed_on_every_connect():
    """Resubscription completeness: never a subset (this part was always correct)."""
    tokens = list(range(500))
    bridge = _bridge(LiveTicker, tokens=tokens)
    bridge.start()
    assert bridge._ticker.subscribed == tokens

    bridge.reconnect()
    assert bridge._ticker.subscribed == tokens, "a reconnect must resubscribe everything"
    assert bridge.tokens == tokens, "the authoritative token list is immutable"


async def test_mode_is_always_full_so_oi_and_depth_arrive():
    bridge = _bridge(LiveTicker)
    captured = {}

    class ModeRecordingTicker(LiveTicker):
        def set_mode(self, mode, tokens):
            captured["mode"] = mode

    bridge._factory = lambda k, t: ModeRecordingTicker(k, t)
    bridge.start()
    assert captured["mode"] == MODE_FULL
