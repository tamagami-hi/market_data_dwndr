"""Tests for the KiteTicker -> asyncio bridge (no network)."""

from __future__ import annotations

import asyncio
import threading

from app.kite.ticker import MODE_FULL, TickerBridge


class FakeTicker:
    """Stand-in for kiteconnect.KiteTicker that records interactions."""

    def __init__(self, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.subscribed: list[int] = []
        self.mode: tuple[str, list[int]] | None = None
        self.connected_threaded: bool | None = None
        self.closed = False
        # callbacks assigned by the bridge
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None

    def connect(self, threaded: bool = False) -> None:
        self.connected_threaded = threaded
        # simulate the SDK firing on_connect right after connecting
        self.on_connect(self)

    def subscribe(self, tokens: list[int]) -> None:
        self.subscribed = list(tokens)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        self.mode = (mode, list(tokens))

    def close(self, *args, **kwargs) -> None:
        self.closed = True


def _make_bridge(tokens):
    created = {}

    def factory(api_key, access_token):
        t = FakeTicker(api_key, access_token)
        created["ticker"] = t
        return t

    bridge = TickerBridge("key", "tok", tokens, ticker_factory=factory)
    return bridge, created


async def test_start_subscribes_and_sets_full_mode():
    bridge, created = _make_bridge([1, 2, 3])
    bridge.start()
    ticker = created["ticker"]
    assert ticker.connected_threaded is True
    assert ticker.subscribed == [1, 2, 3]
    assert ticker.mode == (MODE_FULL, [1, 2, 3])
    assert bridge.connected is True


async def test_on_ticks_from_thread_reaches_async_queue():
    bridge, _ = _make_bridge([738561])
    bridge.bind_loop(asyncio.get_running_loop())

    batch = [{"instrument_token": 738561, "last_price": 2456.70}]

    # Fire the callback from a *different* thread, like KiteTicker does.
    t = threading.Thread(target=lambda: bridge._on_ticks(None, batch))
    t.start()
    t.join()

    received = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert received == batch
    assert bridge.batches_received == 1
    assert bridge.ticks_received == 1


async def test_queue_full_drops_oldest_and_stays_live():
    bridge, _ = _make_bridge([1])
    bridge.bind_loop(asyncio.get_running_loop())
    # tiny queue to force overflow
    bridge.queue = asyncio.Queue(maxsize=1)

    bridge._enqueue([{"instrument_token": 1, "n": 1}])
    bridge._enqueue([{"instrument_token": 1, "n": 2}])  # overflow -> drop oldest

    assert bridge.dropped_batches == 1
    latest = bridge.queue.get_nowait()
    assert latest[0]["n"] == 2  # newest survived


async def test_stop_closes_ticker():
    bridge, created = _make_bridge([1])
    bridge.start()
    bridge.stop()
    assert created["ticker"].closed is True
    assert bridge.connected is False



async def test_auth_error_from_ticker_thread_signals_event():
    bridge, _ = _make_bridge([1])
    bridge.bind_loop(asyncio.get_running_loop())

    thread = threading.Thread(
        target=lambda: bridge._on_error(None, 403, "Token is invalid or has expired")
    )
    thread.start()
    thread.join()

    await asyncio.wait_for(bridge.auth_failed.wait(), timeout=1.0)


async def test_non_auth_close_does_not_signal_auth_failure():
    bridge, _ = _make_bridge([1])
    bridge.bind_loop(asyncio.get_running_loop())

    thread = threading.Thread(
        target=lambda: bridge._on_close(None, 1006, "connection reset")
    )
    thread.start()
    thread.join()
    await asyncio.sleep(0)

    assert bridge.auth_failed.is_set() is False
