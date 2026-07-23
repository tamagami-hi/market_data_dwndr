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


def stats_dir(root: str | os.PathLike[str]) -> Path:
    """Return the stats directory (identity -- files live directly in ``root``)."""
    return Path(root)


def compression_history_path(stats_dir_path: str | os.PathLike[str]) -> Path:
    return Path(stats_dir_path) / COMPRESSION_HISTORY_FILE


def capture_snapshot_path(stats_dir_path: str | os.PathLike[str], trading_date: str) -> Path:
    return Path(stats_dir_path) / f"capture-{trading_date}.json"


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
