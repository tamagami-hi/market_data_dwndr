"""Capture Monitor metrics.

Builds the ``CaptureStatus`` telemetry that drives the frontend dashboard
(docs/50-frontend/frontend.md):

- **per-underlying** (each index + the stocks file): connected, last tick time, frames
  written, current file size, 1 Hz heartbeat (a frame written in the last ~2 s),
  unmatched-tick counter.
- **global**: total unique tokens subscribed, frames/sec, ``MARKET_DATA`` disk usage.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.capture.writer_thread import FileWriterThread
from app.chain.table import IndexTable
from app.session import now_ms
from app.stocks.matrix import StockMatrix
from app.ws import protocol

HEARTBEAT_WINDOW_MS = 2_000

# Full-session frame baseline (09:00-15:30 @ 1 Hz). Overridable via Settings.
DEFAULT_EXPECTED_FRAMES = 23_400


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
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        self.engine = engine
        self.bridge = bridge
        self.market_data_path = market_data_path
        self._clock = clock
        self.heartbeat_window_ms = heartbeat_window_ms
        self.expected_frames = expected_frames if expected_frames > 0 else DEFAULT_EXPECTED_FRAMES
        # Capture start timestamp for uptime; defaults to first construction time.
        self.capture_start_ms = capture_start_ms if capture_start_ms is not None else clock()
        # fps rate tracking
        self._last_fps_time: int | None = None
        self._last_capture_count = 0

    def _entry(self, underlying: str, unmatched: int, writer: FileWriterThread | None) -> dict:
        now = self._clock()
        frames = writer.frames_written if writer else 0
        last_write = writer.last_write_ms if writer else None
        file_bytes = 0
        if writer is not None and writer.path.exists():
            file_bytes = writer.path.stat().st_size
        heartbeat_ok = last_write is not None and (now - last_write) <= self.heartbeat_window_ms
        heartbeat_age_ms = (now - last_write) if last_write is not None else None
        last_tick_ms = self.engine.stall.last_message_ms if self.engine is not None else None
        connected = bool(self.bridge.connected) if self.bridge is not None else False
        return {
            "underlying": underlying,
            "connected": connected,
            "last_tick_ms": last_tick_ms,
            "frames_written": frames,
            "frames_expected": self.expected_frames,
            "frame_loss_pct": round(frame_loss_pct(frames, self.expected_frames), 3),
            "file_bytes": file_bytes,
            "avg_bytes_per_frame": round(avg_bytes_per_frame(file_bytes, frames), 1),
            "projected_eod_bytes": projected_eod_bytes(file_bytes, frames, self.expected_frames),
            "heartbeat_ok": heartbeat_ok,
            "heartbeat_age_ms": heartbeat_age_ms,
            "unmatched": unmatched,
        }

    def per_underlying(self) -> list[dict]:
        entries = [
            self._entry(name, table.unmatched, self.index_writers.get(name))
            for name, table in self.index_tables.items()
        ]
        if self.stock_matrix is not None:
            entries.append(self._entry("STOCKS", self.stock_matrix.unmatched, self.stock_writer))
        return entries

    def _unique_token_count(self) -> int:
        tokens: set[int] = set()
        for table in self.index_tables.values():
            tokens.update(table.tokens)
        if self.stock_matrix is not None:
            tokens.update(self.stock_matrix.tokens)
        return len(tokens)

    def _fps(self) -> float:
        """Frames-per-second since the previous call (0 on the first call)."""
        if self.engine is None:
            return 0.0
        now = self._clock()
        captures = self.engine.captures
        if self._last_fps_time is None:
            self._last_fps_time = now
            self._last_capture_count = captures
            return 0.0
        elapsed_ms = now - self._last_fps_time
        delta = captures - self._last_capture_count
        self._last_fps_time = now
        self._last_capture_count = captures
        if elapsed_ms <= 0:
            return 0.0
        return delta / (elapsed_ms / 1000.0)

    def global_metrics(self) -> dict:
        dropped_batches = self.bridge.dropped_batches if self.bridge is not None else 0
        captures = self.engine.captures if self.engine is not None else 0
        now = self._clock()
        uptime_ms = max(0, now - self.capture_start_ms)
        disk_free, disk_total = disk_usage(self.market_data_path)
        # Overall frame integrity: sum of frames vs sum of per-underlying baselines.
        entries = self.per_underlying()
        total_frames = sum(int(e["frames_written"]) for e in entries)
        total_expected = self.expected_frames * len(entries) if entries else 0
        return {
            "tokens": self._unique_token_count(),
            "fps": round(self._fps(), 3),
            "disk_bytes": directory_bytes(self.market_data_path) if self.market_data_path else 0,
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
        }

    def snapshot(self) -> dict:
        """The full ``CaptureStatus`` envelope for the ``capture-status`` topic."""
        return protocol.capture_status(self.per_underlying(), self.global_metrics())
