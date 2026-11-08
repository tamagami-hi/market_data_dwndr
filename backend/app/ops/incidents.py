"""Per-trading-date incident ledger: *what happened to capture, when, and why*.

The session summary says how much of a day is missing; it cannot say why. On 2026-11-05
the record read "3032 s not running" for what was really a 51-minute loop of ~100 process
restarts during a DNS outage, each skipping every index chain and dying 15 s later on a
ticker that could not connect. Nothing persisted that story, so the dashboard could not
tell it. This ledger does.

One file per IST trading date in the stats directory: ``incidents-<YYYY-MM-DD>.json``.
Written atomically (``stats_store._atomic_write_text``), like every other stats artifact.

**Lossless, bounded by collapsing, never by dropping.** Every distinct incident is kept
forever. A *repeating cycle* of identical incidents — e.g. ``process_start`` ->
``capture_start`` -> ``capture_failed`` every ~30 s for 51 minutes — collapses into one
entry per member with an exact ``count`` and its ``first_at`` / ``last_at`` (epoch ms).
Two occurrences are "identical" when their kind and *signature fields* match (see
``SIGNATURE_FIELDS``); volatile detail (a growing resume downtime, an error message that
embeds an object address) is kept from the latest occurrence. A cycle is recognised when
the matching entry is one of the last ``COLLAPSE_LOOKBACK`` entries and recurred within
``COLLAPSE_WINDOW_MS`` — a crash at 10:23 and another at 14:10 stay two entries.

Recording is telemetry: :func:`record_incident` never raises, so a full disk or a bad
stats path can never break capture, a stop, or the scheduler.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

INCIDENT_LEDGER_PREFIX = "incidents-"
LEDGER_VERSION = 1

# How far back a recurrence may match: a restart loop is a cycle of up to four events
# (process start, capture start, capture failure, and possibly an auth transition).
COLLAPSE_LOOKBACK = 4
# A recurrence further apart than this is a new incident, not the same loop.
COLLAPSE_WINDOW_MS = 10 * 60_000
# Free-text detail (exception messages) is truncated to keep entries small.
REASON_MAX_CHARS = 200

KIND_PROCESS_START = "process_start"
KIND_CAPTURE_START = "capture_start"
KIND_START_FAILED = "start_failed"
KIND_CAPTURE_FAILED = "capture_failed"
KIND_CAPTURE_STOPPED = "capture_stopped"
KIND_AUTH_STATE = "auth_state"
KIND_MAINTENANCE_ACQUIRED = "maintenance_acquired"
KIND_MAINTENANCE_RELEASED = "maintenance_released"
KIND_EOD = "eod"

STOP_REASON_MARKET_CLOSE = "market_close"
STOP_REASON_HOLIDAY = "holiday"
STOP_REASON_OUTSIDE_WINDOW = "outside_window"
STOP_REASON_MAINTENANCE = "maintenance"
STOP_REASON_SHUTDOWN = "shutdown"

# Fields that make two occurrences "the same incident". Everything else is detail.
SIGNATURE_FIELDS: Mapping[str, tuple[str, ...]] = {
    KIND_PROCESS_START: (),
    KIND_CAPTURE_START: ("tokens", "skipped_indices", "quote_error_type"),
    KIND_START_FAILED: ("exception",),
    KIND_CAPTURE_FAILED: ("exception", "feed_live", "process_exit"),
    KIND_CAPTURE_STOPPED: ("reason", "clean"),
    KIND_AUTH_STATE: ("state", "error"),
    KIND_MAINTENANCE_ACQUIRED: (),
    KIND_MAINTENANCE_RELEASED: (),
    KIND_EOD: ("status",),
}

_RESERVED = frozenset({"kind", "first_at", "last_at", "count"})
_SECRET_PARAM = re.compile(
    r"(?i)\b(access_token|api_key|api_secret|request_token|enctoken|token|password)"
    r"(\s*[=:]\s*)[^\s&,;'\"]+"
)
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
_LOCK = threading.Lock()


def incident_ledger_path(stats_dir_path: str | os.PathLike[str], trading_date: str) -> Path:
    return Path(stats_dir_path) / f"{INCIDENT_LEDGER_PREFIX}{trading_date}.json"


def empty_ledger(trading_date: str) -> dict:
    return {"trading_date": trading_date, "version": LEDGER_VERSION, "entries": [], "counts": {}}


def sanitize_reason(value: object) -> str:
    """A short, secret-free, stable rendering of an error message for the ledger."""
    text = " ".join(str(value or "").split())
    text = _SECRET_PARAM.sub(r"\1\2***", text)
    text = _ADDRESS.sub("0x…", text)
    if len(text) > REASON_MAX_CHARS:
        text = text[: REASON_MAX_CHARS - 1] + "…"
    return text


def load_incidents(stats_dir_path: str | os.PathLike[str], trading_date: str) -> dict:
    """The ledger for ``trading_date``; an empty one when missing or unreadable."""
    path = incident_ledger_path(stats_dir_path, trading_date)
    if not path.exists():
        return empty_ledger(trading_date)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("incident ledger for %s is unreadable; treating it as empty", trading_date)
        return empty_ledger(trading_date)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return empty_ledger(trading_date)
    entries = [e for e in payload["entries"] if _is_entry(e)]
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {**empty_ledger(trading_date), "entries": entries, "counts": dict(counts)}


def ledger_exists(stats_dir_path: str | os.PathLike[str], trading_date: str) -> bool:
    return incident_ledger_path(stats_dir_path, trading_date).exists()


def _is_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("kind"), str)
        and isinstance(entry.get("first_at"), int)
        and isinstance(entry.get("last_at"), int)
        and isinstance(entry.get("count"), int)
    )


def _normalise(value: object) -> object:
    """JSON-stable form, so a signature compares equal before and after a round trip."""
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in value.items()}
    return value


def _signature(kind: str, fields: Mapping[str, object]) -> tuple:
    keys = SIGNATURE_FIELDS.get(kind)
    if keys is None:
        keys = tuple(sorted(key for key in fields if key not in _RESERVED))
    return tuple(json.dumps(_normalise(fields.get(key)), sort_keys=True) for key in keys)


def merge_incident(ledger: dict, kind: str, at_ms: int, fields: Mapping[str, object]) -> dict:
    """Return a new ledger with one occurrence of ``kind`` at ``at_ms`` folded in.

    Pure: the input ledger is not modified. Collapses into a recent identical entry (see
    the module docstring); otherwise appends. Never removes anything.
    """
    clean = {k: _normalise(v) for k, v in fields.items() if k not in _RESERVED}
    entries = list(ledger.get("entries") or [])
    signature = _signature(kind, clean)
    lookback_start = max(0, len(entries) - COLLAPSE_LOOKBACK)
    match_index = None
    for index in range(len(entries) - 1, lookback_start - 1, -1):
        candidate = entries[index]
        if candidate.get("kind") != kind:
            continue
        if _signature(kind, candidate) != signature:
            continue
        if 0 <= at_ms - int(candidate["last_at"]) <= COLLAPSE_WINDOW_MS:
            match_index = index
        break
    if match_index is None:
        entries.append({"kind": kind, "first_at": at_ms, "last_at": at_ms, "count": 1, **clean})
    else:
        previous = entries[match_index]
        entries[match_index] = {
            **previous,
            **clean,
            "kind": kind,
            "first_at": previous["first_at"],
            "last_at": max(int(previous["last_at"]), at_ms),
            "count": int(previous["count"]) + 1,
        }
    counts = dict(ledger.get("counts") or {})
    counts[kind] = int(counts.get(kind, 0)) + 1
    return {**ledger, "version": LEDGER_VERSION, "entries": entries, "counts": counts}


def append_incident(
    stats_dir_path: str | os.PathLike[str],
    trading_date: str,
    kind: str,
    at_ms: int,
    **fields: object,
) -> dict:
    """Fold one incident into the day's ledger and persist it; returns the new ledger."""
    from app.ops.stats_store import _atomic_write_text

    with _LOCK:
        ledger = merge_incident(
            load_incidents(stats_dir_path, trading_date), kind, int(at_ms), fields
        )
        _atomic_write_text(
            incident_ledger_path(stats_dir_path, trading_date), json.dumps(ledger, indent=1)
        )
    return ledger


def record_incident(
    stats_dir_path: str | os.PathLike[str] | None,
    trading_date: str | None,
    kind: str,
    at_ms: int,
    **fields: object,
) -> None:
    """Best-effort :func:`append_incident`: a no-op without a stats dir, never raises."""
    if stats_dir_path is None or not trading_date:
        return
    try:
        append_incident(stats_dir_path, trading_date, kind, at_ms, **fields)
    except Exception:  # noqa: BLE001 - incident telemetry must never break capture
        logger.debug("failed to record %s incident for %s", kind, trading_date, exc_info=True)


__all__ = [
    "COLLAPSE_LOOKBACK",
    "COLLAPSE_WINDOW_MS",
    "KIND_AUTH_STATE",
    "KIND_CAPTURE_FAILED",
    "KIND_CAPTURE_START",
    "KIND_CAPTURE_STOPPED",
    "KIND_EOD",
    "KIND_MAINTENANCE_ACQUIRED",
    "KIND_MAINTENANCE_RELEASED",
    "KIND_PROCESS_START",
    "KIND_START_FAILED",
    "STOP_REASON_HOLIDAY",
    "STOP_REASON_MAINTENANCE",
    "STOP_REASON_MARKET_CLOSE",
    "STOP_REASON_OUTSIDE_WINDOW",
    "STOP_REASON_SHUTDOWN",
    "append_incident",
    "empty_ledger",
    "incident_ledger_path",
    "ledger_exists",
    "load_incidents",
    "merge_incident",
    "record_incident",
    "sanitize_reason",
]
