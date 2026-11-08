"""Data-retention helpers + integrity checks (docs/60-operations/data-retention.md).

Policy: raw ``.bin`` is transient (removed after the verified ``.zst`` is written);
compressed ``.bin.zst`` and instrument archives are kept indefinitely. These helpers
report on storage and spot-check that a compressed file still decodes + re-indexes with
monotonic timestamps.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.bin_codec.reader import IndexBinReader


@dataclass(frozen=True)
class RetentionReport:
    raw_bin_files: int
    compressed_files: int
    instrument_files: int
    state_files: int
    raw_bytes: int
    compressed_bytes: int


@dataclass(frozen=True)
class CaptureSessionHistory:
    trading_date: str
    total_bytes: int
    raw_bytes: int
    archived_bytes: int
    data_files: int
    raw_files: int
    archived_files: int
    index_files: int
    stock_files: int
    indices: tuple[str, ...]
    # Index futures board (``INDICES_FnO/<date>.bin``), counted on its own.
    index_fno_files: int = 0


@dataclass(frozen=True)
class CaptureHistoryReport:
    sessions: tuple[CaptureSessionHistory, ...]
    total_bytes: int
    raw_bytes: int
    archived_bytes: int
    data_files: int


def scan_storage(
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
) -> RetentionReport:
    """Summarize transient SSD data and durable HDD archives."""
    live_root = Path(market_data_path)
    archive_root = Path(archive_data_path)
    raw = list(live_root.rglob("*.bin"))
    zst = list(archive_root.rglob("*.bin.zst"))
    inst_dir = live_root / "_instruments"
    state_dir = live_root / "_state"
    instruments = list(inst_dir.rglob("*.csv")) if inst_dir.exists() else []
    state = list(state_dir.rglob("*.json")) if state_dir.exists() else []
    return RetentionReport(
        raw_bin_files=len(raw),
        compressed_files=len(zst),
        instrument_files=len(instruments),
        state_files=len(state),
        raw_bytes=sum(p.stat().st_size for p in raw),
        compressed_bytes=sum(p.stat().st_size for p in zst),
    )


CAPTURE_AREAS = ("INDICES", "STOCKS", "INDICES_FnO")
_SEGMENT = re.compile(r"\.p\d+$")


def scan_capture_history(
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
) -> CaptureHistoryReport:
    """Aggregate live-capture storage by trading date across SSD and HDD roots.

    Only the production ``INDICES``, ``STOCKS`` and ``INDICES_FnO`` trees are included
    (``<date>.pN.bin`` segments count toward their date). Historical
    backfill trees and internal metadata are intentionally excluded. A logical data
    file is counted once if both its transient raw file and verified archive briefly
    coexist during end-of-day compression; physical byte totals still report both.
    """
    records: dict[str, dict[str, object]] = {}

    def collect(root: Path, pattern: str, *, archived: bool) -> None:
        for area in CAPTURE_AREAS:
            area_root = root / area
            if not area_root.exists():
                continue
            for path in area_root.rglob(pattern):
                suffix = ".bin.zst" if archived else ".bin"
                if not path.name.endswith(suffix):
                    continue
                # ``<date>.pN`` is a later segment of the same day's file.
                trading_date = _SEGMENT.sub("", path.name.removesuffix(suffix))
                try:
                    date.fromisoformat(trading_date)
                except ValueError:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    # EOD can remove a raw file between rglob() and stat(). The next
                    # history poll will observe its atomically published archive.
                    continue
                record = records.setdefault(
                    trading_date,
                    {
                        "raw_bytes": 0,
                        "archived_bytes": 0,
                        "raw_files": 0,
                        "archived_files": 0,
                        "logical_files": set(),
                        "index_files": set(),
                        "stock_files": set(),
                        "index_fno_files": set(),
                        "indices": set(),
                    },
                )
                byte_key = "archived_bytes" if archived else "raw_bytes"
                file_key = "archived_files" if archived else "raw_files"
                record[byte_key] = int(record[byte_key]) + size
                record[file_key] = int(record[file_key]) + 1

                relative = path.relative_to(area_root).as_posix().removesuffix(suffix)
                logical_key = f"{area}/{relative}"
                logical_files = record["logical_files"]
                assert isinstance(logical_files, set)
                logical_files.add(logical_key)
                if area == "INDICES":
                    index_files = record["index_files"]
                    indices = record["indices"]
                    assert isinstance(index_files, set) and isinstance(indices, set)
                    index_files.add(logical_key)
                    relative_parts = path.relative_to(area_root).parts
                    if len(relative_parts) >= 2:
                        indices.add(relative_parts[0])
                else:
                    key = "index_fno_files" if area == "INDICES_FnO" else "stock_files"
                    area_files = record[key]
                    assert isinstance(area_files, set)
                    area_files.add(logical_key)

    collect(Path(market_data_path), "*.bin", archived=False)
    collect(Path(archive_data_path), "*.bin.zst", archived=True)

    sessions: list[CaptureSessionHistory] = []
    for trading_date in sorted(records, reverse=True):
        record = records[trading_date]
        raw_bytes = int(record["raw_bytes"])
        archived_bytes = int(record["archived_bytes"])
        logical_files = record["logical_files"]
        index_files = record["index_files"]
        stock_files = record["stock_files"]
        indices = record["indices"]
        assert all(
            isinstance(value, set)
            for value in (logical_files, index_files, stock_files, indices)
        )
        sessions.append(
            CaptureSessionHistory(
                trading_date=trading_date,
                total_bytes=raw_bytes + archived_bytes,
                raw_bytes=raw_bytes,
                archived_bytes=archived_bytes,
                data_files=len(logical_files),
                raw_files=int(record["raw_files"]),
                archived_files=int(record["archived_files"]),
                index_files=len(index_files),
                stock_files=len(stock_files),
                indices=tuple(sorted(str(index) for index in indices)),
                index_fno_files=len(record["index_fno_files"]),  # type: ignore[arg-type]
            )
        )

    return CaptureHistoryReport(
        sessions=tuple(sessions),
        total_bytes=sum(session.total_bytes for session in sessions),
        raw_bytes=sum(session.raw_bytes for session in sessions),
        archived_bytes=sum(session.archived_bytes for session in sessions),
        data_files=sum(session.data_files for session in sessions),
    )


def verify_integrity(path: str | os.PathLike[str]) -> bool:
    """Spot-check a ``.bin`` / ``.bin.zst``: it decodes, has frames, and timestamps are
    non-decreasing. (Framing + timestamps are schema-independent, so we can use the
    index reader for both index and stock files.)
    """
    try:
        with IndexBinReader(path) as reader:
            ts = reader.timestamps
            if not ts:
                return False
            return all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1))
    except Exception:  # noqa: BLE001 - any decode failure => not intact
        return False
