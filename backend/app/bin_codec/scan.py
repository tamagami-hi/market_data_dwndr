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
    # Every data frame's timestamp, in file order, when ``collect_timestamps`` was set.
    # This is what lets completeness be proven from the ARCHIVE rather than from
    # telemetry: a crash, container kill or power loss can destroy the telemetry snapshot,
    # but the frames that reached the disk cannot lie about which seconds exist.
    timestamps: tuple[int, ...] = ()

    @property
    def exists(self) -> bool:
        return self.frames > 0


def scan_frames(
    path: str | os.PathLike[str], *, collect_timestamps: bool = False
) -> FrameScan:
    """Count data frames and read the first/last timestamps from ``path``.

    Tolerates a torn trailing frame (possible only if the process died between writing
    the length prefix and the payload, since every frame is fsynced): the partial record
    is ignored and ``truncated`` is set, rather than raising.

    ``collect_timestamps`` additionally returns every frame's timestamp. It costs nothing
    extra on the I/O side — the timestamp is already read for first/last, so the same
    single pass just keeps them — and it is what makes interior holes visible. Sequence
    numbers cannot serve that purpose: they reset to 0 whenever the process restarts (the
    tables are rebuilt), so they only prove continuity *within* one process run.
    """
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        return FrameScan()
    file_size = file_path.stat().st_size

    frames = 0
    first_ts: int | None = None
    last_ts: int | None = None
    truncated = False
    collected: list[int] = []

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
                # ``seek`` past EOF succeeds silently, so the payload tail has to be
                # *proven* present. Without this a frame torn mid-payload was counted as
                # good and the file reported untruncated, while the reader — which checks
                # properly — discards it. That made ``frames_on_disk`` one too high and
                # could take ``last_timestamp_ms`` from a record nothing can read.
                if handle.seek(remaining, os.SEEK_CUR) > file_size:
                    truncated = True
                    break
            if tag == layout.TAG_DATA:
                frames += 1
                if first_ts is None:
                    first_ts = timestamp
                last_ts = timestamp
                if collect_timestamps:
                    collected.append(timestamp)

    return FrameScan(
        frames=frames,
        first_timestamp_ms=first_ts,
        last_timestamp_ms=last_ts,
        truncated=truncated,
        timestamps=tuple(collected),
    )


# Axis kinds, matching the three header layouts.
AXIS_INDEX = "index"  # IndexHeader: ... String expiry, f64 rate, Vec<i64> strikes
AXIS_STOCK = "stock"  # StockHeader: ... f64 rate, u64 n_stocks
AXIS_INDEX_FNO = "index_fno"  # IndexFnoHeader: ... f64 rate, u64 n_indices


def read_header_schema_version(path: str | os.PathLike[str]) -> int | None:
    """Schema version declared by the file's header record, or ``None`` if empty/unread.

    This is what decides whether an existing file may be appended to: frames of two
    schema versions decode differently and must never share a file. Reads only the first
    record, so it is cheap enough to call on every writer open.
    """
    payload = _read_header_payload(path)
    if payload is None:
        return None
    cursor = layout.Cursor(payload)
    if cursor.u32() != layout.TAG_HEADER:
        return None
    return int(cursor.u32())


def _read_header_payload(path: str | os.PathLike[str]) -> bytes | None:
    """The first record's payload bytes, or ``None`` for a missing/empty/short file."""
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        return None
    with open(file_path, "rb") as handle:
        raw_len = handle.read(_LEN.size)
        if len(raw_len) < _LEN.size:
            return None
        (length,) = _LEN.unpack(raw_len)
        payload = handle.read(length)
    if len(payload) < length:
        return None
    return payload


def read_header_axis(path: str | os.PathLike[str], kind: str) -> int | None:
    """Length of the axis the file's existing header declares, or ``None`` if absent.

    "Axis" is the fixed-width dimension every data frame in the file must match: the row
    count for the stocks matrix and the index-F&O grid.

    Those two domains still fix their row axis in the header, so a reopened file has to be
    checked against it: the header is written **once**, when the file is empty, while the
    in-memory axis is rebuilt on every bootstrap. Frames appended at a different width
    decode fine (Vec lengths are self-describing) but map every column onto the wrong row.

    Index files from schema v2 onward declare **no** strike axis in the header — each frame
    carries its own — so this returns ``None`` for them, and an index axis change is a
    normal in-file event rather than a conflict. Legacy v1 index files still report their
    header axis, which is what keeps the historical archive readable.

    Reads only the first record, so it is cheap enough to call on every writer open.
    """
    payload = _read_header_payload(path)
    if payload is None:
        return None
    cursor = layout.Cursor(payload)
    if cursor.u32() != layout.TAG_HEADER:
        return None
    schema_version = cursor.u32()
    cursor.string()  # trading_date
    if kind == AXIS_INDEX:
        if schema_version >= layout.SCHEMA_VERSION_INDEX:
            return None  # v2+ index headers carry no strike axis
        cursor.string()  # underlying
        cursor.string()  # expiry_date
    cursor.f64()  # risk_free_rate
    return int(cursor.u64())  # Vec/list element count = the axis length
