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


class WriterThreadError(RuntimeError):
    """Raised when a BIN writer cannot safely accept or persist more frames."""


class WriterShutdownError(WriterThreadError):
    """Raised when a writer has not proven that its queue drained and thread stopped."""


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
        self._stop_requested = False

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
        except BaseException as exc:  # noqa: BLE001 - propagate through health checks
            self._error = exc
        finally:
            try:
                self._writer.close()
            except BaseException as exc:  # noqa: BLE001 - close/flush failure is fatal
                if self._error is None:
                    self._error = exc

    def enqueue(self, frame: Any) -> None:
        self.check_health()
        if self._stop_requested:
            raise WriterShutdownError(f"writer {self.name} is already stopping")
        self._queue.put(frame)

    def stop(self, join: bool = True, timeout: float | None = 5.0) -> None:
        """Signal a graceful stop after all queued frames are written."""
        self.request_stop()
        if join:
            self.join(timeout=timeout)
            if self.is_alive():
                raise WriterShutdownError(
                    f"writer {self.name} did not stop after draining deadline"
                )
            self.check_health()

    def request_stop(self) -> None:
        """Enqueue the stop sentinel once without waiting for the writer."""
        if self._stop_requested:
            return
        self._stop_requested = True
        self._queue.put(_STOP)

    def check_health(self) -> None:
        """Raise a redacted error when open, append, flush, or close failed."""
        if self._error is not None:
            raise WriterThreadError(
                f"writer {self.name} failed ({type(self._error).__name__})"
            ) from self._error

    def wait_until_ready(self, timeout: float | None = 5.0) -> None:
        """Block until the header has been written (or startup failed)."""
        if not self._started_ok.wait(timeout=timeout):
            raise WriterShutdownError(f"writer {self.name} did not become ready")
        self.check_health()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def path(self) -> Path:
        return Path(self._writer.path)
