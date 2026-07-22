"""Data-retention helpers + integrity checks (docs/60-operations/data-retention.md).

Policy: raw ``.bin`` is transient (removed after the verified ``.zst`` is written);
compressed ``.bin.zst`` and instrument archives are kept indefinitely. These helpers
report on storage and spot-check that a compressed file still decodes + re-indexes with
monotonic timestamps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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


def scan_storage(
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
) -> RetentionReport:
    """Summarize transient SSD data and durable HDD archives."""
    live_root = Path(market_data_path)
    archive_root = Path(archive_data_path)
    raw = [p for p in live_root.rglob("*.bin")]
    zst = [p for p in archive_root.rglob("*.bin.zst")]
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
