"""MARKET_DATA_BUS wire protocol: ``BusHeader`` + ``NormTick`` + session metadata.

This is the contract between TickVault (producer) and every in-house consumer. It is a
distribution format, deliberately **not** the BIN frame format and **not** the frontend
JSON snapshot: BIN is the authoritative durable archive, the browser payload is
display-shaped, and this is a realtime normalized tick stream.

Encoding rules, fixed so Rust / Python / Node consumers agree byte for byte:

* little-endian throughout (``<``);
* fixed-width integers only — no varints, no floats anywhere in ``NormTick``;
* prices are **integer paise** exactly as TickVault stores them (``value * 100``), so a
  consumer never has to re-derive a rounding convention;
* counts (quantity, OI, volume, order counts) are unsigned;
* absent/unparseable values are ``0``, matching ``app.kite.ticks``;
* every struct is a whole number of 8-byte words, so no consumer needs padding rules.

Message shape is a 3-part ZMQ multipart frame::

    [topic]  [BusHeader]  [payload]

``topic`` is ASCII (see :data:`TOPIC_TICK` / :data:`TOPIC_SESSION`) so SUB-side prefix
filtering works without parsing. ``BusHeader`` is fixed size and always parseable, so a
consumer can classify a message (version / type / epoch / sequence) before it trusts the
payload. For ``market.tick`` the payload is ``count`` back-to-back ``NormTick`` records;
for ``market.session`` it is UTF-8 JSON, which is the right trade for a low-rate message
whose shape must stay readable and extensible.

Sequence semantics: ``epoch`` identifies one continuous run of a producer, ``sequence``
counts messages within that epoch starting at 1. A consumer therefore distinguishes

    same epoch, sequence == last + 1   -> in order
    same epoch, sequence  > last + 1   -> gap (PUB/SUB dropped, or a slow consumer)
    different epoch                    -> producer or bus restarted; reset expectations
    unknown protocol_version           -> refuse, do not guess

These are **bus** sequence numbers. They are a different domain from the per-frame
``sequence`` inside a BIN ``IndexFrame`` and must never be compared with it.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1

TOPIC_TICK = b"market.tick"
TOPIC_SESSION = b"market.session"
TOPICS = (TOPIC_TICK, TOPIC_SESSION)

MSG_TICK_BATCH = 1
MSG_SESSION = 2

DEPTH_LEVELS = 5

_HEADER = struct.Struct("<HHHHQQQ")
HEADER_SIZE = _HEADER.size

_TICK_FIXED = struct.Struct("<QqqQQQQQQqqqqQQ")
_DEPTH_LEVEL = struct.Struct("<qQII")
NORMTICK_SIZE = _TICK_FIXED.size + 2 * DEPTH_LEVELS * _DEPTH_LEVEL.size

MAX_TICKS_PER_MESSAGE = 8192


class ProtocolError(ValueError):
    """Raised when a received message cannot be trusted as this protocol version."""


@dataclass(frozen=True)
class DepthLevel:
    """One order-book level. Price in paise; quantity and order count unsigned."""

    price: int = 0
    quantity: int = 0
    orders: int = 0


@dataclass(frozen=True)
class NormTick:
    """One normalized instrument update.

    Field set is derived from what ``app.kite.ticks`` actually normalizes today, plus the
    two exchange timestamps a downstream consumer cannot reconstruct from anything else.
    Prices are integer paise; ``exchange_timestamp`` and ``last_trade_time`` are integer
    epoch **seconds** (0 when the feed omitted them).

    On-wire layout: 120-byte fixed part, then 2 x 5 depth levels of 24 bytes = 360 total::

        off  type  field
          0  u64   instrument_token
          8  i64   last_price              paise
         16  i64   average_traded_price    paise
         24  u64   volume_traded
         32  u64   total_buy_quantity
         40  u64   total_sell_quantity
         48  u64   oi
         56  u64   oi_day_high
         64  u64   oi_day_low
         72  i64   ohlc_open               paise
         80  i64   ohlc_high               paise
         88  i64   ohlc_low                paise
         96  i64   ohlc_close              paise
        104  u64   exchange_timestamp      epoch seconds
        112  u64   last_trade_time         epoch seconds
        120  DepthLevel[5]  bid side       (i64 price, u64 qty, u32 orders, u32 pad)
        240  DepthLevel[5]  ask side
    """

    instrument_token: int
    last_price: int = 0
    average_traded_price: int = 0
    volume_traded: int = 0
    total_buy_quantity: int = 0
    total_sell_quantity: int = 0
    oi: int = 0
    oi_day_high: int = 0
    oi_day_low: int = 0
    ohlc_open: int = 0
    ohlc_high: int = 0
    ohlc_low: int = 0
    ohlc_close: int = 0
    exchange_timestamp: int = 0
    last_trade_time: int = 0
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()


@dataclass(frozen=True)
class BusHeader:
    """Fixed 32-byte envelope that precedes every payload.

    On-wire layout::

        off  type  field
          0  u16   protocol_version
          2  u16   message_type       MSG_TICK_BATCH | MSG_SESSION
          4  u16   count              ticks in this batch (0 for session)
          6  u16   reserved           0
          8  u64   epoch              producer run id (ms at producer start)
         16  u64   sequence           1-based, monotonic within the epoch
         24  u64   publish_unix_ns    producer clock at send
    """

    epoch: int
    sequence: int
    publish_unix_ns: int
    message_type: int = MSG_TICK_BATCH
    count: int = 0
    protocol_version: int = PROTOCOL_VERSION
    reserved: int = 0


def _pad_levels(levels: tuple[DepthLevel, ...]) -> list[DepthLevel]:
    out = list(levels[:DEPTH_LEVELS])
    while len(out) < DEPTH_LEVELS:
        out.append(DepthLevel())
    return out


def encode_header(header: BusHeader) -> bytes:
    return _HEADER.pack(
        header.protocol_version,
        header.message_type,
        header.count,
        header.reserved,
        header.epoch,
        header.sequence,
        header.publish_unix_ns,
    )


def decode_header(raw: bytes) -> BusHeader:
    if len(raw) != HEADER_SIZE:
        raise ProtocolError(f"header must be {HEADER_SIZE} bytes, got {len(raw)}")
    version, message_type, count, reserved, epoch, sequence, publish_ns = _HEADER.unpack(raw)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported bus protocol version {version} (this build speaks "
            f"{PROTOCOL_VERSION}); refusing to interpret the payload"
        )
    if message_type not in (MSG_TICK_BATCH, MSG_SESSION):
        raise ProtocolError(f"unknown bus message type {message_type}")
    return BusHeader(
        epoch=epoch,
        sequence=sequence,
        publish_unix_ns=publish_ns,
        message_type=message_type,
        count=count,
        protocol_version=version,
        reserved=reserved,
    )


def encode_tick(tick: NormTick) -> bytes:
    out = bytearray(
        _TICK_FIXED.pack(
            tick.instrument_token,
            tick.last_price,
            tick.average_traded_price,
            tick.volume_traded,
            tick.total_buy_quantity,
            tick.total_sell_quantity,
            tick.oi,
            tick.oi_day_high,
            tick.oi_day_low,
            tick.ohlc_open,
            tick.ohlc_high,
            tick.ohlc_low,
            tick.ohlc_close,
            tick.exchange_timestamp,
            tick.last_trade_time,
        )
    )
    for side in (tick.bids, tick.asks):
        for level in _pad_levels(side):
            out += _DEPTH_LEVEL.pack(level.price, level.quantity, level.orders, 0)
    return bytes(out)


def decode_tick(raw: bytes, offset: int = 0) -> NormTick:
    if len(raw) - offset < NORMTICK_SIZE:
        raise ProtocolError(
            f"truncated NormTick: need {NORMTICK_SIZE} bytes at offset {offset}, "
            f"have {len(raw) - offset}"
        )
    values = _TICK_FIXED.unpack_from(raw, offset)
    cursor = offset + _TICK_FIXED.size
    sides: list[tuple[DepthLevel, ...]] = []
    for _ in range(2):
        levels = []
        for _ in range(DEPTH_LEVELS):
            price, quantity, orders, _pad = _DEPTH_LEVEL.unpack_from(raw, cursor)
            cursor += _DEPTH_LEVEL.size
            levels.append(DepthLevel(price=price, quantity=quantity, orders=orders))
        sides.append(tuple(levels))
    return NormTick(
        instrument_token=values[0],
        last_price=values[1],
        average_traded_price=values[2],
        volume_traded=values[3],
        total_buy_quantity=values[4],
        total_sell_quantity=values[5],
        oi=values[6],
        oi_day_high=values[7],
        oi_day_low=values[8],
        ohlc_open=values[9],
        ohlc_high=values[10],
        ohlc_low=values[11],
        ohlc_close=values[12],
        exchange_timestamp=values[13],
        last_trade_time=values[14],
        bids=sides[0],
        asks=sides[1],
    )


def encode_tick_batch(ticks: list[NormTick] | tuple[NormTick, ...]) -> bytes:
    return b"".join(encode_tick(tick) for tick in ticks)


def decode_tick_batch(raw: bytes, count: int) -> list[NormTick]:
    """Decode exactly ``count`` ticks, refusing any length that is not consistent."""
    expected = count * NORMTICK_SIZE
    if len(raw) != expected:
        raise ProtocolError(
            f"tick batch payload is {len(raw)} bytes but the header declares "
            f"{count} tick(s) = {expected} bytes"
        )
    return [decode_tick(raw, i * NORMTICK_SIZE) for i in range(count)]


@dataclass(frozen=True)
class InstrumentInfo:
    """Enough identity for a consumer to interpret a token without TickVault's files.

    Deliberately carries no credentials and no session tokens.
    """

    instrument_token: int
    exchange: str
    tradingsymbol: str
    instrument_type: str
    domain: str
    underlying: str = ""
    expiry: str = ""
    strike_paise: int = 0
    option_side: str = ""


def encode_session(
    *,
    trading_date: str,
    producer: str,
    epoch: int,
    instruments: list[InstrumentInfo] | tuple[InstrumentInfo, ...],
) -> bytes:
    """Encode the session/metadata payload as UTF-8 JSON.

    JSON rather than a packed struct because this message is low-rate (startup, universe
    change, periodic refresh), must stay human-inspectable for operators, and needs to
    gain fields without a protocol bump. Strike stays an integer paise value so it obeys
    the same no-floats rule as the tick stream.
    """
    return json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "trading_date": trading_date,
            "producer": producer,
            "epoch": epoch,
            "instrument_count": len(instruments),
            "instruments": [
                {
                    "instrument_token": info.instrument_token,
                    "exchange": info.exchange,
                    "tradingsymbol": info.tradingsymbol,
                    "instrument_type": info.instrument_type,
                    "domain": info.domain,
                    "underlying": info.underlying,
                    "expiry": info.expiry,
                    "strike_paise": info.strike_paise,
                    "option_side": info.option_side,
                }
                for info in instruments
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def decode_session(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"session payload is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("session payload must be a JSON object")
    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported session protocol version {version!r} "
            f"(this build speaks {PROTOCOL_VERSION})"
        )
    return payload


@dataclass
class SequenceTracker:
    """Consumer-side ordering state: distinguishes in-order, gap, and epoch change.

    Every consumer needs this and none of it can be inferred from ZMQ, which offers no
    delivery guarantee on PUB/SUB. Keeping it here means Python consumers share one
    implementation and other languages have a reference.
    """

    epoch: int | None = None
    last_sequence: int = 0
    gaps: int = 0
    missed: int = 0
    epoch_changes: int = 0
    accepted: int = 0
    history: list[str] = field(default_factory=list)

    def observe(self, header: BusHeader) -> str:
        """Classify ``header`` and update state. Returns one of the outcome strings."""
        outcome = "ok"
        if self.epoch is None:
            outcome = "first"
        elif header.epoch != self.epoch:
            outcome = "epoch_change"
            self.epoch_changes += 1
        elif header.sequence == self.last_sequence + 1:
            outcome = "ok"
        elif header.sequence > self.last_sequence + 1:
            outcome = "gap"
            self.gaps += 1
            self.missed += header.sequence - self.last_sequence - 1
        else:
            outcome = "stale"
        if outcome != "stale":
            self.epoch = header.epoch
            self.last_sequence = header.sequence
            self.accepted += 1
        self.history.append(outcome)
        return outcome
