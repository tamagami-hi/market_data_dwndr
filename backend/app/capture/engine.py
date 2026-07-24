"""The 1 Hz capture engine.

Ties the pieces together (docs/30-live-capture/live-data-pipeline.md):

    ticks -> apply to tables/matrix (O(1) token route) -> 1 Hz snapshot -> writer queue

Ticks are applied continuously; a 1-second timer snapshots the latest state of each
table/matrix into a frame and enqueues it to that file's writer thread (last-value-wins
per second). A token may fan out to several tables (India VIX updates every index), so
the routing map holds a *list* of owners per token.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.capture.reconnect import FreshnessMonitor, ReconnectPolicy, StallDetector
from app.capture.snapshot import CaptureSnapshot
from app.capture.writer_thread import (
    FileWriterThread,
    WriterShutdownError,
    WriterThreadError,
)
from app.chain.table import IndexTable
from app.session import now_ms
from app.stocks.matrix import StockMatrix

logger = logging.getLogger(__name__)


class CaptureEngine:
    """Routes ticks, snapshots at 1 Hz, and drives the writer threads."""

    def __init__(
        self,
        index_tables: dict[str, IndexTable],
        stock_matrix: StockMatrix | None,
        index_writers: dict[str, FileWriterThread],
        stock_writer: FileWriterThread | None,
        *,
        clock=now_ms,
        stale_after_ms: int = 5_000,
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        self._clock = clock
        self.unmatched = 0
        self.captures = 0
        # Time (ms) spent building+enqueuing the most recent snapshot — pipeline health.
        self.last_snapshot_ms = 0.0
        self.stall = StallDetector()
        # Data-freshness health: detects "connected but frozen values" and drives a
        # self-driven ticker reconnect (threshold from CAPTURE_STALE_SECONDS).
        self.freshness = FreshnessMonitor(stale_after_ms=stale_after_ms)
        self.reconnect_policy = ReconnectPolicy()
        self.degraded = False
        self._reconnect_at_ms: int | None = None
        self._owners: dict[int, list] = {}
        self._build_routing()

    def _build_routing(self) -> None:
        """token -> [owners]; VIX fans out to every index table."""
        self._owners.clear()
        for table in self.index_tables.values():
            for token in table.tokens:
                self._owners.setdefault(token, []).append(table)
        if self.stock_matrix is not None:
            for token in self.stock_matrix.tokens:
                self._owners.setdefault(token, []).append(self.stock_matrix)

    # -- apply ------------------------------------------------------------- #

    def apply_ticks(self, ticks: list[dict]) -> int:
        """Route a batch of ticks to their owning table(s). Returns applied count."""
        applied = 0
        for tick in ticks:
            owners = self._owners.get(tick.get("instrument_token"))
            if not owners:
                self.unmatched += 1
                continue
            for owner in owners:
                owner.apply_tick(tick)
                applied += 1
        return applied

    # -- capture ----------------------------------------------------------- #

    def capture_once(self, timestamp_unix_ms: int | None = None) -> int:
        """Snapshot every table/matrix at ``ts`` and enqueue to writers."""
        snapshot = self.capture_snapshot(timestamp_unix_ms)
        index_writes = sum(
            1 for name, _frame in snapshot.index_frames if name in self.index_writers
        )
        stock_writes = int(snapshot.stock_frame is not None and self.stock_writer is not None)
        return index_writes + stock_writes

    def capture_snapshot(self, timestamp_unix_ms: int | None = None) -> CaptureSnapshot:
        """Copy and enqueue frames, returning the same immutable display hand-off."""
        ts = timestamp_unix_ms if timestamp_unix_ms is not None else self._clock()
        build_start = time.perf_counter()
        index_frames = tuple(
            (name, table.snapshot(ts)) for name, table in self.index_tables.items()
        )
        for name, frame in index_frames:
            writer = self.index_writers.get(name)
            if writer is not None:
                writer.enqueue(frame)

        stock_frame = self.stock_matrix.snapshot(ts) if self.stock_matrix is not None else None
        if stock_frame is not None and self.stock_writer is not None:
            self.stock_writer.enqueue(stock_frame)

        self.captures += 1
        self.last_snapshot_ms = (time.perf_counter() - build_start) * 1000.0
        return CaptureSnapshot(ts, index_frames, stock_frame)

    # -- writer lifecycle -------------------------------------------------- #

    def _all_writers(self) -> list[FileWriterThread]:
        writers = list(self.index_writers.values())
        if self.stock_writer is not None:
            writers.append(self.stock_writer)
        return writers

    def start_writers(self) -> None:
        writers = self._all_writers()
        for writer in writers:
            writer.start()
        try:
            for writer in writers:
                writer.wait_until_ready()
        except WriterThreadError:
            self.stop_writers()
            raise

    def stop_writers(self) -> None:
        writers = self._all_writers()
        for writer in writers:
            writer.request_stop()
        failures: list[WriterThreadError] = []
        for writer in writers:
            try:
                writer.stop()
            except WriterThreadError as exc:
                failures = [*failures, exc]
        if failures:
            raise WriterShutdownError(
                f"{len(failures)} BIN writer(s) did not flush and stop safely"
            ) from failures[0]

    # -- async live loop --------------------------------------------------- #

    @staticmethod
    def _due_ticks(
        next_tick: int, now: int, interval_ms: int, max_catchup: int
    ) -> tuple[list[int], int, bool]:
        """Return ``(timestamps_to_emit, new_next_tick, stalled)`` for a grid tick.

        Guarantees **no grid second is skipped**: every whole-interval boundary from
        ``next_tick`` up to ``now`` yields exactly one timestamp (last-value-wins per
        second). If we have fallen behind by more than ``max_catchup`` intervals — a
        real stall / clock jump — we emit ``max_catchup`` frames, then resync the grid
        to just after ``now`` and flag ``stalled`` so the caller can log the gap
        (fabricating thousands of duplicate frames would be worse than an honest,
        recorded gap). Under normal operation this returns exactly one timestamp.
        """
        if now < next_tick:
            return [], next_tick, False
        ticks: list[int] = []
        t = next_tick
        while t <= now and len(ticks) < max_catchup:
            ticks.append(t)
            t += interval_ms
        stalled = t <= now  # still behind after the catch-up cap
        if stalled:
            t = now + interval_ms  # resync the grid to the next boundary after now
        return ticks, t, stalled

    def _maybe_reconnect(self, bridge, now: int) -> bool:
        """Drive a self-managed ticker reconnect when the feed goes stale.

        Uses the content-freshness signal (which also covers a total tick outage) and
        an exponential backoff so we don't hammer Kite. Returns True if a reconnect
        was triggered on this call. When fresh data resumes, degraded state clears and
        the backoff resets. Once the circuit breaker trips we stay degraded and keep
        snapshotting last-known values rather than spinning forever.
        """
        reconnect = getattr(bridge, "reconnect", None)
        if not self.freshness.is_stale(now):
            if self.degraded:
                logger.info("live feed recovered; fresh ticks resumed")
            self.degraded = False
            self.reconnect_policy.reset()
            self._reconnect_at_ms = None
            return False

        self.degraded = True
        if not callable(reconnect) or self.reconnect_policy.should_give_up():
            return False
        if self._reconnect_at_ms is not None and now < self._reconnect_at_ms:
            return False  # still inside the backoff window from the last attempt
        logger.warning(
            "live feed stale (%s ms without fresh ticks); forcing reconnect",
            self.freshness.content_age_ms(now),
        )
        reconnect()
        delay_s = self.reconnect_policy.next_delay()
        self._reconnect_at_ms = now + int(delay_s * 1000)
        return True

    async def run(
        self,
        bridge,
        stop_event: asyncio.Event,
        interval_s: float = 1.0,
        broadcaster=None,
        max_catchup: int = 60,
    ) -> None:  # pragma: no cover - live loop, integration-only
        """Consume ticks and snapshot on a drift-free 1 Hz grid until ``stop_event``.

        The grid is aligned to whole ``interval_s`` boundaries and advanced by a fixed
        step (never ``sleep(interval)``-after-work, which drifts), so timestamps stay
        on the second and the daily frame count converges on the expected total. Every
        due boundary is snapshotted — a slow cycle catches up instead of skipping — so
        no second's data is lost. Each snapshot is enqueued to the per-file writer
        threads, which fsync every frame to disk.

        Websocket delivery via ``broadcaster`` is best-effort and never awaited here.
        """
        self.start_writers()
        consumer = asyncio.create_task(self._consume(bridge))
        interval_ms = max(1, int(round(interval_s * 1000)))
        # Align the grid to the next whole-interval boundary from now.
        now0 = self._clock()
        self.freshness.start(now0)
        next_tick = ((now0 // interval_ms) + 1) * interval_ms
        try:
            while not stop_event.is_set():
                sleep_s = (next_tick - self._clock()) / 1000.0
                if sleep_s > 0:
                    try:
                        # Interruptible wait so a stop is acted on promptly.
                        await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
                        break
                    except asyncio.TimeoutError:
                        pass
                due, next_tick, stalled = self._due_ticks(
                    next_tick, self._clock(), interval_ms, max_catchup
                )
                if stalled:
                    logger.warning(
                        "capture fell behind by >%d intervals; filled %d frame(s) then "
                        "resynced the grid (a gap was recorded)",
                        max_catchup,
                        len(due),
                    )
                for ts in due:
                    snapshot = self.capture_snapshot(ts)
                    if broadcaster is not None:
                        broadcaster.publish_latest(snapshot)
                # Health check: reconnect ourselves if the feed has gone stale.
                self._maybe_reconnect(bridge, self._clock())
        finally:
            # Drain + durably persist everything queued before returning (no loss).
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            try:
                self.stop_writers()
            finally:
                close_broadcaster = getattr(broadcaster, "close", None)
                if close_broadcaster is not None:
                    await close_broadcaster()

    async def _consume(self, bridge) -> None:  # pragma: no cover - live loop
        async for batch in bridge.batches():
            now = self._clock()
            self.apply_ticks(batch)
            self.stall.mark_message(now)
            self.freshness.observe(batch, now)


def build_index_writer(table: IndexTable, path) -> FileWriterThread:
    """Convenience: a writer thread for an index table's file (fsync per frame)."""
    from app.bin_codec.writer import IndexBinWriter

    return FileWriterThread(
        IndexBinWriter(path, sync=True), table.header(), name=f"idx-{table.chain.underlying}"
    )


def build_stock_writer(matrix: StockMatrix, path) -> FileWriterThread:
    """Convenience: a writer thread for the stock matrix file (fsync per frame)."""
    from app.bin_codec.writer import StockBinWriter

    return FileWriterThread(StockBinWriter(path, sync=True), matrix.header(), name="stocks")


__all__ = [
    "CaptureEngine",
    "FileWriterThread",
    "ReconnectPolicy",
    "StallDetector",
    "build_index_writer",
    "build_stock_writer",
]
