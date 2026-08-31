"""Freeze paths: a live-looking pipeline that has silently stopped delivering data.

Each test here pins one confirmed failure, all of which shared a symptom — the socket
looks fine, every counter reads healthy, and the data never changes again:

* one malformed tick used to terminate the tick consumer, freezing every table while the
  1 Hz grid kept writing the stale state;
* a subscribe/set_mode failure left the transport open but unsubscribed, so ``on_close``
  never fired and no ticks ever arrived;
* ``reconnect()`` reported success as soon as a ticker object existed, so a dead
  replacement looked like recovery;
* one websocket client that never drained blocked the broadcast loop, stopping every topic
  for every client;
* a websocket that closed by any route other than ``WebSocketDisconnect`` leaked into the
  hub and was sent to once a second forever.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import WebSocketDisconnect

from app.capture.engine import (
    CaptureConsumerFailed,
    CaptureEngine,
    CaptureStalledError,
)
from app.chain.assembler import ROLE_OPTION, ROLE_SPOT, ROLE_VIX, OptionChain, Role
from app.chain.table import IndexTable
from app.kite.ticks import depth_side, field_int
from app.ws.routes import SEND_TIMEOUT_S, ConnectionManager

# --------------------------------------------------------------------------- #
# 1. A malformed tick must not kill ingestion
# --------------------------------------------------------------------------- #

TOKEN_CE, TOKEN_PE = 11, 21


def _chain(n: int = 3) -> OptionChain:
    strikes = np.arange(2_200_000, 2_200_000 + n * 5_000, 5_000, dtype="<i8")
    call_tokens = np.array([TOKEN_CE + i for i in range(n)], dtype="<u8")
    put_tokens = np.array([TOKEN_PE + i for i in range(n)], dtype="<u8")
    token_map: dict[int, Role] = {}
    for i in range(n):
        token_map[int(call_tokens[i])] = Role(ROLE_OPTION, side="CE", index=i)
        token_map[int(put_tokens[i])] = Role(ROLE_OPTION, side="PE", index=i)
    token_map[999_999] = Role(ROLE_SPOT)
    token_map[264_969] = Role(ROLE_VIX)
    return OptionChain(
        "NIFTY", "2026-09-01", 2_250_000, 2_250_000,
        strikes, call_tokens, put_tokens, token_map,
    )


def _engine() -> tuple[CaptureEngine, IndexTable]:
    table = IndexTable(_chain(), 0.0526, "2026-09-01")
    engine = CaptureEngine({"NIFTY": table}, None, {}, None, clock=lambda: 1_000)
    return engine, table


def test_field_int_clamps_a_negative_count_instead_of_overflowing():
    """A negative count is nonsense, not a parse failure: the column is unsigned."""
    assert field_int({"oi": -5}, "oi") == 0
    assert field_int({"oi": 7}, "oi") == 7
    assert field_int({}, "oi") == 0
    assert field_int({"oi": None}, "oi") == 0
    # It must actually be assignable to the unsigned column it feeds.
    column = np.zeros(1, dtype=np.uint64)
    column[0] = field_int({"oi": -5}, "oi")
    assert int(column[0]) == 0


def test_depth_quantities_are_clamped_too():
    levels = {"depth": {"buy": [{"price": 10.0, "quantity": -3, "orders": -1}]}}
    assert depth_side(levels, "buy", 1) == [(1_000, 0, 0)]


def test_a_negative_oi_tick_no_longer_raises_through_apply():
    engine, table = _engine()
    applied = engine.apply_ticks([{"instrument_token": TOKEN_CE, "last_price": 100.0, "oi": -5}])
    assert applied == 1
    assert engine.malformed_ticks == 0, "a clamped count is valid input, not malformed"
    assert int(table.calls.columns["oi"][0]) == 0
    assert int(table.calls.columns["ltp"][0]) == 10_000


def test_one_malformed_tick_is_counted_and_the_rest_of_the_batch_still_applies():
    """The narrow guarantee: a poison tick costs itself, never the batch."""
    engine, table = _engine()
    batch = [
        {"instrument_token": TOKEN_CE, "last_price": 100.0, "oi": 1},
        {"instrument_token": TOKEN_CE + 1, "last_price": "not-a-price", "oi": 2},
        {"instrument_token": TOKEN_CE + 2, "last_price": 300.0, "oi": 3},
    ]
    applied = engine.apply_ticks(batch)

    assert engine.malformed_ticks == 1
    assert applied == 2, "the two good ticks in the same batch still landed"
    assert int(table.calls.columns["ltp"][0]) == 10_000
    assert int(table.calls.columns["ltp"][1]) == 0, "the malformed strike is untouched"
    assert int(table.calls.columns["ltp"][2]) == 30_000


def test_a_malformed_tick_does_not_refresh_artifact_freshness():
    """A rejected tick must not look like a healthy update for that artifact."""
    engine, _table = _engine()
    engine.apply_ticks([{"instrument_token": TOKEN_CE, "last_price": "bad"}], now=5_000)
    assert engine.malformed_ticks == 1
    assert engine.artifact_last_update_ms.get("NIFTY") is None


def test_malformed_tick_logging_is_rate_limited_but_always_counted(caplog):
    engine, _table = _engine()
    bad = {"instrument_token": TOKEN_CE, "last_price": "bad", "oi": -1}
    with caplog.at_level("ERROR"):
        for i in range(50):
            engine.apply_ticks([dict(bad)], now=1_000 + i)
    assert engine.malformed_ticks == 50
    rejections = [r for r in caplog.records if "malformed tick" in r.getMessage()]
    assert len(rejections) == 1, "throttled to one line per window"
    assert "token=11" in rejections[0].getMessage()
    assert "last_price='bad'" in rejections[0].getMessage()


async def test_a_malformed_tick_does_not_terminate_the_consumer():
    """The original freeze: the consumer survives, and later batches still apply."""
    engine, table = _engine()

    class Bridge:
        async def batches(self):
            yield [{"instrument_token": TOKEN_CE, "last_price": 100.0}]
            yield [{"instrument_token": TOKEN_CE, "last_price": "poison"}]
            yield [{"instrument_token": TOKEN_CE, "last_price": 400.0}]
            await asyncio.sleep(3600)  # stay alive like the real feed

    consumer = asyncio.create_task(engine._consume(Bridge()))
    await asyncio.sleep(0.05)

    assert not consumer.done(), "the consumer must still be running"
    assert engine.malformed_ticks == 1
    assert int(table.calls.columns["ltp"][0]) == 40_000, "the later batch was applied"
    engine.check_pipeline_alive(consumer)  # healthy: must not raise

    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)


# --------------------------------------------------------------------------- #
# 2. Consumer death becomes visible immediately
# --------------------------------------------------------------------------- #


async def test_consumer_death_is_surfaced_on_the_next_grid_tick():
    """Structural failure: detected now, not inferred ~60 s later from staleness."""
    engine, _table = _engine()

    class BrokenBridge:
        async def batches(self):
            raise RuntimeError("transport exploded")
            yield []  # pragma: no cover - unreachable, keeps this an async generator

    consumer = asyncio.create_task(engine._consume(BrokenBridge()))
    await asyncio.gather(consumer, return_exceptions=True)

    assert consumer.done()
    assert isinstance(engine.consumer_error, RuntimeError)
    with pytest.raises(CaptureConsumerFailed, match="tick consumer is no longer running"):
        engine.check_pipeline_alive(consumer)


async def test_a_consumer_that_exits_without_an_exception_is_also_fatal():
    """A finished generator means ingestion stopped, however politely."""
    engine, _table = _engine()

    class FinishedBridge:
        async def batches(self):
            return
            yield []  # pragma: no cover - unreachable

    consumer = asyncio.create_task(engine._consume(FinishedBridge()))
    await asyncio.gather(consumer, return_exceptions=True)

    with pytest.raises(CaptureConsumerFailed):
        engine.check_pipeline_alive(consumer)


async def test_a_cancelled_consumer_is_not_treated_as_a_failure():
    """Normal shutdown cancels the consumer; that must not raise."""
    engine, _table = _engine()

    class IdleBridge:
        async def batches(self):
            await asyncio.sleep(3600)
            yield []  # pragma: no cover

    consumer = asyncio.create_task(engine._consume(IdleBridge()))
    await asyncio.sleep(0)
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)

    engine.check_pipeline_alive(consumer)  # must not raise


async def test_an_unrecoverable_bridge_escalates_immediately():
    engine, _table = _engine()

    class Bridge:
        feed_unrecoverable = asyncio.Event()
        subscribe_failures = 2
        reconnect_failures = 3

    Bridge.feed_unrecoverable.set()
    with pytest.raises(CaptureStalledError, match="confirmed subscription"):
        engine.check_pipeline_alive(None, Bridge())


# --------------------------------------------------------------------------- #
# 3. Broadcast: one wedged client must not block the topic
# --------------------------------------------------------------------------- #


class HangingClient:
    """A client whose send never completes (full receive window / stalled proxy)."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send_text(self, text: str) -> None:
        self.attempts += 1
        await asyncio.Event().wait()


class GoodClient:
    def __init__(self) -> None:
        self.received: list[str] = []

    async def send_text(self, text: str) -> None:
        self.received.append(text)


class FailingClient:
    async def send_text(self, text: str) -> None:
        raise ConnectionResetError("peer went away")


async def test_a_hanging_client_cannot_block_healthy_subscribers(monkeypatch):
    monkeypatch.setattr("app.ws.routes.SEND_TIMEOUT_S", 0.05)
    hub = ConnectionManager()
    hanging, healthy = HangingClient(), GoodClient()
    hub._topics["market-data"] = {hanging, healthy}

    sent = await asyncio.wait_for(hub.broadcast("market-data", {"type": "grid"}), timeout=2.0)

    assert sent == 1, "the healthy client was still served"
    assert len(healthy.received) == 1
    assert hub.send_timeouts == 1
    assert hub.count("market-data") == 1, "the wedged client was pruned"


async def test_a_pruned_client_is_not_retried_on_the_next_broadcast(monkeypatch):
    monkeypatch.setattr("app.ws.routes.SEND_TIMEOUT_S", 0.05)
    hub = ConnectionManager()
    hanging, healthy = HangingClient(), GoodClient()
    hub._topics["market-data"] = {hanging, healthy}

    await hub.broadcast("market-data", {"type": "grid"})
    await hub.broadcast("market-data", {"type": "grid"})

    assert hanging.attempts == 1, "pruned after the first timeout, never attempted again"
    assert len(healthy.received) == 2


async def test_a_client_whose_send_raises_is_pruned_and_counted():
    hub = ConnectionManager()
    healthy = GoodClient()
    hub._topics["stocks"] = {FailingClient(), healthy}

    sent = await hub.broadcast("stocks", {"type": "stock_board"})

    assert sent == 1
    assert hub.send_failures == 1
    assert hub.count("stocks") == 1


async def test_broadcast_and_count_do_not_mint_empty_topic_entries():
    hub = ConnectionManager()
    assert await hub.broadcast("never-used", {"a": 1}) == 0
    assert hub.count("never-used") == 0
    assert "never-used" not in hub._topics


# --------------------------------------------------------------------------- #
# 4. Hub cleanup on every exit path
# --------------------------------------------------------------------------- #


class _Settings:
    cors_origins = ["http://testserver"]


class FakeWebSocket:
    """Minimal websocket driving the real endpoint, including its failure paths."""

    def __init__(
        self,
        *,
        origin: str = "http://testserver",
        send_json_error: type[BaseException] | None = None,
        receive_error: type[BaseException] | None = None,
    ) -> None:
        self.headers = {"origin": origin}
        self.app = SimpleNamespace(state=SimpleNamespace(settings=_Settings()))
        self.accepted = False
        self.closed_with: int | None = None
        self.sent: list[dict] = []
        self._send_json_error = send_json_error
        self._receive_error = receive_error

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        if self._send_json_error is not None:
            raise self._send_json_error("handshake failed")
        self.sent.append(data)

    async def receive_text(self) -> str:
        raise (self._receive_error or WebSocketDisconnect)(1000)

    async def close(self, code: int | None = None) -> None:
        self.closed_with = code


def _real_endpoint():
    from app.ws.routes import create_ws_router

    hub = ConnectionManager()
    router = create_ws_router(hub)
    return hub, router.routes[0].endpoint  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "receive_error",
    [WebSocketDisconnect, ConnectionResetError, RuntimeError, ValueError],
    ids=["client-disconnect", "reset", "runtime", "unexpected"],
)
async def test_every_endpoint_exit_path_deregisters_the_socket(receive_error):
    """No termination route may leave a subscriber behind in the hub."""
    hub, endpoint = _real_endpoint()
    socket = FakeWebSocket(receive_error=receive_error)

    await endpoint(socket, "market-data")

    assert socket.accepted is True
    assert hub.count("market-data") == 0, "no exit path may leak a subscriber"


async def test_cancellation_deregisters_and_still_propagates():
    """Server shutdown cancels the handler; the socket must still be removed."""
    hub, endpoint = _real_endpoint()
    socket = FakeWebSocket(receive_error=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await endpoint(socket, "session")

    assert hub.count("session") == 0


async def test_a_failed_handshake_after_registration_does_not_leak():
    """``send_json`` used to run outside the try, so its failure leaked the client."""
    hub, endpoint = _real_endpoint()
    socket = FakeWebSocket(send_json_error=RuntimeError)

    await endpoint(socket, "session")

    assert socket.sent == [], "the handshake never got out"
    assert hub.count("session") == 0


async def test_a_rejected_origin_is_never_registered():
    hub, endpoint = _real_endpoint()
    socket = FakeWebSocket(origin="http://evil.example")

    await endpoint(socket, "market-data")

    assert socket.accepted is False
    assert socket.closed_with == 1008
    assert hub.count("market-data") == 0


async def test_a_disallowed_topic_is_never_registered():
    hub, endpoint = _real_endpoint()
    socket = FakeWebSocket()

    await endpoint(socket, "not-a-topic")

    assert socket.accepted is False
    assert socket.closed_with == 1008
    assert hub.count("not-a-topic") == 0


def test_send_timeout_is_short_enough_for_a_1hz_dashboard():
    """Guards against someone raising this to the ping-timeout scale again."""
    assert 0 < SEND_TIMEOUT_S <= 5.0



# --------------------------------------------------------------------------- #
# 5. Ticker: an open-but-unsubscribed socket is a zombie, not a connection
# --------------------------------------------------------------------------- #


class _Ticker:
    """A ticker whose subscribe/set_mode outcome the test controls."""

    def __init__(self, api_key: str, access_token: str, *, fail: str | None = None) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._fail = fail
        self.subscribed: list[int] = []
        self.mode: tuple[str, list[int]] | None = None
        self.closed = False
        self.on_ticks = self.on_connect = self.on_close = None
        self.on_error = self.on_reconnect = None

    def connect(self, threaded: bool = False) -> None:
        self.on_connect(self)  # the SDK fires on_connect right after the transport is up

    def subscribe(self, tokens: list[int]) -> None:
        if self._fail == "subscribe":
            raise OSError("subscribe rejected")
        self.subscribed = list(tokens)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        if self._fail == "set_mode":
            raise OSError("set_mode rejected")
        self.mode = (mode, list(tokens))

    def close(self, *args, **kwargs) -> None:
        self.closed = True


def _factory(created: list[_Ticker], *, fail_first: str | None = None, fail_all=None):
    def make(api_key: str, access_token: str) -> _Ticker:
        fail = fail_all if fail_all is not None else (fail_first if not created else None)
        ticker = _Ticker(api_key, access_token, fail=fail)
        created.append(ticker)
        return ticker

    return make


@pytest.mark.parametrize("failing_call", ["subscribe", "set_mode"])
async def test_a_subscribe_failure_is_never_reported_as_a_confirmed_connection(failing_call):
    """Confirmation requires transport + subscribe + FULL mode, not just a socket."""
    from app.kite.ticker import TickerBridge, TickerConnectFailed

    created: list[_Ticker] = []
    bridge = TickerBridge(
        "key", "tok", [1, 2], ticker_factory=_factory(created, fail_all=failing_call)
    )
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._start_ticker()

    assert bridge.connected is False
    assert bridge.connect_confirmed.is_set() is False
    assert bridge.subscribe_failures == 1
    with pytest.raises(TickerConnectFailed):
        await bridge.await_connected(timeout=0.05)

    bridge.stop()


async def test_a_subscribe_failure_tears_down_the_socket_and_recovers():
    """No zombie: the unsubscribed socket is closed and a confirmed one replaces it."""
    from app.kite.ticker import TickerBridge

    created: list[_Ticker] = []
    bridge = TickerBridge(
        "key", "tok", [1, 2], ticker_factory=_factory(created, fail_first="subscribe")
    )
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._start_ticker()

    # _on_connect schedules recovery on the loop; let it run.
    await asyncio.sleep(0)
    await asyncio.wait_for(bridge.connect_confirmed.wait(), timeout=1.0)

    assert created[0].closed is True, "the unsubscribed socket must not be left open"
    assert len(created) == 2
    assert created[1].subscribed == [1, 2]
    assert created[1].mode[0] == "full"
    assert bridge.connected is True
    assert bridge.feed_unrecoverable.is_set() is False

    bridge.stop()


async def test_an_exhausted_reconnect_ladder_flags_the_feed_unrecoverable(monkeypatch):
    """Every rung fails -> escalate now, rather than waiting for the stale deadline."""
    from app.kite.ticker import RECOVERY_MAX_ATTEMPTS, TickerBridge

    monkeypatch.setattr("app.kite.ticker.RECOVERY_CONFIRM_TIMEOUT_S", 0.02)
    created: list[_Ticker] = []
    bridge = TickerBridge(
        "key", "tok", [1], ticker_factory=_factory(created, fail_all="subscribe")
    )
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._start_ticker()

    await asyncio.sleep(0)
    task = bridge._recovery_task
    assert task is not None
    await asyncio.wait_for(task, timeout=5.0)

    assert bridge.feed_unrecoverable.is_set() is True
    assert bridge.reconnect_failures == RECOVERY_MAX_ATTEMPTS
    assert bridge.connect_confirmed.is_set() is False
    assert created[-1].closed is True, "the last bad socket is closed, not abandoned open"

    # And the engine turns that into an immediate fatal rather than a 60 s wait.
    engine, _table = _engine()
    with pytest.raises(CaptureStalledError):
        engine.check_pipeline_alive(None, bridge)

    bridge.stop()


async def test_only_one_recovery_ladder_runs_at_a_time(monkeypatch):
    """Repeated failures must not stack recovery tasks."""
    from app.kite.ticker import TickerBridge

    monkeypatch.setattr("app.kite.ticker.RECOVERY_CONFIRM_TIMEOUT_S", 0.02)
    created: list[_Ticker] = []
    bridge = TickerBridge(
        "key", "tok", [1], ticker_factory=_factory(created, fail_all="subscribe")
    )
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._schedule_recovery("first")
    first = bridge._recovery_task
    bridge._schedule_recovery("second")

    assert bridge._recovery_task is first, "a second ladder must not be started"

    await asyncio.wait_for(first, timeout=5.0)
    bridge.stop()


async def test_an_auth_rejected_subscription_does_not_spin_reconnects():
    """A token problem cannot be fixed by another socket, so don't churn."""
    from app.kite.ticker import TickerBridge

    class AuthReject(_Ticker):
        def subscribe(self, tokens):
            raise PermissionError("Invalid access token")

    created: list[AuthReject] = []

    def make(api_key, access_token):
        t = AuthReject(api_key, access_token)
        created.append(t)
        return t

    bridge = TickerBridge("key", "tok", [1], ticker_factory=make)
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._start_ticker()
    await asyncio.sleep(0.01)

    assert bridge.auth_failed.is_set() is True
    assert len(created) == 1, "no replacement socket for an auth failure"
    assert bridge._recovery_task is None

    bridge.stop()


async def test_ticks_arriving_without_a_bound_loop_are_counted_not_silently_dropped():
    from app.kite.ticker import TickerBridge

    bridge = TickerBridge("key", "tok", [1], ticker_factory=lambda k, t: _Ticker(k, t))
    bridge._on_ticks(None, [{"instrument_token": 1}])
    assert bridge.dropped_unbound_batches == 1
    assert bridge.batches_received == 0


async def test_stop_unbinds_the_loop_so_a_late_callback_cannot_raise():
    """Shutdown race: a tick after stop() must not throw inside the KiteTicker thread."""
    from app.kite.ticker import TickerBridge

    created: list[_Ticker] = []
    bridge = TickerBridge("key", "tok", [1], ticker_factory=_factory(created))
    bridge.bind_loop(asyncio.get_running_loop())
    bridge._start_ticker()
    bridge.stop()

    bridge._on_ticks(None, [{"instrument_token": 1}])  # must not raise
    assert bridge.dropped_unbound_batches == 1
