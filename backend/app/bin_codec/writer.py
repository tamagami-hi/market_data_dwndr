"""BIN frame writers.

Encodes the logical frame models from ``layout`` into the exact wire bytes and
appends them as ``[u32 LE len][payload]`` records. The header frame (tag 0) is
written **only when the file is empty**, so a mid-day restart that reopens today's
file never writes a duplicate header (see docs/60-operations/failure-modes.md).

Writers flush after every frame so at most the in-flight frame is ever at risk.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from app.bin_codec import layout
from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
    TAG_DATA,
    TAG_HEADER,
    IndexFrame,
    IndexHeader,
    InstrColumns,
    RawBlock,
    StockFrame,
    StockHeader,
)

# --------------------------------------------------------------------------- #
# Payload encoders (pure functions -> bytes, easy to unit-test)
# --------------------------------------------------------------------------- #


def _encode_raw_block(out: bytearray, block: RawBlock, expected_len: int) -> None:
    block.validate(expected_len)
    for col in RAW_BLOCK_COLUMNS:
        layout.put_vec(out, block.columns[col.name], col.dtype)


def _encode_instr_columns(out: bytearray, instr: InstrColumns, expected_len: int) -> None:
    instr.validate(expected_len)
    for col in INSTR_SCALAR_COLUMNS:
        layout.put_vec(out, instr.scalars[col.name], col.dtype)
    # Vec<DepthLevel> depth: u64 level-count then each level's 6 columns.
    layout.put_u64(out, len(instr.depth))
    for level in instr.depth:
        for col in DEPTH_LEVEL_COLUMNS:
            layout.put_vec(out, level[col.name], col.dtype)


def encode_index_header(header: IndexHeader) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_HEADER)
    layout.put_u32(out, header.schema_version)
    layout.put_string(out, header.trading_date)
    layout.put_string(out, header.underlying)
    layout.put_string(out, header.expiry_date)
    layout.put_f64(out, header.risk_free_rate)
    layout.put_vec(out, header.strikes, layout.DT_I64)
    return bytes(out)


def encode_index_frame(frame: IndexFrame, n_strikes: int) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_DATA)
    layout.put_u64(out, frame.timestamp_unix_ms)
    layout.put_u64(out, frame.sequence)
    layout.put_i64(out, frame.spot_price)
    layout.put_i64(out, frame.vix)
    _encode_raw_block(out, frame.calls, n_strikes)
    _encode_raw_block(out, frame.puts, n_strikes)
    return bytes(out)


def encode_stock_header(header: StockHeader) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_HEADER)
    layout.put_u32(out, header.schema_version)
    layout.put_string(out, header.trading_date)
    layout.put_f64(out, header.risk_free_rate)
    layout.put_u64(out, len(header.stocks))  # Vec<StockRef> count
    for stock in header.stocks:
        layout.put_string(out, stock.tradingsymbol)
        layout.put_string(out, stock.name)
        layout.put_u64(out, stock.spot_token)
        layout.put_u32(out, stock.lot_size)
        layout.put_u64(out, len(stock.futures))  # Vec<FutureRef> count
        for fut in stock.futures:
            layout.put_u64(out, fut.token)
            layout.put_string(out, fut.expiry)
            layout.put_u32(out, fut.lot_size)
    return bytes(out)


def encode_stock_frame(frame: StockFrame, n_stocks: int) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_DATA)
    layout.put_u64(out, frame.timestamp_unix_ms)
    layout.put_u64(out, frame.sequence)
    for leg in frame.legs():
        _encode_instr_columns(out, leg, n_stocks)
    return bytes(out)


# --------------------------------------------------------------------------- #
# File writers (append-only, header-once)
# --------------------------------------------------------------------------- #


class _BaseWriter:
    """Common append-only file handling with header-once semantics.

    ``sync=True`` fsyncs every frame after flushing so each record is durably on
    disk (used by the live 1 Hz capture writers, where the fsync cost is trivial
    and zero data loss on crash/power-loss matters). Bulk writers (e.g. historical
    backfill) leave it False to keep throughput high, relying on the fsync-on-close.
    """

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        self.path = Path(path)
        self._fh = None
        self._header_written = False
        self._sync = sync

    def open(self) -> _BaseWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 'ab' = append; the header is only emitted when the file is empty.
        self._header_written = self.path.exists() and self.path.stat().st_size > 0
        self._fh = open(self.path, "ab")
        return self

    def _write_framed(self, payload: bytes) -> None:
        if self._fh is None:
            raise RuntimeError("writer is not open")
        self._fh.write(layout.frame_bytes(payload))
        self._fh.flush()
        if self._sync:
            # Push the just-appended frame past the OS page cache onto the device,
            # so a crash or power loss cannot lose an already-captured second.
            os.fsync(self._fh.fileno())

    @property
    def header_written(self) -> bool:
        return self._header_written

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())  # durable close for every writer
            except OSError:
                pass
            self._fh.close()
            self._fh = None

    def __enter__(self) -> _BaseWriter:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class IndexBinWriter(_BaseWriter):
    """Writer for an index option-chain file (one index, one trading day)."""

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        super().__init__(path, sync=sync)
        self._n_strikes: int | None = None

    def write_header(self, header: IndexHeader) -> bool:
        """Write the header iff the file is empty. Returns True if written now."""
        self._n_strikes = int(header.strikes.shape[0])
        if self._header_written:
            return False
        self._write_framed(encode_index_header(header))
        self._header_written = True
        return True

    def append_frame(self, frame: IndexFrame) -> None:
        if not self._header_written:
            raise RuntimeError("index header must be written before frames")
        if self._n_strikes is None:
            raise RuntimeError("call write_header first to establish strike count")
        self._write_framed(encode_index_frame(frame, self._n_strikes))


class StockBinWriter(_BaseWriter):
    """Writer for the daily stocks matrix file (all F&O stocks)."""

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        super().__init__(path, sync=sync)
        self._n_stocks: int | None = None

    def write_header(self, header: StockHeader) -> bool:
        self._n_stocks = len(header.stocks)
        if self._header_written:
            return False
        self._write_framed(encode_stock_header(header))
        self._header_written = True
        return True

    def append_frame(self, frame: StockFrame) -> None:
        if not self._header_written:
            raise RuntimeError("stock header must be written before frames")
        if self._n_stocks is None:
            raise RuntimeError("call write_header first to establish stock count")
        self._write_framed(encode_stock_frame(frame, self._n_stocks))
