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


class CaptureStalledError(RuntimeError):
    """Raised when the live feed stays stale after the reconnect recovery is exhausted.

    Propagates out of the capture run loop so the controller records an unrecoverable
    failure and self-exits — Docker's restart policy then brings up a clean process that
    re-bootstraps with a freshly fetched session token (see
    ``CaptureController`` / ``_default_fatal_handler``). This is the escalation that
    replaces the old *silently give up and keep writing frozen frames* behaviour.
    """


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
        token_refresh_after: int = 2,
        max_cycles: int = 3,
        escalate_to_exit: bool = True,
        token_max_age_ms: int = 0,
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        self._clock = clock
        self.unmatched = 0
        self.captures = 0
        # --- data-loss accounting (the 1 Hz grid is the ground truth) ----------- #
        # A "gap" is a resync event: we fell so far behind that whole grid seconds
        # could not be written. These were previously log-only, so a session's real
        # data loss was invisible to the dashboard.
        self.grid_gaps = 0
        self.grid_seconds_lost = 0
        # First/last grid timestamps actually snapshotted this session — lets the
        # monitor compute loss against *elapsed* time instead of the full-day baseline.
        self.first_capture_ms: int | None = None
        self.last_capture_ms: int | None = None
        # Seconds snapshotted while the feed was known stale (duplicate/frozen data).
        self.frozen_seconds = 0
        # Time (ms) spent building+enqueuing the most recent snapshot — pipeline health.
        self.last_snapshot_ms = 0.0
        self.stall = StallDetector()
        # Data-freshness health: detects "connected but frozen values" and drives a
        # self-driven ticker reconnect (threshold from CAPTURE_STALE_SECONDS).
        self.freshness = FreshnessMonitor(stale_after_ms=stale_after_ms)
        self.reconnect_policy = ReconnectPolicy()
        self.degraded = False
        self._reconnect_at_ms: int | None = None
        # -- tiered self-healing reconnect ladder --------------------------------- #
        # Tier 1: cheap reconnect reusing the current token (half-open socket).
        # Tier 2: fetch a fresh token from calspread, then reconnect (expired token).
        # Escalation: after ``max_cycles`` exhausted backoff cycles, either exit for a
        # clean Docker restart or keep cycling (never silently freeze).
        self.token_refresh_after = max(0, int(token_refresh_after))
        self.max_cycles = max(0, int(max_cycles))
        self.escalate_to_exit = bool(escalate_to_exit)
        self.token_max_age_ms = max(0, int(token_max_age_ms))
        self.reconnect_tier = 0
        self.reconnect_cycles = 0
        self.exhausted = False
        self._reconnect_task: asyncio.Task | None = None
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
        if self.first_capture_ms is None:
            self.first_capture_ms = ts
        self.last_capture_ms = ts
        # A frame written while the feed is stale carries duplicate (last-known) values:
        # it occupies a grid second but contains no fresh market data.
        if self.freshness.is_stale(ts):
            self.frozen_seconds += 1
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
        """Drive a tiered, self-healing reconnect when the feed goes stale.

        Uses the content-freshness signal (which also covers a total tick outage) and an
        exponential backoff so we don't hammer Kite. The ladder escalates on every check:

        * **Tier 1** — cheap reconnect reusing the current token (fixes a half-open
          socket). Tried for the first ``token_refresh_after`` attempts of a cycle.
        * **Tier 2** — fetch a *fresh* token from calspread (off-loop) then reconnect
          (fixes an expired/rotated token). Also used immediately when the in-memory
          token is older than ``token_max_age_ms`` or after any exhausted cycle.
        * **Escalation** — once the backoff circuit breaker trips, the cycle counter
          advances; after ``max_cycles`` the recovery is *exhausted*. Rather than the old
          behaviour (silently give up and keep writing frozen frames), we either raise
          :class:`CaptureStalledError` to force a clean process restart, or (when
          ``escalate_to_exit`` is false) keep cycling with token refresh.

        Returns ``True`` if a reconnect was triggered on this call. When fresh data
        resumes, all recovery state clears and the backoff resets.
        """
        reconnect = getattr(bridge, "reconnect", None)
        if not self.freshness.is_stale(now):
            if self.degraded:
                logger.info("live feed recovered; fresh ticks resumed")
            self.degraded = False
            self.reconnect_policy.reset()
            self._reconnect_at_ms = None
            self.reconnect_tier = 0
            self.reconnect_cycles = 0
            self.exhausted = False
            return False

        self.degraded = True

        # A tier-2 token-refresh reconnect is still in flight — don't stack another.
        task = self._reconnect_task
        if task is not None and not task.done():
            return False

        if not callable(reconnect):
            return False
        if self._reconnect_at_ms is not None and now < self._reconnect_at_ms:
            return False  # still inside the backoff window from the last attempt

        # Circuit breaker for the current backoff cycle tripped: escalate rather than
        # silently give up (the historical bug that froze ingestion permanently).
        if self.reconnect_policy.should_give_up():
            return self._escalate_exhausted_cycle()

        # Decide the tier for this attempt. ``attempt`` is 0-based until next_delay().
        attempt_number = self.reconnect_policy.attempt + 1
        use_refresh = (
            self.reconnect_cycles >= 1
            or attempt_number > self.token_refresh_after
            or self._token_too_old(bridge, now)
        )
        self._trigger_reconnect(bridge, now, use_refresh)
        delay_s = self.reconnect_policy.next_delay()
        self._reconnect_at_ms = now + int(delay_s * 1000)
        return True

    def _token_too_old(self, bridge, now: int) -> bool:
        """True when a proactive token refresh is due purely on token age."""
        if self.token_max_age_ms <= 0:
            return False
        age_fn = getattr(bridge, "token_age_ms", None)
        if not callable(age_fn):
            return False
        try:
            return int(age_fn(now)) >= self.token_max_age_ms
        except Exception:  # noqa: BLE001 - never break recovery, but stay visible
            logger.warning(
                "token age check failed; proactive refresh skipped this attempt",
                exc_info=True,
            )
            return False

    def _trigger_reconnect(self, bridge, now: int, use_refresh: bool) -> None:
        """Fire the appropriate reconnect tier for this attempt."""
        age_ms = self.freshness.content_age_ms(now)
        refresh = getattr(bridge, "reconnect_with_refresh", None)
        if use_refresh and callable(refresh):
            self.reconnect_tier = 2
            logger.warning(
                "live feed stale (%s ms); reconnecting with a fresh token (calspread)",
                age_ms,
            )
            self._schedule_reconnect_task(refresh())
        else:
            self.reconnect_tier = 1
            logger.warning(
                "live feed stale (%s ms); reconnecting (reusing current token)", age_ms
            )
            bridge.reconnect()

    def _schedule_reconnect_task(self, coro) -> None:
        """Run an async (token-refresh) reconnect without blocking the capture loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (unit test / sync context): best-effort, don't leak the coro.
            coro.close()
            return
        task = loop.create_task(coro)
        self._reconnect_task = task

        def _clear(completed: asyncio.Task) -> None:
            if self._reconnect_task is completed:
                self._reconnect_task = None
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                logger.error("token-refresh reconnect task failed: %s", type(exc).__name__)

        task.add_done_callback(_clear)

    def _escalate_exhausted_cycle(self) -> bool:
        """Advance the cycle counter and escalate once ``max_cycles`` is reached.

        Never returns while silently degraded: either we keep cycling (with token
        refresh from here on) or we raise to force a clean restart.
        """
        self.reconnect_cycles += 1
        max_attempts = self.reconnect_policy.max_attempts
        self.reconnect_policy.reset()
        self._reconnect_at_ms = None
        logger.error(
            "reconnect cycle %d exhausted (%d attempts); live feed still stale",
            self.reconnect_cycles,
            max_attempts,
        )
        if self.max_cycles and self.reconnect_cycles >= self.max_cycles:
            self.exhausted = True
            if self.escalate_to_exit:
                raise CaptureStalledError(
                    f"live feed stale after {self.reconnect_cycles} reconnect cycle(s); "
                    "restarting the process to obtain a clean session and fresh token"
                )
        return False

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
                    except TimeoutError:
                        pass
                grid_start = next_tick
                now_grid = self._clock()
                due, next_tick, stalled = self._due_ticks(
                    grid_start, now_grid, interval_ms, max_catchup
                )
                if stalled:
                    # Every whole interval from grid_start..now_grid was owed; whatever
                    # we could not emit before the catch-up cap is permanently lost.
                    owed = ((now_grid - grid_start) // interval_ms) + 1
                    lost = max(0, owed - len(due))
                    self.grid_gaps += 1
                    self.grid_seconds_lost += lost
                    logger.warning(
                        "capture fell behind by >%d intervals; filled %d frame(s), lost %d "
                        "grid second(s), then resynced the grid (gap #%d)",
                        max_catchup,
                        len(due),
                        lost,
                        self.grid_gaps,
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
                # ``stop_writers`` joins each writer thread (up to 5s each), which would
                # block the event loop for tens of seconds on a slow disk. Run it in a
                # worker thread so the loop stays responsive while the flush completes.
                await asyncio.to_thread(self.stop_writers)
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
    "CaptureStalledError",
    "FileWriterThread",
    "ReconnectPolicy",
    "StallDetector",
    "build_index_writer",
    "build_stock_writer",
]
