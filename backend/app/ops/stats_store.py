"""Persistent statistics store for the capture monitor dashboard.

Two artifacts live in the configured **stats directory** (``Settings.stats_dir`` --
seeded from ``STATS_DATA_PATH`` in the env, defaulting to ``MARKET_DATA/_state/stats``):

- ``compression-history.jsonl`` -- one JSON line per EOD compression sweep
  (date, files, raw/zst bytes, ratio, total_elapsed_ms, avg_file_ms,
  throughput_mbps, threads). Append-only; enables cross-day averages.
- ``capture-<YYYY-MM-DD>.json`` -- the latest enriched monitor snapshot for the
  day (per-underlying + global metrics), rewritten periodically while capture runs.

All writes are atomic (temp file + rename + fsync), mirroring ``app.session``, so a
crash mid-write never corrupts a file. Reads tolerate missing/partial data.

Every function takes ``stats_dir`` -- the directory the files live in directly (no
extra nesting), so the caller controls the location entirely via configuration.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

COMPRESSION_HISTORY_FILE = "compression-history.jsonl"
# Keep cross-day averages bounded to the most recent sweeps.
MAX_COMPRESSION_HISTORY = 365

SESSION_HISTORY_FILE = "session-history.jsonl"
# One record per capture session; a year of trading days is plenty.
MAX_SESSION_HISTORY = 365


def stats_dir(root: str | os.PathLike[str]) -> Path:
    """Return the stats directory (identity -- files live directly in ``root``)."""
    return Path(root)


def compression_history_path(stats_dir_path: str | os.PathLike[str]) -> Path:
    return Path(stats_dir_path) / COMPRESSION_HISTORY_FILE


def capture_snapshot_path(stats_dir_path: str | os.PathLike[str], trading_date: str) -> Path:
    return Path(stats_dir_path) / f"capture-{trading_date}.json"


def session_history_path(stats_dir_path: str | os.PathLike[str]) -> Path:
    return Path(stats_dir_path) / SESSION_HISTORY_FILE


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp + fsync + rename + dir fsync)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        tmp.replace(path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def record_compression(
    state_dir: str | os.PathLike[str],
    result,
    *,
    trading_date: str,
    threads: int | None = None,
) -> Path:
    """Append one EOD sweep summary to the compression history log.

    ``result`` is an ``app.ops.eod.EODResult`` (uses its ``ratio``, ``avg_file_ms``,
    ``throughput_mbps`` properties). The append is done by rewriting the (small,
    capped) file atomically so a crash never leaves a torn line.
    """
    record = {
        "trading_date": trading_date,
        "files": len(getattr(result, "compressed", []) or []),
        "raw_bytes": int(getattr(result, "total_raw_bytes", 0) or 0),
        "zst_bytes": int(getattr(result, "total_zst_bytes", 0) or 0),
        "ratio": round(float(getattr(result, "ratio", 0.0) or 0.0), 4),
        "total_elapsed_ms": int(getattr(result, "elapsed_ms", 0) or 0),
        "avg_file_ms": round(float(getattr(result, "avg_file_ms", 0.0) or 0.0), 1),
        "throughput_mbps": round(float(getattr(result, "throughput_mbps", 0.0) or 0.0), 2),
        "threads": int(threads) if threads is not None else None,
    }
    path = compression_history_path(state_dir)
    existing = load_compression_history(state_dir)
    existing.append(record)
    existing = existing[-MAX_COMPRESSION_HISTORY:]
    text = "\n".join(json.dumps(row) for row in existing) + "\n"
    _atomic_write_text(path, text)
    return path


def load_compression_history(state_dir: str | os.PathLike[str]) -> list[dict]:
    """Return all compression-sweep records (oldest first); empty if none/corrupt."""
    path = compression_history_path(state_dir)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.debug("skipping corrupt compression-history line")
    return records


def compression_averages(state_dir: str | os.PathLike[str]) -> dict:
    """Cross-day means of ratio, sweep time, per-file time, and throughput.

    Also returns the most recent (last) sweep for the "last batch" panel.
    """
    history = load_compression_history(state_dir)
    if not history:
        return {
            "samples": 0,
            "avg_ratio": 0.0,
            "avg_total_elapsed_ms": 0.0,
            "avg_file_ms": 0.0,
            "avg_throughput_mbps": 0.0,
            "last": None,
        }
    n = len(history)

    def _mean(key: str) -> float:
        return sum(float(r.get(key, 0.0) or 0.0) for r in history) / n

    return {
        "samples": n,
        "avg_ratio": round(_mean("ratio"), 4),
        "avg_total_elapsed_ms": round(_mean("total_elapsed_ms"), 1),
        "avg_file_ms": round(_mean("avg_file_ms"), 1),
        "avg_throughput_mbps": round(_mean("throughput_mbps"), 2),
        "last": history[-1],
    }


def write_capture_snapshot(
    state_dir: str | os.PathLike[str],
    trading_date: str,
    payload: dict,
) -> Path:
    """Persist the latest enriched monitor payload for the trading day."""
    path = capture_snapshot_path(state_dir, trading_date)
    _atomic_write_text(path, json.dumps(payload, indent=2))
    return path


def load_capture_snapshot(
    state_dir: str | os.PathLike[str],
    trading_date: str,
) -> dict | None:
    """Load the persisted capture snapshot for a day, or ``None``."""
    path = capture_snapshot_path(state_dir, trading_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.debug("capture snapshot for %s is corrupt", trading_date)
        return None


def latest_capture_snapshot(
    state_dir: str | os.PathLike[str],
) -> tuple[str, dict] | None:
    """Return ``(trading_date, payload)`` for the newest snapshot on disk, any date.

    Lets the dashboard keep showing the **last session's** telemetry when today's
    capture has not started yet (or on a holiday/weekend), instead of an empty page.
    """
    root = Path(state_dir)
    if not root.exists():
        return None
    best: tuple[str, dict] | None = None
    for path in sorted(root.glob("capture-*.json"), reverse=True):
        date_part = path.stem[len("capture-") :]
        if not date_part:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("skipping unreadable capture snapshot %s", path.name)
            continue
        if isinstance(payload, dict):
            best = (date_part, payload)
            break
    return best


def record_session_summary(
    state_dir: str | os.PathLike[str],
    summary: dict,
) -> Path:
    """Record one completed-session summary, merged with any earlier row for the date.

    One line per trading date. Re-recording the same date used to *replace* the earlier
    row outright, which lost real information: several counters
    (``ticks_received``, ``reconnects``, ``dropped_batches``, ``unmatched_ticks`` …) are
    process-local and are not carried across a restart, so the surviving row was whichever
    generation happened to stop last. That is why the dashboard's ticks/sec column ranged
    28.7–927.3 with no physical meaning.

    Cumulative counters are therefore merged with ``max`` while the day-level figures —
    which are already whole-day via the carried counters and the on-disk frame count — take
    the newest value.
    """
    trading_date = str(summary.get("trading_date") or "unknown")
    history = load_session_history(state_dir)
    previous = next(
        (r for r in reversed(history) if r.get("trading_date") == trading_date), None
    )
    merged = _merge_session_summary(previous, summary)
    history = [r for r in history if r.get("trading_date") != trading_date]
    history.append(merged)
    history = history[-MAX_SESSION_HISTORY:]
    path = session_history_path(state_dir)
    text = "\n".join(json.dumps(row) for row in history) + "\n"
    _atomic_write_text(path, text)
    return path


# Counters that accumulate over a trading date but reset with the process, so the day's
# true total is the maximum seen across generations, never the last one's value.
_CUMULATIVE_SESSION_FIELDS = (
    "captures",
    "frames_written",
    "uptime_ms",
    "grid_gaps",
    "grid_seconds_lost",
    "stale_seconds",
    "stale_events",
    "awaiting_first_tick_seconds",
    "restart_downtime_seconds",
    "dropped_batches",
    "unmatched_ticks",
    "ticks_received",
    "reconnects",
    "in_process_reconnects",
    "token_refreshes",
    "longest_stale_spell_seconds",
    "escalations",
    "disk_bytes",
    "missing_seconds",
    "not_running_seconds",
)


def _merge_session_summary(previous: dict | None, latest: dict) -> dict:
    """Merge a new session summary over an earlier one for the same trading date."""
    if not previous:
        return dict(latest)
    merged = {**previous, **latest}
    for field in _CUMULATIVE_SESSION_FIELDS:
        old, new = previous.get(field), latest.get(field)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            merged[field] = max(old, new)
    merged["generations"] = int(previous.get("generations") or 1) + 1
    return merged


RECONCILIATION_FILE = "reconciliation-history.jsonl"
MAX_RECONCILIATION_HISTORY = 365


def reconciliation_history_path(stats_dir_path: str | os.PathLike[str]) -> Path:
    return Path(stats_dir_path) / RECONCILIATION_FILE


def record_reconciliation(
    state_dir: str | os.PathLike[str],
    record: dict,
) -> Path:
    """Persist the archive-derived completeness record for one trading date.

    Kept in its own log, separate from the telemetry-derived session history, precisely so
    the two can be compared: when they disagree, the archive is right.
    """
    trading_date = str(record.get("trading_date") or "unknown")
    history = [
        r
        for r in load_reconciliation_history(state_dir)
        if r.get("trading_date") != trading_date
    ]
    history.append(record)
    history = history[-MAX_RECONCILIATION_HISTORY:]
    path = reconciliation_history_path(state_dir)
    _atomic_write_text(path, "\n".join(json.dumps(row) for row in history) + "\n")
    return path


def load_reconciliation_history(state_dir: str | os.PathLike[str]) -> list[dict]:
    """Return every archive reconciliation record (oldest first); empty if none/corrupt."""
    path = reconciliation_history_path(state_dir)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping a corrupt reconciliation history line")
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_session_history(state_dir: str | os.PathLike[str]) -> list[dict]:
    """Return all recorded session summaries (oldest first); empty if none/corrupt."""
    path = session_history_path(state_dir)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("skipping corrupt session-history line")
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def session_history(state_dir: str | os.PathLike[str], limit: int = 30) -> list[dict]:
    """Most recent session summaries, newest first (bounded by ``limit``)."""
    records = load_session_history(state_dir)
    records.sort(key=lambda r: str(r.get("trading_date") or ""), reverse=True)
    return records[: max(0, int(limit))]



# --- recovery escalation ledger ---------------------------------------------------
#
# Restart-first recovery exits the process when the live feed is dead (see
# ``CaptureEngine.observe_feed_health``). The exit count for the day has to survive that
# exit, or a feed that can never be restored would restart the container all day. This
# tiny per-date ledger is that memory.

ESCALATION_LEDGER_PREFIX = "escalations-"


def escalation_ledger_path(stats_dir_path: str | os.PathLike[str], trading_date: str) -> Path:
    return Path(stats_dir_path) / f"{ESCALATION_LEDGER_PREFIX}{trading_date}.json"


def load_escalations(
    state_dir: str | os.PathLike[str],
    trading_date: str,
) -> dict:
    """Return ``{"count": int, "timestamps": [ms, ...]}`` for the day (never raises).

    A missing or corrupt ledger reads as "no escalations yet": losing the count can only
    cost extra restarts, whereas raising here would block capture from starting at all.
    """
    empty = {"trading_date": trading_date, "count": 0, "timestamps": []}
    path = escalation_ledger_path(state_dir, trading_date)
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("escalation ledger for %s is unreadable; assuming none", trading_date)
        return empty
    if not isinstance(payload, dict):
        return empty
    try:
        count = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    timestamps = payload.get("timestamps")
    if not isinstance(timestamps, list):
        timestamps = []
    return {
        "trading_date": trading_date,
        "count": max(0, count),
        "timestamps": [t for t in timestamps if isinstance(t, int)],
    }


def record_escalation(
    state_dir: str | os.PathLike[str],
    trading_date: str,
    at_ms: int,
) -> int:
    """Append one escalation for the day and return the new count."""
    ledger = load_escalations(state_dir, trading_date)
    ledger["count"] += 1
    ledger["timestamps"] = [*ledger["timestamps"], int(at_ms)][-50:]
    _atomic_write_text(
        escalation_ledger_path(state_dir, trading_date), json.dumps(ledger, indent=2)
    )
    return ledger["count"]
