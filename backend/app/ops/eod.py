"""End-of-day orchestration: flush/close writers, then compress the day's files.

At/after close: writers flush and close today's ``.bin`` files, then each is compressed
to ``.bin.zst`` (zstd L17) and the raw is removed **only after the compressed copy
verifies** (docs/60-operations/operations-runbook.md, data-retention.md).

``prune_stale_raw`` runs at startup to mop up any raw ``.bin`` a prior crash left
uncompressed. Only ``*.bin`` files are touched -- ``_instruments/`` CSVs and ``_state/``
JSON are left alone.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.bin_codec import compress

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = compress.DEFAULT_LEVEL


@dataclass
class EODResult:
    compressed: list[Path]
    total_raw_bytes: int
    total_zst_bytes: int

    @property
    def ratio(self) -> float:
        if self.total_zst_bytes == 0:
            return 0.0
        return self.total_raw_bytes / self.total_zst_bytes


def compress_raw_files(
    market_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
    remove_src: bool = True,
) -> EODResult:
    """Compress every raw ``*.bin`` under ``market_data_path`` (verify then remove)."""
    root = Path(market_data_path)
    raw_files = sorted(root.rglob("*.bin"))
    total_raw = sum(p.stat().st_size for p in raw_files)
    compressed: list[Path] = []
    for bin_path in raw_files:
        try:
            dst = compress.compress_file(bin_path, level=level, remove_src=remove_src)
            compressed.append(dst)
        except Exception:  # noqa: BLE001 - one bad file must not abort the sweep
            logger.exception("failed to compress %s (keeping raw)", bin_path)
    total_zst = sum(p.stat().st_size for p in compressed if p.exists())
    logger.info(
        "EOD compressed %d files: %.1f MB -> %.1f MB",
        len(compressed),
        total_raw / 1e6,
        total_zst / 1e6,
    )
    return EODResult(compressed=compressed, total_raw_bytes=total_raw, total_zst_bytes=total_zst)


def run_eod(
    stop_capture: Callable[[], None],
    market_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
) -> EODResult:
    """Full EOD sequence: stop/flush writers, then compress today's files."""
    logger.info("EOD: stopping capture and closing writers")
    stop_capture()
    return compress_raw_files(market_data_path, level=level)


def prune_stale_raw(
    market_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
) -> EODResult:
    """Startup cleanup: compress any raw ``.bin`` left over from a prior crash."""
    logger.info("startup: pruning stale raw .bin under %s", market_data_path)
    return compress_raw_files(market_data_path, level=level)
