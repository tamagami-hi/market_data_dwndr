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
    cctx = zstd.ZstdCompressor(level=level)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        cctx.copy_stream(fin, fout)
    if remove_src:
        if not verify_roundtrip(src, dst):
            raise ValueError(f"compressed {dst} did not verify against {src}; keeping raw")
        src.unlink()
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
    """True if decompressing ``dst_zst`` reproduces ``src_bin`` byte-for-byte."""
    with open(src_bin, "rb") as f:
        raw = f.read()
    return decompress_to_bytes(dst_zst) == raw


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
