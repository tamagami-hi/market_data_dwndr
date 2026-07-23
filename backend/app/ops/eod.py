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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.bin_codec import compress

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = compress.DEFAULT_LEVEL
DEFAULT_THREADS = compress.DEFAULT_THREADS

# A progress callback receives a snapshot dict describing the EOD sweep. It is
# invoked once when the sweep starts, once per file (before and after), and once
# on completion. Keys mirror ``app.ws.protocol.compression_progress``.
ProgressCallback = Callable[[dict], None]


@dataclass
class EODResult:
    compressed: list[Path]
    total_raw_bytes: int
    total_zst_bytes: int
    elapsed_ms: int = 0
    file_times_ms: list[float] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if self.total_zst_bytes == 0:
            return 0.0
        return self.total_raw_bytes / self.total_zst_bytes

    @property
    def avg_file_ms(self) -> float:
        if not self.file_times_ms:
            return 0.0
        return sum(self.file_times_ms) / len(self.file_times_ms)

    @property
    def throughput_mbps(self) -> float:
        """Raw MB compressed per second over the whole sweep."""
        if self.elapsed_ms <= 0:
            return 0.0
        return (self.total_raw_bytes / 1e6) / (self.elapsed_ms / 1000.0)


def compress_raw_files(
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
    threads: int = DEFAULT_THREADS,
    remove_src: bool = True,
    progress_cb: ProgressCallback | None = None,
) -> EODResult:
    """Move verified archives to ``archive_data_path``, retaining the live layout.

    ``threads`` enables multithreaded zstd (capped at ``compress.MAX_THREADS``).
    ``progress_cb`` is called with a status dict at start, around each file, and
    at completion so callers (e.g. the monitor) can render a live progress bar.
    """
    root = Path(market_data_path)
    archive_root = Path(archive_data_path)
    raw_files = sorted(root.rglob("*.bin"))
    total_raw = sum(p.stat().st_size for p in raw_files)
    files_total = len(raw_files)
    used_threads = compress._clamp_threads(threads)
    started_at_ms = int(time.time() * 1000)
    sweep_start = time.perf_counter()
    file_times_ms: list[float] = []

    def _emit(phase: str, *, files_done: int, bytes_done: int, current: str | None,
              zst_done: int, file_elapsed_ms: float = 0.0) -> None:
        if progress_cb is None:
            return
        elapsed_ms = (time.perf_counter() - sweep_start) * 1000.0
        avg_file_ms = (sum(file_times_ms) / len(file_times_ms)) if file_times_ms else 0.0
        throughput_mbps = (
            (bytes_done / 1e6) / (elapsed_ms / 1000.0) if elapsed_ms > 0 and bytes_done else 0.0
        )
        try:
            progress_cb(
                {
                    "phase": phase,
                    "files_done": files_done,
                    "files_total": files_total,
                    "bytes_done": bytes_done,
                    "bytes_total": total_raw,
                    "zst_bytes": zst_done,
                    "ratio": round(bytes_done / zst_done, 2) if zst_done else 0.0,
                    "current_file": current,
                    "threads": used_threads or 1,
                    "started_at": started_at_ms,
                    "updated_at": int(time.time() * 1000),
                    "elapsed_ms": int(round(elapsed_ms)),
                    "file_elapsed_ms": int(round(file_elapsed_ms)),
                    "avg_file_ms": round(avg_file_ms, 1),
                    "throughput_mbps": round(throughput_mbps, 2),
                }
            )
        except Exception:  # noqa: BLE001 - progress reporting must never break the sweep
            logger.debug("compression progress callback failed", exc_info=True)

    _emit("running", files_done=0, bytes_done=0, current=None, zst_done=0)

    compressed: list[Path] = []
    bytes_done = 0
    zst_done = 0
    for bin_path in raw_files:
        if bin_path.is_symlink():
            logger.warning("skipping symlinked raw file outside managed storage: %s", bin_path)
            continue
        raw_size = bin_path.stat().st_size
        _emit("running", files_done=len(compressed), bytes_done=bytes_done,
              current=bin_path.name, zst_done=zst_done)
        try:
            relative_archive = bin_path.relative_to(root).with_name(f"{bin_path.name}.zst")
            file_start = time.perf_counter()
            dst = compress.compress_file(
                bin_path,
                archive_root / relative_archive,
                level=level,
                threads=threads,
                remove_src=remove_src,
            )
            file_elapsed_ms = (time.perf_counter() - file_start) * 1000.0
            file_times_ms.append(file_elapsed_ms)
            compressed = [*compressed, dst]
            bytes_done += raw_size
            zst_done += dst.stat().st_size if dst.exists() else 0
            _emit("running", files_done=len(compressed), bytes_done=bytes_done,
                  current=bin_path.name, zst_done=zst_done, file_elapsed_ms=file_elapsed_ms)
        except Exception:  # noqa: BLE001 - one bad file must not abort the sweep
            logger.exception("failed to compress %s (keeping raw)", bin_path)
    total_zst = sum(p.stat().st_size for p in compressed if p.exists())
    total_elapsed_ms = int(round((time.perf_counter() - sweep_start) * 1000.0))
    logger.info(
        "EOD compressed %d files (%d threads): %.1f MB -> %.1f MB",
        len(compressed),
        used_threads or 1,
        total_raw / 1e6,
        total_zst / 1e6,
    )
    _emit("done", files_done=len(compressed), bytes_done=bytes_done,
          current=None, zst_done=total_zst)
    return EODResult(
        compressed=compressed,
        total_raw_bytes=total_raw,
        total_zst_bytes=total_zst,
        elapsed_ms=total_elapsed_ms,
        file_times_ms=file_times_ms,
    )


def run_eod(
    stop_capture: Callable[[], None],
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
    threads: int = DEFAULT_THREADS,
    progress_cb: ProgressCallback | None = None,
) -> EODResult:
    """Full EOD sequence: stop/flush writers, then compress today's files."""
    logger.info("EOD: stopping capture and closing writers")
    stop_capture()
    return compress_raw_files(
        market_data_path, archive_data_path,
        level=level, threads=threads, progress_cb=progress_cb,
    )


def prune_stale_raw(
    market_data_path: str | os.PathLike[str],
    archive_data_path: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
    threads: int = DEFAULT_THREADS,
    progress_cb: ProgressCallback | None = None,
) -> EODResult:
    """Startup cleanup: compress any raw ``.bin`` left over from a prior crash."""
    logger.info("startup: pruning stale raw .bin under %s", market_data_path)
    return compress_raw_files(
        market_data_path, archive_data_path,
        level=level, threads=threads, progress_cb=progress_cb,
    )
