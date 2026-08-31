"""Archive-derived completeness: the one loss figure a lost session cannot hide from.

Every live percentage is bounded by what the capture process itself observed, so an outage
that delays the start shrinks its own denominator. On 2026-08-28 capture began 3,239 s
late; the field labelled "data loss" read **0.77%** while 14.69% of the session was
missing. These tests pin the reconciliation that reads the owed grid from configuration and
the captured seconds from the frames on disk, so neither input depends on the process
having been alive.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.bin_codec.writer import IndexBinWriter, resolve_index_segment_path
from app.ops import stats_store
from app.ops.reconcile_session import (
    artifact_timestamps,
    reconcile_artifact,
    reconcile_session,
    segment_paths,
)

DAY_START = 1_788_000_000_000  # an arbitrary whole second
SESSION = ((DAY_START, DAY_START + 100_000),)  # 100 owed seconds


def _header() -> IndexHeader:
    return IndexHeader(
        trading_date="2026-08-28",
        underlying="NIFTY",
        expiry_date="2026-09-01",
        risk_free_rate=0.0526,
    )


def _axis(n: int = 5) -> np.ndarray:
    return np.arange(1_000_000, 1_000_000 + n * 5_000, 5_000, dtype=np.int64)


def _write(path, timestamps, n: int = 5) -> None:
    writer = IndexBinWriter(path)
    writer.open()
    writer.write_header(_header())
    for i, ts in enumerate(timestamps):
        writer.append_frame(
            IndexFrame(
                timestamp_unix_ms=ts,
                sequence=i,
                spot_price=1,
                vix=2,
                calls=RawBlock.zeros(n),
                strikes=_axis(n),
                puts=RawBlock.zeros(n),
            )
        )
    writer.close()


def test_a_complete_session_reconciles_to_zero_loss(tmp_path):
    path = tmp_path / "2026-08-28.bin"
    _write(path, [DAY_START + s * 1_000 for s in range(100)])

    result = reconcile_artifact(path, SESSION)

    assert result.scheduled_seconds == 100
    assert result.captured_seconds == 100
    assert result.missing_seconds == 0
    assert result.data_loss_pct == 0.0
    assert result.reconciles


def test_a_late_start_is_counted_in_full(tmp_path):
    """The 2026-08-28 shape in miniature: the hole is *before* the first frame.

    This is exactly what a process-observed denominator cannot see, because it begins at
    the first frame the process wrote.
    """
    path = tmp_path / "2026-08-28.bin"
    _write(path, [DAY_START + s * 1_000 for s in range(60, 100)])  # first 60 s missing

    result = reconcile_artifact(path, SESSION)

    assert result.captured_seconds == 40
    assert result.missing_seconds == 60
    assert result.data_loss_pct == 60.0
    assert len(result.gaps) == 1
    assert result.gaps[0].seconds == 60


def test_an_early_stop_is_counted_in_full(tmp_path):
    path = tmp_path / "2026-08-28.bin"
    _write(path, [DAY_START + s * 1_000 for s in range(70)])

    result = reconcile_artifact(path, SESSION)

    assert result.missing_seconds == 30
    assert result.data_loss_pct == 30.0


def test_interior_holes_are_found_and_sized(tmp_path):
    path = tmp_path / "2026-08-28.bin"
    kept = [s for s in range(100) if not (10 <= s < 20) and not (50 <= s < 55)]
    _write(path, [DAY_START + s * 1_000 for s in kept])

    result = reconcile_artifact(path, SESSION)

    assert result.missing_seconds == 15
    assert sorted(g.seconds for g in result.gaps) == [5, 10]


def test_frames_from_a_legacy_rolled_segment_are_credited(tmp_path):
    """Historical ``.pN`` segments must not read as data loss — the seconds are on disk.

    New index captures never roll (each frame carries its own strike axis), but days
    already on disk do have segments, so reconciliation still has to sum across them.
    """
    base = tmp_path / "2026-08-28.bin"
    _write(base, [DAY_START + s * 1_000 for s in range(50)], n=5)
    rolled = base.with_name("2026-08-28.p2.bin")
    _write(rolled, [DAY_START + s * 1_000 for s in range(50, 100)], n=4)

    assert [p.name for p in segment_paths(base)] == [
        "2026-08-28.bin",
        "2026-08-28.p2.bin",
    ]
    assert len(artifact_timestamps(base)) == 100

    result = reconcile_artifact(base, SESSION)
    assert result.missing_seconds == 0, "segments must be reconciled together"


def test_an_index_axis_change_stays_in_one_file(tmp_path):
    """The new contract: a different strike count appends, it does not roll."""
    base = tmp_path / "2026-08-28.bin"
    _write(base, [DAY_START + s * 1_000 for s in range(50)], n=5)
    assert resolve_index_segment_path(base) == base

    _write(base, [DAY_START + s * 1_000 for s in range(50, 100)], n=4)

    assert [p.name for p in segment_paths(base)] == ["2026-08-28.bin"]
    assert len(artifact_timestamps(base)) == 100
    assert reconcile_artifact(base, SESSION).missing_seconds == 0


def test_frames_outside_the_session_are_not_credited(tmp_path):
    """A frame at 15:35 does not make 15:29 complete."""
    path = tmp_path / "2026-08-28.bin"
    inside = [DAY_START + s * 1_000 for s in range(90)]
    outside = [DAY_START + 200_000 + s * 1_000 for s in range(20)]
    _write(path, inside + outside)

    result = reconcile_artifact(path, SESSION)
    assert result.captured_seconds == 90
    assert result.missing_seconds == 10


def test_the_session_figure_is_the_worst_artifact_not_the_average(tmp_path):
    """Pooling would dilute one dead dataset by the number of healthy ones."""
    healthy = tmp_path / "NIFTY" / "2026-08-28.bin"
    healthy.parent.mkdir(parents=True)
    _write(healthy, [DAY_START + s * 1_000 for s in range(100)])
    broken = tmp_path / "STOCKS" / "2026-08-28.bin"
    broken.parent.mkdir(parents=True)
    _write(broken, [DAY_START + s * 1_000 for s in range(25)])

    record = reconcile_session(
        {"NIFTY": healthy, "STOCKS": broken}, SESSION, trading_date="2026-08-28"
    )

    assert record["worst_artifact"] == "STOCKS"
    assert record["data_loss_pct"] == 75.0
    assert record["artifacts"]["NIFTY"]["data_loss_pct"] == 0.0
    assert record["source"] == "archive"


def test_a_missing_file_is_a_total_loss_not_an_absent_obligation(tmp_path):
    result = reconcile_artifact(tmp_path / "never-written.bin", SESSION)
    assert result.captured_seconds == 0
    assert result.missing_seconds == 100
    assert result.data_loss_pct == 100.0


def test_the_reconciliation_record_is_persisted_and_keyed_by_date(tmp_path):
    stats = tmp_path / "stats"
    stats_store.record_reconciliation(
        stats, {"trading_date": "2026-08-27", "data_loss_pct": 1.9}
    )
    stats_store.record_reconciliation(
        stats, {"trading_date": "2026-08-28", "data_loss_pct": 14.69}
    )
    # Re-recording a date replaces it rather than duplicating.
    stats_store.record_reconciliation(
        stats, {"trading_date": "2026-08-28", "data_loss_pct": 14.7}
    )

    history = stats_store.load_reconciliation_history(stats)
    assert [r["trading_date"] for r in history] == ["2026-08-27", "2026-08-28"]
    assert history[-1]["data_loss_pct"] == 14.7


def test_reconciliation_history_tolerates_a_corrupt_line(tmp_path):
    stats = tmp_path / "stats"
    stats_store.record_reconciliation(stats, {"trading_date": "2026-08-27"})
    path = stats_store.reconciliation_history_path(stats)
    path.write_text(path.read_text() + "{not json\n", encoding="utf-8")

    assert [r["trading_date"] for r in stats_store.load_reconciliation_history(stats)] == [
        "2026-08-27"
    ]


def test_scheduled_windows_on_returns_absolute_millisecond_windows():
    from app.ops.calendar import TradingCalendar
    from app.ops.sessions import MarketSession, parse_hhmm

    calendar = TradingCalendar(timezone_name="Asia/Kolkata")
    session = MarketSession(
        name="equity_deriv",
        open_at=parse_hhmm("09:15"),
        close_at=parse_hhmm("15:40"),
    )

    windows = session.scheduled_windows_on(calendar, date(2026, 8, 28))  # a Friday

    assert len(windows) == 1
    start, end = windows[0]
    assert (end - start) // 1_000 == 6 * 3_600 + 25 * 60 == 23_100

    # A Saturday owes nothing at all.
    assert session.scheduled_windows_on(calendar, date(2026, 8, 29)) == ()


def test_session_summary_merges_generations_instead_of_replacing_them(tmp_path):
    """A restarted day must not report the last generation's process-local zeros."""
    stats = tmp_path / "stats"
    stats_store.record_session_summary(
        stats,
        {"trading_date": "2026-08-28", "ticks_received": 21_000_000, "captures": 19_000},
    )
    stats_store.record_session_summary(
        stats,
        {"trading_date": "2026-08-28", "ticks_received": 2_277_452, "captures": 19_708},
    )

    history = stats_store.load_session_history(stats)
    assert len(history) == 1
    row = history[0]
    assert row["ticks_received"] == 21_000_000, "cumulative counters take the max"
    assert row["captures"] == 19_708, "day-level counters take the newest"
    assert row["generations"] == 2



def test_the_2026_08_28_session_reconciles_to_the_real_loss_not_the_reported_one(tmp_path):
    """The regression this whole change exists for, reproduced at production scale.

    On 2026-08-28 the broker served a token Kite refused from 08:30 until 10:08:49, so
    capture's first frame was at 10:08:59 — 3,239 s after the 09:15 open — and three
    escalation restarts then cost 47 + 49 + 57 = 153 s. Total missing: 3,392 of 23,100
    owed seconds = 14.68%.

    The dashboard's ``data_loss_pct`` reported **0.77%**, because its denominator
    (``last_grid_ms - first_grid_ms``) begins at the first frame the process wrote and so
    shrank to fit the outage. This asserts the archive-derived figure sees all of it.
    """
    open_ms = 1_788_000_000_000              # stands in for 09:15:00
    session = ((open_ms, open_ms + 23_100_000),)  # 23,100 owed seconds
    late_start_s = 3_239
    gaps = {  # (offset from the first frame, length) — the three restart holes
        3_239 + 4: 47,
        3_239 + 11_167: 49,
        3_239 + 16_672: 57,
    }

    captured = []
    skip_until = -1
    for second in range(late_start_s, 23_100):
        if second in gaps:
            skip_until = second + gaps[second]
        if second < skip_until:
            continue
        captured.append(open_ms + second * 1_000)

    path = tmp_path / "2026-08-28.bin"
    _write(path, captured, n=1)

    result = reconcile_artifact(path, session)

    assert result.scheduled_seconds == 23_100
    assert result.missing_seconds == late_start_s + sum(gaps.values()) == 3_392
    # Independently reproduces the deployed session-history row for 2026-08-28 to three
    # decimals: captures 19,708, missing 3,392, scheduled_loss_pct 14.684 — while the field
    # labelled "data loss" on that same session read 0.77%.
    assert result.captured_seconds == 19_708
    assert result.data_loss_pct == 14.684
    # The late start is one contiguous hole, and it dominates — which is the finding the
    # process-relative metric structurally could not surface.
    longest = max(g.seconds for g in result.gaps)
    assert longest == late_start_s
    assert len(result.gaps) == 1 + len(gaps)
    assert result.reconciles
