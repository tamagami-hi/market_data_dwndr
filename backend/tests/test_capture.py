"""Tests for the capture engine, writer threads, and reconnect/stall policy."""

from __future__ import annotations

import asyncio

import pytest

from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.bin_codec.writer import IndexBinWriter
from app.capture.engine import (
    CaptureEngine,
    CaptureStalledError,
    build_index_writer,
    build_stock_writer,
)
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


# --- restart-first staleness escalation ---------------------------------------
#
# Regression coverage for the 2026-08-04/05/06 sessions, where the feed delivered no
# ticks for 9/72/91 minutes and the process never escalated. Reconstructed from the
# deployment's own artifacts (_state/session-*.invalidated-*.json timestamps and
# _state/stats/capture-<date>.json):
#
#   * the old tiered ladder fired ~27 token refreshes on 08-06, every one of which
#     returned no token (token_refreshes=0, last_token_refresh_ms=None on EVERY day)
#     while destroying that day's persisted session file;
#   * a momentary tick at 10:25:32 reset the backoff cycle, so reconnect_cycles stayed
#     0, `exhausted` stayed False, and the escalate-and-restart safety net never ran;
#   * staleness was declared 5s after capture start at MARKET_OPEN=09:10, before NSE
#     actually trades at 09:15, so the ladder ran at every single open.
#
# The engine now tracks one continuous stale spell that a flicker cannot reset, and
# escalates once — but only while the market is genuinely trading.


class _FakeBridge:
    def __init__(self):
        self.reconnects = 0

    def reconnect(self):
        self.reconnects += 1


def _stale_engine(**kwargs):
    """Engine with recovery armed and a 60s stale deadline, clock-driven."""
    defaults = {
        "stale_after_ms": 5_000,
        "stale_exit_ms": 60_000,
        "recovery_armed": lambda _now: True,
    }
    return CaptureEngine({}, None, {}, None, **{**defaults, **kwargs})


def test_continuous_staleness_escalates_at_the_deadline():
    engine = _stale_engine()
    engine.freshness.start(0)

    # Stale from 5s, but inside the deadline nothing escalates.
    engine.observe_feed_health(5_000)
    assert engine.degraded is True
    assert engine.stale_spell_ms(5_000) == 0
    engine.observe_feed_health(60_000)
    assert engine.escalations == 0

    # 60s of continuous staleness: escalate exactly once, loudly.
    with pytest.raises(CaptureStalledError, match="60s"):
        engine.observe_feed_health(65_000)
    assert engine.escalations == 1


def test_a_momentary_tick_does_not_reset_the_stale_spell():
    """The 08-06 failure: one fresh batch reset the ladder and disarmed escalation."""
    engine = _stale_engine(stale_recovery_confirm_ms=15_000)
    engine.freshness.start(0)
    engine.observe_feed_health(5_000)  # spell starts

    # A single fresh batch 30s in — exactly the 10:25:32 flicker.
    engine.freshness.observe([_tick(price=1.0)], now_ms=35_000)
    engine.observe_feed_health(35_000)
    assert engine.escalations == 0  # currently fresh: never restart a healthy feed

    # It goes stale again immediately; the spell must still be counted from 5s.
    engine.observe_feed_health(41_000)
    assert engine.stale_spell_ms(41_000) == 36_000

    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(66_000)


def test_sustained_recovery_clears_the_spell():
    engine = _stale_engine(stale_recovery_confirm_ms=15_000)
    engine.freshness.start(0)
    engine.observe_feed_health(5_000)

    # Fresh ticks for longer than the confirm window: the spell is genuinely over.
    for ts in range(35_000, 55_000, 1_000):
        engine.freshness.observe([_tick(price=ts / 1000.0)], now_ms=ts)
        engine.observe_feed_health(ts)
    assert engine.stale_spell_ms(54_000) == 0
    assert engine.degraded is False

    # A later spell is measured from its own start, not the earlier one.
    engine.observe_feed_health(60_000)
    assert engine.stale_spell_ms(60_000) == 0


def test_pre_open_staleness_never_escalates():
    """Capture starts at MARKET_OPEN=09:10; NSE trades at 09:15. No ticks is normal."""
    engine = _stale_engine(recovery_armed=lambda _now: False)
    engine.freshness.start(0)

    for ts in range(5_000, 900_000, 5_000):
        engine.observe_feed_health(ts)  # 15 minutes of pre-open silence
    assert engine.escalations == 0
    assert engine.stale_spell_ms(900_000) >= 60_000  # tracked, just not acted on


def test_recovery_is_never_abandoned_while_the_market_is_trading():
    """A spent budget must not stop us fetching: gaps are tolerable, going deaf is not.

    The budget is persisted per trading date and carried across restarts, so the old
    absolute cap meant three escalations before 10:00 left capture online but permanently
    deaf for the rest of the session — the 72- and 91-minute holes on 2026-08-05/06.
    """
    recorded = []
    engine = _stale_engine(
        escalations_before=3,
        escalation_limit=3,
        escalation_recorder=lambda: recorded.append(1) or len(recorded),
    )
    engine.freshness.start(0)
    engine.observe_feed_health(5_000)

    # Past the deadline with the budget already spent: it still restarts.
    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(70_000)
    assert engine.recovery_abandoned is False
    assert engine.exhausted is False
    assert engine.escalations == 4  # counted beyond the budget, not capped at it
    assert recorded == [1]  # and still recorded for the session history


def test_recovery_keeps_escalating_for_a_feed_that_is_down_all_session():
    """Unbounded by design: every deadline breach gets another restart attempt."""
    engine = _stale_engine(escalation_limit=3)
    engine.freshness.start(0)

    for attempt in range(1, 7):
        # Each restart re-bootstraps, so the spell restarts from the new process's clock.
        engine._stale_spell_start_ms = None
        engine._fresh_since_ms = None
        base = attempt * 100_000
        engine.observe_feed_health(base)
        with pytest.raises(CaptureStalledError):
            engine.observe_feed_health(base + 61_000)
        assert engine.escalations == attempt
        assert engine.recovery_abandoned is False


def test_escalation_refreshes_the_token_without_destroying_it():
    """08-06 destroyed 27 session files. Escalation may swap, never delete-and-hope."""
    calls = []
    engine = _stale_engine(token_refresher=lambda: calls.append("refresh"))
    engine.freshness.start(0)
    engine.observe_feed_health(5_000)

    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(66_000)
    assert calls == ["refresh"]


def test_token_refresh_failure_still_escalates():
    def _boom():
        raise RuntimeError("calspread unreachable")

    engine = _stale_engine(token_refresher=_boom)
    engine.freshness.start(0)
    engine.observe_feed_health(5_000)

    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(66_000)


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



# --- session-aware scheduling -------------------------------------------------
#
# Feed health and data loss are only meaningful while a frame is expected. Without this,
# the pre-open silence that begins every trading day is indistinguishable from a dead
# feed — which is exactly how the 2026-08-04/05/06 sessions started.


def test_unscheduled_seconds_are_not_written_and_are_not_loss():
    engine = CaptureEngine({}, None, {}, None, capture_expected=lambda _now: False)
    engine.freshness.start(0)

    snapshot = engine.capture_snapshot(10_000)

    assert snapshot.scheduled is False
    assert snapshot.written is False
    assert engine.unscheduled_seconds == 1
    # None of the loss counters move: this second was never owed.
    assert engine.stale_seconds == 0
    assert engine.captures == 0
    assert engine.first_grid_ms is None  # the elapsed baseline has not started either


def test_a_scheduled_second_still_writes_normally():
    engine = CaptureEngine({}, None, {}, None, capture_expected=lambda _now: True)
    engine.freshness.start(0)
    engine.freshness.observe([_tick(price=1.0)], now_ms=1_000)

    snapshot = engine.capture_snapshot(1_000)

    assert snapshot.scheduled is True
    assert snapshot.written is True
    assert engine.unscheduled_seconds == 0
    assert engine.captures == 1


def test_pre_open_silence_does_not_accumulate_a_stale_spell():
    """The spell must not breach the deadline the instant recovery arms at the open.

    If the spell were allowed to grow through a 15-minute pre-open, arming at 09:15 would
    find a 900s spell already past a 60s deadline and restart the process on every single
    trading day.
    """
    scheduled = {"value": False}
    engine = CaptureEngine(
        {},
        None,
        {},
        None,
        stale_after_ms=5_000,
        stale_exit_ms=60_000,
        recovery_armed=lambda _now: True,
        capture_expected=lambda _now: scheduled["value"],
    )
    engine.freshness.start(0)

    # 15 minutes of unscheduled silence.
    for ts in range(5_000, 900_000, 5_000):
        engine.observe_feed_health(ts)
    assert engine.stale_spell_ms(900_000) == 0
    assert engine.degraded is False

    # The session opens. The spell starts now, from zero.
    scheduled["value"] = True
    engine.observe_feed_health(900_000)
    assert engine.stale_spell_ms(900_000) == 0
    engine.observe_feed_health(930_000)
    assert engine.stale_spell_ms(930_000) == 30_000
    assert engine.escalations == 0  # 30s < 60s deadline

    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(961_000)


def test_leaving_the_session_clears_an_open_stale_spell():
    """§14: after the close, inactivity is not a fault and must not linger as one."""
    scheduled = {"value": True}
    engine = CaptureEngine(
        {},
        None,
        {},
        None,
        stale_after_ms=5_000,
        stale_exit_ms=60_000,
        recovery_armed=lambda _now: True,
        capture_expected=lambda _now: scheduled["value"],
    )
    engine.freshness.start(0)
    engine.observe_feed_health(30_000)  # spell starts here
    engine.observe_feed_health(40_000)
    assert engine.stale_spell_ms(40_000) == 10_000

    scheduled["value"] = False  # market closes
    engine.observe_feed_health(45_000)

    assert engine.stale_spell_ms(45_000) == 0
    assert engine.degraded is False
    # And no amount of post-close silence escalates.
    for ts in range(50_000, 600_000, 10_000):
        engine.observe_feed_health(ts)
    assert engine.escalations == 0



# --- transport vs artifact staleness ------------------------------------------
#
# §16: restart-first recovery answers a dead TRANSPORT. One frozen dataset while packets
# keep arriving is an artifact-level condition — it must be recorded and exposed, not
# allowed to take down capture for every dataset that is working fine.


def _two_artifact_engine(**kwargs):
    """An engine with two real artifacts so per-artifact routing can be exercised."""
    from app.chain.assembler import build_option_chain
    from app.chain.config import get_index_config

    options = _make_options("NIFTY", "2026-07-31", [24500, 24550])
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24550.0, expiry="2026-07-31"
    )
    nifty = IndexTable(chain, 0.0691, "2026-07-31")

    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-31")

    defaults = {
        "stale_after_ms": 5_000,
        "stale_exit_ms": 60_000,
        "recovery_armed": lambda _now: True,
        "capture_expected": lambda _now: True,
    }
    return CaptureEngine(
        {"NIFTY": nifty}, matrix, {}, None, **{**defaults, **kwargs}
    ), nifty, matrix


def test_apply_ticks_records_per_artifact_freshness():
    engine, nifty, matrix = _two_artifact_engine()

    engine.apply_ticks([{"instrument_token": nifty.tokens[0], "last_price": 100.0}], 1_000)

    ages = engine.artifact_ages_ms(1_000)
    assert ages["NIFTY"] == 0
    assert ages["STOCKS"] is None  # never updated yet
    assert engine.artifact_names() == ("NIFTY", "STOCKS")


def test_one_frozen_artifact_does_not_restart_the_process():
    """The socket is alive and STOCKS keeps updating; NIFTY froze. No escalation."""
    engine, nifty, matrix = _two_artifact_engine()
    engine.freshness.start(0)

    # Both artifacts start healthy.
    engine.apply_ticks([{"instrument_token": nifty.tokens[0], "last_price": 1.0}], 0)
    engine.apply_ticks([{"instrument_token": matrix.tokens[0], "last_price": 2.0}], 0)

    # From now on only STOCKS receives ticks. The transport stays alive, but the CONTENT
    # digest stops changing because we replay an identical batch.
    stock_tick = [{"instrument_token": matrix.tokens[0], "last_price": 2.0}]
    for ts in range(1_000, 120_000, 1_000):
        engine.apply_ticks(stock_tick, ts)
        engine.freshness.observe(stock_tick, ts)
        engine.observe_feed_health(ts)  # must not raise

    assert engine.escalations == 0
    assert engine.stale_artifact_names(119_000) == ("NIFTY",)
    assert engine.feed_health(119_000) == "ARTIFACT_STALE"


def test_a_dead_transport_still_escalates():
    """The 2026-08-06 shape: no packets at all. This is what a restart is for."""
    engine, _nifty, _matrix = _two_artifact_engine()
    engine.freshness.start(0)
    engine.observe_feed_health(6_000)

    assert engine.feed_health(6_000) == "TRANSPORT_STALE"
    with pytest.raises(CaptureStalledError):
        engine.observe_feed_health(70_000)


def test_a_quiet_market_is_not_a_fault_and_never_restarts():
    """Packets arriving, every artifact updating, values simply not moving."""
    engine, nifty, matrix = _two_artifact_engine()
    engine.freshness.start(0)

    identical = [
        {"instrument_token": nifty.tokens[0], "last_price": 1.0},
        {"instrument_token": matrix.tokens[0], "last_price": 2.0},
    ]
    for ts in range(0, 120_000, 1_000):
        engine.apply_ticks(identical, ts)
        engine.freshness.observe(identical, ts)  # digest never changes -> content stale
        engine.observe_feed_health(ts)

    assert engine.feed_health(119_000) == "QUIET"
    assert engine.escalations == 0
