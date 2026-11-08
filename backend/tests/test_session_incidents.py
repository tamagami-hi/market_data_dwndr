"""The incident ledger and the session outcome it explains.

Centre piece: a replay of 2026-11-05 through the real controller hooks. The persisted
record that day read "3032 s not running, escalations 0, generations 2". The replay
asserts the record now says what happened: a 51-minute restart/connect-failure loop with
every index chain skipped, two escalations, and 104 process starts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.capture import CaptureController
from app.capture.engine import CaptureStalledError
from app.kite.ticker import TickerConnectFailed
from app.ops import incidents, market_overrides, stats_store
from app.ops.calendar import IST_FALLBACK
from app.ops.incident_timeline import analyze_incidents
from app.ops.market_overrides import MarketOverrideStore, SpecialSession
from app.ops.session_outcome import (
    OUTCOME_COMPLETE,
    OUTCOME_DEGRADED,
    OUTCOME_FAILED,
    OUTCOME_NOT_CAPTURED,
    OUTCOME_NOT_SCHEDULED,
    OUTCOME_PARTIAL,
    classify_outcome,
    describe_session,
)
from app.ops.session_report import describe_rows, session_window_for

DAY = "2026-11-05"
INDICES = ["BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"]


def _t(hour: int, minute: int, second: int = 0, day: int = 5) -> int:
    return int(
        datetime(2026, 11, day, hour, minute, second, tzinfo=IST_FALLBACK).timestamp() * 1000
    )


WINDOW = {"windows": [[_t(9, 15), _t(15, 40)]], "scheduled_seconds": 23_100}

# The persisted 2026-11-05 row, as GET /api/stats returned it (streams trimmed).
PROD_SUMMARY = {
    "trading_date": DAY,
    "captures": 19_916,
    "frames_written": 159_328,
    "frames_expected": 184_800,
    "frame_loss_pct": 13.784,
    "session_frames_expected": 23_046,
    "session_loss_pct": 13.582,
    "grid_seconds_elapsed": 23_100,
    "data_loss_pct": 13.784,
    "grid_gaps": 2,
    "grid_seconds_lost": 0,
    "stale_seconds": 54,
    "escalations": 0,
    "scheduled_seconds": 23_100,
    "scheduled_seconds_elapsed": 23_100,
    "captured_seconds": 19_916,
    "scheduled_loss_pct": 13.784,
    "missing_seconds": 3_184,
    "stale_feed_seconds": 54,
    "downtime_seconds": 3_130,
    "write_path_seconds": 0,
    "unclassified_seconds": 0,
    "awaiting_first_tick_seconds": 0,
    "restart_downtime_seconds": 98,
    "not_running_seconds": 3_032,
}


# --- the replay -----------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now


def _settings(stats_dir):
    return SimpleNamespace(
        stats_dir=stats_dir,
        timezone="Asia/Kolkata",
        market_open="09:10",
        market_close="15:40",
        equity_deriv_open="09:15",
        equity_deriv_close="15:40",
        equity_cash_open="09:15",
        equity_cash_close="15:15",
        market_holidays=[],
    )


def _process(settings, clock, *, tokens, skipped=(), run_fn, feed_live, summary=None):
    """One backend process: ``process_start`` plus a controller wired like production."""
    incidents.record_incident(settings.stats_dir, DAY, incidents.KIND_PROCESS_START, clock())
    context = SimpleNamespace(
        trading_date=DAY,
        index_tables={name: object() for name in INDICES if name not in skipped},
        stock_matrix=None,
        tokens=list(range(tokens)),
        skipped_indices=list(skipped),
        skip_reasons={name: "spot must be > 0" for name in skipped},
        quote_error=(
            "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): "
            "Temporary failure in name resolution (<urllib3 object at 0x7f3a2b1c9d40>)"
            if skipped
            else None
        ),
        resume={"resumed": True, "frames_on_disk": 4_000, "downtime_seconds": 40},
        monitor=SimpleNamespace(
            session_summary=lambda d: dict(summary or {"trading_date": d, "captures": 1})
        ),
        bridge=SimpleNamespace(connects_confirmed=1 if feed_live else 0),
    )
    return CaptureController(
        settings,
        SimpleNamespace(
            active_session=lambda: SimpleNamespace(access_token="T", risk_free_rate=0.07),
            trading_date=lambda: DAY,
        ),
        hub=None,
        bootstrap_fn=lambda *_a, **_k: context,
        run_fn=run_fn,
        fatal_handler=lambda: None,
        clock=clock,
    )


async def _settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


async def _replay_2026_11_05(tmp_path) -> SimpleNamespace:
    settings, clock = _settings(tmp_path), _Clock()
    stall = asyncio.Event()

    async def run_until_stall(_ctx, _stop) -> None:
        await stall.wait()
        raise CaptureStalledError("ticker could not establish a confirmed subscription")

    async def connect_fails(_ctx, _stop) -> None:
        raise TickerConnectFailed("ticker did not confirm a live subscription within 15s")

    async def run_until_stopped(_ctx, stop) -> None:
        await stop.wait()

    # 09:05 boot, 09:10 capture with every chain; 10:23 the feed dies -> restart.
    clock.now = _t(9, 5)
    first = _process(settings, clock, tokens=2034, run_fn=run_until_stall, feed_live=True)
    clock.now = _t(9, 10)
    await first.start()
    clock.now = _t(10, 23)
    stall.set()
    await _settle()

    # 10:23:30-11:13:25: DNS is down. Every process skips all six chains, starts with 891
    # tokens and dies 15 s later on a ticker that never connects. ~100 times.
    for attempt in range(100):
        boot = _t(10, 23, 30) + attempt * 30_000
        clock.now = boot
        looping = _process(
            settings, clock, tokens=891, skipped=INDICES, run_fn=connect_fails, feed_live=False
        )
        clock.now = boot + 10_000
        await looping.start()
        clock.now = boot + 25_000
        await _settle()

    # 11:14 the network is back: full capture until the 14:10 stall.
    stall.clear()
    clock.now = _t(11, 14)
    recovered = _process(settings, clock, tokens=2034, run_fn=run_until_stall, feed_live=True)
    clock.now = _t(11, 14, 10)
    await recovered.start()
    clock.now = _t(14, 10)
    stall.set()
    await _settle()

    # 14:10:30 restart, back in 15 s with 2000 tokens; market close at 15:40.
    clock.now = _t(14, 10, 30)
    final_summary = {k: v for k, v in PROD_SUMMARY.items() if k != "escalations"}
    final_summary["escalations"] = 0  # what the engine reported that day
    last = _process(
        settings, clock, tokens=2000, run_fn=run_until_stopped, feed_live=True,
        summary=final_summary,
    )
    clock.now = _t(14, 10, 45)
    await last.start()
    clock.now = _t(15, 40)
    await last.stop(reason=incidents.STOP_REASON_MARKET_CLOSE)

    # 15:42 EOD with archive reconciliation; 18:53 a stray restart.
    stats_store.record_reconciliation(
        tmp_path,
        {
            "trading_date": DAY,
            "source": "archive",
            "data_loss_pct": 13.79,
            "worst_artifact": "NIFTY",
            "artifact_count": 8,
            "artifacts": {"NIFTY": {"scheduled_seconds": 23_100, "captured_seconds": 19_914,
                                    "missing_seconds": 3_186, "gap_count": 3}},
        },
    )
    incidents.record_incident(
        tmp_path, DAY, incidents.KIND_EOD, _t(15, 42), status="ok", files=8,
        archive_data_loss_pct=13.79, worst_artifact="NIFTY", artifact_count=8,
    )
    incidents.record_incident(tmp_path, DAY, incidents.KIND_PROCESS_START, _t(18, 53))
    [row] = describe_rows(
        stats_store.session_history(tmp_path), settings=settings, stats_dir=tmp_path,
        now_ms=_t(23, 0),
    )
    return SimpleNamespace(row=row, ledger=incidents.load_incidents(tmp_path, DAY))


async def test_replay_attributes_the_51_minutes_to_a_restart_loop(tmp_path):
    replay = await _replay_2026_11_05(tmp_path)
    row = replay.row

    assert row["outcome"] == OUTCOME_PARTIAL
    assert row["completeness_pct"] == pytest.approx(86.216, abs=0.001)
    causes = row["loss_causes"]
    assert causes["restart_loop_seconds"] == 3_070  # 10:23:00 -> 11:14:10
    assert causes["restart_seconds"] == 45  # 14:10:00 -> 14:10:45
    assert causes["stale_feed_seconds"] == 54
    assert sum(causes.values()) == row["missing_seconds"] == 3_184

    loop = next(o for o in row["outages"] if o["cause"] == "restart_loop")
    assert loop["start_at"] == _t(10, 23)
    assert loop["end_at"] == _t(11, 14, 10)
    assert loop["failed_starts"] == 100
    assert loop["exceptions"] == ["TickerConnectFailed"]
    assert loop["trigger"] == "CaptureStalledError"
    assert loop["skipped_indices"] == INDICES
    assert any("restart loop 10:23-11:14" in line for line in row["explanation"])


async def test_replay_counts_processes_escalations_and_skipped_datasets(tmp_path):
    row = (await _replay_2026_11_05(tmp_path)).row

    assert row["process_starts"] == 104
    assert row["capture_starts"] == 103
    assert row["generations"] == 103
    assert row["failed_starts"] == 100
    assert row["process_exits"] == 102
    assert row["escalations"] >= 2
    assert row["escalations"] == 2
    missing = {d["dataset"]: d for d in row["datasets_missing"]}
    assert sorted(missing) == INDICES
    assert missing["NIFTY"]["seconds"] == 3_030  # 10:23:40 -> 11:14:10
    assert missing["NIFTY"]["starts"] == 100
    assert missing["NIFTY"]["reason"] == "spot must be > 0"
    assert "name resolution" in missing["NIFTY"]["quote_error"]
    assert "0x7f3a" not in missing["NIFTY"]["quote_error"], "addresses are normalised"
    assert row["eod"]["status"] == "ok" and row["eod_runs"] == 1
    assert row["archive_completeness_pct"] == pytest.approx(86.21)
    assert row["session_window"]["open"] == "09:15"
    assert row["session_window"]["is_special"] is False


async def test_replay_ledger_collapses_the_loop_without_losing_a_single_count(tmp_path):
    ledger = (await _replay_2026_11_05(tmp_path)).ledger

    assert len(ledger["entries"]) < 20, "100 restarts must not become 300 entries"
    assert ledger["counts"]["process_start"] == 104
    assert ledger["counts"]["capture_failed"] == 102
    loop = next(
        e for e in ledger["entries"]
        if e["kind"] == "capture_failed" and e["exception"] == "TickerConnectFailed"
    )
    assert loop["count"] == 100
    assert loop["first_at"] == _t(10, 23, 55)
    assert loop["last_at"] == _t(11, 13, 25)
    assert loop["feed_live"] is False and loop["process_exit"] is True


# --- ledger mechanics -----------------------------------------------------------------


def test_distinct_incidents_are_never_dropped(tmp_path):
    for i in range(600):
        incidents.append_incident(
            tmp_path, DAY, incidents.KIND_START_FAILED, i * COLLAPSE_GAP, exception=f"E{i}"
        )
    ledger = incidents.load_incidents(tmp_path, DAY)
    assert len(ledger["entries"]) == 600
    assert ledger["entries"][0]["exception"] == "E0"


COLLAPSE_GAP = incidents.COLLAPSE_WINDOW_MS + 1


def test_repeats_far_apart_stay_separate_incidents(tmp_path):
    incidents.append_incident(tmp_path, DAY, incidents.KIND_CAPTURE_FAILED, _t(10, 23),
                              exception="CaptureStalledError", feed_live=True, process_exit=True)
    incidents.append_incident(tmp_path, DAY, incidents.KIND_CAPTURE_FAILED, _t(14, 10),
                              exception="CaptureStalledError", feed_live=True, process_exit=True)
    entries = incidents.load_incidents(tmp_path, DAY)["entries"]
    assert [e["count"] for e in entries] == [1, 1]


def test_reasons_are_redacted_and_bounded():
    text = incidents.sanitize_reason(
        "GET /quote?api_key=abc123&access_token=SECRET obj at 0xdeadbeef01 " + "x" * 500
    )
    assert "SECRET" not in text and "abc123" not in text
    assert "0xdeadbeef01" not in text
    assert len(text) <= incidents.REASON_MAX_CHARS


def test_recording_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    incidents.record_incident(blocker, DAY, incidents.KIND_PROCESS_START, 1)  # no raise
    incidents.record_incident(None, DAY, incidents.KIND_PROCESS_START, 1)


def test_ledger_round_trips_as_json(tmp_path):
    incidents.append_incident(tmp_path, DAY, incidents.KIND_CAPTURE_START, 1,
                              tokens=5, skipped_indices=("NIFTY",), quote_error_type=None)
    raw = json.loads(incidents.incident_ledger_path(tmp_path, DAY).read_text())
    assert raw["entries"][0]["skipped_indices"] == ["NIFTY"]
    incidents.append_incident(tmp_path, DAY, incidents.KIND_CAPTURE_START, 2,
                              tokens=5, skipped_indices=["NIFTY"], quote_error_type=None)
    assert incidents.load_incidents(tmp_path, DAY)["entries"][0]["count"] == 2


# --- attribution ----------------------------------------------------------------------


def _ledger(*events):
    ledger = incidents.empty_ledger(DAY)
    for kind, at, fields in events:
        ledger = incidents.merge_incident(ledger, kind, at, fields)
    return ledger


def test_waiting_for_the_broker_token_is_its_own_cause():
    ledger = _ledger(
        (incidents.KIND_AUTH_STATE, _t(8, 31), {"state": "NO_SESSION", "error": None}),
        (incidents.KIND_AUTH_STATE, _t(9, 40), {"state": "OK", "error": None}),
        (incidents.KIND_CAPTURE_START, _t(9, 41), {"tokens": 2034, "skipped_indices": []}),
    )
    analysis = analyze_incidents(ledger, [(_t(9, 15), _t(15, 40))], _t(23, 0))
    assert analysis["cause_seconds"]["awaiting_token"] == 25 * 60
    assert analysis["cause_seconds"]["process_down"] == 60


def test_a_maintenance_window_is_attributed_to_maintenance():
    ledger = _ledger(
        (incidents.KIND_CAPTURE_START, _t(9, 10), {"tokens": 2034, "skipped_indices": []}),
        (incidents.KIND_MAINTENANCE_ACQUIRED, _t(12, 0), {"expires_at": _t(12, 15)}),
        (incidents.KIND_CAPTURE_STOPPED, _t(12, 0), {"reason": "maintenance", "clean": True}),
        (incidents.KIND_MAINTENANCE_RELEASED, _t(12, 5), {}),
        (incidents.KIND_PROCESS_START, _t(12, 5, 30), {}),
        (incidents.KIND_CAPTURE_START, _t(12, 6), {"tokens": 2034, "skipped_indices": []}),
    )
    analysis = analyze_incidents(ledger, [(_t(9, 15), _t(15, 40))], _t(23, 0))
    assert analysis["cause_seconds"]["maintenance"] == 360
    assert analysis["outages"][0]["cause"] == "maintenance"


def test_a_dataset_skipped_all_day_degrades_an_otherwise_complete_day():
    ledger = _ledger(
        (incidents.KIND_CAPTURE_START, _t(9, 10), {"tokens": 1900, "skipped_indices": ["NIFTY"],
                                                   "skip_reasons": {"NIFTY": "spot must be > 0"}}),
        (incidents.KIND_CAPTURE_STOPPED, _t(15, 40), {"reason": "market_close", "clean": True}),
    )
    summary = {"trading_date": DAY, "scheduled_seconds": 23_100,
               "scheduled_seconds_elapsed": 23_100, "captured_seconds": 23_100}
    row = describe_session(summary, ledger=ledger, window=WINDOW, now_ms=_t(23, 0))
    assert row["completeness_pct"] == 100.0
    assert row["outcome"] == OUTCOME_DEGRADED
    assert row["datasets_missing"][0]["seconds"] == 23_100


def test_without_a_ledger_engine_buckets_are_kept_and_counts_are_unknown():
    row = describe_session(PROD_SUMMARY, window=WINDOW, now_ms=_t(23, 0))
    assert row["incidents_recorded"] is False
    assert row["process_starts"] is None
    assert row["loss_causes"]["restart_seconds"] == 98
    assert row["loss_causes"]["process_down_seconds"] == 3_032


def test_a_worse_archive_figure_decides_the_outcome():
    summary = {"trading_date": DAY, "scheduled_seconds": 23_100,
               "scheduled_seconds_elapsed": 23_100, "captured_seconds": 23_100}
    reconciliation = {"trading_date": DAY, "data_loss_pct": 7.5, "worst_artifact": "STOCKS",
                      "artifact_count": 8, "artifacts": {}}
    row = describe_session(summary, reconciliation=reconciliation, window=WINDOW,
                           now_ms=_t(23, 0))
    assert row["outcome"] == OUTCOME_PARTIAL
    assert row["outcome_basis"] == "archive"
    assert row["archive_completeness_pct"] == 92.5


def test_an_unfinished_day_is_measured_against_elapsed_time():
    summary = {"trading_date": DAY, "scheduled_seconds": 23_100,
               "scheduled_seconds_elapsed": 3_600, "captured_seconds": 3_600}
    row = describe_session(summary, window=WINDOW, now_ms=_t(10, 15))
    assert row["final"] is False
    assert row["completeness_basis_seconds"] == 3_600
    assert row["outcome"] == OUTCOME_COMPLETE


@pytest.mark.parametrize(
    ("pct", "captured", "scheduled", "expected"),
    [
        (100.0, 23_100, 23_100, OUTCOME_COMPLETE),
        (99.5, 22_985, 23_100, OUTCOME_COMPLETE),
        (99.49, 22_982, 23_100, OUTCOME_DEGRADED),
        (95.0, 21_945, 23_100, OUTCOME_DEGRADED),
        (94.9, 21_922, 23_100, OUTCOME_PARTIAL),
        (50.0, 11_550, 23_100, OUTCOME_PARTIAL),
        (49.9, 11_527, 23_100, OUTCOME_FAILED),
        (0.0, 0, 23_100, OUTCOME_NOT_CAPTURED),
        (None, 0, 0, OUTCOME_NOT_SCHEDULED),
    ],
)
def test_outcome_thresholds(pct, captured, scheduled, expected):
    assert classify_outcome(pct, scheduled_seconds=scheduled, captured_seconds=captured) == expected


# --- session window -------------------------------------------------------------------


def test_session_window_reports_the_regular_schedule(tmp_path):
    window = session_window_for(_settings(tmp_path), DAY)
    assert (window["open"], window["close"]) == ("09:15", "15:40")
    assert window["is_special"] is False
    assert window["scheduled_seconds"] == 23_100
    assert window["windows"] == [[_t(9, 15), _t(15, 40)]]


def test_session_window_reports_an_operator_special_session(tmp_path):
    store = MarketOverrideStore(tmp_path / "market_calendar.json")
    market_overrides.install(store)
    try:
        store.save(store.load().with_special(
            SpecialSession.create("2026-11-08", "14:20", "15:30", "13:00", 5, "Diwali muhurat")
        ))
        window = session_window_for(_settings(tmp_path), "2026-11-08")
    finally:
        market_overrides.install(None)
    assert window["is_special"] is True
    assert window["note"] == "Diwali muhurat"
    assert (window["open"], window["close"]) == ("14:20", "15:30")
    assert window["scheduled_seconds"] == 70 * 60


def test_a_restart_into_a_usable_session_ends_the_token_wait():
    """A new process starts in the OK auth state, so it never records "back to OK"."""
    ledger = _ledger(
        (incidents.KIND_CAPTURE_START, _t(9, 10), {"tokens": 2034, "skipped_indices": []}),
        (incidents.KIND_AUTH_STATE, _t(10, 0), {"state": "NO_SESSION", "error": None}),
        (incidents.KIND_CAPTURE_START, _t(10, 1), {"tokens": 2034, "skipped_indices": []}),
        (incidents.KIND_CAPTURE_FAILED, _t(12, 0),
         {"exception": "CaptureStalledError", "feed_live": True, "process_exit": True}),
        (incidents.KIND_CAPTURE_START, _t(12, 1), {"tokens": 2034, "skipped_indices": []}),
    )
    analysis = analyze_incidents(ledger, [(_t(9, 15), _t(15, 40))], _t(23, 0))
    assert analysis["cause_seconds"]["awaiting_token"] == 0
    assert analysis["cause_seconds"]["restart"] == 60
