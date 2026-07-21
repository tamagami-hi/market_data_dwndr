"""Round-trip tests (Phase 1 DoD gate): write -> read -> identical integer arrays."""

from __future__ import annotations

import struct

import numpy as np

from app.bin_codec import writer
from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
)
from app.bin_codec.reader import IndexBinReader, StockBinReader, paise_to_rupees
from tests.conftest import (
    make_index_frame,
    make_index_header,
    make_stock_frame,
    make_stock_header,
)


def _assert_raw_block_identical(got, expected):
    for col in RAW_BLOCK_COLUMNS:
        a, b = got.columns[col.name], expected.columns[col.name]
        assert a.dtype == b.dtype, col.name
        np.testing.assert_array_equal(a, b, err_msg=col.name)


def _assert_instr_identical(got, expected):
    for col in INSTR_SCALAR_COLUMNS:
        a, b = got.scalars[col.name], expected.scalars[col.name]
        assert a.dtype == b.dtype, col.name
        np.testing.assert_array_equal(a, b, err_msg=col.name)
    assert len(got.depth) == len(expected.depth)
    for lvl, (g, e) in enumerate(zip(got.depth, expected.depth, strict=True)):
        for col in DEPTH_LEVEL_COLUMNS:
            assert g[col.name].dtype == e[col.name].dtype, (lvl, col.name)
            np.testing.assert_array_equal(g[col.name], e[col.name], err_msg=f"{lvl}:{col.name}")


def test_index_round_trip_identical(tmp_path):
    rng = np.random.default_rng(42)
    header = make_index_header(n_strikes=7)
    n = header.strikes.shape[0]
    frames = [make_index_frame(rng, n, ts=1_753_070_400_000 + i * 1000, seq=i) for i in range(5)]

    path = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)

    with IndexBinReader(path) as r:
        h = r.header()
        assert h.trading_date == "2026-07-21"
        assert h.underlying == "NIFTY"
        assert h.expiry_date == "2026-07-24"
        assert h.risk_free_rate == header.risk_free_rate
        np.testing.assert_array_equal(h.strikes, header.strikes)
        assert h.strikes.dtype == np.dtype("<i8")
        assert len(r) == len(frames)
        for i, expected in enumerate(frames):
            got = r.frame(i)
            assert got.timestamp_unix_ms == expected.timestamp_unix_ms
            assert got.sequence == expected.sequence
            assert got.spot_price == expected.spot_price
            assert got.vix == expected.vix
            _assert_raw_block_identical(got.calls, expected.calls)
            _assert_raw_block_identical(got.puts, expected.puts)


def test_stock_round_trip_identical(tmp_path):
    rng = np.random.default_rng(7)
    header = make_stock_header()
    n = len(header.stocks)
    frames = [make_stock_frame(rng, n, ts=1_753_070_400_000 + i * 1000, seq=i) for i in range(4)]

    path = tmp_path / "STOCKS" / "2026-07-21.bin"
    with writer.StockBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)

    with StockBinReader(path) as r:
        h = r.header()
        assert h.trading_date == "2026-07-21"
        assert h.risk_free_rate == header.risk_free_rate
        assert [s.tradingsymbol for s in h.stocks] == ["RELIANCE", "M&M"]
        # Missing-futures validity comes from StockRef.futures length.
        assert len(h.stocks[0].futures) == 3
        assert len(h.stocks[1].futures) == 1
        assert h.stocks[1].futures[0].expiry == "2026-07-31"
        assert len(r) == len(frames)
        for i, expected in enumerate(frames):
            got = r.frame(i)
            assert got.timestamp_unix_ms == expected.timestamp_unix_ms
            assert got.sequence == expected.sequence
            for leg_got, leg_exp in zip(got.legs(), expected.legs(), strict=True):
                _assert_instr_identical(leg_got, leg_exp)


def test_nearest_timestamp_and_range(tmp_path):
    rng = np.random.default_rng(1)
    header = make_index_header(n_strikes=3)
    n = header.strikes.shape[0]
    base = 1_753_070_400_000
    frames = [make_index_frame(rng, n, ts=base + i * 1000, seq=i) for i in range(10)]

    path = tmp_path / "2026-07-21.bin"
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)

    with IndexBinReader(path) as r:
        # nearest: 1499ms past base -> frame 1 (base+1000) is closer than frame 2.
        assert r.nearest_index(base + 1499) == 1
        assert r.nearest_index(base + 1500) in (1, 2)  # exact midpoint tie
        assert r.nearest_index(base + 1501) == 2
        assert r.frame_at(base + 3000).sequence == 3
        # range inclusive on both ends
        idxs = r.indices_in_range(base + 2000, base + 4000)
        assert idxs == [2, 3, 4]
        assert [f.sequence for f in r.frames_in_range(base + 2000, base + 4000)] == [2, 3, 4]


def test_truncated_trailing_frame_is_ignored(tmp_path):
    rng = np.random.default_rng(3)
    header = make_index_header(n_strikes=3)
    n = header.strikes.shape[0]
    frames = [make_index_frame(rng, n, ts=1000 + i, seq=i) for i in range(3)]

    path = tmp_path / "2026-07-21.bin"
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)

    # Simulate a crash mid-write: a length prefix claiming more bytes than present.
    with open(path, "ab") as fh:
        fh.write(struct.pack("<I", 9999))
        fh.write(b"\x01\x00\x00\x00partial")

    with IndexBinReader(path) as r:
        assert len(r) == 3  # trailing partial ignored
        assert [f.sequence for f in r.frames()] == [0, 1, 2]


def test_paise_to_rupees_helper():
    arr = np.array([2_456_705, -100, 0], dtype="<i8")
    out = paise_to_rupees(arr)
    assert out.dtype == np.float64
    np.testing.assert_allclose(out, [24567.05, -1.0, 0.0])
