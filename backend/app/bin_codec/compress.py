"""Whole-file zstd compression for BIN files.

At end of day each raw ``<date>.bin`` is compressed with **zstd level 17** to
``<date>.bin.zst`` (a zstd stream of the same framed bytes) and the raw file is
removed only after the compressed copy is verified (see
docs/60-operations/data-retention.md, docs/20-data-and-storage/bin-format.md).

``decompress_to_bytes`` is used by ``reader`` for transparent ``.zst`` reads.
Streaming APIs keep memory flat for multi-GB daily files.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import zstandard as zstd

DEFAULT_LEVEL = 17
ZST_SUFFIX = ".zst"


def compressed_path(src: str | os.PathLike[str]) -> Path:
    """``.../2026-07-21.bin`` -> ``.../2026-07-21.bin.zst`` (append, don't replace)."""
    src = Path(src)
    return src.with_name(src.name + ZST_SUFFIX)


def compress_file(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str] | None = None,
    *,
    level: int = DEFAULT_LEVEL,
    remove_src: bool = False,
) -> Path:
    """Compress ``src`` -> ``dst`` (default ``src + .zst``) with zstd ``level``.

    If ``remove_src`` is set, the raw file is deleted only after the compressed
    copy verifies byte-for-byte.
    """
    src = Path(src)
    dst = Path(dst) if dst is not None else compressed_path(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            is_existing_archive_valid = verify_roundtrip(src, dst)
        except (OSError, zstd.ZstdError):
            is_existing_archive_valid = False
        if is_existing_archive_valid:
            if remove_src:
                src.unlink()
            return dst
    cctx = zstd.ZstdCompressor(level=level)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dst.parent,
            prefix=f".{dst.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            with open(src, "rb") as source_file:
                cctx.copy_stream(source_file, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        if not verify_roundtrip(src, temp_path):
            raise ValueError(f"compressed {dst} did not verify against {src}; keeping raw")

        os.replace(temp_path, dst)
        temp_path = None
        _fsync_directory(dst.parent)
        if remove_src:
            src.unlink()
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return dst


def decompress_to_bytes(src: str | os.PathLike[str]) -> bytes:
    """Decompress an entire ``.zst`` file into memory."""
    dctx = zstd.ZstdDecompressor()
    with open(src, "rb") as fin, dctx.stream_reader(fin) as reader:
        return reader.read()


def decompress_file(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
) -> Path:
    """Stream-decompress ``src`` (.zst) to ``dst`` (raw ``.bin``)."""
    dst = Path(dst)
    dctx = zstd.ZstdDecompressor()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        dctx.copy_stream(fin, fout)
    return dst


def verify_roundtrip(src_bin: str | os.PathLike[str], dst_zst: str | os.PathLike[str]) -> bool:
    """Stream-compare a compressed file with its raw source byte-for-byte."""
    dctx = zstd.ZstdDecompressor()
    with open(src_bin, "rb") as raw_file, open(dst_zst, "rb") as compressed_file:
        with dctx.stream_reader(compressed_file) as reader:
            while raw_chunk := raw_file.read(1024 * 1024):
                if reader.read(len(raw_chunk)) != raw_chunk:
                    return False
            return reader.read(1) == b""


def _fsync_directory(path: Path) -> None:
    """Persist a published filename when the platform supports directory fsync."""
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def compress_directory(
    root: str | os.PathLike[str],
    *,
    level: int = DEFAULT_LEVEL,
    remove_src: bool = False,
) -> list[Path]:
    """Compress every ``*.bin`` under ``root`` (recursive). Used by the EOD sweep."""
    root = Path(root)
    outputs: list[Path] = []
    for bin_path in sorted(root.rglob("*.bin")):
        outputs.append(
            compress_file(bin_path, level=level, remove_src=remove_src)
        )
    return outputs
