"""End-to-end over real ZMQ sockets: TickVault PUSH -> bus PULL/PUB -> SUB consumer.

The unit tests use a fake socket to pin behaviour precisely; this file proves the same
contract holds across genuine ZMQ transports, including the parts that only real sockets
exhibit: a subscriber that joins late, a subscriber that disappears mid-stream, and a bus
that restarts and therefore issues a new epoch.

Also asserts the two protocol copies are byte-identical. The bus is a separate deployable
under /srv/dev_stack/MARKET_DATA_BUS with its own image, so it cannot import TickVault's
package; each side owns a copy and this test is what keeps them honest.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

zmq = pytest.importorskip("zmq")

BUS_ROOT = Path(__file__).resolve().parents[2] / "market_data_bus"
if str(BUS_ROOT) not in sys.path:
    sys.path.insert(0, str(BUS_ROOT))

from mdbus import protocol as bus_protocol  # noqa: E402
from mdbus.service import BusConfig, MarketDataBus  # noqa: E402

from app.bus import protocol as tv_protocol  # noqa: E402
from app.bus.protocol import (  # noqa: E402
    TOPIC_SESSION,
    TOPIC_TICK,
    InstrumentInfo,  # noqa: E402
    SequenceTracker,
    decode_header,
    decode_session,
    decode_tick_batch,
)
from app.bus.publisher import BusPublisher  # noqa: E402

PORT_BASE = 25_400


def _endpoints(offset: int) -> tuple[str, str, str]:
    base = PORT_BASE + offset * 10
    return (
        f"tcp://127.0.0.1:{base}",
        f"tcp://127.0.0.1:{base + 1}",
        f"tcp://127.0.0.1:{base + 2}",
    )


def _settle(publisher, *, timeout_s: float = 2.0) -> None:
    """Wait for the publisher's worker thread to flush its queue."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if publisher._queue.qsize() == 0:
            time.sleep(0.03)
            if publisher._queue.qsize() == 0:
                return
        time.sleep(0.01)


def _pump(bus: MarketDataBus, *, rounds: int = 200) -> int:
    """Drain everything currently queued on the bus ingest socket."""
    handled = 0
    for _ in range(rounds):
        try:
            parts = bus.ingest.recv_multipart(flags=zmq.DONTWAIT)
        except zmq.Again:
            time.sleep(0.005)
            continue
        bus.handle_ingest(parts)
        handled += 1
    return handled


def _drain_sub(sub, *, expect: int, timeout_s: float = 3.0) -> list[list[bytes]]:
    out: list[list[bytes]] = []
    deadline = time.monotonic() + timeout_s
    while len(out) < expect and time.monotonic() < deadline:
        try:
            out.append(sub.recv_multipart(flags=zmq.DONTWAIT))
        except zmq.Again:
            time.sleep(0.005)
    return out


def _pub(publisher, ticks) -> None:
    publisher.publish_ticks(ticks)
    _settle(publisher)


def _pubs(publisher, **kwargs) -> bool:
    result = publisher.publish_session(**kwargs)
    _settle(publisher)
    return result


def _tick(token: int, price: float) -> dict:
    return {
        "instrument_token": token,
        "last_price": price,
        "volume_traded": 1_000,
        "oi": 2_000,
        "ohlc": {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
        "depth": {
            "buy": [{"price": price - 0.05, "quantity": 5, "orders": 1}],
            "sell": [{"price": price + 0.05, "quantity": 6, "orders": 2}],
        },
    }


@pytest.fixture
def rig(request):
    offset = abs(hash(request.node.name)) % 40
    ingest, publish, health = _endpoints(offset)
    context = zmq.Context()
    bus = MarketDataBus(
        BusConfig(ingest_endpoint=ingest, publish_endpoint=publish, health_endpoint=health),
        context=context,
    )
    bus.open()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(publish)
    publisher = BusPublisher(ingest, sndhwm=1_000)
    assert publisher.open() is True
    time.sleep(0.25)
    try:
        yield bus, publisher, sub, context, (ingest, publish, health)
    finally:
        publisher.close()
        sub.close(linger=0)
        bus.close()
        context.term()


def test_the_two_protocol_copies_are_byte_identical():
    """The bus image cannot import TickVault, so the copies must not drift."""
    assert bus_protocol.PROTOCOL_VERSION == tv_protocol.PROTOCOL_VERSION
    assert bus_protocol.HEADER_SIZE == tv_protocol.HEADER_SIZE
    assert bus_protocol.NORMTICK_SIZE == tv_protocol.NORMTICK_SIZE
    assert bus_protocol.TOPIC_TICK == tv_protocol.TOPIC_TICK
    assert bus_protocol.TOPIC_SESSION == tv_protocol.TOPIC_SESSION
    assert bus_protocol.MAX_TICKS_PER_MESSAGE == tv_protocol.MAX_TICKS_PER_MESSAGE

    tick = tv_protocol.NormTick(
        instrument_token=42,
        last_price=245_670,
        oi=2**63 + 1,
        bids=(tv_protocol.DepthLevel(1, 2, 3),),
    )
    encoded = tv_protocol.encode_tick(tick)
    assert bus_protocol.decode_tick(encoded).instrument_token == 42
    assert bus_protocol.decode_tick(encoded).oi == 2**63 + 1
    assert bus_protocol.encode_tick(bus_protocol.decode_tick(encoded)) == encoded

    header = tv_protocol.BusHeader(epoch=9, sequence=3, publish_unix_ns=7, count=1)
    assert bus_protocol.decode_header(tv_protocol.encode_header(header)).sequence == 3


def test_a_tick_batch_survives_the_full_hop(rig):
    bus, publisher, sub, _ctx, _eps = rig

    _pub(publisher, [_tick(11, 123.45), _tick(12, 200.10)])
    _pump(bus, rounds=20)

    messages = _drain_sub(sub, expect=1)
    assert len(messages) == 1
    topic, raw_header, payload = messages[0]
    assert topic == TOPIC_TICK

    header = decode_header(raw_header)
    assert header.count == 2
    assert header.epoch == bus.metrics.bus_epoch, "subscribers track the BUS epoch"
    assert header.sequence == 1

    ticks = decode_tick_batch(payload, header.count)
    assert [t.instrument_token for t in ticks] == [11, 12]
    assert ticks[0].last_price == 12_345
    assert ticks[1].last_price == 20_010
    assert ticks[0].bids[0].price == 12_340
    assert ticks[0].asks[0].price == 12_350


def test_session_metadata_maps_a_token_for_a_late_joining_subscriber(rig):
    bus, publisher, sub, context, (_ingest, publish, _health) = rig
    instruments = [
        InstrumentInfo(
            instrument_token=11,
            exchange="NFO",
            tradingsymbol="NIFTY26SEP22000CE",
            instrument_type="CE",
            domain="index_option",
            underlying="NIFTY",
            expiry="2026-09-01",
            strike_paise=2_200_000,
            option_side="CE",
        )
    ]

    _pubs(publisher, trading_date="2026-09-01", instruments=instruments)
    _pump(bus, rounds=20)
    _drain_sub(sub, expect=1)

    late = context.socket(zmq.SUB)
    late.setsockopt(zmq.LINGER, 0)
    late.setsockopt(zmq.SUBSCRIBE, TOPIC_SESSION)
    late.connect(publish)
    time.sleep(0.25)
    try:
        _pubs(publisher, trading_date="2026-09-01", instruments=instruments)
        _pump(bus, rounds=20)
        received = _drain_sub(late, expect=1)
        assert len(received) == 1, "a periodic republish serves a late joiner"
        session = decode_session(received[0][2])
        entry = session["instruments"][0]
        assert entry["instrument_token"] == 11
        assert entry["underlying"] == "NIFTY"
        assert entry["strike_paise"] == 2_200_000
    finally:
        late.close(linger=0)


def test_topic_filtering_lets_a_consumer_take_only_ticks(rig):
    bus, publisher, sub, context, (_ingest, publish, _health) = rig
    ticks_only = context.socket(zmq.SUB)
    ticks_only.setsockopt(zmq.LINGER, 0)
    ticks_only.setsockopt(zmq.SUBSCRIBE, TOPIC_TICK)
    ticks_only.connect(publish)
    time.sleep(0.25)
    try:
        _pubs(publisher, trading_date="2026-09-01", instruments=[])
        _pub(publisher, [_tick(11, 1.0)])
        _pump(bus, rounds=20)
        got = _drain_sub(ticks_only, expect=1)
        assert len(got) == 1
        assert got[0][0] == TOPIC_TICK
    finally:
        ticks_only.close(linger=0)


def test_a_consumer_sees_a_monotonic_sequence_and_detects_no_gaps(rig):
    bus, publisher, sub, _ctx, _eps = rig
    for i in range(12):
        _pub(publisher, [_tick(11, 100.0 + i)])
    _pump(bus, rounds=60)

    messages = _drain_sub(sub, expect=12)
    assert len(messages) == 12

    tracker = SequenceTracker()
    outcomes = [tracker.observe(decode_header(parts[1])) for parts in messages]
    assert outcomes[0] == "first"
    assert set(outcomes[1:]) == {"ok"}
    assert tracker.gaps == 0
    assert tracker.missed == 0


def test_a_bus_restart_is_a_new_epoch_not_a_gap(rig):
    bus, publisher, sub, context, (ingest, publish, health) = rig
    _pub(publisher, [_tick(11, 1.0)])
    _pump(bus, rounds=20)
    first = _drain_sub(sub, expect=1)
    assert len(first) == 1

    tracker = SequenceTracker()
    assert tracker.observe(decode_header(first[0][1])) == "first"
    old_epoch = bus.metrics.bus_epoch

    sub.close(linger=0)
    bus.close()
    time.sleep(0.2)

    restarted = MarketDataBus(
        BusConfig(ingest_endpoint=ingest, publish_endpoint=publish, health_endpoint=health),
        context=context,
        clock=lambda: int(time.time() * 1000) + 10_000,
    )
    restarted.open()
    new_sub = context.socket(zmq.SUB)
    new_sub.setsockopt(zmq.LINGER, 0)
    new_sub.setsockopt(zmq.SUBSCRIBE, b"")
    new_sub.connect(publish)
    time.sleep(0.35)
    try:
        _pub(publisher, [_tick(11, 2.0)])
        _pump(restarted, rounds=40)
        after = _drain_sub(new_sub, expect=1)
        assert len(after) == 1

        header = decode_header(after[0][1])
        assert header.epoch != old_epoch, "a bus restart must issue a new epoch"
        assert header.sequence == 1
        assert tracker.observe(header) == "epoch_change"
        assert tracker.gaps == 0, "a restart must never be reported as a gap"
    finally:
        new_sub.close(linger=0)
        restarted.close()


def test_the_producer_survives_a_bus_that_disappears_and_returns(rig):
    """TickVault keeps publishing across a bus outage; only bus counters move."""
    bus, publisher, sub, context, (ingest, publish, health) = rig
    _pub(publisher, [_tick(11, 1.0)])
    _pump(bus, rounds=20)
    assert publisher.publish_total == 1

    sub.close(linger=0)
    bus.close()
    time.sleep(0.2)

    for i in range(5):
        _pub(publisher, [_tick(11, 10.0 + i)])
    assert publisher.publish_errors == 0, "PUSH buffers to HWM rather than erroring"

    restarted = MarketDataBus(
        BusConfig(ingest_endpoint=ingest, publish_endpoint=publish, health_endpoint=health),
        context=context,
    )
    restarted.open()
    try:
        time.sleep(0.3)
        _pub(publisher, [_tick(11, 99.0)])
        assert _pump(restarted, rounds=60) >= 1
        assert publisher.publish_total == 7
    finally:
        restarted.close()


def test_a_vanishing_subscriber_stops_neither_bus_nor_producer(rig):
    bus, publisher, sub, _ctx, _eps = rig
    _pub(publisher, [_tick(11, 1.0)])
    _pump(bus, rounds=20)
    _drain_sub(sub, expect=1)

    sub.close(linger=0)
    time.sleep(0.15)

    for i in range(10):
        _pub(publisher, [_tick(11, 50.0 + i)])
    _pump(bus, rounds=60)

    assert publisher.publish_total == 11
    assert publisher.publish_errors == 0
    assert bus.metrics.messages_out_total >= 11
    assert bus.metrics.protocol_errors_total == 0


def test_the_bus_rejects_malformed_and_foreign_protocol_messages(rig):
    bus, _publisher, _sub, context, (ingest, _publish, _health) = rig
    raw = context.socket(zmq.PUSH)
    raw.setsockopt(zmq.LINGER, 0)
    raw.connect(ingest)
    time.sleep(0.2)
    try:
        raw.send_multipart([TOPIC_TICK, b"too-short"])
        raw.send_multipart([TOPIC_TICK, b"x" * 32, b""])
        raw.send_multipart([b"market.bogus", b"x" * 32, b""])
        time.sleep(0.2)
        _pump(bus, rounds=40)
        assert bus.metrics.protocol_errors_total >= 3
        assert bus.metrics.messages_out_total == 0
    finally:
        raw.close(linger=0)


def test_the_bus_tracks_a_producer_restart_separately_from_its_own_epoch(rig):
    bus, publisher, _sub, _ctx, (ingest, _publish, _health) = rig
    _pub(publisher, [_tick(11, 1.0)])
    _pump(bus, rounds=20)
    assert bus.metrics.producer_epochs_seen == 1

    second = BusPublisher(ingest, sndhwm=100)
    second.epoch = publisher.epoch + 5_000
    assert second.open() is True
    try:
        time.sleep(0.2)
        _pub(second, [_tick(12, 2.0)])
        _pump(bus, rounds=40)
        assert bus.metrics.producer_epochs_seen == 2
        assert bus.metrics.producer_gaps_total == 0
    finally:
        second.close()


def test_health_snapshot_is_honest_and_credential_free(rig):
    bus, publisher, _sub, context, (_ingest, _publish, health) = rig
    _pub(publisher, [_tick(11, 1.0), _tick(12, 2.0)])
    _pump(bus, rounds=20)

    req = context.socket(zmq.REQ)
    req.setsockopt(zmq.LINGER, 0)
    req.setsockopt(zmq.RCVTIMEO, 2_000)
    req.setsockopt(zmq.SNDTIMEO, 2_000)
    req.connect(health)
    try:
        req.send(b"health")
        import json

        deadline = time.monotonic() + 3.0
        raw = None
        while raw is None and time.monotonic() < deadline:
            bus.handle_health()
            try:
                raw = req.recv(flags=zmq.DONTWAIT)
            except zmq.Again:
                time.sleep(0.01)
        assert raw is not None, "the bus did not answer its health socket"
        snapshot = json.loads(raw.decode("utf-8"))
    finally:
        req.close(linger=0)

    assert snapshot["bus_up"] is True
    assert snapshot["ticks_in_total"] == 2
    assert snapshot["ticks_out_total"] == 2
    assert snapshot["messages_in_total"] == 1
    assert snapshot["current_bus_epoch"] == bus.metrics.bus_epoch
    assert snapshot["publish_dropped_total"] == 0
    assert snapshot["protocol_errors_total"] == 0
    blob = repr(snapshot).lower()
    for forbidden in ("token", "secret", "passcode", "password"):
        assert forbidden not in blob
