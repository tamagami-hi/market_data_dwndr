"""Full trading-lifecycle drill for the session-aware capture pipeline.

The unit tests assert each mechanism in isolation. This module walks a whole simulated
trading day through the **real** engine, real writer threads, real files and the real
session registry, checking the properties that only emerge from the sequence of phases:

    bootstrap -> pre-open -> open -> live -> failure -> recovery -> close

Every assertion here corresponds to a way the pipeline previously misread its own state:

* silence before the exchange opens looked identical to a dead feed (the 2026-08-04/05/06
  sessions each began their destructive recovery in that window);
* a loss denominator built from what the process observed erased its own downtime;
* one frozen dataset was indistinguishable from a dead uplink, so it could restart capture
  for every dataset that was working;
* a single tick during an outage reset the recovery ladder and disarmed escalation;
* post-close inactivity kept counting as data loss.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.bin_codec.reader import IndexBinReader
from app.bin_codec.scan import scan_frames
from app.capture.engine import (
    CaptureEngine,
    CaptureStalledError,
    build_index_fno_writer,
    build_index_writer,
    build_stock_writer,
)
from app.capture.monitor import CaptureMonitor
from app.capture.subscription import plan_subscriptions
from app.index_fno.board import build_index_fno_board
from app.index_fno.matrix import IndexFnoMatrix
from app.ops.completeness import classify_downtime, reconcile
from app.ops.sessions import build_session_registry
from app.stocks.board import build_board
from app.stocks.matrix import StockMatrix
from tests.test_capture import _nifty_table, _sample_instruments
from tests.test_index_fno import _future

IST = ZoneInfo("Asia/Kolkata")
TRADING_DATE = "2026-08-10"  # a Monday
INTERVAL_MS = 1_000
STALE_AFTER_MS = 5_000
STALE_EXIT_MS = 60_000


def at(hour: int, minute: int, second: int = 0) -> int:
    """Epoch ms for an IST wall-clock time on the drill's trading date."""
    return int(
        datetime(2026, 8, 10, hour, minute, second, tzinfo=IST).timestamp() * 1000
    )


class Clock:
    def __init__(self, start: int) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance_to(self, ms: int) -> None:
        self.now = ms


def _settings(tmp_path):
    """Deployment-shaped settings: capture opens at 09:15 with an uncaptured pre-open."""
    return SimpleNamespace(
        market_holidays=[],
        timezone="Asia/Kolkata",
        market_open="09:15",
        market_close="15:30",
        capture_recovery_arm_delay_seconds=300.0,
        equity_deriv_open="09:15",
        equity_deriv_close="15:30",
        equity_deriv_preopen_start="09:00",
        equity_deriv_preopen_end="09:15",
        equity_deriv_capture_preopen=False,
        equity_deriv_enabled=True,
        market_data_path=tmp_path,
        stats_dir=tmp_path / "_state" / "stats",
    )


class Rig:
    """A live capture rig over real files, driven one grid second at a time."""

    def __init__(self, tmp_path, clock: Clock) -> None:
        self.tmp_path = tmp_path
        self.clock = clock
        settings = _settings(tmp_path)

        table = _nifty_table()
        nfo, nse = _sample_instruments()
        matrix = StockMatrix(build_board(nfo, nse), 0.0691, TRADING_DATE)
        fno_board = build_index_fno_board(
            {"NFO": [_future("NIFTY", "2026-08-27", 5001)], "BFO": []}, ["NIFTY"]
        )
        fno = IndexFnoMatrix(fno_board, 0.0691, TRADING_DATE)

        self.index_path = tmp_path / "INDICES" / "NIFTY" / f"{TRADING_DATE}.bin"
        self.stock_path = tmp_path / "STOCKS" / f"{TRADING_DATE}.bin"
        self.fno_path = tmp_path / "INDICES_FnO" / f"{TRADING_DATE}.bin"

        self.registry = build_session_registry(
            settings,
            {"NIFTY": "equity_deriv", "STOCKS": "equity_deriv", "INDICES_FnO": "equity_deriv"},
        )
        self.subscription = plan_subscriptions(
            {
                "NIFTY": list(table.tokens),
                "STOCKS": list(matrix.tokens),
                "INDICES_FnO": list(fno.tokens),
            }
        )
        self.engine = CaptureEngine(
            {"NIFTY": table},
            matrix,
            {"NIFTY": build_index_writer(table, self.index_path)},
            build_stock_writer(matrix, self.stock_path),
            clock=clock,
            index_fno_matrix=fno,
            index_fno_writer=build_index_fno_writer(fno, self.fno_path),
            stale_after_ms=STALE_AFTER_MS,
            stale_exit_ms=STALE_EXIT_MS,
            stale_recovery_confirm_ms=15_000,
            recovery_armed=self.registry.any_stale_armed,
            capture_expected=self.registry.any_capture_expected,
            escalation_limit=3,
        )
        self.monitor = CaptureMonitor(
            {"NIFTY": table},
            matrix,
            self.engine.index_writers,
            self.engine.stock_writer,
            engine=self.engine,
            index_fno_matrix=fno,
            index_fno_writer=self.engine.index_fno_writer,
            market_data_path=tmp_path,
            clock=clock,
            session_registry=self.registry,
            subscription=self.subscription,
            expected_frames=22_500,
        )
        self.table, self.matrix, self.fno = table, matrix, fno
        self.engine.freshness.start(clock.now)

    # -- driving ---------------------------------------------------------- #

    def tick_batch(self, price: float) -> list[dict]:
        """A batch touching every artifact, with a changing price so content is fresh."""
        return [
            {"instrument_token": self.table.tokens[0], "last_price": price},
            {"instrument_token": self.matrix.tokens[0], "last_price": price},
            {"instrument_token": self.fno.tokens[0], "last_price": price},
        ]

    def run_seconds(self, start_ms: int, count: int, *, feed=True, price_base=100.0):
        """Advance the grid ``count`` seconds from ``start_ms``.

        ``feed=False`` simulates a total transport outage: no batch is delivered at all,
        which is what a half-open socket looks like from here.
        """
        for step in range(count):
            ts = start_ms + step * INTERVAL_MS
            self.clock.advance_to(ts)
            if feed:
                batch = self.tick_batch(price_base + step)
                self.engine.apply_ticks(batch, ts)
                self.engine.stall.mark_message(ts)
                self.engine.freshness.observe(batch, ts)
            self.engine.capture_snapshot(ts)
            self.engine.observe_feed_health(ts)
        # Leave the clock on the next grid boundary, where the real loop waits. Elapsed
        # scheduled time is then exactly ``count`` seconds rather than count - 1.
        self.clock.advance_to(start_ms + count * INTERVAL_MS)

    def start(self) -> None:
        self.engine.start_writers()

    def stop(self) -> None:
        self.engine.stop_writers()


@pytest.fixture
def rig(tmp_path):
    clock = Clock(at(8, 55))
    rig = Rig(tmp_path, clock)
    rig.start()
    try:
        yield rig
    finally:
        try:
            rig.stop()
        except Exception:  # noqa: BLE001 - already stopped by the test
            pass


# --- 1. startup before market activity ----------------------------------------


def test_startup_before_the_market_records_no_loss_and_no_escalation(rig):
    """Bootstrap succeeds at 08:55 and the idle wait is not a fault."""
    rig.run_seconds(at(8, 55), 60, feed=False)

    assert rig.engine.escalations == 0
    assert rig.engine.stale_seconds == 0  # nothing was owed, so nothing was skipped
    assert rig.engine.unscheduled_seconds == 60
    assert rig.engine.captures == 0
    globals_ = rig.monitor.global_metrics()
    assert globals_["market_phase"] == "BOOTSTRAP"
    assert globals_["capture_expected"] is False
    assert globals_["feed_health"] == "INACTIVE"
    assert globals_["missing_seconds"] == 0
    assert globals_["scheduled_loss_pct"] == 0.0


# --- 2. pre-open ---------------------------------------------------------------


def test_pre_open_silence_is_inactive_not_a_dead_feed(rig):
    """The window that started the 2026-08-04/05/06 incidents."""
    rig.run_seconds(at(9, 0), 120, feed=False)

    globals_ = rig.monitor.global_metrics()
    assert globals_["market_phase"] == "PRE_OPEN"
    assert globals_["capture_expected"] is False  # pre-open capture is off by policy
    assert globals_["feed_health"] == "INACTIVE"
    # No frames owed, none written, no loss, no restart.
    assert globals_["scheduled_seconds_elapsed"] == 0
    assert globals_["missing_seconds"] == 0
    assert rig.engine.escalations == 0
    assert rig.engine.stale_spell_ms(at(9, 2)) == 0
    # And no file has been created with fabricated frames.
    assert scan_frames(rig.index_path).frames == 0


# --- 3 + 4. open and normal live operation ------------------------------------


def test_open_begins_persistence_and_loss_accounting_from_the_scheduled_point(rig):
    rig.run_seconds(at(9, 0), 60, feed=False)  # pre-open, nothing owed
    rig.run_seconds(at(9, 15), 30, feed=True)  # the exchange opens
    rig.stop()

    globals_ = rig.monitor.global_metrics()
    assert globals_["market_phase"] == "OPEN"
    assert globals_["capture_expected"] is True
    assert globals_["feed_health"] == "HEALTHY"
    # 30 scheduled seconds elapsed, 30 captured: the denominator starts at the open, not
    # at whenever the process happened to come up.
    assert globals_["scheduled_seconds_elapsed"] == 30
    assert globals_["captured_seconds"] == 30
    assert globals_["missing_seconds"] == 0
    assert globals_["downtime_seconds"] == 0

    # Every domain persisted the same 30 grid seconds, on one shared timing grid.
    for path in (rig.index_path, rig.stock_path, rig.fno_path):
        scan = scan_frames(path, collect_timestamps=True)
        assert scan.frames == 30
        assert scan.first_timestamp_ms == at(9, 15)
        assert scan.last_timestamp_ms == at(9, 15, 29)
        assert scan.timestamps == tuple(
            at(9, 15) + step * INTERVAL_MS for step in range(30)
        )

    with IndexBinReader(rig.index_path) as reader:
        # Sequences are contiguous across consecutive written seconds. They do not start at
        # zero: the table's sequence advances once per grid second *built*, and the
        # pre-open seconds were built for the dashboard even though nothing was persisted.
        # That is the documented self-auditing property — a sequence step of N proves N
        # grid seconds elapsed — and it is only meaningful within one process run, which is
        # why timestamps, not sequences, are the archive's completeness evidence.
        sequences = [f.sequence for f in reader.frames()]
        assert len(sequences) == 30
        assert sequences == list(range(sequences[0], sequences[0] + 30))


def test_live_operation_keeps_every_artifact_healthy_and_queues_drained(rig):
    rig.run_seconds(at(9, 20), 45, feed=True)
    rig.stop()

    entries = {e["underlying"]: e for e in rig.monitor.per_underlying()}
    assert set(entries) == {"NIFTY", "STOCKS", "INDICES_FnO"}
    for entry in entries.values():
        assert entry["market_phase"] == "OPEN"
        assert entry["capture_active"] is True
        assert entry["artifact_stale"] is False
        assert entry["frames_written"] == 45
        assert entry["writer_pending"] == 0  # queues drained
    globals_ = rig.monitor.global_metrics(list(entries.values()))
    assert globals_["feed_health"] == "HEALTHY"
    assert globals_["transport"]["dropped_batches"] == 0
    assert globals_["transport"]["subscription_over_threshold"] is False


# --- 5. complete transport failure --------------------------------------------


def test_a_total_transport_outage_suppresses_writes_and_then_escalates(rig):
    rig.run_seconds(at(9, 20), 10, feed=True)  # healthy
    # The uplink dies. Writes must stop rather than duplicate the last board.
    rig.run_seconds(at(9, 20, 10), 50, feed=False)

    globals_ = rig.monitor.global_metrics()
    assert globals_["feed_health"] in {"TRANSPORT_STALE", "RECOVERY_PENDING"}
    assert globals_["stale_seconds"] > 0
    # Detection costs CAPTURE_STALE_SECONDS: the four seconds after the last tick are
    # still within tolerance and are legitimately written, then suppression begins.
    assert rig.engine.captures == 14
    assert rig.engine.stale_spell_ms(at(9, 20, 59)) >= 40_000

    # Past the deadline it escalates exactly once, for a clean restart.
    with pytest.raises(CaptureStalledError, match="stale for"):
        rig.run_seconds(at(9, 21, 15), 2, feed=False)
    assert rig.engine.escalations == 1

    # Writers still drain safely on the way out; the archive holds only real seconds —
    # the 10 healthy ones plus the 4 inside the detection tolerance.
    rig.stop()
    scan = scan_frames(rig.index_path, collect_timestamps=True)
    assert scan.frames == 14
    assert scan.timestamps == tuple(at(9, 20) + s * INTERVAL_MS for s in range(14))


def test_the_outage_gap_stays_auditable_from_the_archive_alone(rig):
    rig.run_seconds(at(9, 20), 10, feed=True)
    rig.run_seconds(at(9, 20, 10), 30, feed=False)
    rig.run_seconds(at(9, 20, 40), 20, feed=True)
    rig.stop()

    # Reconcile the schedule against the frames on disk — no telemetry consulted.
    scan = scan_frames(rig.index_path, collect_timestamps=True)
    scheduled = [(at(9, 20), at(9, 21))]  # the 60 seconds the drill covered
    result = reconcile(scheduled, list(scan.timestamps))

    assert result.scheduled_seconds == 60
    # 10 healthy seconds + the 4 within the staleness tolerance + 20 after recovery.
    assert result.captured_seconds == 34
    assert result.missing_seconds == 26
    assert len(result.gaps) == 1
    assert result.gaps[0].start_ms == at(9, 20, 14)
    assert result.gaps[0].seconds == 26
    assert result.reconciles


def test_a_restart_mid_session_counts_its_downtime_and_resumes_the_file(rig, tmp_path):
    rig.run_seconds(at(9, 20), 20, feed=True)
    rig.stop()  # the process dies here

    # A new process comes up two minutes later and resumes the same files.
    clock = Clock(at(9, 24))
    resumed = Rig(tmp_path, clock)
    resume = resumed.engine.resume_from_disk()
    resumed.start()
    try:
        resumed.run_seconds(at(9, 24), 20, feed=True)
    finally:
        resumed.stop()

    assert resume["resumed"] is True
    assert resume["frames_on_disk"] == 20
    # The gap is counted, not erased. Owed since the open is wall-clock derived, so it
    # includes the two minutes during which no process existed to count them.
    globals_ = resumed.monitor.global_metrics()
    owed = (at(9, 24, 20) - at(9, 15)) // 1000
    assert globals_["scheduled_seconds_elapsed"] == owed
    assert globals_["captured_seconds"] == 40  # 20 before the restart + 20 after
    assert globals_["missing_seconds"] == owed - 40
    assert globals_["downtime_seconds"] > 0

    # Both runs' frames are in one file, and the hole between them is visible.
    scan = scan_frames(rig.index_path, collect_timestamps=True)
    assert scan.frames == 40
    assert scan.first_timestamp_ms == at(9, 20)
    assert scan.last_timestamp_ms == at(9, 24, 19)
    assert at(9, 22) not in scan.timestamps  # the downtime left a real hole


# --- 6. artifact-specific failure ---------------------------------------------


def test_one_frozen_dataset_is_artifact_level_and_never_restarts_capture(rig):
    rig.run_seconds(at(9, 20), 5, feed=True)

    # From here only STOCKS and the index-F&O FUTURE receive ticks; NIFTY goes quiet while
    # the transport stays perfectly healthy. Note the fno matrix also holds NIFTY's SPOT
    # token, which the option dataset owns too — ticking it would keep NIFTY fresh, so the
    # future's own token is used instead.
    fno_future_token = rig.fno.tokens[-1]
    for step in range(60):
        ts = at(9, 20, 5) + step * INTERVAL_MS
        rig.clock.advance_to(ts)
        batch = [
            {"instrument_token": rig.matrix.tokens[0], "last_price": 200.0 + step},
            {"instrument_token": fno_future_token, "last_price": 300.0 + step},
        ]
        rig.engine.apply_ticks(batch, ts)
        rig.engine.stall.mark_message(ts)
        rig.engine.freshness.observe(batch, ts)
        rig.engine.observe_feed_health(ts)  # must not raise
    rig.stop()

    now = at(9, 21, 4)
    assert rig.engine.escalations == 0  # the working datasets keep running
    assert rig.engine.stale_artifact_names(now) == ("NIFTY",)
    assert rig.engine.feed_health(now) == "ARTIFACT_STALE"
    entries = {e["underlying"]: e for e in rig.monitor.per_underlying()}
    assert entries["NIFTY"]["artifact_stale"] is True
    assert entries["STOCKS"]["artifact_stale"] is False
    assert entries["INDICES_FnO"]["artifact_stale"] is False


# --- 7. brief recovery flicker ------------------------------------------------


def test_a_single_transient_tick_does_not_erase_a_sustained_stale_spell(rig):
    rig.run_seconds(at(9, 20), 5, feed=True)
    rig.run_seconds(at(9, 20, 5), 30, feed=False)  # outage begins
    spell_before = rig.engine.stale_spell_ms(at(9, 20, 34))
    assert spell_before >= 25_000

    # One lonely batch arrives, then silence resumes.
    rig.run_seconds(at(9, 20, 35), 1, feed=True)
    rig.run_seconds(at(9, 20, 36), 5, feed=False)

    # The spell is still measured from its original start, so the deadline is not reset.
    assert rig.engine.stale_spell_ms(at(9, 20, 40)) > spell_before
    assert rig.engine.escalations == 0  # not yet past the deadline

    with pytest.raises(CaptureStalledError):
        rig.run_seconds(at(9, 21, 10), 2, feed=False)


# --- 8. normal close ----------------------------------------------------------


def test_the_close_drains_finalises_and_stops_counting_loss(rig):
    rig.run_seconds(at(15, 29), 30, feed=True)  # the last half minute of trading
    captured_at_close = rig.engine.captures

    # After 15:30 nothing is owed. Long inactivity must not accumulate loss or restart.
    rig.run_seconds(at(15, 30), 300, feed=False)
    rig.stop()

    globals_ = rig.monitor.global_metrics()
    assert globals_["market_phase"] == "CLOSED"
    assert globals_["capture_expected"] is False
    assert globals_["feed_health"] == "INACTIVE"
    assert rig.engine.captures == captured_at_close  # nothing written after the close
    assert rig.engine.escalations == 0
    assert rig.engine.unscheduled_seconds == 300
    assert rig.engine.stale_spell_ms(at(15, 35)) == 0

    # The full session is accounted for: 22,500 scheduled seconds, 30 captured, and the
    # rest is honest loss — no post-close second inflates the denominator.
    assert globals_["scheduled_seconds"] == 22_500
    assert globals_["scheduled_seconds_elapsed"] == 22_500
    assert globals_["captured_seconds"] == 30
    assert globals_["missing_seconds"] == 22_470
    breakdown = (
        globals_["stale_feed_seconds"]
        + globals_["downtime_seconds"]
        + globals_["write_path_seconds"]
        + globals_["unclassified_seconds"]
    )
    assert breakdown == globals_["missing_seconds"]

    # Final persistence completed for every domain.
    for path in (rig.index_path, rig.stock_path, rig.fno_path):
        assert scan_frames(path).frames == 30

    # Attribution from the archive agrees: the missing time is downtime, not fabrication.
    scan = scan_frames(rig.index_path, collect_timestamps=True)
    attributed = classify_downtime(
        reconcile([(at(9, 15), at(15, 30))], list(scan.timestamps)),
        [(at(15, 29), at(15, 29, 30))],
    )
    assert attributed.captured_seconds == 30
    assert attributed.missing_seconds == 22_470
    assert attributed.reconciles
