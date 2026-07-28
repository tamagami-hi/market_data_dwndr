"""Cheap structural scan of a ``.bin`` capture file.

Used at capture startup to recover the day's progress after a **mid-session restart**.
The writers open files in append mode (``"ab"``, header written only when the file is
empty), so captured data survives a restart — but every counter the monitor shows lives in
process memory and would otherwise restart from zero, making a healthy resumed session
look like near-total data loss.

Deliberately does NOT use ``IndexBinReader``/``StockBinReader``: those mmap the whole file
and run the full validating decode path. This walks only the ``[u32 len][payload]`` framing
plus the 12-byte tag+timestamp prefix of each frame, so the bytes actually read stay in the
hundreds of KB no matter how large the file is — measured 0.39–0.48 s for a full day
(17.5k frames; 410 MB index files and a 4.1 GB stocks file), paid once at startup.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

from app.bin_codec import layout

_LEN = struct.Struct("<I")
_TAG_TS = struct.Struct("<IQ")  # u32 tag + u64 timestamp_unix_ms


@dataclass(frozen=True)
class FrameScan:
    """What a resumed session needs to know about an existing capture file."""

    frames: int = 0
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    truncated: bool = False  # a partial trailing frame was found and ignored

    @property
    def exists(self) -> bool:
        return self.frames > 0


def scan_frames(path: str | os.PathLike[str]) -> FrameScan:
    """Count data frames and read the first/last timestamps from ``path``.

    Tolerates a torn trailing frame (possible only if the process died between writing
    the length prefix and the payload, since every frame is fsynced): the partial record
    is ignored and ``truncated`` is set, rather than raising.
    """
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        return FrameScan()

    frames = 0
    first_ts: int | None = None
    last_ts: int | None = None
    truncated = False

    with open(file_path, "rb") as handle:
        while True:
            raw_len = handle.read(_LEN.size)
            if len(raw_len) < _LEN.size:
                truncated = bool(raw_len)
                break
            (length,) = _LEN.unpack(raw_len)
            if length < _TAG_TS.size:
                # Too short to be a data frame (e.g. a stub record): skip the payload.
                if len(handle.read(length)) < length:
                    truncated = True
                    break
                continue
            prefix = handle.read(_TAG_TS.size)
            if len(prefix) < _TAG_TS.size:
                truncated = True
                break
            tag, timestamp = _TAG_TS.unpack(prefix)
            remaining = length - _TAG_TS.size
            if remaining:
                handle.seek(remaining, os.SEEK_CUR)
            if tag == layout.TAG_DATA:
                frames += 1
                if first_ts is None:
                    first_ts = timestamp
                last_ts = timestamp

    return FrameScan(
        frames=frames,
        first_timestamp_ms=first_ts,
        last_timestamp_ms=last_ts,
        truncated=truncated,
    )
