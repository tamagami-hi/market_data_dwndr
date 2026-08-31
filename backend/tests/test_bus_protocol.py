"""MARKET_DATA_BUS wire protocol: roundtrip, type preservation, and rejection.

The bus is a cross-language contract (Python now, Rust/Node later), so the byte layout and
its refusal behaviour are the thing under test — not just "does it decode". In particular a
consumer must be able to reject a message it cannot interpret rather than guess, and integer
market values must survive the trip unchanged: prices stay integer paise and counts stay
unsigned, because a float would silently re-introduce the rounding problem the codec avoids.
"""

from __future__ import annotations

import pytest

from app.bus.protocol import (
    HEADER_SIZE,
    MSG_SESSION,
    MSG_TICK_BATCH,
    NORMTICK_SIZE,
    PROTOCOL_VERSION,
    TOPIC_SESSION,
    TOPIC_TICK,
    BusHeader,
    DepthLevel,
    InstrumentInfo,
    NormTick,
    ProtocolError,
    SequenceTracker,
    decode_header,
    decode_session,
    decode_tick,
    decode_tick_batch,
    encode_header,
    encode_session,
    encode_tick,
    encode_tick_batch,
)


def _tick(token: int = 12345, **overrides) -> NormTick:
    base = dict(
        instrument_token=token,
        last_price=2_456_705,
        average_traded_price=2_455_010,
        volume_traded=98_765_432,
        total_buy_quantity=1_234_567,
        total_sell_quantity=7_654_321,
        oi=555_000,
        oi_day_high=560_000,
        oi_day_low=540_000,
        ohlc_open=2_450_000,
        ohlc_high=2_460_000,
        ohlc_low=2_440_000,
        ohlc_close=2_452_500,
        exchange_timestamp=1_788_147_904,
        last_trade_time=1_788_147_903,
        bids=tuple(DepthLevel(2_456_700 - i * 5, 100 + i, 3 + i) for i in range(5)),
        asks=tuple(DepthLevel(2_456_710 + i * 5, 200 + i, 4 + i) for i in range(5)),
    )
    base.update(overrides)
    return NormTick(**base)


def test_sizes_are_fixed_and_word_aligned():
    """A cross-language consumer needs stable sizes with no padding guesswork."""
    assert HEADER_SIZE == 32
    assert NORMTICK_SIZE == 360
    assert HEADER_SIZE % 8 == 0
    assert NORMTICK_SIZE % 8 == 0


def test_header_roundtrip():
    header = BusHeader(
        epoch=1_788_000_000_000,
        sequence=42,
        publish_unix_ns=1_788_000_000_123_456_789,
        message_type=MSG_TICK_BATCH,
        count=7,
    )
    got = decode_header(encode_header(header))
    assert got == header


def test_tick_roundtrip_preserves_every_integer_exactly():
    tick = _tick()
    got = decode_tick(encode_tick(tick))
    assert got == tick
    for name in (
        "instrument_token",
        "last_price",
        "average_traded_price",
        "volume_traded",
        "total_buy_quantity",
        "total_sell_quantity",
        "oi",
        "oi_day_high",
        "oi_day_low",
        "ohlc_open",
        "ohlc_high",
        "ohlc_low",
        "ohlc_close",
        "exchange_timestamp",
        "last_trade_time",
    ):
        value = getattr(got, name)
        assert isinstance(value, int), f"{name} must stay an integer, got {type(value)}"
        assert value == getattr(tick, name)


def test_prices_stay_integer_paise_with_no_float_conversion():
    """2456705 paise must not become 24567.05 anywhere in the codec."""
    tick = _tick(last_price=2_456_705)
    got = decode_tick(encode_tick(tick))
    assert got.last_price == 2_456_705
    assert not isinstance(got.last_price, float)


def test_large_unsigned_counts_survive():
    tick = _tick(volume_traded=2**63 + 5, oi=2**64 - 1)
    got = decode_tick(encode_tick(tick))
    assert got.volume_traded == 2**63 + 5
    assert got.oi == 2**64 - 1


def test_depth_is_padded_to_five_levels_each_side():
    tick = _tick(bids=(DepthLevel(100, 1, 1),), asks=())
    got = decode_tick(encode_tick(tick))
    assert len(got.bids) == 5
    assert len(got.asks) == 5
    assert got.bids[0] == DepthLevel(100, 1, 1)
    assert got.bids[4] == DepthLevel(0, 0, 0)
    assert got.asks[0] == DepthLevel(0, 0, 0)


def test_multi_tick_batch_roundtrip():
    ticks = [_tick(token=1000 + i, last_price=100 + i) for i in range(9)]
    raw = encode_tick_batch(ticks)
    assert len(raw) == 9 * NORMTICK_SIZE
    got = decode_tick_batch(raw, 9)
    assert got == ticks
    assert [t.instrument_token for t in got] == [1000 + i for i in range(9)]


def test_an_unknown_protocol_version_is_refused_not_guessed():
    raw = bytearray(encode_header(BusHeader(epoch=1, sequence=1, publish_unix_ns=1)))
    raw[0:2] = (PROTOCOL_VERSION + 1).to_bytes(2, "little")
    with pytest.raises(ProtocolError, match="unsupported bus protocol version"):
        decode_header(bytes(raw))


def test_an_unknown_message_type_is_refused():
    raw = bytearray(encode_header(BusHeader(epoch=1, sequence=1, publish_unix_ns=1)))
    raw[2:4] = (99).to_bytes(2, "little")
    with pytest.raises(ProtocolError, match="unknown bus message type"):
        decode_header(bytes(raw))


def test_a_truncated_header_is_refused():
    raw = encode_header(BusHeader(epoch=1, sequence=1, publish_unix_ns=1))
    with pytest.raises(ProtocolError, match="header must be"):
        decode_header(raw[:-1])


def test_a_truncated_tick_is_refused():
    raw = encode_tick(_tick())
    with pytest.raises(ProtocolError, match="truncated NormTick"):
        decode_tick(raw[:-1])


def test_a_batch_whose_length_disagrees_with_the_header_count_is_refused():
    """The count in the header is authoritative; a mismatch must never be tolerated."""
    raw = encode_tick_batch([_tick(), _tick(token=2)])
    with pytest.raises(ProtocolError, match="declares 3 tick"):
        decode_tick_batch(raw, 3)
    with pytest.raises(ProtocolError, match="declares 1 tick"):
        decode_tick_batch(raw, 1)


def test_empty_batch_is_valid():
    assert decode_tick_batch(b"", 0) == []


def test_topics_are_stable_ascii():
    assert TOPIC_TICK == b"market.tick"
    assert TOPIC_SESSION == b"market.session"


def test_session_roundtrip_keeps_strike_as_integer_paise():
    instruments = [
        InstrumentInfo(
            instrument_token=111,
            exchange="NFO",
            tradingsymbol="NIFTY26SEP24000CE",
            instrument_type="CE",
            domain="index_option",
            underlying="NIFTY",
            expiry="2026-09-01",
            strike_paise=2_400_000,
            option_side="CE",
        )
    ]
    payload = encode_session(
        trading_date="2026-09-01", producer="tickvault", epoch=7, instruments=instruments
    )
    got = decode_session(payload)
    assert got["trading_date"] == "2026-09-01"
    assert got["epoch"] == 7
    assert got["instrument_count"] == 1
    entry = got["instruments"][0]
    assert entry["strike_paise"] == 2_400_000
    assert isinstance(entry["strike_paise"], int)


def test_session_rejects_a_foreign_protocol_version():
    with pytest.raises(ProtocolError, match="unsupported session protocol version"):
        decode_session(b'{"protocol_version":999}')


def test_session_rejects_non_json_and_non_object():
    with pytest.raises(ProtocolError, match="not valid UTF-8 JSON"):
        decode_session(b"\xff\xfe not json")
    with pytest.raises(ProtocolError, match="must be a JSON object"):
        decode_session(b"[1,2,3]")


def test_session_message_type_is_carried_in_the_header():
    header = decode_header(
        encode_header(
            BusHeader(epoch=1, sequence=1, publish_unix_ns=1, message_type=MSG_SESSION)
        )
    )
    assert header.message_type == MSG_SESSION
    assert header.count == 0


# --------------------------------------------------------------------------- #
# Ordering: in-order, gap, and epoch change must be distinguishable
# --------------------------------------------------------------------------- #


def _header(epoch: int, sequence: int) -> BusHeader:
    return BusHeader(epoch=epoch, sequence=sequence, publish_unix_ns=sequence)


def test_monotonic_sequence_within_one_epoch_is_in_order():
    tracker = SequenceTracker()
    outcomes = [tracker.observe(_header(5, n)) for n in range(1, 6)]
    assert outcomes == ["first", "ok", "ok", "ok", "ok"]
    assert tracker.gaps == 0
    assert tracker.missed == 0


def test_a_gap_is_detected_and_sized():
    tracker = SequenceTracker()
    tracker.observe(_header(5, 1))
    assert tracker.observe(_header(5, 2)) == "ok"
    assert tracker.observe(_header(5, 9)) == "gap"
    assert tracker.gaps == 1
    assert tracker.missed == 6
    assert tracker.observe(_header(5, 10)) == "ok"


def test_a_restart_is_distinguishable_from_a_gap():
    """A new epoch resets expectations instead of reporting a huge phantom gap."""
    tracker = SequenceTracker()
    for n in range(1, 2_001):
        tracker.observe(_header(5, n))

    assert tracker.observe(_header(6, 1)) == "epoch_change"
    assert tracker.epoch_changes == 1
    assert tracker.gaps == 0, "an epoch change must never be counted as a gap"
    assert tracker.missed == 0
    assert tracker.observe(_header(6, 2)) == "ok"


def test_a_replayed_or_stale_sequence_does_not_rewind_state():
    tracker = SequenceTracker()
    tracker.observe(_header(5, 1))
    tracker.observe(_header(5, 7))
    assert tracker.observe(_header(5, 3)) == "stale"
    assert tracker.last_sequence == 7
