"""Read a day's incident ledger as a timeline and attribute owed time to causes.

Pure functions over the ledger produced by :mod:`app.ops.incidents`; nothing here touches
the disk. The output answers what the session summary cannot: of the scheduled seconds
the capture process was *not* producing frames for, how many were a restart loop, a
single restart, a wait for the broker token, a maintenance window, or simply unexplained.

How the timeline is read
------------------------
A collapsed entry (``count`` > 1) is replayed as two instants, its ``first_at`` and its
``last_at``; the occurrences in between are identical and lie inside that span, so the
boundaries of every interval below survive collapsing. Instants are replayed in time
order to build *capture runs*:

* a run starts at ``capture_start`` and ends at the next ``capture_failed`` /
  ``capture_stopped`` / ``capture_start`` / ``process_start``;
* a run is **live** when it ended in a clean stop or in a failure *after* the ticker had
  confirmed a subscription (``feed_live``), **failed** when it died without ever
  confirming one (``TickerConnectFailed`` and friends), and *unknown* when the process
  vanished without a word (treated as live, so the ledger never over-claims downtime).

Every gap between live runs inside the scheduled windows is an **outage**, classified by
what opened it and what happened inside it:

=================  ==========================================================================
``restart_loop``   at least one failed (non-auth) start inside the gap — "failed to connect"
``restart``        opened by a capture failure that exited the process; recovered first time
``awaiting_token`` opened by a broker-auth failure, or every failed start was an auth failure
``maintenance``    opened by a maintenance stop
``process_down``   anything else: late start, operator stop, process gone with no record
=================  ==========================================================================

Two overlays then take precedence inside any gap: a held maintenance lease
(``maintenance``) and a non-OK broker auth state (``awaiting_token``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.ops.incidents import (
    KIND_AUTH_STATE,
    KIND_CAPTURE_FAILED,
    KIND_CAPTURE_START,
    KIND_CAPTURE_STOPPED,
    KIND_EOD,
    KIND_MAINTENANCE_ACQUIRED,
    KIND_MAINTENANCE_RELEASED,
    KIND_PROCESS_START,
    KIND_START_FAILED,
    STOP_REASON_MAINTENANCE,
)

# Failures that are an escalation to a process restart for a dead feed / dead pipeline.
STALL_EXCEPTIONS = frozenset({"CaptureStalledError", "CaptureConsumerFailed"})
AUTH_EXCEPTIONS = frozenset({"KiteAuthenticationError"})
AUTH_OK = "OK"

CAUSE_RESTART_LOOP = "restart_loop"
CAUSE_RESTART = "restart"
CAUSE_AWAITING_TOKEN = "awaiting_token"
CAUSE_MAINTENANCE = "maintenance"
CAUSE_PROCESS_DOWN = "process_down"
CAUSES = (
    CAUSE_RESTART_LOOP,
    CAUSE_RESTART,
    CAUSE_AWAITING_TOKEN,
    CAUSE_MAINTENANCE,
    CAUSE_PROCESS_DOWN,
)

Window = tuple[int, int]


@dataclass(frozen=True)
class _Instant:
    at: int
    order: int
    entry: Mapping


@dataclass(frozen=True)
class _Run:
    start: int | None
    end: int | None
    live: bool | None
    exception: str | None = None
    stop_reason: str | None = None
    skipped: tuple[str, ...] = ()


def _instants(entries: Sequence[Mapping]) -> list[_Instant]:
    out: list[_Instant] = []
    for order, entry in enumerate(entries):
        first, last = int(entry["first_at"]), int(entry["last_at"])
        out.append(_Instant(first, order, entry))
        if int(entry.get("count") or 1) > 1 and last != first:
            out.append(_Instant(last, order, entry))
    return sorted(out, key=lambda i: (i.at, i.order))


def _runs(instants: Iterable[_Instant]) -> list[_Run]:
    """Replay the instants into capture runs (see the module docstring)."""
    runs: list[_Run] = []
    current: tuple[int, tuple[str, ...]] | None = None
    for instant in instants:
        entry, kind = instant.entry, instant.entry["kind"]
        start, skipped = current if current is not None else (None, ())
        if kind == KIND_CAPTURE_START:
            if current is not None:
                runs.append(_Run(start, instant.at, None, skipped=skipped))
            current = (instant.at, tuple(entry.get("skipped_indices") or ()))
        elif kind == KIND_CAPTURE_FAILED:
            runs.append(
                _Run(start, instant.at, bool(entry.get("feed_live")),
                     exception=entry.get("exception"), skipped=skipped)
            )
            current = None
        elif kind == KIND_CAPTURE_STOPPED:
            runs.append(
                _Run(start, instant.at, True, stop_reason=entry.get("reason"), skipped=skipped)
            )
            current = None
        elif kind == KIND_PROCESS_START and current is not None:
            runs.append(_Run(start, instant.at, None, skipped=skipped))
            current = None
        elif kind == KIND_START_FAILED:
            runs.append(_Run(instant.at, instant.at, False, exception=entry.get("exception")))
    if current is not None:
        runs.append(_Run(current[0], None, None, skipped=current[1]))
    return runs


def _overlay(
    instants: Iterable[_Instant], opens, closes, horizon: int, *, expiry_key: str | None = None
) -> list[Window]:
    """Intervals opened by instants matching ``opens`` and closed by ``closes``."""
    spans: list[Window] = []
    opened_at: int | None = None
    expires_at: int | None = None
    for instant in instants:
        if opened_at is None and opens(instant.entry):
            opened_at = instant.at
            raw = instant.entry.get(expiry_key) if expiry_key else None
            expires_at = int(raw) if isinstance(raw, int) else None
        elif opened_at is not None and closes(instant.entry):
            spans.append((opened_at, instant.at))
            opened_at = None
    if opened_at is not None:
        end = horizon if expires_at is None else min(horizon, expires_at)
        spans.append((opened_at, max(opened_at, end)))
    return spans


def scheduled_ms(windows: Sequence[Window], start: int, end: int) -> int:
    """Milliseconds of ``[start, end)`` that fall inside the scheduled windows."""
    return sum(max(0, min(end, w_end) - max(start, w_start)) for w_start, w_end in windows)


def _covered(spans: Sequence[Window], start: int, end: int) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def _sum_counts(entries: Iterable[Mapping], predicate) -> int:
    return sum(int(e.get("count") or 1) for e in entries if predicate(e))


def _overlaps(entry: Mapping, start: int, end: int) -> bool:
    return int(entry["first_at"]) <= end and int(entry["last_at"]) >= start


def _gaps(runs: Sequence[_Run], lo: int, hi: int) -> list[tuple[int, int, _Run | None]]:
    """Gaps between live runs inside ``[lo, hi]``, each with the run that opened it."""
    live = sorted(
        (r for r in runs if r.live is not False),
        key=lambda r: (r.start if r.start is not None else lo),
    )
    gaps: list[tuple[int, int, _Run | None]] = []
    cursor, opener = lo, None
    for run in live:
        start = lo if run.start is None else run.start
        end = hi if run.end is None else run.end
        if start > cursor:
            gaps.append((cursor, min(start, hi), opener))
        if end >= cursor:
            cursor, opener = end, run
        elif cursor == lo:
            opener = run  # ended before the schedule opened: it still explains the gap
    if cursor < hi:
        gaps.append((cursor, hi, opener))
    return [g for g in gaps if g[1] > g[0]]


def _base_cause(opener: _Run | None, failed: Sequence[Mapping]) -> str:
    non_auth = [e for e in failed if e.get("exception") not in AUTH_EXCEPTIONS]
    if non_auth:
        return CAUSE_RESTART_LOOP
    if failed:
        return CAUSE_AWAITING_TOKEN
    if opener is None:
        return CAUSE_PROCESS_DOWN
    if opener.exception in AUTH_EXCEPTIONS:
        return CAUSE_AWAITING_TOKEN
    if opener.exception is not None:
        return CAUSE_RESTART
    if opener.stop_reason == STOP_REASON_MAINTENANCE:
        return CAUSE_MAINTENANCE
    return CAUSE_PROCESS_DOWN


def _is_failed_start(entry: Mapping) -> bool:
    if entry["kind"] == KIND_START_FAILED:
        return True
    return entry["kind"] == KIND_CAPTURE_FAILED and not entry.get("feed_live")


def _outages(
    entries: Sequence[Mapping],
    runs: Sequence[_Run],
    windows: Sequence[Window],
    horizon: int,
    overlays: Mapping[str, Sequence[Window]],
) -> tuple[list[dict], dict[str, int]]:
    cause_ms = dict.fromkeys(CAUSES, 0)
    outages: list[dict] = []
    if not windows:
        return outages, cause_ms
    lo = min(w[0] for w in windows)
    for start, end, opener in _gaps(runs, lo, horizon):
        failed = [e for e in entries if _is_failed_start(e) and _overlaps(e, start, end)]
        base = _base_cause(opener, failed)
        points = sorted(
            {start, end}
            | {
                point
                for spans in overlays.values()
                for span in spans
                for point in span
                if start < point < end
            }
        )
        for seg_start, seg_end in zip(points, points[1:], strict=False):
            if _covered(overlays[CAUSE_MAINTENANCE], seg_start, seg_end):
                cause = CAUSE_MAINTENANCE
            elif _covered(overlays[CAUSE_AWAITING_TOKEN], seg_start, seg_end):
                cause = CAUSE_AWAITING_TOKEN
            else:
                cause = base
            cause_ms[cause] += scheduled_ms(windows, seg_start, seg_end)
        seconds = scheduled_ms(windows, start, end) // 1000
        if seconds <= 0:
            continue
        starts = [
            e for e in entries if e["kind"] == KIND_CAPTURE_START and _overlaps(e, start, end)
        ]
        outages.append(
            {
                "cause": base,
                "start_at": start,
                "end_at": end,
                # False when the outage was still open at the end of the schedule (or now).
                "recovered": end < horizon,
                "seconds": int(seconds),
                "trigger": opener.exception if opener is not None else None,
                "failed_starts": _sum_counts(failed, lambda _e: True),
                "exceptions": sorted({str(e.get("exception")) for e in failed}),
                "skipped_indices": sorted(
                    {name for e in starts for name in e.get("skipped_indices") or ()}
                ),
            }
        )
    return outages, cause_ms


def _datasets_missing(
    entries: Sequence[Mapping], runs: Sequence[_Run], windows: Sequence[Window], horizon: int
) -> list[dict]:
    """Datasets a capture run started without, and for how many scheduled seconds."""
    since: dict[str, int] = {}
    spans: dict[str, list[Window]] = {}
    for run in sorted((r for r in runs if r.start is not None), key=lambda r: r.start):
        for name in run.skipped:
            since.setdefault(name, run.start)
        for name in [n for n in since if n not in run.skipped]:
            spans.setdefault(name, []).append((since.pop(name), run.start))
    for name, start in since.items():
        spans.setdefault(name, []).append((start, horizon))
    starts = [e for e in entries if e["kind"] == KIND_CAPTURE_START]
    out = []
    for name in sorted(spans):
        skipping = [e for e in starts if name in (e.get("skipped_indices") or ())]
        latest = skipping[-1] if skipping else {}
        out.append(
            {
                "dataset": name,
                "seconds": int(sum(scheduled_ms(windows, s, e) for s, e in spans[name]) // 1000),
                "from_at": spans[name][0][0],
                "to_at": spans[name][-1][1],
                "starts": _sum_counts(skipping, lambda _e: True),
                "reason": (latest.get("skip_reasons") or {}).get(name),
                "quote_error": latest.get("quote_error"),
            }
        )
    return out


def analyze_incidents(ledger: Mapping, windows: Sequence[Window], now_ms: int) -> dict:
    """Counts, outages, causes and dataset gaps for one trading date's ledger."""
    entries = [e for e in ledger.get("entries") or () if isinstance(e, Mapping)]
    windows = sorted((int(s), int(e)) for s, e in windows)
    horizon = min(now_ms, max(w[1] for w in windows)) if windows else now_ms
    instants = _instants(entries)
    runs = _runs(instants)

    def of(kind: str) -> list[Mapping]:
        return [e for e in entries if e["kind"] == kind]

    failures = of(KIND_CAPTURE_FAILED)
    overlays = {
        CAUSE_MAINTENANCE: _overlay(
            instants,
            lambda e: e["kind"] == KIND_MAINTENANCE_ACQUIRED,
            lambda e: e["kind"] in (KIND_MAINTENANCE_RELEASED, KIND_CAPTURE_START),
            horizon,
            expiry_key="expires_at",
        ),
        CAUSE_AWAITING_TOKEN: _overlay(
            instants,
            lambda e: e["kind"] == KIND_AUTH_STATE and e.get("state") != AUTH_OK,
            # A capture start also closes it: a process that restarts into a usable
            # session starts in the OK state and so never records the transition back.
            lambda e: (e["kind"] == KIND_AUTH_STATE and e.get("state") == AUTH_OK)
            or e["kind"] == KIND_CAPTURE_START,
            horizon,
        ),
    }
    outages, cause_ms = _outages(entries, runs, windows, horizon, overlays)
    eods = of(KIND_EOD)
    return {
        "process_starts": _sum_counts(of(KIND_PROCESS_START), lambda _e: True),
        "capture_starts": _sum_counts(of(KIND_CAPTURE_START), lambda _e: True),
        "failed_starts": _sum_counts(entries, _is_failed_start),
        "capture_failures": _sum_counts(failures, lambda _e: True),
        "process_exits": _sum_counts(failures, lambda e: bool(e.get("process_exit"))),
        "escalations": _sum_counts(failures, lambda e: e.get("exception") in STALL_EXCEPTIONS),
        "auth_failures": _sum_counts(
            failures + of(KIND_START_FAILED),
            lambda e: e.get("exception") in AUTH_EXCEPTIONS,
        ),
        "cause_seconds": {cause: int(ms // 1000) for cause, ms in cause_ms.items()},
        "outages": outages,
        "datasets_missing": _datasets_missing(entries, runs, windows, horizon),
        "stops": [
            {"at": e["last_at"], "reason": e.get("reason"), "clean": e.get("clean"),
             "count": int(e.get("count") or 1)}
            for e in of(KIND_CAPTURE_STOPPED)
        ],
        "eod_runs": _sum_counts(eods, lambda _e: True),
        "eod": _eod_summary(eods[-1]) if eods else None,
    }


def _eod_summary(entry: Mapping) -> dict:
    keep = ("status", "files", "archive_data_loss_pct", "worst_artifact", "artifact_count")
    return {"at": entry["last_at"], **{k: entry.get(k) for k in keep}}


__all__ = [
    "AUTH_EXCEPTIONS",
    "CAUSES",
    "CAUSE_AWAITING_TOKEN",
    "CAUSE_MAINTENANCE",
    "CAUSE_PROCESS_DOWN",
    "CAUSE_RESTART",
    "CAUSE_RESTART_LOOP",
    "STALL_EXCEPTIONS",
    "analyze_incidents",
    "scheduled_ms",
]
