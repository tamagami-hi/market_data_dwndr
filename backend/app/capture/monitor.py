"""Capture Monitor metrics.

Builds the ``CaptureStatus`` telemetry that drives the frontend dashboard
(docs/50-frontend/frontend.md):

- **per-underlying** (each index + the stocks file): connected, last tick time, frames
  written, current file size, 1 Hz heartbeat (a frame written in the last ~2 s),
  unmatched-tick counter.
- **global**: total unique tokens subscribed, frames/sec, ``MARKET_DATA`` disk usage.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import deque
from pathlib import Path

from app.capture.writer_thread import FileWriterThread
from app.chain.table import IndexTable
from app.session import now_ms
from app.stocks.matrix import StockMatrix
from app.ws import protocol

logger = logging.getLogger(__name__)

HEARTBEAT_WINDOW_MS = 2_000

# Frames/sec is measured over this trailing window so the value is a stable rate
# regardless of how often (or from how many callers) ``snapshot()`` is invoked.
FPS_WINDOW_MS = 5_000
# Minimum spanned time before a rate is reported (avoids startup / tiny-interval spikes).
FPS_MIN_SPAN_MS = 900

# ``directory_bytes`` is an O(files) filesystem walk; cache it for this long so the
# per-second broadcast and every dashboard poll don't each pay for it.
DISK_BYTES_TTL_MS = 30_000

# Full-session frame baseline (09:00-15:30 @ 1 Hz). Overridable via Settings.
DEFAULT_EXPECTED_FRAMES = 0  # no configured session window == no defensible baseline


def directory_bytes(root: str | os.PathLike[str]) -> int:
    """Total size in bytes of every file under ``root`` (0 if missing)."""
    root = Path(root)
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def disk_usage(path: str | os.PathLike[str] | None) -> tuple[int, int]:
    """Return ``(free_bytes, total_bytes)`` for the filesystem holding ``path``.

    Walks up to the nearest existing ancestor so a not-yet-created data dir still
    reports its target volume. Returns ``(0, 0)`` when nothing is resolvable.
    """
    if path is None:
        return (0, 0)
    probe = Path(path)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:  # reached filesystem root without finding an existing dir
            return (0, 0)
        probe = parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return (0, 0)
    return (int(usage.free), int(usage.total))


def frame_loss_pct(frames_written: int, frames_expected: int) -> float:
    """Percent of the full-session baseline not yet captured (0..100, clamped)."""
    if frames_expected <= 0:
        return 0.0
    loss = (frames_expected - frames_written) / frames_expected * 100.0
    return max(0.0, min(100.0, loss))


def expected_frames_elapsed(
    first_capture_ms: int | None,
    last_capture_ms: int | None,
    interval_ms: int = 1_000,
) -> int:
    """Frames the 1 Hz grid *should* have produced over the elapsed capture span.

    ``frame_loss_pct`` against the whole-day baseline reports ~96% "loss" at 09:20,
    which is correct as a completion figure but useless as a health signal. Measuring
    against elapsed grid time answers the question that actually matters intraday:
    *of the seconds we have been running, how many did we capture?*
    """
    if first_capture_ms is None or last_capture_ms is None:
        return 0
    interval_ms = max(1, int(interval_ms))
    span = max(0, int(last_capture_ms) - int(first_capture_ms))
    return span // interval_ms + 1


def drop_rate_pct(dropped_batches: int, captures: int) -> float:
    """Percent of ingest batches dropped: dropped / (captures + dropped) * 100."""
    denom = captures + dropped_batches
    if denom <= 0:
        return 0.0
    return dropped_batches / denom * 100.0


def avg_bytes_per_frame(file_bytes: int, frames_written: int) -> float:
    """Mean on-disk bytes per written frame (0 when no frames yet)."""
    if frames_written <= 0:
        return 0.0
    return file_bytes / frames_written


def projected_eod_bytes(file_bytes: int, frames_written: int, frames_expected: int) -> int:
    """Extrapolated end-of-day file size from the current average frame size."""
    if frames_written <= 0:
        return 0
    return int(round(avg_bytes_per_frame(file_bytes, frames_written) * frames_expected))


class CaptureMonitor:
    """Computes live capture telemetry from the engine's tables/writers."""

    def __init__(
        self,
        index_tables: dict[str, IndexTable],
        stock_matrix: StockMatrix | None,
        index_writers: dict[str, FileWriterThread],
        stock_writer: FileWriterThread | None,
        *,
        engine=None,
        bridge=None,
        market_data_path: str | os.PathLike[str] | None = None,
        clock=now_ms,
        heartbeat_window_ms: int = HEARTBEAT_WINDOW_MS,
        expected_frames: int = DEFAULT_EXPECTED_FRAMES,
        capture_start_ms: int | None = None,
        fps_window_ms: int = FPS_WINDOW_MS,
        disk_bytes_ttl_ms: int = DISK_BYTES_TTL_MS,
        session_registry=None,
        subscription=None,
        index_fno_matrix=None,
        index_fno_writer: FileWriterThread | None = None,
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        self.engine = engine
        self.bridge = bridge
        self.market_data_path = market_data_path
        # Session schedule (app/ops/sessions.py). When present it replaces the
        # process-observed grid as the loss denominator: expected seconds must not depend
        # on the application having been alive to count them, or an outage erases itself
        # from its own loss figure.
        self.session_registry = session_registry
        # Resolved subscription plan (app/capture/subscription.py), for capacity telemetry.
        self.subscription = subscription
        # Third capture domain, reported as just another artifact.
        self.index_fno_matrix = index_fno_matrix
        self.index_fno_writer = index_fno_writer
        self._clock = clock
        self.heartbeat_window_ms = heartbeat_window_ms
        # A non-positive baseline means "unknown", not "assume a standard day": every
        # percentage derived from it guards against zero rather than dividing by an
        # invented market schedule.
        self.expected_frames = max(0, int(expected_frames))
        # Capture start timestamp for uptime; defaults to first construction time.
        self.capture_start_ms = capture_start_ms if capture_start_ms is not None else clock()
        # fps rate tracking: a trailing window of (timestamp_ms, total_captures)
        # samples. Measuring against the OLDEST sample in the window makes the rate
        # independent of how often snapshot() is called (broadcaster + REST polls +
        # persistence all share this monitor), so extra calls can't create spikes.
        self.fps_window_ms = fps_window_ms if fps_window_ms > 0 else FPS_WINDOW_MS
        self._fps_samples: deque[tuple[int, int]] = deque()
        # Same trailing-window treatment for ingest rate (see _ticks_rate).
        self._ticks_samples: deque[tuple[int, int]] = deque()
        self._fps_lock = threading.Lock()
        # ``directory_bytes`` walks the whole MARKET_DATA tree (rglob + stat per file),
        # which grows without bound as sessions accumulate — and snapshot() is called
        # every second by the broadcaster *plus* on every /api/status and /api/stats
        # read. Cache it behind a TTL so dashboard polling can't multiply the cost.
        self.disk_bytes_ttl_ms = (
            disk_bytes_ttl_ms if disk_bytes_ttl_ms > 0 else DISK_BYTES_TTL_MS
        )
        self._disk_bytes_cache: tuple[int, int] | None = None  # (measured_at_ms, bytes)

    def _cached_directory_bytes(self, now: int) -> int:
        """``directory_bytes`` behind a TTL (see ``disk_bytes_ttl_ms``)."""
        if self.market_data_path is None:
            return 0
        cache = self._disk_bytes_cache
        if cache is not None and (now - cache[0]) < self.disk_bytes_ttl_ms:
            return cache[1]
        total = directory_bytes(self.market_data_path)
        self._disk_bytes_cache = (now, total)
        return total

    def _feed_stale(self, now: int) -> bool:
        """True when the engine's freshness monitor reports frozen/absent data."""
        freshness = getattr(self.engine, "freshness", None)
        if freshness is None:
            return False
        return bool(freshness.is_stale(now))

    def _loss_baselines(self) -> tuple[int, int, int]:
        """Return ``(grid_seconds_elapsed, writable_seconds, stale_seconds)``.

        Three different denominators, each answering a different question:

        * ``grid_seconds_elapsed`` — every grid second the capture loop reached, whether
          a frame was persisted or not. The honest denominator for *total* market-data
          loss, and the only one that still moves while stale writes are suppressed.
        * ``writable_seconds`` — elapsed seconds minus the stale ones, i.e. the seconds a
          frame *could* legitimately have been written for. Loss against this isolates
          gaps and write-path failures from an upstream feed that had nothing to give.
        * ``stale_seconds`` — seconds deliberately not written because the feed was
          frozen or absent (see ``CaptureEngine.capture_snapshot``).
        """
        first_grid = getattr(self.engine, "first_grid_ms", None)
        last_grid = getattr(self.engine, "last_grid_ms", None)
        if first_grid is None or last_grid is None:
            # Pre-suppression engines (and unit doubles) only track written frames.
            first_grid = getattr(self.engine, "first_capture_ms", None)
            last_grid = getattr(self.engine, "last_capture_ms", None)
        elapsed = expected_frames_elapsed(first_grid, last_grid)
        stale_seconds = int(getattr(self.engine, "stale_seconds", 0))
        writable = max(0, elapsed - stale_seconds)
        return elapsed, writable, stale_seconds

    def _queue_depth(self) -> int:
        """Depth of the bridge's ingest queue (0 when unavailable).

        A sustained depth is the early warning that tick application is falling behind the
        socket, which is a different problem from a slow writer queue.
        """
        queue = getattr(self.bridge, "queue", None)
        if queue is None:
            return 0
        try:
            return int(queue.qsize())
        except Exception:  # noqa: BLE001 - telemetry must never break capture
            return 0

    def _scheduled_baselines(self, now: int) -> tuple[int, int]:
        """Return ``(scheduled_today, scheduled_elapsed)`` from the session schedule.

        Both are derived from configuration and the trading date alone. That is the whole
        point: if the application or the server was down from 09:15 to 09:27, those 720
        seconds were still owed, and a denominator built from what the process observed
        would quietly erase them from its own loss figure (§17.1).

        Falls back to ``(0, 0)`` without a registry, in which case callers keep using the
        process-observed grid.
        """
        registry = self.session_registry
        if registry is None:
            return 0, 0
        artifact = next(iter(self.index_tables), "STOCKS")
        try:
            return (
                int(registry.scheduled_seconds(artifact)),
                int(registry.scheduled_seconds_elapsed(artifact, now)),
            )
        except Exception:  # noqa: BLE001 - telemetry must never break capture
            logger.debug("scheduled-baseline lookup failed", exc_info=True)
            return 0, 0

    def _loss_breakdown(self, scheduled_elapsed: int, captured: int) -> dict:
        """Split total missing seconds into causes that reconcile with the total (§17.11).

        ``stale`` is what the engine deliberately suppressed; ``write_path`` is the gap
        accounting for frames that should have been written and were not; whatever is left
        is downtime — the application or the server was not there to capture it, which
        covers a crash, a restart, a reboot and a deliberate stop alike. Anything that
        cannot be attributed stays visible as ``unclassified`` rather than being dropped.
        """
        missing = max(0, scheduled_elapsed - captured)
        stale = min(missing, int(getattr(self.engine, "stale_seconds", 0)))
        write_path = min(
            max(0, missing - stale), int(getattr(self.engine, "grid_seconds_lost", 0))
        )
        downtime = max(0, missing - stale - write_path)
        return {
            "missing_seconds": missing,
            "stale_feed_seconds": stale,
            "write_path_seconds": write_path,
            "downtime_seconds": downtime,
            "unclassified_seconds": max(0, missing - stale - write_path - downtime),
        }

    def _entry(
        self,
        underlying: str,
        unmatched: int,
        writer: FileWriterThread | None,
        applied: int = 0,
    ) -> dict:
        now = self._clock()
        frames = writer.frames_written if writer else 0
        last_write = writer.last_write_ms if writer else None
        file_bytes = 0
        if writer is not None and writer.path.exists():
            file_bytes = writer.path.stat().st_size
        feed_stale = self._feed_stale(now)
        wrote_recently = last_write is not None and (now - last_write) <= self.heartbeat_window_ms
        # Heartbeat is only OK when a frame was written recently AND the feed is
        # delivering *fresh* data. A frozen feed still writes duplicate frames every
        # second, so the old write-only check reported healthy during a freeze.
        heartbeat_ok = bool(wrote_recently and not feed_stale)
        heartbeat_age_ms = (now - last_write) if last_write is not None else None
        last_tick_ms = self.engine.stall.last_message_ms if self.engine is not None else None
        connected = bool(self.bridge.connected) if self.bridge is not None else False
        # Three DIFFERENT loss figures, previously conflated into one alarming number:
        #   frame_loss_pct   – vs the whole-day baseline. At 10:30 a perfect session still
        #                      reads ~75%, because most of the day has not happened yet.
        #                      It is a completeness/progress measure, not a fault.
        #   session_loss_pct – vs the grid seconds a frame COULD have been written for
        #                      (elapsed minus stale). This isolates gaps and write-path
        #                      failures: anything above ~0 means frames we owed and lost.
        #   data_loss_pct    – vs every elapsed grid second, stale ones included. This is
        #                      the "how much of the session is actually missing from the
        #                      archive" number, and the one a frozen feed moves.
        grid_elapsed, writable, _stale = self._loss_baselines()
        return {
            "underlying": underlying,
            "connected": connected,
            "last_tick_ms": last_tick_ms,
            "frames_written": frames,
            "frames_expected": self.expected_frames,
            "frame_loss_pct": round(frame_loss_pct(frames, self.expected_frames), 3),
            "session_frames_expected": writable,
            "session_loss_pct": round(frame_loss_pct(frames, writable), 3),
            "grid_seconds_elapsed": grid_elapsed,
            "data_loss_pct": round(frame_loss_pct(frames, grid_elapsed), 3),
            "day_complete_pct": round(
                min(100.0, frames / self.expected_frames * 100.0) if self.expected_frames else 0.0,
                2,
            ),
            "file_bytes": file_bytes,
            "avg_bytes_per_frame": round(avg_bytes_per_frame(file_bytes, frames), 1),
            "projected_eod_bytes": projected_eod_bytes(file_bytes, frames, self.expected_frames),
            "heartbeat_ok": heartbeat_ok,
            "heartbeat_age_ms": heartbeat_age_ms,
            "data_fresh": not feed_stale,
            "unmatched": unmatched,
            # Ticks routed into this table/matrix — separates "one underlying froze"
            # from "the whole feed froze" (the global freshness signal can't).
            "applied": int(applied),
            # This file's writer queue depth; a sustained non-zero value is the early
            # warning that the write path is falling behind for *this* stream.
            "writer_pending": int(writer.pending) if writer is not None else 0,
            # --- per-artifact lifecycle and freshness (§20) ---
            # Each artifact answers for itself: which session phase it is in, whether a
            # frame is owed right now, and how long since IT last received a relevant
            # update. A frozen index is invisible in the global signals.
            "market_phase": self._artifact_phase(underlying, now),
            "capture_active": self._artifact_capture_active(underlying, now),
            "artifact_age_ms": self._artifact_age_ms(underlying, now),
            "artifact_stale": underlying in self._stale_artifact_set(now),
            "last_frame_ms": last_write,
        }

    def _artifact_phase(self, artifact: str, now: int) -> str | None:
        if self.session_registry is None:
            return None
        try:
            return self.session_registry.phase(artifact, now)
        except Exception:  # noqa: BLE001 - telemetry must never break capture
            return None

    def _artifact_capture_active(self, artifact: str, now: int) -> bool | None:
        if self.session_registry is None:
            return None
        try:
            return self.session_registry.is_capture_expected(artifact, now)
        except Exception:  # noqa: BLE001
            return None

    def _artifact_age_ms(self, artifact: str, now: int) -> int | None:
        ages = getattr(self.engine, "artifact_ages_ms", None)
        if not callable(ages):
            return None
        try:
            return ages(now).get(artifact)
        except Exception:  # noqa: BLE001
            return None

    def _stale_artifact_set(self, now: int) -> frozenset[str]:
        names = getattr(self.engine, "stale_artifact_names", None)
        if not callable(names):
            return frozenset()
        try:
            return frozenset(names(now))
        except Exception:  # noqa: BLE001
            return frozenset()

    def per_underlying(self) -> list[dict]:
        entries = [
            self._entry(
                name,
                table.unmatched,
                self.index_writers.get(name),
                applied=getattr(table, "applied", 0),
            )
            for name, table in self.index_tables.items()
        ]
        if self.stock_matrix is not None:
            entries.append(
                self._entry(
                    "STOCKS",
                    self.stock_matrix.unmatched,
                    self.stock_writer,
                    applied=getattr(self.stock_matrix, "applied", 0),
                )
            )
        # The consolidated index-F&O domain is just another artifact here. Everything
        # downstream — the WebSocket payload, the dashboard's health table, the per-artifact
        # loss figures — iterates this list, so a new domain appears without any consumer
        # needing to know it exists.
        if self.index_fno_matrix is not None:
            entries.append(
                self._entry(
                    "INDICES_FnO",
                    self.index_fno_matrix.unmatched,
                    self.index_fno_writer,
                    applied=getattr(self.index_fno_matrix, "applied", 0),
                )
            )
        return entries

    def _unique_token_count(self) -> int:
        tokens: set[int] = set()
        for table in self.index_tables.values():
            tokens.update(table.tokens)
        if self.stock_matrix is not None:
            tokens.update(self.stock_matrix.tokens)
        return len(tokens)

    def _fps(self) -> float:
        """Capture rate over a trailing window (stable regardless of call cadence).

        Records the current ``(now, total_captures)`` sample, drops samples older
        than ``fps_window_ms``, and measures the rate against the OLDEST sample
        still in the window. Because the window endpoints stay ~``fps_window_ms``
        apart no matter how many extra callers hit ``snapshot()``, the value can't
        spike from a short interval between two nearby calls.
        """
        if self.engine is None:
            return 0.0
        now = self._clock()
        captures = self.engine.captures
        # snapshot() is called concurrently from the display worker thread (broadcaster)
        # and the event loop (/api/status, /api/stats), so the sample deque needs a lock
        # or the two callers interleave and produce a nonsense rate.
        with self._fps_lock:
            samples = self._fps_samples
            samples.append((now, captures))
            # Trim to the trailing window, always keeping at least two samples to measure.
            while len(samples) > 2 and (now - samples[0][0]) > self.fps_window_ms:
                samples.popleft()
            oldest_ts, oldest_captures = samples[0]
        elapsed_ms = now - oldest_ts
        if elapsed_ms < FPS_MIN_SPAN_MS:
            return 0.0  # not enough spanned time yet (startup / rapid successive calls)
        return (captures - oldest_captures) / (elapsed_ms / 1000.0)

    def _ticks_rate(self, now: int, ticks_received: int) -> float:
        """Ticks/sec over a trailing window (same approach as ``_fps``).

        Measured against the OLDEST sample still inside the window, so the value is a
        genuine current rate and is unaffected by how often ``snapshot()`` is called.
        """
        with self._fps_lock:
            samples = self._ticks_samples
            samples.append((now, ticks_received))
            while len(samples) > 2 and (now - samples[0][0]) > self.fps_window_ms:
                samples.popleft()
            oldest_ts, oldest_ticks = samples[0]
        elapsed_ms = now - oldest_ts
        if elapsed_ms < FPS_MIN_SPAN_MS:
            return 0.0
        return round((ticks_received - oldest_ticks) / (elapsed_ms / 1000.0), 2)

    def global_metrics(self, entries: list[dict] | None = None) -> dict:
        dropped_batches = self.bridge.dropped_batches if self.bridge is not None else 0
        captures = self.engine.captures if self.engine is not None else 0
        now = self._clock()
        uptime_ms = max(0, now - self.capture_start_ms)
        disk_free, disk_total = disk_usage(self.market_data_path)
        # Pipeline health: how long the last snapshot took to build+enqueue, and the
        # deepest writer queue (a sustained lag is the early warning that the write
        # path is falling behind — i.e. a data-loss risk).
        snapshot_ms = round(getattr(self.engine, "last_snapshot_ms", 0.0), 3)
        writers = [*self.index_writers.values()]
        if self.stock_writer is not None:
            writers.append(self.stock_writer)
        writer_lag_max = max((w.pending for w in writers), default=0)
        # Overall frame integrity: sum of frames vs sum of per-underlying baselines.
        # ``entries`` is passed in by snapshot() so per_underlying() (which stats every
        # writer file) is computed once per snapshot rather than twice.
        if entries is None:
            entries = self.per_underlying()
        total_frames = sum(int(e["frames_written"]) for e in entries)
        total_expected = self.expected_frames * len(entries) if entries else 0
        # Data-freshness health (the "is the feed actually updating?" signal).
        freshness = getattr(self.engine, "freshness", None)
        data_age_ms = freshness.content_age_ms(now) if freshness is not None else None
        liveness_age_ms = freshness.liveness_age_ms(now) if freshness is not None else None
        frozen_batches = freshness.frozen_batches if freshness is not None else 0
        stale = self._feed_stale(now)
        degraded = bool(getattr(self.engine, "degraded", False)) or stale
        reconnects = int(getattr(self.bridge, "reconnects", 0)) if self.bridge is not None else 0
        # Restart-first recovery telemetry: how long the CURRENT continuous stale spell
        # has run, how many times today's session has escalated to a process restart, and
        # whether the restart budget is spent (``recovery_abandoned`` — capture stays up
        # but is knowingly not receiving data). These replace the old reconnect
        # tier/cycle counters, which reported 0 through a 91-minute outage because a
        # single fresh tick reset them.
        stale_spell_seconds = int(getattr(self.engine, "stale_spell_ms", lambda _n: 0)(now)) // 1000
        longest_stale_spell_seconds = int(
            getattr(self.engine, "longest_stale_spell_seconds", 0)
        )
        escalations = int(getattr(self.engine, "escalations", 0))
        recovery_abandoned = bool(getattr(self.engine, "recovery_abandoned", False))
        recovery_armed = bool(getattr(self.engine, "recovery_armed", lambda _n: False)(now))
        exhausted = bool(getattr(self.engine, "exhausted", False))
        token_refreshes = (
            int(getattr(self.bridge, "token_refreshes", 0)) if self.bridge is not None else 0
        )
        last_token_refresh_ms = (
            getattr(self.bridge, "last_token_refresh_ms", None)
            if self.bridge is not None
            else None
        )
        token_age_fn = getattr(self.bridge, "token_age_ms", None) if self.bridge else None
        token_age_ms = int(token_age_fn(now)) if callable(token_age_fn) else None
        # --- per-session data loss ------------------------------------------------ #
        # Grid gaps are the ground truth for lost seconds (previously log-only).
        grid_gaps = int(getattr(self.engine, "grid_gaps", 0))
        grid_seconds_lost = int(getattr(self.engine, "grid_seconds_lost", 0))
        stale_events = int(getattr(self.engine, "stale_events", 0))
        grid_seconds_elapsed, writable_seconds, stale_seconds = self._loss_baselines()
        # Loss measured against the seconds a frame could legitimately have been written
        # for (elapsed minus stale) — gaps and write-path failures only.
        session_loss_pct = round(frame_loss_pct(captures, writable_seconds), 3)
        # Total market-data loss: every elapsed grid second with no frame in the archive,
        # whether it was lost to a gap or skipped because the feed had frozen. This is the
        # number that would have exposed a feed that stopped updating at 09:00 while the
        # frame count, file size and 1 Hz cadence all still looked perfect.
        data_loss_pct = round(frame_loss_pct(captures, grid_seconds_elapsed), 3)
        # Ingest throughput from the bridge. `ticks_per_sec` is a TRAILING-WINDOW rate,
        # not ticks_received/uptime — a lifetime average only ever creeps toward the mean
        # and cannot show the current rate (which is what the label promises).
        batches_received = int(getattr(self.bridge, "batches_received", 0)) if self.bridge else 0
        ticks_received = int(getattr(self.bridge, "ticks_received", 0)) if self.bridge else 0
        ticks_per_sec = self._ticks_rate(now, ticks_received)
        # --- session-scheduled completeness (§17) --------------------------------- #
        # The scheduled figures are uptime-independent, so ``downtime_seconds`` finally
        # makes an outage visible in the loss number instead of vanishing with the process
        # that would have counted it.
        scheduled_today, scheduled_elapsed = self._scheduled_baselines(now)
        breakdown = self._loss_breakdown(scheduled_elapsed, captures)
        scheduled_loss_pct = (
            round(frame_loss_pct(captures, scheduled_elapsed), 3) if scheduled_elapsed else 0.0
        )
        # Feed health as three separate signals plus one classification (§11/§12), kept
        # distinct from the market phase (§23) which the session registry owns.
        health = (
            self.engine.feed_health(now)
            if hasattr(self.engine, "feed_health")
            else None
        )
        artifact_ages = (
            self.engine.artifact_ages_ms(now)
            if hasattr(self.engine, "artifact_ages_ms")
            else {}
        )
        stale_artifact_names = (
            list(self.engine.stale_artifact_names(now))
            if hasattr(self.engine, "stale_artifact_names")
            else []
        )
        transport_age_ms = (
            self.engine.transport_age_ms(now)
            if hasattr(self.engine, "transport_age_ms")
            else None
        )
        market_phase = None
        capture_expected = None
        if self.session_registry is not None:
            artifact = next(iter(self.index_tables), "STOCKS")
            market_phase = self.session_registry.phase(artifact, now)
            capture_expected = self.session_registry.is_capture_expected(artifact, now)
        # Transport-level observability (§21): enough to tell a transport problem from
        # market inactivity, and enough headroom detail to see a capacity wall coming.
        transport = {
            "connected": bool(getattr(self.bridge, "connected", False))
            if self.bridge is not None
            else False,
            "queue_depth": self._queue_depth(),
            "batches_received": batches_received,
            "dropped_batches": dropped_batches,
        }
        if self.subscription is not None:
            transport.update(self.subscription.as_telemetry())
        return {
            "tokens": self._unique_token_count(),
            "fps": round(self._fps(), 3),
            "disk_bytes": self._cached_directory_bytes(now),
            "disk_free_bytes": disk_free,
            "disk_total_bytes": disk_total,
            "captures": captures,
            "dropped_batches": dropped_batches,
            "drop_rate_pct": round(drop_rate_pct(dropped_batches, captures), 4),
            "ingestion_degraded": dropped_batches > 0,
            "uptime_ms": uptime_ms,
            "frames_written": total_frames,
            "frames_expected": total_expected,
            "frame_loss_pct": round(frame_loss_pct(total_frames, total_expected), 3),
            "snapshot_ms": snapshot_ms,
            "writer_lag_max": writer_lag_max,
            # Live-feed freshness / self-healing telemetry.
            "data_age_ms": data_age_ms,
            "liveness_age_ms": liveness_age_ms,
            "stale": stale,
            "degraded": degraded,
            "frozen_batches": frozen_batches,
            "reconnects": reconnects,
            "stale_spell_seconds": stale_spell_seconds,
            "longest_stale_spell_seconds": longest_stale_spell_seconds,
            "escalations": escalations,
            "recovery_abandoned": recovery_abandoned,
            "recovery_armed": recovery_armed,
            "exhausted": exhausted,
            "token_refreshes": token_refreshes,
            "last_token_refresh_ms": last_token_refresh_ms,
            "token_age_ms": token_age_ms,
            # --- per-session data loss ---
            "grid_gaps": grid_gaps,
            "grid_seconds_lost": grid_seconds_lost,
            "stale_seconds": stale_seconds,
            "stale_events": stale_events,
            "stale_writes_suppressed": bool(
                getattr(self.engine, "suppress_stale_writes", False)
            ),
            "grid_seconds_elapsed": grid_seconds_elapsed,
            "session_frames_expected": writable_seconds,
            "session_loss_pct": session_loss_pct,
            "data_loss_pct": data_loss_pct,
            "unmatched_ticks": int(getattr(self.engine, "unmatched", 0)),
            # --- session-scheduled completeness (uptime-independent) ---
            "scheduled_seconds": scheduled_today,
            "scheduled_seconds_elapsed": scheduled_elapsed,
            "captured_seconds": captures,
            "scheduled_loss_pct": scheduled_loss_pct,
            "unscheduled_seconds": int(getattr(self.engine, "unscheduled_seconds", 0)),
            **breakdown,
            # --- feed health: three signals, one classification ---
            "feed_health": health,
            "transport_age_ms": transport_age_ms,
            "artifact_ages_ms": artifact_ages,
            "stale_artifacts": stale_artifact_names,
            # --- market phase (independent of feed health) ---
            "market_phase": market_phase,
            "capture_expected": capture_expected,
            # --- transport / subscription capacity ---
            "transport": transport,
            "batches_received": batches_received,
            "ticks_received": ticks_received,
            "ticks_per_sec": ticks_per_sec,
            "first_grid_ms": getattr(self.engine, "first_grid_ms", None),
        }

    def snapshot(self) -> dict:
        """The full ``CaptureStatus`` envelope for the ``capture-status`` topic."""
        entries = self.per_underlying()
        return protocol.capture_status(entries, self.global_metrics(entries))

    def session_summary(self, trading_date: str) -> dict:
        """A compact end-of-session record for the cross-session history log."""
        entries = self.per_underlying()
        g = self.global_metrics(entries)
        return {
            "trading_date": trading_date,
            "recorded_at": self._clock(),
            "uptime_ms": g["uptime_ms"],
            "captures": g["captures"],
            "frames_written": g["frames_written"],
            "frames_expected": g["frames_expected"],
            "frame_loss_pct": g["frame_loss_pct"],
            "session_frames_expected": g["session_frames_expected"],
            "session_loss_pct": g["session_loss_pct"],
            "grid_seconds_elapsed": g["grid_seconds_elapsed"],
            "data_loss_pct": g["data_loss_pct"],
            "grid_gaps": g["grid_gaps"],
            "grid_seconds_lost": g["grid_seconds_lost"],
            "stale_seconds": g["stale_seconds"],
            "stale_events": g["stale_events"],
            "dropped_batches": g["dropped_batches"],
            "drop_rate_pct": g["drop_rate_pct"],
            "unmatched_ticks": g["unmatched_ticks"],
            "ticks_received": g["ticks_received"],
            "reconnects": g["reconnects"],
            "token_refreshes": g["token_refreshes"],
            "longest_stale_spell_seconds": g["longest_stale_spell_seconds"],
            "escalations": g["escalations"],
            "recovery_abandoned": g["recovery_abandoned"],
            "exhausted": g["exhausted"],
            # Session-scheduled completeness: the honest "how much of this trading day is
            # missing" figure, including time the process or server was not running.
            "scheduled_seconds": g["scheduled_seconds"],
            "scheduled_seconds_elapsed": g["scheduled_seconds_elapsed"],
            "captured_seconds": g["captured_seconds"],
            "scheduled_loss_pct": g["scheduled_loss_pct"],
            "missing_seconds": g["missing_seconds"],
            "stale_feed_seconds": g["stale_feed_seconds"],
            "downtime_seconds": g["downtime_seconds"],
            "write_path_seconds": g["write_path_seconds"],
            "unclassified_seconds": g["unclassified_seconds"],
            "disk_bytes": g["disk_bytes"],
            "streams": [
                {
                    "underlying": e["underlying"],
                    "frames_written": e["frames_written"],
                    "frame_loss_pct": e["frame_loss_pct"],
                    "file_bytes": e["file_bytes"],
                }
                for e in entries
            ],
        }
