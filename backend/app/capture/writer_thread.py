"""One dedicated writer thread per ``.bin`` file.

Serializing + writing releases the GIL (NumPy ``tobytes`` + ``file.write``), so a
thread per file gives isolation without contention -- a slow disk op on one file never
delays another (docs/10-architecture/concurrency-and-gil.md). At 1 Hz the thread is
near-idle; it simply drains a queue of frames and appends them.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from app.session import now_ms

# Sentinel enqueued to request a graceful stop.
_STOP = object()


class FileWriterThread(threading.Thread):
    """Owns a single BIN writer; drains a queue of frames and appends them.

    ``bin_writer`` is an unopened ``IndexBinWriter`` / ``StockBinWriter``. The header is
    written once on startup (header-once semantics make restart safe).
    """

    def __init__(self, bin_writer: Any, header: Any, name: str | None = None) -> None:
        super().__init__(name=name or f"writer-{id(self):x}", daemon=True)
        self._writer = bin_writer
        self._header = header
        self._queue: queue.Queue = queue.Queue()
        self.frames_written = 0
        self.last_write_ms: int | None = None
        self._started_ok = threading.Event()
        self._error: BaseException | None = None

    def run(self) -> None:  # pragma: no cover - exercised via integration test
        try:
            self._writer.open()
            self._writer.write_header(self._header)
            self._started_ok.set()
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._started_ok.set()
            return
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    break
                self._writer.append_frame(item)
                self.frames_written += 1
                self.last_write_ms = now_ms()
        finally:
            self._writer.close()

    def enqueue(self, frame: Any) -> None:
        self._queue.put(frame)

    def stop(self, join: bool = True, timeout: float | None = 5.0) -> None:
        """Signal a graceful stop after all queued frames are written."""
        self._queue.put(_STOP)
        if join:
            self.join(timeout=timeout)

    def wait_until_ready(self, timeout: float | None = 5.0) -> None:
        """Block until the header has been written (or startup failed)."""
        self._started_ok.wait(timeout=timeout)
        if self._error is not None:
            raise self._error

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def path(self) -> Path:
        return Path(self._writer.path)
