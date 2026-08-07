"""Tests for per-session data-loss telemetry and the session-history store."""

from __future__ import annotations

from types import SimpleNamespace

from app.capture.engine import CaptureEngine
from app.capture.monitor import (
    CaptureMonitor,
    expected_frames_elapsed,
)
from app.ops import stats_store
from tests.test_capture import _nifty_table

# --- pure helpers -------------------------------------------------------------


def test_expected_frames_elapsed_counts_grid_seconds():
    # 09:00:00 -> 09:00:10 inclusive is 11 grid seconds at 1 Hz.
    assert expected_frames_elapsed(1_000_000, 1_010_000) == 11
    assert expected_frames_elapsed(1_000_000, 1_000_000) == 1
    assert expected_frames_elapsed(None, None) == 0


# --- engine gap accounting ----------------------------------------------------


def test_capture_snapshot_tracks_first_last_and_stale_seconds():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)

    engine.capture_snapshot(1_000)
    engine.capture_snapshot(2_000)
    assert engine.first_capture_ms == 1_000
    assert engine.last_capture_ms == 2_000
    # No stale feed yet at t<5000 -> nothing counted as stale.
    assert engine.stale_seconds == 0

    # Past the staleness threshold with no fresh ticks: the frame is skipped, not written.
    engine.capture_snapshot(10_000)
    assert engine.stale_seconds == 1
    assert engine.captures == 2, "a stale grid second must not count as a capture"
    assert engine.last_capture_ms == 2_000, "written-frame timestamps must not advance"
    # ...but the elapsed grid span does advance, so the loss stays visible.
    assert engine.last_grid_ms == 10_000


def test_grid_gap_counters_start_at_zero():
    engine = CaptureEngine({}, None, {}, None)
    assert engine.grid_gaps == 0
    assert engine.grid_seconds_lost == 0
    assert engine.stale_seconds == 0
    assert engine.stale_events == 0


# --- monitor payload ----------------------------------------------------------


def _monitor(engine, bridge=None, **kwargs):
    return CaptureMonitor(
        {}, None, {}, None, engine=engine, bridge=bridge, clock=lambda: 20_000, **kwargs
    )


def test_global_metrics_exposes_data_loss_fields():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    engine.capture_snapshot(1_000)
    engine.capture_snapshot(2_000)
    engine.grid_gaps = 2
    engine.grid_seconds_lost = 37
    engine.unmatched = 5
    bridge = SimpleNamespace(
        dropped_batches=0,
        reconnects=0,
        batches_received=10,
        ticks_received=400,
        token_refreshes=0,
        last_token_refresh_ms=None,
        connected=True,
    )
    g = _monitor(engine, bridge, capture_start_ms=0).global_metrics()

    assert g["grid_gaps"] == 2
    assert g["grid_seconds_lost"] == 37
    assert g["unmatched_ticks"] == 5
    assert g["batches_received"] == 10
    assert g["ticks_received"] == 400
    # 2 captures over an elapsed span of 1s (1000->2000) = 2 expected -> no loss.
    assert g["session_frames_expected"] == 2
    assert g["session_loss_pct"] == 0.0
    assert g["data_loss_pct"] == 0.0
    assert g["stale_seconds"] == 0
    assert g["stale_events"] == 0
    assert g["grid_seconds_elapsed"] == 2
    assert "ticks_per_sec" in g


def test_session_loss_pct_uses_elapsed_not_daily_baseline():
    """A short healthy session must not report ~96% loss (the old daily-baseline bug)."""
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    for ts in range(1_000, 11_000, 1_000):  # 10 consecutive grid seconds
        # A live feed changes content every second; without this the engine (correctly)
        # treats the feed as frozen and suppresses the writes.
        engine.freshness.observe([{"instrument_token": 1, "last_price": ts}], ts)
        engine.capture_snapshot(ts)
    g = _monitor(engine, capture_start_ms=0).global_metrics()
    assert g["session_frames_expected"] == 10
    assert g["session_loss_pct"] == 0.0
    assert g["data_loss_pct"] == 0.0
    # The full-day completeness figure remains available and is (correctly) large.
    assert g["frame_loss_pct"] >= 0.0


def test_directory_bytes_is_cached_within_ttl(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    engine = CaptureEngine({}, None, {}, None)
    calls = {"n": 0}
    monitor = CaptureMonitor(
        {},
        None,
        {},
        None,
        engine=engine,
        market_data_path=tmp_path,
        clock=lambda: 1_000,
        disk_bytes_ttl_ms=30_000,
    )
    real = monitor._cached_directory_bytes

    def counting(now):
        calls["n"] += 1
        return real(now)

    monitor._cached_directory_bytes = counting  # type: ignore[method-assign]
    first = monitor.global_metrics()["disk_bytes"]
    second = monitor.global_metrics()["disk_bytes"]
    assert first == second == 10
    # The expensive walk ran once; the second read came from the TTL cache.
    assert monitor._disk_bytes_cache is not None


def test_per_underlying_computed_once_per_snapshot():
    engine = CaptureEngine({}, None, {}, None)
    monitor = _monitor(engine)
    calls = {"n": 0}
    real = monitor.per_underlying

    def counting():
        calls["n"] += 1
        return real()

    monitor.per_underlying = counting  # type: ignore[method-assign]
    monitor.snapshot()
    assert calls["n"] == 1  # previously 2 (snapshot + global_metrics)


# --- session history store ----------------------------------------------------


def test_record_and_load_session_history(tmp_path):
    stats_store.record_session_summary(
        tmp_path, {"trading_date": "2026-07-24", "grid_seconds_lost": 3}
    )
    stats_store.record_session_summary(
        tmp_path, {"trading_date": "2026-07-27", "grid_seconds_lost": 0}
    )
    history = stats_store.session_history(tmp_path)
    assert [r["trading_date"] for r in history] == ["2026-07-27", "2026-07-24"]


def test_record_session_summary_replaces_same_date(tmp_path):
    stats_store.record_session_summary(tmp_path, {"trading_date": "2026-07-27", "captures": 10})
    stats_store.record_session_summary(tmp_path, {"trading_date": "2026-07-27", "captures": 99})
    history = stats_store.session_history(tmp_path)
    assert len(history) == 1
    assert history[0]["captures"] == 99  # a mid-day restart must not duplicate the day


def test_session_history_tolerates_corrupt_lines(tmp_path):
    path = stats_store.session_history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"trading_date": "2026-07-27"}\nNOT JSON\n', encoding="utf-8")
    assert [r["trading_date"] for r in stats_store.session_history(tmp_path)] == ["2026-07-27"]


def test_latest_capture_snapshot_returns_newest_any_date(tmp_path):
    stats_store.write_capture_snapshot(tmp_path, "2026-07-24", {"global": {"captures": 1}})
    stats_store.write_capture_snapshot(tmp_path, "2026-07-27", {"global": {"captures": 2}})
    latest = stats_store.latest_capture_snapshot(tmp_path)
    assert latest is not None
    assert latest[0] == "2026-07-27"
    assert latest[1]["global"]["captures"] == 2


def test_latest_capture_snapshot_none_when_empty(tmp_path):
    assert stats_store.latest_capture_snapshot(tmp_path) is None


def test_ticks_per_sec_is_a_trailing_rate_not_a_lifetime_average():
    """The label promises a rate, so it must reflect the CURRENT rate.

    The old form was ticks_received/uptime, which only ever creeps toward the session
    mean: if ingest stops, it keeps reporting a large number, and if ingest doubles it
    barely moves. This asserts the value tracks the recent window instead.
    """
    engine = CaptureEngine({}, None, {}, None)
    clock = {"now": 0}
    bridge = SimpleNamespace(
        dropped_batches=0, reconnects=0, batches_received=0, ticks_received=0,
        token_refreshes=0, last_token_refresh_ms=None, connected=True,
    )
    monitor = CaptureMonitor(
        {}, None, {}, None, engine=engine, bridge=bridge,
        clock=lambda: clock["now"], capture_start_ms=0,
    )

    # Steady 100 ticks/sec for 5 seconds.
    for sec in range(1, 6):
        clock["now"] = sec * 1000
        bridge.ticks_received = sec * 100
        rate = monitor.global_metrics()["ticks_per_sec"]
    assert 90 <= rate <= 110, f"expected ~100 ticks/s, got {rate}"

    # Ingest STOPS. A lifetime average would still report ~100; a trailing rate decays.
    for sec in range(6, 13):
        clock["now"] = sec * 1000
        rate = monitor.global_metrics()["ticks_per_sec"]
    assert rate == 0.0, f"a stalled feed must report 0 ticks/s, got {rate}"


def test_session_summary_payload_shape():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    engine.capture_snapshot(1_000)
    engine.grid_seconds_lost = 4
    summary = _monitor(engine, capture_start_ms=0).session_summary("2026-07-27")
    assert summary["trading_date"] == "2026-07-27"
    assert summary["grid_seconds_lost"] == 4
    for key in ("session_loss_pct", "data_loss_pct", "stale_seconds", "captures", "streams"):
        assert key in summary


# --- per-stream loss: elapsed vs whole-day baselines --------------------------


def test_per_underlying_reports_elapsed_loss_not_day_progress(tmp_path):
    """A healthy mid-morning session must not look like 75% data loss.

    The per-underlying row used only the whole-day baseline, so at 10:30 a perfect
    capture reported ~75% "loss". Elapsed-based loss is the health signal; day progress
    is reported separately.
    """
    from app.bin_codec.writer import IndexBinWriter
    from app.capture.writer_thread import FileWriterThread

    table = _nifty_table()
    path = tmp_path / "NIFTY.bin"
    writer = FileWriterThread(IndexBinWriter(path), table.header(), name="idx")
    writer.start()
    writer.wait_until_ready()

    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    # 10 consecutive grid seconds captured, all written (fresh content each second).
    for ts in range(1_000, 11_000, 1_000):
        engine.freshness.observe([{"instrument_token": 1, "last_price": ts}], ts)
        engine.capture_snapshot(ts)
    writer.stop(join=True)

    monitor = CaptureMonitor(
        {"NIFTY": table}, None, {"NIFTY": writer}, None,
        engine=engine, clock=lambda: 11_000, expected_frames=23_700, capture_start_ms=0,
    )
    row = monitor.per_underlying()[0]

    assert row["frames_written"] == 10
    # Health: 10 frames over 10 elapsed grid seconds -> no loss.
    assert row["session_frames_expected"] == 10
    assert row["session_loss_pct"] == 0.0
    # Progress: only 10 of 23,700 frames of the full day so far.
    assert row["frame_loss_pct"] > 99          # the old, alarming-looking number
    assert row["day_complete_pct"] < 1         # ...is really just day progress


def test_broadcast_messages_carry_pipeline_latency():
    """Every streamed message gets a server-measured pipeline_ms (no clock skew)."""
    from app.ws import protocol

    msg = protocol.envelope("X", {"a": 1}, {"pipeline_ms": 12})
    assert msg == {"type": "X", "payload": {"a": 1}, "meta": {"pipeline_ms": 12}}
    # No meta key at all when none is supplied (keeps existing messages byte-identical).
    assert protocol.envelope("X", {"a": 1}) == {"type": "X", "payload": {"a": 1}}



# --- stale data must never reach the .bin files -------------------------------


def _index_writer(tmp_path, table, name="NIFTY"):
    from app.bin_codec.scan import scan_frames
    from app.bin_codec.writer import IndexBinWriter
    from app.capture.writer_thread import FileWriterThread

    path = tmp_path / f"{name}.bin"
    writer = FileWriterThread(
        IndexBinWriter(path),
        table.header(),
        name=name,
        frames_on_disk=scan_frames(path).frames,
    )
    writer.start()
    writer.wait_until_ready()
    return writer


def test_stale_grid_seconds_are_not_written_to_disk(tmp_path):
    """The core data-integrity guarantee: a frozen feed must leave a HOLE, not a lie.

    The fixed-width .bin layout means a frame of duplicated last-known values is
    indistinguishable from a real one — same size, same cadence, same frame count. Such a
    file cannot be repaired because nothing marks which prints never happened. A missing
    second is recoverable (visible, counted, backfillable), so absence is the safer
    failure. This asserts the writer never sees a stale frame.
    """
    from app.bin_codec.scan import scan_frames

    table = _nifty_table()
    writer = _index_writer(tmp_path, table)
    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None, stale_after_ms=5_000)
    engine.freshness.start(0)

    # 3 fresh seconds: each observed batch changes the content digest.
    for ts in (1_000, 2_000, 3_000):
        engine.freshness.observe([{"instrument_token": 1, "last_price": ts}], ts)
        assert engine.capture_once(ts) == 1

    # The feed now freezes: no further batches arrive at all.
    for ts in (10_000, 11_000, 12_000):
        assert engine.capture_once(ts) == 0, "a stale second must enqueue nothing"

    # ...and then recovers.
    engine.freshness.observe([{"instrument_token": 1, "last_price": 99}], 13_000)
    assert engine.capture_once(13_000) == 1

    writer.stop(join=True)

    scan = scan_frames(tmp_path / "NIFTY.bin")
    assert scan.frames == 4, "only the 4 fresh grid seconds may exist on disk"
    assert engine.captures == 4
    assert engine.stale_seconds == 3
    assert engine.stale_events == 1, "one continuous freeze is one event"
    # The hole is visible in the timestamps rather than papered over.
    assert scan.first_timestamp_ms == 1_000
    assert scan.last_timestamp_ms == 13_000


def test_suppression_can_be_disabled_for_legacy_behaviour(tmp_path):
    table = _nifty_table()
    writer = _index_writer(tmp_path, table, name="LEGACY")
    engine = CaptureEngine(
        {"NIFTY": table},
        None,
        {"NIFTY": writer},
        None,
        stale_after_ms=5_000,
        suppress_stale_writes=False,
    )
    engine.freshness.start(0)
    assert engine.capture_once(10_000) == 1, "opt-out must restore the old write-anyway path"
    writer.stop(join=True)
    assert engine.stale_seconds == 0
    assert engine.captures == 1


def test_stale_events_counts_spells_not_seconds():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    # Spell 1: two consecutive stale seconds.
    engine.capture_snapshot(10_000)
    engine.capture_snapshot(11_000)
    # Recovery.
    engine.freshness.observe([{"instrument_token": 1, "last_price": 1}], 12_000)
    engine.capture_snapshot(12_000)
    # Spell 2.
    engine.capture_snapshot(20_000)
    assert engine.stale_seconds == 3
    assert engine.stale_events == 2


def test_data_loss_pct_exposes_a_feed_that_froze_from_the_open():
    """The regression this whole change exists for.

    A feed that stops updating at the open used to produce a session that looked
    flawless: a frame every second, ~0% elapsed loss, ~100% frame integrity. With stale
    writes suppressed, the same scenario must report the truth — most of the session
    missing — while the gaps-only figure correctly stays clean, because nothing was
    dropped by the write path.
    """
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    # 2 genuinely fresh seconds...
    for ts in (1_000, 2_000):
        engine.freshness.observe([{"instrument_token": 1, "last_price": ts}], ts)
        engine.capture_snapshot(ts)
    # ...then the feed freezes for good. The 5s staleness threshold means 3_000..6_000 are
    # still inside the tolerance and get written; everything from 7_000 on is suppressed.
    for ts in range(3_000, 21_000, 1_000):
        engine.capture_snapshot(ts)

    g = _monitor(engine, capture_start_ms=0).global_metrics()
    assert g["stale_seconds"] == 14
    assert g["grid_seconds_elapsed"] == 20
    assert g["captures"] == 6
    # 6 written of the 6 seconds that were writable -> the write path lost nothing.
    assert g["session_frames_expected"] == 6
    assert g["session_loss_pct"] == 0.0
    # 6 written of 20 elapsed seconds -> 70% of the session is genuinely missing.
    assert g["data_loss_pct"] == 70.0
    assert g["stale_writes_suppressed"] is True


def test_stale_suppression_survives_a_restart(tmp_path):
    """A restart must not reset the stale tally to zero (it did, twice over).

    The persisted snapshot was read from the wrong directory AND with the wrong nesting,
    so ``carried`` was always empty — which is why a session with 3h46m of frozen feed
    reported 0 frozen seconds after its process restarted.
    """
    table = _nifty_table()
    writer = _index_writer(tmp_path, table, name="RESUME")
    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    engine.freshness.observe([{"instrument_token": 1, "last_price": 5}], 60_000)
    engine.capture_once(60_000)
    writer.stop(join=True)

    # Fresh process: new engine, same file, counters carried from the persisted snapshot.
    table2 = _nifty_table()
    writer2 = _index_writer(tmp_path, table2, name="RESUME")
    engine2 = CaptureEngine(
        {"NIFTY": table2}, None, {"NIFTY": writer2}, None, clock=lambda: 120_000
    )
    resume = engine2.resume_from_disk(
        {
            "grid_gaps": 1,
            "grid_seconds_lost": 4,
            "stale_seconds": 1_200,
            "stale_events": 2,
            "first_grid_ms": 1_000,
        }
    )
    writer2.stop(join=True)

    assert resume["resumed"] is True
    assert engine2.stale_seconds == 1_200
    assert engine2.stale_events == 2
    # The elapsed baseline starts at the session's real start, not the first frame on
    # disk — otherwise a suppressed morning would silently vanish from the loss figure.
    assert engine2.first_grid_ms == 1_000



# --- session-scheduled loss accounting (§17) ----------------------------------
#
# The denominator must come from the session schedule, not from what the process managed
# to observe. Otherwise an outage erases itself from its own loss figure: if capture is
# down 09:15-09:27 then the first grid second it ever sees is 09:27, and those 12 missing
# minutes simply never enter the arithmetic.


def _registry_at(open_at="09:15", close_at="15:30"):
    from types import SimpleNamespace

    from app.ops.sessions import build_session_registry

    settings = SimpleNamespace(
        market_holidays=[],
        timezone="Asia/Kolkata",
        market_open=open_at,
        market_close=close_at,
        capture_recovery_arm_delay_seconds=300.0,
        equity_deriv_open=open_at,
        equity_deriv_close=close_at,
    )
    return build_session_registry(settings, {"NIFTY": "equity_deriv"})


def _ist(hour, minute, day=10):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return int(
        datetime(2026, 8, day, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
        * 1000
    )


def _monitor_with_session(captures, **engine_attrs):
    from app.capture.monitor import CaptureMonitor

    engine = SimpleNamespace(
        captures=captures,
        stale_seconds=0,
        grid_seconds_lost=0,
        grid_gaps=0,
        stale_events=0,
        unmatched=0,
        unscheduled_seconds=0,
        first_grid_ms=None,
        last_grid_ms=None,
        first_capture_ms=None,
        last_capture_ms=None,
        last_snapshot_ms=0.0,
        suppress_stale_writes=True,
        degraded=False,
        exhausted=False,
        recovery_abandoned=False,
        escalations=0,
        longest_stale_spell_seconds=0,
        stale_spell_ms=lambda _now: 0,
        recovery_armed=lambda _now: True,
        freshness=None,
    )
    for key, value in engine_attrs.items():
        setattr(engine, key, value)
    return CaptureMonitor(
        {"NIFTY": SimpleNamespace(tokens=[])},
        None,
        {},
        None,
        engine=engine,
        clock=lambda: _ist(9, 27),
        session_registry=_registry_at(),
        expected_frames=22_500,
    )


def test_process_downtime_appears_in_the_loss_figure():
    """§17.5: capture was down 09:15-09:27, so 720 scheduled seconds are missing."""
    monitor = _monitor_with_session(captures=0)

    g = monitor.global_metrics([])

    assert g["scheduled_seconds"] == 6 * 3600 + 15 * 60  # the full session
    assert g["scheduled_seconds_elapsed"] == 720  # owed so far today
    assert g["captured_seconds"] == 0
    assert g["missing_seconds"] == 720
    assert g["downtime_seconds"] == 720  # nothing else can explain it
    assert g["scheduled_loss_pct"] == 100.0


def test_a_stale_feed_is_attributed_to_the_feed_not_to_downtime():
    monitor = _monitor_with_session(captures=420, stale_seconds=300)

    g = monitor.global_metrics([])

    assert g["missing_seconds"] == 300
    assert g["stale_feed_seconds"] == 300
    assert g["downtime_seconds"] == 0
    assert g["unclassified_seconds"] == 0


def test_stale_and_downtime_are_split_and_reconcile_with_the_total():
    """§17.11: the breakdown must add up to the total, with nothing silently dropped."""
    monitor = _monitor_with_session(captures=300, stale_seconds=120)

    g = monitor.global_metrics([])

    assert g["scheduled_seconds_elapsed"] == 720
    assert g["missing_seconds"] == 420
    assert g["stale_feed_seconds"] == 120
    assert g["downtime_seconds"] == 300
    total = (
        g["stale_feed_seconds"]
        + g["downtime_seconds"]
        + g["write_path_seconds"]
        + g["unclassified_seconds"]
    )
    assert total == g["missing_seconds"]


def test_a_fully_captured_session_so_far_reports_no_loss():
    monitor = _monitor_with_session(captures=720)

    g = monitor.global_metrics([])

    assert g["missing_seconds"] == 0
    assert g["downtime_seconds"] == 0
    assert g["scheduled_loss_pct"] == 0.0


def test_market_phase_is_reported_separately_from_feed_health():
    """§23: two independent dimensions, never overloaded onto one status variable."""
    monitor = _monitor_with_session(captures=720)

    g = monitor.global_metrics([])

    assert g["market_phase"] == "OPEN"
    assert g["capture_expected"] is True
    # feed_health comes from the engine's three signals, not from the phase.
    assert "feed_health" in g
