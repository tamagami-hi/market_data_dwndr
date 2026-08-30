"""Archive-truth session reconciliation, run at EOD.

The one loss figure that cannot be fooled by a counter. Every percentage the dashboard
shows during a session is derived from in-memory telemetry, and three of the four use a
denominator bounded by what the *process itself* observed — so an outage that delays the
start or ends the day early shrinks its own denominator and disappears from the metric. On
2026-08-28 that is exactly what happened: 14.69% of the session was missing while the
field labelled "data loss" read 0.77%.

This module answers the question from two things that survive a crash, a container kill, a
reboot and a power cut:

* the **scheduled grid**, derived from session configuration plus the trading date
  (:mod:`app.ops.sessions`) — independent of whether anything was running;
* the **frame timestamps on disk** (``scan_frames(..., collect_timestamps=True)``).

:mod:`app.ops.completeness` already implemented the reconciliation and had no caller. This
wires it to the EOD sweep and records the result per artifact, so every trading date ends
with one authoritative, archive-derived completeness record.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from app.bin_codec.scan import scan_frames
from app.ops.completeness import CAUSE_STALE, Completeness, reconcile

logger = logging.getLogger(__name__)


def segment_paths(day_path: str | os.PathLike[str]) -> list[Path]:
    """Every file holding frames for one artifact-day, including rolled segments.

    A strike-axis change mid-day rolls capture into ``<date>.pN.bin`` (see
    ``app.bin_codec.writer.resolve_segment_path``), so completeness has to consider all of
    them or it would report the rolled seconds as missing.
    """
    base = Path(day_path)
    found = [base] if base.exists() else []
    found.extend(sorted(base.parent.glob(f"{base.stem}.p*{base.suffix}")))
    return found


def artifact_timestamps(day_path: str | os.PathLike[str]) -> tuple[int, ...]:
    """Frame timestamps across every segment for one artifact-day, ascending."""
    stamps: list[int] = []
    for path in segment_paths(day_path):
        stamps.extend(scan_frames(path, collect_timestamps=True).timestamps)
    return tuple(sorted(stamps))


def reconcile_artifact(
    day_path: str | os.PathLike[str],
    scheduled_windows: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    *,
    stale_windows: list[tuple[int, int]] | None = None,
) -> Completeness:
    """Reconcile one artifact-day's archive against its scheduled grid."""
    return reconcile(
        scheduled_windows,
        artifact_timestamps(day_path),
        stale_windows=stale_windows,
    )


def reconcile_session(
    artifact_paths: Mapping[str, str | os.PathLike[str]],
    scheduled_windows: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    *,
    trading_date: str,
    stale_windows: list[tuple[int, int]] | None = None,
) -> dict:
    """Reconcile every artifact of a trading date; returns a JSON-friendly record.

    The session-level figure is the **worst** artifact, not the average: a day in which one
    dataset lost an hour is not 12% complete-per-artifact healthy, and pooling would dilute
    it by the number of healthy artifacts (with eight artifacts a wholly dead one reads as
    12.5% loss rather than 100%).
    """
    artifacts: dict[str, dict] = {}
    worst_loss = 0.0
    worst_name: str | None = None
    for name, path in artifact_paths.items():
        result = reconcile_artifact(path, scheduled_windows, stale_windows=stale_windows)
        by_cause = result.by_cause()
        artifacts[name] = {
            "scheduled_seconds": result.scheduled_seconds,
            "captured_seconds": result.captured_seconds,
            "missing_seconds": result.missing_seconds,
            "data_loss_pct": result.data_loss_pct,
            "captured_pct": result.captured_pct,
            "gap_count": len(result.gaps),
            "longest_gap_seconds": max((g.seconds for g in result.gaps), default=0),
            "stale_attributed_seconds": by_cause.get(CAUSE_STALE, 0),
            "segments": [p.name for p in segment_paths(path)],
            "reconciles": result.reconciles,
        }
        if result.data_loss_pct >= worst_loss:
            worst_loss = result.data_loss_pct
            worst_name = name

    record = {
        "trading_date": trading_date,
        "source": "archive",
        "artifacts": artifacts,
        "worst_artifact": worst_name,
        "data_loss_pct": worst_loss,
        "artifact_count": len(artifacts),
    }
    if artifacts:
        logger.info(
            "archive reconciliation for %s: worst artifact %s at %.3f%% loss across %d "
            "artifact(s)",
            trading_date,
            worst_name,
            worst_loss,
            len(artifacts),
        )
    return record


__all__ = [
    "artifact_timestamps",
    "reconcile_artifact",
    "reconcile_session",
    "segment_paths",
]
