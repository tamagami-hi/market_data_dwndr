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

from app.capture.reconnect import ReconnectPolicy, StallDetector
from app.capture.writer_thread import FileWriterThread
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
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.index_writers = index_writers
        self.stock_writer = stock_writer
        self._clock = clock
        self.unmatched = 0
        self.captures = 0
        self.stall = StallDetector()
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
        ts = timestamp_unix_ms if timestamp_unix_ms is not None else self._clock()
        n = 0
        for name, table in self.index_tables.items():
            writer = self.index_writers.get(name)
            if writer is not None:
                writer.enqueue(table.snapshot(ts))
                n += 1
        if self.stock_matrix is not None and self.stock_writer is not None:
            self.stock_writer.enqueue(self.stock_matrix.snapshot(ts))
            n += 1
        self.captures += 1
        return n

    # -- writer lifecycle -------------------------------------------------- #

    def _all_writers(self) -> list[FileWriterThread]:
        writers = list(self.index_writers.values())
        if self.stock_writer is not None:
            writers.append(self.stock_writer)
        return writers

    def start_writers(self) -> None:
        for w in self._all_writers():
            w.start()
        for w in self._all_writers():
            w.wait_until_ready()

    def stop_writers(self) -> None:
        for w in self._all_writers():
            w.stop()

    # -- async live loop --------------------------------------------------- #

    async def run(
        self,
        bridge,
        stop_event: asyncio.Event,
        interval_s: float = 1.0,
    ) -> None:  # pragma: no cover - live loop, integration-only
        """Consume ticks and snapshot every ``interval_s`` until ``stop_event`` is set."""
        self.start_writers()
        consumer = asyncio.create_task(self._consume(bridge))
        try:
            while not stop_event.is_set():
                await asyncio.sleep(interval_s)
                self.capture_once()
        finally:
            consumer.cancel()
            self.stop_writers()

    async def _consume(self, bridge) -> None:  # pragma: no cover - live loop
        async for batch in bridge.batches():
            self.apply_ticks(batch)
            self.stall.mark_message(self._clock())


def build_index_writer(table: IndexTable, path) -> FileWriterThread:
    """Convenience: a writer thread for an index table's file."""
    from app.bin_codec.writer import IndexBinWriter

    return FileWriterThread(
        IndexBinWriter(path), table.header(), name=f"idx-{table.chain.underlying}"
    )


def build_stock_writer(matrix: StockMatrix, path) -> FileWriterThread:
    """Convenience: a writer thread for the stock matrix file."""
    from app.bin_codec.writer import StockBinWriter

    return FileWriterThread(StockBinWriter(path), matrix.header(), name="stocks")


__all__ = [
    "CaptureEngine",
    "FileWriterThread",
    "ReconnectPolicy",
    "StallDetector",
    "build_index_writer",
    "build_stock_writer",
]
