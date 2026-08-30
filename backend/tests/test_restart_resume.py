"""Mid-session restart must preserve both the data and the reported stats.

The data half already worked (writers open ``"ab"`` and only write a header into an empty
file), but every counter the monitor shows lived in process memory, so a restart made a
healthy resumed session report near-total data loss.
"""

from __future__ import annotations

from app.bin_codec.reader import IndexBinReader
from app.bin_codec.scan import scan_frames
from app.bin_codec.writer import IndexBinWriter
from app.capture.engine import CaptureEngine, build_index_writer
from app.capture.monitor import CaptureMonitor
from app.capture.writer_thread import FileWriterThread
from tests.test_capture import _nifty_table as nifty_table


def _write_frames(path, table, count, start_ts=1_000, step=1_000):
    """Append ``count`` frames the way a live capture session would."""
    writer = FileWriterThread(
        IndexBinWriter(path, sync=False),
        table.header(),
        name="idx",
        frames_on_disk=scan_frames(path).frames,
    )
    writer.start()
    writer.wait_until_ready()
    for i in range(count):
        writer.enqueue(table.snapshot(start_ts + i * step))
    writer.stop()
    return writer


def test_bin_file_appends_across_restarts_without_a_second_header(tmp_path):
    """Two writer generations against one path must accumulate, not truncate."""
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()

    first = _write_frames(path, table, 5, start_ts=1_000)
    assert first.frames_written == 5

    # Restart: a brand-new writer over the same path.
    second = _write_frames(path, table, 3, start_ts=6_000)

    # The file holds every frame from both generations, and stays readable — proving the
    # header was written exactly once.
    with IndexBinReader(path) as reader:
        assert len(reader) == 8
        assert reader.timestamps == [1_000, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000, 8_000]

    # And the second generation reports the DAY's total, not just its own 3 frames.
    assert second.frames_on_disk == 5
    assert second.frames_appended == 3
    assert second.frames_written == 8


def test_scan_frames_matches_the_reader(tmp_path):
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    _write_frames(path, table, 7, start_ts=5_000, step=1_000)

    scan = scan_frames(path)
    with IndexBinReader(path) as reader:
        assert scan.frames == len(reader)
        assert scan.first_timestamp_ms == reader.timestamps[0]
        assert scan.last_timestamp_ms == reader.timestamps[-1]
    assert scan.truncated is False


def test_scan_frames_on_missing_and_empty_files(tmp_path):
    assert scan_frames(tmp_path / "nope.bin").frames == 0
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert scan_frames(empty).exists is False


def test_scan_frames_tolerates_a_torn_trailing_frame(tmp_path):
    """A process killed between the length prefix and the payload must not break resume."""
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    _write_frames(path, table, 4)
    intact = scan_frames(path)

    with open(path, "ab") as handle:
        handle.write((999_999).to_bytes(4, "little"))  # a length with no payload

    torn = scan_frames(path)
    assert torn.frames == intact.frames, "complete frames must still be counted"
    assert torn.truncated is True


def test_engine_resume_restores_frames_first_timestamp_and_carried_counters(tmp_path):
    """The whole point: after a restart the monitor reports the day, not the process."""
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    session_start = 1_700_000_000_000
    _write_frames(path, table, 600, start_ts=session_start, step=1_000)

    # New process: writers rebuilt against the existing file.
    writer = build_index_writer(table, path)
    assert writer.frames_on_disk == 600, "must see the frames already on disk"

    now = session_start + 600_000 + 30_000  # 30s of downtime after the last frame
    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None, clock=lambda: now)
    assert engine.captures == 0 and engine.first_capture_ms is None

    result = engine.resume_from_disk(
        {"grid_gaps": 2, "grid_seconds_lost": 7, "stale_seconds": 3}
    )

    assert result["resumed"] is True
    assert result["frames_on_disk"] == 600
    assert engine.captures == 600
    assert engine.first_capture_ms == session_start, "session start comes from the file"
    assert engine.stale_seconds == 3
    # One extra gap for the restart hole, on top of the 2 carried.
    assert engine.grid_gaps == 3
    # Restart downtime has its own counter. Folding it into ``grid_seconds_lost`` made the
    # dashboard label it "write fail", and double-counted the suppressed seconds already
    # held in ``stale_seconds``.
    assert engine.grid_seconds_lost == 7, "carried write-path loss must not absorb downtime"
    assert engine.restart_downtime_seconds == result["downtime_seconds"]
    # The grid resyncs to the *next* whole boundary after ``now`` (``run()`` computes
    # ``((now // interval) + 1) * interval``), so the second containing ``now`` is not
    # captured either and the hole is 31 s, not 30. The old ``- 1`` under-counted it.
    assert result["downtime_seconds"] == 31


def test_carried_counters_are_restored_with_max_not_assignment(tmp_path):
    """A throttled snapshot is a lower bound; it must never erase live accounting.

    The persisted snapshot is written at most once a minute, so on every escalation up to
    a minute of stale/gap accounting never reaches disk. Assigning it back on resume threw
    away whatever the new process had already counted — which is how a session with three
    restarts reported 67 stale seconds against a real ~105.
    """
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    _write_frames(path, table, 10, start_ts=1_700_000_000_000, step=1_000)
    writer = build_index_writer(table, path)
    engine = CaptureEngine(
        {"NIFTY": table}, None, {"NIFTY": writer}, None, clock=lambda: 1_700_000_020_000
    )
    engine.stale_seconds = 40
    engine.grid_gaps = 5

    engine.resume_from_disk({"stale_seconds": 12, "grid_gaps": 1, "stale_events": 4})

    assert engine.stale_seconds == 40, "a smaller carried value must not overwrite"
    assert engine.stale_events == 4, "an absent live value still takes the carried one"
    assert engine.grid_gaps >= 5


def test_resume_is_a_no_op_on_a_fresh_day(tmp_path):
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    writer = build_index_writer(table, path)
    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None)

    result = engine.resume_from_disk(None)

    assert result == {"resumed": False, "frames_on_disk": 0}
    assert engine.captures == 0
    assert engine.first_capture_ms is None
    assert engine.grid_seconds_lost == 0


def test_resumed_session_does_not_report_bogus_data_loss(tmp_path):
    """Regression: the symptom the user saw — stats vanishing on a mid-session restart."""
    path = tmp_path / "NIFTY" / "2026-07-28.bin"
    table = nifty_table()
    session_start = 1_700_000_000_000
    _write_frames(path, table, 3_600, start_ts=session_start, step=1_000)  # 1h captured

    writer = build_index_writer(table, path)
    now = session_start + 3_600_000 + 2_000
    engine = CaptureEngine({"NIFTY": table}, None, {"NIFTY": writer}, None, clock=lambda: now)
    engine.resume_from_disk(None)

    monitor = CaptureMonitor(
        {"NIFTY": table}, None, {"NIFTY": writer}, None,
        engine=engine, clock=lambda: now,
        capture_start_ms=engine.first_capture_ms,
    )
    entry = monitor.snapshot()["payload"]["per_underlying"][0]

    assert entry["frames_written"] == 3_600, "the hour already on disk must be reported"
    # Elapsed-based loss stays near zero: we captured ~3600 of ~3601 elapsed seconds.
    assert entry["session_loss_pct"] < 1.0, (
        f"a healthy resumed session reported {entry['session_loss_pct']}% loss"
    )
