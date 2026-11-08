"""Regression tests for the capture-pipeline bugs confirmed in the 2026-11-08 bug hunt."""

from __future__ import annotations

import pytest

from app.bin_codec.layout import frame_bytes
from app.bin_codec.reader import IndexBinReader
from app.bin_codec.scan import complete_records_end, scan_frames
from app.bin_codec.writer import encode_index_frame
from app.capture.engine import CaptureEngine
from app.kite.instruments import CSV_COLUMNS, InstrumentStore
from app.ops import stats_store
from tests.test_bootstrap import _at, _bootstrap_for_recovery
from tests.test_capture import _nifty_table
from tests.test_restart_resume import _write_frames

TICK = [{"instrument_token": 42, "last_price": 101.5}]


# --- torn trailing record --------------------------------------------------------- #


def test_restart_after_a_torn_frame_keeps_every_later_frame_readable(tmp_path):
    path = tmp_path / "NIFTY" / "2026-11-08.bin"
    table = _nifty_table()
    _write_frames(path, table, 4, start_ts=1_000)
    record = frame_bytes(encode_index_frame(table.snapshot(5_000)))
    with open(path, "ab") as handle:
        handle.write(record[: len(record) // 2])  # crash mid-write
    torn_at = complete_records_end(path)

    _write_frames(path, table, 3, start_ts=6_000)  # the restarted process appends

    with IndexBinReader(path) as reader:
        assert reader.timestamps == [1_000, 2_000, 3_000, 4_000, 6_000, 7_000, 8_000]
    assert scan_frames(path).truncated is False
    sidecar = path.with_name(f"{path.name}.torn-{torn_at}")
    assert sidecar.read_bytes() == record[: len(record) // 2]


def test_a_torn_header_is_replaced_by_a_fresh_one(tmp_path):
    path = tmp_path / "NIFTY" / "2026-11-08.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\x00\x00\x00partial-header")  # length says 255, far less follows
    _write_frames(path, _nifty_table(), 2, start_ts=1_000)
    with IndexBinReader(path) as reader:
        assert reader.timestamps == [1_000, 2_000]


def test_complete_records_end_matches_size_for_a_clean_file(tmp_path):
    path = tmp_path / "x.bin"
    _write_frames(path, _nifty_table(), 3, start_ts=1_000)
    assert complete_records_end(path) == path.stat().st_size


# --- instrument archive -------------------------------------------------------------- #

ROWS = [
    "256265,1001,NIFTY 50,NIFTY 50,0,,0,0.05,0,EQ,INDICES,NSE",
    "738561,2885,RELIANCE,RELIANCE,0,,0,0.05,1,EQ,NSE,NSE",
    "519937,2031,M&M,M&M,0,,0,0.05,1,EQ,NSE,NSE",
]
FULL = ",".join(CSV_COLUMNS) + "\n" + "\n".join(ROWS) + "\n"


def test_a_failed_archive_write_leaves_no_partial_archive(tmp_path, monkeypatch):
    import os

    fetches: list[str] = []
    store = InstrumentStore(tmp_path, lambda exchange: fetches.append(exchange) or FULL)
    real_fsync = os.fsync

    def _disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", _disk_full)
    with pytest.raises(OSError):
        store.get("NSE", "2026-11-08")
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert not store.archive_path("NSE", "2026-11-08").exists()
    assert list(store.archive_path("NSE", "2026-11-08").parent.iterdir()) == []
    assert len(store.get("NSE", "2026-11-08")) == 3
    assert fetches == ["NSE", "NSE"]


def test_an_unreadable_archive_is_refetched(tmp_path):
    fetches: list[str] = []
    store = InstrumentStore(tmp_path, lambda exchange: fetches.append(exchange) or FULL)
    path = store.archive_path("NSE", "2026-11-08")
    path.parent.mkdir(parents=True)
    path.write_text(FULL[: len(FULL) - 30])  # cut inside the last row (pre-fix damage)
    assert len(store.get("NSE", "2026-11-08")) == 3
    assert fetches == ["NSE"]
    assert len(store.get("NSE", "2026-11-08")) == 3  # now served from the good archive
    assert fetches == ["NSE"]


# --- recovery arming evidence ---------------------------------------------------------- #


def _engine(capture_expected) -> CaptureEngine:
    return CaptureEngine(
        {},
        None,
        {},
        None,
        stale_after_ms=5_000,
        recovery_armed=lambda _now: False,  # time-based gate still closed
        capture_expected=capture_expected,
    )


def test_a_pre_open_snapshot_is_not_evidence_the_session_is_live():
    open_ms = 300_000
    engine = _engine(lambda now: now >= open_ms)
    engine.freshness.start(0)
    engine.apply_ticks(TICK, 100_000)  # subscribe snapshot during the capture lead
    engine.freshness.observe(TICK, 100_000)
    assert engine.recovery_armed(open_ms + 60_000) is False


def test_a_tick_inside_the_session_is_evidence_by_either_entry_point():
    open_ms = 300_000
    via_apply = _engine(lambda now: now >= open_ms)
    via_apply.apply_ticks(TICK, open_ms + 1_000)
    assert via_apply.recovery_armed(open_ms + 2_000) is True

    via_freshness = _engine(lambda now: now >= open_ms)
    via_freshness.freshness.start(0)
    via_freshness.freshness.observe(TICK, open_ms + 1_000)
    assert via_freshness.recovery_armed(open_ms + 2_000) is True


# --- restart telemetry carry-over ------------------------------------------------------ #


def test_restart_carries_downtime_and_first_tick_wait(tmp_path):
    stats_dir = tmp_path / "_state" / "stats"
    stats_dir.mkdir(parents=True)
    stats_store.write_capture_snapshot(
        stats_dir,
        "2026-08-06",
        {
            "global": {
                "stale_seconds": 40,
                "restart_downtime_seconds": 95,
                "awaiting_first_tick_seconds": 12,
                "first_grid_ms": _at("09:15"),
            }
        },
    )
    engine = _bootstrap_for_recovery(tmp_path, state_dir=tmp_path / "_state").engine
    assert engine.stale_seconds == 40
    assert engine.restart_downtime_seconds >= 95
    assert engine.awaiting_first_tick_seconds == 12
