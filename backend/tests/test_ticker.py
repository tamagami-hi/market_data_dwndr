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


async def test_reconnect_replaces_ticker_and_resubscribes():
    created: list[FakeTicker] = []

    def factory(api_key, access_token):
        t = FakeTicker(api_key, access_token)
        created.append(t)
        return t

    bridge = TickerBridge("key", "tok", [1, 2], ticker_factory=factory)
    bridge.start()
    assert len(created) == 1
    first = created[0]

    bridge.reconnect()

    # Old ticker closed, a brand-new one created and re-subscribed in full mode.
    assert first.closed is True
    assert len(created) == 2
    assert created[1].subscribed == [1, 2]
    assert created[1].mode == (MODE_FULL, [1, 2])
    assert bridge.reconnects == 1
    assert bridge.connected is True



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


# --- reconnect drill + GC hygiene --------------------------------------------
#
# The live capture path no longer performs in-process reconnects at all: recovery is
# restart-first (see ``CaptureEngine.observe_feed_health``). ``reconnect()`` is kept for
# the operator drill documented in docs/30-live-capture/live-data-pipeline.md, so the
# behaviour it must still guarantee is "a replacement socket resubscribes everything".


def test_the_live_path_has_no_token_refresh_ladder():
    """Regression guard for the destructive tier-2 refresh.

    It fired ~27 times in the 2026-08-06 session, obtained a token on none of them
    (``token_refreshes`` was 0 on every recorded day), and deleted that day's persisted
    session each attempt. It must not come back.
    """
    assert not hasattr(TickerBridge, "reconnect_with_refresh")


async def test_reconnect_resubscribes_every_token_in_full_mode():
    """The drill's success criterion: a replacement socket is fully subscribed."""
    created: list[FakeTicker] = []

    def factory(api_key, access_token):
        t = FakeTicker(api_key, access_token)
        created.append(t)
        return t

    bridge = TickerBridge("key", "TOK", [1, 2], ticker_factory=factory)
    bridge.bind_loop(asyncio.get_running_loop())
    bridge.start()

    bridge.reconnect()

    assert len(created) == 2  # a brand-new socket, not the old one reused
    assert created[-1].access_token == "TOK"
    assert created[-1].subscribed == [1, 2]
    assert created[-1].mode == (MODE_FULL, [1, 2])
    assert bridge.reconnects == 1


async def test_reconnect_frees_the_superseded_ticker():
    """The stale ticker (and its token copy) must be collectable after a reconnect."""
    import gc
    import weakref

    created: list[FakeTicker] = []

    def factory(api_key, access_token):
        t = FakeTicker(api_key, access_token)
        created.append(t)
        return t

    bridge = TickerBridge("key", "tok", [1], ticker_factory=factory)
    bridge.bind_loop(asyncio.get_running_loop())
    bridge.start()

    ref = weakref.ref(created[0])
    created.clear()  # drop the test's own reference to the first ticker

    bridge.reconnect()  # replaces it; nothing should still reference the old one
    gc.collect()

    assert ref() is None  # the superseded ticker was garbage-collected


def test_token_age_advances_with_the_clock():
    times = iter([1_000, 1_000, 9_000])
    bridge = TickerBridge("key", "tok", [1], clock=lambda: next(times))
    # __init__ stamps the token at the first clock() == 1000; next reads are 1000, 9000.
    assert bridge.token_age_ms() == 0
    assert bridge.token_age_ms() == 8_000
