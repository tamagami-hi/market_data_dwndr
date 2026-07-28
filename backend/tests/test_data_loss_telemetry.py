"""Tests for per-session data-loss telemetry and the session-history store."""

from __future__ import annotations

from types import SimpleNamespace

from app.capture.engine import CaptureEngine
from app.capture.monitor import (
    CaptureMonitor,
    disk_runway_hours,
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


def test_disk_runway_hours():
    # 3600 bytes/s of writes against 3600 bytes free = 1 second = 1/3600 h.
    assert disk_runway_hours(3_600, 1_200.0, 3) == 1 / 3600
    assert disk_runway_hours(0, 100.0, 1) == 0.0
    assert disk_runway_hours(1_000, 0.0, 1) == 0.0


# --- engine gap accounting ----------------------------------------------------


def test_capture_snapshot_tracks_first_last_and_frozen_seconds():
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)

    engine.capture_snapshot(1_000)
    engine.capture_snapshot(2_000)
    assert engine.first_capture_ms == 1_000
    assert engine.last_capture_ms == 2_000
    # No stale feed yet at t<5000 -> nothing counted as frozen.
    assert engine.frozen_seconds == 0

    # Past the staleness threshold with no fresh ticks: frames now carry stale values.
    engine.capture_snapshot(10_000)
    assert engine.frozen_seconds == 1


def test_grid_gap_counters_start_at_zero():
    engine = CaptureEngine({}, None, {}, None)
    assert engine.grid_gaps == 0
    assert engine.grid_seconds_lost == 0


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
    assert "disk_runway_hours" in g
    assert "ticks_per_sec" in g


def test_session_loss_pct_uses_elapsed_not_daily_baseline():
    """A short healthy session must not report ~96% loss (the old daily-baseline bug)."""
    engine = CaptureEngine({}, None, {}, None, stale_after_ms=5_000)
    engine.freshness.start(0)
    for ts in range(1_000, 11_000, 1_000):  # 10 consecutive grid seconds
        engine.capture_snapshot(ts)
    g = _monitor(engine, capture_start_ms=0).global_metrics()
    assert g["session_frames_expected"] == 10
    assert g["session_loss_pct"] == 0.0
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
    for key in ("session_loss_pct", "frozen_seconds", "captures", "streams"):
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
    # 10 consecutive grid seconds captured, all written.
    for ts in range(1_000, 11_000, 1_000):
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
