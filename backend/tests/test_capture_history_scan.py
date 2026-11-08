"""Download-history storage scan: every capture area and every segment of a day."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_history_api_attaches_each_days_capture_loss(tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.capture import create_capture_router
    from app.ops import stats_store

    live, archive, stats = tmp_path / "live", tmp_path / "archive", tmp_path / "stats"
    _touch(archive / "STOCKS" / "2026-11-05.bin.zst", 10)
    _touch(archive / "STOCKS" / "2026-11-06.bin.zst", 10)
    _touch(archive / "STOCKS" / "2026-11-04.bin.zst", 10)  # no session recorded
    stats_store.record_session_summary(
        stats,
        {"trading_date": "2026-11-05", "scheduled_seconds": 23_100, "captured_seconds": 19_916},
    )
    stats_store.record_session_summary(
        stats,
        {"trading_date": "2026-11-06", "scheduled_seconds": 23_100, "captured_seconds": 23_100},
    )
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        market_data_path=live,
        archive_data_path=archive,
        stats_dir=stats,
        timezone="Asia/Kolkata",
    )
    app.state.session_service = None
    app.include_router(create_capture_router())

    rows = {
        row["trading_date"]: row
        for row in TestClient(app).get("/api/capture/history").json()["sessions"]
    }

    assert rows["2026-11-05"]["loss_pct"] == pytest.approx(13.784, abs=0.01)
    assert rows["2026-11-05"]["outcome"] == "partial"
    assert rows["2026-11-06"]["loss_pct"] == 0.0
    assert rows["2026-11-04"]["loss_pct"] is None
