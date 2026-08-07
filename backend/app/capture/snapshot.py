"""Immutable hand-off from the capture loop to persistence and display workers."""

from __future__ import annotations

from dataclasses import dataclass

from app.bin_codec.layout import IndexFnoFrame, IndexFrame, StockFrame


@dataclass(frozen=True)
class CaptureSnapshot:
    """Copied 1 Hz frames; producers never mutate them after construction.

    ``stale`` marks a grid second whose values did not change (frozen or absent feed).
    Such a snapshot is still handed to the display worker — the dashboard should keep
    rendering the last known board, flagged stale — but it is **not** persisted, so the
    ``.bin`` files only ever contain market data that actually moved. ``written`` records
    what the persistence layer decided, so callers never have to re-derive it.

    ``scheduled`` distinguishes the two very different reasons a frame may be absent:
    a *scheduled* second with no frame is data loss, while an *unscheduled* one (outside
    the artifact's market session, or a session explicitly disabled by configuration) is
    simply not expected data and must never enter the loss figure.
    """

    timestamp_unix_ms: int
    index_frames: tuple[tuple[str, IndexFrame], ...]
    stock_frame: StockFrame | None
    stale: bool = False
    written: bool = True
    scheduled: bool = True
    # The consolidated index-F&O frame (index futures + spot on the same grid), when that
    # domain is enabled. Carried separately from ``stock_frame`` because index and stock
    # derivatives are independent domains with independently evolvable schemas.
    index_fno_frame: IndexFnoFrame | None = None

