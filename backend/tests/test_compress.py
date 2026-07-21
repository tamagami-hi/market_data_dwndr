"""Compression tests (Phase 1 DoD gate): compress -> re-index -> identical."""

from __future__ import annotations

import numpy as np

from app.bin_codec import compress, writer
from app.bin_codec.reader import IndexBinReader, StockBinReader
from tests.conftest import (
    make_index_frame,
    make_index_header,
    make_stock_frame,
    make_stock_header,
)


def _write_index_file(path, n_frames=6):
    rng = np.random.default_rng(11)
    header = make_index_header(n_strikes=5)
    n = header.strikes.shape[0]
    base = 1_753_070_400_000
    frames = [make_index_frame(rng, n, ts=base + i * 1000, seq=i) for i in range(n_frames)]
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)
    return header, frames


def test_compressed_path_appends_suffix(tmp_path):
    p = tmp_path / "2026-07-21.bin"
    assert compress.compressed_path(p).name == "2026-07-21.bin.zst"


def test_decompress_reproduces_raw_bytes(tmp_path):
    path = tmp_path / "2026-07-21.bin"
    _write_index_file(path)
    zst = compress.compress_file(path)
    assert zst.name == "2026-07-21.bin.zst"
    with open(path, "rb") as f:
        raw = f.read()
    assert compress.decompress_to_bytes(zst) == raw
    assert compress.verify_roundtrip(path, zst) is True


def test_index_reindex_after_compression_identical(tmp_path):
    path = tmp_path / "2026-07-21.bin"
    header, frames = _write_index_file(path)
    zst = compress.compress_file(path)

    # Re-index the compressed file transparently and compare to the originals.
    with IndexBinReader(zst) as r:
        assert len(r) == len(frames)
        assert r.timestamps == [f.timestamp_unix_ms for f in frames]
        h = r.header()
        np.testing.assert_array_equal(h.strikes, header.strikes)
        for i, expected in enumerate(frames):
            got = r.frame(i)
            assert got.timestamp_unix_ms == expected.timestamp_unix_ms
            assert got.spot_price == expected.spot_price
            np.testing.assert_array_equal(
                got.calls.columns["ltp"], expected.calls.columns["ltp"]
            )
            np.testing.assert_array_equal(
                got.puts.columns["oi"], expected.puts.columns["oi"]
            )


def test_stock_reindex_after_compression_identical(tmp_path):
    rng = np.random.default_rng(5)
    header = make_stock_header()
    n = len(header.stocks)
    frames = [make_stock_frame(rng, n, ts=2000 + i * 1000, seq=i) for i in range(3)]
    path = tmp_path / "2026-07-21.bin"
    with writer.StockBinWriter(path) as w:
        w.write_header(header)
        for f in frames:
            w.append_frame(f)

    zst = compress.compress_file(path)
    with StockBinReader(zst) as r:
        assert r.timestamps == [f.timestamp_unix_ms for f in frames]
        for i, expected in enumerate(frames):
            got = r.frame(i)
            for leg_got, leg_exp in zip(got.legs(), expected.legs(), strict=True):
                np.testing.assert_array_equal(
                    leg_got.scalars["ltp"], leg_exp.scalars["ltp"]
                )
                np.testing.assert_array_equal(
                    leg_got.depth[4]["ask_orders"], leg_exp.depth[4]["ask_orders"]
                )


def test_remove_src_deletes_raw_after_verify(tmp_path):
    path = tmp_path / "2026-07-21.bin"
    _write_index_file(path)
    zst = compress.compress_file(path, remove_src=True)
    assert zst.exists()
    assert not path.exists()  # raw removed only after verification


def test_compress_directory_sweep(tmp_path):
    (tmp_path / "INDICES" / "NIFTY").mkdir(parents=True)
    p1 = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"
    p2 = tmp_path / "STOCKS" / "2026-07-21.bin"
    p2.parent.mkdir(parents=True)
    _write_index_file(p1)
    _write_index_file(p2)
    outputs = compress.compress_directory(tmp_path, remove_src=True)
    assert len(outputs) == 2
    assert all(o.suffix == ".zst" for o in outputs)
    assert not p1.exists() and not p2.exists()
