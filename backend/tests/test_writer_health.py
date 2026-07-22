"""Persistence failure and drain-proof tests for the capture writer boundary."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.capture.writer_thread import (
    FileWriterThread,
    WriterShutdownError,
    WriterThreadError,
)


class _BlockingWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.append_started = threading.Event()
        self.allow_append = threading.Event()

    def open(self):
        return self

    def write_header(self, _header) -> None:
        return None

    def append_frame(self, _frame) -> None:
        self.append_started.set()
        self.allow_append.wait()

    def close(self) -> None:
        return None


class _FailingWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def open(self):
        return self

    def write_header(self, _header) -> None:
        return None

    def append_frame(self, _frame) -> None:
        raise OSError("simulated disk failure")

    def close(self) -> None:
        return None


def test_stop_raises_until_a_slow_writer_has_proven_it_terminated(tmp_path):
    backend = _BlockingWriter(tmp_path / "slow.bin")
    writer = FileWriterThread(backend, object(), name="slow-writer")
    writer.start()
    writer.wait_until_ready()
    writer.enqueue(object())
    assert backend.append_started.wait(timeout=1)

    with pytest.raises(WriterShutdownError, match="did not stop"):
        writer.stop(timeout=0.01)

    backend.allow_append.set()
    writer.join(timeout=1)
    assert writer.is_alive() is False


def test_append_failure_is_propagated_and_future_frames_are_rejected(tmp_path):
    writer = FileWriterThread(
        _FailingWriter(tmp_path / "failed.bin"), object(), name="failed-writer"
    )
    writer.start()
    writer.wait_until_ready()
    writer.enqueue(object())
    writer.join(timeout=1)

    with pytest.raises(WriterThreadError, match="failed-writer"):
        writer.check_health()
    with pytest.raises(WriterThreadError, match="failed-writer"):
        writer.enqueue(object())
