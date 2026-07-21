"""Tests for the capture engine, writer threads, and reconnect/stall policy."""

from __future__ import annotations

from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.bin_codec.writer import IndexBinWriter
from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.capture.reconnect import ReconnectPolicy, StallDetector
from app.capture.writer_thread import FileWriterThread
from app.chain.assembler import build_option_chain
from app.chain.config import VIX_TOKEN, get_index_config
from app.chain.table import IndexTable
from app.stocks.board import build_board
from app.stocks.matrix import StockMatrix
from tests.test_board import _sample_instruments
from tests.test_chain import _make_options
from tests.test_table_matrix import _full_tick

# --- reconnect policy / stall detector ---------------------------------------


def test_reconnect_backoff_and_circuit_breaker():
    policy = ReconnectPolicy(base_delay_s=5.0, max_delay_s=300.0, max_attempts=20)
    delays = [policy.next_delay() for _ in range(8)]
    assert delays[:4] == [5.0, 10.0, 20.0, 40.0]
    assert delays[-1] == 300.0  # capped
    assert not policy.should_give_up()
    for _ in range(20):
        policy.next_delay()
    assert policy.should_give_up()
    policy.reset()
    assert policy.attempt == 0 and not policy.should_give_up()


def test_stall_detector():
    d = StallDetector(timeout_ms=30_000)
    assert d.is_stalled(1_000) is False  # no message yet
    d.mark_message(1_000)
    assert d.is_stalled(1_000 + 29_999) is False
    assert d.is_stalled(1_000 + 30_000) is True


# --- writer thread integration -----------------------------------------------


def _nifty_table():
    options = _make_options("NIFTY", "2026-07-31", [24500, 24550, 24600])
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24550.0, expiry="2026-07-31"
    )
    return IndexTable(chain, 0.0691, "2026-07-21")


def test_file_writer_thread_writes_frames(tmp_path):
    table = _nifty_table()
    path = tmp_path / "NIFTY" / "2026-07-21.bin"
    wt = FileWriterThread(IndexBinWriter(path), table.header())
    wt.start()
    wt.wait_until_ready()
    wt.enqueue(table.snapshot(1000))
    wt.enqueue(table.snapshot(2000))
    wt.stop(join=True)
    assert wt.frames_written == 2

    with IndexBinReader(path) as r:
        assert len(r) == 2
        assert [f.timestamp_unix_ms for f in r.frames()] == [1000, 2000]


# --- engine routing ----------------------------------------------------------


def test_engine_vix_fans_out_to_all_indices():
    nifty = _nifty_table()
    bn_options = _make_options("BANKNIFTY", "2026-07-31", [51800, 51900, 52000, 52100])
    bn_chain = build_option_chain(
        bn_options, get_index_config("BANKNIFTY"), spot=52000.0, expiry="2026-07-31"
    )
    banknifty = IndexTable(bn_chain, 0.0691, "2026-07-21")

    engine = CaptureEngine(
        index_tables={"NIFTY": nifty, "BANKNIFTY": banknifty},
        stock_matrix=None,
        index_writers={},
        stock_writer=None,
    )
    applied = engine.apply_ticks([_full_tick(VIX_TOKEN, 12.34)])
    assert applied == 2  # one VIX tick updated both index tables
    assert nifty.vix == 1234
    assert banknifty.vix == 1234


def test_engine_unmatched_counter():
    engine = CaptureEngine({"NIFTY": _nifty_table()}, None, {}, None)
    engine.apply_ticks([_full_tick(999999, 1.0)])
    assert engine.unmatched == 1


# --- engine end-to-end -------------------------------------------------------


def test_engine_capture_once_grows_files(tmp_path):
    table = _nifty_table()
    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-21")

    idx_path = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"
    stk_path = tmp_path / "STOCKS" / "2026-07-21.bin"
    index_writers = {"NIFTY": build_index_writer(table, idx_path)}
    stock_writer = build_stock_writer(matrix, stk_path)

    engine = CaptureEngine(
        index_tables={"NIFTY": table},
        stock_matrix=matrix,
        index_writers=index_writers,
        stock_writer=stock_writer,
    )
    engine.start_writers()
    try:
        # apply some ticks then snapshot twice (two "seconds")
        engine.apply_ticks([_full_tick(int(table.chain.call_tokens[0]), 100.0, oi=500)])
        engine.apply_ticks([_full_tick(519937, 2950.5, oi=1000)])  # M&M spot
        assert engine.capture_once(1_000) == 2  # index + stocks
        assert engine.capture_once(2_000) == 2
    finally:
        engine.stop_writers()

    with IndexBinReader(idx_path) as r:
        assert len(r) == 2
        assert r.frame(0).calls.columns["ltp"][0] == 10000
    with StockBinReader(stk_path) as r:
        assert len(r) == 2
        assert r.frame(0).spot.scalars["ltp"][0] == 295050
        assert len(r.frame(0).spot.depth) == 5  # L5 stocks
