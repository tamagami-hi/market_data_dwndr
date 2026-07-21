"""Tests for Capture Monitor metrics."""

from __future__ import annotations

from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.capture.monitor import CaptureMonitor, directory_bytes
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
        )
        snap = monitor.snapshot()
        assert snap["type"] == "CaptureStatus"
        per = {e["underlying"]: e for e in snap["payload"]["per_underlying"]}
        assert set(per) == {"NIFTY", "STOCKS"}
        assert per["NIFTY"]["frames_written"] == 1
        assert per["NIFTY"]["file_bytes"] > 0
        assert per["NIFTY"]["heartbeat_ok"] is True  # written at clock t, window 2s
        # Unknown tokens are counted globally by the engine, not per-underlying.
        assert per["STOCKS"]["unmatched"] == 0
        assert engine.unmatched == 1

        g = snap["payload"]["global"]
        assert g["tokens"] > 0
        assert g["disk_bytes"] > 0
        assert g["captures"] == 1
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
