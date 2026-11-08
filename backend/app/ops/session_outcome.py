"""One trading day's capture outcome: how complete it is, and why it is not.

``describe_session`` takes a recorded session summary (``CaptureMonitor.session_summary``
or the live monitor's ``global`` block) and adds the explanation the dashboard needs:

* **one primary completeness figure** — ``completeness_pct`` = captured seconds over the
  scheduled seconds owed (the whole day once it is over, the elapsed part while it runs).
  Every other loss percentage in the record answers a narrower question:

  ===================  =====================================================================
  ``scheduled_loss_pct``  100 - completeness over the *elapsed* scheduled seconds (§17); the
                          live twin of ``completeness_pct``.
  ``data_loss_pct``       loss over the grid span the process observed (first to last grid
                          second): blind to a late start or an early stop.
  ``session_loss_pct``    loss over the seconds a frame *could* have been written for (that
                          span minus stale, first-tick wait and restart downtime): the
                          write-path / grid-gap health figure, ~0 on a healthy pipeline.
  ``frame_loss_pct``      frames on disk across every stream against the full-day baseline:
                          a progress figure, and the one a skipped dataset moves.
  ===================  =====================================================================

* an **outcome** class from documented thresholds (``OUTCOME_THRESHOLDS``);
* every missing second attributed to exactly one cause (``loss_causes``), with the
  not-running time split by the incident ledger (:mod:`app.ops.incident_timeline`);
* the archive reconciliation figure for the day, which wins when it is worse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from app.ops.incident_timeline import (
    CAUSE_AWAITING_TOKEN,
    CAUSE_MAINTENANCE,
    CAUSE_RESTART,
    CAUSE_RESTART_LOOP,
    analyze_incidents,
)

OUTCOME_COMPLETE = "complete"
OUTCOME_DEGRADED = "degraded"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"
OUTCOME_NOT_CAPTURED = "not_captured"
OUTCOME_NOT_SCHEDULED = "not_scheduled"

# Lower bound (inclusive) of each class on the deciding completeness %. ``complete``
# tolerates ~115 s on a 23,100 s day: the open's first-tick wait and a stale blip.
COMPLETE_MIN_PCT = 99.5
DEGRADED_MIN_PCT = 95.0
PARTIAL_MIN_PCT = 50.0
OUTCOME_THRESHOLDS = {
    OUTCOME_COMPLETE: COMPLETE_MIN_PCT,
    OUTCOME_DEGRADED: DEGRADED_MIN_PCT,
    OUTCOME_PARTIAL: PARTIAL_MIN_PCT,
    OUTCOME_FAILED: 0.0,
}

ENGINE_CAUSE_FIELDS = (
    "stale_feed_seconds",
    "awaiting_first_tick_seconds",
    "write_path_seconds",
    "unclassified_seconds",
)


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def classify_outcome(
    completeness_pct: float | None,
    *,
    scheduled_seconds: int,
    captured_seconds: int,
    dataset_gap_seconds: int = 0,
) -> str:
    """Outcome class for a day (see ``OUTCOME_THRESHOLDS``).

    A day that would be ``complete`` is ``degraded`` when any dataset was missing for more
    than the complete-class tolerance: a full grid with no NIFTY chain is not complete.
    """
    if scheduled_seconds <= 0 or completeness_pct is None:
        return OUTCOME_NOT_SCHEDULED
    if captured_seconds <= 0:
        return OUTCOME_NOT_CAPTURED
    if completeness_pct >= COMPLETE_MIN_PCT:
        tolerance = scheduled_seconds * (100.0 - COMPLETE_MIN_PCT) / 100.0
        return OUTCOME_DEGRADED if dataset_gap_seconds > tolerance else OUTCOME_COMPLETE
    if completeness_pct >= DEGRADED_MIN_PCT:
        return OUTCOME_DEGRADED
    if completeness_pct >= PARTIAL_MIN_PCT:
        return OUTCOME_PARTIAL
    return OUTCOME_FAILED


def loss_causes(summary: Mapping, analysis: Mapping | None) -> dict:
    """Every missing second, attributed once; the values sum to ``missing_seconds``.

    The engine's buckets (stale, first-tick wait, write path) are kept as they are. The
    rest is time capture was not running — ``restart_downtime_seconds`` plus
    ``not_running_seconds`` — and *which* of those two engine counters happened to see it
    says nothing about why. The incident ledger does, so that pool is split by it, in
    order of specificity; whatever the ledger cannot explain is ``process_down_seconds``.
    The engine's own restart measurement is a floor for restart time.
    """
    causes = {field: _int(summary.get(field)) for field in ENGINE_CAUSE_FIELDS}
    restart_downtime = _int(summary.get("restart_downtime_seconds"))
    pool = restart_downtime + _int(summary.get("not_running_seconds"))
    split = dict.fromkeys(
        (CAUSE_MAINTENANCE, CAUSE_AWAITING_TOKEN, CAUSE_RESTART_LOOP, CAUSE_RESTART), 0
    )
    if analysis is None:
        split[CAUSE_RESTART] = restart_downtime
        remaining = pool - restart_downtime
    else:
        remaining = pool
        ledger_seconds = analysis.get("cause_seconds") or {}
        for cause in split:
            taken = min(remaining, _int(ledger_seconds.get(cause)))
            split[cause] = taken
            remaining -= taken
        shortfall = restart_downtime - split[CAUSE_RESTART_LOOP] - split[CAUSE_RESTART]
        moved = max(0, min(shortfall, remaining))
        split[CAUSE_RESTART] += moved
        remaining -= moved
    return {
        **causes,
        "restart_seconds": split[CAUSE_RESTART],
        "restart_loop_seconds": split[CAUSE_RESTART_LOOP],
        "awaiting_token_seconds": split[CAUSE_AWAITING_TOKEN],
        "maintenance_seconds": split[CAUSE_MAINTENANCE],
        "process_down_seconds": max(0, remaining),
    }


def archive_summary(reconciliation: Mapping | None) -> dict | None:
    """The archive-truth completeness record for the day, in dashboard form."""
    if not reconciliation:
        return None
    loss = float(reconciliation.get("data_loss_pct") or 0.0)
    worst = reconciliation.get("worst_artifact")
    artifact = (reconciliation.get("artifacts") or {}).get(worst) or {}
    return {
        "completeness_pct": round(max(0.0, 100.0 - loss), 3),
        "data_loss_pct": round(loss, 3),
        "worst_artifact": worst,
        "artifact_count": _int(reconciliation.get("artifact_count")),
        "scheduled_seconds": _int(artifact.get("scheduled_seconds")),
        "captured_seconds": _int(artifact.get("captured_seconds")),
        "missing_seconds": _int(artifact.get("missing_seconds")),
        "gap_count": _int(artifact.get("gap_count")),
        "longest_gap_seconds": _int(artifact.get("longest_gap_seconds")),
    }


def describe_session(
    summary: Mapping,
    *,
    ledger: Mapping | None = None,
    reconciliation: Mapping | None = None,
    window: Mapping | None = None,
    now_ms: int,
    tz_name: str = "Asia/Kolkata",
) -> dict:
    """``summary`` plus outcome, completeness, causes, incidents and archive truth."""
    windows: Sequence[tuple[int, int]] = [
        (int(s), int(e)) for s, e in (window or {}).get("windows") or ()
    ]
    scheduled = _int(summary.get("scheduled_seconds")) or _int(
        (window or {}).get("scheduled_seconds")
    )
    elapsed = _int(summary.get("scheduled_seconds_elapsed")) or scheduled
    final = now_ms >= max(e for _s, e in windows) if windows else elapsed >= scheduled
    basis = scheduled if final else elapsed
    captured = _int(summary.get("captured_seconds", summary.get("captures")))
    completeness = round(min(100.0, captured / basis * 100.0), 3) if basis else None
    has_ledger = bool(ledger and ledger.get("entries"))
    analysis = analyze_incidents(ledger, windows, now_ms) if has_ledger else None
    archive = archive_summary(reconciliation)
    deciding, outcome_basis = completeness, "telemetry"
    if archive is not None and completeness is not None:
        if archive["completeness_pct"] < completeness:
            deciding, outcome_basis = archive["completeness_pct"], "archive"
    datasets = analysis["datasets_missing"] if analysis else []
    outcome = classify_outcome(
        deciding,
        scheduled_seconds=basis,
        captured_seconds=captured,
        dataset_gap_seconds=max((d["seconds"] for d in datasets), default=0),
    )
    described = {
        **summary,
        "completeness_pct": completeness,
        "completeness_basis_seconds": basis,
        "final": bool(final),
        "outcome": outcome,
        "outcome_basis": outcome_basis,
        "archive_completeness_pct": archive["completeness_pct"] if archive else None,
        "archive_reconciliation": archive,
        "session_window": dict(window) if window else summary.get("session_window"),
        "incidents_recorded": has_ledger,
        "loss_causes": loss_causes(summary, analysis),
        **_incident_fields(summary, analysis),
    }
    described["explanation"] = explain(described, tz_name)
    return described


def _incident_fields(summary: Mapping, analysis: Mapping | None) -> dict:
    stored_generations = _int(summary.get("generations")) or None
    escalations = _int(summary.get("escalations"))
    if analysis is None:
        return {
            "process_starts": None,
            "capture_starts": None,
            "failed_starts": None,
            "capture_failures": None,
            "process_exits": None,
            "escalations": escalations,
            "generations": stored_generations,
            "outages": [],
            "datasets_missing": [],
            "stops": [],
            "eod": None,
            "eod_runs": None,
        }
    return {
        "process_starts": analysis["process_starts"],
        "capture_starts": analysis["capture_starts"],
        "failed_starts": analysis["failed_starts"],
        "capture_failures": analysis["capture_failures"],
        "process_exits": analysis["process_exits"],
        # The ledger sees every escalation, including the ones an old engine never counted.
        "escalations": max(escalations, analysis["escalations"]),
        "generations": max(stored_generations or 0, analysis["capture_starts"]) or None,
        "outages": analysis["outages"],
        "datasets_missing": analysis["datasets_missing"],
        "stops": analysis["stops"],
        "eod": analysis["eod"],
        "eod_runs": analysis["eod_runs"],
    }


def _duration(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _clock(ms: int, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - formatting only
        tz = UTC
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(tz).strftime("%H:%M")


_CAUSE_LABELS = {
    "restart_loop_seconds": "a restart loop (capture repeatedly failed to start or connect)",
    "restart_seconds": "process restarts",
    "awaiting_token_seconds": "waiting for a usable broker token",
    "maintenance_seconds": "release maintenance",
    "process_down_seconds": "capture not running, no recorded reason",
    "stale_feed_seconds": "a stale feed (frames suppressed)",
    "awaiting_first_tick_seconds": "waiting for the first tick",
    "write_path_seconds": "write-path gaps",
    "unclassified_seconds": "unclassified",
}


def explain(described: Mapping, tz_name: str = "Asia/Kolkata") -> list[str]:
    """Short human sentences, biggest cause first, for the dashboard tooltip."""
    lines: list[str] = []
    pct = described.get("completeness_pct")
    if pct is not None:
        lines.append(
            f"{described['outcome']}: {pct:.1f}% of {described['completeness_basis_seconds']} "
            "scheduled seconds captured"
            + (" (archive figure is worse and decides)"
               if described.get("outcome_basis") == "archive" else "")
        )
    causes = sorted(
        ((v, k) for k, v in (described.get("loss_causes") or {}).items() if v > 0), reverse=True
    )
    for seconds, key in causes:
        lines.append(f"{_duration(seconds)} lost to {_CAUSE_LABELS.get(key, key)}")
    for outage in described.get("outages") or ():
        if outage["cause"] != CAUSE_RESTART_LOOP:
            continue
        skipped = outage.get("skipped_indices") or []
        lines.append(
            f"restart loop {_clock(outage['start_at'], tz_name)}-"
            f"{_clock(outage['end_at'], tz_name)}: {outage['failed_starts']} failed start(s) "
            f"({', '.join(outage['exceptions'])})"
            + (f" after {outage['trigger']}" if outage.get("trigger") else "")
            + (f"; index chains skipped: {', '.join(skipped)}" if skipped else "")
        )
    for dataset in described.get("datasets_missing") or ():
        if dataset["seconds"] > 0:
            reason = f" ({dataset['reason']})" if dataset.get("reason") else ""
            lines.append(
                f"{dataset['dataset']} missing for {_duration(dataset['seconds'])}{reason}"
            )
    return lines


__all__ = [
    "COMPLETE_MIN_PCT",
    "DEGRADED_MIN_PCT",
    "OUTCOME_COMPLETE",
    "OUTCOME_DEGRADED",
    "OUTCOME_FAILED",
    "OUTCOME_NOT_CAPTURED",
    "OUTCOME_NOT_SCHEDULED",
    "OUTCOME_PARTIAL",
    "OUTCOME_THRESHOLDS",
    "PARTIAL_MIN_PCT",
    "archive_summary",
    "classify_outcome",
    "describe_session",
    "explain",
    "loss_causes",
]
