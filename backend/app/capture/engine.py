"""The 1 Hz capture engine.

Ties the pieces together (docs/30-live-capture/live-data-pipeline.md):

    ticks -> apply to tables/matrix (O(1) token route) -> 1 Hz snapshot -> writer queue

Ticks are applied continuously; a 1-second timer snapshots the latest state of each
table/matrix into a frame and enqueues it to that file's writer thread (last-value-wins
per second). A token may fan out to several tables (India VIX updates every index), so
the routing map holds a *list* of owners per token.

Snapshot consistency (verified, not assumed)
--------------------------------------------
The canonical live state is mutated on exactly one thread, and snapshots observe it from
that same thread, so a frame can never contain a half-applied batch:

1. ``KiteTicker`` invokes ``TickerBridge._on_ticks`` on its own websocket thread. That
   callback does **not** touch any table — it only calls
   ``loop.call_soon_threadsafe(self._enqueue, ticks)`` (app/kite/ticker.py).
2. ``_enqueue`` therefore runs on the event-loop thread and merely puts the batch on an
   ``asyncio.Queue``.
3. :meth:`CaptureEngine._consume` drains that queue on the event loop and calls
   :meth:`apply_ticks`, which is a plain synchronous function containing no ``await``.
4. :meth:`capture_snapshot` is likewise synchronous and yields nowhere: it copies every
   table/matrix (``numpy.ndarray.copy``) and hands the copies to the writer threads.

Because steps 3 and 4 are both non-yielding synchronous calls scheduled on the same event
loop, they cannot interleave. No lock is required and none is used — the guarantee comes
from event-loop ownership.

**This is load-bearing.** If ``apply_ticks`` or ``capture_snapshot`` ever becomes a
coroutine, or gains an ``await`` between building the index frames and the stock frame,
the loop may run a tick batch mid-snapshot and produce a frame whose datasets disagree
about the instant they represent. Cross-instrument analysis (index basis, spreads,
lead/lag) depends on one timestamp meaning one consistent observation, so any future
change here must preserve the no-yield property or replace it with an explicit lock.

The writer threads only ever see the copies, so they never race the tick-apply path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.bin_codec.scan import scan_frames
from app.capture import feed_health
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
    """Raised when the live feed stays stale past the configured deadline.

    Propagates out of the capture run loop so the controller records an unrecoverable
    failure and self-exits — Docker's restart policy then brings up a clean process that
    re-bootstraps with a freshly fetched session token (see
    ``CaptureController`` / ``_default_fatal_handler``). This is the escalation that
    replaces the old *silently give up and keep writing frozen frames* behaviour.
    """


class CaptureConsumerFailed(CaptureStalledError):
    """Raised when the tick-consumer task itself dies.

    A *malformed tick* is not this: those are counted and skipped per tick so the rest of
    the batch still lands (see ``CaptureEngine.apply_ticks``). This is the structural case
    — the consumer coroutine stopped — which means nothing will ever update the tables
    again no matter how healthy the socket looks.

    Subclasses :class:`CaptureStalledError` so it takes the established escalation path
    (controller fatal handler -> self-exit -> clean restart), but is named separately so
    the cause is unambiguous in the logs. Detected on the very next grid tick rather than
    inferred ~60 s later from the freshness monitor, which was the only signal before.
    """


def _always_armed(_now: int) -> bool:
    return True


# Throttle for the malformed-tick log: counted always, logged at most this often.
_MALFORMED_LOG_INTERVAL_MS = 10_000

# Fields parsed as unsigned counts; a negative or un-coercible value here is what makes a
# tick unusable, so they are what the throttled log names.
_UNSIGNED_TICK_FIELDS = (
    "oi",
    "volume",
    "total_buy_quantity",
    "total_sell_quantity",
    "oi_day_high",
    "oi_day_low",
)


def _suspect_tick_fields(tick: dict) -> list[str]:
    """Fields in ``tick`` that are negative or not coercible — for the rejection log."""
    suspects: list[str] = []
    for key in _UNSIGNED_TICK_FIELDS:
        if key not in tick:
            continue
        value = tick.get(key)
        if value is None:
            continue
        try:
            if int(value) < 0:
                suspects.append(f"{key}={value!r}")
        except (TypeError, ValueError):
            suspects.append(f"{key}={value!r}")
    for key in ("last_price", "instrument_token"):
        if key not in tick:
            continue
        try:
            float(tick[key])
        except (TypeError, ValueError):
            suspects.append(f"{key}={tick[key]!r}")
    return suspects


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
        index_fno_matrix=None,
        index_fno_writer: FileWriterThread | None = None,
        stale_after_ms: int = 5_000,
        suppress_stale_writes: bool = True,
        stale_exit_ms: int = 0,
        stale_recovery_confirm_ms: int = 15_000,
        reconnect_after_ms: int = 0,
        reconnect_limit_per_hour: int = 6,
        recovery_armed=None,
        capture_expected=None,
        token_refresher=None,
        escalation_recorder=None,
        escalations_before: int = 0,
        escalation_limit: int = 3,
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        # Third capture domain: the consolidated index-F&O dataset. Added as its own slot
        # rather than folded into the index tables, so the existing per-index files and the
        # stock file keep their exact writers, frame types and binary contracts.
        self.index_fno_matrix = index_fno_matrix
        self.index_fno_writer = index_fno_writer
        self._clock = clock
        self.unmatched = 0
        # Ticks rejected because they could not be applied (bad field, bad value). Counted
        # and skipped per tick; they must never terminate the consumer.
        self.malformed_ticks = 0
        self._malformed_since_log = 0
        self._last_malformed_log_ms: int | None = None
        # Set when the tick-consumer task dies, so the next grid tick can fail loudly
        # instead of leaving the freshness monitor to infer it ~60 s later.
        self.consumer_error: BaseException | None = None
        self.captures = 0
        # Frames already on disk when this process started (set by resume_from_disk).
        self.resumed_frames = 0
        # Divergence between the best and worst artifact's frame count at resume time.
        self.resume_frame_spread = 0
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
        # First/last grid second the loop *reached*, whether or not a frame was written.
        # The written-frame timestamps above stop advancing while stale writes are being
        # suppressed, so they cannot measure elapsed time; these can. Without them a
        # session that goes stale at 09:00 would report a zero-length span and therefore
        # 0% loss — exactly the blind spot that made a dead feed look like a clean run.
        self.first_grid_ms: int | None = None
        self.last_grid_ms: int | None = None
        # Grid seconds skipped because the feed was stale: the data existed only as
        # duplicated last-known values, so no frame was persisted for them.
        self.stale_seconds = 0
        # Number of distinct stale spells (one long freeze vs many brief blips).
        self.stale_events = 0
        self._in_stale_spell = False
        # Grid seconds owed while the feed had never delivered a single batch. Its own
        # bucket, because "never started" and "stopped moving" call for different actions:
        # the first means the socket is wrong, the second means the market or feed is.
        self.awaiting_first_tick_seconds = 0
        self._reported_awaiting_first_tick = False
        self._ticks_ever_applied = False
        # Restart downtime, kept separate from ``grid_seconds_lost`` so the two are never
        # summed into a figure larger than the hole that actually exists.
        self.restart_downtime_seconds = 0
        self.suppress_stale_writes = bool(suppress_stale_writes)
        # Time (ms) spent building+enqueuing the most recent snapshot — pipeline health.
        self.last_snapshot_ms = 0.0
        self.stall = StallDetector()
        # Data-freshness health: detects "connected but frozen values" and a total tick
        # outage (threshold from CAPTURE_STALE_SECONDS).
        self.freshness = FreshnessMonitor(stale_after_ms=stale_after_ms)
        self.degraded = False
        # -- restart-first recovery ------------------------------------------------ #
        # One continuous stale *spell*, deliberately immune to flickers: the 2026-08-06
        # session recovered for a single second at 10:25:32, which was enough to reset
        # the old backoff ladder and disarm its escalation for another hour. A spell
        # therefore only ends after ``stale_recovery_confirm_ms`` of *sustained*
        # freshness, while escalation additionally requires the feed to be stale right
        # now — so a recovered feed is never restarted.
        self.stale_exit_ms = max(0, int(stale_exit_ms))
        self.stale_recovery_confirm_ms = max(0, int(stale_recovery_confirm_ms))
        self._stale_spell_start_ms: int | None = None
        self._fresh_since_ms: int | None = None
        self.longest_stale_spell_seconds = 0
        # -- in-process reconnect rung -------------------------------------------- #
        # Between "wait and hope the SDK recovers" and "SIGTERM the process" there was
        # nothing, so every mid-day stall cost a full restart (~47 s). Replacing the socket
        # in place costs ~2 s and fixes the common case. Rate-limited, because a reconnect
        # storm against the broker is its own outage.
        self.reconnect_after_ms = max(0, int(reconnect_after_ms))
        self.reconnect_limit_per_hour = max(0, int(reconnect_limit_per_hour))
        self.reconnect_attempts = 0
        self._reconnect_fn = None
        self._reconnect_times_ms: list[int] = []
        self._broadcaster = None
        # Recovery is only armed while the market is genuinely trading. Capture starts at
        # MARKET_OPEN (09:10 in the deployment) but NSE does not trade until 09:15, so an
        # ungated deadline would exit the process every minute of every pre-open.
        self._recovery_armed = recovery_armed or _always_armed
        # Whether a frame is *expected* at all right now (the artifact's market session,
        # see app/ops/sessions.py). Broader than the arming gate above: the pre-open
        # auction can be captured while still never justifying a restart. Seconds that are
        # not scheduled are not written and never counted as loss.
        self._capture_expected = capture_expected or _always_armed
        # Grid seconds the loop reached while no frame was scheduled (pre-open, after the
        # close, or a disabled session). Reported for transparency, never as data loss.
        self.unscheduled_seconds = 0
        # Best-effort, bounded token swap attempted just before escalating, so the
        # replacement process can start from a fresh token when the broker has one.
        self._token_refresher = token_refresher
        self._escalation_recorder = escalation_recorder
        self.escalations = max(0, int(escalations_before))
        self.escalation_limit = max(0, int(escalation_limit))
        self.recovery_abandoned = False
        self.exhausted = False
        self._owners: dict[int, list] = {}
        # id(owner) -> artifact name, and the per-artifact freshness clock it feeds.
        self._artifact_names: dict[int, str] = {}
        self.artifact_last_update_ms: dict[str, int] = {}
        self._reported_stale_artifacts: tuple[str, ...] = ()
        self._build_routing()

    def _build_routing(self) -> None:
        """token -> [owners]; VIX fans out to every index table."""
        self._owners.clear()
        self._artifact_names.clear()
        for name, table in self.index_tables.items():
            self._artifact_names[id(table)] = name
            for token in table.tokens:
                self._owners.setdefault(token, []).append(table)
        if self.stock_matrix is not None:
            self._artifact_names[id(self.stock_matrix)] = "STOCKS"
            for token in self.stock_matrix.tokens:
                self._owners.setdefault(token, []).append(self.stock_matrix)
        if self.index_fno_matrix is not None:
            self._artifact_names[id(self.index_fno_matrix)] = "INDICES_FnO"
            for token in self.index_fno_matrix.tokens:
                self._owners.setdefault(token, []).append(self.index_fno_matrix)

    # -- apply ------------------------------------------------------------- #

    def apply_ticks(self, ticks: list[dict], now: int | None = None) -> int:
        """Route a batch of ticks to their owning table(s). Returns applied count.

        Also records *per-artifact* freshness: which logical dataset last received a
        relevant update. The transport can be perfectly healthy while one dataset stops
        updating, and only per-artifact ages can tell those apart.

        A tick that cannot be applied is **counted and skipped** at the narrowest level —
        one (tick, owner) apply — so the remaining ticks in the same batch still land. One
        malformed market field must never cost the batch, and must never kill the consumer.
        """
        applied = 0
        stamp = now if now is not None else self._clock()
        if ticks:
            # Proof the feed has delivered something. Tracked here as well as on the
            # freshness monitor so the "never ticked" gate holds for any caller, not only
            # the live consumer that happens to update both.
            self._ticks_ever_applied = True
        for tick in ticks:
            token = tick.get("instrument_token")
            owners = self._owners.get(token)
            if not owners:
                self.unmatched += 1
                continue
            for owner in owners:
                try:
                    owner.apply_tick(tick)
                except Exception as exc:  # noqa: BLE001 - one bad tick, not a dead feed
                    self._record_malformed_tick(token, tick, exc, stamp)
                    continue
                applied += 1
                # Only a *successful* apply counts as freshness for the artifact.
                name = self._artifact_names.get(id(owner))
                if name is not None:
                    self.artifact_last_update_ms[name] = stamp
        return applied

    def _record_malformed_tick(
        self, token: object, tick: dict, exc: BaseException, now: int
    ) -> None:
        """Count a rejected tick and log it, throttled to ~once per 10 s.

        Runs at the tick cadence, so unconditional logging would flood. The throttled line
        carries the instrument token, the field(s) that look wrong, and the exception type
        — enough to identify the contract and the offending value without a debugger.
        """
        self.malformed_ticks += 1
        self._malformed_since_log += 1
        if (
            self._last_malformed_log_ms is not None
            and (now - self._last_malformed_log_ms) < _MALFORMED_LOG_INTERVAL_MS
        ):
            return
        suspects = _suspect_tick_fields(tick)
        logger.error(
            "rejected %d malformed tick(s) in the last window (%d total); latest: "
            "token=%s %s -> %s: %s",
            self._malformed_since_log,
            self.malformed_ticks,
            token,
            ", ".join(suspects) if suspects else "no field flagged",
            type(exc).__name__,
            exc,
        )
        self._last_malformed_log_ms = now
        self._malformed_since_log = 0

    def check_pipeline_alive(self, consumer, bridge=None) -> None:
        """Fail fast if the ingestion pipeline has stopped. Called every grid tick.

        Two structural failures are surfaced here instead of being inferred from the
        freshness monitor ~60 s later:

        * the tick-consumer task is no longer running — nothing can update the tables;
        * the bridge has exhausted its confirmed-reconnect ladder, so the socket is known
          to be unusable right now.

        Both raise, which routes to the controller's fatal handler and a clean restart.
        """
        if consumer is not None and consumer.done() and not consumer.cancelled():
            cause = self.consumer_error
            if cause is None:
                try:
                    cause = consumer.exception()
                except asyncio.CancelledError:  # pragma: no cover - cancelled mid-check
                    return
            raise CaptureConsumerFailed(
                "tick consumer is no longer running "
                f"({type(cause).__name__ if cause else 'exited without an exception'}); "
                f"{self.malformed_ticks} malformed tick(s) rejected this session"
            ) from cause
        unrecoverable = getattr(bridge, "feed_unrecoverable", None)
        if unrecoverable is not None and unrecoverable.is_set():
            raise CaptureStalledError(
                "ticker could not establish a confirmed subscription after "
                f"{getattr(bridge, 'subscribe_failures', 0)} subscription failure(s) and "
                f"{getattr(bridge, 'reconnect_failures', 0)} failed confirmed reconnect(s)"
            )

    def has_received_data(self) -> bool:
        """True once any tick batch has reached the engine from any entry point.

        Guards the pre-first-tick write suppression. Deliberately tolerant about *how* the
        data arrived (routed through :meth:`apply_ticks`, or observed by the freshness
        monitor) so the gate cannot be bypassed by a caller that only exercises one.
        """
        return bool(self._ticks_ever_applied or self.freshness.has_ticked)

    def artifact_names(self) -> tuple[str, ...]:
        """Every logical dataset this engine persists."""
        names = [*self.index_tables]
        if self.stock_matrix is not None:
            names.append("STOCKS")
        if self.index_fno_matrix is not None:
            names.append("INDICES_FnO")
        return tuple(names)

    def artifact_ages_ms(self, now: int) -> dict[str, int | None]:
        """Age of the last relevant update per artifact (``None`` = never updated)."""
        ages: dict[str, int | None] = {}
        for name in self.artifact_names():
            last = self.artifact_last_update_ms.get(name)
            ages[name] = None if last is None else max(0, now - last)
        return ages

    def stale_artifact_names(self, now: int) -> tuple[str, ...]:
        """Artifacts not receiving relevant updates while the transport is alive."""
        return feed_health.stale_artifacts(
            self.artifact_ages_ms(now), self.freshness.stale_after_ms
        )

    def transport_age_ms(self, now: int) -> int | None:
        """Milliseconds since ANY broker packet arrived (the transport signal)."""
        return self.freshness.liveness_age_ms(now)

    def feed_health(self, now: int) -> str:
        """Classify the feed from the transport, artifact, and content signals."""
        return feed_health.classify(
            capture_expected=self.is_capture_expected(now),
            transport_age_ms=self.transport_age_ms(now),
            content_age_ms=self.freshness.content_age_ms(now),
            stale_after_ms=self.freshness.stale_after_ms,
            artifact_ages_ms=self.artifact_ages_ms(now),
            recovery_pending=self._recovery_pending(now),
            recovery_abandoned=self.recovery_abandoned,
        )

    def _recovery_pending(self, now: int) -> bool:
        """True when a restart escalation is imminent for the current stale spell."""
        if self.stale_exit_ms <= 0 or self.recovery_abandoned:
            return False
        spell = self.stale_spell_ms(now)
        return spell > 0 and spell >= self.stale_exit_ms and self.recovery_armed(now)

    def _is_whole_feed_stale(self, now: int) -> bool:
        """True when the outage is feed-wide rather than confined to one artifact.

        §16: a dead transport justifies restarting the process; a single frozen dataset
        does not — that is recorded and exposed instead, so a localised anomaly cannot
        take down capture for every other dataset that is working fine.
        """
        transport_age = self.transport_age_ms(now)
        if transport_age is not None and transport_age >= self.freshness.stale_after_ms:
            return True
        names = self.artifact_names()
        if not names:
            # No artifacts to attribute to (unit rigs): fall back to the content signal.
            return True
        return len(self.stale_artifact_names(now)) == len(names)

    # -- capture ----------------------------------------------------------- #

    def capture_once(self, timestamp_unix_ms: int | None = None) -> int:
        """Snapshot every table/matrix at ``ts`` and enqueue to writers.

        Returns the number of frames actually enqueued — 0 when the snapshot was
        suppressed because the feed was stale (see :meth:`capture_snapshot`).
        """
        snapshot = self.capture_snapshot(timestamp_unix_ms)
        if not snapshot.written:
            return 0
        index_writes = sum(
            1 for name, _frame in snapshot.index_frames if name in self.index_writers
        )
        stock_writes = int(snapshot.stock_frame is not None and self.stock_writer is not None)
        return index_writes + stock_writes

    def resume_from_disk(self, carried: dict | None = None) -> dict:
        """Restore day-level counters after a mid-session restart.

        Captured data already survives a restart (writers append, and the header is only
        written when the file is empty), but every counter lives in process memory.
        Without this, a restart at 12:30 reports ~0 frames against a full-session
        baseline, so a healthy resumed session looks like catastrophic data loss.

        Two sources, in order of authority:
          * the ``.bin`` files themselves — frame counts and the session's true first
            timestamp. Authoritative, being the data that actually landed.
          * ``carried`` — counters that leave no trace on disk (grid gaps, seconds lost,
            stale seconds) from the last persisted monitor snapshot. Best-effort: that
            snapshot is written periodically, so the final few seconds may be missing.
        """
        writers = [*self.index_writers.values()]
        if self.stock_writer is not None:
            writers.append(self.stock_writer)
        if self.index_fno_writer is not None:
            # Previously omitted, so a day whose only frames were in the index-F&O file
            # resumed as "nothing on disk" and re-derived its counters from zero.
            writers.append(self.index_fno_writer)

        scans = []
        for writer in writers:
            path = getattr(getattr(writer, "_writer", None), "path", None)
            if path is not None:
                scans.append(scan_frames(path))

        per_artifact = [int(getattr(w, "frames_on_disk", 0)) for w in writers]
        frames = max(per_artifact, default=0)
        # ``max`` keeps a resume working when an artifact was skipped in an earlier
        # generation, but it also hides a lagging file. Record the spread so divergence is
        # visible instead of silently flattened into the healthiest artifact's count.
        self.resume_frame_spread = frames - min(per_artifact, default=0)
        if self.resume_frame_spread > 0:
            logger.warning(
                "artifact frame counts diverge on resume by %d frame(s): %s",
                self.resume_frame_spread,
                ", ".join(
                    f"{getattr(w, 'name', '?')}={n}"
                    for w, n in zip(writers, per_artifact, strict=False)
                ),
            )
        first_candidates = [s.first_timestamp_ms for s in scans if s.first_timestamp_ms]
        # Counters that leave no trace on disk are restored first, so they survive even a
        # restart with nothing written yet — the case a stale-from-open session produces.
        carried_grid_start = self._apply_carried(carried)
        if frames <= 0 or not first_candidates:
            return {"resumed": False, "frames_on_disk": 0}

        self.captures = frames
        self.first_capture_ms = min(first_candidates)
        # The elapsed-time baseline must start at the session's true beginning. When the
        # feed was stale from the open, the first frame on disk is *later* than the real
        # start, so prefer the carried grid start when it is earlier — otherwise the
        # suppressed morning would silently vanish from the loss figure.
        self.first_grid_ms = min(
            [ms for ms in (self.first_capture_ms, carried_grid_start) if ms is not None]
        )
        self.resumed_frames = frames

        # The restart is itself a hole in the grid. Count the wall-clock seconds between
        # the last frame on disk and now as downtime, so the resumed session's loss figure
        # reflects it instead of pretending the feed was continuous. Tracked in its own
        # counter: folding it into ``grid_seconds_lost`` made the dashboard report restart
        # downtime as a write failure, and double-counted the suppressed seconds that
        # preceded the restart (``stale_seconds`` already holds those).
        last_candidates = [s.last_timestamp_ms for s in scans if s.last_timestamp_ms]
        downtime_s = 0
        if last_candidates:
            last_frame_ms = max(last_candidates)
            self.last_capture_ms = last_frame_ms
            # Seconds strictly between the last written frame and the current grid second.
            downtime_s = max(0, (self._clock() - last_frame_ms) // 1000)
            if downtime_s > 0:
                self.grid_gaps += 1
                self.restart_downtime_seconds += downtime_s

        logger.info(
            "resumed mid-session: %d frames already on disk, session first frame %s, "
            "%ds downtime counted as lost",
            frames,
            self.first_capture_ms,
            downtime_s,
        )
        return {
            "resumed": True,
            "frames_on_disk": frames,
            "first_capture_ms": self.first_capture_ms,
            "downtime_seconds": downtime_s,
        }

    def _apply_carried(self, carried: dict | None) -> int | None:
        """Restore off-disk counters from the last persisted snapshot.

        Restored with ``max`` rather than assignment. The snapshot is written on a
        throttle, so it is always a *lower bound* on the true counts; assigning it could
        only ever discard accounting this process had already accumulated (the reason a
        session with three restarts reported 67 stale seconds against a real ~105).

        Returns the carried grid start timestamp (or ``None``), which the caller folds
        into the elapsed baseline.
        """
        if not carried:
            return None

        def _carry(attr: str, key: str) -> None:
            setattr(self, attr, max(int(getattr(self, attr, 0)), int(carried.get(key) or 0)))

        _carry("grid_gaps", "grid_gaps")
        _carry("grid_seconds_lost", "grid_seconds_lost")
        _carry("stale_seconds", "stale_seconds")
        _carry("stale_events", "stale_events")
        _carry("longest_stale_spell_seconds", "longest_stale_spell_seconds")
        _carry("awaiting_first_tick_seconds", "awaiting_first_tick_seconds")
        _carry("restart_downtime_seconds", "restart_downtime_seconds")
        grid_start = carried.get("first_grid_ms")
        if grid_start is None:
            return None
        try:
            return int(grid_start)
        except (TypeError, ValueError):
            return None

    def capture_snapshot(self, timestamp_unix_ms: int | None = None) -> CaptureSnapshot:
        """Copy the current frames, persist them **only if the feed is fresh**, and
        return the immutable display hand-off.

        A stale grid second (see :class:`FreshnessMonitor`) means the tables hold nothing
        but the last known values: writing them would fabricate a trade print that never
        happened, and because the ``.bin`` layout is fixed-width the file looks perfectly
        healthy afterwards — same byte count, same frame count, same 1 Hz cadence. That
        is unrecoverable corruption of the archive. A missing second is not: it is
        visible, countable, and can be backfilled from the historical API.

        So when stale, the frames are still built and handed to the display worker (the
        dashboard should keep showing the last board, badged stale) but are **not**
        enqueued to any writer, and ``captures`` / ``first_capture_ms`` /
        ``last_capture_ms`` do not advance — those track persisted frames only.

        One deliberate consequence: ``sequence`` is owned by the table/matrix and advances
        once per grid second *built*, so a suppressed second leaves a gap in the on-disk
        sequence. That is the point — a sequence step of N proves N grid seconds elapsed,
        so the archive records its own holes and can be audited without the telemetry JSON
        (which a process restart can lose). See ``tests/test_stale_write_suppression_e2e``.
        """
        ts = timestamp_unix_ms if timestamp_unix_ms is not None else self._clock()
        build_start = time.perf_counter()
        # Is a frame expected at all right now? Outside the artifact's market session (or
        # with that session explicitly disabled) the answer is no, and such a second must
        # not be written, must not count as stale, and must never appear as data loss.
        scheduled = self.is_capture_expected(ts)
        # Freshness is evaluated BEFORE anything is enqueued: the decision to write has
        # to be made on the same timestamp the frame is stamped with.
        stale = self.freshness.is_stale(ts)
        # A frame built before the first tick has ever arrived contains nothing but the
        # zero-initialised table. It is not market data, and writing it also writes the
        # file header from an empty generation — so it is suppressed and accounted for
        # separately from staleness (which means "we had data and it stopped moving").
        awaiting_first_tick = not self.has_received_data()
        suppress = (
            not scheduled or awaiting_first_tick or (stale and self.suppress_stale_writes)
        )
        if scheduled:
            self._track_grid_second(ts)

        index_frames = tuple(
            (name, table.snapshot(ts)) for name, table in self.index_tables.items()
        )
        stock_frame = self.stock_matrix.snapshot(ts) if self.stock_matrix is not None else None
        index_fno_frame = (
            self.index_fno_matrix.snapshot(ts) if self.index_fno_matrix is not None else None
        )

        if not scheduled:
            # Not expected data. The board is still handed to the display worker so the
            # dashboard can render it, but nothing is persisted or counted.
            self.unscheduled_seconds += 1
        elif awaiting_first_tick:
            # Owed, but the feed has never delivered anything: an honest hole, not a
            # frame of zeros. Counted in its own bucket so it is never confused with a
            # feed that was working and stopped.
            self.awaiting_first_tick_seconds += 1
            if not self._reported_awaiting_first_tick:
                self._reported_awaiting_first_tick = True
                logger.warning(
                    "no tick has arrived yet; suppressing frame writes until the feed "
                    "delivers its first batch (the header is not written from an empty "
                    "table)"
                )
        elif suppress:
            self.stale_seconds += 1
            if not self._in_stale_spell:
                self._in_stale_spell = True
                self.stale_events += 1
                logger.warning(
                    "feed stale (content unchanged for %sms); suppressing frame writes "
                    "until fresh ticks resume (stale spell #%d)",
                    self.freshness.content_age_ms(ts),
                    self.stale_events,
                )
        else:
            if self._reported_awaiting_first_tick:
                self._reported_awaiting_first_tick = False
                logger.info(
                    "first tick received; writing frames (%d second(s) waited)",
                    self.awaiting_first_tick_seconds,
                )
            if self._in_stale_spell:
                self._in_stale_spell = False
                logger.info(
                    "fresh ticks resumed; writing frames again (%d stale second(s) "
                    "skipped so far this session)",
                    self.stale_seconds,
                )
            for name, frame in index_frames:
                writer = self.index_writers.get(name)
                if writer is not None:
                    writer.enqueue(frame)
            if stock_frame is not None and self.stock_writer is not None:
                self.stock_writer.enqueue(stock_frame)
            if index_fno_frame is not None and self.index_fno_writer is not None:
                self.index_fno_writer.enqueue(index_fno_frame)
            self.captures += 1
            if self.first_capture_ms is None:
                self.first_capture_ms = ts
            self.last_capture_ms = ts

        self.last_snapshot_ms = (time.perf_counter() - build_start) * 1000.0
        return CaptureSnapshot(
            ts,
            index_frames,
            stock_frame,
            stale=stale,
            written=not suppress,
            scheduled=scheduled,
            index_fno_frame=index_fno_frame,
        )

    def _track_grid_second(self, ts: int) -> None:
        """Record that the grid reached ``ts``, written or not (elapsed-time baseline)."""
        if self.first_grid_ms is None:
            self.first_grid_ms = ts
        if self.last_grid_ms is None or ts > self.last_grid_ms:
            self.last_grid_ms = ts

    # -- writer lifecycle -------------------------------------------------- #

    def _all_writers(self) -> list[FileWriterThread]:
        writers = list(self.index_writers.values())
        if self.stock_writer is not None:
            writers.append(self.stock_writer)
        if self.index_fno_writer is not None:
            writers.append(self.index_fno_writer)
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

    def stale_spell_ms(self, now: int) -> int:
        """Length of the current continuous stale spell (0 when the feed is healthy).

        A spell survives brief flickers of fresh data — see ``observe_feed_health``.
        """
        if self._stale_spell_start_ms is None:
            return 0
        return max(0, now - self._stale_spell_start_ms)

    def recovery_armed(self, now: int) -> bool:
        """True when a stale feed is genuinely a fault worth acting on.

        Two independent ways to arm, and the second is the important one:

        * the artifact's session says enough time has passed since the open
          (``open_at + CAPTURE_RECOVERY_ARM_DELAY_SECONDS``) — the original, purely
          time-based gate;
        * **the feed has already proven it can deliver inside this session.** Evidence
          beats elapsed time. The time-based gate alone meant a feed that died at the open
          could not be acted on for the full arm delay, which is precisely what turned a
          detectable ~47-second fault into a ~295-second hole on six of nine sessions.
        """
        try:
            if self._recovery_armed(now):
                return True
        except Exception:  # noqa: BLE001 - a gate failure must not disable capture
            logger.warning("recovery arming check failed; treating as disarmed", exc_info=True)
            return False
        return bool(self.has_received_data() and self.is_capture_expected(now))

    def set_reconnect_fn(self, reconnect_fn) -> None:
        """Wire the in-process socket replacement used before escalating to a restart."""
        self._reconnect_fn = reconnect_fn

    def _reconnect_budget_left(self, now: int) -> int:
        """Remaining in-process reconnect attempts in the trailing hour."""
        if self.reconnect_limit_per_hour <= 0:
            return 0
        window_start = now - 3_600_000
        self._reconnect_times_ms = [t for t in self._reconnect_times_ms if t >= window_start]
        return max(0, self.reconnect_limit_per_hour - len(self._reconnect_times_ms))

    def _try_reconnect(self, now: int, spell_ms: int) -> bool:
        """Replace the socket in place. Returns True when an attempt was made.

        Deliberately does **not** clear the stale spell: a reconnect that does not restore
        the feed must still reach the escalation deadline. It only defers the next attempt.
        """
        if self._reconnect_fn is None or self.reconnect_after_ms <= 0:
            return False
        if spell_ms < self.reconnect_after_ms:
            return False
        if self.stale_exit_ms and spell_ms >= self.stale_exit_ms:
            return False  # past the deadline: restart rather than keep trying in-process
        last = self._reconnect_times_ms[-1] if self._reconnect_times_ms else None
        if last is not None and (now - last) < self.reconnect_after_ms:
            return False
        if self._reconnect_budget_left(now) <= 0:
            logger.warning(
                "in-process reconnect budget for the hour is spent (%d); leaving recovery "
                "to the restart escalation",
                self.reconnect_limit_per_hour,
            )
            return False
        self._reconnect_times_ms.append(now)
        self.reconnect_attempts += 1
        logger.warning(
            "feed stale for %ds; replacing the socket in place (in-process reconnect "
            "%d, %d left this hour) before escalating to a restart",
            spell_ms // 1000,
            self.reconnect_attempts,
            self._reconnect_budget_left(now),
        )
        try:
            self._reconnect_fn()
        except Exception:  # noqa: BLE001 - a failed reconnect must not mask escalation
            logger.warning("in-process reconnect failed", exc_info=True)
        return True

    def is_capture_expected(self, now: int) -> bool:
        """True when the artifact's market session expects a frame at ``now``."""
        try:
            return bool(self._capture_expected(now))
        except Exception:  # noqa: BLE001 - a schedule failure must not stop capture
            logger.warning(
                "capture-schedule check failed; treating the second as scheduled",
                exc_info=True,
            )
            return True

    def observe_feed_health(self, now: int) -> None:
        """Track the current stale spell and escalate once it passes the deadline.

        Restart-first recovery. There is deliberately **no** in-process reconnect ladder:
        the deployment's own artifacts showed the old tiered ladder firing ~27 token
        refreshes in one session, none of which ever returned a token
        (``token_refreshes=0`` on every recorded day) while each one deleted that day's
        persisted session file. A clean process restart re-bootstraps everything —
        socket, subscriptions, and token — in one step, and ``resume_from_disk`` makes it
        cheap because the ``.bin`` files append.

        Feed health is only meaningful while data is expected. Outside the session the
        spell is cleared rather than accumulated: an absent feed at 09:00 is normal, and
        letting the spell grow through the pre-open would make it breach the deadline the
        instant recovery armed at 09:15 — turning every single open into a restart.

        Raises :class:`CaptureStalledError` when the feed has been continuously stale for
        ``stale_exit_ms`` *while trading*, unless the day's restart budget is spent.
        """
        if not self.is_capture_expected(now):
            self.degraded = False
            self._stale_spell_start_ms = None
            self._fresh_since_ms = None
            return

        content_stale = self.freshness.is_stale(now)
        # The restart spell tracks a FEED-WIDE outage only. A quiet market (values not
        # moving) and a single frozen dataset both leave the transport and the other
        # artifacts healthy, so neither may accumulate time towards a process restart —
        # otherwise a quiet spell would report an imminent restart that never comes.
        stale = content_stale and self._is_whole_feed_stale(now)
        self.degraded = content_stale
        if stale:
            self._fresh_since_ms = None
            if self._stale_spell_start_ms is None:
                self._stale_spell_start_ms = now
        elif self._stale_spell_start_ms is not None:
            # Fresh again — but one tick is not a recovery. Only sustained freshness
            # ends the spell; anything less and the spell keeps accumulating.
            if self._fresh_since_ms is None:
                self._fresh_since_ms = now
            if (now - self._fresh_since_ms) >= self.stale_recovery_confirm_ms:
                spell_s = self.stale_spell_ms(self._fresh_since_ms) // 1000
                logger.info(
                    "live feed recovered; %ds stale spell ended after %ds of fresh ticks",
                    spell_s,
                    (now - self._fresh_since_ms) // 1000,
                )
                self._stale_spell_start_ms = None
                self._fresh_since_ms = None

        spell_ms = self.stale_spell_ms(now)
        self.longest_stale_spell_seconds = max(
            self.longest_stale_spell_seconds, spell_ms // 1000
        )
        # Only ever restart a feed that is stale *right now*: a spell kept open by the
        # confirm window must not take down a process whose ticks have resumed.
        if not stale or self.stale_exit_ms <= 0 or spell_ms < self.stale_exit_ms:
            if stale and self.recovery_armed(now) and self._try_reconnect(now, spell_ms):
                return
            self._report_stale_artifacts(now, spell_ms)
            return
        if not self.recovery_armed(now):
            return
        self._escalate_stale(now, spell_ms)

    def _report_stale_artifacts(self, now: int, spell_ms: int) -> None:
        """Log artifact-level staleness once per change (§16: expose, do not restart)."""
        if not self.freshness.is_stale(now):
            self._reported_stale_artifacts = ()
            return
        stale_names = self.stale_artifact_names(now)
        if stale_names == self._reported_stale_artifacts:
            return
        self._reported_stale_artifacts = stale_names
        if stale_names and len(stale_names) < len(self.artifact_names()):
            logger.error(
                "artifact-level staleness: %s not updating while the transport is alive "
                "and %d other dataset(s) are healthy; NOT restarting",
                ", ".join(stale_names),
                len(self.artifact_names()) - len(stale_names),
            )

    def _escalate_stale(self, now: int, spell_ms: int) -> None:
        """Swap the token if possible, then exit for a clean restart.

        Recovery is **never abandoned while the market session expects data**. The restart
        budget used to be an absolute per-day cap, and because the count is persisted in a
        ledger and carried across restarts, three escalations early in the morning left
        capture online but permanently deaf for the rest of the session — the 72- and
        91-minute holes on 2026-08-05/06. A gap is recoverable data loss; a process that
        has stopped trying to fetch is not, so the budget now only decides when to start
        shouting, not when to give up.

        This is unbounded by design: a feed that is down all session restarts roughly once
        per ``stale_exit_ms`` plus bootstrap. That is the accepted cost of never stopping.
        Note that escalation is only reachable while ``is_capture_expected`` holds (see
        ``observe_feed_health``), so there is no path here that runs outside the session.
        """
        spell_s = spell_ms // 1000
        if self.escalation_limit and self.escalations >= self.escalation_limit:
            logger.critical(
                "live feed stale for %ds and the nominal restart budget (%d) is already "
                "spent, but the market session is open — restarting anyway rather than "
                "abandoning recovery (escalation %d)",
                spell_s,
                self.escalation_limit,
                self.escalations + 1,
            )

        self.escalations += 1
        # Flush telemetry BEFORE raising. The snapshot is written on a throttle, so without
        # this the last minute of stale/gap accounting never reaches disk and the restarted
        # process carries a stale lower bound (the reason a three-restart session reported
        # 67 stale seconds against a real ~105).
        self._persist_telemetry_now()
        # Best-effort: give the replacement process a fresh token to start from. This is
        # a *swap*, never a delete — the old ladder invalidated the persisted session
        # before fetching, so a broker that returned nothing (every recorded day) left
        # the deployment with no session at all and capture unable to start.
        if self._token_refresher is not None:
            try:
                self._token_refresher()
            except Exception:  # noqa: BLE001 - must never mask the escalation
                logger.warning("pre-restart token refresh failed", exc_info=True)
        if self._escalation_recorder is not None:
            try:
                self._escalation_recorder()
            except Exception:  # noqa: BLE001 - ledger is advisory, not a gate
                logger.warning("could not record the escalation", exc_info=True)
        raise CaptureStalledError(
            f"live feed stale for {spell_s}s while the market is trading "
            f"(escalation {self.escalations}/{self.escalation_limit or '∞'}); "
            "restarting the process for a clean session and fresh token"
        )

    def _persist_telemetry_now(self) -> None:
        """Force the throttled monitor snapshot to disk (best effort, never raises)."""
        persist = getattr(self._broadcaster, "persist_snapshot_now", None)
        if persist is None:
            return
        try:
            persist()
        except Exception:  # noqa: BLE001 - telemetry must never block recovery or shutdown
            logger.debug("could not force-persist the capture snapshot", exc_info=True)

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
        self._broadcaster = broadcaster
        # The socket-replacement rung needs the live bridge; wiring it here keeps the
        # engine constructible without one (unit rigs) while the live loop always has it.
        reconnect_fn = getattr(bridge, "reconnect", None)
        if reconnect_fn is not None and self._reconnect_fn is None:
            self.set_reconnect_fn(reconnect_fn)
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
                # Structural failures first: a dead consumer or an unusable socket is known
                # now, and must not wait for the freshness monitor's deadline to be noticed.
                self.check_pipeline_alive(consumer, bridge)
                # Health check: escalate to a clean process restart if the feed is dead.
                self.observe_feed_health(self._clock())
        finally:
            # Drain + durably persist everything queued before returning (no loss).
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            # Final telemetry write, so the day's counters do not end a minute stale.
            self._persist_telemetry_now()
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
        """Drain tick batches into the tables until cancelled.

        Individual malformed ticks are absorbed inside :meth:`apply_ticks`. Anything that
        escapes to here is structural, so it is recorded and re-raised: without ingestion
        the tables can never update again, and :meth:`check_pipeline_alive` turns that into
        an immediate, named failure on the next grid tick.
        """
        try:
            async for batch in bridge.batches():
                now = self._clock()
                self.apply_ticks(batch, now)
                self.stall.mark_message(now)
                self.freshness.observe(batch, now)
        except asyncio.CancelledError:
            raise  # normal shutdown path
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            self.consumer_error = exc
            logger.exception(
                "tick consumer terminated (%s); no table can update until capture restarts",
                type(exc).__name__,
            )
            raise


def build_index_writer(table: IndexTable, path) -> FileWriterThread:
    """Convenience: a writer thread for an index table's file (fsync per frame).

    A strike-axis change no longer needs a new file: every frame carries its own strike
    vector, so generations with different strike counts append to the same ``<date>.bin``.
    The resolver only steps aside for a *schema version* difference, which can happen once
    per artifact on the first run after a format change and leaves the older file untouched.
    """
    from app.bin_codec.writer import IndexBinWriter, resolve_index_segment_path

    header = table.header()
    target = resolve_index_segment_path(path)
    if target != Path(path):
        logger.warning(
            "%s: %s was written by a different schema version; continuing in %s so the "
            "existing frames stay readable",
            table.chain.underlying,
            Path(path).name,
            target.name,
        )
    return FileWriterThread(
        IndexBinWriter(target, sync=True),
        header,
        name=f"idx-{table.chain.underlying}",
        frames_on_disk=scan_frames(target).frames,
    )


def build_stock_writer(matrix: StockMatrix, path) -> FileWriterThread:
    """Convenience: a writer thread for the stock matrix file (fsync per frame)."""
    from app.bin_codec.scan import AXIS_STOCK
    from app.bin_codec.writer import StockBinWriter, resolve_segment_path

    header = matrix.header()
    target = resolve_segment_path(path, len(header.stocks), AXIS_STOCK)
    if target != Path(path):
        logger.warning(
            "STOCKS: board size changed to %d rows; rolling to segment %s",
            len(header.stocks),
            target.name,
        )
    return FileWriterThread(
        StockBinWriter(target, sync=True),
        header,
        name="stocks",
        frames_on_disk=scan_frames(target).frames,
    )


def build_index_fno_writer(matrix, path) -> FileWriterThread:
    """Convenience: a writer thread for the consolidated index-F&O file (fsync per frame)."""
    from app.bin_codec.scan import AXIS_INDEX_FNO
    from app.bin_codec.writer import IndexFnoBinWriter, resolve_segment_path

    header = matrix.header()
    target = resolve_segment_path(path, len(header.indices), AXIS_INDEX_FNO)
    if target != Path(path):
        logger.warning(
            "INDICES_FnO: index count changed to %d; rolling to segment %s",
            len(header.indices),
            target.name,
        )
    return FileWriterThread(
        IndexFnoBinWriter(target, sync=True),
        header,
        name="index-fno",
        frames_on_disk=scan_frames(target).frames,
    )


__all__ = [
    "CaptureConsumerFailed",
    "CaptureEngine",
    "CaptureStalledError",
    "FileWriterThread",
    "ReconnectPolicy",
    "StallDetector",
    "build_index_fno_writer",
    "build_index_writer",
    "build_stock_writer",
]
