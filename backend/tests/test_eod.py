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
    archive_root = tmp_path / "archive"
    result = compress_raw_files(tmp_path, archive_root)

    assert len(result.compressed) == 2
    assert all(p.suffix == ".zst" for p in result.compressed)
    assert not idx.exists() and not stk.exists()  # raw removed after verify
    archived_index = archive_root / "INDICES" / "NIFTY" / "2026-07-21.bin.zst"
    assert archived_index.exists()
    # non-.bin artifacts untouched
    assert inst.exists() and state.exists()
    assert result.total_raw_bytes > 0 and result.total_zst_bytes > 0

    # re-index the compressed index file transparently
    with IndexBinReader(archived_index) as r:
        assert len(r) == 5


def test_compression_timing_and_throughput(tmp_path):
    _make_market_data(tmp_path)
    progress: list[dict] = []
    result = compress_raw_files(tmp_path, tmp_path / "archive", progress_cb=progress.append)

    # EODResult timing fields.
    assert result.elapsed_ms >= 0
    assert len(result.file_times_ms) == 2  # two .bin files
    assert result.avg_file_ms >= 0
    # Throughput math: raw MB / seconds.
    if result.elapsed_ms > 0:
        expected = (result.total_raw_bytes / 1e6) / (result.elapsed_ms / 1000.0)
        assert abs(result.throughput_mbps - expected) < 1e-6

    # Progress dicts carry the new timing keys.
    done = [p for p in progress if p["phase"] == "done"]
    assert done, "expected a 'done' progress event"
    final = done[-1]
    for key in ("elapsed_ms", "file_elapsed_ms", "avg_file_ms", "throughput_mbps"):
        assert key in final
    # A per-file 'running' event should report a positive per-file elapsed time.
    running_with_file = [p for p in progress if p["phase"] == "running" and p["file_elapsed_ms"] > 0]
    assert running_with_file, "expected per-file timing in running events"


def test_run_eod_stops_capture_then_compresses(tmp_path):
    idx, *_ = _make_market_data(tmp_path)
    stopped = {"called": False}

    def stop():
        stopped["called"] = True

    result = run_eod(stop, tmp_path, tmp_path / "archive")
    assert stopped["called"] is True
    assert len(result.compressed) == 2
    assert result.ratio > 0


def test_prune_stale_raw(tmp_path):
    _make_market_data(tmp_path)
    archive_root = tmp_path / "archive"
    result = prune_stale_raw(tmp_path, archive_root)
    assert len(result.compressed) == 2
    # idempotent: a second run finds no raw files
    again = prune_stale_raw(tmp_path, archive_root)
    assert again.compressed == []


def test_compression_moves_archive_and_preserves_relative_layout(tmp_path):
    live_root = tmp_path / "ssd-live"
    archive_root = tmp_path / "hdd-archive"
    source = live_root / "INDICES" / "NIFTY" / "2026-07-21.bin"
    _write_index_file(source)

    result = compress_raw_files(live_root, archive_root)

    expected = archive_root / "INDICES" / "NIFTY" / "2026-07-21.bin.zst"
    assert result.compressed == [expected]
    assert expected.exists()
    assert not source.exists()
    assert not source.with_name("2026-07-21.bin.zst").exists()

    with IndexBinReader(expected) as reader:
        assert len(reader) == 5


def test_archive_failure_keeps_raw_and_continues_other_files(tmp_path, monkeypatch):
    live_root = tmp_path / "ssd-live"
    archive_root = tmp_path / "hdd-archive"
    first = live_root / "INDICES" / "NIFTY" / "2026-07-21.bin"
    second = live_root / "STOCKS" / "2026-07-21.bin"
    _write_index_file(first)
    _write_index_file(second)
    original = __import__("app.bin_codec.compress", fromlist=["compress_file"]).compress_file

    def fail_first(src, dst=None, **kwargs):
        if src == first:
            raise OSError("archive disk unavailable")
        return original(src, dst, **kwargs)

    monkeypatch.setattr("app.ops.eod.compress.compress_file", fail_first)

    result = compress_raw_files(live_root, archive_root)

    assert first.exists()
    assert result.compressed == [archive_root / "STOCKS" / "2026-07-21.bin.zst"]
    assert not second.exists()
