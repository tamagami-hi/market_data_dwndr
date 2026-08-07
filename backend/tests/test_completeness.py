"""Tests for archive-derived session completeness (loss accounting that survives a crash)."""

from __future__ import annotations

from app.ops.completeness import (
    CAUSE_DOWNTIME,
    CAUSE_STALE,
    CAUSE_UNCLASSIFIED,
    classify_downtime,
    reconcile,
)

SECOND = 1_000


def window(start_s: int, end_s: int) -> tuple[int, int]:
    return (start_s * SECOND, end_s * SECOND)


def frames(*ranges: tuple[int, int]) -> list[int]:
    out: list[int] = []
    for start_s, end_s in ranges:
        out.extend(second * SECOND for second in range(start_s, end_s))
    return out


def test_a_fully_captured_session_reports_no_loss():
    result = reconcile([window(0, 100)], frames((0, 100)))

    assert result.scheduled_seconds == 100
    assert result.captured_seconds == 100
    assert result.missing_seconds == 0
    assert result.data_loss_pct == 0.0
    assert result.gaps == ()
    assert result.reconciles


def test_a_session_with_nothing_captured_is_total_loss():
    """§17.6: capture never started, so there is no earlier frame to compare against."""
    result = reconcile([window(0, 100)], [])

    assert result.missing_seconds == 100
    assert result.data_loss_pct == 100.0
    assert len(result.gaps) == 1
    assert result.gaps[0].seconds == 100


def test_an_interior_hole_is_found_from_the_archive_alone():
    """No telemetry involved: the frames on disk are the evidence."""
    result = reconcile([window(0, 100)], frames((0, 40), (70, 100)))

    assert result.captured_seconds == 70
    assert result.missing_seconds == 30
    assert [(gap.start_ms, gap.end_ms) for gap in result.gaps] == [(40 * SECOND, 70 * SECOND)]
    assert result.reconciles


def test_several_holes_are_reported_separately():
    result = reconcile([window(0, 100)], frames((0, 10), (20, 30), (40, 100)))

    assert [gap.seconds for gap in result.gaps] == [10, 10]
    assert result.missing_seconds == 20


def test_frames_outside_the_schedule_are_ignored_not_credited():
    """A frame written after the close does not make an in-session second complete."""
    result = reconcile([window(0, 100)], frames((0, 50)) + frames((200, 260)))

    assert result.captured_seconds == 50
    assert result.missing_seconds == 50


def test_unscheduled_time_never_appears_in_the_denominator():
    """Only scheduled seconds participate: pre-open/after-close cannot be loss."""
    result = reconcile([window(100, 200)], frames((100, 200)))

    assert result.scheduled_seconds == 100
    assert result.missing_seconds == 0


def test_a_disabled_session_schedules_nothing_and_cannot_lose_data():
    result = reconcile([], [])

    assert result.scheduled_seconds == 0
    assert result.missing_seconds == 0
    assert result.data_loss_pct == 0.0
    assert result.captured_pct == 100.0


def test_multiple_scheduled_windows_are_summed():
    """A session that captures pre-open has two windows."""
    result = reconcile([window(0, 10), window(20, 40)], frames((0, 10), (20, 30)))

    assert result.scheduled_seconds == 30
    assert result.captured_seconds == 20
    assert result.missing_seconds == 10


# --- cause attribution --------------------------------------------------------


def test_a_stale_window_attributes_its_gap_to_the_feed():
    result = reconcile(
        [window(0, 100)], frames((0, 40), (70, 100)), stale_windows=[window(40, 70)]
    )

    assert result.by_cause()[CAUSE_STALE] == 30
    assert result.by_cause()[CAUSE_UNCLASSIFIED] == 0
    assert result.reconciles


def test_a_gap_with_no_explanation_stays_unclassified_rather_than_guessed():
    result = reconcile([window(0, 100)], frames((0, 40), (70, 100)))

    assert result.by_cause()[CAUSE_UNCLASSIFIED] == 30
    assert result.by_cause()[CAUSE_STALE] == 0
    assert result.reconciles


def test_one_gap_split_between_stale_and_unexplained_seconds():
    """Causes must not bleed across each other: the run breaks where the cause changes."""
    result = reconcile(
        [window(0, 100)], frames((0, 40), (80, 100)), stale_windows=[window(40, 60)]
    )

    causes = result.by_cause()
    assert causes[CAUSE_STALE] == 20
    assert causes[CAUSE_UNCLASSIFIED] == 20
    assert causes[CAUSE_STALE] + causes[CAUSE_UNCLASSIFIED] == result.missing_seconds


def test_downtime_is_attributed_when_the_process_was_not_running():
    """§17.5/§17.8: crash, reboot or a deliberate stop all read the same from the archive."""
    result = reconcile([window(0, 100)], frames((0, 40), (70, 100)))
    # The process is known to have been up only for the periods it wrote frames.
    attributed = classify_downtime(result, [window(0, 40), window(70, 100)])

    assert attributed.by_cause()[CAUSE_DOWNTIME] == 30
    assert attributed.by_cause()[CAUSE_UNCLASSIFIED] == 0
    assert attributed.missing_seconds == 30
    assert attributed.reconciles


def test_downtime_classification_leaves_stale_gaps_alone():
    result = reconcile(
        [window(0, 100)], frames((0, 40), (70, 100)), stale_windows=[window(40, 70)]
    )
    attributed = classify_downtime(result, [window(0, 100)])

    assert attributed.by_cause()[CAUSE_STALE] == 30
    assert attributed.by_cause()[CAUSE_DOWNTIME] == 0


def test_a_gap_inside_a_known_uptime_window_is_not_downtime():
    """The process was up, so something else lost those seconds — do not mislabel it."""
    result = reconcile([window(0, 100)], frames((0, 40), (70, 100)))
    attributed = classify_downtime(result, [window(0, 100)])

    assert attributed.by_cause()[CAUSE_DOWNTIME] == 0
    assert attributed.by_cause()[CAUSE_UNCLASSIFIED] == 30


def test_the_loss_invariant_holds_for_a_messy_session():
    """§17.15: every scheduled second is either a frame or accounted-for loss."""
    result = reconcile(
        [window(0, 300)],
        frames((0, 50), (60, 200), (250, 300)),
        stale_windows=[window(50, 60)],
    )
    attributed = classify_downtime(result, [window(0, 200)])

    assert attributed.scheduled_seconds == 300
    assert attributed.captured_seconds == 240
    assert attributed.missing_seconds == 60
    causes = attributed.by_cause()
    assert causes[CAUSE_STALE] == 10
    assert causes[CAUSE_DOWNTIME] == 50
    assert sum(causes.values()) == attributed.missing_seconds
    assert attributed.reconciles
    assert attributed.captured_pct == 80.0
    assert attributed.data_loss_pct == 20.0



# --- end to end: a real .bin file is the only evidence ------------------------


def test_completeness_is_derived_from_a_real_bin_file_without_any_telemetry(tmp_path):
    """The point of §17.4/§17.14: reconstruct loss when telemetry never got written.

    Simulates a session that captured 09:15:00-09:15:39, then died (no final snapshot, no
    session summary), leaving 09:15:40-09:16:39 missing from a schedule that ran to
    09:16:39. Nothing but the file and the session config is consulted.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.bin_codec.scan import scan_frames
    from app.bin_codec.writer import IndexBinWriter
    from tests.test_capture import _nifty_table

    ist = ZoneInfo("Asia/Kolkata")
    open_ms = int(datetime(2026, 8, 10, 9, 15, tzinfo=ist).timestamp() * 1000)

    table = _nifty_table()

    path = tmp_path / "NIFTY" / "2026-08-10.bin"
    writer = IndexBinWriter(path, sync=False)
    writer.open()
    writer.write_header(table.header())
    for offset in range(40):  # 09:15:00 .. 09:15:39 inclusive
        writer.append_frame(table.snapshot(open_ms + offset * SECOND))
    writer.close()

    scan = scan_frames(path, collect_timestamps=True)
    assert scan.frames == 40
    assert len(scan.timestamps) == 40

    scheduled = [(open_ms, open_ms + 100 * SECOND)]
    result = reconcile(scheduled, list(scan.timestamps))

    assert result.scheduled_seconds == 100
    assert result.captured_seconds == 40
    assert result.missing_seconds == 60
    assert result.data_loss_pct == 60.0
    assert len(result.gaps) == 1
    assert result.gaps[0].start_ms == open_ms + 40 * SECOND

    # With the archive as the only uptime evidence, the tail is downtime.
    attributed = classify_downtime(result, [(open_ms, open_ms + 40 * SECOND)])
    assert attributed.by_cause()[CAUSE_DOWNTIME] == 60
    assert attributed.reconciles
