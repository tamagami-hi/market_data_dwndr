"""Tests for the capture engine, writer threads, and reconnect/stall policy."""

from __future__ import annotations

import asyncio

from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.bin_codec.writer import IndexBinWriter
from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.capture.reconnect import FreshnessMonitor, ReconnectPolicy, StallDetector
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


# --- freshness monitor (content-level staleness) -----------------------------


def _tick(token=1, price=100.0, **extra):
    return {"instrument_token": token, "last_price": price, **extra}


def test_freshness_flags_a_frozen_feed():
    """Ticks keep arriving but with identical values -> content goes stale."""
    fm = FreshnessMonitor(stale_after_ms=5_000, start_ms=0)
    fm.observe([_tick(price=100.0)], now_ms=0)
    assert fm.is_stale(0) is False
    # Same content re-delivered every second — the "connected but frozen" case.
    fm.observe([_tick(price=100.0)], now_ms=1_000)
    fm.observe([_tick(price=100.0)], now_ms=2_000)
    assert fm.frozen_batches == 2
    assert fm.is_stale(4_999) is False
    assert fm.is_stale(5_000) is True  # 5s since content last *changed*
    # A genuinely new value clears it.
    fm.observe([_tick(price=100.5)], now_ms=6_000)
    assert fm.is_stale(6_000) is False
    assert fm.content_age_ms(6_000) == 0


def test_freshness_flags_a_total_tick_outage():
    """No batches at all after the first -> content age still grows (half-open socket)."""
    fm = FreshnessMonitor(stale_after_ms=5_000, start_ms=0)
    fm.observe([_tick(price=100.0)], now_ms=1_000)
    assert fm.is_stale(5_999) is False
    assert fm.is_stale(6_000) is True  # 5s since the last (and only) change at t=1000


def test_freshness_before_first_tick_measures_from_start():
    fm = FreshnessMonitor(stale_after_ms=5_000, start_ms=0)
    assert fm.is_stale(4_999) is False
    assert fm.is_stale(5_000) is True  # a dead connection from the very start is caught


def test_freshness_exchange_timestamp_advance_counts_as_fresh():
    """A moving exchange timestamp proves the feed is live even if the price repeats."""
    fm = FreshnessMonitor(stale_after_ms=5_000, start_ms=0)
    fm.observe([_tick(price=100.0, exchange_timestamp="09:15:00")], now_ms=0)
    fm.observe([_tick(price=100.0, exchange_timestamp="09:15:01")], now_ms=1_000)
    assert fm.frozen_batches == 0
    assert fm.is_stale(5_000) is False


# --- self-driven reconnect on stall ------------------------------------------


class _FakeBridge:
    def __init__(self):
        self.reconnects = 0

    def reconnect(self):
        self.reconnects += 1


def test_engine_maybe_reconnect_fires_then_backs_off():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    bridge = _FakeBridge()

    assert engine._maybe_reconnect(bridge, 0) is False  # fresh
    assert engine.degraded is False

    # Threshold crossed: first reconnect fires immediately.
    assert engine._maybe_reconnect(bridge, 5_000) is True
    assert engine.degraded is True
    assert bridge.reconnects == 1

    # Inside the 5s backoff window: no repeat.
    assert engine._maybe_reconnect(bridge, 7_000) is False
    assert bridge.reconnects == 1

    # Backoff elapsed and still stale: reconnect again.
    assert engine._maybe_reconnect(bridge, 10_000) is True
    assert bridge.reconnects == 2


def test_engine_maybe_reconnect_recovers_and_resets():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    bridge = _FakeBridge()

    engine._maybe_reconnect(bridge, 5_000)
    assert engine.degraded is True
    assert engine.reconnect_policy.attempt == 1

    # Fresh data resumes.
    engine.freshness.observe([_tick(price=1.0)], now_ms=6_000)
    assert engine._maybe_reconnect(bridge, 6_000) is False
    assert engine.degraded is False
    assert engine.reconnect_policy.attempt == 0  # backoff reset on recovery


def test_engine_maybe_reconnect_gives_up_after_circuit_breaker():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    bridge = _FakeBridge()
    # Exhaust the circuit breaker directly, then confirm we stop reconnecting.
    engine.reconnect_policy.attempt = engine.reconnect_policy.max_attempts
    assert engine._maybe_reconnect(bridge, 100_000) is False
    assert engine.degraded is True  # still flagged degraded for the frontend
    assert bridge.reconnects == 0


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


async def test_live_loop_never_awaits_frontend_publishing():
    """Saving cadence continues even while a display publish is still pending."""

    class IdleBridge:
        async def batches(self):
            while True:
                await asyncio.sleep(1)
                yield []

    class BestEffortBroadcaster:
        def __init__(self):
            self.timestamps: list[int] = []

        def publish_latest(self, snapshot) -> None:
            self.timestamps.append(snapshot.timestamp_unix_ms)

    engine = CaptureEngine({}, None, {}, None)  # real wall-clock (grid-driven scheduler)
    stop_event = asyncio.Event()
    broadcaster = BestEffortBroadcaster()

    async def stop_after_several_intervals() -> None:
        await asyncio.sleep(0.08)
        stop_event.set()

    stopper = asyncio.create_task(stop_after_several_intervals())
    await asyncio.wait_for(
        engine.run(IdleBridge(), stop_event, interval_s=0.01, broadcaster=broadcaster),
        timeout=1.0,
    )
    await stopper

    # The 10 ms grid over ~80 ms yields several frames; every snapshot is published
    # (never blocked by the broadcaster), and timestamps are monotonic + grid-aligned.
    assert engine.captures >= 3
    assert len(broadcaster.timestamps) == engine.captures
    assert broadcaster.timestamps == sorted(broadcaster.timestamps)
    assert all(ts % 10 == 0 for ts in broadcaster.timestamps)


# --- drift-free, no-skip 1 Hz scheduler --------------------------------------


def test_due_ticks_on_time_emits_exactly_one():
    due, nxt, stalled = CaptureEngine._due_ticks(1000, 1005, 1000, 60)
    assert due == [1000]
    assert nxt == 2000
    assert stalled is False


def test_due_ticks_not_due_yet():
    due, nxt, stalled = CaptureEngine._due_ticks(2000, 1500, 1000, 60)
    assert due == [] and nxt == 2000 and stalled is False


def test_due_ticks_catches_up_without_skipping_any_second():
    # Fell behind ~3 intervals → every missed grid second is emitted (no gaps).
    due, nxt, stalled = CaptureEngine._due_ticks(1000, 3100, 1000, 60)
    assert due == [1000, 2000, 3000]
    assert nxt == 4000
    assert stalled is False


def test_due_ticks_stall_is_bounded_then_resyncs():
    # Pathological jump (e.g. clock skew): fill up to the cap, then resync + flag.
    due, nxt, stalled = CaptureEngine._due_ticks(1000, 100_000, 1000, 5)
    assert due == [1000, 2000, 3000, 4000, 5000]  # bounded — no runaway burst
    assert stalled is True
    assert nxt == 101_000  # grid resynced to the boundary just after now


def test_index_writer_sync_roundtrips(tmp_path):
    """fsync-per-frame (live-capture) writer still produces a valid, readable file."""
    import numpy as np

    from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock

    path = tmp_path / "sync.bin"
    strikes = np.array([2_450_000, 2_455_000], dtype="<i8")
    header = IndexHeader("2026-07-24", "NIFTY", "2026-07-31", 0.0691, strikes)
    with IndexBinWriter(path, sync=True) as w:
        w.write_header(header)
        for i in range(3):
            w.append_frame(
                IndexFrame(1_000 + i, i, 100, 1234, RawBlock.zeros(2), RawBlock.zeros(2))
            )
    with IndexBinReader(path) as r:
        assert len(r) == 3
        assert r.frame(0).spot_price == 100
