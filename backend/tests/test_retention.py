"""Tests for retention reporting, integrity checks, and logging config."""

from __future__ import annotations

import logging

import numpy as np

from app.bin_codec import compress, writer
from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.logging_config import configure_logging
from app.ops.retention import scan_storage, verify_integrity


def _write_index_file(path, n_frames=4):
    strikes = np.array([2_450_000, 2_455_000], dtype="<i8")
    header = IndexHeader("2026-07-21", "NIFTY", "2026-07-24", 0.0691, strikes)
    with writer.IndexBinWriter(path) as w:
        w.write_header(header)
        for i in range(n_frames):
            w.append_frame(
                IndexFrame(1000 + i, i, 0, 0, RawBlock.zeros(2), RawBlock.zeros(2))
            )


def test_scan_storage_counts(tmp_path):
    live_root = tmp_path / "live"
    archive_root = tmp_path / "archive"
    idx = live_root / "INDICES" / "NIFTY" / "2026-07-21.bin"
    _write_index_file(idx)
    archived = archive_root / "INDICES" / "NIFTY" / "2026-07-20.bin.zst"
    _write_index_file(tmp_path / "archive-source.bin")
    compress.compress_file(tmp_path / "archive-source.bin", archived)
    (live_root / "_instruments" / "2026-07-21").mkdir(parents=True)
    (live_root / "_instruments" / "2026-07-21" / "NFO.csv").write_text("x\n")
    (live_root / "_state").mkdir()
    (live_root / "_state" / "session-2026-07-21.json").write_text("{}")

    report = scan_storage(live_root, archive_root)
    assert report.raw_bin_files == 1
    assert report.compressed_files == 1
    assert report.instrument_files == 1
    assert report.state_files == 1
    assert report.raw_bytes > 0
    assert report.compressed_bytes > 0


def test_verify_integrity_raw_and_compressed(tmp_path):
    idx = tmp_path / "2026-07-21.bin"
    _write_index_file(idx)
    assert verify_integrity(idx) is True

    zst = compress.compress_file(idx)
    assert verify_integrity(zst) is True  # transparent .zst decode + monotonic ts

    # a non-BIN / garbage file is not intact
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a bin file")
    assert verify_integrity(bad) is False


def test_configure_logging_sets_level():
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    configure_logging("INFO")
    assert logging.getLogger().level == logging.INFO
