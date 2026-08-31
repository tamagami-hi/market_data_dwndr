"""The header axis contract, for the domains that still have one.

Stock and index-F&O files fix their **row** axis in the header: it is written once, when
the file is empty, while the in-memory axis is rebuilt on every bootstrap. Appending frames
of a different width behind that header decodes fine (Vec lengths are self-describing) but
maps every column onto the wrong row, so a mismatch must raise and the caller must roll a
segment.

Index option-chain files no longer work this way. Their strike axis lives in each frame
(schema v2), so a changed strike window is a normal in-file event -- see
``tests/test_index_frame_axis.py``. What survives here for the index writer is the rule
that a header must be *earned* by data: on 2026-08-26/27 the header was written by a
generation that produced only five all-zero frames, and the whole day was then judged
against it.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.bin_codec.layout import (
    SCHEMA_VERSION_INDEX,
    FutureRef,
    IndexFrame,
    IndexHeader,
    RawBlock,
    StockFrame,
    StockHeader,
    StockRef,
)
from app.bin_codec.scan import (
    AXIS_INDEX,
    AXIS_STOCK,
    read_header_axis,
    read_header_schema_version,
)
from app.bin_codec.writer import (
    HeaderAxisMismatch,
    IndexBinWriter,
    StockBinWriter,
    resolve_segment_path,
)
from tests.conftest import make_instr_columns


def _stock_refs(n: int) -> list[StockRef]:
    return [
        StockRef(
            tradingsymbol=f"S{i}",
            name=f"S{i}",
            spot_token=i,
            lot_size=100,
            futures=[FutureRef(token=1_000 + i, expiry="2026-09-25", lot_size=100)],
        )
        for i in range(n)
    ]


def _write_stocks(path, n: int, count: int = 2, start_ts: int = 1_000) -> None:
    rng = np.random.default_rng(n)
    writer = StockBinWriter(path)
    writer.open()
    writer.write_header(
        StockHeader(trading_date="2026-08-27", risk_free_rate=0.05, stocks=_stock_refs(n))
    )
    for i in range(count):
        legs = [make_instr_columns(rng, n) for _ in range(4)]
        writer.append_frame(StockFrame(start_ts + i * 1_000, i, *legs))
    writer.close()


def test_a_stock_row_axis_change_is_refused(tmp_path):
    """The stocks matrix is still fixed-width per file: a changed row count must raise."""
    path = tmp_path / "2026-08-27.bin"
    _write_stocks(path, 7)
    assert read_header_axis(path, AXIS_STOCK) == 7

    writer = StockBinWriter(path)
    writer.open()
    try:
        with pytest.raises(HeaderAxisMismatch) as excinfo:
            writer.write_header(
                StockHeader(
                    trading_date="2026-08-27", risk_free_rate=0.05, stocks=_stock_refs(6)
                )
            )
    finally:
        writer.close()
    assert excinfo.value.on_disk == 7
    assert excinfo.value.incoming == 6


def test_a_changed_stock_axis_rolls_into_its_own_segment(tmp_path):
    base = tmp_path / "2026-08-27.bin"
    assert resolve_segment_path(base, 7, AXIS_STOCK) == base
    _write_stocks(base, 7)

    rolled = resolve_segment_path(base, 6, AXIS_STOCK)
    assert rolled.name == "2026-08-27.p2.bin"
    _write_stocks(rolled, 6, start_ts=9_000)

    assert read_header_axis(base, AXIS_STOCK) == 7
    assert read_header_axis(rolled, AXIS_STOCK) == 6
    # An oscillating axis reuses its original segment rather than fanning out files.
    assert resolve_segment_path(base, 7, AXIS_STOCK) == base
    assert resolve_segment_path(base, 6, AXIS_STOCK) == rolled


def test_read_header_axis_ignores_a_missing_or_empty_file(tmp_path):
    assert read_header_axis(tmp_path / "nope.bin", AXIS_STOCK) is None
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert read_header_axis(empty, AXIS_STOCK) is None
    assert read_header_schema_version(empty) is None


def test_a_v2_index_header_declares_no_axis(tmp_path):
    """The index header no longer carries a strike vector, so there is none to read."""
    path = tmp_path / "2026-08-27.bin"
    writer = IndexBinWriter(path)
    writer.open()
    writer.write_header(
        IndexHeader("2026-08-27", "NIFTY", "2026-09-01", 0.052603)
    )
    writer.append_frame(
        IndexFrame(
            1_000, 0, 2_456_705, 1_234,
            RawBlock.zeros(3), np.array([1, 2, 3], dtype="<i8") * 5_000, RawBlock.zeros(3),
        )
    )
    writer.close()

    assert read_header_axis(path, AXIS_INDEX) is None
    assert read_header_schema_version(path) == SCHEMA_VERSION_INDEX


def test_a_generation_that_writes_no_frame_leaves_no_header(tmp_path):
    """A header must be earned by data.

    Preserved from the RC-2 remediation: binding without a tick must not leave a
    header-only shell behind for the EOD sweep to archive.
    """
    path = tmp_path / "2026-08-27.bin"
    writer = IndexBinWriter(path)
    writer.open()
    writer.bind_header(IndexHeader("2026-08-27", "NIFTY", "2026-09-01", 0.052603))
    writer.close()

    assert not path.exists(), "no frames means no file, not a header-only shell"
