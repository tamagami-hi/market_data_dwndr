"""Download-history storage scan: every capture area and every segment of a day."""

from __future__ import annotations

from pathlib import Path

from app.ops.retention import scan_capture_history


def _touch(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_history_counts_index_futures_and_later_segments(tmp_path):
    live, archive = tmp_path / "live", tmp_path / "archive"
    day = "2026-11-05"
    _touch(archive / "INDICES" / "NIFTY" / f"{day}.bin.zst", 100)
    _touch(archive / "INDICES" / "NIFTY" / f"{day}.p2.bin.zst", 10)  # restart segment
    _touch(archive / "STOCKS" / f"{day}.bin.zst", 200)
    _touch(archive / "INDICES_FnO" / f"{day}.bin.zst", 30)
    _touch(live / "INDICES_HIS" / "NIFTY" / f"{day}.bin", 999)  # backfill: excluded

    report = scan_capture_history(live, archive)

    (session,) = report.sessions
    assert session.trading_date == day
    assert session.archived_bytes == 340
    assert session.data_files == 4
    assert session.index_files == 2
    assert session.stock_files == 1
    assert session.index_fno_files == 1
    assert session.indices == ("NIFTY",)
    assert report.total_bytes == 340


def test_raw_and_archived_copies_of_one_file_count_once(tmp_path):
    live, archive = tmp_path / "live", tmp_path / "archive"
    _touch(live / "INDICES_FnO" / "2026-11-06.bin", 50)
    _touch(archive / "INDICES_FnO" / "2026-11-06.bin.zst", 20)

    (session,) = scan_capture_history(live, archive).sessions
    assert session.data_files == 1
    assert session.raw_files == 1 and session.archived_files == 1
    assert session.total_bytes == 70
