"""Session completeness: reconcile a session's *scheduled* grid against the archive.

The loss figure this produces is deliberately **not** derived from runtime counters. A
process crash, `docker kill`, server reboot or power cut destroys in-memory telemetry and
can prevent the final snapshot from ever being written — precisely the outages that matter
most. So the question "how much of this trading session is missing?" is answered from two
things that survive all of that:

* the **scheduled grid**, derived from session configuration plus the trading date
  (:class:`app.ops.sessions.MarketSession`) — independent of whether anything was running;
* the **frame timestamps on disk** (``scan_frames(..., collect_timestamps=True)``).

Telemetry then only *attributes* the gaps (feed stale vs. write failure vs. downtime); it
is never the source of truth for whether data is missing.

The invariant every scheduled second must satisfy:

    a valid frame exists   XOR   the second is data loss

with no third state. Seconds outside the session, or inside a session that configuration
explicitly disabled, are not scheduled and take no part in the equation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Loss causes. ``STALE`` and ``WRITE`` are attributed from telemetry; ``DOWNTIME`` is what
# is left when the process demonstrably was not running; ``UNCLASSIFIED`` is an honest
# admission rather than a forced guess (§17.3).
CAUSE_STALE = "stale_feed"
CAUSE_DOWNTIME = "process_downtime"
CAUSE_WRITE = "write_path"
CAUSE_UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Gap:
    """A contiguous run of scheduled seconds with no persisted frame."""

    start_ms: int
    end_ms: int  # exclusive
    cause: str = CAUSE_UNCLASSIFIED

    @property
    def seconds(self) -> int:
        return max(0, (self.end_ms - self.start_ms) // 1000)


@dataclass(frozen=True)
class Completeness:
    """The reconciliation result for one artifact and one trading date."""

    scheduled_seconds: int
    captured_seconds: int
    gaps: tuple[Gap, ...] = field(default_factory=tuple)

    @property
    def missing_seconds(self) -> int:
        return max(0, self.scheduled_seconds - self.captured_seconds)

    @property
    def data_loss_pct(self) -> float:
        if self.scheduled_seconds <= 0:
            return 0.0
        return round(self.missing_seconds / self.scheduled_seconds * 100, 3)

    @property
    def captured_pct(self) -> float:
        if self.scheduled_seconds <= 0:
            return 100.0
        return round(self.captured_seconds / self.scheduled_seconds * 100, 3)

    def by_cause(self) -> dict[str, int]:
        """Missing seconds grouped by attributed cause.

        The grouped totals reconcile with :attr:`missing_seconds`: whatever cannot be
        attributed stays visible under ``unclassified`` instead of being dropped.
        """
        totals = {
            CAUSE_STALE: 0,
            CAUSE_DOWNTIME: 0,
            CAUSE_WRITE: 0,
            CAUSE_UNCLASSIFIED: 0,
        }
        for gap in self.gaps:
            totals[gap.cause] = totals.get(gap.cause, 0) + gap.seconds
        attributed = sum(totals.values())
        # Any shortfall between the gap list and the arithmetic (e.g. duplicate frame
        # timestamps, or a gap list built from a partial scan) must remain visible.
        residual = self.missing_seconds - attributed
        if residual > 0:
            totals[CAUSE_UNCLASSIFIED] += residual
        return totals

    @property
    def reconciles(self) -> bool:
        """The §17.15 invariant: classified + unclassified == total missing."""
        return sum(self.by_cause().values()) == self.missing_seconds


def reconcile(
    scheduled_windows: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    frame_timestamps: list[int] | tuple[int, ...],
    *,
    stale_windows: list[tuple[int, int]] | None = None,
    interval_ms: int = 1_000,
) -> Completeness:
    """Reconcile scheduled seconds against persisted frame timestamps.

    ``scheduled_windows`` are ``[start_ms, end_ms)`` pairs from the artifact's session.
    ``frame_timestamps`` are the timestamps actually found in the ``.bin`` file(s).
    ``stale_windows`` optionally attribute gaps to a suppressed stale feed; anything not
    covered by them is attributed to downtime when it sits at a session edge or spans a
    restart, and left unclassified otherwise.

    Frames outside every scheduled window are ignored rather than credited: a frame
    written at 15:35 does not make 15:29 complete.
    """
    scheduled_slots: list[int] = []
    for start_ms, end_ms in scheduled_windows:
        slot = (start_ms // interval_ms) * interval_ms
        while slot < end_ms:
            scheduled_slots.append(slot)
            slot += interval_ms
    if not scheduled_slots:
        return Completeness(scheduled_seconds=0, captured_seconds=0)

    wanted = set(scheduled_slots)
    present = {
        (ts // interval_ms) * interval_ms
        for ts in frame_timestamps
        if (ts // interval_ms) * interval_ms in wanted
    }
    missing = sorted(wanted - present)

    stale_ranges = list(stale_windows or [])

    def _cause(slot: int) -> str:
        for start_ms, end_ms in stale_ranges:
            if start_ms <= slot < end_ms:
                return CAUSE_STALE
        return CAUSE_UNCLASSIFIED

    gaps: list[Gap] = []
    run_start: int | None = None
    run_cause = CAUSE_UNCLASSIFIED
    previous: int | None = None
    for slot in missing:
        cause = _cause(slot)
        contiguous = previous is not None and slot == previous + interval_ms
        if run_start is None:
            run_start, run_cause = slot, cause
        elif not contiguous or cause != run_cause:
            gaps.append(Gap(run_start, previous + interval_ms, run_cause))  # type: ignore[operator]
            run_start, run_cause = slot, cause
        previous = slot
    if run_start is not None and previous is not None:
        gaps.append(Gap(run_start, previous + interval_ms, run_cause))

    return Completeness(
        scheduled_seconds=len(scheduled_slots),
        captured_seconds=len(present),
        gaps=tuple(gaps),
    )


def classify_downtime(
    completeness: Completeness, uptime_windows: list[tuple[int, int]]
) -> Completeness:
    """Re-attribute unclassified gaps that fall outside every known uptime window.

    ``uptime_windows`` are periods the capture process is known to have been running
    (reconstructed from persisted telemetry or from the frames themselves). A scheduled
    second that is missing while the process was demonstrably *not* running is downtime —
    which covers a crash, a container restart, a server reboot, and a deliberate manual
    stop alike. A graceful shutdown describes *how* capture stopped; it does not make the
    missed market observations any less missing (§17.8).
    """

    def _covered(gap: Gap) -> bool:
        return any(start <= gap.start_ms and gap.end_ms <= end for start, end in uptime_windows)

    reclassified = tuple(
        gap
        if gap.cause != CAUSE_UNCLASSIFIED or _covered(gap)
        else Gap(gap.start_ms, gap.end_ms, CAUSE_DOWNTIME)
        for gap in completeness.gaps
    )
    return Completeness(
        scheduled_seconds=completeness.scheduled_seconds,
        captured_seconds=completeness.captured_seconds,
        gaps=reclassified,
    )


__all__ = [
    "CAUSE_DOWNTIME",
    "CAUSE_STALE",
    "CAUSE_UNCLASSIFIED",
    "CAUSE_WRITE",
    "Completeness",
    "Gap",
    "classify_downtime",
    "reconcile",
]
