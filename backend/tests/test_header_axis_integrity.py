"""Every frame in a file must match the axis its own header declares.

Guards RC-2, the silent corruption. ``IndexBinWriter.write_header`` used to assign the
frame width *before* the header-once guard, so after a mid-day restart the on-disk header
kept the old strike vector while frames were encoded at the new width. Because
``option_chain_filter`` re-centres the ATM ± 50 window on spot and clips against the
available strike list, a restart can legitimately change the strike count — and then
``header.strikes[j]`` no longer describes column ``j``.

Measured in the production archive: 8 of 15 NIFTY sessions affected, ~128,110 frames. On
2026-08-26 and 2026-08-27 the header came from a five-frame all-zero generation, so 99.98%
of the day was mapped onto the wrong strikes. Nothing detected it — the frames decode fine,
the frame count is right, and every dashboard field reads healthy.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.bin_codec.reader import IndexBinReader
from app.bin_codec.scan import AXIS_INDEX, AXIS_STOCK, read_header_axis, scan_frames
from app.bin_codec.writer import (
    HeaderAxisMismatch,
    IndexBinWriter,
    resolve_segment_path,
)


def _header(n_strikes: int, *, first_strike: int = 1_000_000) -> IndexHeader:
    """An index header whose window starts at ``first_strike`` (50-point steps, paise)."""
    return IndexHeader(
        trading_date="2026-08-27",
        underlying="NIFTY",
        expiry_date="2026-09-01",
        risk_free_rate=0.052603,
        strikes=np.arange(
            first_strike, first_strike + n_strikes * 5_000, 5_000, dtype=np.int64
        ),
    )


def _write(path, header: IndexHeader, count: int, start_ts: int = 1_000) -> None:
    n = int(header.strikes.shape[0])
    writer = IndexBinWriter(path)
    writer.open()
    writer.write_header(header)
    for i in range(count):
        writer.append_frame(
            IndexFrame(
                timestamp_unix_ms=start_ts + i * 1_000,
                sequence=i,
                spot_price=2_456_705,
                vix=1_234,
                calls=RawBlock.zeros(n),
                puts=RawBlock.zeros(n),
            )
        )
    writer.close()


def _frame_widths(path) -> set[int]:
    """Column length of every data frame, decoded independently of the header."""
    with IndexBinReader(path) as reader:
        return {
            int(reader.frame_at(i).calls.columns["ltp"].shape[0])
            for i in range(len(reader))
        }


def test_reopening_with_a_different_axis_is_refused(tmp_path):
    """The exact production condition, on one file: it must raise, not silently append."""
    path = tmp_path / "2026-08-27.bin"
    _write(path, _header(97), 5)
    assert read_header_axis(path, AXIS_INDEX) == 97

    writer = IndexBinWriter(path)
    writer.open()
    try:
        with pytest.raises(HeaderAxisMismatch) as excinfo:
            writer.write_header(_header(94, first_strike=1_010_000))
    finally:
        writer.close()
    assert excinfo.value.on_disk == 97
    assert excinfo.value.incoming == 94


def test_reopening_with_the_same_axis_appends_normally(tmp_path):
    """Header-once behaviour is preserved for the overwhelmingly common case."""
    path = tmp_path / "2026-08-27.bin"
    _write(path, _header(101), 4)
    _write(path, _header(101), 4, start_ts=9_000)

    assert scan_frames(path).frames == 8
    assert _frame_widths(path) == {101}
    with IndexBinReader(path) as reader:
        assert reader.header().strikes.shape[0] == 101


def test_a_shifted_window_rolls_into_its_own_segment(tmp_path):
    """No data is dropped: the new axis gets a new file with its own honest header."""
    base = tmp_path / "2026-08-27.bin"
    assert resolve_segment_path(base, 97, AXIS_INDEX) == base
    _write(base, _header(97), 5)

    rolled = resolve_segment_path(base, 94, AXIS_INDEX)
    assert rolled.name == "2026-08-27.p2.bin"
    _write(rolled, _header(94, first_strike=1_010_000), 3, start_ts=9_000)

    # Both files self-describe correctly — the property the archive lacked.
    for path, axis, frames in ((base, 97, 5), (rolled, 94, 3)):
        assert read_header_axis(path, AXIS_INDEX) == axis
        assert _frame_widths(path) == {axis}
        assert scan_frames(path).frames == frames


def test_a_returning_axis_reuses_its_original_segment(tmp_path):
    """Segments are keyed by axis, so an oscillating window does not fan out files."""
    base = tmp_path / "2026-08-27.bin"
    _write(base, _header(97), 2)
    rolled = resolve_segment_path(base, 94, AXIS_INDEX)
    _write(rolled, _header(94, first_strike=1_010_000), 2, start_ts=9_000)

    assert resolve_segment_path(base, 97, AXIS_INDEX) == base
    assert resolve_segment_path(base, 94, AXIS_INDEX) == rolled
    assert resolve_segment_path(base, 88, AXIS_INDEX).name == "2026-08-27.p3.bin"


def test_three_generations_with_three_axes_each_stay_self_consistent(tmp_path):
    """The 2026-08-27 shape: 97 -> 96 -> 94, five frames in the first generation."""
    base = tmp_path / "2026-08-27.bin"
    generations = [(97, 5), (96, 8_692), (94, 3_856)]
    written: list[tuple] = []
    for axis, count in generations:
        target = resolve_segment_path(base, axis, AXIS_INDEX)
        _write(target, _header(axis, first_strike=1_000_000 + axis), count, start_ts=axis * 1000)
        written.append((target, axis, count))

    assert len({t for t, _, _ in written}) == 3, "each axis gets its own segment"
    for target, axis, count in written:
        assert read_header_axis(target, AXIS_INDEX) == axis
        assert _frame_widths(target) == {axis}, "no frame may sit on a foreign axis"
        assert scan_frames(target).frames == count


def test_read_header_axis_ignores_a_missing_or_empty_file(tmp_path):
    assert read_header_axis(tmp_path / "nope.bin", AXIS_INDEX) is None
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert read_header_axis(empty, AXIS_INDEX) is None


def test_read_header_axis_reads_the_stock_layout_too(tmp_path):
    """Stocks and index-F&O share a header shape; the row count is their axis."""
    from app.bin_codec.layout import FutureRef, StockHeader, StockRef
    from app.bin_codec.writer import StockBinWriter

    refs = [
        StockRef(
            tradingsymbol=f"S{i}",
            name=f"S{i}",
            spot_token=i,
            lot_size=100,
            futures=[FutureRef(token=1_000 + i, expiry="2026-09-25", lot_size=100)],
        )
        for i in range(7)
    ]
    path = tmp_path / "2026-08-27.bin"
    writer = StockBinWriter(path)
    writer.open()
    writer.write_header(
        StockHeader(trading_date="2026-08-27", risk_free_rate=0.05, stocks=refs)
    )
    writer.close()
    assert read_header_axis(path, AXIS_STOCK) == 7


def test_a_generation_that_writes_no_frame_leaves_no_header(tmp_path):
    """A header must be earned by data — it is what fixes the file's axis.

    On 2026-08-26/27 the header was written by a generation that produced only five
    all-zero frames, and the whole day was then judged against it.
    """
    path = tmp_path / "2026-08-27.bin"
    writer = IndexBinWriter(path)
    writer.open()
    writer.bind_header(_header(97))
    writer.close()

    assert not path.exists(), "no frames means no file, not a header-only shell"
