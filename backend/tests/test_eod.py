"""Tests for the EOD compression sweep + stale-raw prune."""

from __future__ import annotations

import numpy as np

from app.bin_codec import writer
from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.bin_codec.reader import IndexBinReader
from app.ops.eod import compress_raw_files, prune_stale_raw, run_eod


def _write_index_file(path, n_frames=5):
    strikes = np.array([2_450_000, 2_455_000, 2_460_000], dtype="<i8")
    header = IndexHeader("2026-07-21", "NIFTY", "2026-07-24", 0.0691, strikes)
    n = strikes.shape[0]
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for i in range(n_frames):
            w.append_frame(
                IndexFrame(1_753_070_400_000 + i * 1000, i, 2_456_705, 1234,
                           RawBlock.zeros(n), RawBlock.zeros(n))
            )


def _make_market_data(tmp_path):
    idx = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"
    stk = tmp_path / "STOCKS" / "2026-07-21.bin"
    _write_index_file(idx)
    _write_index_file(stk)  # shape doesn't matter for the sweep test
    # non-.bin artifacts that must be left untouched
    inst = tmp_path / "_instruments" / "2026-07-21" / "NFO.csv"
    inst.parent.mkdir(parents=True)
    inst.write_text("instrument_token,tradingsymbol\n1,X\n")
    state = tmp_path / "_state" / "session-2026-07-21.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}")
    return idx, stk, inst, state


def test_compress_raw_files_verifies_and_removes(tmp_path):
    idx, stk, inst, state = _make_market_data(tmp_path)
    result = compress_raw_files(tmp_path)

    assert len(result.compressed) == 2
    assert all(p.suffix == ".zst" for p in result.compressed)
    assert not idx.exists() and not stk.exists()  # raw removed after verify
    assert idx.with_name("2026-07-21.bin.zst").exists()
    # non-.bin artifacts untouched
    assert inst.exists() and state.exists()
    assert result.total_raw_bytes > 0 and result.total_zst_bytes > 0

    # re-index the compressed index file transparently
    with IndexBinReader(idx.with_name("2026-07-21.bin.zst")) as r:
        assert len(r) == 5


def test_run_eod_stops_capture_then_compresses(tmp_path):
    idx, *_ = _make_market_data(tmp_path)
    stopped = {"called": False}

    def stop():
        stopped["called"] = True

    result = run_eod(stop, tmp_path)
    assert stopped["called"] is True
    assert len(result.compressed) == 2
    assert result.ratio > 0


def test_prune_stale_raw(tmp_path):
    _make_market_data(tmp_path)
    result = prune_stale_raw(tmp_path)
    assert len(result.compressed) == 2
    # idempotent: a second run finds no raw files
    again = prune_stale_raw(tmp_path)
    assert again.compressed == []
