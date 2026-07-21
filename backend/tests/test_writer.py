"""Writer tests: byte-level header layout + header-once semantics."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from app.bin_codec import writer
from app.bin_codec.layout import (
    SCHEMA_VERSION,
    TAG_HEADER,
    Cursor,
    FutureRef,
    IndexFrame,
    IndexHeader,
    RawBlock,
    StockHeader,
    StockRef,
)


def _sample_index_header() -> IndexHeader:
    strikes = np.array([2_450_000, 2_455_000, 2_460_000], dtype="<i8")
    return IndexHeader(
        trading_date="2026-07-21",
        underlying="NIFTY",
        expiry_date="2026-07-24",
        risk_free_rate=0.0691,
        strikes=strikes,
    )


def test_index_header_byte_level_layout():
    header = _sample_index_header()
    payload = writer.encode_index_header(header)

    # Parse the payload back by hand and assert exact field values / order.
    cur = Cursor(payload)
    assert cur.u32() == TAG_HEADER
    assert cur.u32() == SCHEMA_VERSION
    assert cur.string() == "2026-07-21"
    assert cur.string() == "NIFTY"
    assert cur.string() == "2026-07-24"
    assert cur.f64() == pytest.approx(0.0691)
    strikes = cur.vec(np.dtype("<i8"))
    np.testing.assert_array_equal(strikes, header.strikes)
    assert cur.pos == len(payload)

    # First bytes are the tag as a little-endian u32 == 0.
    assert struct.unpack_from("<I", payload, 0)[0] == 0


def test_stock_header_byte_level_layout():
    header = StockHeader(
        trading_date="2026-07-21",
        risk_free_rate=0.0691,
        stocks=[
            StockRef(
                tradingsymbol="M&M",
                name="M&M",
                spot_token=519937,
                lot_size=700,
                futures=[
                    FutureRef(token=111, expiry="2026-07-31", lot_size=700),
                    FutureRef(token=222, expiry="2026-08-28", lot_size=700),
                ],
            ),
        ],
    )
    payload = writer.encode_stock_header(header)
    cur = Cursor(payload)
    assert cur.u32() == TAG_HEADER
    assert cur.u32() == SCHEMA_VERSION
    assert cur.string() == "2026-07-21"
    assert cur.f64() == pytest.approx(0.0691)
    assert cur.u64() == 1  # one stock
    assert cur.string() == "M&M"
    assert cur.string() == "M&M"
    assert cur.u64() == 519937
    assert cur.u32() == 700
    assert cur.u64() == 2  # two futures
    assert cur.u64() == 111
    assert cur.string() == "2026-07-31"
    assert cur.u32() == 700
    assert cur.u64() == 222
    assert cur.string() == "2026-08-28"
    assert cur.u32() == 700
    assert cur.pos == len(payload)


def test_header_written_once_across_reopen(tmp_path):
    path = tmp_path / "NIFTY" / "2026-07-21.bin"
    header = _sample_index_header()
    n = header.strikes.shape[0]
    frame = IndexFrame(
        timestamp_unix_ms=1_753_070_400_000,
        sequence=0,
        spot_price=2_456_705,
        vix=1_234,
        calls=RawBlock.zeros(n),
        puts=RawBlock.zeros(n),
    )

    with writer.IndexBinWriter(path) as w:
        assert w.write_header(header) is True  # fresh file -> header written
        w.append_frame(frame)
    size_after_first = path.stat().st_size

    # Reopen (simulating a mid-day restart) -> header must NOT be rewritten.
    with writer.IndexBinWriter(path) as w:
        assert w.write_header(header) is False  # idempotent
        frame.sequence = 1
        w.append_frame(frame)
    size_after_second = path.stat().st_size

    # File grew by exactly one data frame, not a second header.
    header_frame_len = 4 + len(writer.encode_index_header(header))
    data_frame_len = 4 + len(writer.encode_index_frame(frame, n))
    assert size_after_first == header_frame_len + data_frame_len
    assert size_after_second == size_after_first + data_frame_len


def test_append_before_header_raises(tmp_path):
    path = tmp_path / "2026-07-21.bin"
    header = _sample_index_header()
    n = header.strikes.shape[0]
    frame = IndexFrame(0, 0, 0, 0, RawBlock.zeros(n), RawBlock.zeros(n))
    with writer.IndexBinWriter(path) as w:
        with pytest.raises(RuntimeError):
            w.append_frame(frame)
