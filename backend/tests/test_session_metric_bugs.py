"""Regressions for the session-record metric bugs exposed by the 2026-11-05 session.

That day's record said ``escalations: 0`` and ``generations: 2`` for a session with two
CaptureStalledError restarts and ~100 process starts, carried two disagreeing loss
percentages, and was followed by a second EOD sweep after an evening restart.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.capture import CaptureController
from app.capture.engine import (
    CaptureConsumerFailed,
    CaptureEngine,
    CaptureStalledError,
    build_index_writer,
)
from app.capture.monitor import CaptureMonitor
from app.ops import incidents, stats_store
from app.ops.automation import DailyAutomationService
from app.ops.calendar import IST_FALLBACK
from tests.test_restart_resume import _write_frames, nifty_table

# --- 1. escalations ------------------------------------------------------------------


async def test_unrecoverable_feed_escalation_is_counted_and_recorded():
    """The ticker ladder's "escalating to a process restart" path bypassed the ledger."""
    recorded: list[int] = []
    engine = CaptureEngine(
        {}, None, {}, None,
        escalation_recorder=lambda: recorded.append(1) or len(recorded),
    )

    class Bridge:
        feed_unrecoverable = asyncio.Event()
        subscribe_failures = 0
        reconnect_failures = 3

    Bridge.feed_unrecoverable.set()
    with pytest.raises(CaptureStalledError, match="confirmed subscription"):
        engine.check_pipeline_alive(None, Bridge())

    assert engine.escalations == 1
    assert recorded == [1], "the escalation ledger must see this restart too"


async def test_dead_consumer_escalation_is_counted():
    engine = CaptureEngine({}, None, {}, None)

    async def boom() -> None:
        raise RuntimeError("consumer died")

    consumer = asyncio.create_task(boom())
    await asyncio.gather(consumer, return_exceptions=True)
    with pytest.raises(CaptureConsumerFailed):
        engine.check_pipeline_alive(consumer)

    assert engine.escalations == 1


# --- 2. generations ------------------------------------------------------------------


class _Monitor:
    def __init__(self) -> None:
        self.calls = 0

    def session_summary(self, trading_date: str) -> dict:
        self.calls += 1
        return {"trading_date": trading_date, "captures": 100 + self.calls}


def _controller(tmp_path, run_fn, *, feed_live: bool = True):
    monitor = _Monitor()
    context = SimpleNamespace(
        trading_date="2026-11-05",
        index_tables={},
        stock_matrix=None,
        tokens=list(range(891)),
        skipped_indices=["NIFTY"],
        skip_reasons={"NIFTY": "spot must be > 0"},
        monitor=monitor,
        bridge=SimpleNamespace(connects_confirmed=1 if feed_live else 0),
    )
    fatal = {"n": 0}
    controller = CaptureController(
        SimpleNamespace(stats_dir=tmp_path),
        SimpleNamespace(
            active_session=lambda: SimpleNamespace(access_token="T", risk_free_rate=0.07),
            trading_date=lambda: "2026-11-05",
        ),
        hub=None,
        bootstrap_fn=lambda *_a, **_k: context,
        run_fn=run_fn,
        fatal_handler=lambda: fatal.__setitem__("n", fatal["n"] + 1),
        clock=lambda: 1_793_850_000_000,
    )
    return controller, monitor, fatal


async def _wait_for_stop(_context, stop_event) -> None:
    await stop_event.wait()


async def test_the_closing_double_stop_records_one_generation(tmp_path):
    """15:40 STOP plus the pre-EOD "final flush" STOP used to record the day twice."""
    controller, monitor, _fatal = _controller(tmp_path, _wait_for_stop)

    await controller.start()
    await controller.stop()
    await controller.stop()  # nothing is running any more: must not re-record

    [row] = stats_store.load_session_history(tmp_path)
    assert monitor.calls == 1
    assert row["generations"] == 1


async def test_a_crashed_run_records_its_summary_and_a_failure_incident(tmp_path):
    """A crash exits the process without a stop, so its run was never recorded at all."""

    async def connect_fails(_context, _stop_event) -> None:
        raise RuntimeError("ticker did not confirm a live subscription")

    controller, _monitor, fatal = _controller(tmp_path, connect_fails, feed_live=False)

    await controller.start()
    await asyncio.sleep(0)

    assert fatal["n"] == 1
    [row] = stats_store.load_session_history(tmp_path)
    assert row["generations"] == 1
    kinds = [e["kind"] for e in incidents.load_incidents(tmp_path, "2026-11-05")["entries"]]
    assert kinds == ["capture_start", "capture_failed"]
    failure = incidents.load_incidents(tmp_path, "2026-11-05")["entries"][1]
    assert failure["exception"] == "RuntimeError"
    assert failure["feed_live"] is False
    assert failure["process_exit"] is True


def test_first_summary_of_a_day_is_generation_one(tmp_path):
    stats_store.record_session_summary(tmp_path, {"trading_date": "2026-11-05"})
    [row] = stats_store.load_session_history(tmp_path)
    assert row["generations"] == 1


# --- 3. loss percentages ---------------------------------------------------------------


def test_session_loss_excludes_restart_downtime_from_its_baseline():
    """session_loss_pct claims to isolate the write path, yet counted restart downtime.

    90 frames over a 100-second grid span, 10 of which the process was restarting: every
    second a frame could have been written for was written, so the write-path loss is 0.
    """
    engine = CaptureEngine({}, None, {}, None)
    engine.first_grid_ms, engine.last_grid_ms = 0, 99_000
    engine.captures = 90
    engine.restart_downtime_seconds = 10
    monitor = CaptureMonitor({}, None, {}, None, engine=engine, clock=lambda: 100_000)

    g = monitor.global_metrics([])

    assert g["session_frames_expected"] == 90
    assert g["session_loss_pct"] == 0.0
    # The total-loss figures still see the hole.
    assert g["data_loss_pct"] == 10.0


# --- 4. grid gaps --------------------------------------------------------------------


def test_a_restart_gap_counts_as_a_grid_gap_with_its_seconds_in_restart_downtime(tmp_path):
    """``grid_gaps 2`` with ``grid_seconds_lost 0`` is consistent, not a lost-data bug.

    A resume is a gap in the grid, but its seconds are restart downtime — deliberately kept
    out of ``grid_seconds_lost`` (the catch-up/write-path figure) so the two are never
    summed into a hole larger than the real one.
    """
    path = tmp_path / "NIFTY" / "2026-11-05.bin"
    table = nifty_table()
    _write_frames(path, table, 60, start_ts=1_000_000, step=1_000)
    engine = CaptureEngine(
        {"NIFTY": table}, None, {"NIFTY": build_index_writer(table, path)}, None,
        clock=lambda: 1_000_000 + 59_000 + 98_000,
    )
    engine.resume_from_disk(None)

    assert engine.grid_gaps == 1
    assert engine.grid_seconds_lost == 0
    assert engine.restart_downtime_seconds == 98


# --- 5. EOD after a restart ----------------------------------------------------------


def _ist_ms(hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, 7, 21, hour, minute, second, tzinfo=IST_FALLBACK).timestamp() * 1000)


def _automation(tmp_path, eod_calls: list[str]) -> DailyAutomationService:
    class Capture:
        running = False

        async def stop(self, reason=None) -> None:
            return None

    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
        stats_dir=tmp_path / "stats",
    )

    def eod_fn(*_args, **_kwargs) -> None:
        eod_calls.append("eod")

    return DailyAutomationService(
        settings, SimpleNamespace(active_session=lambda: None), Capture(), eod_fn=eod_fn
    )


async def test_a_restarted_process_does_not_rerun_a_completed_eod(tmp_path):
    eod_calls: list[str] = []
    first = _automation(tmp_path, eod_calls)
    await first.tick(_ist_ms(15, 30, 5))
    assert eod_calls == ["eod"]

    restarted = _automation(tmp_path, eod_calls)  # 18:53: a fresh process, empty memory
    await restarted.tick(_ist_ms(18, 53))

    assert eod_calls == ["eod"], "EOD for a completed date must not run a second time"
    assert stats_store.is_eod_completed(tmp_path / "stats", "2026-07-21")


async def test_a_failed_eod_is_not_marked_complete(tmp_path):
    def failing_eod(*_args, **_kwargs):
        raise OSError("disk full")

    automation = _automation(tmp_path, [])
    automation._eod_fn = failing_eod
    await automation.tick(_ist_ms(15, 30, 5))

    assert not stats_store.is_eod_completed(tmp_path / "stats", "2026-07-21")
