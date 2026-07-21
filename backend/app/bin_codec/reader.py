"""BIN frame readers.

On open the file is scanned once to build a ``timestamp -> (offset, size)`` index
over the data frames; the header frame is parsed separately. Supports nearest-
timestamp binary search and random-access frame ranges (spec section 6).

Reading is transparent for ``.zst`` files (whole-stream decompress via ``compress``)
and memory-mapped for raw ``.bin`` files (random access without loading it all).

Decoding returns the **raw integer arrays** exactly as written (the codec is
integer-native). Converting prices from paise to rupees is a separate, explicit
step (:func:`paise_to_rupees`) so the round-trip stays bit-exact.

Truncated trailing frame (e.g. a crash mid-write) is ignored: the scan stops at the
last complete frame (see docs/60-operations/failure-modes.md).
"""

from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import numpy as np

from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
    TAG_HEADER,
    Cursor,
    FutureRef,
    IndexFrame,
    IndexHeader,
    InstrColumns,
    RawBlock,
    StockFrame,
    StockHeader,
    StockRef,
)

_LEN = struct.Struct("<I")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")


def paise_to_rupees(arr: np.ndarray) -> np.ndarray:
    """Convert an integer paise array to a float rupees array (value / 100)."""
    return arr.astype(np.float64) / 100.0


@dataclass(frozen=True)
class FrameRecord:
    """Location of one data frame's payload within the buffer."""

    payload_offset: int
    payload_len: int
    timestamp_unix_ms: int


# --------------------------------------------------------------------------- #
# Base: buffer management + framing scan + timestamp index
# --------------------------------------------------------------------------- #


class _BaseReader:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._buf: bytes | mmap.mmap = b""
        self._size = 0
        self._fh = None
        self._mm: mmap.mmap | None = None
        self._header_span: tuple[int, int] | None = None
        self._frames: list[FrameRecord] = []
        self._sorted_ts: np.ndarray = np.empty(0, dtype=np.uint64)
        self._sorted_idx: np.ndarray = np.empty(0, dtype=np.int64)

    # -- open / close -------------------------------------------------------- #

    def open(self) -> "_BaseReader":
        if self.path.suffix == ".zst":
            from app.bin_codec import compress

            data = compress.decompress_to_bytes(self.path)
            self._buf = data
            self._size = len(data)
        else:
            self._fh = open(self.path, "rb")
            self._size = os.fstat(self._fh.fileno()).st_size
            if self._size == 0:
                self._buf = b""
            else:
                self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
                self._buf = self._mm
        self._scan()
        return self

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "_BaseReader":
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- scan ---------------------------------------------------------------- #

    def _scan(self) -> None:
        buf, total = self._buf, self._size
        pos = 0
        frames: list[FrameRecord] = []
        while pos + 4 <= total:
            (length,) = _LEN.unpack_from(buf, pos)
            payload_start = pos + 4
            payload_end = payload_start + length
            if payload_end > total:
                break  # truncated trailing frame -> stop at last complete frame
            tag = _U32.unpack_from(buf, payload_start)[0]
            if tag == TAG_HEADER:
                self._header_span = (payload_start, length)
            else:
                # data frame: u64 timestamp immediately follows the u32 tag.
                ts = _U64.unpack_from(buf, payload_start + 4)[0]
                frames.append(FrameRecord(payload_start, length, ts))
            pos = payload_end
        self._frames = frames
        ts_array = np.array([f.timestamp_unix_ms for f in frames], dtype=np.uint64)
        order = np.argsort(ts_array, kind="stable")
        self._sorted_idx = order.astype(np.int64)
        self._sorted_ts = ts_array[order]

    # -- generic access ------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def timestamps(self) -> list[int]:
        """Data-frame timestamps in file order."""
        return [f.timestamp_unix_ms for f in self._frames]

    def _header_cursor(self) -> Cursor:
        if self._header_span is None:
            raise ValueError(f"{self.path} has no header frame")
        offset, length = self._header_span
        return Cursor(self._buf, offset)

    def _frame_cursor(self, index: int) -> Cursor:
        rec = self._frames[index]
        return Cursor(self._buf, rec.payload_offset)

    def nearest_index(self, timestamp_unix_ms: int) -> int:
        """Index (file order) of the frame whose timestamp is closest to the target."""
        if not self._frames:
            raise ValueError("no data frames")
        pos = int(np.searchsorted(self._sorted_ts, timestamp_unix_ms, side="left"))
        candidates = []
        if pos < len(self._sorted_ts):
            candidates.append(pos)
        if pos > 0:
            candidates.append(pos - 1)
        best = min(candidates, key=lambda p: abs(int(self._sorted_ts[p]) - timestamp_unix_ms))
        return int(self._sorted_idx[best])

    def indices_in_range(self, start_ms: int, end_ms: int) -> list[int]:
        """Frame indices (ascending by timestamp) with start_ms <= ts <= end_ms."""
        lo = int(np.searchsorted(self._sorted_ts, start_ms, side="left"))
        hi = int(np.searchsorted(self._sorted_ts, end_ms, side="right"))
        return [int(i) for i in self._sorted_idx[lo:hi]]


# --------------------------------------------------------------------------- #
# Shared block decoders
# --------------------------------------------------------------------------- #


def _decode_raw_block(cur: Cursor) -> RawBlock:
    columns = {col.name: cur.vec(col.dtype) for col in RAW_BLOCK_COLUMNS}
    return RawBlock(columns)


def _decode_instr_columns(cur: Cursor) -> InstrColumns:
    scalars = {col.name: cur.vec(col.dtype) for col in INSTR_SCALAR_COLUMNS}
    n_levels = cur.u64()
    depth = []
    for _ in range(n_levels):
        depth.append({col.name: cur.vec(col.dtype) for col in DEPTH_LEVEL_COLUMNS})
    return InstrColumns(scalars, depth)


# --------------------------------------------------------------------------- #
# Index reader
# --------------------------------------------------------------------------- #


class IndexBinReader(_BaseReader):
    def header(self) -> IndexHeader:
        cur = self._header_cursor()
        cur.u32()  # tag
        schema_version = cur.u32()
        trading_date = cur.string()
        underlying = cur.string()
        expiry_date = cur.string()
        risk_free_rate = cur.f64()
        strikes = cur.vec(np.dtype("<i8"))
        return IndexHeader(
            trading_date=trading_date,
            underlying=underlying,
            expiry_date=expiry_date,
            risk_free_rate=risk_free_rate,
            strikes=strikes,
            schema_version=schema_version,
        )

    def frame(self, index: int) -> IndexFrame:
        cur = self._frame_cursor(index)
        cur.u32()  # tag
        timestamp = cur.u64()
        sequence = cur.u64()
        spot_price = cur.i64()
        vix = cur.i64()
        calls = _decode_raw_block(cur)
        puts = _decode_raw_block(cur)
        return IndexFrame(timestamp, sequence, spot_price, vix, calls, puts)

    def frames(self):
        for i in range(len(self)):
            yield self.frame(i)

    def frame_at(self, timestamp_unix_ms: int) -> IndexFrame:
        return self.frame(self.nearest_index(timestamp_unix_ms))

    def frames_in_range(self, start_ms: int, end_ms: int) -> list[IndexFrame]:
        return [self.frame(i) for i in self.indices_in_range(start_ms, end_ms)]


# --------------------------------------------------------------------------- #
# Stock reader
# --------------------------------------------------------------------------- #


class StockBinReader(_BaseReader):
    def header(self) -> StockHeader:
        cur = self._header_cursor()
        cur.u32()  # tag
        schema_version = cur.u32()
        trading_date = cur.string()
        risk_free_rate = cur.f64()
        n_stocks = cur.u64()
        stocks: list[StockRef] = []
        for _ in range(n_stocks):
            tradingsymbol = cur.string()
            name = cur.string()
            spot_token = cur.u64()
            lot_size = cur.u32()
            n_fut = cur.u64()
            futures = [
                FutureRef(token=cur.u64(), expiry=cur.string(), lot_size=cur.u32())
                for _ in range(n_fut)
            ]
            stocks.append(
                StockRef(
                    tradingsymbol=tradingsymbol,
                    name=name,
                    spot_token=spot_token,
                    lot_size=lot_size,
                    futures=futures,
                )
            )
        return StockHeader(
            trading_date=trading_date,
            risk_free_rate=risk_free_rate,
            stocks=stocks,
            schema_version=schema_version,
        )

    def frame(self, index: int) -> StockFrame:
        cur = self._frame_cursor(index)
        cur.u32()  # tag
        timestamp = cur.u64()
        sequence = cur.u64()
        spot = _decode_instr_columns(cur)
        fut_current = _decode_instr_columns(cur)
        fut_mid = _decode_instr_columns(cur)
        fut_far = _decode_instr_columns(cur)
        return StockFrame(timestamp, sequence, spot, fut_current, fut_mid, fut_far)

    def frames(self):
        for i in range(len(self)):
            yield self.frame(i)

    def frame_at(self, timestamp_unix_ms: int) -> StockFrame:
        return self.frame(self.nearest_index(timestamp_unix_ms))

    def frames_in_range(self, start_ms: int, end_ms: int) -> list[StockFrame]:
        return [self.frame(i) for i in self.indices_in_range(start_ms, end_ms)]
