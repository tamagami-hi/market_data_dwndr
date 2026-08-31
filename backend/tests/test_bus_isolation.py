"""The bus must never become a dependency of capture or persistence.

Every test here asserts the same invariant from a different failure angle: with the bus
absent, dead, full, restarting, or subscriber-less, TickVault keeps applying ticks, keeps
its tables correct, and keeps its counters honest. A bus fault is allowed to show up as
degraded bus telemetry and nothing else — never as capture loss, and never as a reason to
stop or restart capture.

Also covered: fidelity. The values a downstream consumer decodes must equal the values the
capture path applied to its own tables, because the bus and the BIN archive have to agree.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.bus.normalize import epoch_seconds, normalize_tick
from app.bus.protocol import (
    NORMTICK_SIZE,
    TOPIC_SESSION,
    TOPIC_TICK,
    decode_header,
    decode_session,
    decode_tick_batch,
)
from app.bus.publisher import BusPublisher, NullBusPublisher, build_publisher
from app.bus.session import build_instruments
from app.capture.engine import CaptureEngine
from app.chain.assembler import ROLE_OPTION, ROLE_SPOT, ROLE_VIX, OptionChain, Role
from app.chain.table import IndexTable

TOKEN_CE, TOKEN_PE = 11, 21


def _chain(n: int = 3) -> OptionChain:
    strikes = np.arange(2_200_000, 2_200_000 + n * 5_000, 5_000, dtype="<i8")
    call_tokens = np.array([TOKEN_CE + i for i in range(n)], dtype="<u8")
    put_tokens = np.array([TOKEN_PE + i for i in range(n)], dtype="<u8")
    token_map: dict[int, Role] = {}
    for i in range(n):
        token_map[int(call_tokens[i])] = Role(ROLE_OPTION, side="CE", index=i)
        token_map[int(put_tokens[i])] = Role(ROLE_OPTION, side="PE", index=i)
    token_map[256_265] = Role(ROLE_SPOT)
    token_map[264_969] = Role(ROLE_VIX)
    return OptionChain(
        "NIFTY", "2026-09-01", 2_250_000, 2_250_000,
        strikes, call_tokens, put_tokens, token_map,
    )


def _engine():
    table = IndexTable(_chain(), 0.0526, "2026-09-01")
    engine = CaptureEngine({"NIFTY": table}, None, {}, None, clock=lambda: 1_000)
    return engine, table


class FakeSocket:
    """A PUSH stand-in whose behaviour each test dictates."""

    def __init__(self, *, mode: str = "ok") -> None:
        self.mode = mode
        self.sent: list[list[bytes]] = []
        self.attempts = 0
        self.closed = False

    def send_multipart(self, parts, flags=0):  # noqa: ARG002
        self.attempts += 1
        if self.mode == "full":
            import zmq

            raise zmq.Again("HWM reached")
        if self.mode == "dead":
            raise OSError("bus went away")
        if self.mode == "explode":
            raise RuntimeError("unexpected socket fault")
        self.sent.append(list(parts))

    def close(self, linger=0):  # noqa: ARG002
        self.closed = True


def _publisher(mode: str = "ok") -> tuple[BusPublisher, FakeSocket]:
    socket = FakeSocket(mode=mode)
    publisher = BusPublisher(
        "tcp://127.0.0.1:1",
        clock=lambda: 5_000,
        socket_factory=lambda: socket,
        worker=False,
    )
    assert publisher.open() is True
    return publisher, socket


def _tick(token: int = TOKEN_CE, **extra) -> dict:
    base = {
        "instrument_token": token,
        "last_price": 123.45,
        "volume_traded": 500,
        "oi": 900,
        "total_buy_quantity": 10,
        "total_sell_quantity": 20,
        "oi_day_high": 950,
        "oi_day_low": 850,
        "ohlc": {"open": 120.0, "high": 130.5, "low": 119.25, "close": 121.75},
        "depth": {
            "buy": [{"price": 123.40, "quantity": 5, "orders": 2}],
            "sell": [{"price": 123.50, "quantity": 7, "orders": 3}],
        },
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# Disabled bus: indistinguishable from the pre-bus build
# --------------------------------------------------------------------------- #


def test_a_disabled_bus_publishes_nothing_and_opens_no_socket():
    publisher = build_publisher(SimpleNamespace(market_data_bus_enabled=False))
    assert isinstance(publisher, NullBusPublisher)
    assert publisher.publish_ticks([_tick()]) is False
    assert publisher.status() == {"enabled": False}


def test_enabled_without_an_endpoint_stays_disabled():
    publisher = build_publisher(
        SimpleNamespace(market_data_bus_enabled=True, market_data_bus_ingest_endpoint="")
    )
    assert isinstance(publisher, NullBusPublisher)


def test_capture_is_unchanged_with_no_bus_attached():
    engine, table = _engine()
    applied = engine.apply_ticks([_tick(), _tick(TOKEN_PE)])
    assert applied == 2
    assert int(table.calls.columns["ltp"][0]) == 12_345
    assert int(table.puts.columns["ltp"][0]) == 12_345


# --------------------------------------------------------------------------- #
# Bus unavailable / dead / full: capture continues
# --------------------------------------------------------------------------- #


def test_a_bus_that_cannot_connect_does_not_break_construction():
    def boom():
        raise OSError("connection refused")

    publisher = BusPublisher("tcp://127.0.0.1:1", socket_factory=boom, worker=False)
    assert publisher.open() is False
    assert publisher.connected is False
    assert publisher.publish_errors == 1
    assert publisher.publish_ticks([_tick()]) is False


@pytest.mark.parametrize("mode", ["dead", "explode"])
def test_capture_continues_when_the_bus_is_unavailable(mode):
    engine, table = _engine()
    publisher, socket = _publisher(mode)
    engine.set_bus(publisher)

    applied = engine.apply_ticks([_tick(), _tick(TOKEN_PE)])

    assert applied == 2, "capture must apply every tick regardless of the bus"
    assert int(table.calls.columns["ltp"][0]) == 12_345
    assert engine.malformed_ticks == 0, "a bus fault is not a malformed tick"
    assert socket.attempts == 1
    assert publisher.publish_errors == 1
    assert publisher.publish_total == 0


def test_a_full_hwm_drops_and_counts_but_capture_continues():
    engine, table = _engine()
    publisher, socket = _publisher("full")
    engine.set_bus(publisher)

    for _ in range(4):
        assert engine.apply_ticks([_tick()]) == 1

    assert int(table.calls.columns["ltp"][0]) == 12_345
    assert publisher.publish_dropped == 4
    assert publisher.publish_total == 0
    assert publisher.sequence == 0, "a dropped message must not consume a sequence number"
    assert socket.sent == []


def test_a_slow_consumer_cannot_backpressure_capture():
    """The publisher never awaits: a permanently full socket costs one attempt per batch."""
    engine, _table = _engine()
    publisher, socket = _publisher("full")
    engine.set_bus(publisher)

    for _ in range(500):
        engine.apply_ticks([_tick()])

    assert socket.attempts == 500
    assert publisher.publish_dropped == 500
    assert engine.consumer_error is None


def test_a_bus_restart_does_not_restart_capture():
    """New epoch on the producer side is the consumer's signal; capture is untouched."""
    engine, _table = _engine()
    publisher, socket = _publisher()
    engine.set_bus(publisher)
    engine.apply_ticks([_tick()])
    assert publisher.publish_total == 1

    socket.mode = "dead"
    for _ in range(3):
        engine.apply_ticks([_tick()])
    assert publisher.publish_errors >= 1

    socket.mode = "ok"
    assert engine.apply_ticks([_tick()]) == 1
    assert publisher.publish_total == 2
    assert engine.consumer_error is None
    engine.check_pipeline_alive(None, None)


def test_bus_telemetry_is_reported_without_being_capture_loss():
    publisher, _socket = _publisher("full")
    publisher.publish_ticks([_tick()])
    status = publisher.status()
    assert status["bus_publish_dropped"] == 1
    assert status["bus_publish_total"] == 0
    assert status["enabled"] is True
    assert "capture" not in status
    assert "frames" not in status


def test_the_publisher_reports_no_delivery_guarantee():
    """ZMQ PUB cannot prove a subscriber received anything; no metric may imply it."""
    publisher, _socket = _publisher()
    keys = set(publisher.status())
    for invented in ("subscribers", "delivered", "acked", "confirmed"):
        assert not any(invented in key for key in keys)


# --------------------------------------------------------------------------- #
# Poison ticks must not reach the bus as valid market data
# --------------------------------------------------------------------------- #


def test_a_malformed_tick_is_not_published_as_valid_market_data():
    engine, _table = _engine()
    publisher, socket = _publisher()
    engine.set_bus(publisher)

    applied = engine.apply_ticks(
        [_tick(), _tick(TOKEN_CE + 1, last_price="not-a-price"), _tick(TOKEN_CE + 2)]
    )

    assert applied == 2
    assert engine.malformed_ticks == 1
    header = decode_header(socket.sent[0][1])
    assert header.count == 2, "the rejected tick must not appear in the batch"
    tokens = {t.instrument_token for t in decode_tick_batch(socket.sent[0][2], header.count)}
    assert tokens == {TOKEN_CE, TOKEN_CE + 2}


def test_an_unmatched_token_is_not_published():
    """A token no table owns is not part of the captured universe."""
    engine, _table = _engine()
    publisher, socket = _publisher()
    engine.set_bus(publisher)

    assert engine.apply_ticks([_tick(999_999_999)]) == 0
    assert engine.unmatched == 1
    assert socket.sent == []


# --------------------------------------------------------------------------- #
# Fidelity: the bus and the capture tables must agree
# --------------------------------------------------------------------------- #


def test_published_values_match_what_capture_applied_to_its_tables():
    engine, table = _engine()
    publisher, socket = _publisher()
    engine.set_bus(publisher)

    engine.apply_ticks([_tick()])

    header = decode_header(socket.sent[0][1])
    published = decode_tick_batch(socket.sent[0][2], header.count)[0]

    assert published.instrument_token == TOKEN_CE
    assert published.last_price == int(table.calls.columns["ltp"][0]) == 12_345
    assert published.oi == int(table.calls.columns["oi"][0]) == 900
    assert published.volume_traded == int(table.calls.columns["volume"][0]) == 500
    assert published.ohlc_open == int(table.calls.columns["ohlc_open"][0]) == 12_000
    assert published.ohlc_high == int(table.calls.columns["ohlc_high"][0]) == 13_050
    assert published.ohlc_low == int(table.calls.columns["ohlc_low"][0]) == 11_925
    assert published.ohlc_close == int(table.calls.columns["ohlc_close"][0]) == 12_175
    assert published.bids[0].price == int(table.calls.columns["bid"][0]) == 12_340
    assert published.asks[0].price == int(table.calls.columns["ask"][0]) == 12_350


def test_negative_counts_are_clamped_identically_on_both_paths():
    """field_int clamps for capture; the bus must not publish the raw negative."""
    engine, table = _engine()
    publisher, socket = _publisher()
    engine.set_bus(publisher)

    engine.apply_ticks([_tick(oi=-5)])

    header = decode_header(socket.sent[0][1])
    published = decode_tick_batch(socket.sent[0][2], header.count)[0]
    assert published.oi == 0
    assert int(table.calls.columns["oi"][0]) == 0


def test_prices_reach_the_bus_as_integer_paise():
    """2456.70 must become 245670, using the same rounding as capture."""
    publisher, socket = _publisher()
    publisher.publish_ticks([_tick(last_price=2456.70)])
    header = decode_header(socket.sent[0][1])
    published = decode_tick_batch(socket.sent[0][2], header.count)[0]
    assert published.last_price == 245_670


def test_exchange_timestamps_are_normalized_to_integer_epoch_seconds():
    from datetime import UTC, datetime

    when = datetime(2026, 9, 1, 9, 15, 4, tzinfo=UTC)
    got = normalize_tick(_tick(exchange_timestamp=when, last_trade_time=1_788_147_903))
    assert got.exchange_timestamp == int(when.timestamp())
    assert got.last_trade_time == 1_788_147_903
    assert epoch_seconds(None) == 0
    assert epoch_seconds("garbage") == 0
    assert epoch_seconds(-5) == 0


# --------------------------------------------------------------------------- #
# Batching and sequencing on the producer side
# --------------------------------------------------------------------------- #


def test_one_batch_becomes_one_message_with_a_monotonic_sequence():
    publisher, socket = _publisher()
    for _ in range(3):
        publisher.publish_ticks([_tick(), _tick(TOKEN_PE)])

    sequences = [decode_header(parts[1]).sequence for parts in socket.sent]
    assert sequences == [1, 2, 3]
    epochs = {decode_header(parts[1]).epoch for parts in socket.sent}
    assert len(epochs) == 1
    assert all(parts[0] == TOPIC_TICK for parts in socket.sent)
    assert all(len(parts[2]) == 2 * NORMTICK_SIZE for parts in socket.sent)


def test_a_large_batch_is_split_without_losing_ticks():
    from app.bus.protocol import MAX_TICKS_PER_MESSAGE

    publisher, socket = _publisher()
    count = MAX_TICKS_PER_MESSAGE + 25
    publisher.publish_ticks([_tick(TOKEN_CE) for _ in range(count)])

    assert len(socket.sent) == 2
    total = sum(decode_header(parts[1]).count for parts in socket.sent)
    assert total == count
    assert publisher.ticks_published == count


# --------------------------------------------------------------------------- #
# Session metadata
# --------------------------------------------------------------------------- #


def test_a_consumer_can_map_a_published_token_using_session_metadata():
    table = IndexTable(_chain(), 0.0526, "2026-09-01")
    publisher, socket = _publisher()

    assert publisher.publish_session(
        trading_date="2026-09-01", instruments=build_instruments({"NIFTY": table})
    )

    topic, raw_header, payload = socket.sent[0]
    assert topic == TOPIC_SESSION
    assert decode_header(raw_header).message_type == 2
    session = decode_session(payload)
    by_token = {i["instrument_token"]: i for i in session["instruments"]}

    entry = by_token[TOKEN_CE]
    assert entry["underlying"] == "NIFTY"
    assert entry["option_side"] == "CE"
    assert entry["expiry"] == "2026-09-01"
    assert entry["strike_paise"] == 2_200_000
    assert entry["domain"] == "index_option"
    assert by_token[TOKEN_PE]["option_side"] == "PE"
    assert 256_265 in by_token, "the index spot token must be mappable too"


def test_a_slow_joining_consumer_can_get_metadata_without_a_producer_restart():
    table = IndexTable(_chain(), 0.0526, "2026-09-01")
    publisher, socket = _publisher()
    publisher.publish_session(
        trading_date="2026-09-01", instruments=build_instruments({"NIFTY": table})
    )
    publisher.publish_ticks([_tick()])

    publisher.publish_session(
        trading_date="2026-09-01", instruments=build_instruments({"NIFTY": table})
    )

    assert publisher.session_publish_total == 2
    assert [parts[0] for parts in socket.sent] == [TOPIC_SESSION, TOPIC_TICK, TOPIC_SESSION]
    assert publisher.epoch == decode_header(socket.sent[-1][1]).epoch


def test_no_credentials_appear_in_session_or_status_payloads():
    table = IndexTable(_chain(), 0.0526, "2026-09-01")
    publisher, socket = _publisher()
    publisher.publish_session(
        trading_date="2026-09-01", instruments=build_instruments({"NIFTY": table})
    )
    blob = socket.sent[0][2].decode("utf-8").lower() + repr(publisher.status()).lower()
    for forbidden in (
        "access_token",
        "api_secret",
        "passcode",
        "password",
        "maintenance_token",
        "secret",
    ):
        assert forbidden not in blob, f"{forbidden} must never reach the bus"


# --------------------------------------------------------------------------- #
# The worker thread: encoding must not run on the tick-consumer thread
# --------------------------------------------------------------------------- #


def _worker_publisher(mode: str = "ok"):
    socket = FakeSocket(mode=mode)
    publisher = BusPublisher(
        "tcp://127.0.0.1:1", socket_factory=lambda: socket, queue_maxsize=4
    )
    assert publisher.open() is True
    return publisher, socket


def _settle(publisher, *, timeout_s: float = 2.0) -> None:
    import time as _time

    deadline = _time.monotonic() + timeout_s
    while publisher._queue.unfinished_tasks if False else True:
        if publisher._queue.qsize() == 0:
            _time.sleep(0.02)
            if publisher._queue.qsize() == 0:
                return
        if _time.monotonic() > deadline:
            return
        _time.sleep(0.01)


def test_the_tick_path_only_enqueues_and_the_worker_does_the_encoding():
    """publish_ticks must not serialize: that cost belongs off the tick thread."""
    publisher, socket = _worker_publisher()
    try:
        assert publisher.publish_ticks([_tick()]) is True
        _settle(publisher)
        assert len(socket.sent) == 1
        assert publisher.publish_total == 1
        assert publisher.ticks_published == 1
    finally:
        publisher.close()


def test_a_full_worker_queue_drops_instead_of_blocking_the_tick_path():
    """A stalled worker must shed bus work, never delay capture."""
    import threading

    release = threading.Event()

    class Blocking(FakeSocket):
        def send_multipart(self, parts, flags=0):  # noqa: ARG002
            release.wait(timeout=5.0)
            super().send_multipart(parts, flags=flags)

    socket = Blocking()
    publisher = BusPublisher(
        "tcp://127.0.0.1:1", socket_factory=lambda: socket, queue_maxsize=2
    )
    assert publisher.open() is True
    try:
        accepted = sum(publisher.publish_ticks([_tick()]) for _ in range(60))
        assert accepted < 60, "a bounded queue must refuse work once full"
        assert publisher.queue_dropped > 0
        assert publisher.status()["bus_queue_dropped"] == publisher.queue_dropped
    finally:
        release.set()
        publisher.close()


def test_capture_keeps_applying_while_the_worker_queue_is_full():
    import threading

    release = threading.Event()

    class Blocking(FakeSocket):
        def send_multipart(self, parts, flags=0):  # noqa: ARG002
            release.wait(timeout=5.0)
            super().send_multipart(parts, flags=flags)

    socket = Blocking()
    publisher = BusPublisher(
        "tcp://127.0.0.1:1", socket_factory=lambda: socket, queue_maxsize=1
    )
    assert publisher.open() is True
    engine, table = _engine()
    engine.set_bus(publisher)
    try:
        for i in range(200):
            assert engine.apply_ticks([_tick(last_price=100.0 + i)]) == 1
        assert int(table.calls.columns["ltp"][0]) == 10_000 + 19_900
        assert engine.malformed_ticks == 0
        assert publisher.queue_dropped > 0
    finally:
        release.set()
        publisher.close()


def test_close_stops_the_worker_thread():
    publisher, _socket = _worker_publisher()
    publisher.publish_ticks([_tick()])
    _settle(publisher)
    worker = publisher._worker
    publisher.close()
    assert worker is not None
    assert worker.is_alive() is False
