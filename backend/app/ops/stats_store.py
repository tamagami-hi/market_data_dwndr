"""Persistent statistics store for the capture monitor dashboard.

Two artifacts live in the configured **stats directory** (``Settings.stats_dir`` --
seeded from ``STATS_DATA_PATH`` in the env, defaulting to ``MARKET_DATA/_state/stats``):

- ``compression-history.jsonl`` -- one JSON line per EOD compression sweep
  (date, files, raw/zst bytes, ratio, total_elapsed_ms, avg_file_ms,
  throughput_mbps, threads). Append-only; enables cross-day averages.
- ``capture-<YYYY-MM-DD>.json`` -- the latest enriched monitor snapshot for the
  day (per-underlying + global metrics), rewritten periodically while capture runs.
- ``session-history.jsonl`` / ``reconciliation-history.jsonl`` -- one line per trading
  date (telemetry summary / archive-truth completeness).
- ``escalations-<date>.json`` / ``incidents-<date>.json`` -- per-day recovery ledgers.
- ``eod-state.json`` -- which trading dates finished their EOD sweep.

**Nothing is ever pruned.** Every recorded session, sweep, reconciliation and escalation
is kept for good; an operator decides when history goes, not a write-time cap. The
per-date JSONL logs therefore grow without bound, so they are rewritten from their *raw
lines* (only the row being replaced is parsed), and paged reads index rows by their
``trading_date`` with a regex instead of decoding every line: a few thousand sessions
cost milliseconds, not a full JSON decode per dashboard poll.

All writes are atomic (temp file + rename + fsync), mirroring ``app.session``, so a
crash mid-write never corrupts a file. Reads tolerate missing/partial data; a line that
cannot be decoded is skipped on read but preserved on rewrite.

Every function takes ``stats_dir`` -- the directory the files live in directly (no
extra nesting), so the caller controls the location entirely via configuration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

COMPRESSION_HISTORY_FILE = "compression-history.jsonl"
SESSION_HISTORY_FILE = "session-history.jsonl"
EOD_STATE_FILE = "eod-state.json"

# The ``trading_date`` of a JSONL row without decoding the whole line.
_TRADING_DATE = re.compile(r'"trading_date":\s*"([^"\\]*)"')
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    ``throughput_mbps`` properties). The append is done by rewriting the file's raw lines
    atomically so a crash never leaves a torn line. Nothing earlier is ever dropped.
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
    _write_lines(path, [*_read_lines(path), json.dumps(record)])
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
    path = session_history_path(state_dir)
    kept, previous = _split_rows_for_date(_read_lines(path), trading_date)
    merged = {"trading_date": trading_date, **_merge_session_summary(previous, summary)}
    _write_lines(path, [*kept, json.dumps(merged)])
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
    """Merge a new session summary over an earlier one for the same trading date.

    ``generations`` counts the capture runs that recorded a summary for the date: the
    controller records exactly once per run (clean stop, auth expiry or crash), so it is
    the number of runs, starting at 1. It used to be absent on the first record and then
    bumped by the pre-EOD "final flush" stop re-recording the same run, which is why
    almost every day in the history read ``generations: 2``.
    """
    if not previous:
        return {**latest, "generations": 1}
    merged = {**previous, **latest}
    for field in _CUMULATIVE_SESSION_FIELDS:
        old, new = previous.get(field), latest.get(field)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            merged[field] = max(old, new)
    merged["generations"] = int(previous.get("generations") or 1) + 1
    return merged


RECONCILIATION_FILE = "reconciliation-history.jsonl"


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
    path = reconciliation_history_path(state_dir)
    kept, _previous = _split_rows_for_date(_read_lines(path), trading_date)
    _write_lines(path, [*kept, json.dumps({"trading_date": trading_date, **record})])
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
    return session_history_page(state_dir, limit=limit)["sessions"]


def session_history_page(
    state_dir: str | os.PathLike[str],
    *,
    limit: int = 30,
    before: str | None = None,
) -> dict:
    """One page of the full session history, newest trading date first.

    Returns ``{"sessions": [...], "total": N, "next_before": date | None}``: ``total``
    counts every dated row in the history, and ``next_before`` is the cursor for the
    following (older) page, ``None`` on the last page. Only the rows on the page are
    decoded; the rest are ordered by their regex-extracted ``trading_date``.
    """
    dated = [
        (row_date, index, line)
        for index, (row_date, line) in enumerate(_dated_lines(session_history_path(state_dir)))
        if row_date is not None and _ISO_DATE.match(row_date)
    ]
    dated.sort(reverse=True)
    total = len(dated)
    if before is not None:
        dated = [row for row in dated if row[0] < before]
    size = max(0, int(limit))
    page = dated[:size]
    sessions = [row for row in (_decode(line) for _d, _i, line in page) if row is not None]
    next_before = page[-1][0] if page and len(dated) > size else None
    return {"sessions": sessions, "total": total, "next_before": next_before}


def reconciliation_for_dates(
    state_dir: str | os.PathLike[str], dates: set[str] | frozenset[str]
) -> dict[str, dict]:
    """Archive reconciliation records for ``dates`` only (newest row wins per date)."""
    found: dict[str, dict] = {}
    for row_date, line in _dated_lines(reconciliation_history_path(state_dir)):
        if row_date in dates:
            row = _decode(line)
            if row is not None and row.get("trading_date") == row_date:
                found[row_date] = row
    return found


# --- raw-line JSONL helpers ---------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    """Non-empty raw lines of a JSONL file (missing file -> empty)."""
    if not path.exists():
        return []
    stripped = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in stripped if line]


def _write_lines(path: Path, lines: list[str]) -> None:
    _atomic_write_text(path, "\n".join(lines) + "\n" if lines else "")


def _decode(line: str) -> dict | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("skipping a corrupt JSONL row")
        return None
    return row if isinstance(row, dict) else None


def _line_date(line: str) -> str | None:
    match = _TRADING_DATE.search(line)
    return match.group(1) if match else None


def _dated_lines(path: Path) -> list[tuple[str | None, str]]:
    return [(_line_date(line), line) for line in _read_lines(path)]


def _split_rows_for_date(lines: list[str], trading_date: str) -> tuple[list[str], dict | None]:
    """``(lines without trading_date's rows, that date's newest decoded row)``.

    Rows for other dates — and undecodable lines — pass through byte for byte, so a
    rewrite can never lose or reformat history it did not mean to touch.
    """
    kept: list[str] = []
    previous: dict | None = None
    for line in lines:
        if _line_date(line) == trading_date:
            row = _decode(line)
            if row is not None and row.get("trading_date") == trading_date:
                previous = row
                continue
        kept.append(line)
    return kept, previous


# --- EOD completion -----------------------------------------------------------------
#
# The scheduler kept "EOD done for <date>" in memory only, so a process that restarted
# after the sweep (18:53 on 2026-11-05) ran a second EOD for the same date — compressing
# nothing and recording a zero-file sweep into the cross-day averages.


def eod_state_path(stats_dir_path: str | os.PathLike[str]) -> Path:
    return Path(stats_dir_path) / EOD_STATE_FILE


def load_eod_state(state_dir: str | os.PathLike[str]) -> dict:
    """``{"last_completed_date": str | None, "completed": {date: completed_at_ms}}``."""
    empty: dict = {"last_completed_date": None, "completed": {}}
    path = eod_state_path(state_dir)
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("EOD state file is unreadable; assuming no completed sweep")
        return empty
    if not isinstance(payload, dict):
        return empty
    raw = payload.get("completed")
    completed = (
        {str(k): v for k, v in raw.items() if isinstance(v, int)} if isinstance(raw, dict) else {}
    )
    last = payload.get("last_completed_date")
    return {
        "last_completed_date": last if isinstance(last, str) else None,
        "completed": completed,
    }


def is_eod_completed(state_dir: str | os.PathLike[str], trading_date: str) -> bool:
    return trading_date in load_eod_state(state_dir)["completed"]


def record_eod_completed(
    state_dir: str | os.PathLike[str], trading_date: str, at_ms: int
) -> Path:
    """Persist that ``trading_date``'s EOD sweep finished (every date is kept)."""
    state = load_eod_state(state_dir)
    completed = {**state["completed"], trading_date: int(at_ms)}
    latest = max(completed)
    path = eod_state_path(state_dir)
    _atomic_write_text(
        path,
        json.dumps({"last_completed_date": latest, "completed": completed}, indent=1),
    )
    return path


def is_iso_date(value: str) -> bool:
    """True for a real ``YYYY-MM-DD`` calendar date."""
    if not _ISO_DATE.match(value or ""):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True



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
    # Every escalation is kept: the count and its timestamps never diverge.
    ledger["timestamps"] = [*ledger["timestamps"], int(at_ms)]
    _atomic_write_text(
        escalation_ledger_path(state_dir, trading_date), json.dumps(ledger, indent=2)
    )
    return ledger["count"]
