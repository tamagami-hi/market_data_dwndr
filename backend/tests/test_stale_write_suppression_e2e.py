"""End-to-end proof that only genuine market data reaches the ``.bin`` files.

The unit tests in ``test_data_loss_telemetry.py`` assert the engine's accounting. This
module asserts the *artifact*: it drives the real ``CaptureEngine.run`` loop with real
writer threads over real files, lets the fake uplink go silent mid-session, and then
decodes the files back with the production readers.

Why this matters more than the counters: the ``.bin`` layout is fixed-width, so a frame
built from duplicated last-known values is byte-indistinguishable from a real one. Frame
count, file size, cadence and "frame integrity" all still look perfect. Nothing in the
archive marks which prints never happened, so the corruption is permanent and silent. A
missing second is the strictly better failure — it is visible in the timestamps, counted
in the telemetry, and backfillable from the historical API.
"""

from __future__ import annotations

import asyncio

import pytest

from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.chain.assembler import VIX_TOKEN
from app.chain.config import get_index_config
from app.stocks.board import build_board
from app.stocks.matrix import StockMatrix
from tests.test_capture import _nifty_table, _sample_instruments

STALE_AFTER_MS = 3_000
INTERVAL_MS = 1_000


class FakeClock:
    """A manually advanced clock shared by the engine and its freshness monitor."""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


class ScriptedBridge:
    """A tick uplink driven by a script of per-second batches.

    ``None`` in the script means "this second delivers nothing at all" — the total-outage
    shape of the incident being guarded against (a half-open socket that keeps the
    connection nominally up while no ticks arrive).
    """

    def __init__(self, script: list[list[dict] | None]) -> None:
        self.script = script
        self.connected = True
        self.reconnects = 0
        self.dropped_batches = 0
        self.batches_received = 0
        self.ticks_received = 0
        self.token_refreshes = 0
        self.last_token_refresh_ms = None

    def reconnect(self) -> None:
        # A reconnect must not conjure data: the script still decides what arrives.
        self.reconnects += 1

    async def batches(self):  # pragma: no cover - driven by the loop below
        while True:
            await asyncio.sleep(3600)


def _spot_tick(price: float) -> dict:
    return {"instrument_token": get_index_config("NIFTY").spot_token, "last_price": price}


def _vix_tick(price: float) -> dict:
    return {"instrument_token": VIX_TOKEN, "last_price": price}


async def _drive(engine: CaptureEngine, bridge: ScriptedBridge, clock: FakeClock) -> None:
    """Run one grid second per scripted entry, exactly as ``run()`` sequences it.

    ``run()`` itself sleeps against wall-clock time, which a deterministic test cannot
    drive. This mirrors its per-iteration order — deliver batch, observe freshness,
    snapshot, then evaluate recovery — so the code under test is the real
    ``capture_snapshot`` write gate, not a reimplementation of it.
    """
    engine.freshness.start(clock.now)
    for batch in bridge.script:
        clock.now += INTERVAL_MS
        if batch is not None:
            bridge.batches_received += 1
            bridge.ticks_received += len(batch)
            engine.apply_ticks(batch)
            engine.stall.mark_message(clock.now)
            engine.freshness.observe(batch, clock.now)
        engine.capture_snapshot(clock.now)
        engine.observe_feed_health(clock.now)


@pytest.fixture
def rig(tmp_path):
    """A full capture rig: 1 index table + the stock matrix, both on real files."""
    table = _nifty_table()
    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-31")
    index_path = tmp_path / "INDICES" / "NIFTY" / "2026-07-31.bin"
    stock_path = tmp_path / "STOCKS" / "2026-07-31.bin"
    index_writer = build_index_writer(table, index_path)
    stock_writer = build_stock_writer(matrix, stock_path)
    clock = FakeClock()
    engine = CaptureEngine(
        {"NIFTY": table},
        matrix,
        {"NIFTY": index_writer},
        stock_writer,
        clock=clock,
        stale_after_ms=STALE_AFTER_MS,
    )
    engine.start_writers()
    yield engine, clock, index_path, stock_path
    engine.stop_writers()


def test_a_feed_outage_leaves_a_hole_not_fabricated_frames(rig):
    """The headline guarantee, verified against the decoded files.

    Script: 4 fresh seconds, a 10-second total outage, then 4 fresh seconds again. The
    last fresh content lands at second 4, so with a 3s tolerance seconds 5 and 6 are still
    considered fresh and written; from second 7 the feed is stale and nothing is written.
    """
    engine, clock, index_path, stock_path = rig
    start = clock.now
    fresh_a = [[_spot_tick(24_500 + i), _vix_tick(11.0 + i)] for i in range(4)]
    fresh_b = [[_spot_tick(24_600 + i), _vix_tick(12.0 + i)] for i in range(4)]
    bridge = ScriptedBridge([*fresh_a, *([None] * 10), *fresh_b])

    asyncio.run(_drive(engine, bridge, clock))
    engine.stop_writers()

    with IndexBinReader(index_path) as reader:
        timestamps = list(reader.timestamps)
        spots = [f.spot_price for f in reader.frames()]

    seconds = [(ts - start) // INTERVAL_MS for ts in timestamps]
    # 1-4 fresh, 5-6 inside the staleness tolerance, 7-14 suppressed, 15-18 fresh again.
    assert seconds == [1, 2, 3, 4, 5, 6, 15, 16, 17, 18]
    assert engine.stale_seconds == 8
    assert engine.stale_events == 1

    # The gap is a real discontinuity in the timeline, not a run of duplicates.
    assert timestamps[6] - timestamps[5] == 9 * INTERVAL_MS

    # Every frame after the outage carries the NEW spot, and no frame carries a value the
    # feed never sent. (The 2 tolerance frames legitimately repeat the last real print.)
    assert spots[:4] == [2_450_000, 2_450_100, 2_450_200, 2_450_300]
    assert spots[4:6] == [2_450_300] * 2
    assert spots[6:] == [2_460_000, 2_460_100, 2_460_200, 2_460_300]

    # The stock file must show exactly the same shape — suppression is per grid second,
    # not per stream, so the two files stay frame-aligned for reconstruction.
    with StockBinReader(stock_path) as reader:
        assert list(reader.timestamps) == timestamps


def test_a_session_that_never_receives_a_tick_writes_nothing(rig):
    """No ticks at all must produce a near-empty file, not a day of zero-filled frames.

    This is the 09:00 shape of the 2026-07-31 incident: the ticker connects, the grid
    starts, and nothing ever arrives. Previously this wrote a full session of frames
    holding the initial all-zero table, which is indistinguishable from real data — and
    which compressed 3.7x better than a normal day, the only trace it left.
    """
    engine, clock, index_path, _stock_path = rig
    bridge = ScriptedBridge([None] * 30)

    asyncio.run(_drive(engine, bridge, clock))
    engine.stop_writers()

    with IndexBinReader(index_path) as reader:
        written = len(reader)
    # Only the seconds before the staleness threshold trips may exist; 28 are suppressed.
    assert written == 2
    assert engine.stale_seconds == 28
    assert engine.captures == 2
    # And the outage was acted on rather than absorbed: it is tracked as one continuous
    # stale spell (measured from the first stale grid second), which is what
    # restart-first recovery escalates on.
    assert engine.stale_spell_ms(clock.now) == 27 * INTERVAL_MS
    assert engine.degraded is True


def test_stale_suppression_is_reflected_in_the_dashboard_metrics(rig):
    """The metrics must tell the truth about the file the test just decoded."""
    from app.capture.monitor import CaptureMonitor

    engine, clock, index_path, _stock_path = rig
    start = clock.now
    bridge = ScriptedBridge([
        *[[_spot_tick(24_500 + i)] for i in range(5)],
        *([None] * 15),
    ])
    asyncio.run(_drive(engine, bridge, clock))
    engine.stop_writers()

    with IndexBinReader(index_path) as reader:
        frames_on_disk = len(reader)

    monitor = CaptureMonitor(
        {"NIFTY": engine.index_tables["NIFTY"]},
        engine.stock_matrix,
        engine.index_writers,
        engine.stock_writer,
        engine=engine,
        clock=lambda: clock.now,
        expected_frames=23_400,
        capture_start_ms=start,
    )
    g = monitor.global_metrics()

    # 5 fresh + 2 tolerance seconds written, 13 suppressed, 20 elapsed.
    assert frames_on_disk == 7
    assert g["captures"] == 7
    assert g["stale_seconds"] == 13
    assert g["grid_seconds_elapsed"] == 20
    # The write path lost nothing, so gap-only loss stays clean...
    assert g["session_loss_pct"] == 0.0
    # ...while total data loss reports the 13 missing seconds: 13/20 = 65%.
    assert g["data_loss_pct"] == 65.0
    # The retired tiles are gone from the payload.
    assert "disk_runway_hours" not in g
    # Frame integrity now moves with the missing data instead of reading ~100%.
    assert g["frames_written"] == 14  # 7 frames x 2 streams


def test_the_file_itself_records_where_seconds_were_skipped(rig):
    """Suppression must leave the frame chain decodable and self-documenting.

    ``sequence`` advances once per grid second the loop reached, while only fresh seconds
    are persisted — so an on-disk sequence jump is exactly the number of skipped seconds.
    That makes the archive auditable on its own: how much data is missing can be recovered
    from the ``.bin`` alone, without the telemetry JSON that a process restart can wipe.
    """
    engine, clock, index_path, _stock_path = rig
    bridge = ScriptedBridge([
        *[[_spot_tick(24_500 + i)] for i in range(3)],
        *([None] * 8),
        *[[_spot_tick(24_700 + i)] for i in range(3)],
        *([None] * 8),
        *[[_spot_tick(24_900 + i)] for i in range(3)],
    ])
    asyncio.run(_drive(engine, bridge, clock))
    engine.stop_writers()

    for writer in [*engine.index_writers.values(), engine.stock_writer]:
        writer.check_health()  # raises if the thread died

    with IndexBinReader(index_path) as reader:
        frames = list(reader.frames())

    assert len(frames) == 13
    assert engine.stale_seconds == 12
    assert engine.stale_events == 2, "two separate outages, two spells"

    timestamps = [f.timestamp_unix_ms for f in frames]
    sequences = [f.sequence for f in frames]
    assert timestamps == sorted(timestamps)
    assert sequences == sorted(sequences)
    # The auditable invariant: a sequence step of N means N grid seconds elapsed, so a
    # step > 1 is a recorded hole rather than data quietly overwritten with duplicates.
    for i in range(1, len(frames)):
        assert sequences[i] - sequences[i - 1] == (timestamps[i] - timestamps[i - 1]) // (
            INTERVAL_MS
        )
    skipped = (sequences[-1] - sequences[0]) - (len(frames) - 1)
    assert skipped == engine.stale_seconds
