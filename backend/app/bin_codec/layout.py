"""Single source of truth for the BIN byte layout.

Everything about *how* bytes are arranged lives here: primitive encode/decode,
enum tags, NumPy dtypes, the fixed column order of every block, and the logical
frame data models. ``writer.py`` and ``reader.py`` import from this module only, so
the on-disk format is defined in exactly one place.

Authoritative spec: docs/20-data-and-storage/bin-structure-spec.md.

All integers are little-endian and fixed-width:
    u32 (4)  u64 (8)  i64 (8, signed -- prices in paise = value*100)  f64 (8, IEEE-754)
    String := u64 byte-length + UTF-8 bytes
    Vec<T>  := u64 element-count + elements
    enum tag: u32  (0 = Header, 1 = Data)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = 1

TAG_HEADER = 0
TAG_DATA = 1

# Number of order-book depth levels stored per instrument leg (stocks = L5).
DEPTH_LEVELS = 5

# NumPy dtypes -- explicitly little-endian so ``tobytes()`` == wire layout.
DT_I64 = np.dtype("<i8")  # prices in paise
DT_U64 = np.dtype("<u8")  # quantities, OI, volume, tokens, timestamps
DT_U32 = np.dtype("<u4")  # order counts

# struct formats for scalars (little-endian).
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_I64 = struct.Struct("<q")
_F64 = struct.Struct("<d")

# Frame length prefix: [u32 LE payload_len].
_LEN = struct.Struct("<I")


# --------------------------------------------------------------------------- #
# Column specifications (fixed order -- do NOT reorder without a schema bump)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Column:
    """One columnar field: its name, wire dtype, and whether it is a price (paise)."""

    name: str
    dtype: np.dtype
    is_price: bool = False


# Index option-chain RawBlock (L1): 15 columns per side, each a Vec aligned to strikes.
RAW_BLOCK_COLUMNS: tuple[Column, ...] = (
    Column("ltp", DT_I64, is_price=True),
    Column("oi", DT_U64),
    Column("volume", DT_U64),
    Column("buy_quantity", DT_U64),
    Column("sell_quantity", DT_U64),
    Column("bid", DT_I64, is_price=True),
    Column("bid_qty", DT_U64),
    Column("ask", DT_I64, is_price=True),
    Column("ask_qty", DT_U64),
    Column("oi_day_high", DT_U64),
    Column("oi_day_low", DT_U64),
    Column("ohlc_open", DT_I64, is_price=True),
    Column("ohlc_high", DT_I64, is_price=True),
    Column("ohlc_low", DT_I64, is_price=True),
    Column("ohlc_close", DT_I64, is_price=True),
)

# Stock InstrColumns scalar part: 11 columns, each a Vec of length N (stocks).
INSTR_SCALAR_COLUMNS: tuple[Column, ...] = (
    Column("ltp", DT_I64, is_price=True),
    Column("oi", DT_U64),
    Column("volume", DT_U64),
    Column("buy_quantity", DT_U64),
    Column("sell_quantity", DT_U64),
    Column("oi_day_high", DT_U64),
    Column("oi_day_low", DT_U64),
    Column("ohlc_open", DT_I64, is_price=True),
    Column("ohlc_high", DT_I64, is_price=True),
    Column("ohlc_low", DT_I64, is_price=True),
    Column("ohlc_close", DT_I64, is_price=True),
)

# One order-book depth level: 6 columns, each a Vec of length N.
DEPTH_LEVEL_COLUMNS: tuple[Column, ...] = (
    Column("bid_price", DT_I64, is_price=True),
    Column("bid_qty", DT_U64),
    Column("bid_orders", DT_U32),
    Column("ask_price", DT_I64, is_price=True),
    Column("ask_qty", DT_U64),
    Column("ask_orders", DT_U32),
)

RAW_BLOCK_COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in RAW_BLOCK_COLUMNS)
INSTR_SCALAR_COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in INSTR_SCALAR_COLUMNS)
DEPTH_LEVEL_COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in DEPTH_LEVEL_COLUMNS)


# --------------------------------------------------------------------------- #
# Primitive encoders (append onto a bytearray)
# --------------------------------------------------------------------------- #


def put_u32(out: bytearray, value: int) -> None:
    out += _U32.pack(value)


def put_u64(out: bytearray, value: int) -> None:
    out += _U64.pack(value)


def put_i64(out: bytearray, value: int) -> None:
    out += _I64.pack(value)


def put_f64(out: bytearray, value: float) -> None:
    out += _F64.pack(value)


def put_string(out: bytearray, value: str) -> None:
    raw = value.encode("utf-8")
    out += _U64.pack(len(raw))
    out += raw


def put_vec(out: bytearray, arr: np.ndarray, dtype: np.dtype) -> None:
    """Encode ``Vec<T>``: u64 element-count then the little-endian element bytes."""
    contiguous = np.ascontiguousarray(arr, dtype=dtype)
    if contiguous.ndim != 1:
        raise ValueError(f"Vec must be 1-D, got shape {contiguous.shape}")
    out += _U64.pack(contiguous.shape[0])
    out += contiguous.tobytes()


def frame_bytes(payload: bytes | bytearray) -> bytes:
    """Wrap a payload as a framed record: ``[u32 LE len][payload]``."""
    return _LEN.pack(len(payload)) + bytes(payload)


# --------------------------------------------------------------------------- #
# Cursor: sequential decoder over any buffer (bytes / memoryview / mmap)
# --------------------------------------------------------------------------- #


class Cursor:
    """A forward-only reader over a byte buffer."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def u32(self) -> int:
        value = _U32.unpack_from(self.buf, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        value = _U64.unpack_from(self.buf, self.pos)[0]
        self.pos += 8
        return value

    def i64(self) -> int:
        value = _I64.unpack_from(self.buf, self.pos)[0]
        self.pos += 8
        return value

    def f64(self) -> float:
        value = _F64.unpack_from(self.buf, self.pos)[0]
        self.pos += 8
        return value

    def string(self) -> str:
        n = self.u64()
        raw = bytes(self.buf[self.pos : self.pos + n])
        self.pos += n
        return raw.decode("utf-8")

    def vec(self, dtype: np.dtype) -> np.ndarray:
        n = self.u64()
        nbytes = n * dtype.itemsize
        chunk = bytes(self.buf[self.pos : self.pos + nbytes])
        self.pos += nbytes
        # .copy() -> owned, writable array independent of the source buffer.
        return np.frombuffer(chunk, dtype=dtype).copy()


# --------------------------------------------------------------------------- #
# Logical frame data models
# --------------------------------------------------------------------------- #


@dataclass
class RawBlock:
    """L1 option-chain block: one NumPy array per column in RAW_BLOCK_COLUMNS."""

    columns: dict[str, np.ndarray]

    @classmethod
    def zeros(cls, n: int) -> RawBlock:
        return cls({c.name: np.zeros(n, dtype=c.dtype) for c in RAW_BLOCK_COLUMNS})

    def length(self) -> int:
        return int(self.columns[RAW_BLOCK_COLUMN_NAMES[0]].shape[0])

    def validate(self, expected_len: int | None = None) -> None:
        names = set(self.columns)
        if names != set(RAW_BLOCK_COLUMN_NAMES):
            missing = set(RAW_BLOCK_COLUMN_NAMES) - names
            extra = names - set(RAW_BLOCK_COLUMN_NAMES)
            raise ValueError(f"RawBlock columns mismatch (missing={missing}, extra={extra})")
        n = expected_len if expected_len is not None else self.length()
        for c in RAW_BLOCK_COLUMNS:
            if self.columns[c.name].shape[0] != n:
                raise ValueError(f"RawBlock column '{c.name}' length != {n}")


@dataclass
class InstrColumns:
    """One instrument leg (spot / a future) across all N stocks, with L5 depth."""

    scalars: dict[str, np.ndarray]
    depth: list[dict[str, np.ndarray]]  # length DEPTH_LEVELS; index 0 = best (L1)

    @classmethod
    def zeros(cls, n: int) -> InstrColumns:
        scalars = {c.name: np.zeros(n, dtype=c.dtype) for c in INSTR_SCALAR_COLUMNS}
        depth = [
            {c.name: np.zeros(n, dtype=c.dtype) for c in DEPTH_LEVEL_COLUMNS}
            for _ in range(DEPTH_LEVELS)
        ]
        return cls(scalars, depth)

    def length(self) -> int:
        return int(self.scalars[INSTR_SCALAR_COLUMN_NAMES[0]].shape[0])

    def validate(self, expected_len: int | None = None) -> None:
        if set(self.scalars) != set(INSTR_SCALAR_COLUMN_NAMES):
            raise ValueError("InstrColumns scalar columns mismatch")
        n = expected_len if expected_len is not None else self.length()
        for c in INSTR_SCALAR_COLUMNS:
            if self.scalars[c.name].shape[0] != n:
                raise ValueError(f"InstrColumns scalar '{c.name}' length != {n}")
        if len(self.depth) != DEPTH_LEVELS:
            raise ValueError(f"InstrColumns depth must have {DEPTH_LEVELS} levels")
        for level, dl in enumerate(self.depth):
            if set(dl) != set(DEPTH_LEVEL_COLUMN_NAMES):
                raise ValueError(f"InstrColumns depth level {level} columns mismatch")
            for c in DEPTH_LEVEL_COLUMNS:
                if dl[c.name].shape[0] != n:
                    raise ValueError(f"InstrColumns depth[{level}].'{c.name}' length != {n}")


@dataclass
class IndexHeader:
    """IndexHeader (tag 0) -- written once per index file."""

    trading_date: str
    underlying: str
    expiry_date: str
    risk_free_rate: float
    strikes: np.ndarray  # i64 paise, ascending, fixed for the day
    schema_version: int = SCHEMA_VERSION


@dataclass
class IndexFrame:
    """IndexFrame (tag 1) -- one per second."""

    timestamp_unix_ms: int
    sequence: int
    spot_price: int  # paise
    vix: int  # x100
    calls: RawBlock
    puts: RawBlock


@dataclass
class FutureRef:
    token: int
    expiry: str
    lot_size: int


@dataclass
class StockRef:
    tradingsymbol: str
    name: str
    spot_token: int
    lot_size: int
    futures: list[FutureRef] = field(default_factory=list)  # 1..3 ordered [current,mid,far]


@dataclass
class StockHeader:
    """StockHeader (tag 0) -- written once per stocks file."""

    trading_date: str
    risk_free_rate: float
    stocks: list[StockRef]
    schema_version: int = SCHEMA_VERSION


@dataclass
class StockFrame:
    """StockFrame (tag 1) -- one per second; four instrument legs as matrices."""

    timestamp_unix_ms: int
    sequence: int
    spot: InstrColumns
    fut_current: InstrColumns
    fut_mid: InstrColumns
    fut_far: InstrColumns

    def legs(self) -> tuple[InstrColumns, InstrColumns, InstrColumns, InstrColumns]:
        return (self.spot, self.fut_current, self.fut_mid, self.fut_far)
