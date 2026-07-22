"""Immutable hand-off from the capture loop to persistence and display workers."""

from __future__ import annotations

from dataclasses import dataclass

from app.bin_codec.layout import IndexFrame, StockFrame


@dataclass(frozen=True)
class CaptureSnapshot:
    """Copied 1 Hz frames; producers never mutate them after construction."""

    timestamp_unix_ms: int
    index_frames: tuple[tuple[str, IndexFrame], ...]
    stock_frame: StockFrame | None

    @property
    def frame_count(self) -> int:
        return len(self.index_frames) + int(self.stock_frame is not None)

