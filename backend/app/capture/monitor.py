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
from pathlib import Path

from app.capture.writer_thread import FileWriterThread
from app.chain.table import IndexTable
from app.session import now_ms
from app.stocks.matrix import StockMatrix
from app.ws import protocol

HEARTBEAT_WINDOW_MS = 2_000


def directory_bytes(root: str | os.PathLike[str]) -> int:
    """Total size in bytes of every file under ``root`` (0 if missing)."""
    root = Path(root)
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


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
        last_tick_ms = self.engine.stall.last_message_ms if self.engine is not None else None
        connected = bool(self.bridge.connected) if self.bridge is not None else False
        return {
            "underlying": underlying,
            "connected": connected,
            "last_tick_ms": last_tick_ms,
            "frames_written": frames,
            "file_bytes": file_bytes,
            "heartbeat_ok": heartbeat_ok,
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
        return {
            "tokens": self._unique_token_count(),
            "fps": round(self._fps(), 3),
            "disk_bytes": directory_bytes(self.market_data_path) if self.market_data_path else 0,
            "captures": self.engine.captures if self.engine is not None else 0,
        }

    def snapshot(self) -> dict:
        """The full ``CaptureStatus`` envelope for the ``capture-status`` topic."""
        return protocol.capture_status(self.per_underlying(), self.global_metrics())
