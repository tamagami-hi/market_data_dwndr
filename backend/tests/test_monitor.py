"""Tests for Capture Monitor metrics."""

from __future__ import annotations

from types import SimpleNamespace

from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.capture.monitor import (
    CaptureMonitor,
    avg_bytes_per_frame,
    directory_bytes,
    disk_usage,
    drop_rate_pct,
    frame_loss_pct,
    projected_eod_bytes,
)
from app.chain.assembler import build_option_chain
from app.chain.config import get_index_config
from app.chain.table import IndexTable
from app.stocks.board import build_board
from app.stocks.matrix import StockMatrix
from tests.test_board import _sample_instruments
from tests.test_chain import _make_options
from tests.test_table_matrix import _full_tick


def _nifty_table():
    options = _make_options("NIFTY", "2026-07-31", [24500, 24550, 24600])
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24550.0, expiry="2026-07-31"
    )
    return IndexTable(chain, 0.0691, "2026-07-21")


def test_directory_bytes(tmp_path):
    assert directory_bytes(tmp_path / "missing") == 0
    (tmp_path / "a.bin").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"678")
    assert directory_bytes(tmp_path) == 8


def test_disk_usage_reports_free_and_total(tmp_path):
    free, total = disk_usage(tmp_path)
    assert total > 0
    assert 0 <= free <= total


def test_disk_usage_walks_up_to_existing_ancestor(tmp_path):
    # A not-yet-created child still resolves to the mounted volume via its parent.
    free, total = disk_usage(tmp_path / "does" / "not" / "exist")
    assert total > 0
    assert 0 <= free <= total


def test_disk_usage_none_is_zero():
    assert disk_usage(None) == (0, 0)


def test_frame_loss_pct():
    assert frame_loss_pct(0, 23_400) == 100.0
    assert frame_loss_pct(23_400, 23_400) == 0.0
    assert frame_loss_pct(11_700, 23_400) == 50.0
    # Over-capture clamps to 0, never negative.
    assert frame_loss_pct(30_000, 23_400) == 0.0
    # Zero baseline is a safe 0 (no divide-by-zero).
    assert frame_loss_pct(0, 0) == 0.0


def test_drop_rate_pct():
    assert drop_rate_pct(0, 0) == 0.0
    assert drop_rate_pct(0, 100) == 0.0
    assert drop_rate_pct(1, 3) == 25.0  # 1 / (3 + 1)


def test_avg_bytes_per_frame_and_projection():
    assert avg_bytes_per_frame(0, 0) == 0.0
    assert avg_bytes_per_frame(1000, 10) == 100.0
    assert projected_eod_bytes(0, 0, 23_400) == 0
    # 100 B/frame * 23,400 expected = 2,340,000
    assert projected_eod_bytes(1000, 10, 23_400) == 2_340_000


def _fps_monitor():
    """A CaptureMonitor wired to a fake clock + engine for isolated fps tests."""
    clock = {"t": 0}
    engine = SimpleNamespace(captures=0)
    monitor = CaptureMonitor(
        {}, None, {}, None, engine=engine, clock=lambda: clock["t"], fps_window_ms=5_000
    )
    return monitor, engine, clock


def test_fps_is_steady_at_1hz():
    monitor, engine, clock = _fps_monitor()
    fps = 0.0
    for second in range(1, 8):  # 1 capture per second
        clock["t"] = second * 1000
        engine.captures = second
        fps = monitor._fps()
    assert abs(fps - 1.0) < 0.15  # steady ~1 Hz


def test_fps_does_not_spike_on_rapid_extra_snapshot_calls():
    # Reproduces the bug: a capture lands, then a second (REST-poll) snapshot fires
    # a few ms later. The OLD per-call delta/elapsed math returned ~1/0.01 = 100.
    monitor, engine, clock = _fps_monitor()
    for second in range(1, 7):  # warm up the window at 1 Hz
        clock["t"] = second * 1000
        engine.captures = second
        monitor._fps()
    clock["t"] = 6010  # a capture lands 10 ms after the last sample
    engine.captures = 7
    clock["t"] = 6020  # a rapid extra caller (e.g. /api/stats poll) 10 ms later
    spike = monitor._fps()
    assert spike < 2.0, f"fps spiked to {spike} on a rapid extra call"


def test_fps_reflects_a_real_stall():
    # If captures stop, the windowed rate decays toward 0 (real signal, not noise).
    monitor, engine, clock = _fps_monitor()
    for second in range(1, 7):
        clock["t"] = second * 1000
        engine.captures = second
        monitor._fps()
    # 6s later with no new captures, the window sees 0 new frames.
    clock["t"] = 12_000
    assert monitor._fps() == 0.0


def test_monitor_snapshot_end_to_end(tmp_path):
    table = _nifty_table()
    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-21")

    idx_path = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"
    stk_path = tmp_path / "STOCKS" / "2026-07-21.bin"
    index_writers = {"NIFTY": build_index_writer(table, idx_path)}
    stock_writer = build_stock_writer(matrix, stk_path)

    clock = {"t": 1_000_000}

    engine = CaptureEngine(
        {"NIFTY": table}, matrix, index_writers, stock_writer, clock=lambda: clock["t"]
    )
    engine.start_writers()
    try:
        engine.apply_ticks([_full_tick(999999, 1.0)])  # unmatched
        engine.stall.mark_message(clock["t"])
        engine.capture_once(clock["t"])
        # let the writer thread flush
        import time

        for _ in range(50):
            if index_writers["NIFTY"].frames_written >= 1 and stock_writer.frames_written >= 1:
                break
            time.sleep(0.01)

        monitor = CaptureMonitor(
            {"NIFTY": table},
            matrix,
            index_writers,
            stock_writer,
            engine=engine,
            market_data_path=tmp_path,
            clock=lambda: clock["t"],
            expected_frames=23_400,
            capture_start_ms=clock["t"] - 60_000,  # started 60s ago
        )
        snap = monitor.snapshot()
        assert snap["type"] == "CaptureStatus"
        per = {e["underlying"]: e for e in snap["payload"]["per_underlying"]}
        assert set(per) == {"NIFTY", "STOCKS"}
        assert per["NIFTY"]["frames_written"] == 1
        assert per["NIFTY"]["file_bytes"] > 0
        assert per["NIFTY"]["heartbeat_ok"] is True  # written at clock t, window 2s
        # New per-underlying metrics.
        assert per["NIFTY"]["frames_expected"] == 23_400
        assert per["NIFTY"]["frame_loss_pct"] > 99.9  # only 1 of 23,400 frames so far
        assert per["NIFTY"]["avg_bytes_per_frame"] == per["NIFTY"]["file_bytes"]  # 1 frame
        assert per["NIFTY"]["projected_eod_bytes"] > per["NIFTY"]["file_bytes"]
        assert per["NIFTY"]["heartbeat_age_ms"] is not None  # writer wrote a frame
        # Unknown tokens are counted globally by the engine, not per-underlying.
        assert per["STOCKS"]["unmatched"] == 0
        assert engine.unmatched == 1

        g = snap["payload"]["global"]
        assert g["tokens"] > 0
        assert g["disk_bytes"] > 0
        assert g["captures"] == 1
        # New global metrics.
        assert g["uptime_ms"] == 60_000
        assert g["disk_total_bytes"] > 0
        assert 0 <= g["disk_free_bytes"] <= g["disk_total_bytes"]
        assert g["drop_rate_pct"] == 0.0
        assert g["frames_expected"] == 23_400 * 2  # NIFTY + STOCKS
        assert g["frames_written"] == 2  # 1 each
        assert 0 <= g["frame_loss_pct"] <= 100
    finally:
        engine.stop_writers()


def test_monitor_heartbeat_goes_stale(tmp_path):
    table = _nifty_table()
    idx_path = tmp_path / "NIFTY.bin"
    writer = build_index_writer(table, idx_path)
    writer.start()
    writer.wait_until_ready()
    writer.enqueue(table.snapshot(1000))
    import time

    for _ in range(50):
        if writer.frames_written >= 1:
            break
        time.sleep(0.01)
    writer.stop()

    now = (writer.last_write_ms or 0) + 5_000  # 5s later -> stale
    monitor = CaptureMonitor(
        {"NIFTY": table}, None, {"NIFTY": writer}, None, clock=lambda: now
    )
    entry = monitor.per_underlying()[0]
    assert entry["heartbeat_ok"] is False
    assert entry["frames_written"] == 1
