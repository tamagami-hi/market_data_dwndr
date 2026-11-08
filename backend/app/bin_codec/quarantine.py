"""Mislabelled frames in legacy (schema v1) index files, treated as missing by readers.

Schema-v1 index files (2026-07-23 .. 2026-08-31) stored their strike axis once, in the
header. A capture restart that rebuilt the option chain around a different spot appended
frames whose columns belong to a different strike window than the header names, so those
frames decode cleanly but every column sits under the wrong strike. They were found per
process generation by put-call parity and are listed in
``data/mislabelled_v1_index_frames.json``.

The archive files are never modified. Readers drop the listed frames (and any v1 frame
whose width differs from the header's strike count, which is wrong by construction) and
expose how many were dropped, so the data reads as a gap rather than as wrong strikes.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "data" / "mislabelled_v1_index_frames.json"


@lru_cache(maxsize=1)
def _manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": []}


@lru_cache(maxsize=1)
def _ranges_by_file() -> dict[tuple[str, str], tuple[tuple[int, int, str], ...]]:
    index: dict[tuple[str, str], tuple[tuple[int, int, str], ...]] = {}
    for entry in _manifest().get("files", []):
        key = (str(entry["artifact"]), str(entry["file"]))
        index[key] = tuple(
            (int(r["from_ms"]), int(r["to_ms"]), str(r["reason"])) for r in entry["ranges"]
        )
    return index


def _file_key(path: str | Path) -> tuple[str, str]:
    """``(artifact, '<date>[.pN].bin')`` for an index file, raw or ``.zst``."""
    path = Path(path)
    name = path.name.removesuffix(".zst")
    return path.parent.name, name


def excluded_ranges(path: str | Path) -> tuple[tuple[int, int, str], ...]:
    """``(from_ms, to_ms, reason)`` ranges to treat as missing in this index file."""
    return _ranges_by_file().get(_file_key(path), ())


def summary_by_date() -> dict[str, dict]:
    """``{date: {frames, artifacts, ranges}}`` of frames treated as missing."""
    out: dict[str, dict] = {}
    for entry in _manifest().get("files", []):
        day = str(entry["trading_date"])
        record = out.setdefault(day, {"frames": 0, "artifacts": [], "ranges": 0})
        record["frames"] += sum(int(r["frames"]) for r in entry["ranges"])
        record["ranges"] += len(entry["ranges"])
        if entry["artifact"] not in record["artifacts"]:
            record["artifacts"] = sorted([*record["artifacts"], entry["artifact"]])
    return out


__all__ = ["MANIFEST_PATH", "excluded_ranges", "summary_by_date"]
