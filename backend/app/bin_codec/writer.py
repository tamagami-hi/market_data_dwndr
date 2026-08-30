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
from typing import Any

from app.bin_codec import layout
from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
    TAG_DATA,
    TAG_HEADER,
    IndexFnoFrame,
    IndexFnoHeader,
    IndexFrame,
    IndexHeader,
    InstrColumns,
    RawBlock,
    StockFrame,
    StockHeader,
)
from app.bin_codec.scan import (
    AXIS_INDEX,
    AXIS_INDEX_FNO,
    AXIS_STOCK,
    read_header_axis,
)

# Hard cap on ``<date>.pN.bin`` segments per artifact per day. The axis only changes on a
# restart, so a day needing more than this has a far worse problem than segmentation.
MAX_SEGMENTS = 24

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


def encode_index_fno_header(header: IndexFnoHeader) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_HEADER)
    layout.put_u32(out, header.schema_version)
    layout.put_string(out, header.trading_date)
    layout.put_f64(out, header.risk_free_rate)
    layout.put_u64(out, len(header.indices))  # Vec<IndexFnoRef> count
    for index in header.indices:
        layout.put_string(out, index.underlying)
        layout.put_string(out, index.spot_symbol)
        layout.put_u64(out, index.spot_token)
        layout.put_u64(out, len(index.futures))  # Vec<FutureRef> count
        for fut in index.futures:
            layout.put_u64(out, fut.token)
            layout.put_string(out, fut.expiry)
            layout.put_u32(out, fut.lot_size)
    return bytes(out)


def encode_index_fno_frame(frame: IndexFnoFrame, n_indices: int) -> bytes:
    out = bytearray()
    layout.put_u32(out, TAG_DATA)
    layout.put_u64(out, frame.timestamp_unix_ms)
    layout.put_u64(out, frame.sequence)
    for leg in frame.legs():
        _encode_instr_columns(out, leg, n_indices)
    return bytes(out)


class HeaderAxisMismatch(RuntimeError):
    """Raised when a reopened file's header declares a different axis than the new header.

    The header is written once, when the file is empty; the in-memory axis is rebuilt on
    every bootstrap. For an index chain the ATM ± 50 window re-centres as spot moves and
    clips against the available strike list, so a mid-day restart can legitimately produce
    a different strike count. Appending those frames behind the original header silently
    mis-attributes every column, because ``header.strikes[j]`` no longer describes column
    ``j``. Raising here lets the caller roll a fresh segment instead
    (:func:`app.bin_codec.writer.resolve_segment_path`).
    """

    def __init__(self, path: Path, on_disk: int, incoming: int) -> None:
        super().__init__(
            f"{path.name}: header on disk declares an axis of {on_disk}, "
            f"but the incoming header has {incoming}"
        )
        self.path = path
        self.on_disk = on_disk
        self.incoming = incoming


def resolve_segment_path(path: str | os.PathLike[str], axis_len: int, kind: str) -> Path:
    """Return the file this generation should append to, rolling a segment if needed.

    Returns ``path`` itself when the file is empty or its header already describes
    ``axis_len``. Otherwise returns the first ``<stem>.pN<suffix>`` sibling that is empty
    or axis-compatible, so a shifted strike window is recorded honestly in its own file
    with its own header rather than crammed onto a stale axis.

    Segments are ordinary ``.bin`` files in the same directory, so the EOD sweep archives
    them with no change (it globs ``*.bin`` recursively) and the reader opens each one
    normally.
    """
    base = Path(path)
    candidate = base
    for segment in range(1, MAX_SEGMENTS + 1):
        on_disk = read_header_axis(candidate, kind)
        if on_disk is None or on_disk == axis_len:
            return candidate
        candidate = base.with_name(f"{base.stem}.p{segment + 1}{base.suffix}")
    raise HeaderAxisMismatch(base, -1, axis_len)


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

    # Which header layout this writer emits, for the axis read-back on reopen.
    _axis_kind: str = AXIS_STOCK

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        self.path = Path(path)
        self._fh = None
        self._header_written = False
        self._sync = sync
        # The axis the file on disk already commits to (None when the file is new).
        self._axis_on_disk: int | None = None
        self._bound_header: Any | None = None

    def open(self) -> _BaseWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 'ab' = append; the header is only emitted when the file is empty.
        self._header_written = self.path.exists() and self.path.stat().st_size > 0
        self._axis_on_disk = (
            read_header_axis(self.path, self._axis_kind) if self._header_written else None
        )
        self._fh = open(self.path, "ab")
        return self

    def _commit_axis(self, incoming: int) -> int:
        """Reconcile the incoming header's axis with the one the file already declares.

        Returns the axis every frame in this file must use. Raises
        :class:`HeaderAxisMismatch` when the file already commits to a different one —
        the caller is expected to roll a new segment rather than corrupt the mapping.
        """
        if self._header_written and self._axis_on_disk is not None:
            if self._axis_on_disk != incoming:
                raise HeaderAxisMismatch(self.path, self._axis_on_disk, incoming)
            return self._axis_on_disk
        return incoming

    def bind_header(self, header) -> None:
        """Commit the file's axis from ``header`` without emitting the header record.

        Split from :meth:`write_header` so a generation that never receives a tick leaves
        no header behind. The header is what fixes a file's fixed-width axis, so writing
        it from an empty generation is how a five-frame all-zero start came to define the
        strike mapping for a whole trading day.
        """
        raise NotImplementedError

    def flush_header(self) -> bool:
        """Emit the bound header record if the file is empty. Returns True if written."""
        header = self._bound_header
        if header is None:
            raise RuntimeError("bind_header must be called before flush_header")
        if self._header_written:
            return False
        self._write_framed(self._encode_header(header))
        self._header_written = True
        return True

    def _encode_header(self, header) -> bytes:
        raise NotImplementedError

    def write_header(self, header) -> bool:
        """Bind the axis and emit the header record (the eager, single-step form)."""
        self.bind_header(header)
        return self.flush_header()

    def _write_framed(self, payload: bytes) -> None:
        if self._fh is None:
            raise RuntimeError("writer is not open")
        self._fh.write(layout.frame_bytes(payload))
        self._fh.flush()
        if self._sync:
            # Push the just-appended frame past the OS page cache onto the device,
            # so a crash or power loss cannot lose an already-captured second.
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())  # durable close for every writer
            except OSError:
                pass
            self._fh.close()
            self._fh = None
        # A generation that never wrote a header wrote nothing at all: opening in append
        # mode created the file, so remove the empty shell rather than leave a 0-byte
        # ``.bin`` for the EOD sweep to archive and the segment resolver to reason about.
        if not self._header_written:
            try:
                if self.path.exists() and self.path.stat().st_size == 0:
                    self.path.unlink()
            except OSError:  # pragma: no cover - best effort tidy-up
                pass

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

    _axis_kind = AXIS_INDEX

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        super().__init__(path, sync=sync)
        self._n_strikes: int | None = None

    def bind_header(self, header: IndexHeader) -> None:
        """Commit the strike axis, taking it from whichever header the file is bound to.

        The width is never taken blindly from the incoming header: assigning it before the
        header-once guard is what let post-restart frames be encoded at a new strike count
        behind a header describing the old set.
        """
        self._n_strikes = self._commit_axis(int(header.strikes.shape[0]))
        self._bound_header = header

    def _encode_header(self, header: IndexHeader) -> bytes:
        return encode_index_header(header)

    def append_frame(self, frame: IndexFrame) -> None:
        if not self._header_written:
            raise RuntimeError("index header must be written before frames")
        if self._n_strikes is None:
            raise RuntimeError("call write_header first to establish strike count")
        self._write_framed(encode_index_frame(frame, self._n_strikes))


class StockBinWriter(_BaseWriter):
    """Writer for the daily stocks matrix file (all F&O stocks)."""

    _axis_kind = AXIS_STOCK

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        super().__init__(path, sync=sync)
        self._n_stocks: int | None = None

    def bind_header(self, header: StockHeader) -> None:
        self._n_stocks = self._commit_axis(len(header.stocks))
        self._bound_header = header

    def _encode_header(self, header: StockHeader) -> bytes:
        return encode_stock_header(header)

    def append_frame(self, frame: StockFrame) -> None:
        if not self._header_written:
            raise RuntimeError("stock header must be written before frames")
        if self._n_stocks is None:
            raise RuntimeError("call write_header first to establish stock count")
        self._write_framed(encode_stock_frame(frame, self._n_stocks))




class IndexFnoBinWriter(_BaseWriter):
    """Writer for the daily consolidated index-F&O file (all configured indices).

    A separate file and a separate header from the stocks writer, deliberately: the stock
    F&O binary contract is untouched by this domain, so existing historical data stays
    readable and the two schemas can evolve independently.
    """

    _axis_kind = AXIS_INDEX_FNO

    def __init__(self, path: str | os.PathLike[str], *, sync: bool = False) -> None:
        super().__init__(path, sync=sync)
        self._n_indices: int | None = None

    def bind_header(self, header: IndexFnoHeader) -> None:
        self._n_indices = self._commit_axis(len(header.indices))
        self._bound_header = header

    def _encode_header(self, header: IndexFnoHeader) -> bytes:
        return encode_index_fno_header(header)

    def append_frame(self, frame: IndexFnoFrame) -> None:
        if not self._header_written:
            raise RuntimeError("index-F&O header must be written before frames")
        if self._n_indices is None:
            raise RuntimeError("call write_header first to establish index count")
        self._write_framed(encode_index_fno_frame(frame, self._n_indices))
